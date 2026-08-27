"""Durable context coverage for terminal cancel-and-replan flows."""

from types import SimpleNamespace

import pytest

from enterprise_agent.api.services.chat_history import (
    build_model_history,
    claim_latest_continuation_receipt,
)


def _message(
    role: str,
    content: str,
    *,
    trace_id: str,
) -> SimpleNamespace:
    return SimpleNamespace(role=role, content=content, trace_id=trace_id)


def test_build_model_history_deduplicates_rows_and_drops_unanchored_assistant():
    messages = [
        _message("assistant", "orphaned tail", trace_id="trace-old"),
        _message("user", "inspect workspace", trace_id="trace-1"),
        _message("user", "inspect workspace", trace_id="trace-1"),
        _message("user", "inspect workspace", trace_id="trace-duplicate"),
        _message("assistant", "workspace inspected", trace_id="trace-1"),
        _message("tool", "must not enter model history", trace_id="trace-1"),
        _message("assistant", "   ", trace_id="trace-1"),
    ]

    history = build_model_history(
        messages,
        max_messages=20,
        max_characters=1_000,
    )

    assert history == [
        {"role": "user", "content": "inspect workspace"},
        {"role": "assistant", "content": "workspace inspected"},
    ]


def test_build_model_history_enforces_message_and_character_bounds_from_newest_rows():
    messages = [
        _message("user", "discarded request", trace_id="trace-1"),
        _message("assistant", "discarded answer", trace_id="trace-1"),
        _message("user", "abcdefghij", trace_id="trace-2"),
    ]

    history = build_model_history(
        messages,
        max_messages=2,
        max_characters=4,
    )

    assert history == [{"role": "user", "content": "ghij"}]


class _ReceiptRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ReceiptResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _ReceiptRows(self._rows)


class _ReceiptDb:
    """Small query-aware fake that enforces the SQL Session/user boundary."""

    def __init__(self, rows_by_owner):
        self.rows_by_owner = rows_by_owner
        self.executed_owners = []
        self.commits = 0

    async def execute(self, query):
        params = query.compile().params
        session_id = next(
            value for key, value in params.items() if key.startswith("session_id")
        )
        user_id = next(value for key, value in params.items() if key.startswith("user_id"))
        owner = (session_id, user_id)
        self.executed_owners.append(owner)
        return _ReceiptResult(self.rows_by_owner.get(owner, []))

    async def commit(self):
        self.commits += 1


def _cancelled_assistant(trace_id: str, original_task: str) -> SimpleNamespace:
    return SimpleNamespace(
        role="assistant",
        status="cancelled",
        continuation_receipt={
            "trace_id": trace_id,
            "original_task": original_task,
            "completed_items": [],
            "unfinished_items": [original_task],
            "modified_files": [],
            "verification_results": [],
            "risks": [],
        },
        updated_at=None,
    )


@pytest.mark.asyncio
async def test_continuation_receipt_is_claimed_once_and_isolated_by_session():
    first_session_receipt = _cancelled_assistant("trace-a", "task for session A")
    second_session_receipt = _cancelled_assistant("trace-b", "task for session B")
    db = _ReceiptDb({
        ("session-a", 7): [first_session_receipt],
        ("session-b", 7): [second_session_receipt],
    })

    claimed_a = await claim_latest_continuation_receipt(
        db,
        session_id="session-a",
        user_id=7,
        consumer_trace_id="trace-a-replan",
    )
    claimed_a_again = await claim_latest_continuation_receipt(
        db,
        session_id="session-a",
        user_id=7,
        consumer_trace_id="trace-a-overlap",
    )

    assert claimed_a["original_task"] == "task for session A"
    assert claimed_a_again is None
    assert first_session_receipt.continuation_receipt["consumed_by_trace_id"] == (
        "trace-a-replan"
    )
    assert "consumed_by_trace_id" not in second_session_receipt.continuation_receipt

    claimed_b = await claim_latest_continuation_receipt(
        db,
        session_id="session-b",
        user_id=7,
        consumer_trace_id="trace-b-replan",
    )

    assert claimed_b["original_task"] == "task for session B"
    assert second_session_receipt.continuation_receipt["consumed_by_trace_id"] == (
        "trace-b-replan"
    )
    assert db.executed_owners == [
        ("session-a", 7),
        ("session-a", 7),
        ("session-b", 7),
    ]
    assert db.commits == 3


