"""Checkpoint ordering and resume semantics for cooperative user pause."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from enterprise_agent.core.agent import nodes
from enterprise_agent.core.agent.state import AgentState


async def test_pause_gate_acknowledges_only_an_exact_running_task(monkeypatch):
    async def pause_request(user_id, session_id, trace_id):
        assert (user_id, session_id, trace_id) == (9, "session-9", "trace-9")
        return {
            "user_id": 9,
            "session_id": "session-9",
            "trace_id": "trace-9",
            "requested_at": "2026-08-10T10:00:00+00:00",
            "reason": "Inspect intermediate result",
        }

    monkeypatch.setattr(
        "enterprise_agent.core.execution.pause_control.get_task_pause_request",
        pause_request,
    )
    monkeypatch.setattr(nodes, "_record_trace", lambda *args, **kwargs: None)

    result = await nodes.pause_gate_node(
        {
            "task_status": "running",
            "user_id": 9,
            "session_id": "session-9",
            "trace_id": "trace-9",
        },
        "tool_executor",
    )

    assert result["task_status"] == "paused"
    assert result["pause_resume_target"] == "tool_executor"
    assert result["pause_reason"] == "Inspect intermediate result"
    assert nodes.route_after_pause_gate(result) == "pause"


async def test_pause_gate_ignores_missing_request(monkeypatch):
    async def no_pause(*_args):
        return None

    monkeypatch.setattr(
        "enterprise_agent.core.execution.pause_control.get_task_pause_request",
        no_pause,
    )

    result = await nodes.pause_gate_node(
        {
            "task_status": "running",
            "user_id": 9,
            "session_id": "session-9",
            "trace_id": "trace-9",
        },
        "llm_call",
    )
    assert result == {}


async def test_paused_status_is_checkpointed_before_interrupt_and_can_continue(monkeypatch):
    async def pause_request(*_args):
        return {
            "user_id": 9,
            "session_id": "session-9",
            "trace_id": "trace-9",
            "requested_at": "2026-08-10T10:00:00+00:00",
            "reason": "Pause now",
        }

    cleared = []

    async def clear_pause(*identity):
        cleared.append(identity)
        return True

    monkeypatch.setattr(
        "enterprise_agent.core.execution.pause_control.get_task_pause_request",
        pause_request,
    )
    monkeypatch.setattr(
        "enterprise_agent.core.execution.pause_control.clear_task_pause_request",
        clear_pause,
    )
    monkeypatch.setattr(nodes, "_record_trace", lambda *args, **kwargs: None)

    async def prepare(state):
        return await nodes.pause_gate_node(state, "after_pause")

    side_effects = []

    async def after_pause(_state):
        side_effects.append("executed")
        return {"execution_phase": "executing"}

    builder = StateGraph(AgentState)
    builder.add_node("prepare_pause", prepare)
    builder.add_node("user_pause", nodes.user_pause_node)
    builder.add_node("after_pause", after_pause)
    builder.set_entry_point("prepare_pause")
    builder.add_conditional_edges(
        "prepare_pause",
        nodes.route_after_pause_gate,
        {"pause": "user_pause", "continue": "after_pause"},
    )
    builder.add_conditional_edges(
        "user_pause",
        nodes.route_after_user_pause,
        {"continue": "after_pause", "cancel": END},
    )
    builder.add_edge("after_pause", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "session-9"}}

    await graph.ainvoke(
        {
            "task_status": "running",
            "user_id": 9,
            "session_id": "session-9",
            "trace_id": "trace-9",
            "messages": [],
        },
        config,
    )
    paused = await graph.aget_state(config)

    assert paused.values["task_status"] == "paused"
    assert paused.values["pause_resume_target"] == "after_pause"
    assert paused.tasks[0].interrupts[0].value["type"] == "user_pause"
    assert paused.tasks[0].interrupts[0].value["trace_id"] == "trace-9"
    assert side_effects == []

    result = await graph.ainvoke(
        Command(resume={"action": "continue", "trace_id": "trace-9"}),
        config,
    )

    assert result["task_status"] == "running"
    assert result["execution_phase"] == "executing"
    assert result["pause_resume_target"] is None
    assert cleared == [(9, "session-9", "trace-9")]
    assert side_effects == ["executed"]


async def test_paused_task_can_be_cancelled_without_visiting_resume_target(monkeypatch):
    async def clear_pause(*_identity):
        return True

    monkeypatch.setattr(
        "enterprise_agent.core.execution.pause_control.clear_task_pause_request",
        clear_pause,
    )
    monkeypatch.setattr(nodes, "_record_trace", lambda *args, **kwargs: None)

    builder = StateGraph(AgentState)
    builder.add_node("user_pause", nodes.user_pause_node)
    builder.set_entry_point("user_pause")
    builder.add_edge("user_pause", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "cancel-paused"}}

    await graph.ainvoke(
        {
            "task_status": "paused",
            "user_id": 9,
            "session_id": "session-9",
            "trace_id": "trace-9",
            "pause_resume_target": "tool_executor",
            "messages": [],
        },
        config,
    )
    result = await graph.ainvoke(
        Command(resume={
            "action": "cancel",
            "trace_id": "trace-9",
            "reason": "Cancelled during review",
        }),
        config,
    )

    assert result["task_status"] == "cancelled"
    assert result["failure_reason"] == "Cancelled during review"
    assert nodes.route_after_user_pause(result) == "cancel"
