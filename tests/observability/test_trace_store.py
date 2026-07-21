"""Portable trace persistence and metric tests."""

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
