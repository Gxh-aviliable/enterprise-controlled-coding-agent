"""Tests for session history loading and empty-session filtering."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from enterprise_agent.models.session import SessionStatus


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def all(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _ExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return _ScalarResult(self._value)

    def scalar_one_or_none(self):
        return self._value


class _FakeDb:
    def __init__(self, value):
        self.value = value

    async def execute(self, _query):
        return _ExecuteResult(self.value)


class _FakeGraph:
    def __init__(self, messages_by_session):
        self.messages_by_session = messages_by_session

    async def aget_state(self, config):
        session_id = config["configurable"]["thread_id"]
        return SimpleNamespace(values={"messages": self.messages_by_session.get(session_id, [])})


def _session(session_id, title="title"):
    return SimpleNamespace(
        id=session_id,
        user_id=1,
        title=title,
        status=SessionStatus.ACTIVE,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        session_metadata={},
    )


@pytest.mark.asyncio
async def test_list_sessions_keeps_empty_or_expired_mysql_sessions(monkeypatch):
    from enterprise_agent.api.routes import chat

    empty = _session("empty-session", "empty")
    filled = _session("filled-session", "filled")
    monkeypatch.setattr(
        chat,
        "get_agent_graph",
        lambda: _FakeGraph(
            {
                "empty-session": [],
                "filled-session": [
                    HumanMessage(content="hello"),
                    AIMessage(content="hi"),
                ],
            }
        ),
    )
    async def no_durable_messages(_db, *, user_id):
        assert user_id == 1
        return {}

    monkeypatch.setattr(chat, "message_counts_by_session", no_durable_messages)

    result = await chat.list_sessions(user_id=1, db=_FakeDb([empty, filled]))

    assert [session.id for session in result] == ["empty-session", "filled-session"]
    assert result[0].message_count == 0
    assert result[0].history_status == "empty"
    assert result[1].message_count == 2
    assert result[1].history_status == "checkpoint"


@pytest.mark.asyncio
async def test_get_session_messages_returns_message_count_for_empty_session(monkeypatch):
    from enterprise_agent.api.routes import chat

    session = _session("empty-session", "empty")
    monkeypatch.setattr(chat, "get_agent_graph", lambda: _FakeGraph({"empty-session": []}))
    async def no_durable_messages(_db, *, session_id, user_id):
        assert session_id == "empty-session"
        assert user_id == 1
        return []

    monkeypatch.setattr(chat, "list_durable_messages", no_durable_messages)

    result = await chat.get_session_messages(
        session_id="empty-session",
        user_id=1,
        db=_FakeDb(session),
    )

    assert result == {
        "session_id": "empty-session",
        "title": "empty",
        "message_count": 0,
        "history_status": "empty",
        "messages": [],
    }


@pytest.mark.asyncio
async def test_get_session_messages_prefers_durable_mysql_history(monkeypatch):
    from enterprise_agent.api.routes import chat

    session = _session("durable-session", "durable")
    durable = [
        SimpleNamespace(role="user", content="persisted request"),
        SimpleNamespace(role="assistant", content="persisted answer"),
    ]

    async def durable_messages(_db, *, session_id, user_id):
        assert session_id == "durable-session"
        assert user_id == 1
        return durable

    monkeypatch.setattr(chat, "list_durable_messages", durable_messages)
    monkeypatch.setattr(
        chat,
        "_load_history_messages",
        lambda *_args, **_kwargs: pytest.fail("Redis fallback must not run for durable history"),
    )

    result = await chat.get_session_messages(
        session_id="durable-session",
        user_id=1,
        db=_FakeDb(session),
    )

    assert result["history_status"] == "durable"
    assert result["message_count"] == 2
    assert result["messages"] == [
        {"role": "user", "content": "persisted request"},
        {"role": "assistant", "content": "persisted answer"},
    ]


@pytest.mark.asyncio
async def test_get_session_messages_keeps_tool_only_assistant_timeline(monkeypatch):
    from enterprise_agent.api.routes import chat

    session = _session("tool-session", "tool")
    durable = [
        SimpleNamespace(role="user", content="run the tool", timeline=None),
        SimpleNamespace(
            role="assistant",
            content="",
            timeline=[{
                "role": "tool_call",
                "toolCallId": "call-1",
                "toolName": "bash",
                "toolStatus": "done",
                "toolResult": "ok",
            }],
        ),
    ]

    async def durable_messages(_db, *, session_id, user_id):
        assert session_id == "tool-session"
        assert user_id == 1
        return durable

    monkeypatch.setattr(chat, "list_durable_messages", durable_messages)

    result = await chat.get_session_messages(
        session_id="tool-session",
        user_id=1,
        db=_FakeDb(session),
    )

    assert result["message_count"] == 2
    assert result["messages"] == [
        {"role": "user", "content": "run the tool"},
        {
            "role": "assistant",
            "content": "",
            "timeline": [{
                "role": "tool_call",
                "toolCallId": "call-1",
                "toolName": "bash",
                "toolStatus": "done",
                "toolResult": "ok",
                "toolError": "",
                "toolDuration": None,
            }],
        },
    ]
