"""Long-term-memory tool routing and pagination tests."""

import json

from enterprise_agent.core.agent.tools.memory import list_memories
from enterprise_agent.core.agent.tools.workspace import set_current_user_id


async def test_list_memories_pages_deterministically_without_artifacts(
    monkeypatch,
):
    pattern_accesses = []
    conversation_accesses = []

    class FakeMemory:
        async def get_all_patterns(self, **_kwargs):
            return [
                {
                    "id": "pattern-old",
                    "pattern_type": "preference",
                    "pattern_key": "editor",
                    "value": "VS Code",
                    "confidence": 0.8,
                    "updated_at": "2026-01-01T00:00:00Z",
                },
                {
                    "id": "pattern-new",
                    "pattern_type": "workflow",
                    "pattern_key": "tests",
                    "value": "pytest",
                    "confidence": 1.0,
                    "updated_at": "2026-02-01T00:00:00Z",
                },
            ]

        async def list_conversations(self, *, limit, **_kwargs):
            rows = [
                {
                    "id": f"summary-{index}",
                    "content": f"task summary {index}",
                    "metadata": {
                        "importance": 1.0 - index / 10,
                        "timestamp": f"2026-03-0{index + 1}T00:00:00Z",
                    },
                }
                for index in range(3)
            ]
            return rows[:limit]

        async def update_pattern_access_count(self, memory_id):
            pattern_accesses.append(memory_id)

        async def update_access_count(self, memory_id):
            conversation_accesses.append(memory_id)

    monkeypatch.setattr(
        "enterprise_agent.memory.long_term.get_long_term_memory",
        lambda _user_id: FakeMemory(),
    )
    set_current_user_id(301)
    try:
        first = json.loads(await list_memories.ainvoke({"cursor": 0, "limit": 2}))
        second = json.loads(
            await list_memories.ainvoke({"cursor": first["next_cursor"], "limit": 2})
        )
        final = json.loads(
            await list_memories.ainvoke({"cursor": second["next_cursor"], "limit": 2})
        )
    finally:
        set_current_user_id(None)

    assert [item["id"] for item in first["items"]] == [
        "pattern-new",
        "pattern-old",
    ]
    assert first["next_cursor"] == 2
    assert first["eof"] is False
    assert [item["id"] for item in second["items"]] == ["summary-0", "summary-1"]
    assert second["next_cursor"] == 4
    assert [item["id"] for item in final["items"]] == ["summary-2"]
    assert final["next_cursor"] is None
    assert final["eof"] is True
    assert pattern_accesses == ["pattern-new", "pattern-old"]
    assert conversation_accesses == ["summary-0", "summary-1", "summary-2"]
