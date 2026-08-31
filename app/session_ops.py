"""대화 기록을 되돌리는 작업.

토론은 한번 시작하면 끝까지 가는 것이 원래 설계였습니다. 정지(`TurnControl`)조차
"남은 라운드를 건너뛰고 지금까지의 것으로 합성하라" 는 뜻이라, 요청 자체가 틀렸을
때 — 오타, 잘못 붙여넣은 글, 다른 대화에 보낼 뻔한 요청 — 할 수 있는 것이 없었습니다.
합성이 끝날 때까지 기다렸다가, 틀린 요청과 그에 답한 발언들을 기록에 남긴 채 다시
쓰는 것뿐이었습니다. 그 기록은 다음 턴의 맥락으로 계속 따라다닙니다.

여기서는 그 턴이 남긴 것을 지웁니다. 지우는 범위는 **그 턴이 만든 것**뿐입니다.
앞선 턴의 발언은 그대로 둡니다 — 사람이 고치려는 것은 방금 보낸 요청이지 대화
전체가 아닙니다.
"""

import logging
from typing import Iterable, List, Sequence

from sqlalchemy import delete, func, select

from app.database.models import (
    ArtifactModel,
    MessageModel,
    SessionModel,
    ToolCallRecordModel,
)

logger = logging.getLogger(__name__)


def _clean(ids: Iterable[str]) -> List[str]:
    """빈 값과 중복을 걷어냅니다 (스트리밍 중 기록은 id 가 비어 있을 수 있습니다)."""
    seen: List[str] = []
    for value in ids or ():
        if value and value not in seen:
            seen.append(value)
    return seen


async def discard_turn(
    db,
    session_id: str,
    message_ids: Sequence[str],
    artifact_ids: Sequence[str] = (),
) -> bool:
    """한 턴이 남긴 발언·도구 기록·산출물을 지웁니다.

    돌려주는 값은 **이 대화가 시작 전 상태로 돌아갔는지**입니다. 남은 발언이
    하나도 없으면 페르소나 잠금을 풀어 줍니다 — 첫 요청을 지웠다면 이 대화는
    아직 시작하지 않은 것이고, 그렇다면 에이전트 구성도 다시 만질 수 있어야
    말이 맞습니다. 굳혀 둔 구성 스냅샷(`session_agents`)은 지우지 않습니다.
    다음 턴이 시작될 때 그 시점의 `conf.json` 으로 다시 굳고 다시 잠깁니다.

    도구 기록을 먼저 지웁니다. `messages.id` 를 가리키는 행이라, 발언을 먼저
    지우면 아무도 가리키지 않는 기록이 남습니다 (SQLite 가 외래키를 검사하지
    않아 조용히 남을 뿐입니다).
    """
    ids = _clean(message_ids)
    if ids:
        await db.execute(
            delete(ToolCallRecordModel).where(ToolCallRecordModel.message_id.in_(ids))
        )
        await db.execute(
            delete(MessageModel).where(
                MessageModel.id.in_(ids),
                MessageModel.session_id == session_id,
            )
        )

    art_ids = _clean(artifact_ids)
    if art_ids:
        await db.execute(
            delete(ArtifactModel).where(
                ArtifactModel.id.in_(art_ids),
                ArtifactModel.session_id == session_id,
            )
        )

    remaining = await db.scalar(
        select(func.count())
        .select_from(MessageModel)
        .where(MessageModel.session_id == session_id)
    )
    started_over = not remaining

    if started_over:
        session = await db.get(SessionModel, session_id)
        if session is not None and session.personas_locked:
            session.personas_locked = False

    await db.commit()
    logger.info(
        f"Discarded a turn from session {session_id}: "
        f"{len(ids)} message(s), {len(art_ids)} artifact(s)"
        + (" — the conversation is back to 'not started'" if started_over else "")
    )
    return started_over
