import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.nodes import prepare_tool_execution_node, tool_confirm_node, tool_executor_node
from enterprise_agent.core.agent.tools.workspace import get_user_workspace
from enterprise_agent.core.execution.approvals import approval_is_current, approval_scope, refresh_authorization


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr(settings, "ENABLE_TOOL_CONFIRMATION", True)
    return {
        "user_id": 501,
        "trace_id": "approval-trace",
        "session_id": "approval-session",
        "task_status": "running",
        "permissions": ["tools:file"],
        "messages": [],
        "pending_tool_calls": [{"id": "write-one", "name": "write_file", "args": {"path": "a.txt", "content": "one"}}],
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_id", 502),
        ("trace_id", "other-trace"),
        ("session_id", "other-session"),
        ("permissions", []),
        (
            "pending_tool_calls",
            [{"id": "write-one", "name": "write_file", "args": {"path": "a.txt", "content": "different"}}],
        ),
    ],
)
def test_approval_cannot_authorize_another_scope(state, field, value):
    signature = approval_scope(state)
    deadline = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    assert approval_is_current(state, signature, deadline)
    modified = copy.deepcopy(state)
    modified[field] = value
    assert not approval_is_current(modified, signature, deadline)


def test_user_edit_or_expiry_invalidates_decision(state):
    signature = approval_scope(state)
    deadline = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    (get_user_workspace(501) / "human.txt").write_text("later edit")
    assert not approval_is_current(state, signature, deadline)
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert not approval_is_current(state, approval_scope(state), expired)


async def test_batch_change_requires_fresh_approval_for_remaining_write(state, monkeypatch):
    state["pending_tool_calls"].append(
        {"id": "write-two", "name": "write_file", "args": {"path": "b.txt", "content": "two"}}
    )
    monkeypatch.setattr("enterprise_agent.core.agent.nodes.interrupt", lambda _: {"approved": True})
    state.update(await prepare_tool_execution_node(state))
    state.update(await tool_confirm_node(state))
    output = await tool_executor_node(state)
    root = get_user_workspace(501)
    assert (root / "a.txt").read_text() == "one"
    assert not (root / "b.txt").exists()
    assert output["tool_execution_records"][-1]["error_code"] == "approval_stale"


async def test_legacy_unsigned_confirmation_fails_closed(state, monkeypatch):
    monkeypatch.setattr("enterprise_agent.core.agent.nodes.interrupt", lambda _: {"approved": True})
    state.update(await tool_confirm_node(state))
    output = await tool_executor_node(state)
    assert not (get_user_workspace(501) / "a.txt").exists()
    assert output["tool_execution_records"][0]["error_code"] == "approval_stale"


async def test_api_permission_refresh_invalidates_signed_scope(state, monkeypatch):
    signature = approval_scope(state)
    deadline = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(is_active=False, is_superuser=True)
    context = AsyncMock()
    context.__aenter__.return_value = db
    monkeypatch.setattr("enterprise_agent.db.mysql.async_session_factory", lambda: context)
    updated = await refresh_authorization({**state, "authorization_source": "database"})
    assert updated["permissions"] == []
    assert not approval_is_current(updated, signature, deadline)


def test_mutation_boundary_rechecks_version_after_dispatch(state):
    from enterprise_agent.core.execution.evidence import current_evidence, observe_file_mutation
    signature = approval_scope(state)
    deadline = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    token = current_evidence.set({'approval': (state, signature, deadline)})
    try:
        (get_user_workspace(501) / 'human-late.txt').write_text('edit after dispatch')
        with pytest.raises(PermissionError, match='approval_stale'):
            with observe_file_mutation():
                pytest.fail('Mutation body must not execute')
    finally:
        current_evidence.reset(token)


@pytest.mark.parametrize("tool_name", ["bash", "background_run"])
@pytest.mark.parametrize("decision", ["rejected", "expired"])
async def test_new_docker_syntax_still_obeys_approval(state, monkeypatch, tool_name, decision):
    monkeypatch.setattr(settings, "AGENT_EXECUTOR", "docker")
    state["permissions"] = ["tools:shell"]
    state["pending_tool_calls"] = [{
        "id": "inline-write", "name": tool_name,
        "args": {"command": 'python -c "from pathlib import Path; Path(\'unexpected\').touch()"'},
    }]
    state.update(await prepare_tool_execution_node(state))
    assert state["task_status"] == "waiting_confirmation"
    monkeypatch.setattr("enterprise_agent.core.agent.nodes.interrupt", lambda _: {"approved": decision != "rejected"})
    state.update(await tool_confirm_node(state))
    if decision == "expired":
        state["pending_tool_calls"][0]["_approval_deadline"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
    monkeypatch.setattr("enterprise_agent.sandbox.executor.get_executor", lambda: pytest.fail("must not execute"))
    output = await tool_executor_node(state)
    assert output["tool_execution_records"][0]["error_code"] == (
        "user_rejected" if decision == "rejected" else "approval_stale"
    )
    assert not (get_user_workspace(501) / "unexpected").exists()
