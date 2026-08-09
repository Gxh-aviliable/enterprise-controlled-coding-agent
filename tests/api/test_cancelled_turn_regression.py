"""Regression coverage for cancelling an SSE turn before its model reply."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from enterprise_agent.api.routes import chat
from enterprise_agent.api.services import chat_history
from enterprise_agent.core.agent import nodes
from enterprise_agent.core.agent.state import AgentState


def _message_signature(message):
    return (
        getattr(message, "type", ""),
        getattr(message, "content", ""),
        getattr(message, "id", None),
    )


def _is_cancellation_tombstone(message) -> bool:
    content = str(getattr(message, "content", "")).lower()
    return any(marker in content for marker in ("cancel", "stopp", "取消", "终止"))


async def test_stopped_turn_is_closed_once_before_next_same_session_model_call(
    monkeypatch,
):
    """An aborted SSE must not leave old and new requests as adjacent humans."""
    model_inputs = []

    class RecordingLLM:
        async def ainvoke(self, messages):
            model_inputs.append(list(messages))
            return AIMessage(
                content="The stopped question was about tools.",
                usage_metadata={
                    "input_tokens": 8,
                    "output_tokens": 4,
                    "total_tokens": 12,
                },
            )

    async def parse_task(state):
        return await nodes.task_parse_node(state)

    async def call_model_only_for_next_trace(state):
        if state.get("trace_id") != "trace-next":
            return {}
        return await nodes.llm_call_node(state)

    builder = StateGraph(AgentState)
    builder.add_node("task_parse", parse_task)
    builder.add_node("model", call_model_only_for_next_trace)
    builder.set_entry_point("task_parse")
    builder.add_edge("task_parse", "model")
    builder.add_edge("model", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "same-session"}}

    monkeypatch.setattr(nodes, "get_llm_with_tools", lambda *_args, **_kwargs: RecordingLLM())
    monkeypatch.setattr(nodes, "_build_runtime_system_prompt", lambda _state: "system")
    monkeypatch.setattr(nodes, "_record_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat, "get_agent_graph", lambda: graph)
    monkeypatch.setattr(chat, "clear_task_pause_request", AsyncMock(return_value=True))
    monkeypatch.setattr(chat, "get_trace_store", MagicMock(return_value=MagicMock()))
    durable_cancel = AsyncMock()
    monkeypatch.setattr(chat, "_safe_mark_durable_assistant_cancelled", durable_cancel)
    monkeypatch.setattr(chat, "_cancel_events", {})
    monkeypatch.setattr(chat, "_active_stream_traces", {})
    monkeypatch.setattr(
        "enterprise_agent.core.agent.tools.background.clear_background_manager",
        lambda _session_id: None,
    )

    # The browser has already aborted the SSE, leaving no in-process cancel
    # event.  The checkpoint contains one completed turn and the unanswered
    # HumanMessage from the task that is about to be cancelled.
    await graph.ainvoke(
        {
            "messages": [
                HumanMessage(content="Where is the system prompt?", id="previous-human"),
                AIMessage(content="It is injected at runtime.", id="previous-ai"),
                HumanMessage(content="What tools do you have?", id="cancelled-human"),
            ],
            "session_id": "same-session",
            "trace_id": "trace-cancelled",
            "user_id": 1,
            "permissions": [],
            "execution_mode": "single_agent",
            "current_user_request": "What tools do you have?",
            "task_status": "pending",
            "execution_phase": "parsing",
            "task_started_at": None,
        },
        config,
    )

    result = await chat.request_task_cancellation(
        "same-session",
        1,
        trace_id="trace-cancelled",
    )
    assert result["status"] == "cancelled"

    after_first_cancel = await graph.aget_state(config)
    first_messages = after_first_cancel.values["messages"]
    assert after_first_cancel.values["task_status"] == "cancelled"
    assert [message.type for message in first_messages[-2:]] == ["human", "ai"]
    assert first_messages[-2].content == "What tools do you have?"
    assert _is_cancellation_tombstone(first_messages[-1])

    # A duplicated Stop request is idempotent and cannot add a second marker.
    await chat.request_task_cancellation(
        "same-session",
        1,
        trace_id="trace-cancelled",
    )
    after_second_cancel = await graph.aget_state(config)
    assert [
        _message_signature(message)
        for message in after_second_cancel.values["messages"]
    ] == [_message_signature(message) for message in first_messages]
    assert durable_cancel.await_count == 2
    assert durable_cancel.await_args_list[0].kwargs == {
        "session_id": "same-session",
        "user_id": 1,
        "trace_id": "trace-cancelled",
    }

    await chat._ensure_session_accepts_new_task(
        graph,
        session_id="same-session",
        user_id=1,
    )
    await graph.ainvoke(
        chat._task_input(
            session_id="same-session",
            trace_id="trace-next",
            user_id=1,
            permissions=[],
            content="What did I just ask?",
        ),
        config,
    )

    assert len(model_inputs) == 1
    conversation = [message for message in model_inputs[0] if message.type != "system"]
    assert [(message.type, message.content) for message in conversation[:2]] == [
        ("human", "Where is the system prompt?"),
        ("ai", "It is injected at runtime."),
    ]
    assert [message.type for message in conversation[-3:]] == ["human", "ai", "human"]
    assert conversation[-3].content == "What tools do you have?"
    assert _is_cancellation_tombstone(conversation[-2])
    assert conversation[-1].content == "What did I just ask?"
    assert all(
        not (left.type == right.type == "human")
        for left, right in zip(conversation, conversation[1:])
    )


async def test_late_interrupted_persistence_cannot_downgrade_cancelled_message():
    """The aborted SSE's finally block may arrive after the cancel endpoint."""
    assistant = SimpleNamespace(
        content="partial answer",
        status="cancelled",
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
        user_id=1,
        content="",
        status="interrupted",
        append=True,
    )

    assert updated is True
    assert assistant.content == "partial answer"
    assert assistant.status == "cancelled"
    db.commit.assert_awaited_once()


async def test_durable_cancellation_tombstone_is_idempotent():
    assistant = SimpleNamespace(
        content="partial answer",
        status="streaming",
        updated_at=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = assistant
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()

    for _ in range(2):
        updated = await chat_history.mark_assistant_message_cancelled(
            db,
            session_id="same-session",
            user_id=1,
            trace_id="trace-cancelled",
            tombstone=chat.CANCELLATION_TOMBSTONE,
        )
        assert updated is True

    assert assistant.status == "cancelled"
    assert assistant.content == f"partial answer\n\n{chat.CANCELLATION_TOMBSTONE}"
    assert assistant.content.count(chat.CANCELLATION_TOMBSTONE) == 1
    assert db.commit.await_count == 2
