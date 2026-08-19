"""Redis-authoritative interrupt control and fencing tests."""

import fnmatch
import json

import pytest

from enterprise_agent.core.execution import interrupt_control


class FakeRedis:
    """Small Lua-aware fake covering the production control contracts."""

    def __init__(self):
        self.values = {}
        self.hashes = {}
        self.expirations = {}
        self.counters = {}

    def _exists(self, key):
        return key in self.values or key in self.hashes

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and self._exists(key):
            return False
        self.values[key] = value
        self.expirations[key] = ex
        return True

    async def get(self, key):
        return self.values.get(key)

    async def exists(self, key):
        return int(self._exists(key))

    async def delete(self, key):
        existed = self._exists(key)
        self.values.pop(key, None)
        self.hashes.pop(key, None)
        self.expirations.pop(key, None)
        return int(existed)

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def scan_iter(self, *, match, count):
        del count
        keys = sorted(set(self.values) | set(self.hashes))
        for key in keys:
            if fnmatch.fnmatch(key, match):
                yield key

    async def eval(self, script, numkeys, *parts):
        keys = list(parts[:numkeys])
        args = list(parts[numkeys:])
        if "interrupt-control:compare-delete" in script:
            key = keys[0]
            if self.values.get(key) == args[0]:
                await self.delete(key)
                return 1
            return 0
        if "interrupt-control:claim-active-trace" in script:
            active_key, fence_key = keys
            if self._exists(active_key):
                return [0, self.hashes[active_key].get("trace_id", "")]
            fence = self.counters.get(fence_key, 0) + 1
            self.counters[fence_key] = fence
            self.hashes[active_key] = {
                "user_id": args[0],
                "session_id": args[1],
                "trace_id": args[2],
                "lease_token": args[3],
                "fence": str(fence),
                "runner_token": args[4],
                "runner_epoch": "1",
                "runner_state": "starting",
                "cancel_requested": "0",
                "created_at": args[5],
                "updated_at": args[5],
            }
            self.expirations[active_key] = int(args[6])
            return [1, str(fence)]
        if "interrupt-control:reserve-runner" in script:
            active_key, cancel_key = keys
            if active_key not in self.hashes:
                return -1
            active = self.hashes[active_key]
            if active.get("trace_id") != args[0] or active.get("lease_token") != args[1]:
                return 0
            if active.get("runner_state") != "stopped":
                return -2
            if active.get("cancel_requested") == "1" or self._exists(cancel_key):
                return -3
            epoch = int(active["runner_epoch"]) + 1
            active.update({
                "runner_epoch": str(epoch),
                "runner_token": args[2],
                "runner_state": "starting",
                "updated_at": args[3],
            })
            active.pop("stopped_at", None)
            active.pop("stop_reason", None)
            self.expirations[active_key] = int(args[4])
            return epoch
        if "interrupt-control:start-runner" in script:
            active_key, cancel_key = keys
            if active_key not in self.hashes:
                return -1
            active = self.hashes[active_key]
            if (
                active.get("trace_id") != args[0]
                or active.get("lease_token") != args[1]
                or active.get("runner_token") != args[2]
            ):
                return 0
            if active.get("runner_state") != "starting":
                return -2
            if active.get("cancel_requested") == "1" or self._exists(cancel_key):
                active.update({
                    "runner_state": "stopped",
                    "stopped_at": args[3],
                    "stop_reason": "cancelled_before_start",
                    "updated_at": args[3],
                })
                self.expirations[active_key] = int(args[4])
                return -3
            active.update({"runner_state": "running", "updated_at": args[3]})
            self.expirations[active_key] = int(args[4])
            return 1
        if "interrupt-control:stop-runner" in script:
            active_key = keys[0]
            if active_key not in self.hashes:
                return -1
            active = self.hashes[active_key]
            if (
                active.get("trace_id") != args[0]
                or active.get("lease_token") != args[1]
                or active.get("runner_token") != args[2]
            ):
                return 0
            active.update({
                "runner_state": "stopped",
                "stopped_at": args[3],
                "stop_reason": args[4],
                "updated_at": args[3],
            })
            self.expirations[active_key] = int(args[5])
            return 1
        if "interrupt-control:renew-lease" in script:
            active_key = keys[0]
            active = self.hashes.get(active_key)
            if not active:
                return 0
            if active.get("trace_id") != args[0] or active.get("lease_token") != args[1]:
                return 0
            active["updated_at"] = args[2]
            self.expirations[active_key] = int(args[3])
            return 1
        if "interrupt-control:renew-runner" in script:
            active_key = keys[0]
            active = self.hashes.get(active_key)
            if not active:
                return 0
            if (
                active.get("trace_id") != args[0]
                or active.get("lease_token") != args[1]
                or active.get("runner_token") != args[2]
                or active.get("runner_state") not in {"starting", "running"}
            ):
                return 0
            active["updated_at"] = args[3]
            self.expirations[active_key] = int(args[4])
            return 1
        if "interrupt-control:request-cancel" in script:
            active_key, cancel_key = keys
            active = self.hashes.get(active_key)
            if active and active.get("trace_id") != args[0]:
                return [-1, 0]
            created = int(not self._exists(cancel_key))
            if created:
                self.values[cancel_key] = args[1]
            self.expirations[cancel_key] = int(args[2])
            if not active:
                return [0, created]
            active.update({
                "cancel_requested": "1",
                "cancel_requested_at": args[3],
                "updated_at": args[3],
            })
            self.expirations[active_key] = int(args[4])
            if active.get("runner_state") == "starting":
                active.update({
                    "runner_state": "stopped",
                    "stopped_at": args[3],
                    "stop_reason": "cancelled_before_start",
                })
                return [2, created]
            return [1, created]
        if "interrupt-control:release-lease" in script:
            active_key = keys[0]
            active = self.hashes.get(active_key)
            if not active:
                return 2
            if active.get("trace_id") != args[0] or active.get("lease_token") != args[1]:
                return 0
            if active.get("runner_state") != "stopped":
                return -1
            await self.delete(active_key)
            return 1
        raise AssertionError("Unexpected interrupt-control Lua script")


