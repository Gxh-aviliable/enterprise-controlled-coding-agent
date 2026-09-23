"""Ownership migration preserves packages and never resurrects an administrator retirement."""

import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.compiler import compiles

from enterprise_agent.db.mysql import Base
from enterprise_agent.models.admin import AdminAuditLog, SharedSkill, SharedSkillVersion
from enterprise_agent.models.user import User
from enterprise_agent.skills.packages import validate_package


@compiles(sa.BigInteger, "sqlite")
def sqlite_bigint(type_, compiler, **kw):
    return "INTEGER"


@pytest.fixture
def migrated_db(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "migrations/versions/20260923_0008_admin_owned_skills.py"
    spec = importlib.util.spec_from_file_location("ownership_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        Base.metadata.create_all(conn)
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        yield conn, migration
    engine.dispose()


def add_user(conn):
    conn.execute(User.__table__.insert().values(
        id=1, username="existing", email="old@example.com", password_hash="test"
    ))


def test_fresh_installation_has_no_preinstalled_skills(migrated_db):
    conn, migration = migrated_db
    migration.upgrade()
    assert conn.scalar(sa.select(sa.func.count()).select_from(SharedSkill)) == 0


def test_existing_install_migrates_full_packages_once_and_retirements_win(migrated_db):
    conn, migration = migrated_db
    add_user(conn)
    migration.upgrade()
    rows = conn.execute(sa.select(SharedSkill.__table__)).mappings().all()
    assert {r["name"] for r in rows} == {"python", "fastapi", "langgraph", "agent-interviewer"}
    assert all(r["status"] == "published" and r["active_version"] == 1 and r["draft_content"] for r in rows)
    for row in rows:
        version = conn.execute(sa.select(SharedSkillVersion.__table__).where(
            SharedSkillVersion.skill_id == row["id"]
        )).mappings().one()
        evidence = validate_package(version["package"])
        assert evidence["sha256"] == version["content_sha256"]
        assert evidence["content"] == row["draft_content"]
        assert row["draft_package"] == version["package"]
    # Simulate admin edits, retirement, and an upgrade retry. Never republish or overwrite.
    conn.execute(sa.update(SharedSkill).where(SharedSkill.name == "python").values(
        status="retired", active_version=None, draft_content="admin edited draft", revision=4
    ))
    migration.downgrade()
    migration.upgrade()
    python = conn.execute(sa.select(SharedSkill.__table__).where(SharedSkill.name == "python")).mappings().one()
    assert python["status"] == "retired" and python["draft_content"] == "admin edited draft"
    assert python["revision"] == 4
    assert conn.scalar(sa.select(sa.func.count()).select_from(SharedSkillVersion)) == 4
    assert conn.scalar(sa.select(sa.func.count()).select_from(AdminAuditLog)) == 4


def test_existing_name_is_not_overwritten_and_corruption_is_atomic(migrated_db):
    conn, migration = migrated_db
    add_user(conn)
    conn.execute(SharedSkill.__table__.insert().values(
        name="python", description="My admin version", draft_content="existing", status="draft"
    ))
    path = Path(migration.__file__).parents[1] / "data/20260923_0008_legacy_skills.json"
    packages = json.loads(path.read_text())
    packages[-1]["sha256"] = "bad"
    with pytest.raises(RuntimeError, match="hash mismatch"):
        migration.migrate_packages(conn, packages)
    assert conn.scalar(sa.select(sa.func.count()).select_from(SharedSkill)) == 1
    migration.upgrade()
    row = conn.execute(sa.select(SharedSkill.__table__).where(SharedSkill.name == "python")).mappings().one()
    assert row["draft_content"] == "existing" and row["status"] == "draft"
    assert conn.scalar(sa.select(sa.func.count()).select_from(SharedSkillVersion)) == 3
