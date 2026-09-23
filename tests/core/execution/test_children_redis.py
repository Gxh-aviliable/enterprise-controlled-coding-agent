"""Real Redis/Lua integration. Run only against an explicitly provided test instance."""

import asyncio
import json
import os
import time
import uuid

import pytest
from redis.asyncio import Redis

from enterprise_agent.core.execution import child_store, children
from enterprise_agent.core.execution import interrupt_control as control
from tests.core.execution.test_children import Model, call, scope  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis_scope(scope, monkeypatch):  # noqa: F811
    url = os.getenv("CHILD_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set CHILD_TEST_REDIS_URL to a dedicated disposable Redis instance")
    client = Redis.from_url(url, decode_responses=True)
    await client.ping()

    async def get_client():
        return client

    monkeypatch.setattr(child_store, "get_redis", get_client)
    monkeypatch.setattr(control, "get_redis", get_client)
    state = dict(scope, session_id="test-" + uuid.uuid4().hex)
    lease = await control.claim_active_trace_lease(state["user_id"], state["session_id"], state["trace_id"])
    runner = (state["user_id"], state["session_id"], state["trace_id"], lease["lease_token"], lease["runner_token"])
    await control.start_active_trace_runner(*runner)
    yield client, state, runner
    await client.aclose()


async def test_real_lua_idempotency_and_fenced_late_result(redis_scope):
    client, state, runner = redis_scope
    store = child_store.ChildStore("test-" + uuid.uuid4().hex, runner)
    payload = {
        "runner": runner,
        "deadline": time.time() + 10,
        "owner": runner[-1],
        "request_hash": "test",
        "initial_result": {"status": "failed"},
    }
    reserved = await asyncio.gather(store.reserve(payload), store.reserve(payload))
    assert sum(r is None for r in reserved) == 1
    await client.hset(f"agent:active-session:{runner[0]}:{runner[1]}", "runner_token", "new-owner")
    with pytest.raises(RuntimeError):
        await store.finish(dict(payload, result={"status": "succeeded"}))
    assert not json.loads(await client.get(store.key)).get("result")
    await child_store.recover_abandoned_children()
    assert json.loads(await client.get(store.key))["result"]["status"] == "interrupted"


async def test_real_cancellation_wins_success_commit(redis_scope):
    client, state, runner = redis_scope
    store = child_store.ChildStore("test-" + uuid.uuid4().hex, runner)
    payload = {
        "runner": runner,
        "deadline": time.time() + 10,
        "owner": runner[-1],
        "request_hash": "test",
        "initial_result": {"status": "failed"},
    }
    await store.reserve(payload)
    await control.request_trace_cancellation(runner[0], runner[1], runner[2])
    assert await store.finish(dict(payload, result={"status": "succeeded"})) is True
    assert json.loads(await client.get(store.key))["result"]["status"] == "cancelled"


async def test_real_runtime_replay_uses_receipt(redis_scope, monkeypatch):
    client, state, runner = redis_scope
    token = control.set_current_task_runner_identity(*runner)
    try:
        first = await children.run_batch(state, [call()])
        assert first["d1"]["status"] == "succeeded"
        monkeypatch.setattr(children, "get_llm", lambda: pytest.fail("must not repeat model call"))
        second = await children.run_batch(state, [call()])
        assert second == first
        assert json.loads(await client.get("agent:child:" + first["d1"]["child_id"]))["result"] == first["d1"]
    finally:
        control.reset_current_task_runner_identity(token)


async def test_real_stop_does_not_cancel_other_session(redis_scope, monkeypatch):
    client, state, runner = redis_scope
    other = dict(state, session_id=state["session_id"] + "-other")
    lease = await control.claim_active_trace_lease(other["user_id"], other["session_id"], other["trace_id"])
    other_runner = (
        other["user_id"],
        other["session_id"],
        other["trace_id"],
        lease["lease_token"],
        lease["runner_token"],
    )
    await control.start_active_trace_runner(*other_runner)
    entered = asyncio.Event()

    class Slow(Model):
        async def ainvoke(self, messages, config=None):
            entered.set()
            await asyncio.sleep(0.3)
            return await super().ainvoke(messages, config)

    monkeypatch.setattr(children, "get_llm", Slow)

    async def start(s, r):
        token = control.set_current_task_runner_identity(*r)
        try:
            return await children.run_batch(s, [call()])
        finally:
            control.reset_current_task_runner_identity(token)

    task_a = asyncio.create_task(start(state, runner))
    task_b = asyncio.create_task(start(other, other_runner))
    await entered.wait()
    await control.request_trace_cancellation(runner[0], runner[1], runner[2])
    a, b = await asyncio.gather(task_a, task_b)
    assert a["d1"]["status"] == "cancelled"
    assert b["d1"]["status"] == "succeeded"
    assert a["d1"]["child_id"] != b["d1"]["child_id"]


async def test_online_store_outage_has_no_file_fallback(redis_scope, monkeypatch, tmp_path):
    client, state, runner = redis_scope

    async def unavailable():
        raise ConnectionError("test Redis outage")

    monkeypatch.setattr(child_store, "get_redis", unavailable)
    token = control.set_current_task_runner_identity(*runner)
    try:
        result = await children.run_batch(state, [call()])
        assert result["d1"]["status"] == "failed"
        assert not list(tmp_path.rglob(".child-tasks"))
    finally:
        control.reset_current_task_runner_identity(token)


async def test_restart_recovery_preserves_live_worker_and_terminal_receipts(redis_scope):
    client, state, runner = redis_scope
    store = child_store.ChildStore('test-' + uuid.uuid4().hex, runner)
    payload = {'runner': runner, 'deadline': time.time() + 30, 'owner': runner[-1],
               'request_hash': 'test', 'initial_result': {'status': 'failed'}}
    await store.reserve(payload)
    await child_store.recover_abandoned_children()
    assert not json.loads(await client.get(store.key)).get('result')
    await client.hset(f'agent:active-session:{runner[0]}:{runner[1]}', 'runner_state', 'stopped')
    await child_store.recover_abandoned_children()
    assert json.loads(await client.get(store.key))['result']['status'] == 'interrupted'
    await child_store.recover_abandoned_children()
    assert json.loads(await client.get(store.key))['result']['status'] == 'interrupted'