class _ExpiredCheckpointGraph:
    def __init__(self):
        self.requested_sessions = []

    async def aget_state(self, config):
        self.requested_sessions.append(config["configurable"]["thread_id"])
        return SimpleNamespace(values={})


@pytest.mark.asyncio
async def test_expired_checkpoint_injects_same_session_history_into_fresh_trace(
    monkeypatch,
):
    from enterprise_agent.api.routes import chat

    graph = _ExpiredCheckpointGraph()
    durable_rows = [
        _message("user", "build the report", trace_id="trace-cancelled"),
        _message("assistant", "draft created", trace_id="trace-cancelled"),
    ]
    receipt = {
        "trace_id": "trace-cancelled",
        "original_task": "build the report",
        "completed_items": ["draft created"],
        "unfinished_items": ["run validation"],
        "modified_files": ["report.md"],
        "verification_results": [],
        "risks": ["validation pending"],
    }

    async def list_rows(_db, *, session_id, user_id):
        assert (session_id, user_id) == ("session-1", 7)
        return durable_rows

    async def claim_receipt(_db, *, session_id, user_id, consumer_trace_id):
        assert (session_id, user_id, consumer_trace_id) == (
            "session-1",
            7,
            "trace-replan",
        )
        return receipt

    monkeypatch.setattr(chat, "list_durable_messages", list_rows)
    monkeypatch.setattr(chat, "claim_latest_continuation_receipt", claim_receipt)

    history, claimed_receipt = await chat._claim_new_task_context(
        object(),
        session_id="session-1",
        user_id=7,
        trace_id="trace-replan",
        graph=graph,
    )
    task_input = chat._task_input(
        session_id="session-1",
        trace_id="trace-replan",
        user_id=7,
        permissions=[],
        content="continue after checking the real workspace",
        history_messages=history,
        continuation_receipt=claimed_receipt,
    )

    assert graph.requested_sessions == ["session-1"]
    assert task_input["trace_id"] == "trace-replan"
    assert task_input["trace_id"] != receipt["trace_id"]
    assert task_input["messages"] == [
        {"role": "user", "content": "build the report"},
        {"role": "assistant", "content": "draft created"},
        {"role": "user", "content": "continue after checking the real workspace"},
    ]
    assert task_input["continuation_receipt"] == receipt


@pytest.mark.asyncio
async def test_new_session_does_not_inherit_another_sessions_goal(monkeypatch):
    from enterprise_agent.api.routes import chat

    graph = _ExpiredCheckpointGraph()
    old_session_rows = [
        _message("user", "secret old objective", trace_id="trace-old"),
    ]
    old_receipt = {
        "trace_id": "trace-old",
        "original_task": "secret old objective",
    }

    async def list_rows(_db, *, session_id, user_id):
        assert user_id == 7
        return old_session_rows if session_id == "session-old" else []

    async def claim_receipt(_db, *, session_id, user_id, consumer_trace_id):
        assert user_id == 7
        assert consumer_trace_id == "trace-new"
        return old_receipt if session_id == "session-old" else None

    monkeypatch.setattr(chat, "list_durable_messages", list_rows)
    monkeypatch.setattr(chat, "claim_latest_continuation_receipt", claim_receipt)

    history, receipt = await chat._claim_new_task_context(
        object(),
        session_id="session-new",
        user_id=7,
        trace_id="trace-new",
        graph=graph,
    )
    task_input = chat._task_input(
        session_id="session-new",
        trace_id="trace-new",
        user_id=7,
        permissions=[],
        content="independent new objective",
        history_messages=history,
        continuation_receipt=receipt,
    )

    assert graph.requested_sessions == ["session-new"]
    assert task_input["messages"] == [
        {"role": "user", "content": "independent new objective"},
    ]
    assert task_input["continuation_receipt"] is None
    assert "secret old objective" not in str(task_input)
