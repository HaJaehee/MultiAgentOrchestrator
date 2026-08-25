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
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "./workspace"],
        ),
        "search": MCPServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-brave-search"],
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
