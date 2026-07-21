"""Tests for memory management API routes."""

import asyncio

from enterprise_agent.api.routes.memory import (
    delete_conversation_memory,
    list_conversation_memories,
)


def test_list_conversation_memories_uses_explicit_list_api(monkeypatch):
    """Memory management should list owned memories, not approximate with search."""
    calls = {}

    class FakeMemory:
        async def list_conversations(self, limit, role, min_importance, active_only):
            calls["args"] = {
                "limit": limit,
                "role": role,
                "min_importance": min_importance,
                "active_only": active_only,
            }
            return [
                {
                    "id": "doc-1",
                    "content": "stored summary",
                    "metadata": {
                        "importance": 0.8,
                        "timestamp": "2026-06-27T00:00:00+00:00",
                        "session_id": "session-1",
                        "rounds": 2,
                        "has_tool_actions": True,
                        "memory_type": "task_outcome",
                        "task_status": "succeeded",
                        "admission_reason": "verified_engineering_outcome",
                        "schema_version": 2,
                        "retrieval_count": 3,
                        "last_retrieved_at": "2026-07-20T00:00:00+00:00",
                    },
                    "quality_status": "active",
                }
            ]

        async def search_conversations(self, *args, **kwargs):
            raise AssertionError("list route must not use semantic search")

    monkeypatch.setattr(
        "enterprise_agent.api.routes.memory.get_long_term_memory",
        lambda user_id: FakeMemory(),
    )

    result = asyncio.run(
        list_conversation_memories(user_id=1, limit=10, min_importance=0.5)
    )

    assert calls["args"] == {
        "limit": 10,
        "role": "task_summary",
        "min_importance": 0.5,
        "active_only": False,
    }
    assert result["count"] == 1
    assert result["active_count"] == 1
    assert result["legacy_count"] == 0
    assert result["memories"][0]["id"] == "doc-1"
    assert result["memories"][0]["memory_type"] == "task_outcome"
    assert result["memories"][0]["retrieval_count"] == 3
    assert result["memories"][0]["last_retrieved_at"] == "2026-07-20T00:00:00+00:00"


def test_delete_conversation_returns_cascade_receipt(monkeypatch):
    class FakeMemory:
        async def delete_conversation_with_dependents(self, doc_id):
            return {
                "id": doc_id,
                "deleted_pattern_ids": ["pattern-1", "pattern-2"],
                "deleted_pattern_count": 2,
            }

    monkeypatch.setattr(
        "enterprise_agent.api.routes.memory.get_long_term_memory",
        lambda user_id: FakeMemory(),
    )

    result = asyncio.run(
        delete_conversation_memory(doc_id="memory-1", user_id=1)
    )

    assert result == {
        "status": "deleted",
        "id": "memory-1",
        "deleted_pattern_ids": ["pattern-1", "pattern-2"],
        "deleted_pattern_count": 2,
    }
