"""Cleanup after container removal; staging paths are service-owned, never user input."""

import os
import shutil
import stat
import time
from pathlib import Path


def remove_snapshot(root: Path):
    """Restore directory traversal if untrusted code chmod'ed its snapshot to 000.

    Only call after the owning container has been removed. Never follow or chmod
    file/symlink targets: hardlinks and symlinks may have been created by code.
    """
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError("Snapshot root must be a plain directory")
    os.chmod(root, 0o700, follow_symlinks=False)
    for current, dirs, _ in os.walk(root, followlinks=False):
        for name in dirs:
            directory = Path(current) / name
            if stat.S_ISDIR(directory.lstat().st_mode):
                os.chmod(directory, 0o700, follow_symlinks=False)
    for attempt in range(3):
        try:
            shutil.rmtree(root)
            return
        except PermissionError:
            # Docker Desktop can briefly deny rmdir on a just-detached nested
            # bind target. Retry only this owned snapshot; persistent errors surface.
            if attempt == 2:
                raise
            time.sleep(0.05 * (attempt + 1))
