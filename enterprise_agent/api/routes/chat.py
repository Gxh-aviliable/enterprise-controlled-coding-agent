import asyncio
import json
import logging
import re
import time
import uuid
from contextlib import aclosing
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.admin.quotas import acquire_task_quota
from enterprise_agent.api.middleware.auth import get_current_user, get_current_user_permissions
from enterprise_agent.api.schemas.chat import (
    AgentCapabilities,
    ChatRequest,
    ChatResponse,
    ResumeRequest,
    SessionCreate,
    SessionResponse,
)
from enterprise_agent.api.services.chat_history import (
    build_model_history,
    claim_latest_continuation_receipt,
    create_assistant_message,
    find_assistant_message_id,
    get_latest_assistant_task,
    mark_assistant_message_cancelled,
    merge_timeline,
    message_counts_by_session,
    persist_legacy_messages,
    serialize_message,
    start_turn,
    update_assistant_message,
)
from enterprise_agent.api.services.chat_history import (
    list_messages as list_durable_messages,
)
from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.graph import get_agent_graph
from enterprise_agent.core.agent.tools.workspace import set_current_user_id
from enterprise_agent.core.execution.interrupt_control import (
    acquire_task_resume_lock,
    claim_active_trace_lease,
    clear_legacy_pause_key,
    get_active_trace_lease,
    get_trace_cancel_request,
    mark_active_trace_runner_stopped,
    owns_current_task_runner,
    release_active_trace_lease,
    release_task_resume_lock,
    renew_active_trace_runner,
    request_trace_cancellation,
    reserve_active_trace_runner,
    reset_current_task_control_identity,
    reset_current_task_runner_identity,
    scan_legacy_pause_keys,
    set_current_task_control_identity,
    set_current_task_runner_identity,
    set_user_pause_protocol_retired,
    start_active_trace_runner,
)
from enterprise_agent.core.execution.state_machine import (
    ExecutionPhase,
    InvalidTaskTransitionError,
    TaskStatus,
    transition_task_status,
)
from enterprise_agent.db.mysql import async_session_factory, get_db
from enterprise_agent.models.session import Session, SessionStatus
from enterprise_agent.observability.trace_store import get_trace_store


def _extract_delta(content) -> str:
    """Extract plain text delta from chunk content, which may be str or list of blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "")
                if t:
                    parts.append(t)
            elif hasattr(block, "text"):
                parts.append(block.text)
        return "".join(parts)
    return str(content) if content else ""


def _final_response_content(result: dict) -> str:
    """Extract the final assistant text from a terminal graph result."""
    messages = result.get("messages", [])
    if not messages:
        return ""
    last_message = messages[-1]
    raw_content = (
        last_message.content
        if hasattr(last_message, "content")
        else last_message.get("content", "")
        if isinstance(last_message, dict)
        else str(last_message)
    )
    if isinstance(raw_content, list):
        parts = []
        for block in raw_content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif hasattr(block, "text"):
                parts.append(str(block.text))
        return "\n".join(part for part in parts if part)
    return str(raw_content or "")


def _sse_event(payload: dict) -> str:
    """Serialize one SSE JSON event."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _task_terminal_outcome(
    values: dict,
    *,
    session_id: str,
    user_id: int,
    trace_id: str,
) -> tuple[str, str | None]:
    """Project one authoritative Agent terminal state into chat persistence.

    The graph state is the source of truth.  A normally exhausted HTTP/SSE
    iterator is only a transport fact; it must never be interpreted as task
    success on its own.  Missing identity, an unknown status, or a non-terminal
    status therefore fails closed instead of producing a false ``completed``
    assistant message.
    """
    if not isinstance(values, dict) or not values:
        return "failed", "Agent checkpoint is missing after execution."

    if str(values.get("session_id") or "") != str(session_id):
        return "failed", "Agent checkpoint session does not match the completed request."
    try:
        checkpoint_user_id = int(values.get("user_id", -1))
    except (TypeError, ValueError):
        checkpoint_user_id = -1
    if checkpoint_user_id != int(user_id):
        return "failed", "Agent checkpoint owner does not match the completed request."
    if str(values.get("trace_id") or "") != str(trace_id):
        return "failed", "Agent checkpoint trace does not match the completed request."

    task_status = values.get("task_status")
    if task_status == TaskStatus.SUCCEEDED.value:
        return "completed", None
    if task_status == TaskStatus.CANCELLED.value:
        return "cancelled", str(values.get("failure_reason") or "Task cancelled by user.")[:500]
    if task_status == TaskStatus.FAILED.value:
        reason = values.get("failure_reason") or values.get("error") or "Agent task failed."
        return "failed", str(reason)[:500]

    rendered_status = str(task_status) if task_status is not None else "missing"
    return (
        "failed",
        f"Agent execution ended before reaching a terminal task status ({rendered_status}).",
    )


def _terminal_stream_event(
    *,
    assistant_status: str,
    reason: str | None,
    session_id: str,
    trace_id: str,
) -> dict | None:
    """Return the terminal SSE event for non-success outcomes."""
    if assistant_status == "completed":
        return None
    if assistant_status == "cancelled":
        return {
            "event": "cancelled",
            "session_id": session_id,
            "trace_id": trace_id,
            "status": TaskStatus.CANCELLED.value,
            "message": reason or "Task cancelled by user.",
        }
    return {
        "event": "task_finished",
        "session_id": session_id,
        "trace_id": trace_id,
        "status": TaskStatus.FAILED.value,
        "task_status": TaskStatus.FAILED.value,
        "error": reason or "Agent task failed.",
    }


def _scoped_stream_event(
    event: dict,
    *,
    session_id: str,
    trace_id: str,
    fence: int | str,
) -> dict:
    """Attach the exact runner scope to every mutable SSE event."""
    return {
        **event,
        "session_id": session_id,
        "trace_id": trace_id,
        "stream_fence": fence,
    }


def _tool_sse_events(node_output: dict) -> list[dict]:
    """Build authoritative completion events for the current executor batch.

    ``pending_tool_calls`` is intentionally empty after execution, so it cannot
    be used to decide which cards should finish. The normalized execution
    records carry the real name/status/duration for success and failure alike.
    """
    tool_results = node_output.get("tool_results", {})
    current_ids = set(tool_results)
    records = {
        str(record.get("tool_call_id")): record
        for record in node_output.get("tool_execution_records", [])
        if str(record.get("tool_call_id")) in current_ids
    }
    events = []
    for tool_id, raw_result in tool_results.items():
        record = records.get(str(tool_id), {})
        result = str(record.get("output", raw_result))
        if len(result) > 2000:
            result = result[:2000] + "... [truncated]"
        metadata = {
            "id": str(tool_id),
            "name": record.get("tool_name", ""),
            "status": record.get("status", "error"),
            "ok": bool(record.get("ok", False)),
            "duration_ms": int(record.get("duration_ms") or 0),
            "error_code": record.get("error_code"),
        }
        if record.get("artifact_path"):
            metadata.update({
                "artifact_path": record["artifact_path"],
                "artifact_available": True,
                "artifact_storage_status": "stored",
                "artifact_sha256": record.get("artifact_sha256"),
            })
        elif record.get("artifact_error"):
            metadata.update({
                "artifact_available": False,
                "artifact_storage_status": "failed",
                "artifact_error": record["artifact_error"],
            })
        events.append({"event": "tool_result", "result": result, **metadata})
        events.append({"event": "tool_end", **metadata})
    return events


class _StreamTimelineRecorder:
    """Compact the visible SSE stream into durable assistant/tool blocks."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def record_delta(self, delta: str) -> None:
        if delta:
            self.entries = merge_timeline(
                self.entries,
                [{"role": "assistant", "content": delta}],
            )

    def record_event(self, event: dict) -> None:
        event_name = str(event.get("event") or "")
        if event_name == "tool_start":
            self._record_tool(event, default_status="running")
            return
        if event_name in {"tool_result", "tool_end"}:
            self._record_tool(event, default_status="error")
            return
        if event_name != "interrupt":
            return

        payload = event.get("data") if isinstance(event.get("data"), dict) else {}
        for tool in payload.get("tools", []) or []:
            if not isinstance(tool, dict):
                continue
            self._record_tool(
                {
                    "id": tool.get("id", ""),
                    "name": tool.get("name", ""),
                    "status": "waiting",
                },
                default_status="waiting",
            )

    def _record_tool(self, event: dict, *, default_status: str) -> None:
        raw_status = str(event.get("status") or "").lower()
        if raw_status:
            status = raw_status
        elif event.get("ok") is True:
            status = "done"
        elif event.get("ok") is False:
            status = "error"
        else:
            status = default_status

        entry = {
            "role": "tool_call",
            "toolCallId": str(event.get("id") or ""),
            "toolName": str(event.get("name") or "tool"),
            "toolStatus": status,
        }
        if "result" in event:
            entry["toolResult"] = event.get("result")
        if event.get("error_code"):
            entry["toolError"] = f"Tool failed: {event['error_code']}"
        if "duration_ms" in event:
            entry["toolDuration"] = event.get("duration_ms")
        self.entries = merge_timeline(self.entries, [entry])


_SUPPRESS_MAX_CHUNKS = 30  # Safety timeout for summary mode

# Per-trace cancellation events. A session can outlive many task traces, so a
# delayed cancel from an old browser stream must never stop a newer task.
_cancel_events: dict[str, "asyncio.Event"] = {}
_active_stream_traces: dict[str, str] = {}
_confirmation_timeout_tasks: dict[str, "asyncio.Task"] = {}

CANCELLATION_TOMBSTONE = (
    "*[Generation stopped by user. The preceding request was cancelled and "
    "will not be continued.]*"
)


async def _claim_new_trace_control(
    *,
    user_id: int,
    session_id: str,
    trace_id: str,
) -> dict:
    """Atomically claim the session before any new trace state is persisted."""
    lease = await claim_active_trace_lease(
        user_id,
        session_id,
        trace_id,
        ttl_seconds=settings.ACTIVE_TRACE_LEASE_SECONDS,
    )
    if lease is None:
        active = await get_active_trace_lease(user_id, session_id)
        active_trace = active.get("trace_id") if active else "unknown"
        raise HTTPException(
            status_code=409,
            detail=f"Task {active_trace} is still active for this session.",
        )
    return lease


async def _reserve_existing_trace_runner(
    *,
    user_id: int,
    session_id: str,
    trace_id: str,
) -> dict:
    """Reserve one runner for a stopped HITL trace, including legacy checkpoints."""
    if await get_trace_cancel_request(user_id, session_id, trace_id):
        raise HTTPException(status_code=409, detail="This task is being cancelled.")
    active = await get_active_trace_lease(user_id, session_id)
    if active is None:
        lease = await claim_active_trace_lease(
            user_id,
            session_id,
            trace_id,
            ttl_seconds=settings.ACTIVE_TRACE_LEASE_SECONDS,
        )
    else:
        if active.get("trace_id") != trace_id:
            raise HTTPException(status_code=409, detail="Another trace owns this session.")
        lease = await reserve_active_trace_runner(
            user_id,
            session_id,
            trace_id,
            active["lease_token"],
            ttl_seconds=settings.ACTIVE_TRACE_LEASE_SECONDS,
        )
    if lease is None:
        raise HTTPException(status_code=409, detail="This confirmation is already being resumed.")
    return lease


async def _mark_runner_stopped_and_release(
    *,
    user_id: int,
    session_id: str,
    trace_id: str,
    lease_token: str,
    runner_token: str,
    reason: str,
    release: bool,
) -> str:
    """Acknowledge runner quiescence before an exact compare-and-release."""
    await mark_active_trace_runner_stopped(
        user_id,
        session_id,
        trace_id,
        lease_token,
        runner_token,
        reason=reason,
        ttl_seconds=settings.ACTIVE_TRACE_LEASE_SECONDS,
    )
    if not release:
        return "retained"
    return await release_active_trace_lease(
        user_id,
        session_id,
        trace_id,
        lease_token,
    )


async def _runner_cancel_request(
    *,
    user_id: int,
    session_id: str,
    trace_id: str,
    local_event: asyncio.Event | None = None,
) -> dict | None:
    if local_event is not None and local_event.is_set():
        return await get_trace_cancel_request(user_id, session_id, trace_id) or {
            "reason": "Cancelled by user",
        }
    return await get_trace_cancel_request(user_id, session_id, trace_id)


async def _wait_for_cancelled_lease_release(
    *,
    user_id: int,
    session_id: str,
    trace_id: str,
    timeout: float,
) -> bool:
    """Wait briefly for the owner runner to persist cancellation and quiesce."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        lease = await get_active_trace_lease(user_id, session_id)
        if lease is None:
            return True
        if lease.get("trace_id") != trace_id:
            return False
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.05)


