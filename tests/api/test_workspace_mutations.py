"""Workspace mutation routes share one lock and offload blocking disk work."""

import io
import zipfile
from contextlib import contextmanager

import pytest
from fastapi import HTTPException, UploadFile


@pytest.mark.asyncio
async def test_workspace_mutation_routes_use_threadpool_and_per_user_lock(
    monkeypatch,
    tmp_path,
):
    from enterprise_agent.api.routes import workspace

    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    lock_users = []
    threadpool_functions = []

    @contextmanager
    def recording_lock(user_id):
        lock_users.append(user_id)
        yield

    async def recording_threadpool(function, *args, **kwargs):
        threadpool_functions.append(function.__name__)
        return function(*args, **kwargs)

    monkeypatch.setattr(workspace, "workspace_write_lock", recording_lock)
    monkeypatch.setattr(workspace, "run_in_threadpool", recording_threadpool)

    assert await workspace.create_directory(path="docs", user_id=61) == {
        "path": "docs",
        "created": True,
    }
    upload = UploadFile(filename="notes.txt", file=io.BytesIO(b"hello"))
    assert await workspace.upload_file(file=upload, path="docs", user_id=61) == {
        "path": "docs/notes.txt",
        "name": "notes.txt",
        "size": 5,
    }
    assert await workspace.move_item(
        source="docs/notes.txt",
        dest="docs/renamed.txt",
        user_id=61,
    ) == {"from": "docs/notes.txt", "to": "docs/renamed.txt", "moved": True}
    assert await workspace.delete_item(path="docs/renamed.txt", user_id=61) == {
        "deleted": True,
        "path": "docs/renamed.txt",
    }

    assert threadpool_functions == [
        "_create_directory",
        "_store_upload",
        "_move_item",
        "_delete_item",
    ]
    assert lock_users == [61, 61, 61, 61]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete", "move", "upload"])
async def test_workspace_mutations_never_follow_symlink_targets(
    monkeypatch,
    tmp_path,
    operation,
):
    from enterprise_agent.api.routes import workspace

    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    root = tmp_path / "user_62"
    root.mkdir()
    target = root / "real.txt"
    target.write_text("keep", encoding="utf-8")
    alias = root / "alias.txt"
    alias.symlink_to(target)

    with pytest.raises(HTTPException) as exc_info:
        if operation == "delete":
            await workspace.delete_item(path="alias.txt", user_id=62)
        elif operation == "move":
            await workspace.move_item(
                source="alias.txt",
                dest="moved.txt",
                user_id=62,
            )
        else:
            upload = UploadFile(filename="alias.txt", file=io.BytesIO(b"overwrite"))
            await workspace.upload_file(file=upload, path="", user_id=62)

    assert exc_info.value.status_code == 403
    assert target.read_text(encoding="utf-8") == "keep"
    assert alias.is_symlink()


@pytest.mark.asyncio
async def test_tree_keeps_symlink_lexical_path_before_mutation_rejection(
    monkeypatch,
    tmp_path,
):
    from enterprise_agent.api.routes import workspace

    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    root = tmp_path / "user_64"
    root.mkdir()
    target = root / "real.txt"
    target.write_text("keep", encoding="utf-8")
    alias = root / "alias.txt"
    alias.symlink_to(target)

    tree = await workspace.get_tree(path="", depth=2, file_type="all", user_id=64)
    alias_node = next(node for node in tree["children"] if node["name"] == "alias.txt")
    assert alias_node == {
        "path": "alias.txt",
        "name": "alias.txt",
        "type": "symlink",
        "size": 0,
    }

    with pytest.raises(HTTPException) as exc_info:
        await workspace.delete_item(path=alias_node["path"], user_id=64)

    assert exc_info.value.status_code == 403
    assert target.read_text(encoding="utf-8") == "keep"
    assert alias.is_symlink()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [".env", ".agent/trace.json", ".vscode/settings.json"])
async def test_workspace_mutations_reject_platform_managed_paths(
    monkeypatch,
    tmp_path,
    path,
):
    from enterprise_agent.api.routes import workspace

    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    root = tmp_path / "user_63"
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("keep", encoding="utf-8")

    with pytest.raises(HTTPException) as exc_info:
        await workspace.delete_item(path=path, user_id=63)

    assert exc_info.value.status_code == 403
    assert target.read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_workspace_zip_skips_symlink_to_outside_file(monkeypatch, tmp_path):
    from enterprise_agent.api.routes import workspace

    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    root = tmp_path / "user_65"
    docs = root / "docs"
    docs.mkdir(parents=True)
    (docs / "inside.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("do-not-export", encoding="utf-8")
    (docs / "leak.txt").symlink_to(outside)

    response = await workspace.download_zip(
        paths="docs",
        name="workspace",
        user_id=65,
    )
    archive_bytes = b"".join([chunk async for chunk in response.body_iterator])
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert archive.namelist() == ["inside.txt"]
        assert archive.read("inside.txt") == b"inside"
