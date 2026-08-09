"""Durable MySQL-backed chat transcript operations."""

import uuid
from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.models.chat_message import ChatMessage
from enterprise_agent.models.session import Session


def serialize_message(message: ChatMessage) -> dict[str, str]:
    """Return the stable frontend message shape."""
    return {"role": message.role, "content": message.content}


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
    message.content = f"{message.content}{content}" if append else content
    message.status = status
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
    if tombstone not in message.content:
        separator = "\n\n" if message.content else ""
        message.content = f"{message.content}{separator}{tombstone}"
    message.status = "cancelled"
    message.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return True
