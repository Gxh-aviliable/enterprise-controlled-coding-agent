import asyncio
import json
import logging
import re
import uuid
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
    create_assistant_message,
    find_assistant_message_id,
    mark_assistant_message_cancelled,
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
from enterprise_agent.core.execution.pause_control import (
    acquire_task_resume_lock,
    clear_task_pause_request,
    release_task_resume_lock,
    request_task_pause,
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
    if interrupt_type == "user_pause":
        return {
            "event": "paused",
            "session_id": session_id,
            "trace_id": trace_id,
            "status": TaskStatus.PAUSED.value,
            "data": payload,
        }, "paused"
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

            set_current_user_id(user_id)
            await graph.ainvoke(
                Command(resume={
                    "approved": False,
                    "approved_ids": [],
                    "reason": "confirmation_timeout",
                }),
                config,
            )
            checkpoint_trace_id = values.get("trace_id")
            if checkpoint_trace_id:
                async with async_session_factory() as history_db:
                    message_id = await find_assistant_message_id(
                        history_db,
                        session_id=session_id,
                        user_id=user_id,
                        trace_id=checkpoint_trace_id,
                    )
                    if message_id is not None:
                        await update_assistant_message(
                            history_db,
                            message_id=message_id,
                            user_id=user_id,
                            content="\n\n*[Tool confirmation timed out and was rejected.]*",
                            status="failed",
                            append=True,
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
) -> None:
    """Persist one completed SSE segment with an independent DB session."""
    try:
        async with async_session_factory() as history_db:
            await update_assistant_message(
                history_db,
                message_id=message_id,
                user_id=user_id,
                content=content,
                status=status,
                append=True,
            )
    except Exception:
        logging.exception("Failed to persist assistant stream segment message_id=%s", message_id)


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
        "pause_requested_at": None,
        "paused_at": None,
        "pause_reason": None,
        "pause_resume_target": None,
        "messages": [{"role": "user", "content": content}],
    }


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
) -> None:
    """Best-effort MySQL convergence for every cancellation entry point."""
    try:
        async with async_session_factory() as history_db:
            updated = await mark_assistant_message_cancelled(
                history_db,
                session_id=session_id,
                user_id=user_id,
                trace_id=trace_id,
                tombstone=CANCELLATION_TOMBSTONE,
            )
            if not updated:
                logging.warning(
                    "No durable assistant message found for cancelled trace %s",
                    trace_id,
                )
    except Exception:
        logging.warning("Failed to persist cancelled assistant message", exc_info=True)


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
            if current == target.value:
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
        if target == TaskStatus.CANCELLED:
            trace_id = str(values.get("trace_id") or expected_trace_id or "")
            if trace_id:
                terminal_update["messages"] = _cancellation_checkpoint_messages(
                    values,
                    trace_id,
                )
        await graph.aupdate_state(config, terminal_update)
        from enterprise_agent.core.agent.nodes import terminalize_open_work_items
        terminal_todos = terminalize_open_work_items(values, status)
        trace_id = values.get("trace_id")
        user_id = values.get("user_id")
        await graph.aupdate_state(
            config,
            {
                "todos": terminal_todos,
                "has_open_todos": False,
            },
        )
        if trace_id and user_id is not None:
            try:
                await clear_task_pause_request(
                    int(user_id),
                    str(values.get("session_id") or config["configurable"]["thread_id"]),
                    str(trace_id),
                )
            except Exception:
                logging.warning("Failed to clear terminal pause request", exc_info=True)
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


