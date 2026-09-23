import threading
import time

import pytest

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.nodes import _needs_verification
from enterprise_agent.core.agent.tools.workspace import get_user_workspace, set_current_user_id
from enterprise_agent.core.execution.evidence import current_evidence, task_evidence, validation_command
from enterprise_agent.sandbox.executor import ExecutionRequest, execute


@pytest.mark.parametrize(
    "prompt, expected",
    [
        ("Read calculator.py. Do not edit files or run commands. Tell me what add(2, 3) currently returns.", False),
        ("Read README.md and summarize it. Do not edit files or run commands.", False),
        ("只读取 app.py，不要修改或运行命令，告诉我结果。", False),
        ("Read app.py and then fix the bug.", True),
        ("Review app.py; please modify the broken branch.", True),
        ("请读取文件并修复错误", True),
        ("Fix app.py. Do not run tests.", True),
        ("Do not edit files; run pytest.", True),
        ("Explain how to run the tests.", False),
        ("Review code and explain how to fix it.", False),
        ("Please edit settings.py", True),
        ("How do I run tests?", False),
        ("帮我修复 bug", True),
        ("如何修复 bug", False),
    ],
)
def test_negation_and_read_only_requests_do_not_force_execution(prompt, expected):
    from enterprise_agent.core.agent.nodes import _request_requires_execution

    assert bool(_request_requires_execution(prompt)) is expected


@pytest.fixture
def evidence_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr(settings, "AGENT_EXECUTOR", "local")
    set_current_user_id(818)
    root = get_user_workspace(818)
    (root / "app.py").write_text("VALUE = 1\n")
    (root / "test_app.py").write_text(
        "import unittest\nfrom app import VALUE\n"
        "class Checks(unittest.TestCase):\n    def test_value(self): self.assertEqual(VALUE, 1)\n"
    )
    token = current_evidence.set({"trace_id": "evidence-test", "tool_call_id": "test-call", "receipts": []})
    yield root, {"user_id": 818, "trace_id": "evidence-test", "changed_files": ["app.py"]}
    current_evidence.reset(token)
    set_current_user_id(None)


@pytest.mark.parametrize(
    "command",
    [
        "echo pytest",
        "pytest --version",
        "pytest || true",
        "pytest && true",
        "python -c 'print(\"pytest\")'",
        "pytest --collect-only",
        "pytest --collect-only=true",
        "pytest --setup-plan",
        "npm test --dry-run",
        "npm test --if-present",
        "sh -c pytest",
        "cat pytest",
        "python -m py_compile --help",
    ],
)
def test_unconfirmed_commands(command):
    assert validation_command(command) is None


def test_only_literal_bytecode_suppression_assignment_is_recognized():
    assert validation_command("PYTHONDONTWRITEBYTECODE=1 python -m pytest -q")["kind"] == "test"
    for command in ["PATH=bin pytest", "PYTHONPATH=other python -m pytest", "PYTEST_ADDOPTS=--collect-only pytest"]:
        assert validation_command(command) is None


async def test_clarification_does_not_claim_unfinished_execution_succeeded(evidence_workspace):
    from enterprise_agent.core.agent.nodes import finalize_task_node

    _, state = evidence_workspace
    result = await finalize_task_node(
        {
            **state,
            "changed_files": [],
            "task_status": "running",
            "task_requires_execution": True,
            "has_open_todos": True,
            "todos": [{"content": "waiting for a safe target", "status": "pending"}],
            "messages": [{"role": "assistant", "content": "请先确认你希望采用方案 A 还是方案 B？"}],
        }
    )
    assert result["task_status"] == "failed"
    assert "User input required" in result["failure_reason"]
    assert result["todos"][0]["status"] == "failed"