class FakeSyncRedis:
    def __init__(self, redis):
        self.redis = redis

    def get(self, key):
        return self.redis.values.get(key)


@pytest.fixture
def fake_redis(monkeypatch):
    redis = FakeRedis()

    async def get_fake_redis():
        return redis

    monkeypatch.setattr(interrupt_control, "get_redis", get_fake_redis)
    monkeypatch.setattr(interrupt_control, "_get_sync_redis", lambda: FakeSyncRedis(redis))
    return redis


async def test_resume_lock_is_owned_by_an_exact_token(fake_redis):
    owner = await interrupt_control.acquire_task_resume_lock(
        7,
        "session-1",
        "trace-1",
        ttl_seconds=45,
    )

    assert owner
    assert await interrupt_control.acquire_task_resume_lock(
        7,
        "session-1",
        "trace-1",
        ttl_seconds=45,
    ) is None
    key = "agent:resume-lock:7:session-1:trace-1"
    assert fake_redis.expirations[key] == 45
    assert await interrupt_control.release_task_resume_lock(
        7,
        "session-1",
        "trace-1",
        "foreign-token",
    ) is False
    assert fake_redis.values[key] == owner
    assert await interrupt_control.release_task_resume_lock(7, "session-1", "trace-1", owner) is True
    assert await interrupt_control.release_task_resume_lock(7, "session-1", "trace-1", owner) is False


async def test_active_lease_claim_is_session_scoped_and_fenced(fake_redis):
    lease = await interrupt_control.claim_active_trace_lease(
        7,
        "session-1",
        "trace-1",
        ttl_seconds=90,
    )

    assert lease is not None
    assert lease["trace_id"] == "trace-1"
    assert lease["runner_state"] == "starting"
    assert lease["runner_epoch"] == 1
    assert lease["fence"] == 1
    assert fake_redis.expirations["agent:active-session:7:session-1"] == 90
    assert await interrupt_control.claim_active_trace_lease(7, "session-1", "trace-2") is None
    assert await interrupt_control.owns_active_trace_lease(
        7,
        "session-1",
        "trace-1",
        lease["lease_token"],
    ) is True
    assert await interrupt_control.owns_active_trace_lease(
        7,
        "session-1",
        "trace-1",
        "foreign-token",
    ) is False
    other_user_lease = await interrupt_control.claim_active_trace_lease(8, "session-1", "trace-2")
    assert other_user_lease is not None
    assert await interrupt_control.renew_active_trace_lease(
        7,
        "session-1",
        "trace-1",
        lease["lease_token"],
        ttl_seconds=120,
    ) is True
    assert fake_redis.expirations["agent:active-session:7:session-1"] == 120


