# UI Components Reference

The web application workspace is organized into four primary UI components in [app/ui/components/](file:///d:/MultiAgentOrchestrator/app/ui/components/) alongside the dedicated persona editor page.

```text
+-----------------------------------------------------------------------------------+
|  Header Bar: App Title | MCP Server Status Chips (FS/Mem/Git/Sandbox) | Refresh  |
+---------------------+---------------------------------------+---------------------+
|                     | Top: Agent Roster & Controls          |                     |
|                     | [x] Architect [x] Coder [x] Critic    |                     |
|                     | Strategy: [Sequential v] Rounds: [3]  |                     |
| Left Sidebar:       +---------------------------------------+ Right Panel:        |
| - [+ New Chat]      | Center: Chat & Debate Feed            | Artifact Viewer     |
| - Session 1         | - [User] Prompt...                    | [Report] [Code]     |
| - Session 2         | - [Orch] Planning...                  | [Mermaid] [JSON]    |
| - Session 3         | - [Architect] Design...               |                     |
|                     |   v [Tool: write_file] (Accordion)    | Content &           |
|                     | - [Coder] Implementation...           | One-click Copy/DL   |
|                     | - [Critic] Audit...                   |                     |
|                     | - [Orch] Final Synthesis...           |                     |
|                     +---------------------------------------+                     |
|                     | Input: [ Type message here...    ] [>]|                     |
+---------------------+---------------------------------------+---------------------+
```

---

## 1. Component Breakdown

### 1.1. Session Sidebar ([app/ui/components/sidebar.py](file:///d:/MultiAgentOrchestrator/app/ui/components/sidebar.py))
- **`+ New Chat` Button**: Instantiates a fresh debate session and clears the workspace.
- **Session List**: Displays historical sessions ordered by `updated_at` descending.
- **Badges & Metadata**: Displays creation timestamps and colored chips for participating agents.
- **Session Management**: Allows inline renaming of session titles and deletion with confirmation dialogs.

### 1.2. Agent Roster Control ([app/ui/components/roster.py](file:///d:/MultiAgentOrchestrator/app/ui/components/roster.py))
- **Agent Toggle Cards**: Allows users to include or exclude specific specialists (e.g. toggling the Critic off for faster brainstorming). The Master Orchestrator is fixed and always enabled.
- **Dynamic Live Refresh**: Rendered inside a reactive container (`cards_row`). When personas are updated via the persona editor or config reloads, `update_agents()` and `refresh_agent_cards()` dynamically update card labels and roles without a page reload.
- **Configuration Tooltips**: Hovering over an agent card reveals its configured model, endpoint URL, and sequential thinking mode.
- **Strategy Dropdown**: Selects between `free_debate`, `sequential_review`, and `adversarial_debate`.
- **Max Rounds Slider**: Sets the maximum debate depth ($1$ to $10$ rounds).
- **Custom Instructions Box**: Allows injecting ad-hoc guidelines into all agent prompts for the current session.
- **Persona Settings Button**: Links directly to `/personas/{session_id}`. Displays a lock icon if debate has commenced.
- **MCP Server Chips**: Displays real-time connection states (Green/Orange/Red) and opens diagnostic tooltips on hover.

### 1.3. Chat & Debate Feed ([app/ui/components/chat_feed.py](file:///d:/MultiAgentOrchestrator/app/ui/components/chat_feed.py))
- **Color-Coded Message Timeline**: Displays user prompts, orchestrator guidance, and specialist contributions with distinct avatars, roles, and colors.
- **Real-Time Token Streaming**: Supports incremental token streaming (`start_streaming_message`, `append_stream_chunk`, and `finalize_streaming_message`). Agent messages stream directly into reactive markdown cards as LLM completion chunks arrive.
- **Folding Tool Accordions**: Each MCP tool call (input arguments and execution outputs) renders inside an expandable Quasar accordion, preserving timeline readability.
- **Status & Progress Banner**: Shows real-time speaker indicators (e.g. `[Senior Python Engineer] 발언 및 분석 중...`) and round counters during execution.
- **Input Bar**: Auto-expanding message textarea with submit shortcuts (`Enter` / `Ctrl+Enter`).

### 1.4. Artifact Viewer ([app/ui/components/artifact_viewer.py](file:///d:/MultiAgentOrchestrator/app/ui/components/artifact_viewer.py))
- **Tabbed Interface**:
  - **Comprehensive Report Tab**: Markdown rendering of the final synthesis report.
  - **Source Code Tab**: Language-highlighted code viewer for extracted scripts.
  - **Architecture Diagram Tab**: Interactive SVG rendering of Mermaid diagrams.
  - **JSON Summary Tab**: Structured session metadata.
- **Action Toolbar**: Includes one-click **"Copy to Clipboard"** and **"Download File"** buttons for all extracted artifacts.

### 1.5. Persona Editor Page ([app/ui/personas_page.py](file:///d:/MultiAgentOrchestrator/app/ui/personas_page.py))
- Dedicated page accessible at `/personas/{session_id}`.
- Renders cards for each registered agent with editable fields:
  - **Display Name**
  - **Role Title**
  - **System Instructions**
- **Draft & Reset Controls**: Allows saving drafts to `session_agents` or resetting to `conf.toml` defaults.
- **Lock Banner**: If the session has already begun (`personas_locked = true`), inputs are disabled, displaying a read-only warning badge.
