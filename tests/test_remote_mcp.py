"""원격(HTTP) MCP 서버.

지금까지 이 앱은 MCP 서버를 **자기가 띄우는 프로세스**로만 알았습니다
(`stdio_client`). 사내 게이트웨이나 SaaS 처럼 이미 떠 있는 서버는 주소만 있고
띄울 명령이 없으므로 아예 붙일 수 없었습니다.

여기서 지키려는 것.

1. 서버 하나는 **로컬 프로세스이거나 원격 주소**이지, 둘 다일 수 없다.
2. 무엇을 하려는지 알 수 없는 설정은 **거절한다**. 조용히 한쪽을 고르면, 고치는
   사람은 왜 자기가 적은 값이 무시되는지 알 수 없다.
3. 토큰은 설정 파일에 박히지 않는다 — `${환경변수}` 표기가 그대로 저장되고,
   읽을 때만 풀린다.
4. `auto` 는 지금의 표준(Streamable HTTP)을 먼저 시도하고 옛 SSE 로 물러선다.
"""

import json
import textwrap
from pathlib import Path

import pytest

from app.config import (
    MCPServerConfig,
    add_mcp_server_to_conf_file,
    load_config,
    read_conf_file,
)
from app.mcp.client import MCPClientConnection


# --------------------------------------------------------------- 설정 모델


def test_a_local_server_needs_a_command():
    cfg = MCPServerConfig(command="npx", args=["-y", "server-everything"])

    assert cfg.is_remote is False
    assert cfg.resolved_transport == "stdio"
    assert cfg.endpoint_label == "npx -y server-everything"


def test_a_remote_server_needs_only_a_url():
    cfg = MCPServerConfig(url="https://mcp.example.com/mcp")

    assert cfg.is_remote is True
    assert cfg.resolved_transport == "http"
    assert cfg.endpoint_label == "https://mcp.example.com/mcp"


def test_a_server_cannot_be_both():
    """무엇을 하려는지 알 수 없습니다. 조용히 하나를 고르면 안 됩니다."""
    with pytest.raises(ValueError, match="하나만"):
        MCPServerConfig(command="npx", url="https://mcp.example.com/mcp")


def test_a_server_must_be_something():
    with pytest.raises(ValueError, match="command|url"):
        MCPServerConfig()


@pytest.mark.parametrize("kwargs, message", [
    ({"url": "https://h/mcp", "transport": "stdio"}, "command"),
    ({"command": "npx", "transport": "http"}, "url"),
    ({"command": "npx", "transport": "sse"}, "url"),
])
def test_the_transport_must_match_what_is_configured(kwargs, message):
    with pytest.raises(ValueError, match=message):
        MCPServerConfig(**kwargs)


def test_an_explicit_transport_is_kept():
    assert MCPServerConfig(url="https://h/sse", transport="sse").resolved_transport == "sse"


# --------------------------------------------------------------- 붙는 순서


def test_a_remote_server_falls_back_from_http_to_sse():
    """두 규격이 같은 주소를 씁니다. 붙여 보기 전에는 알 수 없습니다."""
    client = MCPClientConnection("remote", url="https://h/mcp")

    assert client.connect_attempts == ["http", "sse"]
    assert client.transport == "http"
    assert client.is_remote is True


def test_pinning_the_transport_skips_the_round_trip():
    client = MCPClientConnection("remote", url="https://h/sse", transport="sse")

    assert client.connect_attempts == ["sse"]


def test_a_local_server_only_ever_speaks_stdio():
    client = MCPClientConnection("local", command="npx", args=["-y", "x"])

    assert client.connect_attempts == ["stdio"]
    assert client.is_remote is False
    assert client.endpoint_label == "npx -y x"


# --------------------------------------------------------------- 설정 파일 쓰기


