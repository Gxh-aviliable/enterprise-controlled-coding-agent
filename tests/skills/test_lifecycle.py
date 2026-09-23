from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import BigInteger, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from enterprise_agent.api.middleware.auth import get_current_user
from enterprise_agent.api.routes.skills import router
from enterprise_agent.config.settings import settings
from enterprise_agent.db.mysql import Base, get_db
from enterprise_agent.models.admin import SharedSkill, SharedSkillVersion
from enterprise_agent.skills.catalog import catalog, task_snapshot
from enterprise_agent.skills.packages import SkillError, validate_package
from enterprise_agent.skills.registry import publish
from enterprise_agent.skills.runtime import bind_snapshot, current_loader, read_cached_package
from tests.skills.test_packages import CONTENT, archive, package


@compiles(BigInteger, "sqlite")
def sqlite_bigint(type_, compiler, **kw):
    return "INTEGER"


@pytest.fixture
async def database(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("SHARED_SKILLS_DIR", str(tmp_path / "builtin"))
    monkeypatch.setattr(settings, "MANAGED_SHARED_SKILLS_DIR", str(tmp_path / "managed"))
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "test.db"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    bind_snapshot({})
    await engine.dispose()


@pytest.fixture
async def client(database):
    app = FastAPI()
    app.include_router(router)
    identity = {"id": 1}
    app.dependency_overrides[get_current_user] = lambda: identity["id"]

    async def db():
        async with database() as session:
            yield session

    app.dependency_overrides[get_db] = db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, identity


async def install_zip(http, *, project="", replace=None):
    response = await http.post(
        "/skills/imports/zip",
        content=archive(
            {
                "sample/SKILL.md": CONTENT,
                "sample/references/guide.txt": "reference-v1",
                "sample/scripts/example.py": "print('skill-ok')\n",
            }
        ),
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert len(preview["candidates"]) == 1
    body = {"preview_id": preview["preview_id"], "candidate": 0, "project": project}
    if replace:
        body.update(replace_id=replace["id"], expected_version=replace["version"])
    return await http.post("/skills/install", json=body)


async def test_user_isolation_disable_update_uninstall_and_restart(client, database):
    http, identity = client
    first = await install_zip(http)
    assert first.status_code == 201, first.text
    installed = first.json()
    sid = installed["id"]
    assert (await install_zip(http)).status_code == 409
    detail = (await http.get("/skills/" + sid)).json()
    assert len(detail["files"]) == 3
    identity["id"] = 2
    assert (await http.get("/skills")).json()["items"] == []
    assert (await http.get("/skills/" + sid)).status_code == 404
    assert (await http.patch("/skills/" + sid, json={"expected_version": 1, "enabled": False})).status_code == 404
    assert (await http.delete("/skills/" + sid + "?expected_version=1")).status_code == 404
    identity["id"] = 1
    async with database() as db:
        pinned = await task_snapshot(db, 1, "", [sid], "", False)
    disabled = await http.patch("/skills/" + sid, json={"expected_version": 1, "enabled": False})
    assert disabled.status_code == 200
    async with database() as db:
        with pytest.raises(SkillError, match="disabled"):
            await task_snapshot(db, 1, "", [sid], "", False)
    bind_snapshot({"user_id": 1, "skill_snapshot": pinned})
    assert "Follow the project" in current_loader(1).load(sid)
    assert read_cached_package(installed["sha256"])
    assert (await http.patch("/skills/" + sid, json={"expected_version": 1, "enabled": True})).status_code == 409
    assert (await http.patch("/skills/" + sid, json={"expected_version": 2, "enabled": True})).status_code == 200
    updated = await install_zip(http, replace={"id": sid, "version": 3})
    assert updated.status_code == 201
    assert (await http.delete("/skills/" + sid + "?expected_version=4")).status_code == 200
    assert (await http.get("/skills/" + sid)).status_code == 404
    assert "Follow the project" in current_loader(1).load(sid)  # running task still pinned


async def test_workspace_template_project_and_same_name(client, database, tmp_path):
    from enterprise_agent.skills.imports import authorized_directory

    http, _ = client
    root = authorized_directory(1)
    project = root / "repo"
    source = root / "offline" / "sample"
    source.mkdir(parents=True)
    project.mkdir()
    (source / "SKILL.md").write_text(CONTENT)
    preview = await http.post("/skills/imports", json={"kind": "workspace", "path": "offline"})
    assert preview.status_code == 200
    response = await http.post(
        "/skills/install", json={"preview_id": preview.json()["preview_id"], "candidate": 0, "project": "repo"}
    )
    assert response.status_code == 201, response.text
    assert (await http.get("/skills")).json()["items"] == []
    project_id = response.json()["id"]
    assert (await http.get("/skills/" + project_id)).status_code == 404
    assert (await http.get("/skills/" + project_id + "?project=repo")).status_code == 200
    personal = await install_zip(http)
    async with database() as db:
        with pytest.raises(SkillError, match="Ambiguous"):
            await task_snapshot(db, 1, "repo", [], "$sample")
        snapshot = await task_snapshot(db, 1, "repo", [project_id], "", False)
        assert snapshot["selected"] == [project_id]
        assert len(snapshot["items"]) == 1
    assert personal.status_code == 201
    legacy = root / ".skills" / "legacy"
    legacy.mkdir(parents=True)
    (legacy / "SKILL.md").write_text("Legacy Markdown instructions")
    assert any(i["name"] == "legacy" for i in (await http.get("/skills")).json()["items"])
    preview = await http.post("/skills/imports", json={"kind": "template", "name": "new-skill"})
    assert preview.status_code == 200
    assert (
        await http.post("/skills/install", json={"preview_id": preview.json()["preview_id"], "candidate": 0})
    ).status_code == 201
    assert (await http.get("/skills?project=../user_2")).status_code == 403


async def test_public_full_package_rollback_and_failed_commit(database, monkeypatch, tmp_path):
    from enterprise_agent.api.routes.admin import retire_shared_skill

    request = SimpleNamespace(headers={}, client=None)
    builtin = tmp_path / "builtin" / "sample"
    builtin.mkdir(parents=True)
    (builtin / "SKILL.md").write_text(CONTENT)
    async with database() as db:
        skill = SharedSkill(name="sample", draft_content=CONTENT, draft_package=package(), revision=1)
        db.add(skill)
        await db.commit()
        assert len((await catalog(db, 1)).skills) == 0  # filesystem public sources are no longer loaded
        first = await publish(db, "sample", None, request, changelog="first release", expected_revision=1)
        assert first["version"] == 1
        from enterprise_agent.api.routes.admin import list_shared_skills

        skill.description = "unpublished description"
        await db.commit()
        listing = await list_shared_skills(SimpleNamespace(id=1), db)
        managed_item = next(item for item in listing["items"] if item["source"] == "managed")
        assert managed_item["sha256"] == first["sha256"]
        assert managed_item["description"] != "unpublished description"
        assert managed_item["draft_description"] == "unpublished description"
    async with database() as db:  # a new database session/process sees the release
        loader = await catalog(db, 2)
        assert len(loader.skills) == 1
        assert loader.resolve("sample")["source"] == "managed"
        published = loader.resolve("managed:1")
        assert "scripts/example.py" in published["package"]
        with pytest.raises(HTTPException) as conflict:
            await publish(db, "sample", None, request, changelog="duplicate")
        assert conflict.value.status_code == 409
        await db.rollback()
        skill = await db.scalar(select(SharedSkill))
        changed = package()
        changed["references/guide.txt"] = "djI="
        skill.draft_package = changed
        await db.commit()
        assert (await catalog(db, 1)).resolve("managed:1")["sha256"] == first["sha256"]
        await publish(db, "sample", None, request, changelog="v2 resources")
        rolled = await publish(db, "sample", None, request, changelog="rollback", rollback_version=1)
        assert rolled["version"] == 3 and rolled["sha256"] == first["sha256"]
        await retire_shared_skill("sample", request, "retire now", SimpleNamespace(id=None), db)
        assert len((await catalog(db, 1)).skills) == 0
    async with database() as db:

        async def fail():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", fail)
        with pytest.raises(RuntimeError):
            await publish(db, "sample", None, request, changelog="failed publication", rollback_version=1)
    async with database() as db:
        assert len((await catalog(db, 1)).skills) == 0
        versions = (await db.scalars(select(SharedSkillVersion))).all()
        assert len(versions) == 3
        assert validate_package(versions[0].package)["sha256"] == first["sha256"]


async def test_migrated_public_skill_can_be_edited_published_and_retired(database):
    import importlib.util
    import json
    from pathlib import Path

    from enterprise_agent.api.routes.admin import get_shared_skill, retire_shared_skill, save_shared_skill_draft
    from enterprise_agent.api.schemas.admin import SharedSkillDraftRequest

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "public_ownership", root / "migrations/versions/20260923_0008_admin_owned_skills.py"
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    packages = json.loads((root / "migrations/data/20260923_0008_legacy_skills.json").read_text())
    request, admin = SimpleNamespace(headers={}, client=None), SimpleNamespace(id=None)
    async with database() as db:
        conn = await db.connection()
        await conn.run_sync(migration.migrate_packages, packages)
        await db.commit()
        detail = await get_shared_skill("python", admin, db)
        assert detail["draft_content"] and detail["validation"]["valid"]
        original = (await catalog(db, 1)).resolve("python")
        assert original["source"] == "managed"
        changed = detail["draft_content"] + "\nFollow the administrator's new guidance.\n"
        await save_shared_skill_draft(SharedSkillDraftRequest(
            name="python", content=changed, description=detail["description"], expected_revision=1
        ), request, admin, db)
        assert (await catalog(db, 1)).resolve("python")["sha256"] == original["sha256"]
        result = await publish(db, "python", None, request, changelog="Administrator update", expected_revision=2)
        current = (await catalog(db, 2)).resolve("python")
        assert current["version"] == result["version"] == 2
        assert current["sha256"] != original["sha256"]
        await retire_shared_skill("python", request, "No longer needed", admin, db)
    async with database() as db:
        assert {i["name"] for i in (await catalog(db, 1)).skills.values()} == {
            "agent-interviewer", "fastapi", "langgraph"
        }


async def test_api_rejects_untrusted_preview_and_corrupt_update(client, database):
    http, identity = client
    first = (await install_zip(http)).json()
    preview = (await http.post("/skills/imports", json={"kind": "template", "name": "sample"})).json()
    identity["id"] = 2
    assert (await http.get(f"/skills/imports/{preview['preview_id']}/0")).status_code == 404
    assert (
        await http.post("/skills/install", json={"preview_id": preview["preview_id"], "candidate": 0})
    ).status_code == 404
    identity["id"] = 1
    assert (
        await http.post(
            "/skills/install",
            json={
                "preview_id": preview["preview_id"],
                "candidate": 0,
                "replace_id": first["id"],
                "expected_version": 99,
            },
        )
    ).status_code == 409
    assert (await http.get("/skills/" + first["id"])).json()["sha256"] == first["sha256"]


@pytest.mark.integration
async def test_real_git_subdirectory_import_and_install(client):
    import os

    if os.getenv("RUN_SKILL_GIT_IMPORT_TESTS") != "1":
        pytest.skip("Opt in to read-only public Git network access")
    http, _ = client
    response = await http.post(
        "/skills/imports",
        json={
            "kind": "git",
            "url": "https://github.com/anthropics/skills",
            "ref": "main",
            "subdirectory": "skills/algorithmic-art",
        },
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert len(preview["source"]["commit"]) == 40
    installed = await http.post("/skills/install", json={"preview_id": preview["preview_id"], "candidate": 0})
    assert installed.status_code == 201, installed.text
    detail = (await http.get("/skills/" + installed.json()["id"])).json()
    assert detail["origin"]["commit"] == preview["source"]["commit"]
    assert detail["name"] == "algorithmic-art"


async def test_new_routes_require_authentication_and_public_admin_permission(database):
    from enterprise_agent.api.middleware.auth import get_current_user_record
    from enterprise_agent.api.routes.admin import router as admin_router
    from enterprise_agent.models.user import User

    app = FastAPI()
    app.include_router(router)
    app.include_router(admin_router)

    async def db():
        async with database() as session:
            yield session

    app.dependency_overrides[get_db] = db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.get("/skills")).status_code in {401, 403}
        assert (await http.post("/skills/imports", json={"kind": "template"})).status_code in {401, 403}
        app.dependency_overrides[get_current_user_record] = lambda: User(id=1, is_superuser=False, is_active=True)
        assert (await http.post("/admin/skills", json={"name": "sample", "content": CONTENT})).status_code == 403
        assert (await http.post("/admin/skills/sample/publish", json={"changelog": "unauthorized"})).status_code == 403


async def test_snapshot_failure_is_visible_and_catalog_rebuilds_cache(database, tmp_path):
    from enterprise_agent.core.agent.nodes import _build_available_skills
    from enterprise_agent.skills.runtime import cache_root

    async with database() as db:
        db.add(SharedSkill(name="sample", draft_content=CONTENT, draft_package=package(), revision=1))
        await db.commit()
        await publish(db, "sample", None, None, changelog="cache recovery")
        snapshot = await task_snapshot(db, 1, "", ["managed:1"], "", False)
        state = {"user_id": 1, "skill_snapshot": snapshot}
        assert "Follow the project" in _build_available_skills(state)
        digest = snapshot["items"][0]["sha256"]
        (cache_root() / (digest + ".json")).write_text("{}")
        with pytest.raises(ValueError, match="snapshot unavailable"):
            _build_available_skills(state)
        rebuilt = await task_snapshot(db, 1, "", ["managed:1"], "", False)
        assert "Follow the project" in _build_available_skills({"user_id": 1, "skill_snapshot": rebuilt})


async def test_task_snapshot_survives_new_process_and_cache_write_failure(database, monkeypatch):
    import json
    import os
    import subprocess
    import sys

    async with database() as db:
        db.add(SharedSkill(name="sample", draft_content=CONTENT, draft_package=package(), revision=1))
        await db.commit()
        await publish(db, "sample", None, None, changelog="restart test")
        snapshot = await task_snapshot(db, 1, "", ["managed:1"], "", False)
        code = """import json,sys
from enterprise_agent.skills.runtime import bind_snapshot,current_loader
state=json.load(sys.stdin)
bind_snapshot(state)
assert 'Follow the project' in current_loader(1).load('managed:1')
assert current_loader(2) is None
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            input=json.dumps({"user_id": 1, "skill_snapshot": snapshot}),
            env={**os.environ, "MANAGED_SHARED_SKILLS_DIR": settings.MANAGED_SHARED_SKILLS_DIR},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr

        def fail(*args):
            raise OSError("disk unavailable")

        with monkeypatch.context() as patch:
            patch.setattr("enterprise_agent.skills.runtime.os.replace", fail)
            with pytest.raises(SkillError, match="persistent volume"):
                await task_snapshot(db, 1, "", ["managed:1"], "", False)
        assert (await catalog(db, 1)).resolve("managed:1")["version"] == 1
        assert (await task_snapshot(db, 1, "", ["managed:1"], "", False))["selected"] == ["managed:1"]


async def test_install_never_executes_scripts(client, tmp_path):
    http, _ = client
    marker = tmp_path / "must-not-exist"
    code = f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n'
    preview = (
        await http.post(
            "/skills/imports/zip",
            content=archive(
                {
                    "sample/SKILL.md": CONTENT,
                    "sample/scripts/install.py": code,
                    "sample/setup.py": code,
                }
            ),
        )
    ).json()
    assert not marker.exists()
    response = await http.post("/skills/install", json={"preview_id": preview["preview_id"], "candidate": 0})
    assert response.status_code == 201
    assert not marker.exists()


async def test_trusted_intranet_git_tls_ref_subdirectory_and_redirect(client, monkeypatch, tmp_path):
    from tests.skills.git_server import https_git

    http, _ = client
    with https_git(tmp_path) as (url, ca, port):
        monkeypatch.setattr(settings, "SKILL_GIT_HOSTS", "")
        monkeypatch.setattr(settings, "SKILL_GIT_INTERNAL_HOSTS", "")
        payload = {"kind": "git", "url": url, "ref": "demo-v1", "subdirectory": "skills/sample"}
        assert (await http.post("/skills/imports", json=payload)).status_code == 422
        monkeypatch.setattr(settings, "SKILL_GIT_INTERNAL_HOSTS", f"localhost:{port}")
        monkeypatch.setattr(settings, "SKILL_GIT_CA_BUNDLE", "")
        untrusted_certificate = await http.post("/skills/imports", json=payload)
        assert untrusted_certificate.status_code == 422
        assert "TLS" in untrusted_certificate.json()["detail"]
        monkeypatch.setattr(settings, "SKILL_GIT_CA_BUNDLE", ca)
        preview = await http.post("/skills/imports", json=payload)
        assert preview.status_code == 200, preview.text
        data = preview.json()
        assert len(data["source"]["commit"]) == 40
        installed = await http.post("/skills/install", json={"preview_id": data["preview_id"], "candidate": 0})
        assert installed.status_code == 201, installed.text
        detail = (await http.get("/skills/" + installed.json()["id"])).json()
        assert detail["origin"]["ref"] == "demo-v1"
        assert any(f["path"] == "reference.txt" for f in detail["files"])
        redirected = await http.post(
            "/skills/imports", json={**payload, "url": url.replace("fixture.git", "redirect.git")}
        )
        assert redirected.status_code == 422