def _interrupt_payload(interrupt_obj) -> dict | None:
    """Normalize the first LangGraph interrupt payload without guessing its type."""
    candidates = interrupt_obj if isinstance(interrupt_obj, (tuple, list)) else [interrupt_obj]
    if not candidates:
        return None
    value = candidates[0]
    if hasattr(value, "value"):
        value = value.value
    return value if isinstance(value, dict) else None


def _snapshot_interrupt_payload(snapshot) -> dict | None:
    """Return the current checkpoint interrupt payload, if one exists."""
    if not snapshot:
        return None
    for task in getattr(snapshot, "tasks", ()) or ():
        for interrupt_obj in getattr(task, "interrupts", ()) or ():
            payload = _interrupt_payload(interrupt_obj)
            if payload:
                return payload
    return None


def _require_checkpoint_identity(
    values: dict,
    *,
    session_id: str,
    user_id: int,
    trace_id: str,
) -> None:
    """Reject a stale or cross-tenant control request before touching a checkpoint."""
    if not values:
        raise HTTPException(status_code=409, detail="The task checkpoint has expired.")
    if str(values.get("session_id") or "") != session_id:
        raise HTTPException(status_code=409, detail="Checkpoint session does not match the request.")
    if int(values.get("user_id", -1)) != int(user_id):
        raise HTTPException(status_code=409, detail="Checkpoint owner does not match the request.")
    if str(values.get("trace_id") or "") != trace_id:
        raise HTTPException(status_code=409, detail="The task trace is no longer active for this session.")


def _require_interrupt_type(snapshot, expected_type: str) -> dict:
    payload = _snapshot_interrupt_payload(snapshot)
    if not payload:
        raise HTTPException(status_code=409, detail="No resumable interrupt exists for this task.")
    if payload.get("type") != expected_type:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This endpoint resumes {expected_type!r}, but the task is waiting on "
                f"{payload.get('type', 'an unknown interrupt')!r}."
            ),
        )
    return payload


def _stream_interrupt_event(
    *,
    interrupt_obj,
    session_id: str,
    trace_id: str,
    user_id: int,
) -> tuple[dict, str]:
    """Map a typed graph interrupt to its authoritative SSE event."""
    payload = _interrupt_payload(interrupt_obj)
    if not payload or not payload.get("type"):
        raise RuntimeError("Agent produced an untyped interrupt; refusing an unsafe resume path.")

    interrupt_type = payload["type"]
    if interrupt_type == "tool_confirmation":
        _schedule_confirmation_timeout(
            session_id,
            trace_id,
            user_id,
            payload.get("deadline"),
        )
        return {"event": "interrupt", "data": payload}, "interrupted"
    raise RuntimeError(f"Unsupported interrupt type: {interrupt_type}")


def _cancel_confirmation_timeout(session_id: str) -> None:
    task = _confirmation_timeout_tasks.pop(session_id, None)
    if task and not task.done():
        task.cancel()


def _schedule_confirmation_timeout(
    session_id: str,
    trace_id: str,
    user_id: int,
    deadline_raw: str | None,
) -> None:
    """Resume an interrupted graph with a deterministic timeout rejection."""
    _cancel_confirmation_timeout(session_id)

    async def expire_confirmation():
        try:
            try:
                deadline = datetime.fromisoformat(deadline_raw) if deadline_raw else None
            except (TypeError, ValueError):
                deadline = None
            if deadline is None:
                deadline = datetime.now(timezone.utc) + timedelta(
                    seconds=settings.CONFIRMATION_TIMEOUT_SECONDS
                )
            delay = max(0.0, (deadline - datetime.now(timezone.utc)).total_seconds())
            await asyncio.sleep(delay)

            graph = get_agent_graph()
            config = {"configurable": {"thread_id": session_id}}
            snapshot = await graph.aget_state(config)
            values = snapshot.values if snapshot and snapshot.values else {}
            interrupt_payload = _snapshot_interrupt_payload(snapshot) or {}
            if (
                values.get("task_status") != TaskStatus.WAITING_CONFIRMATION.value
                or values.get("trace_id") != trace_id
                or values.get("user_id") != user_id
                or interrupt_payload.get("type") != "tool_confirmation"
            ):
                return

            resume_lock_token = await acquire_task_resume_lock(
                user_id,
                session_id,
                trace_id,
            )
            if not resume_lock_token:
                return
            lease = None
            control_token = None
            runner_context_token = None
            terminal_persisted = False
            assistant_status = "failed"
            try:
                lease = await _reserve_existing_trace_runner(
                    user_id=user_id,
                    session_id=session_id,
                    trace_id=trace_id,
                )
                if not await start_active_trace_runner(
                    user_id,
                    session_id,
                    trace_id,
                    lease["lease_token"],
                    lease["runner_token"],
                    ttl_seconds=settings.ACTIVE_TRACE_LEASE_SECONDS,
                ):
                    return
                set_current_user_id(user_id)
                control_token = set_current_task_control_identity(
                    user_id,
                    session_id,
                    trace_id,
                )
                runner_context_token = set_current_task_runner_identity(
                    user_id,
                    session_id,
                    trace_id,
                    lease["lease_token"],
                    lease["runner_token"],
                )
                result = await graph.ainvoke(
                    Command(resume={
                        "approved": False,
                        "approved_ids": [],
                        "reason": "confirmation_timeout",
                    }),
                    config,
                )
                assistant_status, timeout_error = _task_terminal_outcome(
                    result,
                    session_id=session_id,
                    user_id=user_id,
                    trace_id=trace_id,
                )
                if assistant_status == "failed" and result.get("task_status") not in {
                    TaskStatus.FAILED.value,
                    TaskStatus.SUCCEEDED.value,
                    TaskStatus.CANCELLED.value,
                }:
                    await _safe_mark_task_terminal(
                        graph,
                        config,
                        TaskStatus.FAILED,
                        timeout_error,
                        expected_trace_id=trace_id,
                    )
                if assistant_status == "cancelled":
                    await _safe_mark_durable_assistant_cancelled(
                        session_id=session_id,
                        user_id=user_id,
                        trace_id=trace_id,
                        values=result,
                        reason=timeout_error or "Cancelled by user",
                    )
                    terminal_persisted = await _durable_cancellation_confirmed(
                        session_id=session_id,
                        user_id=user_id,
                        trace_id=trace_id,
                    )
                else:
                    async with async_session_factory() as history_db:
                        message_id = await find_assistant_message_id(
                            history_db,
                            session_id=session_id,
                            user_id=user_id,
                            trace_id=trace_id,
                        )
                        if message_id is None:
                            message_id = await create_assistant_message(
                                history_db,
                                session_id=session_id,
                                user_id=user_id,
                                trace_id=trace_id,
                                status="interrupted",
                            )
                        terminal_persisted = await update_assistant_message(
                            history_db,
                            message_id=message_id,
                            user_id=user_id,
                            content="\n\n*[Tool confirmation timed out and was rejected.]*",
                            status=assistant_status,
                            append=True,
                        )
            finally:
                if runner_context_token is not None:
                    reset_current_task_runner_identity(runner_context_token)
                if control_token is not None:
                    reset_current_task_control_identity(control_token)
                if lease is not None:
                    await _mark_runner_stopped_and_release(
                        user_id=user_id,
                        session_id=session_id,
                        trace_id=trace_id,
                        lease_token=lease["lease_token"],
                        runner_token=lease["runner_token"],
                        reason="confirmation_timeout",
                        release=terminal_persisted
                        and assistant_status in {"completed", "failed", "cancelled"},
                    )
                await release_task_resume_lock(
                    user_id,
                    session_id,
                    trace_id,
                    resume_lock_token,
                )
            logging.info("[confirm] Session %s expired and was rejected", session_id)
        except asyncio.CancelledError:
            return
        except Exception:
            logging.warning("Automatic confirmation timeout failed", exc_info=True)
        finally:
            current = _confirmation_timeout_tasks.get(session_id)
            if current is asyncio.current_task():
                _confirmation_timeout_tasks.pop(session_id, None)

    _confirmation_timeout_tasks[session_id] = asyncio.create_task(expire_confirmation())


class InternalStreamFilter:
    """Per-SSE-stream filter for internal memory-evaluation model output.

    The previous module globals allowed one concurrent user's internal summary
    to suppress another user's normal tokens. Every generator now owns one
    instance, so state cannot cross session/request boundaries.
    """

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.suppress_internal = False
        self.mode = None
        self.chunk_count = 0
        self.json_balance = 0

    def is_internal_json(self, delta: str) -> bool:
        import re

        stripped = delta.strip()

        if self.suppress_internal:
            self.chunk_count += 1

            if self.mode == "json":
                for char in stripped:
                    if char == "{":
                        self.json_balance += 1
                    elif char == "}":
                        self.json_balance -= 1
                if self.json_balance <= 0:
                    self.reset()
                return True

            if self.mode == "summary":
                if re.search(r'\{\s*"importance"\s*:\s*[\d.]+', stripped):
                    self.mode = "json"
                    self.json_balance = stripped.count("{") - stripped.count("}")
                    if self.json_balance <= 0:
                        self.reset()
                    return True
                if self.chunk_count > _SUPPRESS_MAX_CHUNKS:
                    self.reset()
                    return False
                return True

        accumulator_headers = (
            "[User Request]:",
            "[Actions]:",
            "[Result]:",
            "[Key Findings]:",
            "[Prior Context]:",
            "[Prior compressed context]:",
        )
        if any(stripped.startswith(header) or f"\n{header}" in stripped for header in accumulator_headers):
            self.suppress_internal = True
            self.mode = "summary"
            self.chunk_count = 0
            self.json_balance = 0
            if re.search(r'\{\s*"importance"\s*:\s*[\d.]+', stripped):
                self.mode = "json"
                self.json_balance = stripped.count("{") - stripped.count("}")
                if self.json_balance <= 0:
                    self.reset()
            return True

        if stripped.startswith(('{"importance"', '[{"type"', '{"importance":', '{"type":')):
            self.suppress_internal = True
            self.mode = "json"
            self.chunk_count = 0
            self.json_balance = stripped.count("{") - stripped.count("}")
            if self.json_balance <= 0:
                self.reset()
            return True

        if re.search(r'\{\s*"importance"\s*:\s*[\d.]+', stripped):
            self.suppress_internal = True
            self.mode = "json"
            self.chunk_count = 0
            self.json_balance = stripped.count("{") - stripped.count("}")
            if self.json_balance <= 0:
                self.reset()
            return True

        has_reason = re.search(r'"reason"\s*:\s*"[^"]{5,}"\s*[,}]', stripped)
        has_importance = re.search(r'"importance"\s*:\s*[\d.]+', stripped)
        if has_reason and has_importance:
            self.suppress_internal = True
            self.mode = "json"
            self.chunk_count = 0
            self.json_balance = stripped.count("{") - stripped.count("}")
            if self.json_balance <= 0:
                self.reset()
            return True

        return False


# Compatibility wrappers for focused unit tests/older imports. Runtime SSE
# generators use their own InternalStreamFilter instance.
_default_stream_filter = InternalStreamFilter()


def _reset_stream_globals():
    _default_stream_filter.reset()


def _is_internal_json(delta: str) -> bool:
    """Check if a delta looks like internal evaluation output leaking from
    memory/importance evaluator LLM calls inside the LangGraph.

    Uses a STATEFUL suppression mode with two sub-modes:
    - "summary": suppress until importance JSON is detected or timeout
    - "json": track brace balance until JSON closes

    Detection covers two leak types:
    1. Structured task summary: [User Request]:, [Actions]:, etc.
    2. Importance evaluation JSON: {"importance": 0.X, "reason": "..."}
    """
    return _default_stream_filter.is_internal_json(delta)


def _extract_content_from_message(msg) -> str:
    """Extract text content from a message object or dict."""
    if isinstance(msg, dict):
        content = msg.get("content", "")
        return _extract_delta(content) if content else ""
    elif hasattr(msg, "content"):
        return _extract_delta(msg.content) if msg.content else ""
    return str(msg) if msg else ""


def _serialize_history_messages(raw_messages) -> list[dict]:
    """Serialize checkpoint messages into frontend-visible chat messages."""
    messages = []
    for msg in raw_messages:
        if hasattr(msg, "type"):
            role = msg.type
            logging.debug(
                "[history] msg type=%s, content_preview=%s",
                role,
                str(msg.content)[:80] if msg.content else "(empty)",
            )
            # Skip tool results (raw JSON) and internal system prompts
            if role in ("tool", "system"):
                continue
            content = _extract_content_from_message(msg)
            if not content:
                continue  # Skip empty messages (e.g., AI with only tool_calls)
            if role in ("human", "user"):
                role = "user"
            elif role in ("ai", "assistant"):
                role = "assistant"
            else:
                continue  # Unknown role — skip
            messages.append({"role": role, "content": content})
        elif isinstance(msg, dict):
            role = msg.get("role", "")
            if role in ("tool", "system"):
                continue
            content = _extract_content_from_message(msg)
            if not content:
                continue
            messages.append({
                "role": msg.get("role", "user"),
                "content": content
            })
    return messages