BASE_CONF = textwrap.dedent("""\
    {
      "app": { "host": "127.0.0.1", "port": 8000 },
      "// mcp_servers": "여기 적은 설명은 화면에서 서버를 추가해도 그대로 남아야 합니다",
      "mcp_servers": {
        "// filesystem": "공용 작업 공간 파일 I/O",
        "filesystem": { "command": "npx", "args": ["-y", "server-filesystem"], "enabled": true }
      },
      "agents": {
        "orchestrator": { "name": "Orch", "role": "Lead", "api_key": "k" }
      }
    }
    """)


@pytest.fixture()
def conf(tmp_path: Path) -> Path:
    path = tmp_path / "conf.json"
    path.write_text(BASE_CONF, encoding="utf-8")
    return path


def test_adding_a_remote_server_writes_a_url_not_a_command(conf: Path):
    add_mcp_server_to_conf_file(
        "gateway", config_path=conf,
        url="https://mcp.example.com/mcp",
        headers={"Authorization": "Bearer ${GATEWAY_TOKEN}"},
    )

    block = read_conf_file(conf)["mcp_servers"]["gateway"]
    assert block["url"] == "https://mcp.example.com/mcp"
    assert block["headers"] == {"Authorization": "Bearer ${GATEWAY_TOKEN}"}
    assert block["enabled"] is True
    # 원격 서버에는 띄울 명령이 없습니다. 빈 값이라도 적어 두면 "둘 다" 가 됩니다.
    assert "command" not in block and "args" not in block


def test_the_token_stays_a_placeholder_in_the_file(conf: Path, monkeypatch):
    """설정 파일에 실제 토큰이 박히면 배포판과 저장소에 함께 실려 나갑니다."""
    monkeypatch.setenv("GATEWAY_TOKEN", "s3cr3t")
    add_mcp_server_to_conf_file(
        "gateway", config_path=conf, url="https://h/mcp",
        headers={"Authorization": "Bearer ${GATEWAY_TOKEN}"},
    )

    assert "s3cr3t" not in conf.read_text(encoding="utf-8")
    # 읽을 때만 풀립니다.
    loaded = load_config(conf).mcp_servers["gateway"]
    assert loaded.headers["Authorization"] == "Bearer s3cr3t"


def test_an_explicit_transport_is_written_but_auto_is_not(conf: Path):
    """기본값을 파일에 적으면, 나중에 기본값이 바뀌어도 옛 값에 묶입니다."""
    add_mcp_server_to_conf_file("a", config_path=conf, url="https://h/sse", transport="sse")
    add_mcp_server_to_conf_file("b", config_path=conf, url="https://h/mcp", transport="auto")

    servers = read_conf_file(conf)["mcp_servers"]
    assert servers["a"]["transport"] == "sse"
    assert "transport" not in servers["b"]


def test_comments_survive_adding_a_remote_server(conf: Path):
    add_mcp_server_to_conf_file("gateway", config_path=conf, url="https://h/mcp")

    text = conf.read_text(encoding="utf-8")
    assert "여기 적은 설명은" in text
    assert "공용 작업 공간 파일 I/O" in text


@pytest.mark.parametrize("kwargs, message", [
    ({"command": "npx", "url": "https://h/mcp"}, "함께 쓸 수 없습니다"),
    ({}, "하나는 있어야 합니다"),
    ({"url": "mcp.example.com"}, "http"),
    ({"url": "https://h/mcp", "headers": {"Bad Header": "x"}}, "헤더 이름"),
])
def test_a_bad_remote_server_is_refused_before_the_file_is_touched(conf: Path, kwargs, message):
    before = conf.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        add_mcp_server_to_conf_file("broken", config_path=conf, **kwargs)

    assert conf.read_text(encoding="utf-8") == before


def test_a_remote_server_loads_from_the_config_file(conf: Path):
    data = json.loads(conf.read_text(encoding="utf-8"))
    data["mcp_servers"]["gateway"] = {
        "url": "https://mcp.example.com/mcp",
        "headers": {"Authorization": "Bearer t"},
        "transport": "http",
        "timeout": 5,
    }
    conf.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    server = load_config(conf).mcp_servers["gateway"]

    assert server.is_remote and server.resolved_transport == "http"
    assert server.timeout == 5
    assert server.enabled is True
