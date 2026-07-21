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
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import interrupt

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.context import get_context_manager
from enterprise_agent.core.agent.llm_factory import get_llm
from enterprise_agent.core.agent.state import AgentState
from enterprise_agent.core.agent.tools import (
    ALL_TOOLS,
    get_sensitive_tool_info,
    get_tools_for_permissions,
    tool_requires_confirmation,
)
from enterprise_agent.core.agent.tools.contracts import (
    TOOL_CONTRACTS,
    ToolExecutionRecord,
    ToolResultStatus,
    get_tool_contract,
    normalize_tool_result,
    resolve_tool_risk,
)
from enterprise_agent.core.execution.state_machine import (
    ExecutionPhase,
    TaskStatus,
    transition_task_status,
)
from enterprise_agent.observability.trace_store import get_trace_store

# System prompts for different agent roles
MAIN_SYSTEM_PROMPT = """You are an enterprise-grade AI assistant with access to powerful tools.

## Environment
{environment_info}

## Available Skills
{available_skills}

## Execution Mode
{execution_mode_info}
The single-Agent baseline remains the default unless this block explicitly says MULTI-AGENT.
Delegation is permitted only when multi-Agent mode is explicitly enabled for this request.

## When NOT to Use Tools
- Simple greetings ("你好", "hi", "hello") → respond directly, NO tools
- Simple questions you can answer directly → respond directly, NO tools
- Casual chat → just chat, NO tools

## Decision Framework
Before acting, check these questions. If YES, use the indicated tool:

1. Simple chat? → respond directly (skip tools)
2. **User asks about their memory/preferences/history?** → First, check <long_term_memory> in the first message.
   It contains pre-loaded memories.
   If it says "(no prior memories found)" or the user wants a complete listing, use the `search_memory` tool.
   Actively query ChromaDB when needed.
   Use a broad query like "task summary user preference workflow" to list all stored memories.
   **CRITICAL: ONLY `search_memory` accesses long-term memory.**
   Do NOT use `task_list` (it lists operational .tasks/ JSON files — NOT user memories), do NOT use `list_transcripts`
   (it lists compression backup transcripts — NOT user memories).
   Do NOT explore .tasks/, .transcripts/, .team/ with bash/file tools.
   These are workspace operational artifacts, completely unrelated to the user's long-term memory.
   The one and only tool for long-term memory is `search_memory`.
3. Domain knowledge needed? → check Available Skills above first; use `load_skill(name)` if relevant
4. Follow the Execution Mode block exactly. Never simulate unavailable Agent collaboration.
5. Search code with read-only file/shell tools before considering delegation.
6. Long-running command? → `background_run()` then `check_background()`
7. Complex implementation? → `task(agent_type="general-purpose")`
8. Context too long? → use `compress`

## Critical Rules
- Use only tools present in the bound tool schema; never simulate unavailable tools
- `task_create` only creates an operational tracking record; it never starts another Agent
- Use the shell commands described in the Environment block for this host OS
- Track multi-step work with `todo_update`
- Be concise and direct in responses
- When asked about user's memory/preferences/profile: check <long_term_memory> block first.
  Then use `search_memory` tool to actively query ChromaDB if needed.
  The ONLY tool for long-term memory is `search_memory`.
  Do NOT use `task_list` (operational task tracking), `list_transcripts`
  (compression backups), or explore .tasks/, .transcripts/, .team/ — those are workspace artifacts, NOT user memory."""


