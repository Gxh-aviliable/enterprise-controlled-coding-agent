"""Durable MySQL-backed chat transcript operations."""

import json
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.models.chat_message import ChatMessage
from enterprise_agent.models.session import Session

_OPEN_TOOL_STATUSES = {"running", "waiting"}
_TERMINAL_TOOL_STATUSES = {"done", "error"}
_TOOL_STATUS_ALIASES = {
    "blocked": "error",
    "cancelled": "error",
    "completed": "done",
    "failed": "error",
    "interrupted": "error",
    "pending": "running",
    "success": "done",
    "succeeded": "done",
    "timeout": "error",
    "waiting_confirmation": "waiting",
}


def _timeline_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _tool_status(value: Any, *, default: str = "running") -> str:
    normalized = str(value or "").strip().lower()
    normalized = _TOOL_STATUS_ALIASES.get(normalized, normalized)
    return normalized if normalized in _OPEN_TOOL_STATUSES | _TERMINAL_TOOL_STATUSES else default


def _tool_duration(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _first_present(entry: dict, *keys: str) -> tuple[bool, Any]:
    for key in keys:
        if key in entry:
            return True, entry[key]
    return False, None


def merge_timeline(
    existing: Iterable[dict] | None,
    entries: Iterable[dict] | None,
) -> list[dict[str, Any]]:
    """Normalize and merge compact assistant/tool timeline blocks.

    Assistant blocks are coalesced only when adjacent. Tool updates keep their
    original position: a non-empty provider call ID is authoritative, while a
    provider that omits IDs falls back to the most recent unresolved tool with
    the same name.
    """
    merged: list[dict[str, Any]] = []

    def merge_one(raw_entry: dict) -> None:
        if not isinstance(raw_entry, dict):
            return
        role = str(raw_entry.get("role") or "").strip().lower()
        if role == "assistant":
            content = _timeline_text(raw_entry.get("content"))
            if not content:
                return
            if merged and merged[-1].get("role") == "assistant":
                merged[-1]["content"] += content
            else:
                merged.append({"role": "assistant", "content": content})
            return
        if role != "tool_call":
            return

        id_present, raw_tool_id = _first_present(raw_entry, "toolCallId", "tool_call_id", "id")
        name_present, raw_tool_name = _first_present(raw_entry, "toolName", "tool_name", "name")
        status_present, raw_status = _first_present(raw_entry, "toolStatus", "tool_status", "status")
        result_present, raw_result = _first_present(raw_entry, "toolResult", "tool_result", "result")
        error_present, raw_error = _first_present(raw_entry, "toolError", "tool_error", "error")
        duration_present, raw_duration = _first_present(
            raw_entry,
            "toolDuration",
            "tool_duration",
            "duration_ms",
        )

        tool_id = str(raw_tool_id or "") if id_present else ""
        tool_name = str(raw_tool_name or "") if name_present else ""
        incoming_status = _tool_status(raw_status) if status_present else None

        match_index = None
        if tool_id:
            for index in range(len(merged) - 1, -1, -1):
                block = merged[index]
                if block.get("role") == "tool_call" and block.get("toolCallId") == tool_id:
                    match_index = index
                    break
        elif tool_name:
            for index in range(len(merged) - 1, -1, -1):
                block = merged[index]
                if (
                    block.get("role") == "tool_call"
                    and block.get("toolName") == tool_name
                    and block.get("toolStatus") in _OPEN_TOOL_STATUSES
                ):
                    match_index = index
                    break

        if match_index is None:
            merged.append({
                "role": "tool_call",
                "toolCallId": tool_id,
                "toolName": tool_name or "tool",
                "toolStatus": incoming_status or "running",
                "toolResult": _timeline_text(raw_result) if result_present else "",
                "toolError": _timeline_text(raw_error) if error_present else "",
                "toolDuration": _tool_duration(raw_duration) if duration_present else None,
            })
            return

        block = merged[match_index]
        if tool_id:
            block["toolCallId"] = tool_id
        if name_present and tool_name:
            block["toolName"] = tool_name
        if incoming_status is not None:
            terminal_downgrade = (
                block.get("toolStatus") in _TERMINAL_TOOL_STATUSES
                and incoming_status in _OPEN_TOOL_STATUSES
            )
            if not terminal_downgrade:
                block["toolStatus"] = incoming_status
        if result_present:
            block["toolResult"] = _timeline_text(raw_result)
        if error_present:
            block["toolError"] = _timeline_text(raw_error)
        if duration_present:
            duration = _tool_duration(raw_duration)
            if duration is not None:
                block["toolDuration"] = duration

    for entry in existing or ():
        merge_one(entry)
    for entry in entries or ():
        merge_one(entry)
    return merged


def terminalize_timeline(
    timeline: Iterable[dict] | None,
    assistant_status: str,
) -> list[dict[str, Any]]:
    """Mark tools without an authoritative result as failed at task end."""
    normalized = merge_timeline(None, timeline)
    if assistant_status not in {"completed", "failed", "cancelled"}:
        return normalized
    error_by_status = {
        "completed": "Tool ended without an authoritative completion result",
        "failed": "Task failed before tool completion",
        "cancelled": "Task cancelled before tool completion",
    }
    for block in normalized:
        if block.get("role") != "tool_call" or block.get("toolStatus") not in _OPEN_TOOL_STATUSES:
            continue
        block["toolStatus"] = "error"
        if not block.get("toolError"):
            block["toolError"] = error_by_status[assistant_status]
    return normalized


def serialize_message(message: ChatMessage) -> dict[str, Any]:
    """Return the stable frontend message shape."""
    serialized: dict[str, Any] = {"role": message.role, "content": message.content}
    timeline = merge_timeline(None, getattr(message, "timeline", None))
    if message.role == "assistant" and timeline:
        serialized["timeline"] = timeline
    return serialized


async def list_messages(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: int,
) -> list[ChatMessage]:
    result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.user_id == user_id,
        )
        .order_by(ChatMessage.id.asc())
    )
    return list(result.scalars().all())


