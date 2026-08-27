# Bundled MCP Servers & Tool Capabilities

The platform comes pre-configured with a suite of official and open-source MCP servers. These servers provide file manipulation, knowledge retention across context truncations, git version tracking, and live Python code execution.

---

## 1. Bundled Server Roster

| Server Key | Runtime | Source Package / Repo | Tool Count | Default Status | Primary Responsibility |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `filesystem` | Node.js | `@modelcontextprotocol/server-filesystem` | 14 | **Enabled** | File read/write/search within `./workspace`. |
| `memory` | Node.js | `@modelcontextprotocol/server-memory` | 9 | **Enabled** | Knowledge graph persistence (`.memory-graph.json`). |
| `git` | Python | `mcp-server-git` (pip package) | 12 | **Enabled** | Repository version control & diff tracking. |
| `sandbox` | Python | [AirgappedPySandbox](https://github.com/HaJaehee/AirgappedPySandbox) | 5 | **Enabled** | Stateful IPython kernel code execution. |
| `sequential_thinking` | Node.js | `@modelcontextprotocol/server-sequential-thinking`| 1 | *Disabled* | Step-by-step reasoning (used only when `mode = "mcp"`). |
| `fetch` | Python | `mcp-server-fetch` (pip package) | 1 | *Disabled* | Web URL to Markdown extractor. **Keep disabled in air-gaps.** |

---

## 2. Server Profiles & Capabilities

### 2.1. Filesystem Server (`filesystem`)
- **Execution**: Direct Node.js entrypoint (`dist/index.js`).
- **Scope**: Strictly restricted to `./workspace`. Any attempt to access files outside this path is rejected at the protocol layer.
- **MCP Roots Capability**: The host registers a `list_roots_callback` returning `file:///.../workspace`, declaring client Roots support to eliminate "Client does not support MCP Roots" fallback warnings.
- **Key Tools**:
  - `read_text_file`: Inspects code or configuration files.
  - `write_file`: Creates new files in the workspace.
  - `edit_file`: Performs targeted edits on existing files.
  - `directory_tree`: Inspects project directory structures recursively.
  - `search_files`: Fast regex/glob search across the workspace.

### 2.2. Memory Knowledge Graph (`memory`)
- **Execution**: Node.js entrypoint. Persists graph to `./workspace/.memory-graph.json`.
- **Purpose**: When debates extend over multiple rounds, earlier context may be truncated due to token budget limits. The memory graph stores verified architectural decisions and agreed constraints as graph nodes and relations.
- **Key Tools**:
  - `create_entities`: Creates entities (e.g. `Database`, `AuthService`).
  - `create_relations`: Links entities (e.g. `AuthService --[authenticates]--> UserSession`).
  - `add_observations`: Appends factual observations to existing entities.
  - `search_nodes`: Searches for stored architectural decisions.

### 2.3. Git Versioning Server (`git`)
- **Execution**: Python module (`-m mcp_server_git --repository ./workspace`).
- **Prerequisite**: Requires `./workspace` to be an initialized git repository.
- **Corporate Intranet & Git Discovery**: [`find_git_executable()`](file:///d:/MultiAgentOrchestrator/app/mcp/manager.py) locates git across custom enterprise paths (e.g. `C:\Program Files\Git\cmd\git.exe` or `%LOCALAPPDATA%\Programs\Git\cmd\git.exe`).
- **Safe Directory Protection**: Executes `git config --global --add safe.directory "*"` automatically to prevent `fatal: detected dubious ownership in repository` errors in containerized or shared drive environments.
- **Workspace Auto-Init**: [`ensure_workspace()`](file:///d:/MultiAgentOrchestrator/app/mcp/manager.py) checks for `./workspace/.git` upon startup. If missing, it initializes the repository with `git init`, creates a `.gitkeep`, and creates the initial commit automatically.
- **Key Tools**:
  - `git_status`: Checks working tree changes.
  - `git_diff`: Inspects modifications introduced during a debate round.
  - `git_commit`: Commits verified functional increments.
  - `git_log`: Inspects change history across debate rounds.

### 2.4. Python Code Execution Sandbox (`sandbox`)
- **Repository**: [HaJaehee/AirgappedPySandbox](https://github.com/HaJaehee/AirgappedPySandbox)
- **Execution**: Embedded stateful IPython execution daemon.
- **Resilience & Default Namespace**: All sandbox tools default to `namespace="default"`, preventing tool call failures when LLMs omit the namespace argument. CLI argument parsing uses standard `--` prefixes instead of unicode em-dashes (`\u2014`).
- **Offline Kernel Requirements**: Core data science libraries (`requirements-kernel.txt`: `ipykernel`, `pandas`, `numpy`, `matplotlib`, `seaborn`, `scipy`, `sympy`) are pre-packaged into offline bundle wheels.
- **Why It Matters**: Prevents hallucination by allowing the Coder to verify implementations and empowering the Critic to test hypotheses and security boundaries with real code executions.
- **Key Tools**:
  - `execute_python_code`: Runs code snippets in an isolated namespace; maintains variables and imports across calls.
  - `run_python_file`: Executes a target script inside the workspace.
  - `reset_kernel_state`: Clears kernel state and memory when starting a clean session.

---

## 3. Agent Tool Allocation Matrix

Tools are distributed across agents according to their specialized responsibilities:

| Agent | Assigned MCP Servers | Purpose |
| :--- | :--- | :--- |
| **Orchestrator** | `filesystem`, `memory` | Read existing files, coordinate decisions, record facts. |
| **Architect** | `filesystem`, `memory`, `fetch` | Review project structure, query facts, inspect external specs. |
| **Coder** | `filesystem`, `sandbox`, `git` | Write code files, verify code execution, commit functional diffs. |
| **Critic** | `filesystem`, `sandbox`, `git`, `memory` | Inspect code, run stress/edge-case tests in sandbox, verify git diffs, record security findings. |

> **Security Note**: The `critic` is intentionally given access to the `sandbox` so that code review criticisms can be backed by empirical execution logs rather than theoretical assertions.
