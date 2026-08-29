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

# 프로젝트 루트 = app/ 의 부모. cwd 가 아니라 이 값을 기준으로 삼습니다.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 작업 공간은 **절대 경로**로 고정합니다.
#
# conf.toml 이 `${WORKSPACE_DIR:-./workspace}` 로 상대 경로를 넘기면, 그 값을
# 받는 쪽이 각자의 cwd 로 resolve 합니다. filesystem MCP(node)와 sandbox MCP
# (python)는 서로 다른 프로세스이고, 특히 샌드박스는 받은 값을
# `Path(v).resolve()` 로 자기 cwd 기준으로 풉니다. 그래서 "같은 ./workspace 를
# 줬는데 두 서버가 다른 폴더를 본다" 가 됩니다.
#
# 여기서 한 번 절대 경로로 만들어 두면 argv 로 가든 env 로 가든 같은 곳을
# 가리킵니다.
def resolve_workspace_dir(value: Optional[str] = None) -> Path:
    """작업 공간 경로를 절대 경로로 정규화합니다. 비어 있으면 `<루트>/workspace`."""
    raw = (value or "").strip()
    if not raw:
        return PROJECT_ROOT / "workspace"
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


os.environ["WORKSPACE_DIR"] = str(resolve_workspace_dir(os.environ.get("WORKSPACE_DIR")))


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
    max_tool_iterations: int = Field(default=30, ge=1, le=50, description="Max MCP tool-loop iterations per turn")
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

    # 치환 **전** 의 [mcp_servers] 원문. 작업 공간을 런타임에 바꿀 때 이걸
    # 새 WORKSPACE_DIR 로 다시 풉니다. 이미 치환된 경로 문자열을 찾아 바꾸는
    # 것보다 정확합니다 — 같은 치환기를 그대로 한 번 더 돌리는 것이니까요.
    raw_mcp_servers: Dict[str, Any] = Field(default_factory=dict, exclude=True)

    def mcp_servers_for_workspace(self, workspace: Path) -> Dict[str, MCPServerConfig]:
        """`WORKSPACE_DIR` 를 바꿔 놓고 [mcp_servers] 를 다시 해석합니다.

        환경변수를 실제로 바꾸는 것은 의도한 부작용입니다. MCP 서버는 자식
        프로세스로 뜨므로 이 값을 물려받아야 하고, Roots 응답도 이걸 읽습니다.
        """
        os.environ["WORKSPACE_DIR"] = str(workspace)
        if not self.raw_mcp_servers:
            return self.enabled_mcp_servers
        resolved = resolve_env_vars(self.raw_mcp_servers)
        servers = {name: MCPServerConfig.model_validate(cfg) for name, cfg in resolved.items()}
        # 매니저는 켜져 있는 서버만 띄웁니다 (`enabled_mcp_servers` 와 같은 기준).
        return {name: cfg for name, cfg in servers.items() if cfg.enabled}

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
    config = RootConfig.model_validate(resolved_dict)
    config.raw_mcp_servers = raw_dict.get("mcp_servers") or {}
    return config


# Global singleton instance
_config: Optional[RootConfig] = None
# 그 싱글턴을 어느 파일에서 읽었는지. 아래 persona 갱신이 "지금 쓰는 설정"인지
# 판단하는 데 씁니다.
_config_path: Optional[Path] = None


def get_config(reload: bool = False, config_path: str | Path = "conf.toml") -> RootConfig:
    global _config, _config_path
    if _config is None or reload:
        _config = load_config(config_path)
        _config_path = Path(config_path).resolve()
    return _config


def active_config_path() -> Optional[Path]:
    """현재 전역 설정을 읽어 온 파일 경로 (아직 안 읽었으면 None)."""
    return _config_path


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
    reload_config_if_active(path)


def reload_config_if_active(path: Path) -> bool:
    """방금 쓴 파일이 **지금 앱이 쓰고 있는 설정일 때만** 다시 읽습니다.

    조건 없이 다시 읽으면, 다른 파일을 고쳤을 뿐인데 전역 설정이 그 파일로
    갈아끼워집니다. 그 뒤의 에이전트 풀·MCP 서버 목록이 통째로 바뀝니다.
    """
    active = active_config_path()
    if active is not None and Path(path).resolve() == active:
        get_config(reload=True, config_path=path)
        return True
    return False


