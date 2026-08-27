"""Administrator task cancellation must stay bound to the selected Trace."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from enterprise_agent.api.routes import admin
from enterprise_agent.api.schemas.admin import AdminReasonRequest


class _ScalarResult:
    def all(self):
        return [7]


class _Db:
    def __init__(self):
        self.commit = AsyncMock()

    async def scalars(self, _query):
        return _ScalarResult()

    def add(self, _value):
        return None


class _TraceStore:
    def __init__(self):
        self.finished = []

    def get_trace(self, user_id, trace_id):
        assert user_id == 7
        assert trace_id == "trace-selected"
        return {
            "session_id": "shared-session",
            "user_id": 7,
            "status": "running",
        }

    def finish_trace(self, **kwargs):
        self.finished.append(kwargs)


async def test_admin_cancel_passes_the_selected_trace_to_task_control(monkeypatch):
    cancel = AsyncMock(return_value={"status": "cancelled"})
    monkeypatch.setattr(admin, "get_trace_store", lambda: _TraceStore())
    monkeypatch.setattr(admin, "add_audit_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "enterprise_agent.api.routes.chat.request_task_cancellation",
        cancel,
    )

    result = await admin.cancel_admin_task(
        trace_id="trace-selected",
        payload=AdminReasonRequest(reason="Operator requested cancellation"),
        request=SimpleNamespace(client=None, headers={}),
        admin=SimpleNamespace(id=1),
        db=_Db(),
    )

    cancel.assert_awaited_once_with(
        "shared-session",
        7,
        "Operator requested cancellation",
        trace_id="trace-selected",
    )
    assert result["trace_id"] == "trace-selected"
    assert result["user_id"] == 7
