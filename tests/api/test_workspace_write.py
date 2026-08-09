"""Security and concurrency tests for browser workspace editing."""

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from enterprise_agent.api.schemas.workspace import WorkspaceFileWriteRequest
from enterprise_agent.api.services import workspace_read
from enterprise_agent.api.services.workspace_read import (
    WorkspaceBinaryFileError,
    WorkspaceFileTooLargeError,
    write_workspace_text,
)
from enterprise_agent.core.agent.tools.workspace_lock import WorkspaceWriteLockTimeoutError


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _user_workspace(monkeypatch, tmp_path: Path, user_id: int) -> Path:
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    root = tmp_path / f"user_{user_id}"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.mark.asyncio
async def test_write_route_atomically_updates_existing_utf8_file(monkeypatch, tmp_path):
    from enterprise_agent.api.routes import workspace

    root = _user_workspace(monkeypatch, tmp_path, 11)
    target = root / "src" / "hello.py"
    target.parent.mkdir()
    original = "print('old')\n".encode()
    target.write_bytes(original)
    target.chmod(0o640)

    payload = WorkspaceFileWriteRequest(
        path="src/hello.py",
        content="print('你好')\n",
        expected_sha256=_sha256(original),
    )
    result = await workspace.write_file(payload, user_id=11)

    replacement = "print('你好')\n".encode()
    assert target.read_bytes() == replacement
    assert target.stat().st_mode & 0o777 == 0o640
    assert result == {
        "path": "src/hello.py",
        "sha256": _sha256(replacement),
        "size": len(replacement),
        "lines": 1,
        "modified_at": result["modified_at"],
    }
    assert isinstance(result["modified_at"], float)
    assert list(target.parent.glob(".hello.py.*.tmp")) == []


@pytest.mark.asyncio
async def test_write_route_rejects_stale_digest_without_changing_file(monkeypatch, tmp_path):
    from enterprise_agent.api.routes import workspace

    root = _user_workspace(monkeypatch, tmp_path, 12)
    target = root / "README.md"
    current = b"newer content\n"
    target.write_bytes(current)
    payload = WorkspaceFileWriteRequest(
        path="README.md",
        content="stale replacement\n",
        expected_sha256=_sha256(b"older content\n"),
    )

    with pytest.raises(HTTPException) as exc_info:
        await workspace.write_file(payload, user_id=12)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "version_conflict",
        "message": "The file changed after it was opened. Reload before saving.",
        "current_sha256": _sha256(current),
    }
    assert target.read_bytes() == current


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [".env", ".agent/trace.json", ".git/config", ".vscode/settings.json"],
)
async def test_write_route_rejects_sensitive_and_agent_managed_paths(
    monkeypatch,
    tmp_path,
    path,
):
    from enterprise_agent.api.routes import workspace

    root = _user_workspace(monkeypatch, tmp_path, 13)
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("protected", encoding="utf-8")
    payload = WorkspaceFileWriteRequest(
        path=path,
        content="changed",
        expected_sha256=_sha256(b"protected"),
    )

    with pytest.raises(HTTPException) as exc_info:
        await workspace.write_file(payload, user_id=13)

    assert exc_info.value.status_code == 403
    assert target.read_text(encoding="utf-8") == "protected"


@pytest.mark.asyncio
async def test_write_route_rejects_missing_file_instead_of_creating_it(monkeypatch, tmp_path):
    from enterprise_agent.api.routes import workspace

    root = _user_workspace(monkeypatch, tmp_path, 14)
    payload = WorkspaceFileWriteRequest(
        path="new.py",
        content="print('new')\n",
        expected_sha256=_sha256(b""),
    )

    with pytest.raises(HTTPException) as exc_info:
        await workspace.write_file(payload, user_id=14)

    assert exc_info.value.status_code == 404
    assert not (root / "new.py").exists()


