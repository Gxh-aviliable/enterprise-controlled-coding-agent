"""Idempotent child receipts. Online writes are fenced by the parent Redis lease."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from filelock import FileLock

from enterprise_agent.config.settings import settings
from enterprise_agent.db.redis import get_redis

# A different runner may reconcile abandoned work, never silently execute it again.
_RESERVE = """
local lease = KEYS[2]
if redis.call('HGET', lease, 'trace_id') ~= ARGV[1]
 or redis.call('HGET', lease, 'lease_token') ~= ARGV[2]
 or redis.call('HGET', lease, 'runner_token') ~= ARGV[3]
 or redis.call('HGET', lease, 'runner_state') == 'stopped'
 or redis.call('HGET', lease, 'cancel_requested') == '1' then return 'fenced' end
local old = redis.call('GET', KEYS[1])
if old then return old end
redis.call('SET', KEYS[1], ARGV[4], 'EX', ARGV[5])
return 'created'
"""
_FINISH = """
if redis.call('HGET', KEYS[2], 'trace_id') ~= ARGV[1]
 or redis.call('HGET', KEYS[2], 'lease_token') ~= ARGV[2]
 or redis.call('HGET', KEYS[2], 'runner_token') ~= ARGV[3]
 or redis.call('HGET', KEYS[2], 'runner_state') == 'stopped' then return 0 end
local old = redis.call('GET', KEYS[1])
if not old then return 0 end
local data = cjson.decode(old)
if data.result then return 0 end
local updated = cjson.decode(ARGV[4])
local cancelled = redis.call('HGET', KEYS[2], 'cancel_requested') == '1'
if cancelled and updated.result.status == 'succeeded' then
    return 2
end
redis.call('SET', KEYS[1], ARGV[4], 'EX', ARGV[5])
return 1
"""


class ChildStore:
    def __init__(self, child_id: str, runner: tuple | None):
        self.key = "agent:child:" + child_id
        self.runner = runner
        self.ttl = max(3600, settings.CHECKPOINT_TTL_HOURS * 3600)
        self.path: Path | None = None
        if runner is None:
            # Offline graph/benchmark adapter only. Online Redis errors propagate.
            from enterprise_agent.core.agent.tools.workspace import get_workspace_base

            root = get_workspace_base() / ".child-tasks"
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if root.is_symlink() or root.stat().st_mode & 0o022:
                raise RuntimeError("Unsafe offline child receipt directory")
            self.path = root / (child_id + ".json")

    async def reserve(self, payload: dict):
        if self.runner:
            user, session, trace, lease, owner = self.runner
            client = await get_redis()
            raw = await client.eval(
                _RESERVE,
                2,
                self.key,
                f"agent:active-session:{user}:{session}",
                trace,
                lease,
                owner,
                json.dumps(payload),
                self.ttl,
            )
            if raw == "fenced":
                raise RuntimeError("Parent runner fence lost")
            return None if raw == "created" else json.loads(raw)
        with FileLock(str(self.path) + ".lock"):
            if self.path.exists():
                return json.loads(self.path.read_text())
            self._write(payload)
            return None

    def _write(self, payload):
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w") as output:
            json.dump(payload, output)
        os.replace(temporary, self.path)

    async def finish(self, payload: dict):
        if self.runner:
            user, session, trace, lease, owner = self.runner
            client = await get_redis()
            saved = await client.eval(
                _FINISH,
                2,
                self.key,
                f"agent:active-session:{user}:{session}",
                trace,
                lease,
                owner,
                json.dumps(payload),
                self.ttl,
            )
            if saved not in (1, 2):
                raise RuntimeError("Child result fenced or already terminal")
            if saved == 2:
                cancelled_payload = dict(
                    payload, result=dict(payload["result"], status="cancelled", error_code="task_cancelled")
                )
                await self.finish(cancelled_payload)
                return True
            return False
        else:
            with FileLock(str(self.path) + ".lock"):
                old = json.loads(self.path.read_text())
                if old.get("result"):
                    raise RuntimeError("Child already terminal")
                self._write(payload)


async def recover_abandoned_children():
    """Reconcile expired online receipts without disturbing another live worker."""
    client = await get_redis()
    async for key in client.scan_iter(match="agent:child:*", count=100):
        raw = await client.get(key)
        if not raw:
            continue
        payload = json.loads(raw)
        if payload.get("result"):
            continue
        user, session, trace, lease, owner = payload["runner"]
        active = await client.hgetall(f"agent:active-session:{user}:{session}")
        alive = (
            active.get("trace_id") == trace
            and active.get("lease_token") == lease
            and active.get("runner_token") == owner
            and active.get("runner_state") in {"starting", "running"}
            and payload["deadline"] > time.time()
        )
        if alive:
            continue
        result = dict(payload["initial_result"], status="interrupted", error_code="child_interrupted")
        updated = dict(payload, result=result)
        # CAS: never overwrite a terminal result that arrived during the scan.
        await client.eval(
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "redis.call('SET', KEYS[1], ARGV[2], 'KEEPTTL'); return 1 end return 0",
            1,
            key,
            raw,
            json.dumps(updated),
        )
