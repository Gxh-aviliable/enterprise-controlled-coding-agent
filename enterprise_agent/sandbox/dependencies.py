"""Workspace-local package storage reused by disposable execution containers."""

import hashlib
import json
import os
import uuid
from pathlib import Path

from enterprise_agent.config.settings import settings
from enterprise_agent.sandbox.engine import SandboxError


def dependency_directory(workspace):
    key = hashlib.sha256(str(workspace.absolute()).encode()).hexdigest()[:24]
    return Path(settings.SANDBOX_STAGING_BASE).absolute() / "dependencies" / key


def dependency_mounts(workspace, baseline):
    root = Path(settings.SANDBOX_STAGING_BASE).absolute()
    directory = dependency_directory(workspace)
    host_root = Path(settings.SANDBOX_HOST_STAGING_BASE or str(root))

    def mount(relative, target):
        path = directory / relative
        current = root
        for part in path.relative_to(root).parts:
            current /= part
            current.mkdir(mode=0o700, exist_ok=True)
            if current.is_symlink() or not current.is_dir():
                raise SandboxError("Dependency directory must not be a link")
        return {"Type": "bind", "Source": str(host_root / path.relative_to(root)),
                "Target": target, "ReadOnly": False,
                "BindOptions": {"Propagation": "rprivate"}}

    mounts = [mount("python", "/opt/python-user"), mount("npm-cache", "/opt/npm-cache")]
    # Existing package.json files cover root and nested frontend projects.
    projects = {Path(".")} | {Path(p).parent for p in baseline if Path(p).name == "package.json"}
    for project in sorted(projects):
        key = hashlib.sha256(project.as_posix().encode()).hexdigest()[:24]
        mounts.append(mount("node/" + key, str(Path("/workspace") / project / "node_modules")))
    return mounts


def dependency_stamp(workspace):
    """Include installed packages in validation freshness without reading binaries.

    ctime also changes when code resets mtime. Cache downloads are not inputs;
    installed Python/Node files are. Links are observed, never followed.
    """
    directory = dependency_directory(workspace)
    entries = {}
    def failed_scan(error):
        raise error

    try:
        for kind in ("python", "node"):
            base = directory / kind
            if not base.exists() and not base.is_symlink():
                continue
            if base.is_symlink():
                raise SandboxError("Dependency root must not be a link")
            for current, dirs, files in os.walk(base, followlinks=False, onerror=failed_scan):
                dirs[:] = sorted(d for d in dirs if d != "__pycache__")
                for name in sorted(dirs + files):
                    path = Path(current) / name
                    metadata = path.lstat()
                    # Empty mount directories do not invalidate first execution.
                    if path.is_symlink() or not path.is_dir():
                        entries[path.relative_to(directory).as_posix()] = [
                            metadata.st_size, metadata.st_mode, metadata.st_mtime_ns, metadata.st_ctime_ns,
                        ]
    except FileNotFoundError:
        # Missing cache is normal before the first execution. A disappearing
        # entry during installation must never look like stable validation.
        if directory.exists():
            return "unstable:" + uuid.uuid4().hex
    except OSError:
        return "unstable:" + uuid.uuid4().hex
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