async def _load_history_messages(session_id: str, graph=None) -> list[dict]:
    """Load legacy frontend-visible messages from the LangGraph checkpoint."""
    graph = graph or get_agent_graph()
    state = await graph.aget_state({"configurable": {"thread_id": session_id}})

    if not state or not state.values:
        return []

    raw_messages = state.values.get("messages", [])
    logging.debug("[history] session=%s, raw message count=%s", session_id, len(raw_messages))
    return _serialize_history_messages(raw_messages)


def _checkpoint_history_expired(session: Session) -> bool:
    """Best-effort classification for legacy sessions without durable messages."""
    created_at = session.created_at
    if created_at is None:
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.CHECKPOINT_TTL_HOURS)
    return created_at <= cutoff


def _history_status(
    session: Session,
    *,
    durable_count: int,
    checkpoint_count: int = 0,
) -> str:
    metadata = session.session_metadata or {}
    if durable_count:
        return "partial" if metadata.get("history_gap") else "durable"
    if checkpoint_count:
        return "checkpoint"
    return "expired" if _checkpoint_history_expired(session) else "empty"


async def _prepare_durable_turn(
    db: AsyncSession,
    *,
    session: Session,
    user_id: int,
    graph,
) -> None:
    """Migrate readable legacy history or mark an irreversible history gap."""
    existing = await list_durable_messages(db, session_id=session.id, user_id=user_id)
    if existing:
        return

    try:
        legacy_messages = await _load_history_messages(session.id, graph)
    except Exception:
        logging.warning("Failed to inspect legacy history for session %s", session.id, exc_info=True)
        legacy_messages = []

    if legacy_messages:
        await persist_legacy_messages(
            db,
            session_id=session.id,
            user_id=user_id,
            messages=legacy_messages,
        )
        return

    if _checkpoint_history_expired(session):
        session.session_metadata = {
            **(session.session_metadata or {}),
            "history_gap": True,
            "history_gap_reason": "redis_checkpoint_expired",
            "history_gap_detected_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.commit()


async def _persist_stream_segment(
    *,
    message_id: int,
    user_id: int,
    content: str,
    status: str,
    timeline_entries: list[dict] | None = None,
) -> bool:
    """Persist one completed SSE segment with an independent DB session."""
    try:
        async with async_session_factory() as history_db:
            updated = await update_assistant_message(
                history_db,
                message_id=message_id,
                user_id=user_id,
                content=content,
                status=status,
                append=True,
                timeline_entries=timeline_entries,
            )
            if not updated:
                raise RuntimeError("Assistant stream row no longer exists.")
        return True
    except Exception:
        logging.exception("Failed to persist assistant stream segment message_id=%s", message_id)
        return False


async def migrate_readable_checkpoint_histories() -> int:
    """Best-effort startup migration for legacy Redis-only conversations."""
    migrated = 0
    graph = get_agent_graph()
    async with async_session_factory() as db:
        result = await db.execute(
            select(Session).where(Session.status != SessionStatus.DELETED)
        )
        sessions = result.scalars().all()
        for session in sessions:
            metadata = session.session_metadata or {}
            if metadata.get("history_migration_checked") and _checkpoint_history_expired(session):
                continue
            existing = await list_durable_messages(
                db,
                session_id=session.id,
                user_id=session.user_id,
            )
            if existing:
                continue
            try:
                legacy_messages = await _load_history_messages(session.id, graph)
                migrated += await persist_legacy_messages(
                    db,
                    session_id=session.id,
                    user_id=session.user_id,
                    messages=legacy_messages,
                )
                if not legacy_messages and _checkpoint_history_expired(session):
                    session.session_metadata = {
                        **metadata,
                        "history_migration_checked": True,
                        "history_migration_checked_at": datetime.now(timezone.utc).isoformat(),
                    }
                    await db.commit()
            except Exception:
                logging.warning(
                    "Failed to migrate legacy checkpoint history for session %s",
                    session.id,
                    exc_info=True,
                )
    return migrated


async def retire_legacy_user_pause_tasks() -> int:
    """Idempotently terminalize every pre-removal user-pause artifact.

    Phase-B instances call this before readiness.  A matching running v2 lease
    is a deployment-order violation and fails startup instead of racing an
    owner that can still write the checkpoint.
    """
    await set_user_pause_protocol_retired("user_pause_feature_retired")
    retired = 0
    graph = get_agent_graph()
    trace_store = get_trace_store()
    async with async_session_factory() as db:
        result = await db.execute(
            select(Session).where(Session.status != SessionStatus.DELETED)
        )
        sessions = result.scalars().all()
        for session in sessions:
            config = {"configurable": {"thread_id": session.id}}
            snapshot = await graph.aget_state(config)
            values = snapshot.values if snapshot and snapshot.values else {}
            interrupt_payload = _snapshot_interrupt_payload(snapshot) or {}
            is_legacy_checkpoint = (
                values.get("task_status")
                in {TaskStatus.PAUSED.value, "pause_requested", "resuming"}
                or interrupt_payload.get("type") == "user_pause"
            )
            if is_legacy_checkpoint:
                active = await get_active_trace_lease(session.user_id, session.id)
                legacy_trace_id = str(
                    values.get("trace_id") or interrupt_payload.get("trace_id") or ""
                )
                if active and active.get("trace_id") != legacy_trace_id:
                    raise RuntimeError(
                        "Legacy pause checkpoint and active lease disagree: "
                        f"session={session.id} checkpoint={legacy_trace_id} "
                        f"lease={active.get('trace_id')}"
                    )
                if active and active.get("runner_state") != "stopped":
                    raise RuntimeError(
                        "Cannot retire a legacy pause while its runner lease is active: "
                        f"session={session.id} trace={active.get('trace_id')}"
                    )
                if await _retire_legacy_pause_checkpoint(
                    graph,
                    config,
                    snapshot,
                    session_id=session.id,
                    user_id=session.user_id,
                ):
                    retired += 1
                    if active:
                        await _mark_runner_stopped_and_release(
                            user_id=session.user_id,
                            session_id=session.id,
                            trace_id=str(active["trace_id"]),
                            lease_token=active["lease_token"],
                            runner_token=active["runner_token"],
                            reason="user_pause_feature_retired",
                            release=True,
                        )

            # Trace JSON is an audit projection and can outlive its checkpoint.
            # Terminalize orphaned lifecycle projections without deleting their
            # historical paused events.
            for trace in trace_store.list_traces(session.user_id, limit=500):
                if trace.get("session_id") != session.id or trace.get("status") not in {
                    TaskStatus.PAUSED.value,
                    "pause_requested",
                    "resuming",
                }:
                    continue
                trace_id = str(trace.get("trace_id") or "")
                if not trace_id:
                    continue
                trace_store.finish_trace(
                    user_id=session.user_id,
                    trace_id=trace_id,
                    status=TaskStatus.CANCELLED.value,
                    error="user_pause_feature_retired",
                )
                await _safe_mark_durable_assistant_cancelled(
                    session_id=session.id,
                    user_id=session.user_id,
                    trace_id=trace_id,
                    values={"current_user_request": trace.get("request_summary", "")},
                    reason="user_pause_feature_retired",
                )
                await clear_legacy_pause_key(session.user_id, session.id, trace_id)
                retired += 1

    # Delete any orphan control key that no longer has a MySQL Session row.
    for key in await scan_legacy_pause_keys():
        parts = key.split(":", 4)
        if len(parts) != 5:
            continue
        try:
            key_user_id = int(parts[2])
        except ValueError:
            continue
        await clear_legacy_pause_key(key_user_id, parts[3], parts[4])
    return retired


async def restore_pending_confirmation_timeouts() -> int:
    """Re-arm durable tool-confirmation deadlines after a worker restart."""
    restored = 0
    graph = get_agent_graph()
    async with async_session_factory() as db:
        result = await db.execute(
            select(Session).where(Session.status != SessionStatus.DELETED)
        )
        for session in result.scalars().all():
            snapshot = await graph.aget_state(
                {"configurable": {"thread_id": session.id}}
            )
            values = snapshot.values if snapshot and snapshot.values else {}
            interrupt_payload = _snapshot_interrupt_payload(snapshot) or {}
            if (
                values.get("task_status") != TaskStatus.WAITING_CONFIRMATION.value
                or interrupt_payload.get("type") != "tool_confirmation"
                or values.get("user_id") != session.user_id
            ):
                continue
            trace_id = str(values.get("trace_id") or "")
            if not trace_id:
                continue
            _schedule_confirmation_timeout(
                session.id,
                trace_id,
                session.user_id,
                str(
                    values.get("confirmation_deadline")
                    or interrupt_payload.get("deadline")
                    or ""
                ),
            )
            restored += 1
    return restored


router = APIRouter(prefix="/chat", tags=["chat"])


def _agent_capabilities(permissions: list[str]) -> AgentCapabilities:
    globally_enabled = settings.ENABLE_MULTI_AGENT
    permitted = "tools:advanced" in permissions or "tools:all" in permissions
    available_modes = ["single_agent"]
    reason = None
    if globally_enabled and permitted:
        available_modes.append("multi_agent")
    elif not globally_enabled:
        reason = "Multi-Agent mode is disabled by server configuration."
    else:
        reason = "Multi-Agent mode requires the tools:advanced permission."
    return AgentCapabilities(
        available_modes=available_modes,
        multi_agent_enabled=globally_enabled,
        multi_agent_permitted=permitted,
        multi_agent_reason=reason,
    )


def _validate_execution_mode(mode: str, permissions: list[str]) -> None:
    if mode == "single_agent":
        return
    capabilities = _agent_capabilities(permissions)
    if not capabilities.multi_agent_enabled:
        raise HTTPException(status_code=409, detail=capabilities.multi_agent_reason)
    if not capabilities.multi_agent_permitted:
        raise HTTPException(status_code=403, detail=capabilities.multi_agent_reason)


_MULTI_AGENT_TERMS = (
    "多智能体",
    "多代理",
    "多个智能体",
    "多个代理",
    "子智能体",
    "子代理",
    "multi-agent",
    "multi agent",
    "multiple agents",
    "subagent",
    "sub-agent",
)
_MULTI_AGENT_ACTIONS = (
    "协作",
    "合作",
    "分工",
    "委派",
    "调用",
    "使用",
    "运用",
    "让",
    "delegate",
    "collaborat",
    "work together",
    "use",
)


def _requests_multi_agent_execution(content: str) -> bool:
    """Detect explicit requests to *run* multiple agents, not explain the concept."""
    normalized = re.sub(r"\s+", " ", content.strip().lower())
    return (
        any(term in normalized for term in _MULTI_AGENT_TERMS)
        and any(action in normalized for action in _MULTI_AGENT_ACTIONS)
    )


def _validate_request_mode(mode: str, content: str, permissions: list[str]) -> None:
    """Keep natural-language intent from silently falling back to fake collaboration."""
    _validate_execution_mode(mode, permissions)
    if mode == "single_agent" and _requests_multi_agent_execution(content):
        raise HTTPException(
            status_code=409,
            detail=(
                "This request explicitly asks for real Multi-Agent execution, but the request "
                "mode is Single. Select Multi and send it again; Single mode will not simulate "
                "agent collaboration with scripts or role-play."
            ),
        )


@router.get("/capabilities", response_model=AgentCapabilities)
async def get_agent_capabilities(
    permissions: list = Depends(get_current_user_permissions),
):
    """Return explicit execution-mode availability for the current user."""
    return _agent_capabilities(permissions)


async def _require_owned_session(
    session_id: str,
    user_id: int,
    db: AsyncSession,
) -> Session:
    """Return an active caller-owned session or hide its existence with 404."""
    result = await db.execute(
        select(Session).where(
            Session.id == session_id,
            Session.user_id == user_id,
            Session.status != SessionStatus.DELETED,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


async def _resolve_chat_session(
    request: ChatRequest,
    user_id: int,
    db: AsyncSession,
) -> str:
    """Verify a supplied session or create a caller-owned one compatibly."""
    if request.session_id:
        await _require_owned_session(request.session_id, user_id, db)
        return request.session_id

    session_id = str(uuid.uuid4())
    db.add(Session(
        id=session_id,
        user_id=user_id,
        title=request.content.strip()[:80] or "New task",
        status=SessionStatus.ACTIVE,
    ))
    await db.commit()
    return session_id


def _task_input(
    *,
    session_id: str,
    trace_id: str,
    user_id: int,
    permissions: list[str],
    content: str,
    mode: str = "single_agent",
    history_messages: list[dict[str, str]] | None = None,
    continuation_receipt: dict | None = None,
) -> dict:
    """Build a backward-compatible graph input for a new task run."""
    return {
        "session_id": session_id,
        "trace_id": trace_id,
        "user_id": user_id,
        "permissions": permissions,
        "execution_mode": mode,
        "current_user_request": content,
        "task_status": TaskStatus.PENDING.value,
        "execution_phase": ExecutionPhase.PARSING.value,
        "task_started_at": datetime.now(timezone.utc).isoformat(),
        "continuation_receipt": continuation_receipt,
        "messages": [
            *(history_messages or []),
            {"role": "user", "content": content},
        ],
    }


async def _claim_new_task_context(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: int,
    trace_id: str,
    graph,
) -> tuple[list[dict[str, str]], dict | None]:
    """Return durable fallback history and the one-shot cancellation receipt.

    A readable Redis checkpoint already contains the canonical LangGraph
    message list, so injecting MySQL rows in that case would duplicate every
    previous turn.  MySQL becomes model input only when checkpoint messages are
    unavailable.
    """
    snapshot = await graph.aget_state({"configurable": {"thread_id": session_id}})
    values = snapshot.values if snapshot and snapshot.values else {}
    history_messages: list[dict[str, str]] = []
    if not values.get("messages"):
        durable_rows = await list_durable_messages(
            db,
            session_id=session_id,
            user_id=user_id,
        )
        history_messages = build_model_history(
            durable_rows,
            max_messages=settings.MAX_MESSAGES_PER_SESSION,
            max_characters=settings.DURABLE_HISTORY_MAX_CHARS,
        )

    continuation_receipt = await claim_latest_continuation_receipt(
        db,
        session_id=session_id,
        user_id=user_id,
        consumer_trace_id=trace_id,
    )
    return history_messages, continuation_receipt


def _receipt_item_content(item: dict) -> str:
    return str(
        item.get("content")
        or item.get("title")
        or item.get("description")
        or item.get("activeForm")
        or ""
    ).strip()


def _build_continuation_receipt(
    values: dict,
    *,
    trace_id: str,
    reason: str,
    fallback_goal: str = "",
) -> dict:
    """Create a compact evidence hand-off for a fresh post-cancel trace."""
    todos = [item for item in values.get("todos", []) if isinstance(item, dict)]
    completed_items = [
        _receipt_item_content(item)
        for item in todos
        if item.get("status") == "completed" and _receipt_item_content(item)
    ][:20]
    incomplete_items = [
        _receipt_item_content(item)
        for item in todos
        if item.get("status") in {"pending", "in_progress"}
        and _receipt_item_content(item)
    ][:20]
    if not incomplete_items:
        incomplete_items = [
            "Re-inspect the workspace and chat evidence to determine remaining work."
        ]

    validations = []
    for item in values.get("validation_results", []) or []:
        if not isinstance(item, dict):
            continue
        validations.append({
            "command": str(item.get("command") or "")[:500],
            "ok": bool(item.get("ok")),
            "status": str(item.get("status") or "")[:50],
            "exit_code": item.get("exit_code"),
        })
        if len(validations) >= 20:
            break

    current_task = values.get("current_task")
    current_task = current_task if isinstance(current_task, dict) else {}
    original_goal = str(
        values.get("current_user_request")
        or current_task.get("request")
        or fallback_goal
        or "Unknown cancelled task goal"
    )[:10_000]
    receipt = {
        "schema_version": 1,
        "trace_id": trace_id,
        "cancelled_at": datetime.now(timezone.utc).isoformat(),
        "cancellation_reason": str(reason or "Cancelled by user")[:500],
        "original_task_goal": original_goal,
        "completed_items": completed_items,
        "incomplete_items": incomplete_items,
        "modified_files": [
            str(path)[:1000] for path in (values.get("changed_files", []) or [])[:100]
        ],
        "validation_results": validations,
        "risks": [
            "Cancellation is terminal and does not roll back file or external side effects already completed.",
            (
                "Operations that cannot be interrupted immediately are cancelled on a "
                "best-effort basis; verify workspace and external state before continuing."
            ),
        ],
    }
    return receipt


def _message_tool_calls(message) -> list[dict]:
    if isinstance(message, dict):
        return message.get("tool_calls", []) or []
    return getattr(message, "tool_calls", []) or []


def _message_tool_call_id(message) -> str:
    if isinstance(message, dict):
        return str(message.get("tool_call_id") or "")
    return str(getattr(message, "tool_call_id", "") or "")


def _cancellation_checkpoint_messages(values: dict, trace_id: str) -> list:
    """Close unresolved tool calls and append one idempotent cancellation turn."""
    unresolved: dict[str, str] = {}
    for message in values.get("messages", []) or []:
        for tool_call in _message_tool_calls(message):
            if not isinstance(tool_call, dict):
                continue
            tool_call_id = str(tool_call.get("id") or "")
            if tool_call_id:
                unresolved[tool_call_id] = str(tool_call.get("name") or "")
        tool_call_id = _message_tool_call_id(message)
        if tool_call_id:
            unresolved.pop(tool_call_id, None)

    messages = [
        ToolMessage(
            content=f"Tool execution not performed (task_cancelled): {tool_name}",
            tool_call_id=tool_call_id,
            id=f"task-cancelled-tool:{trace_id}:{tool_call_id}",
        )
        for tool_call_id, tool_name in unresolved.items()
    ]
    messages.append(AIMessage(
        content=CANCELLATION_TOMBSTONE,
        id=f"task-cancelled:{trace_id}",
    ))
    return messages


async def _safe_mark_durable_assistant_cancelled(
    *,
    session_id: str,
    user_id: int,
    trace_id: str,
    values: dict | None = None,
    reason: str = "Cancelled by user",
) -> None:
    """Best-effort MySQL convergence and durable continuation hand-off."""
    try:
        fallback_goal = ""
        try:
            fallback_goal = str(
                get_trace_store().get_trace(user_id, trace_id).get("request_summary")
                or ""
            )
        except (FileNotFoundError, ValueError):
            pass
        receipt = _build_continuation_receipt(
            values or {},
            trace_id=trace_id,
            reason=reason,
            fallback_goal=fallback_goal,
        )
        async with async_session_factory() as history_db:
            updated = await mark_assistant_message_cancelled(
                history_db,
                session_id=session_id,
                user_id=user_id,
                trace_id=trace_id,
                tombstone=CANCELLATION_TOMBSTONE,
                continuation_receipt=receipt,
            )
            if not updated:
                # Pre-durable legacy checkpoints and a Stop that wins before
                # first checkpoint can lack an assistant row for this exact
                # trace.  Create the audit anchor instead of silently losing
                # the terminal receipt.
                await create_assistant_message(
                    history_db,
                    session_id=session_id,
                    user_id=user_id,
                    trace_id=trace_id,
                    status="interrupted",
                )
                updated = await mark_assistant_message_cancelled(
                    history_db,
                    session_id=session_id,
                    user_id=user_id,
                    trace_id=trace_id,
                    tombstone=CANCELLATION_TOMBSTONE,
                    continuation_receipt=receipt,
                )
            try:
                get_trace_store().record_event(
                    user_id=user_id,
                    trace_id=trace_id,
                    event_type="control",
                    name="continuation_receipt_persisted",
                    status="success" if updated else "error",
                    data=receipt,
                )
            except (FileNotFoundError, ValueError):
                logging.warning(
                    "Trace was unavailable while recording continuation receipt %s",
                    trace_id,
                )
    except Exception:
        logging.warning("Failed to persist cancelled assistant message", exc_info=True)


async def _durable_cancellation_confirmed(
    *,
    session_id: str,
    user_id: int,
    trace_id: str,
) -> bool:
    """Require both the terminal assistant row and its continuation receipt."""
    try:
        async with async_session_factory() as history_db:
            evidence = await get_latest_assistant_task(
                history_db,
                session_id=session_id,
                user_id=user_id,
                trace_id=trace_id,
            )
    except Exception:
        logging.warning("Failed to verify durable cancellation", exc_info=True)
        return False
    return bool(
        evidence
        and evidence.get("status") == "cancelled"
        and evidence.get("continuation_receipt")
    )


def _start_task_trace(
    *,
    session_id: str,
    trace_id: str,
    user_id: int,
    content: str,
    mode: str = "single_agent",
) -> None:
    get_trace_store().start_trace(
        trace_id=trace_id,
        session_id=session_id,
        user_id=user_id,
        request_summary=content,
        mode=mode,
    )


async def _safe_mark_task_terminal(
    graph,
    config: dict,
    target: TaskStatus,
    reason: str | None = None,
    *,
    expected_trace_id: str | None = None,
) -> bool:
    """Best-effort terminal checkpoint update used by API error/cancel paths."""
    try:
        snapshot = await graph.aget_state(config)
        values = snapshot.values if snapshot and snapshot.values else {}
        if expected_trace_id and values.get("trace_id") != expected_trace_id:
            logging.warning(
                "Ignoring stale terminal update for trace %s; checkpoint now belongs to %s",
                expected_trace_id,
                values.get("trace_id"),
            )
            return False
        current = values.get("task_status")
        try:
            status = transition_task_status(current, target)
        except InvalidTaskTransitionError:
            if target == TaskStatus.CANCELLED and current in {
                TaskStatus.PAUSED.value,
                "pause_requested",
                "resuming",
            }:
                status = target.value
            elif current == target.value:
                status = target.value
            else:
                logging.warning("Cannot mark task %s from terminal status %s", target.value, current)
                return False
        terminal_update = {
            "task_status": status,
            "task_finished_at": datetime.now(timezone.utc).isoformat(),
            "failure_reason": reason,
            "pending_tool_calls": [],
            "should_end": True,
        }
        from enterprise_agent.core.agent.nodes import terminalize_open_work_items

        terminal_update.update({
            "todos": terminalize_open_work_items(values, status),
            "has_open_todos": False,
        })
        if target == TaskStatus.CANCELLED:
            trace_id = str(values.get("trace_id") or expected_trace_id or "")
            if trace_id:
                terminal_update["messages"] = _cancellation_checkpoint_messages(
                    values,
                    trace_id,
                )
            # A cancellation is a terminal fence, not a resumable checkpoint.
            # At this point callers have stopped/closed the owner runner.
            await graph.aupdate_state(
                config,
                terminal_update,
                as_node="persist_memory",
            )
        else:
            await graph.aupdate_state(config, terminal_update)
        trace_id = values.get("trace_id")
        user_id = values.get("user_id")
        if trace_id and user_id is not None:
            try:
                await clear_legacy_pause_key(
                    int(user_id),
                    str(values.get("session_id") or config["configurable"]["thread_id"]),
                    str(trace_id),
                )
            except Exception:
                logging.warning("Failed to clear legacy pause request", exc_info=True)
            try:
                get_trace_store().finish_trace(
                    user_id=user_id,
                    trace_id=trace_id,
                    status=status,
                    error=reason,
                )
            except Exception:
                logging.warning("Failed to finish terminal task trace", exc_info=True)
        return True
    except Exception:
        logging.warning("Failed to persist terminal task status", exc_info=True)
        return False


async def _read_stream_terminal_outcome(
    graph,
    config: dict,
    *,
    session_id: str,
    user_id: int,
    trace_id: str,
) -> tuple[str, str | None]:
    """Read and project the final checkpoint for a normally exhausted stream.

    If the graph iterator ends while its checkpoint is missing or non-terminal,
    converge a matching live task to ``failed`` on a best-effort basis.  The
    returned assistant status remains failed even when that convergence cannot
    be persisted, so transport completion can never masquerade as task success.
    """
    try:
        snapshot = await graph.aget_state(config)
        values = snapshot.values if snapshot and snapshot.values else {}
    except Exception as exc:
        logging.warning("Failed to read terminal stream checkpoint", exc_info=True)
        values = {}
        assistant_status = "failed"
        reason = f"Unable to read the final Agent checkpoint: {str(exc)[:400]}"
    else:
        assistant_status, reason = _task_terminal_outcome(
            values,
            session_id=session_id,
            user_id=user_id,
            trace_id=trace_id,
        )

    if assistant_status == "failed" and values.get("task_status") not in {
        TaskStatus.FAILED.value,
        TaskStatus.SUCCEEDED.value,
        TaskStatus.CANCELLED.value,
    }:
        await _safe_mark_task_terminal(
            graph,
            config,
            TaskStatus.FAILED,
            reason,
            expected_trace_id=trace_id,
        )
    return assistant_status, reason


async def _converge_stream_cancellation(
    graph,
    config: dict,
    trace_id: str,
    fallback_status: str,
) -> str:
    """Resolve a Stop/completion race without downgrading an existing terminal task."""
    if await _safe_mark_task_terminal(
        graph,
        config,
        TaskStatus.CANCELLED,
        "Cancelled by user",
        expected_trace_id=trace_id,
    ):
        return "cancelled"
    try:
        snapshot = await graph.aget_state(config)
        values = snapshot.values if snapshot and snapshot.values else {}
        if values.get("trace_id") != trace_id:
            return fallback_status
        return {
            TaskStatus.SUCCEEDED.value: "completed",
            TaskStatus.FAILED.value: "failed",
            TaskStatus.CANCELLED.value: "cancelled",
        }.get(values.get("task_status"), fallback_status)
    except Exception:
        logging.warning("Failed to resolve stream cancellation race", exc_info=True)
        return fallback_status


async def _retire_legacy_pause_checkpoint(
    graph,
    config: dict,
    snapshot,
    *,
    session_id: str,
    user_id: int,
) -> bool:
    """Terminalize a straggling pre-removal user-pause checkpoint in place."""
    values = snapshot.values if snapshot and snapshot.values else {}
    interrupt_payload = _snapshot_interrupt_payload(snapshot) or {}
    legacy_statuses = {TaskStatus.PAUSED.value, "pause_requested", "resuming"}
    if values.get("task_status") not in legacy_statuses and interrupt_payload.get(
        "type"
    ) != "user_pause":
        return False
    trace_id = str(values.get("trace_id") or interrupt_payload.get("trace_id") or "")
    if not trace_id:
        return False
    _require_checkpoint_identity(
        values,
        session_id=session_id,
        user_id=user_id,
        trace_id=trace_id,
    )
    reason = "user_pause_feature_retired"
    await _safe_mark_task_terminal(
        graph,
        config,
        TaskStatus.CANCELLED,
        reason,
        expected_trace_id=trace_id,
    )
    await clear_legacy_pause_key(user_id, session_id, trace_id)
    await _safe_mark_durable_assistant_cancelled(
        session_id=session_id,
        user_id=user_id,
        trace_id=trace_id,
        values=values,
        reason=reason,
    )
    return True


async def _ensure_session_accepts_new_task(
    graph,
    *,
    session_id: str,
    user_id: int,
) -> None:
    """Prevent a new trace from overwriting any authoritative active owner."""
    active_lease = await get_active_trace_lease(user_id, session_id)
    if active_lease:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Task {active_lease.get('trace_id', '')} is still active for this session."
            ),
        )

    # The process-local map remains a latency/debugging optimization only.
    active_trace = _active_stream_traces.get(session_id)
    if active_trace:
        logging.warning(
            "Discarding stale process-local active trace %s for session %s",
            active_trace,
            session_id,
        )
        _active_stream_traces.pop(session_id, None)

    config = {"configurable": {"thread_id": session_id}}
    snapshot = await graph.aget_state(config)
    if await _retire_legacy_pause_checkpoint(
        graph,
        config,
        snapshot,
        session_id=session_id,
        user_id=user_id,
    ):
        snapshot = await graph.aget_state(config)
    values = snapshot.values if snapshot and snapshot.values else {}
    if values and values.get("user_id") not in {None, user_id}:
        raise HTTPException(status_code=409, detail="Checkpoint owner mismatch.")
    if values.get("task_status") in {
        TaskStatus.PENDING.value,
        TaskStatus.RUNNING.value,
        TaskStatus.WAITING_CONFIRMATION.value,
    }:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Task {values.get('trace_id', '')} is {values.get('task_status')}; "
                "resume or cancel it before starting another task in this session."
            ),
        )


