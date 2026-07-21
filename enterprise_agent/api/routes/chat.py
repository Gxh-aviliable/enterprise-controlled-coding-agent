import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.api.middleware.auth import get_current_user, get_current_user_permissions
from enterprise_agent.api.schemas.chat import (
    AgentCapabilities,
    ChatRequest,
    ChatResponse,
    ResumeRequest,
    SessionCreate,
    SessionResponse,
)
from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.graph import get_agent_graph
from enterprise_agent.core.agent.tools.workspace import set_current_user_id
from enterprise_agent.core.execution.state_machine import (
    ExecutionPhase,
    InvalidTaskTransitionError,
    TaskStatus,
    transition_task_status,
)
from enterprise_agent.db.mysql import get_db
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
        events.append({"event": "tool_result", "result": result, **metadata})
        events.append({"event": "tool_end", **metadata})
    return events


_SUPPRESS_MAX_CHUNKS = 30  # Safety timeout for summary mode

# Per-session cancellation events.
# Maps session_id -> asyncio.Event. When the event is set, the SSE generator
# for that session stops iterating and closes the connection.
_cancel_events: dict[str, "asyncio.Event"] = {}
_confirmation_timeout_tasks: dict[str, "asyncio.Task"] = {}


def _cancel_confirmation_timeout(session_id: str) -> None:
    task = _confirmation_timeout_tasks.pop(session_id, None)
    if task and not task.done():
        task.cancel()


