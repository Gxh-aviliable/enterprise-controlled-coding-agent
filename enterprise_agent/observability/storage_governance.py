"""Single-writer service-record retention and verified offline backup utilities."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from filelock import FileLock, Timeout

from enterprise_agent.core.agent.tools.workspace import get_user_workspace
from enterprise_agent.core.agent.tools.workspace_lock import _workspace_lock_directory, workspace_write_lock
from enterprise_agent.core.execution.changes import RETENTION_SECONDS, _atomic_json
from enterprise_agent.core.execution.evidence import _path
from enterprise_agent.observability.trace_store import get_trace_store

logger = logging.getLogger(__name__)
TRACE_RETENTION_SECONDS = 30 * 86400
CAPACITY_WARNING_BYTES = 512 * 1024 * 1024
TERMINAL = {"succeeded", "failed", "cancelled"}


def _records(base):
    for section in ("traces", "evidence"):
        directory = base / section
        if directory.is_symlink():
            raise PermissionError("Unsafe service record directory")
        for path in directory.rglob("*.json"):
            if path.is_symlink() or any(p.is_symlink() for p in path.parents if p.is_relative_to(base)):
                raise PermissionError("Unsafe service record entry")
            if path.is_file() and path.stat().st_nlink == 1:
                yield path


def maintain_storage(now=None):
    """Delete only expired terminal service records; never legacy user files.

    Receipt/restore uncertainty holds the entire task for administrator review.
    A process-shared lock prevents concurrent janitors; TraceStore's lock serializes
    writers in the supported single API process. Multi-worker is not supported.
    """
    now = time.time() if now is None else now
    base = _workspace_lock_directory()
    store = get_trace_store()
    report = {"expired_snapshots": 0, "deleted_tasks": 0, "held_uncertain_tasks": 0, "errors": 0}
    try:
        with FileLock(str(base / "maintenance.lock"), timeout=0), store._lock:
            for path in (base / "evidence").glob("*.snapshots.json"):
                if path.is_symlink():
                    report["errors"] += 1
                    continue
                try:
                    with FileLock(str(path) + ".lock", timeout=0):
                        data = json.loads(path.read_text())
                        if now - data["created_at"] > RETENTION_SECONDS and not data.get("expired"):
                            _atomic_json(path, {**data, "blobs": {}, "expired": True})
                            report["expired_snapshots"] += 1
                except (OSError, ValueError, KeyError, Timeout):
                    report["errors"] += 1
            for path in (base / "traces").glob("*/*.json"):
                try:
                    if path.is_symlink() or path.parent.is_symlink():
                        raise ValueError("Unsafe trace entry")
                    owner = int(path.parent.name)
                    trace = store.get_trace(owner, path.stem)
                    if trace.get("status") not in TERMINAL or not trace.get("finished_at"):
                        continue
                    if now - datetime.fromisoformat(trace["finished_at"]).timestamp() <= TRACE_RETENTION_SECONDS:
                        continue
                    receipt = _path(owner, path.stem, get_user_workspace(owner))
                    journal = receipt.with_suffix(".restore.json")
                    with workspace_write_lock(owner, timeout=0), FileLock(str(receipt) + ".lock", timeout=0):
                        entries = json.loads(receipt.read_text()) if receipt.exists() else []
                        latest = {e["execution_id"]: e for e in entries}
                        restore = json.loads(journal.read_text()) if journal.exists() else {}
                        if any(e.get("phase") != "finished" for e in latest.values()) or (
                            restore and restore.get("status") != "completed"
                        ):
                            report["held_uncertain_tasks"] += 1
                            continue
                        # Locks are kept: unlinking a coordination inode races writers.
                        for suffix in (".stream.json", ".snapshots.json", ".restore.json"):
                            target = receipt.with_suffix(suffix)
                            with FileLock(str(target) + ".lock", timeout=0):
                                if target.is_symlink():
                                    raise ValueError("Unsafe evidence entry")
                                target.unlink(missing_ok=True)
                        receipt.unlink(missing_ok=True)
                        path.unlink()
                        report["deleted_tasks"] += 1
                except (OSError, ValueError, KeyError, Timeout):
                    report["errors"] += 1
    except Timeout:
        return {"status": "another_janitor_active"}
    records = list(_records(base))
    report.update(files=len(records), bytes=sum(p.stat().st_size for p in records), retention_days=30, snapshot_days=7)
    report["capacity_warning"] = report["bytes"] >= CAPACITY_WARNING_BYTES
    if report["capacity_warning"] or report["held_uncertain_tasks"] or report["errors"]:
        logger.warning("Service record maintenance needs attention: %s", report)
    return report


async def maintenance_loop():
    while True:
        try:
            await asyncio.to_thread(maintain_storage)
        except Exception:
            logger.exception("Service record maintenance failed; records retained")
        await asyncio.sleep(3600)


def backup_records(destination: Path):
    """Private diagnostic backup; DB/Redis need their own coordinated backup."""
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    base = _workspace_lock_directory()
    manifest = {"schema": 1, "created_at": time.time(), "scope": "service-records-only", "files": {}}
    with get_trace_store()._lock:
        for source in _records(base):
            with FileLock(str(source) + ".lock"):
                raw = source.read_bytes()
                relative = source.relative_to(base).as_posix()
                target = destination / relative
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                target.write_bytes(raw)
                os.chmod(target, 0o600)
                manifest["files"][relative] = hashlib.sha256(raw).hexdigest()
    _atomic_json(destination / "manifest.json", manifest)
    verify_backup(destination)
    return manifest


def verify_backup(directory: Path):
    manifest = json.loads((directory / "manifest.json").read_text())
    for relative, digest in manifest["files"].items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or path.parts[0] not in {"traces", "evidence"}:
            raise ValueError("Unsafe backup path")
        target = directory / path
        if target.is_symlink() or any(p.is_symlink() for p in target.parents if p.is_relative_to(directory)):
            raise ValueError("Unsafe backup link")
        raw = target.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Backup checksum mismatch")
        json.loads(raw)
    return manifest


def restore_backup(directory: Path, destination: Path):
    """Restore verified records to a NEW staging directory, never overwrite live data."""
    manifest = verify_backup(directory)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    for relative in manifest["files"]:
        target = destination / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(directory / relative, target)
        os.chmod(target, 0o600)
    _atomic_json(destination / "manifest.json", manifest)
    verify_backup(destination)
    return {"restored_files": len(manifest["files"]), "destination": str(destination), "live_data_changed": False}


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("cleanup")
    for action in ("backup", "verify", "restore"):
        command = sub.add_parser(action)
        command.add_argument("directory", type=Path)
        if action == "restore":
            command.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.action == "cleanup":
        result = maintain_storage()
    elif args.action == "backup":
        result = backup_records(args.directory)
        result = {"files": len(result["files"]), "scope": result["scope"], "verified": True}
    elif args.action == "verify":
        result = {"verified_files": len(verify_backup(args.directory)["files"])}
    else:
        result = restore_backup(args.directory, args.destination)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