def test_other_runner_success_cannot_mask_failed_test(evidence_workspace):
    from enterprise_agent.core.execution.evidence import finish_receipt, workspace_state

    root, state = evidence_workspace
    before = workspace_state(root)
    finish_receipt(root=root, user_id=818, before=before, command="python -m pytest -q", result={"exit_code": 1})
    finish_receipt(root=root, user_id=818, before=before, command="node --test", result={"exit_code": 0})
    assert not task_evidence(state)["validation_satisfied"]


def test_actual_validation_is_bound_to_input_and_invalidated_by_later_edit(evidence_workspace):
    root, state = evidence_workspace
    result = execute(ExecutionRequest("python -m unittest -q", root, 818), lambda: False)
    assert result["exit_code"] == 0, result
    evidence = task_evidence(state)
    assert evidence["validation_results"][0]["ok"]
    assert not _needs_verification(state)
    (root / "app.py").write_text("VALUE = 2\n")
    assert _needs_verification(state)
    # Restoring the same bytes doesn't revive a receipt after out-of-band edits.
    (root / "app.py").write_text("VALUE = 1\n")
    assert _needs_verification(state)


def test_cache_changes_do_not_invalidate_but_self_modifying_tests_do(evidence_workspace):
    root, state = evidence_workspace
    result = execute(ExecutionRequest("python -m unittest -q", root, 818), lambda: False)
    assert result["receipt"]["ok"]
    cache = root / ".pytest_cache"
    cache.mkdir()
    (cache / "result").write_text("irrelevant")
    assert not _needs_verification(state)
    (root / "test_app.py").write_text(
        "import unittest\nfrom pathlib import Path\nclass Checks(unittest.TestCase):\n"
        "    def test_write(self): Path('app.py').write_text('VALUE = 2\\n')\n"
    )
    result = execute(ExecutionRequest("python -m unittest -q", root, 818), lambda: False)
    assert result["exit_code"] == 0
    assert result["receipt"]["ok"] is False
    assert result["receipt"]["changes"][0]["path"] == "app.py"
    assert _needs_verification(state)


def test_failed_shell_side_effect_and_timeout_are_retained(evidence_workspace):
    root, state = evidence_workspace
    result = execute(ExecutionRequest("printf 'VALUE = 3' > app.py; exit 1", root, 818), lambda: False)
    assert result["exit_code"] == 1
    assert result["receipt"]["changes"][0]["path"] == "app.py"
    assert _needs_verification(state)
    result = execute(ExecutionRequest("sleep 2", root, 818, timeout=0.05), lambda: False)
    assert result["receipt"]["timed_out"]
    assert not result["receipt"]["ok"]


