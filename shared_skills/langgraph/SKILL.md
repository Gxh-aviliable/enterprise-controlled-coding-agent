---
name: langgraph
description: LangGraph framework patterns, StateGraph construction, and node design
---

# LangGraph Development Patterns

## Graph Construction
- Use `StateGraph(AgentState)` with TypedDict state
- Build separate `build_agent_graph()` functions for each agent variant
- Always compile with a checkpointer for persistence
- Use `add_conditional_edges()` for routing, not imperative if/else

## Node Design
- Each node is an async function: `async def node(state: AgentState) -> Dict[str, Any]`
- Nodes return partial state updates, not full state
- Use `add_messages` reducer for message lists
- Keep nodes small and focused — one responsibility per node

## Streaming
- Use `graph.astream(input, config)` for SSE streaming
- Stream modes: `values` (full state) or `updates` (deltas only)
- Handle `interrupt` events for human-in-the-loop

## Checkpointer
- Use `AsyncRedisSaver` for production persistence
- TTL via `ttl={"default_ttl": minutes}`
- Call `checkpointer.asetup()` before first use
- `thread_id` maps to session_id

## Common Patterns
- Tool calling: bind tools with `.bind_tools(ALL_TOOLS)`, parse `response.tool_calls`
- Human-in-the-loop: use `interrupt()` in nodes, `Command(resume=...)` to continue
- Memory: Redis for short-term, Chroma for long-term semantic
