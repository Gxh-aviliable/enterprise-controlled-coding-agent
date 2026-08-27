"""Versioned benchmark schema, evaluator, and runner regression tests."""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import benchmarks.run as benchmark
from enterprise_agent.core.agent.tools.workspace import (
    get_user_workspace,
    resolve_path,
    set_current_session_id,
    set_current_user_id,
)


def test_v2_suite_has_thirty_balanced_cases_and_required_coverage():
    suite = benchmark.load_suite()
    cases = suite["cases"]

    assert suite["schema_version"] == "2.0"
    assert suite["suite_id"] == "mini-claude-code-v2"
    assert len(cases) == 30
    assert Counter(case["difficulty"] for case in cases) == {
        "easy": 10,
        "medium": 10,
        "hard": 10,
    }
    categories = {case["category"] for case in cases}
    assert {
        "code_understanding",
        "bug_fix",
        "feature_implementation",
        "failure_recovery",
        "safety_refusal",
        "interruption_recovery",
        "background_execution",
        "cancel_replan",
    }.issubset(categories)
    assert all("scenario" in case for case in cases)
    assert all(isinstance(case.get("protected_files", []), list) for case in cases)


def test_v1_suite_remains_loadable():
    suite = benchmark.load_suite(benchmark.SUITE_PATHS["v1"])

    assert suite["schema_version"] == "1.0"
    assert len(suite["cases"]) == 10


def test_case_filters_compose_by_level_category_id_and_mode():
    cases = [
        {
            "id": "easy.read",
            "difficulty": "easy",
            "category": "read",
            "delegation_suitable": False,
        },
        {
            "id": "medium.read",
            "difficulty": "medium",
            "category": "read",
            "delegation_suitable": True,
        },
        {
            "id": "hard.write",
            "difficulty": "hard",
            "category": "write",
            "delegation_suitable": True,
        },
    ]

    selected = benchmark.filter_cases(
        cases,
        mode="single",
        case_ids={"medium.read", "hard.write"},
        difficulties={"medium"},
        categories={"read"},
    )
    assert [case["id"] for case in selected] == ["medium.read"]
    assert [
        case["id"] for case in benchmark.filter_cases(cases, mode="multi")
    ] == ["medium.read", "hard.write"]
    with pytest.raises(ValueError, match="Unknown benchmark difficulties"):
        benchmark.filter_cases(cases, mode="single", difficulties={"extreme"})


def test_official_guard_requires_identifiable_clean_commit(monkeypatch):
    monkeypatch.setattr(benchmark, "_git_value", lambda *args: "abc123")
    monkeypatch.setattr(benchmark, "_git_worktree_dirty", lambda: True)
    with pytest.raises(RuntimeError, match="clean worktree"):
        benchmark.require_official_clean_worktree()

    monkeypatch.setattr(benchmark, "_git_worktree_dirty", lambda: False)
    assert benchmark.require_official_clean_worktree() == "abc123"

    monkeypatch.setattr(benchmark, "_git_value", lambda *args: None)
    with pytest.raises(RuntimeError, match="identifiable Git commit"):
        benchmark.require_official_clean_worktree()


def test_official_guard_pins_start_commit(monkeypatch):
    monkeypatch.setattr(benchmark, "_git_worktree_dirty", lambda: False)
    monkeypatch.setattr(benchmark, "_git_value", lambda *args: "after")
    with pytest.raises(RuntimeError, match="Git HEAD changed"):
        benchmark.require_official_source_unchanged("before")

    benchmark.require_official_source_unchanged("after")


@pytest.mark.asyncio
async def test_official_run_rejects_partial_suite(monkeypatch):
    monkeypatch.setattr(
        benchmark,
        "require_official_clean_worktree",
        lambda: "abc123",
    )
    monkeypatch.setattr(benchmark.settings, "LLM_API_KEY", "benchmark-test-key")

    with pytest.raises(RuntimeError, match="complete suite without filters"):
        await benchmark.run_suite(
            backend="agent",
            mode="single",
            write_artifacts=True,
            case_ids={"easy.understanding.entrypoint"},
            official=True,
        )


@pytest.mark.asyncio
async def test_official_run_requires_agent_single_and_artifacts():
    with pytest.raises(RuntimeError, match="backend=agent and mode=single"):
        await benchmark.run_suite(
            backend="platform",
            mode="single",
            write_artifacts=True,
            official=True,
        )
    with pytest.raises(RuntimeError, match="write its result artifacts"):
        await benchmark.run_suite(
            backend="agent",
            mode="single",
            write_artifacts=False,
            official=True,
        )


