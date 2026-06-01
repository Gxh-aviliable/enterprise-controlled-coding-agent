import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.api.middleware.auth import get_current_user
from enterprise_agent.api.schemas.chat import ChatRequest, ChatResponse, ResumeRequest, SessionCreate, SessionResponse
from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.graph import get_agent_graph
from enterprise_agent.core.agent.tools.workspace import set_current_user_id
from enterprise_agent.db.mysql import get_db
from enterprise_agent.models.session import Session, SessionStatus

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


def _is_internal_json(delta: str) -> bool:
    """Check if a delta looks like internal evaluation JSON (importance, patterns).

    These leak from memory/importance evaluator LLM calls that run
    inside the LangGraph and get captured by stream_mode=['messages'].
    """
    stripped = delta.strip()
    return (
        stripped.startswith('{"importance"') or
        stripped.startswith('[{"type"') or
        stripped.startswith('{"importance":') or
        stripped.startswith('{"type":')
    )


def _extract_content_from_message(msg) -> str:
    """Extract text content from a message object or dict."""
    if isinstance(msg, dict):
        content = msg.get("content", "")
        return _extract_delta(content) if content else ""
    elif hasattr(msg, "content"):
        return _extract_delta(msg.content) if msg.content else ""
    return str(msg) if msg else ""


