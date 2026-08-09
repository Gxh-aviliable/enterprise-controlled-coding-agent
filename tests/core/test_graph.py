"""Tests for LangGraph workflow construction."""

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from enterprise_agent.core.agent import nodes
from enterprise_agent.core.agent.context import ContextManager, TranscriptManager
from enterprise_agent.core.agent.graph import build_agent_graph, build_simple_agent_graph
from enterprise_agent.core.agent.state import AgentState


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


def test_simple_graph_registers_manual_compress_before_resuming_model():
    graph = build_simple_agent_graph(checkpointer=InMemorySaver())
    drawable = graph.get_graph()
    assert "manual_compress" in drawable.nodes
    assert any(
        edge.source == "manual_compress" and edge.target == "pause_after_compression_gate"
        for edge in drawable.edges
    )
    assert {
        "pause_before_llm_gate",
        "user_pause_before_llm",
        "pause_before_tool_execution_gate",
        "user_pause_before_tool_execution",
        "pause_after_tool_gate",
        "user_pause_after_tool",
    }.issubset(drawable.nodes)


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