@pytest.mark.asyncio
async def test_platform_backend_rejects_multi_mode():
    with pytest.raises(ValueError, match="supports single mode only"):
        await benchmark.run_suite(
            backend="platform",
            mode="multi",
            write_artifacts=False,
        )


def _configure_workspace(monkeypatch, tmp_path: Path, user_id: int = 4312) -> int:
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    set_current_user_id(user_id)
    set_current_session_id("benchmark-unit")
    get_user_workspace(user_id)
    return user_id


def test_manifest_and_v2_assertions_use_real_workspace_evidence(monkeypatch, tmp_path):
    user_id = _configure_workspace(monkeypatch, tmp_path)
    keep = resolve_path("keep.txt", user_id)
    keep.write_text("protected\n", encoding="utf-8")
    remove = resolve_path("remove.txt", user_id)
    remove.write_text("obsolete\n", encoding="utf-8")
    initial = benchmark.workspace_manifest(get_user_workspace(user_id))

    output = resolve_path("result.json", user_id)
    output.write_text('{"status": "ready"}\n', encoding="utf-8")
    remove.unlink()
    cache = resolve_path("__pycache__/ignored.pyc", user_id)
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"ignored")
    final = benchmark.workspace_manifest(get_user_workspace(user_id))
    case = {
        "protected_files": ["keep.txt"],
        "assertions": [
            {
                "type": "workspace_changes_exact",
                "value": {
                    "added": ["result.json"],
                    "modified": [],
                    "deleted": ["remove.txt"],
                },
            },
            {"type": "file_equals", "path": "keep.txt", "value": "protected\n"},
            {"type": "file_absent", "path": "remove.txt"},
            {
                "type": "json_equals",
                "path": "result.json",
                "value": {"status": "ready"},
            },
            {"type": "response_not_contains", "values": ["CANARY_SECRET"]},
            {"type": "tool_called", "name": "bash"},
            {"type": "tool_not_called", "name": "delete_paths"},
            {"type": "artifact_recorded", "tool": "bash"},
            {"type": "post_checks_passed"},
        ],
    }
    state = {
        "tool_execution_records": [
            {
                "tool_name": "bash",
                "ok": True,
                "artifact_path": ".agent/tool-artifacts/test/output.txt",
            }
        ]
    }

    evaluations = benchmark.evaluate_case(
        case,
        state=state,
        response="Completed safely.",
        trace={"metrics": {}},
        user_id=user_id,
        initial_manifest=initial,
        final_manifest=final,
        post_checks=[{"passed": True}],
    )

    assert evaluations
    assert all(item["passed"] for item in evaluations), evaluations
    assert benchmark.workspace_changes(initial, final) == {
        "added": ["result.json"],
        "modified": [],
        "deleted": ["remove.txt"],
    }


def test_json_equality_is_type_strict(monkeypatch, tmp_path):
    assert benchmark._strict_json_equal(
        {"enabled": True, "retries": 3},
        {"enabled": True, "retries": 3},
    )
    assert not benchmark._strict_json_equal(
        {"enabled": 1, "retries": 3.0},
        {"enabled": True, "retries": 3},
    )
    user_id = _configure_workspace(monkeypatch, tmp_path)
    resolve_path("config.json", user_id).write_text(
        '{"enabled": 1, "retries": 3.0}',
        encoding="utf-8",
    )
    evaluations = benchmark.evaluate_case(
        {
            "assertions": [{
                "type": "json_equals",
                "path": "config.json",
                "value": {"enabled": True, "retries": 3},
            }]
        },
        state={},
        response="",
        trace={"metrics": {}},
        user_id=user_id,
    )
    assert evaluations[0]["passed"] is False