def _build_environment_info() -> str:
    """Build environment info block for system prompt."""
    from enterprise_agent.core.agent.tools.workspace import get_user_workspace
    try:
        workspace = get_user_workspace()
    except Exception:
        workspace = str(Path(_os.getcwd()))

    system = platform.system()
    if system == "Windows":
        shell_info = "cmd.exe (Windows) — use commands like `dir`, `cd /d`, `mkdir`, `python`"
    elif system == "Darwin":
        shell_info = "POSIX shell (macOS) — use commands like `ls`, `pwd`, `mkdir -p`, `python3`"
    else:
        shell_info = "POSIX shell (Linux/Unix) — use commands like `ls`, `pwd`, `mkdir -p`, `python3`"

    return (
        f"- OS: {system} ({platform.release()})\n"
        f"- Shell: {shell_info}\n"
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
_llm_with_tools_cache = {}


def _build_execution_mode_info(state: Dict[str, Any]) -> str:
    if state.get("execution_mode") == "multi_agent":
        return (
            "MULTI-AGENT. Use `delegate_task` for real independent specialist roles. "
            "Never write or run a script that merely simulates agents, random opinions, "
            "or collaboration. For creative work, delegate planning, drafting, or review "
            "to actual subagents and let the lead synthesize their returned work. "
            "Name the delegated roles and distinguish their returned contributions in the summary."
        )
    return (
        "SINGLE-AGENT BASELINE. Complete the task yourself. Delegation/team tools are "
        "not available in this run; do not claim that multiple agents collaborated."
    )


def get_llm_with_tools(
    permissions: List[str] | None = None,
    execution_mode: str = "single_agent",
):
    """Get the LLM bound only to tools allowed for this task."""
    global _llm
    if _llm is None:
        _llm = get_llm()

    allowed_tools = get_tools_for_permissions(
        permissions or [],
        enable_multi_agent=(
            execution_mode == "multi_agent" and settings.ENABLE_MULTI_AGENT
        ),
    )
    cache_key = tuple(tool.name for tool in allowed_tools)
    if cache_key not in _llm_with_tools_cache:
        _llm_with_tools_cache[cache_key] = _llm.bind_tools(allowed_tools)
    return _llm_with_tools_cache[cache_key]


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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_trace(
    state: AgentState,
    *,
    event_type: str,
    name: str,
    status: str = "success",
    duration_ms: int = 0,
    data: Dict[str, Any] | None = None,
) -> None:
    """Best-effort trace recording must never break task execution."""
    trace_id = state.get("trace_id")
    user_id = state.get("user_id")
    if not trace_id or user_id is None:
        return
    try:
        get_trace_store().record_event(
            user_id=user_id,
            trace_id=trace_id,
            event_type=event_type,
            name=name,
            status=status,
            duration_ms=duration_ms,
            data=data,
        )
    except Exception:
        logging.warning("Failed to record %s trace event", event_type, exc_info=True)


def _record_tool_trace(
    state: AgentState,
    record: ToolExecutionRecord,
    tool_args: Dict[str, Any],
) -> None:
    try:
        contract = get_tool_contract(record.tool_name)
        contract_data = {
            "risk": resolve_tool_risk(record.tool_name, tool_args).value,
            "idempotent": contract.idempotent,
            "timeout_seconds": contract.timeout_seconds,
        }
    except KeyError:
        contract_data = {"risk": "dangerous", "idempotent": False}
    _record_trace(
        state,
        event_type="tool",
        name=record.tool_name,
        status=record.status.value,
        duration_ms=record.duration_ms,
        data={
            "tool_call_id": record.tool_call_id,
            "args_summary": tool_args,
            "output_summary": record.output[:1000],
            "attempt_count": record.attempt_count,
            "error_code": record.error_code,
            "exit_code": record.exit_code,
            **contract_data,
        },
    )


def _last_user_request(messages: List[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") in ("user", "human"):
            return _extract_text(message.get("content", ""))
        if getattr(message, "type", "") in ("user", "human"):
            return _extract_text(getattr(message, "content", ""))
    return ""


async def task_parse_node(state: AgentState) -> Dict[str, Any]:
    """Start one durable task run and capture its request without an LLM call."""
    started_at = state.get("task_started_at") or _utc_now_iso()
    request = state.get("current_user_request") or _last_user_request(
        state.get("messages", [])
    )
    return {
        "current_user_request": request,
        "task_status": transition_task_status(state.get("task_status"), TaskStatus.RUNNING),
        "execution_phase": ExecutionPhase.PARSING.value,
        "task_started_at": started_at,
        "task_finished_at": None,
        "failure_reason": None,
        "current_task": {
            "request": request[:10000],
            "trace_id": state.get("trace_id", ""),
            "started_at": started_at,
        },
    }


async def plan_task_node(state: AgentState) -> Dict[str, Any]:
    """Mark the planning phase before the first model decision."""
    return {"execution_phase": ExecutionPhase.PLANNING.value}


async def prepare_tool_execution_node(state: AgentState) -> Dict[str, Any]:
    """Checkpoint risk/confirmation state before a potentially interrupting node."""
    pending = state.get("pending_tool_calls", [])
    # Unknown tools are not executable. Let the executor turn them into a
    # normalized failed ToolExecutionRecord instead of trying to classify their
    # risk here (which would raise before the failure can be traced).
    registered_pending = [
        call for call in pending if call.get("name", "") in TOOL_CONTRACTS
    ]
    needs_confirmation = settings.ENABLE_TOOL_CONFIRMATION and any(
        tool_requires_confirmation(call.get("name", ""), call.get("args", {}))
        for call in registered_pending
    )
    if not needs_confirmation:
        return {
            "task_status": transition_task_status(state.get("task_status"), TaskStatus.RUNNING),
            "execution_phase": ExecutionPhase.EXECUTING.value,
            "confirmation_deadline": None,
        }

    deadline = datetime.now(timezone.utc) + timedelta(seconds=settings.CONFIRMATION_TIMEOUT_SECONDS)
    _record_trace(
        state,
        event_type="confirmation",
        name="confirmation_requested",
        status="waiting",
        data={
            "deadline": deadline.isoformat(),
            "tools": [
                {
                    "name": call.get("name", ""),
                    "risk": resolve_tool_risk(call.get("name", ""), call.get("args", {})).value,
                }
                for call in registered_pending
                if tool_requires_confirmation(call.get("name", ""), call.get("args", {}))
            ],
        },
    )
    return {
        "task_status": transition_task_status(
            state.get("task_status"), TaskStatus.WAITING_CONFIRMATION
        ),
        "execution_phase": ExecutionPhase.EXECUTING.value,
        "confirmation_deadline": deadline.isoformat(),
    }


CODE_FILE_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".vue", ".java", ".go",
    ".rs", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift",
    ".kt", ".kts", ".scala", ".sh", ".zsh", ".sql", ".toml", ".yaml", ".yml",
}
CODE_FILE_NAMES = {"Dockerfile", "Makefile", "pyproject.toml", "package.json"}
VALIDATION_MARKERS = (
    "pytest", "unittest", "ruff", "mypy", "pyright", "compileall",
    "npm test", "npm run test", "npm run build", "npm run lint",
    "pnpm test", "pnpm run build", "pnpm run lint", "yarn test", "yarn build",
    "cargo test", "go test", "make test", "gradle test", "mvn test",
)


def _is_code_file(path: str) -> bool:
    candidate = Path(path)
    return candidate.suffix.lower() in CODE_FILE_SUFFIXES or candidate.name in CODE_FILE_NAMES


def _is_validation_command(command: str) -> bool:
    lowered = " ".join(command.lower().split())
    return any(marker in lowered for marker in VALIDATION_MARKERS)


def _has_successful_validation(state: AgentState) -> bool:
    return any(result.get("ok") is True for result in state.get("validation_results", []))


def _needs_verification(state: AgentState) -> bool:
    code_changes = any(_is_code_file(path) for path in state.get("changed_files", []))
    return code_changes and not _has_successful_validation(state)


def terminalize_open_work_items(state: Dict[str, Any], final_status: str) -> List[Dict[str, Any]]:
    """Close checklist and persistent task artifacts when a task cannot continue."""
    todos = [dict(item) for item in state.get("todos", [])]
    if final_status not in {TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}:
        return todos

    for item in todos:
        if item.get("status") in {"pending", "in_progress"}:
            item["status"] = final_status

    created_task_ids = state.get("created_task_ids", [])
    if created_task_ids:
        try:
            from enterprise_agent.core.agent.tools.task import get_task_manager
            from enterprise_agent.core.agent.tools.workspace import set_current_user_id

            user_id = state.get("user_id")
            if user_id is not None:
                set_current_user_id(user_id)
            manager = get_task_manager()
            for task_id in created_task_ids:
                try:
                    task_data = json.loads(manager.get(int(task_id)))
                    if task_data.get("status") in {"pending", "in_progress"}:
                        manager.update(int(task_id), status=final_status)
                except (ValueError, TypeError, json.JSONDecodeError):
                    logging.warning(
                        "Failed to terminalize persistent task %s", task_id, exc_info=True
                    )
        except Exception:
            logging.warning("Failed to terminalize persistent task artifacts", exc_info=True)

    return todos


async def checkpoint_task_node(state: AgentState) -> Dict[str, Any]:
    """Expose an explicit durable checkpoint phase after tool execution."""
    return {"execution_phase": ExecutionPhase.CHECKPOINTING.value}


async def verification_gate_node(state: AgentState) -> Dict[str, Any]:
    """Ask the Agent to validate code changes before it can summarize success."""
    attempts = state.get("verification_attempts", 0) + 1
    failed = [item for item in state.get("validation_results", []) if not item.get("ok")]
    failure_context = ""
    if failed:
        last = failed[-1]
        failure_context = f" The last validation failed: {last.get('command', '')}."
    return {
        "execution_phase": ExecutionPhase.VALIDATING.value,
        "verification_attempts": attempts,
        "should_end_after_save": False,
        "messages": [{
            "role": "user",
            "content": (
                "<verification-required>Code files were modified, but no successful validation "
                "is recorded. Run the narrowest relevant test, build, lint, or compile command "
                "now. If validation cannot run, explain the concrete blocker in the final report."
                f"{failure_context}</verification-required>"
            ),
        }],
    }


async def finalize_task_node(state: AgentState) -> Dict[str, Any]:
    """Finish the task with a truthful terminal status."""
    current = state.get("task_status", TaskStatus.RUNNING.value)
    if current in (TaskStatus.FAILED.value, TaskStatus.CANCELLED.value):
        final_status = current
        failure_reason = state.get("failure_reason")
    elif (
        state.get("round_count", 0) >= settings.MAX_AGENT_ROUNDS
        and not state.get("should_end_after_save")
    ):
        final_status = transition_task_status(current, TaskStatus.FAILED)
        failure_reason = f"Agent round budget exhausted ({settings.MAX_AGENT_ROUNDS} rounds)."
    elif state.get("execution_mode") == "multi_agent" and not any(
        record.get("tool_name") == "delegate_task" and record.get("ok")
        for record in state.get("tool_execution_records", [])
    ):
        final_status = transition_task_status(current, TaskStatus.FAILED)
        failure_reason = (
            "Multi-Agent mode finished without a successful delegate_task call; "
            "simulated collaboration is not accepted."
        )
    elif _needs_verification(state):
        final_status = transition_task_status(current, TaskStatus.FAILED)
        failure_reason = "Code changes were not successfully validated within the task budget."
    else:
        final_status = transition_task_status(current, TaskStatus.SUCCEEDED)
        failure_reason = None

    return {
        "task_status": final_status,
        "execution_phase": ExecutionPhase.SUMMARIZING.value,
        "task_finished_at": _utc_now_iso(),
        "failure_reason": failure_reason,
        "todos": terminalize_open_work_items(state, final_status),
        "has_open_todos": False if final_status in {
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
        } else state.get("has_open_todos", False),
    }


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
    from enterprise_agent.core.agent.context import get_context_manager
    from enterprise_agent.core.agent.tools.background import clear_background_manager
    from enterprise_agent.core.agent.tools.task import clear_todo_manager, get_todo_manager

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
        "task_token_count": 0,
        "pending_tool_calls": [],
        "tool_results": {},
        "tool_call_stats": {},
        "tool_execution_records": [],
        "tool_call_count": 0,
        "created_task_ids": [],
        "round_count": 0,
        "should_compress": False,
        "should_end": False,
        "should_end_after_save": False,  # Reset - will be set by llm_call_node if no tool calls
        # TodoWrite nag reminder state
        "rounds_without_todo": 0,
        "used_todo_last_round": False,
        "has_open_todos": False,
        "changed_files": [],
        "validation_results": [],
        "verification_attempts": 0,
        "confirmation_deadline": None,
        "retrieved_memory_context": "",
    }
    if is_new_session:
        result["memory_accumulator"] = {}  # Fresh accumulator for new session

    # === Chroma memory retrieval for this task invocation ===
    #
    # Keep recall context outside ``messages`` so an injected block cannot be
    # checkpointed and replayed forever. ``llm_call_node`` inserts this
    # ephemeral field for each model round in the current task only.
    user_id = state.get("user_id")
    current_request = state.get("current_user_request") or _last_user_request(messages)

    if settings.ENABLE_LONG_TERM_MEMORY and user_id and current_request:
        retrieval_started = time.perf_counter()
        try:
            from enterprise_agent.memory.long_term import get_long_term_memory

            memory = get_long_term_memory(user_id)
            meta_memory_keywords = [
                "长期记忆", "记忆里", "记得什么", "记住什么", "我的记忆",
                "我的偏好", "我的信息", "关于我", "存储了什么", "有什么记忆",
                "你的记忆", "保存了什么", "记了什么",
                "my memory", "remember me", "what do you know about me",
                "my preferences", "my info", "stored about me",
                "what do you remember", "any memories",
            ]
            is_meta_memory_question = any(
                keyword in current_request.lower()
                for keyword in meta_memory_keywords
            )

            if is_meta_memory_question:
                user_patterns = await memory.get_all_patterns(active_only=True)
                past_conversations = await memory.list_conversations(
                    limit=50,
                    role="task_summary",
                    active_only=True,
                )
                pattern_candidates = [
                    {
                        **item,
                        "rank": index + 1,
                        "eligible": True,
                        "filter_reason": "eligible",
                    }
                    for index, item in enumerate(user_patterns)
                ]
                conversation_candidates = [
                    {
                        **item,
                        "rank": index + 1,
                        "eligible": True,
                        "filter_reason": "eligible",
                    }
                    for index, item in enumerate(past_conversations)
                ]
                logging.info(
                    "[init_context] Meta-memory question detected; "
                    "listing active schema-v2 memories"
                )
            else:
                pattern_candidates = await memory.search_patterns(
                    query=current_request,
                    n_results=3,
                    active_only=True,
                    max_distance=settings.MEMORY_RELEVANCE_MAX_DISTANCE,
                    retrieval_enabled_only=True,
                    include_rejected=True,
                )
                conversation_candidates = await memory.search_conversations(
                    query=current_request,
                    n_results=3,
                    role="task_summary",
                    active_only=True,
                    max_distance=settings.MEMORY_RELEVANCE_MAX_DISTANCE,
                    retrieval_enabled_only=True,
                    include_rejected=True,
                )
                user_patterns = [
                    item for item in pattern_candidates if item.get("eligible")
                ][:3]
                past_conversations = [
                    item for item in conversation_candidates if item.get("eligible")
                ][:3]

            for pattern in user_patterns:
                if pattern.get("id"):
                    await memory.update_pattern_access_count(pattern["id"])
            for conversation in past_conversations:
                if conversation.get("id"):
                    await memory.update_access_count(conversation["id"])

            context_parts = []
            if user_patterns:
                context_parts.append("=== User preferences and workflows ===")
                for pattern in user_patterns:
                    pattern_value = (
                        pattern.get("value")
                        or pattern.get("text")
                        or ""
                    )
                    context_parts.append(
                        f"[memory_id={pattern.get('id', 'unknown')}] "
                        f"[{pattern.get('pattern_type', 'unknown')}] "
                        f"{pattern.get('pattern_key', '')}: {pattern_value[:500]}"
                    )

            if past_conversations:
                context_parts.append("\n=== Relevant durable memories ===")
                for conversation in past_conversations:
                    memory_type = conversation.get("metadata", {}).get(
                        "memory_type",
                        "task_outcome",
                    )
                    context_parts.append(
                        f"[memory_id={conversation.get('id', 'unknown')}] "
                        f"[{memory_type}] {conversation.get('content', '')[:500]}"
                    )

            if context_parts:
                context_text = "\n".join(context_parts)
                if len(context_text) > 3000:
                    context_text = context_text[:3000] + "..."
                memory_block = (
                    "<long_term_memory>\n"
                    "These are the user's Active durable memories. Current explicit "
                    "instructions always override them. Use only relevant items.\n"
                    f"{context_text}\n"
                    "</long_term_memory>"
                )
            else:
                memory_block = (
                    "<long_term_memory>\n"
                    "(no relevant Active memories or patterns passed the threshold)\n"
                    "</long_term_memory>"
                )

            memory_tokens = ctx_mgr.estimate_tokens([
                {"role": "user", "content": memory_block}
            ])
            result["retrieved_memory_context"] = memory_block
            result["token_count"] = initial_token_count + memory_tokens

            def trace_candidate(
                item: Dict[str, Any],
                collection: str,
            ) -> Dict[str, Any]:
                metadata = item.get("metadata", {})
                return {
                    "memory_id": item.get("id", ""),
                    "collection": collection,
                    "memory_type": (
                        item.get("pattern_type")
                        or metadata.get("memory_type")
                        or metadata.get("role")
                        or "unknown"
                    ),
                    "rank": item.get("rank"),
                    "semantic_rank": item.get("semantic_rank"),
                    "distance": item.get("distance"),
                    "lexical_score": item.get("lexical_score"),
                    "eligible": bool(item.get("eligible", True)),
                    "filter_reason": item.get("filter_reason", "eligible"),
                }

            candidates = [
                *(
                    trace_candidate(item, "patterns")
                    for item in pattern_candidates
                ),
                *(
                    trace_candidate(item, "conversations")
                    for item in conversation_candidates
                ),
            ]
            injected_ids = [
                *(item.get("id") for item in user_patterns if item.get("id")),
                *(
                    item.get("id")
                    for item in past_conversations
                    if item.get("id")
                ),
            ]
            _record_trace(
                state,
                event_type="memory",
                name="memory_retrieval",
                status="success",
                duration_ms=int((time.perf_counter() - retrieval_started) * 1000),
                data={
                    "query_summary": current_request[:500],
                    "strategy": (
                        "complete_listing"
                        if is_meta_memory_question
                        else next(
                            (
                                item.get("retrieval_strategy")
                                for item in [
                                    *pattern_candidates,
                                    *conversation_candidates,
                                ]
                                if item.get("retrieval_strategy")
                            ),
                            "semantic_top_k",
                        )
                    ),
                    "threshold": settings.MEMORY_RELEVANCE_MAX_DISTANCE,
                    "top_k_per_collection": 3,
                    "candidates": candidates,
                    "injected_ids": injected_ids,
                    "injected_count": len(injected_ids),
                    "injected_characters": len(memory_block),
                    "injected_tokens": memory_tokens,
                    "application_status": "not_attributed",
                },
            )
            logging.info(
                "[init_context] Memory retrieval: candidates=%s, injected=%s, tokens=%s",
                len(candidates),
                len(injected_ids),
                memory_tokens,
            )
        except Exception as exc:
            _record_trace(
                state,
                event_type="memory",
                name="memory_retrieval",
                status="error",
                duration_ms=int((time.perf_counter() - retrieval_started) * 1000),
                data={
                    "error": str(exc)[:1000],
                    "query_summary": current_request[:500],
                },
            )
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

    if len(compacted) != len(messages):
        _record_trace(
            state,
            event_type="context",
            name="microcompact",
            data={
                "messages_before": len(messages),
                "messages_after": len(compacted),
                "keep_last": settings.MICROCOMPACT_KEEP_LAST,
            },
        )

    return {"messages": compacted}


async def llm_call_node(state: AgentState) -> Dict[str, Any]:
    """LLM call node - Invoke LLM with tools bound.

    Handles both text responses and tool use requests.
    Intermediate system-level context uses role="user" with XML tags so
    MAIN_SYSTEM_PROMPT stays the sole SystemMessage. Long-term recall is held
    in an ephemeral state field rather than persisted in chat history.
    """
    if state.get("task_token_count", 0) >= settings.TASK_TOKEN_BUDGET:
        failure_reason = (
            f"Task token budget exhausted ({state.get('task_token_count', 0)} / "
            f"{settings.TASK_TOKEN_BUDGET})."
        )
        _record_trace(
            state,
            event_type="budget",
            name="token_budget_exhausted",
            status="error",
            data={
                "used": state.get("task_token_count", 0),
                "limit": settings.TASK_TOKEN_BUDGET,
            },
        )
        return {
            "messages": [{"role": "assistant", "content": failure_reason}],
            "pending_tool_calls": [],
            "should_end_after_save": True,
            "task_status": transition_task_status(state.get("task_status"), TaskStatus.FAILED),
            "execution_phase": ExecutionPhase.SUMMARIZING.value,
            "failure_reason": failure_reason,
        }

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
        available_skills=_build_available_skills(state),
        execution_mode_info=_build_execution_mode_info(state),
    )))
    memory_context = state.get("retrieved_memory_context", "")
    if memory_context:
        lc_messages.insert(1, HumanMessage(content=memory_context))

    # Log: entering LLM call
    msg_count = len(lc_messages)
    total_chars = sum(len(str(m.content)) if hasattr(m, "content") else 0 for m in lc_messages)
    logging.info(
        "[llm_call] %s messages (~%s chars, ~%s tokens) → invoking LLM...",
        msg_count,
        total_chars,
        state.get("token_count", 0),
    )

    # LLM call with retry on transient failures
    model_started = time.perf_counter()
    for attempt in range(MAX_LLM_RETRIES):
        try:
            response = await get_llm_with_tools(
                state.get("permissions", []),
                state.get("execution_mode", "single_agent"),
            ).ainvoke(lc_messages)
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
                _record_trace(
                    state,
                    event_type="model",
                    name="llm_call",
                    status="error",
                    duration_ms=int((time.perf_counter() - model_started) * 1000),
                    data={
                        "message_count": msg_count,
                        "input_chars": total_chars,
                        "retry_count": attempt,
                        "error": str(e)[:1000],
                    },
                )
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
    task_token_count = state.get("task_token_count", 0)
    usage = getattr(response, "usage_metadata", {})
    if usage:
        usage_tokens = usage.get("total_tokens", 0)
        token_count += usage_tokens
        task_token_count += usage_tokens
    else:
        # Estimate if not provided
        ctx_mgr = get_context_manager()
        estimated_tokens = ctx_mgr.estimate_tokens([response])
        token_count += estimated_tokens
        task_token_count += estimated_tokens

    # Convert response back to dict format
    response_dict = _convert_from_langchain_messages([response])[0]

    round_count = state.get("round_count", 0) + 1
    logging.info(f"[llm_call] round {round_count}/{settings.MAX_AGENT_ROUNDS}")

    # Determine if this should end after save_memory
    # When there are no tool calls, the text response should end the invocation
    should_end_after_save = not tool_calls  # True if no tool calls

    _record_trace(
        state,
        event_type="model",
        name="llm_call",
        status="success",
        duration_ms=int((time.perf_counter() - model_started) * 1000),
        data={
            "message_count": msg_count,
            "input_chars": total_chars,
            "input_summary": _last_user_request(messages)[-500:],
            "output_summary": _extract_text(getattr(response, "content", ""))[:1000],
            "tool_calls": [call.get("name") for call in tool_calls],
            "input_tokens": usage.get("input_tokens", 0) if usage else 0,
            "output_tokens": usage.get("output_tokens", 0) if usage else 0,
            "total_tokens": usage_tokens if usage else estimated_tokens,
            "retry_count": attempt,
        },
    )

    return {
        "messages": [response_dict],
        "pending_tool_calls": tool_calls,
        "token_count": token_count,
        "task_token_count": task_token_count,
        "round_count": round_count,
        "should_end_after_save": should_end_after_save,  # Signal to route_after_tool
        "execution_phase": (
            ExecutionPhase.EXECUTING.value if tool_calls else ExecutionPhase.SUMMARIZING.value
        ),
    }


