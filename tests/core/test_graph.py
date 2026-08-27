"""Tests for LangGraph workflow construction."""

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from enterprise_agent.core.agent import nodes
from enterprise_agent.core.agent.context import ContextManager, TranscriptManager
from enterprise_agent.core.agent.graph import build_agent_graph, build_simple_agent_graph
from enterprise_agent.core.agent.state import AgentState

DIRECT_EXECUTION_EDGES = {
    ("plan_task", "pre_microcompact"),
    ("llm_call", "prepare_tool_execution"),
    ("tool_confirm", "tool_executor"),
    ("checkpoint_task", "save_memory"),
    ("verification_gate", "pre_microcompact"),
    ("save_memory", "finalize_task"),
    ("save_memory", "pre_microcompact"),
    ("compress_context", "llm_call"),
    ("manual_compress", "llm_call"),
}


def _assert_user_pause_is_retired(graph):
    drawable = graph.get_graph()
    node_names = set(drawable.nodes)
    edge_pairs = {(edge.source, edge.target) for edge in drawable.edges}

    assert not any(
        name.startswith("pause_") or name.startswith("user_pause_")
        for name in node_names
    )
    assert DIRECT_EXECUTION_EDGES.issubset(edge_pairs)


def test_build_agent_graph_compiles_registered_nodes():
    """The full graph should compile with only registered node names."""
    graph = build_agent_graph()

    assert graph is not None
    node_names = set(graph.get_graph().nodes)
    assert {
        "task_parse",
        "plan_task",
        "prepare_tool_execution",
        "checkpoint_task",
        "verification_gate",
        "finalize_task",
    }.issubset(node_names)
    _assert_user_pause_is_retired(graph)


def test_simple_graph_connects_execution_boundaries_without_user_pause():
    graph = build_simple_agent_graph(checkpointer=InMemorySaver())

    _assert_user_pause_is_retired(graph)


async def test_checkpoint_contains_only_reduced_history_after_full_compression(
    monkeypatch,
    tmp_path,
):
    """Exercise RemoveMessage through a compiled graph and real checkpointer."""

    class SummaryLLM:
        async def ainvoke(self, _messages):
            return AIMessage(
                content="checkpoint continuation",
                usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
            )

    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    manager = ContextManager(
        llm=SummaryLLM(),
        transcript_manager=TranscriptManager(tmp_path / "user_31"),
    )
    monkeypatch.setattr(nodes, "get_context_manager", lambda: manager)

    builder = StateGraph(AgentState)
    builder.add_node("compress", nodes.compress_context_node)
    builder.set_entry_point("compress")
    builder.add_edge("compress", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "compression-checkpoint"}}
    old_ids = {"old-user", "old-ai"}

    result = await graph.ainvoke(
        {
            "messages": [
                HumanMessage(content="old user context", id="old-user"),
                AIMessage(content="old assistant context", id="old-ai"),
            ],
            "session_id": "compression-checkpoint",
            "user_id": 31,
            "token_count": manager.token_threshold,
            "task_token_count": 0,
            "session_token_count": 0,
            "current_user_request": "continue safely",
            "task_status": "running",
            "execution_phase": "executing",
        },
        config,
    )
    snapshot = await graph.aget_state(config)

    assert len(result["messages"]) == 1
    assert len(snapshot.values["messages"]) == 1
    assert not old_ids.intersection(message.id for message in snapshot.values["messages"])
    assert "checkpoint continuation" in snapshot.values["messages"][0].content