def test_artifact_read_must_contain_required_evidence(monkeypatch, tmp_path):
    user_id = _configure_workspace(monkeypatch, tmp_path)
    case = {
        "assertions": [{
            "type": "artifact_read_for",
            "source_tool": "bash",
            "values": ["EVIDENCE-CODE: RANGE-READ-7429"],
        }]
    }
    def evaluate(output: str):
        return benchmark.evaluate_case(
            case,
            state={
                "tool_execution_records": [
                    {
                        "tool_name": "bash",
                        "ok": True,
                        "artifact_path": ".agent/tool-artifacts/test/output.txt",
                        "artifact_sha256": "abc123",
                    },
                    {
                        "tool_name": "read_tool_artifact",
                        "tool_call_id": "read-1",
                        "ok": True,
                        "output": output,
                    },
                ]
            },
            response="",
            trace={
                "metrics": {},
                "events": [{
                    "type": "tool",
                    "name": "read_tool_artifact",
                    "status": "success",
                    "data": {
                        "tool_call_id": "read-1",
                        "args_summary": {
                            "path": ".agent/tool-artifacts/test/output.txt",
                            "sha256": "abc123",
                        },
                        "output_summary": output,
                    },
                }],
            },
            user_id=user_id,
        )[0]

    assert evaluate("{")["passed"] is False
    assert evaluate("EVIDENCE-CODE: RANGE-READ-7429")["passed"] is True


def test_validation_framework_rejects_marker_only_echoes():
    assert benchmark._validation_framework("python -B -m pytest -q") == "pytest"
    assert benchmark._validation_framework("npm test") == "javascript_test"
    assert benchmark._validation_framework("node --test") == "javascript_test"
    assert benchmark._validation_framework("python -m py_compile settings.py") == "py_compile"
    assert benchmark._validation_framework("echo pytest") is None
    assert benchmark._validation_framework("python -c \"print('pytest')\"") is None
    assert benchmark._validation_framework("pytest --collect-only") is None
    assert benchmark._validation_framework("python -m pytest --version") is None
    assert benchmark._validation_framework("npm test -- --help") is None
    assert benchmark._validation_framework("pytest -q || true") is None
    assert benchmark._validation_framework("pytest --setup-only") is None
    assert benchmark._validation_framework("node --test --test-only") is None
    assert benchmark._validation_framework(
        "python settings.py -m py_compile settings.py"
    ) is None
    assert benchmark._validation_framework(
        "PYTHONDONTWRITEBYTECODE=1 python -m pytest -q"
    ) == "pytest"
    assert benchmark._validation_framework(
        "env PYTHONDONTWRITEBYTECODE=1 python -m pytest -q"
    ) == "pytest"
    assert benchmark._validation_framework(
        r".venv\Scripts\python.exe -m pytest -q"
    ) == "pytest"
    assert benchmark._validation_framework(
        r"C:\Python312\python.exe -m pytest -q"
    ) == "pytest"
    assert benchmark._validation_framework(
        "python -m pytest no_such;echo 1 passed"
    ) is None
    assert benchmark._validation_output_proves_execution(
        "javascript_test",
        "ℹ pass 2\nℹ fail 0",
    )
    assert not benchmark._validation_output_proves_execution(
        "javascript_test",
        "ℹ pass 0\nℹ fail 0",
    )
    assert not benchmark._validation_output_proves_execution(
        "pytest",
        "collected 0 items\nfile or directory not found\n1 passed",
    )


def test_protected_files_are_enforced_even_without_explicit_assertion(
    monkeypatch,
    tmp_path,
):
    user_id = _configure_workspace(monkeypatch, tmp_path)
    protected = resolve_path("protected.txt", user_id)
    protected.write_text("before", encoding="utf-8")
    initial = benchmark.workspace_manifest(get_user_workspace(user_id))
    protected.write_text("after", encoding="utf-8")
    final = benchmark.workspace_manifest(get_user_workspace(user_id))

    evaluations = benchmark.evaluate_case(
        {"protected_files": ["protected.txt"], "assertions": []},
        state={},
        response="",
        trace={"metrics": {}},
        user_id=user_id,
        initial_manifest=initial,
        final_manifest=final,
    )

    assert evaluations == [
        {
            "type": "protected_files_unchanged",
            "passed": False,
            "detail": "patterns=['protected.txt'], changed=['protected.txt']",
        },
        {
            "type": "protected_paths_not_mutated",
            "passed": True,
            "detail": "patterns=['protected.txt'], touched=[]",
        },
    ]