# Tools that are safe to retry (read-only, no side effects)
IDEMPOTENT_TOOLS = {
    name for name, contract in TOOL_CONTRACTS.items() if contract.idempotent
}

# Error patterns that indicate transient failures worth retrying
RETRYABLE_ERROR_PATTERNS = ("timeout", "connection", "rate limit", "try again")

MAX_TOOL_RETRIES = max(contract.max_retries for contract in TOOL_CONTRACTS.values()) + 1


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
    from enterprise_agent.core.agent.tools.workspace import set_current_session_id, set_current_user_id

    # Set context variables for tools to access
    session_id = state.get("session_id", "")
    user_id = state.get("user_id")
    set_current_session_id(session_id)
    if user_id:
        set_current_user_id(user_id)

    results = {}
    all_tool_map = {tool.name: tool for tool in ALL_TOOLS}
    allowed_tools = get_tools_for_permissions(
        state.get("permissions", []),
        enable_multi_agent=(
            state.get("execution_mode") == "multi_agent"
            and settings.ENABLE_MULTI_AGENT
        ),
    )
    tool_map = {tool.name: tool for tool in allowed_tools}
    compress_requested = False
    used_todo = False
    updated_todos = None  # Track todos for AgentState persistence
    execution_records = list(state.get("tool_execution_records", []))
    delegation_succeeded = any(
        record.get("tool_name") == "delegate_task" and record.get("ok")
        for record in execution_records
    )
    changed_files = set(state.get("changed_files", []))
    validation_results = list(state.get("validation_results", []))
    created_task_ids = set(state.get("created_task_ids", []))
    tool_call_count = state.get("tool_call_count", 0)
    failure_reason = state.get("failure_reason")
    task_status = state.get("task_status", TaskStatus.RUNNING.value)

    pending = state.get("pending_tool_calls", [])
    tool_call_stats = state.get("tool_call_stats", {}).copy()  # mutable state: copy before modifying
    logging.info(
        "[tool_exec] Session %s: executing %s tool(s): %s",
        session_id,
        len(pending),
        [tc.get("name") for tc in pending],
    )

    for tool_call in pending:
        tool_name = tool_call.get("name")
        tool_input = tool_call.get("args", {})
        tool_id = tool_call.get("id", tool_name)
        tool_call_count += 1

        # Auto-increment tool call stats (framework counts, no LLM hallucination)
        tool_call_stats[tool_name] = tool_call_stats.get(tool_name, 0) + 1

        if tool_call_count > settings.MAX_TOOL_CALLS_PER_TASK:
            failure_reason = (
                f"Tool-call budget exhausted ({settings.MAX_TOOL_CALLS_PER_TASK} calls)."
            )
            task_status = transition_task_status(task_status, TaskStatus.FAILED)
            _record_trace(
                state,
                event_type="budget",
                name="tool_call_budget_exhausted",
                status="error",
                data={"used": tool_call_count, "limit": settings.MAX_TOOL_CALLS_PER_TASK},
            )
            record = normalize_tool_result(
                tool_name=tool_name or "unknown",
                tool_call_id=tool_id or "unknown",
                raw_result=f"Error: {failure_reason}",
                duration_ms=0,
                attempt_count=1,
            )
            results[tool_id] = record.output
            execution_records.append(record.to_dict())
            _record_tool_trace(state, record, tool_input)
            continue

        if tool_name not in all_tool_map:
            record = ToolExecutionRecord(
                tool_name=tool_name or "unknown",
                tool_call_id=tool_id or "unknown",
                status=ToolResultStatus.ERROR,
                ok=False,
                output=f"Error: Unknown tool: {tool_name}",
                duration_ms=0,
                attempt_count=1,
                error_code="unknown_tool",
            )
            results[tool_id] = record.output
            execution_records.append(record.to_dict())
            _record_tool_trace(state, record, tool_input)
            logging.warning(f"[tool_exec] unknown tool: {tool_name}")
            continue

        if tool_name not in tool_map:
            record = ToolExecutionRecord(
                tool_name=tool_name,
                tool_call_id=tool_id,
                status=ToolResultStatus.BLOCKED,
                ok=False,
                output=f"Error: Permission denied for tool '{tool_name}'",
                duration_ms=0,
                attempt_count=1,
                error_code="permission_denied",
            )
            results[tool_id] = record.output
            execution_records.append(record.to_dict())
            _record_tool_trace(state, record, tool_input)
            logging.warning("[tool_exec] permission denied for tool: %s", tool_name)
            continue

        tool = tool_map[tool_name]
        contract = get_tool_contract(tool_name)
        if (
            state.get("execution_mode") == "multi_agent"
            and not delegation_succeeded
            and tool_name != "delegate_task"
            and (
                tool_name == "task_create"
                or contract.side_effect in {
                    "filesystem_write",
                    "process",
                    "background_process",
                    "subagent",
                    "agent_message",
                    "agent_control",
                }
            )
        ):
            record = ToolExecutionRecord(
                tool_name=tool_name,
                tool_call_id=tool_id,
                status=ToolResultStatus.BLOCKED,
                ok=False,
                output=(
                    "Error: Multi-Agent mode requires at least one successful real "
                    "delegate_task call before mutating the workspace or simulating "
                    "coordination. Delegate a specialist first."
                ),
                duration_ms=0,
                attempt_count=1,
                error_code="delegation_required",
            )
            results[tool_id] = record.output
            execution_records.append(record.to_dict())
            _record_tool_trace(state, record, tool_input)
            continue

        started = time.perf_counter()
        final_record = None
        if tool_name == "search_memory":
            # ``asyncio.wait_for`` executes the tool in a child task with a
            # copied Context. Seed a shared mutable audit slot in the parent so
            # retrieval evidence can be consumed after the tool returns.
            from enterprise_agent.core.agent.tools.memory import (
                prepare_memory_search_audit,
            )

            prepare_memory_search_audit()
        max_attempts = contract.max_retries + 1
        for attempt in range(max_attempts):
            try:
                # Invoke tool (tools may be sync or async)
                if hasattr(tool, "ainvoke"):
                    result = await asyncio.wait_for(
                        tool.ainvoke(tool_input),
                        timeout=contract.timeout_seconds,
                    )
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

                final_record = normalize_tool_result(
                    tool_name=tool_name,
                    tool_call_id=tool_id,
                    raw_result=result_str,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    attempt_count=attempt + 1,
                )
                if (
                    not final_record.ok
                    and attempt < max_attempts - 1
                    and contract.idempotent
                    and any(pattern in result_str.lower() for pattern in RETRYABLE_ERROR_PATTERNS)
                ):
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue

                results[tool_id] = result_str
                result_preview = result_str[:120].replace("\n", " ")
                marker = "✓" if final_record.ok else "✗"
                logging.info(
                    f"[tool_exec] {marker} {tool_name} ({len(result_str)} chars): {result_preview}..."
                )
                break
            except asyncio.TimeoutError:
                final_record = ToolExecutionRecord(
                    tool_name=tool_name,
                    tool_call_id=tool_id,
                    status=ToolResultStatus.TIMEOUT,
                    ok=False,
                    output=f"Error: Tool timed out after {contract.timeout_seconds} seconds",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    attempt_count=attempt + 1,
                    error_code="tool_timeout",
                )
                results[tool_id] = final_record.output
                break
            except Exception as e:
                if attempt < max_attempts - 1 and _should_retry_tool(tool_name, e):
                    delay = 1.0 * (attempt + 1)
                    logging.warning(
                        f"Retrying idempotent tool '{tool_name}' after error: {e} "
                        f"(attempt {attempt+1}/{max_attempts})"
                    )
                    await asyncio.sleep(delay)
                else:
                    final_record = normalize_tool_result(
                        tool_name=tool_name,
                        tool_call_id=tool_id,
                        raw_result=f"Error executing {tool_name}: {e}",
                        duration_ms=int((time.perf_counter() - started) * 1000),
                        attempt_count=attempt + 1,
                    )
                    results[tool_id] = final_record.output
                    logging.warning(f"[tool_exec] ✗ {tool_name} FAILED: {e}")
                    break

        if final_record is None:
            final_record = normalize_tool_result(
                tool_name=tool_name,
                tool_call_id=tool_id,
                raw_result=results.get(tool_id, "Error: tool produced no result"),
                duration_ms=int((time.perf_counter() - started) * 1000),
                attempt_count=max_attempts,
            )
        execution_records.append(final_record.to_dict())
        _record_tool_trace(state, final_record, tool_input)
        if tool_name == "search_memory":
            # ``search_memory`` is a second retrieval entry point in addition
            # to init-context injection. Record it with the same event shape so
            # the Memory Ledger and Trace metrics cannot silently disagree.
            from enterprise_agent.core.agent.tools.memory import (
                consume_memory_search_audit,
            )

            memory_audit = consume_memory_search_audit()
            if memory_audit:
                _record_trace(
                    state,
                    event_type="memory",
                    name="memory_retrieval",
                    status="error" if memory_audit.get("error") else "success",
                    duration_ms=final_record.duration_ms,
                    data=memory_audit,
                )

        if final_record.ok and tool_name == "delegate_task":
            delegation_succeeded = True

        if final_record.ok and tool_name == "task_create":
            try:
                created_task_ids.add(int(json.loads(final_record.output)["id"]))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                logging.warning("Could not extract created task ID from task_create output")

        if final_record.ok and tool_name in {"write_file", "edit_file"}:
            changed_path = str(tool_input.get("path", ""))
            if changed_path:
                changed_files.add(changed_path)
        if tool_name == "bash" and _is_validation_command(str(tool_input.get("command", ""))):
            validation_results.append({
                "command": str(tool_input.get("command", "")),
                "ok": final_record.ok,
                "status": final_record.status.value,
                "exit_code": final_record.exit_code,
                "duration_ms": final_record.duration_ms,
            })

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
        "tool_execution_records": execution_records,
        "tool_call_count": tool_call_count,
        "created_task_ids": sorted(created_task_ids),
        "changed_files": sorted(changed_files),
        "validation_results": validation_results,
        "should_compress": compress_requested,  # Trigger compression if requested
        "used_todo_last_round": used_todo,
        "has_open_todos": has_open_todos,
        "should_end_after_save": task_status in {
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
        },
        "task_status": task_status,
        "failure_reason": failure_reason,
        "execution_phase": ExecutionPhase.EXECUTING.value,
    }

    # Persist todos to AgentState (for Redis checkpoint)
    if updated_todos:
        result_dict["todos"] = updated_todos

    return result_dict


