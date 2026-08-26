import asyncio
import json
import logging
import os
import sys
import threading
from collections import deque

import anyio
from typing import Any, Dict, List, Optional
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class MCPToolError(Exception):
    """서버가 `isError: true` 로 보고한 도구 실행 실패.

    MCP 는 프로토콜 오류(JSON-RPC error)와 도구 실행 오류를 구분합니다.
    전자는 클라이언트에서 소비되고 끝나지만, 후자는 성공 응답과 똑같이 LLM
    컨텍스트에 주입되어야 모델이 원인을 보고 스스로 고칠 수 있습니다.
    그래서 서버 메시지를 그대로 담아 전달합니다.
    """

    def __init__(self, server_name: str, tool_name: str, message: str):
        self.server_name = server_name
        self.tool_name = tool_name
        self.message = message
        super().__init__(message)


def _describe_exception(exc: Optional[BaseException]) -> str:
    """ExceptionGroup 을 펼쳐 실제 원인을 문자열로 만듭니다."""
    if exc is None:
        return ""
    nested = getattr(exc, "exceptions", None)
    if nested:
        return " / ".join(_describe_exception(sub) for sub in nested)
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__

# 서버 기동 후 initialize 응답까지 기다리는 한도(초). IPython 커널을 띄우는
# 샌드박스 서버처럼 초기화가 무거운 경우를 감안한 값입니다.
STARTUP_TIMEOUT = 60.0

# 종료 신호를 보낸 뒤 프로세스가 정리되기를 기다리는 한도(초).
SHUTDOWN_TIMEOUT = 10.0


