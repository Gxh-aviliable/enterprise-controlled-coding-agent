"""Session ownership and async checkpoint API regressions."""

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
from enterprise_agent.api.schemas.chat import ChatRequest
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


async def test_checkpoint_routes_hide_unknown_or_foreign_session():
    with pytest.raises(HTTPException) as exc:
        await chat._require_owned_session("foreign", 7, _owned_db(None))
    assert exc.value.status_code == 404


async def test_missing_chat_session_is_created_for_authenticated_user():
    db = _owned_db()
    request = ChatRequest(content="Inspect this repository")
    session_id = await chat._resolve_chat_session(request, 42, db)

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
    assert "Select Multi" in exc.value.detail


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


def test_tool_sse_events_expose_stored_artifact_metadata():
    events = chat._tool_sse_events({
        "tool_results": {"call-stored": "bounded preview"},
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
        assert event["artifact_sha256"] == "a" * 64
        assert "artifact_error" not in event


def test_tool_sse_events_expose_failed_artifact_metadata_without_breaking_result():
    events = chat._tool_sse_events({
        "tool_results": {"call-failed": "complete short fallback"},
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


async def test_confirm_uses_async_graph_api_and_owned_session(monkeypatch):
    session = Session(id="session-1", user_id=1, status=SessionStatus.ACTIVE)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(
        values={
            "session_id": "session-1",
            "user_id": 1,
            "trace_id": "trace-confirm",
            "task_status": "waiting_confirmation",
            "confirmation_deadline": (
                datetime.now(timezone.utc) + timedelta(minutes=1)
            ).isoformat(),
        },
        tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={
            "type": "tool_confirmation",
            "trace_id": "trace-confirm",
            "tools": [],
        })])],
    ))
    graph.ainvoke = AsyncMock(return_value={})
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)

    response = await chat.confirm_tool(
        session_id="session-1",
        approved=True,
        approved_ids=["tool-1"],
        user_id=1,
        db=_owned_db(session),
    )

    assert response["status"] == "resumed"
    graph.aget_state.assert_awaited_once()
    graph.ainvoke.assert_awaited_once()


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
    graph.aget_state.assert_awaited_once()


def test_internal_stream_filters_are_isolated_per_request():
    first = chat.InternalStreamFilter()
    second = chat.InternalStreamFilter()

    assert first.is_internal_json("[User Request]: internal summary") is True
    assert second.is_internal_json("normal user-visible token") is False
    assert first.is_internal_json("still internal") is True


def test_user_pause_interrupt_has_dedicated_sse_event_without_confirmation_timeout(
    monkeypatch,
):
    schedule_timeout = MagicMock()
    monkeypatch.setattr(chat, "_schedule_confirmation_timeout", schedule_timeout)
    payload = {
        "type": "user_pause",
        "trace_id": "trace-pause-sse",
        "resume_target": "tool_executor",
        "reason": "Inspect intermediate state",
    }

    event, assistant_status = chat._stream_interrupt_event(
        interrupt_obj=(SimpleNamespace(value=payload),),
        session_id="session-pause-sse",
        trace_id="trace-pause-sse",
        user_id=17,
    )

    assert event == {
        "event": "paused",
        "session_id": "session-pause-sse",
        "trace_id": "trace-pause-sse",
        "status": "paused",
        "data": payload,
    }
    assert assistant_status == "paused"
    schedule_timeout.assert_not_called()


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
    schedule_timeout.assert_called_once_with(
        "session-confirm-sse",
        "trace-confirm-sse",
        18,
        "2026-08-10T12:00:00+00:00",
    )


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


async def test_confirmation_timeout_resumes_with_rejection(monkeypatch):
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(
        values={
            "session_id": "timeout-session",
            "user_id": 9,
            "trace_id": "trace-timeout",
            "task_status": "waiting_confirmation",
        },
        tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={
            "type": "tool_confirmation",
            "trace_id": "trace-timeout",
            "tools": [],
        })])],
    ))
    graph.ainvoke = AsyncMock(return_value={})
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    monkeypatch.setattr(chat, "find_assistant_message_id", AsyncMock(return_value=None))

    past_deadline = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    chat._schedule_confirmation_timeout(
        "timeout-session",
        "trace-timeout",
        9,
        past_deadline,
    )
    await asyncio.wait_for(chat._confirmation_timeout_tasks["timeout-session"], timeout=1)

    graph.ainvoke.assert_awaited_once()
    command = graph.ainvoke.await_args.args[0]
    assert command.resume["reason"] == "confirmation_timeout"


