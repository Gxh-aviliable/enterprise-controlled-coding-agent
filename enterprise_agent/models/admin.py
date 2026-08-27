"""Persistence models for the internal administration control plane."""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    TIMESTAMP,
    BigInteger,
    Boolean,
    Column,
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from enterprise_agent.db.mysql import Base


def utcnow():
    return datetime.now(timezone.utc)


class UserQuota(Base):
    """Per-user metered limits plus an always-on concurrency boundary."""

    __tablename__ = "user_quotas"

    user_id = Column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    daily_task_limit = Column(Integer, nullable=False, default=50)
    daily_token_limit = Column(BigInteger, nullable=False, default=500_000)
    monthly_token_limit = Column(BigInteger, nullable=False, default=5_000_000)
    concurrent_task_limit = Column(Integer, nullable=False, default=2)
    workspace_bytes_limit = Column(BigInteger, nullable=False, default=1_073_741_824)
    enabled = Column(Boolean, nullable=False, default=True)
    updated_by = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(TIMESTAMP, nullable=False, default=utcnow, onupdate=utcnow)


class UserUsageDaily(Base):
    """Daily settled usage for quota reporting and reconciliation."""

    __tablename__ = "user_usage_daily"
    __table_args__ = (UniqueConstraint("user_id", "usage_date", name="uq_usage_user_date"),)

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    usage_date = Column(Date, nullable=False, index=True)
    task_count = Column(Integer, nullable=False, default=0)
    input_tokens = Column(BigInteger, nullable=False, default=0)
    output_tokens = Column(BigInteger, nullable=False, default=0)
    total_tokens = Column(BigInteger, nullable=False, default=0)
    tool_calls = Column(Integer, nullable=False, default=0)
    failed_tasks = Column(Integer, nullable=False, default=0)
    safety_interceptions = Column(Integer, nullable=False, default=0)
    updated_at = Column(TIMESTAMP, nullable=False, default=utcnow, onupdate=utcnow)


class AdminAuditLog(Base):
    """Append-only evidence for privileged administrator actions."""

    __tablename__ = "admin_audit_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    actor_user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(100), nullable=False, index=True)
    target_type = Column(String(50), nullable=False, index=True)
    target_id = Column(String(255), nullable=True, index=True)
    reason = Column(Text, nullable=True)
    before_json = Column(JSON, nullable=True)
    after_json = Column(JSON, nullable=True)
    request_id = Column(String(64), nullable=True, index=True)
    source_ip = Column(String(64), nullable=True)
    user_agent = Column(String(255), nullable=True)
    outcome = Column(String(20), nullable=False, default="succeeded")
    created_at = Column(TIMESTAMP, nullable=False, default=utcnow, index=True)


class AdminAccessGrant(Base):
    """Short-lived, actor-bound permission to read one user's workspace content."""

    __tablename__ = "admin_access_grants"

    id = Column(String(36), primary_key=True)
    actor_user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    target_user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    scope = Column(String(80), nullable=False, default="workspace:content")
    reason = Column(Text, nullable=False)
    expires_at = Column(TIMESTAMP, nullable=False, index=True)
    revoked_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, nullable=False, default=utcnow)


class SharedSkill(Base):
    """Registry entry for an administrator-managed shared Skill."""

    __tablename__ = "managed_shared_skills"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(80), nullable=False, unique=True, index=True)
    description = Column(String(500), nullable=False, default="")
    status = Column(String(20), nullable=False, default="draft", index=True)
    draft_content = Column(Text, nullable=False, default="")
    active_version = Column(Integer, nullable=True)
    created_by = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(TIMESTAMP, nullable=False, default=utcnow)
    updated_at = Column(TIMESTAMP, nullable=False, default=utcnow, onupdate=utcnow)


class SharedSkillVersion(Base):
    """Immutable published Skill body and validation evidence."""

    __tablename__ = "managed_shared_skill_versions"
    __table_args__ = (UniqueConstraint("skill_id", "version", name="uq_shared_skill_version"),)

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    skill_id = Column(
        BigInteger,
        ForeignKey("managed_shared_skills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    content_path = Column(String(500), nullable=False)
    content_sha256 = Column(String(64), nullable=False, index=True)
    validation_json = Column(JSON, nullable=False)
    changelog = Column(String(500), nullable=False, default="")
    created_by = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_at = Column(TIMESTAMP, nullable=False, default=utcnow)
