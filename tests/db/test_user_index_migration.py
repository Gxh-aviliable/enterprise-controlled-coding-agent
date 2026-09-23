"""Verify legacy server indexes can be removed without losing accounts/uniqueness."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.fixture
def migration():
    path = Path(__file__).resolve().parents[2] / (
        "migrations/versions/20260914_0006_remove_duplicate_user_indexes.py"
    )
    spec = importlib.util.spec_from_file_location("user_index_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("legacy", [(), ("email",), ("username",), ("email", "username")])
def test_existing_and_fresh_schemas_preserve_accounts(migration, monkeypatch, legacy):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE users (username TEXT NOT NULL, email TEXT NOT NULL)")
        for name in ("username", "email"):
            conn.exec_driver_sql(f"CREATE UNIQUE INDEX ix_users_{name} ON users({name})")
        for name in legacy:
            conn.exec_driver_sql(f"CREATE UNIQUE INDEX {name} ON users({name})")
        conn.exec_driver_sql("INSERT INTO users VALUES ('existing', 'existing@example.com')")
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.upgrade()
        migration.upgrade()
        assert {i["name"] for i in sa.inspect(conn).get_indexes("users")} == {
            "ix_users_username", "ix_users_email"
        }
        assert conn.exec_driver_sql("SELECT * FROM users").one() == ("existing", "existing@example.com")
        for username, email in [("existing", "new@example.com"), ("new", "existing@example.com")]:
            with pytest.raises(sa.exc.IntegrityError):
                conn.execute(sa.text("INSERT INTO users VALUES (:username,:email)"),
                             {"username": username, "email": email})
        migration.downgrade()
        assert len(sa.inspect(conn).get_indexes("users")) == 4
    engine.dispose()


@pytest.mark.parametrize("unsafe", ["username", "email"])
def test_all_indexes_validated_before_any_drop(migration, monkeypatch, unsafe):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE users (username TEXT NOT NULL, email TEXT NOT NULL)")
        for name in ("username", "email"):
            conn.exec_driver_sql(f"CREATE UNIQUE INDEX {name} ON users({name})")
            if name != unsafe:
                conn.exec_driver_sql(f"CREATE UNIQUE INDEX ix_users_{name} ON users({name})")
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        before = sa.inspect(conn).get_indexes("users")
        with pytest.raises(RuntimeError, match="refusing"):
            migration.upgrade()
        assert sa.inspect(conn).get_indexes("users") == before
    engine.dispose()
