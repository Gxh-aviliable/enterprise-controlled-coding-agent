"""Context compaction, transcript and continuation-integrity tests."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph.message import add_messages

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent import nodes
from enterprise_agent.core.agent.context import (
    ContextCompressionError,
    ContextManager,
    TranscriptManager,
)
from enterprise_agent.core.agent.tool_artifacts import ToolArtifactStore
from enterprise_agent.core.agent.tools.context_tools import get_transcript
from enterprise_agent.core.agent.tools.workspace import set_current_user_id


class FakeSummaryLLM:
    def __init__(self, content="A deliberately incomplete model summary."):
        self.content = content
        self.inputs = []
        self.bound_kwargs = {}

    def bind(self, **kwargs):
        self.bound_kwargs = kwargs
        return self

    async def ainvoke(self, messages):
        self.inputs.append(messages)
        return AIMessage(
            content=self.content,
            usage_metadata={"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
        )


def test_effective_threshold_respects_configured_model_window(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "TOKEN_THRESHOLD", 500_000)
    monkeypatch.setattr(settings, "MODEL_CONTEXT_WINDOW_TOKENS", 100_000)
    monkeypatch.setattr(settings, "CONTEXT_COMPRESSION_RATIO", 0.8)
    manager = ContextManager(
        llm=FakeSummaryLLM(),
        transcript_manager=TranscriptManager(tmp_path),
    )
    assert manager.token_threshold == 80_000


def test_cjk_estimate_matches_documented_weight(tmp_path):
    manager = ContextManager(
        llm=FakeSummaryLLM(),
        transcript_manager=TranscriptManager(tmp_path),
    )
    assert manager.estimate_tokens([{"role": "user", "content": "中" * 100}]) >= 150


def test_token_estimator_includes_tool_call_arguments(tmp_path):
    manager = ContextManager(
        llm=FakeSummaryLLM(),
        transcript_manager=TranscriptManager(tmp_path),
    )
    message = AIMessage(
        content="",
        tool_calls=[{
            "id": "large-write",
            "name": "write_file",
            "args": {"path": "large.txt", "content": "x" * 10_000},
        }],
    )
    assert manager.estimate_tokens([message]) > 2_000


def test_token_estimator_is_conservative_for_emoji_and_high_entropy(tmp_path):
    manager = ContextManager(
        llm=FakeSummaryLLM(),
        transcript_manager=TranscriptManager(tmp_path),
    )
    emoji = manager.estimate_tokens([{"role": "user", "content": "🙂" * 10_000}])
    randomish = manager.estimate_tokens([{
        "role": "user",
        "content": ("aZ19+/Bq7_" * 1_000),
    }])

    assert emoji >= 30_000
    assert randomish >= 7_000


def _tool_messages(count=8):
    return [
        ToolMessage(
            content=f"FACT_TOOL_{index}=" + (str(index) * 3000),
            tool_call_id=f"call-{index}",
            id=f"message-{index}",
        )
        for index in range(count)
    ]


def test_microcompact_persists_before_replacing_and_does_not_mutate(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    messages = _tool_messages()
    original_first = messages[0].content
    manager = ContextManager(
        llm=FakeSummaryLLM(),
        transcript_manager=TranscriptManager(tmp_path / "user_8"),
    )

    report = manager.microcompact_with_report(
        messages,
        keep_last=6,
        trace_id="trace-micro",
        user_id=8,
    )

    assert report["compacted_count"] == 2
    assert messages[0].content == original_first
    assert report["messages"][0].content.startswith("[tool output compacted;")
    assert report["messages"][2].content.startswith("FACT_TOOL_2=")
    artifact_path = tmp_path / "user_8" / report["messages"][0].artifact["path"]
    assert artifact_path.read_text(encoding="utf-8") == original_first

    reduced = add_messages(messages, report["changed_messages"])
    assert len(reduced) == 8
    assert reduced[0].id == "message-0"
    assert reduced[0].content.startswith("[tool output compacted;")


async def test_microcompact_node_traces_content_change_without_message_count_change(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    events = []

    class FakeTraceStore:
        def record_event(self, **event):
            events.append(event)

    monkeypatch.setattr(nodes, "get_trace_store", lambda: FakeTraceStore())
    manager = ContextManager(
        llm=FakeSummaryLLM(),
        transcript_manager=TranscriptManager(tmp_path / "user_9"),
    )
    monkeypatch.setattr(nodes, "get_context_manager", lambda: manager)
    messages = _tool_messages()

    update = await nodes.pre_llm_microcompact_node({
        "trace_id": "trace-micro-node",
        "session_id": "session",
        "user_id": 9,
        "messages": messages,
    })

    reduced = add_messages(messages, update["messages"])
    assert len(reduced) == len(messages)
    uncompacted_estimate = nodes._estimate_next_llm_context(
        {
            "trace_id": "trace-micro-node",
            "session_id": "session",
            "user_id": 9,
            "messages": messages,
        },
        messages,
    )
    assert update["token_count"] < uncompacted_estimate
    assert update["token_count"] == nodes._estimate_next_llm_context(
        {
            "trace_id": "trace-micro-node",
            "session_id": "session",
            "user_id": 9,
            "messages": reduced,
        },
        reduced,
    )
    event = next(event for event in events if event["name"] == "microcompact")
    assert event["data"]["compacted_count"] == 2
    assert event["data"]["messages_before"] == event["data"]["messages_after"]


def test_microcompact_keeps_body_when_receipt_is_outside_or_tampered(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    messages = _tool_messages(7)
    messages[0].artifact = {
        "path": "unrelated.txt",
        "sha256": "0" * 64,
        "original_chars": len(messages[0].content),
        "storage_status": "stored",
    }
    (tmp_path / "user_13").mkdir(parents=True)
    (tmp_path / "user_13" / "unrelated.txt").write_text("WRONG", encoding="utf-8")
    manager = ContextManager(
        llm=FakeSummaryLLM(),
        transcript_manager=TranscriptManager(tmp_path / "user_13"),
    )

    report = manager.microcompact_with_report(
        messages,
        keep_last=6,
        trace_id="trace-tampered",
        user_id=13,
    )

    assert report["compacted_count"] == 0
    assert report["messages"][0].content.startswith("FACT_TOOL_0=")
    assert "invalid_artifact_path" in report["artifact_errors"][0]


def test_microcompact_keeps_body_when_artifact_write_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    messages = _tool_messages(7)
    manager = ContextManager(
        llm=FakeSummaryLLM(),
        transcript_manager=TranscriptManager(tmp_path / "user_14"),
    )

    def fail_save(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(ToolArtifactStore, "save", fail_save)
    report = manager.microcompact_with_report(
        messages,
        keep_last=6,
        trace_id="trace-write-failure",
        user_id=14,
    )

    assert report["compacted_count"] == 0
    assert report["messages"][0].content.startswith("FACT_TOOL_0=")
    assert report["artifact_paths"] == []
    assert report["artifact_errors"]


async def test_noop_microcompact_refreshes_full_next_context_estimate(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    manager = ContextManager(
        llm=FakeSummaryLLM(),
        transcript_manager=TranscriptManager(tmp_path / "user_15"),
    )
    monkeypatch.setattr(nodes, "get_context_manager", lambda: manager)
    message = AIMessage(
        content="",
        tool_calls=[{
            "id": "large-write",
            "name": "write_file",
            "args": {"path": "large.txt", "content": "x" * 10_000},
        }],
    )

    update = await nodes.pre_llm_microcompact_node({
        "trace_id": "trace-noop",
        "session_id": "session",
        "user_id": 15,
        "permissions": ["tools:basic"],
        "execution_mode": "single_agent",
        "retrieved_memory_context": "MEMORY_FACT=" + ("y" * 2_000),
        "messages": [message],
        "token_count": 1,
    })

    assert "messages" not in update
    assert update["token_count"] > manager.estimate_tokens([message])


async def test_full_compaction_replaces_history_and_keeps_deterministic_facts(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr(settings, "TOKEN_THRESHOLD", 20_000)
    manager = ContextManager(
        llm=FakeSummaryLLM("The model omitted every exact project fact."),
        transcript_manager=TranscriptManager(tmp_path / "user_10"),
    )
    monkeypatch.setattr(nodes, "get_context_manager", lambda: manager)
    old_messages = [
        HumanMessage(content="Please fix the order total", id="old-user"),
        AIMessage(content="I will inspect it", id="old-ai"),
    ]
    state = {
        "messages": old_messages,
        "session_id": "session-10",
        "user_id": 10,
        "token_count": 20_000,
        "task_token_count": 100,
        "session_token_count": 200,
        "current_user_request": "FACT_GOAL=fix order total",
        "trace_id": "trace-10",
        "task_status": "running",
        "execution_phase": "verifying",
        "failure_reason": "FACT_FAILURE=expected 2 got 8",
        "current_task": {"request": "fix"},
        "todos": [{"content": "FACT_PENDING=add negative test", "status": "pending"}],
        "has_open_todos": True,
        "changed_files": ["FACT_FILE=enterprise_agent/order.py"],
        "validation_results": [{"command": "pytest tests/order -q", "ok": False}],
        "tool_execution_records": [],
        "round_count": 3,
        "tool_call_count": 2,
    }

    update = await nodes.compress_context_node(state)
    reduced = add_messages(old_messages, update["messages"])

    assert len(reduced) == 1
    assert all(message.id not in {"old-user", "old-ai"} for message in reduced)
    packet = json.loads(update["context_summary"])
    durable = packet["durable_state"]
    assert durable["objective"] == "FACT_GOAL=fix order total"
    assert durable["task"]["failure_reason"] == "FACT_FAILURE=expected 2 got 8"
    assert durable["evidence"]["changed_files"] == ["FACT_FILE=enterprise_agent/order.py"]
    assert durable["plan"]["todos"][0]["content"] == "FACT_PENDING=add negative test"
    assert update["token_count"] > 0
    assert update["task_token_count"] == 125
    transcript = tmp_path / "user_10" / update["transcript_path"]
    assert transcript.is_file()
    assert "Please fix the order total" in transcript.read_text(encoding="utf-8")


async def test_context_manager_resolves_transcript_workspace_per_user(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    manager = ContextManager(llm=FakeSummaryLLM())
    messages = [HumanMessage(content="tenant scoped", id="user-message")]

    # Deliberately make ambient context wrong: authoritative AgentState must
    # select the transcript workspace.
    set_current_user_id(999)
    try:
        first = await manager.auto_compact(messages, "same-session", {"user_id": 11})
        second = await manager.auto_compact(messages, "same-session", {"user_id": 12})
    finally:
        set_current_user_id(None)

    assert (tmp_path / "user_11" / first["transcript_path"]).is_file()
    assert (tmp_path / "user_12" / second["transcript_path"]).is_file()


async def test_manual_compaction_replaces_history_and_continues(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    manager = ContextManager(
        llm=FakeSummaryLLM(),
        transcript_manager=TranscriptManager(tmp_path / "user_16"),
    )
    monkeypatch.setattr(nodes, "get_context_manager", lambda: manager)
    old_messages = [
        HumanMessage(content="old request", id="old-user"),
        AIMessage(content="old response", id="old-ai"),
    ]

    update = await nodes.manual_compress_node({
        "messages": old_messages,
        "session_id": "manual-session",
        "user_id": 16,
        "trace_id": "manual-trace",
        "permissions": ["tools:basic"],
        "execution_mode": "single_agent",
        "task_token_count": 10,
        "session_token_count": 20,
        "current_user_request": "continue the task",
    })
    reduced = add_messages(old_messages, update["messages"])

    assert len(reduced) == 1
    assert reduced[0].id not in {"old-user", "old-ai"}
    assert update["should_end"] is False
    assert update["should_end_after_save"] is False
    assert update["task_token_count"] == 35
    assert update["session_token_count"] == 45


def test_transcript_round_trip_is_unique_atomic_and_path_safe(tmp_path):
    manager = TranscriptManager(tmp_path)
    messages = [
        HumanMessage(content="hello", id="human-1"),
        AIMessage(content="working", id="ai-1", tool_calls=[{
            "id": "call-1",
            "name": "read_file",
            "args": {"path": "app.py"},
        }]),
        ToolMessage(
            content="file body",
            tool_call_id="call-1",
            id="tool-1",
            artifact={"path": ".agent/tool-artifacts/t/c.txt"},
        ),
    ]

    first = manager.save(messages, "../../escape")
    second = manager.save(messages, "../../escape")
    loaded = manager.load(first)

    assert first != second
    assert first.parent == manager.transcript_dir
    assert loaded[0]["role"] == "user"
    assert loaded[1]["role"] == "assistant"
    assert loaded[2]["role"] == "tool"
    assert loaded[2]["tool_call_id"] == "call-1"
    assert loaded[2]["artifact"]["path"].endswith("c.txt")

    with pytest.raises(ValueError):
        manager.load(tmp_path / "outside.jsonl")


async def test_summary_failure_preserves_original_messages_and_reports_transcript(
    monkeypatch,
    tmp_path,
):
    class FailingLLM:
        async def ainvoke(self, _messages):
            raise RuntimeError("provider unavailable")

    manager = ContextManager(
        llm=FailingLLM(),
        transcript_manager=TranscriptManager(tmp_path),
    )
    messages = [HumanMessage(content="FACT_MUST_SURVIVE", id="fact")]

    with pytest.raises(ContextCompressionError) as exc_info:
        await manager.auto_compact(messages, "failure-session", {})

    assert messages[0].content == "FACT_MUST_SURVIVE"
    transcript = tmp_path / exc_info.value.transcript_path
    assert transcript.is_file()
    assert "FACT_MUST_SURVIVE" in transcript.read_text(encoding="utf-8")


async def test_summary_prompt_hard_caps_whole_state_and_recent_context(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(settings, "MODEL_CONTEXT_WINDOW_TOKENS", 2_000)
    monkeypatch.setattr(settings, "CONTEXT_COMPRESSION_RATIO", 0.8)
    monkeypatch.setattr(settings, "CONTEXT_SUMMARY_MAX_TOKENS", 50_000)
    monkeypatch.setattr(settings, "CONTEXT_SUMMARY_OUTPUT_RESERVE_TOKENS", 4_096)
    llm = FakeSummaryLLM()
    manager = ContextManager(
        llm=llm,
        transcript_manager=TranscriptManager(tmp_path),
    )
    huge = "中" * 10_000

    result = await manager.auto_compact(
        [HumanMessage(content=huge, id="huge-message")],
        "bounded-summary",
        {
            "user_id": 1,
            "current_user_request": huge,
            "current_task": {"request": huge, "status": "running"},
            "failure_reason": huge,
            "todos": [{"content": huge, "status": "pending"}] * 20,
        },
    )

    actual_prompt_tokens = manager.estimate_tokens(llm.inputs[-1])
    assert actual_prompt_tokens <= result["summary_input_budget"]
    assert result["summary_input_budget"] == 1_350
    assert result["continuity_snapshot_truncated"] is True
    assert result["summary_prompt_estimated_tokens"] == actual_prompt_tokens
    assert llm.bound_kwargs == {
        "max_tokens": result["summary_output_token_limit"],
    }


async def test_full_compaction_caps_the_next_main_model_context(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr(settings, "MODEL_CONTEXT_WINDOW_TOKENS", 16_000)
    monkeypatch.setattr(settings, "TOKEN_THRESHOLD", 500_000)
    monkeypatch.setattr(settings, "CONTEXT_COMPRESSION_RATIO", 0.8)
    manager = ContextManager(
        llm=FakeSummaryLLM("总" * 30_000),
        transcript_manager=TranscriptManager(tmp_path / "user_41"),
    )
    monkeypatch.setattr(nodes, "get_context_manager", lambda: manager)
    state = {
        "messages": [HumanMessage(content="history " * 2_000, id="history")],
        "session_id": "next-turn-budget",
        "user_id": 41,
        "permissions": ["tools:basic"],
        "execution_mode": "single_agent",
        "token_count": manager.token_threshold,
        "task_token_count": 0,
        "session_token_count": 0,
        "current_user_request": "continue safely",
        "task_status": "running",
        "execution_phase": "executing",
        "retrieved_memory_context": "记忆" * 300,
    }

    update = await nodes.compress_context_node(state)
    reduced = add_messages(state["messages"], update["messages"])
    packet = json.loads(update["context_summary"])
    headroom = nodes._continuation_growth_headroom(manager.token_threshold)

    assert update["token_count"] <= manager.token_threshold - headroom
    assert packet["continuation_packet_truncated"] is True
    assert "model-summary-truncated" in packet["model_summary"]
    assert nodes._estimate_next_llm_context(state, reduced) == update["token_count"]


def test_summary_record_fits_full_body_before_truncation_marker(tmp_path):
    manager = ContextManager(
        llm=FakeSummaryLLM(),
        transcript_manager=TranscriptManager(tmp_path),
    )
    record = {"role": "user", "content": "a", "extra": "z" * 100}
    base = {
        "schema_version": 1,
        "role": "user",
        "id": None,
        "tool_call_id": None,
        "artifact": None,
        "summary_input_truncated": True,
        "content": "a",
    }
    exact_cost = manager._payload_tokens(json.dumps(base, ensure_ascii=False))

    fitted = manager._fit_summary_record(
        record,
        token_limit=exact_cost,
        max_chars=1_000,
    )

    assert fitted is not None
    assert json.loads(fitted[0])["content"] == "a"


def test_continuation_transcript_handle_supports_bounded_utf8_paging(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    set_current_user_id(42)
    try:
        manager = TranscriptManager()
        transcript = manager.save(
            [HumanMessage(content="你好世界", id="unicode")],
            "handle-session",
        )
        handle = manager.relative_path(transcript)
        offset = 0
        chunks = []
        while True:
            page = json.loads(get_transcript.invoke({
                "filename": handle,
                "offset_bytes": offset,
                "limit_bytes": 1,
            }))
            chunks.append(page["content"])
            if page["eof"]:
                break
            assert page["next_offset_bytes"] > offset
            offset = page["next_offset_bytes"]

        restored = "".join(chunks)
        assert "你好世界" in restored
        assert "�" not in restored
        assert get_transcript.invoke({
            "filename": "../outside.jsonl",
        }).startswith("Error: Transcript read rejected")
    finally:
        set_current_user_id(None)
