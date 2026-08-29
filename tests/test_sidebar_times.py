"""세션 카드에 찍히는 시각.

카드에 있던 것은 세션 행이 만들어진 시각이었습니다. 새 세션을 열어 두고 나중에
말을 걸면 둘이 크게 벌어져서, 목록에서 "이 대화 언제 했더라" 를 찾을 때 쓸모가
없었습니다. 사람이 기억하는 것은 **말을 건 시각**입니다.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.database.models import MessageModel, SessionModel
from app.database.session import get_session_factory, init_db
from app.ui.components.sidebar import first_user_message_times, to_local

DB_URL = "sqlite+aiosqlite:///:memory:"


async def _seed(rows) -> str:
    """세션 하나와 그 발언들을 넣고 세션 id 를 돌려줍니다."""
    await init_db(DB_URL)
    factory = get_session_factory(DB_URL)
    sid = f"times-{uuid.uuid4().hex[:8]}"
    async with factory() as db:
        db.add(SessionModel(id=sid, title="T", created_at=datetime(2026, 1, 1, 0, 0)))
        for sender_key, when in rows:
            db.add(MessageModel(
                id=str(uuid.uuid4()), session_id=sid, sender_key=sender_key,
                sender_name=sender_key, content="...", created_at=when,
                msg_type="user" if sender_key == "user" else "agent",
            ))
        await db.commit()
    return sid


@pytest.mark.asyncio
async def test_time_is_the_first_user_message_not_the_row_creation():
    """세션은 1월 1일에 만들어졌지만 말을 건 것은 3월 5일입니다."""
    sid = await _seed([
        ("user", datetime(2026, 3, 5, 14, 30)),
        ("architect", datetime(2026, 3, 5, 14, 31)),
    ])

    async with get_session_factory(DB_URL)() as db:
        times = await first_user_message_times(db)

    assert times[sid] == datetime(2026, 3, 5, 14, 30)


@pytest.mark.asyncio
async def test_later_interjections_do_not_move_the_start():
    """토론 중 개입도 사용자 발언이지만, 대화가 시작된 시각은 첫 발언입니다."""
    sid = await _seed([
        ("user", datetime(2026, 3, 5, 14, 30)),      # 최초 요청
        ("coder", datetime(2026, 3, 5, 14, 31)),
        ("user", datetime(2026, 3, 5, 15, 10)),      # 토론 중 개입
    ])

    async with get_session_factory(DB_URL)() as db:
        times = await first_user_message_times(db)

    assert times[sid] == datetime(2026, 3, 5, 14, 30)


@pytest.mark.asyncio
async def test_agent_only_session_has_no_start_time():
    """사용자가 아직 말하지 않았으면 시작 시각이 없습니다 (카드에는 '시작 전')."""
    sid = await _seed([("orchestrator", datetime(2026, 3, 5, 14, 31))])

    async with get_session_factory(DB_URL)() as db:
        times = await first_user_message_times(db)

    assert sid not in times


@pytest.mark.asyncio
async def test_each_session_gets_its_own_start():
    first = await _seed([("user", datetime(2026, 3, 1, 9, 0))])
    second = await _seed([("user", datetime(2026, 3, 2, 18, 45))])

    async with get_session_factory(DB_URL)() as db:
        times = await first_user_message_times(db)

    assert times[first] == datetime(2026, 3, 1, 9, 0)
    assert times[second] == datetime(2026, 3, 2, 18, 45)


# --------------------------------------------------------------- 시간대


def test_naive_timestamps_are_read_as_utc_and_shown_locally():
    """SQLite 는 오프셋을 버립니다. 그대로 찍으면 한국에서 9시간 전으로 보입니다."""
    stored = datetime(2026, 3, 5, 5, 30)              # UTC 벽시계
    local = to_local(stored)

    assert local.tzinfo is not None
    assert local == stored.replace(tzinfo=timezone.utc)
    # 같은 순간을 가리키되, 표시용 벽시계는 이 기계의 시간대를 따릅니다.
    assert local.utcoffset() == datetime.now().astimezone().utcoffset()


def test_aware_timestamps_are_left_as_they_are():
    aware = datetime(2026, 3, 5, 5, 30, tzinfo=timezone(timedelta(hours=9)))
    assert to_local(aware) == aware
