"""Redis-backed cooperative pause control for one Agent task.

The LangGraph checkpoint remains the source of truth for execution state.  The
keys in this module are only cross-process control signals telling the graph to
stop at its next safe boundary.  Every key and payload is scoped by authenticated
user, conversation session, and task trace so a late request cannot pause a
newer task in the same conversation.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from enterprise_agent.config.settings import settings
from enterprise_agent.db.redis import get_redis

_CONTROL_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def _validate_identity(user_id: int, session_id: str, trace_id: str) -> None:
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 0:
        raise ValueError("Invalid pause-control user ID")
    if not _CONTROL_ID.fullmatch(str(session_id)):
        raise ValueError("Invalid pause-control session ID")
    if not _CONTROL_ID.fullmatch(str(trace_id)):
        raise ValueError("Invalid pause-control trace ID")


def _pause_key(user_id: int, session_id: str, trace_id: str) -> str:
    _validate_identity(user_id, session_id, trace_id)
    return f"agent:pause:{user_id}:{session_id}:{trace_id}"


def _resume_lock_key(user_id: int, session_id: str, trace_id: str) -> str:
    _validate_identity(user_id, session_id, trace_id)
    return f"agent:resume-lock:{user_id}:{session_id}:{trace_id}"


def _decode_request(
    raw: Any,
    *,
    user_id: int,
    session_id: str,
    trace_id: str,
) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        payload = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid pause-control payload") from exc
    expected = {
        "user_id": user_id,
        "session_id": session_id,
        "trace_id": trace_id,
    }
    if not isinstance(payload, dict) or any(payload.get(key) != value for key, value in expected.items()):
        raise ValueError("Pause-control identity mismatch")
    return payload


async def request_task_pause(
    user_id: int,
    session_id: str,
    trace_id: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Create an idempotent, expiring pause request for an exact task."""
    key = _pause_key(user_id, session_id, trace_id)
    payload = {
        "user_id": user_id,
        "session_id": session_id,
        "trace_id": trace_id,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "reason": str(reason or "Paused by user")[:500],
    }
    redis = await get_redis()
    created = await redis.set(
        key,
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        nx=True,
        ex=max(60, settings.CHECKPOINT_TTL_HOURS * 3600),
    )
    if created:
        return {**payload, "created": True}

    # SET NX makes repeat requests idempotent.  Read the winning request back
    # instead of replacing its original timestamp/reason.
    existing = await redis.get(key)
    decoded = _decode_request(
        existing,
        user_id=user_id,
        session_id=session_id,
        trace_id=trace_id,
    )
    if decoded is None:
        # The key can expire between SET NX and GET.  A single retry preserves
        # the same semantics without an unbounded loop.
        created = await redis.set(
            key,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            nx=True,
            ex=max(60, settings.CHECKPOINT_TTL_HOURS * 3600),
        )
        if created:
            return {**payload, "created": True}
        existing = await redis.get(key)
        decoded = _decode_request(
            existing,
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
        )
    if decoded is None:
        raise RuntimeError("Pause request changed concurrently; retry the operation")
    return {**decoded, "created": False}


async def get_task_pause_request(
    user_id: int,
    session_id: str,
    trace_id: str,
) -> dict[str, Any] | None:
    """Return the exact task's pause request, rejecting malformed identities."""
    redis = await get_redis()
    raw = await redis.get(_pause_key(user_id, session_id, trace_id))
    return _decode_request(
        raw,
        user_id=user_id,
        session_id=session_id,
        trace_id=trace_id,
    )


async def clear_task_pause_request(
    user_id: int,
    session_id: str,
    trace_id: str,
) -> bool:
    """Clear one exact task's control request after resume/cancel/termination."""
    key = _pause_key(user_id, session_id, trace_id)
    redis = await get_redis()
    existing = await redis.get(key)
    if existing is None:
        return False
    _decode_request(
        existing,
        user_id=user_id,
        session_id=session_id,
        trace_id=trace_id,
    )
    return bool(await redis.delete(key))


async def acquire_task_resume_lock(
    user_id: int,
    session_id: str,
    trace_id: str,
    ttl_seconds: int = 30,
) -> bool:
    """Acquire a short cross-process lock preventing duplicate Command(resume)."""
    if ttl_seconds <= 0:
        raise ValueError("Resume-lock TTL must be positive")
    key = _resume_lock_key(user_id, session_id, trace_id)
    redis = await get_redis()
    value = json.dumps({
        "user_id": user_id,
        "session_id": session_id,
        "trace_id": trace_id,
    }, sort_keys=True)
    return bool(await redis.set(key, value, nx=True, ex=int(ttl_seconds)))


async def release_task_resume_lock(
    user_id: int,
    session_id: str,
    trace_id: str,
) -> bool:
    """Release the exact task's resume lock (missing locks are idempotent)."""
    redis = await get_redis()
    return bool(await redis.delete(_resume_lock_key(user_id, session_id, trace_id)))


__all__ = [
    "acquire_task_resume_lock",
    "clear_task_pause_request",
    "get_task_pause_request",
    "release_task_resume_lock",
    "request_task_pause",
]