async def _background_flush(
    acc,
    accumulator_state,
    session_id,
    user_id,
    messages,
    task_context,
):
    """Background task: flush accumulated content to Chroma.

    Runs as a fire-and-forget asyncio task, detached from the LangGraph
    streaming context. This prevents internal LLM evaluation tokens
    (task summaries, importance JSON) from leaking through
    stream_mode=["messages"] to the user-facing SSE stream.

    All parameters are copies owned exclusively by this task — the
    graph node continues immediately and may mutate its originals.
    """
    try:
        flush_result = await acc.flush(
            accumulator_state,
            session_id,
            user_id,
            messages,
            task_context=task_context,
        )
        logging.info(
            f"[save_memory] Background flush complete: stored={flush_result['stored']}, "
            f"importance={flush_result['importance']:.2f}, "
            f"reason={flush_result.get('reason', 'unknown')}"
        )
    except Exception as e:
        logging.warning(f"[save_memory] Background flush failed (non-fatal): {e}", exc_info=True)


_memory_flush_tasks: set[asyncio.Task] = set()


def _schedule_memory_flush(
    acc,
    accumulator_state,
    session_id,
    user_id,
    messages,
    task_context=None,
) -> None:
    """Schedule and track a background memory flush task."""
    task = asyncio.create_task(
        _background_flush(
            acc,
            accumulator_state,
            session_id,
            user_id,
            messages,
            task_context or {},
        )
    )
    _memory_flush_tasks.add(task)
    task.add_done_callback(_memory_flush_tasks.discard)


