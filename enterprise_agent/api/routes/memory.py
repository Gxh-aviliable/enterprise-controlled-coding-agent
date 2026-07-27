"""Memory viewer API — exposes user's long-term memories and patterns.

Supports list, view, and delete operations for both conversation
memories (task summaries) and learned user behavior patterns.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

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
        List of memory objects with id, content, importance, timestamp, session_id
    """
    memory = get_long_term_memory(user_id)

    results = await memory.list_conversations(
        limit=limit,
        role="task_summary",
        min_importance=min_importance,
        active_only=False,
    )

    # Format response
    memories = []
    for item in results:
        importance = item.get("metadata", {}).get("importance", 0)
        metadata = item.get("metadata", {})
        memories.append({
            "id": item.get("id", ""),
            "content": item.get("content", ""),
            "importance": importance,
            "timestamp": metadata.get("timestamp", ""),
            "session_id": metadata.get("session_id", ""),
            "rounds": metadata.get("rounds", 0),
            "has_tool_actions": metadata.get("has_tool_actions", False),
            "memory_type": metadata.get("memory_type", "legacy_summary"),
            "task_status": metadata.get("task_status", "unknown"),
            "quality_status": item.get("quality_status", "legacy"),
            "admission_reason": metadata.get("admission_reason", "legacy_unclassified"),
            "schema_version": metadata.get("schema_version", 1),
            "source": metadata.get("source", "legacy"),
            "content_format": metadata.get("content_format", "legacy_summary"),
            "retrieval_enabled": metadata.get("retrieval_enabled", True),
            "retrieval_count": metadata.get(
                "retrieval_count",
                metadata.get("access_count", 0),
            ),
            "last_retrieved_at": metadata.get(
                "last_retrieved_at",
                metadata.get("last_access", ""),
            ),
        })

    active_count = sum(m["quality_status"] == "active" for m in memories)
    return {
        "user_id": user_id,
        "count": len(memories),
        "active_count": active_count,
        "legacy_count": len(memories) - active_count,
        "memories": memories[:limit],
    }


@router.delete("/conversations/{doc_id}")
async def delete_conversation_memory(
    doc_id: str,
    user_id: int = Depends(get_current_user),
):
    """Delete a single conversation memory by document ID.

    Only the owner can delete their own memories. The user_id is verified
    against the document's metadata.

    Args:
        doc_id: ChromaDB document ID to delete
        user_id: Current user ID from JWT

    Returns:
        Success message

    Raises:
        HTTPException: 404 if document not found or not owned by user
    """
    memory = get_long_term_memory(user_id)
    receipt = await memory.delete_conversation_with_dependents(doc_id)

    if receipt is None:
        raise HTTPException(
            status_code=404,
            detail="Memory not found or you do not have permission to delete it",
        )

    return {"status": "deleted", **receipt}


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
        List of pattern objects with id, type, key, confidence
    """
    memory = get_long_term_memory(user_id)
    patterns = await memory.get_all_patterns(active_only=False)

    formatted_patterns = [
        {
            "id": p.get("id", ""),
            "pattern_type": p.get("pattern_type", ""),
            "pattern_key": p.get("pattern_key", ""),
            "confidence": p.get("confidence", 0),
            "evidence_count": p.get("evidence_count", 0),
            "updated_at": p.get("updated_at", ""),
            "retrieval_count": p.get("retrieval_count", 0),
            "last_retrieved_at": p.get("last_retrieved_at", ""),
            "value": p.get("value", ""),
            "text": p.get("text", ""),
            "quality_status": p.get("quality_status", "legacy"),
            "quarantine_reason": p.get("quarantine_reason", ""),
            "source_memory_ids": p.get("source_memory_ids", []),
        }
        for p in patterns
    ]
    active_count = sum(p["quality_status"] == "active" for p in formatted_patterns)
    return {
        "user_id": user_id,
        "count": len(formatted_patterns),
        "active_count": active_count,
        "legacy_count": len(formatted_patterns) - active_count,
        "patterns": formatted_patterns,
    }


@router.delete("/patterns/{pattern_id}")
async def delete_user_pattern(
    pattern_id: str,
    user_id: int = Depends(get_current_user),
):
    """Delete a single user pattern by document ID.

    Only the owner can delete their own patterns. The user_id is verified
    against the document's metadata.

    Args:
        pattern_id: ChromaDB document ID to delete
        user_id: Current user ID from JWT

    Returns:
        Success message

    Raises:
        HTTPException: 404 if document not found or not owned by user
    """
    memory = get_long_term_memory(user_id)
    deleted = await memory.delete_pattern(pattern_id)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Pattern not found or you do not have permission to delete it",
        )

    return {"status": "deleted", "id": pattern_id}