async def _ensure_session_accepts_new_task(
    graph,
    *,
    session_id: str,
    user_id: int,
) -> None:
    """Prevent a new trace from overwriting a resumable checkpoint."""
    active_trace = _active_stream_traces.get(session_id)
    if active_trace:
        raise HTTPException(
            status_code=409,
            detail=f"Task {active_trace} is still active for this session.",
        )

    snapshot = await graph.aget_state({"configurable": {"thread_id": session_id}})
    values = snapshot.values if snapshot and snapshot.values else {}
    if values and values.get("user_id") not in {None, user_id}:
        raise HTTPException(status_code=409, detail="Checkpoint owner mismatch.")
    if values.get("task_status") in {
        TaskStatus.PENDING.value,
        TaskStatus.RUNNING.value,
        TaskStatus.PAUSED.value,
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
    """Non-streaming chat completion

    Args:
        request: Chat request
        user_id: Current user ID from JWT

    Returns:
        Chat response
    """
    _validate_request_mode(request.mode, request.content, permissions)
    if request.session_id:
        await _require_owned_session(request.session_id, user_id, db)
    quota_lease = await acquire_task_quota(user_id, db)
    assistant_message_id = None
    session_id = None
    trace_id = None
    assistant_status = "failed"
    terminal_reason = None
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

        # Set user context for workspace isolation
        set_current_user_id(user_id)

        # Execute agent graph with thread_id for state persistence
        config = {"configurable": {"thread_id": session_id}}
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
                    ),
                    config=config,
                ),
                timeout=settings.AGENT_INVOKE_TIMEOUT_SECONDS
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
            raise HTTPException(status_code=504, detail="Request timed out")
        except Exception:
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
            raise
    finally:
        if session_id and _active_stream_traces.get(session_id) == trace_id:
            _active_stream_traces.pop(session_id, None)
        if session_id and trace_id:
            try:
                await clear_task_pause_request(user_id, session_id, trace_id)
            except Exception:
                logging.warning("Failed to clear terminal pause request", exc_info=True)
        await quota_lease.release()

    # Get last message (guard against empty messages)
    messages = result.get("messages", [])
    if not messages:
        await update_assistant_message(
            db,
            message_id=assistant_message_id,
            user_id=user_id,
            content="",
            status="failed",
        )
        raise HTTPException(status_code=500, detail="Agent returned no response")
    last_msg = messages[-1]

    # Extract content - handle both string and content block formats
    if hasattr(last_msg, "content"):
        raw_content = last_msg.content

        # Debug logging
        logging.debug(f"Content type: {type(raw_content)}, content: {raw_content}")

        # If content is a list of blocks (Anthropic format), extract text
        if isinstance(raw_content, list):
            text_parts = []
            for block in raw_content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                elif hasattr(block, "text"):
                    text_parts.append(block.text)
            content = "\n".join(text_parts) if text_parts else "(thinking only — no text response)"
        elif isinstance(raw_content, str):
            # Try to parse if it looks like a list representation
            if raw_content.startswith("[") and raw_content.endswith("]"):
                try:
                    import ast
                    parsed = ast.literal_eval(raw_content)
                    if isinstance(parsed, list):
                        text_parts = []
                        for block in parsed:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                        content = "\n".join(text_parts) if text_parts else "(thinking only — no text response)"
                    else:
                        content = raw_content
                except Exception:
                    content = raw_content
            else:
                content = raw_content
        else:
            content = str(raw_content)
    else:
        content = str(last_msg)

    if assistant_status == "failed" and terminal_reason:
        failure_suffix = f"\n\n❌ **Task failed:** {terminal_reason}"
        if failure_suffix not in content:
            content += failure_suffix
    elif assistant_status == "cancelled" and CANCELLATION_TOMBSTONE not in content:
        content += f"\n\n{CANCELLATION_TOMBSTONE}"

    await update_assistant_message(
        db,
        message_id=assistant_message_id,
        user_id=user_id,
        content=content,
        status=assistant_status,
        append=False,
    )

    return ChatResponse(
        session_id=session_id,
        trace_id=trace_id,
        message_id=assistant_message_id,
        role="assistant",
        content=content,
        created_at=datetime.now(timezone.utc)
    )


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    user_id: int = Depends(get_current_user),
    permissions: list = Depends(get_current_user_permissions),
    db: AsyncSession = Depends(get_db),
):
    """Streaming chat completion (SSE) with interrupt support.

    Uses astream(stream_mode="updates") to detect interrupts from tool_confirm_node.

    Args:
        request: Chat request
        user_id: Current user ID from JWT

    Returns:
        StreamingResponse with SSE events (delta, tool_start, tool_end, interrupt)
    """
    _validate_request_mode(request.mode, request.content, permissions)
    if request.session_id:
        await _require_owned_session(request.session_id, user_id, db)
    quota_lease = await acquire_task_quota(user_id, db)
    session_id = None
    trace_id = None
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
        set_current_user_id(user_id)

        config = {"configurable": {"thread_id": session_id}}
    except Exception:
        if session_id and _active_stream_traces.get(session_id) == trace_id:
            _active_stream_traces.pop(session_id, None)
        await quota_lease.release()
        raise

    async def generate():
        stream_filter = InternalStreamFilter()
        assistant_parts: list[str] = []
        assistant_status = "interrupted"
        assistant_suffix = ""

        # Register cancellation by trace so an old stream cannot stop a newer task.
        cancel_event = asyncio.Event()
        _cancel_events[trace_id] = cancel_event

        try:
            yield _sse_event({
                "event": "task_started",
                "session_id": session_id,
                "trace_id": trace_id,
                "status": TaskStatus.PENDING.value,
            })
            # Dual stream modes:
            #   "messages" → token-level deltas from LLM (true streaming)
            #   "updates" → node-level state updates (interrupts, tool results)
            async for stream_event in graph.astream(
                _task_input(
                    session_id=session_id,
                    trace_id=trace_id,
                    user_id=user_id,
                    permissions=permissions,
                    content=request.content,
                    mode=request.mode,
                ),
                config=config,
                stream_mode=["messages", "updates"]
            ):
                # Check for user-requested cancellation
                if cancel_event.is_set():
                    logging.info(f"[stream] Session {session_id} cancelled by user")
                    yield _sse_event({"event": "cancelled", "message": "Generation stopped by user"})
                    return

                # With dual modes, LangGraph yields (mode, data) tuples
                mode, data = stream_event

                # ── Token-level streaming from LLM ──
                if mode == "messages":
                    # data is (AIMessageChunk, metadata) tuple
                    msg_chunk, _ = data
                    if hasattr(msg_chunk, "content") and msg_chunk.content:
                        delta = _extract_delta(msg_chunk.content)
                        # Filter out internal evaluation JSON (importance, patterns)
                        # that leak from memory evaluator LLM calls inside the graph
                        if delta and not stream_filter.is_internal_json(delta):
                            assistant_parts.append(delta)
                            yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
                    # Check for tool calls in the chunk
                    if hasattr(msg_chunk, "tool_calls") and msg_chunk.tool_calls:
                        for tc in msg_chunk.tool_calls:
                            if tc.get("name"):
                                yield _sse_event({
                                    "event": "tool_start",
                                    "id": tc.get("id", ""),
                                    "name": tc["name"],
                                })

                # ── Node-level updates for interrupts & tool results ──
                elif mode == "updates":
                    # Typed interrupts keep HITL confirmation and user pause separate.
                    if "__interrupt__" in data:
                        interrupt_obj = data["__interrupt__"]
                        logging.info(f"[stream] Interrupt detected: {type(interrupt_obj)}")
                        event, assistant_status = _stream_interrupt_event(
                            interrupt_obj=interrupt_obj,
                            session_id=session_id,
                            trace_id=trace_id,
                            user_id=user_id,
                        )
                        yield _sse_event(event)
                        return

                    # Process node outputs
                    for node_name, node_output in data.items():
                        if node_name == "__interrupt__":
                            continue

                        # Tool executor output
                        if node_name == "tool_executor":
                            for event in _tool_sse_events(node_output):
                                yield _sse_event(event)

            assistant_status, terminal_reason = await _read_stream_terminal_outcome(
                graph,
                config,
                session_id=session_id,
                user_id=user_id,
                trace_id=trace_id,
            )
            terminal_event = _terminal_stream_event(
                assistant_status=assistant_status,
                reason=terminal_reason,
                session_id=session_id,
                trace_id=trace_id,
            )
            if terminal_event is None:
                yield "data: [DONE]\n\n"
            else:
                if assistant_status == "failed":
                    assistant_suffix = f"\n\n❌ **Task failed:** {terminal_reason}"
                yield _sse_event(terminal_event)
        except GeneratorExit:
            logging.debug("[stream] Generator closed (normal for interrupt/client disconnect)")
            assistant_status = "interrupted"
            return
        except Exception as e:
            logging.exception("Stream error: %s", e)
            await _safe_mark_task_terminal(
                graph,
                config,
                TaskStatus.FAILED,
                str(e)[:500],
                expected_trace_id=trace_id,
            )
            assistant_status = "failed"
            assistant_suffix = f"\n\n❌ **Error:** {str(e)[:500]}"
            yield _sse_event(_terminal_stream_event(
                assistant_status=assistant_status,
                reason=str(e)[:500],
                session_id=session_id,
                trace_id=trace_id,
            ))
        finally:
            if cancel_event.is_set():
                assistant_status = await _converge_stream_cancellation(
                    graph,
                    config,
                    trace_id,
                    assistant_status,
                )
            await _persist_stream_segment(
                message_id=assistant_message_id,
                user_id=user_id,
                content="".join(assistant_parts) + assistant_suffix,
                status=assistant_status,
            )
            # Clean up request-local cancellation state.
            _cancel_events.pop(trace_id, None)
            if _active_stream_traces.get(session_id) == trace_id:
                _active_stream_traces.pop(session_id, None)
            if assistant_status in {"completed", "failed", "cancelled"}:
                try:
                    await clear_task_pause_request(user_id, session_id, trace_id)
                except Exception:
                    logging.warning("Failed to clear terminal pause request", exc_info=True)
            await quota_lease.release()

    return StreamingResponse(generate(), media_type="text/event-stream")


