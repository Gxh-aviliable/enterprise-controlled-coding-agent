"""Exercise the duplicate-index migration against a real disposable SQL database."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.fixture
def migration():
    path = Path(__file__).resolve().parents[2] / (
        "migrations/versions/20260914_0005_remove_duplicate_skill_name_index.py"
    )
    spec = importlib.util.spec_from_file_location("skill_index_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("legacy", [False, True])
def test_upgrade_preserves_rows_and_uniqueness(migration, monkeypatch, legacy):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE managed_shared_skills (name VARCHAR(80) NOT NULL)")
        conn.exec_driver_sql("CREATE UNIQUE INDEX ix_managed_shared_skills_name ON managed_shared_skills(name)")
        if legacy:
            conn.exec_driver_sql("CREATE UNIQUE INDEX name ON managed_shared_skills(name)")
        conn.exec_driver_sql("INSERT INTO managed_shared_skills VALUES ('existing-skill')")
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.upgrade()
        migration.upgrade()
        assert {i["name"] for i in sa.inspect(conn).get_indexes("managed_shared_skills")} == {
            "ix_managed_shared_skills_name"
        }
        assert conn.exec_driver_sql("SELECT name FROM managed_shared_skills").scalar() == "existing-skill"
        with pytest.raises(sa.exc.IntegrityError):
            conn.exec_driver_sql("INSERT INTO managed_shared_skills VALUES ('existing-skill')")
        migration.downgrade()
        assert len(sa.inspect(conn).get_indexes("managed_shared_skills")) == 2
    engine.dispose()


def test_upgrade_refuses_to_drop_only_unique_index(migration, monkeypatch):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE managed_shared_skills (name VARCHAR(80) NOT NULL)")
        conn.exec_driver_sql("CREATE UNIQUE INDEX name ON managed_shared_skills(name)")
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        with pytest.raises(RuntimeError, match="refusing"):
            migration.upgrade()
        assert sa.inspect(conn).get_indexes("managed_shared_skills")[0]["unique"]
    engine.dispose()
