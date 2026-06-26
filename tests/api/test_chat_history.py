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
    )


@pytest.mark.asyncio
async def test_list_sessions_hides_empty_checkpoint_sessions(monkeypatch):
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

    result = await chat.list_sessions(user_id=1, db=_FakeDb([empty, filled]))

    assert [session.id for session in result] == ["filled-session"]
    assert result[0].message_count == 2


@pytest.mark.asyncio
async def test_get_session_messages_returns_message_count_for_empty_session(monkeypatch):
    from enterprise_agent.api.routes import chat

    session = _session("empty-session", "empty")
    monkeypatch.setattr(chat, "get_agent_graph", lambda: _FakeGraph({"empty-session": []}))

    result = await chat.get_session_messages(
        session_id="empty-session",
        user_id=1,
        db=_FakeDb(session),
    )

    assert result == {
        "session_id": "empty-session",
        "title": "empty",
        "message_count": 0,
        "messages": [],
    }