@router.post("/completions", response_model=ChatResponse)
async def chat_completion(
    request: ChatRequest,
    user_id: int = Depends(get_current_user),
    permissions: list = Depends(get_current_user_permissions),
    db: AsyncSession = Depends(get_db),
):
    """Run one non-streaming task under an authoritative Redis trace lease."""
    _validate_request_mode(request.mode, request.content, permissions)
    if request.session_id:
        await _require_owned_session(request.session_id, user_id, db)
    quota_lease = await acquire_task_quota(user_id, db)
    assistant_message_id: int | None = None
    session_id: str | None = None
    trace_id: str | None = None
    graph = None
    config: dict | None = None
    lease: dict | None = None
    control_token = None
    runner_context_token = None
    runner_started = False
    terminal_persisted = False
    assistant_status = "failed"
    terminal_reason: str | None = None
    try:
        session_id = await _resolve_chat_session(request, user_id, db)
        trace_id = str(uuid.uuid4())
        graph = get_agent_graph()
        session = await _require_owned_session(session_id, user_id, db)
        await _prepare_durable_turn(db, session=session, user_id=user_id, graph=graph)
        await _ensure_session_accepts_new_task(
            graph,
            session_id=session_id,
            user_id=user_id,
        )
        lease = await _claim_new_trace_control(
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
        )
        _active_stream_traces[session_id] = trace_id
        assistant_message_id = await start_turn(
            db,
            session=session,
            user_id=user_id,
            trace_id=trace_id,
            content=request.content,
        )
        _start_task_trace(
            session_id=session_id,
            trace_id=trace_id,
            user_id=user_id,
            content=request.content,
            mode=request.mode,
        )
        history_messages, continuation_receipt = await _claim_new_task_context(
            db,
            session_id=session_id,
            user_id=user_id,
            trace_id=trace_id,
            graph=graph,
        )
        set_current_user_id(user_id)
        config = {"configurable": {"thread_id": session_id}}
        runner_started = await start_active_trace_runner(
            user_id,
            session_id,
            trace_id,
            lease["lease_token"],
            lease["runner_token"],
            ttl_seconds=settings.ACTIVE_TRACE_LEASE_SECONDS,
        )
        if not runner_started:
            cancellation = await get_trace_cancel_request(user_id, session_id, trace_id)
            if cancellation is None:
                raise HTTPException(status_code=409, detail="The task runner lease is no longer valid.")
            await _safe_mark_durable_assistant_cancelled(
                session_id=session_id,
                user_id=user_id,
                trace_id=trace_id,
                values={"current_user_request": request.content},
                reason=str(cancellation.get("reason") or "Cancelled by user"),
            )
            assistant_status = "cancelled"
            terminal_persisted = await _durable_cancellation_confirmed(
                session_id=session_id,
                user_id=user_id,
                trace_id=trace_id,
            )
            if not terminal_persisted:
                raise HTTPException(
                    status_code=503,
                    detail="Cancellation is still converging; retry or check status.",
                )
            return ChatResponse(
                session_id=session_id,
                trace_id=trace_id,
                message_id=assistant_message_id,
                role="assistant",
                content=CANCELLATION_TOMBSTONE,
                created_at=datetime.now(timezone.utc),
            )

        control_token = set_current_task_control_identity(user_id, session_id, trace_id)
        runner_context_token = set_current_task_runner_identity(
            user_id,
            session_id,
            trace_id,
            lease["lease_token"],
            lease["runner_token"],
        )
        try:
            result = await asyncio.wait_for(
                graph.ainvoke(
                    _task_input(
                        session_id=session_id,
                        trace_id=trace_id,
                        user_id=user_id,
                        permissions=permissions,
                        content=request.content,
                        mode=request.mode,
                        history_messages=history_messages,
                        continuation_receipt=continuation_receipt,
                    ),
                    config=config,
                ),
                timeout=settings.AGENT_INVOKE_TIMEOUT_SECONDS,
            )
            assistant_status, terminal_reason = _task_terminal_outcome(
                result,
                session_id=session_id,
                user_id=user_id,
                trace_id=trace_id,
            )
            if assistant_status == "failed" and result.get("task_status") not in {
                TaskStatus.FAILED.value,
                TaskStatus.SUCCEEDED.value,
                TaskStatus.CANCELLED.value,
            }:
                await _safe_mark_task_terminal(
                    graph,
                    config,
                    TaskStatus.FAILED,
                    terminal_reason,
                    expected_trace_id=trace_id,
                )
        except asyncio.TimeoutError:
            if await owns_current_task_runner():
                await _safe_mark_task_terminal(
                    graph,
                    config,
                    TaskStatus.FAILED,
                    "Agent invocation timed out",
                    expected_trace_id=trace_id,
                )
            await update_assistant_message(
                db,
                message_id=assistant_message_id,
                user_id=user_id,
                content="",
                status="failed",
            )
            terminal_persisted = True
            raise HTTPException(status_code=504, detail="Request timed out")
        except Exception:
            if await owns_current_task_runner():
                await _safe_mark_task_terminal(
                    graph,
                    config,
                    TaskStatus.FAILED,
                    "Agent invocation failed",
                    expected_trace_id=trace_id,
                )
            if assistant_message_id is not None:
                await update_assistant_message(
                    db,
                    message_id=assistant_message_id,
                    user_id=user_id,
                    content="",
                    status="failed",
                )
                terminal_persisted = True
            raise
        finally:
            if runner_context_token is not None:
                reset_current_task_runner_identity(runner_context_token)
                runner_context_token = None
            if control_token is not None:
                reset_current_task_control_identity(control_token)
                control_token = None

        content = _final_response_content(result)
        if not content and assistant_status != "cancelled":
            assistant_status = "failed"
            terminal_reason = terminal_reason or "Agent returned no response"
            await _safe_mark_task_terminal(
                graph,
                config,
                TaskStatus.FAILED,
                terminal_reason,
                expected_trace_id=trace_id,
            )
        if assistant_status == "failed" and terminal_reason:
            content = f"{content}\n\n❌ **Task failed:** {terminal_reason}".strip()
        elif assistant_status == "cancelled":
            await _safe_mark_durable_assistant_cancelled(
                session_id=session_id,
                user_id=user_id,
                trace_id=trace_id,
                values=result,
                reason=terminal_reason or "Cancelled by user",
            )
            content = f"{content}\n\n{CANCELLATION_TOMBSTONE}".strip()
            terminal_persisted = await _durable_cancellation_confirmed(
                session_id=session_id,
                user_id=user_id,
                trace_id=trace_id,
            )
            if not terminal_persisted:
                raise RuntimeError("Failed to persist the cancellation receipt.")

        if assistant_status != "cancelled":
            updated = await update_assistant_message(
                db,
                message_id=assistant_message_id,
                user_id=user_id,
                content=content,
                status=assistant_status,
                append=False,
            )
            if not updated:
                raise RuntimeError("Failed to persist the terminal assistant response.")
            terminal_persisted = True
        return ChatResponse(
            session_id=session_id,
            trace_id=trace_id,
            message_id=assistant_message_id,
            role="assistant",
            content=content,
            created_at=datetime.now(timezone.utc),
        )
    finally:
        if runner_context_token is not None:
            reset_current_task_runner_identity(runner_context_token)
        if control_token is not None:
            reset_current_task_control_identity(control_token)
        if session_id and _active_stream_traces.get(session_id) == trace_id:
            _active_stream_traces.pop(session_id, None)
        if lease and session_id and trace_id:
            await _mark_runner_stopped_and_release(
                user_id=user_id,
                session_id=session_id,
                trace_id=trace_id,
                lease_token=lease["lease_token"],
                runner_token=lease["runner_token"],
                reason=assistant_status,
                release=terminal_persisted,
            )
        await quota_lease.release()


