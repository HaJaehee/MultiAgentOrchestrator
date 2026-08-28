from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from app.config import AgentConfig, SequentialThinkingConfig

# Color and avatar mappings for UI styling
AGENT_STYLE_MAP: Dict[str, Dict[str, str]] = {
    "orchestrator": {"avatar": "forum", "color": "indigo-8", "badge_color": "#3f51b5"},
    "architect": {"avatar": "account_tree", "color": "teal-8", "badge_color": "#009688"},
    "coder": {"avatar": "code", "color": "deep-purple-8", "badge_color": "#673ab7"},
    "critic": {"avatar": "security", "color": "amber-9", "badge_color": "#ff8f00"},
    "user": {"avatar": "chat_bubble", "color": "blue-grey-8", "badge_color": "#607d8b"},
}

DEFAULT_STYLE = {"avatar": "smart_toy", "color": "primary", "badge_color": "#1976d2"}


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
        style = AGENT_STYLE_MAP.get(key, DEFAULT_STYLE)
        return cls(key=key, **cfg.model_dump(), **style)
