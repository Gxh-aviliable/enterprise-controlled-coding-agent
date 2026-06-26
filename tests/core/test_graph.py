"""Tests for LangGraph workflow construction."""

from enterprise_agent.core.agent.graph import build_agent_graph


def test_build_agent_graph_compiles_registered_nodes():
    """The full graph should compile with only registered node names."""
    graph = build_agent_graph()

    assert graph is not None
