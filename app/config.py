import copy
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from dotenv import load_dotenv
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# Load .env file if present
load_dotenv()

ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z0-9_]+)(?::-(.*?))?\}")

DEFAULT_SEQUENTIAL_THINKING_PROMPT = """[Sequential Thinking Protocol]
최종 답변을 작성하기 전에, 문제를 최대 {max_steps}단계로 나누어 순차적으로 사고하세요.
- `Thought 1..N` 형식으로 각 단계를 명시하고, 각 단계에서는 직전 단계의 결론을 근거로 다음 논리를 전개합니다.
- 중간에 가정이 틀렸다고 판단되면 `Revision of Thought K:` 로 해당 단계를 수정한 뒤 진행하세요.
- 도구(MCP)가 필요한 단계에서는 먼저 도구를 호출해 사실을 검증한 후 다음 단계로 넘어가세요.
- 사고가 끝나면 `---` 구분선 뒤에 `## 최종 결론`을 두고, 그 아래에는 사고 과정이 아닌 결과물만 작성하세요."""


VAR_NAME_PATTERN = re.compile(r"[A-Za-z0-9_]+")

# Python 으로 구동되는 MCP 서버는 기본적으로 **앱과 같은 인터프리터**로 띄웁니다.
# conf.toml 의 `${PYTHON_BIN:-python}` 이 PATH 의 python 으로 풀리면, 앱이 가상환경
# 에서 돌 때 의존성이 없는 다른 인터프리터를 가리켜 서버가 기동에 실패합니다.
# 사용자가 PYTHON_BIN 을 직접 지정했다면 그 값을 존중합니다.
if not os.environ.get("PYTHON_BIN"):
    os.environ["PYTHON_BIN"] = sys.executable


def _substitute_env(text: str) -> str:
    """Expands ${VAR} / ${VAR:-default}, where the default may itself contain ${...}."""
    out: List[str] = []
    i, n = 0, len(text)

    while i < n:
        if text[i] == "$" and i + 1 < n and text[i + 1] == "{":
            depth, j = 1, i + 2
            while j < n and depth:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                j += 1
            if depth:  # unbalanced braces: emit the remainder verbatim
                out.append(text[i:])
                return "".join(out)

            expr = text[i + 2 : j - 1]
            var_name, sep, default_expr = expr.partition(":-")
            var_name = var_name.strip()
            if VAR_NAME_PATTERN.fullmatch(var_name):
                resolved = os.environ.get(var_name, "")
                if not resolved and sep:
                    resolved = _substitute_env(default_expr)
                out.append(resolved)
            else:
                out.append(text[i:j])
            i = j
        else:
            out.append(text[i])
            i += 1

    return "".join(out)


