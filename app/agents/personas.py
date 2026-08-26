"""세션별 에이전트 페르소나 / 시스템 프롬프트 관리.

`conf.toml` 의 에이전트 정의는 서버 전역 기본값입니다. 세션마다 다른 페르소나로
토론시키려면 서버를 재기동해야 했는데, 이 모듈이 그 제약을 없앱니다.

수명주기:

1. **세션 생성 직후 (열림)** — 유저가 페르소나 편집 페이지에서 이름/역할/시스템
   프롬프트를 고칩니다. 편집분은 `session_agents` 에 초안으로 저장됩니다.
   저장하지 않은 에이전트는 `conf.toml` 기본값을 그대로 씁니다.
2. **첫 유저 메시지 (잠금)** — 그 시점의 유효값이 *모든* 에이전트에 대해
   기록되고 `sessions.personas_locked` 가 True 가 됩니다. 토론 도중 페르소나가
   바뀌면 앞선 발언과 뒤의 발언이 서로 다른 인격에서 나오게 되므로, 기록을
   해석할 수 없게 됩니다.
3. **세션 재개** — 저장된 값이 그대로 사용됩니다. 그 사이 `conf.toml` 이
   바뀌었더라도 세션은 잠글 때의 페르소나를 유지합니다.
"""

import logging
from typing import Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import Agent
from app.agents.pool import AgentPool
from app.database.models import SessionAgentModel, SessionModel

logger = logging.getLogger(__name__)

# 편집 가능한 항목. 모델/엔드포인트/도구 권한은 운영 설정이므로 conf.toml 에 둡니다.
EDITABLE_FIELDS = ("name", "role", "system_prompt")


