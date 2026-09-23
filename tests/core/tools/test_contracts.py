"""Uniform tool contract tests."""

import json

import pytest

from enterprise_agent.core.agent.tools import (
    ALL_TOOLS,
    get_tools_for_permissions,
    tool_requires_confirmation,
)
from enterprise_agent.core.agent.tools.contracts import (
    ArtifactPolicy,
    RiskLevel,
    ToolResultStatus,
    describe_tool,
    get_tool_contract,
    normalize_tool_result,
    resolve_tool_risk,
    should_persist_artifact,
    validate_tool_contracts,
)


def test_every_executable_tool_has_exactly_one_contract():
    validate_tool_contracts(ALL_TOOLS)


def test_recovery_and_bounded_memory_tools_never_create_artifacts():
    for name in ("read_tool_artifact", "search_memory", "list_memories"):
        contract = get_tool_contract(name)
        assert contract.artifact_policy is ArtifactPolicy.NEVER
        assert should_persist_artifact(
            contract,
            raw_chars=1_000_000,
            source_truncated=True,
        ) is False


def test_contract_exposes_input_schema_and_policy():
    descriptor = describe_tool(next(tool for tool in ALL_TOOLS if tool.name == "write_file"))
    assert descriptor["risk"] == "review"
    assert descriptor["requires_confirmation"] is True
    assert descriptor["idempotent"] is False
    assert "path" in descriptor["input_schema"]["properties"]


def test_sensitive_policy_is_derived_from_contracts():
    assert tool_requires_confirmation("write_file") is True
    assert tool_requires_confirmation("background_run") is True
    assert tool_requires_confirmation("task") is True
    assert tool_requires_confirmation("delegate_task") is True
    assert tool_requires_confirmation("delete_paths") is True
    assert tool_requires_confirmation("read_file") is False


def test_shell_confirmation_is_argument_sensitive():
    assert tool_requires_confirmation("bash", {"command": "pwd"}) is False
    assert tool_requires_confirmation("bash", {"command": "pytest -q"}) is True
    assert tool_requires_confirmation("bash", {"command": "git status"}) is False
    assert tool_requires_confirmation("bash", {"command": "git commit -m test"}) is True
    assert tool_requires_confirmation("bash", {"command": "python3 cleanup.py"}) is True
    assert tool_requires_confirmation("bash", {"command": "python3 -m pytest -q"}) is True
    # Dangerous commands cannot be approved; the executor policy blocks them.
    assert tool_requires_confirmation("bash", {"command": "rm -rf /"}) is False


def test_background_confirmation_uses_the_same_command_risk():
    assert resolve_tool_risk("background_run", {"command": "pytest -q"}) == RiskLevel.REVIEW
    assert tool_requires_confirmation("background_run", {"command": "pytest -q"}) is True
    assert tool_requires_confirmation(
        "background_run", {"command": "npm install package"}
    ) is True
    assert resolve_tool_risk(
        "background_run", {"command": "rm -rf /"}
    ) == RiskLevel.DANGEROUS
    assert tool_requires_confirmation(
        "background_run", {"command": "rm -rf /"}
    ) is False


def test_jwt_permission_names_map_to_executable_tools():
    basic = {tool.name for tool in get_tools_for_permissions(["tools:basic"])}
    shell = {tool.name for tool in get_tools_for_permissions(["tools:shell"])}
    advanced = {
        tool.name
        for tool in get_tools_for_permissions(["tools:advanced"], enable_multi_agent=True)
    }

    assert {"read_file", "write_file", "delete_paths", "task_list"}.issubset(basic)
    assert shell == {"bash", "background_run", "check_background"}
    assert "delegate_task" in advanced
    assert not {"task", "spawn_teammate", "broadcast"}.intersection(advanced)
    assert "bash" not in basic


def test_memory_query_mode_exposes_exactly_one_retrieval_path():
    semantic = {
        tool.name
        for tool in get_tools_for_permissions(
            ["tools:memory"],
            memory_query_mode="semantic",
        )
    }
    listing = {
        tool.name
        for tool in get_tools_for_permissions(
            ["tools:memory"],
            memory_query_mode="listing",
        )
    }

    assert semantic == {"search_memory"}
    assert listing == {"list_memories"}


