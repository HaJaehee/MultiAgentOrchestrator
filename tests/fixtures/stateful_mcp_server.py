"""테스트용 상태 유지 MCP 서버.

호출마다 증가하는 카운터와 자기 PID 를 돌려줍니다. 클라이언트가 세션을
유지하지 않고 호출마다 프로세스를 새로 띄우면 카운터가 항상 1 이고 PID 도
매번 달라지므로, 세션 재사용 여부를 그대로 드러냅니다.

`echo_scope` 는 호스트가 요청 메타데이터로 보낸 대화 스코프를 되돌려줍니다.
"""

import os

from mcp.server.fastmcp import Context, FastMCP

server = FastMCP("stateful-test-server")

_state = {"count": 0}


@server.tool()
def bump() -> str:
    """호출 횟수를 1 증가시키고 현재 값과 서버 PID 를 반환합니다."""
    _state["count"] += 1
    return f"count={_state['count']} pid={os.getpid()}"


@server.tool()
def echo_scope(ctx: Context) -> str:
    """이 호출의 요청 메타데이터에 실려 온 대화 스코프를 그대로 돌려줍니다.

    호스트가 `_meta` 를 보내지 않으면 'scope=none' 이 됩니다.
    """
    rc = getattr(ctx, "request_context", None)
    meta = getattr(rc, "meta", None) if rc is not None else None
    value = getattr(meta, "conversationId", None) if meta is not None else None
    return f"scope={value or 'none'}"


@server.tool()
def fail(reason: str) -> str:
    """항상 실패하는 도구. isError 경로 검증용입니다."""
    raise ValueError(f"의도된 실패: {reason}")


if __name__ == "__main__":
    server.run()
