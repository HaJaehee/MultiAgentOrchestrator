"""화면에서 MCP 서버를 추가·삭제·on/off 할 때 conf.toml 이 어떻게 바뀌는지.

이 파일이 지키려는 것은 두 가지입니다.

1. **주석이 살아남는다.** conf.toml 의 절반은 어떤 서버가 무엇을 하고 왜 꺼져
   있는지를 적어 둔 글입니다. 파이썬 표준 라이브러리에는 TOML 기록기가 없어
   통째로 다시 쓰면 그게 전부 사라지므로, 줄 단위로 고칩니다.
2. **`${VAR}` 가 그대로 남는다.** 화면이 보는 값은 이미 환경변수가 풀린 값입니다.
   그 값을 되쓰면 다른 기계에서 열었을 때 남의 절대 경로가 박혀 있게 됩니다.
"""

import sys
import tomllib
from pathlib import Path

import pytest

from app.config import (
    add_mcp_server_to_conf_file,
    get_config,
    remove_mcp_server_from_conf_file,
    set_agent_allowed_mcp_servers_in_conf_file,
    set_mcp_server_enabled_in_conf_file,
)
from app.mcp.manager import MCPManager

FIXTURE_SERVER = str(Path(__file__).parent / "fixtures" / "stateful_mcp_server.py")

PROJECT_CONF = Path(__file__).resolve().parent.parent / "conf.toml"

SAMPLE = """[app]
port = 8000

# --- 파일 읽기/쓰기 (툴 11종) ---------------------------------------------
# 이 주석은 filesystem 서버를 설명합니다.
[mcp_servers.filesystem]
command = "${NODE_BIN:-node}"
args = ["${MCP_NODE_HOME:-./mcp_node}/server.js", "${WORKSPACE_DIR:-./workspace}"]
enabled = true

# 이 주석은 fetch 서버를 설명합니다.
[mcp_servers.fetch]
command = "python"
args = ["-m", "mcp_server_fetch"]

[agents.orchestrator]
name = "Master Orchestrator"
allowed_mcp_servers = ["filesystem"]

# 도구 목록이 시스템 프롬프트 뒤에 오는 배치. 프롬프트 안에 대괄호 줄이 있어서,
# 여러 줄 문자열을 건너뛰지 못하면 섹션 경계를 여기서 잘못 끊습니다.
[agents.critic]
name = "Critic"
system_prompt = \"\"\"
[검토 항목]
- 보안 취약점
\"\"\"
allowed_mcp_servers = [
    "filesystem",
    "git",
]
"""


@pytest.fixture()
def conf(tmp_path: Path) -> Path:
    path = tmp_path / "conf.toml"
    path.write_text(SAMPLE, encoding="utf-8")
    return path


