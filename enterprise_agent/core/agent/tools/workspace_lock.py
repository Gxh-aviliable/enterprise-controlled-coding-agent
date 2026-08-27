"""Cross-process serialization for workspace mutations.

Browser/API mutations and structured Agent file tools take the same lock for a
user. The lock files live next to (not inside) user-controlled workspaces so
Agent file operations cannot delete or replace them through normal workspace
paths. Arbitrary Shell, IDE, or host-process writes remain out-of-band writers;
the browser's SHA check detects their normal before/after changes but cannot
turn those external processes into cooperative lock participants.
"""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from filelock import FileLock, Timeout

from enterprise_agent.core.agent.tools.workspace import (
    get_current_user_id,
    get_workspace_base,
)

WORKSPACE_WRITE_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_DIRECTORY_NAME = ".workspace-locks"


class WorkspaceWriteLockTimeoutError(TimeoutError):
    """A workspace mutation could not acquire its per-user lock in time."""


class WorkspaceWriteLockSecurityError(PermissionError):
    """The lock directory or lock file is not a safe plain filesystem entry."""


def _require_plain_directory(path: Path, *, label: str) -> None:
    """Reject symlinks and non-directory entries used by lock coordination."""
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise WorkspaceWriteLockSecurityError(f"{label} disappeared during lock setup") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise WorkspaceWriteLockSecurityError(f"{label} cannot be a symlink")
    if not stat.S_ISDIR(metadata.st_mode):
        raise WorkspaceWriteLockSecurityError(f"{label} must be a directory")


def _workspace_lock_directory() -> Path:
    """Create the service-owned lock directory and verify it is not a symlink."""
    configured_base = Path(os.path.abspath(get_workspace_base()))
    configured_base.mkdir(parents=True, exist_ok=True)
    _require_plain_directory(configured_base, label="Workspace base")

    lock_directory = configured_base / _LOCK_DIRECTORY_NAME
    try:
        lock_directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    _require_plain_directory(lock_directory, label="Workspace lock directory")
    return lock_directory


def _lock_key(user_id: int | None) -> str:
    effective_user_id = get_current_user_id() if user_id is None else user_id
    if effective_user_id is None:
        return "default"
    try:
        normalized = int(effective_user_id)
    except (TypeError, ValueError) as exc:
        raise WorkspaceWriteLockSecurityError("Workspace lock user ID must be an integer") from exc
    if normalized < 0:
        raise WorkspaceWriteLockSecurityError("Workspace lock user ID cannot be negative")
    return f"user_{normalized}"


def workspace_write_lock_path(user_id: int | None = None) -> Path:
    """Return the safe, stable lock path for one user's entire workspace."""
    lock_path = _workspace_lock_directory() / f"{_lock_key(user_id)}.write.lock"
    if lock_path.is_symlink():
        raise WorkspaceWriteLockSecurityError("Workspace lock file cannot be a symlink")
    if lock_path.exists() and not lock_path.is_file():
        raise WorkspaceWriteLockSecurityError("Workspace lock file must be a regular file")
    return lock_path


@contextmanager
def workspace_write_lock(
    user_id: int | None = None,
    *,
    timeout: float = WORKSPACE_WRITE_LOCK_TIMEOUT_SECONDS,
) -> Iterator[None]:
    """Serialize cooperative workspace mutations across threads and processes."""
    lock_path = workspace_write_lock_path(user_id)
    lock = FileLock(lock_path, timeout=max(0.0, float(timeout)))
    try:
        with lock:
            # Validate again after acquisition so a pre-existing unsafe entry
            # cannot be accepted merely because it appeared after preflight.
            if lock_path.is_symlink() or not lock_path.is_file():
                raise WorkspaceWriteLockSecurityError(
                    "Workspace lock file must remain a regular non-symlink file"
                )
            yield
    except Timeout as exc:
        raise WorkspaceWriteLockTimeoutError(
            "Workspace is busy with another write operation; retry later"
        ) from exc