async def test_runner_lifecycle_requires_exact_fencing_tokens(fake_redis):
    lease = await interrupt_control.claim_active_trace_lease(7, "session-1", "trace-1")
    assert lease is not None
    lease_token = lease["lease_token"]
    runner_token = lease["runner_token"]

    assert await interrupt_control.owns_active_trace_runner(
        7, "session-1", "trace-1", lease_token, runner_token
    ) is True
    assert await interrupt_control.start_active_trace_runner(
        7, "session-1", "trace-1", lease_token, runner_token
    ) is True
    assert await interrupt_control.start_active_trace_runner(
        7, "session-1", "trace-1", lease_token, runner_token
    ) is False
    assert await interrupt_control.renew_active_trace_runner(
        7,
        "session-1",
        "trace-1",
        lease_token,
        runner_token,
        ttl_seconds=180,
    ) is True
    assert await interrupt_control.release_active_trace_lease(
        7, "session-1", "trace-1", lease_token
    ) == "runner_active"
    assert await interrupt_control.mark_active_trace_runner_stopped(
        7,
        "session-1",
        "trace-1",
        lease_token,
        "foreign-runner",
    ) is False
    assert await interrupt_control.mark_active_trace_runner_stopped(
        7,
        "session-1",
        "trace-1",
        lease_token,
        runner_token,
        "completed",
    ) is True
    assert await interrupt_control.owns_active_trace_runner(
        7, "session-1", "trace-1", lease_token, runner_token
    ) is False
    assert await interrupt_control.release_active_trace_lease(
        7, "session-1", "trace-1", "foreign-token"
    ) == "not_owner"
    assert await interrupt_control.release_active_trace_lease(
        7, "session-1", "trace-1", lease_token
    ) == "released"
    assert await interrupt_control.release_active_trace_lease(
        7, "session-1", "trace-1", lease_token
    ) == "missing"


async def test_cancel_atomically_stops_a_starting_runner(fake_redis):
    lease = await interrupt_control.claim_active_trace_lease(7, "session-1", "trace-1")
    assert lease is not None

    requested = await interrupt_control.request_trace_cancellation(
        7,
        "session-1",
        "trace-1",
        reason="Stop now",
        ttl_seconds=333,
    )

    assert requested["status"] == "cancelled_before_start"
    assert requested["created"] is True
    current = await interrupt_control.get_active_trace_lease(7, "session-1")
    assert current is not None
    assert current["cancel_requested"] is True
    assert current["runner_state"] == "stopped"
    assert await interrupt_control.start_active_trace_runner(
        7,
        "session-1",
        "trace-1",
        lease["lease_token"],
        lease["runner_token"],
    ) is False
    assert await interrupt_control.reserve_active_trace_runner(
        7,
        "session-1",
        "trace-1",
        lease["lease_token"],
    ) is None
    cancel_key = "agent:cancel-requested:7:session-1:trace-1"
    assert fake_redis.expirations[cancel_key] == 333
    assert await interrupt_control.is_trace_cancel_requested(7, "session-1", "trace-1") is True
    assert await interrupt_control.is_trace_cancel_requested(7, "session-1", "trace-2") is False
    duplicate = await interrupt_control.request_trace_cancellation(
        7,
        "session-1",
        "trace-1",
        reason="A duplicate Stop",
    )
    assert duplicate["created"] is False
    assert duplicate["reason"] == "Stop now"
    assert await interrupt_control.release_active_trace_lease(
        7,
        "session-1",
        "trace-1",
        lease["lease_token"],
    ) == "released"
    assert await interrupt_control.is_trace_cancel_requested(7, "session-1", "trace-1") is True


async def test_running_cancel_waits_for_runner_to_stop_before_release(fake_redis):
    lease = await interrupt_control.claim_active_trace_lease(7, "session-1", "trace-1")
    assert lease is not None
    assert await interrupt_control.start_active_trace_runner(
        7,
        "session-1",
        "trace-1",
        lease["lease_token"],
        lease["runner_token"],
    ) is True

    requested = await interrupt_control.request_trace_cancellation(7, "session-1", "trace-1")

    assert requested["status"] == "requested"
    assert await interrupt_control.release_active_trace_lease(
        7,
        "session-1",
        "trace-1",
        lease["lease_token"],
    ) == "runner_active"
    assert await interrupt_control.mark_active_trace_runner_stopped(
        7,
        "session-1",
        "trace-1",
        lease["lease_token"],
        lease["runner_token"],
        "cancelled",
    ) is True
    assert await interrupt_control.release_active_trace_lease(
        7,
        "session-1",
        "trace-1",
        lease["lease_token"],
    ) == "released"


async def test_stale_cancel_cannot_touch_a_new_active_trace(fake_redis):
    assert await interrupt_control.claim_active_trace_lease(7, "session-1", "trace-new")

    stale = await interrupt_control.request_trace_cancellation(7, "session-1", "trace-old")

    assert stale["status"] == "stale"
    assert stale["created"] is False
    assert await interrupt_control.get_trace_cancel_request(7, "session-1", "trace-old") is None
    missing = await interrupt_control.request_trace_cancellation(7, "session-2", "trace-racing")
    assert missing["status"] == "missing"
    assert missing["created"] is True
    assert await interrupt_control.is_trace_cancel_requested(7, "session-2", "trace-racing") is True


