"""새로고침·연결 끊김·다이어그램 누락에 대한 회귀 테스트.

사용자가 실제로 겪은 네 가지를 그대로 고정합니다.

1. 진행 중인 토론이 페이지와 함께 죽지 않는다 (`DebateRunner`).
2. 합성 보고서에 Mermaid 가 없어도 다이어그램 아티팩트가 나온다.
3. 스냅샷으로 다시 붙었을 때 스트리밍 중인 발언이 이어진다.
4. LLM 이 죽으면 지어낸 답변 대신 실패 사실이 기록된다.
"""

import asyncio
import uuid

import pytest

from app.agents.base import Agent
from app.agents.llm import LLMUnavailableError
from app.agents.pool import AgentPool
from app.config import AgentConfig
from app.database.models import SessionModel
from app.database.session import get_session_factory, init_db
from app.orchestration.engine import (
    OrchestratorEngine,
    extract_code_blocks,
    normalize_mermaid,
)
from app.orchestration.runner import DebateRunner
from tests.fake_llm import ARCHITECT_REPLY, FakeLLMCaller


def _fixed_pool() -> AgentPool:
    """conf.toml 이나 다른 테스트가 건드린 전역 풀에 흔들리지 않는 고정 풀."""
    return AgentPool({
        key: AgentConfig(name=name, role=role, model="fake/model", api_key="test-key")
        for key, name, role in (
            ("orchestrator", "Master Orchestrator", "Moderator"),
            ("architect", "System Architect", "Architecture"),
            ("coder", "Senior Engineer", "Implementation"),
            ("critic", "Quality Critic", "Review"),
        )
    })


def _engine(**kwargs) -> OrchestratorEngine:
    return OrchestratorEngine(agent_pool=_fixed_pool(), **kwargs)


async def _make_session(**kwargs) -> str:
    await init_db("sqlite+aiosqlite:///:memory:")
    session_factory = get_session_factory("sqlite+aiosqlite:///:memory:")
    sid = f"resilience-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        db.add(SessionModel(
            id=sid,
            title="Resilience",
            strategy=kwargs.get("strategy", "sequential_review"),
            max_rounds=kwargs.get("max_rounds", 1),
            active_agents=["orchestrator", "architect", "coder", "critic"],
        ))
        await db.commit()
    return sid


# --------------------------------------------------------------- 1. 백그라운드 실행


@pytest.mark.asyncio
async def test_runner_survives_subscriber_going_away():
    """구독자가 사라져도 토론 태스크는 끝까지 돕니다 (새로고침 시나리오)."""
    sid = await _make_session()
    runner = DebateRunner(_engine(llm_caller=FakeLLMCaller()))

    run = runner.start(sid, "분산 캐시 설계")
    queue = run.subscribe()
    await queue.get()          # 첫 이벤트만 받고
    run.unsubscribe(queue)     # 화면이 사라진 것처럼 구독을 끊습니다

    await run.task
    assert run.status == "completed"
    assert run.busy is False
    # 구독자가 없어도 정본 스냅샷은 계속 쌓입니다.
    assert len(run.snapshot()["messages"]) > 3
    assert run.snapshot()["artifacts"]


@pytest.mark.asyncio
async def test_late_subscriber_gets_full_snapshot():
    """토론 도중에 붙은 화면이 지금까지의 발언을 전부 받습니다."""
    sid = await _make_session()
    runner = DebateRunner(_engine(llm_caller=FakeLLMCaller()))

    run = runner.start(sid, "이벤트 드리븐 아키텍처")
    await run.task

    snapshot = run.snapshot()
    assert snapshot["status"] == "completed"
    assert snapshot["streaming_ids"] == set()
    assert any(m["sender_key"] == "user" for m in snapshot["messages"])
    # id 중복 없이 한 번씩만 들어 있어야 DB 기록과 병합할 수 있습니다.
    ids = [m["id"] for m in snapshot["messages"]]
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_snapshot_tracks_streaming_message_in_progress():
    """스트리밍 중에 붙은 화면이 이어 쓸 수 있도록 진행 중인 발언을 표시합니다."""
    from app.orchestration.runner import TurnRun

    run = TurnRun("s1", "prompt")
    run.apply({"type": "message_stream_start", "message": {
        "id": "m1", "sender_key": "architect", "sender_name": "A",
        "sender_role": "R", "content": "", "round_number": 1, "msg_type": "agent",
    }})
    run.apply({"type": "message_stream_chunk", "message_id": "m1", "delta": "앞부분"})

    snap = run.snapshot()
    assert snap["streaming_ids"] == {"m1"}
    assert snap["messages"][0]["content"] == "앞부분"

    run.apply({"type": "message_added", "message": {
        "id": "m1", "sender_key": "architect", "sender_name": "A",
        "sender_role": "R", "content": "앞부분 뒷부분", "round_number": 1, "msg_type": "agent",
    }})
    snap = run.snapshot()
    assert snap["streaming_ids"] == set()
    assert len(snap["messages"]) == 1
    assert snap["messages"][0]["content"] == "앞부분 뒷부분"


