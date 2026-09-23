"""Bounded text diffs and conflict-checked recovery outside user workspaces."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import stat
import time
import uuid
from pathlib import Path

from filelock import FileLock

from enterprise_agent.core.agent.tools.workspace_lock import workspace_write_lock
from enterprise_agent.core.execution.evidence import _path, read_receipts, workspace_state
from enterprise_agent.observability.trace_store import redact_text
from enterprise_agent.sandbox.workspace import excluded

MAX_FILE_BYTES = 256 * 1024
MAX_TASK_BYTES = 4 * 1024 * 1024
MAX_TASK_FILES = 100
RETENTION_SECONDS = 7 * 86400


def _atomic_json(path, value):
    temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _snapshot_path(root, user_id, trace_id):
    return _path(user_id, trace_id, root).with_suffix(".snapshots.json")


def _read_snapshots(path):
    if path.is_symlink():
        raise PermissionError("Unsafe snapshot file")
    if not path.exists():
        return {"created_at": time.time(), "blobs": {}, "paths": []}
    data = json.loads(path.read_text())
    if time.time() - data["created_at"] > RETENTION_SECONDS:
        return {**data, "blobs": {}, "expired": True}
    return data


def capture_text(root, user_id, trace_id, files):
    """Save before execution; never include snapshots in tool/model messages."""
    path = _snapshot_path(root, user_id, trace_id)
    with FileLock(str(path) + ".lock"):
        data = _read_snapshots(path)
        if data.get("expired"):
            return
        changed = not path.exists()
        for relative, version in sorted(files.items()):
            if excluded(relative) or version[0].startswith("symlink:"):
                continue
            if relative not in data["paths"] and len(data["paths"]) >= MAX_TASK_FILES:
                continue
            if version[0] in data["blobs"]:
                continue
            source = root / relative
            try:
                with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
                    meta = os.fstat(stream.fileno())
                    if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or meta.st_size > MAX_FILE_BYTES:
                        continue
                    raw = stream.read(MAX_FILE_BYTES + 1)
                if len(raw) > MAX_FILE_BYTES or hashlib.sha256(raw).hexdigest() != version[0]:
                    continue
                text = raw.decode("utf-8")
                if any(ord(c) < 32 and c not in "\n\r\t" for c in text) or redact_text(text, limit=None) != text:
                    continue
                data["blobs"][version[0]] = text
                new_path = relative not in data["paths"]
                if new_path:
                    data["paths"].append(relative)
                if len(json.dumps(data, ensure_ascii=False).encode()) > MAX_TASK_BYTES:
                    del data["blobs"][version[0]]
                    if new_path:
                        data["paths"].remove(relative)
                    continue
                changed = True
            except (OSError, UnicodeError):
                continue
        if changed:
            _atomic_json(path, data)


def task_changes(root, user_id, trace_id):
    events = read_receipts(user_id, trace_id, root)
    latest = {e["execution_id"]: e for e in events}
    files = {}
    for event in sorted(latest.values(), key=lambda e: (e.get("recorded_at", 0), e.get("seq", 0))):
        for change in event.get("changes", []):
            relative = change["path"]
            if relative in files:
                previous = files[relative]
                previous["continuous"] &= previous["after"] == change["before"]
                previous["after"] = change["after"]
            else:
                files[relative] = {**change, "continuous": True}
    # Receipts preserve transient effects; this list describes the net file diff.
    files = {path: change for path, change in files.items() if change['before'] != change['after']}
    snapshot_path = _snapshot_path(root, user_id, trace_id)
    with FileLock(str(snapshot_path) + ".lock"):
        snapshots = _read_snapshots(snapshot_path)
        if snapshots.get("expired"):
            # Remove expired text on access; retain a tombstone so later reads
            # cannot renew the retention window of an old task.
            _atomic_json(snapshot_path, snapshots)
    current_state = workspace_state(root) if latest else {"files": {}, "stamp": None}
    current = current_state["files"]
    for relative, change in files.items():
        before, after = change["before"], change["after"]
        before_text = "" if before is None else snapshots["blobs"].get(before[0])
        after_text = "" if after is None else snapshots["blobs"].get(after[0])
        supported = before_text is not None and after_text is not None and change["continuous"]
        now = current.get(relative)
        conflict = (list(now) if now else None) != (list(after) if after else None)
        change.update(
            operation="added" if before is None else "deleted" if after is None else "modified",
            restorable=supported and not conflict and before != after,
            conflict=conflict,
            supported=supported,
            reason="content_changed" if conflict else None if supported else "snapshot_unavailable_or_discontinuous",
            diff="".join(
                difflib.unified_diff(
                    before_text.splitlines(True),
                    after_text.splitlines(True),
                    fromfile="before/" + relative,
                    tofile="after/" + relative,
                )
            )
            if supported
            else None,
        )
    version = hashlib.sha256(json.dumps(events, sort_keys=True).encode()).hexdigest()
    journal_path = _path(user_id, trace_id, root).with_suffix(".restore.json")
    if journal_path.is_symlink():
        raise PermissionError("Unsafe restore journal")
    journal = json.loads(journal_path.read_text()) if journal_path.exists() else None
    return {
        "trace_id": trace_id,
        "version": version,
        "files": list(files.values()),
        "validations": [
            {**e, "current": e.get("input_stamp") == current_state["stamp"]}
            for e in latest.values()
            if e.get("validation")
        ],
        "pending_execution": any(e["phase"] == "running" for e in latest.values()),
        "restore": journal,
        "coverage": {
            "max_file_bytes": MAX_FILE_BYTES,
            "max_task_bytes": MAX_TASK_BYTES,
            "max_files": MAX_TASK_FILES,
            "retention_days": RETENTION_SECONDS // 86400,
            "scope": ("UTF-8 text files only; excludes sensitive paths, detected secrets and links. "
                      "External effects are not restored."),
        },
    }


class RestoreConflictError(ValueError):
    pass


def _restore_file(root, change, blobs):
    """Pin each parent directory; a swapped link cannot redirect a write."""
    parts = Path(change["path"]).parts
    if not parts or Path(change["path"]).is_absolute() or ".." in parts:
        raise RestoreConflictError("Invalid recorded path")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            try:
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        name = parts[-1]
        before = change["before"]
        if before is None:
            os.unlink(name, dir_fd=descriptor)
        else:
            temporary = ".restore-" + uuid.uuid4().hex
            try:
                handle = os.open(
                    temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, before[1], dir_fd=descriptor
                )
                with os.fdopen(handle, "wb") as stream:
                    os.fchmod(stream.fileno(), before[1])
                    stream.write(blobs[before[0]].encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
            finally:
                try:
                    os.unlink(temporary, dir_fd=descriptor)
                except FileNotFoundError:
                    pass
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def restore_changes(root, user_id, trace_id, paths, expected_version):
    """Preflight every selected file; retain a per-file journal on interruption."""
    with workspace_write_lock(user_id):
        view = task_changes(root, user_id, trace_id)
        if view["version"] != expected_version or view["pending_execution"]:
            raise RestoreConflictError("Task evidence changed or execution remains unresolved")
        if view["restore"] and view["restore"]["status"] != "completed":
            raise RestoreConflictError("Previous restore is incomplete; inspect its applied paths before proceeding")
        by_path = {c["path"]: c for c in view["files"]}
        if not paths or len(paths) != len(set(paths)) or any(p not in by_path for p in paths):
            raise RestoreConflictError("Select distinct paths from this task")
        selected = [by_path[p] for p in paths]
        conflicts = [c["path"] for c in selected if not c["restorable"]]
        for change in selected:
            destination = root / change["path"]
            if not destination.resolve().is_relative_to(root.resolve()) or any(
                p.is_symlink() or (p.exists() and not p.is_dir()) for p in destination.parents if p.is_relative_to(root)
            ):
                conflicts.append(change["path"])
        if conflicts:
            raise RestoreConflictError("No files restored; unavailable or changed: " + ", ".join(conflicts))
        snapshots = _read_snapshots(_snapshot_path(root, user_id, trace_id))
        journal_path = _path(user_id, trace_id, root).with_suffix(".restore.json")
        journal = {
            "operation_id": uuid.uuid4().hex,
            "status": "running",
            "started_at": time.time(),
            "requested_paths": paths,
            "applied_paths": [],
            "version": expected_version,
        }
        _atomic_json(journal_path, journal)
        try:
            for change in selected:
                # Out-of-band IDE writers do not take our lock: check again just
                # before each replacement; this is not a whole-tree transaction.
                relative = change["path"]
                current = workspace_state(root)["files"].get(relative)
                if (list(current) if current else None) != change["after"]:
                    raise RestoreConflictError("Concurrent edit during restore: " + relative)
                destination = root / relative
                if not destination.resolve().is_relative_to(root.resolve()) or any(
                    p.is_symlink() for p in [destination, *destination.parents] if p.is_relative_to(root)
                ):
                    raise RestoreConflictError("Unsafe path during restore: " + relative)
                journal["attempting_path"] = relative
                _atomic_json(journal_path, journal)
                _restore_file(root, change, snapshots["blobs"])
                journal["applied_paths"].append(relative)
                journal["attempting_path"] = None
                _atomic_json(journal_path, journal)
            journal["status"] = "completed"
        except BaseException:
            journal["status"] = "interrupted"
            raise
        finally:
            journal["finished_at"] = time.time()
            _atomic_json(journal_path, journal)
        return journal
