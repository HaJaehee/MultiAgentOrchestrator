import pytest
from app.agents.base import Agent
from app.agents.pool import AgentPool
from app.database.models import SessionModel
from app.database.session import get_session_factory, init_db
from app.orchestration.engine import OrchestratorEngine
from app.orchestration.state import DebateState
from tests.fake_llm import FakeLLMCaller
from app.orchestration.strategies import (
    AdversarialDebateStrategy,
    FreeDebateStrategy,
    SequentialReviewStrategy,
)


def test_debate_strategies_sequencing():
    a1 = Agent(key="orchestrator", name="Orch", role="Lead")
    a2 = Agent(key="architect", name="Arch", role="Arch")
    a3 = Agent(key="coder", name="Dev", role="Coder")
    a4 = Agent(key="critic", name="Critic", role="Reviewer")
    agents = [a1, a2, a3, a4]

    state = DebateState(session_id="test-1", user_prompt="Build App")

    # 1. Free Debate
    free_strat = FreeDebateStrategy()
    speakers_free = free_strat.get_speakers_for_round(agents, 1, state)
    assert len(speakers_free) == 3
    assert a1 not in speakers_free

    # 2. Sequential Review
    seq_strat = SequentialReviewStrategy()
    speakers_seq = seq_strat.get_speakers_for_round(agents, 1, state)
    assert [s.key for s in speakers_seq] == ["architect", "coder", "critic"]

    # 3. Adversarial Debate
    adv_strat = AdversarialDebateStrategy()
    speakers_adv = adv_strat.get_speakers_for_round(agents, 1, state)
    assert len(speakers_adv) == 3
    assert speakers_adv[0].key in ["architect", "coder"]
    assert speakers_adv[1].key == "critic"


@pytest.mark.asyncio
async def test_orchestrator_engine_turn_e2e():
    import uuid
    db_url = "sqlite+aiosqlite:///:memory:"
    await init_db(db_url)
    session_factory = get_session_factory(db_url)

    # Create session in DB
    sid = f"test-session-e2e-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        session = SessionModel(
            id=sid,
            title="E2E Microservice Architecture",
            strategy="sequential_review",
            max_rounds=1,
            active_agents=["orchestrator", "architect", "coder", "critic"],
        )
        db.add(session)
        await db.commit()

    engine = OrchestratorEngine(llm_caller=FakeLLMCaller())

    events = []
    async def capture_event(ev):
        events.append(ev)

    state = await engine.run_turn(
        session_id=sid,
        user_prompt="분산 캐싱을 포함한 비동기 백엔드 아키텍처 설계 및 구현",
        on_event=capture_event,
    )

    assert state.status == "completed"
    assert state.is_consensus_reached is True
    assert len(state.messages) >= 4  # User + Orch Plan + Specialists + Orch Synthesis
    assert len(state.artifacts) >= 1  # Synthesis report, mermaid, code
    assert any(ev["type"] == "turn_completed" for ev in events)
    assert any(ev["type"] == "artifacts_synthesized" for ev in events)