@pytest.mark.asyncio
async def test_write_route_rejects_binary_file_and_nul_replacement(monkeypatch, tmp_path):
    from enterprise_agent.api.routes import workspace

    root = _user_workspace(monkeypatch, tmp_path, 15)
    binary = root / "image.bin"
    binary.write_bytes(b"\xff\xfe\x00")
    binary_payload = WorkspaceFileWriteRequest(
        path="image.bin",
        content="replacement",
        expected_sha256=_sha256(binary.read_bytes()),
    )

    with pytest.raises(HTTPException) as binary_error:
        await workspace.write_file(binary_payload, user_id=15)
    assert binary_error.value.status_code == 415

    text = root / "text.txt"
    text.write_text("safe", encoding="utf-8")
    nul_payload = WorkspaceFileWriteRequest(
        path="text.txt",
        content="unsafe\x00content",
        expected_sha256=_sha256(b"safe"),
    )
    with pytest.raises(HTTPException) as nul_error:
        await workspace.write_file(nul_payload, user_id=15)
    assert nul_error.value.status_code == 415
    assert text.read_text(encoding="utf-8") == "safe"


@pytest.mark.asyncio
async def test_write_route_is_scoped_to_authenticated_user(monkeypatch, tmp_path):
    from enterprise_agent.api.routes import workspace

    user_one = _user_workspace(monkeypatch, tmp_path, 21)
    user_two = _user_workspace(monkeypatch, tmp_path, 22)
    (user_one / "same.txt").write_text("one", encoding="utf-8")
    (user_two / "same.txt").write_text("two", encoding="utf-8")
    payload = WorkspaceFileWriteRequest(
        path="same.txt",
        content="one updated",
        expected_sha256=_sha256(b"one"),
    )

    await workspace.write_file(payload, user_id=21)

    assert (user_one / "same.txt").read_text(encoding="utf-8") == "one updated"
    assert (user_two / "same.txt").read_text(encoding="utf-8") == "two"


@pytest.mark.asyncio
async def test_write_route_rejects_path_escape_and_symlink_alias(monkeypatch, tmp_path):
    from enterprise_agent.api.routes import workspace

    root = _user_workspace(monkeypatch, tmp_path, 23)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    escape_payload = WorkspaceFileWriteRequest(
        path="../outside.txt",
        content="changed",
        expected_sha256=_sha256(b"outside"),
    )
    with pytest.raises(HTTPException) as escape_error:
        await workspace.write_file(escape_payload, user_id=23)
    assert escape_error.value.status_code == 400
    assert outside.read_text(encoding="utf-8") == "outside"

    real = root / "real.txt"
    real.write_text("real", encoding="utf-8")
    (root / "alias.txt").symlink_to(real)
    alias_payload = WorkspaceFileWriteRequest(
        path="alias.txt",
        content="changed",
        expected_sha256=_sha256(b"real"),
    )
    with pytest.raises(HTTPException) as symlink_error:
        await workspace.write_file(alias_payload, user_id=23)
    assert symlink_error.value.status_code == 403
    assert real.read_text(encoding="utf-8") == "real"


def test_write_service_enforces_existing_and_replacement_size_limits(monkeypatch, tmp_path):
    root = _user_workspace(monkeypatch, tmp_path, 31)
    target = root / "large.txt"
    target.write_text("12345", encoding="utf-8")

    with pytest.raises(WorkspaceFileTooLargeError):
        write_workspace_text(
            31,
            "large.txt",
            "ok",
            expected_sha256=_sha256(b"12345"),
            max_bytes=4,
        )

    target.write_text("old", encoding="utf-8")
    with pytest.raises(WorkspaceFileTooLargeError):
        write_workspace_text(
            31,
            "large.txt",
            "12345",
            expected_sha256=_sha256(b"old"),
            max_bytes=4,
        )
    assert target.read_text(encoding="utf-8") == "old"


