"""Redis-authoritative task interrupt, runner, and resume controls.

LangGraph checkpoints remain the durable execution state.  The primitives in
this module fence which worker may currently drive one session checkpoint and
carry an exact, expiring cancellation signal across workers.  Process-local
events may mirror these controls for latency, but are never authoritative.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any, Literal

import redis as redis_sync

from enterprise_agent.config.settings import settings
from enterprise_agent.db.redis import get_redis

logger = logging.getLogger(__name__)

_CONTROL_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_TASK_CONTROL_IDENTITY: ContextVar[tuple[int, str, str] | None] = ContextVar(
    "task_control_identity",
    default=None,
)
_TASK_RUNNER_IDENTITY: ContextVar[tuple[int, str, str, str, str] | None] = ContextVar(
    "task_runner_identity",
    default=None,
)
_sync_redis_client: Any | None = None

DEFAULT_RESUME_LOCK_TTL_SECONDS = 30
DEFAULT_ACTIVE_LEASE_TTL_SECONDS = max(60, settings.ACTIVE_TRACE_LEASE_SECONDS)
DEFAULT_CANCEL_TTL_SECONDS = max(60, settings.CHECKPOINT_TTL_HOURS * 3600)
USER_PAUSE_PROTOCOL_RETIRED_KEY = "agent:protocol:user-pause:retired"

LeaseReleaseStatus = Literal["released", "missing", "not_owner", "runner_active"]


_CLAIM_ACTIVE_TRACE_SCRIPT = r"""
-- interrupt-control:claim-active-trace
if redis.call("EXISTS", KEYS[1]) == 1 then
    return {0, redis.call("HGET", KEYS[1], "trace_id") or ""}
end
local fence = redis.call("INCR", KEYS[2])
redis.call(
    "HSET", KEYS[1],
    "user_id", ARGV[1],
    "session_id", ARGV[2],
    "trace_id", ARGV[3],
    "lease_token", ARGV[4],
    "fence", tostring(fence),
    "runner_token", ARGV[5],
    "runner_epoch", "1",
    "runner_state", "starting",
    "cancel_requested", "0",
    "created_at", ARGV[6],
    "updated_at", ARGV[6]
)
redis.call("EXPIRE", KEYS[1], tonumber(ARGV[7]))
return {1, tostring(fence)}
"""

_RESERVE_RUNNER_SCRIPT = r"""
-- interrupt-control:reserve-runner
if redis.call("EXISTS", KEYS[1]) == 0 then
    return -1
end
if redis.call("HGET", KEYS[1], "trace_id") ~= ARGV[1]
    or redis.call("HGET", KEYS[1], "lease_token") ~= ARGV[2] then
    return 0
end
if redis.call("HGET", KEYS[1], "runner_state") ~= "stopped" then
    return -2
end
if redis.call("HGET", KEYS[1], "cancel_requested") == "1"
    or redis.call("EXISTS", KEYS[2]) == 1 then
    return -3
end
local epoch = redis.call("HINCRBY", KEYS[1], "runner_epoch", 1)
redis.call(
    "HSET", KEYS[1],
    "runner_token", ARGV[3],
    "runner_state", "starting",
    "updated_at", ARGV[4]
)
redis.call("HDEL", KEYS[1], "stopped_at", "stop_reason")
redis.call("EXPIRE", KEYS[1], tonumber(ARGV[5]))
return epoch
"""

_START_RUNNER_SCRIPT = r"""
-- interrupt-control:start-runner
if redis.call("EXISTS", KEYS[1]) == 0 then
    return -1
end
if redis.call("HGET", KEYS[1], "trace_id") ~= ARGV[1]
    or redis.call("HGET", KEYS[1], "lease_token") ~= ARGV[2]
    or redis.call("HGET", KEYS[1], "runner_token") ~= ARGV[3] then
    return 0
end
if redis.call("HGET", KEYS[1], "runner_state") ~= "starting" then
    return -2
end
if redis.call("HGET", KEYS[1], "cancel_requested") == "1"
    or redis.call("EXISTS", KEYS[2]) == 1 then
    redis.call(
        "HSET", KEYS[1],
        "runner_state", "stopped",
        "stopped_at", ARGV[4],
        "stop_reason", "cancelled_before_start",
        "updated_at", ARGV[4]
    )
    redis.call("EXPIRE", KEYS[1], tonumber(ARGV[5]))
    return -3
