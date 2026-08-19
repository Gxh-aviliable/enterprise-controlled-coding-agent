"""Shared, workspace-isolated file inspection and text editing primitives."""

import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import Optional

from enterprise_agent.core.agent.tools.workspace import (
    get_user_workspace,
    is_operational_agent_path,
    is_sensitive_agent_path,
    resolve_path,
)
from enterprise_agent.core.agent.tools.workspace_lock import workspace_write_lock

# Browser editing intentionally targets small source/configuration files. Larger
# files remain readable through pagination and can still be edited through the
# Agent's separately governed tools.
WORKSPACE_EDIT_MAX_BYTES = 1_048_576


class WorkspaceWriteConflictError(Exception):
    """The file changed after the caller read it."""

    def __init__(self, current_sha256: str):
        super().__init__("Workspace file changed since it was read")
        self.current_sha256 = current_sha256


class WorkspaceBinaryFileError(ValueError):
    """The target is not a UTF-8 text file."""


class WorkspaceFileTooLargeError(ValueError):
    """The existing or replacement file exceeds the browser edit limit."""


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_bytes(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read()


def _require_plain_workspace_path(user_id: int, path: str) -> Path:
    """Resolve one path while rejecting credential, operational, and symlink aliases."""
    if is_sensitive_agent_path(path) or is_operational_agent_path(path):
        raise PermissionError("Sensitive or Agent-managed workspace paths cannot be edited")

    root = get_user_workspace(user_id).resolve()
    resolved = resolve_path(path, user_id)

    # ``resolve_path`` safely contains symlinks, but editing through a symlink is
    # surprising and creates a check/write alias. Reject every symlink component.
    candidate = Path(path) if Path(path).is_absolute() else root / path
    lexical = Path(os.path.abspath(candidate))
    if not lexical.is_relative_to(root):
        raise ValueError(f"Path escapes workspace: {path}")
    relative = lexical.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PermissionError("Workspace files reached through symlinks cannot be edited")

    return resolved


def _decode_utf8_text(content: bytes) -> str:
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceBinaryFileError("Only UTF-8 text files can be edited") from exc
    if "\x00" in decoded:
        raise WorkspaceBinaryFileError("Binary files cannot be edited")
    return decoded


def _line_count(content: str) -> int:
    return len(content.splitlines(keepends=True))


def build_tree(root: Path, current: Path, depth: int, file_type: str) -> Optional[dict]:
    """Build a bounded tree without following directory symlinks."""
    try:
        # Keep the lexical path for symlink nodes. Using ``resolve()`` here
        # mislabeled ``alias.txt`` as its target (for example ``real.txt``),
        # which could make a subsequent UI mutation act on the wrong file.
        lexical_root = Path(os.path.abspath(root))
        lexical_current = Path(os.path.abspath(current))
        rel = lexical_current.relative_to(lexical_root).as_posix()
    except ValueError:
        return None
    if rel == ".":
        rel = ""

    if current.is_symlink():
        return {"path": rel, "name": current.name, "type": "symlink", "size": 0}
    if current.is_dir():
        entry = {"path": rel, "name": current.name or str(current), "type": "dir", "children": []}
        if depth > 0:
            try:
                children = sorted(current.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower()))
            except PermissionError:
                children = []
            for child in children:
                child_entry = build_tree(root, child, depth - 1, file_type)
                if child_entry:
                    entry["children"].append(child_entry)
        return entry
    if file_type == "dir":
        return None
    return {
        "path": rel,
        "name": current.name,
        "type": "file",
        "size": current.stat().st_size if current.exists() else 0,
        "modified_at": current.stat().st_mtime if current.exists() else None,
        "sensitive": is_sensitive_agent_path(rel),
    }


def get_workspace_tree(user_id: int, path: str, depth: int, file_type: str = "all") -> dict:
    resolved = resolve_path(path, user_id)
    if not resolved.exists():
        raise FileNotFoundError(path)
    root = get_user_workspace(user_id).resolve()
    result = build_tree(root, resolved, depth, file_type)
    if result is None:
        raise ValueError("Requested workspace path is not readable")
    return result


