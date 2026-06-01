"""Memory query tool for Enterprise Agent.

Provides active ChromaDB long-term memory access for the agent.
Before this tool, the agent could only see memories pre-injected into
the <long_term_memory> block on the first message. Now it can query
ChromaDB at any time — listing all memories or searching by topic.
"""

import logging
from typing import Optional

from langchain_core.tools import tool

from enterprise_agent.core.agent.tools.workspace import get_current_user_id

logger = logging.getLogger(__name__)


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
            # Extract value from pattern text if available
            if " = " in text:
                value_part = text.split(" = ", 1)[1]
                parts.append(f"- **{pkey}** ({ptype}, 置信度: {confidence:.0%})")
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
                parts.append(f"### {i}. (重要性: {importance})")
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

    IMPORTANT: This IS the long-term memory. Do NOT use bash/dir/read_file to
    explore .tasks/, .transcripts/, or .team/ directories — those are NOT the
    long-term memory store. This tool queries the actual ChromaDB vector database
    where memories are persisted.

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

    search_query = query.strip() if query and query.strip() else "task summary user preference workflow"

    try:
        from enterprise_agent.memory.long_term import get_long_term_memory

        memory = get_long_term_memory(user_id)

        # Search conversations (task_summary role for structured memories)
        conversations = await memory.search_conversations(
            query=search_query,
            n_results=10,
        )

        # Search patterns
        patterns = await memory.search_patterns(
            query=search_query,
            n_results=5,
        )

        return _format_memory_results(conversations, patterns, search_query)

    except Exception as e:
        logger.warning(f"search_memory tool failed: {e}", exc_info=True)
        return f"Error searching long-term memory: {e}"
