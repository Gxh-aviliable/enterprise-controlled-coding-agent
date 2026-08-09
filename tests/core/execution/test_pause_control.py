"""Redis pause-control identity, idempotency, and resume-lock tests."""

import json

import pytest

from enterprise_agent.core.execution import pause_control


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.expirations = {}

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.expirations[key] = ex
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        existed = key in self.values
        self.values.pop(key, None)
        self.expirations.pop(key, None)
        return int(existed)


@pytest.fixture
def fake_redis(monkeypatch):
    redis = FakeRedis()

    async def get_fake_redis():
        return redis

    monkeypatch.setattr(pause_control, "get_redis", get_fake_redis)
    return redis


async def test_pause_request_is_scoped_and_idempotent(fake_redis):
    first = await pause_control.request_task_pause(
        7,
        "session-1",
        "trace-1",
        "Need to inspect progress",
    )
    second = await pause_control.request_task_pause(
        7,
        "session-1",
        "trace-1",
        "A later duplicate reason",
    )

    assert first["created"] is True
    assert second["created"] is False
    assert second["requested_at"] == first["requested_at"]
    assert first["reason"] == "Need to inspect progress"
    stored = await pause_control.get_task_pause_request(7, "session-1", "trace-1")
    assert stored == {key: value for key, value in first.items() if key != "created"}
    key = "agent:pause:7:session-1:trace-1"
    assert fake_redis.expirations[key] >= 60


async def test_pause_requests_for_different_traces_do_not_collide(fake_redis):
    await pause_control.request_task_pause(7, "session-1", "trace-old")

    assert await pause_control.get_task_pause_request(7, "session-1", "trace-new") is None
    assert await pause_control.clear_task_pause_request(7, "session-1", "trace-new") is False
    assert await pause_control.get_task_pause_request(7, "session-1", "trace-old") is not None


async def test_pause_payload_identity_mismatch_is_rejected(fake_redis):
    key = "agent:pause:7:session-1:trace-1"
    fake_redis.values[key] = json.dumps({
        "user_id": 8,
        "session_id": "session-1",
        "trace_id": "trace-1",
    })

    with pytest.raises(ValueError, match="identity mismatch"):
        await pause_control.get_task_pause_request(7, "session-1", "trace-1")


async def test_clear_pause_request_is_idempotent(fake_redis):
    await pause_control.request_task_pause(7, "session-1", "trace-1")

    assert await pause_control.clear_task_pause_request(7, "session-1", "trace-1") is True
    assert await pause_control.clear_task_pause_request(7, "session-1", "trace-1") is False


async def test_resume_lock_allows_only_one_holder_and_expires(fake_redis):
    assert await pause_control.acquire_task_resume_lock(
        7, "session-1", "trace-1", ttl_seconds=45
    ) is True
    assert await pause_control.acquire_task_resume_lock(
        7, "session-1", "trace-1", ttl_seconds=45
    ) is False
    assert fake_redis.expirations["agent:resume-lock:7:session-1:trace-1"] == 45
    assert await pause_control.release_task_resume_lock(7, "session-1", "trace-1") is True
    assert await pause_control.acquire_task_resume_lock(
        7, "session-1", "trace-1", ttl_seconds=45
    ) is True


@pytest.mark.parametrize(
    ("user_id", "session_id", "trace_id"),
    [(-1, "session", "trace"), (1, "bad:session", "trace"), (1, "session", "bad:trace")],
)
async def test_invalid_control_identity_is_rejected(
    fake_redis,
    user_id,
    session_id,
    trace_id,
):
    with pytest.raises(ValueError, match="Invalid pause-control"):
        await pause_control.request_task_pause(user_id, session_id, trace_id)