end
redis.call("HSET", KEYS[1], "runner_state", "running", "updated_at", ARGV[4])
redis.call("EXPIRE", KEYS[1], tonumber(ARGV[5]))
return 1
"""

_STOP_RUNNER_SCRIPT = r"""
-- interrupt-control:stop-runner
if redis.call("EXISTS", KEYS[1]) == 0 then
    return -1
end
if redis.call("HGET", KEYS[1], "trace_id") ~= ARGV[1]
    or redis.call("HGET", KEYS[1], "lease_token") ~= ARGV[2]
    or redis.call("HGET", KEYS[1], "runner_token") ~= ARGV[3] then
    return 0
end
redis.call(
    "HSET", KEYS[1],
    "runner_state", "stopped",
    "stopped_at", ARGV[4],
    "stop_reason", ARGV[5],
    "updated_at", ARGV[4]
)
redis.call("EXPIRE", KEYS[1], tonumber(ARGV[6]))
return 1
"""

_RENEW_LEASE_SCRIPT = r"""
-- interrupt-control:renew-lease
if redis.call("EXISTS", KEYS[1]) == 0 then
    return 0
end
if redis.call("HGET", KEYS[1], "trace_id") ~= ARGV[1]
    or redis.call("HGET", KEYS[1], "lease_token") ~= ARGV[2] then
    return 0
end
redis.call("HSET", KEYS[1], "updated_at", ARGV[3])
redis.call("EXPIRE", KEYS[1], tonumber(ARGV[4]))
return 1
"""

_RENEW_RUNNER_SCRIPT = r"""
-- interrupt-control:renew-runner
if redis.call("EXISTS", KEYS[1]) == 0 then
    return 0
end
if redis.call("HGET", KEYS[1], "trace_id") ~= ARGV[1]
    or redis.call("HGET", KEYS[1], "lease_token") ~= ARGV[2]
    or redis.call("HGET", KEYS[1], "runner_token") ~= ARGV[3] then
    return 0
end
local state = redis.call("HGET", KEYS[1], "runner_state")
if state ~= "starting" and state ~= "running" then
    return 0
end
redis.call("HSET", KEYS[1], "updated_at", ARGV[4])
redis.call("EXPIRE", KEYS[1], tonumber(ARGV[5]))
return 1
"""

_REQUEST_CANCEL_SCRIPT = r"""
-- interrupt-control:request-cancel
local active_exists = redis.call("EXISTS", KEYS[1])
if active_exists == 1 and redis.call("HGET", KEYS[1], "trace_id") ~= ARGV[1] then
    return {-1, 0}
end
local created = 0
if redis.call("EXISTS", KEYS[2]) == 0 then
    if redis.call("SET", KEYS[2], ARGV[2], "EX", tonumber(ARGV[3]), "NX") then
        created = 1
    end
else
    redis.call("EXPIRE", KEYS[2], tonumber(ARGV[3]))
end
if active_exists == 0 then
    return {0, created}
end
redis.call(
    "HSET", KEYS[1],
    "cancel_requested", "1",
    "cancel_requested_at", ARGV[4],
    "updated_at", ARGV[4]
)
redis.call("EXPIRE", KEYS[1], tonumber(ARGV[5]))
if redis.call("HGET", KEYS[1], "runner_state") == "starting" then
    redis.call(
        "HSET", KEYS[1],
        "runner_state", "stopped",
        "stopped_at", ARGV[4],
        "stop_reason", "cancelled_before_start"
    )
    return {2, created}
end
return {1, created}
"""

_RELEASE_LEASE_SCRIPT = r"""
-- interrupt-control:release-lease
if redis.call("EXISTS", KEYS[1]) == 0 then
    return 2
end
if redis.call("HGET", KEYS[1], "trace_id") ~= ARGV[1]
    or redis.call("HGET", KEYS[1], "lease_token") ~= ARGV[2] then
    return 0
end
if redis.call("HGET", KEYS[1], "runner_state") ~= "stopped" then
    return -1
end
redis.call("DEL", KEYS[1])
return 1
"""

_COMPARE_DELETE_SCRIPT = r"""
-- interrupt-control:compare-delete
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
end
return 0
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_user_session(user_id: int, session_id: str) -> None:
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 0:
        raise ValueError("Invalid interrupt-control user ID")
    if not isinstance(session_id, str) or not _CONTROL_ID.fullmatch(session_id):
        raise ValueError("Invalid interrupt-control session ID")