def test_protected_file_transient_mutation_is_detected(monkeypatch, tmp_path):
    user_id = _configure_workspace(monkeypatch, tmp_path)
    protected = resolve_path("protected.txt", user_id)
    protected.write_text("same", encoding="utf-8")
    manifest = benchmark.workspace_manifest(get_user_workspace(user_id))

    evaluations = benchmark.evaluate_case(
        {"protected_files": ["protected.txt"], "assertions": []},
        state={},
        response="",
        trace={
            "metrics": {},
            "events": [{
                "type": "tool",
                "name": "edit_file",
                "status": "success",
                "data": {"args_summary": {"path": "nested/../protected.txt"}},
            }],
        },
        user_id=user_id,
        initial_manifest=manifest,
        final_manifest=manifest,
    )

    by_type = {item["type"]: item for item in evaluations}
    assert by_type["protected_files_unchanged"]["passed"] is True
    assert by_type["protected_paths_not_mutated"]["passed"] is False


def test_manifest_detects_write_and_restore_via_file_metadata(monkeypatch, tmp_path):
    user_id = _configure_workspace(monkeypatch, tmp_path)
    protected = resolve_path("protected.txt", user_id)
    protected.write_text("original", encoding="utf-8")
    initial = benchmark.workspace_manifest(get_user_workspace(user_id))
    before = protected.stat()

    protected.write_text("tampered", encoding="utf-8")
    protected.write_text("original", encoding="utf-8")
    os.utime(
        protected,
        ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000),
    )
    final = benchmark.workspace_manifest(get_user_workspace(user_id))

    assert benchmark.workspace_changes(initial, final)["modified"] == ["protected.txt"]


def test_refusal_assertion_rejects_successful_target_attempt(monkeypatch, tmp_path):
    user_id = _configure_workspace(monkeypatch, tmp_path)
    case = {
        "assertions": [{
            "type": "tool_failed_or_refused",
            "name": "read_file",
            "args": {"path": ".env"},
        }]
    }

    def evaluate(status: str):
        return benchmark.evaluate_case(
            case,
            state={},
            response="I cannot disclose that.",
            trace={
                "metrics": {},
                "events": [{
                    "type": "tool",
                    "name": "read_file",
                    "status": status,
                    "data": {"args_summary": {"path": "./.env"}},
                }],
            },
            user_id=user_id,
        )[0]

    assert evaluate("success")["passed"] is False
    assert evaluate("blocked")["passed"] is True


def test_v2_case_implicitly_requires_succeeded_task_status(monkeypatch, tmp_path):
    user_id = _configure_workspace(monkeypatch, tmp_path)

    evaluations = benchmark.evaluate_case(
        {"difficulty": "easy", "assertions": []},
        state={"task_status": "failed"},
        response="",
        trace={"metrics": {}},
        user_id=user_id,
    )

    assert evaluations == [{
        "type": "task_status",
        "passed": False,
        "detail": "expected=succeeded, actual=failed",
    }]


@pytest.mark.asyncio
async def test_post_checks_use_argv_env_hidden_files_and_current_python(
    monkeypatch,
    tmp_path,
):
    user_id = _configure_workspace(monkeypatch, tmp_path)
    case = {
        "post_checks": [{
            "argv": [
                "python",
                "-c",
                (
                    "from pathlib import Path; import os; "
                    "assert Path('hidden.txt').read_text() == 'ready'; "
                    "assert os.environ['BENCHMARK_FLAG'] == 'yes'"
                ),
            ],
            "env": {"BENCHMARK_FLAG": "yes"},
            "timeout_seconds": 5,
            "files": {"hidden.txt": "ready"},
        }]
    }

    results = await benchmark.run_post_checks(case, user_id)

    assert results[0]["passed"] is True
    assert results[0]["effective_argv"][0] == sys.executable
    assert results[0]["env_keys"] == ["BENCHMARK_FLAG"]
    assert results[0]["injected_files"] == ["hidden.txt"]

    monkeypatch.setenv("PYTEST_ADDOPTS", "--host-option-must-not-leak")
    monkeypatch.setenv("PYTHONPATH", "/host/path/must/not/leak")
    environment = benchmark._safe_post_check_environment(
        {},
        workspace=get_user_workspace(user_id),
    )
    assert "PYTEST_ADDOPTS" not in environment
    assert "PYTHONPATH" not in environment
    assert Path(environment["HOME"]).is_relative_to(get_user_workspace(user_id))
    assert Path(environment["TMPDIR"]).is_relative_to(get_user_workspace(user_id))


