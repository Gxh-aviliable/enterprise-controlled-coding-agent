import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, TIMESTAMP, BigInteger, Column, Enum, ForeignKey, String
from sqlalchemy.orm import relationship

from enterprise_agent.db.mysql import Base


class SessionStatus(str, enum.Enum):
    """Session status enum"""
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class Session(Base):
    """Session model for conversation sessions"""
    __tablename__ = "sessions"

    id = Column(String(36), primary_key=True)  # UUID
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=True)
    status = Column(Enum(SessionStatus), default=SessionStatus.ACTIVE)
    session_metadata = Column("metadata", JSON, default=dict)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        TIMESTAMP,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user = relationship("User", back_populates="sessions")
    messages = relationship(
        "ChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
