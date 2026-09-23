"""Immutable, filtered evidence view. No shell, filesystem write or network tools."""

from __future__ import annotations

import hashlib
import os
import stat
from types import MappingProxyType

from langchain_core.tools import tool

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tools.workspace import get_user_workspace
from enterprise_agent.core.agent.tools.workspace_lock import workspace_write_lock
from enterprise_agent.sandbox.workspace import inventory


def read_snapshot(user_id):
    root = get_user_workspace(user_id)
    if root.is_symlink():
        raise ValueError("Workspace root must not be a symlink")
    files = {}
    with workspace_write_lock(user_id):
        baseline = inventory(root, settings.SANDBOX_WORKSPACE_MAX_BYTES, settings.SANDBOX_WORKSPACE_MAX_FILES)
        for path, (digest, _) in baseline.items():
            with os.fdopen(os.open(root / path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError("Workspace file changed during snapshot")
                data = stream.read(settings.SANDBOX_WORKSPACE_MAX_BYTES + 1)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("Workspace changed during snapshot; retry the exploration")
            files[path] = data
    revision = hashlib.sha256(
        repr(sorted((p, hashlib.sha256(b).hexdigest()) for p, b in files.items())).encode()
    ).hexdigest()
    return MappingProxyType(files), revision


def snapshot_tools(files):
    def path_name(path):
        path = path.replace("\\", "/")
        if path.startswith("/") or ".." in path.split("/") or ":" in path:
            raise ValueError("Only snapshot-relative paths are allowed")
        return path.removeprefix("./")

    @tool
    def read_file(path: str, offset: int = 0, limit: int = 6000) -> dict:
        """Read UTF-8 evidence from the immutable snapshot; offset/limit are characters."""
        path = path_name(path)
        if path not in files:
            return {"error": "File is absent from authorized snapshot"}
        content = files[path].decode("utf-8", errors="replace")
        offset, limit = max(0, offset), min(6000, max(1, limit))
        end = min(len(content), offset + limit)
        return {"path": path, "content": content[offset:end], "next_offset": end, "truncated": end < len(content)}

    @tool
    def list_files(prefix: str = "", offset: int = 0) -> dict:
        """List authorized snapshot files, 100 per page. Never guesses file names."""
        prefix = path_name(prefix)
        paths = [p for p in sorted(files) if p.startswith(prefix)]
        offset = max(0, offset)
        return {
            "files": paths[offset : offset + 100],
            "next_offset": offset + 100,
            "truncated": len(paths) > offset + 100,
        }

    @tool
    def search_files(text: str, prefix: str = "") -> dict:
        """Search literal text in authorized UTF-8 snapshot files; at most 50 line matches."""
        prefix = path_name(prefix)
        if not text or len(text) > 500:
            return {"error": "Search text must contain 1 to 500 characters"}
        matches = []
        for path in sorted(files):
            if not path.startswith(prefix):
                continue
            for number, line in enumerate(files[path].decode("utf-8", errors="replace").splitlines(), 1):
                if text in line:
                    if len(matches) >= 50:
                        return {"matches": matches, "truncated": True}
                    matches.append({"path": path, "line": number, "text": line[:300]})
        return {"matches": matches, "truncated": False}

    return [read_file, list_files, search_files]