def _paused_snapshot(trace_id: str, interrupt_type: str = "user_pause"):
    interrupt = SimpleNamespace(value={
        "type": interrupt_type,
        "trace_id": trace_id,
        "resume_target": "tool_executor",
    })
    return SimpleNamespace(
        values={
            "task_status": "paused",
            "trace_id": trace_id,
            "user_id": 1,
            "session_id": "pause-session",
        },
        tasks=[SimpleNamespace(interrupts=[interrupt])],
    )


def _terminal_snapshot(
    trace_id: str,
    status: str,
    *,
    failure_reason: str | None = None,
):
    return SimpleNamespace(
        values={
            "task_status": status,
            "trace_id": trace_id,
            "user_id": 1,
            "session_id": "pause-session",
            "failure_reason": failure_reason,
        },
        tasks=[],
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
    values = _terminal_snapshot(
        "trace-terminal",
        task_status,
        failure_reason=failure_reason,
    ).values

    assistant_status, reason = chat._task_terminal_outcome(
        values,
        session_id="pause-session",
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
    values = _terminal_snapshot("trace-terminal", "succeeded").values
    values.update(overrides)

    assistant_status, reason = chat._task_terminal_outcome(
        values,
        session_id="pause-session",
        user_id=1,
        trace_id="trace-terminal",
    )

    assert assistant_status == "failed"
    assert reason


@pytest.mark.parametrize(
    "status",
    ["pending", "running", "paused", "waiting_confirmation"],
)
async def test_new_task_cannot_overwrite_a_nonterminal_checkpoint(status):
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(
        values={
            "task_status": status,
            "trace_id": "trace-existing",
            "user_id": 1,
            "session_id": "protected-session",
        },
        tasks=[],
    ))

    with pytest.raises(HTTPException) as exc:
        await chat._ensure_session_accepts_new_task(
            graph,
            session_id="protected-session",
            user_id=1,
        )

    assert exc.value.status_code == 409
    assert "resume or cancel" in str(exc.value.detail)


@pytest.mark.parametrize("operation", ["pause", "status", "continue"])
async def test_pause_control_routes_hide_unknown_or_foreign_session(operation):
    db = _owned_db(None)

    with pytest.raises(HTTPException) as exc:
        if operation == "pause":
            await chat.request_task_pause_endpoint(
                session_id="foreign",
                trace_id="trace-foreign",
                reason=None,
                user_id=7,
                db=db,
            )
        elif operation == "status":
            await chat.get_stream_status(
                session_id="foreign",
                user_id=7,
                db=db,
            )
        else:
            await chat.continue_paused_stream(
                session_id="foreign",
                trace_id="trace-foreign",
                user_id=7,
                db=db,
            )

    assert exc.value.status_code == 404


async def test_pause_request_is_bound_to_owned_session_and_current_trace(monkeypatch):
    session = Session(id="pause-session", user_id=1, status=SessionStatus.ACTIVE)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(
        values={
            "session_id": "pause-session",
            "user_id": 1,
            "task_status": "running",
            "trace_id": "trace-current",
        },
        tasks=[],
    ))
    request_pause = AsyncMock(return_value={"requested": True})
    trace_store = MagicMock()
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    monkeypatch.setattr(chat, "request_task_pause", request_pause)
    monkeypatch.setattr(chat, "get_trace_store", lambda: trace_store)

    response = await chat.request_task_pause_endpoint(
        session_id="pause-session",
        trace_id="trace-current",
        reason="Pause at the next safe boundary",
        user_id=1,
        db=_owned_db(session),
    )

    assert response == {
        "status": "pause_requested",
        "session_id": "pause-session",
        "trace_id": "trace-current",
    }
    request_pause.assert_awaited_once()
    assert request_pause.await_args.args == (1, "pause-session", "trace-current")
    assert request_pause.await_args.kwargs == {
        "reason": "Pause at the next safe boundary",
    }
    trace_store.record_event.assert_called_once()
    event = trace_store.record_event.call_args.kwargs
    assert event["user_id"] == 1
    assert event["trace_id"] == "trace-current"
    assert event["event_type"] == "control"
    assert event["name"] == "pause_requested"
    assert event["status"] == "requested"
    assert event["data"]["task_status"] == "running"
    assert event["data"]["reason"] == "Pause at the next safe boundary"