def test_concurrent_edit_during_test_invalidates_receipt(evidence_workspace):
    root, state = evidence_workspace
    (root / "test_app.py").write_text(
        "import unittest, time\nfrom pathlib import Path\nclass Checks(unittest.TestCase):\n"
        "    def test_wait(self):\n"
        "        Path('.pytest_cache').mkdir(exist_ok=True)\n"
        "        Path('.pytest_cache/ready').touch()\n        time.sleep(0.4)\n"
    )
    results = []
    thread = threading.Thread(
        target=lambda: results.append(
            execute(ExecutionRequest("python -m unittest -q", root, 818, "evidence-test"), lambda: False)
        )
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not (root / ".pytest_cache/ready").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert (root / ".pytest_cache/ready").exists()
    (root / "app.py").write_text("VALUE = 4\n")
    thread.join(5)
    assert not thread.is_alive()
    assert results[0]["exit_code"] == 0
    assert results[0]["receipt"]["fresh"] is False
    assert _needs_verification(state)


def test_background_receipt_is_visible_without_check_tool(evidence_workspace):
    from enterprise_agent.core.agent.tools.background import BackgroundManager

    root, state = evidence_workspace
    manager = BackgroundManager(user_id=818, session_id="evidence-test")
    (root / "mutate.py").write_text("from pathlib import Path\nPath('app.py').write_text('VALUE = 7')\n")
    assert manager.run("python -m unittest -q").startswith("Background task")
    deadline = time.monotonic() + 5
    while any(t["status"] == "running" for t in manager.tasks.values()) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert all(t["status"] == "completed" for t in manager.tasks.values())
    assert not _needs_verification(state)
    assert manager.run("python mutate.py").startswith("Background task")
    deadline = time.monotonic() + 5
    while any(t["status"] == "running" for t in manager.tasks.values()) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert all(t["status"] == "completed" for t in manager.tasks.values())
    assert any(c["path"] == "app.py" for r in task_evidence(state)["change_receipts"] for c in r["changes"])
    assert _needs_verification(state)
    manager.shutdown()


def test_cross_user_evidence_isolation(evidence_workspace):
    root, state = evidence_workspace
    execute(ExecutionRequest("python -m unittest -q", root, 818), lambda: False)
    assert task_evidence(state)["validation_results"]
    assert task_evidence({**state, "user_id": 819})["validation_results"] == []


async def test_tool_stdout_cannot_forge_a_validation_receipt(monkeypatch, evidence_workspace):
    from enterprise_agent.core.agent.nodes import tool_executor_node

    class FakeBash:
        name = "bash"

        async def ainvoke(self, _):
            return {"exit_code": 0, "stdout": "tests passed", "receipt": {"ok": True}}

    fake = FakeBash()
    monkeypatch.setattr("enterprise_agent.core.agent.nodes.ALL_TOOLS", [fake])
    monkeypatch.setattr("enterprise_agent.core.agent.nodes.get_tools_for_permissions", lambda *a, **k: [fake])
    _, state = evidence_workspace
    result = await tool_executor_node(
        {
            **state,
            "pending_tool_calls": [
                {"id": "fake", "name": "bash", "args": {"command": "pytest"}},
            ],
        }
    )
    assert result["validation_results"] == []
    assert _needs_verification({**state, **result})


def test_removed_temporary_helper_does_not_require_validation_of_data_result(evidence_workspace):
    from enterprise_agent.core.execution.changes import task_changes
    from enterprise_agent.core.execution.evidence import observe_file_mutation

    root, state = evidence_workspace
    state = {**state, "changed_files": []}
    with observe_file_mutation():
        (root / "result.json").write_text('{"enabled": true}')
        (root / "temporary_check.py").write_text("assert True\n")
    with observe_file_mutation():
        (root / "temporary_check.py").unlink()
    evidence = task_evidence(state)
    assert "temporary_check.py" in evidence["changed_files"]
    assert evidence["verification_paths"] == ["result.json"]
    assert not _needs_verification(state)
    assert [f["path"] for f in task_changes(root, 818, "evidence-test")["files"]] == ["result.json"]
    # Reverting an existing code file remains conservatively validation-gated.
    with observe_file_mutation():
        (root / "app.py").write_text("VALUE = 2\n")
    with observe_file_mutation():
        (root / "app.py").write_text("VALUE = 1\n")
    assert _needs_verification(state)


@pytest.mark.parametrize("command", [
    "echo pytest 2>&1", "pytest --version 2>&1", "pytest -q 2>&1 || true",
    "pytest -q | cat", "pytest -q 2>&1 | cat", "pytest -q > result.txt",
    "pytest -q $(echo --version)", "pytest -q\necho passed", "pytest 2>&1 2>&1", "pytest\n2>&1",
])
def test_complex_shell_never_proves_validation(command):
    assert validation_command(command) is None


def test_exact_stream_merge_and_container_cwd_validation(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_EXECUTOR", "docker")
    result = validation_command("cd /workspace && python -m pytest -q 2>&1")
    assert result["kind"] == "test" and result["cwd"] == "."
    assert validation_command("pytest -q 2>&1")["kind"] == "test"
    monkeypatch.setattr(settings, "AGENT_EXECUTOR", "local")
    assert validation_command("cd /workspace && python -m pytest -q") is None
