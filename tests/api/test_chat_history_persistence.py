from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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


def _locked_message_db(message):
    result = MagicMock()
    result.scalar_one_or_none.return_value = message
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    return db


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


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled"])
@pytest.mark.parametrize("late_status", ["interrupted", "completed"])
@pytest.mark.parametrize("append", [True, False], ids=["append", "replace"])
async def test_terminal_assistant_message_ignores_late_stream_persistence(
    terminal_status,
    late_status,
    append,
):
    """A stale SSE finalizer cannot rewrite or append to a terminal response."""
    assistant = SimpleNamespace(
        content="authoritative terminal response",
        status=terminal_status,
        updated_at=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = assistant
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()

    updated = await chat_history.update_assistant_message(
        db,
        message_id=17,
        user_id=7,
        content=" stale late segment",
        status=late_status,
        append=append,
    )

    assert updated is True
    assert assistant.content == "authoritative terminal response"
    assert assistant.status == terminal_status
    assert assistant.updated_at is None
    db.commit.assert_awaited_once()


def test_timeline_merges_chinese_text_and_updates_tool_in_original_order():
    timeline = chat_history.merge_timeline(
        None,
        [
            {"role": "assistant", "content": "先读取配置。"},
            {
                "role": "tool_call",
                "toolCallId": "call-read",
                "toolName": "read_file",
                "toolStatus": "running",
            },
            {"role": "assistant", "content": "读取完成"},
            {"role": "assistant", "content": "，配置正常。"},
            {
                "role": "tool_call",
                "toolCallId": "call-read",
                "toolName": "read_file",
                "toolStatus": "success",
                "toolResult": "DEBUG=false",
                "toolDuration": 18,
            },
        ],
    )

    assert [block["role"] for block in timeline] == [
        "assistant",
        "tool_call",
        "assistant",
    ]
    assert timeline[0]["content"] == "先读取配置。"
    assert timeline[1] == {
        "role": "tool_call",
        "toolCallId": "call-read",
        "toolName": "read_file",
        "toolStatus": "done",
        "toolResult": "DEBUG=false",
        "toolError": "",
        "toolDuration": 18,
    }
    assert timeline[2]["content"] == "读取完成，配置正常。"


def test_timeline_without_tool_id_updates_most_recent_unresolved_same_name():
    timeline = chat_history.merge_timeline(
        [
            {
                "role": "tool_call",
                "toolName": "bash",
                "toolStatus": "done",
                "toolResult": "first",
            },
            {"role": "assistant", "content": "中间说明"},
            {
                "role": "tool_call",
                "toolName": "bash",
                "toolStatus": "running",
            },
        ],
        [{
            "role": "tool_call",
            "toolName": "bash",
            "toolStatus": "done",
            "toolResult": "ok",
        }],
    )

    tools = [block for block in timeline if block["role"] == "tool_call"]
    assert [tool["toolStatus"] for tool in tools] == ["done", "done"]
    assert tools[0]["toolResult"] == "first"
    assert tools[1]["toolResult"] == "ok"


def test_terminal_tool_status_cannot_be_downgraded_by_duplicate_start():
    timeline = chat_history.merge_timeline(
        [{
            "role": "tool_call",
            "toolCallId": "call-1",
            "toolName": "bash",
            "toolStatus": "done",
            "toolResult": "complete",
        }],
        [{
            "role": "tool_call",
            "toolCallId": "call-1",
            "toolName": "bash",
            "toolStatus": "running",
        }],
    )

    assert len(timeline) == 1
    assert timeline[0]["toolStatus"] == "done"
    assert timeline[0]["toolResult"] == "complete"


def test_timeline_preserves_rejected_as_an_authoritative_terminal_status():
    timeline = chat_history.merge_timeline(
        [{
            "role": "tool_call",
            "toolCallId": "call-rejected",
            "toolName": "write_file",
            "toolStatus": "waiting",
        }],
        [{
            "role": "tool_call",
            "toolCallId": "call-rejected",
            "toolName": "write_file",
            "toolStatus": "rejected",
            "toolError": "Not approved — this tool was not run.",
        }],
    )

    assert timeline[0]["toolStatus"] == "rejected"
    assert timeline[0]["toolError"] == "Not approved — this tool was not run."

    terminalized = chat_history.terminalize_timeline(timeline, "completed")
    assert terminalized[0]["toolStatus"] == "rejected"
    assert terminalized[0]["toolError"] == "Not approved — this tool was not run."


@pytest.mark.asyncio
async def test_update_assistant_message_builds_and_resumes_compact_timeline():
    assistant = SimpleNamespace(
        content="",
        status="streaming",
        timeline=None,
        updated_at=None,
    )
    db = _locked_message_db(assistant)

    await chat_history.update_assistant_message(
        db,
        message_id=17,
        user_id=7,
        content="第一段",
        status="interrupted",
    )
    await chat_history.update_assistant_message(
        db,
        message_id=17,
        user_id=7,
        content="继续说明",
        status="streaming",
    )
    await chat_history.update_assistant_message(
        db,
        message_id=17,
        user_id=7,
        content="",
        status="streaming",
        timeline_entries=[{
            "role": "tool_call",
            "toolCallId": "call-1",
            "toolName": "bash",
            "toolStatus": "done",
            "toolResult": "ok",
        }],
    )
    await chat_history.update_assistant_message(
        db,
        message_id=17,
        user_id=7,
        content="工具完成",
        status="completed",
        timeline_entries=[{"role": "assistant", "content": "工具完成"}],
    )

    assert assistant.content == "第一段继续说明工具完成"
    assert assistant.timeline == [
        {"role": "assistant", "content": "第一段继续说明"},
        {
            "role": "tool_call",
            "toolCallId": "call-1",
            "toolName": "bash",
            "toolStatus": "done",
            "toolResult": "ok",
            "toolError": "",
            "toolDuration": None,
        },
        {"role": "assistant", "content": "工具完成"},
    ]
    assert assistant.status == "completed"
    assert db.commit.await_count == 4


@pytest.mark.asyncio
async def test_terminal_assistant_status_fails_only_unresolved_tools():
    assistant = SimpleNamespace(
        content="result",
        status="streaming",
        timeline=[
            {
                "role": "tool_call",
                "toolCallId": "open",
                "toolName": "bash",
                "toolStatus": "running",
            },
            {
                "role": "tool_call",
                "toolCallId": "done",
                "toolName": "read_file",
                "toolStatus": "done",
                "toolResult": "ok",
            },
        ],
        updated_at=None,
    )
    db = _locked_message_db(assistant)

    await chat_history.update_assistant_message(
        db,
        message_id=18,
        user_id=7,
        content="",
        status="completed",
    )

    assert assistant.timeline[0]["toolStatus"] == "error"
    assert "without an authoritative completion" in assistant.timeline[0]["toolError"]
    assert assistant.timeline[1]["toolStatus"] == "done"
    assert assistant.timeline[1]["toolResult"] == "ok"


@pytest.mark.asyncio
async def test_cancel_tombstone_is_appended_to_content_and_timeline_once():
    tombstone = "*[Generation stopped by user.]*"
    assistant = SimpleNamespace(
        content="正在执行",
        status="streaming",
        timeline=[
            {"role": "assistant", "content": "正在执行"},
            {
                "role": "tool_call",
                "toolCallId": "call-open",
                "toolName": "bash",
                "toolStatus": "running",
            },
        ],
        updated_at=None,
    )
    db = _locked_message_db(assistant)

    await chat_history.mark_assistant_message_cancelled(
        db,
        session_id="session-1",
        user_id=7,
        trace_id="trace-1",
        tombstone=tombstone,
    )
    await chat_history.mark_assistant_message_cancelled(
        db,
        session_id="session-1",
        user_id=7,
        trace_id="trace-1",
        tombstone=tombstone,
    )

    assert assistant.content == f"正在执行\n\n{tombstone}"
    assert [block["role"] for block in assistant.timeline] == [
        "assistant",
        "tool_call",
        "assistant",
    ]
    assert assistant.timeline[1]["toolStatus"] == "error"
    assert assistant.timeline[1]["toolError"] == "Task cancelled before tool completion"
    assert assistant.timeline[2]["content"] == f"\n\n{tombstone}"
    assert assistant.status == "cancelled"


def test_serialize_message_adds_timeline_only_for_new_assistant_rows():
    legacy = SimpleNamespace(role="assistant", content="legacy answer")
    assert chat_history.serialize_message(legacy) == {
        "role": "assistant",
        "content": "legacy answer",
    }

    user = SimpleNamespace(
        role="user",
        content="question",
        timeline=[{"role": "assistant", "content": "must not leak"}],
    )
    assert chat_history.serialize_message(user) == {
        "role": "user",
        "content": "question",
    }

    current = SimpleNamespace(
        role="assistant",
        content="answer",
        timeline=[
            {"role": "assistant", "content": "ans"},
            {"role": "assistant", "content": "wer"},
        ],
    )
    assert chat_history.serialize_message(current) == {
        "role": "assistant",
        "content": "answer",
        "timeline": [{"role": "assistant", "content": "answer"}],
    }