def _stream_graph_response(
    *,
    graph,
    config: dict,
    graph_input,
    session_id: str,
    trace_id: str,
    user_id: int,
    assistant_message_id: int,
    lease: dict,
    log_context: str,
    emit_task_started: bool = False,
    quota_lease=None,
    resume_lock_token: str | None = None,
) -> StreamingResponse:
    """Stream one fenced graph runner and release its lease only after durable terminalization."""

    async def generate():
        stream_filter = InternalStreamFilter()
        timeline = _StreamTimelineRecorder()
        assistant_parts: list[str] = []
        assistant_status = "interrupted"
        assistant_suffix = ""
        terminal_reason: str | None = None
        cancellation_values: dict = {}
        finalized = False
        runner_started = False
        control_token = None
        runner_context_token = None
        cancel_event = asyncio.Event()
        _cancel_events[trace_id] = cancel_event
        _active_stream_traces[session_id] = trace_id
        fence = lease["fence"]

        async def finalize_runner() -> bool:
            nonlocal finalized
            if finalized:
                return assistant_status in {"completed", "failed", "cancelled"}
            finalized = True
            if assistant_suffix:
                timeline.record_delta(assistant_suffix)
            persisted = await _persist_stream_segment(
                message_id=assistant_message_id,
                user_id=user_id,
                content="".join(assistant_parts) + assistant_suffix,
                status=assistant_status,
                timeline_entries=timeline.entries,
            )
            if assistant_status == "cancelled":
                await _safe_mark_durable_assistant_cancelled(
                    session_id=session_id,
                    user_id=user_id,
                    trace_id=trace_id,
                    values=cancellation_values,
                    reason=terminal_reason or "Cancelled by user",
                )
                async with async_session_factory() as history_db:
                    evidence = await get_latest_assistant_task(
                        history_db,
                        session_id=session_id,
                        user_id=user_id,
                        trace_id=trace_id,
                    )
                persisted = bool(
                    persisted
                    and evidence
                    and evidence.get("status") == "cancelled"
                    and evidence.get("continuation_receipt")
                )

            terminal = assistant_status in {"completed", "failed", "cancelled"}
            release_result = await _mark_runner_stopped_and_release(
                user_id=user_id,
                session_id=session_id,
                trace_id=trace_id,
                lease_token=lease["lease_token"],
                runner_token=lease["runner_token"],
                reason=assistant_status,
                release=terminal and persisted,
            )
            if _cancel_events.get(trace_id) is cancel_event:
                _cancel_events.pop(trace_id, None)
            if _active_stream_traces.get(session_id) == trace_id:
                _active_stream_traces.pop(session_id, None)
            if resume_lock_token is not None:
                await release_task_resume_lock(
                    user_id,
                    session_id,
                    trace_id,
                    resume_lock_token,
                )
            if quota_lease is not None:
                await quota_lease.release()
            return terminal and persisted and release_result in {"released", "missing"}

        try:
            logging.info("[%s] Resuming session=%s trace=%s", log_context, session_id, trace_id)
            runner_started = await start_active_trace_runner(
                user_id,
                session_id,
                trace_id,
                lease["lease_token"],
                lease["runner_token"],
                ttl_seconds=settings.ACTIVE_TRACE_LEASE_SECONDS,
            )
            if not runner_started:
                cancellation = await get_trace_cancel_request(user_id, session_id, trace_id)
                if cancellation is None:
                    raise RuntimeError("The active trace runner lease was lost before execution.")
                assistant_status = "cancelled"
                terminal_reason = str(cancellation.get("reason") or "Cancelled by user")
                cancellation_values = {
                    "current_user_request": (
                        graph_input.get("current_user_request", "")
                        if isinstance(graph_input, dict)
                        else ""
                    )
                }
                confirmed = await finalize_runner()
                if confirmed:
                    yield _sse_event(_scoped_stream_event(
                        {
                            "event": "cancelled",
                            "status": TaskStatus.CANCELLED.value,
                            "message": terminal_reason,
                        },
                        session_id=session_id,
                        trace_id=trace_id,
                        fence=fence,
                    ))
                return

            control_token = set_current_task_control_identity(user_id, session_id, trace_id)
            runner_context_token = set_current_task_runner_identity(
                user_id,
                session_id,
                trace_id,
                lease["lease_token"],
                lease["runner_token"],
            )
            if emit_task_started:
                yield _sse_event(_scoped_stream_event(
                    {"event": "task_started", "status": TaskStatus.PENDING.value},
                    session_id=session_id,
                    trace_id=trace_id,
                    fence=fence,
                ))

            interrupted_event = None
            async with aclosing(graph.astream(
                graph_input,
                config=config,
                stream_mode=["messages", "updates"],
            )) as graph_stream:
                async for stream_event in graph_stream:
                    cancellation = await _runner_cancel_request(
                        user_id=user_id,
                        session_id=session_id,
                        trace_id=trace_id,
                        local_event=cancel_event,
                    )
                    if cancellation is not None:
                        terminal_reason = str(cancellation.get("reason") or "Cancelled by user")
                        break
                    if not await renew_active_trace_runner(
                        user_id,
                        session_id,
                        trace_id,
                        lease["lease_token"],
                        lease["runner_token"],
                        ttl_seconds=settings.ACTIVE_TRACE_LEASE_SECONDS,
                    ):
                        raise RuntimeError("The active trace runner fence was lost.")

                    mode, data = stream_event
                    if mode == "messages":
                        msg_chunk, _ = data
                        if hasattr(msg_chunk, "content") and msg_chunk.content:
                            delta = _extract_delta(msg_chunk.content)
                            if delta and not stream_filter.is_internal_json(delta):
                                assistant_parts.append(delta)
                                timeline.record_delta(delta)
                                yield _sse_event(_scoped_stream_event(
                                    {"delta": delta},
                                    session_id=session_id,
                                    trace_id=trace_id,
                                    fence=fence,
                                ))
                        if hasattr(msg_chunk, "tool_calls") and msg_chunk.tool_calls:
                            for tool_call in msg_chunk.tool_calls:
                                if tool_call.get("name"):
                                    event = {
                                        "event": "tool_start",
                                        "id": tool_call.get("id", ""),
                                        "name": tool_call["name"],
                                    }
                                    timeline.record_event(event)
                                    yield _sse_event(_scoped_stream_event(
                                        event,
                                        session_id=session_id,
                                        trace_id=trace_id,
                                        fence=fence,
                                    ))
                    elif mode == "updates":
                        if "__interrupt__" in data:
                            event, assistant_status = _stream_interrupt_event(
                                interrupt_obj=data["__interrupt__"],
                                session_id=session_id,
                                trace_id=trace_id,
                                user_id=user_id,
                            )
                            timeline.record_event(event)
                            interrupted_event = _scoped_stream_event(
                                event,
                                session_id=session_id,
                                trace_id=trace_id,
                                fence=fence,
                            )
                            break
                        for node_name, node_output in data.items():
                            if node_name == "tool_executor":
                                for event in _tool_sse_events(node_output):
                                    timeline.record_event(event)
                                    yield _sse_event(_scoped_stream_event(
                                        event,
                                        session_id=session_id,
                                        trace_id=trace_id,
                                        fence=fence,
                                    ))

            cancellation = await _runner_cancel_request(
                user_id=user_id,
                session_id=session_id,
                trace_id=trace_id,
                local_event=cancel_event,
            )
            if cancellation is not None:
                terminal_reason = str(cancellation.get("reason") or terminal_reason or "Cancelled by user")
                snapshot = await graph.aget_state(config)
                cancellation_values = snapshot.values if snapshot and snapshot.values else {}
                checkpoint_cancelled = await _safe_mark_task_terminal(
                    graph,
                    config,
                    TaskStatus.CANCELLED,
                    terminal_reason,
                    expected_trace_id=trace_id,
                )
                if not checkpoint_cancelled:
                    latest = await graph.aget_state(config)
                    latest_values = latest.values if latest and latest.values else {}
                    assistant_status, terminal_reason = _task_terminal_outcome(
                        latest_values,
                        session_id=session_id,
                        user_id=user_id,
                        trace_id=trace_id,
                    )
                    if assistant_status not in {"completed", "failed"}:
                        assistant_status = "interrupted"
                        await finalize_runner()
                        return
                    if assistant_status == "failed":
                        assistant_suffix = f"\n\n❌ **Task failed:** {terminal_reason}"
                    confirmed = await finalize_runner()
                    if confirmed:
                        terminal_event = _terminal_stream_event(
                            assistant_status=assistant_status,
                            reason=terminal_reason,
                            session_id=session_id,
                            trace_id=trace_id,
                        )
                        if terminal_event is None:
                            yield "data: [DONE]\n\n"
                        else:
                            yield _sse_event(_scoped_stream_event(
                                terminal_event,
                                session_id=session_id,
                                trace_id=trace_id,
                                fence=fence,
                            ))
                    return
                from enterprise_agent.core.agent.tools.background import clear_background_manager

                clear_background_manager(session_id, trace_id)
                assistant_status = "cancelled"
                confirmed = await finalize_runner()
                if confirmed:
                    yield _sse_event(_scoped_stream_event(
                        {
                            "event": "cancelled",
                            "status": TaskStatus.CANCELLED.value,
                            "message": terminal_reason,
                        },
                        session_id=session_id,
                        trace_id=trace_id,
                        fence=fence,
                    ))
                return

            if interrupted_event is not None:
                await finalize_runner()
                yield _sse_event(interrupted_event)
                return

            assistant_status, terminal_reason = await _read_stream_terminal_outcome(
                graph,
                config,
                session_id=session_id,
                user_id=user_id,
                trace_id=trace_id,
            )
            if assistant_status == "cancelled":
                snapshot = await graph.aget_state(config)
                cancellation_values = snapshot.values if snapshot and snapshot.values else {}
            terminal_event = _terminal_stream_event(
                assistant_status=assistant_status,
                reason=terminal_reason,
                session_id=session_id,
                trace_id=trace_id,
            )
            if assistant_status == "failed":
                assistant_suffix = f"\n\n❌ **Task failed:** {terminal_reason}"
            confirmed = await finalize_runner()
            if not confirmed:
                yield _sse_event(_scoped_stream_event(
                    {
                        "event": "control_error",
                        "status": "finalizing",
                        "message": "Task ended but terminal persistence is still converging.",
                    },
                    session_id=session_id,
                    trace_id=trace_id,
                    fence=fence,
                ))
                return
            if terminal_event is None:
                yield "data: [DONE]\n\n"
            else:
                yield _sse_event(_scoped_stream_event(
                    terminal_event,
                    session_id=session_id,
                    trace_id=trace_id,
                    fence=fence,
                ))
        except GeneratorExit:
            logging.debug("[%s] Generator closed", log_context)
            assistant_status = "interrupted"
            return
        except Exception as exc:
            logging.exception("%s failed", log_context)
            if runner_started and await owns_current_task_runner():
                await _safe_mark_task_terminal(
                    graph,
                    config,
                    TaskStatus.FAILED,
                    str(exc)[:500],
                    expected_trace_id=trace_id,
                )
            assistant_status = "failed"
            assistant_suffix = f"\n\n❌ **Error:** {str(exc)[:500]}"
            terminal_reason = str(exc)[:500]
            confirmed = await finalize_runner()
            if confirmed:
                yield _sse_event(_scoped_stream_event(
                    _terminal_stream_event(
                        assistant_status=assistant_status,
                        reason=terminal_reason,
                        session_id=session_id,
                        trace_id=trace_id,
                    ),
                    session_id=session_id,
                    trace_id=trace_id,
                    fence=fence,
                ))
        finally:
            if runner_context_token is not None:
                reset_current_task_runner_identity(runner_context_token)
            if control_token is not None:
                reset_current_task_control_identity(control_token)
            await finalize_runner()

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    user_id: int = Depends(get_current_user),
    permissions: list = Depends(get_current_user_permissions),
    db: AsyncSession = Depends(get_db),
):
    """Start a new SSE task trace under a Redis active-trace lease."""
    _validate_request_mode(request.mode, request.content, permissions)
    if request.session_id:
        await _require_owned_session(request.session_id, user_id, db)
    quota_lease = await acquire_task_quota(user_id, db)
    session_id: str | None = None
    trace_id: str | None = None
    lease: dict | None = None
    try:
        session_id = await _resolve_chat_session(request, user_id, db)
        trace_id = str(uuid.uuid4())
        graph = get_agent_graph()
        session = await _require_owned_session(session_id, user_id, db)
        await _prepare_durable_turn(db, session=session, user_id=user_id, graph=graph)
        await _ensure_session_accepts_new_task(
            graph,
            session_id=session_id,
            user_id=user_id,
        )
        lease = await _claim_new_trace_control(
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
        )
        _active_stream_traces[session_id] = trace_id
        assistant_message_id = await start_turn(
            db,
            session=session,
            user_id=user_id,
            trace_id=trace_id,
            content=request.content,
        )
        history_messages, continuation_receipt = await _claim_new_task_context(
            db,
            session_id=session_id,
            user_id=user_id,
            trace_id=trace_id,
            graph=graph,
        )
        _start_task_trace(
            session_id=session_id,
            trace_id=trace_id,
            user_id=user_id,
            content=request.content,
            mode=request.mode,
        )
        set_current_user_id(user_id)
        graph_input = _task_input(
            session_id=session_id,
            trace_id=trace_id,
            user_id=user_id,
            permissions=permissions,
            content=request.content,
            mode=request.mode,
            history_messages=history_messages,
            continuation_receipt=continuation_receipt,
        )
        return _stream_graph_response(
            graph=graph,
            config={"configurable": {"thread_id": session_id}},
            graph_input=graph_input,
            session_id=session_id,
            trace_id=trace_id,
            user_id=user_id,
            assistant_message_id=assistant_message_id,
            lease=lease,
            log_context="stream/new-trace",
            emit_task_started=True,
            quota_lease=quota_lease,
        )
    except Exception:
        if session_id and _active_stream_traces.get(session_id) == trace_id:
            _active_stream_traces.pop(session_id, None)
        if lease and session_id and trace_id:
            await _mark_runner_stopped_and_release(
                user_id=user_id,
                session_id=session_id,
                trace_id=trace_id,
                lease_token=lease["lease_token"],
                runner_token=lease["runner_token"],
                reason="setup_failed",
                release=True,
            )
        await quota_lease.release()
        raise


