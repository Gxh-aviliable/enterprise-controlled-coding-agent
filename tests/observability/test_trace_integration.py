"""Trace integration with model, tool and LangGraph node execution."""

import json

import pytest
from langchain_core.messages import AIMessage
from langgraph.errors import GraphInterrupt

from enterprise_agent.core.agent import nodes
from enterprise_agent.core.agent.context import ContextManager, TranscriptManager
from enterprise_agent.core.agent.graph import _traced_node
from enterprise_agent.observability.trace_store import get_trace_store


def _start(user_id: int, trace_id: str):
    get_trace_store().start_trace(
        trace_id=trace_id,
        session_id="session",
        user_id=user_id,
        request_summary="Run a traced task",
    )


async def test_model_call_records_summary_tokens_retries_and_duration(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    _start(31, "trace-model")

    class FakeBoundModel:
        async def ainvoke(self, _messages):
            return AIMessage(
                content="Model result",
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            )

    monkeypatch.setattr(
        nodes,
        "get_llm_with_tools",
        lambda _permissions, _execution_mode="single_agent": FakeBoundModel(),
    )
    result = await nodes.llm_call_node({
        "trace_id": "trace-model",
        "session_id": "session",
        "user_id": 31,
        "permissions": ["tools:basic"],
        "task_status": "running",
        "messages": [{"role": "user", "content": "Explain app.py"}],
        "task_token_count": 0,
        "session_token_count": 20,
        "token_count": 999,
        "round_count": 0,
    })

    trace = get_trace_store().get_trace(31, "trace-model")
    model_event = next(event for event in trace["events"] if event["type"] == "model")
    assert model_event["data"]["output_summary"] == "Model result"
    assert trace["metrics"]["total_tokens"] == 15
    assert result["token_count"] == 15
    assert result["task_token_count"] == 15
    assert result["session_token_count"] == 35


async def test_session_token_budget_stops_before_another_model_call(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr(nodes.settings, "SESSION_TOKEN_BUDGET", 100)
    _start(35, "trace-session-budget")

    result = await nodes.llm_call_node({
        "trace_id": "trace-session-budget",
        "session_id": "session",
        "user_id": 35,
        "permissions": ["tools:basic"],
        "task_status": "running",
        "messages": [{"role": "user", "content": "Continue"}],
        "task_token_count": 10,
        "session_token_count": 100,
        "token_count": 10,
        "round_count": 1,
    })

    assert result["task_status"] == "failed"
    assert result["failure_reason"] == "Session token budget exhausted (100 / 100)."
    trace = get_trace_store().get_trace(35, "trace-session-budget")
    budget_event = next(event for event in trace["events"] if event["type"] == "budget")
    assert budget_event["data"] == {"scope": "session", "used": 100, "limit": 100}


async def test_task_token_budget_stops_before_another_model_call(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr(nodes.settings, "SESSION_TOKEN_BUDGET", 0)
    monkeypatch.setattr(nodes.settings, "TASK_TOKEN_BUDGET", 100)
    _start(40, "trace-task-budget")

    result = await nodes.llm_call_node({
        "trace_id": "trace-task-budget",
        "session_id": "session",
        "user_id": 40,
        "permissions": ["tools:basic"],
        "task_status": "running",
        "messages": [{"role": "user", "content": "Continue"}],
        "task_token_count": 100,
        "session_token_count": 500,
        "token_count": 10,
        "round_count": 1,
    })

    assert result["task_status"] == "failed"
    assert result["failure_reason"] == "Task token budget exhausted (100 / 100)."
    trace = get_trace_store().get_trace(40, "trace-task-budget")
    budget_event = next(event for event in trace["events"] if event["type"] == "budget")
    assert budget_event["data"] == {"scope": "task", "used": 100, "limit": 100}


async def test_zero_token_budgets_disable_cumulative_guard(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr(nodes.settings, "TASK_TOKEN_BUDGET", 0)
    monkeypatch.setattr(nodes.settings, "SESSION_TOKEN_BUDGET", 0)
    _start(41, "trace-unlimited-token-budgets")
    calls = 0

    class FakeBoundModel:
        async def ainvoke(self, _messages):
            nonlocal calls
            calls += 1
            return AIMessage(
                content="Still allowed",
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            )

    monkeypatch.setattr(
        nodes,
        "get_llm_with_tools",
        lambda _permissions, _execution_mode="single_agent": FakeBoundModel(),
    )
    result = await nodes.llm_call_node({
        "trace_id": "trace-unlimited-token-budgets",
        "session_id": "session",
        "user_id": 41,
        "permissions": ["tools:basic"],
        "task_status": "running",
        "messages": [{"role": "user", "content": "Continue"}],
        "task_token_count": 10_000_000,
        "session_token_count": 20_000_000,
        "token_count": 10,
        "round_count": 1,
    })

    assert calls == 1
    assert result["task_token_count"] == 10_000_015
    assert result["session_token_count"] == 20_000_015
    assert result["should_end_after_save"] is True


async def test_provider_context_overflow_requests_one_compression_recovery(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    _start(38, "trace-context-overflow")

    class OverflowModel:
        async def ainvoke(self, _messages):
            raise RuntimeError("400 maximum context length exceeded")

    monkeypatch.setattr(
        nodes,
        "get_llm_with_tools",
        lambda _permissions, _execution_mode="single_agent": OverflowModel(),
    )
    result = await nodes.llm_call_node({
        "trace_id": "trace-context-overflow",
        "session_id": "session",
        "user_id": 38,
        "permissions": ["tools:basic"],
        "task_status": "running",
        "messages": [{"role": "user", "content": "oversized input"}],
        "task_token_count": 0,
        "session_token_count": 0,
        "token_count": 1,
        "round_count": 0,
        "context_overflow_recovery_attempts": 0,
    })

    assert result["should_compress"] is True
    assert result["context_overflow_recovery_attempts"] == 1
    assert result["token_count"] >= nodes.get_context_manager().token_threshold
    trace = get_trace_store().get_trace(38, "trace-context-overflow")
    event = next(
        item for item in trace["events"]
        if item["name"] == "provider_context_overflow"
    )
    assert event["data"]["recovery_attempt"] == 1


async def test_tool_executor_records_permission_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    _start(32, "trace-tool")
    await nodes.tool_executor_node({
        "trace_id": "trace-tool",
        "session_id": "session",
        "user_id": 32,
        "permissions": ["tools:basic"],
        "task_status": "running",
        "pending_tool_calls": [{
            "id": "call-1",
            "name": "bash",
            "args": {"command": "echo denied"},
        }],
    })

    trace = get_trace_store().get_trace(32, "trace-tool")
    tool_event = next(event for event in trace["events"] if event["type"] == "tool")
    assert tool_event["status"] == "blocked"
    assert tool_event["data"]["error_code"] == "permission_denied"
    assert trace["metrics"]["tool_failures"] == 1


async def test_tool_trace_links_artifact_without_copying_raw_output(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr(nodes.settings, "TOOL_OUTPUT_MAX_CHARS", 1_500)
    _start(36, "trace-artifact")

    class FakeRead:
        name = "read_file"

        async def ainvoke(self, _tool_input):
            return "api_key=must-not-enter-trace\n" + ("raw-evidence-" * 500)

    fake_read = FakeRead()
    monkeypatch.setattr(nodes, "ALL_TOOLS", [fake_read])
    monkeypatch.setattr(
        nodes,
        "get_tools_for_permissions",
        lambda *_args, **_kwargs: [fake_read],
    )

    result = await nodes.tool_executor_node({
        "trace_id": "trace-artifact",
        "session_id": "session",
        "user_id": 36,
        "permissions": ["tools:basic"],
        "task_status": "running",
        "messages": [],
        "pending_tool_calls": [{
            "id": "artifact-call",
            "name": "read_file",
            "args": {"path": "large.log"},
        }],
    })

    record = result["tool_execution_records"][0]
    trace = get_trace_store().get_trace(36, "trace-artifact")
    tool_event = next(event for event in trace["events"] if event["type"] == "tool")
    assert tool_event["data"]["artifact_path"] == record["artifact_path"]
    assert tool_event["data"]["artifact_sha256"] == record["artifact_sha256"]
    assert tool_event["data"]["model_truncated"] is True
    serialized_trace = json.dumps(trace, ensure_ascii=False)
    assert "must-not-enter-trace" not in serialized_trace
    artifact = tmp_path / "user_36" / record["artifact_path"]
    assert "must-not-enter-trace" not in artifact.read_text(encoding="utf-8")


async def test_auto_compact_records_summary_cost_and_context_event(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr(nodes.settings, "MODEL_CONTEXT_WINDOW_TOKENS", 25_000)
    monkeypatch.setattr(nodes.settings, "CONTEXT_COMPRESSION_RATIO", 0.8)
    _start(37, "trace-auto-compact")

    class FakeSummaryModel:
        async def ainvoke(self, _messages):
            return AIMessage(
                content="Operational summary",
                usage_metadata={"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
            )

    manager = ContextManager(
        llm=FakeSummaryModel(),
        transcript_manager=TranscriptManager(tmp_path / "user_37"),
    )
    monkeypatch.setattr(nodes, "get_context_manager", lambda: manager)
    result = await nodes.compress_context_node({
        "trace_id": "trace-auto-compact",
        "session_id": "session",
        "user_id": 37,
        "permissions": ["tools:basic"],
        "execution_mode": "single_agent",
        "task_status": "running",
        "token_count": 20_000,
        "task_token_count": 10,
        "session_token_count": 20,
        "messages": [{"role": "user", "content": "HISTORY_FACT=" + ("z" * 500)}],
        "current_user_request": "continue safely",
        "tool_execution_records": [],
        "todos": [],
        "changed_files": [],
        "validation_results": [],
    })

    trace = get_trace_store().get_trace(37, "trace-auto-compact")
    model_event = next(
        event for event in trace["events"]
        if event["type"] == "model" and event["name"] == "context_summary"
    )
    context_event = next(
        event for event in trace["events"]
        if event["type"] == "context" and event["name"] == "auto_compact"
    )
    assert model_event["data"]["total_tokens"] == 25
    assert trace["metrics"]["model_calls"] == 1
    assert trace["metrics"]["total_tokens"] == 25
    assert context_event["data"]["transcript_path"] == result["transcript_path"]
    assert result["task_token_count"] == 35
    assert result["session_token_count"] == 45
    assert "HISTORY_FACT=" not in json.dumps(trace, ensure_ascii=False)


async def test_finalize_wrapper_records_node_then_finishes_trace(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    _start(33, "trace-final")

    async def fake_finalize(_state):
        return {
            "task_status": "succeeded",
            "execution_phase": "summarizing",
            "failure_reason": None,
        }

    wrapped = _traced_node("finalize_task", fake_finalize)
    await wrapped({
        "trace_id": "trace-final",
        "user_id": 33,
        "task_status": "running",
        "messages": [{"role": "assistant", "content": "All checks passed"}],
    })

    trace = get_trace_store().get_trace(33, "trace-final")
    assert trace["status"] == "succeeded"
    assert trace["result_summary"] == "All checks passed"
    assert [event["name"] for event in trace["events"]][-2:] == [
        "finalize_task",
        "task_finished",
    ]


async def test_finalize_wrapper_excludes_thinking_from_trace_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    _start(39, "trace-final-thinking")

    async def fake_finalize(_state):
        return {
            "task_status": "succeeded",
            "execution_phase": "summarizing",
            "failure_reason": None,
        }

    wrapped = _traced_node("finalize_task", fake_finalize)
    await wrapped({
        "trace_id": "trace-final-thinking",
        "user_id": 39,
        "task_status": "running",
        "messages": [
            {"role": "assistant", "content": "Earlier visible progress"},
            {
                "role": "assistant",
                "content": [{
                    "type": "thinking",
                    "thinking": "private reasoning must not become the result",
                    "signature": "sig",
                }],
            },
        ],
    })

    trace = get_trace_store().get_trace(39, "trace-final-thinking")
    assert trace["result_summary"] == "Earlier visible progress"
    assert "private reasoning" not in json.dumps(trace, ensure_ascii=False)


async def test_human_interrupt_is_traced_as_waiting_control_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    _start(34, "trace-interrupt")

    async def fake_interrupt(_state):
        raise GraphInterrupt()

    wrapped = _traced_node("tool_confirm", fake_interrupt)
    with pytest.raises(GraphInterrupt):
        await wrapped({
            "trace_id": "trace-interrupt",
            "user_id": 34,
            "task_status": "waiting_confirmation",
            "execution_phase": "executing",
        })

    trace = get_trace_store().get_trace(34, "trace-interrupt")
    interrupt_event = trace["events"][-1]
    assert interrupt_event["status"] == "interrupted"
    assert trace["status"] == "waiting_confirmation"
    assert trace["error"] is None
