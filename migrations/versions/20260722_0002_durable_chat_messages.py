"""Create the durable chat transcript ledger.

Revision ID: 20260722_0002
Revises: 20260721_0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20260722_0002"
down_revision = "20260721_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``create_all`` remains a development fallback and may already have
    # created this table before an existing installation receives the
    # revision.  Adopt that state without destroying migrated transcripts.
    offline = op.get_context().as_sql
    inspector = None if offline else sa.inspect(op.get_bind())
    if not offline and inspector.has_table("chat_messages"):
        return

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("trace_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", mysql.LONGTEXT(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "trace_id",
            "role",
            name="uq_chat_messages_session_trace_role",
        ),
    )
    op.create_index("ix_chat_messages_user_id", "chat_messages", ["user_id"])
    op.create_index("ix_chat_messages_trace_id", "chat_messages", ["trace_id"])
    op.create_index(
        "ix_chat_messages_session_id_id",
        "chat_messages",
        ["session_id", "id"],
    )


def downgrade() -> None:
    offline = op.get_context().as_sql
    inspector = None if offline else sa.inspect(op.get_bind())
    if offline or inspector.has_table("chat_messages"):
        op.drop_table("chat_messages")
