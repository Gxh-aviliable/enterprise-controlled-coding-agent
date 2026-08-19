"""Reliable LangGraph lifecycle-node behavior tests."""

import json

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.graph import _traced_node
from enterprise_agent.core.agent.nodes import (
    finalize_task_node,
    prepare_tool_execution_node,
    route_after_tool,
    task_parse_node,
    tool_executor_node,
)
from enterprise_agent.core.agent.tool_artifacts import ToolArtifactStore
from enterprise_agent.observability.trace_store import get_trace_store


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


async def test_dangerous_shell_skips_confirmation_and_is_policy_blocked(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
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


async def test_executor_checks_cancel_before_each_tool_in_a_batch(monkeypatch):
    """A Stop observed after one result must fence every later tool call."""
    cancelled = False
    invocations = []

    class FakeRead:
        name = "read_file"

        async def ainvoke(self, tool_input):
            nonlocal cancelled
            invocations.append(tool_input["path"])
            if tool_input["path"] == "first.txt":
                cancelled = True
                return "first tool may already have completed"
            raise AssertionError("second tool ran after cancellation")

    async def fake_cancel_request(_state):
        return {"reason": "Stop requested"} if cancelled else None

    fake_read = FakeRead()
    monkeypatch.setattr("enterprise_agent.core.agent.nodes.ALL_TOOLS", [fake_read])
    monkeypatch.setattr(
        "enterprise_agent.core.agent.nodes.get_tools_for_permissions",
        lambda *_args, **_kwargs: [fake_read],
    )
    monkeypatch.setattr(
        "enterprise_agent.core.agent.nodes._tool_cancel_request",
        fake_cancel_request,
    )

    result = await tool_executor_node({
        "session_id": "cancel-mid-batch",
        "trace_id": "trace-cancel-mid-batch",
        "user_id": 120,
        "permissions": ["tools:basic"],
        "task_status": "running",
        "pending_tool_calls": [
            {"id": "read-first", "name": "read_file", "args": {"path": "first.txt"}},
            {"id": "read-second", "name": "read_file", "args": {"path": "second.txt"}},
        ],
    })

    assert invocations == ["first.txt"]
    assert result["task_status"] == "cancelled"
    records = {
        record["tool_call_id"]: record
        for record in result["tool_execution_records"]
    }
    assert records["read-first"]["ok"] is True
    assert records["read-second"]["error_code"] == "task_cancelled"
    assert records["read-second"]["attempt_count"] == 0


async def test_executor_checks_cancel_before_retry_invocation(monkeypatch):
    """A retryable failure must not trigger another call after Stop arrives."""
    cancelled = False
    invocation_count = 0

    class RetryableRead:
        name = "read_file"

        async def ainvoke(self, _tool_input):
            nonlocal cancelled, invocation_count
            invocation_count += 1
            cancelled = True
            raise RuntimeError("connection timeout")

    async def fake_cancel_request(_state):
        return {"reason": "Stop requested during retry delay"} if cancelled else None

    async def no_retry_delay(_seconds):
        return None

    fake_read = RetryableRead()
    monkeypatch.setattr("enterprise_agent.core.agent.nodes.ALL_TOOLS", [fake_read])
    monkeypatch.setattr(
        "enterprise_agent.core.agent.nodes.get_tools_for_permissions",
        lambda *_args, **_kwargs: [fake_read],
    )
    monkeypatch.setattr(
        "enterprise_agent.core.agent.nodes._tool_cancel_request",
        fake_cancel_request,
    )
    monkeypatch.setattr("enterprise_agent.core.agent.nodes.asyncio.sleep", no_retry_delay)

    result = await tool_executor_node({
        "session_id": "cancel-before-retry",
        "trace_id": "trace-cancel-before-retry",
        "user_id": 121,
        "permissions": ["tools:basic"],
        "task_status": "running",
        "pending_tool_calls": [{
            "id": "retry-read",
            "name": "read_file",
            "args": {"path": "unstable.txt"},
        }],
    })

    assert invocation_count == 1
    assert result["task_status"] == "cancelled"
    record = result["tool_execution_records"][0]
    assert record["error_code"] == "task_cancelled"
    assert record["attempt_count"] == 1


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


async def test_py_compile_exit_zero_is_recorded_as_code_validation(monkeypatch, tmp_path):
    """A narrow Python syntax check is valid evidence when its exit code is zero."""
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))

    class SuccessfulBash:
        name = "bash"

        async def ainvoke(self, _tool_input):
            return {"stdout": "", "stderr": "", "exit_code": 0}

    fake_bash = SuccessfulBash()
    monkeypatch.setattr("enterprise_agent.core.agent.nodes.ALL_TOOLS", [fake_bash])
    monkeypatch.setattr(
        "enterprise_agent.core.agent.nodes.get_tools_for_permissions",
        lambda *_args, **_kwargs: [fake_bash],
    )

    result = await tool_executor_node({
        "session_id": "py-compile-success",
        "user_id": 109,
        "permissions": ["tools:shell"],
        "task_status": "running",
        "changed_files": ["src/example.py"],
        "pending_tool_calls": [{
            "id": "validate-py-success",
            "name": "bash",
            "args": {"command": "python -m py_compile src/example.py"},
        }],
        "messages": [],
    })

    assert len(result["validation_results"]) == 1
    validation = result["validation_results"][0]
    assert validation["command"] == "python -m py_compile src/example.py"
    assert validation["ok"] is True
    assert validation["status"] == "success"
    assert validation["exit_code"] == 0
    assert validation["duration_ms"] >= 0
    final = await finalize_task_node(result)
    assert final["task_status"] == "succeeded"
    assert final["failure_reason"] is None


