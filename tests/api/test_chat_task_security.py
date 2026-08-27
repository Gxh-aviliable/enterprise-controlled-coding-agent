"""Session ownership and authoritative Stop/confirmation control regressions."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph.message import add_messages
from langgraph.types import Command

from enterprise_agent.api.routes import chat
from enterprise_agent.api.schemas.chat import ChatRequest, ResumeRequest
from enterprise_agent.api.services.chat_history import (
    mark_assistant_message_cancelled,
    update_assistant_message,
)
from enterprise_agent.core.execution.state_machine import TaskStatus
from enterprise_agent.models.session import Session, SessionStatus


def _owned_db(session=None):
    result = MagicMock()
    result.scalar_one_or_none.return_value = session
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    return db


class _AsyncSessionContext:
    def __init__(self, db=None):
        self.db = db or MagicMock()

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


def _lease(trace_id: str, *, runner_state: str = "stopped", runner_token="runner-1"):
    return {
        "trace_id": trace_id,
        "lease_token": "lease-1",
        "runner_token": runner_token,
        "runner_state": runner_state,
        "fence": 7,
        "cancel_requested": False,
    }


def _snapshot(
    *,
    session_id: str,
    user_id: int,
    trace_id: str,
    status: str,
    interrupt_type: str | None = None,
    failure_reason: str | None = None,
):
    tasks = []
    if interrupt_type:
        tasks = [SimpleNamespace(interrupts=[SimpleNamespace(value={
            "type": interrupt_type,
            "trace_id": trace_id,
            "tools": [],
        })])]
    return SimpleNamespace(
        values={
            "session_id": session_id,
            "user_id": user_id,
            "trace_id": trace_id,
            "task_status": status,
            "failure_reason": failure_reason,
        },
        tasks=tasks,
    )


def _patch_control_layer(
    monkeypatch,
    *,
    active=None,
    cancellation=None,
    durable=None,
):
    """Keep API tests fully isolated from Redis and durable DB factories."""
    reserved = _lease(
        str((active or {}).get("trace_id") or "trace-control"),
        runner_token="runner-reserved",
    )
    controls = {
        "get_active": AsyncMock(return_value=active),
        "get_cancel": AsyncMock(return_value=cancellation),
        "claim": AsyncMock(return_value=reserved),
        "reserve": AsyncMock(return_value=reserved),
        "start": AsyncMock(return_value=True),
        "renew": AsyncMock(return_value=True),
        "mark_stopped": AsyncMock(return_value=True),
        "release_lease": AsyncMock(return_value="released"),
        "request_cancel": AsyncMock(return_value={"status": "requested"}),
        "acquire_resume": AsyncMock(return_value="resume-lock-token"),
        "release_resume": AsyncMock(return_value=True),
    }
    monkeypatch.setattr(chat, "get_active_trace_lease", controls["get_active"])
    monkeypatch.setattr(chat, "get_trace_cancel_request", controls["get_cancel"])
    monkeypatch.setattr(chat, "claim_active_trace_lease", controls["claim"])
    monkeypatch.setattr(chat, "reserve_active_trace_runner", controls["reserve"])
    monkeypatch.setattr(chat, "start_active_trace_runner", controls["start"])
    monkeypatch.setattr(chat, "renew_active_trace_runner", controls["renew"])
    monkeypatch.setattr(
        chat,
        "mark_active_trace_runner_stopped",
        controls["mark_stopped"],
    )
    monkeypatch.setattr(chat, "release_active_trace_lease", controls["release_lease"])
    monkeypatch.setattr(chat, "request_trace_cancellation", controls["request_cancel"])
    monkeypatch.setattr(chat, "acquire_task_resume_lock", controls["acquire_resume"])
    monkeypatch.setattr(chat, "release_task_resume_lock", controls["release_resume"])
    monkeypatch.setattr(chat, "async_session_factory", lambda: _AsyncSessionContext())
    monkeypatch.setattr(chat, "get_latest_assistant_task", AsyncMock(return_value=durable))
    monkeypatch.setattr(chat, "clear_legacy_pause_key", AsyncMock(return_value=True))
    monkeypatch.setattr(chat, "get_trace_store", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(chat, "_cancel_events", {})
    monkeypatch.setattr(chat, "_active_stream_traces", {})

    from enterprise_agent.core.agent.tools import background

    monkeypatch.setattr(background, "clear_background_manager", MagicMock())
    return controls


async def test_checkpoint_routes_hide_unknown_or_foreign_session():
    with pytest.raises(HTTPException) as exc:
        await chat._require_owned_session("foreign", 7, _owned_db(None))
    assert exc.value.status_code == 404


async def test_missing_chat_session_is_created_for_authenticated_user():
    db = _owned_db()
    session_id = await chat._resolve_chat_session(
        ChatRequest(content="Inspect this repository"),
        42,
        db,
    )
    assert session_id
    created = db.add.call_args.args[0]
    assert created.user_id == 42
    assert created.status == SessionStatus.ACTIVE
    db.commit.assert_awaited_once()


def test_multi_agent_mode_is_rejected_when_server_disabled(monkeypatch):
    monkeypatch.setattr(chat.settings, "ENABLE_MULTI_AGENT", False)
    with pytest.raises(HTTPException) as exc:
        chat._validate_execution_mode("multi_agent", ["tools:advanced"])
    assert exc.value.status_code == 409


def test_multi_agent_mode_requires_advanced_permission(monkeypatch):
    monkeypatch.setattr(chat.settings, "ENABLE_MULTI_AGENT", True)
    with pytest.raises(HTTPException) as exc:
        chat._validate_execution_mode("multi_agent", ["tools:basic"])
    assert exc.value.status_code == 403


def test_capabilities_and_task_input_preserve_requested_mode(monkeypatch):
    monkeypatch.setattr(chat.settings, "ENABLE_MULTI_AGENT", True)
    capabilities = chat._agent_capabilities(["tools:advanced"])
    assert capabilities.available_modes == ["single_agent", "multi_agent"]
    task_input = chat._task_input(
        session_id="session-mode",
        trace_id="trace-mode",
        user_id=1,
        permissions=["tools:advanced"],
        content="Use several reviewers",
        mode="multi_agent",
    )
    assert task_input["execution_mode"] == "multi_agent"
    assert task_input["current_user_request"] == "Use several reviewers"


def test_explicit_multi_agent_request_cannot_silently_run_in_single_mode():
    assert chat._requests_multi_agent_execution("运用你的多智能体协作能力，写一篇短篇小说")
    assert not chat._requests_multi_agent_execution("请解释什么是多智能体")
    with pytest.raises(HTTPException) as exc:
        chat._validate_request_mode(
            "single_agent",
            "运用你的多智能体协作能力，写一篇短篇小说",
            ["tools:advanced"],
        )
    assert exc.value.status_code == 409


def test_tool_sse_events_use_normalized_execution_record_not_empty_pending_list():
    events = chat._tool_sse_events({
        "tool_results": {"call-1": "Blocked: denied"},
        "pending_tool_calls": [],
        "tool_execution_records": [{
            "tool_name": "bash",
            "tool_call_id": "call-1",
            "status": "blocked",
            "ok": False,
            "output": "Blocked: denied",
            "duration_ms": 7,
            "error_code": "policy_blocked",
        }],
    })
    assert [event["event"] for event in events] == ["tool_result", "tool_end"]
    assert events[1] == {
        "event": "tool_end",
        "id": "call-1",
        "name": "bash",
        "status": "blocked",
        "ok": False,
        "duration_ms": 7,
        "error_code": "policy_blocked",
    }


def test_tool_sse_events_preserve_confirmation_rejection():
    events = chat._tool_sse_events({
        "tool_results": {"call-rejected": "Tool execution rejected by user."},
        "tool_execution_records": [{
            "tool_name": "write_file",
            "tool_call_id": "call-rejected",
            "status": "rejected",
            "ok": False,
            "output": "Tool execution rejected by user.",
            "duration_ms": 0,
            "error_code": "user_rejected",
        }],
    })

    assert [event["event"] for event in events] == ["tool_result", "tool_end"]
    assert events[1]["status"] == "rejected"
    assert events[1]["ok"] is False
    assert events[1]["error_code"] == "user_rejected"


def test_tool_sse_events_expose_artifact_metadata():
    events = chat._tool_sse_events({
        "tool_results": {"call-stored": "bounded preview"},
        "pending_tool_calls": [],
        "tool_execution_records": [{
            "tool_name": "bash",
            "tool_call_id": "call-stored",
            "status": "success",
            "ok": True,
            "output": "bounded preview",
            "duration_ms": 13,
            "error_code": None,
            "artifact_path": ".agent/tool-artifacts/trace/call.txt",
            "artifact_sha256": "a" * 64,
        }],
    })
    assert events[0]["result"] == "bounded preview"
    for event in events:
        assert event["artifact_path"] == ".agent/tool-artifacts/trace/call.txt"
        assert event["artifact_available"] is True
        assert event["artifact_storage_status"] == "stored"


def test_tool_sse_events_expose_failed_artifact_metadata_without_breaking_result():
    events = chat._tool_sse_events({
        "tool_results": {"call-failed": "complete short fallback"},
        "pending_tool_calls": [],
        "tool_execution_records": [{
            "tool_name": "read_file",
            "tool_call_id": "call-failed",
            "status": "success",
            "ok": True,
            "output": "complete short fallback",
            "duration_ms": 5,
            "error_code": None,
            "artifact_path": None,
            "artifact_error": "artifact_write_failed",
        }],
    })
    assert events[0]["result"] == "complete short fallback"
    for event in events:
        assert event["artifact_available"] is False
        assert event["artifact_storage_status"] == "failed"
        assert event["artifact_error"] == "artifact_write_failed"
        assert "artifact_path" not in event
        assert "artifact_sha256" not in event


def test_stream_timeline_recorder_keeps_text_tool_text_order():
    recorder = chat._StreamTimelineRecorder()
    recorder.record_delta("先读取文件。")
    recorder.record_event({"event": "tool_start", "id": "call-read", "name": "read_file"})
    recorder.record_event({
        "event": "tool_result",
        "id": "call-read",
        "name": "read_file",
        "status": "success",
        "ok": True,
        "result": "# Project",
        "duration_ms": 12,
    })
    recorder.record_event({
        "event": "tool_end",
        "id": "call-read",
        "name": "read_file",
        "status": "success",
        "ok": True,
        "duration_ms": 12,
    })
    recorder.record_delta("读取完成。")
    assert [entry["role"] for entry in recorder.entries] == [
        "assistant", "tool_call", "assistant",
    ]
    assert recorder.entries[1] == {
        "role": "tool_call",
        "toolCallId": "call-read",
        "toolName": "read_file",
        "toolStatus": "done",
        "toolResult": "# Project",
        "toolError": "",
        "toolDuration": 12,
    }


def test_stream_timeline_recorder_recovers_missing_start_and_deduplicates_interrupt():
    recorder = chat._StreamTimelineRecorder()
    recorder.record_event({
        "event": "tool_result",
        "id": "call-late",
        "name": "bash",
        "status": "success",
        "ok": True,
        "result": "ok",
    })
    recorder.record_event({"event": "tool_start", "id": "call-confirm", "name": "write_file"})
    recorder.record_event({"event": "tool_start", "id": "call-confirm", "name": "write_file"})
    recorder.record_event({
        "event": "interrupt",
        "data": {"tools": [{"id": "call-confirm", "name": "write_file"}]},
    })
    assert len(recorder.entries) == 2
    assert recorder.entries[0]["toolCallId"] == "call-late"
    assert recorder.entries[0]["toolStatus"] == "done"
    assert recorder.entries[1]["toolCallId"] == "call-confirm"
    assert recorder.entries[1]["toolStatus"] == "waiting"


def test_internal_stream_filters_are_isolated_per_request():
    first = chat.InternalStreamFilter()
    second = chat.InternalStreamFilter()
    assert first.is_internal_json("[User Request]: internal summary") is True
    assert second.is_internal_json("normal user-visible token") is False
    assert first.is_internal_json("still internal") is True


def test_tool_confirmation_interrupt_keeps_confirmation_sse_and_timeout(monkeypatch):
    schedule_timeout = MagicMock()
    monkeypatch.setattr(chat, "_schedule_confirmation_timeout", schedule_timeout)
    payload = {
        "type": "tool_confirmation",
        "trace_id": "trace-confirm-sse",
        "deadline": "2026-08-10T12:00:00+00:00",
        "tools": [],
    }
    event, assistant_status = chat._stream_interrupt_event(
        interrupt_obj=SimpleNamespace(value=payload),
        session_id="session-confirm-sse",
        trace_id="trace-confirm-sse",
        user_id=18,
    )
    assert event == {"event": "interrupt", "data": payload}
    assert assistant_status == "interrupted"
    schedule_timeout.assert_called_once()


@pytest.mark.parametrize(
    "interrupt_obj",
    [
        SimpleNamespace(value={"type": "unsupported_control"}),
        SimpleNamespace(value={"trace_id": "untyped-trace"}),
        SimpleNamespace(value="not-a-dict"),
    ],
)
def test_unknown_or_untyped_interrupt_fails_closed(interrupt_obj, monkeypatch):
    schedule_timeout = MagicMock()
    monkeypatch.setattr(chat, "_schedule_confirmation_timeout", schedule_timeout)
    with pytest.raises(RuntimeError):
        chat._stream_interrupt_event(
            interrupt_obj=interrupt_obj,
            session_id="session-invalid-sse",
            trace_id="trace-invalid-sse",
            user_id=19,
        )
    schedule_timeout.assert_not_called()


async def test_pending_confirmation_reads_interrupt_value_async(monkeypatch):
    session = Session(id="session-2", user_id=2, status=SessionStatus.ACTIVE)
    interrupt = SimpleNamespace(value={"type": "tool_confirmation", "tools": []})
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(
        values={},
        tasks=[SimpleNamespace(interrupts=[interrupt])],
    ))
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    response = await chat.get_pending_confirmation(
        session_id="session-2",
        user_id=2,
        db=_owned_db(session),
    )
    assert response["status"] == "pending"
    assert response["confirmation"]["type"] == "tool_confirmation"


async def test_confirm_resumes_same_trace_under_owner_token(monkeypatch):
    session_id = "confirm-session"
    trace_id = "trace-confirm"
    session = Session(id=session_id, user_id=1, status=SessionStatus.ACTIVE)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_snapshot(
        session_id=session_id,
        user_id=1,
        trace_id=trace_id,
        status="waiting_confirmation",
        interrupt_type="tool_confirmation",
    ))
    graph.ainvoke = AsyncMock(return_value={
        "session_id": session_id,
        "user_id": 1,
        "trace_id": trace_id,
        "task_status": "succeeded",
    })
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    controls = _patch_control_layer(monkeypatch, active=_lease(trace_id))
    reserved = _lease(trace_id, runner_token="runner-resumed")
    controls["reserve"].return_value = reserved
    monkeypatch.setattr(chat, "find_assistant_message_id", AsyncMock(return_value=17))
    monkeypatch.setattr(chat, "update_assistant_message", AsyncMock(return_value=True))

    response = await chat.confirm_tool(
        session_id=session_id,
        approved=True,
        approved_ids=["tool-1"],
        trace_id=trace_id,
        user_id=1,
        db=_owned_db(session),
    )
    assert response["status"] == "resumed"
    assert response["trace_id"] == trace_id
    assert graph.ainvoke.await_args.args[0].resume == {
        "approved": True,
        "approved_ids": ["tool-1"],
    }
    controls["acquire_resume"].assert_awaited_once_with(1, session_id, trace_id)
    controls["reserve"].assert_awaited_once_with(
        1,
        session_id,
        trace_id,
        "lease-1",
        ttl_seconds=chat.settings.ACTIVE_TRACE_LEASE_SECONDS,
    )
    controls["start"].assert_awaited_once_with(
        1,
        session_id,
        trace_id,
        "lease-1",
        "runner-resumed",
        ttl_seconds=chat.settings.ACTIVE_TRACE_LEASE_SECONDS,
    )
    controls["release_resume"].assert_awaited_once_with(
        1,
        session_id,
        trace_id,
        "resume-lock-token",
    )


async def test_confirmation_resume_lock_prevents_duplicate_runner(monkeypatch):
    trace_id = "trace-confirm"
    session = Session(id="confirm-session", user_id=1, status=SessionStatus.ACTIVE)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_snapshot(
        session_id="confirm-session",
        user_id=1,
        trace_id=trace_id,
        status="waiting_confirmation",
        interrupt_type="tool_confirmation",
    ))
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    controls = _patch_control_layer(monkeypatch, active=_lease(trace_id))
    controls["acquire_resume"].return_value = None

    with pytest.raises(HTTPException) as exc:
        await chat.confirm_tool(
            session_id="confirm-session",
            approved=False,
            trace_id=trace_id,
            user_id=1,
            db=_owned_db(session),
        )
    assert exc.value.status_code == 409
    controls["reserve"].assert_not_awaited()
    controls["start"].assert_not_awaited()


async def test_stream_resume_preserves_trace_and_resume_lock(monkeypatch):
    session_id = "resume-session"
    trace_id = "trace-resume"
    session = Session(id=session_id, user_id=1, status=SessionStatus.ACTIVE)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_snapshot(
        session_id=session_id,
        user_id=1,
        trace_id=trace_id,
        status="waiting_confirmation",
        interrupt_type="tool_confirmation",
    ))
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    controls = _patch_control_layer(monkeypatch, active=_lease(trace_id))
    reserved = _lease(trace_id, runner_token="runner-resume-stream")
    controls["reserve"].return_value = reserved
    monkeypatch.setattr(chat, "find_assistant_message_id", AsyncMock(return_value=21))
    monkeypatch.setattr(chat, "update_assistant_message", AsyncMock(return_value=True))
    response_sentinel = object()
    stream_resume = MagicMock(return_value=response_sentinel)
    monkeypatch.setattr(chat, "_stream_resumed_command", stream_resume)

    response = await chat.chat_stream_resume(
        session_id=session_id,
        approved=True,
        body=ResumeRequest(approved_ids=["tool-2"], trace_id=trace_id),
        user_id=1,
        db=_owned_db(session),
    )
    assert response is response_sentinel
    kwargs = stream_resume.call_args.kwargs
    assert kwargs["trace_id"] == trace_id
    assert kwargs["command"].resume == {"approved": True, "approved_ids": ["tool-2"]}
    assert kwargs["lease"] == reserved
    assert kwargs["resume_lock_token"] == "resume-lock-token"
    controls["start"].assert_not_awaited()


async def test_stream_resume_rejects_stale_trace_before_lock(monkeypatch):
    session = Session(id="resume-session", user_id=1, status=SessionStatus.ACTIVE)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_snapshot(
        session_id="resume-session",
        user_id=1,
        trace_id="trace-current",
        status="waiting_confirmation",
        interrupt_type="tool_confirmation",
    ))
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    controls = _patch_control_layer(monkeypatch, active=_lease("trace-current"))
    cancel_timeout = MagicMock()
    monkeypatch.setattr(chat, "_cancel_confirmation_timeout", cancel_timeout)

    with pytest.raises(HTTPException) as exc:
        await chat.chat_stream_resume(
            session_id="resume-session",
            approved=True,
            body=ResumeRequest(trace_id="trace-stale"),
            user_id=1,
            db=_owned_db(session),
        )
    assert exc.value.status_code == 409
    controls["acquire_resume"].assert_not_awaited()
    cancel_timeout.assert_not_called()


async def test_confirmation_timeout_rejects_same_trace(monkeypatch):
    session_id = "timeout-session"
    trace_id = "trace-timeout"
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_snapshot(
        session_id=session_id,
        user_id=9,
        trace_id=trace_id,
        status="waiting_confirmation",
        interrupt_type="tool_confirmation",
    ))
    graph.ainvoke = AsyncMock(return_value={
        "session_id": session_id,
        "user_id": 9,
        "trace_id": trace_id,
        "task_status": "succeeded",
    })
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    controls = _patch_control_layer(monkeypatch, active=_lease(trace_id))
    controls["reserve"].return_value = _lease(trace_id, runner_token="runner-timeout")
    monkeypatch.setattr(chat, "find_assistant_message_id", AsyncMock(return_value=31))
    monkeypatch.setattr(chat, "update_assistant_message", AsyncMock(return_value=True))

    past_deadline = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    chat._schedule_confirmation_timeout(session_id, trace_id, 9, past_deadline)
    await asyncio.wait_for(chat._confirmation_timeout_tasks[session_id], timeout=1)
    assert graph.ainvoke.await_args.args[0].resume == {
        "approved": False,
        "approved_ids": [],
        "reason": "confirmation_timeout",
    }
    controls["acquire_resume"].assert_awaited_once_with(9, session_id, trace_id)
    controls["release_resume"].assert_awaited_once_with(
        9,
        session_id,
        trace_id,
        "resume-lock-token",
    )


@pytest.mark.parametrize(
    ("task_status", "failure_reason", "expected_status", "expected_reason"),
    [
        ("succeeded", None, "completed", None),
        ("failed", "validation failed", "failed", "validation failed"),
        ("cancelled", "stopped by user", "cancelled", "stopped by user"),
    ],
)
def test_task_terminal_outcome_maps_authoritative_checkpoint_status(
    task_status,
    failure_reason,
    expected_status,
    expected_reason,
):
    values = _snapshot(
        session_id="terminal-session",
        user_id=1,
        trace_id="trace-terminal",
        status=task_status,
        failure_reason=failure_reason,
    ).values
    assistant_status, reason = chat._task_terminal_outcome(
        values,
        session_id="terminal-session",
        user_id=1,
        trace_id="trace-terminal",
    )
    assert assistant_status == expected_status
    assert reason == expected_reason


@pytest.mark.parametrize(
    "overrides",
    [
        {"task_status": "running"},
        {"session_id": "another-session"},
        {"user_id": 2},
        {"trace_id": "another-trace"},
    ],
)
def test_task_terminal_outcome_fails_closed_for_nonterminal_or_wrong_identity(overrides):
    values = _snapshot(
        session_id="terminal-session",
        user_id=1,
        trace_id="trace-terminal",
        status="succeeded",
    ).values
    values.update(overrides)
    assistant_status, reason = chat._task_terminal_outcome(
        values,
        session_id="terminal-session",
        user_id=1,
        trace_id="trace-terminal",
    )
    assert assistant_status == "failed"
    assert reason


@pytest.mark.parametrize("status", ["pending", "running", "waiting_confirmation"])
async def test_new_task_cannot_overwrite_nonterminal_checkpoint(status, monkeypatch):
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_snapshot(
        session_id="protected-session",
        user_id=1,
        trace_id="trace-existing",
        status=status,
    ))
    controls = _patch_control_layer(monkeypatch, active=None)
    with pytest.raises(HTTPException) as exc:
        await chat._ensure_session_accepts_new_task(
            graph,
            session_id="protected-session",
            user_id=1,
        )
    assert exc.value.status_code == 409
    controls["get_active"].assert_awaited_once_with(1, "protected-session")


async def test_new_task_is_blocked_by_lease_even_before_checkpoint(monkeypatch):
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(values={}, tasks=[]))
    controls = _patch_control_layer(
        monkeypatch,
        active=_lease("trace-before-checkpoint", runner_state="starting"),
    )
    with pytest.raises(HTTPException) as exc:
        await chat._ensure_session_accepts_new_task(
            graph,
            session_id="protected-session",
            user_id=1,
        )
    assert exc.value.status_code == 409
    assert "trace-before-checkpoint" in str(exc.value.detail)
    graph.aget_state.assert_not_awaited()
    controls["get_active"].assert_awaited_once()


@pytest.mark.parametrize("operation", ["status", "cancel", "resume"])
async def test_stop_control_routes_hide_unknown_or_foreign_session(operation):
    db = _owned_db(None)
    with pytest.raises(HTTPException) as exc:
        if operation == "status":
            await chat.get_stream_status(session_id="foreign", user_id=7, db=db)
        elif operation == "cancel":
            await chat.cancel_stream(
                session_id="foreign",
                trace_id="trace-foreign",
                user_id=7,
                db=db,
            )
        else:
            await chat.chat_stream_resume(
                session_id="foreign",
                approved=False,
                body=ResumeRequest(trace_id="trace-foreign"),
                user_id=7,
                db=db,
            )
    assert exc.value.status_code == 404


async def test_stream_status_prefers_authoritative_lease(monkeypatch):
    session = Session(id="status-session", user_id=1, status=SessionStatus.ACTIVE)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_snapshot(
        session_id="status-session",
        user_id=1,
        trace_id="old-checkpoint",
        status="succeeded",
    ))
    active = _lease("trace-active", runner_state="running")
    active["cancel_requested"] = True
    _patch_control_layer(
        monkeypatch,
        active=active,
        cancellation={"reason": "Stop requested"},
        durable={"trace_id": "old-durable", "status": "completed"},
    )
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)

    response = await chat.get_stream_status(
        session_id="status-session",
        user_id=1,
        db=_owned_db(session),
    )
    assert response["status"] == "cancelling"
    assert response["trace_id"] == "trace-active"
    assert response["runner_state"] == "running"
    assert response["stream_fence"] == 7
    assert response["cancel_requested"] is True


async def test_running_stop_returns_cancelling_until_owner_release(monkeypatch):
    session_id = "running-session"
    trace_id = "trace-running"
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_snapshot(
        session_id=session_id,
        user_id=1,
        trace_id=trace_id,
        status="running",
    ))
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    controls = _patch_control_layer(
        monkeypatch,
        active=_lease(trace_id, runner_state="running"),
    )
    monkeypatch.setattr(
        chat,
        "_wait_for_cancelled_lease_release",
        AsyncMock(return_value=False),
    )
    mark_terminal = AsyncMock()
    mark_durable = AsyncMock()
    monkeypatch.setattr(chat, "_safe_mark_task_terminal", mark_terminal)
    monkeypatch.setattr(chat, "_safe_mark_durable_assistant_cancelled", mark_durable)

    response = await chat.request_task_cancellation(session_id, 1, trace_id=trace_id)
    assert response["status"] == "cancelling"
    assert response["trace_id"] == trace_id
    controls["request_cancel"].assert_awaited_once_with(
        1,
        session_id,
        trace_id,
        reason="Cancelled by user",
        ttl_seconds=chat.settings.ACTIVE_TRACE_LEASE_SECONDS,
    )
    mark_terminal.assert_not_awaited()
    mark_durable.assert_not_awaited()
    controls["release_lease"].assert_not_awaited()


async def test_running_stop_never_reports_cancelled_if_task_wins_race(monkeypatch):
    session_id = "race-session"
    trace_id = "trace-race"
    graph = MagicMock()
    graph.aget_state = AsyncMock(side_effect=[
        _snapshot(
            session_id=session_id,
            user_id=1,
            trace_id=trace_id,
            status="running",
        ),
        _snapshot(
            session_id=session_id,
            user_id=1,
            trace_id=trace_id,
            status="succeeded",
        ),
    ])
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    controls = _patch_control_layer(
        monkeypatch,
        active=_lease(trace_id, runner_state="running"),
    )
    monkeypatch.setattr(
        chat,
        "_wait_for_cancelled_lease_release",
        AsyncMock(return_value=False),
    )

    with pytest.raises(HTTPException) as exc:
        await chat.request_task_cancellation(session_id, 1, trace_id=trace_id)
    assert exc.value.status_code == 409
    assert "succeeded" in str(exc.value.detail)
    controls["release_lease"].assert_not_awaited()


async def test_waiting_confirmation_stop_terminalizes_without_resume(monkeypatch):
    session_id = "waiting-session"
    trace_id = "trace-waiting"
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_snapshot(
        session_id=session_id,
        user_id=1,
        trace_id=trace_id,
        status="waiting_confirmation",
        interrupt_type="tool_confirmation",
    ))
    graph.ainvoke = AsyncMock()
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    controls = _patch_control_layer(monkeypatch, active=_lease(trace_id))
    mark_terminal = AsyncMock(return_value=True)
    monkeypatch.setattr(chat, "_safe_mark_task_terminal", mark_terminal)
    monkeypatch.setattr(chat, "_safe_mark_durable_assistant_cancelled", AsyncMock())
    monkeypatch.setattr(chat, "_durable_cancellation_confirmed", AsyncMock(return_value=True))

    response = await chat.request_task_cancellation(
        session_id,
        1,
        reason="Cancelled while waiting",
        trace_id=trace_id,
    )
    assert response["status"] == "cancelled"
    graph.ainvoke.assert_not_awaited()
    mark_terminal.assert_awaited_once_with(
        graph,
        {"configurable": {"thread_id": session_id}},
        TaskStatus.CANCELLED,
        "Cancelled while waiting",
        expected_trace_id=trace_id,
    )
    controls["mark_stopped"].assert_awaited_once()
    controls["release_lease"].assert_awaited_once_with(
        1,
        session_id,
        trace_id,
        "lease-1",
    )


async def test_cancel_exact_active_trace_before_first_checkpoint(monkeypatch):
    session_id = "early-stop-session"
    trace_id = "new-active-trace"
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_snapshot(
        session_id=session_id,
        user_id=1,
        trace_id="older-terminal-trace",
        status="succeeded",
    ))
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    controls = _patch_control_layer(
        monkeypatch,
        active=_lease(trace_id, runner_state="starting"),
    )
    cancel_event = asyncio.Event()
    chat._cancel_events[trace_id] = cancel_event
    mark_durable = AsyncMock()
    monkeypatch.setattr(chat, "_safe_mark_durable_assistant_cancelled", mark_durable)
    monkeypatch.setattr(chat, "_durable_cancellation_confirmed", AsyncMock(return_value=True))

    response = await chat.request_task_cancellation(session_id, 1, trace_id=trace_id)
    assert response["status"] == "cancelled"
    assert response["trace_id"] == trace_id
    assert cancel_event.is_set()
    assert mark_durable.await_args.kwargs["trace_id"] == trace_id
    controls["mark_stopped"].assert_awaited_once()
    controls["release_lease"].assert_awaited_once()


async def test_cancel_starting_to_running_race_waits_for_owner_quiescence(monkeypatch):
    session_id = "runner-start-race-session"
    trace_id = "trace-runner-start-race"
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_snapshot(
        session_id=session_id,
        user_id=1,
        trace_id=trace_id,
        status="running",
    ))
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    controls = _patch_control_layer(monkeypatch, active=None)
    controls["get_active"].side_effect = [
        _lease(trace_id, runner_state="starting"),
        _lease(trace_id, runner_state="running"),
    ]
    wait_for_release = AsyncMock(return_value=False)
    monkeypatch.setattr(
        chat,
        "_wait_for_cancelled_lease_release",
        wait_for_release,
    )
    mark_terminal = AsyncMock()
    mark_durable = AsyncMock()
    monkeypatch.setattr(chat, "_safe_mark_task_terminal", mark_terminal)
    monkeypatch.setattr(chat, "_safe_mark_durable_assistant_cancelled", mark_durable)

    response = await chat.request_task_cancellation(
        session_id,
        1,
        trace_id=trace_id,
    )

    assert response["status"] == "cancelling"
    assert response["trace_id"] == trace_id
    assert controls["get_active"].await_count == 2
    controls["request_cancel"].assert_awaited_once()
    wait_for_release.assert_awaited_once_with(
        user_id=1,
        session_id=session_id,
        trace_id=trace_id,
        timeout=chat.settings.CANCEL_CONVERGENCE_WAIT_SECONDS,
    )
    mark_terminal.assert_not_awaited()
    mark_durable.assert_not_awaited()
    controls["mark_stopped"].assert_not_awaited()
    controls["release_lease"].assert_not_awaited()


async def test_cancel_failure_keeps_task_cancelling_and_lease_held(monkeypatch):
    session_id = "cancel-failure-session"
    trace_id = "trace-cancel-failure"
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_snapshot(
        session_id=session_id,
        user_id=1,
        trace_id=trace_id,
        status="waiting_confirmation",
        interrupt_type="tool_confirmation",
    ))
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    controls = _patch_control_layer(monkeypatch, active=_lease(trace_id))
    monkeypatch.setattr(chat, "_safe_mark_task_terminal", AsyncMock(return_value=True))
    monkeypatch.setattr(chat, "_safe_mark_durable_assistant_cancelled", AsyncMock())
    monkeypatch.setattr(chat, "_durable_cancellation_confirmed", AsyncMock(return_value=False))

    response = await chat.request_task_cancellation(session_id, 1, trace_id=trace_id)
    assert response["status"] == "cancelling"
    controls["mark_stopped"].assert_not_awaited()
    controls["release_lease"].assert_not_awaited()


@pytest.mark.parametrize("status", ["succeeded", "failed"])
async def test_cancel_does_not_overwrite_terminal_trace(status, monkeypatch):
    session_id = "terminal-session"
    trace_id = "trace-terminal"
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_snapshot(
        session_id=session_id,
        user_id=1,
        trace_id=trace_id,
        status=status,
    ))
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    controls = _patch_control_layer(monkeypatch, active=None)
    mark_durable = AsyncMock()
    monkeypatch.setattr(chat, "_safe_mark_durable_assistant_cancelled", mark_durable)

    with pytest.raises(HTTPException) as exc:
        await chat.request_task_cancellation(session_id, 1, trace_id=trace_id)
    assert exc.value.status_code == 409
    controls["request_cancel"].assert_not_awaited()
    mark_durable.assert_not_awaited()


async def test_stale_stop_cannot_cancel_new_active_trace(monkeypatch):
    session_id = "stale-session"
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_snapshot(
        session_id=session_id,
        user_id=1,
        trace_id="trace-new",
        status="running",
    ))
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    controls = _patch_control_layer(
        monkeypatch,
        active=_lease("trace-new", runner_state="running"),
    )
    with pytest.raises(HTTPException) as exc:
        await chat.request_task_cancellation(session_id, 1, trace_id="trace-old")
    assert exc.value.status_code == 409
    controls["request_cancel"].assert_not_awaited()


class _ReducingCheckpointGraph:
    def __init__(self, values):
        self.values = values
        self.updates = []

    async def aget_state(self, _config):
        return SimpleNamespace(values=self.values, tasks=[])

    async def aupdate_state(self, _config, update, **kwargs):
        self.updates.append((update, kwargs))
        if "messages" in update:
            self.values["messages"] = add_messages(
                self.values.get("messages", []),
                update["messages"],
            )
        self.values.update({key: value for key, value in update.items() if key != "messages"})


async def test_cancel_terminal_update_preserves_human_and_closes_context(monkeypatch):
    graph = _ReducingCheckpointGraph({
        "session_id": "cancel-context",
        "user_id": 1,
        "trace_id": "trace-cancel-context",
        "task_status": "running",
        "messages": [
            HumanMessage(content="你现在有什么tools", id="old-human"),
            AIMessage(
                content="",
                id="tool-request",
                tool_calls=[{"id": "call-open", "name": "bash", "args": {}}],
            ),
        ],
        "pending_tool_calls": [{"id": "call-open", "name": "bash", "args": {}}],
        "todos": [],
    })
    _patch_control_layer(monkeypatch, active=None)
    for _ in range(2):
        await chat._safe_mark_task_terminal(
            graph,
            {"configurable": {"thread_id": "cancel-context"}},
            TaskStatus.CANCELLED,
            "Cancelled by user",
            expected_trace_id="trace-cancel-context",
        )

    messages = graph.values["messages"]
    assert any(isinstance(message, HumanMessage) and message.id == "old-human" for message in messages)
    assert sum(
        isinstance(message, ToolMessage) and message.tool_call_id == "call-open"
        for message in messages
    ) == 1
    tombstones = [
        message
        for message in messages
        if isinstance(message, AIMessage)
        and message.id == "task-cancelled:trace-cancel-context"
    ]
    assert len(tombstones) == 1
    assert tombstones[0].content == chat.CANCELLATION_TOMBSTONE
    assert graph.values["task_status"] == "cancelled"
    assert graph.updates[0][1] == {"as_node": "persist_memory"}


async def test_failed_confirmation_resume_persists_suffix_timeline_on_same_trace(
    monkeypatch,
):
    session_id = "failed-resume-session"
    trace_id = "trace-failed-resume"
    graph = MagicMock()

    async def exhausted_stream(*_args, **_kwargs):
        if False:
            yield None

    graph.astream = exhausted_stream
    graph.aget_state = AsyncMock(return_value=_snapshot(
        session_id=session_id,
        user_id=1,
        trace_id=trace_id,
        status="failed",
        failure_reason="validation failed",
    ))
    controls = _patch_control_layer(monkeypatch, active=_lease(trace_id))
    persist_segment = AsyncMock(return_value=True)
    monkeypatch.setattr(chat, "_persist_stream_segment", persist_segment)

    response = chat._stream_resumed_command(
        graph=graph,
        config={"configurable": {"thread_id": session_id}},
        command=Command(resume={"approved": False, "approved_ids": []}),
        session_id=session_id,
        trace_id=trace_id,
        user_id=1,
        assistant_message_id=29,
        lease=_lease(trace_id, runner_token="runner-failed-resume"),
        log_context="test-failed-confirmation-resume",
        resume_lock_token="resume-lock-token",
    )
    payload = "".join([
        chunk.decode() if isinstance(chunk, bytes) else chunk
        async for chunk in response.body_iterator
    ])

    assert "[DONE]" not in payload
    assert '"event": "task_finished"' in payload
    assert '"status": "failed"' in payload
    assert f'"trace_id": "{trace_id}"' in payload
    assert '"error": "validation failed"' in payload
    failed_suffix = "\n\n❌ **Task failed:** validation failed"
    persist_segment.assert_awaited_once_with(
        message_id=29,
        user_id=1,
        content=failed_suffix,
        status="failed",
        timeline_entries=[{
            "role": "assistant",
            "content": failed_suffix,
        }],
    )
    controls["start"].assert_awaited_once_with(
        1,
        session_id,
        trace_id,
        "lease-1",
        "runner-failed-resume",
        ttl_seconds=chat.settings.ACTIVE_TRACE_LEASE_SECONDS,
    )
    controls["release_resume"].assert_awaited_once_with(
        1,
        session_id,
        trace_id,
        "resume-lock-token",
    )


async def test_resumed_stream_persists_same_text_tool_text_timeline(monkeypatch):
    trace_id = "trace-ordered"
    graph = MagicMock()

    async def ordered_stream(*_args, **_kwargs):
        yield ("messages", (SimpleNamespace(content="before", tool_calls=[]), {}))
        yield ("messages", (SimpleNamespace(
            content="",
            tool_calls=[{"id": "call-read", "name": "read_file"}],
        ), {}))
        yield ("updates", {"tool_executor": {
            "tool_results": {"call-read": "# Project"},
            "tool_execution_records": [{
                "tool_name": "read_file",
                "tool_call_id": "call-read",
                "status": "success",
                "ok": True,
                "output": "# Project",
                "duration_ms": 9,
                "error_code": None,
            }],
        }})
        yield ("messages", (SimpleNamespace(content="after", tool_calls=[]), {}))

    graph.astream = ordered_stream
    graph.aget_state = AsyncMock(return_value=_snapshot(
        session_id="ordered-session",
        user_id=1,
        trace_id=trace_id,
        status="succeeded",
    ))
    controls = _patch_control_layer(monkeypatch, active=_lease(trace_id))
    persist_segment = AsyncMock(return_value=True)
    monkeypatch.setattr(chat, "_persist_stream_segment", persist_segment)

    response = chat._stream_resumed_command(
        graph=graph,
        config={"configurable": {"thread_id": "ordered-session"}},
        command=Command(resume={"approved": True, "approved_ids": ["call-read"]}),
        session_id="ordered-session",
        trace_id=trace_id,
        user_id=1,
        assistant_message_id=31,
        lease=_lease(trace_id, runner_token="runner-resumed"),
        log_context="test-ordered-timeline",
        resume_lock_token="resume-lock-token",
    )
    chunks = [
        chunk.decode() if isinstance(chunk, bytes) else chunk
        async for chunk in response.body_iterator
    ]
    payload = "".join(chunks)

    assert payload.index('"delta": "before"') < payload.index('"event": "tool_start"')
    assert payload.index('"event": "tool_end"') < payload.index('"delta": "after"')
    persist_segment.assert_awaited_once_with(
        message_id=31,
        user_id=1,
        content="beforeafter",
        status="completed",
        timeline_entries=[
            {"role": "assistant", "content": "before"},
            {
                "role": "tool_call",
                "toolCallId": "call-read",
                "toolName": "read_file",
                "toolStatus": "done",
                "toolResult": "# Project",
                "toolError": "",
                "toolDuration": 9,
            },
            {"role": "assistant", "content": "after"},
        ],
    )
    controls["start"].assert_awaited_once()
    controls["release_resume"].assert_awaited_once_with(
        1,
        "ordered-session",
        trace_id,
        "resume-lock-token",
    )


async def test_mysql_cancel_tombstone_is_idempotent_and_late_stream_cannot_regress():
    message = SimpleNamespace(
        content="partial",
        status="streaming",
        continuation_receipt=None,
        updated_at=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = message
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()

    for _ in range(2):
        assert await mark_assistant_message_cancelled(
            db,
            session_id="session-db-cancel",
            user_id=1,
            trace_id="trace-db-cancel",
            tombstone=chat.CANCELLATION_TOMBSTONE,
        )
    assert message.content.count(chat.CANCELLATION_TOMBSTONE) == 1
    assert message.status == "cancelled"

    assert await update_assistant_message(
        db,
        message_id=10,
        user_id=1,
        content="late output",
        status="interrupted",
    )
    assert message.content == f"partial\n\n{chat.CANCELLATION_TOMBSTONE}"
    assert message.status == "cancelled"
