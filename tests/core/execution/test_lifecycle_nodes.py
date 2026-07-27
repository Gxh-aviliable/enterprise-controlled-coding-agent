"""Reliable LangGraph lifecycle-node behavior tests."""

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.nodes import (
    finalize_task_node,
    prepare_tool_execution_node,
    route_after_tool,
    task_parse_node,
    tool_executor_node,
)


async def test_task_parse_starts_pending_task():
    result = await task_parse_node({
        "task_status": "pending",
        "trace_id": "trace-1",
        "messages": [{"role": "user", "content": "Fix the parser"}],
    })
    assert result["task_status"] == "running"
    assert result["execution_phase"] == "parsing"
    assert result["current_task"]["request"] == "Fix the parser"


async def test_sensitive_tool_is_checkpointed_as_waiting_confirmation():
    result = await prepare_tool_execution_node({
        "task_status": "running",
        "pending_tool_calls": [{"id": "1", "name": "write_file", "args": {}}],
    })
    assert result["task_status"] == "waiting_confirmation"
    assert result["confirmation_deadline"] is not None


async def test_safe_shell_command_skips_confirmation():
    result = await prepare_tool_execution_node({
        "task_status": "running",
        "pending_tool_calls": [{
            "id": "safe-shell",
            "name": "bash",
            "args": {"command": "pytest -q"},
        }],
    })
    assert result["task_status"] == "running"
    assert result["confirmation_deadline"] is None


async def test_review_shell_command_waits_for_confirmation():
    result = await prepare_tool_execution_node({
        "task_status": "running",
        "pending_tool_calls": [{
            "id": "review-shell",
            "name": "bash",
            "args": {"command": "git commit -m test"},
        }],
    })
    assert result["task_status"] == "waiting_confirmation"
    assert result["confirmation_deadline"] is not None


async def test_dangerous_shell_skips_confirmation_and_is_policy_blocked():
    state = {
        "session_id": "dangerous-shell-test",
        "user_id": 103,
        "permissions": ["tools:shell"],
        "task_status": "running",
        "pending_tool_calls": [{
            "id": "dangerous-shell",
            "name": "bash",
            "args": {"command": "rm -rf /"},
        }],
    }
    prepared = await prepare_tool_execution_node(state)
    assert prepared["task_status"] == "running"
    assert prepared["confirmation_deadline"] is None

    executed = await tool_executor_node(state)
    record = executed["tool_execution_records"][0]
    assert record["status"] == "blocked"
    assert record["error_code"] == "policy_blocked"


async def test_unknown_tool_skips_confirmation_and_reaches_safe_rejection():
    state = {
        "session_id": "unknown-tool-test",
        "user_id": 102,
        "permissions": ["tools:all"],
        "task_status": "running",
        "pending_tool_calls": [{
            "id": "unknown-1",
            "name": "run_command",
            "args": {"command": "echo must-not-run"},
        }],
    }

    prepared = await prepare_tool_execution_node(state)
    assert prepared["task_status"] == "running"
    assert prepared["confirmation_deadline"] is None

    executed = await tool_executor_node(state)
    record = executed["tool_execution_records"][0]
    assert executed["tool_results"]["unknown-1"] == "Error: Unknown tool: run_command"
    assert record["ok"] is False
    assert record["status"] == "error"
    assert record["error_code"] == "unknown_tool"


async def test_unverified_code_change_finishes_failed():
    result = await finalize_task_node({
        "task_status": "running",
        "changed_files": ["src/app.py"],
        "validation_results": [],
        "todos": [{
            "content": "Run verification",
            "status": "in_progress",
            "activeForm": "Running verification",
        }],
    })
    assert result["task_status"] == "failed"
    assert "not successfully validated" in result["failure_reason"]
    assert result["todos"][0]["status"] == "failed"
    assert result["has_open_todos"] is False


async def test_non_modifying_task_finishes_succeeded():
    result = await finalize_task_node({
        "task_status": "running",
        "changed_files": [],
        "validation_results": [],
    })
    assert result["task_status"] == "succeeded"
    assert result["failure_reason"] is None


async def test_multi_agent_task_cannot_succeed_without_real_delegation():
    result = await finalize_task_node({
        "task_status": "running",
        "execution_mode": "multi_agent",
        "tool_execution_records": [],
        "changed_files": [],
        "validation_results": [],
    })
    assert result["task_status"] == "failed"
    assert "without a successful delegate_task" in result["failure_reason"]