def build_model_history(
    messages: Iterable[ChatMessage],
    *,
    max_messages: int,
    max_characters: int,
) -> list[dict[str, str]]:
    """Build bounded, de-duplicated model context from durable chat rows.

    Redis checkpoints remain the preferred execution context.  This projection
    is used only when that checkpoint is missing, so a MySQL-backed UI history
    and the model cannot silently disagree after checkpoint expiry.
    """
    if max_messages <= 0 or max_characters <= 0:
        return []

    normalized: list[dict[str, str]] = []
    seen_rows: set[tuple[str, str, str]] = set()
    for message in messages:
        role = str(getattr(message, "role", ""))
        content = str(getattr(message, "content", "") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        trace_id = str(getattr(message, "trace_id", "") or "")
        row_key = (trace_id, role, content)
        if row_key in seen_rows:
            continue
        seen_rows.add(row_key)
        if normalized and normalized[-1] == {"role": role, "content": content}:
            continue
        normalized.append({"role": role, "content": content})

    # Keep the newest complete evidence that fits both limits.  Preserve
    # chronological order in the returned context.
    selected: list[dict[str, str]] = []
    used_characters = 0
    for message in reversed(normalized[-max_messages:]):
        content = message["content"]
        remaining = max_characters - used_characters
        if remaining <= 0:
            break
        if len(content) > remaining:
            if selected:
                break
            content = content[-remaining:]
        selected.append({"role": message["role"], "content": content})
        used_characters += len(content)
    selected.reverse()

    # A leading assistant fragment has no user request anchor and is more
    # likely to be the tail of a truncated turn than useful standalone context.
    while selected and selected[0]["role"] == "assistant":
        selected.pop(0)
    return selected


async def persist_continuation_receipt(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: int,
    trace_id: str,
    receipt: dict[str, Any],
) -> bool:
    """Idempotently attach one cancellation hand-off to its assistant row."""
    result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.user_id == user_id,
            ChatMessage.trace_id == trace_id,
            ChatMessage.role == "assistant",
        )
        .with_for_update()
    )
    message = result.scalar_one_or_none()
    if message is None:
        return False

    previous = getattr(message, "continuation_receipt", None)
    previous = dict(previous) if isinstance(previous, dict) else {}
    if previous.get("trace_id") == trace_id and previous.get("consumed_by_trace_id"):
        receipt = {
            **receipt,
            "consumed_by_trace_id": previous["consumed_by_trace_id"],
            "consumed_at": previous.get("consumed_at"),
        }
    message.continuation_receipt = dict(receipt)
    message.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return True


