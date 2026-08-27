"""Phase-B startup retirement for legacy user-pause artifacts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from enterprise_agent.api.routes import chat


class _DbContext:
    def __init__(self, sessions):
        result = MagicMock()
        result.scalars.return_value.all.return_value = sessions
        self.db = MagicMock()
        self.db.execute = AsyncMock(return_value=result)

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _TraceStore:
    def __init__(self, traces=None):
        self.traces = list(traces or [])
        self.finish_trace = MagicMock(side_effect=self._finish_trace)

    def list_traces(self, user_id, limit=500):
        assert user_id == 7
        assert limit == 500
        return list(self.traces)

    def _finish_trace(self, *, user_id, trace_id, status, error):
        assert user_id == 7
        for trace in self.traces:
            if trace.get("trace_id") == trace_id:
                trace["status"] = status
                trace["error"] = error
                break


def _install_common_mocks(monkeypatch, *, snapshot, traces=None, pause_keys=None):
    session = SimpleNamespace(id="session-1", user_id=7)
    db_context = _DbContext([session])
    graph = SimpleNamespace(aget_state=AsyncMock(return_value=snapshot))
    trace_store = _TraceStore(traces)

    monkeypatch.setattr(chat, "async_session_factory", lambda: db_context)
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    monkeypatch.setattr(chat, "get_trace_store", lambda: trace_store)
    monkeypatch.setattr(
        chat,
        "set_user_pause_protocol_retired",
        AsyncMock(return_value={"reason": "user_pause_feature_retired"}),
    )
    monkeypatch.setattr(
        chat,
        "scan_legacy_pause_keys",
        AsyncMock(return_value=list(pause_keys or [])),
    )
    monkeypatch.setattr(chat, "clear_legacy_pause_key", AsyncMock(return_value=True))
    return graph, trace_store


@pytest.mark.asyncio
async def test_startup_retirement_terminalizes_a_paused_checkpoint_once(monkeypatch):
    paused = SimpleNamespace(
        values={"task_status": "paused", "trace_id": "trace-paused"},
        tasks=(),
    )
    graph, _ = _install_common_mocks(monkeypatch, snapshot=paused)
    graph.aget_state.side_effect = [
        paused,
        SimpleNamespace(
            values={"task_status": "cancelled", "trace_id": "trace-paused"},
            tasks=(),
        ),
    ]
    retire_checkpoint = AsyncMock(return_value=True)
    monkeypatch.setattr(chat, "_retire_legacy_pause_checkpoint", retire_checkpoint)
    monkeypatch.setattr(chat, "get_active_trace_lease", AsyncMock(return_value=None))

    assert await chat.retire_legacy_user_pause_tasks() == 1
    assert await chat.retire_legacy_user_pause_tasks() == 0

    retire_checkpoint.assert_awaited_once()
    assert retire_checkpoint.await_args.kwargs == {
        "session_id": "session-1",
        "user_id": 7,
    }
    chat.set_user_pause_protocol_retired.assert_awaited_with(
        "user_pause_feature_retired"
    )


@pytest.mark.asyncio
async def test_startup_retirement_fails_fast_for_an_active_legacy_runner(monkeypatch):
    snapshot = SimpleNamespace(
        values={"task_status": "pause_requested", "trace_id": "trace-live"},
        tasks=(),
    )
    _install_common_mocks(monkeypatch, snapshot=snapshot)
    retire_checkpoint = AsyncMock(return_value=True)
    monkeypatch.setattr(chat, "_retire_legacy_pause_checkpoint", retire_checkpoint)
    monkeypatch.setattr(
        chat,
        "get_active_trace_lease",
        AsyncMock(return_value={"trace_id": "trace-live", "runner_state": "running"}),
    )

    with pytest.raises(RuntimeError, match="runner lease is active"):
        await chat.retire_legacy_user_pause_tasks()

    retire_checkpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_retirement_closes_orphan_trace_and_clears_legacy_keys(
    monkeypatch,
):
    snapshot = SimpleNamespace(values={}, tasks=())
    graph, trace_store = _install_common_mocks(
        monkeypatch,
        snapshot=snapshot,
        traces=[{
            "trace_id": "trace-orphan",
            "session_id": "session-1",
            "status": "resuming",
            "request_summary": "finish the migration",
        }],
        pause_keys=[
            "agent:pause:7:session-1:trace-orphan",
            "agent:pause:not-an-int:bad-session:bad-trace",
            "malformed",
        ],
    )
    persist_cancelled = AsyncMock(return_value=None)
    monkeypatch.setattr(
        chat,
        "_safe_mark_durable_assistant_cancelled",
        persist_cancelled,
    )

    assert await chat.retire_legacy_user_pause_tasks() == 1
    assert await chat.retire_legacy_user_pause_tasks() == 0

    trace_store.finish_trace.assert_called_once_with(
        user_id=7,
        trace_id="trace-orphan",
        status="cancelled",
        error="user_pause_feature_retired",
    )
    persist_cancelled.assert_awaited_once_with(
        session_id="session-1",
        user_id=7,
        trace_id="trace-orphan",
        values={"current_user_request": "finish the migration"},
        reason="user_pause_feature_retired",
    )
    assert (
        7,
        "session-1",
        "trace-orphan",
    ) in [call.args for call in chat.clear_legacy_pause_key.await_args_list]