router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/completions", response_model=ChatResponse)
async def chat_completion(
    request: ChatRequest,
    user_id: int = Depends(get_current_user)
):
    """Non-streaming chat completion

    Args:
        request: Chat request
        user_id: Current user ID from JWT

    Returns:
        Chat response
    """
    session_id = request.session_id or str(uuid.uuid4())

    # Set user context for workspace isolation
    set_current_user_id(user_id)

    # Execute agent graph with thread_id for state persistence
    try:
        result = await asyncio.wait_for(
            get_agent_graph().ainvoke(
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "messages": [{"role": "user", "content": request.content}]
                },
                config={"configurable": {"thread_id": session_id}}
            ),
            timeout=settings.AGENT_INVOKE_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Request timed out")

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
                except:
                    content = raw_content
            else:
                content = raw_content
        else:
            content = str(raw_content)
    else:
        content = str(last_msg)

    return ChatResponse(
        session_id=session_id,
        role="assistant",
        content=content,
        created_at=datetime.now(timezone.utc)
    )


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    user_id: int = Depends(get_current_user)
):
    """Streaming chat completion (SSE) with interrupt support.

    Uses astream(stream_mode="updates") to detect interrupts from tool_confirm_node.

    Args:
        request: Chat request
        user_id: Current user ID from JWT

    Returns:
        StreamingResponse with SSE events (delta, tool_start, tool_end, interrupt)
    """
    session_id = request.session_id or str(uuid.uuid4())
    set_current_user_id(user_id)

    config = {"configurable": {"thread_id": session_id}}
    graph = get_agent_graph()

    async def generate():
        try:
            # Dual stream modes:
            #   "messages" → token-level deltas from LLM (true streaming)
            #   "updates" → node-level state updates (interrupts, tool results)
            async for stream_event in graph.astream(
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "messages": [{"role": "user", "content": request.content}]
                },
                config=config,
                stream_mode=["messages", "updates"]
            ):
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
                        if delta and not _is_internal_json(delta):
                            yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
                    # Check for tool calls in the chunk
                    if hasattr(msg_chunk, "tool_calls") and msg_chunk.tool_calls:
                        for tc in msg_chunk.tool_calls:
                            if tc.get("name"):
                                yield f"data: {json.dumps({'event': 'tool_start', 'name': tc['name']}, ensure_ascii=False)}\n\n"

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

                        yield f"data: {json.dumps({'event': 'interrupt', 'data': interrupt_data}, ensure_ascii=False)}\n\n"
                        return

                    # Process node outputs
                    for node_name, node_output in data.items():
                        if node_name == "__interrupt__":
                            continue

                        # Tool executor output
                        if node_name == "tool_executor":
                            tool_results = node_output.get("tool_results", {})
                            pending_tools = node_output.get("pending_tool_calls", [])
                            for tool_id, result in tool_results.items():
                                result_str = str(result)
                                if len(result_str) > 200:
                                    result_str = result_str[:200] + "..."
                                yield f"data: {json.dumps({'event': 'tool_result', 'id': tool_id, 'result': result_str}, ensure_ascii=False)}\n\n"
                            # Emit tool_end for each tool so frontend can mark cards as done
                            for tc in pending_tools:
                                yield f"data: {json.dumps({'event': 'tool_end', 'name': tc.get('name', '')}, ensure_ascii=False)}\n\n"

            yield "data: [DONE]\n\n"
        except GeneratorExit:
            logging.debug(f"[stream] Generator closed (normal for interrupt/client disconnect)")
            return
        except Exception as e:
            logging.exception("Stream error: %s", e)
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/stream/resume")
async def chat_stream_resume(
    session_id: str,
    approved: bool,
    body: ResumeRequest = None,
    user_id: int = Depends(get_current_user)
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
    set_current_user_id(user_id)

    approved_ids = body.approved_ids if body else []

    config = {"configurable": {"thread_id": session_id}}
    graph = get_agent_graph()

    async def generate():
        try:
            logging.info(f"[stream/resume] Session {session_id}: approved={approved}, approved_ids={approved_ids}")

            async for stream_event in graph.astream(
                Command(resume={"approved": approved, "approved_ids": approved_ids or []}),
                config=config,
                stream_mode=["messages", "updates"]
            ):
                mode, data = stream_event

                if mode == "messages":
                    msg_chunk, _ = data
                    if hasattr(msg_chunk, "content") and msg_chunk.content:
                        delta = _extract_delta(msg_chunk.content)
                        if delta:
                            yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
                    if hasattr(msg_chunk, "tool_calls") and msg_chunk.tool_calls:
                        for tc in msg_chunk.tool_calls:
                            if tc.get("name"):
                                yield f"data: {json.dumps({'event': 'tool_start', 'name': tc['name']}, ensure_ascii=False)}\n\n"

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
                        yield f"data: {json.dumps({'event': 'interrupt', 'data': interrupt_data}, ensure_ascii=False)}\n\n"
                        return

            yield "data: [DONE]\n\n"
        except GeneratorExit:
            logging.debug(f"[stream/resume] Generator closed (normal for interrupt/client disconnect)")
            return
        except Exception as e:
            logging.exception("Stream resume error: %s", e)
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


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

    return [
        SessionResponse(
            id=s.id,
            user_id=s.user_id,
            title=s.title,
            status=s.status.value,
            created_at=s.created_at
        )
        for s in sessions
    ]


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

    # Load state from Redis checkpointer
    config = {"configurable": {"thread_id": session_id}}
    graph = get_agent_graph()
    state = await graph.aget_state(config)

    # Extract and serialize messages (skip tool results and system prompts)
    messages = []
    if state and state.values:
        raw_messages = state.values.get("messages", [])
        logging.debug(f"[history] session={session_id}, raw message count={len(raw_messages)}")
        for msg in raw_messages:
            if hasattr(msg, "type"):
                role = msg.type
                logging.debug(f"[history] msg type={role}, content_preview={str(msg.content)[:80] if msg.content else '(empty)'}")
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

    logging.debug(f"[history] returning {len(messages)} messages")
    return {
        "session_id": session_id,
        "title": session.title,
        "messages": messages
    }


# === Human-in-the-loop Tool Confirmation ===

@router.post("/confirm")
async def confirm_tool(
    session_id: str,
    approved: bool,
    approved_ids: list[str] = None,
    user_id: int = Depends(get_current_user)
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
    from langgraph.types import Command

    set_current_user_id(user_id)

    config = {"configurable": {"thread_id": session_id}}
    graph = get_agent_graph()

    # Resume execution with user's decision
    # The interrupt() in tool_confirm_node will receive this as user_response
    result = await graph.invoke(
        Command(resume={"approved": approved, "approved_ids": approved_ids or []}),
        config
    )

    logging.info(f"[confirm] Session {session_id}: approved={approved}, approved_ids={approved_ids}")

    return {"status": "resumed", "session_id": session_id, "approved": approved}


@router.get("/pending_confirm/{session_id}")
async def get_pending_confirmation(
    session_id: str,
    user_id: int = Depends(get_current_user)
):
    """Get pending tool confirmation request for a session.

    Returns the current interrupt state if a tool confirmation is pending.

    Args:
        session_id: Session/thread ID
        user_id: Current user ID

    Returns:
        Pending confirmation details or empty if none pending
    """
    config = {"configurable": {"thread_id": session_id}}
    graph = get_agent_graph()

    # Get current state to check for pending interrupts
    state = await graph.get_state(config)

    # Check if there's a pending interrupt for tool confirmation
    tasks = state.tasks
    pending_confirm = None

    for task in tasks:
        if task.interrupts:
            for interrupt_data in task.interrupts:
                if isinstance(interrupt_data, dict) and interrupt_data.get("type") == "tool_confirmation":
                    pending_confirm = interrupt_data
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