def _schedule_confirmation_timeout(session_id: str, user_id: int, deadline_raw: str | None) -> None:
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
            if values.get("task_status") != TaskStatus.WAITING_CONFIRMATION.value:
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
    """Load frontend-visible messages from the LangGraph checkpoint."""
    graph = graph or get_agent_graph()
    state = await graph.aget_state({"configurable": {"thread_id": session_id}})

    if not state or not state.values:
        return []

    raw_messages = state.values.get("messages", [])
    logging.debug("[history] session=%s, raw message count=%s", session_id, len(raw_messages))
    return _serialize_history_messages(raw_messages)


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
        "messages": [{"role": "user", "content": content}],
    }


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
) -> None:
    """Best-effort terminal checkpoint update used by API error/cancel paths."""
    try:
        snapshot = await graph.aget_state(config)
        current = snapshot.values.get("task_status") if snapshot and snapshot.values else None
        try:
            status = transition_task_status(current, target)
        except InvalidTaskTransitionError:
            if current == target.value:
                status = target.value
            else:
                logging.warning("Cannot mark task %s from terminal status %s", target.value, current)
                return
        await graph.aupdate_state(
            config,
            {
                "task_status": status,
                "task_finished_at": datetime.now(timezone.utc).isoformat(),
                "failure_reason": reason,
                "pending_tool_calls": [],
                "should_end": True,
            },
        )
        values = snapshot.values if snapshot and snapshot.values else {}
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
                get_trace_store().finish_trace(
                    user_id=user_id,
                    trace_id=trace_id,
                    status=status,
                    error=reason,
                )
            except Exception:
                logging.warning("Failed to finish terminal task trace", exc_info=True)
    except Exception:
        logging.warning("Failed to persist terminal task status", exc_info=True)


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
    session_id = await _resolve_chat_session(request, user_id, db)
    trace_id = str(uuid.uuid4())
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
    graph = get_agent_graph()
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
    except asyncio.TimeoutError:
        await _safe_mark_task_terminal(graph, config, TaskStatus.FAILED, "Agent invocation timed out")
        raise HTTPException(status_code=504, detail="Request timed out")
    except Exception:
        await _safe_mark_task_terminal(graph, config, TaskStatus.FAILED, "Agent invocation failed")
        raise

    # Get last message (guard against empty messages)
    messages = result.get("messages", [])
    if not messages:
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

    return ChatResponse(
        session_id=session_id,
        trace_id=trace_id,
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
    session_id = await _resolve_chat_session(request, user_id, db)
    trace_id = str(uuid.uuid4())
    _start_task_trace(
        session_id=session_id,
        trace_id=trace_id,
        user_id=user_id,
        content=request.content,
        mode=request.mode,
    )
    set_current_user_id(user_id)

    config = {"configurable": {"thread_id": session_id}}
    graph = get_agent_graph()

    async def generate():
        stream_filter = InternalStreamFilter()

        # Register cancel event for this session
        cancel_event = asyncio.Event()
        _cancel_events[session_id] = cancel_event

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
                    # Check for interrupt (from tool_confirm_node)
                    if "__interrupt__" in data:
                        interrupt_obj = data["__interrupt__"]
                        logging.info(f"[stream] Interrupt detected: {type(interrupt_obj)}")

                        interrupt_data = None
                        if isinstance(interrupt_obj, tuple) and len(interrupt_obj) > 0:
                            first_item = interrupt_obj[0]
                            if hasattr(first_item, 'value'):
                                interrupt_data = first_item.value
                            elif isinstance(first_item, dict):
                                interrupt_data = first_item
                        elif hasattr(interrupt_obj, 'value') and not isinstance(interrupt_obj, tuple):
                            interrupt_data = interrupt_obj.value
                        elif isinstance(interrupt_obj, dict):
                            interrupt_data = interrupt_obj
                        elif isinstance(interrupt_obj, list) and len(interrupt_obj) > 0:
                            first_item = interrupt_obj[0]
                            interrupt_data = first_item.value if hasattr(first_item, 'value') else interrupt_obj
                        else:
                            interrupt_data = {"raw": str(interrupt_obj)[:200]}

                        if not isinstance(interrupt_data, (dict, list)):
                            interrupt_data = {"raw": str(interrupt_data)}

                        deadline = interrupt_data.get("deadline") if isinstance(interrupt_data, dict) else None
                        _schedule_confirmation_timeout(session_id, user_id, deadline)
                        yield _sse_event({"event": "interrupt", "data": interrupt_data})
                        return

                    # Process node outputs
                    for node_name, node_output in data.items():
                        if node_name == "__interrupt__":
                            continue

                        # Tool executor output
                        if node_name == "tool_executor":
                            for event in _tool_sse_events(node_output):
                                yield _sse_event(event)

            yield "data: [DONE]\n\n"
        except GeneratorExit:
            logging.debug("[stream] Generator closed (normal for interrupt/client disconnect)")
            return
        except Exception as e:
            logging.exception("Stream error: %s", e)
            await _safe_mark_task_terminal(graph, config, TaskStatus.FAILED, str(e)[:500])
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        finally:
            # Clean up request-local cancellation state.
            _cancel_events.pop(session_id, None)

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

    async def generate():
        stream_filter = InternalStreamFilter()

        # Register cancel event for this session (reuse same registry keyed by session_id;
        # if the original stream was cancelled, this overwrites the old event)
        cancel_event = asyncio.Event()
        _cancel_events[session_id] = cancel_event

        try:
            logging.info(f"[stream/resume] Session {session_id}: approved={approved}, approved_ids={approved_ids}")

            async for stream_event in graph.astream(
                Command(resume=resume_payload),
                config=config,
                stream_mode=["messages", "updates"]
            ):
                # Check for user-requested cancellation
                if cancel_event.is_set():
                    logging.info(f"[stream/resume] Session {session_id} cancelled by user")
                    yield _sse_event({"event": "cancelled", "message": "Generation stopped by user"})
                    return

                mode, data = stream_event

                if mode == "messages":
                    msg_chunk, _ = data
                    if hasattr(msg_chunk, "content") and msg_chunk.content:
                        delta = _extract_delta(msg_chunk.content)
                        if delta and not stream_filter.is_internal_json(delta):
                            yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
                    if hasattr(msg_chunk, "tool_calls") and msg_chunk.tool_calls:
                        for tc in msg_chunk.tool_calls:
                            if tc.get("name"):
                                yield _sse_event({
                                    "event": "tool_start",
                                    "id": tc.get("id", ""),
                                    "name": tc["name"],
                                })

                elif mode == "updates":
                    if "__interrupt__" in data:
                        interrupt_obj = data["__interrupt__"]
                        logging.info(f"[stream/resume] Another interrupt: {interrupt_obj}")

                        interrupt_data = None
                        if isinstance(interrupt_obj, tuple) and len(interrupt_obj) > 0:
                            first_item = interrupt_obj[0]
                            interrupt_data = first_item.value if hasattr(first_item, 'value') else first_item
                        elif hasattr(interrupt_obj, 'value') and not isinstance(interrupt_obj, tuple):
                            interrupt_data = interrupt_obj.value
                        elif isinstance(interrupt_obj, dict):
                            interrupt_data = interrupt_obj
                        else:
                            interrupt_data = {"raw": str(interrupt_obj)[:200]}

                        if not isinstance(interrupt_data, (dict, list)):
                            interrupt_data = {"raw": str(interrupt_data)}
                        deadline = interrupt_data.get("deadline") if isinstance(interrupt_data, dict) else None
                        _schedule_confirmation_timeout(session_id, user_id, deadline)
                        yield _sse_event({"event": "interrupt", "data": interrupt_data})
                        return

                    for node_name, node_output in data.items():
                        if node_name == "tool_executor":
                            for event in _tool_sse_events(node_output):
                                yield _sse_event(event)

            yield "data: [DONE]\n\n"
        except GeneratorExit:
            logging.debug("[stream/resume] Generator closed (normal for interrupt/client disconnect)")
            return
        except Exception as e:
            logging.exception("Stream resume error: %s", e)
            await _safe_mark_task_terminal(graph, config, TaskStatus.FAILED, str(e)[:500])
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        finally:
            _cancel_events.pop(session_id, None)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/stream/cancel")
async def cancel_stream(
    session_id: str,
    user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel an in-progress SSE stream for a session.

    Sets the cancel event to stop the SSE generator. Also handles the
    case where the graph is paused at a tool confirmation interrupt by
    resuming with a rejection (fire-and-forget), so the graph completes
    cleanly instead of staying in an interrupted state.

    Args:
        session_id: Session/thread ID to cancel
        user_id: Current user ID from JWT

    Returns:
        Status indicating cancellation was requested
    """
    await _require_owned_session(session_id, user_id, db)
    _cancel_confirmation_timeout(session_id)
    set_current_user_id(user_id)

    # 1. Signal the running SSE generator to stop
    cancel_event = _cancel_events.get(session_id)
    if cancel_event:
        cancel_event.set()
        logging.info(f"[cancel] Cancel event set for session {session_id}")
    else:
        logging.info(f"[cancel] No active stream for session {session_id}")

    from enterprise_agent.core.agent.tools.background import clear_background_manager

    clear_background_manager(session_id)

    # 2. Handle pending interrupts (tool confirmation paused state).
    #    If the graph is mid-interrupt, resume with rejection to cleanly
    #    close out the interrupt so the graph reaches END. Fire-and-forget
    #    so the HTTP response returns immediately.
    config = {"configurable": {"thread_id": session_id}}
    graph = get_agent_graph()

    try:
        state = await graph.aget_state(config)

        has_interrupts = False
        if state and state.tasks:
            for task in state.tasks:
                if task.interrupts:
                    has_interrupts = True
                    logging.info(
                        f"[cancel] Pending interrupt found for session {session_id}, "
                        f"resuming with rejection"
                    )
                    break

        if has_interrupts:
            # Resume with rejection — fire-and-forget.
            # The graph will execute tool_confirm_node (reject all),
            # run save_memory, and end. This cleans up the interrupt state.
            task = asyncio.create_task(
                graph.ainvoke(
                    Command(resume={
                        "approved": False,
                        "approved_ids": [],
                        "reason": "task_cancelled",
                    }),
                    config
                )
            )
            # Suppress "Task exception was never retrieved" warning by
            # adding a done callback that logs any exception
            task.add_done_callback(
                lambda t: logging.warning(f"[cancel] Rejection resume failed: {t.exception()}")
                if t.exception() else None
            )
            logging.info(f"[cancel] Fire-and-forget rejection resume started for {session_id}")
        else:
            await _safe_mark_task_terminal(
                graph,
                config,
                TaskStatus.CANCELLED,
                "Cancelled by user",
            )
            logging.info(f"[cancel] Checkpoint marked cancelled for session {session_id}")

    except Exception as e:
        logging.warning(f"[cancel] State check failed (non-fatal): {e}")

    return {
        "status": "cancelled",
        "session_id": session_id,
        "message": "Stream cancellation requested"
    }


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
        )
    )
    sessions = result.scalars().all()

    graph = get_agent_graph()
    responses = []
    for s in sessions:
        messages = await _load_history_messages(s.id, graph)
        if not messages:
            continue
        responses.append(SessionResponse(
            id=s.id,
            user_id=s.user_id,
            title=s.title,
            status=s.status.value,
            created_at=s.created_at,
            message_count=len(messages),
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
    """Get chat history for a session.

    Loads messages from the LangGraph RedisSaver checkpointer.

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
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = await _load_history_messages(session_id)

    logging.debug(f"[history] returning {len(messages)} messages")
    return {
        "session_id": session_id,
        "title": session.title,
        "message_count": len(messages),
        "messages": messages
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
