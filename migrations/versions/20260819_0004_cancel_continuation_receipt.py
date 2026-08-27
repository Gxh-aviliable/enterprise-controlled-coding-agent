"""add cancellation continuation receipt

Revision ID: 20260819_0004
Revises: 20260817_0003
Create Date: 2026-08-19
"""

import sqlalchemy as sa
from alembic import op

revision = "20260819_0004"
down_revision = "20260817_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    offline = op.get_context().as_sql
    inspector = None if offline else sa.inspect(op.get_bind())
    if not offline:
        if not inspector.has_table("chat_messages"):
            return
        if any(
            column["name"] == "continuation_receipt"
            for column in inspector.get_columns("chat_messages")
        ):
            return
    op.add_column(
        "chat_messages",
        sa.Column("continuation_receipt", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    offline = op.get_context().as_sql
    inspector = None if offline else sa.inspect(op.get_bind())
    if offline or (
        inspector.has_table("chat_messages")
        and any(
            column["name"] == "continuation_receipt"
            for column in inspector.get_columns("chat_messages")
        )
    ):
        op.drop_column("chat_messages", "continuation_receipt")