def test_shell_risk_is_argument_sensitive():
    assert resolve_tool_risk("bash", {"command": "pwd"}) == RiskLevel.SAFE
    assert resolve_tool_risk("bash", {"command": "git commit -m test"}) == RiskLevel.REVIEW
    assert resolve_tool_risk("bash", {"command": "python3 cleanup.py"}) == RiskLevel.REVIEW
    assert (
        resolve_tool_risk("bash", {"command": "python3 -m py_compile src/example.py"})
        == RiskLevel.REVIEW
    )
    assert resolve_tool_risk("bash", {"command": "node cleanup.js"}) == RiskLevel.REVIEW
    assert resolve_tool_risk("delete_paths", {"paths": ["generated"]}) == RiskLevel.DANGEROUS
    assert resolve_tool_risk("bash", {"command": "rm -rf /"}) == RiskLevel.DANGEROUS


def test_normalize_successful_shell_result():
    record = normalize_tool_result(
        tool_name="bash",
        tool_call_id="call-1",
        raw_result=json.dumps({"stdout": "ok", "stderr": "", "exit_code": 0}),
        duration_ms=12,
        attempt_count=1,
    )
    assert record.ok is True
    assert record.status == ToolResultStatus.SUCCESS
    assert record.exit_code == 0


def test_normalize_policy_block_and_legacy_error_string():
    blocked = normalize_tool_result(
        tool_name="bash",
        tool_call_id="call-2",
        raw_result=json.dumps({"stdout": "", "stderr": "Blocked: dangerous", "exit_code": 1}),
        duration_ms=1,
        attempt_count=1,
    )
    error = normalize_tool_result(
        tool_name="read_file",
        tool_call_id="call-3",
        raw_result="Error: file not found",
        duration_ms=1,
        attempt_count=1,
    )
    assert blocked.status == ToolResultStatus.BLOCKED
    assert blocked.error_code == "policy_blocked"
    assert error.status == ToolResultStatus.ERROR
    assert error.ok is False


@pytest.mark.parametrize("tool_name", ["bash", "background_run"])
@pytest.mark.parametrize("command, expected", [
    ("ls -la", RiskLevel.SAFE), ("echo hello", RiskLevel.SAFE),
    ("find . -not -path './.git/*'", RiskLevel.SAFE),
    ("python --version", RiskLevel.SAFE), ("python -v", RiskLevel.REVIEW),
    ("python -c 'print(2)'", RiskLevel.REVIEW),
    ("node -e 'console.log(2)'", RiskLevel.REVIEW),
    ("find . -exec touch changed ';'", RiskLevel.REVIEW),
    ("find . -delete", RiskLevel.REVIEW), ("find . -fprint result", RiskLevel.REVIEW),
    ("echo $HOME", RiskLevel.REVIEW), ("echo $(touch changed)", RiskLevel.REVIEW),
    ("echo `touch changed`", RiskLevel.REVIEW), ("echo ok\ntouch changed", RiskLevel.REVIEW),
    ("ls missing 2>/dev/null", RiskLevel.REVIEW), ("pytest -q 2>&1", RiskLevel.REVIEW),
    ("echo ok &", RiskLevel.REVIEW), ("ls | head", RiskLevel.REVIEW),
    ("curl -I https://example.com", RiskLevel.REVIEW),
    ("wget https://example.com", RiskLevel.REVIEW),
    ("cd /workspace && python -m pytest -q", RiskLevel.REVIEW),
    ("rm -- generated.tmp", RiskLevel.REVIEW), ("bash -c 'echo ok'", RiskLevel.REVIEW),
    ("rg --pre=helper pattern", RiskLevel.REVIEW),
    ("rg --hostname-bin=helper pattern", RiskLevel.REVIEW),
    ("./ls", RiskLevel.REVIEW), ("git status", RiskLevel.REVIEW),
    ("rm -rf /", RiskLevel.DANGEROUS), ("mkfs.ext4 /dev/sda", RiskLevel.DANGEROUS),
])
def test_docker_risk_shared_by_foreground_and_background(monkeypatch, tool_name, command, expected):
    from enterprise_agent.config.settings import settings
    monkeypatch.setattr(settings, "AGENT_EXECUTOR", "docker")
    assert resolve_tool_risk(tool_name, {"command": command, "executor": "local"}) == expected


@pytest.mark.parametrize("executor", ["docker", "local"])
@pytest.mark.parametrize("stderr", ["Blocked: application rejected request", "request timed out", "command not found"])
def test_executor_stderr_does_not_impersonate_policy(monkeypatch, executor, stderr):
    record = normalize_tool_result(
        tool_name="bash", tool_call_id="real-error", duration_ms=1, attempt_count=1,
        raw_result={"exit_code": 1, "stderr": stderr, "executor": executor},
    )
    assert record.error_code == "nonzero_exit"
