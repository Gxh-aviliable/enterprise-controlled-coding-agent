"""Memory accumulator for task-level storage.

Accumulates meaningful content across rounds and flushes to Chroma
only at task boundaries (conversation end, all todos completed, or
safety valve triggered). This replaces the per-round fragment storage
that caused severe information loss in multi-step tasks.

Stored documents use role="task_summary" with structured format:
    [User Request]: original request
    [Actions]: concise tool action log
    [Result]: key assistant response or LLM-generated summary
    [Key Findings]: extracted decisions and discoveries
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from enterprise_agent.config.settings import settings

logger = logging.getLogger(__name__)

# Patterns that indicate system-injected (non-substantive) messages
SYSTEM_MESSAGE_PATTERNS = [
    "<reminder>",
    "<tool_stats>",
    "Update your todos",
    "Framework-counted tool usage",
    "<background-results>",
    "<inbox>",
]


def _is_substantive_user_message(content: str) -> bool:
    """Check if a user message is a real user request (not system-injected)."""
    if not content or len(content.strip()) < 5:
        return False
    content_lower = content.lower()
    for pattern in SYSTEM_MESSAGE_PATTERNS:
        if pattern.lower() in content_lower:
            return False
    return True


def _extract_text_from_content(content: Any) -> str:
    """Extract plain text from message content (may be str or content blocks)."""
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


def _summarize_tool_calls(pending_tool_calls: List[Dict]) -> str:
    """Generate a one-line summary of tool calls made this round."""
    if not pending_tool_calls:
        return ""
    parts = []
    for tc in pending_tool_calls:
        name = tc.get("name", "")
        args = tc.get("args", {})
        # Show key arg for context (truncated)
        key_arg = ""
        if name == "read_file":
            key_arg = args.get("path", "")[:60]
        elif name == "write_file":
            key_arg = args.get("path", "")[:60]
        elif name == "edit_file":
            key_arg = args.get("path", "")[:60]
        elif name == "bash":
            key_arg = args.get("command", "")[:80]
        elif name == "todo_update":
            todos = args.get("todos", [])
            statuses = [t.get("status", "?") for t in todos]
            key_arg = f"{len(todos)} items: {','.join(statuses)}"
        elif name == "task_create":
            key_arg = args.get("subject", "")[:40]
        elif name == "spawn_teammate":
            key_arg = args.get("role", "")[:30]
        if key_arg:
            parts.append(f"{name}: {key_arg}")
        else:
            parts.append(name)
    return "; ".join(parts)


class MemoryAccumulator:
    """Accumulates conversation content across rounds and flushes at task boundaries.

    Replaces the per-round "last user + last assistant" storage approach.
    Instead, meaningful content is accumulated and stored as a single
    structured task_summary document at task completion boundaries.
    """

    def _new_accumulator(self) -> Dict[str, Any]:
        """Create a fresh accumulator dict."""
        return {
            "user_request": "",
            "assistant_responses": [],
            "tool_actions": [],
            "round_count": 0,
            "start_timestamp": datetime.now(timezone.utc).isoformat(),
            "context_summary_pre": "",  # Filled if compression happened mid-task
        }

    def accumulate_round(
        self,
        state: Dict[str, Any],
        messages: List[Any],
        accumulator: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Add content from the current round to the accumulator.

        Args:
            state: Current AgentState
            messages: All messages in the conversation
            accumulator: Current accumulator state (may be empty dict for new session)

        Returns:
            Updated accumulator dict
        """
        # Initialize if empty
        if not accumulator or not accumulator.get("start_timestamp"):
            accumulator = self._new_accumulator()

        accumulator["round_count"] += 1

        # --- Extract user request (first substantive user message) ---
        if not accumulator.get("user_request"):
            # Find the first substantive user message in the conversation
            for msg in messages:
                content = ""
                if isinstance(msg, dict):
                    if msg.get("role") == "user":
                        content = msg.get("content", "")
                elif hasattr(msg, "type") and getattr(msg, "type", "") == "human":
                    content = _extract_text_from_content(msg.content if hasattr(msg, "content") else "")

                if content and _is_substantive_user_message(content):
                    # Truncate very long requests for accumulator storage
                    accumulator["user_request"] = content[:500] if len(content) > 500 else content
                    break

        # --- Extract assistant text response from current round ---
        # Look for the last assistant message with substantive text content
        # (not just tool_calls — we want the final text response)
        for msg in reversed(messages):
            content = ""
            is_assistant = False

            if isinstance(msg, dict):
                is_assistant = msg.get("role") == "assistant"
                content = msg.get("content", "")
                # Skip messages that are just tool_calls with no text content
                if msg.get("tool_calls") and not content:
                    continue
            elif hasattr(msg, "type") and getattr(msg, "type", "") == "ai":
                is_assistant = True
                raw = msg.content if hasattr(msg, "content") else ""
                content = _extract_text_from_content(raw)
                # Skip tool-call-only rounds
                if hasattr(msg, "tool_calls") and msg.tool_calls and not content.strip():
                    continue

            if is_assistant and content and len(content.strip()) > 10:
                # Truncate for accumulator
                truncated = content[:500] if len(content) > 500 else content
                # Avoid duplicate responses
                if truncated not in accumulator["assistant_responses"]:
                    accumulator["assistant_responses"].append(truncated)
                break

        # --- Log tool actions for this round ---
        pending = state.get("pending_tool_calls", [])
        tool_summary = _summarize_tool_calls(pending)
        if tool_summary:
            accumulator["tool_actions"].append(tool_summary)

        return accumulator

    def should_flush(self, state: Dict[str, Any]) -> bool:
        """Check if we're at a task boundary and should flush to Chroma.

        Boundary conditions (any one triggers flush):
        1. should_end_after_save=True — conversation is ending (LLM gave text response)
        2. round_count >= MAX_ROUNDS — safety valve (prevent unbounded accumulation)
        3. All todos completed (has_open_todos=False AND used_todo_last_round=True)
           — means a tracked task just finished

        Args:
            state: Current AgentState

        Returns:
            True if we should flush accumulated content to Chroma
        """
        accumulator = state.get("memory_accumulator", {})

        # No content accumulated — nothing to flush
        if not accumulator or not accumulator.get("user_request"):
            return False

        # Boundary 1: conversation is ending
        if state.get("should_end_after_save"):
            return True

        # Boundary 2: safety valve — too many rounds accumulated
        round_count = accumulator.get("round_count", 0)
        if round_count >= settings.MEMORY_ACCUMULATOR_MAX_ROUNDS:
            logger.info(f"[accumulator] Safety valve: {round_count} rounds accumulated, forcing flush")
            return True

        # Boundary 3: tracked task completed (todos closed)
        used_todo = state.get("used_todo_last_round", False)
        has_open_todos = state.get("has_open_todos", False)
        if used_todo and not has_open_todos:
            return True

        return False

    async def flush(
        self,
        accumulator: Dict[str, Any],
        session_id: str,
        user_id: int,
        messages: List[Any] = None,
    ) -> Dict[str, Any]:
        """Flush accumulated content to Chroma as a task_summary document.

        Steps:
        1. For simple conversations (round_count <= 2): skip LLM summary, use raw content
        2. For multi-round tasks: generate LLM summary
        3. Evaluate importance on the combined content
        4. If importance >= threshold: store to Chroma
        5. If importance >= pattern threshold: extract user patterns
        6. Reset accumulator for next task

        Args:
            accumulator: The accumulated content dict
            session_id: Session identifier
            user_id: User identifier
            messages: Full conversation messages (for pattern extraction context)

        Returns:
            Dict with: accumulator_reset (empty dict), stored (bool), importance (float)
        """
        user_request = accumulator.get("user_request", "")
        assistant_responses = accumulator.get("assistant_responses", [])
        tool_actions = accumulator.get("tool_actions", [])
        round_count = accumulator.get("round_count", 0)
        context_summary_pre = accumulator.get("context_summary_pre", "")

        if not user_request:
            logger.debug("[accumulator] No user_request to flush, skipping")
            return {"accumulator_reset": self._new_accumulator(), "stored": False, "importance": 0.0}

        # --- Step 1: Format content for storage ---
        # Simple path: for short conversations (1-2 rounds), skip LLM summary generation
        if round_count <= 2 and len(tool_actions) <= 2:
            content = self._format_simple_content(user_request, assistant_responses, context_summary_pre)
            importance = await self._evaluate_importance(content, user_id, messages)
        else:
            # Multi-round path: generate LLM summary
            content = await self._generate_task_summary(
                user_request, assistant_responses, tool_actions, context_summary_pre
            )
            importance = await self._evaluate_importance(content, user_id, messages)

        # --- Step 2: Store to Chroma if important enough ---
        stored = False
        if importance >= settings.IMPORTANCE_THRESHOLD_STORE and user_id:
            try:
                from enterprise_agent.memory.long_term import get_long_term_memory
                memory = get_long_term_memory(user_id)

                # Dedup check before storing
                dedup_skip = await self._check_dedup(memory, user_request)
                if dedup_skip:
                    logger.debug("[accumulator] Skipped duplicate task summary")
                    return {"accumulator_reset": self._new_accumulator(), "stored": False, "importance": importance}

                doc_id = await memory.store_conversation(
                    session_id=session_id,
                    role="task_summary",
                    content=content,
                    metadata={
                        "importance": importance,
                        "access_count": 0,
                        "rounds": round_count,
                        "has_tool_actions": len(tool_actions) > 0,
                    },
                )
                stored = True
                logger.info(f"[accumulator] Stored task_summary (importance={importance:.2f}, {round_count} rounds, doc_id={doc_id})")

            except Exception as e:
                logger.warning(f"[accumulator] Chroma storage failed (non-fatal): {e}", exc_info=True)

        # --- Step 3: Extract patterns if high importance ---
        if stored and importance >= settings.IMPORTANCE_THRESHOLD_PATTERN and assistant_responses:
            try:
                from enterprise_agent.memory.pattern_extractor import get_pattern_extractor
                extractor = get_pattern_extractor()

                # Use the original user request + key assistant response
                key_response = assistant_responses[-1] if assistant_responses else ""
                # Convert messages to dicts (they may be LangChain objects)
                context_dicts = []
                for m in (messages or []):
                    if hasattr(m, 'type'):
                        context_dicts.append({'role': m.type, 'content': str(m.content)[:200]})
                    elif isinstance(m, dict):
                        context_dicts.append(m)
                patterns = await extractor.extract_patterns_from_conversation(
                    user_msg=user_request,
                    assistant_msg=key_response,
                    context=context_dicts,
                )

                if patterns:
                    from enterprise_agent.memory.long_term import get_long_term_memory
                    memory = get_long_term_memory(user_id)
                    for p in patterns:
                        await memory.store_pattern(
                            pattern_type=p.get("type", "preference"),
                            pattern_key=p.get("key", "unknown"),
                            pattern_value=p.get("value", {}),
                            confidence=p.get("confidence", 0.7),
                        )
                    logger.info(f"[accumulator] Extracted {len(patterns)} patterns from task_summary")

            except Exception as e:
                logger.warning(f"[accumulator] Pattern extraction failed (non-fatal): {e}")

        return {
            "accumulator_reset": self._new_accumulator(),
            "stored": stored,
            "importance": importance,
        }

    def _format_simple_content(
        self,
        user_request: str,
        assistant_responses: List[str],
        context_summary_pre: str,
    ) -> str:
        """Format content for simple (1-2 round) conversations without LLM summary."""
        parts = []
        if context_summary_pre:
            parts.append(f"[Prior Context]: {context_summary_pre}")
        parts.append(f"[User Request]: {user_request}")
        if assistant_responses:
            parts.append(f"[Result]: {assistant_responses[-1]}")
        return "\n".join(parts)

    async def _generate_task_summary(
        self,
        user_request: str,
        assistant_responses: List[str],
        tool_actions: List[str],
        context_summary_pre: str,
    ) -> str:
        """Generate a structured task summary using LLM.

        Reuses the summarization approach from ContextManager.auto_compact
        but formats the output as a structured document for Chroma storage.
        """
        from enterprise_agent.core.agent.llm_factory import get_llm
        from langchain_core.messages import HumanMessage

        # Build the input for LLM summarization
        actions_text = "\n".join(f"  - {a}" for a in tool_actions) if tool_actions else "None"
        responses_text = "\n".join(f"  - {r[:300]}" for r in assistant_responses[-3:]) if assistant_responses else "None"

        prompt = f"""Generate a structured task summary for memory storage. This summary will be stored in a vector database for future semantic retrieval.

{f"[Prior compressed context]: {context_summary_pre}" if context_summary_pre else ""}

[User Request]: {user_request}

[Tool Actions Taken]:
{actions_text}

[Assistant Key Responses]:
{responses_text}

Produce a concise structured summary with these sections:
1. [User Request]: What the user originally asked for
2. [Actions]: Key tools used and what they did (be specific about files/commands)
3. [Result]: The final outcome or answer
4. [Key Findings]: Important decisions, discoveries, or lessons learned

Keep total length under 500 words. Be specific (mention actual file paths, commands, decisions)."""

        try:
            llm = get_llm()
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            summary = _extract_text_from_content(response.content)
            logger.info(f"[accumulator] Generated task summary ({len(summary)} chars)")
            return summary
        except Exception as e:
            logger.warning(f"[accumulator] LLM summary generation failed, using raw content: {e}")
            # Fallback: use structured raw content format
            return self._format_simple_content(user_request, assistant_responses, context_summary_pre)

    async def _evaluate_importance(
        self,
        content: str,
        user_id: int,
        messages: List[Any] = None,
    ) -> float:
        """Evaluate importance of the accumulated task content.

        Uses the existing HybridImportanceEvaluator but with the full
        task content (not just a single message fragment).
        """
        from enterprise_agent.memory.importance import get_importance_evaluator

        evaluator = get_importance_evaluator()

        # Format context for evaluator
        context = messages[-5:] if messages and len(messages) >= 5 else (messages or [])

        importance = await evaluator.evaluate(
            content=content,
            role="task_summary",  # New role type
            context=context,
            enable_llm=settings.ENABLE_LLM_IMPORTANCE_EVAL,
        )

        logger.debug(f"[accumulator] Importance evaluation: {importance:.2f}")
        return importance

    async def _check_dedup(
        self,
        memory: Any,
        user_request: str,
    ) -> bool:
        """Check if a similar task summary already exists in Chroma.

        Uses Chroma's semantic search distance (vector embedding) for dedup,
        which handles long/short text mismatch correctly — unlike Jaccard
        word-set similarity which breaks when comparing a short user_request
        against a long structured task_summary.

        Args:
            memory: ChromaLongTermMemory instance
            user_request: The user request to check for duplicates

        Returns:
            True if duplicate found (should skip storage)
        """
        try:
            recent = await memory.search_conversations(
                query=user_request,  # Use full text for semantic search
                n_results=3,
            )
            for r in recent:
                distance = r.get("distance")
                # Chroma vector distance < 0.3 = semantically near-duplicate
                # (L2 distance in embedding space, lower = more similar)
                if distance is not None and distance < 0.3:
                    return True
            return False
        except Exception:
            return False  # On error, don't skip storage