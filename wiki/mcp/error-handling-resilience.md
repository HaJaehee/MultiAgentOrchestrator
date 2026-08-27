# MCP Error Handling, Diagnostics & Resilience

External subprocess communications are inherently vulnerable to runtime disruptions (e.g., process crashes, environment misconfigurations, and invalid tool arguments). The platform implements robust diagnostic and fault-tolerance patterns in [app/mcp/client.py](file:///d:/MultiAgentOrchestrator/app/mcp/client.py).

---

## 1. Two Classes of Tool Failures

MCP distinguishes between communication protocol failures and semantic tool execution failures:

| Category | Transport Representation | System Handling | LLM Context Injection |
| :--- | :--- | :--- | :--- |
| **Protocol Error** | JSON-RPC error or broken pipe | Logged as warning; triggers [`MCPToolError`](file:///d:/MultiAgentOrchestrator/app/mcp/client.py#L18-L32) | Marked as `status: "error"`; returns error message string. |
| **Tool Execution Error**| Valid JSON-RPC response with `isError: true` | Recorded with `status: "error"`; keeps process alive | **Raw server error text is injected verbatim** into LLM context. |

### Preserving Verbatim Error Text for LLM Self-Correction
When an agent passes an invalid path (e.g. `write_file(path="../outside.py")`) and the server responds with:
```json
{
  "isError": true,
  "content": [{"type": "text", "text": "Path traversal rejected: path must be inside workspace"}]
}
```
The platform **never obfuscates or replaces this message with a generic error**. The LLM reads the exact failure string in its observation message, analyzes the mistake, adjusts the parameters, and successfully re-executes the tool within the same turn.

---

## 2. Stderr Diagnostics via `_StderrTee`

When an MCP subprocess crashes during startup (e.g., due to a missing Python package or syntax error in a custom server), the `anyio` async framework raises a generic exception:
```text
ExceptionGroup: unhandled errors in a TaskGroup
```
This generic message contains zero diagnostic value. The actual root cause (`ModuleNotFoundError: No module named 'xyz'`) was written by the child process directly to `stderr`.

### The `_StderrTee` Solution ([app/mcp/client.py](file:///d:/MultiAgentOrchestrator/app/mcp/client.py#L52-L111)):
1. Creates an OS pipe (`os.pipe()`) and attaches the write descriptor to the subprocess's `stderr`.
2. Spawns a background daemon thread that pumps the read descriptor to the main application's console while maintaining a ring buffer of:
   - **Head lines** (first 4 lines: typically the immediate failure statement, e.g. `Cannot find module ...`).
   - **Tail lines** (last 8 lines: the stack trace root).
3. Stores this diagnostic string in `MCPClientConnection.connect_error`.
4. Renders the exact error trace inside a hover tooltip on the UI's server status chip:

```mermaid
flowchart LR
    Child[MCP Subprocess] -->|stderr pipe| Pipe[os.pipe Descriptor]
    Pipe --> Pump[Daemon Thread _StderrTee]
    Pump --> Console[Console sys.stderr]
    Pump --> Buffer[Head/Tail Ring Buffer]
    Buffer --> Tooltip[UI Roster Hover Tooltip]
```

---

## 3. Automatic Reconnection & Safety

When a tool invocation fails because the underlying stdio process died:
- [`MCPClientConnection.call_tool()`](file:///d:/MultiAgentOrchestrator/app/mcp/client.py) detects the broken pipe and attempts **exactly one automatic reconnection**.
- If reconnection succeeds, the call proceeds.
- If a tool invocation fails logically (`isError: true` with a live server), **no retry is attempted**. Automatically retrying side-effecting operations (such as file appending or git commits) could cause duplicate operations or state corruption.
- **Process Teardown & Windows Safety**: The client tracks `_process_pid` from the stdio context. During teardown or shutdown, the child process is terminated explicitly before awaiting the owner task, ensuring Windows process handles and I/O pipes close cleanly without hanging or leaving orphan processes.
- **App Crash Isolation & Watcher Exclusion**:
  - `(Exception, BaseException)` blocks wrap tool execution and client teardown to prevent unhandled `BaseExceptionGroup` instances from crashing the host process.
  - Uvicorn's file watcher explicitly excludes `workspace/`, `*.db*`, and `.git/` so that file operations performed by sandbox or filesystem tools do not trigger false hot-reloads and application restarts.

---

## 4. Real-Time Connection Monitoring

The system exposes connection states via [`MCPManager.connection_status()`](file:///d:/MultiAgentOrchestrator/app/mcp/manager.py#L210-L230) and the `GET /api/mcp` endpoint:

| Status Chip | Visual Indicator | Meaning & Health State |
| :--- | :--- | :--- |
| 🟢 `filesystem 툴 14` | Green Chip | Connected and healthy; shows registered tool count. |
| 🟠 `연결 끊김` | Orange Chip | Subprocess terminated; will auto-reconnect on next call. |
| 🔴 `연결 실패` | Red Chip | Process startup failed; hover displays stderr diagnostic trace. |
| ⚪ `비활성` | Grey Chip | Server explicitly disabled (`enabled = false` in `conf.toml`). |

A **"Refresh"** button in the UI header invokes `mcp_manager.reconnect_disconnected()`, re-attempting initialization solely for failed servers without interrupting healthy ones.
