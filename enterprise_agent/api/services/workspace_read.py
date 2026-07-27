"""Shared read-only workspace inspection primitives."""

from pathlib import Path
from typing import Optional

from enterprise_agent.core.agent.tools.workspace import (
    get_user_workspace,
    is_sensitive_agent_path,
    resolve_path,
)


def build_tree(root: Path, current: Path, depth: int, file_type: str) -> Optional[dict]:
    """Build a bounded tree without following directory symlinks."""
    try:
        rel = current.resolve().relative_to(root.resolve()).as_posix()
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

    try:
        with resolved.open("r", encoding=encoding) as handle:
            lines = handle.readlines()
    except UnicodeDecodeError:
        return {
            "path": path,
            "content": "",
            "size": resolved.stat().st_size,
            "lines": 0,
            "binary": True,
        }
    return {
        "path": path,
        "content": "".join(lines[offset : offset + limit]),
        "size": resolved.stat().st_size,
        "lines": len(lines),
        "offset": offset,
        "limit": limit,
        "binary": False,
    }
