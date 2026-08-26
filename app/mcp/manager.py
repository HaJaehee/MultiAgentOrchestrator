import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from app.config import MCPServerConfig
from app.mcp.client import MCPClientConnection, MCPToolDefinition

logger = logging.getLogger(__name__)


class MCPManager:
    """Central MCP Host & Tool Registry manager."""

    def __init__(self, server_configs: Optional[Dict[str, MCPServerConfig]] = None):
        self.server_configs = server_configs or {}
        self.clients: Dict[str, MCPClientConnection] = {}
        self._tool_lookup: Dict[str, Tuple[MCPClientConnection, str]] = {}  # qualified_name -> (client, tool_name)
        self._initialized = False

    async def initialize(self) -> None:
        """Initializes all configured MCP clients and discovers tools.

        각 클라이언트는 여기서 연 세션을 `shutdown()` 까지 유지합니다.
        """
        await self.shutdown()
        self._tool_lookup.clear()

        for name, cfg in self.server_configs.items():
            client = MCPClientConnection(
                server_name=name,
                command=cfg.command,
                args=cfg.args,
                env=cfg.env,
            )
            self.clients[name] = client

            try:
                tools = await client.discover_tools()
                for tool in tools:
                    self._tool_lookup[tool.qualified_name] = (client, tool.name)
                    # Also allow plain tool name if unambiguous
                    if tool.name not in self._tool_lookup:
                        self._tool_lookup[tool.name] = (client, tool.name)
            except Exception as e:
                logger.error(f"Failed to discover tools for MCP server '{name}': {e}")

        self._initialized = True
        connected = [n for n, c in self.clients.items() if c.is_connected]
        failed = [n for n in self.clients if n not in connected]
        logger.info(
            f"MCPManager initialized. Connected: {connected or '-'} | "
            f"Unavailable: {failed or '-'} | Total registered tools: {len(self._tool_lookup)}"
        )

    async def shutdown(self) -> None:
        """열려 있는 모든 MCP 세션과 서버 프로세스를 정리합니다."""
        for name, client in list(self.clients.items()):
            try:
                await client.close()
            except Exception as e:  # noqa: BLE001 - 종료 경로의 오류는 무시
                logger.warning(f"Error closing MCP server '{name}': {e}")
        self.clients.clear()
        self._initialized = False

    def connection_status(self) -> Dict[str, Dict[str, Any]]:
        """서버별 연결 상태 요약 (UI/헬스체크용)."""
        return {
            name: {
                "connected": client.is_connected,
                "available": client.is_available,
                "tool_count": len(client.tools),
                "command": client.command,
            }
            for name, client in self.clients.items()
        }

    def get_tools_for_servers(self, allowed_servers: List[str]) -> List[MCPToolDefinition]:
        """Returns tool definitions available for the specified server names."""
        tools: List[MCPToolDefinition] = []
        for srv_name in allowed_servers:
            if srv_name in self.clients:
                tools.extend(self.clients[srv_name].tools)
        return tools

    def get_openai_tools_for_servers(self, allowed_servers: List[str]) -> List[Dict[str, Any]]:
        """Returns OpenAI/LiteLLM function schemas for allowed servers."""
        tools = self.get_tools_for_servers(allowed_servers)
        return [t.to_openai_tool() for t in tools]

    async def execute_tool(self, tool_name: str, arguments: Any) -> Tuple[str, str]:
        """
        Executes a tool by qualified name (e.g. 'filesystem__read_file') or plain name ('read_file').
        Returns (output_str, status ['success'|'error']).
        """
        # Ensure arguments are dict
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except Exception:
                arguments = {"input": arguments}
        elif not isinstance(arguments, dict):
            arguments = {}

        if tool_name in self._tool_lookup:
            client, actual_tool_name = self._tool_lookup[tool_name]
            try:
                result = await client.execute_tool(actual_tool_name, arguments)
                return result, "success"
            except Exception as e:
                return f"Tool execution failed ({type(e).__name__}): {e}", "error"

        # Check if qualified name split works e.g. server__tool
        if "__" in tool_name:
            server_name, actual_tool_name = tool_name.split("__", 1)
            if server_name in self.clients:
                try:
                    result = await self.clients[server_name].execute_tool(actual_tool_name, arguments)
                    return result, "success"
                except Exception as e:
                    return f"Tool execution failed ({type(e).__name__}): {e}", "error"

        return f"Unknown tool: '{tool_name}'. Available tools: {list(self._tool_lookup.keys())}", "error"


# Global singleton instance
_mcp_manager: Optional[MCPManager] = None


def get_mcp_manager() -> MCPManager:
    global _mcp_manager
    if _mcp_manager is None:
        from app.config import get_config
        cfg = get_config()
        _mcp_manager = MCPManager(cfg.enabled_mcp_servers)
    return _mcp_manager
