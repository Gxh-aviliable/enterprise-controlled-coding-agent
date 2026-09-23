"""Move legacy bundled guidance into the administrator-owned public registry.

The frozen packages are migration data only, never a runtime discovery source.
Fresh installations start empty; existing administrator decisions win on collision.
"""

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "20260923_0008"
down_revision = "20260923_0007"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # Existing deployments retain their previously available public guidance.
    # An empty installation has no administrator decisions or legacy users to preserve.
    if bind.execute(sa.text("SELECT 1 FROM users LIMIT 1")).first() is None:
        return
    packages = json.loads(
        (Path(__file__).parents[1] / "data" / "20260923_0008_legacy_skills.json").read_text(encoding="utf-8")
    )
    migrate_packages(bind, packages)


def migrate_packages(bind, packages):
    metadata = sa.MetaData()
    skills = sa.Table("managed_shared_skills", metadata, autoload_with=bind)
    versions = sa.Table("managed_shared_skill_versions", metadata, autoload_with=bind)
    audits = sa.Table("admin_audit_logs", metadata, autoload_with=bind)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Validate every frozen package before the first write.
    for item in packages:
        digest = hashlib.sha256(
            json.dumps(item["package"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if digest != item["sha256"]:
            raise RuntimeError("Legacy Skill migration package hash mismatch")
    for item in packages:
        name = item["metadata"]["name"]
        # Do not overwrite an existing draft, publication, or retirement; also makes retries safe.
        if bind.execute(sa.select(skills.c.id).where(skills.c.name == name)).first():
            continue
        content = base64.b64decode(item["package"]["SKILL.md"], validate=True).decode("utf-8")
        result = bind.execute(skills.insert().values(
            name=name, description=item["metadata"]["description"], status="published",
            draft_content=content, draft_package=item["package"], revision=1, active_version=1,
            created_by=None, created_at=now, updated_at=now,
        ))
        skill_id = result.inserted_primary_key[0]
        bind.execute(versions.insert().values(
            skill_id=skill_id, version=1, content=content, package=item["package"],
            content_path=f"db://managed/{skill_id}/1", content_sha256=item["sha256"],
            validation_json={"valid": True, "metadata": item["metadata"], "sha256": item["sha256"]},
            changelog="迁移原有公共 Skill，后续由管理员管理", created_by=None, published_at=now,
        ))
        bind.execute(audits.insert().values(
            actor_user_id=None, action="shared_skill.migrate", target_type="shared_skill", target_id=name,
            reason="Migration 20260923_0008: remove the immutable builtin source",
            after_json={"version": 1, "sha256": item["sha256"], "source": "managed"},
            outcome="succeeded", created_at=now,
        ))


def downgrade():
    # Ownership conversion is intentionally retained: never delete subsequent administrator edits.
    pass