async def test_pause_request_rejects_stale_trace_id(monkeypatch):
    session = Session(id="pause-session", user_id=1, status=SessionStatus.ACTIVE)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(
        values={"task_status": "running", "trace_id": "trace-current"},
        tasks=[],
    ))
    request_pause = AsyncMock()
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    monkeypatch.setattr(chat, "request_task_pause", request_pause)

    with pytest.raises(HTTPException) as exc:
        await chat.request_task_pause_endpoint(
            session_id="pause-session",
            trace_id="trace-stale",
            reason=None,
            user_id=1,
            db=_owned_db(session),
        )

    assert exc.value.status_code == 409
    assert "trace" in str(exc.value.detail).lower()
    request_pause.assert_not_awaited()


async def test_stream_status_reports_durable_user_pause_interrupt(monkeypatch):
    session = Session(id="pause-session", user_id=1, status=SessionStatus.ACTIVE)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_paused_snapshot("trace-paused"))
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)

    response = await chat.get_stream_status(
        session_id="pause-session",
        user_id=1,
        db=_owned_db(session),
    )

    assert response["status"] == "paused"
    assert response["trace_id"] == "trace-paused"
    assert response["interrupt_type"] == "user_pause"
    assert response["interrupt"]["resume_target"] == "tool_executor"


async def test_continue_rejects_stale_trace_and_wrong_interrupt_type(monkeypatch):
    session = Session(id="pause-session", user_id=1, status=SessionStatus.ACTIVE)
    graph = MagicMock()
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)

    graph.aget_state = AsyncMock(return_value=_paused_snapshot("trace-current"))
    with pytest.raises(HTTPException) as stale_exc:
        await chat.continue_paused_stream(
            session_id="pause-session",
            trace_id="trace-stale",
            user_id=1,
            db=_owned_db(session),
        )
    assert stale_exc.value.status_code == 409
    assert "trace" in str(stale_exc.value.detail).lower()

    graph.aget_state = AsyncMock(return_value=_paused_snapshot(
        "trace-current",
        interrupt_type="tool_confirmation",
    ))
    with pytest.raises(HTTPException) as type_exc:
        await chat.continue_paused_stream(
            session_id="pause-session",
            trace_id="trace-current",
            user_id=1,
            db=_owned_db(session),
        )
    assert type_exc.value.status_code == 409
    assert "user_pause" in str(type_exc.value.detail)


async def test_confirmation_resume_cannot_consume_user_pause_interrupt(monkeypatch):
    session = Session(id="pause-session", user_id=1, status=SessionStatus.ACTIVE)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_paused_snapshot("trace-current"))
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)

    with pytest.raises(HTTPException) as exc:
        await chat.chat_stream_resume(
            session_id="pause-session",
            approved=True,
            body=None,
            user_id=1,
            db=_owned_db(session),
        )

    assert exc.value.status_code == 409
    assert "tool_confirmation" in str(exc.value.detail)


async def test_continue_resumes_exact_paused_trace_with_dedicated_payload(monkeypatch):
    session = Session(id="pause-session", user_id=1, status=SessionStatus.ACTIVE)
    graph = MagicMock()
    graph.aget_state = AsyncMock(side_effect=[
        _paused_snapshot("trace-current"),
        _terminal_snapshot("trace-current", "succeeded"),
    ])
    captured = {}

    async def fake_stream(command, *, config, stream_mode):
        captured["command"] = command
        captured["config"] = config
        captured["stream_mode"] = stream_mode
        yield ("updates", {"user_pause": {"task_status": "running"}})

    graph.astream = fake_stream
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    monkeypatch.setattr(chat, "find_assistant_message_id", AsyncMock(return_value=19))
    monkeypatch.setattr(chat, "update_assistant_message", AsyncMock(return_value=True))
    persist_segment = AsyncMock()
    monkeypatch.setattr(chat, "_persist_stream_segment", persist_segment)
    acquire_lock = AsyncMock(return_value=True)
    monkeypatch.setattr(chat, "acquire_task_resume_lock", acquire_lock)
    release_lock = AsyncMock(return_value=True)
    monkeypatch.setattr(chat, "release_task_resume_lock", release_lock)
    monkeypatch.setattr(chat, "get_trace_store", MagicMock(return_value=MagicMock()))

    response = await chat.continue_paused_stream(
        session_id="pause-session",
        trace_id="trace-current",
        user_id=1,
        db=_owned_db(session),
    )
    chunks = [chunk async for chunk in response.body_iterator]

    assert captured["command"].resume == {
        "action": "continue",
        "trace_id": "trace-current",
    }
    assert captured["config"] == {"configurable": {"thread_id": "pause-session"}}
    assert captured["stream_mode"] == ["messages", "updates"]
    assert any("[DONE]" in (chunk.decode() if isinstance(chunk, bytes) else chunk) for chunk in chunks)
    assert graph.aget_state.await_count == 2
    persist_segment.assert_awaited_once_with(
        message_id=19,
        user_id=1,
        content="",
        status="completed",
    )
    acquire_lock.assert_awaited_once()
    release_lock.assert_awaited_once()