@pytest.mark.asyncio
async def test_slow_subscriber_is_dropped_not_the_run():
    """따라오지 못하는 구독자는 버리고 토론은 계속합니다."""
    from app.orchestration.runner import TurnRun

    run = TurnRun("s1", "prompt")
    queue = run.subscribe()
    for _ in range(queue.maxsize):
        queue.put_nowait({"type": "noop"})

    run._fanout({"type": "overflow"})
    assert run.subscriber_count == 0


# --------------------------------------------------------------- 2. 다이어그램


def test_unterminated_fence_is_still_extracted():
    """max_tokens 로 잘린 마지막 블록도 건집니다."""
    text = "보고서\n\n```mermaid\ngraph TD\n    A --> B"
    blocks = extract_code_blocks(text)
    assert [b["language"] for b in blocks] == ["mermaid"]
    assert "A --> B" in blocks[0]["code"]


def test_unlabelled_block_with_diagram_header_counts_as_mermaid():
    blocks = extract_code_blocks("```\nflowchart LR\n    A --> B\n```")
    assert blocks[0]["language"] == "mermaid"


def test_normalize_mermaid_quotes_parenthesised_labels():
    src = "graph TD\n    A[결제 서비스 (Payment)] --> B[(DB)]\n"
    out = normalize_mermaid(src)
    assert 'A["결제 서비스 (Payment)"]' in out
    # 원통 노드 `[(...)]` 같은 모양 문법은 건드리지 않습니다.
    assert "B[(DB)]" in out


@pytest.mark.asyncio
async def test_diagram_falls_back_to_debate_transcript():
    """합성 보고서에 Mermaid 가 없으면 토론 본문에서 찾아옵니다."""
    sid = await _make_session()
    caller = FakeLLMCaller(replies={"orchestrator": "## 최종 합의\n\n다이어그램 없이 요약만 적었습니다."})
    engine = _engine(llm_caller=caller)

    state = await engine.run_turn(session_id=sid, user_prompt="설계해줘")

    mermaid = [a for a in state.artifacts if a.artifact_type == "mermaid"]
    assert mermaid, "아키텍트가 그린 다이어그램을 산출물로 건져야 합니다"
    assert "graph TD" in mermaid[0].content
    assert ARCHITECT_REPLY.count("graph TD") == 1


# --------------------------------------------------------------- 4. 정직한 실패


@pytest.mark.asyncio
async def test_failed_agent_is_recorded_not_faked():
    sid = await _make_session()
    engine = _engine(llm_caller=FakeLLMCaller(fail_keys=["coder"]))

    state = await engine.run_turn(session_id=sid, user_prompt="구현해줘")

    coder_msgs = [m for m in state.messages if m.sender_key == "coder"]
    assert coder_msgs, "실패해도 자리는 남아야 합니다"
    assert all(m.msg_type == "error" for m in coder_msgs)
    assert "연결 끊김" in coder_msgs[0].content
    assert "500 Internal Server Error" in coder_msgs[0].content
    assert state.failed_agent_keys == ["coder"]
    assert state.is_consensus_reached is False


@pytest.mark.asyncio
async def test_failure_notice_never_enters_the_next_agents_context():
    """실패 안내문이 다음 발언자의 입력에 섞이면 그걸 논평하기 시작합니다."""
    sid = await _make_session()
    caller = FakeLLMCaller(fail_keys=["architect"])
    engine = _engine(llm_caller=caller)
    state = await engine.run_turn(session_id=sid, user_prompt="설계해줘")

    context = engine._build_context_for_agent(state, Agent(key="critic", name="C", role="R"))
    joined = "\n".join(m["content"] for m in context)
    assert "연결 끊김" not in joined

    synthesis = engine._build_synthesis_prompt(state)[0]["content"]
    assert "연결 끊김" not in synthesis
    # 다만 누가 빠졌는지는 합성 프롬프트에 명시되어야 합니다.
    assert "architect" in synthesis


@pytest.mark.asyncio
async def test_synthesis_failure_still_produces_honest_artifacts():
    sid = await _make_session()
    engine = _engine(llm_caller=FakeLLMCaller(fail_keys=["orchestrator"]))

    state = await engine.run_turn(session_id=sid, user_prompt="설계해줘")

    titles = [a.title for a in state.artifacts]
    assert "합성 실패 (LLM 연결 끊김)" in titles
    summary = next(a for a in state.artifacts if a.artifact_type == "json")
    assert '"consensus_reached": false' in summary.content
    assert '"orchestrator"' in summary.content


@pytest.mark.asyncio
async def test_partial_stream_is_kept_alongside_the_failure_notice():
    """연결이 끊기기 전까지 도착한 본문은 버리지 않습니다."""
    sid = await _make_session()

    class HalfWayCaller(FakeLLMCaller):
        async def call_agent(self, agent, messages, custom_instructions="",
                             on_tool_call=None, on_chunk=None):
            if agent.key == "critic":
                if on_chunk:
                    await on_chunk("검토를 시작하겠습니다")
                raise LLMUnavailableError(agent, "APIError: 500")
            return await super().call_agent(
                agent, messages, custom_instructions, on_tool_call, on_chunk
            )

    engine = _engine(llm_caller=HalfWayCaller())
    state = await engine.run_turn(session_id=sid, user_prompt="검토해줘")

    critic = next(m for m in state.messages if m.sender_key == "critic")
    assert "검토를 시작하겠습니다" in critic.content
    assert "연결 끊김" in critic.content
    assert critic.msg_type == "error"
