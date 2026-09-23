"""Remove the legacy duplicate Skill name index without weakening uniqueness.

Revision ID: 20260914_0005
Revises: 20260819_0004
"""

import sqlalchemy as sa
from alembic import op

revision = "20260914_0005"
down_revision = "20260819_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("managed_shared_skills"):
        return
    indexes = {index["name"]: index for index in inspector.get_indexes("managed_shared_skills")}
    legacy = indexes.get("name")
    if legacy is None:
        return
    canonical = indexes.get("ix_managed_shared_skills_name")
    if not (
        legacy["unique"] and legacy["column_names"] == ["name"]
        and canonical and canonical["unique"] and canonical["column_names"] == ["name"]
    ):
        raise RuntimeError("Unexpected Skill name indexes; refusing to remove a uniqueness constraint")
    op.drop_index("name", table_name="managed_shared_skills")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("managed_shared_skills") and not any(
        index["name"] == "name" for index in inspector.get_indexes("managed_shared_skills")
    ):
        op.create_index("name", "managed_shared_skills", ["name"], unique=True)
