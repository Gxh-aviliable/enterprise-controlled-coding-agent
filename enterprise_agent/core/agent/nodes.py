"""LangGraph agent nodes for Enterprise Agent.

Each node is an async function that takes state and returns state updates.
Nodes are connected in a StateGraph workflow defined in graph.py.

Node flow:
    init_context -> check_background -> check_inbox -> pre_microcompact -> llm_call
                                                                              |
                         +----------------------------------------------------+
                         |                    |                               |
                    tool_executor         compress_context                END
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

State persistence (messages, todos, etc.) is handled automatically by
RedisSaver checkpointer — no manual message loading/saving needed.
"""

import asyncio
import json
import logging
import os as _os
import platform
from pathlib import Path
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import Command, interrupt

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.context import get_context_manager
from enterprise_agent.core.agent.llm_factory import get_llm
from enterprise_agent.core.agent.state import AgentState
from enterprise_agent.core.agent.tools import ALL_TOOLS, tool_requires_confirmation, get_sensitive_tool_info


# System prompts for different agent roles
MAIN_SYSTEM_PROMPT = """You are an enterprise-grade AI assistant with access to powerful tools.

## Environment
{environment_info}

## Available Skills
{available_skills}

## When NOT to Use Tools
- Simple greetings ("你好", "hi", "hello") → respond directly, NO tools
- Simple questions you can answer directly → respond directly, NO tools
- Casual chat → just chat, NO tools

## Decision Framework
Before acting, check these questions. If YES, use the indicated tool:

1. Simple chat? → respond directly (skip tools)
2. Domain knowledge needed? → check Available Skills above first; use `load_skill(name)` if relevant
3. Independent sub-tasks? → `spawn_teammate()` (parallel agents)
4. Search large codebase? → `task(agent_type="Explore")`
5. Long-running command? → `background_run()` then `check_background()`
6. Complex implementation? → `task(agent_type="general-purpose")`
7. Context too long? → use `compress`

## Critical Rules
- Use ACTUAL tools (spawn_teammate, task, background_run), NOT simulated Python scripts
- Use Windows commands: `dir`, `cd /d`, `python` (NOT `ls`, `pwd`, `python3`)
- Track multi-step work with `todo_update`
- Be concise and direct in responses"""


def _build_environment_info() -> str:
    """Build environment info block for system prompt."""
    from enterprise_agent.core.agent.tools.workspace import get_user_workspace
    try:
        workspace = get_user_workspace()
    except Exception:
        workspace = str(Path(_os.getcwd()))
    return (
        f"- OS: {platform.system()} ({platform.release()})\n"
        f"- Shell: cmd.exe (Windows) — use Windows commands like `dir`, `cd /d`, `mkdir` (no -p)\n"
        f"- Workspace: {workspace}\n"
        f"- Python: {platform.python_version()}\n"
        f"- Encoding: utf-8 (PYTHONIOENCODING=utf-8 is auto-set for all commands)"
    )

def _build_available_skills(state: Dict) -> str:
    """Build available skills block for system prompt.

    Injects the list of global + user skills so the LLM knows
    what knowledge modules are available without a tool call.
    """
    try:
        from enterprise_agent.core.agent.tools.skills import get_skill_loader
        user_id = state.get("user_id")
        loader = get_skill_loader(user_id)
        return loader.descriptions()
    except Exception as e:
        return f"(skills unavailable: {e})"


