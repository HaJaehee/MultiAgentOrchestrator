import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class MCPToolDefinition(BaseModel):
    server_name: str
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = {}

    @property
    def qualified_name(self) -> str:
        """Returns unique tool identifier combining server and tool name."""
        return f"{self.server_name}__{self.name}"

    def to_openai_tool(self) -> Dict[str, Any]:
        """Converts MCP Tool definition to LiteLLM/OpenAI tool format."""
        schema = self.input_schema if self.input_schema else {"type": "object", "properties": {}}
        return {
            "type": "function",
            "function": {
                "name": self.qualified_name,
                "description": f"[{self.server_name}] {self.description or self.name}",
                "parameters": schema,
            },
        }


class MCPClientConnection:
    """Manages individual MCP Server process lifecycle and tool calls."""

    def __init__(self, server_name: str, command: str, args: List[str], env: Optional[Dict[str, str]] = None):
        self.server_name = server_name
        self.command = command
        self.args = args
        self.env = env or {}
        self._tools: List[MCPToolDefinition] = []
        self._is_available = False

    @property
    def tools(self) -> List[MCPToolDefinition]:
        return self._tools

    @property
    def is_available(self) -> bool:
        return self._is_available

    def _get_server_params(self) -> StdioServerParameters:
        merged_env = os.environ.copy()
        merged_env.update(self.env)
        return StdioServerParameters(
            command=self.command,
            args=self.args,
            env=merged_env,
        )

    async def discover_tools(self) -> List[MCPToolDefinition]:
        """Spawns server, initializes session, discovers tools and caches them."""
        server_params = self._get_server_params()
        try:
            async with stdio_client(server_params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    discovered: List[MCPToolDefinition] = []
                    for t in result.tools:
                        input_schema = getattr(t, "inputSchema", getattr(t, "input_schema", {}))
                        if hasattr(input_schema, "model_dump"):
                            input_schema = input_schema.model_dump()
                        elif not isinstance(input_schema, dict):
                            input_schema = dict(input_schema) if input_schema else {}

                        discovered.append(
                            MCPToolDefinition(
                                server_name=self.server_name,
                                name=t.name,
                                description=t.description or "",
                                input_schema=input_schema,
                            )
                        )
                    self._tools = discovered
                    self._is_available = True
                    logger.info(f"Discovered {len(discovered)} tools from MCP server '{self.server_name}'")
                    return self._tools
        except Exception as e:
            logger.warning(f"Could not connect to MCP server '{self.server_name}' ({self.command}): {e}")
            self._is_available = False
            # Fallback mock tools for simulation/testing if external server cannot be spawned
            self._tools = self._get_fallback_mock_tools()
            return self._tools

    def _get_fallback_mock_tools(self) -> List[MCPToolDefinition]:
        """Provide mock tool definitions if external server is offline or fails."""
        if "filesystem" in self.server_name:
            return [
                MCPToolDefinition(
                    server_name=self.server_name,
                    name="read_file",
                    description="Read file contents from workspace",
                    input_schema={
                        "type": "object",
                        "properties": {"path": {"type": "string", "description": "Relative file path"}},
                        "required": ["path"],
                    },
                ),
                MCPToolDefinition(
                    server_name=self.server_name,
                    name="write_file",
                    description="Write content to a file in workspace",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Relative file path"},
                            "content": {"type": "string", "description": "File content"},
                        },
                        "required": ["path", "content"],
                    },
                ),
                MCPToolDefinition(
                    server_name=self.server_name,
                    name="list_directory",
                    description="List files in a directory",
                    input_schema={
                        "type": "object",
                        "properties": {"path": {"type": "string", "description": "Directory path", "default": "."}},
                    },
                ),
            ]
        elif "search" in self.server_name:
            return [
                MCPToolDefinition(
                    server_name=self.server_name,
                    name="search",
                    description="Search the web for technical documentation and libraries",
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string", "description": "Search query"}},
                        "required": ["query"],
                    },
                )
            ]
        return []

    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Executes a tool on this MCP server."""
        if self._is_available:
            server_params = self._get_server_params()
            try:
                async with stdio_client(server_params) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        res = await session.call_tool(tool_name, arguments=arguments)
                        # Extract content from response
                        if hasattr(res, "content"):
                            texts = []
                            for item in res.content:
                                if hasattr(item, "text"):
                                    texts.append(item.text)
                                else:
                                    texts.append(str(item))
                            return "\n".join(texts)
                        return str(res)
            except Exception as e:
                logger.error(f"Error executing tool '{tool_name}' on '{self.server_name}': {e}")
                return f"Error executing tool '{tool_name}': {str(e)}"

        # Execute fallback simulation if server unavailable
        return self._simulate_tool_execution(tool_name, arguments)

    def _simulate_tool_execution(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Simulates tool execution locally for offline/test environments."""
        if tool_name == "read_file":
            path = arguments.get("path", "")
            return f"[Simulated File Read: '{path}'] (File exists. Length: 120 lines. Status: Ready)"
        elif tool_name == "write_file":
            path = arguments.get("path", "")
            content_len = len(arguments.get("content", ""))
            return f"[Simulated File Write: '{path}'] (Successfully wrote {content_len} bytes)"
        elif tool_name == "list_directory":
            path = arguments.get("path", ".")
            return f"[Simulated Dir List: '{path}'] ['app/', 'tests/', 'conf.toml', 'README.md', 'requirements.txt']"
        elif tool_name == "search":
            query = arguments.get("query", "")
            return f"[Simulated Web Search: '{query}'] Found 3 references: FastAPI docs, NiceGUI documentation, Python stdlib guidelines."
        return f"[Simulated Tool Execution: {self.server_name}.{tool_name}({json.dumps(arguments)})] Success."