def _load(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


# --------------------------------------------------------------- on / off


def test_toggle_rewrites_only_the_enabled_line(conf: Path):
    set_mcp_server_enabled_in_conf_file("filesystem", False, conf)

    data = _load(conf)
    assert data["mcp_servers"]["filesystem"]["enabled"] is False
    # 나머지 값과 주석은 그대로입니다.
    assert data["mcp_servers"]["filesystem"]["command"] == "${NODE_BIN:-node}"
    assert "이 주석은 filesystem 서버를 설명합니다." in conf.read_text(encoding="utf-8")


def test_toggle_adds_the_enabled_line_when_missing(conf: Path):
    """enabled 를 적지 않은 섹션(기본값 true)도 끌 수 있어야 합니다."""
    set_mcp_server_enabled_in_conf_file("fetch", False, conf)

    data = _load(conf)
    assert data["mcp_servers"]["fetch"]["enabled"] is False
    # 다음 섹션을 침범하지 않았습니다.
    assert data["agents"]["orchestrator"]["name"] == "Master Orchestrator"


def test_toggle_of_unknown_server_is_an_error(conf: Path):
    with pytest.raises(KeyError):
        set_mcp_server_enabled_in_conf_file("nope", True, conf)


# --------------------------------------------------------------- 추가


def test_added_server_lands_in_the_mcp_group_with_placeholders_intact(conf: Path):
    add_mcp_server_to_conf_file(
        "everything",
        "${PYTHON_BIN:-python}",
        ["-m", "server_everything", "${WORKSPACE_DIR:-./workspace}"],
        {"API_KEY": "${SOME_API_KEY}"},
        True,
        conf,
    )

    data = _load(conf)
    server = data["mcp_servers"]["everything"]
    assert server["command"] == "${PYTHON_BIN:-python}"
    assert server["args"][-1] == "${WORKSPACE_DIR:-./workspace}"
    assert server["env"] == {"API_KEY": "${SOME_API_KEY}"}
    assert server["enabled"] is True

    text = conf.read_text(encoding="utf-8")
    # MCP 서버들 사이에 들어가야 합니다. 파일 끝(에이전트 뒤)이 아니라.
    assert text.index("[mcp_servers.everything]") < text.index("[agents.orchestrator]")
    # 다음 섹션에 붙어 있던 주석을 가로채지 않았습니다.
    assert text.index("# 이 주석은 fetch 서버를 설명합니다.") < text.index("[mcp_servers.fetch]")


def test_add_rejects_duplicates_and_bad_input(conf: Path):
    with pytest.raises(ValueError):
        add_mcp_server_to_conf_file("filesystem", "node", [], {}, True, conf)
    with pytest.raises(ValueError):
        add_mcp_server_to_conf_file("has space", "node", [], {}, True, conf)
    with pytest.raises(ValueError):
        add_mcp_server_to_conf_file("ok_name", "   ", [], {}, True, conf)
    with pytest.raises(ValueError):
        add_mcp_server_to_conf_file("ok_name", "node", [], {"bad key": "v"}, True, conf)
    # 실패한 시도가 파일을 건드리지 않았습니다.
    assert _load(conf)["mcp_servers"].keys() == {"filesystem", "fetch"}


def test_quotes_and_backslashes_survive_the_round_trip(conf: Path):
    add_mcp_server_to_conf_file(
        "winpath",
        r"C:\Program Files\node\node.exe",
        [r'--tag="alpha"', r"C:\tools\server.js"],
        {},
        True,
        conf,
    )
    server = _load(conf)["mcp_servers"]["winpath"]
    assert server["command"] == r"C:\Program Files\node\node.exe"
    assert server["args"] == [r'--tag="alpha"', r"C:\tools\server.js"]


# --------------------------------------------------------------- 삭제


def test_remove_drops_the_section_and_keeps_the_neighbours(conf: Path):
    remove_mcp_server_from_conf_file("fetch", conf)

    data = _load(conf)
    assert "fetch" not in data["mcp_servers"]
    assert "filesystem" in data["mcp_servers"]
    assert data["agents"]["orchestrator"]["name"] == "Master Orchestrator"


def test_add_then_remove_leaves_the_file_byte_identical(conf: Path):
    before = conf.read_text(encoding="utf-8")
    for _ in range(3):
        add_mcp_server_to_conf_file("temp_server", "node", ["a.js"], {}, True, conf)
        remove_mcp_server_from_conf_file("temp_server", conf)
    assert conf.read_text(encoding="utf-8") == before


@pytest.mark.skipif(not PROJECT_CONF.is_file(), reason="conf.toml 이 없는 설치")
def test_real_conf_survives_an_add_and_remove_cycle(tmp_path: Path):
    """주석이 빽빽한 실제 설정 파일로도 같은 결과여야 합니다."""
    path = tmp_path / "conf.toml"
    path.write_text(PROJECT_CONF.read_text(encoding="utf-8"), encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    add_mcp_server_to_conf_file("probe", "node", ["probe.js"], {"K": "v"}, False, path)
    assert _load(path)["mcp_servers"]["probe"]["enabled"] is False
    remove_mcp_server_from_conf_file("probe", path)

    assert path.read_text(encoding="utf-8") == before


def test_editing_another_file_does_not_swap_the_live_config(conf: Path):
    """앱이 읽고 있는 파일이 아닌 것을 고쳤다고 전역 설정이 갈아끼워지면 안 됩니다.

    그렇게 되면 에이전트 풀과 MCP 서버 목록이 통째로 그 파일 것으로 바뀝니다.
    """
    live = get_config()
    add_mcp_server_to_conf_file("probe", "node", [], {}, True, conf)
    assert get_config() is live
    assert "probe" not in get_config().mcp_servers


# --------------------------------------------------------------- 실제 서버에 반영


LIVE_CONF = """[app]
port = 8000

[mcp_servers.probe]
command = "{python}"
args = ["{server}"]
enabled = true

[agents.orchestrator]
name = "Master Orchestrator"
role = "Moderator"
model = "fake/model"
api_key = "test-key"
"""


@pytest.mark.asyncio
async def test_conf_edits_reach_the_running_servers(tmp_path: Path, monkeypatch):
    """파일만 고치고 끝나면 화면과 실제로 떠 있는 서버가 어긋납니다.

    화면의 on/off·추가·삭제가 거치는 경로 그대로 — conf.toml 을 고치고
    `reload_from_config()` 로 다시 띄우기 — 를 실제 MCP 서버로 확인합니다.
    """
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "ws"))
    conf = tmp_path / "conf.toml"
    conf.write_text(
        LIVE_CONF.format(python=sys.executable.replace("\\", "\\\\"),
                         server=FIXTURE_SERVER.replace("\\", "\\\\")),
        encoding="utf-8",
    )
    get_config(reload=True, config_path=conf)

    manager = MCPManager(get_config().enabled_mcp_servers)
    try:
        await manager.initialize()
        assert manager.clients["probe"].is_connected

        # 끄면 프로세스가 내려갑니다.
        set_mcp_server_enabled_in_conf_file("probe", False, conf)
        await manager.reload_from_config()
        assert "probe" not in manager.clients

        # 다시 켜면 올라옵니다.
        set_mcp_server_enabled_in_conf_file("probe", True, conf)
        await manager.reload_from_config()
        assert manager.clients["probe"].is_connected

        # 추가한 서버는 곧바로 도구를 내놓습니다.
        add_mcp_server_to_conf_file("probe2", sys.executable, [FIXTURE_SERVER], {}, True, conf)
        status = await manager.reload_from_config()
        assert status["probe2"]["connected"] is True
        assert manager.get_tools_for_servers(["probe2"])

        # 삭제하면 도구 목록에서도 사라집니다.
        remove_mcp_server_from_conf_file("probe2", conf)
        await manager.reload_from_config()
        assert "probe2" not in manager.clients
        assert manager.get_tools_for_servers(["probe2"]) == []
    finally:
        await manager.shutdown()
        # 전역 설정을 프로젝트 파일로 되돌립니다 (다른 테스트가 그것을 봅니다).
        if PROJECT_CONF.is_file():
            get_config(reload=True, config_path=PROJECT_CONF)