async def test_py_compile_nonzero_exit_is_failed_validation_evidence(monkeypatch, tmp_path):
    """Recognizing py_compile must never turn a non-zero exit into success."""
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))

    class FailedBash:
        name = "bash"

        async def ainvoke(self, _tool_input):
            return {
                "stdout": "",
                "stderr": "SyntaxError: invalid syntax",
                "exit_code": 1,
            }

    fake_bash = FailedBash()
    monkeypatch.setattr("enterprise_agent.core.agent.nodes.ALL_TOOLS", [fake_bash])
    monkeypatch.setattr(
        "enterprise_agent.core.agent.nodes.get_tools_for_permissions",
        lambda *_args, **_kwargs: [fake_bash],
    )

    result = await tool_executor_node({
        "session_id": "py-compile-failure",
        "user_id": 110,
        "permissions": ["tools:shell"],
        "task_status": "running",
        "changed_files": ["src/broken.py"],
        "pending_tool_calls": [{
            "id": "validate-py-failure",
            "name": "bash",
            "args": {"command": "python -m py_compile src/broken.py"},
        }],
        "messages": [],
    })

    assert len(result["validation_results"]) == 1
    assert result["validation_results"][0]["ok"] is False
    assert result["validation_results"][0]["exit_code"] == 1
    final = await finalize_task_node(result)
    assert final["task_status"] == "failed"
    assert "not successfully validated" in final["failure_reason"]


async def test_successful_delegate_execution_and_final_trace_status_agree(
    monkeypatch,
    tmp_path,
):
    """Multi-Agent success must come from an executed delegate tool and reach Trace."""
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr(settings, "ENABLE_MULTI_AGENT", True)

    class FakeDelegate:
        name = "delegate_task"

        async def ainvoke(self, _tool_input):
            return "Independent reviewer confirmed the proposed design."

    class FakeWrite:
        name = "write_file"

        async def ainvoke(self, _tool_input):
            return "Successfully wrote src/demo.py"

    class FakeBash:
        name = "bash"

        async def ainvoke(self, _tool_input):
            return {"stdout": "", "stderr": "", "exit_code": 0}

    delegate = FakeDelegate()
    writer = FakeWrite()
    bash = FakeBash()
    executable_tools = [delegate, writer, bash]
    monkeypatch.setattr("enterprise_agent.core.agent.nodes.ALL_TOOLS", executable_tools)
    monkeypatch.setattr(
        "enterprise_agent.core.agent.nodes.get_tools_for_permissions",
        lambda *_args, **_kwargs: executable_tools,
    )

    trace_id = "trace-real-delegation"
    user_id = 111
    store = get_trace_store()
    store.start_trace(
        trace_id=trace_id,
        session_id="multi-agent-trace",
        user_id=user_id,
        request_summary="Ask an independent specialist to review the design",
        mode="multi_agent",
    )
    base_state = {
        "session_id": "multi-agent-trace",
        "trace_id": trace_id,
        "user_id": user_id,
        "permissions": ["tools:advanced"],
        "execution_mode": "multi_agent",
        "task_status": "running",
        "pending_tool_calls": [
            {
                "id": "delegate-review",
                "name": "delegate_task",
                "args": {"role": "reviewer", "prompt": "Review the design"},
            },
            {
                "id": "write-demo",
                "name": "write_file",
                "args": {"path": "src/demo.py", "content": "VALUE = 1\n"},
            },
            {
                "id": "validate-demo",
                "name": "bash",
                "args": {"command": "python3 -m py_compile src/demo.py"},
            },
        ],
        "messages": [{"role": "assistant", "content": "Specialist review incorporated."}],
        "changed_files": [],
        "validation_results": [],
    }
    executed = await tool_executor_node(base_state)
    records = {record["tool_name"]: record for record in executed["tool_execution_records"]}
    assert records["delegate_task"]["ok"] is True
    assert records["write_file"]["ok"] is True
    assert records["bash"]["ok"] is True
    assert executed["changed_files"] == ["src/demo.py"]
    assert executed["validation_results"][0]["ok"] is True

    final_state = {**base_state, **executed, "pending_tool_calls": []}
    finalized = await _traced_node("finalize_task", finalize_task_node)(final_state)
    trace = store.get_trace(user_id, trace_id)
    delegate_event = next(
        event
        for event in trace["events"]
        if event["type"] == "tool" and event["name"] == "delegate_task"
    )

    assert finalized["task_status"] == "succeeded"
    assert delegate_event["status"] == "success"
    assert trace["status"] == finalized["task_status"]
    assert trace["events"][-1]["name"] == "task_finished"
    assert trace["events"][-1]["status"] == finalized["task_status"]


