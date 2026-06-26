"""LangGraph agent workflow definition.

Builds the StateGraph that orchestrates the agent's behavior:

    init_context -> check_background -> check_inbox -> pre_microcompact -> llm_call -> route_after_llm
                                                                                             |
                         +-------------------------------------------------------------------+
                         |                    |                                              |
                    tool_executor         compress_context                               END
                         |
                    save_memory
                         |
                    route_after_tool
                         |
               +---------+---------+
               |                   |
          compress_context    pre_microcompact
                                |
                           llm_call

State persistence is handled by RedisSaver (checkpointer), which automatically
saves/restores the full AgentState (including messages) keyed by thread_id.
"""

import redis.asyncio as redis_async
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph import END, StateGraph

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.nodes import (
    check_background_node,
    check_inbox_node,
    compress_context_node,
    init_context_node,
    llm_call_node,
    manual_compress_node,
    pre_llm_microcompact_node,
    route_after_llm,
    route_after_tool,
    save_memory_node,
    tool_confirm_node,
    tool_executor_node,
)
from enterprise_agent.core.agent.state import AgentState

# Dedicated Redis client for checkpointer (no decode_responses — binary protocol required)
# NOTE: RediSearch (FT.CREATE) only works on db 0, so checkpointer shares db 0 with app Redis
_checkpointer_pool = redis_async.ConnectionPool(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD,
    max_connections=10,
    decode_responses=False,
    db=0,
)
_checkpointer_client = redis_async.Redis(connection_pool=_checkpointer_pool)


def build_agent_graph():
    """Build LangGraph workflow.

    The graph implements:
    - Pre-LLM microcompact to prevent tool output bloat
    - Conditional routing after LLM (tool_call / compress / end)
    - Post-tool routing with threshold check
    - Manual compression support
    - Background task notification injection
    - Inbox message checking

    State persistence is handled by RedisSaver checkpointer.
    Pass config={"configurable": {"thread_id": session_id}} when invoking.

    Returns:
        Compiled StateGraph with AsyncRedisSaver checkpointer
    """
    graph = StateGraph(AgentState)

    # === Add Nodes ===

    # Entry and initialization
    graph.add_node("init_context", init_context_node)

    # Pre-LLM microcompact (key mechanism from original)
    graph.add_node("pre_microcompact", pre_llm_microcompact_node)

    # Core LLM call
    graph.add_node("llm_call", llm_call_node)

    # Tool execution (with human-in-the-loop confirmation)
    graph.add_node("tool_confirm", tool_confirm_node)
    graph.add_node("tool_executor", tool_executor_node)
    graph.add_node("save_memory", save_memory_node)

    # Compression nodes
    graph.add_node("compress_context", compress_context_node)
    graph.add_node("manual_compress", manual_compress_node)

    # Optional: Background and inbox checks
    graph.add_node("check_background", check_background_node)
    graph.add_node("check_inbox", check_inbox_node)

    # === Define Edges ===

    # Entry flow (no load_memory — RedisSaver restores state automatically)
    graph.set_entry_point("init_context")
    graph.add_edge("init_context", "check_background")

    # Pre-processing before LLM
    graph.add_edge("check_background", "check_inbox")   # Inject inbox messages
    graph.add_edge("check_inbox", "pre_microcompact")   # Apply microcompact
    graph.add_edge("pre_microcompact", "llm_call")      # Then call LLM

    # Conditional routing after LLM
    # tool_call -> tool_confirm (human-in-the-loop check) -> tool_executor
    graph.add_conditional_edges(
        "llm_call",
        route_after_llm,
        {
            "tool_call": "tool_confirm",  # First check for sensitive tools
            "save_memory": "save_memory",  # Text response -> save then end
            "compress": "compress_context",
        }
    )

    # Tool confirmation -> tool_executor (after user approval or if no sensitive tools)
    graph.add_edge("tool_confirm", "tool_executor")

    # Tool execution flow — always run microcompact before next LLM call
    graph.add_edge("tool_executor", "save_memory")
    graph.add_conditional_edges(
        "save_memory",
        route_after_tool,
        {
            "end": END,  # Text response completed -> end
            "compress": "compress_context",
            "manual_compress": "manual_compress",
            "llm_call": "pre_microcompact"  # Run microcompact before returning to LLM
        }
    )

    # Compression flow - back to LLM with compressed context
    graph.add_edge("compress_context", "llm_call")

    # Manual compress ends the invocation
    graph.add_edge("manual_compress", END)

    # Compile with RedisSaver for persistent state management
    # TTL ensures checkpoints are automatically cleaned up after expiry
    checkpointer = AsyncRedisSaver(
        redis_client=_checkpointer_client,
        ttl={"default_ttl": settings.CHECKPOINT_TTL_HOURS * 60}
    )
    return graph.compile(checkpointer=checkpointer)


