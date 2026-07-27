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

import logging
import time
from functools import wraps

import redis.asyncio as redis_async
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, StateGraph

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.nodes import (
    check_background_node,
    check_inbox_node,
    checkpoint_task_node,
    compress_context_node,
    finalize_task_node,
    init_context_node,
    llm_call_node,
    manual_compress_node,
    persist_memory_node,
    plan_task_node,
    pre_llm_microcompact_node,
    prepare_tool_execution_node,
    route_after_llm,
    route_after_tool,
    save_memory_node,
    task_parse_node,
    tool_confirm_node,
    tool_executor_node,
    verification_gate_node,
)
from enterprise_agent.core.agent.state import AgentState
from enterprise_agent.observability.trace_store import get_trace_store

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


def _traced_node(node_name, node):
    """Wrap a LangGraph node with local duration/error tracing."""

    @wraps(node)
    async def wrapped(state):
        started = time.perf_counter()
        try:
            result = await node(state)
        except GraphInterrupt:
            # LangGraph uses this exception as normal control flow for HITL.
            # Recording it as an error made healthy confirmation pauses look
            # failed in Trace and polluted the task's top-level error field.
            try:
                if state.get("trace_id") and state.get("user_id") is not None:
                    get_trace_store().record_event(
                        user_id=state["user_id"],
                        trace_id=state["trace_id"],
                        event_type="node",
                        name=node_name,
                        status="interrupted",
                        duration_ms=int((time.perf_counter() - started) * 1000),
                        data={
                            "phase": state.get("execution_phase"),
                            "task_status": state.get("task_status", "waiting_confirmation"),
                        },
                    )
            except Exception:
                logging.warning("Failed to record node interrupt trace", exc_info=True)
            raise
        except Exception as exc:
            try:
                if state.get("trace_id") and state.get("user_id") is not None:
                    get_trace_store().record_event(
                        user_id=state["user_id"],
                        trace_id=state["trace_id"],
                        event_type="node",
                        name=node_name,
                        status="error",
                        duration_ms=int((time.perf_counter() - started) * 1000),
                        data={"error": str(exc)[:1000], "phase": state.get("execution_phase")},
                    )
            except Exception:
                logging.warning("Failed to record node error trace", exc_info=True)
            raise

        try:
            if state.get("trace_id") and state.get("user_id") is not None:
                result_data = result if isinstance(result, dict) else {}
                store = get_trace_store()
                store.record_event(
                    user_id=state["user_id"],
                    trace_id=state["trace_id"],
                    event_type="node",
                    name=node_name,
                    status="success",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    data={
                        "phase": result_data.get("execution_phase", state.get("execution_phase")),
                        "task_status": result_data.get("task_status", state.get("task_status")),
                        "round_count": result_data.get("round_count", state.get("round_count", 0)),
                    },
                )
                if node_name == "finalize_task":
                    result_summary = ""
                    for message in reversed(state.get("messages", [])):
                        role = message.get("role") if isinstance(message, dict) else getattr(message, "type", "")
                        if role in {"assistant", "ai"}:
                            content = (
                                message.get("content", "")
                                if isinstance(message, dict)
                                else getattr(message, "content", "")
                            )
                            result_summary = str(content)
                            break
                    store.finish_trace(
                        user_id=state["user_id"],
                        trace_id=state["trace_id"],
                        status=result_data.get("task_status", state.get("task_status", "failed")),
                        result_summary=result_summary,
                        error=result_data.get("failure_reason"),
                    )
        except Exception:
            logging.warning("Failed to record node trace", exc_info=True)
        return result

    return wrapped


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

    def add_node(name, node):
        graph.add_node(name, _traced_node(name, node))

    # === Add Nodes ===

    # Explicit task lifecycle phases
    add_node("task_parse", task_parse_node)
    add_node("init_context", init_context_node)
    add_node("plan_task", plan_task_node)

    # Pre-LLM microcompact keeps tool output growth bounded.
    add_node("pre_microcompact", pre_llm_microcompact_node)

    # Core LLM call
    add_node("llm_call", llm_call_node)

    # Tool execution (with human-in-the-loop confirmation)
    add_node("prepare_tool_execution", prepare_tool_execution_node)
    add_node("tool_confirm", tool_confirm_node)
    add_node("tool_executor", tool_executor_node)
    add_node("checkpoint_task", checkpoint_task_node)
    add_node("save_memory", save_memory_node)
    add_node("verification_gate", verification_gate_node)
    add_node("finalize_task", finalize_task_node)
    add_node("persist_memory", persist_memory_node)

    # Compression nodes
    add_node("compress_context", compress_context_node)
    add_node("manual_compress", manual_compress_node)

    # Optional: Background and inbox checks
    add_node("check_background", check_background_node)
    add_node("check_inbox", check_inbox_node)

    # === Define Edges ===

    # Entry flow (no load_memory — RedisSaver restores state automatically)
    graph.set_entry_point("task_parse")
    graph.add_edge("task_parse", "init_context")
    graph.add_edge("init_context", "check_background")

    # Pre-processing before LLM
    graph.add_edge("check_background", "check_inbox")   # Inject inbox messages
    graph.add_edge("check_inbox", "plan_task")          # Explicit planning phase
    graph.add_edge("plan_task", "pre_microcompact")     # Apply microcompact
    graph.add_edge("pre_microcompact", "llm_call")      # Then call LLM

    # Conditional routing after LLM
    # tool_call -> tool_confirm (human-in-the-loop check) -> tool_executor
    graph.add_conditional_edges(
        "llm_call",
        route_after_llm,
        {
            "tool_call": "prepare_tool_execution",
            "save_memory": "save_memory",  # Text response -> save then end
            "compress": "compress_context",
        }
    )

    # Persist status before a potential LangGraph interrupt.
    graph.add_edge("prepare_tool_execution", "tool_confirm")
    graph.add_edge("tool_confirm", "tool_executor")

    # Tool execution flow — always run microcompact before next LLM call
    graph.add_edge("tool_executor", "checkpoint_task")
    graph.add_edge("checkpoint_task", "save_memory")
    graph.add_conditional_edges(
        "save_memory",
        route_after_tool,
        {
            "end": "finalize_task",
            "verify": "verification_gate",
            "compress": "compress_context",
            "manual_compress": "manual_compress",
            "llm_call": "pre_microcompact"  # Run microcompact before returning to LLM
        }
    )
    graph.add_edge("verification_gate", "pre_microcompact")
    graph.add_edge("finalize_task", "persist_memory")
    graph.add_edge("persist_memory", END)

    # Compression flow - back to LLM with compressed context
    graph.add_edge("compress_context", "llm_call")

    # Manual compress ends the invocation
    graph.add_edge("manual_compress", "finalize_task")

    # Compile with RedisSaver for persistent state management
    # TTL ensures checkpoints are automatically cleaned up after expiry
    checkpointer = AsyncRedisSaver(
        redis_client=_checkpointer_client,
        ttl={"default_ttl": settings.CHECKPOINT_TTL_HOURS * 60}
    )
    return graph.compile(checkpointer=checkpointer)


