"""Remove legacy duplicate username/email indexes while retaining uniqueness.

Revision ID: 20260914_0006
Revises: 20260914_0005
"""

import sqlalchemy as sa
from alembic import op

revision = "20260914_0006"
down_revision = "20260914_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("users"):
        return
    indexes = {index["name"]: index for index in inspector.get_indexes("users")}
    redundant = []
    for name in ("username", "email"):
        legacy = indexes.get(name)
        if legacy is None:
            continue
        canonical = indexes.get("ix_users_" + name)
        if not (
            legacy["unique"] and legacy["column_names"] == [name]
            and canonical and canonical["unique"] and canonical["column_names"] == [name]
        ):
            raise RuntimeError("Unexpected user indexes; refusing to remove a uniqueness constraint")
        redundant.append(name)
    # Validate all affected indexes before the first nontransactional MySQL DDL.
    for name in redundant:
        op.drop_index(name, table_name="users")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("users"):
        return
    indexes = {index["name"] for index in inspector.get_indexes("users")}
    for name in ("username", "email"):
        if name not in indexes:
            op.create_index(name, "users", [name], unique=True)
