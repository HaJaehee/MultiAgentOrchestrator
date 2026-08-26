import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest
from app.config import MCPServerConfig
from app.mcp.client import MCPClientConnection, MCPToolDefinition
from app.mcp.manager import MCPManager


def test_tool_definition_to_openai():
    tool = MCPToolDefinition(
        server_name="filesystem",
        name="read_file",
        description="Reads a file from workspace",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    openai_format = tool.to_openai_tool()
    assert openai_format["type"] == "function"
    assert openai_format["function"]["name"] == "filesystem__read_file"
    assert "[filesystem]" in openai_format["function"]["description"]
    assert "path" in openai_format["function"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_mcp_manager_discovery_and_execution():
    server_configs = {
        "filesystem": MCPServerConfig(
            command="node",
            args=["./mcp_node/node_modules/@modelcontextprotocol/server-filesystem/dist/index.js", "./workspace"],
        ),
        "search": MCPServerConfig(
            command="python",
            args=["-m", "duckduckgo_mcp_server"],
        ),
    }
    manager = MCPManager(server_configs)
    await manager.initialize()

    # Verify tool retrieval for servers
    fs_tools = manager.get_tools_for_servers(["filesystem"])
    assert len(fs_tools) > 0
    assert any(t.name == "read_file" for t in fs_tools)

    openai_tools = manager.get_openai_tools_for_servers(["filesystem", "search"])
    assert len(openai_tools) >= 2

    # Verify tool execution (simulation / actual)
    output, status = await manager.execute_tool("filesystem__read_file", {"path": "sample.py"})
    assert status == "success"
    assert len(output) > 0


FIXTURE_SERVER = str(Path(__file__).parent / "fixtures" / "stateful_mcp_server.py")


def _field(output: str, key: str) -> str:
    """'count=2 pid=123' 형태의 응답에서 값을 꺼냅니다."""
    return next(part.split("=", 1)[1] for part in output.split() if part.startswith(f"{key}="))


@pytest.mark.asyncio
async def test_session_is_reused_across_tool_calls():
    """도구 호출마다 서버를 재기동하면 서버가 들고 있는 상태가 사라진다.

    코드 실행 샌드박스의 네임스페이스별 커널(변수/데이터프레임)이 이 성질에
    의존하므로, 세션 재사용이 깨지면 여기서 잡힌다.
    """
    manager = MCPManager({"stateful": MCPServerConfig(command=sys.executable, args=[FIXTURE_SERVER])})
    await manager.initialize()
    try:
        client = manager.clients["stateful"]
        assert client.is_connected, "stdio 세션이 유지되지 않았습니다"
        assert any(t.name == "bump" for t in client.tools)

        out1, status1 = await manager.execute_tool("stateful__bump", {})
        out2, status2 = await manager.execute_tool("stateful__bump", {})
        assert status1 == "success" and status2 == "success"

        # 상태가 이어진다 (재기동되었다면 둘 다 count=1)
        assert _field(out1, "count") == "1"
        assert _field(out2, "count") == "2"
        # 같은 프로세스가 응답한다
        assert _field(out1, "pid") == _field(out2, "pid")
    finally:
        await manager.shutdown()

    assert manager.clients == {}, "shutdown 후에도 클라이언트가 남아 있습니다"


@pytest.mark.asyncio
async def test_shutdown_terminates_server_process():
    """shutdown 이후에는 서버 프로세스가 실제로 종료되어야 한다."""
    manager = MCPManager({"stateful": MCPServerConfig(command=sys.executable, args=[FIXTURE_SERVER])})
    await manager.initialize()
    out, _ = await manager.execute_tool("stateful__bump", {})
    pid = int(_field(out, "pid"))

    await manager.shutdown()

    for _ in range(50):  # 프로세스 정리까지 최대 5초 대기
        try:
            os.kill(pid, 0)
        except OSError:
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail(f"MCP 서버 프로세스(pid={pid})가 종료되지 않았습니다")


@pytest.mark.asyncio
async def test_reconnects_after_server_process_dies():
    """서버가 죽으면 다음 호출에서 한 번 재연결한다.

    프로세스가 죽어도 소유 태스크는 곧바로 끝나지 않으므로, 스트림이 닫혔다는
    anyio 예외를 세션 사망 신호로 봐야 한다.
    """
    manager = MCPManager({"stateful": MCPServerConfig(command=sys.executable, args=[FIXTURE_SERVER])})
    await manager.initialize()
    try:
        out, _ = await manager.execute_tool("stateful__bump", {})
        old_pid = int(_field(out, "pid"))

        os.kill(old_pid, signal.SIGKILL)
        await asyncio.sleep(0.5)

        out2, status2 = await manager.execute_tool("stateful__bump", {})
        assert status2 == "success", f"재연결에 실패했습니다: {out2}"
        assert int(_field(out2, "pid")) != old_pid, "새 서버 프로세스로 붙지 않았습니다"
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_connection_status_and_reconnect():
    """UI 가 쓰는 상태 조회와 재연결 경로."""
    good = MCPServerConfig(command=sys.executable, args=[FIXTURE_SERVER])
    bad = MCPServerConfig(command=sys.executable, args=["-m", "no_such_mcp_server_xyz"])
    manager = MCPManager({"stateful": good, "broken": bad})
    await manager.initialize()
    try:
        status = manager.connection_status()
        assert status["stateful"]["connected"] is True
        assert status["stateful"]["tool_count"] > 0

        assert status["broken"]["connected"] is False
        # 서버가 stderr 로 남긴 실제 원인이 보고되어야 한다
        # (anyio 의 'unhandled errors in a TaskGroup' 이 아니라)
        assert "no_such_mcp_server_xyz" in (status["broken"]["error"] or "")

        # 실패한 서버만 다시 시도하며, 여전히 실패하면 False
        assert await manager.reconnect() is False
        assert manager.connection_status()["stateful"]["connected"] is True

        # 정상 서버를 지정해 재연결하면 새 프로세스로 붙는다
        out_before, _ = await manager.execute_tool("stateful__bump", {})
        assert await manager.reconnect("stateful") is True
        out_after, _ = await manager.execute_tool("stateful__bump", {})
        assert _field(out_before, "pid") != _field(out_after, "pid")
    finally:
        await manager.shutdown()