def resolve_env_vars(value: Any) -> Any:
    """Recursively resolves ${VAR_NAME} or ${VAR_NAME:-default} strings using os.environ."""
    if isinstance(value, str):
        return _substitute_env(value)
    elif isinstance(value, dict):
        return {k: resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [resolve_env_vars(item) for item in value]
    return value


class AppConfig(BaseModel):
    host: str = Field(default="127.0.0.1", description="Application Host")
    port: int = Field(default=8000, description="Application Port")
    db_url: str = Field(
        default="sqlite+aiosqlite:///./multiagent.db",
        description="Async SQLAlchemy database URL",
    )
    debug: bool = Field(default=True, description="Debug mode")

    @field_validator("port", mode="before")
    @classmethod
    def _coerce_port(cls, v: Any) -> int:
        if isinstance(v, str):
            v = v.strip()
            return int(v) if v else 8000
        return int(v)


class MCPServerConfig(BaseModel):
    command: str = Field(description="Command to execute MCP server, e.g. npx or python")
    args: List[str] = Field(default_factory=list, description="CLI arguments")
    env: Dict[str, str] = Field(default_factory=dict, description="Environment variables for the server")
    enabled: bool = Field(default=True, description="Whether this MCP server is spawned at startup")


class SequentialThinkingConfig(BaseModel):
    """Step-by-step (Sequential Thinking) reasoning settings for an agent."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = Field(default=False, description="Enable sequential/step-by-step reasoning")
    mode: Literal["prompt", "native", "mcp"] = Field(
        default="prompt",
        description=(
            "prompt: inject a step-by-step protocol into the system prompt | "
            "native: use the provider's own reasoning parameters (reasoning_effort / thinking budget) | "
            "mcp: force usage of a sequential-thinking MCP server"
        ),
    )
    max_steps: int = Field(default=5, ge=1, le=50, description="Maximum number of reasoning steps")
    reasoning_effort: Optional[Literal["minimal", "low", "medium", "high"]] = Field(
        default=None, description="native mode: provider reasoning effort level"
    )
    thinking_budget_tokens: Optional[int] = Field(
        default=None, ge=0, description="native mode: extended-thinking token budget (Anthropic style)"
    )
    mcp_server: str = Field(
        default="sequential_thinking",
        description="mcp mode: MCP server key providing the sequentialthinking tool",
    )
    prompt_template: str = Field(
        default=DEFAULT_SEQUENTIAL_THINKING_PROMPT,
        description="prompt/mcp mode: text appended to the system prompt ({max_steps} placeholder supported)",
    )
    show_steps: bool = Field(
        default=True,
        description="Show reasoning steps in the debate feed (False keeps only the final conclusion)",
    )

    def render_prompt(self) -> str:
        """Renders the protocol text injected into the system prompt."""
        try:
            return self.prompt_template.format(max_steps=self.max_steps)
        except (KeyError, IndexError, ValueError):
            return self.prompt_template


# Fields an agent inherits from the global [llm] section when it does not set them itself.
INHERITABLE_LLM_FIELDS = (
    "model",
    "api_key",
    "api_base",
    "api_url",
    "base_url",
    "api_version",
    "provider",
    "custom_llm_provider",
    "temperature",
    "top_p",
    "max_tokens",
    "max_context_window",
    "timeout",
    "num_retries",
    "drop_params",
    "extra_headers",
    "extra_body",
    "max_tool_iterations",
)

# Aliases that refer to the same underlying field; setting any one blocks inheritance of the group.
_ALIAS_GROUPS = (
    ("api_base", "api_url", "base_url"),
    ("provider", "custom_llm_provider"),
)


def _is_blank(value: Any) -> bool:
    """None, empty string, or whitespace-only string (e.g. an unresolved env var)."""
    return value is None or (isinstance(value, str) and not value.strip())


class LLMConfig(BaseModel):
    """Global LLM defaults ([llm] section). Every agent inherits these unless it overrides them."""

    model_config = ConfigDict(populate_by_name=True)

    model: Optional[str] = Field(default=None, description="Default model name, e.g. 'openai/gpt-4o'")
    api_key: Optional[str] = Field(default=None, description="Default API key")
    api_base: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("api_base", "api_url", "base_url"),
        description="Default API endpoint URL (OpenAI-compatible gateway, Ollama, vLLM, LM Studio...)",
    )
    api_version: Optional[str] = Field(default=None, description="Default API version (Azure OpenAI)")
    provider: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("provider", "custom_llm_provider"),
        description="Force the LiteLLM provider, e.g. 'openai', 'azure', 'ollama', 'vertex_ai'",
    )
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    max_tokens: Optional[int] = Field(default=None, gt=0)
    max_context_window: Optional[int] = Field(default=None, gt=0)
    timeout: Optional[float] = Field(default=None, gt=0, description="Request timeout in seconds")
    num_retries: Optional[int] = Field(default=None, ge=0, description="LiteLLM retry count")
    drop_params: Optional[bool] = Field(
        default=None, description="Silently drop parameters the endpoint does not support"
    )
    extra_headers: Optional[Dict[str, str]] = Field(default=None, description="Extra HTTP headers")
    extra_body: Optional[Dict[str, Any]] = Field(default=None, description="Extra JSON body fields")
    max_tool_iterations: Optional[int] = Field(default=None, ge=1, le=50)
    sequential_thinking: SequentialThinkingConfig = Field(default_factory=SequentialThinkingConfig)

class AgentConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(description="Agent display name")
    role: str = Field(description="Agent role or title")
    enabled: bool = Field(default=True, description="Register this agent in the pool")
    model: str = Field(default="openai/gpt-4o", description="LLM model identifier")
    api_key: Optional[str] = Field(default="", description="API key or env var reference")
    api_base: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("api_base", "api_url", "base_url"),
        description="Custom API base URL (accepts api_base / api_url / base_url)",
    )
    api_version: Optional[str] = Field(default=None, description="API version (Azure OpenAI)")
    provider: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("provider", "custom_llm_provider"),
        description="Explicit LiteLLM provider override",
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int = Field(default=4096, gt=0)
    max_context_window: int = Field(default=128000, gt=0)
    timeout: Optional[float] = Field(default=None, gt=0, description="Request timeout in seconds")
    num_retries: int = Field(default=0, ge=0, description="LiteLLM retry count")
    drop_params: bool = Field(
        default=True, description="Silently drop parameters the endpoint does not support"
    )
    extra_headers: Dict[str, str] = Field(default_factory=dict, description="Extra HTTP headers")
    extra_body: Dict[str, Any] = Field(default_factory=dict, description="Extra JSON body fields")
    max_tool_iterations: int = Field(default=20, ge=1, le=50, description="Max MCP tool-loop iterations per turn")
    allowed_mcp_servers: List[str] = Field(
        default_factory=list, description="List of MCP server keys this agent can access"
    )
    sequential_thinking: SequentialThinkingConfig = Field(default_factory=SequentialThinkingConfig)
    system_prompt: str = Field(default="", description="Base system instructions")

    @field_validator("api_key", "api_base", "api_version", "provider", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        """Unresolved env vars ('${LLM_API_BASE}' with no value) resolve to '' -> treat as unset."""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("api_base")
    @classmethod
    def _strip_trailing_slash(cls, v: Optional[str]) -> Optional[str]:
        return v.rstrip("/") if isinstance(v, str) else v

    @property
    def is_live(self) -> bool:
        """True when the agent has enough connection info to reach a real LLM endpoint."""
        if self.api_base:
            return True
        if self.api_key and self.api_key.strip():
            return True
        # Local runtimes that need neither an API key nor an explicit URL
        return self.model.split("/", 1)[0] in {"ollama", "ollama_chat", "lm_studio"}


class RootConfig(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    mcp_servers: Dict[str, MCPServerConfig] = Field(default_factory=dict)
    agents: Dict[str, AgentConfig] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def apply_llm_defaults(cls, data: Any) -> Any:
        """Merges the global [llm] section into every agent that does not override a field."""
        if not isinstance(data, dict):
            return data

        llm_raw = data.get("llm") or {}
        agents_raw = data.get("agents") or {}
        if not isinstance(llm_raw, dict) or not llm_raw or not isinstance(agents_raw, dict):
            return data

        data = copy.deepcopy(data)
        llm_raw = data["llm"]
        agents_raw = data["agents"]

        for agent_raw in agents_raw.values():
            if not isinstance(agent_raw, dict):
                continue

            for field in INHERITABLE_LLM_FIELDS:
                if _is_blank(llm_raw.get(field)):
                    continue
                group = next((g for g in _ALIAS_GROUPS if field in g), (field,))
                # An agent value that resolved to an empty string (unset env var) still inherits.
                if any(not _is_blank(agent_raw.get(alias)) for alias in group):
                    continue
                agent_raw[field] = llm_raw[field]

            st_default = llm_raw.get("sequential_thinking")
            if isinstance(st_default, dict):
                merged = dict(st_default)
                st_agent = agent_raw.get("sequential_thinking")
                if isinstance(st_agent, dict):
                    merged.update(st_agent)
                agent_raw["sequential_thinking"] = merged

        return data

    @model_validator(mode="after")
    def validate_orchestrator_exists(self) -> "RootConfig":
        if "orchestrator" not in self.agents:
            raise ValueError("Configuration must contain an [agents.orchestrator] definition.")
        if not self.agents["orchestrator"].enabled:
            raise ValueError("The [agents.orchestrator] agent cannot be disabled (enabled = false).")
        return self

    @property
    def enabled_agents(self) -> Dict[str, AgentConfig]:
        return {k: v for k, v in self.agents.items() if v.enabled}

    @property
    def enabled_mcp_servers(self) -> Dict[str, MCPServerConfig]:
        return {k: v for k, v in self.mcp_servers.items() if v.enabled}


def load_config(config_path: str | Path = "conf.toml") -> RootConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found at: {path.resolve()}")

    with open(path, "rb") as f:
        raw_dict = tomllib.load(f)

    resolved_dict = resolve_env_vars(raw_dict)
    return RootConfig.model_validate(resolved_dict)


# Global singleton instance
_config: Optional[RootConfig] = None


def get_config(reload: bool = False, config_path: str | Path = "conf.toml") -> RootConfig:
    global _config
    if _config is None or reload:
        _config = load_config(config_path)
    return _config


def update_agent_persona_in_conf_file(
    agent_key: str,
    name: str,
    role: str,
    system_prompt: str,
    config_path: str | Path = "conf.toml",
) -> None:
    """Updates name, role, and system_prompt for [agents.<agent_key>] in conf.toml."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path.resolve()}")

    content = path.read_text(encoding="utf-8")

    # Format system prompt for TOML (use multiline string if contains newlines)
    clean_prompt = system_prompt.strip()
    if "\n" in clean_prompt:
        escaped_prompt = clean_prompt.replace('"""', r'\"\"\"')
        prompt_repr = f'"""{escaped_prompt}"""'
    else:
        escaped_prompt = clean_prompt.replace("\\", "\\\\").replace('"', r'\"')
        prompt_repr = f'"{escaped_prompt}"'

    escaped_name = name.strip().replace("\\", "\\\\").replace('"', r'\"')
    escaped_role = role.strip().replace("\\", "\\\\").replace('"', r'\"')

    target_header = f"[agents.{agent_key}]"

    lines = content.splitlines(keepends=True)
    in_section = False
    section_start = -1
    section_end = len(lines)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == target_header:
            in_section = True
            section_start = i
            continue
        if in_section and stripped.startswith("[") and stripped.endswith("]"):
            section_end = i
            break

    if section_start == -1:
        new_section = (
            f"\n\n{target_header}\n"
            f'name = "{escaped_name}"\n'
            f'role = "{escaped_role}"\n'
            f"system_prompt = {prompt_repr}\n"
        )
        content += new_section
    else:
        section_lines = lines[section_start:section_end]
        found_name = False
        found_role = False
        found_prompt = False

        new_section_lines = []
        skip_multiline_prompt = False

        for sline in section_lines:
            stripped = sline.strip()
            if skip_multiline_prompt:
                if '"""' in stripped or "'''" in stripped:
                    skip_multiline_prompt = False
                continue

            if re.match(r"^name\s*=", stripped):
                new_section_lines.append(f'name = "{escaped_name}"\n')
                found_name = True
            elif re.match(r"^role\s*=", stripped):
                new_section_lines.append(f'role = "{escaped_role}"\n')
                found_role = True
            elif re.match(r"^system_prompt\s*=", stripped):
                new_section_lines.append(f"system_prompt = {prompt_repr}\n")
                found_prompt = True
                if stripped.startswith('system_prompt = """') and stripped.count('"""') == 1:
                    skip_multiline_prompt = True
                elif stripped.startswith("system_prompt = '''") and stripped.count("'''") == 1:
                    skip_multiline_prompt = True
            else:
                new_section_lines.append(sline)

        insertions = []
        if not found_name:
            insertions.append(f'name = "{escaped_name}"\n')
        if not found_role:
            insertions.append(f'role = "{escaped_role}"\n')
        if not found_prompt:
            insertions.append(f"system_prompt = {prompt_repr}\n")

        if insertions:
            new_section_lines = [new_section_lines[0]] + insertions + new_section_lines[1:]

        lines = lines[:section_start] + new_section_lines + lines[section_end:]
        content = "".join(lines)

    path.write_text(content, encoding="utf-8")
    get_config(reload=True, config_path=config_path)