def read_workspace_text(
    user_id: int,
    path: str,
    *,
    encoding: str = "utf-8",
    offset: int = 0,
    limit: int = 500,
    allow_sensitive: bool = False,
) -> dict:
    if is_sensitive_agent_path(path) and not allow_sensitive:
        raise PermissionError("Sensitive workspace paths cannot be read")
    resolved = resolve_path(path, user_id)
    if not resolved.exists():
        raise FileNotFoundError(path)
    if not resolved.is_file():
        raise IsADirectoryError(path)

    raw_content = _read_bytes(resolved)
    digest = _sha256_bytes(raw_content)
    try:
        decoded_content = raw_content.decode(encoding)
        if "\x00" in decoded_content:
            raise UnicodeDecodeError(encoding, raw_content, 0, 1, "NUL byte")
        lines = decoded_content.splitlines(keepends=True)
    except (LookupError, UnicodeDecodeError):
        return {
            "path": path,
            "content": "",
            "size": resolved.stat().st_size,
            "lines": 0,
            "binary": True,
            "sha256": digest,
        }
    return {
        "path": path,
        "content": "".join(lines[offset : offset + limit]),
        "size": resolved.stat().st_size,
        "lines": len(lines),
        "offset": offset,
        "limit": limit,
        "binary": False,
        "sha256": digest,
    }


def _write_workspace_text_locked(
    user_id: int,
    path: str,
    content: str,
    *,
    expected_sha256: str,
    max_bytes: int = WORKSPACE_EDIT_MAX_BYTES,
) -> dict:
    """Replace a file while the caller holds the user's workspace write lock.

    The caller must send the SHA-256 returned by ``read_workspace_text``. This
    prevents a stale browser tab from silently overwriting a newer Agent or user
    edit. The temporary file lives beside the target so ``os.replace`` remains
    atomic on the target filesystem.
    """
    resolved = _require_plain_workspace_path(user_id, path)
    # Capture the root before replacing the file. ``get_user_workspace`` also
    # repairs platform-managed VS Code defaults, so calling it after a write
    # would make the response digest describe a version that no longer exists.
    workspace_root = get_user_workspace(user_id).resolve()
    if not resolved.exists():
        raise FileNotFoundError(path)
    if not resolved.is_file():
        raise IsADirectoryError(path)

    original = _read_bytes(resolved)
    if len(original) > max_bytes:
        raise WorkspaceFileTooLargeError(
            f"File exceeds the browser edit limit of {max_bytes} bytes"
        )
    _decode_utf8_text(original)
    current_sha256 = _sha256_bytes(original)
    if current_sha256 != expected_sha256.lower():
        raise WorkspaceWriteConflictError(current_sha256)

    try:
        replacement = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise WorkspaceBinaryFileError("Replacement content must be valid UTF-8") from exc
    if b"\x00" in replacement:
        raise WorkspaceBinaryFileError("Replacement content must be UTF-8 text")
    if len(replacement) > max_bytes:
        raise WorkspaceFileTooLargeError(
            f"Replacement exceeds the browser edit limit of {max_bytes} bytes"
        )

    mode = stat.S_IMODE(resolved.stat().st_mode)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=resolved.parent,
            prefix=f".{resolved.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(replacement)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)

        # Re-check immediately before replacement. This closes the practical
        # stale-write window for edits performed by Agent tools or another tab.
        latest = _read_bytes(resolved)
        latest_sha256 = _sha256_bytes(latest)
        if latest_sha256 != current_sha256:
            raise WorkspaceWriteConflictError(latest_sha256)
        os.replace(temporary_path, resolved)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    new_sha256 = _sha256_bytes(replacement)
    return {
        "path": resolved.relative_to(workspace_root).as_posix(),
        "sha256": new_sha256,
        "size": len(replacement),
        "lines": _line_count(content),
        "modified_at": resolved.stat().st_mtime,
    }


def write_workspace_text(
    user_id: int,
    path: str,
    content: str,
    *,
    expected_sha256: str,
    max_bytes: int = WORKSPACE_EDIT_MAX_BYTES,
) -> dict:
    """Atomically replace a current UTF-8 file under the per-user global lock.

    Path resolution, symlink validation, version comparison, temporary-file
    creation, and ``os.replace`` all happen while the same cross-process lock is
    held. This makes a pair of writes with the same expected digest deterministic:
    the first can succeed and the second observes a version conflict.
    """
    with workspace_write_lock(user_id):
        return _write_workspace_text_locked(
            user_id,
            path,
            content,
            expected_sha256=expected_sha256,
            max_bytes=max_bytes,
        )