# --------------------------------------------------------------- 에이전트 도구 할당


def test_agent_tools_replace_an_existing_list(conf: Path):
    set_agent_allowed_mcp_servers_in_conf_file("orchestrator", ["filesystem", "fetch"], conf)

    data = _load(conf)
    assert data["agents"]["orchestrator"]["allowed_mcp_servers"] == ["filesystem", "fetch"]
    assert data["agents"]["orchestrator"]["name"] == "Master Orchestrator"


def test_agent_tools_survive_a_bracket_line_inside_the_system_prompt(conf: Path):
    """프롬프트 안의 `[검토 항목]` 을 섹션 헤더로 읽으면 목록이 중복 삽입됩니다.

    그러면 TOML 이 같은 키를 두 번 갖게 되어 파일 자체가 못 읽는 상태가 됩니다.
    """
    set_agent_allowed_mcp_servers_in_conf_file("critic", ["memory"], conf)

    data = _load(conf)   # 중복 키가 생겼다면 여기서 터집니다
    assert data["agents"]["critic"]["allowed_mcp_servers"] == ["memory"]
    assert "[검토 항목]" in data["agents"]["critic"]["system_prompt"]
    assert conf.read_text(encoding="utf-8").count("allowed_mcp_servers") == 2


def test_agent_tools_are_added_when_the_key_is_missing(conf: Path):
    """conf.toml 에 목록을 적지 않은 에이전트에게도 도구를 줄 수 있어야 합니다."""
    add_mcp_server_to_conf_file("extra", "node", ["x.js"], {}, True, conf)
    path_text = conf.read_text(encoding="utf-8")
    conf.write_text(path_text + '\n[agents.newbie]\nname = "Newbie"\n', encoding="utf-8")

    set_agent_allowed_mcp_servers_in_conf_file("newbie", ["extra"], conf)
    assert _load(conf)["agents"]["newbie"]["allowed_mcp_servers"] == ["extra"]


def test_agent_tools_can_be_emptied(conf: Path):
    set_agent_allowed_mcp_servers_in_conf_file("orchestrator", [], conf)
    assert _load(conf)["agents"]["orchestrator"]["allowed_mcp_servers"] == []


def test_agent_tools_reject_junk(conf: Path):
    with pytest.raises(KeyError):
        set_agent_allowed_mcp_servers_in_conf_file("nobody", ["filesystem"], conf)
    with pytest.raises(ValueError):
        set_agent_allowed_mcp_servers_in_conf_file("orchestrator", ["has space"], conf)
    # 실패한 시도가 파일을 건드리지 않았습니다.
    assert _load(conf)["agents"]["orchestrator"]["allowed_mcp_servers"] == ["filesystem"]


@pytest.mark.skipif(not PROJECT_CONF.is_file(), reason="conf.toml 이 없는 설치")
def test_agent_tools_round_trip_on_the_real_conf(tmp_path: Path):
    path = tmp_path / "conf.toml"
    path.write_text(PROJECT_CONF.read_text(encoding="utf-8"), encoding="utf-8")
    before = _load(path)["agents"]["critic"]["allowed_mcp_servers"]

    set_agent_allowed_mcp_servers_in_conf_file("critic", ["filesystem", "fetch"], path)
    assert _load(path)["agents"]["critic"]["allowed_mcp_servers"] == ["filesystem", "fetch"]
    # 다른 에이전트와 프롬프트는 그대로입니다.
    assert _load(path)["agents"]["coder"]["allowed_mcp_servers"] ==         _load(PROJECT_CONF)["agents"]["coder"]["allowed_mcp_servers"]
    assert _load(path)["agents"]["critic"]["system_prompt"] ==         _load(PROJECT_CONF)["agents"]["critic"]["system_prompt"]

    set_agent_allowed_mcp_servers_in_conf_file("critic", before, path)
    assert path.read_text(encoding="utf-8") == PROJECT_CONF.read_text(encoding="utf-8")
