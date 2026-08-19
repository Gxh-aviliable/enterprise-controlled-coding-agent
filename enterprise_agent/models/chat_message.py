"""Durable chat transcript records.

LangGraph checkpoints are intentionally short-lived execution state.  This
table is the user-facing source of truth for conversation history so a Redis
TTL expiry or service restart cannot make a MySQL session disappear.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    TIMESTAMP,
    BigInteger,
    Column,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import relationship

from enterprise_agent.db.mysql import Base


class ChatMessage(Base):
    """One durable user/assistant message in a conversation."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "trace_id",
            "role",
            name="uq_chat_messages_session_trace_role",
        ),
        Index("ix_chat_messages_session_id_id", "session_id", "id"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    trace_id = Column(String(36), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    content = Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=False, default="")
    status = Column(String(24), nullable=False, default="completed")
    source = Column(String(24), nullable=False, default="mysql")
    # Compact user-facing execution timeline.  This is intentionally a list of
    # rendered assistant/tool blocks rather than the raw per-token SSE event
    # stream, which keeps durable history bounded and frontend-oriented.
    timeline = Column(JSON, nullable=True, default=None)
    # Structured hand-off written when a trace is terminally cancelled.  It is
    # kept on the cancelled assistant row so the next trace can replan from
    # durable evidence even after the Redis checkpoint expires.
    continuation_receipt = Column(JSON, nullable=True, default=None)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        TIMESTAMP,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    session = relationship("Session", back_populates="messages")
