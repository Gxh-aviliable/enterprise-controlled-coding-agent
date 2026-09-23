"""Full Skill packages and owner-scoped installations.

Nullable package columns preserve old rows; services wrap legacy content on read.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260923_0007"
down_revision = "20260914_0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("managed_shared_skills", sa.Column("draft_package", sa.JSON(), nullable=True))
    op.add_column("managed_shared_skills", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("managed_shared_skill_versions", sa.Column("package", sa.JSON(), nullable=True))
    op.create_table(
        "skill_installations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project", sa.String(240), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("package", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("source_json", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("implicit_allowed", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("installed_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=False),
        sa.UniqueConstraint("user_id", "project", "name", name="uq_skill_install_scope_name"),
    )
    op.create_index("ix_skill_installations_user_id", "skill_installations", ["user_id"])
    op.create_table(
        "skill_import_previews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("source_json", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(), nullable=False),
    )
    op.create_index("ix_skill_import_previews_user_id", "skill_import_previews", ["user_id"])
    op.create_index("ix_skill_import_previews_expires_at", "skill_import_previews", ["expires_at"])


def downgrade():
    op.drop_table("skill_import_previews")
    op.drop_table("skill_installations")
    op.drop_column("managed_shared_skill_versions", "package")
    op.drop_column("managed_shared_skills", "revision")
    op.drop_column("managed_shared_skills", "draft_package")
