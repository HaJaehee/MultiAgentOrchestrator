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


class _StubAgent:
    def __init__(self, key: str):
        self.key = key


class _StubPool:
    """`list_all()` 만 흉내 내는 에이전트 풀."""

    def __init__(self, keys: List[str]):
        self.keys = list(keys)

    def list_all(self):
        return [_StubAgent(k) for k in self.keys]


class _StubConfig:
    """MCP 서버가 없는 설정."""

    mcp_servers: dict = {}
    enabled_mcp_servers: dict = {}


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
    """추가·삭제·도구 할당 창은 잠긴 동안 아예 열리지 않습니다."""
    control, runner, _ = roster
    opened: List[str] = []
    monkeypatch.setattr(roster_module.ui, "dialog", lambda *a, **k: opened.append("dialog"))

    runner.running = ["session-a"]
    control._open_mcp_add_dialog()
    control._open_mcp_delete_dialog("filesystem")
    control._open_agent_tools_dialog("orchestrator")

    assert opened == []


@pytest.mark.asyncio
async def test_agent_tool_change_does_not_restart_the_servers(roster):
    """도구 할당은 서버 구성을 바꾸지 않습니다. 다시 띄우면 몇 초만 버립니다."""
    control, _, manager = roster
    written: List[str] = []

    await control._apply_conf_change(
        lambda: written.append("conf"), "저장했습니다.", restart_servers=False
    )

    assert written == ["conf"]
    assert manager.reloads == 0


@pytest.mark.asyncio
async def test_server_change_still_restarts_the_servers(roster):
    control, _, manager = roster

    await control._apply_conf_change(lambda: None, "저장했습니다.", restart_servers=True)

    assert manager.reloads == 1


# --------------------------------------------------------------- conf.toml 다시 읽기


def test_sync_keeps_choices_prunes_removed_and_turns_new_ones_on(roster):
    """설정을 다시 읽은 뒤 선택 상태를 지금 있는 에이전트에 맞춥니다."""
    control, _, _ = roster
    control.selected_agents = {"orchestrator": True, "architect": False, "gone": True}
    control.agent_pool = _StubPool(["orchestrator", "architect", "researcher"])

    control.sync_agents_with_pool()

    assert control.selected_agents == {
        "orchestrator": True,      # 켜 둔 것은 그대로
        "architect": False,        # 끈 것도 그대로
        "researcher": True,        # 새로 생긴 것은 켠 채로
    }
    assert "gone" not in control.selected_agents, "사라진 에이전트는 목록에서도 빠져야 합니다"


@pytest.mark.asyncio
async def test_reload_is_refused_while_a_debate_runs(roster, monkeypatch):
    control, runner, manager = roster
    rereads: List[str] = []
    reloaded: List[int] = []

    def get_config(*args, **kwargs):
        # 화면을 다시 그리며 설정을 **읽는** 것은 정상입니다. 막아야 하는 것은
        # 파일을 다시 읽어 전역 설정을 갈아 끼우는 쪽(reload=True)입니다.
        if kwargs.get("reload"):
            rereads.append("reload")
        return _StubConfig()

    monkeypatch.setattr(roster_module, "get_config", get_config)
    monkeypatch.setattr(roster_module, "reload_agent_pool", lambda: reloaded.append(1))

    runner.running = ["session-a"]
    await control._on_reload_conf()

    assert rereads == [], "토론 중에는 설정 파일을 다시 읽지 않습니다"
    assert reloaded == []
    assert manager.reloads == 0


@pytest.mark.asyncio
async def test_broken_conf_leaves_the_running_setup_untouched(roster, monkeypatch):
    """설정 파일이 깨져 있으면 아무것도 바꾸지 않고 돌아옵니다."""
    control, _, manager = roster
    original_pool = control.agent_pool

    def get_config(*args, **kwargs):
        if kwargs.get("reload"):
            raise ValueError("TOML 문법 오류")
        return _StubConfig()

    reloaded: List[int] = []
    monkeypatch.setattr(roster_module, "get_config", get_config)
    monkeypatch.setattr(roster_module, "reload_agent_pool", lambda: reloaded.append(1))

    await control._on_reload_conf()

    assert reloaded == [], "읽지도 못한 설정으로 풀을 다시 만들면 안 됩니다"
    assert control.agent_pool is original_pool
    assert manager.reloads == 0


@pytest.mark.asyncio
async def test_servers_restart_only_when_their_config_changed(roster, monkeypatch):
    """에이전트만 바뀌었으면 MCP 서버를 다시 띄우지 않습니다 (몇 초를 버립니다)."""
    control, _, manager = roster
    monkeypatch.setattr(roster_module, "get_config", lambda *a, **k: _StubConfig())
    monkeypatch.setattr(roster_module, "reload_agent_pool",
                        lambda: _StubPool(["orchestrator", "researcher"]))

    monkeypatch.setattr(control, "_mcp_fingerprint", lambda: {"git": ("python", (), ())})
    await control._on_reload_conf()
    assert manager.reloads == 0

    fingerprints = iter([{"git": ("python", (), ())}, {"git": ("node", (), ())}])
    monkeypatch.setattr(control, "_mcp_fingerprint", lambda: next(fingerprints))
    await control._on_reload_conf()
    assert manager.reloads == 1, "MCP 구성이 바뀌었으면 서버도 맞춰야 합니다"


@pytest.mark.asyncio
async def test_reload_picks_up_a_newly_added_agent(roster, monkeypatch):
    control, _, _ = roster
    monkeypatch.setattr(roster_module, "get_config", lambda *a, **k: _StubConfig())
    monkeypatch.setattr(roster_module, "reload_agent_pool",
                        lambda: _StubPool(["orchestrator", "architect", "researcher"]))
    monkeypatch.setattr(control, "_mcp_fingerprint", lambda: {})
    control.selected_agents = {"orchestrator": True, "architect": True}

    await control._on_reload_conf()

    # 새 에이전트가 선택 목록에 들어오고, 켜진 채로 나옵니다.
    assert control.selected_agents["researcher"] is True
    assert set(control.selected_agents) == {"orchestrator", "architect", "researcher"}