def _validate_identity(user_id: int, session_id: str, trace_id: str) -> None:
    _validate_user_session(user_id, session_id)
    if not isinstance(trace_id, str) or not _CONTROL_ID.fullmatch(trace_id):
        raise ValueError("Invalid interrupt-control trace ID")


def _validate_owner_token(value: str, name: str) -> None:
    if not isinstance(value, str) or not _CONTROL_ID.fullmatch(value):
        raise ValueError(f"Invalid interrupt-control {name}")


def _positive_ttl(value: int | None, default: int, name: str) -> int:
    ttl = default if value is None else value
    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
        raise ValueError(f"{name} TTL must be a positive integer")
    return ttl


def _active_lease_key(user_id: int, session_id: str) -> str:
    _validate_user_session(user_id, session_id)
    return f"agent:active-session:{user_id}:{session_id}"


def _active_fence_key(user_id: int, session_id: str) -> str:
    _validate_user_session(user_id, session_id)
    return f"agent:active-session-fence:{user_id}:{session_id}"


def _cancel_key(user_id: int, session_id: str, trace_id: str) -> str:
    _validate_identity(user_id, session_id, trace_id)
    return f"agent:cancel-requested:{user_id}:{session_id}:{trace_id}"


def _resume_lock_key(user_id: int, session_id: str, trace_id: str) -> str:
    _validate_identity(user_id, session_id, trace_id)
    return f"agent:resume-lock:{user_id}:{session_id}:{trace_id}"


def _legacy_pause_key(user_id: int, session_id: str, trace_id: str) -> str:
    _validate_identity(user_id, session_id, trace_id)
    return f"agent:pause:{user_id}:{session_id}:{trace_id}"


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(_as_text(value))
    except (TypeError, ValueError):
        return default


def _decode_hash(raw: dict[Any, Any]) -> dict[str, str]:
    return {_as_text(key): _as_text(value) for key, value in raw.items()}


