"""Tests for cross-process workspace mutation locking."""

from pathlib import Path

import pytest

from enterprise_agent.core.agent.tools.workspace_lock import (
    WorkspaceWriteLockSecurityError,
    WorkspaceWriteLockTimeoutError,
    workspace_write_lock,
    workspace_write_lock_path,
)


def test_lock_path_is_stable_per_user_and_outside_user_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))

    first = workspace_write_lock_path(41)
    same = workspace_write_lock_path(41)
    other = workspace_write_lock_path(42)

    assert first == same
    assert first != other
    assert first.parent == tmp_path / ".workspace-locks"
    assert first.parent.is_dir()
    assert not first.parent.is_symlink()
    assert not first.is_relative_to(tmp_path / "user_41")


def test_nested_independent_lock_instance_times_out_with_stable_error(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))

    with workspace_write_lock(43):
        with pytest.raises(
            WorkspaceWriteLockTimeoutError,
            match="Workspace is busy with another write operation; retry later",
        ):
            with workspace_write_lock(43, timeout=0.01):
                pytest.fail("the same user's lock must remain exclusive")


def test_lock_directory_symlink_is_rejected(monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (base / ".workspace-locks").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("WORKSPACE_BASE", str(base))

    with pytest.raises(WorkspaceWriteLockSecurityError, match="cannot be a symlink"):
        workspace_write_lock_path(44)


def test_lock_file_symlink_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    lock_path = workspace_write_lock_path(45)
    outside = tmp_path / "outside.lock"
    outside.write_text("not a lock", encoding="utf-8")
    lock_path.symlink_to(outside)

    with pytest.raises(WorkspaceWriteLockSecurityError, match="file cannot be a symlink"):
        workspace_write_lock_path(45)


@pytest.mark.parametrize("user_id", [-1, "not-an-integer"])
def test_invalid_user_id_cannot_control_lock_filename(monkeypatch, tmp_path, user_id):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))

    with pytest.raises(WorkspaceWriteLockSecurityError):
        workspace_write_lock_path(user_id)


def test_lock_file_is_plain_after_acquisition(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    lock_path: Path

    with workspace_write_lock(46):
        lock_path = workspace_write_lock_path(46)
        assert lock_path.is_file()
        assert not lock_path.is_symlink()