async def test_long_failed_shell_output_is_normalized_before_artifact_preview(
    monkeypatch,
    tmp_path,
):
    """A truncated JSON preview must never hide the real non-zero exit code."""
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr(settings, "TOOL_OUTPUT_MAX_CHARS", 2_000)

    class FakeBash:
        name = "bash"

        async def ainvoke(self, _tool_input):
            return json.dumps({
                "stdout": "x" * 20_000,
                "stderr": "FACT_FAILURE=tests failed at the tail",
                "exit_code": 2,
            })

    fake_bash = FakeBash()
    monkeypatch.setattr(
        "enterprise_agent.core.agent.nodes.ALL_TOOLS",
        [fake_bash],
    )
    monkeypatch.setattr(
        "enterprise_agent.core.agent.nodes.get_tools_for_permissions",
        lambda *_args, **_kwargs: [fake_bash],
    )

    result = await tool_executor_node({
        "session_id": "long-shell",
        "trace_id": "trace-long-shell",
        "user_id": 106,
        "permissions": ["tools:shell"],
        "task_status": "running",
        "pending_tool_calls": [{
            "id": "long-call",
            "name": "bash",
            "args": {"command": "pytest -q"},
        }],
        "messages": [],
    })

    record = result["tool_execution_records"][0]
    assert record["ok"] is False
    assert record["status"] == "error"
    assert record["error_code"] == "nonzero_exit"
    assert record["exit_code"] == 2
    assert record["model_truncated"] is True
    assert len(result["tool_results"]["long-call"]) <= settings.TOOL_OUTPUT_MAX_CHARS
    artifact = tmp_path / "user_106" / record["artifact_path"]
    raw = json.loads(artifact.read_text(encoding="utf-8"))
    assert raw["exit_code"] == 2
    assert raw["stderr"] == "FACT_FAILURE=tests failed at the tail"
    assert result["messages"][0]["artifact"]["path"] == record["artifact_path"]


async def test_dict_tool_result_preserves_nonzero_exit_semantics(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))

    class DictBash:
        name = "bash"

        async def ainvoke(self, _tool_input):
            return {"stdout": "", "stderr": "dict failure", "exit_code": 2}

    fake_bash = DictBash()
    monkeypatch.setattr("enterprise_agent.core.agent.nodes.ALL_TOOLS", [fake_bash])
    monkeypatch.setattr(
        "enterprise_agent.core.agent.nodes.get_tools_for_permissions",
        lambda *_args, **_kwargs: [fake_bash],
    )

    result = await tool_executor_node({
        "session_id": "dict-shell",
        "trace_id": "trace-dict-shell",
        "user_id": 107,
        "permissions": ["tools:shell"],
        "task_status": "running",
        "pending_tool_calls": [{
            "id": "dict-call",
            "name": "bash",
            "args": {"command": "pytest -q"},
        }],
        "messages": [],
    })

    record = result["tool_execution_records"][0]
    assert record["status"] == "error"
    assert record["error_code"] == "nonzero_exit"
    assert record["exit_code"] == 2


async def test_large_output_fails_closed_when_artifact_write_fails(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr(settings, "TOOL_OUTPUT_MAX_CHARS", 1_000)

    class FakeRead:
        name = "read_file"

        async def ainvoke(self, _tool_input):
            return "UNRECOVERABLE_RAW=" + ("x" * 5_000)

    def fail_save(*_args, **_kwargs):
        raise OSError("/private/server/path is unavailable")

    fake_read = FakeRead()
    monkeypatch.setattr(ToolArtifactStore, "save", fail_save)
    monkeypatch.setattr("enterprise_agent.core.agent.nodes.ALL_TOOLS", [fake_read])
    monkeypatch.setattr(
        "enterprise_agent.core.agent.nodes.get_tools_for_permissions",
        lambda *_args, **_kwargs: [fake_read],
    )

    result = await tool_executor_node({
        "session_id": "artifact-failure",
        "trace_id": "trace-artifact-failure",
        "user_id": 108,
        "permissions": ["tools:basic"],
        "task_status": "running",
        "pending_tool_calls": [{
            "id": "failed-artifact",
            "name": "read_file",
            "args": {"path": "large.log"},
        }],
        "messages": [],
    })

    record = result["tool_execution_records"][0]
    assert result["task_status"] == "failed"
    assert record["error_code"] == "artifact_write_failed"
    assert record["artifact_error"] == "artifact_write_failed"
    assert "UNRECOVERABLE_RAW" not in result["messages"][0]["content"]
    assert "/private/server/path" not in result["messages"][0]["content"]
    assert result["messages"][0]["artifact"] == {
        "storage_status": "failed",
        "error_code": "artifact_write_failed",
    }


async def test_search_memory_tool_records_the_same_retrieval_trace(monkeypatch, tmp_path):
    """Active tool lookup must be visible in Trace and retrieval counters."""
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
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