def test_write_service_cleans_temp_file_if_atomic_replace_fails(monkeypatch, tmp_path):
    root = _user_workspace(monkeypatch, tmp_path, 32)
    target = root / "stable.txt"
    target.write_text("stable", encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(workspace_read.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        write_workspace_text(
            32,
            "stable.txt",
            "replacement",
            expected_sha256=_sha256(b"stable"),
        )

    assert target.read_text(encoding="utf-8") == "stable"
    assert list(root.glob(".stable.txt.*.tmp")) == []


def test_workspace_write_request_requires_hexadecimal_sha256():
    with pytest.raises(ValidationError):
        WorkspaceFileWriteRequest(
            path="file.txt",
            content="content",
            expected_sha256="z" * 64,
        )


def test_workspace_write_request_rejects_oversized_browser_draft():
    with pytest.raises(ValidationError):
        WorkspaceFileWriteRequest(
            path="README.md",
            content="x" * 1_048_577,
            expected_sha256="a" * 64,
        )


def test_write_service_rejects_non_utf8_target(monkeypatch, tmp_path):
    root = _user_workspace(monkeypatch, tmp_path, 33)
    target = root / "latin1.txt"
    target.write_bytes("café".encode("latin-1"))

    with pytest.raises(WorkspaceBinaryFileError):
        write_workspace_text(
            33,
            "latin1.txt",
            "replacement",
            expected_sha256=_sha256(target.read_bytes()),
        )


def test_two_concurrent_writes_with_same_digest_have_one_winner(monkeypatch, tmp_path):
    root = _user_workspace(monkeypatch, tmp_path, 34)
    target = root / "shared.txt"
    original = b"original\n"
    target.write_bytes(original)
    expected = _sha256(original)
    start = Barrier(2)

    def attempt(content: str) -> tuple[str, str]:
        start.wait(timeout=2)
        try:
            result = write_workspace_text(
                34,
                "shared.txt",
                content,
                expected_sha256=expected,
            )
            return "succeeded", result["sha256"]
        except workspace_read.WorkspaceWriteConflictError as exc:
            return "conflict", exc.current_sha256

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ["first\n", "second\n"]))

    assert sorted(status for status, _ in results) == ["conflict", "succeeded"]
    final_hash = _sha256(target.read_bytes())
    assert all(observed_hash == final_hash for _, observed_hash in results)
    assert target.read_text(encoding="utf-8") in {"first\n", "second\n"}


@pytest.mark.asyncio
async def test_write_route_runs_blocking_disk_work_in_threadpool(monkeypatch, tmp_path):
    from enterprise_agent.api.routes import workspace

    root = _user_workspace(monkeypatch, tmp_path, 35)
    target = root / "threaded.txt"
    target.write_text("before", encoding="utf-8")
    calls = []

    async def recording_threadpool(function, *args, **kwargs):
        calls.append(function)
        return function(*args, **kwargs)

    monkeypatch.setattr(workspace, "run_in_threadpool", recording_threadpool)
    payload = WorkspaceFileWriteRequest(
        path="threaded.txt",
        content="after",
        expected_sha256=_sha256(b"before"),
    )

    await workspace.write_file(payload, user_id=35)

    assert calls == [workspace.write_workspace_text]
    assert target.read_text(encoding="utf-8") == "after"


@pytest.mark.asyncio
async def test_write_route_reports_stable_workspace_busy_error(monkeypatch):
    from enterprise_agent.api.routes import workspace

    def busy(*args, **kwargs):
        raise WorkspaceWriteLockTimeoutError("internal lock details")

    monkeypatch.setattr(workspace, "write_workspace_text", busy)
    payload = WorkspaceFileWriteRequest(
        path="busy.txt",
        content="after",
        expected_sha256="a" * 64,
    )

    with pytest.raises(HTTPException) as exc_info:
        await workspace.write_file(payload, user_id=36)

    assert exc_info.value.status_code == 423
    assert exc_info.value.detail == {
        "code": "workspace_busy",
        "message": "The workspace is busy with another write operation. Retry later.",
    }


@pytest.mark.asyncio
async def test_browser_write_log_contains_metadata_but_not_content(
    monkeypatch,
    tmp_path,
    caplog,
):
    from enterprise_agent.api.routes import workspace

    root = _user_workspace(monkeypatch, tmp_path, 37)
    target = root / "audit.txt"
    original = b"before"
    target.write_bytes(original)
    secret_content = "not-for-logs-secret"
    payload = WorkspaceFileWriteRequest(
        path="audit.txt",
        content=secret_content,
        expected_sha256=_sha256(original),
    )

    with caplog.at_level(logging.INFO, logger=workspace.__name__):
        result = await workspace.write_file(payload, user_id=37)

    record = next(record for record in caplog.records if record.message == "workspace_browser_write")
    assert record.workspace_user_id == 37
    assert record.workspace_path == "audit.txt"
    assert record.workspace_before_sha256 == _sha256(original)
    assert record.workspace_after_sha256 == result["sha256"]
    assert record.workspace_size_bytes == len(secret_content.encode())
    assert record.workspace_write_outcome == "succeeded"
    assert secret_content not in caplog.text
