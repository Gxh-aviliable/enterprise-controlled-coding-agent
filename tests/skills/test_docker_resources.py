"""Opt-in actual Docker execution with a read-only Skill resource mount."""

import json

import pytest

from enterprise_agent.config.settings import settings
from enterprise_agent.sandbox.executor import ExecutionRequest
from enterprise_agent.skills.catalog import entry
from enterprise_agent.skills.runtime import bind_snapshot, cache_package
from tests.sandbox.test_docker import sandbox as docker_sandbox_fixture
from tests.skills.test_packages import package

pytestmark = pytest.mark.docker_sandbox
sandbox = docker_sandbox_fixture


def test_skill_resources_run_in_real_sandbox(sandbox, monkeypatch, tmp_path):
    executor, workspace = sandbox
    monkeypatch.setattr(settings, "MANAGED_SHARED_SKILLS_DIR", str(tmp_path / "packages"))
    evidence = cache_package(package())
    skill = entry(
        evidence, id="managed:1", source="managed", scope="global", version=1, enabled=True, implicit_allowed=True
    )
    bind_snapshot({"user_id": 901, "skill_snapshot": {"user_id": 901, "items": [skill], "selected": ["managed:1"]}})
    try:
        path = "/workspace/.skill-resources/" + evidence["sha256"]
        command = f"python {path}/scripts/example.py && cat {path}/references/guide.txt"
        result = executor.run(ExecutionRequest(command, workspace, 901, "skill-real-test", 15), lambda: False)
        assert result["exit_code"] == 0, json.dumps(result, indent=2)
        assert "skill-ok" in result["stdout"] and "reference-v1" in result["stdout"]
        denied = executor.run(
            ExecutionRequest(f"echo changed > {path}/references/guide.txt", workspace, 901, "skill-readonly-test", 15),
            lambda: False,
        )
        assert denied["exit_code"] != 0 and "Read-only" in denied["stderr"], denied
        assert not (workspace / ".skill-resources").exists()
    finally:
        bind_snapshot({})
