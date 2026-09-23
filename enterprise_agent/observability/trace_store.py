"""Workspace-scoped, redacted JSON trace persistence.

This is the portable single-process baseline used for local demos and
benchmarks. The API is intentionally storage-agnostic enough to migrate to a
central database/OpenTelemetry backend for multi-replica production deploys.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from enterprise_agent.core.agent.tools.workspace import get_user_workspace
from enterprise_agent.core.agent.tools.workspace_lock import _require_plain_directory, _workspace_lock_directory

TRACE_SCHEMA_VERSION = 1
MAX_TRACE_BYTES = 8 * 1024 * 1024
MAX_TRACE_EVENTS = 5000
logger = logging.getLogger(__name__)
TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "jwt",
    "llm_api_key",
    "password",
    "refresh_token",
    "secret",
    "secret_key",
}
STRING_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)((?:api[_-]?key|password|secret)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_text(value: str, limit: int | None = 2000) -> str:
    redacted = value
    for pattern in STRING_SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1) if match.lastindex else ''}[REDACTED]", redacted)
    if limit is not None and len(redacted) > limit:
        redacted = redacted[:limit] + "…[truncated]"
    return redacted


def redact_value(value: Any, *, limit: int = 2000) -> Any:
    """Recursively redact credentials and cap trace payload size."""
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in SENSITIVE_KEYS or normalized.endswith(("_password", "_secret", "_api_key")):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = redact_value(item, limit=limit)
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_value(item, limit=limit) for item in value[:100]]
    if isinstance(value, str):
        return redact_text(value, limit=limit)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value), limit=limit)


class TraceStore:
    """Atomic JSON trace store isolated by authenticated user workspace."""

    def __init__(self):
        self._lock = threading.RLock()

    def _trace_dir(self, user_id: int) -> Path:
        if int(user_id) < 0:
            raise ValueError("Invalid trace owner")
        base = _workspace_lock_directory() / "traces"
        base.mkdir(mode=0o700, exist_ok=True)
        _require_plain_directory(base, label="Trace control directory")
        directory = base / str(int(user_id))
        directory.mkdir(mode=0o700, exist_ok=True)
        _require_plain_directory(directory, label="Trace owner directory")
        return directory

    def _path(self, user_id: int, trace_id: str) -> Path:
        if not TRACE_ID_PATTERN.fullmatch(trace_id):
            raise ValueError("Invalid trace ID")
        path = self._trace_dir(user_id) / f"{trace_id}.json"
        if path.is_symlink():
            raise PermissionError("Trace file cannot be a symlink")
        return path

    def _read(self, user_id: int, trace_id: str) -> dict[str, Any]:
        path = self._path(user_id, trace_id)
        if not path.exists():
            path = get_user_workspace(user_id) / ".agent" / "traces" / f"{trace_id}.json"
            for parent in (path.parent.parent, path.parent):
                if parent.is_symlink():
                    raise FileNotFoundError(trace_id)
            legacy = True
        else:
            legacy = False
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise PermissionError("Trace must be a plain private file")
            data = stream.read(MAX_TRACE_BYTES + 1)
        if len(data) > MAX_TRACE_BYTES:
            raise ValueError("Trace exceeds read budget; administrator archival required")
        trace = json.loads(data)
        if trace.get("user_id") != int(user_id) or trace.get("trace_id") != trace_id:
            raise FileNotFoundError(trace_id)
        if legacy:
            # User-writable historical files remain visible, never promoted to
            # trusted approval/restore evidence. Do not delete their source.
            trace = {**redact_value({k: v for k, v in trace.items() if k != "events"}),
                     "events": [redact_value(e) for e in trace.get("events", [])[:MAX_TRACE_EVENTS]],
                     "storage_trust": "legacy_unverified"}
        return trace

    def _write(self, user_id: int, trace: dict[str, Any]) -> None:
        path = self._path(user_id, trace["trace_id"])
        # ponytail: bounded whole-file JSON for one API writer; migrate to MySQL
        # incremental events if measured latency or multiple writers require it.
        events = trace["events"]
        removed = max(0, len(events) - MAX_TRACE_EVENTS)
        if removed:
            del events[:removed]
        payload = json.dumps(trace, ensure_ascii=False, sort_keys=True)
        while len(payload.encode()) > MAX_TRACE_BYTES and events:
            count = max(1, len(events) // 4)
            del events[:count]
            removed += count
            payload = json.dumps(trace, ensure_ascii=False, sort_keys=True)
        if removed:
            trace["events_dropped"] = trace.get("events_dropped", 0) + removed
            trace["events_truncated"] = True
            logger.warning("Trace event retention cap reached for owner %s", user_id)
            payload = json.dumps(trace, ensure_ascii=False, sort_keys=True)
        if len(payload.encode()) > MAX_TRACE_BYTES:
            raise ValueError("Trace summary exceeds storage budget")
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def start_trace(
        self,
        *,
        trace_id: str,
        session_id: str,
        user_id: int,
        request_summary: str,
        mode: str = "single_agent",
    ) -> dict[str, Any]:
        now = utc_now_iso()
        trace = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "storage_trust": "service_control",
            "trace_id": trace_id,
            "session_id": session_id,
            "user_id": user_id,
            "mode": mode,
            "status": "pending",
            "current_phase": "parsing",
            "request_summary": redact_text(request_summary, limit=1000),
            "result_summary": None,
            "error": None,
            "started_at": now,
            "finished_at": None,
            "duration_ms": None,
            "metrics": {
                "node_count": 0,
                "node_duration_ms": 0,
                "model_calls": 0,
                "model_duration_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "tool_calls": 0,
                "tool_successes": 0,
                "tool_failures": 0,
                "tool_duration_ms": 0,
                "retry_count": 0,
                "confirmation_count": 0,
                "pause_count": 0,
                "resume_count": 0,
                "paused_duration_ms": 0,
                "safety_interceptions": 0,
                "memory_retrieval_queries": 0,
                "memory_candidates": 0,
                "memory_injected": 0,
                "memory_injected_tokens": 0,
            },
            "events": [{
                "event_id": str(uuid.uuid4()),
                "timestamp": now,
                "type": "task",
                "name": "task_created",
                "status": "pending",
                "duration_ms": 0,
                "data": {},
            }],
        }
        with self._lock:
            self._write(user_id, trace)
        return trace

    def record_event(
        self,
        *,
        user_id: int,
        trace_id: str,
        event_type: str,
        name: str,
        status: str = "success",
        duration_ms: int = 0,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            trace = self._read(user_id, trace_id)
            terminal_locked = bool(
                trace.get("finished_at")
                and trace.get("status") in {"succeeded", "failed", "cancelled"}
            )
            clean_data = redact_value(data or {})
            event = {
                "event_id": str(uuid.uuid4()),
                "timestamp": utc_now_iso(),
                "type": event_type,
                "name": name,
                "status": status,
                "duration_ms": max(0, int(duration_ms)),
                "data": clean_data,
            }
            trace["events"].append(event)
            metrics = trace["metrics"]

            if event_type == "node":
                metrics["node_count"] += 1
                metrics["node_duration_ms"] += event["duration_ms"]
                phase = clean_data.get("phase")
                if phase and not terminal_locked:
                    trace["current_phase"] = phase
                task_status = clean_data.get("task_status")
                if task_status and not terminal_locked:
                    trace["status"] = task_status
            elif event_type == "model":
                metrics["model_calls"] += 1
                metrics["model_duration_ms"] += event["duration_ms"]
                metrics["input_tokens"] += int(clean_data.get("input_tokens") or 0)
                metrics["output_tokens"] += int(clean_data.get("output_tokens") or 0)
                metrics["total_tokens"] += int(clean_data.get("total_tokens") or 0)
                metrics["retry_count"] += int(clean_data.get("retry_count") or 0)
            elif event_type == "tool":
                metrics["tool_calls"] += 1
                metrics["tool_duration_ms"] += event["duration_ms"]
                metrics["retry_count"] += max(0, int(clean_data.get("attempt_count") or 1) - 1)
                if status == "success":
                    metrics["tool_successes"] += 1
                else:
                    metrics["tool_failures"] += 1
                if status == "blocked":
                    metrics["safety_interceptions"] += 1
            elif (
                event_type == "confirmation"
                and name == "confirmation_requested"
                and not terminal_locked
            ):
                metrics["confirmation_count"] += 1
                trace["status"] = "waiting_confirmation"
            elif event_type == "control" and not terminal_locked:
                if name == "pause_requested":
                    trace["status"] = "pause_requested"
                elif name == "task_paused":
                    trace["status"] = "paused"
                    metrics["pause_count"] = metrics.get("pause_count", 0) + 1
                elif name == "resume_requested":
                    trace["status"] = "resuming"
                elif name == "task_resumed":
                    trace["status"] = "running"
                    metrics["resume_count"] = metrics.get("resume_count", 0) + 1
                    for previous in reversed(trace["events"][:-1]):
                        if previous.get("type") == "control" and previous.get("name") == "task_paused":
                            try:
                                paused_at = datetime.fromisoformat(previous["timestamp"])
                                resumed_at = datetime.fromisoformat(event["timestamp"])
                                metrics["paused_duration_ms"] = metrics.get(
                                    "paused_duration_ms", 0
                                ) + max(
                                    0,
                                    int((resumed_at - paused_at).total_seconds() * 1000),
                                )
                            except (KeyError, TypeError, ValueError):
                                pass
                            break
            elif event_type == "memory" and name == "memory_retrieval":
                metrics["memory_retrieval_queries"] += 1
                metrics["memory_candidates"] += len(clean_data.get("candidates") or [])
                metrics["memory_injected"] += int(clean_data.get("injected_count") or 0)
                metrics["memory_injected_tokens"] += int(
                    clean_data.get("injected_tokens") or 0
                )

            if status == "error" and not terminal_locked:
                trace["error"] = clean_data.get("error") or clean_data.get("message")
            self._write(user_id, trace)
            return event

    def finish_trace(
        self,
        *,
        user_id: int,
        trace_id: str,
        status: str,
        result_summary: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            trace = self._read(user_id, trace_id)
            if (
                trace.get("finished_at")
                and trace.get("status") in {"succeeded", "failed", "cancelled"}
            ):
                return trace
            finished = datetime.now(timezone.utc)
            started = datetime.fromisoformat(trace["started_at"])
            trace["status"] = status
            trace["finished_at"] = finished.isoformat()
            trace["duration_ms"] = max(0, int((finished - started).total_seconds() * 1000))
            trace["result_summary"] = redact_text(result_summary or "", limit=2000) or None
            trace["error"] = redact_text(error or "", limit=1000) or trace.get("error")
            trace["events"].append({
                "event_id": str(uuid.uuid4()),
                "timestamp": trace["finished_at"],
                "type": "task",
                "name": "task_finished",
                "status": status,
                "duration_ms": trace["duration_ms"],
                "data": {"error": trace["error"]},
            })
            self._write(user_id, trace)
            return trace

    def get_trace(self, user_id: int, trace_id: str) -> dict[str, Any]:
        with self._lock:
            return self._read(user_id, trace_id)

    def list_traces(self, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
        summaries = []
        current = self._trace_dir(user_id)
        legacy = get_user_workspace(user_id) / ".agent" / "traces"
        paths = list(current.glob("*.json"))
        if not legacy.is_symlink() and not legacy.parent.is_symlink():
            paths.extend(p for p in legacy.glob("*.json") if not (current / p.name).exists())
        for path in paths:
            try:
                trace = self.get_trace(user_id, path.stem)
                summary = {key: value for key, value in trace.items() if key != "events"}
                summary["event_count"] = len(trace.get("events", []))
                summaries.append(summary)
            except (OSError, ValueError):
                continue
        summaries.sort(key=lambda item: item.get("started_at", ""), reverse=True)
        return summaries[: max(1, min(limit, 500))]

    def aggregate_metrics(self, user_id: int) -> dict[str, Any]:
        traces = self.list_traces(user_id, limit=500)
        completed = [
            trace for trace in traces
            if trace.get("status") in {"succeeded", "failed", "cancelled"}
            and trace.get("storage_trust") != "legacy_unverified"
        ]
        task_count = len(completed)
        succeeded = sum(trace.get("status") == "succeeded" for trace in completed)
        tool_calls = sum(trace["metrics"].get("tool_calls", 0) for trace in completed)
        tool_successes = sum(trace["metrics"].get("tool_successes", 0) for trace in completed)
        confirmations = sum(trace["metrics"].get("confirmation_count", 0) for trace in completed)
        intervened_tasks = sum(trace["metrics"].get("confirmation_count", 0) > 0 for trace in completed)
        memory_tasks = sum(
            trace["metrics"].get("memory_injected", 0) > 0
            for trace in completed
        )

        def average(field: str) -> float:
            if not completed:
                return 0.0
            return round(sum(trace.get(field) or 0 for trace in completed) / len(completed), 2)

        return {
            "task_count": task_count,
            "succeeded": succeeded,
            "failed": sum(trace.get("status") == "failed" for trace in completed),
            "cancelled": sum(trace.get("status") == "cancelled" for trace in completed),
            "task_success_rate": round(succeeded / task_count, 4) if task_count else 0.0,
            "tool_calls": tool_calls,
            "tool_success_rate": round(tool_successes / tool_calls, 4) if tool_calls else 0.0,
            "average_duration_ms": average("duration_ms"),
            "average_tokens": round(
                sum(trace["metrics"].get("total_tokens", 0) for trace in completed) / task_count,
                2,
            ) if task_count else 0.0,
            "human_intervention_rate": round(intervened_tasks / task_count, 4) if task_count else 0.0,
            "confirmation_count": confirmations,
            "safety_interceptions": sum(
                trace["metrics"].get("safety_interceptions", 0) for trace in completed
            ),
            "memory_injection_rate": (
                round(memory_tasks / task_count, 4) if task_count else 0.0
            ),
            "memory_injected": sum(
                trace["metrics"].get("memory_injected", 0)
                for trace in completed
            ),
            "average_memory_tokens": (
                round(
                    sum(
                        trace["metrics"].get("memory_injected_tokens", 0)
                        for trace in completed
                    )
                    / task_count,
                    2,
                )
                if task_count
                else 0.0
            ),
        }


_trace_store = TraceStore()


def get_trace_store() -> TraceStore:
    return _trace_store