async def test_resumed_stream_normal_exhaustion_uses_failed_checkpoint(monkeypatch):
    graph = MagicMock()

    async def exhausted_stream(*_args, **_kwargs):
        if False:
            yield None

    graph.astream = exhausted_stream
    graph.aget_state = AsyncMock(return_value=_terminal_snapshot(
        "trace-failed",
        "failed",
        failure_reason="validation failed",
    ))
    persist_segment = AsyncMock()
    monkeypatch.setattr(chat, "_persist_stream_segment", persist_segment)
    monkeypatch.setattr(chat, "clear_task_pause_request", AsyncMock(return_value=True))

    response = chat._stream_resumed_command(
        graph=graph,
        config={"configurable": {"thread_id": "pause-session"}},
        command=Command(resume={"action": "continue", "trace_id": "trace-failed"}),
        session_id="pause-session",
        trace_id="trace-failed",
        user_id=1,
        assistant_message_id=29,
        log_context="test-failed-exhaustion",
    )
    chunks = [chunk async for chunk in response.body_iterator]
    payload = "".join(
        chunk.decode() if isinstance(chunk, bytes) else chunk
        for chunk in chunks
    )

    assert "[DONE]" not in payload
    assert '"event": "task_finished"' in payload
    assert '"status": "failed"' in payload
    assert '"task_status": "failed"' in payload
    assert '"error": "validation failed"' in payload
    persist_segment.assert_awaited_once_with(
        message_id=29,
        user_id=1,
        content="\n\n❌ **Task failed:** validation failed",
        status="failed",
    )


async def test_cancel_paused_task_uses_user_pause_cancel_payload(monkeypatch):
    graph = MagicMock()
    paused = _paused_snapshot("trace-current")
    cancelled = SimpleNamespace(
        values={**paused.values, "task_status": "cancelled"},
        tasks=[],
    )
    graph.aget_state = AsyncMock(side_effect=[paused, paused, cancelled])
    graph.ainvoke = AsyncMock(return_value={"task_status": "cancelled"})
    graph.aupdate_state = AsyncMock()
    mark_durable = AsyncMock()
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    monkeypatch.setattr(chat, "_safe_mark_durable_assistant_cancelled", mark_durable)
    monkeypatch.setattr(chat, "clear_task_pause_request", AsyncMock(return_value=True))
    monkeypatch.setattr(chat, "get_trace_store", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(
        "enterprise_agent.core.agent.tools.background.clear_background_manager",
        lambda _session_id: None,
    )

    response = await chat.request_task_cancellation(
        session_id="pause-session",
        user_id=1,
        reason="Cancelled while paused",
    )
    await asyncio.sleep(0)

    assert response["status"] == "cancelled"
    graph.ainvoke.assert_awaited_once()
    command = graph.ainvoke.await_args.args[0]
    assert command.resume == {
        "action": "cancel",
        "trace_id": "trace-current",
        "reason": "task_cancelled",
    }
    mark_durable.assert_awaited_once()


class _ReducingCheckpointGraph:
    def __init__(self, values):
        self.values = values
        self.updates = []

    async def aget_state(self, _config):
        return SimpleNamespace(values=self.values, tasks=[])

    async def aupdate_state(self, _config, update):
        self.updates.append(update)
        if "messages" in update:
            self.values["messages"] = add_messages(
                self.values.get("messages", []),
                update["messages"],
            )
        self.values.update({key: value for key, value in update.items() if key != "messages"})


async def test_cancel_terminal_update_preserves_human_and_idempotently_closes_context(
    monkeypatch,
):
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
    monkeypatch.setattr(chat, "clear_task_pause_request", AsyncMock(return_value=True))
    monkeypatch.setattr(chat, "get_trace_store", MagicMock(return_value=MagicMock()))

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
        message for message in messages
        if isinstance(message, AIMessage)
        and message.id == "task-cancelled:trace-cancel-context"
    ]
    assert len(tombstones) == 1
    assert tombstones[0].content == chat.CANCELLATION_TOMBSTONE
    assert graph.values["task_status"] == "cancelled"


