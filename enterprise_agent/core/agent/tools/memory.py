"""Memory query tool for Enterprise Agent.

Provides active ChromaDB long-term memory access for the agent.
Before this tool, the agent could only see memories pre-injected into
the <long_term_memory> block on the first message. Now it can query
ChromaDB at any time — listing all memories or searching by topic.
"""

import logging
from contextvars import ContextVar
from typing import Any, Optional

from langchain_core.tools import tool

from enterprise_agent.core.agent.tools.workspace import get_current_user_id

logger = logging.getLogger(__name__)
_memory_search_audit_slot: ContextVar[dict[str, Any] | None] = ContextVar(
    "memory_search_audit_slot",
    default=None,
)


def prepare_memory_search_audit() -> None:
    """Create a mutable slot before ``asyncio.wait_for`` copies context."""
    _memory_search_audit_slot.set({})


def _set_memory_search_audit(audit: dict[str, Any] | None) -> None:
    slot = _memory_search_audit_slot.get()
    if slot is None:
        slot = {}
        _memory_search_audit_slot.set(slot)
    slot.clear()
    if audit is not None:
        slot["audit"] = audit


def consume_memory_search_audit() -> dict[str, Any] | None:
    """Return and clear the task-local audit produced by ``search_memory``."""
    slot = _memory_search_audit_slot.get()
    audit = slot.pop("audit", None) if slot is not None else None
    _memory_search_audit_slot.set(None)
    return audit


def _format_memory_results(
    conversations: list,
    patterns: list,
    query: str,
) -> str:
    """Format ChromaDB search results into human-readable text.

    Args:
        conversations: List of conversation result dicts
        patterns: List of pattern result dicts
        query: The original search query (for context)

    Returns:
        Formatted markdown string
    """
    parts = []

    # Patterns section (shown first — most compact and high-value)
    if patterns:
        parts.append("## 🧠 用户偏好/习惯 (User Patterns)")
        parts.append("")
        for p in patterns:
            ptype = p.get("pattern_type", "unknown")
            pkey = p.get("pattern_key", "")
            confidence = p.get("confidence", 0) or 0  # Guard against None from Chroma metadata
            text = p.get("text", "")
            value = p.get("value", "")
            # Prefer the UTF-8 value metadata. Older documents encoded Chinese
            # as ``\\uXXXX`` inside their searchable text.
            if value or " = " in text:
                value_part = value or text.split(" = ", 1)[1]
                parts.append(
                    f"- **{pkey}** ({ptype}, 置信度: {confidence:.0%}, "
                    f"memory_id={p.get('id', 'unknown')})"
                )
                parts.append(f"  → {value_part[:200]}")
            else:
                parts.append(f"- **{pkey}** ({ptype}, 置信度: {confidence:.0%})")
        parts.append("")

    # Conversations section (task summaries first)
    if conversations:
        # Separate task_summary from others
        summaries = [c for c in conversations if c.get("metadata", {}).get("role") == "task_summary"]
        others = [c for c in conversations if c.get("metadata", {}).get("role") != "task_summary"]

        if summaries:
            parts.append("## 📋 任务摘要 (Task Summaries)")
            parts.append("")
            for i, conv in enumerate(summaries, 1):
                meta = conv.get("metadata", {})
                importance = meta.get("importance", "N/A")
                if isinstance(importance, (int, float)):
                    importance = f"{importance:.2f}"
                content = conv.get("content", "")
                if len(content) > 500:
                    content = content[:500] + "..."
                parts.append(
                    f"### {i}. (重要性: {importance}, "
                    f"memory_id={conv.get('id', 'unknown')})"
                )
                parts.append(content)
                parts.append("")
                parts.append("---")
                parts.append("")

        if others:
            parts.append("## 💬 其他对话记录")
            parts.append("")
            for i, conv in enumerate(others, 1):
                meta = conv.get("metadata", {})
                role = meta.get("role", "unknown")
                content = conv.get("content", "")
                if len(content) > 300:
                    content = content[:300] + "..."
                parts.append(f"### {i}. [{role}]")
                parts.append(content)
                parts.append("")

    if not parts:
        return (
            f"No memories or patterns found in long-term storage for query: "
            f"\"{query[:100]}\". The user's ChromaDB memory is empty or has "
            f"nothing matching this topic."
        )

    result = "\n".join(parts)
    # Cap at reasonable size so it doesn't overwhelm the context
    if len(result) > 3000:
        result = result[:3000] + "\n\n... (truncated, use a more specific query to narrow results)"
    return result


