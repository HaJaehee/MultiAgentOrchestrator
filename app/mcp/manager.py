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

        for name in self.server_configs:
            await self._start_client(name)

        self._rebuild_tool_lookup()
        self._initialized = True
        tool_total = sum(len(c.tools) for c in self.clients.values())
        connected = [n for n, c in self.clients.items() if c.is_connected]
        failed = [n for n in self.clients if n not in connected]
        logger.info(
            f"MCPManager initialized. Connected: {connected or '-'} | "
            f"Unavailable: {failed or '-'} | Total registered tools: {tool_total}"
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

    async def _start_client(self, name: str) -> None:
        """서버 하나를 띄우고 도구를 검색합니다. 실패해도 예외를 올리지 않습니다."""
        cfg = self.server_configs[name]
        client = MCPClientConnection(
            server_name=name,
            command=cfg.command,
            args=cfg.args,
            env=cfg.env,
        )
        self.clients[name] = client
        try:
            await client.discover_tools()
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to discover tools for MCP server '{name}': {e}")

    def _rebuild_tool_lookup(self) -> None:
        """모든 클라이언트의 도구로 조회 인덱스를 다시 만듭니다."""
        self._tool_lookup.clear()
        for client in self.clients.values():
            for tool in client.tools:
                self._tool_lookup[tool.qualified_name] = (client, tool.name)
                # Also allow plain tool name if unambiguous
                if tool.name not in self._tool_lookup:
                    self._tool_lookup[tool.name] = (client, tool.name)

    async def reconnect(self, server_name: Optional[str] = None) -> bool:
        """서버 하나(또는 실패한 서버 전부)를 다시 띄웁니다.

        UI 의 재연결 버튼용입니다. 하나라도 새로 붙었으면 True 를 반환합니다.
        """
        if server_name is not None:
            targets = [server_name] if server_name in self.server_configs else []
        else:
            targets = [n for n in self.server_configs
                       if n not in self.clients or not self.clients[n].is_connected]

        if not targets:
            return False

        for name in targets:
            existing = self.clients.pop(name, None)
            if existing is not None:
                try:
                    await existing.close()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Error closing MCP server '{name}' before reconnect: {e}")
            await self._start_client(name)

        self._rebuild_tool_lookup()
        reconnected = [n for n in targets if self.clients[n].is_connected]
        logger.info(f"MCP reconnect requested for {targets} -> connected: {reconnected or '-'}")
        return bool(reconnected)

    def connection_status(self) -> Dict[str, Dict[str, Any]]:
        """서버별 연결 상태 요약 (UI/헬스체크용)."""
        return {
            name: {
                "connected": client.is_connected,
                "available": client.is_available,
                "tool_count": len(client.tools),
                "command": client.command,
                "error": client.connect_error,
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
