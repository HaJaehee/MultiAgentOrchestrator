"""세션별 페르소나의 수명주기: 편집 → 첫 메시지에 고정 → 재개 시 재사용."""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agents.personas import (
    PersonasLockedError,
    apply_personas,
    effective_personas,
    freeze_personas,
    load_stored_personas,
    prepare_agents_for_turn,
    reset_persona,
    save_persona,
)
from app.agents.pool import AgentPool
from app.config import AgentConfig
from app.database.models import Base, SessionAgentModel, SessionModel


def _pool() -> AgentPool:
    return AgentPool({
        "orchestrator": AgentConfig(
            name="Master Orchestrator", role="Moderator", model="openai/gpt-4o",
            system_prompt="토론을 중재하세요.",
        ),
        "critic": AgentConfig(
            name="Critic", role="Reviewer", model="openai/gpt-4o",
            system_prompt="비판적으로 검토하세요.",
        ),
    })


@pytest_asyncio.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _new_session(factory, **kwargs) -> str:
    async with factory() as db:
        s = SessionModel(title="테스트 세션", active_agents=["orchestrator", "critic"], **kwargs)
        db.add(s)
        await db.commit()
        return s.id


@pytest.mark.asyncio
async def test_defaults_come_from_config(db_factory):
    """저장분이 없으면 conf.json 값이 그대로 쓰인다."""
    sid = await _new_session(db_factory)
    async with db_factory() as db:
        personas = await effective_personas(db, sid, _pool())
    assert personas["orchestrator"].name == "Master Orchestrator"
    assert personas["critic"].system_prompt == "비판적으로 검토하세요."
    assert all(not p.is_customized for p in personas.values())


@pytest.mark.asyncio
async def test_edit_before_first_message(db_factory):
    """토론 시작 전에는 편집과 되돌리기가 모두 가능하다."""
    sid = await _new_session(db_factory)
    async with db_factory() as db:
        session_model = await db.get(SessionModel, sid)
        await save_persona(
            db, session_model, "critic",
            name="보안 감사관", role="Security Auditor", system_prompt="OWASP 기준으로 검토하세요.",
        )

    async with db_factory() as db:
        personas = await effective_personas(db, sid, _pool())
    assert personas["critic"].name == "보안 감사관"
    assert personas["critic"].is_customized is True
    # 손대지 않은 에이전트는 기본값 유지
    assert personas["orchestrator"].name == "Master Orchestrator"

    async with db_factory() as db:
        session_model = await db.get(SessionModel, sid)
        await reset_persona(db, session_model, "critic")

    async with db_factory() as db:
        personas = await effective_personas(db, sid, _pool())
    assert personas["critic"].name == "Critic"


@pytest.mark.asyncio
async def test_first_turn_freezes_all_agents(db_factory):
    """첫 턴에 모든 에이전트의 페르소나가 기록되고 세션이 잠긴다."""
    sid = await _new_session(db_factory)
    async with db_factory() as db:
        session_model = await db.get(SessionModel, sid)
        await save_persona(db, session_model, "critic", "보안 감사관", "Auditor", "엄격하게.")

    async with db_factory() as db:
        session_model = await db.get(SessionModel, sid)
        assert session_model.personas_locked is False
        await prepare_agents_for_turn(db, session_model, _pool(), ["orchestrator", "critic"])

    async with db_factory() as db:
        session_model = await db.get(SessionModel, sid)
        assert session_model.personas_locked is True
        stored = await load_stored_personas(db, sid)

    # 편집하지 않은 에이전트도 스냅샷이 남아야 한다
    assert set(stored) == {"orchestrator", "critic"}
    assert stored["orchestrator"].name == "Master Orchestrator"
    assert stored["critic"].name == "보안 감사관"


@pytest.mark.asyncio
async def test_locked_session_rejects_edits(db_factory):
    """잠긴 뒤에는 저장도 되돌리기도 거부된다."""
    sid = await _new_session(db_factory, personas_locked=True)
    async with db_factory() as db:
        session_model = await db.get(SessionModel, sid)
        with pytest.raises(PersonasLockedError):
            await save_persona(db, session_model, "critic", "다른 이름", "역할", "프롬프트")
        with pytest.raises(PersonasLockedError):
            await reset_persona(db, session_model, "critic")


