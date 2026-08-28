# MADO — Multi-Agent Debate & Orchestration Platform · Technical Wiki

Welcome to the **MADO: Multi-Agent Debate & Orchestration Platform** technical documentation and context wiki. This documentation repository provides comprehensive knowledge about the system architecture, agent orchestration engine, Model Context Protocol (MCP) host integration, dynamic configuration, reactive UI, and air-gapped deployment mechanisms.

---

## 🌳 Wiki Tree Structure

```text
wiki/
├── README.md                              # Main wiki index, roadmap & system summary
├── architecture/
│   ├── overview.md                        # High-level architecture, technology stack & data flow
│   └── database-schema.md                 # SQLite + SQLAlchemy async ORM data models & relationships
├── configuration/
│   ├── conf-toml-reference.md             # Complete conf.toml configuration guide & schema
│   └── environment-variables.md           # Dynamic env var substitution, defaults & nested evaluation
├── agents/
│   ├── agent-pool-and-roles.md            # AgentPool registry, built-in specialist personas & styling
│   ├── session-personas.md                # Dynamic session persona lifecycle, drafts & freeze/locking
│   └── llm-integration.md                 # LiteLLM multi-provider abstraction, tool loops & failure handling
├── mcp/
│   ├── overview-and-protocol.md           # Stdio MCP host/client architecture & persistent sessions
│   ├── bundled-servers.md                 # Filesystem, Memory, Git, Sandbox, and Sequential Thinking
│   └── error-handling-resilience.md       # Tool failure handling, Stderr streaming tee & auto-reconnect
├── orchestration/
│   ├── engine-lifecycle.md                # 3-phase execution: Planning, Debate Loop & Synthesis
│   ├── debate-strategies.md               # Free Debate, Sequential Review & Adversarial Debate
│   └── artifact-generation.md             # Markdown reports, Mermaid diagrams, code & JSON summaries
├── ui/
│   ├── nicegui-fastapi.md                 # NiceGUI + FastAPI reactive SPA architecture & themes
│   └── components.md                      # Sidebar, Roster Control, Chat Feed, Artifact Viewer & Personas UI
└── operations/
    ├── getting-started.md                 # Local installation, setup scripts, execution & testing
    └── airgap-packaging.md                # Offline packaging bundle, portable runtimes & wheel installation
```

---

## 📌 Quick Topic Navigator

| Topic Area | Documentation Link | Key Subjects Covered |
| :--- | :--- | :--- |
| **Architecture** | [Overview](file:///d:/MultiAgentOrchestrator/wiki/architecture/overview.md)<br>[Database Schema](file:///d:/MultiAgentOrchestrator/wiki/architecture/database-schema.md) | FastAPI, NiceGUI, LiteLLM, Async SQLAlchemy, SQLite, Stdio MCP, StateGraph-inspired debate loops |
| **Configuration** | [conf.toml Reference](file:///d:/MultiAgentOrchestrator/wiki/configuration/conf-toml-reference.md)<br>[Environment Variables](file:///d:/MultiAgentOrchestrator/wiki/configuration/environment-variables.md) | TOML parsing, global `[llm]` inheritance, per-agent overrides, `${VAR:-default}` substitution |
| **Agents & Personas** | [Agent Pool & Roles](file:///d:/MultiAgentOrchestrator/wiki/agents/agent-pool-and-roles.md)<br>[Session Personas](file:///d:/MultiAgentOrchestrator/wiki/agents/session-personas.md)<br>[LLM Integration](file:///d:/MultiAgentOrchestrator/wiki/agents/llm-integration.md) | Master Orchestrator, System Architect, Senior Coder, Security Critic, session freeze/lock, offline simulator |
| **MCP Tool Protocol** | [Overview & Protocol](file:///d:/MultiAgentOrchestrator/wiki/mcp/overview-and-protocol.md)<br>[Bundled Servers](file:///d:/MultiAgentOrchestrator/wiki/mcp/bundled-servers.md)<br>[Resilience & Errors](file:///d:/MultiAgentOrchestrator/wiki/mcp/error-handling-resilience.md) | Stdio client lifecycle, long-lived server processes, tool dispatch, `isError: true` feedback, stderr tee |
| **Orchestration** | [Engine Lifecycle](file:///d:/MultiAgentOrchestrator/wiki/orchestration/engine-lifecycle.md)<br>[Debate Strategies](file:///d:/MultiAgentOrchestrator/wiki/orchestration/debate-strategies.md)<br>[Artifact Synthesis](file:///d:/MultiAgentOrchestrator/wiki/orchestration/artifact-generation.md) | Round-based debate, turn management, consensus criteria, multi-artifact parsing (Code, Markdown, Mermaid) |
| **User Interface** | [NiceGUI & FastAPI](file:///d:/MultiAgentOrchestrator/wiki/ui/nicegui-fastapi.md)<br>[UI Components](file:///d:/MultiAgentOrchestrator/wiki/ui/components.md) | Single Uvicorn process, Quasar dark mode, real-time WebSocket updates, folding tool call logs |
| **Operations** | [Getting Started](file:///d:/MultiAgentOrchestrator/wiki/operations/getting-started.md)<br>[Air-gap Packaging](file:///d:/MultiAgentOrchestrator/wiki/operations/airgap-packaging.md) | `setup_mcp.py`, `package_offline.py`, zero-dependency air-gapped bundles, version pinning |

---

## 🧭 System Overview at a Glance

The MADO: Multi-Agent Debate & Orchestration Platform is a full-stack Python application designed for autonomous multi-agent collaboration, peer review, and artifact synthesis using the Model Context Protocol (MCP).

```mermaid
graph TD
    User([Web User / Browser]) <-->|WebSocket / HTTP| UI[NiceGUI + FastAPI Reactive UI]
    UI <--> Engine[OrchestratorEngine]
    Engine <--> DB[(SQLite Database / aiosqlite)]
    Engine <--> Pool[AgentPool]
    
    Pool --> Orch[Master Orchestrator]
    Pool --> Arch[System Architect]
    Pool --> Coder[Senior Python Engineer]
    Pool --> Critic[Security & Quality Critic]
    
    Orch <--> LLM[LiteLLM Provider Layer]
    Arch <--> LLM
    Coder <--> LLM
    Critic <--> LLM
    
    Orch <--> MCP[MCPManager]
    Coder <--> MCP
    Critic <--> MCP
    Arch <--> MCP
    
    MCP <--> FS[Filesystem MCP Server]
    MCP <--> Mem[Memory Graph MCP Server]
    MCP <--> Git[Git Versioning MCP Server]
    MCP <--> Box[AirgappedPySandbox Server]
```

### Core Value Propositions
1. **Dynamic Configuration-Driven Profiling**: All agents, endpoints, credentials, models, and MCP bindings are defined in [conf.toml](file:///d:/MultiAgentOrchestrator/conf.toml) without touching source code.
2. **Session-Specific Persona Lock**: Custom personas and prompts can be customized per debate session. Once a debate begins (at the first user message), the full roster is snapshot into the database and locked to prevent persona drift.
3. **Robust Tool Calling via MCP**: Agents can inspect real files, maintain persistent knowledge graphs across token truncations, commit diffs to a Git repository, and execute Python code in an isolated IPython kernel sandbox.
4. **Air-Gap First Design**: The platform bundles offline runtimes (Node.js, CPython), reference MCP servers, and pinned wheels for zero-internet intranet deployment.
