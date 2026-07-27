import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Optional

from langchain_core.tools import tool

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tools.workspace import (
    get_current_session_id,
    get_current_user_id,
    get_user_workspace,
    is_sensitive_agent_path,
    resolve_path,
)

MAX_DELETE_PATHS = 100
PROTECTED_DELETE_PARTS = {
    ".agent",
    ".agent_internal",
    ".agent_tmp",
    ".tasks",
    ".team",
    ".transcripts",
    ".vscode",
}
WILDCARD_CHARACTERS = {"*", "?", "[", "]", "{", "}"}


def _validate_agent_file_path(path: str) -> None:
    if is_sensitive_agent_path(path):
        raise ValueError(f"Sensitive credential path is not available to Agent tools: {path}")


def _atomic_write_text(path: Path, content: str) -> None:
    """Replace a file atomically so interrupted writes do not leave partial content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    previous_mode = path.stat().st_mode if path.exists() else None
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        if previous_mode is not None:
            os.chmod(temp_name, previous_mode)
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def _resolve_delete_target(path: str, workspace: Path) -> tuple[Path, str]:
    """Resolve one exact relative path without following a leaf symlink."""
    normalized = str(path or "").strip().replace("\\", "/")
    if not normalized:
        raise ValueError("Delete path cannot be empty")
    if any(character in normalized for character in WILDCARD_CHARACTERS):
        raise ValueError(f"Wildcards are not allowed in delete paths: {path}")

    raw = PurePosixPath(normalized)
    if raw.is_absolute() or not raw.parts or raw == PurePosixPath("."):
        raise ValueError(f"Delete path must be workspace-relative: {path}")
    if any(part in {"", ".", ".."} for part in raw.parts):
        raise ValueError(f"Delete path cannot contain traversal segments: {path}")

    relative = raw.as_posix()
    lowered_parts = {part.lower() for part in raw.parts}
    protected = sorted(lowered_parts.intersection(PROTECTED_DELETE_PARTS))
    if protected:
        raise ValueError(f"Protected Agent path cannot be deleted: {protected[0]}")
    if is_sensitive_agent_path(relative):
        raise ValueError(f"Sensitive credential path cannot be deleted by Agent tools: {relative}")

    candidate = workspace.joinpath(*raw.parts)
    resolved_parent = candidate.parent.resolve()
    if not resolved_parent.is_relative_to(workspace):
        raise ValueError(f"Delete path escapes workspace: {path}")
    if not candidate.exists() and not candidate.is_symlink():
        raise FileNotFoundError(f"Delete target does not exist: {relative}")
    if not candidate.is_symlink() and not candidate.resolve().is_relative_to(workspace):
        raise ValueError(f"Delete path escapes workspace: {path}")
    return candidate, relative


def _preflight_delete_paths(paths: list[str]) -> tuple[Path, list[tuple[Path, str]]]:
    if not paths:
        raise ValueError("At least one exact delete path is required")
    if len(paths) > MAX_DELETE_PATHS:
        raise ValueError(f"At most {MAX_DELETE_PATHS} paths can be deleted in one operation")

    workspace = get_user_workspace().resolve()
    targets = [_resolve_delete_target(path, workspace) for path in paths]
    relative_paths = [relative for _, relative in targets]
    if len(set(relative_paths)) != len(relative_paths):
        raise ValueError("Duplicate delete paths are not allowed")

    path_objects = [PurePosixPath(relative) for relative in relative_paths]
    for index, current in enumerate(path_objects):
        for other_index, other in enumerate(path_objects):
            if index != other_index and current.is_relative_to(other):
                raise ValueError(
                    f"Overlapping delete paths are not allowed: {current} is inside {other}"
                )
    return workspace, targets


@tool
def read_file(path: str, limit: Optional[int] = None) -> str:
    """Read file contents from workspace.

    Args:
        path: Relative path to file within workspace
        limit: Maximum number of lines to read (optional)

    Returns:
        File contents as string
    """
    try:
        _validate_agent_file_path(path)
        fp = resolve_path(path)
        lines = fp.read_text(encoding="utf-8").splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:settings.TOOL_OUTPUT_MAX_CHARS]
    except Exception as e:
        return f"Error: {e}"


@tool
def write_file(path: str, content: str) -> str:
    """Write content to file in workspace.

    Args:
        path: Relative path to file within workspace
        content: Content to write

    Returns:
        Success message with first N lines preview (trust but verify)
    """
    try:
        _validate_agent_file_path(path)
        fp = resolve_path(path)
        _atomic_write_text(fp, content)

        # Auto-verify: re-read and show preview
        verified = fp.read_text(encoding="utf-8")
        lines = verified.splitlines()
        preview_lines = lines[:settings.VERIFICATION_PREVIEW_LINES]
        preview = "\n".join(preview_lines)
        if len(lines) > settings.VERIFICATION_PREVIEW_LINES:
            preview += f"\n... ({len(lines) - settings.VERIFICATION_PREVIEW_LINES} more lines)"

        return f"Wrote {len(content)} bytes to {path}\n\nVerified preview:\n{preview}"
    except Exception as e:
        return f"Error: {e}"


@tool
def edit_file(path: str, old_text: str, new_text: str) -> str:
    """Replace exact text in file.

    Args:
        path: Relative path to file within workspace
        old_text: Exact text to find and replace
        new_text: New text to insert

    Returns:
        Success message with diff preview (trust but verify)
    """
    try:
        _validate_agent_file_path(path)
        fp = resolve_path(path)
        content = fp.read_text(encoding="utf-8")
        if old_text not in content:
            return f"Error: Text not found in {path}"

        # Perform edit
        new_content = content.replace(old_text, new_text, 1)
        _atomic_write_text(fp, new_content)

        # Auto-verify: re-read and show context around edit
        verified = fp.read_text(encoding="utf-8")
        if new_text in verified:
            # Show ~5 lines of context around the edit
            lines = verified.splitlines()
            for i, line in enumerate(lines):
                if new_text in line:
                    start = max(0, i - 3)
                    end = min(len(lines), i + 4)
                    context = "\n".join(f"{j+1}: {lines[j]}" for j in range(start, end))
                    return f"Edited {path}\n\nVerified content around edit:\n{context}"
            # new_text spans multiple lines
            for i in range(len(lines)):
                chunk = "\n".join(lines[i:i+5])
                if new_text[:50] in chunk:  # Check first 50 chars
                    start = max(0, i - 2)
                    end = min(len(lines), i + 6)
                    context = "\n".join(f"{j+1}: {lines[j]}" for j in range(start, end))
                    return f"Edited {path}\n\nVerified content around edit:\n{context}"
            return f"Edited {path}\n(Edit verified successfully)"

        return f"Edited {path}\nWarning: Could not verify edit"
    except Exception as e:
        return f"Error: {e}"


@tool
def delete_paths(paths: list[str], reason: str) -> str:
    """Move exact workspace paths into the protected recovery trash.

    This is the only supported Agent mechanism for deleting files or
    directories. It never accepts wildcards, absolute paths, traversal, or
    protected Agent/system directories. The operation requires explicit human
    confirmation before execution and returns a recovery operation ID.

    Args:
        paths: Exact workspace-relative file or directory paths to remove.
        reason: Human-readable reason shown in the confirmation and audit trace.

    Returns:
        JSON receipt with the moved paths and recovery operation ID.
    """
    normalized_reason = str(reason or "").strip()
    if len(normalized_reason) < 3:
        return "Error: A deletion reason of at least 3 characters is required"

    moved: list[tuple[Path, Path]] = []
    operation_root: Path | None = None
    try:
        workspace, targets = _preflight_delete_paths(paths)
        operation_id = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        operation_root = workspace / ".agent" / "trash" / operation_id
        items_root = operation_root / "items"

        entries = []
        for source, relative in targets:
            kind = "symlink" if source.is_symlink() else "directory" if source.is_dir() else "file"
            destination = items_root.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
            moved.append((source, destination))
            entries.append({
                "path": relative,
                "kind": kind,
                "trash_path": destination.relative_to(workspace).as_posix(),
            })

        manifest = {
            "schema_version": 1,
            "operation_id": operation_id,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "user_id": get_current_user_id(),
            "session_id": get_current_session_id(),
            "reason": normalized_reason,
            "entries": entries,
        }
        _atomic_write_text(
            operation_root / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        return json.dumps({
            "status": "moved_to_recovery_trash",
            "operation_id": operation_id,
            "paths": [entry["path"] for entry in entries],
            "recovery_manifest": (
                operation_root / "manifest.json"
            ).relative_to(workspace).as_posix(),
        }, ensure_ascii=False)
    except Exception as exc:
        rollback_errors = []
        for source, destination in reversed(moved):
            try:
                source.parent.mkdir(parents=True, exist_ok=True)
                destination.replace(source)
            except Exception as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        suffix = f"; rollback errors: {rollback_errors}" if rollback_errors else ""
        return f"Error: {exc}{suffix}"