class _StderrTee:
    """서버 stderr 를 콘솔로 흘려보내면서 마지막 몇 줄을 보관합니다.

    기동에 실패하면 anyio 는 "unhandled errors in a TaskGroup" 같은 예외를
    올릴 뿐 원인을 담고 있지 않습니다. 진짜 원인(`No module named ...`,
    `command not found` 등)은 서버가 stderr 로 내보내므로 그 꼬리를 남겨
    UI 툴팁과 로그에 붙입니다.

    `anyio.open_process` 는 실제 파일 디스크립터를 요구하므로 파이썬 객체를
    그대로 넘길 수 없습니다. 파이프를 만들어 읽기 쪽을 데몬 스레드가 비웁니다.
    """

    def __init__(self, head_lines: int = 4, tail_lines: int = 8):
        # 원인은 대개 첫 줄에 있고(예: "Cannot find module ..."), 그 뒤로 스택
        # 트레이스가 길게 이어집니다. 꼬리만 보관하면 정작 원인이 밀려나므로
        # 머리와 꼬리를 따로 붙잡습니다.
        self._head: List[str] = []
        self._head_limit = head_lines
        self._lines: deque = deque(maxlen=tail_lines)
        read_fd, write_fd = os.pipe()
        self._handle = os.fdopen(write_fd, "w", buffering=1, encoding="utf-8", errors="replace")
        self._thread = threading.Thread(target=self._pump, args=(read_fd,), daemon=True)
        self._thread.start()

    def _pump(self, read_fd: int) -> None:
        try:
            with os.fdopen(read_fd, "r", encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    stripped = line.rstrip()
                    if stripped:
                        if len(self._head) < self._head_limit:
                            self._head.append(stripped)
                        else:
                            self._lines.append(stripped)
                    sys.stderr.write(line)
        except Exception:  # noqa: BLE001 - 로깅 보조 경로가 앱을 막으면 안 됩니다
            pass

    @property
    def handle(self):
        return self._handle

    @property
    def tail(self) -> str:
        head = list(self._head)
        rest = list(self._lines)
        if not rest:
            return "\n".join(head)
        return "\n".join(head + ["..."] + rest)

    def close(self, drain: bool = False) -> None:
        try:
            self._handle.close()
        except Exception:  # noqa: BLE001
            pass
        if drain:
            # 쓰기 쪽을 닫으면 pump 가 EOF 로 끝납니다. 자식이 아직 살아 있으면
            # 자기 복제본을 들고 있으므로 타임아웃으로 빠져나옵니다.
            self._thread.join(timeout=1.0)


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
    """단일 MCP 서버 프로세스의 수명주기와 도구 호출을 관리합니다.

    세션은 최초 연결 시 한 번 열고 계속 유지합니다. 도구 호출마다 프로세스를
    새로 띄우면 서버가 들고 있는 상태(예: 코드 실행 샌드박스의 네임스페이스별
    IPython 커널과 그 안의 변수/데이터프레임)가 매번 사라지고, 호출마다 기동
    비용을 다시 치르게 됩니다.

    stdio 세션은 **자신을 연 태스크(`_serve`)가 끝까지 소유**합니다. anyio 의
    cancel scope 는 태스크에 묶여 있어, 컨텍스트를 연 태스크가 아닌 곳에서
    빠져나오면 런타임 에러가 나기 때문입니다. 다른 태스크는 `_session` 으로
    요청만 보냅니다 (ClientSession 이 요청/응답 다중화를 처리합니다).
    """

    def __init__(self, server_name: str, command: str, args: List[str], env: Optional[Dict[str, str]] = None):
        self.server_name = server_name
        self.command = command
        self.args = args
        self.env = env or {}
        self._tools: List[MCPToolDefinition] = []
        self._is_available = False

        # 살아있는 세션과 그것을 소유한 태스크
        self._session: Optional[ClientSession] = None
        self._owner_task: Optional[asyncio.Task] = None
        self._ready: Optional[asyncio.Event] = None
        self._shutdown: Optional[asyncio.Event] = None
        self._connect_error: Optional[BaseException] = None
        self._stderr_tail: str = ""
        # connect / teardown 직렬화 (도구 호출 자체는 동시 실행 가능)
        self._lock = asyncio.Lock()

    @property
    def tools(self) -> List[MCPToolDefinition]:
        return self._tools

    @property
    def is_available(self) -> bool:
        return self._is_available

    @property
    def is_connected(self) -> bool:
        """실제 stdio 세션이 살아있는지 여부 (mock 폴백과 구분됩니다)."""
        return self._session is not None

    @property
    def connect_error(self) -> Optional[str]:
        """마지막 기동 실패 사유 (UI 표시용). 성공했으면 None.

        서버가 stderr 로 남긴 마지막 줄들을 우선합니다. anyio 예외 문구보다
        원인을 훨씬 정확히 담고 있기 때문입니다.
        """
        if self._connect_error is None and not self._stderr_tail:
            return None
        if self._stderr_tail:
            return self._stderr_tail
        return _describe_exception(self._connect_error)

    def _get_server_params(self) -> StdioServerParameters:
        merged_env = os.environ.copy()
        merged_env.update(self.env)
        return StdioServerParameters(
            command=self.command,
            args=self.args,
            env=merged_env,
        )

    async def _serve(self) -> None:
        """세션을 열고 종료 신호가 올 때까지 살려둡니다.

        컨텍스트 진입과 이탈이 모두 이 태스크 안에서 일어나야 합니다.
        """
        tee = _StderrTee()
        connected = False
        try:
            async with stdio_client(self._get_server_params(), errlog=tee.handle) as (
                read_stream,
                write_stream,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    self._session = session
                    self._connect_error = None
                    self._stderr_tail = ""
                    connected = True
                    self._ready.set()
                    # 종료 요청이 올 때까지 프로세스를 유지합니다.
                    await self._shutdown.wait()
        except Exception as e:  # noqa: BLE001 - 기동 실패는 모두 폴백으로 흡수
            self._connect_error = e
        finally:
            # 기동에 실패했을 때만 stderr 를 회수합니다 (정상 종료 경로는 대기 없이 닫음).
            tee.close(drain=not connected)
            if not connected:
                self._stderr_tail = tee.tail
            self._session = None
            if self._ready is not None:
                # 기동에 실패한 경우에도 대기 중인 connect() 를 깨웁니다.
                self._ready.set()

    async def connect(self) -> bool:
        """세션이 없으면 새로 열고, 사용 가능해졌는지 반환합니다."""
        async with self._lock:
            if self._session is not None:
                return True

            await self._teardown_locked()

            self._ready = asyncio.Event()
            self._shutdown = asyncio.Event()
            self._connect_error = None
            self._owner_task = asyncio.create_task(self._serve(), name=f"mcp-{self.server_name}")

            try:
                await asyncio.wait_for(self._ready.wait(), timeout=STARTUP_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning(
                    f"MCP server '{self.server_name}' did not initialize within {STARTUP_TIMEOUT}s"
                )
                await self._teardown_locked()
                return False

            if self._session is None:
                logger.warning(
                    f"Could not connect to MCP server '{self.server_name}' "
                    f"({self.command}): {self.connect_error or _describe_exception(self._connect_error)}"
                )
                await self._teardown_locked()
                return False

            logger.info(f"MCP session established for '{self.server_name}'")
            return True

    async def close(self) -> None:
        """세션을 닫고 서버 프로세스를 정리합니다."""
        async with self._lock:
            await self._teardown_locked()

    async def _teardown_locked(self) -> None:
        """호출자가 `self._lock` 을 들고 있어야 합니다."""
        task, self._owner_task = self._owner_task, None
        if task is None:
            self._session = None
            self._ready = None
            self._shutdown = None
            return

        if self._shutdown is not None:
            self._shutdown.set()
        try:
            await asyncio.wait_for(task, timeout=SHUTDOWN_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(f"MCP server '{self.server_name}' did not shut down cleanly; cancelled")
        except Exception:  # noqa: BLE001 - 종료 경로의 오류는 무시
            pass
        finally:
            self._session = None
            self._ready = None
            self._shutdown = None

    def _is_session_dead(self, exc: BaseException) -> bool:
        """예외가 '세션이 끊겼다'는 신호인지 판정합니다.

        서버 프로세스가 죽어도 소유 태스크는 곧바로 끝나지 않으므로
        (`_shutdown` 대기 중), 태스크 상태만으로는 판정할 수 없습니다.
        스트림이 닫혔다는 anyio 예외가 가장 이른 신호입니다.
        """
        if self._session is None:
            return True
        task = self._owner_task
        if task is None or task.done():
            return True
        return isinstance(
            exc, (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream)
        )

    async def discover_tools(self) -> List[MCPToolDefinition]:
        """세션을 열고 도구 목록을 받아 캐시합니다."""
        if not await self.connect():
            self._is_available = False
            # 외부 서버를 띄울 수 없으면 시뮬레이션/테스트용 mock 도구로 폴백합니다.
            self._tools = self._get_fallback_mock_tools()
            return self._tools

        session = self._session
        try:
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
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Tool discovery failed for MCP server '{self.server_name}': {e}")
            await self.close()
            self._is_available = False
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
        """유지 중인 세션으로 도구를 실행합니다. 세션이 끊겼으면 1회 재연결합니다."""
        if not self._is_available:
            # Execute fallback simulation if server unavailable
            return self._simulate_tool_execution(tool_name, arguments)

        for attempt in (1, 2):
            session = self._session
            if session is None:
                if not await self.connect():
                    return self._simulate_tool_execution(tool_name, arguments)
                session = self._session
                if session is None:
                    return self._simulate_tool_execution(tool_name, arguments)

            try:
                res = await session.call_tool(tool_name, arguments=arguments)
                text = self._extract_result_text(res)
                if getattr(res, "isError", False):
                    # 도구는 실행됐지만 실패했다. 서버 메시지를 그대로 올려
                    # 모델이 보고 스스로 고칠 수 있게 한다.
                    raise MCPToolError(self.server_name, tool_name, text)
                return text
            except MCPToolError:
                raise
            except Exception as e:  # noqa: BLE001
                # 세션이 죽은 경우에만 재시도합니다. 도구 자체가 실패한 것이라면
                # 재실행이 부작용(파일 쓰기 등)을 두 번 일으킬 수 있습니다.
                if attempt == 1 and self._is_session_dead(e):
                    logger.warning(
                        f"MCP session for '{self.server_name}' dropped during "
                        f"'{tool_name}'; reconnecting once ({type(e).__name__})"
                    )
                    await self.close()
                    continue
                # 호출자(MCPManager)가 'error' 상태로 보고할 수 있도록 전파합니다.
                logger.error(
                    f"Error executing tool '{tool_name}' on '{self.server_name}': "
                    f"{type(e).__name__}: {e}"
                )
                raise

        return self._simulate_tool_execution(tool_name, arguments)

    @staticmethod
    def _extract_result_text(res: Any) -> str:
        """CallToolResult 에서 텍스트 콘텐츠를 뽑아냅니다."""
        if hasattr(res, "content"):
            texts = []
            for item in res.content:
                if hasattr(item, "text"):
                    texts.append(item.text)
                else:
                    texts.append(str(item))
            return "\n".join(texts)
        return str(res)


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