async def test_multi_agent_blocks_workspace_mutation_before_delegation(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_MULTI_AGENT", True)
    result = await tool_executor_node({
        "session_id": "multi-delegation-gate",
        "user_id": 104,
        "permissions": ["tools:all"],
        "execution_mode": "multi_agent",
        "task_status": "running",
        "pending_tool_calls": [{
            "id": "fake-script",
            "name": "write_file",
            "args": {"path": "fake_agents.py", "content": "# simulated agents"},
        }],
    })
    record = result["tool_execution_records"][0]
    assert record["status"] == "blocked"
    assert record["error_code"] == "delegation_required"


def test_route_requests_verification_before_ending_code_change():
    route = route_after_tool({
        "task_status": "running",
        "should_end_after_save": True,
        "changed_files": ["frontend/src/App.vue"],
        "validation_results": [],
        "verification_attempts": 0,
        "round_count": 1,
        "token_count": 10,
    })
    assert route == "verify"


async def test_executor_denies_tool_missing_from_jwt_permissions():
    result = await tool_executor_node({
        "session_id": "permission-test",
        "user_id": 100,
        "permissions": ["tools:basic"],
        "task_status": "running",
        "pending_tool_calls": [{
            "id": "shell-1",
            "name": "bash",
            "args": {"command": "echo should-not-run"},
        }],
    })
    assert "Permission denied" in result["tool_results"]["shell-1"]
    record = result["tool_execution_records"][0]
    assert record["ok"] is False
    assert record["status"] == "blocked"
    assert record["error_code"] == "permission_denied"


async def test_executor_tracks_code_change_and_successful_validation(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    write_result = await tool_executor_node({
        "session_id": "validation-test",
        "user_id": 101,
        "permissions": ["tools:basic"],
        "task_status": "running",
        "pending_tool_calls": [{
            "id": "write-1",
            "name": "write_file",
            "args": {"path": "src/example.py", "content": "value = 1\n"},
        }],
    })
    assert write_result["changed_files"] == ["src/example.py"]

    validation_result = await tool_executor_node({
        "session_id": "validation-test",
        "user_id": 101,
        "permissions": ["tools:shell"],
        "task_status": "running",
        "pending_tool_calls": [{
            "id": "validate-1",
            "name": "bash",
            "args": {"command": "python -m compileall -q ."},
        }],
        "changed_files": write_result["changed_files"],
        "tool_execution_records": write_result["tool_execution_records"],
        "tool_call_count": write_result["tool_call_count"],
    })
    assert validation_result["validation_results"][0]["ok"] is True

    final = await finalize_task_node(validation_result)
    assert final["task_status"] == "succeeded"


async def test_search_memory_tool_records_the_same_retrieval_trace(monkeypatch):
    """Active tool lookup must be visible in Trace and retrieval counters."""
    events = []
    accessed_patterns = []

    class FakeMemory:
        async def search_conversations(self, **kwargs):
            return [{
                "id": "rejected-memory",
                "content": "unrelated",
                "metadata": {"memory_type": "task_outcome"},
                "rank": 2,
                "distance": 0.9,
                "eligible": False,
                "filter_reason": "distance_above_threshold",
                "retrieval_strategy": "semantic_top_k",
            }]

        async def search_patterns(self, **kwargs):
            return [{
                "id": "pattern-uv",
                "text": 'preference: package_manager = {"value":"uv"}',
                "pattern_type": "preference",
                "pattern_key": "package_manager",
                "confidence": 1.0,
                "value": '{"value":"uv"}',
                "rank": 1,
                "distance": 0.1,
                "eligible": True,
                "filter_reason": "eligible",
                "retrieval_strategy": "semantic_top_k",
            }]

        async def update_access_count(self, memory_id):
            raise AssertionError(f"rejected memory was counted: {memory_id}")

        async def update_pattern_access_count(self, pattern_id):
            accessed_patterns.append(pattern_id)

    class FakeTraceStore:
        def record_event(self, **event):
            events.append(event)

    monkeypatch.setattr(
        "enterprise_agent.memory.long_term.get_long_term_memory",
        lambda user_id: FakeMemory(),
    )
    monkeypatch.setattr(
        "enterprise_agent.core.agent.nodes.get_trace_store",
        lambda: FakeTraceStore(),
    )

    result = await tool_executor_node({
        "session_id": "memory-tool-trace",
        "trace_id": "trace-memory-tool",
        "user_id": 105,
        "permissions": ["tools:memory"],
        "task_status": "running",
        "pending_tool_calls": [{
            "id": "memory-search-1",
            "name": "search_memory",
            "args": {"query": "Python dependency management preference"},
        }],
    })

    assert result["tool_execution_records"][0]["ok"] is True
    assert accessed_patterns == ["pattern-uv"]
    memory_event = next(
        event
        for event in events
        if event["event_type"] == "memory"
        and event["name"] == "memory_retrieval"
    )
    assert memory_event["data"]["source"] == "search_memory_tool"
    assert memory_event["data"]["injected_ids"] == ["pattern-uv"]
    assert memory_event["data"]["injected_count"] == 1

    class EmptyMemory:
        async def search_conversations(self, **kwargs):
            return []

        async def search_patterns(self, **kwargs):
            return []

    events.clear()
    monkeypatch.setattr(
        "enterprise_agent.memory.long_term.get_long_term_memory",
        lambda user_id: EmptyMemory(),
    )
    await tool_executor_node({
        "session_id": "empty-memory-tool-trace",
        "trace_id": "trace-empty-memory-tool",
        "user_id": 105,
        "permissions": ["tools:memory"],
        "task_status": "running",
        "pending_tool_calls": [{
            "id": "empty-memory-search",
            "name": "search_memory",
            "args": {"query": "a preference that does not exist"},
        }],
    })

    empty_event = next(
        event for event in events if event["event_type"] == "memory"
    )
    assert empty_event["data"]["injected_count"] == 0
    assert empty_event["data"]["injected_characters"] == 0
    assert empty_event["data"]["injected_tokens"] == 0
