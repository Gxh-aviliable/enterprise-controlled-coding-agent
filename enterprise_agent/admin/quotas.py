"""Runtime quota enforcement for user-started Agent tasks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.config.settings import settings
from enterprise_agent.db.redis import get_redis
from enterprise_agent.models.admin import UserQuota, UserUsageDaily
from enterprise_agent.observability.trace_store import get_trace_store

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuotaLimits:
    daily_task_limit: int = 50
    daily_token_limit: int = 500_000
    monthly_token_limit: int = 5_000_000
    concurrent_task_limit: int = 2
    # Controls daily/monthly metering only; concurrency remains always-on.
    enabled: bool = True


class TaskQuotaLease:
    """One Redis-backed concurrent task slot that can be released once."""

    def __init__(self, redis: Any | None = None, key: str | None = None):
        self.redis = redis
        self.key = key
        self.released = False

    async def release(self) -> None:
        if self.released or not self.redis or not self.key:
            self.released = True
            return
        self.released = True
        try:
            remaining = await self.redis.decr(self.key)
            if int(remaining or 0) <= 0:
                await self.redis.delete(self.key)
        except Exception:
            # The key has a safety TTL, so a transient cleanup failure cannot
            # reserve capacity forever.
            logger.warning("Failed to release task quota lease for %s", self.key, exc_info=True)


def _parse_started_at(trace: dict[str, Any]) -> datetime | None:
    raw = trace.get("started_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def period_usage_from_traces(
    traces: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Calculate current UTC day/month usage from trace summaries."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    daily_tasks = 0
    daily_tokens = 0
    monthly_tokens = 0
    for trace in traces:
        started = _parse_started_at(trace)
        if not started or (started.year, started.month) != (current.year, current.month):
            continue
        tokens = int((trace.get("metrics") or {}).get("total_tokens") or 0)
        monthly_tokens += max(0, tokens)
        if started.date() == current.date():
            daily_tasks += 1
            daily_tokens += max(0, tokens)
    return {
        "daily_tasks": daily_tasks,
        "daily_tokens": daily_tokens,
        "monthly_tokens": monthly_tokens,
    }


def _limits_from_model(quota: UserQuota | None) -> QuotaLimits:
    if quota is None:
        return QuotaLimits()
    return QuotaLimits(
        daily_task_limit=quota.daily_task_limit,
        daily_token_limit=quota.daily_token_limit,
        monthly_token_limit=quota.monthly_token_limit,
        concurrent_task_limit=quota.concurrent_task_limit,
        enabled=quota.enabled,
    )


def _quota_error(code: str, message: str, *, limit: int, used: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={"code": code, "message": message, "limit": limit, "used": used},
        headers={"Retry-After": "60"},
    )


async def acquire_task_quota(user_id: int, db: AsyncSession) -> TaskQuotaLease:
    """Validate metered-usage limits and reserve one concurrent task slot.

    Daily accepted-task usage is settled in MySQL. Token usage is derived from
    persisted traces. Disabling a user's metered quota bypasses those periodic
    checks, but the concurrency guard always uses an atomic Redis counter with
    a crash-recovery TTL.
    """
    quota = await db.get(UserQuota, user_id)
    limits = _limits_from_model(quota)
    usage: dict[str, int] | None = None
    if limits.enabled:
        traces = get_trace_store().list_traces(user_id, limit=500)
        usage = period_usage_from_traces(traces)
        if usage["daily_tokens"] >= limits.daily_token_limit:
            raise _quota_error(
                "daily_token_limit_exceeded",
                "Daily token quota has been reached",
                limit=limits.daily_token_limit,
                used=usage["daily_tokens"],
            )
        if usage["monthly_tokens"] >= limits.monthly_token_limit:
            raise _quota_error(
                "monthly_token_limit_exceeded",
                "Monthly token quota has been reached",
                limit=limits.monthly_token_limit,
                used=usage["monthly_tokens"],
            )

    try:
        redis = await get_redis()
        concurrent_key = f"quota:concurrent:{user_id}"
        concurrent = int(await redis.incr(concurrent_key))
        if concurrent == 1:
            await redis.expire(
                concurrent_key,
                max(60, settings.AGENT_INVOKE_TIMEOUT_SECONDS + 60),
            )
    except Exception as exc:
        logger.warning("Redis quota reservation failed", exc_info=True)
        raise HTTPException(status_code=503, detail="Quota service is temporarily unavailable") from exc

    lease = TaskQuotaLease(redis, concurrent_key)
    if concurrent > limits.concurrent_task_limit:
        await lease.release()
        raise _quota_error(
            "concurrent_task_limit_exceeded",
            "Concurrent task quota has been reached",
            limit=limits.concurrent_task_limit,
            used=concurrent - 1,
        )

    # Metering can be disabled for a specific account, while concurrency
    # remains an operational safety boundary for every account.
    if not limits.enabled:
        return lease

    try:
        today = datetime.now(timezone.utc).date()
        usage_row = await db.scalar(
            select(UserUsageDaily)
            .where(UserUsageDaily.user_id == user_id, UserUsageDaily.usage_date == today)
            .with_for_update()
        )
        settled_tasks = int(usage_row.task_count if usage_row else 0)
        effective_tasks = max(settled_tasks, usage["daily_tasks"] if usage else 0)
        if effective_tasks >= limits.daily_task_limit:
            await db.rollback()
            await lease.release()
            raise _quota_error(
                "daily_task_limit_exceeded",
                "Daily task quota has been reached",
                limit=limits.daily_task_limit,
                used=effective_tasks,
            )
        if usage_row is None:
            usage_row = UserUsageDaily(
                user_id=user_id,
                usage_date=today,
                task_count=effective_tasks + 1,
            )
            db.add(usage_row)
        else:
            usage_row.task_count = effective_tasks + 1
        await db.commit()
    except HTTPException:
        raise
    except Exception:
        await db.rollback()
        await lease.release()
        raise

    return lease