class PersonasLockedError(RuntimeError):
    """첫 유저 메시지 이후 페르소나를 수정하려 할 때 발생합니다."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(
            f"세션 '{session_id}' 은 이미 토론이 시작되어 페르소나가 고정되었습니다."
        )


class AgentPersona(BaseModel):
    """한 에이전트의 세션 내 인격."""

    agent_key: str
    name: str
    role: str
    system_prompt: str = ""
    # conf.toml 기본값과 다른지 여부 (UI 표시용).
    # "저장된 행이 있는가" 가 아니라 "값이 실제로 다른가" 입니다. 세션을 잠글 때
    # 손대지 않은 에이전트까지 전부 스냅샷되므로, 행의 존재만으로는 판단할 수 없습니다.
    is_customized: bool = Field(default=False)


def _differs(a: AgentPersona, b: AgentPersona) -> bool:
    """편집 가능한 항목 중 하나라도 다르면 True."""
    return any(getattr(a, f) != getattr(b, f) for f in EDITABLE_FIELDS)


def persona_from_agent(agent: Agent) -> AgentPersona:
    """conf.toml 기반 에이전트에서 기본 페르소나를 만듭니다."""
    return AgentPersona(
        agent_key=agent.key,
        name=agent.name,
        role=agent.role,
        system_prompt=agent.system_prompt or "",
    )


def default_personas(pool: AgentPool) -> Dict[str, AgentPersona]:
    """conf.toml 기준 기본 페르소나 전체."""
    return {a.key: persona_from_agent(a) for a in pool.list_all()}


async def load_stored_personas(db: AsyncSession, session_id: str) -> Dict[str, AgentPersona]:
    """DB 에 저장된 페르소나(초안 또는 잠긴 스냅샷)를 읽습니다."""
    result = await db.execute(
        select(SessionAgentModel).where(SessionAgentModel.session_id == session_id)
    )
    stored: Dict[str, AgentPersona] = {}
    for row in result.scalars().all():
        stored[row.agent_key] = AgentPersona(
            agent_key=row.agent_key,
            name=row.name,
            role=row.role,
            system_prompt=row.system_prompt or "",
        )
    return stored


async def effective_personas(
    db: AsyncSession, session_id: str, pool: AgentPool
) -> Dict[str, AgentPersona]:
    """이 세션에서 실제로 쓰이는 페르소나.

    저장분이 있으면 그것을, 없으면 `conf.toml` 기본값을 씁니다. 세션을 잠근 뒤에
    `conf.toml` 에 새로 추가된 에이전트는 저장분이 없으므로 기본값으로 참여합니다.
    """
    defaults = default_personas(pool)
    personas = dict(defaults)
    for key, stored in (await load_stored_personas(db, session_id)).items():
        base = defaults.get(key)
        personas[key] = stored.model_copy(
            update={"is_customized": base is None or _differs(stored, base)}
        )
    return personas


async def save_persona(
    db: AsyncSession,
    session_model: SessionModel,
    agent_key: str,
    name: str,
    role: str,
    system_prompt: str,
) -> AgentPersona:
    """페르소나 초안을 저장합니다. 잠긴 세션이면 거부합니다."""
    if session_model.personas_locked:
        raise PersonasLockedError(session_model.id)

    result = await db.execute(
        select(SessionAgentModel).where(
            SessionAgentModel.session_id == session_model.id,
            SessionAgentModel.agent_key == agent_key,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = SessionAgentModel(session_id=session_model.id, agent_key=agent_key)
        db.add(row)

    row.name = name.strip()
    row.role = role.strip()
    row.system_prompt = system_prompt.strip()
    await db.commit()

    logger.info(f"Persona saved for session={session_model.id} agent={agent_key}")
    saved = AgentPersona(
        agent_key=agent_key, name=row.name, role=row.role, system_prompt=row.system_prompt
    )
    return saved


async def reset_persona(db: AsyncSession, session_model: SessionModel, agent_key: str) -> None:
    """초안을 지워 `conf.toml` 기본값으로 되돌립니다."""
    if session_model.personas_locked:
        raise PersonasLockedError(session_model.id)

    await db.execute(
        delete(SessionAgentModel).where(
            SessionAgentModel.session_id == session_model.id,
            SessionAgentModel.agent_key == agent_key,
        )
    )
    await db.commit()
    logger.info(f"Persona reset for session={session_model.id} agent={agent_key}")


async def freeze_personas(
    db: AsyncSession, session_model: SessionModel, pool: AgentPool
) -> Dict[str, AgentPersona]:
    """첫 유저 메시지 시점의 유효 페르소나를 전부 기록하고 세션을 잠급니다.

    이미 잠긴 세션이면 저장된 값을 그대로 돌려줍니다 (멱등).
    """
    if session_model.personas_locked:
        return await effective_personas(db, session_model.id, pool)

    personas = await effective_personas(db, session_model.id, pool)
    stored_keys = set((await load_stored_personas(db, session_model.id)).keys())

    for key, persona in personas.items():
        if key in stored_keys:
            continue  # 초안이 이미 있으면 그대로 둡니다
        db.add(
            SessionAgentModel(
                session_id=session_model.id,
                agent_key=key,
                name=persona.name,
                role=persona.role,
                system_prompt=persona.system_prompt,
            )
        )

    session_model.personas_locked = True
    await db.commit()
    logger.info(
        f"Personas frozen for session={session_model.id}: {sorted(personas.keys())}"
    )
    return personas


def apply_personas(agents: List[Agent], personas: Dict[str, AgentPersona]) -> List[Agent]:
    """에이전트 목록에 페르소나를 입힌 사본을 만듭니다 (원본 풀은 건드리지 않음)."""
    applied: List[Agent] = []
    for agent in agents:
        persona = personas.get(agent.key)
        if persona is None:
            applied.append(agent)
            continue
        applied.append(
            agent.model_copy(
                update={
                    "name": persona.name or agent.name,
                    "role": persona.role or agent.role,
                    "system_prompt": persona.system_prompt or agent.system_prompt,
                }
            )
        )
    return applied


async def prepare_agents_for_turn(
    db: AsyncSession,
    session_model: SessionModel,
    pool: AgentPool,
    active_keys: List[str],
) -> List[Agent]:
    """토론 한 턴에 쓸 에이전트를 준비합니다.

    첫 턴이면 페르소나를 기록하고 잠급니다. 이후 턴은 잠긴 값을 그대로 씁니다.
    """
    personas = await freeze_personas(db, session_model, pool)
    return apply_personas(pool.get_active(active_keys), personas)