async def test_cancel_tool_confirmation_awaits_convergence_and_durable_tombstone(
    monkeypatch,
):
    graph = MagicMock()
    paused = _paused_snapshot(
        "trace-current",
        interrupt_type="tool_confirmation",
    )
    cancelled = SimpleNamespace(
        values={**paused.values, "task_status": "cancelled"},
        tasks=[],
    )
    graph.aget_state = AsyncMock(side_effect=[paused, cancelled])
    graph.ainvoke = AsyncMock(return_value={"task_status": "cancelled"})
    mark_terminal = AsyncMock()
    mark_durable = AsyncMock()
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    monkeypatch.setattr(chat, "_safe_mark_task_terminal", mark_terminal)
    monkeypatch.setattr(chat, "_safe_mark_durable_assistant_cancelled", mark_durable)
    monkeypatch.setattr(chat, "clear_task_pause_request", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "enterprise_agent.core.agent.tools.background.clear_background_manager",
        lambda _session_id: None,
    )

    response = await chat.request_task_cancellation(
        session_id="pause-session",
        user_id=1,
        reason="Cancelled while waiting",
    )

    assert response["status"] == "cancelled"
    command = graph.ainvoke.await_args.args[0]
    assert command.resume == {
        "approved": False,
        "approved_ids": [],
        "reason": "task_cancelled",
    }
    mark_terminal.assert_awaited_once()
    mark_durable.assert_awaited_once_with(
        session_id="pause-session",
        user_id=1,
        trace_id="trace-current",
    )


async def test_cancel_exact_active_trace_before_its_first_checkpoint(monkeypatch):
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(
        values={
            "session_id": "early-stop-session",
            "user_id": 1,
            "trace_id": "older-terminal-trace",
            "task_status": "succeeded",
        },
        tasks=[],
    ))
    cancel_event = asyncio.Event()
    mark_durable = AsyncMock()
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    monkeypatch.setattr(chat, "_safe_mark_durable_assistant_cancelled", mark_durable)
    monkeypatch.setattr(chat, "clear_task_pause_request", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "enterprise_agent.core.agent.tools.background.clear_background_manager",
        lambda _session_id: None,
    )
    chat._active_stream_traces["early-stop-session"] = "new-active-trace"
    chat._cancel_events["new-active-trace"] = cancel_event

    try:
        response = await chat.request_task_cancellation(
            session_id="early-stop-session",
            user_id=1,
            trace_id="new-active-trace",
        )
    finally:
        chat._active_stream_traces.pop("early-stop-session", None)
        chat._cancel_events.pop("new-active-trace", None)

    assert response["status"] == "cancelled"
    assert cancel_event.is_set()
    mark_durable.assert_awaited_once_with(
        session_id="early-stop-session",
        user_id=1,
        trace_id="new-active-trace",
    )


@pytest.mark.parametrize("status", ["succeeded", "failed"])
async def test_cancel_does_not_overwrite_an_already_terminal_trace(status, monkeypatch):
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(
        values={
            "session_id": "terminal-session",
            "user_id": 1,
            "trace_id": "trace-terminal",
            "task_status": status,
        },
        tasks=[],
    ))
    mark_durable = AsyncMock()
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    monkeypatch.setattr(chat, "_safe_mark_durable_assistant_cancelled", mark_durable)

    with pytest.raises(HTTPException) as exc:
        await chat.request_task_cancellation(
            session_id="terminal-session",
            user_id=1,
            trace_id="trace-terminal",
        )

    assert exc.value.status_code == 409
    mark_durable.assert_not_awaited()


async def test_stream_cancel_race_preserves_succeeded_checkpoint(monkeypatch):
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(
        values={
            "session_id": "race-session",
            "user_id": 1,
            "trace_id": "trace-race",
            "task_status": "succeeded",
        },
        tasks=[],
    ))

    status = await chat._converge_stream_cancellation(
        graph,
        {"configurable": {"thread_id": "race-session"}},
        "trace-race",
        "interrupted",
    )

    assert status == "completed"
    graph.aupdate_state.assert_not_called()


async def test_mysql_cancel_tombstone_is_idempotent_and_late_stream_cannot_regress():
    message = SimpleNamespace(content="partial", status="streaming", updated_at=None)
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
