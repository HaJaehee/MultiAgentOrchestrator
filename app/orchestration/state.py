from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from app.agents.base import Agent


class ArtifactItem(BaseModel):
    id: Optional[str] = None
    artifact_type: str = "markdown"  # 'code', 'markdown', 'mermaid', 'json'
    title: str
    content: str
    language: str = "markdown"


class DebateMessage(BaseModel):
    id: Optional[str] = None
    sender_key: str
    sender_name: str
    sender_role: str
    content: str
    round_number: int = 0
    msg_type: str = "agent"  # 'user', 'orchestrator', 'agent', 'system'
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)


class DebateState(BaseModel):
    session_id: str
    user_prompt: str
    strategy: str = "free_debate"
    max_rounds: int = 3
    current_round: int = 0
    custom_instructions: str = ""
    active_agent_keys: List[str] = Field(default_factory=list)
    messages: List[DebateMessage] = Field(default_factory=list)
    tool_records: List[Dict[str, Any]] = Field(default_factory=list)
    is_consensus_reached: bool = False
    artifacts: List[ArtifactItem] = Field(default_factory=list)
    status: str = "idle"  # 'idle', 'planning', 'debating', 'synthesizing', 'completed', 'error'
    current_speaker: Optional[str] = None
    error_message: Optional[str] = None
