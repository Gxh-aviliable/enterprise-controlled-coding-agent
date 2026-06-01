"""Memory viewer API — exposes user's long-term memories and patterns."""

from fastapi import APIRouter, Depends, Query
from enterprise_agent.api.middleware.auth import get_current_user
from enterprise_agent.memory.long_term import get_long_term_memory

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/conversations")
async def list_conversation_memories(
    user_id: int = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
    min_importance: float = Query(0.0, ge=0.0, le=1.0),
):
    """List user's conversation memories (task summaries).

    Returns memories sorted by importance (highest first).

    Args:
        user_id: Current user ID
        limit: Max number of memories to return
        min_importance: Filter by minimum importance score (0.0-1.0)

    Returns:
        List of memory objects with content, importance, timestamp, session_id
    """
    memory = get_long_term_memory(user_id)

    # Search with a broad query to get all task summaries
    results = memory.search_conversations(
        query="task summary",
        n_results=limit,
        role="task_summary",
    )

    # Filter by importance and format response
    memories = []
    for item in results:
        importance = item.get("metadata", {}).get("importance", 0)
        if importance < min_importance:
            continue
        memories.append({
            "content": item.get("content", ""),
            "importance": importance,
            "timestamp": item.get("metadata", {}).get("timestamp", ""),
            "session_id": item.get("metadata", {}).get("session_id", ""),
            "rounds": item.get("metadata", {}).get("rounds", 0),
            "has_tool_actions": item.get("metadata", {}).get("has_tool_actions", False),
        })

    # Sort by importance descending
    memories.sort(key=lambda m: m["importance"], reverse=True)

    return {
        "user_id": user_id,
        "count": len(memories),
        "memories": memories[:limit],
    }


@router.get("/patterns")
async def list_user_patterns(
    user_id: int = Depends(get_current_user),
):
    """List user's behavior patterns.

    Returns patterns like preferences, workflows, and shortcuts
    that the system has learned about the user.

    Args:
        user_id: Current user ID

    Returns:
        List of pattern objects with type, key, confidence
    """
    memory = get_long_term_memory(user_id)
    patterns = memory.get_all_patterns()

    return {
        "user_id": user_id,
        "count": len(patterns),
        "patterns": [
            {
                "pattern_type": p.get("pattern_type", ""),
                "pattern_key": p.get("pattern_key", ""),
                "confidence": p.get("confidence", 0),
            }
            for p in patterns
        ],
    }
