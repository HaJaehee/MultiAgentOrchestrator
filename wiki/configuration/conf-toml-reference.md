# conf.toml Configuration Reference

The [conf.toml](file:///d:/MultiAgentOrchestrator/conf.toml) file is the single source of truth for all runtime behaviors in the MADO: Multi-Agent Debate & Orchestration Platform. It dynamically configures the web application, default LLM provider options, MCP background servers, and all specialist agent personas.

The configuration file is loaded, validated, and normalized by [app/config.py](file:///d:/MultiAgentOrchestrator/app/config.py) using `tomllib` and Pydantic v2.

---

## 1. High-Level Schema Structure

```toml
[app]
# Application network and storage settings

[llm]
# Global LLM settings inherited by all agents
[llm.sequential_thinking]
# Global default for step-by-step reasoning

[mcp_servers.<name>]
# External/local MCP server processes (stdio)

[agents.<key>]
# Specialist agent definitions (overrides [llm])
[agents.<key>.sequential_thinking]
# Optional per-agent sequential thinking overrides
```

---

## 2. Section Specifications

### 2.1. `[app]` Section
Configures the FastAPI and NiceGUI host environment.

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `host` | `str` | `"127.0.0.1"` | IP address to bind Uvicorn server to. |
| `port` | `int` | `8000` | HTTP port for the web interface and API. |
| `db_url` | `str` | `"sqlite+aiosqlite:///./multiagent.db"` | SQLAlchemy async database connection URI. |
| `debug` | `bool` | `true` | Enables FastAPI auto-reload and verbose database logging. |

### 2.2. `[llm]` Global Section
Defines system-wide defaults. Any agent that does not explicitly set an attribute inherits the value defined here.

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `model` | `str` | `"openai/gpt-4o"` | Default model identifier in LiteLLM format (`<provider>/<model_name>`). |
| `api_base` | `str` | `null` | Base URL for LLM API requests. Accepts aliases `api_url` or `base_url`. |
| `api_key` | `str` | `null` | Global API key (can reference environment variables via `${OPENAI_API_KEY}`). |
| `api_version` | `str` | `null` | API version string (required for Azure OpenAI deployments). |
| `provider` | `str` | `null` | Force LiteLLM provider (e.g. `"openai"`, `"azure"`, `"ollama"`, `"vertex_ai"`). |
| `temperature` | `float` | `0.4` | Default sampling temperature (range: `0.0` to `2.0`). |
| `top_p` | `float` | `null` | Nucleus sampling probability cutoff. |
| `max_tokens` | `int` | `4096` | Maximum generation token budget per response. |
| `max_context_window`| `int` | `128000` | The model's context window. The transcript is trimmed to fit before every call — **set this to the endpoint's real limit**, or the endpoint answers with 400 instead. |
| `timeout` | `float` | `120.0` | HTTP request timeout in seconds. |
| `num_retries` | `int` | `2` | Number of automatic retries on network/rate-limit failure. |
| `drop_params` | `bool` | `true` | Silently drops unsupported parameters for local model compatibility. |
| `max_tool_iterations`| `int` | `30` | Maximum number of consecutive tool-call loops per agent turn. Exhausting it raises `LLMUnavailableError` rather than returning a placeholder answer. |
| `extra_headers` | `dict` | `{}` | Custom HTTP headers sent with every LLM request (e.g. gateway auth). |
| `extra_body` | `dict` | `{}` | Custom JSON body fields sent with requests. |

#### Inheritance Rules ([app/config.py](file:///d:/MultiAgentOrchestrator/app/config.py#L148-L175))
- If an agent leaves an inheritable field empty or whitespace-only (e.g., `${LLM_API_BASE}` with no value in the environment), the field resolves to `None` and inherits from `[llm]`.
- Setting any alias in an alias group (e.g., `api_base`, `api_url`, `base_url`) overrides the entire group.

### 2.3. Sequential Thinking Configuration (`[llm.sequential_thinking]`)
Controls step-by-step cognitive reasoning before generating final responses.

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `enabled` | `bool` | `false` | Enables sequential thinking for agents inheriting this section. |
| `mode` | `str` | `"prompt"` | Strategy mode: `"prompt"`, `"native"`, or `"mcp"`. |
| `max_steps` | `int` | `5` | Maximum reasoning steps (`1` to `50`). |
| `show_steps` | `bool` | `true` | If `false`, hides reasoning steps in UI and displays only final conclusions. |
| `reasoning_effort` | `str` | `null` | Native mode effort: `"minimal"`, `"low"`, `"medium"`, `"high"`. |
| `thinking_budget_tokens`| `int`| `null` | Native mode extended thinking token budget (Anthropic models). |
| `mcp_server` | `str` | `"sequential_thinking"` | MCP server identifier providing the `sequentialthinking` tool. |
| `prompt_template` | `str` | `...` | Custom reasoning protocol template supporting `{max_steps}` replacement. |

### 2.4. `[mcp_servers.<name>]` Section
Declares external MCP server processes launched and monitored by the backend.

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `command` | `str` | Required | Executable command (`node`, `python`, etc.). Supports `${NODE_BIN:-node}`. |
| `args` | `list[str]`| `[]` | Command-line arguments passed to the server process. |
| `env` | `dict[str, str]`| `{}` | Process environment variables injected into the child process. |
| `enabled` | `bool` | `true` | When `false`, the server is skipped during startup. |

> **Important**: Never use `npx` in air-gapped or offline production environments, as `npx` attempts to reach the npm registry if packages are not in the current working directory. Execute entrypoint scripts directly with `node` (e.g. `node ./mcp_node/node_modules/.../dist/index.js`).

### 2.5. `[agents.<key>]` Section
Configures specialist agents in the agent pool.

> These sections can be added, edited, and removed from the roster panel without restarting the app;
> the writers edit line ranges so comments and `${VAR}` placeholders survive. See
> [roster-editing.md](../agents/roster-editing.md).

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `name` | `str` | Required | Display name of the agent. |
| `role` | `str` | Required | Role title (e.g. `"System Architect"`, `"Senior Python Engineer"`). |
| `enabled` | `bool` | `true` | When `false`, the agent is omitted from the pool. |
| `model` | `str` | Inherited | Model override. |
| `api_base` | `str` | Inherited | Custom endpoint override. |
| `api_key` | `str` | Inherited | Custom API key override. |
| `temperature` | `float` | Inherited | Custom sampling temperature override. |
| `max_tokens` | `int` | Inherited | Token budget override. |
| `allowed_mcp_servers` | `list[str]` | `[]` | List of MCP server keys this agent is authorized to call. |
| `debate_priority` | `int` | `100` | Speaking order within a round; lower speaks first. Ties keep `conf.toml` order, so leaving every agent at the default speaks in file order. Rewritten as `10, 20, 30…` when cards are dragged in the roster. |
| `debate_stance` | `str` | `"neutral"` | `"proponent"` / `"critic"` / `"neutral"`. Read only by the adversarial strategy, which alternates the two sides. If no agent declares a side, that strategy degrades to a single priority-ordered pass. |
| `system_prompt` | `str` | `""` | Base persona instruction and behavioral guidelines. |
| `sequential_thinking` | `dict` | Inherited | Per-agent sequential thinking overrides (keys merge with `[llm]`). |

---

## 3. Live Mode vs. Unconfigured Agents

Each agent computes an `is_live` property dynamically ([app/config.py](file:///d:/MultiAgentOrchestrator/app/config.py#L269-L277)):

```python
@property
def is_live(self) -> bool:
    if self.api_base:
        return True
    if self.api_key and self.api_key.strip():
        return True
    # Local runtimes that need neither an API key nor an explicit URL
    return self.model.split("/", 1)[0] in {"ollama", "ollama_chat", "lm_studio"}
```

- **Live Execution**: If `api_base`, `api_key`, or a local provider prefix (`ollama/`, `lm_studio/`) is present, real network requests are dispatched via LiteLLM.
- **Keyless Local Endpoints**: When connecting to local servers without an API key (e.g. `vLLM` or `LM Studio` at `http://localhost:1234/v1`), LiteLLM requires a non-empty key parameter. The caller automatically injects a placeholder (`sk-no-key-required`) so local calls succeed.
- **Unconfigured Agents**: If an agent has neither an endpoint nor a key, its turn raises `LLMUnavailableError` and the debate records an explicit "연결 끊김" message in its place. Nothing is invented to fill the gap — see [llm-integration.md](../agents/llm-integration.md).

---

## 4. Configuration Precedence & Override Hierarchy

The platform applies settings through a strictly defined 3-tier precedence hierarchy:

```text
Environment Variables (.env) ──> conf.toml ──> Command-Line Arguments (CLI)
      (Lowest precedence)         (Base)              (Highest precedence)
```

1. **`.env` Environment Variables**:
   Variables defined in `.env` (or inherited from the parent shell) provide base configuration defaults and sensitive credentials.
2. **`conf.toml` File Configuration**:
   References environment variables via syntax such as `host = "${APP_HOST:-${HOST:-127.0.0.1}}"` and `port = "${APP_PORT:-${PORT:-8000}}"`. If the environment variable exists, it is substituted; otherwise, the default fallback is used.
3. **Command-Line Parameters (`app.main`)**:
   CLI arguments (`--host`, `--port`, `--config`, `--reload`, `--no-reload`) supersede any values found in both `.env` and `conf.toml`. For example:
   ```bash
   python -m app.main --host 0.0.0.0 --port 9000 --config custom_conf.toml
   ```
   binds to `0.0.0.0:9000` regardless of values defined in `.env` or `custom_conf.toml`.

