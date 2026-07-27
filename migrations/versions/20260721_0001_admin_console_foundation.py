"""Create administrator control-plane tables.

Revision ID: 20260721_0001
Revises: None
"""

import sqlalchemy as sa
from alembic import op

revision = "20260721_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This repository previously relied on ``Base.metadata.create_all`` and
    # therefore has installations both with and without the legacy core
    # tables. Adopt either state: emit the core schema on a clean database,
    # or only add the revocation generation to an existing users table.
    offline = op.get_context().as_sql
    inspector = None if offline else sa.inspect(op.get_bind())

    def table_missing(name: str) -> bool:
        return offline or not inspector.has_table(name)

    if table_missing("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("username", sa.String(length=50), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("full_name", sa.String(length=100), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column("is_superuser", sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.TIMESTAMP(), nullable=True, server_default=sa.func.now()),
            sa.Column("updated_at", sa.TIMESTAMP(), nullable=True, server_default=sa.func.now()),
            sa.Column("last_login_at", sa.TIMESTAMP(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("email"),
            sa.UniqueConstraint("username"),
        )
        op.create_index("ix_users_email", "users", ["email"], unique=True)
        op.create_index("ix_users_username", "users", ["username"], unique=True)
    elif not any(column["name"] == "auth_version" for column in inspector.get_columns("users")):
        op.add_column(
            "users",
            sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0"),
        )

    if table_missing("sessions"):
        op.create_table(
            "sessions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=True),
            sa.Column("status", sa.Enum("ACTIVE", "ARCHIVED", "DELETED", name="sessionstatus"), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), nullable=True, server_default=sa.func.now()),
            sa.Column("updated_at", sa.TIMESTAMP(), nullable=True, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    if table_missing("api_keys"):
        op.create_table(
            "api_keys",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("key_name", sa.String(length=100), nullable=False),
            sa.Column("key_hash", sa.String(length=255), nullable=False),
            sa.Column("key_prefix", sa.String(length=10), nullable=False),
            sa.Column("permissions", sa.JSON(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column("expires_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("last_used_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), nullable=True, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])

    if table_missing("tool_usage_logs"):
        op.create_table(
            "tool_usage_logs",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=True),
            sa.Column("session_id", sa.String(length=36), nullable=True),
            sa.Column("tool_name", sa.String(length=50), nullable=False),
            sa.Column("tool_input", sa.JSON(), nullable=True),
            sa.Column("tool_result_summary", sa.String(length=500), nullable=True),
            sa.Column("execution_time_ms", sa.Integer(), nullable=True),
            sa.Column("success", sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), nullable=True, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_tool_usage_logs_user_id", "tool_usage_logs", ["user_id"])
        op.create_index("ix_tool_usage_logs_session_id", "tool_usage_logs", ["session_id"])
        op.create_index("ix_tool_usage_logs_created_at", "tool_usage_logs", ["created_at"])

    op.create_table(
        "user_quotas",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("daily_task_limit", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("daily_token_limit", sa.BigInteger(), nullable=False, server_default="500000"),
        sa.Column("monthly_token_limit", sa.BigInteger(), nullable=False, server_default="5000000"),
        sa.Column("concurrent_task_limit", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("workspace_bytes_limit", sa.BigInteger(), nullable=False, server_default="1073741824"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "user_usage_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("task_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tool_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_tasks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("safety_interceptions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "usage_date", name="uq_usage_user_date"),
    )
    op.create_index("ix_user_usage_daily_user_id", "user_usage_daily", ["user_id"])
    op.create_index("ix_user_usage_daily_usage_date", "user_usage_daily", ["usage_date"])

    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=False),
        sa.Column("target_id", sa.String(length=255), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("source_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False, server_default="succeeded"),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("actor_user_id", "action", "target_type", "target_id", "request_id", "created_at"):
        op.create_index(f"ix_admin_audit_logs_{column}", "admin_audit_logs", [column])

    op.create_table(
        "admin_access_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=False),
        sa.Column("scope", sa.String(length=80), nullable=False, server_default="workspace:content"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("revoked_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("actor_user_id", "target_user_id", "expires_at"):
        op.create_index(f"ix_admin_access_grants_{column}", "admin_access_grants", [column])

    op.create_table(
        "managed_shared_skills",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("draft_content", sa.Text(), nullable=False),
        sa.Column("active_version", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_managed_shared_skills_name", "managed_shared_skills", ["name"], unique=True)
    op.create_index("ix_managed_shared_skills_status", "managed_shared_skills", ["status"])

    op.create_table(
        "managed_shared_skill_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("skill_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_path", sa.String(length=500), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=False),
        sa.Column("changelog", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("published_at", sa.TIMESTAMP(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["skill_id"], ["managed_shared_skills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_id", "version", name="uq_shared_skill_version"),
    )
    op.create_index("ix_managed_shared_skill_versions_skill_id", "managed_shared_skill_versions", ["skill_id"])
    op.create_index(
        "ix_managed_shared_skill_versions_content_sha256",
        "managed_shared_skill_versions",
        ["content_sha256"],
    )


def downgrade() -> None:
    op.drop_table("managed_shared_skill_versions")
    op.drop_table("managed_shared_skills")
    op.drop_table("admin_access_grants")
    op.drop_table("admin_audit_logs")
    op.drop_table("user_usage_daily")
    op.drop_table("user_quotas")
