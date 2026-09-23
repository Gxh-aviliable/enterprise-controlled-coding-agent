import pytest

from enterprise_agent.config.settings import settings
from enterprise_agent.core.execution.evidence import workspace_state
from enterprise_agent.sandbox.dependencies import dependency_directory, dependency_mounts
from enterprise_agent.sandbox.engine import SandboxError
from enterprise_agent.sandbox.executor import DockerExecutor, ExecutionRequest


def test_dependency_mounts_and_versions_are_workspace_local(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SANDBOX_STAGING_BASE", str(tmp_path / "stage"))
    monkeypatch.setattr(settings, "SANDBOX_HOST_STAGING_BASE", "/daemon/stage")
    (tmp_path / "stage").mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    before = workspace_state(workspace)
    mounts = dependency_mounts(workspace, {"frontend/package.json": ()})
    assert {m["Target"] for m in mounts} == {
        "/opt/python-user", "/opt/npm-cache", "/workspace/node_modules", "/workspace/frontend/node_modules",
    }
    assert workspace_state(workspace) == before  # empty mounts are not modifications
    package = dependency_directory(workspace) / "python" / "package.py"
    package.write_text("VALUE=1")
    installed = workspace_state(workspace)
    assert installed["files"] == before["files"]
    assert installed["version"] != before["version"]
    assert dependency_directory(workspace) != dependency_directory(tmp_path / "other")
    (dependency_directory(workspace) / "npm-cache" / "download").write_text("cache")
    assert workspace_state(workspace) == installed
    (dependency_directory(workspace) / "python" / "link").symlink_to(tmp_path / "outside")
    assert workspace_state(workspace)["version"] != installed["version"]
    directory = dependency_directory(workspace) / "node"
    for child in directory.iterdir():
        child.rmdir()
    directory.rmdir()
    directory.symlink_to(tmp_path)
    with pytest.raises(SandboxError, match="link"):
        dependency_mounts(workspace, {})


def test_network_switch_keeps_container_isolation(tmp_path, monkeypatch):
    executor = DockerExecutor()
    for enabled, mode in [(True, "bridge"), (False, "none")]:
        monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", enabled)
        spec = executor.spec(ExecutionRequest("pwd", tmp_path, 901), tmp_path, "test", 10)
        assert spec["NetworkDisabled"] == (not enabled)
        assert spec["HostConfig"]["NetworkMode"] == mode
        assert spec["HostConfig"]["ReadonlyRootfs"]
        assert spec["HostConfig"]["SecurityOpt"] == ["no-new-privileges:true"]
        assert "PYTHONUSERBASE=/opt/python-user" in spec["Env"]