async def claim_latest_continuation_receipt(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: int,
    consumer_trace_id: str,
) -> dict[str, Any] | None:
    """Claim the newest unconsumed receipt for exactly one subsequent trace."""
    result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.user_id == user_id,
            ChatMessage.role == "assistant",
            ChatMessage.status == "cancelled",
        )
        .order_by(ChatMessage.id.desc())
        .limit(20)
        .with_for_update()
    )
    for message in result.scalars().all():
        stored = getattr(message, "continuation_receipt", None)
        if not isinstance(stored, dict) or not stored.get("trace_id"):
            continue
        if stored.get("consumed_by_trace_id"):
            continue
        claimed_at = datetime.now(timezone.utc).isoformat()
        message.continuation_receipt = {
            **stored,
            "consumed_by_trace_id": consumer_trace_id,
            "consumed_at": claimed_at,
        }
        message.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return dict(stored)
    await db.commit()
    return None


async def message_counts_by_session(
    db: AsyncSession,
    *,
    user_id: int,
) -> dict[str, int]:
    result = await db.execute(
        select(ChatMessage.session_id, func.count(ChatMessage.id))
        .where(
            ChatMessage.user_id == user_id,
            ChatMessage.content != "",
        )
        .group_by(ChatMessage.session_id)
    )
    return {str(session_id): int(count) for session_id, count in result.all()}


async def persist_legacy_messages(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: int,
    messages: Iterable[dict],
) -> int:
    """Copy a still-readable legacy Redis transcript into MySQL once."""
    if await list_messages(db, session_id=session_id, user_id=user_id):
        return 0

    records = []
    for index, message in enumerate(messages):
        role = str(message.get("role", ""))
        content = str(message.get("content", ""))
        if role not in {"user", "assistant"} or not content:
            continue
        legacy_trace_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"mini-claude:{session_id}:legacy:{index}")
        )
        records.append(
            ChatMessage(
                session_id=session_id,
                user_id=user_id,
                trace_id=legacy_trace_id,
                role=role,
                content=content,
                status="completed",
                source="redis_migration",
            )
        )

    if not records:
        return 0
    db.add_all(records)
    await db.commit()
    return len(records)


async def start_turn(
    db: AsyncSession,
    *,
    session: Session,
    user_id: int,
    trace_id: str,
    content: str,
) -> int:
    """Persist the user request and create its single assistant response row."""
    user_message = ChatMessage(
        session_id=session.id,
        user_id=user_id,
        trace_id=trace_id,
        role="user",
        content=content,
        status="completed",
    )
    assistant_message = ChatMessage(
        session_id=session.id,
        user_id=user_id,
        trace_id=trace_id,
        role="assistant",
        content="",
        status="streaming",
    )
    session.updated_at = datetime.now(timezone.utc)
    db.add_all([user_message, assistant_message])
    await db.flush()
    assistant_message_id = int(assistant_message.id)
    await db.commit()
    return assistant_message_id


async def find_assistant_message_id(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: int,
    trace_id: str,
) -> int | None:
    result = await db.execute(
        select(ChatMessage.id).where(
            ChatMessage.session_id == session_id,
            ChatMessage.user_id == user_id,
            ChatMessage.trace_id == trace_id,
            ChatMessage.role == "assistant",
        )
    )
    value = result.scalar_one_or_none()
    return int(value) if value is not None else None


