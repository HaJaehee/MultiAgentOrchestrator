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
                             on_tool_call=None, on_chunk=None, session_id=None):
            if agent.key == "critic":
                if on_chunk:
                    await on_chunk("검토를 시작하겠습니다")
                raise LLMUnavailableError(agent, "APIError: 500")
            return await super().call_agent(
                agent, messages, custom_instructions, on_tool_call, on_chunk, session_id
            )

    engine = _engine(llm_caller=HalfWayCaller())
    state = await engine.run_turn(session_id=sid, user_prompt="검토해줘")

    critic = next(m for m in state.messages if m.sender_key == "critic")
    assert "검토를 시작하겠습니다" in critic.content
    assert "연결 끊김" in critic.content
    assert critic.msg_type == "error"


# --------------------------------------------------------------- 5. 엔드포인트 400

def test_consecutive_same_role_messages_are_merged():
    """Anthropic·Gemini·일부 OpenAI 호환 셔임은 role 이 교대하지 않으면 400 을 냅니다."""
    from app.agents.llm import merge_consecutive_roles

    merged = merge_consecutive_roles([
        {"role": "system", "content": "s"},
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": "c"},
        {"role": "user", "content": "d"},
        {"role": "user", "content": "e"},
    ])
    assert [m["role"] for m in merged] == ["system", "user", "assistant", "user"]
    assert merged[1]["content"] == "a\n\nb"
    assert merged[3]["content"] == "d\n\ne"


def test_merge_leaves_tool_call_messages_alone():
    """도구 호출이 얽힌 메시지를 합치면 tool_call_id 짝이 깨집니다."""
    from app.agents.llm import merge_consecutive_roles

    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1"}]},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t2"}]},
        {"role": "tool", "tool_call_id": "t1", "content": "out1"},
        {"role": "tool", "tool_call_id": "t2", "content": "out2"},
    ]
    assert merge_consecutive_roles(messages) == messages


@pytest.mark.asyncio
async def test_real_debate_context_alternates_roles():
    """실제 토론 전사가 엔드포인트에 나갈 때 role 이 교대해야 합니다."""
    from itertools import groupby
    from app.agents.llm import fit_context_window, merge_consecutive_roles

    sid = await _make_session(max_rounds=2)
    engine = _engine(llm_caller=FakeLLMCaller())
    state = await engine.run_turn(session_id=sid, user_prompt="설계해줘")

    critic = _fixed_pool().get("critic")
    context = engine._build_context_for_agent(state, critic)
    raw = [{"role": "system", "content": "sys"}] + context
    # 다듬기 전에는 user 가 연달아 나옵니다 (이것이 400 의 원인이었습니다).
    assert max(len(list(g)) for _, g in groupby(m["role"] for m in raw)) > 1

    sent = merge_consecutive_roles(fit_context_window(critic, raw))
    assert max(len(list(g)) for _, g in groupby(m["role"] for m in sent)) == 1


def test_context_window_drops_oldest_middle_messages():
    """한도를 넘으면 가운데를 오래된 것부터 덜어내고, 앞뒤는 남깁니다."""
    from app.agents.llm import fit_context_window

    agent = Agent(key="architect", name="A", role="R", model="fake/model",
                  max_context_window=2000, max_tokens=1000)
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "목표"}]
    messages += [{"role": "user", "content": f"발언 {i} " + "가" * 400} for i in range(10)]
    messages += [{"role": "user", "content": "이번 차례입니다"}]

    fitted = fit_context_window(agent, messages)

    assert len(fitted) < len(messages)
    assert fitted[0]["content"] == "sys"
    assert fitted[1]["content"] == "목표"
    assert fitted[-1]["content"] == "이번 차례입니다"
    assert "컨텍스트 한도로 생략" in fitted[2]["content"]
    # 남은 것은 최근 발언이어야 합니다.
    assert "발언 9" in fitted[-2]["content"]


def test_context_window_leaves_short_conversations_untouched():
    from app.agents.llm import fit_context_window

    agent = Agent(key="architect", name="A", role="R", model="fake/model",
                  max_context_window=128000, max_tokens=4096)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "짧은 요청"},
        {"role": "assistant", "content": "짧은 답"},
        {"role": "user", "content": "이번 차례"},
    ]
    assert fit_context_window(agent, messages) == messages