@tool
async def search_memory(query: Optional[str] = None) -> str:
    """Search the user's long-term memory stored in ChromaDB.

    Use this tool when:
    - The user asks "what do you remember about me?", "what's in my memory?",
      "show me my saved memories", "do you have any memories about...", etc.
    - You need to recall past technical decisions, preferences, or solutions
    - The <long_term_memory> block in the first message is empty or insufficient
    - The user wants to know what information is stored about them

    This tool searches BOTH:
    1. Task summaries (structured records of past conversations — the main
       long-term memory store)
    2. User patterns (learned preferences, workflows, habits)

    CRITICAL: This IS the ONLY tool for long-term memory. Do NOT use `task_list`
    (operational task tracking in .tasks/), `list_transcripts` (compression
    backup transcripts), or bash/dir/read_file on .tasks/, .transcripts/, .team/
    directories. Those are workspace operational artifacts — COMPLETELY UNRELATED
    to the user's long-term memory. Only `search_memory` queries the ChromaDB
    vector database where actual user memories are persisted.

    Args:
        query: What to search for. Use descriptive English queries like:
               - "user preferences about Python environment management"
               - "past decisions about API design"
               - "task summary user preference workflow" (to list ALL memories)
               If empty or None, searches broadly for all stored task summaries.

    Returns:
        Formatted list of matching memories (task summaries and patterns).
    """
    user_id = get_current_user_id()
    if not user_id:
        return "Error: No user context available. Cannot search memory without a user ID."

    search_query = query.strip() if query and query.strip() else ""
    broad_listing = not search_query or search_query == "task summary user preference workflow"
    _set_memory_search_audit(None)

    try:
        from enterprise_agent.config.settings import settings
        from enterprise_agent.memory.long_term import get_long_term_memory

        memory = get_long_term_memory(user_id)

        if broad_listing:
            conversations = await memory.list_conversations(
                limit=50,
                role="task_summary",
                active_only=True,
            )
            patterns = await memory.get_all_patterns(active_only=True)
            conversation_candidates = [
                {
                    **item,
                    "rank": index + 1,
                    "eligible": True,
                    "filter_reason": "eligible",
                }
                for index, item in enumerate(conversations)
            ]
            pattern_candidates = [
                {
                    **item,
                    "rank": index + 1,
                    "eligible": True,
                    "filter_reason": "eligible",
                }
                for index, item in enumerate(patterns)
            ]
            display_query = "all active memories"
            strategy = "complete_listing"
        else:
            conversation_candidates = await memory.search_conversations(
                query=search_query,
                n_results=5,
                role="task_summary",
                active_only=True,
                max_distance=settings.MEMORY_RELEVANCE_MAX_DISTANCE,
                retrieval_enabled_only=True,
                include_rejected=True,
            )
            pattern_candidates = await memory.search_patterns(
                query=search_query,
                n_results=3,
                active_only=True,
                max_distance=settings.MEMORY_RELEVANCE_MAX_DISTANCE,
                retrieval_enabled_only=True,
                include_rejected=True,
            )
            conversations = [
                item
                for item in conversation_candidates
                if item.get("eligible")
            ][:5]
            patterns = [
                item
                for item in pattern_candidates
                if item.get("eligible")
            ][:3]
            display_query = search_query
            strategy = next(
                (
                    item.get("retrieval_strategy")
                    for item in [*pattern_candidates, *conversation_candidates]
                    if item.get("retrieval_strategy")
                ),
                "semantic_top_k",
            )

        for conversation in conversations:
            if conversation.get("id"):
                await memory.update_access_count(conversation["id"])
        for pattern in patterns:
            if pattern.get("id"):
                await memory.update_pattern_access_count(pattern["id"])

        result = _format_memory_results(conversations, patterns, display_query)

        def trace_candidate(item: dict[str, Any], collection: str) -> dict[str, Any]:
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

        injected_ids = [
            *(item.get("id") for item in patterns if item.get("id")),
            *(item.get("id") for item in conversations if item.get("id")),
        ]
        injected_characters = len(result) if injected_ids else 0
        _set_memory_search_audit({
            "query_summary": display_query[:500],
            "strategy": strategy,
            "threshold": settings.MEMORY_RELEVANCE_MAX_DISTANCE,
            "top_k_per_collection": {"patterns": 3, "conversations": 5},
            "candidates": [
                *(
                    trace_candidate(item, "patterns")
                    for item in pattern_candidates
                ),
                *(
                    trace_candidate(item, "conversations")
                    for item in conversation_candidates
                ),
            ],
            "injected_ids": injected_ids,
            "injected_count": len(injected_ids),
            "injected_characters": injected_characters,
            "injected_tokens": (
                max(1, injected_characters // 4)
                if injected_characters
                else 0
            ),
            "application_status": "not_attributed",
            "source": "search_memory_tool",
        })
        return result

    except Exception as e:
        _set_memory_search_audit({
            "query_summary": search_query[:500],
            "error": str(e)[:1000],
            "source": "search_memory_tool",
        })
        logger.warning(f"search_memory tool failed: {e}", exc_info=True)
        return f"Error searching long-term memory: {e}"