def _stream_resumed_command(
    *,
    graph,
    config: dict,
    command: Command,
    session_id: str,
    trace_id: str,
    user_id: int,
    assistant_message_id: int,
    lease: dict,
    log_context: str,
    resume_lock_token: str,
) -> StreamingResponse:
    """Stream one exact tool-confirmation Command(resume) runner."""
    return _stream_graph_response(
        graph=graph,
        config=config,
        graph_input=command,
        session_id=session_id,
        trace_id=trace_id,
        user_id=user_id,
        assistant_message_id=assistant_message_id,
        lease=lease,
        log_context=log_context,
        resume_lock_token=resume_lock_token,
    )


@router.post("/stream/resume")
async def chat_stream_resume(
    session_id: str,
    approved: bool,
    body: ResumeRequest = None,
    user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Resume SSE stream after interrupt confirmation.

    Called by frontend when user approves/rejects tool execution.
    Uses Command(resume=...) to continue the interrupted graph.

    Args:
        session_id: Session/thread ID (from query param)
        approved: Whether user approved the tool(s)
        body: Resume request with approved_ids list
        user_id: Current user ID from JWT

    Returns:
        StreamingResponse with continued SSE events
    """
    await _require_owned_session(session_id, user_id, db)
    if body is None:
        raise HTTPException(status_code=422, detail="The interrupted trace_id is required.")
    set_current_user_id(user_id)

    approved_ids = body.approved_ids

    config = {"configurable": {"thread_id": session_id}}
    graph = get_agent_graph()
    snapshot = await graph.aget_state(config)
    values = snapshot.values if snapshot and snapshot.values else {}
    trace_id = values.get("trace_id")
    if not trace_id:
        raise HTTPException(
            status_code=409,
            detail="The interrupted task checkpoint has expired and cannot be resumed.",
        )
    if body.trace_id != trace_id:
        raise HTTPException(status_code=409, detail="The requested trace is no longer active.")
    _require_checkpoint_identity(
        values,
        session_id=session_id,
        user_id=user_id,
        trace_id=trace_id,
    )
    _require_interrupt_type(snapshot, "tool_confirmation")
    assistant_message_id = await find_assistant_message_id(
        db,
        session_id=session_id,
        user_id=user_id,
        trace_id=trace_id,
    )
    if assistant_message_id is None:
        assistant_message_id = await create_assistant_message(
            db,
            session_id=session_id,
            user_id=user_id,
            trace_id=trace_id,
            status="interrupted",
        )
    deadline_raw = values.get("confirmation_deadline")
    confirmation_expired = False
    if deadline_raw:
        try:
            confirmation_expired = datetime.fromisoformat(deadline_raw) <= datetime.now(timezone.utc)
        except (TypeError, ValueError):
            logging.warning("Invalid confirmation deadline for session %s", session_id)

    resume_payload = {
        "approved": approved and not confirmation_expired,
        "approved_ids": approved_ids or [],
    }
    if confirmation_expired:
        resume_payload["reason"] = "confirmation_timeout"

    resume_lock_token = await acquire_task_resume_lock(user_id, session_id, trace_id)
    if not resume_lock_token:
        raise HTTPException(status_code=409, detail="This confirmation is already being resumed.")
    # A stale/wrong-trace request must not disable the authoritative timeout.
    # Once this exact trace owns the resume lock, the timeout worker can no
    # longer race this Command(resume) and may be cancelled safely.
    _cancel_confirmation_timeout(session_id)
    lease = None
    try:
        if await get_trace_cancel_request(user_id, session_id, trace_id):
            raise HTTPException(status_code=409, detail="This task is being cancelled.")
        active = await get_active_trace_lease(user_id, session_id)
        if active is None:
            lease = await claim_active_trace_lease(
                user_id,
                session_id,
                trace_id,
                ttl_seconds=settings.ACTIVE_TRACE_LEASE_SECONDS,
            )
        else:
            if active.get("trace_id") != trace_id:
                raise HTTPException(status_code=409, detail="Another trace owns this session.")
            lease = await reserve_active_trace_runner(
                user_id,
                session_id,
                trace_id,
                active["lease_token"],
                ttl_seconds=settings.ACTIVE_TRACE_LEASE_SECONDS,
            )
        if lease is None:
            raise HTTPException(status_code=409, detail="This confirmation is already being resumed.")
        await update_assistant_message(
            db,
            message_id=assistant_message_id,
            user_id=user_id,
            content="",
            status="streaming",
        )
        return _stream_resumed_command(
            graph=graph,
            config=config,
            command=Command(resume=resume_payload),
            session_id=session_id,
            trace_id=trace_id,
            user_id=user_id,
            assistant_message_id=assistant_message_id,
            lease=lease,
            log_context="stream/resume-confirmation",
            resume_lock_token=resume_lock_token,
        )
    except Exception:
        if lease is not None:
            await mark_active_trace_runner_stopped(
                user_id,
                session_id,
                trace_id,
                lease["lease_token"],
                lease["runner_token"],
                reason="resume_setup_failed",
                ttl_seconds=settings.ACTIVE_TRACE_LEASE_SECONDS,
            )
        await release_task_resume_lock(
            user_id,
            session_id,
            trace_id,
            resume_lock_token,
        )
        raise


@router.get("/stream/status")
async def get_stream_status(
    session_id: str,
    user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the Redis lease lifecycle, falling back to durable terminal evidence."""
    await _require_owned_session(session_id, user_id, db)
    graph = get_agent_graph()
    config = {"configurable": {"thread_id": session_id}}
    snapshot = await graph.aget_state(config)
    values = snapshot.values if snapshot and snapshot.values else {}
    active = await get_active_trace_lease(user_id, session_id)

    if not active and await _retire_legacy_pause_checkpoint(
        graph,
        config,
        snapshot,
        session_id=session_id,
        user_id=user_id,
    ):
        snapshot = await graph.aget_state(config)
        values = snapshot.values if snapshot and snapshot.values else {}

    if active:
        trace_id = str(active.get("trace_id") or "")
        if values.get("trace_id") == trace_id:
            _require_checkpoint_identity(
                values,
                session_id=session_id,
                user_id=user_id,
                trace_id=trace_id,
            )
        cancellation = await get_trace_cancel_request(user_id, session_id, trace_id)
        interrupt_payload = (
            _snapshot_interrupt_payload(snapshot)
            if values.get("trace_id") == trace_id
            else None
        )
        return {
            "status": (
                "cancelling"
                if cancellation is not None or active.get("cancel_requested")
                else values.get("task_status", TaskStatus.PENDING.value)
                if values.get("trace_id") == trace_id
                else TaskStatus.PENDING.value
            ),
            "session_id": session_id,
            "trace_id": trace_id,
            "execution_phase": values.get("execution_phase"),
            "runner_state": active.get("runner_state"),
            "stream_fence": active.get("fence"),
            "cancel_requested": bool(cancellation or active.get("cancel_requested")),
            "interrupt_type": interrupt_payload.get("type") if interrupt_payload else None,
            "interrupt": interrupt_payload,
        }

    durable = await get_latest_assistant_task(
        db,
        session_id=session_id,
        user_id=user_id,
    )
    if durable and durable.get("trace_id") != values.get("trace_id"):
        return {
            "status": durable.get("status", "idle"),
            "session_id": session_id,
            "trace_id": durable.get("trace_id"),
            "execution_phase": None,
            "runner_state": None,
            "stream_fence": None,
            "cancel_requested": False,
            "interrupt_type": None,
            "interrupt": None,
        }
    if not values:
        return {
            "status": durable.get("status", "idle") if durable else "idle",
            "session_id": session_id,
            "trace_id": durable.get("trace_id") if durable else None,
            "execution_phase": None,
            "runner_state": None,
            "stream_fence": None,
            "cancel_requested": False,
            "interrupt_type": None,
            "interrupt": None,
        }
    if values.get("user_id") not in {None, user_id}:
        raise HTTPException(status_code=409, detail="Checkpoint owner mismatch.")
    interrupt_payload = _snapshot_interrupt_payload(snapshot)
    return {
        "status": values.get("task_status", "idle"),
        "session_id": session_id,
        "trace_id": values.get("trace_id"),
        "execution_phase": values.get("execution_phase"),
        "runner_state": None,
        "stream_fence": None,
        "cancel_requested": False,
        "interrupt_type": interrupt_payload.get("type") if interrupt_payload else None,
        "interrupt": interrupt_payload,
    }


async def request_task_cancellation(
    session_id: str,
    user_id: int,
    reason: str = "Cancelled by user",
    trace_id: str | None = None,
):
    """Request exact-trace Stop and report cancelled only after owner quiescence."""
    _cancel_confirmation_timeout(session_id)
    set_current_user_id(user_id)
    config = {"configurable": {"thread_id": session_id}}
    graph = get_agent_graph()
    state = await graph.aget_state(config)
    values = state.values if state and state.values else {}
    checkpoint_trace = str(values.get("trace_id") or "")
    active = await get_active_trace_lease(user_id, session_id)
    async with async_session_factory() as history_db:
        durable = await get_latest_assistant_task(
            history_db,
            session_id=session_id,
            user_id=user_id,
        )
    active_trace = str(active.get("trace_id") or "") if active else ""
    durable_trace = str(durable.get("trace_id") or "") if durable else ""
    expected_trace = str(trace_id or active_trace or checkpoint_trace or durable_trace)
    if not expected_trace:
        return {
            "status": "idle",
            "session_id": session_id,
            "trace_id": None,
            "message": "No active task to cancel",
        }
    if trace_id and active_trace and active_trace != trace_id:
        raise HTTPException(status_code=409, detail="The requested trace is no longer active.")
    if trace_id and not active_trace and trace_id not in {checkpoint_trace, durable_trace}:
        raise HTTPException(status_code=409, detail="The requested trace is no longer active.")
    if not active and checkpoint_trace != expected_trace and durable_trace == expected_trace:
        durable_status = durable.get("status")
        if durable_status == "cancelled":
            if await _durable_cancellation_confirmed(
                session_id=session_id,
                user_id=user_id,
                trace_id=expected_trace,
            ):
                return {
                    "status": "cancelled",
                    "session_id": session_id,
                    "trace_id": expected_trace,
                    "message": "Task is cancelled",
                }
        if durable_status in {"completed", "failed"}:
            if trace_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"Task is already {durable_status}; it cannot be cancelled.",
                )
            return {
                "status": "idle",
                "session_id": session_id,
                "trace_id": expected_trace,
                "message": "No active task to cancel",
            }
    if checkpoint_trace == expected_trace:
        _require_checkpoint_identity(
            values,
            session_id=session_id,
            user_id=user_id,
            trace_id=expected_trace,
        )
        current_status = values.get("task_status")
        if current_status in {TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value}:
            raise HTTPException(
                status_code=409,
                detail=f"Task is already {current_status}; it cannot be cancelled.",
            )

    cancellation = await request_trace_cancellation(
        user_id,
        session_id,
        expected_trace,
        reason=reason,
        ttl_seconds=settings.ACTIVE_TRACE_LEASE_SECONDS,
    )
    if cancellation.get("status") == "stale":
        raise HTTPException(status_code=409, detail="The requested trace is no longer active.")
    # The runner can atomically move starting -> running between the initial
    # lookup and request_trace_cancellation().  Re-read after that Lua race so
    # only the actual owner ever acknowledges a running runner as quiescent.
    active = await get_active_trace_lease(user_id, session_id)
    if active and active.get("trace_id") != expected_trace:
        raise HTTPException(status_code=409, detail="The requested trace is no longer active.")
    cancel_event = _cancel_events.get(expected_trace)
    if cancel_event:
        cancel_event.set()

    from enterprise_agent.core.agent.tools.background import clear_background_manager

    clear_background_manager(session_id, expected_trace)

    # A running owner must close its graph iterator and persist cancellation
    # itself.  The API waits briefly, but never guesses success while it runs.
    if active and active.get("runner_state") == "running":
        released = await _wait_for_cancelled_lease_release(
            user_id=user_id,
            session_id=session_id,
            trace_id=expected_trace,
            timeout=settings.CANCEL_CONVERGENCE_WAIT_SECONDS,
        )
        if released and await _durable_cancellation_confirmed(
            session_id=session_id,
            user_id=user_id,
            trace_id=expected_trace,
        ):
            return {
                "status": "cancelled",
                "session_id": session_id,
                "trace_id": expected_trace,
                "message": "Task is cancelled",
            }
        latest = await graph.aget_state(config)
        latest_values = latest.values if latest and latest.values else {}
        if latest_values.get("trace_id") == expected_trace and latest_values.get(
            "task_status"
        ) in {TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value}:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Task became {latest_values.get('task_status')} before Stop converged."
                ),
            )
        return {
            "status": "cancelling",
            "session_id": session_id,
            "trace_id": expected_trace,
            "message": "Cancellation is still converging; retry or check status.",
        }

    # No runner is active (pre-start, waiting confirmation, disconnected, or
    # legacy checkpoint), so it is safe to terminalize without Command(resume).
    latest = await graph.aget_state(config)
    latest_values = latest.values if latest and latest.values else {}
    latest_trace = str(latest_values.get("trace_id") or "")
    if latest_trace == expected_trace and latest_values.get("task_status") in {
        TaskStatus.SUCCEEDED.value,
        TaskStatus.FAILED.value,
    }:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Task became {latest_values.get('task_status')} before Stop converged."
            ),
        )

    checkpoint_cancelled = latest_trace != expected_trace
    cancellation_values = latest_values if latest_trace == expected_trace else {}
    if latest_trace == expected_trace:
        checkpoint_cancelled = await _safe_mark_task_terminal(
            graph,
            config,
            TaskStatus.CANCELLED,
            reason,
            expected_trace_id=expected_trace,
        )
        if not checkpoint_cancelled:
            raced = await graph.aget_state(config)
            raced_values = raced.values if raced and raced.values else {}
            if raced_values.get("trace_id") == expected_trace and raced_values.get(
                "task_status"
            ) in {TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value}:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Task became {raced_values.get('task_status')} before Stop converged."
                    ),
                )

    durable_cancelled = False
    if checkpoint_cancelled:
        await _safe_mark_durable_assistant_cancelled(
            session_id=session_id,
            user_id=user_id,
            trace_id=expected_trace,
            values=cancellation_values,
            reason=reason,
        )
        durable_cancelled = await _durable_cancellation_confirmed(
            session_id=session_id,
            user_id=user_id,
            trace_id=expected_trace,
        )
    release_result = "missing"
    if active and checkpoint_cancelled and durable_cancelled:
        release_result = await _mark_runner_stopped_and_release(
            user_id=user_id,
            session_id=session_id,
            trace_id=expected_trace,
            lease_token=active["lease_token"],
            runner_token=active["runner_token"],
            reason="cancelled",
            release=True,
        )
    if checkpoint_cancelled and durable_cancelled and release_result in {"released", "missing"}:
        if _active_stream_traces.get(session_id) == expected_trace:
            _active_stream_traces.pop(session_id, None)
        try:
            get_trace_store().finish_trace(
                user_id=user_id,
                trace_id=expected_trace,
                status=TaskStatus.CANCELLED.value,
                error=reason,
            )
        except (FileNotFoundError, ValueError):
            pass
        return {
            "status": "cancelled",
            "session_id": session_id,
            "trace_id": expected_trace,
            "message": "Task is cancelled",
        }
    return {
        "status": "cancelling",
        "session_id": session_id,
        "trace_id": expected_trace,
        "message": "Cancellation is not terminal yet; retry or check status.",
    }


