"""Public Plan A tools cannot execute outside the governed parent runtime."""

import pytest

from enterprise_agent.core.agent.tools import get_tools_for_permissions
from enterprise_agent.core.agent.tools.subagent import AGENT_TYPES, SUBAGENT_SYSTEM_PROMPTS, delegate_task, task


def test_one_creation_tool_is_exposed():
    names = {t.name for t in get_tools_for_permissions(["tools:all"], enable_multi_agent=True)}
    assert "delegate_task" in names
    assert not names.intersection({"task", "spawn_teammate", "send_message", "broadcast", "idle"})
    assert "delegate_task" not in {t.name for t in get_tools_for_permissions(["tools:all"], enable_multi_agent=False)}


@pytest.mark.parametrize("profile", ["Explore", "general-purpose"])
def test_exploration_has_no_process_or_mutation_entry(profile):
    assert set(AGENT_TYPES[profile]) == {"read_file", "list_files", "search_files"}
    assert "read-only" in SUBAGENT_SYSTEM_PROMPTS[profile]
    assert "untrusted data" in SUBAGENT_SYSTEM_PROMPTS[profile]


@pytest.mark.parametrize(
    "tool,args",
    [(delegate_task, {"role": "reviewer", "prompt": "audit"}), (task, {"prompt": "audit", "agent_type": "Explore"})],
)
async def test_unscoped_tool_execution_is_rejected(tool, args):
    result = await tool.ainvoke(args)
    assert result["status"] == "failed"
    assert result["error_code"] == "child_scope_required"