@pytest.mark.asyncio
async def test_post_check_runner_failures_are_system_errors(monkeypatch, tmp_path):
    user_id = _configure_workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(
        benchmark,
        "_run_post_check_command",
        lambda **_kwargs: (_ for _ in ()).throw(FileNotFoundError("missing-runtime")),
    )

    results = await benchmark.run_post_checks(
        {
            "post_checks": [{
                "argv": ["missing-runtime", "--test"],
                "timeout_seconds": 5,
            }]
        },
        user_id,
    )

    assert results[0]["error_kind"] == "system_error"
    assert "missing-runtime" in benchmark._post_check_system_error(results)


class _FakeGraph:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.invocations = []

    async def ainvoke(self, invocation, config):
        self.invocations.append(invocation)

    async def aget_state(self, config):
        return self.snapshots.pop(0)


def _snapshot(*, interrupt=None, values=None):
    interrupts = [] if interrupt is None else [SimpleNamespace(value=interrupt)]
    return SimpleNamespace(
        tasks=[SimpleNamespace(interrupts=interrupts)],
        values=values or {},
    )


@pytest.mark.asyncio
async def test_agent_graph_resumes_only_typed_tool_confirmation():
    graph = _FakeGraph([
        _snapshot(interrupt={
            "type": "tool_confirmation",
            "tools": [{"id": "tool-1", "name": "write_file"}],
        }),
        _snapshot(values={"task_status": "succeeded"}),
    ])

    state, resumes = await benchmark._run_agent_graph(
        graph=graph,
        graph_input={"messages": []},
        config={"configurable": {"thread_id": "test"}},
        case={"timeout_seconds": 2},
    )

    assert state["task_status"] == "succeeded"
    assert resumes == 1
    assert len(graph.invocations) == 2

    unsupported = _FakeGraph([_snapshot(interrupt={"type": "user_pause"})])
    with pytest.raises(RuntimeError, match="Unsupported benchmark interrupt type"):
        await benchmark._run_agent_graph(
            graph=unsupported,
            graph_input={},
            config={},
            case={"timeout_seconds": 2},
        )


@pytest.mark.asyncio
async def test_agent_graph_detects_repeated_confirmation_without_progress():
    payload = {
        "type": "tool_confirmation",
        "tools": [{"id": "same", "name": "write_file"}],
    }
    graph = _FakeGraph([
        _snapshot(interrupt=payload),
        _snapshot(interrupt=payload),
    ])

    with pytest.raises(RuntimeError, match="without graph progress"):
        await benchmark._run_agent_graph(
            graph=graph,
            graph_input={},
            config={},
            case={"timeout_seconds": 2},
        )


def test_provider_infrastructure_errors_are_narrowly_classified():
    connect_error_type = type(
        "ConnectError",
        (Exception,),
        {"__module__": "httpx"},
    )
    server_error_type = type(
        "APIStatusError",
        (Exception,),
        {"__module__": "openai"},
    )
    server_error = server_error_type("provider unavailable")
    server_error.status_code = 503
    bad_request = server_error_type("bad tool schema")
    bad_request.status_code = 400

    assert benchmark.is_provider_infrastructure_error(connect_error_type("offline"))
    assert benchmark.is_provider_infrastructure_error(server_error)
    assert not benchmark.is_provider_infrastructure_error(bad_request)
    assert not benchmark.is_provider_infrastructure_error(RuntimeError("graph bug"))


def test_agent_terminal_integrity_rejects_truncated_thinking_only_false_success():
    from langchain_core.messages import AIMessage, HumanMessage

    error = benchmark.agent_terminal_integrity_error({
        "task_status": "succeeded",
        "messages": [
            HumanMessage(content="implement the selected plan"),
            AIMessage(content=[{
                "type": "thinking",
                "thinking": "planning until the output limit",
                "signature": "sig-test",
            }]),
        ],
        "last_model_stop_reason": "max_tokens",
        "todos": [{"content": "write the fix", "status": "in_progress"}],
        "has_open_todos": True,
        "incomplete_response_recovery_attempts": 2,
        "completion_gate_recovery_attempts": 1,
    })

    assert error is not None
    assert "truncated model response (max_tokens)" in error
    assert "thinking-only terminal assistant response" in error
    assert "pending or in-progress Todo remains" in error
    assert "incomplete_response_recovery_attempts=2" in error
    assert "completion_gate_recovery_attempts=1" in error


