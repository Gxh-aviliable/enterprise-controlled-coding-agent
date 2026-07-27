from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from enterprise_agent.api.services import chat_history


class _WriteDb:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, value):
        self.added.append(value)

    def add_all(self, values):
        self.added.extend(values)

    async def flush(self):
        for index, value in enumerate(self.added, start=1):
            if getattr(value, "id", None) is None:
                value.id = index

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_start_turn_creates_one_user_and_one_assistant_record():
    db = _WriteDb()
    session = SimpleNamespace(
        id="session-1",
        updated_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )

    assistant_id = await chat_history.start_turn(
        db,
        session=session,
        user_id=7,
        trace_id="trace-1",
        content="hello",
    )

    assert assistant_id == 2
    assert [(message.role, message.content, message.status) for message in db.added] == [
        ("user", "hello", "completed"),
        ("assistant", "", "streaming"),
    ]
    assert {message.trace_id for message in db.added} == {"trace-1"}
    assert session.updated_at > datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert db.commits == 1


@pytest.mark.asyncio
async def test_legacy_checkpoint_migration_is_deterministic(monkeypatch):
    db = _WriteDb()

    async def no_existing_messages(_db, *, session_id, user_id):
        assert session_id == "legacy-session"
        assert user_id == 9
        return []

    monkeypatch.setattr(chat_history, "list_messages", no_existing_messages)

    migrated = await chat_history.persist_legacy_messages(
        db,
        session_id="legacy-session",
        user_id=9,
        messages=[
            {"role": "user", "content": "old request"},
            {"role": "assistant", "content": "old answer"},
            {"role": "tool", "content": "not frontend history"},
        ],
    )

    assert migrated == 2
    assert [message.source for message in db.added] == ["redis_migration", "redis_migration"]
    assert len({message.trace_id for message in db.added}) == 2
    assert db.commits == 1


def test_partial_history_status_is_exposed_after_a_legacy_gap():
    from enterprise_agent.api.routes.chat import _history_status

    session = SimpleNamespace(
        session_metadata={"history_gap": True},
        created_at=datetime.now(timezone.utc),
    )

    assert _history_status(session, durable_count=2) == "partial"
