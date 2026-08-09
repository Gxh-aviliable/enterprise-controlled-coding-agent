"""Portable trace persistence and metric tests."""

import pytest

from enterprise_agent.observability.trace_store import TraceStore, redact_value


def test_trace_lifecycle_and_metric_aggregation(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    store = TraceStore()
    store.start_trace(
        trace_id="trace-1",
        session_id="session-1",
        user_id=1,
        request_summary="Fix the parser",
    )
    store.record_event(
        user_id=1,
        trace_id="trace-1",
        event_type="model",
        name="llm_call",
        duration_ms=50,
        data={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    store.record_event(
        user_id=1,
        trace_id="trace-1",
        event_type="tool",
        name="read_file",
        duration_ms=5,
        data={"attempt_count": 1},
    )
    store.record_event(
        user_id=1,
        trace_id="trace-1",
        event_type="confirmation",
        name="confirmation_requested",
    )
    store.record_event(
        user_id=1,
        trace_id="trace-1",
        event_type="memory",
        name="memory_retrieval",
        data={
            "candidates": [{"memory_id": "memory-1"}],
            "injected_count": 1,
            "injected_tokens": 12,
        },
    )
    store.finish_trace(
        user_id=1,
        trace_id="trace-1",
        status="succeeded",
        result_summary="Parser fixed and tests passed",
    )

    trace = store.get_trace(1, "trace-1")
    metrics = store.aggregate_metrics(1)
    assert trace["status"] == "succeeded"
    assert trace["metrics"]["model_calls"] == 1
    assert trace["metrics"]["total_tokens"] == 15
    assert trace["metrics"]["memory_injected"] == 1
    assert trace["metrics"]["memory_injected_tokens"] == 12
    assert metrics["task_success_rate"] == 1.0
    assert metrics["tool_success_rate"] == 1.0
    assert metrics["human_intervention_rate"] == 1.0
    assert metrics["memory_injection_rate"] == 1.0
    assert metrics["average_memory_tokens"] == 12


def test_trace_is_isolated_by_user_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    store = TraceStore()
    store.start_trace(
        trace_id="same-id",
        session_id="session-a",
        user_id=1,
        request_summary="user one",
    )
    store.start_trace(
        trace_id="same-id",
        session_id="session-b",
        user_id=2,
        request_summary="user two",
    )
    assert store.get_trace(1, "same-id")["request_summary"] == "user one"
    assert store.get_trace(2, "same-id")["request_summary"] == "user two"


def test_trace_redacts_secrets_recursively():
    redacted = redact_value({
        "api_key": "sk-super-secret",
        "nested": {"password": "hunter2"},
        "message": "Authorization: Bearer abc.def.ghi",
    })
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["password"] == "[REDACTED]"
    assert "abc.def.ghi" not in redacted["message"]


def test_blocked_tool_updates_safety_metric(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    store = TraceStore()
    store.start_trace(
        trace_id="trace-blocked",
        session_id="session",
        user_id=3,
        request_summary="Delete root",
    )
    store.record_event(
        user_id=3,
        trace_id="trace-blocked",
        event_type="tool",
        name="bash",
        status="blocked",
        data={"attempt_count": 1},
    )
    store.finish_trace(user_id=3, trace_id="trace-blocked", status="failed")
    assert store.aggregate_metrics(3)["safety_interceptions"] == 1


def test_confirmation_pause_updates_trace_without_recording_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    store = TraceStore()
    store.start_trace(
        trace_id="trace-waiting",
        session_id="session",
        user_id=4,
        request_summary="Run a reviewed command",
    )
    store.record_event(
        user_id=4,
        trace_id="trace-waiting",
        event_type="confirmation",
        name="confirmation_requested",
        status="waiting",
    )

    trace = store.get_trace(4, "trace-waiting")
    assert trace["status"] == "waiting_confirmation"
    assert trace["error"] is None


def test_user_pause_control_events_are_non_terminal_and_not_confirmations(
    monkeypatch,
    tmp_path,
):
    """A user pause is control flow, not HITL confirmation or task completion."""
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    store = TraceStore()
    store.start_trace(
        trace_id="trace-user-pause",
        session_id="session-user-pause",
        user_id=5,
        request_summary="Run a long engineering task",
    )
    store.record_event(
        user_id=5,
        trace_id="trace-user-pause",
        event_type="node",
        name="llm_call",
        data={"task_status": "running", "phase": "executing"},
    )
    store.record_event(
        user_id=5,
        trace_id="trace-user-pause",
        event_type="control",
        name="pause_requested",
        status="requested",
        data={"task_status": "running"},
    )
    store.record_event(
        user_id=5,
        trace_id="trace-user-pause",
        event_type="control",
        name="task_paused",
        status="paused",
        data={"task_status": "paused", "resume_target": "tool_executor"},
    )

    paused = store.get_trace(5, "trace-user-pause")
    assert paused["status"] == "paused"
    assert paused["finished_at"] is None
    assert paused["duration_ms"] is None
    assert paused["error"] is None
    assert paused["metrics"]["confirmation_count"] == 0
    assert [event["name"] for event in paused["events"]][-2:] == [
        "pause_requested",
        "task_paused",
    ]
    assert store.aggregate_metrics(5)["task_count"] == 0

    store.record_event(
        user_id=5,
        trace_id="trace-user-pause",
        event_type="control",
        name="resume_requested",
        status="requested",
        data={"task_status": "paused", "resume_target": "tool_executor"},
    )
    resuming = store.get_trace(5, "trace-user-pause")
    assert resuming["status"] == "resuming"
    assert resuming["finished_at"] is None

    store.record_event(
        user_id=5,
        trace_id="trace-user-pause",
        event_type="control",
        name="task_resumed",
        status="success",
        data={"task_status": "running", "resume_target": "tool_executor"},
    )
    resumed = store.get_trace(5, "trace-user-pause")
    assert resumed["status"] == "running"
    assert resumed["finished_at"] is None
    assert resumed["metrics"]["confirmation_count"] == 0

    store.finish_trace(
        user_id=5,
        trace_id="trace-user-pause",
        status="succeeded",
        result_summary="Task completed after resume",
    )
    metrics = store.aggregate_metrics(5)
    assert metrics["task_count"] == 1
    assert metrics["human_intervention_rate"] == 0.0
    assert metrics["confirmation_count"] == 0


@pytest.mark.parametrize("terminal_status", ["succeeded", "failed", "cancelled"])
def test_finished_trace_keeps_terminal_status_when_late_running_events_arrive(
    monkeypatch,
    tmp_path,
    terminal_status,
):
    """Late worker events remain auditable without reopening a finished task."""
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    store = TraceStore()
    store.start_trace(
        trace_id=f"trace-sticky-{terminal_status}",
        session_id="session-sticky",
        user_id=6,
        request_summary="Complete exactly once",
        mode="multi_agent",
    )
    finished = store.finish_trace(
        user_id=6,
        trace_id=f"trace-sticky-{terminal_status}",
        status=terminal_status,
        result_summary="Terminal result",
    )
    terminal_finished_at = finished["finished_at"]
    event_count = len(finished["events"])

    store.record_event(
        user_id=6,
        trace_id=f"trace-sticky-{terminal_status}",
        event_type="node",
        name="late_worker_update",
        status="success",
        data={"task_status": "running", "phase": "executing"},
    )
    after_node = store.get_trace(6, f"trace-sticky-{terminal_status}")
    assert after_node["status"] == terminal_status
    assert after_node["finished_at"] == terminal_finished_at
    assert len(after_node["events"]) == event_count + 1
    assert after_node["events"][-1]["name"] == "late_worker_update"

    store.record_event(
        user_id=6,
        trace_id=f"trace-sticky-{terminal_status}",
        event_type="control",
        name="task_resumed",
        status="success",
        data={"task_status": "running", "resume_target": "tool_executor"},
    )
    after_resume = store.get_trace(6, f"trace-sticky-{terminal_status}")
    assert after_resume["status"] == terminal_status
    assert after_resume["finished_at"] == terminal_finished_at
    assert len(after_resume["events"]) == event_count + 2
    assert [event["name"] for event in after_resume["events"][-2:]] == [
        "late_worker_update",
        "task_resumed",
    ]