async def _drain_memory_flush_tasks(timeout: float = 5.0) -> None:
    """Wait briefly for pending memory flush tasks during graceful shutdown."""
    if not _memory_flush_tasks:
        return

    pending = list(_memory_flush_tasks)
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        for task in pending:
            if not task.done():
                task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        _memory_flush_tasks.difference_update(task for task in pending if task.done())


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

    acc = MemoryAccumulator()
    accumulator_state = state.get("memory_accumulator", {})

    # 1. Accumulate content from current round
    accumulator_state = acc.accumulate_round(state, messages, accumulator_state)

    return {
        "rounds_without_todo": rounds_without_todo,
        "messages": additional_messages,
        "memory_accumulator": accumulator_state,
    }


async def persist_memory_node(state: AgentState) -> Dict[str, Any]:
    """Evaluate and persist memory only after the task has a terminal status.

    The previous flow flushed from ``save_memory_node`` before verification and
    finalization, so failed or cancelled tasks could be recorded as durable
    knowledge.  This terminal node detaches the potentially model-backed memory
    work from SSE while passing authoritative task evidence to the admission
    policy.
    """
    from enterprise_agent.memory.accumulator import MemoryAccumulator

    acc = MemoryAccumulator()
    accumulator_state = state.get("memory_accumulator", {})
    if (
        settings.ENABLE_LONG_TERM_MEMORY
        and accumulator_state.get("user_request")
        and state.get("user_id")
    ):
        import copy

        task_context = {
            "task_status": state.get("task_status", ""),
            "failure_reason": state.get("failure_reason"),
            "tool_execution_records": copy.deepcopy(
                state.get("tool_execution_records", [])
            ),
            "changed_files": list(state.get("changed_files", [])),
            "validation_results": copy.deepcopy(
                state.get("validation_results", [])
            ),
            "trace_id": state.get("trace_id", ""),
            "execution_mode": state.get("execution_mode", "single_agent"),
        }
        _schedule_memory_flush(
            acc,
            copy.deepcopy(accumulator_state),
            state.get("session_id", "unknown"),
            state.get("user_id"),
            list(state.get("messages", [])),
            task_context,
        )

    return {"memory_accumulator": acc._new_accumulator()}


