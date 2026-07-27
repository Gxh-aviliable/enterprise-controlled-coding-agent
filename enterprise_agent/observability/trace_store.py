"""Workspace-scoped, redacted JSON trace persistence.

This is the portable single-process baseline used for local demos and
benchmarks. The API is intentionally storage-agnostic enough to migrate to a
central database/OpenTelemetry backend for multi-replica production deploys.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from enterprise_agent.core.agent.tools.workspace import get_user_workspace

TRACE_SCHEMA_VERSION = 1
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


def redact_text(value: str, limit: int = 2000) -> str:
    redacted = value
    for pattern in STRING_SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1) if match.lastindex else ''}[REDACTED]", redacted)
    if len(redacted) > limit:
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
        directory = get_user_workspace(user_id) / ".agent" / "traces"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _path(self, user_id: int, trace_id: str) -> Path:
        if not TRACE_ID_PATTERN.fullmatch(trace_id):
            raise ValueError("Invalid trace ID")
        return self._trace_dir(user_id) / f"{trace_id}.json"

    def _read(self, user_id: int, trace_id: str) -> dict[str, Any]:
        path = self._path(user_id, trace_id)
        if not path.exists():
            raise FileNotFoundError(trace_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, user_id: int, trace: dict[str, Any]) -> None:
        path = self._path(user_id, trace["trace_id"])
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temporary.write_text(
            json.dumps(trace, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)

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
                if phase:
                    trace["current_phase"] = phase
                task_status = clean_data.get("task_status")
                if task_status:
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
            elif event_type == "confirmation" and name == "confirmation_requested":
                metrics["confirmation_count"] += 1
                trace["status"] = "waiting_confirmation"
            elif event_type == "memory" and name == "memory_retrieval":
                metrics["memory_retrieval_queries"] += 1
                metrics["memory_candidates"] += len(clean_data.get("candidates") or [])
                metrics["memory_injected"] += int(clean_data.get("injected_count") or 0)
                metrics["memory_injected_tokens"] += int(
                    clean_data.get("injected_tokens") or 0
                )

            if status == "error":
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
        for path in self._trace_dir(user_id).glob("*.json"):
            try:
                trace = json.loads(path.read_text(encoding="utf-8"))
                summary = {key: value for key, value in trace.items() if key != "events"}
                summary["event_count"] = len(trace.get("events", []))
                summaries.append(summary)
            except (OSError, json.JSONDecodeError):
                continue
        summaries.sort(key=lambda item: item.get("started_at", ""), reverse=True)
        return summaries[: max(1, min(limit, 500))]

    def aggregate_metrics(self, user_id: int) -> dict[str, Any]:
        traces = self.list_traces(user_id, limit=500)
        completed = [trace for trace in traces if trace.get("status") in {"succeeded", "failed", "cancelled"}]
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
