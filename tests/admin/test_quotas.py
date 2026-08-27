from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from enterprise_agent.admin import quotas


class FakeTraceStore:
    def __init__(self, traces):
        self.traces = traces

    def list_traces(self, _user_id, limit=500):
        return self.traces[:limit]


class PerUserTraceStore:
    def __init__(self, traces_by_user):
        self.traces_by_user = traces_by_user

    def list_traces(self, user_id, limit=500):
        return self.traces_by_user.get(user_id, [])[:limit]


class FakeRedis:
    def __init__(self, initial=0):
        self.value = initial
        self.deleted = False

    async def incr(self, _key):
        self.value += 1
        return self.value

    async def decr(self, _key):
        self.value -= 1
        return self.value

    async def expire(self, _key, _seconds):
        return True

    async def delete(self, _key):
        self.deleted = True


class FakeDb:
    def __init__(self, quota, usage_row=None):
        self.quota = quota
        self.usage_row = usage_row
        self.added = None
        self.commits = 0
        self.rollbacks = 0

    async def get(self, _model, _user_id):
        return self.quota

    async def scalar(self, _query):
        return self.usage_row

    def add(self, value):
        self.added = value

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class PerUserFakeDb(FakeDb):
    def __init__(self, quotas_by_user):
        super().__init__(quota=None)
        self.quotas_by_user = quotas_by_user

    async def get(self, _model, user_id):
        return self.quotas_by_user.get(user_id)


def make_quota(**overrides):
    values = {
        "daily_task_limit": 50,
        "daily_token_limit": 500_000,
        "monthly_token_limit": 5_000_000,
        "concurrent_task_limit": 2,
        "enabled": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_period_usage_uses_utc_day_and_month():
    traces = [
        {"started_at": "2026-07-21T00:30:00+00:00", "metrics": {"total_tokens": 120}},
        {"started_at": "2026-07-20T23:30:00+00:00", "metrics": {"total_tokens": 80}},
        {"started_at": "2026-06-30T23:30:00+00:00", "metrics": {"total_tokens": 40}},
    ]

    usage = quotas.period_usage_from_traces(
        traces,
        now=datetime(2026, 7, 21, 8, tzinfo=timezone.utc),
    )

    assert usage == {"daily_tasks": 1, "daily_tokens": 120, "monthly_tokens": 200}


@pytest.mark.asyncio
async def test_daily_token_limit_rejects_before_reserving_redis(monkeypatch):
    trace = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "metrics": {"total_tokens": 1_000},
    }
    monkeypatch.setattr(quotas, "get_trace_store", lambda: FakeTraceStore([trace]))

    async def redis_must_not_be_called():
        raise AssertionError("Redis should not be reserved after a period limit is exceeded")

    monkeypatch.setattr(quotas, "get_redis", redis_must_not_be_called)
    db = FakeDb(make_quota(daily_token_limit=1_000))

    with pytest.raises(HTTPException) as exc_info:
        await quotas.acquire_task_quota(7, db)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["code"] == "daily_token_limit_exceeded"


@pytest.mark.asyncio
async def test_concurrent_limit_releases_rejected_reservation(monkeypatch):
    redis = FakeRedis(initial=1)
    monkeypatch.setattr(quotas, "get_trace_store", lambda: FakeTraceStore([]))

    async def get_fake_redis():
        return redis

    monkeypatch.setattr(quotas, "get_redis", get_fake_redis)
    db = FakeDb(make_quota(concurrent_task_limit=1))

    with pytest.raises(HTTPException) as exc_info:
        await quotas.acquire_task_quota(7, db)

    assert exc_info.value.detail["code"] == "concurrent_task_limit_exceeded"
    assert redis.value == 1
    assert db.commits == 0


@pytest.mark.asyncio
async def test_disabled_metering_still_reserves_and_releases_concurrent_slot(monkeypatch):
    redis = FakeRedis()

    def traces_must_not_be_read():
        raise AssertionError("Periodic traces should not be read when metering is disabled")

    monkeypatch.setattr(quotas, "get_trace_store", traces_must_not_be_read)

    async def get_fake_redis():
        return redis

    monkeypatch.setattr(quotas, "get_redis", get_fake_redis)
    db = FakeDb(make_quota(enabled=False))

    lease = await quotas.acquire_task_quota(1, db)

    assert redis.value == 1
    assert db.added is None
    assert db.commits == 0
    await lease.release()
    assert redis.value == 0
    assert redis.deleted is True


@pytest.mark.asyncio
async def test_disabled_metering_does_not_disable_concurrent_limit(monkeypatch):
    redis = FakeRedis(initial=1)

    def traces_must_not_be_read():
        raise AssertionError("Periodic traces should not be read when metering is disabled")

    monkeypatch.setattr(quotas, "get_trace_store", traces_must_not_be_read)

    async def get_fake_redis():
        return redis

    monkeypatch.setattr(quotas, "get_redis", get_fake_redis)
    db = FakeDb(make_quota(enabled=False, concurrent_task_limit=1))

    with pytest.raises(HTTPException) as exc_info:
        await quotas.acquire_task_quota(1, db)

    assert exc_info.value.detail["code"] == "concurrent_task_limit_exceeded"
    assert redis.value == 1
    assert db.commits == 0


@pytest.mark.asyncio
async def test_metering_exemption_is_scoped_to_exact_user_id(monkeypatch):
    today = datetime.now(timezone.utc).isoformat()
    store = PerUserTraceStore({
        1: [{"started_at": today, "metrics": {"total_tokens": 2_000}}],
        2: [{"started_at": today, "metrics": {"total_tokens": 2_000}}],
    })
    redis = FakeRedis()
    monkeypatch.setattr(quotas, "get_trace_store", lambda: store)

    async def get_fake_redis():
        return redis

    monkeypatch.setattr(quotas, "get_redis", get_fake_redis)
    db = PerUserFakeDb({
        1: make_quota(enabled=False, daily_token_limit=1_000),
        2: make_quota(enabled=True, daily_token_limit=1_000),
    })

    exempt_lease = await quotas.acquire_task_quota(1, db)
    assert redis.value == 1
    await exempt_lease.release()

    with pytest.raises(HTTPException) as exc_info:
        await quotas.acquire_task_quota(2, db)

    assert exc_info.value.detail["code"] == "daily_token_limit_exceeded"
    assert redis.value == 0


@pytest.mark.asyncio
async def test_successful_lease_settles_task_and_releases_once(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(quotas, "get_trace_store", lambda: FakeTraceStore([]))

    async def get_fake_redis():
        return redis

    monkeypatch.setattr(quotas, "get_redis", get_fake_redis)
    db = FakeDb(make_quota())

    lease = await quotas.acquire_task_quota(7, db)

    assert db.added.task_count == 1
    assert db.commits == 1
    assert redis.value == 1
    await lease.release()
    await lease.release()
    assert redis.value == 0
    assert redis.deleted is True
