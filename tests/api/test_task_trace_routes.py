"""Task trace API isolation and payload tests."""

import pytest
from fastapi import HTTPException

from enterprise_agent.api.routes import tasks
from enterprise_agent.observability.trace_store import get_trace_store


def _seed_trace(user_id: int, trace_id: str):
    store = get_trace_store()
    store.start_trace(
        trace_id=trace_id,
        session_id="session",
        user_id=user_id,
        request_summary="Understand the repository",
    )
    store.finish_trace(
        user_id=user_id,
        trace_id=trace_id,
        status="succeeded",
        result_summary="Done",
    )


async def test_task_detail_and_replay_are_user_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    _seed_trace(11, "trace-route")

    detail = await tasks.get_task_run("trace-route", user_id=11)
    replay = await tasks.replay_task_trace("trace-route", user_id=11)
    assert detail["status"] == "succeeded"
    assert detail["event_count"] == 2
    assert replay["events"][-1]["name"] == "task_finished"

    with pytest.raises(HTTPException) as exc:
        await tasks.replay_task_trace("trace-route", user_id=12)
    assert exc.value.status_code == 404


async def test_task_list_and_metrics_use_real_trace_files(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    _seed_trace(21, "trace-metrics")

    listing = await tasks.list_task_runs(limit=10, user_id=21)
    metrics = await tasks.get_task_metrics(user_id=21)
    assert listing["tasks"][0]["trace_id"] == "trace-metrics"
    assert metrics["task_count"] == 1
    assert metrics["task_success_rate"] == 1.0


def test_task_changes_and_restore_require_ownership(monkeypatch, tmp_path):
    from enterprise_agent.core.agent.tools.workspace import get_user_workspace, set_current_user_id
    from enterprise_agent.core.execution.evidence import current_evidence, observe_file_mutation

    monkeypatch.setenv('WORKSPACE_BASE', str(tmp_path))
    _seed_trace(31, 'task-diff')
    set_current_user_id(31)
    root = get_user_workspace(31)
    token = current_evidence.set({'trace_id': 'task-diff', 'tool_call_id': 'write', 'receipts': []})
    try:
        with observe_file_mutation():
            (root / 'a.txt').write_text('synthetic')
    finally:
        current_evidence.reset(token)
        set_current_user_id(None)
    data = tasks.get_task_changes('task-diff', user_id=31)
    assert data['files'][0]['operation'] == 'added'
    payload = tasks.RestoreRequest(paths=['a.txt'], expected_version=data['version'])
    with pytest.raises(HTTPException) as error:
        tasks.get_task_changes('task-diff', user_id=32)
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        tasks.restore_task_files('task-diff', payload, user_id=32)
    assert error.value.status_code == 404
    assert (root / 'a.txt').read_text() == 'synthetic'
    assert tasks.restore_task_files('task-diff', payload, user_id=31)['status'] == 'completed'
    assert not (root / 'a.txt').exists()