@pytest.mark.asyncio
async def test_synthesis_prompt_is_bounded_by_context_window():
    """합성 프롬프트는 전사 전체가 user 메시지 하나에 들어가는 자리입니다.

    메시지 단위로 덜어내는 `fit_context_window()` 로는 손댈 수 없으므로,
    만드는 쪽에서 크기를 정해야 합니다.
    """
    sid = await _make_session(max_rounds=3)
    long_reply = "가" * 3000
    engine = _engine(llm_caller=FakeLLMCaller(replies={
        "architect": long_reply, "coder": long_reply, "critic": long_reply,
    }))
    state = await engine.run_turn(session_id=sid, user_prompt="설계해줘")

    tight = Agent(key="orchestrator", name="O", role="R", model="fake/model",
                  max_context_window=4000, max_tokens=1000)
    roomy = Agent(key="orchestrator", name="O", role="R", model="fake/model",
                  max_context_window=128000, max_tokens=4096)

    bounded = engine._build_synthesis_prompt(state, tight)[0]["content"]
    full = engine._build_synthesis_prompt(state, roomy)[0]["content"]

    assert len(bounded) < len(full)
    assert "컨텍스트 한도로 생략" in bounded
    # 잘라도 지시문은 남아야 보고서 형식이 유지됩니다.
    assert "Mermaid 다이어그램" in bounded


# --------------------------------------------------------------- 6. 도구 루프 한도

def test_tool_iteration_default_is_consistent_everywhere():
    """코드 기본값·conf.toml·문서가 어긋나면 설정이 안 먹는 것처럼 보입니다."""
    import re
    import pathlib
    from app.config import AgentConfig

    assert Agent(key="k", name="n", role="r").max_tool_iterations == 30
    assert AgentConfig(name="n", role="r").max_tool_iterations == 30

    for name in ("conf.toml", "conf.example.toml"):
        path = pathlib.Path(name)
        if not path.exists():
            continue
        found = re.search(r"^max_tool_iterations\s*=\s*(\d+)", path.read_text(encoding="utf-8"), re.M)
        assert found and int(found.group(1)) == 30, f"{name} 의 값이 코드 기본값과 다릅니다"


