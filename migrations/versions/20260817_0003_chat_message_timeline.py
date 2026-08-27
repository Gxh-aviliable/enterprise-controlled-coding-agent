"""Add compact durable chat execution timelines.

Revision ID: 20260817_0003
Revises: 20260722_0002
"""

import sqlalchemy as sa
from alembic import op

revision = "20260817_0003"
down_revision = "20260722_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``create_all`` may have created the column first in a local development
    # database. Adopt that schema without touching any existing transcripts.
    offline = op.get_context().as_sql
    inspector = None if offline else sa.inspect(op.get_bind())
    if not offline:
        if not inspector.has_table("chat_messages"):
            return
        if any(column["name"] == "timeline" for column in inspector.get_columns("chat_messages")):
            return

    op.add_column(
        "chat_messages",
        sa.Column("timeline", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    offline = op.get_context().as_sql
    inspector = None if offline else sa.inspect(op.get_bind())
    if offline or (
        inspector.has_table("chat_messages")
        and any(column["name"] == "timeline" for column in inspector.get_columns("chat_messages"))
    ):
        op.drop_column("chat_messages", "timeline")
