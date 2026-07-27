"""Tests for deterministic long-term-memory admission and quality policy."""

import pytest

from enterprise_agent.memory.accumulator import MemoryAccumulator
from enterprise_agent.memory.policy import (
    MemoryAdmissionPolicy,
    has_durable_pattern_signal,
    memory_quality_status,
)


def _successful_tool(name: str = "read_file") -> dict:
    return {"tool_name": name, "ok": True, "status": "succeeded"}


def test_rejects_failed_tasks_even_when_they_are_important():
    decision = MemoryAdmissionPolicy().decide(
        user_request="修复项目中的 API bug",
        task_status="failed",
        importance=1.0,
        tool_execution_records=[_successful_tool()],
    )

    assert decision.accepted is False
    assert decision.reason == "task_not_succeeded"


def test_rejects_one_off_non_engineering_task():
    decision = MemoryAdmissionPolicy().decide(
        user_request="运用多智能体协作写一篇玄幻短篇小说",
        task_status="succeeded",
        importance=0.95,
        tool_execution_records=[_successful_tool("delegate_task")],
    )

    assert decision.accepted is False
    assert decision.reason == "non_engineering_task"


def test_requires_durable_engineering_evidence():
    policy = MemoryAdmissionPolicy()

    rejected = policy.decide(
        user_request="分析这个代码仓库的架构",
        task_status="succeeded",
        importance=0.9,
        tool_execution_records=[_successful_tool("todo_update")],
    )
    accepted = policy.decide(
        user_request="分析这个代码仓库的架构",
        task_status="succeeded",
        importance=0.9,
        tool_execution_records=[_successful_tool("read_file")],
    )

    assert rejected.reason == "no_durable_evidence"
    assert accepted.accepted is True
    assert accepted.memory_type == "task_outcome"


def test_explicit_memory_request_can_store_a_user_note():
    decision = MemoryAdmissionPolicy().decide(
        user_request="请记住：以后这个项目默认使用 uv 运行测试",
        task_status="succeeded",
        importance=0.1,
    )

    assert decision.accepted is True
    assert decision.memory_type == "user_note"
    assert decision.reason == "explicit_user_request"


@pytest.mark.parametrize(
    ("user_text", "expected"),
    [
        ("这次用多智能体写一篇玄幻小说", False),
        ("我喜欢玄幻小说", True),
        ("这次默认使用 Python", False),
        ("以后请默认使用 Python 和 uv", True),
        ("For this task, use TypeScript", False),
        ("From now on, I prefer TypeScript", True),
    ],
)
def test_durable_pattern_signal_requires_persistent_language(user_text, expected):
    assert has_durable_pattern_signal(user_text) is expected


def test_legacy_records_are_quarantined_without_mutation():
    assert memory_quality_status({"importance": 0.9}) == "legacy"
    assert memory_quality_status({
        "schema_version": 2,
        "quality_status": "active",
    }) == "active"


def test_accumulator_binds_to_current_task_in_existing_session():
    accumulator = MemoryAccumulator()
    state = {
        "current_user_request": "请记住：以后 Python 项目默认使用 uv",
        "pending_tool_calls": [],
    }
    messages = [
        {"role": "user", "content": "检查 Python 版本"},
        {"role": "assistant", "content": "Python 3.11"},
        {"role": "user", "content": state["current_user_request"]},
        {"role": "assistant", "content": "我会记录这个偏好"},
    ]

    result = accumulator.accumulate_round(state, messages, {})

    assert result["user_request"] == state["current_user_request"]


def test_accumulator_legacy_fallback_uses_latest_user_message():
    accumulator = MemoryAccumulator()
    messages = [
        {"role": "user", "content": "第一项旧任务"},
        {"role": "assistant", "content": "旧任务完成"},
        {"role": "user", "content": "第二项当前任务"},
        {"role": "assistant", "content": "当前任务完成"},
    ]

    result = accumulator.accumulate_round(
        {"pending_tool_calls": []},
        messages,
        {},
    )

    assert result["user_request"] == "第二项当前任务"


@pytest.mark.asyncio
async def test_accumulator_rejects_failed_task_before_model_calls():
    accumulator = MemoryAccumulator()
    result = await accumulator.flush(
        {
            "user_request": "修复项目中的 API bug",
            "assistant_responses": ["修改完成"],
            "tool_actions": ["edit_file: api.py"],
            "round_count": 4,
            "start_timestamp": "2026-07-17T00:00:00+00:00",
            "context_summary_pre": "",
        },
        session_id="session-failed",
        user_id=1,
        messages=[],
        task_context={
            "task_status": "failed",
            "tool_execution_records": [_successful_tool("edit_file")],
            "changed_files": ["api.py"],
        },
    )

    assert result["stored"] is False
    assert result["reason"] == "task_not_succeeded"
    assert result["importance"] == 0.0


@pytest.mark.asyncio
async def test_explicit_user_note_is_stored_atomically_without_pattern_duplication(
    monkeypatch,
):
    stored = {}

    class FakeMemory:
        async def search_conversations(self, **kwargs):
            return []

        async def store_conversation(self, **kwargs):
            stored.update(kwargs)
            return "note-1"

    monkeypatch.setattr(
        "enterprise_agent.memory.long_term.get_long_term_memory",
        lambda user_id: FakeMemory(),
    )

    result = await MemoryAccumulator().flush(
        {
            "user_request": "请记住：以后 Python 项目默认使用 uv 和 pytest",
            "assistant_responses": ["收到"],
            "tool_actions": [],
            "round_count": 1,
            "start_timestamp": "2026-07-20T00:00:00+00:00",
            "context_summary_pre": "",
        },
        session_id="session-note",
        user_id=1,
        messages=[],
        task_context={"task_status": "succeeded"},
    )

    assert result["stored"] is True
    assert result["memory_type"] == "user_note"
    assert result["importance"] == 1.0
    assert stored["content"] == "[User Note]\n以后 Python 项目默认使用 uv 和 pytest"
    assert stored["metadata"]["content_format"] == "atomic_note"
    assert stored["metadata"]["retrieval_enabled"] is True
