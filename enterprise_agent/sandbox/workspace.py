"""Filtered transaction snapshots. Container-written paths are always untrusted."""

import hashlib
import os
import shutil
import stat
from pathlib import Path

from enterprise_agent.sandbox.engine import SandboxError


def excluded(path):
    from enterprise_agent.core.agent.tools.workspace import is_operational_agent_path, is_sensitive_agent_path

    # node_modules has its own persistent mount, including npm's normal links.
    return (any(part in {"node_modules", ".skill-resources"} for part in Path(path).parts)
            or is_sensitive_agent_path(path) or is_operational_agent_path(path))


def inventory(root: Path, max_bytes: int, max_files: int, *, observe_links=False):
    result = {}
    total = 0

    def failed_scan(error):
        raise SandboxError("Workspace directory cannot be scanned safely") from error

    for current, dirs, files in os.walk(root, followlinks=False, onerror=failed_scan):
        parent = Path(current)
        if observe_links:
            files.extend(d for d in dirs if (parent / d).is_symlink())
        dirs[:] = sorted(
            d for d in dirs if not excluded(str((parent / d).relative_to(root))) and not (parent / d).is_symlink()
        )
        for filename in sorted(files):
            path = parent / filename
            relative = path.relative_to(root).as_posix()
            if excluded(relative):
                continue
            metadata = path.lstat()
            if observe_links and stat.S_ISLNK(metadata.st_mode):
                if len(result) >= max_files:
                    raise SandboxError("Workspace snapshot exceeds file budget")
                result[relative] = ("symlink:" + hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest(), 0)
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise SandboxError(f"Unsupported workspace link/special file: {relative}")
            total += metadata.st_size
            if total > max_bytes or len(result) >= max_files:
                raise SandboxError("Workspace snapshot exceeds file/byte budget")
            # Never follow a symlink exchanged after lstat.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            result[relative] = (digest, stat.S_IMODE(metadata.st_mode) & 0o777)
    return result


def snapshot(source, target, limits):
    baseline = inventory(source, *limits)
    for relative, (_, mode) in baseline.items():
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        # API lock excludes cooperative file tools; container has no source mount.
        with os.fdopen(os.open(source / relative, os.O_RDONLY | os.O_NOFOLLOW), "rb") as src:
            with destination.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        destination.chmod(mode)
    return baseline


def publish(source, target, baseline, limits, *, published_paths=None):
    # The administrator-selected base may have a platform alias (/var on macOS).
    # Reject links at/below the workspace, not trusted ancestors above it.
    if target.is_symlink():
        raise SandboxError("Workspace destination contains symlink")
    target = target.resolve()
    # Caller retains successful paths even if a later file publication fails.
    if published_paths is None:
        published_paths = []
    updated = inventory(source, *limits)
    current = inventory(target, *limits)
    changed = {p for p in baseline.keys() | updated.keys() if baseline.get(p) != updated.get(p)}
    for relative in changed:
        if current.get(relative) != baseline.get(relative):
            raise SandboxError(f"Workspace conflict; no changes published: {relative}")
        path = target / relative
        if not path.resolve().is_relative_to(target.resolve()):
            raise SandboxError("Workspace destination escapes authorization")
        if any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(target)):
            raise SandboxError("Workspace destination contains symlink")
        if any(parent.exists() and not parent.is_dir() for parent in path.parents):
            raise SandboxError("Workspace conflict: destination parent is not a directory")
        if path.exists() and not path.is_file():
            raise SandboxError("Workspace destination is not a regular file")
    for relative in sorted(changed):
        path = target / relative
        if relative not in updated:
            path.unlink(missing_ok=True)
            published_paths.append(relative)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        # Container is removed before publish; never expose its links to host.
        temporary = path.with_name(path.name + ".sandbox-publish")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as dst, (source / relative).open("rb") as src:
                shutil.copyfileobj(src, dst)
            temporary.chmod(updated[relative][1] | 0o600)
            os.replace(temporary, path)
            published_paths.append(relative)
        finally:
            temporary.unlink(missing_ok=True)
    return published_paths
