import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(255), default="New Debate Session")
    strategy: Mapped[str] = mapped_column(String(50), default="free_debate")
    max_rounds: Mapped[int] = mapped_column(Integer, default=3)
    active_agents: Mapped[List[str]] = mapped_column(JSON, default=list)
    custom_instructions: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    messages: Mapped[List["MessageModel"]] = relationship(
        "MessageModel", back_populates="session", cascade="all, delete-orphan", order_by="MessageModel.created_at", lazy="selectin"
    )
    artifacts: Mapped[List["ArtifactModel"]] = relationship(
        "ArtifactModel", back_populates="session", cascade="all, delete-orphan", order_by="ArtifactModel.created_at", lazy="selectin"
    )
    tool_calls: Mapped[List["ToolCallRecordModel"]] = relationship(
        "ToolCallRecordModel", back_populates="session", cascade="all, delete-orphan", order_by="ToolCallRecordModel.created_at", lazy="selectin"
    )


class MessageModel(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    sender_key: Mapped[str] = mapped_column(String(50))  # e.g., 'user', 'orchestrator', 'architect'
    sender_name: Mapped[str] = mapped_column(String(100))
    sender_role: Mapped[str] = mapped_column(String(100), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    round_number: Mapped[int] = mapped_column(Integer, default=0)
    msg_type: Mapped[str] = mapped_column(String(30), default="agent")  # 'user', 'orchestrator', 'agent', 'system'
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped["SessionModel"] = relationship("SessionModel", back_populates="messages")
    tool_calls: Mapped[List["ToolCallRecordModel"]] = relationship(
        "ToolCallRecordModel", back_populates="message", cascade="all, delete-orphan", lazy="selectin"
    )


class ToolCallRecordModel(Base):
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    agent_key: Mapped[str] = mapped_column(String(50))
    tool_name: Mapped[str] = mapped_column(String(100))
    arguments: Mapped[Any] = mapped_column(JSON, default=dict)
    output: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="success")  # 'success', 'error'
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped["SessionModel"] = relationship("SessionModel", back_populates="tool_calls")
    message: Mapped[Optional["MessageModel"]] = relationship("MessageModel", back_populates="tool_calls")


class ArtifactModel(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    artifact_type: Mapped[str] = mapped_column(String(30), default="markdown")  # 'code', 'markdown', 'mermaid', 'json'
    title: Mapped[str] = mapped_column(String(255), default="Synthesized Artifact")
    content: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(50), default="markdown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped["SessionModel"] = relationship("SessionModel", back_populates="artifacts")
