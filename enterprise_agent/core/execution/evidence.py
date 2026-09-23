"""First-party mutation and validation receipts; never parse evidence from stdout."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from filelock import FileLock

from enterprise_agent.config.settings import settings
from enterprise_agent.sandbox.workspace import inventory

current_evidence: ContextVar[dict | None] = ContextVar("current_evidence", default=None)
CACHE_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".npm"}


def validation_command(command: str) -> dict | None:
    """Recognize a small, explicit argv grammar, not arbitrary Shell syntax."""
    if "\n" in command or "\r" in command:
        return None
    # One exact trailing merge preserves the runner's exit code. All other
    # redirects/compound syntax remain executable but cannot prove validation.
    command = re.sub(r"\s+2>&1\s*$", "", command)
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    cwd = "."
    if len(argv) >= 4 and argv[0] == "cd" and argv[2] == "&&":
        directory = Path(argv[1])
        if settings.AGENT_EXECUTOR == "docker" and directory == Path("/workspace"):
            directory = Path(".")
        if directory.is_absolute() or ".." in directory.parts or argv[1].startswith("-"):
            return None
        cwd = directory.as_posix()
        argv = argv[3:]
        checked_command = command.replace("&&", "", 1)
    else:
        checked_command = command
    if any(c in checked_command for c in ";&|<>`$\n\r()"):
        return None
    if not argv or any(arg.split("=")[0] in {
        "--version", "-V", "--help", "-h", "--collect-only", "--co", "--setup-plan", "--setup-only",
    } for arg in argv):
        return None
    # This literal only suppresses Python cache writes. Never accept arbitrary
    # assignments (PATH/PYTHONPATH/test flags can change what is being verified).
    entry = argv[1:] if argv[0] == "PYTHONDONTWRITEBYTECODE=1" else argv
    if not entry:
        return None
    invocation = entry
    if re.fullmatch(r"python(?:3(?:\.\d+)?)?", entry[0]):
        python_args = entry[1:]
        while python_args and python_args[0] in {"-B", "-I", "-E", "-s", "-u"}:
            python_args = python_args[1:]
        if len(python_args) < 2 or python_args[0] != "-m":
            return None
        entry = python_args[1:]
    kind = None
    if entry[0] in {"pytest", "unittest"}:
        kind = "test"
    elif entry[0] in {"py_compile", "compileall"} and entry is not invocation:
        kind = "syntax"
    elif entry[0] in {"mypy", "pyright"} or entry[:2] == ["ruff", "check"]:
        kind = "lint"
    elif entry[:2] in (["node", "--test"], ["go", "test"], ["cargo", "test"]):
        kind = "test"
    # Project scripts execute untrusted code under the usual sandbox/HITL policy.
    elif entry[0] in {"npm", "pnpm", "yarn"}:
        args = entry[1:]
        if args[:1] == ["run"]:
            args = args[1:]
        if args and args[0] in {"test", "build", "lint"}:
            if not any(arg.startswith(("--dry-run", "--if-present", "--ignore-scripts")) for arg in args[1:]):
                kind = args[0]
    return {"kind": kind, "runner": entry[0], "argv": argv, "cwd": cwd} if kind else None


def workspace_state(root: Path, *, dependency_workspace=None) -> dict:
    from enterprise_agent.sandbox.dependencies import dependency_stamp

    files = inventory(root, settings.SANDBOX_WORKSPACE_MAX_BYTES,
                      settings.SANDBOX_WORKSPACE_MAX_FILES, observe_links=True)
    files = {p: value for p, value in files.items() if not CACHE_PARTS.intersection(Path(p).parts)
             and not p.endswith((".pyc", ".pyo"))}
    stamps = {p: [*value, (root / p).lstat().st_mtime_ns, (root / p).lstat().st_ctime_ns]
              for p, value in files.items()}
    dependencies = dependency_stamp(dependency_workspace if dependency_workspace is not None else root)
    return {
        "files": files,
        "dependencies": dependencies,
        "version": hashlib.sha256(json.dumps([files, dependencies], sort_keys=True).encode()).hexdigest(),
        "stamp": hashlib.sha256(json.dumps([stamps, dependencies], sort_keys=True).encode()).hexdigest(),
    }


def _path(user_id, trace_id, root):
    from enterprise_agent.core.agent.tools.workspace_lock import _workspace_lock_directory

    if not trace_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", trace_id):
        raise ValueError("Invalid evidence trace ID")
    owner = "default" if user_id is None else str(int(user_id))
    workspace = hashlib.sha256(str(root.absolute()).encode()).hexdigest()[:24]
    directory = _workspace_lock_directory() / "evidence"
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_mode & 0o022:
        raise PermissionError("Unsafe evidence directory")
    path = directory / f"{owner}-{workspace}-{trace_id}.json"
    if path.is_symlink():
        raise PermissionError("Unsafe evidence file")
    return path


def save_receipt(receipt, user_id, root):
    context = current_evidence.get()
    trace_id = receipt.get("trace_id") or (context or {}).get("trace_id")
    receipt["trace_id"] = trace_id
    receipt["tool_call_id"] = (context or {}).get("tool_call_id")
    if trace_id:
        path = _path(user_id, trace_id, root)
        with FileLock(str(path) + ".lock"):
            events = json.loads(path.read_text()) if path.exists() else []
            receipt["seq"] = len(events) + 1
            events.append(receipt)
            temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            try:
                with temporary.open("x", encoding="utf-8") as handle:
                    os.chmod(temporary, 0o600)
                    json.dump(events, handle, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    if context is not None:
        context["receipts"].append(receipt)


def snapshot_text_before(root, user_id, state, trace_id=None):
    from enterprise_agent.core.execution.changes import capture_text

    trace_id = trace_id or (current_evidence.get() or {}).get("trace_id")
    if trace_id:
        capture_text(root, user_id, trace_id, state["files"])


def read_receipts(user_id, trace_id, root):
    if not trace_id:
        return []
    path = _path(user_id, trace_id, root)
    return json.loads(path.read_text()) if path.exists() else []


def finish_receipt(*, root, user_id, before, result, command="", execution_id=None, trace_id=None):
    from enterprise_agent.observability.trace_store import redact_text

    after = workspace_state(root)
    changes = [
        {"path": p, "before": before["files"].get(p), "after": after["files"].get(p),
         "operation": "added" if p not in before["files"] else "deleted" if p not in after["files"] else "modified"}
        for p in sorted(before["files"].keys() | after["files"].keys())
        if before["files"].get(p) != after["files"].get(p)
    ]
    # Docker publishes only these paths. Concurrent user edits invalidate a test
    # but must not be attributed to the Agent's file changes.
    if result.get("executor") == "docker":
        changes = [c for c in changes if c["path"] in result.get("published_paths", [])]
    snapshot_status = "available"
    try:
        snapshot_text_before(root, user_id, {"files": {c["path"]: c["after"] for c in changes if c["after"]}}, trace_id)
    except (OSError, ValueError):
        snapshot_status = "unavailable"
    classification = validation_command(command)
    if classification:
        classification["argv"] = [redact_text(arg, limit=None) for arg in classification["argv"]]
    fresh = (before["stamp"] == after["stamp"] and not result.get("validation_input_changed", False)
             and result.get("input_version", before["version"]) == before["version"])
    receipt = {
        "execution_id": execution_id or uuid.uuid4().hex, "trace_id": trace_id,
        "phase": "finished", "recorded_at": time.time(), "executor": result.get("executor", "file_tool"),
        "command": redact_text(command, limit=None), "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
        "cwd": classification["cwd"] if classification else ".",
        "input_version": before["version"], "input_stamp": before["stamp"],
        "output_version": after["version"], "output_stamp": after["stamp"], "changes": changes,
        "dependencies_changed": before.get("dependencies") != after.get("dependencies"),
        "snapshot_status": snapshot_status,
        "exit_code": result.get("exit_code"), "error_code": result.get("error_code"),
        "cancelled": bool(result.get("cancellation")), "timed_out": result.get("error_code") == "tool_timeout",
        "output_sha256": hashlib.sha256(
            (str(result.get("stdout", "")) + "\n" + str(result.get("stderr", ""))).encode()
        ).hexdigest(),
        "validation": classification, "fresh": fresh,
        "ok": bool(classification and fresh and result.get("exit_code") == 0
                   and not result.get("error_code") and not result.get("cancellation")),
    }
    save_receipt(receipt, user_id, root)
    return receipt


@contextmanager
def observe_file_mutation():
    """Called inside the existing workspace write lock, including exception exits."""
    from enterprise_agent.core.agent.tools.workspace import get_current_user_id, get_user_workspace
    from enterprise_agent.core.execution.approvals import require_current_approval

    require_current_approval()
    user_id = get_current_user_id()
    root = get_user_workspace(user_id)
    before = workspace_state(root)
    snapshot_text_before(root, user_id, before)
    identifier = uuid.uuid4().hex
    save_receipt({"execution_id": identifier, "phase": "running", "recorded_at": time.time(), "changes": []},
                 user_id, root)
    result = {"exit_code": 0, "executor": "file_tool"}
    try:
        yield
    except BaseException:
        result["error_code"] = "file_mutation_failed"
        raise
    finally:
        finish_receipt(root=root, user_id=user_id, before=before, result=result, execution_id=identifier)


def task_evidence(state):
    from enterprise_agent.core.agent.tools.workspace import get_user_workspace

    root = get_user_workspace(state.get("user_id"))
    events = read_receipts(state.get("user_id"), state.get("trace_id"), root)
    if not state.get("trace_id"):
        events = state.get("change_receipts", [])
    latest = {event["execution_id"]: event for event in events}
    current = workspace_state(root) if latest else None
    changed = set(state.get("changed_files", []))
    validations = []
    first_before, last_after = {}, {}
    for event in sorted(latest.values(), key=lambda e: (e.get("recorded_at", 0), e.get("seq", 0))):
        for change in event.get("changes", []):
            path = change['path']
            changed.add(path)
            first_before.setdefault(path, change['before'])
            last_after[path] = change['after']
        if event.get("validation"):
            validations.append({**event, "status": "success" if event.get("ok") else "unconfirmed",
                                "kind": event["validation"]["kind"],
                                "current": event.get("input_stamp") == (current or {}).get("stamp")})
    # A passing Node test cannot erase a failed Python test on the same inputs.
    by_check = {(v["kind"], v["validation"].get("runner"), v["validation"].get("cwd")): v
                for v in validations}
    current_checks = [v for v in by_check.values() if v["current"]]
    satisfied = bool(current_checks) and all(v["ok"] for v in current_checks)
    # Temporary helpers created and removed within the task remain audited,
    # but do not impose a code-validation requirement on a data-only result.
    temporary = {p for p in first_before if first_before[p] is None and last_after[p] is None}
    return {"changed_files": sorted(changed), "verification_paths": sorted(changed - temporary),
            "change_receipts": list(latest.values()),
            "validation_results": validations,
            "validation_satisfied": satisfied,
            "validation_scope": sorted({v["kind"] for v in current_checks if v["ok"]}),
            "behavioral_validation": satisfied and any(v["kind"] == "test" for v in current_checks),
            "pending_execution": any(e["phase"] == "running" for e in latest.values())}
