"""Immutable task package snapshots. No shared per-user runtime cache."""

import json
import os
import re
import tempfile
from contextvars import ContextVar
from pathlib import Path

from enterprise_agent.config.settings import settings
from enterprise_agent.skills.packages import SkillError, decode_package, validate_package

_snapshot = ContextVar("skill_snapshot", default=None)


def cache_root():
    return Path(settings.MANAGED_SHARED_SKILLS_DIR) / ".packages"


def cache_package(package):
    evidence = validate_package(package)
    try:
        root = cache_root()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink():
            raise SkillError("Skill cache cannot be a symbolic link")
        target = root / (evidence["sha256"] + ".json")
        # Atomic replacement also repairs an incomplete/corrupt disposable cache.
        fd, name = tempfile.mkstemp(dir=root, prefix=".package-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(evidence["package"], stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, target)
        finally:
            Path(name).unlink(missing_ok=True)
        return evidence
    except OSError as exc:
        raise SkillError("Skill cache unavailable; check the managed Skill persistent volume and retry") from exc


def read_cached_package(digest):
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SkillError("Invalid Skill package hash")
    target = cache_root() / (digest + ".json")
    try:
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            data = stream.read(8 * 1024 * 1024)
        package = json.loads(data)
        evidence = validate_package(package)
    except (OSError, ValueError) as exc:
        raise SkillError("Skill task snapshot unavailable; start a new request to rebuild it") from exc
    if evidence["sha256"] != digest:
        raise SkillError("Skill task snapshot content hash mismatch")
    return evidence["package"]


def bind_snapshot(state):
    snapshot = state.get("skill_snapshot")
    if snapshot and snapshot.get("user_id") != state.get("user_id"):
        raise SkillError("Skill snapshot owner mismatch")
    _snapshot.set(snapshot)


def current_loader(user_id):
    snapshot = _snapshot.get()
    if not snapshot or snapshot["user_id"] != user_id:
        return None
    from enterprise_agent.core.agent.tools.skills import SkillLoader

    loader = SkillLoader([])
    for item in snapshot["items"]:
        loader.add(dict(item))
    return loader


def sandbox_resources(staging, host_staging, user_id):
    """Copy only this task's pinned packages and mount them read-only in Docker."""
    loader = current_loader(user_id)
    if loader is None or not loader.skills:
        return []
    destination = staging / ".skill-resources"
    destination.mkdir(exist_ok=True)
    for item in loader.skills.values():
        if not item.get("enabled", True):
            continue
        for name, data in decode_package(read_cached_package(item["sha256"])).items():
            target = destination / item["sha256"] / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o444)
    return [
        {
            "Type": "bind",
            "Source": str(host_staging / ".skill-resources"),
            "Target": "/workspace/.skill-resources",
            "ReadOnly": True,
            "BindOptions": {"Propagation": "rprivate"},
        }
    ]