async def test_stopped_trace_can_reserve_only_a_new_fenced_runner(fake_redis):
    lease = await interrupt_control.claim_active_trace_lease(7, "session-1", "trace-1")
    assert lease is not None
    old_runner = lease["runner_token"]
    assert await interrupt_control.mark_active_trace_runner_stopped(
        7,
        "session-1",
        "trace-1",
        lease["lease_token"],
        old_runner,
        "waiting_confirmation",
    ) is True

    resumed = await interrupt_control.reserve_active_trace_runner(
        7,
        "session-1",
        "trace-1",
        lease["lease_token"],
    )

    assert resumed is not None
    assert resumed["runner_epoch"] == 2
    assert resumed["runner_token"] != old_runner
    assert resumed["runner_state"] == "starting"
    assert await interrupt_control.start_active_trace_runner(
        7,
        "session-1",
        "trace-1",
        lease["lease_token"],
        old_runner,
    ) is False
    assert await interrupt_control.mark_active_trace_runner_stopped(
        7,
        "session-1",
        "trace-1",
        lease["lease_token"],
        old_runner,
    ) is False
    assert await interrupt_control.start_active_trace_runner(
        7,
        "session-1",
        "trace-1",
        lease["lease_token"],
        resumed["runner_token"],
    ) is True


async def test_context_bound_cancel_checks_are_exact_and_safe(fake_redis):
    assert interrupt_control.get_current_task_control_identity() is None
    assert await interrupt_control.is_current_task_cancel_requested() is False
    assert interrupt_control.is_current_task_cancel_requested_sync() is False
    token = interrupt_control.set_current_task_control_identity(7, "session-1", "trace-1")
    try:
        assert await interrupt_control.is_current_task_cancel_requested() is False
        assert interrupt_control.is_current_task_cancel_requested_sync() is False
        await interrupt_control.request_trace_cancellation(7, "session-1", "trace-1")
        assert await interrupt_control.is_current_task_cancel_requested() is True
        assert interrupt_control.is_current_task_cancel_requested_sync() is True
    finally:
        interrupt_control.reset_current_task_control_identity(token)
    assert interrupt_control.get_current_task_control_identity() is None


async def test_legacy_pause_key_migration_and_retirement_marker(fake_redis):
    fake_redis.values.update({
        "agent:pause:7:session-1:trace-1": "legacy-one",
        "agent:pause:8:session-2:trace-2": "legacy-two",
        "agent:other:key": "keep",
    })

    assert await interrupt_control.scan_legacy_pause_keys(batch_size=25) == [
        "agent:pause:7:session-1:trace-1",
        "agent:pause:8:session-2:trace-2",
    ]
    assert await interrupt_control.clear_legacy_pause_key(7, "session-1", "trace-1") is True
    assert await interrupt_control.clear_legacy_pause_key(7, "session-1", "trace-1") is False
    first = await interrupt_control.set_user_pause_protocol_retired()
    second = await interrupt_control.set_user_pause_protocol_retired("do-not-overwrite")
    assert first["created"] is True
    assert second["created"] is False
    assert second["reason"] == "user_pause_feature_retired"
    assert await interrupt_control.is_user_pause_protocol_retired() is True


@pytest.mark.parametrize(
    ("user_id", "session_id", "trace_id"),
    [
        (-1, "session", "trace"),
        (True, "session", "trace"),
        (1, "bad:session", "trace"),
        (1, 123, "trace"),
        (1, "session", "bad:trace"),
        (1, "session", None),
    ],
)
async def test_invalid_control_identity_is_rejected(fake_redis, user_id, session_id, trace_id):
    with pytest.raises(ValueError, match="Invalid interrupt-control"):
        await interrupt_control.claim_active_trace_lease(user_id, session_id, trace_id)


async def test_invalid_owner_tokens_and_malformed_leases_are_rejected(fake_redis):
    with pytest.raises(ValueError, match="owner token"):
        await interrupt_control.release_task_resume_lock(7, "session-1", "trace-1", "")
    with pytest.raises(ValueError, match="lease token"):
        await interrupt_control.renew_active_trace_lease(7, "session-1", "trace-1", "bad:token")

    fake_redis.hashes["agent:active-session:7:session-1"] = {
        "user_id": "7",
        "session_id": "session-1",
        "trace_id": "trace-1",
    }
    with pytest.raises(ValueError, match="lease token"):
        await interrupt_control.get_active_trace_lease(7, "session-1")


async def test_cancel_payload_identity_mismatch_is_rejected(fake_redis):
    key = "agent:cancel-requested:7:session-1:trace-1"
    fake_redis.values[key] = json.dumps({
        "user_id": 8,
        "session_id": "session-1",
        "trace_id": "trace-1",
    })

    with pytest.raises(ValueError, match="identity mismatch"):
        await interrupt_control.get_trace_cancel_request(7, "session-1", "trace-1")
