"""Trace integration with model, tool and LangGraph node execution."""

import pytest
from langchain_core.messages import AIMessage
from langgraph.errors import GraphInterrupt

from enterprise_agent.core.agent import nodes
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
    await nodes.llm_call_node({
        "trace_id": "trace-model",
        "session_id": "session",
        "user_id": 31,
        "permissions": ["tools:basic"],
        "task_status": "running",
        "messages": [{"role": "user", "content": "Explain app.py"}],
        "task_token_count": 0,
        "token_count": 0,
        "round_count": 0,
    })

    trace = get_trace_store().get_trace(31, "trace-model")
    model_event = next(event for event in trace["events"] if event["type"] == "model")
    assert model_event["data"]["output_summary"] == "Model result"
    assert trace["metrics"]["total_tokens"] == 15


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
