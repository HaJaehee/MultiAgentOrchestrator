"""도구를 여러 번 부른 발언의 본문이 온전히 남는가.

증상: 답변이 길고 도구 호출이 많으면, 발언이 끝나는 순간 카드에서 글이 통째로
사라졌습니다. 접힌 것이 아니라 정말로 없어졌습니다 (새로고침해도 돌아오지
않았습니다 — DB 에도 그렇게 들어갔으니까요).

원인은 `_run_litellm_loop` 이 **마지막 판**의 content 만 돌려준 것입니다. 도구를
부르는 판에서 나온 텍스트("먼저 파일을 확인하겠습니다", 도구 결과에 대한 관측과
판단)는 화면에 실시간으로 흘러갔는데, 반환값에는 담기지 않았습니다. 그 반환값이
카드를 통째로 덮어쓰므로, 도구를 많이 부를수록 사라지는 양이 커졌습니다.
도구를 안 부르면 판이 하나뿐이라 마지막 판 == 전체였고, 그래서 짧은 답변에서는
아무 문제도 보이지 않았습니다.

여기서 지키려는 것.

1. 도구를 부른 판의 텍스트도 반환값에 남는다.
2. 화면에 흘려보낸 것(`on_chunk` 누적)과 반환값이 같은 글을 담는다.
3. 확정본이 비어 있으면 흘려보낸 것을 버리지 않는다.
"""

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import app.agents.llm as llm_module
from app.agents.base import Agent
from app.agents.llm import LLMCaller


def _agent(**kw: Any) -> Agent:
    return Agent(key="coder", name="Coder", role="Impl", api_key="sk-test", **kw)


class _FakeMCPManager:
    """도구는 늘 성공하고 짧은 결과를 돌려줍니다."""

    def get_openai_tools_for_servers(self, servers: List[str]) -> List[Dict[str, Any]]:
        return [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]

    async def execute_tool(self, name, args, scope=None, actor=None):
        return f"[{name} 결과]", "success"


def _tool_call(idx: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"call_{idx}",
        function=SimpleNamespace(name="read_file", arguments='{"path": "a.py"}'),
    )


class _FakeMessage(SimpleNamespace):
    """litellm 이 재조립해 주는 메시지 흉내. model_dump 까지 있어야 루프가 씁니다."""

    def model_dump(self) -> Dict[str, Any]:
        return {"role": "assistant", "content": self.content}


def _install_fake_litellm(monkeypatch, turns: List[Dict[str, Any]]) -> None:
    """`turns` 를 순서대로 돌려주는 가짜 엔드포인트를 답니다.

    각 turn 은 {"content": str, "tools": int} — 그 판에서 모델이 내놓은 본문과
    도구 호출 개수입니다.
    """
    remaining = list(turns)

    async def fake_acompletion(**kwargs):
        turn = remaining.pop(0)

        async def _stream():
            for ch in turn["content"]:
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=ch))]
                )

        # stream_chunk_builder 가 이 turn 을 그대로 재조립한 것으로 칩니다.
        fake_acompletion.current = turn
        return _stream()

    def fake_stream_chunk_builder(chunks, messages=None):
        turn = fake_acompletion.current
        tool_calls = [_tool_call(i) for i in range(turn["tools"])] or None
        message = _FakeMessage(content=turn["content"], tool_calls=tool_calls)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr(llm_module.litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(llm_module.litellm, "stream_chunk_builder", fake_stream_chunk_builder)


@pytest.mark.asyncio
async def test_text_from_tool_calling_turns_survives(monkeypatch):
    """도구를 부른 판의 텍스트가 최종 발언에 남는다."""
    _install_fake_litellm(monkeypatch, [
        {"content": "먼저 파일을 확인하겠습니다.", "tools": 1},
        {"content": "읽었습니다. 이제 테스트도 봅니다.", "tools": 1},
        {"content": "정리하면 이렇습니다.", "tools": 0},
    ])
    caller = LLMCaller(mcp_manager=_FakeMCPManager())

    streamed: List[str] = []
    content, logs = await caller.call_agent(
        _agent(), [{"role": "user", "content": "봐줘"}],
        on_chunk=lambda d: streamed.append(d),
    )

    assert "먼저 파일을 확인하겠습니다." in content
    assert "읽었습니다. 이제 테스트도 봅니다." in content
    assert "정리하면 이렇습니다." in content
    assert len(logs) == 2


@pytest.mark.asyncio
async def test_returned_content_matches_what_was_streamed(monkeypatch):
    """화면에 흘려보낸 글과 확정본이 같은 내용을 담는다.

    확정본이 카드를 덮어쓰므로, 여기가 어긋나면 사람이 읽던 글이 없어집니다.
    이어 붙이는 빈 줄만 무시하고 비교합니다.
    """
    _install_fake_litellm(monkeypatch, [
        {"content": "A" * 300, "tools": 2},
        {"content": "B" * 300, "tools": 1},
        {"content": "C" * 50, "tools": 0},
    ])
    caller = LLMCaller(mcp_manager=_FakeMCPManager())

    streamed: List[str] = []
    content, _ = await caller.call_agent(
        _agent(), [{"role": "user", "content": "봐줘"}],
        on_chunk=lambda d: streamed.append(d),
    )

    assert "".join(streamed) == content.replace("\n\n", "")
    assert len(content.replace("\n\n", "")) == 650


@pytest.mark.asyncio
async def test_empty_final_turn_keeps_earlier_text(monkeypatch):
    """마지막 판이 빈손이어도 앞선 판의 글은 남는다."""
    _install_fake_litellm(monkeypatch, [
        {"content": "여기까지가 분석입니다.", "tools": 1},
        {"content": "", "tools": 0},
    ])
    caller = LLMCaller(mcp_manager=_FakeMCPManager())

    content, _ = await caller.call_agent(_agent(), [{"role": "user", "content": "봐줘"}])

    assert content.strip() == "여기까지가 분석입니다."
