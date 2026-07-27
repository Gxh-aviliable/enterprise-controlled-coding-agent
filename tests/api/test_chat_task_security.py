"""Session ownership and async checkpoint API regressions."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from enterprise_agent.api.routes import chat
from enterprise_agent.api.schemas.chat import ChatRequest
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


async def test_confirm_uses_async_graph_api_and_owned_session(monkeypatch):
    session = Session(id="session-1", user_id=1, status=SessionStatus.ACTIVE)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(values={
        "confirmation_deadline": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
    }))
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


async def test_confirmation_timeout_resumes_with_rejection(monkeypatch):
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(values={
        "task_status": "waiting_confirmation",
    }))
    graph.ainvoke = AsyncMock(return_value={})
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)

    past_deadline = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    chat._schedule_confirmation_timeout("timeout-session", 9, past_deadline)
    await asyncio.wait_for(chat._confirmation_timeout_tasks["timeout-session"], timeout=1)

    graph.ainvoke.assert_awaited_once()
    command = graph.ainvoke.await_args.args[0]
    assert command.resume["reason"] == "confirmation_timeout"