async def get_latest_assistant_task(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: int,
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    """Return durable lifecycle evidence for the newest matching assistant row."""
    query = select(ChatMessage).where(
        ChatMessage.session_id == session_id,
        ChatMessage.user_id == user_id,
        ChatMessage.role == "assistant",
    )
    if trace_id is not None:
        query = query.where(ChatMessage.trace_id == trace_id)
    result = await db.execute(query.order_by(ChatMessage.id.desc()).limit(1))
    message = result.scalar_one_or_none()
    if message is None:
        return None
    receipt = getattr(message, "continuation_receipt", None)
    return {
        "message_id": int(message.id),
        "trace_id": str(message.trace_id),
        "status": str(message.status),
        "continuation_receipt": dict(receipt) if isinstance(receipt, dict) else None,
    }


async def create_assistant_message(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: int,
    trace_id: str,
    status: str = "streaming",
) -> int:
    """Create the assistant row needed by a legacy interrupted task."""
    message = ChatMessage(
        session_id=session_id,
        user_id=user_id,
        trace_id=trace_id,
        role="assistant",
        content="",
        status=status,
    )
    db.add(message)
    await db.flush()
    message_id = int(message.id)
    await db.commit()
    return message_id


async def update_assistant_message(
    db: AsyncSession,
    *,
    message_id: int,
    user_id: int,
    content: str,
    status: str,
    append: bool = True,
    timeline_entries: Iterable[dict] | None = None,
) -> bool:
    """Atomically append a streamed segment and terminalize its message row."""
    result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.id == message_id,
            ChatMessage.user_id == user_id,
            ChatMessage.role == "assistant",
        )
        .with_for_update()
    )
    message = result.scalar_one_or_none()
    if message is None:
        return False
    # Terminal task outcomes are authoritative. A delayed/disconnected SSE
    # generator may finish after the graph has already converged, but it must
    # never downgrade the durable row or append stale output after that point.
    if message.status in {"completed", "failed", "cancelled"}:
        await db.commit()
        return True

    previous_content = str(message.content or "")
    if append:
        existing_timeline = merge_timeline(None, getattr(message, "timeline", None))
        # A legacy/interrupted row can already have text while predating the
        # JSON column. Seed it once so a resumed segment cannot hide history.
        if not existing_timeline and previous_content:
            existing_timeline = [{"role": "assistant", "content": previous_content}]
        incoming_timeline = timeline_entries
        if timeline_entries is None and content:
            incoming_timeline = [{"role": "assistant", "content": content}]
        message.timeline = merge_timeline(existing_timeline, incoming_timeline) or None
        message.content = f"{previous_content}{content}"
    else:
        replacement_timeline = timeline_entries
        if timeline_entries is None and content:
            replacement_timeline = [{"role": "assistant", "content": content}]
        message.timeline = merge_timeline(None, replacement_timeline) or None
        message.content = content

    message.status = status
    if status in {"completed", "failed", "cancelled"}:
        message.timeline = terminalize_timeline(message.timeline, status) or None
    message.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return True


async def mark_assistant_message_cancelled(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: int,
    trace_id: str,
    tombstone: str,
    continuation_receipt: dict[str, Any] | None = None,
) -> bool:
    """Idempotently terminalize one trace's durable assistant message."""
    result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.user_id == user_id,
            ChatMessage.trace_id == trace_id,
            ChatMessage.role == "assistant",
        )
        .with_for_update()
    )
    message = result.scalar_one_or_none()
    if message is None:
        return False

    previous_content = str(message.content or "")
    timeline = merge_timeline(None, getattr(message, "timeline", None))
    if not timeline and previous_content:
        timeline = [{"role": "assistant", "content": previous_content}]

    if tombstone not in previous_content:
        separator = "\n\n" if previous_content else ""
        addition = f"{separator}{tombstone}"
        message.content = f"{previous_content}{addition}"
    else:
        addition = ""

    timeline_text = "".join(
        block.get("content", "")
        for block in timeline
        if block.get("role") == "assistant"
    )
    if tombstone not in timeline_text:
        timeline_separator = "\n\n" if timeline_text else ""
        timeline_addition = addition or f"{timeline_separator}{tombstone}"
        timeline = merge_timeline(
            timeline,
            [{"role": "assistant", "content": timeline_addition}],
        )

    message.status = "cancelled"
    message.timeline = terminalize_timeline(timeline, "cancelled") or None
    if continuation_receipt is not None:
        previous_receipt = getattr(message, "continuation_receipt", None)
        previous_receipt = (
            dict(previous_receipt) if isinstance(previous_receipt, dict) else {}
        )
        message.continuation_receipt = {
            **continuation_receipt,
            **({
                "consumed_by_trace_id": previous_receipt["consumed_by_trace_id"],
                "consumed_at": previous_receipt.get("consumed_at"),
            } if previous_receipt.get("consumed_by_trace_id") else {}),
        }
    message.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return True