# ---------------------------------------------------------------------------
# [mcp_servers.*] 편집
#
# MCP 서버 구성은 배포 설정입니다. 대화별로 갈리는 값(작업 공간 등)과 달리 이
# 파일이 정본이고, 화면에서 바꾼 것도 여기 남아야 다음 기동에서 살아납니다.
#
# tomllib 로 읽어 다시 쓰지 않고 줄 단위로 고칩니다. conf.toml 의 절반은 어떤
# 서버가 무엇을 하고 왜 꺼져 있는지를 적어 둔 주석이고, 파이썬 표준 라이브러리에는
# TOML 기록기가 없어 통째로 다시 쓰면 그 주석이 전부 사라집니다.
# ---------------------------------------------------------------------------

# TOML 의 bare key. 따옴표가 필요한 이름은 받지 않습니다 (서버 이름은 도구
# 이름의 접두사로도 쓰이므로 단순해야 합니다).
BARE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")


def _toml_string(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_array(values: List[str]) -> str:
    if not values:
        return "[]"
    single_line = "[" + ", ".join(_toml_string(v) for v in values) + "]"
    if len(single_line) <= 96:
        return single_line
    body = "".join(f"    {_toml_string(v)},\n" for v in values)
    return f"[\n{body}]"


def _toml_inline_table(mapping: Dict[str, str]) -> str:
    items = ", ".join(f"{key} = {_toml_string(val)}" for key, val in mapping.items())
    return "{ " + items + " }"


TOML_MULTILINE_DELIMITERS = ('"""', "'''")


def _advance_multiline_state(line: str, state: Optional[str]) -> Optional[str]:
    """줄 하나를 지나간 뒤의 여러 줄 문자열 상태 (열려 있으면 그 구분자)."""
    i = 0
    while i < len(line):
        if state is None:
            opener = next((d for d in TOML_MULTILINE_DELIMITERS if line.startswith(d, i)), None)
            if opener is not None:
                state = opener
                i += len(opener)
                continue
        elif line.startswith(state, i):
            i += len(state)
            state = None
            continue
        i += 1
    return state


def _find_toml_section(lines: List[str], header: str) -> tuple[int, int]:
    """`header` 섹션이 차지하는 줄 범위 `[start, end)`. 없으면 `(-1, -1)`.

    끝 경계는 다음 섹션 헤더 앞의 주석·빈 줄을 제외합니다. 이 파일에서 주석은
    항상 뒤따르는 섹션을 설명하므로, 그것까지 지우거나 그 사이에 끼워 넣으면
    남의 설명이 사라지거나 엉뚱한 서버에 붙습니다.

    여러 줄 문자열 안은 건너뜁니다. `[agents.*]` 의 system_prompt 는 사용자가
    쓰는 글이라 `[검토 항목]` 같은 줄이 얼마든지 들어갑니다. 그것을 섹션 헤더로
    읽으면 뒤따르는 설정을 남의 섹션 것으로 취급하게 됩니다.
    """
    state: Optional[str] = None
    start = -1
    end = len(lines)

    for i, line in enumerate(lines):
        at_top_level = state is None
        stripped = line.strip()
        if at_top_level and start == -1 and stripped == header:
            start = i
        elif at_top_level and start != -1 and stripped.startswith("[") and stripped.endswith("]"):
            end = i
            break
        state = _advance_multiline_state(line, state)

    if start == -1:
        return -1, -1

    while end - 1 > start:
        prev = lines[end - 1].strip()
        if prev == "" or prev.startswith("#"):
            end -= 1
        else:
            break
    return start, end


def _mcp_section_header(server_name: str) -> str:
    if not BARE_KEY_PATTERN.fullmatch(server_name or ""):
        raise ValueError(
            f"MCP 서버 이름 '{server_name}' 을 쓸 수 없습니다. "
            f"영문/숫자/밑줄/하이픈만 쓰고 숫자나 하이픈으로 시작하지 마세요."
        )
    return f"[mcp_servers.{server_name}]"


def set_mcp_server_enabled_in_conf_file(
    server_name: str,
    enabled: bool,
    config_path: str | Path = "conf.toml",
) -> None:
    """[mcp_servers.<name>] 의 enabled 값을 바꿉니다."""
    path = Path(config_path)
    header = _mcp_section_header(server_name)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    start, end = _find_toml_section(lines, header)
    if start == -1:
        raise KeyError(f"conf.toml 에 {header} 섹션이 없습니다.")

    value = "true" if enabled else "false"
    for i in range(start + 1, end):
        if re.match(r"^\s*enabled\s*=", lines[i]):
            lines[i] = f"enabled = {value}\n"
            break
    else:
        lines.insert(end, f"enabled = {value}\n")

    path.write_text("".join(lines), encoding="utf-8")
    reload_config_if_active(path)


def add_mcp_server_to_conf_file(
    server_name: str,
    command: str,
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    enabled: bool = True,
    config_path: str | Path = "conf.toml",
) -> None:
    """새 [mcp_servers.<name>] 섹션을 추가합니다.

    마지막 MCP 서버 섹션 바로 뒤에 넣습니다. 파일 끝에 붙여도 TOML 로는
    같지만, 설정 파일은 사람이 읽는 문서이기도 하므로 같은 무리에 둡니다.
    """
    path = Path(config_path)
    header = _mcp_section_header(server_name)
    command = (command or "").strip()
    if not command:
        raise ValueError("실행 명령(command)은 비워 둘 수 없습니다.")

    args = [a for a in (args or []) if a.strip() != ""]
    env = env or {}
    for key in env:
        if not BARE_KEY_PATTERN.fullmatch(key):
            raise ValueError(f"환경변수 이름 '{key}' 을 쓸 수 없습니다.")

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if _find_toml_section(lines, header)[0] != -1:
        raise ValueError(f"'{server_name}' 서버가 이미 conf.toml 에 있습니다.")

    insert_at = None
    for line in list(lines):
        stripped = line.strip()
        if stripped.startswith("[mcp_servers.") and stripped.endswith("]"):
            _, section_end = _find_toml_section(lines, stripped)
            insert_at = section_end if insert_at is None else max(insert_at, section_end)

    block = [
        f"{header}\n",
        f"command = {_toml_string(command)}\n",
        f"args = {_toml_array(args)}\n",
    ]
    if env:
        block.append(f"env = {_toml_inline_table(env)}\n")
    block.append(f"enabled = {'true' if enabled else 'false'}\n")

    if insert_at is None:
        # MCP 서버 섹션이 하나도 없는 설정. 파일 끝에 붙입니다.
        tail = "" if not lines or lines[-1].endswith("\n") else "\n"
        lines = lines + [tail + "\n"] + block
    else:
        lines = lines[:insert_at] + ["\n"] + block + lines[insert_at:]

    path.write_text("".join(lines), encoding="utf-8")
    reload_config_if_active(path)


def set_agent_allowed_mcp_servers_in_conf_file(
    agent_key: str,
    servers: List[str],
    config_path: str | Path = "conf.toml",
) -> None:
    """[agents.<key>].allowed_mcp_servers 를 통째로 갈아 끼웁니다.

    어떤 에이전트가 어떤 도구를 쓰는지는 배포 설정이라 conf.toml 이 정본입니다.
    다만 **아직 시작하지 않은 대화**에만 걸립니다. 대화는 첫 발언과 함께 도구
    권한까지 `session_agents.config_snapshot` 으로 굳으므로, 이미 시작한 대화는
    여기서 무엇을 바꾸든 그때의 권한을 그대로 씁니다.
    """
    path = Path(config_path)
    if not BARE_KEY_PATTERN.fullmatch(agent_key or ""):
        raise ValueError(f"에이전트 키 '{agent_key}' 을 쓸 수 없습니다.")
    for name in servers:
        if not BARE_KEY_PATTERN.fullmatch(name or ""):
            raise ValueError(f"MCP 서버 이름 '{name}' 을 쓸 수 없습니다.")

    header = f"[agents.{agent_key}]"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start, end = _find_toml_section(lines, header)
    if start == -1:
        raise KeyError(f"conf.toml 에 {header} 섹션이 없습니다.")

    new_line = f"allowed_mcp_servers = {_toml_array(list(servers))}\n"

    for i in range(start + 1, end):
        if not re.match(r"^\s*allowed_mcp_servers\s*=", lines[i]):
            continue
        # 배열이 여러 줄에 걸쳐 있으면 닫는 대괄호까지 함께 걷어냅니다.
        last = i
        depth = lines[i].count("[") - lines[i].count("]")
        while depth > 0 and last + 1 < end:
            last += 1
            depth += lines[last].count("[") - lines[last].count("]")
        lines[i:last + 1] = [new_line]
        break
    else:
        lines.insert(start + 1, new_line)

    path.write_text("".join(lines), encoding="utf-8")
    reload_config_if_active(path)


def remove_mcp_server_from_conf_file(
    server_name: str,
    config_path: str | Path = "conf.toml",
) -> None:
    """[mcp_servers.<name>] 섹션을 지웁니다.

    섹션 위에 붙은 설명 주석은 남깁니다. 사람이 쓴 글을 지우는 것은 되돌릴 수
    없고, 같은 서버를 다시 추가할 때 그대로 쓸 수 있는 정보이기 때문입니다.
    """
    path = Path(config_path)
    header = _mcp_section_header(server_name)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    start, end = _find_toml_section(lines, header)
    if start == -1:
        raise KeyError(f"conf.toml 에 {header} 섹션이 없습니다.")

    remaining = lines[:start] + lines[end:]

    # 섹션 앞뒤의 빈 줄이 이어 붙어 두 줄이 되거나, 파일 끝에 빈 줄만 남는 경우를
    # 정리합니다. 지웠다 다시 추가하기를 반복해도 파일이 늘어지지 않습니다.
    while start > 0 and not remaining[start - 1].strip() and (
        start >= len(remaining) or not remaining[start].strip()
    ):
        del remaining[start - 1]
        start -= 1

    path.write_text("".join(remaining), encoding="utf-8")
    reload_config_if_active(path)



# ---------------------------------------------------------------------------
# [agents.*] 추가 / 끄기 / 삭제
#
# 에이전트 구성도 MCP 서버와 같은 배포 설정입니다. 대화마다 갈리는 페르소나(이름·
# 역할·시스템 프롬프트)와 달리 conf.toml 이 정본이고, 화면에서 바꾼 것이 다음
# 기동에서도 살아 있어야 합니다.
#
# 화면은 새 에이전트를 만들 때 [llm] 기본값을 미리 채워 보여주지만, **사용자가
# 실제로 바꾼 값만** 파일에 적습니다. 화면이 보는 값은 이미 환경변수가 풀린
# 값이라, 그대로 되쓰면 해석된 API 키가 conf.toml 에 평문으로 박히고 .env 를
# 바꿔도 따라오지 않게 됩니다. 손대지 않은 항목은 아예 적지 않아 [llm] 을 그대로
# 상속합니다.
# ---------------------------------------------------------------------------

# 화면에서 채울 수 있고 [llm] 에서 상속되는 항목. 여기 적힌 순서가 파일에 적히는
# 순서입니다.
AGENT_OVERRIDE_FIELDS = (
    "model",
    "api_base",
    "api_key",
    "api_version",
    "provider",
    "temperature",
    "top_p",
    "max_tokens",
    "max_context_window",
    "timeout",
    "num_retries",
    "drop_params",
    "max_tool_iterations",
)

SEQUENTIAL_THINKING_FIELDS = ("enabled", "mode", "max_steps", "show_steps")


def agent_defaults_from_llm(llm: Optional[LLMConfig] = None) -> Dict[str, Any]:
    """새 에이전트 폼에 미리 채울 값.

    [llm] 에 값이 있으면 그것을, 없으면 `AgentConfig` 의 기본값을 씁니다. 곧
    "아무것도 적지 않은 에이전트가 실제로 갖게 될 값" 입니다. 이 값과 같은 입력은
    `prune_agent_overrides()` 가 걷어내어 파일에 적지 않습니다.
    """
    source = llm if llm is not None else get_config().llm
    defaults: Dict[str, Any] = {}
    for field in AGENT_OVERRIDE_FIELDS:
        value = getattr(source, field, None)
        if _is_blank(value):
            value = AgentConfig.model_fields[field].get_default(call_default_factory=True)
        defaults[field] = value
    return defaults


def _same_value(a: Any, b: Any) -> bool:
    """화면 입력값과 기본값이 같은 값인지. 폼은 숫자도 문자열로 돌려줍니다."""
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) is bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return str(a).strip() == str(b).strip()