async def compress_context_node(state: AgentState) -> Dict[str, Any]:
    """Compress context node - Full summarization when threshold exceeded.

    This implements the auto-compact mechanism:
    1. Check token threshold
    2. Save transcript to file
    3. Generate summary via LLM
    4. Replace messages with summary
    5. Keep the summary in working/checkpoint memory only
    """
    ctx_mgr = get_context_manager()
    token_count = state.get("token_count", 0)

    # Check if compression needed
    if token_count > settings.TOKEN_THRESHOLD:
        messages = state.get("messages", [])
        session_id = state.get("session_id", "unknown")
        # Perform full compression
        compression_result = await ctx_mgr.auto_compact(messages, session_id)

        summary = compression_result.get("context_summary")

        # Append explicit continuation instruction so the LLM doesn't just
        # echo the summary and stop — it receives a clear prompt to act.
        compressed_msgs = compression_result["compressed_messages"]
        compressed_msgs.append({
            "role": "user",
            "content": (
                "<system-reminder>Context has been compressed. Continue the task immediately. "
                "Take the next concrete action using a tool — do NOT summarize or repeat "
                "what was in the compressed context.</system-reminder>"
            ),
        })

        _record_trace(
            state,
            event_type="context",
            name="auto_compact",
            data={
                "token_count_before": token_count,
                "token_count_after": compression_result["token_count_reset"],
                "message_count_before": len(messages),
                "message_count_after": len(compressed_msgs),
                "transcript_path": compression_result["transcript_path"],
            },
        )

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
    # Always compress when manually triggered
    compression_result = await ctx_mgr.manual_compress(messages, session_id)

    summary = compression_result.get("context_summary")

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
    if state.get("task_status") == TaskStatus.FAILED.value:
        return "save_memory"

    # Check for tool calls first
    if state.get("pending_tool_calls"):
        return "tool_call"

    # Safety valve: stop if agent has been looping too long. Tool calls are
    # handled first so every model tool_use still receives a tool_result.
    if state.get("round_count", 0) >= settings.MAX_AGENT_ROUNDS:
        logging.warning(f"[route_after_llm] max rounds ({settings.MAX_AGENT_ROUNDS}) reached, ending")
        return "save_memory"

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
    if state.get("task_status") in (TaskStatus.FAILED.value, TaskStatus.CANCELLED.value):
        return "end"

    # Check if this was a text response that should end
    if state.get("should_end_after_save"):
        if (
            _needs_verification(state)
            and state.get("verification_attempts", 0) < settings.VERIFICATION_MAX_ATTEMPTS
        ):
            return "verify"
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
    from enterprise_agent.core.agent.tools.workspace import set_current_session_id, set_current_user_id

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
        # Unknown tools pass through to the executor, which rejects and traces
        # them without ever executing user-controlled input.
        if tool_name in TOOL_CONTRACTS and tool_requires_confirmation(
            tool_name, tc.get("args", {})
        ):
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
            "risk": resolve_tool_risk(tool_name, tool_args).value,
        })

    # Call interrupt() - will pause on first call, return resume data after resume
    # IMPORTANT: After resume, this node re-executes from start, and interrupt()
    # returns the resume data directly (no second pause)
    user_response = interrupt({
        "type": "tool_confirmation",
        "tools": tool_descriptions,
        "message": f"Confirm execution of {len(sensitive_tools)} sensitive tool(s)?",
        "deadline": state.get("confirmation_deadline"),
    })

    # ========== Only executed AFTER resume (user responded) ==========
    logging.info(
        "[tool_confirm] User response received: approved=%s, approved_ids=%s",
        user_response.get("approved"),
        user_response.get("approved_ids", []),
    )

    approved = user_response.get("approved", False)
    approved_ids = user_response.get("approved_ids", [])
    response_reason = user_response.get("reason")
    decision_name = (
        response_reason
        if response_reason in {"confirmation_timeout", "task_cancelled"}
        else ("confirmation_approved" if approved else "confirmation_rejected")
    )
    _record_trace(
        state,
        event_type="confirmation",
        name=decision_name,
        status="success" if approved and not response_reason else "rejected",
        data={
            "approved": approved,
            "approved_ids": approved_ids,
            "reason": response_reason,
        },
    )

    if response_reason in {"confirmation_timeout", "task_cancelled"}:
        is_cancelled = response_reason == "task_cancelled"
        target_status = TaskStatus.CANCELLED if is_cancelled else TaskStatus.FAILED
        failure_reason = (
            "Cancelled by user while waiting for tool confirmation."
            if is_cancelled
            else "Tool confirmation expired before a decision was received."
        )
        tool_result_messages = [
            {
                "role": "tool",
                "content": f"Tool execution not performed ({response_reason}): {tc.get('name', '')}",
                "tool_call_id": tc.get("id", ""),
            }
            for tc in pending
        ]
        return {
            "pending_tool_calls": [],
            "messages": tool_result_messages,
            "task_status": transition_task_status(state.get("task_status"), target_status),
            "failure_reason": failure_reason,
            "should_end_after_save": True,
            "confirmation_deadline": None,
        }

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
            logging.info(
                "[tool_confirm] Partial approval: %s/%s tools proceeding "
                "(non-sensitive: %s, approved sensitive: %s)",
                len(final_pending),
                len(pending),
                len(non_sensitive_tools),
                len(approved_ids),
            )
        else:
            # Full approval (no specific IDs): add all sensitive tools
            final_pending.extend(sensitive_tools)
            logging.info(
                "[tool_confirm] Full approval: %s tools proceeding "
                "(non-sensitive: %s, all sensitive approved)",
                len(final_pending),
                len(non_sensitive_tools),
            )

        return {
            "pending_tool_calls": final_pending,
            "task_status": transition_task_status(state.get("task_status"), TaskStatus.RUNNING),
            "confirmation_deadline": None,
        }
    else:
        # Rejected: clear all pending tools and inform LLM
        # API requirement: every tool_use must have a corresponding tool_result
        logging.info(
            "[tool_confirm] User rejected %s sensitive tools, clearing all %s pending",
            len(sensitive_tools),
            len(pending),
        )

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
                + "\n".join(
                    f"- {tc['name']}: {get_sensitive_tool_info(tc['name'], tc.get('args', {}))}"
                    for tc in sensitive_tools
                )
                + "\nPlease modify your approach or ask user for clarification.\n"
                "</tool_rejected>"
            )
        })

        return {
            "pending_tool_calls": [],
            "messages": tool_result_messages,
            "task_status": transition_task_status(state.get("task_status"), TaskStatus.RUNNING),
            "confirmation_deadline": None,
        }