def _decode_cancel_request(
    raw: Any,
    *,
    user_id: int,
    session_id: str,
    trace_id: str,
) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        payload = json.loads(_as_text(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid task-cancellation payload") from exc
    expected = {"user_id": user_id, "session_id": session_id, "trace_id": trace_id}
    if not isinstance(payload, dict) or any(payload.get(key) != value for key, value in expected.items()):
        raise ValueError("Task-cancellation identity mismatch")
    return payload


def _eval_code(result: Any) -> int:
    if isinstance(result, (list, tuple)):
        return _as_int(result[0]) if result else 0
    return _as_int(result)


def _eval_created(result: Any) -> bool:
    return bool(
        isinstance(result, (list, tuple))
        and len(result) > 1
        and _as_int(result[1]) == 1
    )


def set_current_task_control_identity(
    user_id: int,
    session_id: str,
    trace_id: str,
) -> Token:
    """Bind an exact task identity to the current async/thread context."""
    _validate_identity(user_id, session_id, trace_id)
    return _TASK_CONTROL_IDENTITY.set((user_id, session_id, trace_id))


def reset_current_task_control_identity(token: Token) -> None:
    """Restore the task-control ContextVar to its previous value."""
    _TASK_CONTROL_IDENTITY.reset(token)


def get_current_task_control_identity() -> tuple[int, str, str] | None:
    """Return the current exact task identity, if one was installed."""
    return _TASK_CONTROL_IDENTITY.get()


def set_current_task_runner_identity(
    user_id: int,
    session_id: str,
    trace_id: str,
    lease_token: str,
    runner_token: str,
) -> Token:
    """Bind the exact Redis runner fence to the current graph context."""
    _validate_identity(user_id, session_id, trace_id)
    _validate_owner_token(lease_token, "lease token")
    _validate_owner_token(runner_token, "runner token")
    return _TASK_RUNNER_IDENTITY.set(
        (user_id, session_id, trace_id, lease_token, runner_token)
    )


def reset_current_task_runner_identity(token: Token) -> None:
    """Restore the previous runner fence context."""
    _TASK_RUNNER_IDENTITY.reset(token)


def get_current_task_runner_identity() -> tuple[int, str, str, str, str] | None:
    """Return the current exact runner fence, if one is installed."""
    return _TASK_RUNNER_IDENTITY.get()


async def owns_current_task_runner() -> bool:
    """Verify that the current graph context still owns its Redis runner fence."""
    identity = get_current_task_runner_identity()
    if identity is None:
        return True
    return await owns_active_trace_runner(*identity)


def _get_sync_redis():
    global _sync_redis_client
    if _sync_redis_client is None:
        _sync_redis_client = redis_sync.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD,
            decode_responses=True,
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
        )
    return _sync_redis_client


def is_current_task_cancel_requested_sync() -> bool:
    """Poll the exact Redis tombstone from synchronous tools such as ``bash``.

    Missing task context deliberately means "not cancellable" so command tools
    keep their existing CLI/test behavior. Redis transport failures also return
    false: the HTTP Stop path must fail closed and retain its active lease, while
    a transient polling failure must not invent a cancellation.
    """
    identity = get_current_task_control_identity()
    if identity is None:
        return False
    user_id, session_id, trace_id = identity
    try:
        redis = _get_sync_redis()
        runner_identity = get_current_task_runner_identity()
        if runner_identity is not None:
            runner_user, runner_session, runner_trace, lease_token, runner_token = (
                runner_identity
            )
            active = _decode_hash(
                redis.hgetall(_active_lease_key(runner_user, runner_session))
            )
            if (
                active.get("trace_id") != runner_trace
                or active.get("lease_token") != lease_token
                or active.get("runner_token") != runner_token
                or active.get("runner_state") not in {"starting", "running"}
            ):
                return True
        raw = redis.get(_cancel_key(user_id, session_id, trace_id))
    except Exception:
        logger.warning("Synchronous cancellation poll failed", exc_info=True)
        return False
    if raw is None:
        return False
    try:
        _decode_cancel_request(
            raw,
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
        )
    except ValueError:
        logger.warning("Malformed exact cancellation tombstone; stopping defensively", exc_info=True)
    return True


async def acquire_task_resume_lock(
    user_id: int,
    session_id: str,
    trace_id: str,
    ttl_seconds: int = DEFAULT_RESUME_LOCK_TTL_SECONDS,
) -> str | None:
    """Acquire an exact interrupt-resume lock and return its owner token."""
    ttl = _positive_ttl(ttl_seconds, DEFAULT_RESUME_LOCK_TTL_SECONDS, "Resume lock")
    key = _resume_lock_key(user_id, session_id, trace_id)
    owner_token = uuid.uuid4().hex
    redis = await get_redis()
    acquired = await redis.set(key, owner_token, nx=True, ex=ttl)
    return owner_token if acquired else None


async def release_task_resume_lock(
    user_id: int,
    session_id: str,
    trace_id: str,
    owner_token: str,
) -> bool:
    """Release a resume lock only when the caller still owns its token."""
    _validate_owner_token(owner_token, "resume-lock owner token")
    redis = await get_redis()
    result = await redis.eval(
        _COMPARE_DELETE_SCRIPT,
        1,
        _resume_lock_key(user_id, session_id, trace_id),
        str(owner_token),
    )
    return bool(_eval_code(result))


async def claim_active_trace_lease(
    user_id: int,
    session_id: str,
    trace_id: str,
    ttl_seconds: int | None = None,
) -> dict[str, Any] | None:
    """Atomically claim the sole active trace for a session.

    The claim includes the first runner reservation in ``starting`` state.
    ``None`` means another active trace already owns the session.
    """
    _validate_identity(user_id, session_id, trace_id)
    ttl = _positive_ttl(ttl_seconds, DEFAULT_ACTIVE_LEASE_TTL_SECONDS, "Active lease")
    lease_token = uuid.uuid4().hex
    runner_token = uuid.uuid4().hex
    redis = await get_redis()
    result = await redis.eval(
        _CLAIM_ACTIVE_TRACE_SCRIPT,
        2,
        _active_lease_key(user_id, session_id),
        _active_fence_key(user_id, session_id),
        str(user_id),
        session_id,
        trace_id,
        lease_token,
        runner_token,
        _utc_now_iso(),
        str(ttl),
    )
    if _eval_code(result) != 1:
        return None
    return await get_active_trace_lease(user_id, session_id)


async def get_active_trace_lease(
    user_id: int,
    session_id: str,
) -> dict[str, Any] | None:
    """Read and validate the Redis-authoritative active-session lease."""
    redis = await get_redis()
    raw = await redis.hgetall(_active_lease_key(user_id, session_id))
    if not raw:
        return None
    payload = _decode_hash(raw)
    if payload.get("user_id") != str(user_id) or payload.get("session_id") != session_id:
        raise ValueError("Active-trace lease identity mismatch")
    trace_id = payload.get("trace_id", "")
    _validate_identity(user_id, session_id, trace_id)
    _validate_owner_token(payload.get("lease_token", ""), "lease token")
    _validate_owner_token(payload.get("runner_token", ""), "runner token")
    if payload.get("runner_state") not in {"starting", "running", "stopped"}:
        raise ValueError("Invalid active-trace runner state")
    fence = _as_int(payload.get("fence"))
    runner_epoch = _as_int(payload.get("runner_epoch"))
    if fence <= 0 or runner_epoch <= 0:
        raise ValueError("Invalid active-trace fencing identity")
    return {
        **payload,
        "user_id": user_id,
        "fence": fence,
        "runner_epoch": runner_epoch,
        "cancel_requested": payload.get("cancel_requested") == "1",
    }


async def owns_active_trace_lease(
    user_id: int,
    session_id: str,
    trace_id: str,
    lease_token: str,
) -> bool:
    """Return whether the exact trace/token pair owns the session lease."""
    _validate_identity(user_id, session_id, trace_id)
    _validate_owner_token(lease_token, "lease token")
    lease = await get_active_trace_lease(user_id, session_id)
    return bool(
        lease
        and lease.get("trace_id") == trace_id
        and lease.get("lease_token") == lease_token
    )


async def owns_active_trace_runner(
    user_id: int,
    session_id: str,
    trace_id: str,
    lease_token: str,
    runner_token: str,
) -> bool:
    """Return whether a runner token is the current fenced runner."""
    _validate_identity(user_id, session_id, trace_id)
    _validate_owner_token(lease_token, "lease token")
    _validate_owner_token(runner_token, "runner token")
    lease = await get_active_trace_lease(user_id, session_id)
    return bool(
        lease
        and lease.get("trace_id") == trace_id
        and lease.get("lease_token") == lease_token
        and lease.get("runner_token") == runner_token
        and lease.get("runner_state") in {"starting", "running"}
    )


async def renew_active_trace_lease(
    user_id: int,
    session_id: str,
    trace_id: str,
    lease_token: str,
    ttl_seconds: int | None = None,
) -> bool:
    """Refresh an exact lease without allowing a stale owner to revive it."""
    _validate_identity(user_id, session_id, trace_id)
    _validate_owner_token(lease_token, "lease token")
    ttl = _positive_ttl(ttl_seconds, DEFAULT_ACTIVE_LEASE_TTL_SECONDS, "Active lease")
    redis = await get_redis()
    result = await redis.eval(
        _RENEW_LEASE_SCRIPT,
        1,
        _active_lease_key(user_id, session_id),
        trace_id,
        lease_token,
        _utc_now_iso(),
        str(ttl),
    )
    return _eval_code(result) == 1


async def renew_active_trace_runner(
    user_id: int,
    session_id: str,
    trace_id: str,
    lease_token: str,
    runner_token: str,
    ttl_seconds: int | None = None,
) -> bool:
    """Heartbeat only the current starting/running runner reservation."""
    _validate_identity(user_id, session_id, trace_id)
    _validate_owner_token(lease_token, "lease token")
    _validate_owner_token(runner_token, "runner token")
    ttl = _positive_ttl(ttl_seconds, DEFAULT_ACTIVE_LEASE_TTL_SECONDS, "Active lease")
    redis = await get_redis()
    result = await redis.eval(
        _RENEW_RUNNER_SCRIPT,
        1,
        _active_lease_key(user_id, session_id),
        trace_id,
        lease_token,
        runner_token,
        _utc_now_iso(),
        str(ttl),
    )
    return _eval_code(result) == 1


async def reserve_active_trace_runner(
    user_id: int,
    session_id: str,
    trace_id: str,
    lease_token: str,
    ttl_seconds: int | None = None,
) -> dict[str, Any] | None:
    """Fence a new runner for a stopped, still-active trace (for HITL resume)."""
    _validate_identity(user_id, session_id, trace_id)
    _validate_owner_token(lease_token, "lease token")
    ttl = _positive_ttl(ttl_seconds, DEFAULT_ACTIVE_LEASE_TTL_SECONDS, "Active lease")
    runner_token = uuid.uuid4().hex
    redis = await get_redis()
    result = await redis.eval(
        _RESERVE_RUNNER_SCRIPT,
        2,
        _active_lease_key(user_id, session_id),
        _cancel_key(user_id, session_id, trace_id),
        trace_id,
        lease_token,
        runner_token,
        _utc_now_iso(),
        str(ttl),
    )
    if _eval_code(result) < 2:
        return None
    return await get_active_trace_lease(user_id, session_id)


async def start_active_trace_runner(
    user_id: int,
    session_id: str,
    trace_id: str,
    lease_token: str,
    runner_token: str,
    ttl_seconds: int | None = None,
) -> bool:
    """Atomically move a runner from starting to running unless Stop won."""
    _validate_identity(user_id, session_id, trace_id)
    _validate_owner_token(lease_token, "lease token")
    _validate_owner_token(runner_token, "runner token")
    ttl = _positive_ttl(ttl_seconds, DEFAULT_ACTIVE_LEASE_TTL_SECONDS, "Active lease")
    redis = await get_redis()
    result = await redis.eval(
        _START_RUNNER_SCRIPT,
        2,
        _active_lease_key(user_id, session_id),
        _cancel_key(user_id, session_id, trace_id),
        trace_id,
        lease_token,
        runner_token,
        _utc_now_iso(),
        str(ttl),
    )
    return _eval_code(result) == 1


async def mark_active_trace_runner_stopped(
    user_id: int,
    session_id: str,
    trace_id: str,
    lease_token: str,
    runner_token: str,
    reason: str | None = None,
    ttl_seconds: int | None = None,
) -> bool:
    """Mark only the currently fenced runner stopped; stale runners are ignored."""
    _validate_identity(user_id, session_id, trace_id)
    _validate_owner_token(lease_token, "lease token")
    _validate_owner_token(runner_token, "runner token")
    ttl = _positive_ttl(ttl_seconds, DEFAULT_ACTIVE_LEASE_TTL_SECONDS, "Active lease")
    redis = await get_redis()
    result = await redis.eval(
        _STOP_RUNNER_SCRIPT,
        1,
        _active_lease_key(user_id, session_id),
        trace_id,
        lease_token,
        runner_token,
        _utc_now_iso(),
        str(reason or "runner_stopped")[:500],
        str(ttl),
    )
    return _eval_code(result) == 1


async def release_active_trace_lease(
    user_id: int,
    session_id: str,
    trace_id: str,
    lease_token: str,
) -> LeaseReleaseStatus:
    """Release an exact stopped lease; running/foreign leases remain fenced."""
    _validate_identity(user_id, session_id, trace_id)
    _validate_owner_token(lease_token, "lease token")
    redis = await get_redis()
    result = await redis.eval(
        _RELEASE_LEASE_SCRIPT,
        1,
        _active_lease_key(user_id, session_id),
        trace_id,
        lease_token,
    )
    code = _eval_code(result)
    return {
        2: "missing",
        1: "released",
        0: "not_owner",
        -1: "runner_active",
    }.get(code, "not_owner")


async def request_trace_cancellation(
    user_id: int,
    session_id: str,
    trace_id: str,
    reason: str = "Cancelled by user",
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    """Persist Stop and atomically fence a runner that has not started yet."""
    _validate_identity(user_id, session_id, trace_id)
    cancel_ttl = _positive_ttl(ttl_seconds, DEFAULT_CANCEL_TTL_SECONDS, "Cancellation")
    now = _utc_now_iso()
    payload = {
        "user_id": user_id,
        "session_id": session_id,
        "trace_id": trace_id,
        "requested_at": now,
        "reason": str(reason or "Cancelled by user")[:500],
    }
    redis = await get_redis()
    result = await redis.eval(
        _REQUEST_CANCEL_SCRIPT,
        2,
        _active_lease_key(user_id, session_id),
        _cancel_key(user_id, session_id, trace_id),
        trace_id,
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        str(cancel_ttl),
        now,
        str(DEFAULT_ACTIVE_LEASE_TTL_SECONDS),
    )
    code = _eval_code(result)
    status = {
        -1: "stale",
        0: "missing",
        1: "requested",
        2: "cancelled_before_start",
    }.get(code, "stale")
    if status == "stale":
        return {**payload, "status": status, "created": False}
    stored = await get_trace_cancel_request(user_id, session_id, trace_id)
    return {**(stored or payload), "status": status, "created": _eval_created(result)}


async def get_trace_cancel_request(
    user_id: int,
    session_id: str,
    trace_id: str,
) -> dict[str, Any] | None:
    """Read and validate one exact trace cancellation tombstone."""
    redis = await get_redis()
    raw = await redis.get(_cancel_key(user_id, session_id, trace_id))
    return _decode_cancel_request(
        raw,
        user_id=user_id,
        session_id=session_id,
        trace_id=trace_id,
    )


async def is_trace_cancel_requested(
    user_id: int,
    session_id: str,
    trace_id: str,
) -> bool:
    """Return whether Stop exists for one exact trace."""
    return await get_trace_cancel_request(user_id, session_id, trace_id) is not None


async def is_current_task_cancel_requested() -> bool:
    """Check Redis cancellation for the task bound to the current context."""
    identity = get_current_task_control_identity()
    if identity is None:
        return False
    return await is_trace_cancel_requested(*identity)


async def clear_legacy_pause_key(user_id: int, session_id: str, trace_id: str) -> bool:
    """Delete one exact retired ``agent:pause`` control key."""
    redis = await get_redis()
    return bool(await redis.delete(_legacy_pause_key(user_id, session_id, trace_id)))


async def scan_legacy_pause_keys(batch_size: int = 100) -> list[str]:
    """Return legacy pause keys using SCAN, never a blocking Redis KEYS call."""
    batch_size = _positive_ttl(batch_size, 100, "Pause scan batch")
    redis = await get_redis()
    keys = []
    async for key in redis.scan_iter(match="agent:pause:*", count=batch_size):
        keys.append(_as_text(key))
    return sorted(keys)


async def set_user_pause_protocol_retired(
    reason: str = "user_pause_feature_retired",
) -> dict[str, Any]:
    """Set the cluster-wide, durable marker that rejects new user Pause calls."""
    payload = {
        "retired": True,
        "retired_at": _utc_now_iso(),
        "reason": str(reason or "user_pause_feature_retired")[:500],
    }
    redis = await get_redis()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    created = bool(await redis.set(USER_PAUSE_PROTOCOL_RETIRED_KEY, encoded, nx=True))
    stored = await redis.get(USER_PAUSE_PROTOCOL_RETIRED_KEY)
    try:
        result = json.loads(_as_text(stored)) if stored is not None else payload
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid user-pause retirement marker") from exc
    return {**result, "created": created}


async def is_user_pause_protocol_retired() -> bool:
    """Return whether the cluster retirement marker exists."""
    redis = await get_redis()
    return bool(await redis.exists(USER_PAUSE_PROTOCOL_RETIRED_KEY))


__all__ = [
    "acquire_task_resume_lock",
    "claim_active_trace_lease",
    "clear_legacy_pause_key",
    "DEFAULT_ACTIVE_LEASE_TTL_SECONDS",
    "DEFAULT_CANCEL_TTL_SECONDS",
    "DEFAULT_RESUME_LOCK_TTL_SECONDS",
    "get_active_trace_lease",
    "get_current_task_control_identity",
    "get_current_task_runner_identity",
    "get_trace_cancel_request",
    "is_current_task_cancel_requested",
    "is_current_task_cancel_requested_sync",
    "is_trace_cancel_requested",
    "is_user_pause_protocol_retired",
    "LeaseReleaseStatus",
    "mark_active_trace_runner_stopped",
    "owns_active_trace_lease",
    "owns_active_trace_runner",
    "owns_current_task_runner",
    "release_active_trace_lease",
    "release_task_resume_lock",
    "renew_active_trace_lease",
    "renew_active_trace_runner",
    "request_trace_cancellation",
    "reserve_active_trace_runner",
    "reset_current_task_control_identity",
    "reset_current_task_runner_identity",
    "scan_legacy_pause_keys",
    "set_current_task_control_identity",
    "set_current_task_runner_identity",
    "set_user_pause_protocol_retired",
    "start_active_trace_runner",
    "USER_PAUSE_PROTOCOL_RETIRED_KEY",
]