@pytest.mark.asyncio
async def test_tool_loop_runs_to_the_limit_then_fails_honestly():
    """한도를 다 쓰면 자리표시자 답변이 아니라 실패를 올려야 합니다."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from app.agents.llm import LLMCaller

    calls = {"n": 0}

    def _message():
        tc = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(name="read_file", arguments='{"path": "a.txt"}'),
        )
        return SimpleNamespace(
            content="", tool_calls=[tc], model_dump=lambda: {"role": "assistant", "tool_calls": []}
        )

    async def fake_acompletion(**kwargs):
        if kwargs.get("stream"):
            raise RuntimeError("streaming unsupported")   # 비스트리밍 경로로 떨어뜨립니다
        calls["n"] += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=_message())])

    agent = Agent(key="coder", name="Coder", role="Engineer",
                  model="fake/model", api_key="k", max_tool_iterations=4)

    caller = LLMCaller()
    caller.mcp_manager = SimpleNamespace(
        get_openai_tools_for_servers=lambda servers: [
            {"type": "function", "function": {"name": "read_file", "parameters": {}}}
        ],
        execute_tool=AsyncMock(return_value=("파일 내용", "success")),
    )

    with patch("litellm.acompletion", side_effect=fake_acompletion):
        with pytest.raises(LLMUnavailableError) as excinfo:
            await caller.call_agent(agent, [{"role": "user", "content": "읽어줘"}])

    assert calls["n"] == 4, "한도만큼 정확히 돌아야 합니다"
    assert "max_tool_iterations" in str(excinfo.value)


# --------------------------------------------------------------- 7. 작업 공간

def test_workspace_paths_are_absolute_and_shared_by_every_server():
    """filesystem 과 sandbox 가 같은 폴더를 봐야 합니다.

    상대 경로를 넘기면 받는 쪽이 각자의 cwd 로 resolve 합니다. node 로 뜨는
    filesystem 과 python 으로 뜨는 sandbox 는 다른 프로세스이므로, 같은
    './workspace' 를 줘도 서로 다른 폴더를 볼 수 있습니다.
    """
    from pathlib import Path
    from app.config import get_config

    cfg = get_config()
    servers = cfg.mcp_servers

    filesystem_root = Path(servers["filesystem"].args[-1])
    sandbox_root = Path(servers["sandbox"].env["SANDBOX_WORKSPACE"])
    git_root = Path(servers["git"].args[-1])

    assert filesystem_root.is_absolute(), "filesystem 이 상대 경로를 받고 있습니다"
    assert sandbox_root.is_absolute(), "sandbox 가 상대 경로를 받고 있습니다"
    assert filesystem_root == sandbox_root == git_root
    assert servers["memory"].env["MEMORY_GRAPH_DIR"].startswith(str(filesystem_root))


def test_default_workspace_is_the_project_root_one():
    from app.config import PROJECT_ROOT, resolve_workspace_dir

    assert resolve_workspace_dir(None) == PROJECT_ROOT / "workspace"
    assert resolve_workspace_dir("") == PROJECT_ROOT / "workspace"
    # 상대 경로는 cwd 가 아니라 프로젝트 루트 기준입니다.
    assert resolve_workspace_dir("./data") == (PROJECT_ROOT / "data").resolve()


def test_mcp_servers_reresolve_for_a_new_workspace(tmp_path):
    """작업 공간을 바꾸면 원문을 다시 풀어 모든 서버에 새 경로가 들어갑니다."""
    import os
    from app.config import get_config

    cfg = get_config()
    previous = os.environ.get("WORKSPACE_DIR")
    try:
        servers = cfg.mcp_servers_for_workspace(tmp_path)
        assert servers["filesystem"].args[-1] == str(tmp_path)
        assert servers["sandbox"].env["SANDBOX_WORKSPACE"] == str(tmp_path)
        assert servers["git"].args[-1] == str(tmp_path)
        # 비활성 서버는 띄우지 않으므로 빠져야 합니다.
        assert "fetch" not in servers
    finally:
        if previous is not None:
            os.environ["WORKSPACE_DIR"] = previous


@pytest.mark.asyncio
async def test_concurrent_debates_in_different_workspaces_are_refused(tmp_path):
    """MCP 서버는 프로세스 전체가 공유합니다. 조용히 남의 폴더를 쓰느니 거절합니다."""
    from app.orchestration.runner import DebateRunner, WorkspaceConflictError

    class Never(FakeLLMCaller):
        async def call_agent(self, *args, **kwargs):
            await asyncio.sleep(30)  # 끝나지 않는 토론

    sid_a = await _make_session()
    sid_b = await _make_session()
    runner = DebateRunner(_engine(llm_caller=Never()))

    run_a = runner.start(sid_a, "첫 대화", str(tmp_path / "ws-a"))
    try:
        with pytest.raises(WorkspaceConflictError) as excinfo:
            runner.start(sid_b, "둘째 대화", str(tmp_path / "ws-b"))
        assert "작업 공간" in str(excinfo.value)

        # 같은 작업 공간이면 막지 않습니다.
        run_c = runner.start(sid_b, "둘째 대화", str(tmp_path / "ws-a"))
        assert run_c.status == "running"
        await runner.cancel(sid_b)
    finally:
        await runner.cancel(sid_a)
    assert run_a.status == "cancelled"


@pytest.mark.asyncio
async def test_session_workspace_is_applied_at_turn_start(tmp_path, monkeypatch):
    """세션에 지정된 폴더로 MCP 서버를 맞춘 뒤에 토론이 시작되어야 합니다."""
    from app.orchestration import engine as engine_module

    switched = []

    class FakeManager:
        workspace = tmp_path / "current"

        async def set_workspace(self, path):
            switched.append(path)
            FakeManager.workspace = path
            return path

    monkeypatch.setattr(engine_module, "get_mcp_manager", lambda: FakeManager())

    sid = await _make_session()
    session_factory = get_session_factory()
    async with session_factory() as db:
        row = await db.get(SessionModel, sid)
        row.workspace_dir = str(tmp_path / "chosen")
        await db.commit()

    await _engine(llm_caller=FakeLLMCaller()).run_turn(session_id=sid, user_prompt="설계해줘")

    assert switched == [(tmp_path / "chosen").resolve()]


# ------------------------------------------------------- 8. 대화별 도구 스코프


@pytest.mark.asyncio
async def test_every_tool_call_in_a_turn_carries_its_session_id():
    """토론 중의 MCP 호출에는 그 대화의 식별자가 스코프로 붙어야 합니다.

    스코프가 빠지면 서버는 어느 대화의 호출인지 알 수 없고, 프로세스를 공유하는
    다음 대화가 이전 대화의 상태(지식 그래프)를 그대로 읽습니다. 스코프를 모델의
    인자에 맡기지 않고 호스트가 매 호출에 싣는 것이 이 설계의 요점입니다.
    """
    caller = FakeLLMCaller()
    sid = await _make_session()

    await _engine(llm_caller=caller).run_turn(session_id=sid, user_prompt="설계해줘")

    assert caller.scopes, "발언이 한 번도 없었습니다"
    assert set(caller.scopes) == {sid}, f"스코프가 새거나 비었습니다: {set(caller.scopes)}"


@pytest.mark.asyncio
async def test_two_sessions_do_not_share_a_tool_scope():
    """대화가 다르면 스코프도 달라야 합니다."""
    caller = FakeLLMCaller()
    sid_a = await _make_session()
    sid_b = await _make_session()
    engine = _engine(llm_caller=caller)

    await engine.run_turn(session_id=sid_a, user_prompt="첫 대화")
    first_round = len(caller.scopes)
    await engine.run_turn(session_id=sid_b, user_prompt="둘째 대화")

    assert set(caller.scopes[:first_round]) == {sid_a}
    assert set(caller.scopes[first_round:]) == {sid_b}