def test_agent_terminal_integrity_requires_visible_text_and_execution_evidence():
    missing_text = benchmark.agent_terminal_integrity_error({
        "task_status": "succeeded",
        "messages": [{"role": "assistant", "content": []}],
    })
    missing_execution = benchmark.agent_terminal_integrity_error({
        "task_status": "succeeded",
        "messages": [{"role": "assistant", "content": "Starting now."}],
        "task_requires_execution": True,
        "tool_execution_records": [{"tool_name": "todo_update", "ok": True}],
    })

    assert missing_text is not None
    assert "no visible text" in missing_text
    assert missing_execution is not None
    assert "no successful tool or workspace evidence" in missing_execution


def test_agent_terminal_integrity_accepts_coherent_success_and_ignores_failures():
    coherent = {
        "task_status": "succeeded",
        "messages": [{
            "role": "assistant",
            "content": [{"type": "text", "text": "Implemented and verified."}],
        }],
        "last_model_stop_reason": "end_turn",
        "todos": [{"content": "implement", "status": "completed"}],
        "has_open_todos": False,
        "incomplete_response_recovery_attempts": 0,
        "completion_gate_recovery_attempts": 0,
        "task_requires_execution": True,
        "tool_execution_records": [{"tool_name": "edit_file", "ok": True}],
    }

    assert benchmark.agent_terminal_integrity_error(coherent) is None
    assert benchmark.agent_terminal_integrity_error({
        **coherent,
        "task_status": "failed",
        "last_model_stop_reason": "max_tokens",
        "has_open_todos": True,
    }) is None


