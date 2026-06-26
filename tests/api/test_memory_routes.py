"""Tests for memory management API routes."""

import asyncio

from enterprise_agent.api.routes.memory import list_conversation_memories


def test_list_conversation_memories_uses_explicit_list_api(monkeypatch):
    """Memory management should list owned memories, not approximate with search."""
    calls = {}

    class FakeMemory:
        async def list_conversations(self, limit, role, min_importance):
            calls["args"] = {
                "limit": limit,
                "role": role,
                "min_importance": min_importance,
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
                    },
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
    }
    assert result["count"] == 1
    assert result["memories"][0]["id"] == "doc-1"