@pytest.mark.asyncio
async def test_resumed_session_keeps_frozen_personas(db_factory):
    """세션을 다시 열면 conf.json 이 바뀌었어도 잠글 때의 페르소나를 쓴다."""
    sid = await _new_session(db_factory)
    async with db_factory() as db:
        session_model = await db.get(SessionModel, sid)
        await save_persona(db, session_model, "critic", "보안 감사관", "Auditor", "엄격하게.")
        await freeze_personas(db, session_model, _pool())

    # 그 사이 conf.json 이 바뀐 상황을 흉내낸다
    changed_pool = AgentPool({
        "orchestrator": AgentConfig(
            name="완전히 다른 오케스트레이터", role="Other", model="openai/gpt-4o",
            system_prompt="다른 지침.",
        ),
        "critic": AgentConfig(
            name="다른 크리틱", role="Other", model="openai/gpt-4o", system_prompt="다른 지침.",
        ),
    })

    async with db_factory() as db:
        session_model = await db.get(SessionModel, sid)
        agents = await prepare_agents_for_turn(
            db, session_model, changed_pool, ["orchestrator", "critic"]
        )

    by_key = {a.key: a for a in agents}
    assert by_key["critic"].name == "보안 감사관"
    assert by_key["critic"].system_prompt == "엄격하게."
    assert by_key["orchestrator"].name == "Master Orchestrator"
    assert by_key["orchestrator"].system_prompt == "토론을 중재하세요."


@pytest.mark.asyncio
async def test_freeze_is_idempotent(db_factory):
    """두 번째 턴이 스냅샷을 덮어쓰지 않는다."""
    sid = await _new_session(db_factory)
    async with db_factory() as db:
        session_model = await db.get(SessionModel, sid)
        await freeze_personas(db, session_model, _pool())

    async with db_factory() as db:
        session_model = await db.get(SessionModel, sid)
        await freeze_personas(db, session_model, _pool())
        rows = (await db.execute(
            select(SessionAgentModel).where(SessionAgentModel.session_id == sid)
        )).scalars().all()

    assert len(rows) == 2, "중복 스냅샷이 생겼습니다"


def test_apply_personas_does_not_mutate_pool():
    """페르소나 적용은 사본에만 반영되어야 한다 (전역 풀 오염 방지)."""
    pool = _pool()
    personas = {}
    async_personas = effective_personas  # noqa: F841 - 명시적 참조

    from app.agents.personas import AgentPersona

    personas["critic"] = AgentPersona(
        agent_key="critic", name="바뀐 이름", role="바뀐 역할", system_prompt="바뀐 프롬프트"
    )
    applied = apply_personas(pool.list_all(), personas)

    by_key = {a.key: a for a in applied}
    assert by_key["critic"].name == "바뀐 이름"
    assert pool.get("critic").name == "Critic", "전역 에이전트 풀이 오염되었습니다"


@pytest.mark.asyncio
async def test_customized_flag_reflects_value_not_row_existence(db_factory):
    """잠금 시 전원이 스냅샷되지만, 손대지 않은 에이전트는 '기본값과 다름'이 아니다."""
    sid = await _new_session(db_factory)
    async with db_factory() as db:
        session_model = await db.get(SessionModel, sid)
        await save_persona(db, session_model, "critic", "보안 감사관", "Auditor", "엄격하게.")
        await freeze_personas(db, session_model, _pool())

    async with db_factory() as db:
        stored = await load_stored_personas(db, sid)
        personas = await effective_personas(db, sid, _pool())

    # 두 에이전트 모두 스냅샷 행이 존재한다
    assert set(stored) == {"orchestrator", "critic"}
    # 그러나 '기본값과 다름'은 실제로 바꾼 쪽만
    assert personas["critic"].is_customized is True
    assert personas["orchestrator"].is_customized is False


@pytest.mark.asyncio
async def test_saving_default_value_is_not_flagged(db_factory):
    """기본값과 똑같이 저장하면 '기본값과 다름' 표시가 붙지 않는다."""
    sid = await _new_session(db_factory)
    async with db_factory() as db:
        session_model = await db.get(SessionModel, sid)
        await save_persona(db, session_model, "critic", "Critic", "Reviewer", "비판적으로 검토하세요.")
        personas = await effective_personas(db, sid, _pool())

    assert personas["critic"].is_customized is False