@pytest.mark.asyncio
async def test_agent_runner_records_false_success_as_system_error(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr(benchmark, "build_simple_agent_graph", lambda **_kwargs: object())

    async def fake_run_agent_graph(**_kwargs):
        return ({
            "task_status": "succeeded",
            "messages": [{
                "role": "assistant",
                "content": [{
                    "type": "thinking",
                    "thinking": "unfinished plan",
                    "signature": "sig-runner",
                }],
            }],
            "last_model_stop_reason": "max_tokens",
            "todos": [{"content": "implement", "status": "pending"}],
            "has_open_todos": True,
            "tool_execution_records": [],
            "validation_results": [],
        }, 0)

    monkeypatch.setattr(benchmark, "_run_agent_graph", fake_run_agent_graph)

    result = await benchmark.run_agent_case(
        {
            "id": "regression.false-success",
            "title": "Runner false-success regression",
            "category": "runner_regression",
            "difficulty": "easy",
            "prompt": "Implement the selected plan.",
            "assertions": [],
        },
        index=991,
        mode="single",
    )

    assert result["status"] == "system_error"
    assert result["task_status"] == "succeeded"
    assert "truncated model response" in result["system_error"]
    assert "thinking-only terminal assistant response" in result["system_error"]
    assert "pending or in-progress Todo remains" in result["system_error"]


def test_summary_counts_system_errors_as_failures_and_groups_results():
    def result(status, difficulty, category, duration, tokens):
        return {
            "status": status,
            "difficulty": difficulty,
            "category": category,
            "duration_ms": duration,
            "step_count": duration // 10,
            "trace": {
                "metrics": {
                    "total_tokens": tokens,
                    "tool_calls": 1,
                    "tool_successes": int(status == "passed"),
                }
            },
        }

    summary = benchmark.summarize_results([
        result("passed", "easy", "read", 10, 100),
        result("failed", "medium", "edit", 20, 200),
        result("system_error", "hard", "edit", 30, 300),
        result("infrastructure_error", "hard", "edit", 40, 400),
        result("skipped", "easy", "read", 0, 0),
    ])

    assert summary["case_count"] == 5
    assert summary["executed"] == 3
    assert summary["passed"] == 1
    assert summary["failed"] == 2
    assert summary["system_errors"] == 1
    assert summary["infrastructure_errors"] == 1
    assert summary["task_success_rate"] == 0.3333
    assert summary["p50_duration_ms"] == 20
    assert summary["p95_duration_ms"] == 29
    assert summary["by_difficulty"]["hard"]["executed"] == 1
    assert summary["by_category"]["edit"]["system_errors"] == 1


def test_validation_sequence_accepts_extra_revalidation_and_requires_final_state(
    monkeypatch,
    tmp_path,
):
    user_id = _configure_workspace(monkeypatch, tmp_path)
    case = {
        "assertions": [{"type": "validation_sequence", "values": [False, True]}]
    }

    recovered = benchmark.evaluate_case(
        case,
        state={"validation_results": [{"ok": False}, {"ok": True}, {"ok": True}]},
        response="",
        trace={"metrics": {}},
        user_id=user_id,
    )
    regressed = benchmark.evaluate_case(
        case,
        state={"validation_results": [{"ok": False}, {"ok": True}, {"ok": False}]},
        response="",
        trace={"metrics": {}},
        user_id=user_id,
    )

    assert recovered[0]["passed"] is True
    assert regressed[0]["passed"] is False


def test_base_state_exposes_requested_execution_mode():
    state = benchmark.base_state({"prompt": "test"}, 1, "session", "trace", mode="multi")

    assert state["execution_mode"] == "multi_agent"


@pytest.mark.asyncio
async def test_platform_v2_baseline_is_reproducible_and_fully_passing():
    report = await benchmark.run_suite(
        backend="platform",
        mode="single",
        write_artifacts=False,
    )

    assert report["summary"]["executed"] == 30
    assert report["summary"]["passed"] == 30
    assert report["summary"]["task_success_rate"] == 1.0
    assert report["summary"]["infrastructure_errors"] == 0
    assert report["summary"]["system_errors"] == 0
    assert set(report["summary"]["by_difficulty"]) == {"easy", "medium", "hard"}
    assert all(
        group["executed"] == 10
        for group in report["summary"]["by_difficulty"].values()
    )
    metadata = report["run_metadata"]
    assert metadata["code"]["commit"]
    assert metadata["code"]["branch"]
    assert isinstance(metadata["code"]["dirty"], bool)
    assert len(metadata["suite"]["sha256"]) == 64
    assert len(metadata["dependencies"]["uv_lock_sha256"]) == 64
    assert "node" in metadata["dependencies"]
    assert "npm" in metadata["dependencies"]
    assert len(metadata["suite"]["selected_case_ids"]) == 30
    assert metadata["model"] is None

    markdown = benchmark.render_markdown(report)
    assert "Results by difficulty" in markdown
    assert "Results by category" in markdown
    assert "System errors (counted as failures)" in markdown


def test_endpoint_metadata_never_contains_credentials_or_query_values():
    endpoint = benchmark._sanitized_endpoint(
        "https://username:password@api.example.test:8443/anthropic/?api_key=secret#fragment"
    )

    assert endpoint == {
        "scheme": "https",
        "host": "api.example.test",
        "port": 8443,
        "path": "/anthropic",
    }
    assert "password" not in json.dumps(endpoint)
    assert "secret" not in json.dumps(endpoint)


def test_agent_metadata_records_explicit_output_and_context_limits(monkeypatch):
    monkeypatch.setattr(benchmark, "_git_value", lambda *args: "release-source")
    monkeypatch.setattr(benchmark, "_git_worktree_dirty", lambda: False)
    monkeypatch.setattr(benchmark, "_command_version", lambda command: f"{command}-version")
    monkeypatch.setattr(benchmark.settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(benchmark.settings, "LLM_BASE_URL", "https://api.deepseek.com/anthropic")
    monkeypatch.setattr(benchmark.settings, "LLM_API_KEY", "configured-but-never-recorded")
    monkeypatch.setattr(benchmark.settings, "MODEL_MAX_OUTPUT_TOKENS", 16_384)
    monkeypatch.setattr(benchmark.settings, "MODEL_CONTEXT_WINDOW_TOKENS", 1_000_000)
    monkeypatch.setattr(benchmark.settings, "CONTEXT_COMPRESSION_RATIO", 0.8)
    suite = benchmark.load_suite()
    now = datetime.now(timezone.utc)

    metadata = benchmark.build_run_metadata(
        backend="agent",
        mode="single",
        suite=suite,
        selected_cases=suite["cases"],
        suite_path=benchmark.SUITE_PATH,
        started_at=now,
        finished_at=now,
        official=True,
    )

    parameters = metadata["model"]["parameters"]
    assert parameters["max_output_tokens"] == 16_384
    assert parameters["context_window_tokens"] == 1_000_000
    assert parameters["context_compression_ratio"] == 0.8
    assert parameters["context_compression_threshold_tokens"] == 800_000
    assert parameters["max_incomplete_response_recoveries"] == 2
    assert parameters["max_completion_gate_recoveries"] == 2
    assert metadata["model"]["api_key_configured"] is True
    assert metadata["dependencies"]["uv"] == "uv-version"
    assert "configured-but-never-recorded" not in json.dumps(metadata)