def _stream_resumed_command(
    *,
    graph,
    config: dict,
    command: Command,
    session_id: str,
    trace_id: str,
    user_id: int,
    assistant_message_id: int,
    log_context: str,
    release_resume_guard: bool = False,
) -> StreamingResponse:
    """Stream one exact checkpoint resume for HITL or user pause."""

    async def generate():
        stream_filter = InternalStreamFilter()
        assistant_parts: list[str] = []
        assistant_status = "interrupted"
        assistant_suffix = ""
        cancel_event = asyncio.Event()
        _cancel_events[trace_id] = cancel_event
        _active_stream_traces[session_id] = trace_id

        try:
            logging.info("[%s] Resuming session=%s trace=%s", log_context, session_id, trace_id)
            async for stream_event in graph.astream(
                command,
                config=config,
                stream_mode=["messages", "updates"],
            ):
                if cancel_event.is_set():
                    yield _sse_event({
                        "event": "cancelled",
                        "session_id": session_id,
                        "trace_id": trace_id,
                        "message": "Generation stopped by user",
                    })
                    return

                mode, data = stream_event
                if mode == "messages":
                    msg_chunk, _ = data
                    if hasattr(msg_chunk, "content") and msg_chunk.content:
                        delta = _extract_delta(msg_chunk.content)
                        if delta and not stream_filter.is_internal_json(delta):
                            assistant_parts.append(delta)
                            yield _sse_event({"delta": delta})
                    if hasattr(msg_chunk, "tool_calls") and msg_chunk.tool_calls:
                        for tool_call in msg_chunk.tool_calls:
                            if tool_call.get("name"):
                                yield _sse_event({
                                    "event": "tool_start",
                                    "id": tool_call.get("id", ""),
                                    "name": tool_call["name"],
                                })
                elif mode == "updates":
                    if "__interrupt__" in data:
                        event, assistant_status = _stream_interrupt_event(
                            interrupt_obj=data["__interrupt__"],
                            session_id=session_id,
                            trace_id=trace_id,
                            user_id=user_id,
                        )
                        yield _sse_event(event)
                        return
                    for node_name, node_output in data.items():
                        if node_name == "tool_executor":
                            for event in _tool_sse_events(node_output):
                                yield _sse_event(event)

            assistant_status, terminal_reason = await _read_stream_terminal_outcome(
                graph,
                config,
                session_id=session_id,
                user_id=user_id,
                trace_id=trace_id,
            )
            terminal_event = _terminal_stream_event(
                assistant_status=assistant_status,
                reason=terminal_reason,
                session_id=session_id,
                trace_id=trace_id,
            )
            if terminal_event is None:
                yield "data: [DONE]\n\n"
            else:
                if assistant_status == "failed":
                    assistant_suffix = f"\n\n❌ **Task failed:** {terminal_reason}"
                yield _sse_event(terminal_event)
        except GeneratorExit:
            logging.debug("[%s] Generator closed", log_context)
            assistant_status = "interrupted"
            return
        except Exception as exc:
            logging.exception("%s failed", log_context)
            await _safe_mark_task_terminal(
                graph,
                config,
                TaskStatus.FAILED,
                str(exc)[:500],
                expected_trace_id=trace_id,
            )
            assistant_status = "failed"
            assistant_suffix = f"\n\n❌ **Error:** {str(exc)[:500]}"
            yield _sse_event(_terminal_stream_event(
                assistant_status=assistant_status,
                reason=str(exc)[:500],
                session_id=session_id,
                trace_id=trace_id,
            ))
        finally:
            if cancel_event.is_set():
                assistant_status = await _converge_stream_cancellation(
                    graph,
                    config,
                    trace_id,
                    assistant_status,
                )
            await _persist_stream_segment(
                message_id=assistant_message_id,
                user_id=user_id,
                content="".join(assistant_parts) + assistant_suffix,
                status=assistant_status,
            )
            _cancel_events.pop(trace_id, None)
            if _active_stream_traces.get(session_id) == trace_id:
                _active_stream_traces.pop(session_id, None)
            if assistant_status in {"completed", "failed", "cancelled"}:
                try:
                    await clear_task_pause_request(user_id, session_id, trace_id)
                except Exception:
                    logging.warning("Failed to clear terminal pause request", exc_info=True)
            if release_resume_guard:
                await release_task_resume_lock(user_id, session_id, trace_id)

    return StreamingResponse(generate(), media_type="text/event-stream")


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
    _cancel_confirmation_timeout(session_id)
    set_current_user_id(user_id)

    approved_ids = body.approved_ids if body else []

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

    if not await acquire_task_resume_lock(user_id, session_id, trace_id):
        raise HTTPException(status_code=409, detail="This confirmation is already being resumed.")

    return _stream_resumed_command(
        graph=graph,
        config=config,
        command=Command(resume=resume_payload),
        session_id=session_id,
        trace_id=trace_id,
        user_id=user_id,
        assistant_message_id=assistant_message_id,
        log_context="stream/resume-confirmation",
        release_resume_guard=True,
    )


