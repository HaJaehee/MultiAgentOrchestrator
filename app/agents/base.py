import zlib
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
from app.config import DEFAULT_DEBATE_PRIORITY, AgentConfig, SequentialThinkingConfig

# Color and avatar mappings for UI styling
AGENT_STYLE_MAP: Dict[str, Dict[str, str]] = {
    "orchestrator": {"avatar": "forum", "color": "indigo-8", "badge_color": "#3f51b5"},
    "architect": {"avatar": "account_tree", "color": "teal-8", "badge_color": "#009688"},
    "coder": {"avatar": "code", "color": "deep-purple-8", "badge_color": "#673ab7"},
    "critic": {"avatar": "security", "color": "amber-9", "badge_color": "#ff8f00"},
    "user": {"avatar": "chat_bubble", "color": "blue-grey-8", "badge_color": "#607d8b"},
}

DEFAULT_STYLE = {"avatar": "smart_toy", "color": "primary", "badge_color": "#1976d2"}

# 화면에서 추가한 에이전트는 이 표에 없습니다. 전부 같은 회색 로봇으로 나오면
# 토론 피드에서 누가 말하는지 색으로 구분할 수 없으므로, 키에서 색을 하나
# 골라 줍니다. 키가 같으면 언제 어느 프로세스에서 보든 같은 색이어야 해서
# (파이썬의 문자열 hash 는 실행마다 달라집니다) crc32 를 씁니다.
CUSTOM_STYLE_PALETTE: List[Dict[str, str]] = [
    {"avatar": "psychology", "color": "cyan-8", "badge_color": "#0097a7"},
    {"avatar": "insights", "color": "pink-8", "badge_color": "#c2185b"},
    {"avatar": "science", "color": "light-green-8", "badge_color": "#689f38"},
    {"avatar": "travel_explore", "color": "orange-9", "badge_color": "#ef6c00"},
    {"avatar": "gavel", "color": "brown-7", "badge_color": "#6d4c41"},
    {"avatar": "diversity_3", "color": "deep-orange-8", "badge_color": "#e64a19"},
]


def style_for_agent(key: str) -> Dict[str, str]:
    """에이전트 키에 붙는 아바타/색. 표에 없는 키는 팔레트에서 고릅니다."""
    known = AGENT_STYLE_MAP.get(key)
    if known is not None:
        return known
    if not key:
        return DEFAULT_STYLE
    return CUSTOM_STYLE_PALETTE[zlib.crc32(key.encode("utf-8")) % len(CUSTOM_STYLE_PALETTE)]


class Agent(BaseModel):
    key: str
    name: str
    role: str
    enabled: bool = True
    model: str = "openai/gpt-4o"
    api_key: Optional[str] = ""
    api_base: Optional[str] = None
    api_version: Optional[str] = None
    provider: Optional[str] = None
    temperature: float = 0.7
    top_p: Optional[float] = None
    max_tokens: int = 4096
    max_context_window: int = 128000
    timeout: Optional[float] = None
    num_retries: int = 0
    drop_params: bool = True
    extra_headers: Dict[str, str] = Field(default_factory=dict)
    extra_body: Dict[str, Any] = Field(default_factory=dict)
    max_tool_iterations: int = 30
    allowed_mcp_servers: List[str] = Field(default_factory=list)
    # 토론에서의 자리. 전략이 이 값으로 순서와 진영을 정합니다 (에이전트 키를
    # 문자열로 박아 두던 방식을 대신합니다).
    debate_priority: int = DEFAULT_DEBATE_PRIORITY
    debate_stance: Literal["proponent", "critic", "neutral"] = "neutral"
    sequential_thinking: SequentialThinkingConfig = Field(default_factory=SequentialThinkingConfig)
    system_prompt: str = ""
    avatar: str = "forum"
    color: str = "primary"
    badge_color: str = "#1976d2"

    @property
    def is_live(self) -> bool:
        """True when the agent has enough connection info to reach a real LLM endpoint.

        False 면 발언 차례에 `LLMUnavailableError` 가 올라옵니다. 대체 응답은 없습니다.
        """
        if self.api_base:
            return True
        if self.api_key and self.api_key.strip():
            return True
        return self.model.split("/", 1)[0] in {"ollama", "ollama_chat", "lm_studio"}

    @property
    def endpoint_label(self) -> str:
        """Short human-readable endpoint description for the UI."""
        if self.api_base:
            return self.api_base
        return "provider default endpoint" if self.is_live else "no endpoint configured"

    @classmethod
    def from_config(cls, key: str, cfg: AgentConfig) -> "Agent":
        style = style_for_agent(key)
        return cls(key=key, **cfg.model_dump(), **style)
