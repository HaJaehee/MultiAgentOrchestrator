"""토론이 돌고 있는 동안 MCP 서버 구성을 잠그는 화면 쪽 규칙.

MCP 서버는 프로세스 전체가 공유합니다. 진행 중인 토론은 지금 그 서버들의 도구를
쓰고 있으므로, 도중에 내리거나 다시 띄우면 그 토론의 도구 호출이 실패하거나 —
더 나쁘게는 — 새로 뜬 다른 구성의 서버가 응답합니다. 이 대화든 다른 대화든
마찬가지라, 하나라도 돌고 있으면 잠급니다.

여기서는 화면 컨트롤이 실제로 잠기는지와, 잠긴 상태에서 누른 조작이 conf.toml 에
닿지 않는지를 봅니다.
"""

from pathlib import Path
from typing import List

import pytest

from app.ui.components import roster as roster_module
from app.ui.components.roster import AgentRosterControl


class _FakeRunner:
    """돌고 있는 대화 목록만 흉내 내는 러너."""

    def __init__(self, running: List[str]):
        self.running = list(running)

    def running_sessions(self) -> List[str]:
        return list(self.running)

    def is_running(self, session_id: str) -> bool:
        return session_id in self.running

    def running_elsewhere(self, session_id: str):
        return []


class _FakeManager:
    """서버를 실제로 띄우지 않는 MCP 매니저."""

    def __init__(self):
        self.reloads = 0
        self.workspace = Path(".").resolve()

    async def reload_from_config(self):
        self.reloads += 1
        return {}

    def connection_status(self):
        return {}


@pytest.fixture()
def roster(monkeypatch):
    runner = _FakeRunner([])
    manager = _FakeManager()
    monkeypatch.setattr(roster_module, "get_debate_runner", lambda: runner)
    monkeypatch.setattr(roster_module, "get_mcp_manager", lambda: manager)
    monkeypatch.setattr(roster_module.ui, "notify", lambda *a, **k: None)

    control = AgentRosterControl()
    control.build_ui()
    return control, runner, manager


def test_controls_are_open_when_nothing_is_running(roster):
    control, _, _ = roster
    assert control.mcp_locked is False
    assert control.mcp_add_btn._props.get("disable") is None
    assert control.mcp_lock_hint.visible is False


def test_a_running_debate_locks_the_controls(roster):
    control, runner, _ = roster

    runner.running = ["session-a", "session-b"]
    control.refresh_mcp_lock()

    assert control.mcp_locked is True
    assert "2개" in control.mcp_lock_reason
    assert control.mcp_add_btn._props.get("disable") is True
    assert control.mcp_lock_hint.visible is True
    assert control.mcp_lock_badge.visible is True


@pytest.mark.asyncio
async def test_locked_toggle_never_reaches_conf_toml(roster, monkeypatch):
    control, runner, manager = roster
    writes: List[tuple] = []
    monkeypatch.setattr(
        roster_module, "set_mcp_server_enabled_in_conf_file",
        lambda *args, **kwargs: writes.append(args),
    )

    runner.running = ["session-a"]
    await control._on_mcp_toggle("filesystem", False)

    assert writes == [], "토론 중에는 설정 파일을 건드리면 안 됩니다"
    assert manager.reloads == 0, "토론 중에는 서버를 다시 띄우면 안 됩니다"


@pytest.mark.asyncio
async def test_toggle_writes_and_reloads_once_the_debate_is_over(roster, monkeypatch):
    control, runner, manager = roster
    writes: List[tuple] = []
    monkeypatch.setattr(
        roster_module, "set_mcp_server_enabled_in_conf_file",
        lambda *args, **kwargs: writes.append(args),
    )

    runner.running = ["session-a"]
    control.refresh_mcp_lock()
    runner.running = []            # 마지막 라운드까지 끝나 아티팩트가 나왔습니다
    control.refresh_mcp_lock()
    assert control.mcp_locked is False

    await control._on_mcp_toggle("filesystem", False)

    assert [args[:2] for args in writes] == [("filesystem", False)]
    assert manager.reloads == 1


@pytest.mark.asyncio
async def test_dialogs_refuse_to_open_while_a_debate_runs(roster, monkeypatch):
    """추가·삭제 창은 잠긴 동안 아예 열리지 않습니다."""
    control, runner, _ = roster
    opened: List[str] = []
    monkeypatch.setattr(roster_module.ui, "dialog", lambda *a, **k: opened.append("dialog"))

    runner.running = ["session-a"]
    control._open_mcp_add_dialog()
    control._open_mcp_delete_dialog("filesystem")

    assert opened == []