@router.post("/stream/cancel")
async def cancel_stream(
    session_id: str,
    trace_id: str | None = None,
    user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel an owned in-progress SSE stream."""
    await _require_owned_session(session_id, user_id, db)
    return await request_task_cancellation(session_id, user_id, trace_id=trace_id)


# Session management routes
sessions_router = APIRouter(prefix="/sessions", tags=["sessions"])


@sessions_router.get("/", response_model=list[SessionResponse])
async def list_sessions(
    user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List user sessions

    Args:
        user_id: Current user ID
        db: Database session

    Returns:
        List of sessions
    """
    result = await db.execute(
        select(Session).where(
            Session.user_id == user_id,
            Session.status != SessionStatus.DELETED
        ).order_by(Session.updated_at.desc(), Session.created_at.desc())
    )
    sessions = result.scalars().all()

    graph = get_agent_graph()
    durable_counts = await message_counts_by_session(db, user_id=user_id)
    responses = []
    for s in sessions:
        durable_count = durable_counts.get(s.id, 0)
        checkpoint_count = 0
        if durable_count == 0:
            try:
                checkpoint_count = len(await _load_history_messages(s.id, graph))
            except Exception:
                logging.warning("Failed to inspect checkpoint history for session %s", s.id, exc_info=True)
        responses.append(SessionResponse(
            id=s.id,
            user_id=s.user_id,
            title=s.title,
            status=s.status.value,
            created_at=s.created_at,
            message_count=durable_count or checkpoint_count,
            history_status=_history_status(
                s,
                durable_count=durable_count,
                checkpoint_count=checkpoint_count,
            ),
        ))
    return responses


@sessions_router.post("/", response_model=SessionResponse)
async def create_session(
    data: SessionCreate,
    user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create new session

    Args:
        data: Session creation data
        user_id: Current user ID
        db: Database session

    Returns:
        New session
    """
    session = Session(
        id=str(uuid.uuid4()),
        user_id=user_id,
        title=data.title,
        status=SessionStatus.ACTIVE
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    return SessionResponse(
        id=session.id,
        user_id=session.user_id,
        title=session.title,
        status=session.status.value,
        created_at=session.created_at,
    )


@sessions_router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete session (soft delete)

    Args:
        session_id: Session ID
        user_id: Current user ID
        db: Database session

    Returns:
        Success message

    Raises:
        HTTPException: If session not found
    """
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user_id)
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.status = SessionStatus.DELETED
    await db.commit()

    return {"message": "Session deleted"}


@sessions_router.get("/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get durable chat history for a session.

    MySQL is authoritative. Legacy sessions fall back to the short-lived
    LangGraph Redis checkpoint until they are migrated by their next turn.

    Args:
        session_id: Session/thread ID
        user_id: Current user ID
        db: Database session

    Returns:
        Session with messages list

    Raises:
        HTTPException: If session not found
    """
    # Verify session ownership
    session = await _require_owned_session(session_id, user_id, db)
    durable_records = await list_durable_messages(db, session_id=session_id, user_id=user_id)
    messages = [
        serialize_message(message)
        for message in durable_records
        if message.content or getattr(message, "timeline", None)
    ]
    checkpoint_count = 0
    if not durable_records:
        messages = await _load_history_messages(session_id)
        checkpoint_count = len(messages)

    logging.debug(f"[history] returning {len(messages)} messages")
    return {
        "session_id": session_id,
        "title": session.title,
        "message_count": len(messages),
        "history_status": _history_status(
            session,
            durable_count=len(durable_records),
            checkpoint_count=checkpoint_count,
        ),
        "messages": messages,
    }


# === Human-in-the-loop Tool Confirmation ===

@router.post("/confirm")
async def confirm_tool(
    session_id: str,
    approved: bool,
    trace_id: str,
    approved_ids: list[str] = None,
    user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Handle tool confirmation response from frontend.

    Resumes execution after user approves/rejects sensitive tool(s).

    Args:
        session_id: Session/thread ID
        approved: Whether user approved the tool(s)
        approved_ids: List of approved tool call IDs (optional, for partial approval)

    Returns:
        Status indicating execution resumed
    """
    await _require_owned_session(session_id, user_id, db)
    set_current_user_id(user_id)

    config = {"configurable": {"thread_id": session_id}}
    graph = get_agent_graph()

    snapshot = await graph.aget_state(config)
    values = snapshot.values if snapshot and snapshot.values else {}
    checkpoint_trace_id = values.get("trace_id")
    if not checkpoint_trace_id:
        raise HTTPException(status_code=409, detail="The confirmation checkpoint has expired.")
    if trace_id != checkpoint_trace_id:
        raise HTTPException(status_code=409, detail="The requested trace is no longer active.")
    trace_id = str(checkpoint_trace_id)
    _require_checkpoint_identity(
        values,
        session_id=session_id,
        user_id=user_id,
        trace_id=trace_id,
    )
    _require_interrupt_type(snapshot, "tool_confirmation")
    deadline_raw = values.get("confirmation_deadline")
    expired = False
    if deadline_raw:
        try:
            expired = datetime.fromisoformat(deadline_raw) <= datetime.now(timezone.utc)
        except (TypeError, ValueError):
            logging.warning("Invalid confirmation deadline for session %s", session_id)

    resume_payload = {
        "approved": approved and not expired,
        "approved_ids": approved_ids or [],
    }
    if expired:
        resume_payload["reason"] = "confirmation_timeout"

    resume_lock_token = await acquire_task_resume_lock(user_id, session_id, trace_id)
    if not resume_lock_token:
        raise HTTPException(status_code=409, detail="This confirmation is already being resumed.")
    _cancel_confirmation_timeout(session_id)
    lease = None
    control_token = None
    runner_context_token = None
    terminal_persisted = False
    assistant_status = "failed"
    try:
        lease = await _reserve_existing_trace_runner(
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
        )
        if not await start_active_trace_runner(
            user_id,
            session_id,
            trace_id,
            lease["lease_token"],
            lease["runner_token"],
            ttl_seconds=settings.ACTIVE_TRACE_LEASE_SECONDS,
        ):
            raise HTTPException(status_code=409, detail="This task is being cancelled.")
        control_token = set_current_task_control_identity(user_id, session_id, trace_id)
        runner_context_token = set_current_task_runner_identity(
            user_id,
            session_id,
            trace_id,
            lease["lease_token"],
            lease["runner_token"],
        )
        result = await graph.ainvoke(Command(resume=resume_payload), config)
        assistant_status, terminal_reason = _task_terminal_outcome(
            result,
            session_id=session_id,
            user_id=user_id,
            trace_id=trace_id,
        )
        if assistant_status == "failed" and result.get("task_status") not in {
            TaskStatus.FAILED.value,
            TaskStatus.SUCCEEDED.value,
            TaskStatus.CANCELLED.value,
        }:
            await _safe_mark_task_terminal(
                graph,
                config,
                TaskStatus.FAILED,
                terminal_reason,
                expected_trace_id=trace_id,
            )
        if assistant_status == "cancelled":
            await _safe_mark_durable_assistant_cancelled(
                session_id=session_id,
                user_id=user_id,
                trace_id=trace_id,
                values=result,
                reason=terminal_reason or "Cancelled by user",
            )
            terminal_persisted = await _durable_cancellation_confirmed(
                session_id=session_id,
                user_id=user_id,
                trace_id=trace_id,
            )
        else:
            message_id = await find_assistant_message_id(
                db,
                session_id=session_id,
                user_id=user_id,
                trace_id=trace_id,
            )
            if message_id is None:
                message_id = await create_assistant_message(
                    db,
                    session_id=session_id,
                    user_id=user_id,
                    trace_id=trace_id,
                    status="interrupted",
                )
            terminal_persisted = await update_assistant_message(
                db,
                message_id=message_id,
                user_id=user_id,
                content=_final_response_content(result),
                status=assistant_status,
                append=True,
            )
    finally:
        if runner_context_token is not None:
            reset_current_task_runner_identity(runner_context_token)
        if control_token is not None:
            reset_current_task_control_identity(control_token)
        if lease is not None:
            await _mark_runner_stopped_and_release(
                user_id=user_id,
                session_id=session_id,
                trace_id=trace_id,
                lease_token=lease["lease_token"],
                runner_token=lease["runner_token"],
                reason=assistant_status,
                release=terminal_persisted
                and assistant_status in {"completed", "failed", "cancelled"},
            )
        await release_task_resume_lock(
            user_id,
            session_id,
            trace_id,
            resume_lock_token,
        )

    logging.info(f"[confirm] Session {session_id}: approved={approved}, approved_ids={approved_ids}")

    return {
        "status": "expired" if expired else "resumed",
        "session_id": session_id,
        "trace_id": trace_id,
        "approved": approved and not expired,
    }


@router.get("/pending_confirm/{session_id}")
async def get_pending_confirmation(
    session_id: str,
    user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get pending tool confirmation request for a session.

    Returns the current interrupt state if a tool confirmation is pending.

    Args:
        session_id: Session/thread ID
        user_id: Current user ID

    Returns:
        Pending confirmation details or empty if none pending
    """
    await _require_owned_session(session_id, user_id, db)
    config = {"configurable": {"thread_id": session_id}}
    graph = get_agent_graph()

    # Get current state to check for pending interrupts
    state = await graph.aget_state(config)

    # Check if there's a pending interrupt for tool confirmation
    tasks = state.tasks
    pending_confirm = None

    for task in tasks:
        if task.interrupts:
            for interrupt_data in task.interrupts:
                value = interrupt_data.value if hasattr(interrupt_data, "value") else interrupt_data
                if isinstance(value, dict) and value.get("type") == "tool_confirmation":
                    pending_confirm = value
                    break

    if pending_confirm:
        return {
            "status": "pending",
            "session_id": session_id,
            "confirmation": pending_confirm
        }
    else:
        return {
            "status": "no_pending",
            "session_id": session_id
        }