def build_simple_agent_graph():
    """Build simplified LangGraph workflow without background/inbox checks.

    This is a simpler version for basic usage:

    init_context -> pre_microcompact -> llm_call -> route
                                                -> tool_executor -> save -> pre_microcompact -> llm_call
                                                -> compress -> llm_call
                                                -> END

    Returns:
        Compiled StateGraph with AsyncRedisSaver checkpointer
    """
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("init_context", init_context_node)
    graph.add_node("pre_microcompact", pre_llm_microcompact_node)
    graph.add_node("llm_call", llm_call_node)
    graph.add_node("tool_confirm", tool_confirm_node)
    graph.add_node("tool_executor", tool_executor_node)
    graph.add_node("save_memory", save_memory_node)
    graph.add_node("compress_context", compress_context_node)

    # Entry flow (no load_memory — RedisSaver restores state automatically)
    graph.set_entry_point("init_context")
    graph.add_edge("init_context", "pre_microcompact")
    graph.add_edge("pre_microcompact", "llm_call")

    # Conditional routing after LLM
    # tool_call -> tool_confirm (human-in-the-loop check) -> tool_executor
    graph.add_conditional_edges(
        "llm_call",
        route_after_llm,
        {
            "tool_call": "tool_confirm",  # First check for sensitive tools
            "save_memory": "save_memory",  # Text response -> save then end
            "compress": "compress_context",
        }
    )

    # Tool confirmation -> tool_executor
    graph.add_edge("tool_confirm", "tool_executor")

    # Tool flow — always run microcompact before next LLM call
    graph.add_edge("tool_executor", "save_memory")
    graph.add_conditional_edges(
        "save_memory",
        route_after_tool,
        {
            "end": END,  # Text response completed -> end
            "compress": "compress_context",
            "manual_compress": END,  # Simplified: manual compress just ends
            "llm_call": "pre_microcompact"  # Run microcompact before returning to LLM
        }
    )

    # Compress back to LLM
    graph.add_edge("compress_context", "llm_call")

    checkpointer = AsyncRedisSaver(
        redis_client=_checkpointer_client,
        ttl={"default_ttl": settings.CHECKPOINT_TTL_HOURS * 60}
    )
    return graph.compile(checkpointer=checkpointer)


# Lazy graph initialization (avoids crash at import time)
_agent_graph = None
_simple_agent_graph = None


def get_agent_graph():
    """Get or create the full agent graph (lazy initialization)."""
    global _agent_graph
    if _agent_graph is None:
        _agent_graph = build_agent_graph()
    return _agent_graph


def get_simple_agent_graph():
    """Get or create the simple agent graph (lazy initialization)."""
    global _simple_agent_graph
    if _simple_agent_graph is None:
        _simple_agent_graph = build_simple_agent_graph()
    return _simple_agent_graph


async def setup_checkpointer():
    """Initialize the RedisSaver checkpointer (call once at app startup).

    AsyncRedisSaver requires asetup() to be called before first use
    to set up Redis indexes for checkpoint storage.
    """
    graph = get_agent_graph()
    # Access the checkpointer from the compiled graph and run setup
    checkpointer = graph.checkpointer
    if hasattr(checkpointer, "asetup"):
        await checkpointer.asetup()