def _extract_text(content: Any) -> str:
    """Extract plain text from LLM response content, which may be str or content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif hasattr(block, "text"):
                parts.append(block.text)
        return "\n".join(parts) if parts else str(content)
    return str(content)


# LLM retry configuration
MAX_LLM_RETRIES = 3
RETRY_BASE_DELAY = 2.0  # seconds

# Lazy LLM initialization (avoids crash at import time if API key not set)
_llm = None
_llm_with_tools = None


def get_llm_with_tools():
    """Get LLM with tools bound (lazy initialization)."""
    global _llm, _llm_with_tools
    if _llm is None:
        _llm = get_llm()
        _llm_with_tools = _llm.bind_tools(ALL_TOOLS)
    return _llm_with_tools


def _convert_to_langchain_messages(messages: List[Any]) -> List[Any]:
    """Convert messages to LangChain message objects.

    Handles both dict messages and existing LangChain message objects.

    Args:
        messages: List of message dicts or LangChain message objects

    Returns:
        List of LangChain message objects
    """
    result = []
    for msg in messages:
        # If already a LangChain message, use it directly
        if hasattr(msg, "type") and hasattr(msg, "content"):
            result.append(msg)
            continue

        # Otherwise, convert from dict
        if isinstance(msg, dict):
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role in ("user", "human"):
                result.append(HumanMessage(content=content))
            elif role in ("assistant", "ai"):
                tool_calls = msg.get("tool_calls", [])
                # Preserve original content blocks (e.g., thinking blocks from
                # DeepSeek's thinking mode) if they were stored during conversion.
                content_blocks = msg.get("content_blocks")
                if content_blocks is not None:
                    result.append(AIMessage(content=content_blocks, tool_calls=tool_calls))
                else:
                    result.append(AIMessage(content=content, tool_calls=tool_calls))
            elif role == "system":
                result.append(SystemMessage(content=content))
            elif role == "tool":
                result.append(ToolMessage(
                    content=content,
                    tool_call_id=msg.get("tool_call_id", "")
                ))
            else:
                result.append(HumanMessage(content=content))
        else:
            # Fallback for unknown types
            result.append(HumanMessage(content=str(msg)))

    return result


def _convert_from_langchain_messages(messages: List[Any]) -> List[Dict]:
    """Convert LangChain message objects to dicts.

    Args:
        messages: List of LangChain messages

    Returns:
        List of message dicts
    """
    result = []
    for msg in messages:
        if hasattr(msg, "type"):
            role = msg.type
            raw_content = msg.content

            # Preserve list-type content (e.g. thinking blocks from
            # DeepSeek/Anthropic extended thinking) as-is.
            # _extract_text() strips thinking blocks, which causes
            # DeepSeek API to return 400: "The `content[].thinking`
            # in the thinking mode must be passed back to the API."
            # Storing the list directly ensures the add_messages
            # reducer round-trips it correctly.
            if isinstance(raw_content, list):
                content = raw_content
            else:
                content = _extract_text(raw_content) if raw_content else ""

            tool_call_id = getattr(msg, "tool_call_id", None)

            entry = {"role": role, "content": content}
            if tool_call_id:
                entry["tool_call_id"] = tool_call_id

            # Extract tool calls from AIMessage
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.get("id", ""),
                        "name": tc.get("name", ""),
                        "args": tc.get("args", {})
                    }
                    for tc in msg.tool_calls
                ]

            result.append(entry)
        elif isinstance(msg, dict):
            result.append(msg)
        else:
            result.append({"role": "unknown", "content": str(msg)})

    return result


async def init_context_node(state: AgentState) -> Dict[str, Any]:
    """Initialize context node - Reset transient state + inject long-term memory.

    Messages are NOT cleared here — RedisSaver automatically restores
    the previous conversation history from the checkpointer.

    Memory retrieval flow:
    1. Search user patterns first (preferences, workflows)
    2. Search relevant conversations
    3. Update access count for retrieved memories
    """
    session_id = state.get("session_id", "")

    # === Clear TodoManager and BackgroundManager for new sessions ===
    from enterprise_agent.core.agent.tools.task import clear_todo_manager, get_todo_manager
    from enterprise_agent.core.agent.tools.background import clear_background_manager
    from enterprise_agent.core.agent.context import get_context_manager

    messages = state.get("messages", [])
    is_new_session = len(messages) == 1

    if is_new_session:
        # New session: clear any leftover state from previous sessions
        clear_todo_manager(session_id)
        clear_background_manager(session_id)
        logging.info(f"[init_context] New session {session_id}: cleared TodoManager and BackgroundManager")
    else:
        # Existing session: restore todos from AgentState if available
        todos = state.get("todos", [])
        if todos:
            todo_mgr = get_todo_manager(session_id)
            todo_mgr.items = todos
            logging.info(f"[init_context] Session {session_id}: restored {len(todos)} todos from state")

    # === Estimate token count from actual messages (not reset to 0) ===
    # This ensures correct compression threshold check across multi-turn conversations
    ctx_mgr = get_context_manager()
    initial_token_count = ctx_mgr.estimate_tokens(messages)
    logging.info(f"[init_context] Estimated token count from {len(messages)} messages: {initial_token_count}")

    result = {
        "token_count": initial_token_count,  # Estimated from actual messages, not 0
        "pending_tool_calls": [],
        "tool_results": {},
        "tool_call_stats": {},
        "round_count": 0,
        "should_compress": False,
        "should_end": False,
        "should_end_after_save": False,  # Reset - will be set by llm_call_node if no tool calls
        # TodoWrite nag reminder state
        "rounds_without_todo": 0,
        "used_todo_last_round": False,
        "has_open_todos": False,
        # Memory accumulator state
        "memory_accumulator": {},  # Fresh accumulator for new session
        "pending_memory_flush": False,
    }

    # === Chroma 长期记忆检索（仅新会话首条消息）===
    user_id = state.get("user_id")

    if is_new_session and user_id and messages:
        # 找第一条用户消息（用于 prepend 长期记忆）
        first_user_msg = None
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "user":
                first_user_msg = msg
                break

        if first_user_msg:
            original_content = first_user_msg.get("content", "")
            # 获取原始消息 ID（用于实现替换而不是追加）
            msg_id = first_user_msg.get("id")

            try:
                from enterprise_agent.memory.long_term import get_long_term_memory
                memory = get_long_term_memory(user_id)

                # Step 1: 检索用户 patterns（偏好、工作流）
                user_patterns = await memory.search_patterns(
                    query=original_content,
                    n_results=3,
                )

                # Step 2: 检索相关历史对话
                past_conversations = await memory.search_conversations(
                    query=original_content,
                    n_results=5,
                )

                # Step 3: 更新 access count（追踪使用频率）
                for conv in past_conversations:
                    doc_id = conv.get("id") or conv.get("metadata", {}).get("doc_id")
                    if doc_id:
                        try:
                            await memory.update_access_count(doc_id)
                        except Exception:
                            pass  # Non-fatal

                # Format output: patterns first, then conversations
                context_parts = []

                # Patterns section
                if user_patterns:
                    context_parts.append("=== 用户偏好/习惯 ===")
                    for p in user_patterns:
                        p_type = p.get("pattern_type", "unknown")
                        p_key = p.get("pattern_key", "")
                        confidence = p.get("confidence", 0)
                        context_parts.append(f"[{p_type}] {p_key} (置信度: {confidence:.2f})")

                # Conversations section
                # task_summary documents are prioritized and displayed differently
                if past_conversations:
                    # Sort: task_summary first, then other roles
                    summaries = [c for c in past_conversations if c.get("metadata", {}).get("role") == "task_summary"]
                    others = [c for c in past_conversations if c.get("metadata", {}).get("role") != "task_summary"]

                    context_parts.append("\n=== 相关历史对话 ===")

                    # Task summaries: show more content (they're structured and concise)
                    for conv in summaries:
                        content = conv.get("content", "")
                        if len(content) > 500:
                            content = content[:500] + "..."
                        context_parts.append(f"[Task Summary]: {content}")

                    # Other roles: shorter preview
                    for conv in others:
                        role = conv.get("metadata", {}).get("role", "unknown")
                        content = conv.get("content", "")
                        if len(content) > 300:
                            content = content[:300] + "..."
                        context_parts.append(f"[{role}]: {content}")

                if context_parts:
                    context_text = "\n".join(context_parts)
                    if len(context_text) > 2000:
                        context_text = context_text[:2000] + "..."

                    # Prepend 长期记忆到第一条用户消息内容中（而不是追加单独消息）
                    # 保持相同 ID 以实现替换（而不是追加新消息）
                    new_content = (
                        "<long_term_memory>\n"
                        "以下是与当前问题相关的历史记忆，供参考：\n"
                        f"{context_text}\n"
                        "</long_term_memory>\n\n"
                        f"{original_content}"
                    )

                    new_msg = {
                        "role": "user",
                        "content": new_content,
                    }
                    if msg_id:
                        new_msg["id"] = msg_id  # 保持 ID 相同，实现替换

                    result["messages"] = [new_msg]

                    # 重新估算 token_count（长期记忆已注入）
                    initial_token_count = ctx_mgr.estimate_tokens([new_msg])
                    result["token_count"] = initial_token_count
                    logging.info(f"[init_context] Re-estimated token count after memory injection: {initial_token_count}")

            except Exception:
                logging.warning("Chroma memory search failed (non-fatal)", exc_info=True)

    return result


async def pre_llm_microcompact_node(state: AgentState) -> Dict[str, Any]:
    """Apply microcompact before LLM call.

    Clears old tool results to prevent token bloat.
    This is the key mechanism from original mini_claude_code.py.
    """
    messages = state.get("messages", [])
    ctx_mgr = get_context_manager()

    # Apply microcompact (use langchain version to handle message objects)
    compacted = ctx_mgr.microcompact_langchain(messages, keep_last=settings.MICROCOMPACT_KEEP_LAST)

    return {"messages": compacted}


async def llm_call_node(state: AgentState) -> Dict[str, Any]:
    """LLM call node - Invoke LLM with tools bound.

    Handles both text responses and tool use requests.
    Intermediate system-level context (memory, background, inbox) uses
    role="user" with XML tags so MAIN_SYSTEM_PROMPT stays the sole SystemMessage.
    """
    messages = state.get("messages", [])

    # Strip any stray system messages from state before conversion.
    # Anthropic API requires all SystemMessage instances to be consecutive
    # at the start — any system-role message in the middle would break.
    # Only MAIN_SYSTEM_PROMPT (injected below) is the allowed SystemMessage.
    messages = [
        m for m in messages
        if not (isinstance(m, dict) and m.get("role") == "system")
        and not (hasattr(m, "type") and getattr(m, "type", "") == "system")
    ]

    # Convert to LangChain format for invocation
    lc_messages = _convert_to_langchain_messages(messages)

    # Insert system prompt as the sole SystemMessage at the beginning.
    # Inject live environment info, available skills
    lc_messages.insert(0, SystemMessage(content=MAIN_SYSTEM_PROMPT.format(
        environment_info=_build_environment_info(),
        available_skills=_build_available_skills(state)
    )))

    # Log: entering LLM call
    msg_count = len(lc_messages)
    total_chars = sum(len(str(m.content)) if hasattr(m, "content") else 0 for m in lc_messages)
    logging.info(f"[llm_call] {msg_count} messages (~{total_chars} chars, ~{state.get('token_count', 0)} tokens) → invoking LLM...")

    # LLM call with retry on transient failures
    for attempt in range(MAX_LLM_RETRIES):
        try:
            response = await get_llm_with_tools().ainvoke(lc_messages)
            break
        except Exception as e:
            # Don't retry on permanent errors (auth, bad request, not found)
            error_msg = str(e).lower()
            non_retryable = any(code in error_msg for code in (
                '401', '403', '400', '404', 'invalid', 'unauthorized',
                'authentication', 'permission', 'not found'
            ))
            if non_retryable or attempt >= MAX_LLM_RETRIES - 1:
                logging.exception(f"LLM call failed (attempt {attempt+1}/{MAX_LLM_RETRIES}): {e}")
                raise

            delay = RETRY_BASE_DELAY * (2 ** attempt)
            logging.warning(
                f"LLM call failed (attempt {attempt+1}/{MAX_LLM_RETRIES}): {e}. "
                f"Retrying in {delay}s..."
            )
            await asyncio.sleep(delay)

    # Extract tool calls if present
    tool_calls = []
    if hasattr(response, "tool_calls") and response.tool_calls:
        tool_calls = [
            {
                "id": tc.get("id", ""),
                "name": tc.get("name", ""),
                "args": tc.get("args", {})
            }
            for tc in response.tool_calls
        ]
        for tc in tool_calls:
            logging.info(f"[llm_call] → tool: {tc['name']}({json.dumps(tc['args'], ensure_ascii=False)[:200]})")
    else:
        text_preview = str(response.content)[:150] if hasattr(response, "content") and response.content else "(empty)"
        logging.info(f"[llm_call] → text response: {text_preview}")

    # Track token usage
    token_count = state.get("token_count", 0)
    usage = getattr(response, "usage_metadata", {})
    if usage:
        token_count += usage.get("total_tokens", 0)
    else:
        # Estimate if not provided
        ctx_mgr = get_context_manager()
        token_count += ctx_mgr.estimate_tokens([response])

    # Convert response back to dict format
    response_dict = _convert_from_langchain_messages([response])[0]

    round_count = state.get("round_count", 0) + 1
    logging.info(f"[llm_call] round {round_count}/{settings.MAX_AGENT_ROUNDS}")

    # Determine if this should end after save_memory
    # When there are no tool calls, the text response should end the invocation
    should_end_after_save = not tool_calls  # True if no tool calls

    return {
        "messages": [response_dict],
        "pending_tool_calls": tool_calls,
        "token_count": token_count,
        "round_count": round_count,
        "should_end_after_save": should_end_after_save,  # Signal to route_after_tool
    }


# Tools that are safe to retry (read-only, no side effects)
IDEMPOTENT_TOOLS = {
    "read_file", "list_skills", "list_teammates", "list_transcripts",
    "get_transcript", "context_status", "check_background", "read_inbox",
    "task_list", "task_get",
}

# Error patterns that indicate transient failures worth retrying
RETRYABLE_ERROR_PATTERNS = ("timeout", "connection", "rate limit", "try again")

MAX_TOOL_RETRIES = 2


def _should_retry_tool(tool_name: str, error: Exception) -> bool:
    """Only retry idempotent (read-only) tools on transient errors.

    Tools with side effects (write_file, bash, edit_file, etc.) are never
    retried because re-executing them would duplicate the side effect.
    """
    if tool_name not in IDEMPOTENT_TOOLS:
        return False
    error_str = str(error).lower()
    return any(pattern in error_str for pattern in RETRYABLE_ERROR_PATTERNS)


async def tool_executor_node(state: AgentState) -> Dict[str, Any]:
    """Tool executor node - Execute pending tool calls.

    Runs each tool and collects results.
    Special handling for compress tool to trigger compression.
    Tracks TodoWrite usage for nag reminder mechanism.

    Idempotent (read-only) tools are retried on transient errors.
    Side-effect tools (write, bash, etc.) are never retried.
    """
    from enterprise_agent.core.agent.tools.task import get_todo_manager
    from enterprise_agent.core.agent.tools.workspace import set_current_user_id, set_current_session_id

    # Set context variables for tools to access
    session_id = state.get("session_id", "")
    user_id = state.get("user_id")
    set_current_session_id(session_id)
    if user_id:
        set_current_user_id(user_id)

    results = {}
    tool_map = {t.name: t for t in ALL_TOOLS}
    compress_requested = False
    used_todo = False
    updated_todos = None  # Track todos for AgentState persistence

    pending = state.get("pending_tool_calls", [])
    tool_call_stats = state.get("tool_call_stats", {}).copy()  # mutable state: copy before modifying
    logging.info(f"[tool_exec] Session {session_id}: executing {len(pending)} tool(s): {[tc.get('name') for tc in pending]}")

    for tool_call in pending:
        tool_name = tool_call.get("name")
        tool_input = tool_call.get("args", {})
        tool_id = tool_call.get("id", tool_name)

        # Auto-increment tool call stats (framework counts, no LLM hallucination)
        tool_call_stats[tool_name] = tool_call_stats.get(tool_name, 0) + 1

        if tool_name not in tool_map:
            results[tool_id] = f"Unknown tool: {tool_name}"
            logging.warning(f"[tool_exec] unknown tool: {tool_name}")
            continue

        tool = tool_map[tool_name]
        last_error = None

        for attempt in range(MAX_TOOL_RETRIES):
            try:
                # Invoke tool (tools may be sync or async)
                if hasattr(tool, "ainvoke"):
                    result = await tool.ainvoke(tool_input)
                else:
                    result = tool.invoke(tool_input)

                # Track TodoWrite usage for nag reminder
                if tool_name == "todo_update":
                    used_todo = True
                    # Save todos to AgentState for Redis persistence
                    updated_todos = tool_input.get("todos", [])
                    logging.info(f"[tool_exec] todo_update: saved {len(updated_todos)} todos to AgentState")

                # Special handling for compress tool
                if tool_name == "compress":
                    compress_requested = True
                    result_str = str(result)
                else:
                    # Limit output size
                    result_str = str(result)
                    if len(result_str) > settings.TOOL_OUTPUT_MAX_CHARS:
                        result_str = result_str[:settings.TOOL_OUTPUT_MAX_CHARS] + "\n... (truncated, see transcript)"

                results[tool_id] = result_str
                result_preview = result_str[:120].replace("\n", " ")
                logging.info(f"[tool_exec] ✓ {tool_name} ({len(result_str)} chars): {result_preview}...")
                break
            except Exception as e:
                last_error = e
                if attempt < MAX_TOOL_RETRIES - 1 and _should_retry_tool(tool_name, e):
                    delay = 1.0 * (attempt + 1)
                    logging.warning(
                        f"Retrying idempotent tool '{tool_name}' after error: {e} "
                        f"(attempt {attempt+1}/{MAX_TOOL_RETRIES})"
                    )
                    await asyncio.sleep(delay)
                else:
                    results[tool_id] = f"Error executing {tool_name}: {e}"
                    logging.warning(f"[tool_exec] ✗ {tool_name} FAILED: {e}")
                    break

    # Build tool result messages
    tool_result_messages = []
    for tool_id, result in results.items():
        tool_result_messages.append({
            "role": "tool",
            "content": result,
            "tool_call_id": tool_id
        })

    # Check if there are open todos for nag reminder
    # Use updated_todos if available, otherwise check existing TodoManager
    todo_mgr = get_todo_manager(session_id)
    if updated_todos:
        # Update TodoManager with new todos
        todo_mgr.items = updated_todos
        has_open_todos = todo_mgr.has_open_items()
    else:
        # No todo_update in this round, check existing state
        has_open_todos = todo_mgr.has_open_items()

    result_dict = {
        "tool_results": results,
        "pending_tool_calls": [],
        "messages": tool_result_messages,
        "tool_call_stats": tool_call_stats,  # Framework auto-counted stats
        "should_compress": compress_requested,  # Trigger compression if requested
        "used_todo_last_round": used_todo,
        "has_open_todos": has_open_todos,
        "should_end_after_save": False,  # After tool execution, need to continue to next LLM call
    }

    # Persist todos to AgentState (for Redis checkpoint)
    if updated_todos:
        result_dict["todos"] = updated_todos

    return result_dict


async def save_memory_node(state: AgentState) -> Dict[str, Any]:
    """Save memory node - handles TodoWrite nag reminder logic + memory accumulator.

    Message persistence is handled automatically by RedisSaver checkpointer.

    This node also:
    1. Accumulates meaningful content across rounds (not per-round fragments)
    2. Flushes accumulated content to Chroma at task boundaries as task_summary
    3. Extracts user patterns from high-importance task summaries
    4. Injects auto-counted tool_call_stats when wrapping up
    """
    # === TodoWrite nag reminder mechanism (s03) ===
    used_todo = state.get("used_todo_last_round", False)
    rounds_without_todo = state.get("rounds_without_todo", 0)
    rounds_without_todo = 0 if used_todo else rounds_without_todo + 1

    has_open_todos = state.get("has_open_todos", False)
    additional_messages = []

    if has_open_todos and rounds_without_todo >= settings.NAG_REMINDER_THRESHOLD:
        additional_messages.append({
            "role": "user",
            "content": "<reminder>Update your todos. You have open todo items that need status updates.</reminder>"
        })
        rounds_without_todo = 0

    # Inject auto-counted tool stats when agent finishes all todos.
    # The LLM self-reports tool counts unreliably (e.g. 24 vs actual 37).
    # Framework-counted stats are injected once as ground truth.
    tool_stats = state.get("tool_call_stats", {})
    if tool_stats and not has_open_todos and used_todo:
        total = sum(tool_stats.values())
        stats_text = "\n".join(f"- {name}: {count}" for name, count in sorted(tool_stats.items()))
        additional_messages.append({
            "role": "user",
            "content": (
                f"<tool_stats>\n"
                f"Framework-counted tool usage (accurate, use these numbers):\n"
                f"{stats_text}\n"
                f"Total: {total} tool calls\n"
                f"</tool_stats>"
            )
        })

    # === Task-level memory accumulator (replaces per-round fragment storage) ===
    from enterprise_agent.memory.accumulator import MemoryAccumulator

    messages = state.get("messages", [])
    user_id = state.get("user_id")
    session_id = state.get("session_id", "unknown")

    acc = MemoryAccumulator()
    accumulator_state = state.get("memory_accumulator", {})

    # 1. Accumulate content from current round
    accumulator_state = acc.accumulate_round(state, messages, accumulator_state)

    # 2. Check if we're at a task boundary → flush to Chroma
    should_flush = acc.should_flush({
        **state,
        "memory_accumulator": accumulator_state  # Use updated accumulator
    })

    if should_flush and accumulator_state.get("user_request"):
        # 3. Flush: generate summary + evaluate importance + store to Chroma
        try:
            flush_result = await acc.flush(accumulator_state, session_id, user_id, messages)
            accumulator_state = flush_result["accumulator_reset"]
            logging.info(
                f"[save_memory] Flush complete: stored={flush_result['stored']}, "
                f"importance={flush_result['importance']:.2f}"
            )
        except Exception as e:
            logging.warning(f"[save_memory] Accumulator flush failed (non-fatal): {e}", exc_info=True)

    return {
        "rounds_without_todo": rounds_without_todo,
        "messages": additional_messages,
        "memory_accumulator": accumulator_state,
    }


async def compress_context_node(state: AgentState) -> Dict[str, Any]:
    """Compress context node - Full summarization when threshold exceeded.

    This implements the auto-compact mechanism:
    1. Check token threshold
    2. Save transcript to file
    3. Generate summary via LLM
    4. Replace messages with summary
    5. Store summary to Chroma as long-term memory
    """
    ctx_mgr = get_context_manager()
    token_count = state.get("token_count", 0)

    # Check if compression needed
    if token_count > settings.TOKEN_THRESHOLD:
        messages = state.get("messages", [])
        session_id = state.get("session_id", "unknown")
        user_id = state.get("user_id")

        # Perform full compression
        compression_result = await ctx_mgr.auto_compact(messages, session_id)

        # Store compression summary to Chroma as long-term memory
        summary = compression_result.get("context_summary")
        if summary and user_id:
            try:
                from enterprise_agent.memory.long_term import get_long_term_memory
                memory = get_long_term_memory(user_id)
                await memory.store_conversation(
                    session_id=session_id,
                    role="system",
                    content=_extract_text(summary),
                    metadata={"type": "session_summary"},
                )
            except Exception:
                logging.warning("Chroma memory store failed during compression (non-fatal)", exc_info=True)

        # Append explicit continuation instruction so the LLM doesn't just
        # echo the summary and stop — it receives a clear prompt to act.
        compressed_msgs = compression_result["compressed_messages"]
        compressed_msgs.append({
            "role": "user",
            "content": "<system-reminder>Context has been compressed. Continue the task immediately. Take the next concrete action using a tool — do NOT summarize or repeat what was in the compressed context.</system-reminder>"
        })

        return {
            "messages": compressed_msgs,
            "context_summary": summary,
            "transcript_path": compression_result["transcript_path"],
            "token_count": compression_result["token_count_reset"],
            "should_compress": False,
            # Reset accumulator but preserve pre-compression summary for future flush
            "memory_accumulator": {
                "user_request": "",
                "assistant_responses": [],
                "tool_actions": [],
                "round_count": 0,
                "start_timestamp": "",
                "context_summary_pre": _extract_text(summary) if summary else "",
            },
        }

    return {"should_compress": False}


async def manual_compress_node(state: AgentState) -> Dict[str, Any]:
    """Manual compression node - Triggered by compress tool.

    Always performs compression regardless of threshold.
    """
    ctx_mgr = get_context_manager()
    messages = state.get("messages", [])
    session_id = state.get("session_id", "unknown")
    user_id = state.get("user_id")

    # Always compress when manually triggered
    compression_result = await ctx_mgr.manual_compress(messages, session_id)

    # Store compression summary to Chroma as long-term memory
    summary = compression_result.get("context_summary")
    if summary and user_id:
        try:
            from enterprise_agent.memory.long_term import get_long_term_memory
            memory = get_long_term_memory(user_id)
            await memory.store_conversation(
                session_id=session_id,
                role="system",
                content=_extract_text(summary),
                metadata={"type": "session_summary"},
            )
        except Exception:
            logging.warning("Chroma memory store failed during manual compression (non-fatal)", exc_info=True)

    return {
        "messages": compression_result["compressed_messages"],
        "context_summary": summary,
        "transcript_path": compression_result["transcript_path"],
        "token_count": compression_result["token_count_reset"],
        "should_compress": False,
        "should_end": True,  # End this invocation after manual compress
        # Reset accumulator — manual compress ends the invocation
        "memory_accumulator": {
            "user_request": "",
            "assistant_responses": [],
            "tool_actions": [],
            "round_count": 0,
            "start_timestamp": "",
            "context_summary_pre": _extract_text(summary) if summary else "",
        },
    }


def route_after_llm(state: AgentState) -> str:
    """Route after LLM call based on state.

    Determines next node based on:
    - Max rounds exceeded -> save_memory (will end due to round_count check in route_after_tool)
    - Has tool calls -> tool_executor
    - Exceeds token threshold -> compress_context
    - Otherwise -> save_memory (will end due to should_end_after_save=True from llm_call_node)

    Note: DO NOT modify state in routing functions - state changes must come from node returns.
    """
    # Safety valve: stop if agent has been looping too long
    if state.get("round_count", 0) >= settings.MAX_AGENT_ROUNDS:
        logging.warning(f"[route_after_llm] max rounds ({settings.MAX_AGENT_ROUNDS}) reached, ending")
        return "save_memory"  # route_after_tool will end due to round_count

    # Check for tool calls first
    if state.get("pending_tool_calls"):
        return "tool_call"

    # Check for auto compression threshold
    if state.get("token_count", 0) > settings.TOKEN_THRESHOLD:
        return "compress"

    # No tool calls and no compression needed -> save memory then end
    # llm_call_node already set should_end_after_save=True
    return "save_memory"


def route_after_tool(state: AgentState) -> str:
    """Route after save_memory (from tool_executor or text response).

    After save_memory runs, we check if:
    - should_end_after_save is set -> end (text response completed)
    - Max rounds exceeded -> end
    - Manual compression was requested via compress tool
    - Auto compression threshold exceeded
    before going back to LLM.
    """
    # Check if this was a text response that should end
    if state.get("should_end_after_save"):
        return "end"

    # Safety valve: stop if agent has been looping too long
    if state.get("round_count", 0) >= settings.MAX_AGENT_ROUNDS:
        logging.warning(f"[route_after_tool] max rounds ({settings.MAX_AGENT_ROUNDS}) reached, ending")
        return "end"

    # Check for manual compression request first
    if state.get("should_compress"):
        return "manual_compress"

    token_count = state.get("token_count", 0)

    # Check threshold after tool execution (tool outputs can be large)
    if token_count > settings.TOKEN_THRESHOLD:
        return "compress"

    return "llm_call"


async def check_background_node(state: AgentState) -> Dict[str, Any]:
    """Check background task notifications.

    Drains completed background task results and injects into context.
    """
    from enterprise_agent.core.agent.tools.background import get_background_manager
    from enterprise_agent.core.agent.tools.workspace import set_current_user_id, set_current_session_id

    # Set context variables for tools to access
    session_id = state.get("session_id", "")
    user_id = state.get("user_id")
    set_current_session_id(session_id)
    if user_id:
        set_current_user_id(user_id)

    bg_mgr = get_background_manager(session_id)
    notifications = bg_mgr.drain_notifications()

    if notifications:
        notification_text = "\n".join(
            f"[Background:{n['task_id']}] {n['status']}: {n['result'][:500]}"
            for n in notifications
        )
        return {
            "messages": [{
                "role": "user",
                "content": f"<background-results>\n{notification_text}\n</background-results>"
            }]
        }

    return {}


async def check_inbox_node(state: AgentState) -> Dict[str, Any]:
    """Check inbox for messages from teammates.

    Reads and drains the lead agent's inbox.
    """
    from enterprise_agent.core.agent.tools.team import get_message_bus

    bus = get_message_bus()
    messages = await bus.read_inbox("lead")

    if messages:
        inbox_text = json.dumps(messages, indent=2)
        return {
            "messages": [{
                "role": "user",
                "content": f"<inbox>\n{inbox_text}\n</inbox>"
            }]
        }

    return {}


async def tool_confirm_node(state: AgentState) -> Dict[str, Any]:
    """Confirm sensitive tool executions before proceeding.

    Uses LangGraph interrupt() to pause execution and wait for user approval.
    Returns state updates to either proceed with execution or reject.

    CRITICAL: Non-sensitive tools always pass through without confirmation.
    Only sensitive tools need user approval. The final pending_tool_calls
    contains: non_sensitive_tools + approved_sensitive_tools.

    LangGraph interrupt behavior:
    - First call: interrupt() pauses execution, waits for resume
    - After resume: node re-executes from start, interrupt() returns resume data
    """
    pending = state.get("pending_tool_calls", [])

    if not pending:
        return {}

    # Split tools into sensitive (needs confirmation) and non-sensitive (pass through)
    sensitive_tools = []
    non_sensitive_tools = []
    for tc in pending:
        tool_name = tc.get("name", "")
        if tool_requires_confirmation(tool_name):
            sensitive_tools.append(tc)
        else:
            non_sensitive_tools.append(tc)

    # No sensitive tools -> proceed directly to tool_executor
    if not sensitive_tools:
        logging.info(f"[tool_confirm] No sensitive tools in {len(pending)} pending calls, proceeding")
        return {}

    # Confirmation disabled -> proceed with all tools
    if not settings.ENABLE_TOOL_CONFIRMATION:
        logging.info(f"[tool_confirm] Confirmation disabled, proceeding with {len(sensitive_tools)} sensitive tools")
        return {}

    # Build interrupt request for sensitive tools only
    tool_descriptions = []
    for tc in sensitive_tools:
        tool_name = tc.get("name", "")
        tool_args = tc.get("args", {})
        desc = get_sensitive_tool_info(tool_name, tool_args)
        tool_descriptions.append({
            "id": tc.get("id", ""),
            "name": tool_name,
            "args": tool_args,
            "description": desc,
        })

    # Call interrupt() - will pause on first call, return resume data after resume
    # IMPORTANT: After resume, this node re-executes from start, and interrupt()
    # returns the resume data directly (no second pause)
    user_response = interrupt({
        "type": "tool_confirmation",
        "tools": tool_descriptions,
        "message": f"Confirm execution of {len(sensitive_tools)} sensitive tool(s)?",
    })

    # ========== Only executed AFTER resume (user responded) ==========
    logging.info(f"[tool_confirm] User response received: approved={user_response.get('approved')}, approved_ids={user_response.get('approved_ids', [])}")

    approved = user_response.get("approved", False)
    approved_ids = user_response.get("approved_ids", [])

    if approved:
        # Build final pending_tool_calls:
        # 1. All non-sensitive tools (always pass through)
        # 2. Approved sensitive tools
        final_pending = non_sensitive_tools.copy()

        if approved_ids:
            # Partial approval: add only approved sensitive tools
            for tc in sensitive_tools:
                if tc.get("id") in approved_ids:
                    final_pending.append(tc)
            logging.info(f"[tool_confirm] Partial approval: {len(final_pending)}/{len(pending)} tools proceeding (non-sensitive: {len(non_sensitive_tools)}, approved sensitive: {len(approved_ids)})")
        else:
            # Full approval (no specific IDs): add all sensitive tools
            final_pending.extend(sensitive_tools)
            logging.info(f"[tool_confirm] Full approval: {len(final_pending)} tools proceeding (non-sensitive: {len(non_sensitive_tools)}, all sensitive approved)")

        return {"pending_tool_calls": final_pending}
    else:
        # Rejected: clear all pending tools and inform LLM
        # API requirement: every tool_use must have a corresponding tool_result
        logging.info(f"[tool_confirm] User rejected {len(sensitive_tools)} sensitive tools, clearing all {len(pending)} pending")

        # Build tool_result for ALL pending tools (satisfies API requirement)
        tool_result_messages = []
        for tc in pending:
            tool_id = tc.get("id", "")
            tool_name = tc.get("name", "")
            tool_result_messages.append({
                "role": "tool",
                "content": f"Tool execution rejected by user. The '{tool_name}' tool was not executed.",
                "tool_call_id": tool_id
            })

        # Add user message explaining rejection
        tool_result_messages.append({
            "role": "user",
            "content": (
                "<tool_rejected>\n"
                f"User rejected execution of {len(sensitive_tools)} sensitive tool(s):\n"
                + "\n".join(f"- {tc['name']}: {get_sensitive_tool_info(tc['name'], tc.get('args', {}))}" for tc in sensitive_tools)
                + "\nPlease modify your approach or ask user for clarification.\n"
                "</tool_rejected>"
            )
        })

        return {
            "pending_tool_calls": [],
            "messages": tool_result_messages
        }