def prune_agent_overrides(
    values: Dict[str, Any], defaults: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """기본값과 같거나 비어 있는 항목을 걷어냅니다.

    남은 것만 [agents.<key>] 에 적히고, 걷어낸 항목은 [llm] 에서 상속됩니다.
    """
    defaults = defaults if defaults is not None else agent_defaults_from_llm()
    pruned: Dict[str, Any] = {}
    for field in AGENT_OVERRIDE_FIELDS:
        if field not in values:
            continue
        value = values[field]
        if _is_blank(value):
            continue  # 빈 칸은 "지정하지 않음" 입니다
        default = defaults.get(field)
        if not _is_blank(default) and _same_value(value, default):
            continue
        pruned[field] = value
    return pruned


def _toml_multiline_string(value: str) -> str:
    """줄바꿈이 있으면 여러 줄 문자열로, 아니면 한 줄 문자열로 적습니다."""
    text = str(value).strip()
    if "\n" not in text:
        return _toml_string(text)
    return '"""' + text.replace('"""', r"\"\"\"") + '"""'


def _toml_value(value: Any) -> str:
    # bool 은 int 의 하위형이라 반드시 먼저 봅니다.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return _toml_multiline_string(value)
    if isinstance(value, (list, tuple)):
        return _toml_array([str(v) for v in value])
    if isinstance(value, dict):
        return _toml_inline_table({str(k): str(v) for k, v in value.items()})
    raise ValueError(f"conf.toml 에 적을 수 없는 값입니다: {value!r}")


def _agent_section_header(agent_key: str) -> str:
    if not BARE_KEY_PATTERN.fullmatch(agent_key or ""):
        raise ValueError(
            f"에이전트 키 '{agent_key}' 을 쓸 수 없습니다. "
            f"영문/숫자/밑줄/하이픈만 쓰고 숫자나 하이픈으로 시작하지 마세요."
        )
    return f"[agents.{agent_key}]"


def _top_level_headers(lines: List[str]) -> List[str]:
    """파일에 있는 섹션 헤더 목록. 여러 줄 문자열 안은 건너뜁니다.

    system_prompt 에 `[검토 항목]` 같은 줄이 얼마든지 들어가므로, 그것을 섹션
    헤더로 세면 안 됩니다 (`_find_toml_section` 과 같은 이유).
    """
    headers: List[str] = []
    state: Optional[str] = None
    for line in lines:
        stripped = line.strip()
        if state is None and stripped.startswith("[") and stripped.endswith("]"):
            headers.append(stripped)
        state = _advance_multiline_state(line, state)
    return headers


def _set_key_in_section(
    lines: List[str], start: int, end: int, key: str, rendered: str
) -> None:
    """섹션 안의 `key = ...` 한 줄을 갈아 끼우거나, 없으면 머리에 넣습니다.

    여러 줄 문자열 안은 건너뜁니다. system_prompt 본문에 우연히 `enabled = ...`
    으로 시작하는 줄이 있어도 그것을 설정값으로 착각하지 않습니다.
    """
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    state: Optional[str] = None
    for i in range(start + 1, end):
        if state is None and pattern.match(lines[i]):
            lines[i] = f"{key} = {rendered}\n"
            return
        state = _advance_multiline_state(lines[i], state)
    lines.insert(start + 1, f"{key} = {rendered}\n")


def add_agent_to_conf_file(
    agent_key: str,
    name: str,
    role: str,
    system_prompt: str = "",
    allowed_mcp_servers: Optional[List[str]] = None,
    overrides: Optional[Dict[str, Any]] = None,
    sequential_thinking: Optional[Dict[str, Any]] = None,
    enabled: bool = True,
    config_path: str | Path = "conf.toml",
) -> None:
    """새 [agents.<key>] 섹션을 추가합니다.

    `overrides` 에는 `prune_agent_overrides()` 를 지난 값만 넘기세요. 여기 없는
    항목은 파일에 적히지 않고 [llm] 에서 상속됩니다.

    마지막 [agents.*] 섹션 바로 뒤에 넣습니다. 파일 끝에 붙여도 TOML 로는 같지만,
    설정 파일은 사람이 읽는 문서이기도 하므로 같은 무리에 둡니다.
    """
    path = Path(config_path)
    header = _agent_section_header(agent_key)

    name = (name or "").strip()
    role = (role or "").strip()
    if not name:
        raise ValueError("에이전트 이름은 비워 둘 수 없습니다.")
    if not role:
        raise ValueError("에이전트 역할은 비워 둘 수 없습니다.")

    servers = [s.strip() for s in (allowed_mcp_servers or []) if s and s.strip()]
    for server in servers:
        if not BARE_KEY_PATTERN.fullmatch(server):
            raise ValueError(f"MCP 서버 이름 '{server}' 을 쓸 수 없습니다.")

    overrides = dict(overrides or {})
    unknown = [k for k in overrides if k not in AGENT_OVERRIDE_FIELDS]
    if unknown:
        raise ValueError(f"모르는 설정 항목입니다: {', '.join(sorted(unknown))}")

    thinking = dict(sequential_thinking or {})
    unknown_st = [k for k in thinking if k not in SEQUENTIAL_THINKING_FIELDS]
    if unknown_st:
        raise ValueError(f"모르는 단계적 사고 항목입니다: {', '.join(sorted(unknown_st))}")

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if _find_toml_section(lines, header)[0] != -1:
        raise ValueError(f"'{agent_key}' 에이전트가 이미 conf.toml 에 있습니다.")

    block = [
        f"{header}\n",
        f"name = {_toml_string(name)}\n",
        f"role = {_toml_string(role)}\n",
    ]
    if not enabled:
        block.append("enabled = false\n")
    for field in AGENT_OVERRIDE_FIELDS:
        if field in overrides:
            block.append(f"{field} = {_toml_value(overrides[field])}\n")
    block.append(f"allowed_mcp_servers = {_toml_array(servers)}\n")
    if (system_prompt or "").strip():
        block.append(f"system_prompt = {_toml_multiline_string(system_prompt)}\n")
    if thinking:
        block.append(f"\n[agents.{agent_key}.sequential_thinking]\n")
        for field in SEQUENTIAL_THINKING_FIELDS:
            if field in thinking:
                block.append(f"{field} = {_toml_value(thinking[field])}\n")

    insert_at = None
    for existing in _top_level_headers(lines):
        if not existing.startswith("[agents."):
            continue
        _, section_end = _find_toml_section(lines, existing)
        insert_at = section_end if insert_at is None else max(insert_at, section_end)

    if insert_at is None:
        # [agents.*] 가 하나도 없는 설정. (오케스트레이터가 없으면 어차피 검증에서
        # 걸리지만, 파일 편집기로서는 동작해야 합니다.) 파일 끝에 붙입니다.
        tail = "" if not lines or lines[-1].endswith("\n") else "\n"
        lines = lines + [tail + "\n"] + block
    else:
        lines = lines[:insert_at] + ["\n"] + block + lines[insert_at:]

    path.write_text("".join(lines), encoding="utf-8")
    reload_config_if_active(path)


def set_agent_enabled_in_conf_file(
    agent_key: str,
    enabled: bool,
    config_path: str | Path = "conf.toml",
) -> None:
    """[agents.<key>] 의 enabled 값을 바꿉니다.

    끈 에이전트는 풀에 등록되지 않아 어떤 대화에도 참여하지 않습니다. 삭제와 다른
    점은 설정과 프롬프트가 파일에 그대로 남아 언제든 되살릴 수 있다는 것입니다.
    """
    if agent_key == "orchestrator" and not enabled:
        raise ValueError(
            "오케스트레이터는 끌 수 없습니다. 토론 진행과 최종 합성을 맡습니다."
        )
    path = Path(config_path)
    header = _agent_section_header(agent_key)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    start, end = _find_toml_section(lines, header)
    if start == -1:
        raise KeyError(f"conf.toml 에 {header} 섹션이 없습니다.")

    _set_key_in_section(lines, start, end, "enabled", "true" if enabled else "false")

    path.write_text("".join(lines), encoding="utf-8")
    reload_config_if_active(path)


def remove_agent_from_conf_file(
    agent_key: str,
    config_path: str | Path = "conf.toml",
) -> None:
    """[agents.<key>] 와 그 하위 블록([agents.<key>.sequential_thinking] 등)을 지웁니다.

    섹션 위에 붙은 설명 주석은 남깁니다. 사람이 쓴 글을 지우는 것은 되돌릴 수 없기
    때문입니다 (MCP 서버 삭제와 같은 규칙).
    """
    if agent_key == "orchestrator":
        raise ValueError(
            "오케스트레이터는 삭제할 수 없습니다. 토론 진행과 최종 합성을 맡습니다."
        )
    path = Path(config_path)
    header = _agent_section_header(agent_key)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    prefix = f"[agents.{agent_key}."
    targets = [h for h in _top_level_headers(lines) if h == header or h.startswith(prefix)]
    if header not in targets:
        raise KeyError(f"conf.toml 에 {header} 섹션이 없습니다.")

    # 지울 때마다 줄 번호가 밀리므로 대상마다 다시 찾습니다.
    for target in targets:
        start, end = _find_toml_section(lines, target)
        if start == -1:
            continue
        lines = lines[:start] + lines[end:]

        # 섹션 앞뒤의 빈 줄이 이어 붙어 두 줄이 되거나, 파일 끝에 빈 줄만 남는
        # 경우를 정리합니다.
        while start > 0 and not lines[start - 1].strip() and (
            start >= len(lines) or not lines[start].strip()
        ):
            del lines[start - 1]
            start -= 1

    path.write_text("".join(lines), encoding="utf-8")
    reload_config_if_active(path)