@router.post("/stream/pause")
async def request_task_pause_endpoint(
    session_id: str,
    trace_id: str,
    reason: str | None = None,
    user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Request cooperative pause at the next LangGraph safety boundary."""
    await _require_owned_session(session_id, user_id, db)
    graph = get_agent_graph()
    config = {"configurable": {"thread_id": session_id}}
    snapshot = await graph.aget_state(config)
    values = snapshot.values if snapshot and snapshot.values else {}

    # The task_started SSE event is emitted just before the first graph
    # checkpoint. During that very small window, the trace store and exact
    # in-process stream mapping still establish ownership safely.
    checkpoint_matches = values.get("trace_id") == trace_id
    if checkpoint_matches:
        _require_checkpoint_identity(
            values,
            session_id=session_id,
            user_id=user_id,
            trace_id=trace_id,
        )
        status = values.get("task_status")
    else:
        try:
            trace = get_trace_store().get_trace(user_id, trace_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail="The task trace is not active.") from exc
        if trace.get("session_id") != session_id or trace.get("user_id") != user_id:
            raise HTTPException(status_code=409, detail="Task trace does not match this session.")
        if _active_stream_traces.get(session_id) != trace_id:
            raise HTTPException(status_code=409, detail="The task trace is no longer active.")
        status = trace.get("status")

    if status == TaskStatus.PAUSED.value:
        return {"status": "paused", "session_id": session_id, "trace_id": trace_id}
    if status not in {TaskStatus.PENDING.value, TaskStatus.RUNNING.value, "pause_requested"}:
        raise HTTPException(
            status_code=409,
            detail=f"A task in status {status!r} cannot be paused.",
        )

    pause_request = await request_task_pause(
        user_id,
        session_id,
        trace_id,
        reason=reason or "Paused by user",
    )
    try:
        get_trace_store().record_event(
            user_id=user_id,
            trace_id=trace_id,
            event_type="control",
            name="pause_requested",
            status="requested",
            data={
                "session_id": session_id,
                "task_status": status,
                "requested_at": (
                    pause_request.get("requested_at")
                    if isinstance(pause_request, dict)
                    else None
                ),
                "reason": reason or "Paused by user",
            },
        )
    except Exception:
        logging.warning("Failed to record pause request trace", exc_info=True)
    return {
        "status": "pause_requested",
        "session_id": session_id,
        "trace_id": trace_id,
    }


@router.get("/stream/status")
async def get_stream_status(
    session_id: str,
    user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the authoritative checkpoint lifecycle and typed interrupt."""
    await _require_owned_session(session_id, user_id, db)
    graph = get_agent_graph()
    snapshot = await graph.aget_state({"configurable": {"thread_id": session_id}})
    values = snapshot.values if snapshot and snapshot.values else {}
    if not values:
        return {
            "status": "idle",
            "session_id": session_id,
            "trace_id": None,
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
        "interrupt_type": interrupt_payload.get("type") if interrupt_payload else None,
        "interrupt": interrupt_payload,
    }


@router.post("/stream/continue")
async def continue_paused_stream(
    session_id: str,
    trace_id: str,
    user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Continue one exact user-paused checkpoint as a new SSE stream."""
    await _require_owned_session(session_id, user_id, db)
    set_current_user_id(user_id)
    graph = get_agent_graph()
    config = {"configurable": {"thread_id": session_id}}
    snapshot = await graph.aget_state(config)
    values = snapshot.values if snapshot and snapshot.values else {}
    _require_checkpoint_identity(
        values,
        session_id=session_id,
        user_id=user_id,
        trace_id=trace_id,
    )
    if values.get("task_status") != TaskStatus.PAUSED.value:
        raise HTTPException(
            status_code=409,
            detail=f"Only a paused task can continue; current status is {values.get('task_status')!r}.",
        )
    _require_interrupt_type(snapshot, "user_pause")
    if not await acquire_task_resume_lock(user_id, session_id, trace_id):
        raise HTTPException(status_code=409, detail="This paused task is already being resumed.")

    try:
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
                status="paused",
            )
        else:
            await update_assistant_message(
                db,
                message_id=assistant_message_id,
                user_id=user_id,
                content="",
                status="streaming",
            )
        get_trace_store().record_event(
            user_id=user_id,
            trace_id=trace_id,
            event_type="control",
            name="resume_requested",
            status="requested",
            data={"session_id": session_id},
        )
    except Exception:
        await release_task_resume_lock(user_id, session_id, trace_id)
        raise

    return _stream_resumed_command(
        graph=graph,
        config=config,
        command=Command(resume={"action": "continue", "trace_id": trace_id}),
        session_id=session_id,
        trace_id=trace_id,
        user_id=user_id,
        assistant_message_id=assistant_message_id,
        log_context="stream/continue-pause",
        release_resume_guard=True,
    )


async def request_task_cancellation(
    session_id: str,
    user_id: int,
    reason: str = "Cancelled by user",
    trace_id: str | None = None,
):
    """Cancel an exact task, with distinct cleanup for HITL and user pause."""
    _cancel_confirmation_timeout(session_id)
    set_current_user_id(user_id)
    config = {"configurable": {"thread_id": session_id}}
    graph = get_agent_graph()
    state = await graph.aget_state(config)
    values = state.values if state and state.values else {}
    checkpoint_trace = values.get("trace_id")
    active_trace = _active_stream_traces.get(session_id)
    expected_trace = trace_id or active_trace or checkpoint_trace
    if not expected_trace:
        return {
            "status": "idle",
            "session_id": session_id,
            "trace_id": None,
            "message": "No active task to cancel",
        }
    if (
        trace_id
        and checkpoint_trace
        and checkpoint_trace != trace_id
        and active_trace != trace_id
    ):
        raise HTTPException(status_code=409, detail="The requested trace is no longer active.")
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

    cancel_event = _cancel_events.get(expected_trace)
    if cancel_event:
        cancel_event.set()

    from enterprise_agent.core.agent.tools.background import clear_background_manager

    clear_background_manager(session_id)
    await clear_task_pause_request(user_id, session_id, expected_trace)

    interrupt_payload = _snapshot_interrupt_payload(state)
    if checkpoint_trace == expected_trace and interrupt_payload:
        interrupt_type = interrupt_payload.get("type")
        if interrupt_type == "tool_confirmation":
            resume_payload = {
                "approved": False,
                "approved_ids": [],
                "reason": "task_cancelled",
            }
        elif interrupt_type == "user_pause":
            resume_payload = {
                "action": "cancel",
                "trace_id": expected_trace,
                "reason": "task_cancelled",
            }
        else:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot safely cancel unknown interrupt type {interrupt_type!r}.",
            )

        try:
            await graph.ainvoke(Command(resume=resume_payload), config)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Preserve the existing best-effort API contract, but converge the
            # checkpoint below even if the typed interrupt resume failed.
            logging.warning("Cancellation resume failed", exc_info=True)

    if checkpoint_trace == expected_trace:
        current_status = values.get("task_status")
        if current_status not in {TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value}:
            await _safe_mark_task_terminal(
                graph,
                config,
                TaskStatus.CANCELLED,
                reason,
                expected_trace_id=expected_trace,
            )

        latest_state = await graph.aget_state(config)
        latest_values = latest_state.values if latest_state and latest_state.values else {}
        latest_trace = latest_values.get("trace_id")
        latest_status = latest_values.get("task_status")
        if latest_trace != expected_trace:
            raise HTTPException(
                status_code=409,
                detail="The task checkpoint changed while cancellation was converging.",
            )
        if latest_status in {TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value}:
            raise HTTPException(
                status_code=409,
                detail=f"Task is already {latest_status}; it cannot be cancelled.",
            )
        if latest_status != TaskStatus.CANCELLED.value:
            raise HTTPException(
                status_code=409,
                detail="Task cancellation did not reach a terminal checkpoint.",
            )

    await _safe_mark_durable_assistant_cancelled(
        session_id=session_id,
        user_id=user_id,
        trace_id=expected_trace,
    )

    # A live stream owns this guard until its finally block has converged.  If
    # there is no request-local stream (paused task, other worker, or an already
    # disconnected browser), cancellation can release the stale guard here.
    if cancel_event is None and _active_stream_traces.get(session_id) == expected_trace:
        _active_stream_traces.pop(session_id, None)
    return {
        "status": "cancelled",
        "session_id": session_id,
        "trace_id": expected_trace,
        "message": "Task cancellation requested",
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
        if message.content
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
    _cancel_confirmation_timeout(session_id)
    set_current_user_id(user_id)

    config = {"configurable": {"thread_id": session_id}}
    graph = get_agent_graph()

    snapshot = await graph.aget_state(config)
    values = snapshot.values if snapshot and snapshot.values else {}
    trace_id = values.get("trace_id")
    if not trace_id:
        raise HTTPException(status_code=409, detail="The confirmation checkpoint has expired.")
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

    # Resume execution with user's decision
    # The interrupt() in tool_confirm_node will receive this as user_response
    await graph.ainvoke(
        Command(resume=resume_payload),
        config
    )

    logging.info(f"[confirm] Session {session_id}: approved={approved}, approved_ids={approved_ids}")

    return {
        "status": "expired" if expired else "resumed",
        "session_id": session_id,
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