def build_simple_agent_graph(checkpointer=None):
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

    def add_node(name, node):
        graph.add_node(name, _traced_node(name, node))

    # Add nodes
    add_node("task_parse", task_parse_node)
    add_node("init_context", init_context_node)
    add_node("plan_task", plan_task_node)
    add_node("pre_microcompact", pre_llm_microcompact_node)
    add_node("llm_call", llm_call_node)
    add_node("prepare_tool_execution", prepare_tool_execution_node)
    add_node("tool_confirm", tool_confirm_node)
    add_node("tool_executor", tool_executor_node)
    add_node("checkpoint_task", checkpoint_task_node)
    add_node("save_memory", save_memory_node)
    add_node("compress_context", compress_context_node)
    add_node("verification_gate", verification_gate_node)
    add_node("finalize_task", finalize_task_node)
    add_node("persist_memory", persist_memory_node)

    # Entry flow (no load_memory — RedisSaver restores state automatically)
    graph.set_entry_point("task_parse")
    graph.add_edge("task_parse", "init_context")
    graph.add_edge("init_context", "plan_task")
    graph.add_edge("plan_task", "pre_microcompact")
    graph.add_edge("pre_microcompact", "llm_call")

    # Conditional routing after LLM
    # tool_call -> tool_confirm (human-in-the-loop check) -> tool_executor
    graph.add_conditional_edges(
        "llm_call",
        route_after_llm,
        {
            "tool_call": "prepare_tool_execution",
            "save_memory": "save_memory",  # Text response -> save then end
            "compress": "compress_context",
        }
    )

    graph.add_edge("prepare_tool_execution", "tool_confirm")
    graph.add_edge("tool_confirm", "tool_executor")

    # Tool flow — always run microcompact before next LLM call
    graph.add_edge("tool_executor", "checkpoint_task")
    graph.add_edge("checkpoint_task", "save_memory")
    graph.add_conditional_edges(
        "save_memory",
        route_after_tool,
        {
            "end": "finalize_task",
            "verify": "verification_gate",
            "compress": "compress_context",
            "manual_compress": "finalize_task",
            "llm_call": "pre_microcompact"  # Run microcompact before returning to LLM
        }
    )
    graph.add_edge("verification_gate", "pre_microcompact")
    graph.add_edge("finalize_task", "persist_memory")
    graph.add_edge("persist_memory", END)

    # Compress back to LLM
    graph.add_edge("compress_context", "llm_call")

    if checkpointer is None:
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
