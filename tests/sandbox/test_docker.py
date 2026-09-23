"""Real Engine acceptance; never substitute mocks for these tests.

RUN_DOCKER_SANDBOX_TESTS=1 SANDBOX_DOCKER_SOCKET=... pytest tests/sandbox/test_docker.py -v
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tools.background import BackgroundManager
from enterprise_agent.core.agent.tools.file_ops import read_file, write_file
from enterprise_agent.core.agent.tools.shell import bash
from enterprise_agent.core.agent.tools.workspace import get_user_workspace, set_current_user_id
from enterprise_agent.sandbox.executor import DockerExecutor, ExecutionRequest
from enterprise_agent.sandbox.reaper import reap

pytestmark = pytest.mark.docker_sandbox


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    if os.getenv("RUN_DOCKER_SANDBOX_TESTS") != "1":
        pytest.skip("Explicit real Docker opt-in required")
    monkeypatch.setattr(settings, "AGENT_EXECUTOR", "docker")
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", False)
    monkeypatch.setattr(settings, "SANDBOX_UID", os.getuid())
    monkeypatch.setattr(settings, "SANDBOX_GID", os.getgid())
    monkeypatch.setattr(settings, "SANDBOX_STAGING_BASE", str(tmp_path / "stage"))
    monkeypatch.setattr(settings, "SANDBOX_HOST_STAGING_BASE", str(tmp_path / "stage"))
    monkeypatch.setattr(settings, "SANDBOX_DEPLOYMENT", "test-" + tmp_path.name[:35])
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path / "workspaces"))
    set_current_user_id(901)
    workspace = get_user_workspace()
    executor = DockerExecutor()
    # Fail, not skip, if opt-in is set but daemon/image unavailable.
    executor.engine.request("GET", "/images/" + settings.SANDBOX_IMAGE + "/json")
    yield executor, workspace
    for container in executor.engine.containers(settings.SANDBOX_DEPLOYMENT):
        executor.engine.remove(container["Id"])
    assert not list((tmp_path / "stage").glob("agent-sandbox-*")), "Execution snapshots were not cleaned up"
    set_current_user_id(None)


def run(executor, workspace, code, timeout=10, cancel=lambda: False):
    (workspace / "probe.py").write_text(code)
    return executor.run(ExecutionRequest("python probe.py", workspace, 901, "real-test", timeout), cancel)


def test_demo_file_tools_modify_test_result(sandbox):
    executor, workspace = sandbox
    assert "Error" not in write_file.invoke({"path": "app.py", "content": "def add(a,b): return a-b\n"})
    assert "a-b" in read_file.invoke({"path": "app.py"})
    write_file.invoke({"path": "test_app.py", "content": "from app import add\ndef test_add(): assert add(2,3)==5\n"})
    failed = json.loads(bash.invoke({"command": "python -m pytest -q"}))
    assert failed["exit_code"] == 1, failed
    result = run(
        executor,
        workspace,
        "from pathlib import Path\np=Path('app.py');p.write_text(p.read_text().replace('a-b','a+b'))\n",
    )
    assert result["exit_code"] == 0, json.dumps(result, indent=2)
    assert "a+b" in read_file.invoke({"path": "app.py"})
    passed = json.loads(bash.invoke({"command": "python -m pytest -q"}))
    assert passed["exit_code"] == 0 and "1 passed" in passed["stdout"], passed
    assert passed["cleanup_confirmed"]
    assert not executor.engine.containers(settings.SANDBOX_DEPLOYMENT)


def test_validation_receipts_bind_real_container_input(sandbox):
    from enterprise_agent.core.agent.nodes import _needs_verification
    from enterprise_agent.core.execution.evidence import current_evidence

    executor, workspace = sandbox
    token = current_evidence.set({"trace_id": "docker-receipts", "tool_call_id": "docker-test", "receipts": []})
    try:
        write_file.invoke({"path": "app.py", "content": "VALUE = 1\n"})
        write_file.invoke(
            {"path": "test_app.py", "content": "from app import VALUE\ndef test_value(): assert VALUE == 1\n"}
        )
        state = {"user_id": 901, "trace_id": "docker-receipts"}
        for command in ("echo pytest", "pytest --version", "pytest --version || true"):
            result = json.loads(bash.invoke({"command": command}))
            assert result["receipt"]["validation"] is None
            assert _needs_verification(state)
        result = json.loads(bash.invoke({"command": "python -m pytest -q"}))
        assert result["receipt"]["ok"], result
        assert result["receipt"]["executor"] == "docker"
        assert not _needs_verification(state)
        write_file.invoke({"path": "app.py", "content": "VALUE = 2\n"})
        assert _needs_verification(state)
        result = json.loads(bash.invoke({"command": "python -m pytest -q || true"}))
        assert result["exit_code"] == 0
        assert not result["receipt"]["ok"]
        assert _needs_verification(state)
        write_file.invoke(
            {
                "path": "test_app.py",
                "content": ("from pathlib import Path\ndef test_write(): Path('app.py').write_text('VALUE = 3\\n')\n"),
            }
        )
        result = json.loads(bash.invoke({"command": "python -m pytest -q"}))
        assert result["exit_code"] == 0
        assert result["validation_input_changed"]
        assert not result["receipt"]["ok"]
        assert "app.py" in result["published_paths"]
        assert _needs_verification(state)
    finally:
        current_evidence.reset(token)


def test_secrets_other_users_host_socket_network_and_readonly(sandbox, tmp_path, monkeypatch):
    executor, workspace = sandbox
    host_secret = tmp_path / "host-secret"
    host_secret.write_text("host-secret")
    other = workspace.parent / "user_902"
    other.mkdir()
    (other / "private.txt").write_text("other-secret")
    (workspace / ".env").write_text("platform-secret")
    (workspace / ".agent").mkdir()
    (workspace / ".agent" / "credentials.json").write_text("internal-secret")
    monkeypatch.setenv("LLM_API_KEY", "platform-secret-env")
    blocked_paths = [
        str(host_secret),
        str(other),
        "/var/run/docker.sock",
        "/run/docker.sock",
        "/app/.env",
        "/workspace/.env",
        "/workspace/.agent",
        "/proc/1/root/app/.env",
    ]
    result = run(
        executor,
        workspace,
        f"""
import os, socket
from pathlib import Path
assert os.getuid() != 0
for p in {blocked_paths!r}:
    assert not Path(p).exists(), p
assert 'LLM_API_KEY' not in os.environ
assert not any('platform-secret' in v for v in os.environ.values())
try:
    Path('/etc/escape').write_text('x')
except OSError: pass
else: raise AssertionError('root is writable')
assert 'NoNewPrivs:\\t1' in Path('/proc/self/status').read_text()
assert 'CapEff:\\t0000000000000000' in Path('/proc/self/status').read_text()
s = socket.socket(); s.settimeout(0.5)
try: s.connect(('1.1.1.1', 53))
except OSError: pass
else: raise AssertionError('network reachable')
assert len(Path('/proc/net/route').read_text().splitlines()) == 1
print('isolation verified')
""",
    )
    assert result["exit_code"] == 0, json.dumps(result, indent=2)
    assert result["stdout"] == "isolation verified"


def test_limits_timeout_output_and_oom(sandbox, monkeypatch):
    executor, workspace = sandbox
    result = run(
        executor,
        workspace,
        "from pathlib import Path\n"
        "print(*(Path('/sys/fs/cgroup', p).read_text() for p in ['memory.max','pids.max','cpu.max']))",
    )
    assert result["exit_code"] == 0, json.dumps(result, indent=2)
    assert str(settings.SANDBOX_MEMORY_MB * 1024 * 1024) in result["stdout"]
    assert str(settings.SANDBOX_PIDS) in result["stdout"]
    assert "100000 100000" in result["stdout"]
    monkeypatch.setattr(settings, "TOOL_SOURCE_CAPTURE_MAX_BYTES", 500)
    result = run(executor, workspace, "print('x'*20000)")
    assert result["source_truncated"] and len(result["stdout"]) <= 500, result
    result = run(executor, workspace, "import time\ntime.sleep(30)", timeout=0.5)
    assert result["error_code"] == "tool_timeout" and result["cleanup_confirmed"], result
    result = run(executor, workspace, "a=bytearray(600*1024*1024)")
    assert result["error_code"] == "sandbox_oom" and result["cleanup_confirmed"], result
    assert not executor.engine.containers(settings.SANDBOX_DEPLOYMENT)


def test_foreground_cancel_descendants_and_parallel_independence(sandbox):
    executor, workspace = sandbox
    code = "import os,time\nif os.fork()==0:\n os.setsid(); time.sleep(60)\nelse:\n time.sleep(60)\n"
    (workspace / "long.py").write_text(code)
    cancel = threading.Event()
    request = ExecutionRequest("python long.py", workspace, 901, "cancel-me", 30)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(executor.run, request, cancel.is_set)
        deadline = time.monotonic() + 10
        while not executor.engine.containers(settings.SANDBOX_DEPLOYMENT):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        cancel.set()
        sibling = executor.run(ExecutionRequest("echo sibling", workspace, 901, "keep-me", 5), lambda: False)
        result = future.result(timeout=15)
    assert result["error_code"] == "task_cancelled" and result["cleanup_confirmed"], result
    assert sibling["stdout"] == "sibling" and sibling["exit_code"] == 0, sibling
    assert not executor.engine.containers(settings.SANDBOX_DEPLOYMENT)


def test_background_cancel_and_normal_completion(sandbox):
    executor, workspace = sandbox
    (workspace / "long.py").write_text("import os,time\nif os.fork()==0: os.setsid()\ntime.sleep(60)\n")
    manager = BackgroundManager(session_id="demo", user_id=901)
    manager.run("python long.py", timeout=30)
    first = next(iter(manager.tasks))
    manager.run("echo unaffected", timeout=10)
    second = list(manager.tasks)[1]
    deadline = time.monotonic() + 10
    while len(executor.engine.containers(settings.SANDBOX_DEPLOYMENT)) < 1:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    manager.cancel(first)
    while manager._threads:
        assert time.monotonic() < deadline + 10
        time.sleep(0.05)
    assert manager.tasks[first]["status"] == "cancelled", manager.tasks[first]
    assert manager.tasks[first]["execution"]["cleanup_confirmed"]
    assert manager.tasks[second]["status"] == "completed", manager.tasks[second]
    assert "unaffected" in manager.check(second)
    assert not executor.engine.containers(settings.SANDBOX_DEPLOYMENT)


def test_unavailable_never_runs_on_host(sandbox, monkeypatch):
    executor, workspace = sandbox
    monkeypatch.setattr(settings, "SANDBOX_IMAGE", "does-not-exist-sandbox:never")
    result = executor.run(ExecutionRequest("touch escaped", workspace, 901), lambda: False)
    assert result["error_code"] == "sandbox_unavailable", result
    assert not (workspace / "escaped").exists()
    monkeypatch.setattr(settings, "SANDBOX_DOCKER_SOCKET", "/no-such-docker.sock")
    result = json.loads(bash.invoke({"command": "touch escaped"}))
    assert result["error_code"] in {"sandbox_unavailable", "sandbox_cleanup_failed"}
    assert not (workspace / "escaped").exists()


def test_reaper_recovers_expired_container_only(sandbox):
    executor, workspace = sandbox
    stage = workspace.parent / "reaper-stage"
    stage.mkdir()
    names = []
    try:
        for suffix, seconds in [("old", -1), ("new", 60)]:
            name = "agent-reaper-test-" + settings.SANDBOX_DEPLOYMENT + suffix
            names.append(name)
            spec = executor.spec(ExecutionRequest("sleep 60", workspace, 901), stage, name, 60)
            spec["Labels"]["enterprise.deadline"] = str(time.time() + seconds)
            executor.engine.request("POST", f"/containers/create?name={name}", spec)
            executor.engine.request("POST", f"/containers/{name}/start")
        assert len(reap(executor.engine, settings.SANDBOX_DEPLOYMENT)) == 1
        assert executor.engine.request("GET", f"/containers/{names[1]}/json")["State"]["Running"]
    finally:
        for name in names:
            executor.engine.remove(name)


def test_pid_limit_tmpfs_and_large_output(sandbox, monkeypatch):
    executor, workspace = sandbox
    result = run(
        executor,
        workspace,
        """
import subprocess
from pathlib import Path
children = []
try:
    for i in range(100):
        try: children.append(subprocess.Popen(['sleep','10']))
        except BlockingIOError: break
    assert 0 < len(children) < 64, len(children)
    print('PID limit enforced', len(children))
finally:
    for p in children: p.kill()
    for p in children: p.wait()
try:
    with open('/tmp/fill', 'wb') as f:
        for i in range(100): f.write(b'x' * 1024 * 1024)
except OSError as exc:
    assert exc.errno == 28, exc
else: raise AssertionError('tmpfs is not bounded')
print('tmpfs bound enforced')
""",
    )
    assert result["exit_code"] == 0, json.dumps(result, indent=2)
    monkeypatch.setattr(settings, "TOOL_SOURCE_CAPTURE_MAX_BYTES", 1024)
    result = run(executor, workspace, "import sys\nsys.stdout.write('x'*5_000_000)\nsys.stderr.write('y'*2_000_000)")
    assert result["exit_code"] == 0, result
    assert result["stdout_original_bytes"] == 5_000_000
    assert result["stderr_original_bytes"] == 2_000_000
    assert result["source_truncated"] and len(result["stdout"]) == len(result["stderr"]) == 1024


def test_python_file_writer_conflicts_with_running_shell(sandbox):
    executor, workspace = sandbox
    (workspace / "shared.txt").write_text("baseline")
    (workspace / "mutate.py").write_text(
        "import time\nfrom pathlib import Path\ntime.sleep(1)\nPath('shared.txt').write_text('agent')\n"
    )
    with ThreadPoolExecutor() as pool:
        result = pool.submit(executor.run, ExecutionRequest("python mutate.py", workspace, 901), lambda: False)
        deadline = time.monotonic() + 10
        while not executor.engine.containers(settings.SANDBOX_DEPLOYMENT):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        write_file.invoke({"path": "shared.txt", "content": "human"})
        outcome = result.result(timeout=15)
    assert outcome["error_code"] == "sandbox_error" and "conflict" in outcome["stderr"], outcome
    assert (workspace / "shared.txt").read_text() == "human"
    assert outcome["cleanup_confirmed"]


def test_container_link_is_never_published_and_cancel_drops_mutations(sandbox):
    executor, workspace = sandbox
    result = run(executor, workspace, "from pathlib import Path\nPath('escape').symlink_to('/etc/passwd')\n")
    assert result["error_code"] == "sandbox_error" and result["cleanup_confirmed"], result
    assert not (workspace / "escape").exists()
    result = run(
        executor,
        workspace,
        "from pathlib import Path\nimport time\nPath('partial').write_text('not published')\ntime.sleep(60)",
        timeout=0.5,
    )
    assert result["error_code"] == "tool_timeout" and not (workspace / "partial").exists()


def test_default_configuration_is_docker_and_unauthorized_workspace_rejected(sandbox, tmp_path):
    from enterprise_agent.config.settings import Settings

    executor, _ = sandbox
    assert Settings.model_fields["AGENT_EXECUTOR"].default == "docker"
    result = executor.run(ExecutionRequest("touch escape", tmp_path, 901), lambda: False)
    assert result["error_code"] == "sandbox_unavailable", result
    assert not (tmp_path / "escape").exists()


def test_async_cancellation_reaches_real_sync_executor_and_trace(sandbox, monkeypatch):
    from enterprise_agent.core.execution.interrupt_control import (
        reset_current_task_control_identity,
        set_current_task_control_identity,
    )
    from enterprise_agent.observability.trace_store import get_trace_store
    from enterprise_agent.sandbox.control import current_execution_stop

    executor, workspace = sandbox
    trace = "real-cancel-trace"
    store = get_trace_store()
    store.start_trace(
        user_id=901,
        session_id="real-session",
        trace_id=trace,
        request_summary="real Docker cancellation",
        mode="single",
    )
    (workspace / "sleep.py").write_text("import os,time\nif os.fork()==0: os.setsid()\ntime.sleep(60)\n")
    stop = threading.Event()
    monkeypatch.setattr("enterprise_agent.core.agent.tools.shell._foreground_cancel_requested", lambda: False)

    def invoke():
        token = current_execution_stop.set(stop)
        identity_token = set_current_task_control_identity(901, "real-session", trace)
        set_current_user_id(901)
        try:
            return json.loads(bash.invoke({"command": "python sleep.py"}))
        finally:
            current_execution_stop.reset(token)
            reset_current_task_control_identity(identity_token)
            set_current_user_id(None)

    with ThreadPoolExecutor() as pool:
        future = pool.submit(invoke)
        deadline = time.monotonic() + 10
        while True:
            containers = executor.engine.containers(settings.SANDBOX_DEPLOYMENT)
            if containers and containers[0]["State"] == "running":
                top = executor.engine.request("GET", f"/containers/{containers[0]['Id']}/top")
                if len(top["Processes"]) >= 3:
                    break
            assert time.monotonic() < deadline
            time.sleep(0.02)
        stop.set()
        result = future.result(timeout=15)
    assert result["error_code"] == "task_cancelled" and result["cleanup_confirmed"], result
    stored = store.get_trace(901, trace)
    assert not store._path(901, trace).is_relative_to(workspace)
    event = next(e for e in stored["events"] if e["name"] == "agent_executor")
    assert event["data"]["container_id"] == result["container_id"]
    assert event["data"]["cleanup_confirmed"]
    assert not executor.engine.containers(settings.SANDBOX_DEPLOYMENT)


def test_independent_reaper_service_recovers_orphan(sandbox):
    """No live API worker is required to collect an orphaned container."""
    executor, workspace = sandbox
    orphan_name = "orphan-" + settings.SANDBOX_DEPLOYMENT
    reaper_name = "reaper-" + settings.SANDBOX_DEPLOYMENT
    foreign_name = "foreign-" + settings.SANDBOX_DEPLOYMENT
    stage = workspace.parent / "orphan-stage"
    stage.mkdir()
    try:
        for name, deployment in [(orphan_name, settings.SANDBOX_DEPLOYMENT), (foreign_name, "foreign-deployment")]:
            spec = executor.spec(ExecutionRequest("sleep 60", workspace, 901), stage, name, 60)
            spec["Labels"]["enterprise.deadline"] = str(time.time() - 1)
            spec["Labels"]["enterprise.sandbox"] = deployment
            executor.engine.request("POST", f"/containers/create?name={name}", spec)
            executor.engine.request("POST", f"/containers/{name}/start")
        executor.engine.request(
            "POST",
            f"/containers/create?name={reaper_name}",
            {
                "Image": os.environ.get("SANDBOX_REAPER_TEST_IMAGE", "enterprise-agent-sandbox-reaper:1"),
                "User": "10001:10001",
                "Env": [f"SANDBOX_DEPLOYMENT={settings.SANDBOX_DEPLOYMENT}"],
                "HostConfig": {
                    "NetworkMode": "none",
                    "ReadonlyRootfs": True,
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges:true"],
                    "GroupAdd": list(
                        {
                            str(os.getgid()),
                            os.getenv("SANDBOX_REAPER_SOCKET_GID", str(os.stat(settings.SANDBOX_DOCKER_SOCKET).st_gid)),
                        }
                    ),
                    "Memory": 64 * 1024 * 1024,
                    "PidsLimit": 32,
                    "Mounts": [
                        {
                            "Type": "bind",
                            "Source": os.getenv("SANDBOX_REAPER_HOST_SOCKET", settings.SANDBOX_DOCKER_SOCKET),
                            "Target": "/var/run/docker.sock",
                        }
                    ],
                },
            },
        )
        executor.engine.request("POST", f"/containers/{reaper_name}/start")
        deadline = time.monotonic() + 15
        while executor.engine.request("GET", f"/containers/{orphan_name}/json", missing_ok=True):
            assert time.monotonic() < deadline, "Independent reaper did not collect orphan"
            time.sleep(0.1)
        assert executor.engine.request("GET", f"/containers/{foreign_name}/json")["State"]["Running"]
    finally:
        for name in (orphan_name, foreign_name, reaper_name):
            executor.engine.remove(name)


def test_early_exit_137_is_not_misreported_as_timeout(sandbox):
    executor, workspace = sandbox
    result = run(executor, workspace, "raise SystemExit(137)", timeout=0.5)
    assert result["exit_code"] == 137, result
    assert not result.get("error_code"), result
    assert result["cleanup_confirmed"]


def test_real_shell_diff_and_safe_restore(sandbox):
    from enterprise_agent.core.execution.changes import restore_changes, task_changes
    from enterprise_agent.core.execution.evidence import current_evidence

    _, workspace = sandbox
    (workspace / "note.txt").write_text("before\n")
    token = current_evidence.set({"trace_id": "docker-diff", "tool_call_id": "change", "receipts": []})
    try:
        result = json.loads(bash.invoke({"command": "echo after > note.txt"}))
        assert result["exit_code"] == 0 and not result.get("error_code"), result
        report = task_changes(workspace, 901, "docker-diff")
        assert report["files"][0]["path"] == "note.txt"
        assert "-before\n+after\n" in report["files"][0]["diff"]
        restore_changes(workspace, 901, "docker-diff", ["note.txt"], report["version"])
        assert (workspace / "note.txt").read_text() == "before\n"
    finally:
        current_evidence.reset(token)


def test_runtime_preflight_detects_missing_node_without_executing_project(sandbox, monkeypatch):
    from enterprise_agent.sandbox.preflight import _CACHE

    executor, workspace = sandbox
    monkeypatch.setattr(settings, "SANDBOX_IMAGE", "enterprise-agent-sandbox:1")
    _CACHE.clear()
    (workspace / "package.json").write_text('{"scripts":{"test":"touch should-not-exist"}}')
    result = executor.run(ExecutionRequest("npm test", workspace, 901), lambda: False)
    assert result["error_code"] == "environment_unavailable", result
    assert "node" in result["stderr"] or "npm" in result["stderr"]
    assert not (workspace / "should-not-exist").exists()
    assert not executor.engine.containers(settings.SANDBOX_DEPLOYMENT)


def test_runtime_preflight_mixed_image_has_python_and_node(sandbox, monkeypatch):
    executor, workspace = sandbox
    monkeypatch.setattr(
        settings, "SANDBOX_IMAGE", os.environ.get("SANDBOX_MIXED_TEST_IMAGE", "enterprise-agent-sandbox-python-node:1")
    )
    (workspace / "test_ok.py").write_text("def test_ok(): assert 2 + 2 == 4\n")
    (workspace / "ok.test.js").write_text(
        "require('node:test')('ok',()=>require('node:assert/strict').equal(2+2,4));\n"
    )
    for command in ["python -B -m pytest -q", "node --test"]:
        result = executor.run(ExecutionRequest(command, workspace, 901), lambda: False)
        assert result["exit_code"] == 0 and not result.get("error_code"), result
        assert result["preflight"]["status"] == "available"
        assert result["preflight"]["image_id"].startswith("sha256:")


def test_online_pip_npm_install_persist_between_containers(sandbox, monkeypatch):
    if os.getenv("RUN_SANDBOX_NETWORK_TESTS") != "1":
        pytest.skip("Explicit PyPI/npm network opt-in required")
    from enterprise_agent.core.execution.evidence import current_evidence, task_evidence
    from enterprise_agent.sandbox.dependencies import dependency_directory

    executor, workspace = sandbox
    monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", True)
    monkeypatch.setattr(
        settings, "SANDBOX_IMAGE", os.environ.get("SANDBOX_MIXED_TEST_IMAGE", "enterprise-agent-sandbox-python-node:1")
    )
    (workspace / "test_ok.py").write_text("def test_ok(): assert 1 + 1 == 2\n")
    (workspace / "check_package.py").write_text(
        "import cowsay\nassert cowsay.__file__.startswith('/opt/python-user/')\nprint('pip-reused')\n"
    )
    token = current_evidence.set({"trace_id": "online-deps", "tool_call_id": "install", "receipts": []})
    try:

        def shell(command):
            result = json.loads(bash.invoke({"command": command}))
            assert result["exit_code"] == 0 and not result.get("error_code"), result
            assert result["cleanup_confirmed"]
            return result

        shell("python -B -m pytest -q")
        state = {"user_id": 901, "trace_id": "online-deps"}
        assert task_evidence(state)["validation_satisfied"]
        installed = shell("python -m pip install --no-deps cowsay==6.1")
        assert installed["receipt"]["dependencies_changed"]
        assert not task_evidence(state)["validation_satisfied"]
        shell("python check_package.py")
        other = get_user_workspace(902)
        (other / "check_package.py").write_text("import cowsay\n")
        isolated = executor.run(ExecutionRequest("python check_package.py", other, 902), lambda: False)
        assert isolated["exit_code"] != 0 and "cowsay" in isolated["stderr"]
        assert dependency_directory(other) != dependency_directory(workspace)
        web = workspace / "web"
        web.mkdir()
        (web / "package.json").write_text('{"name":"synthetic-install","private":true}')
        (web / "check.js").write_text("require('node:assert/strict').equal(require('semver').valid('1.2.3'),'1.2.3')\n")
        shell("cd web && npm install --save-exact semver@7.7.2")
        shell("cd web && node check.js")
        cli = shell("cd web && ./node_modules/.bin/semver 1.2.3")
        assert "1.2.3" in cli["stdout"]
        assert (web / "package-lock.json").is_file()
        assert not (web / "node_modules/semver").exists()  # packages stay out of source/Diff
        shell("python -B -m pytest -q")
        assert task_evidence(state)["validation_satisfied"]
    finally:
        current_evidence.reset(token)


@pytest.mark.parametrize("background", [False, True])
def test_new_shell_syntax_through_real_tools(sandbox, monkeypatch, background):
    from enterprise_agent.core.agent.tools.background import background_run, get_background_manager
    from enterprise_agent.core.agent.tools.contracts import normalize_tool_result

    executor, workspace = sandbox
    monkeypatch.setattr(settings, "SANDBOX_IMAGE", os.environ.get(
        "SANDBOX_MIXED_TEST_IMAGE", "enterprise-agent-sandbox-python-node:1"))
    (workspace / "test_ok.py").write_text("def test_ok(): assert 1 + 1 == 2\n")
    (workspace / "generated.tmp").write_text("delete this synthetic file")
    (workspace / ".git").mkdir()
    (workspace / ".git/config").write_text("synthetic git secret")
    manager = get_background_manager() if background else None

    def invoke(command):
        if manager is None:
            result = json.loads(bash.invoke({"command": command}))
        else:
            old = set(manager.tasks)
            response = background_run.invoke({"command": command, "timeout": 20})
            assert response.startswith("Background task"), response
            task_id, = set(manager.tasks) - old
            deadline = time.monotonic() + 30
            while manager.tasks[task_id]["status"] == "running":
                assert time.monotonic() < deadline
                time.sleep(0.05)
            result = manager.tasks[task_id]["execution"]
        assert result["executor"] == "docker" and result["cleanup_confirmed"], result
        assert not result.get("error_code"), result
        assert result["receipt"]["phase"] == "finished"
        return result

    try:
        for command, expected in [
            ("find . -not -path './.git/*'", "test_ok.py"),
            ('python -c "print(1 + 1)"', "2"),
            ('node -e "console.log(2)"', "2"),
            ("printf 'pipe-ok\\n' | cat", "pipe-ok"),
            ('value=variable-ok; echo "$value"', "variable-ok"),
            ('echo "$(printf substitution-ok)"', "substitution-ok"),
            ("python - <<'PYCODE'\nprint('heredoc-ok')\nPYCODE", "heredoc-ok"),
            ("printf first\nprintf second", "firstsecond"),
            ("cd /workspace && python -m pytest -q 2>&1", "1 passed"),
            ("printf redirected > generated.txt; cat generated.txt", "redirected"),
        ]:
            result = invoke(command)
            assert result["exit_code"] == 0 and expected in result["stdout"], result
            if command.startswith("find"):
                assert ".git" not in result["stdout"]
            if command.startswith("cd /workspace"):
                assert result["receipt"]["ok"] and result["receipt"]["validation"]["kind"] == "test"
        assert (workspace / "generated.txt").read_text() == "redirected"
        deleted = invoke("rm -- generated.tmp")
        assert deleted["exit_code"] == 0 and not (workspace / "generated.tmp").exists()
        assert "generated.tmp" in deleted["published_paths"]
        assert any(c["path"] == "generated.tmp" for c in deleted["receipt"]["changes"])
        failed = invoke("ls missing 2>/dev/null")
        assert failed["exit_code"] != 0 and not failed["stderr"]
        assert not failed["receipt"]["ok"]
        record = normalize_tool_result(tool_name="bash", tool_call_id="missing", raw_result=failed,
                                       duration_ms=1, attempt_count=1)
        assert record.error_code == "nonzero_exit"
        missing = invoke("shell_policy_nonexistent_binary")
        assert missing["exit_code"] == 127 and "not found" in missing["stderr"]
        (workspace / "test_ok.py").write_text("def test_ok(): assert False\n")
        failed = invoke("pytest -q 2>&1")
        assert failed["exit_code"] == 1 and not failed["receipt"]["ok"]
        for command in ("pytest -q 2>&1 | cat", "pytest -q 2>&1 || true", "echo pytest", "pytest --version"):
            masked = invoke(command)
            assert masked["exit_code"] == 0
            assert masked["receipt"]["validation"] is None and not masked["receipt"]["ok"]
    finally:
        if manager:
            manager.shutdown()
    assert not executor.engine.containers(settings.SANDBOX_DEPLOYMENT)


def test_curl_network_and_missing_binary_are_runtime_results(sandbox, monkeypatch):
    """Use an isolated, labelled HTTP endpoint; never send workspace data."""
    executor, workspace = sandbox
    server = "shell-policy-http-" + settings.SANDBOX_DEPLOYMENT
    spec = executor.spec(ExecutionRequest("unused", workspace, 901), workspace, server, 60)
    spec.update(Entrypoint=["python"], Cmd=["-m", "http.server", "8080", "--bind", "0.0.0.0"],
                WorkingDir="/tmp", NetworkDisabled=False)
    spec["HostConfig"].update(NetworkMode="bridge", Mounts=[])
    try:
        executor.engine.request("POST", f"/containers/create?name={server}", spec)
        executor.engine.request("POST", f"/containers/{server}/start")
        networks = executor.engine.request("GET", f"/containers/{server}/json")["NetworkSettings"]["Networks"]
        ip = networks["bridge"]["IPAddress"]
        assert ip
        # Bash TCP client tests networking even when curl is not in the administrator image.
        command = f"exec 3<>/dev/tcp/{ip}/8080; printf 'HEAD / HTTP/1.0\r\n\r\n' >&3; head -1 <&3"
        monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", True)
        connected = json.loads(bash.invoke({"command": command}))
        assert connected["exit_code"] == 0 and "200 OK" in connected["stdout"], connected
        curl = json.loads(bash.invoke({"command": f"curl -I --max-time 3 http://{ip}:8080"}))
        assert curl.get("error_code") != "policy_blocked", curl
        if curl["exit_code"] == 127:
            assert "not found" in curl["stderr"]  # Tool absence remains an honest runtime result.
        else:
            assert curl["exit_code"] == 0 and "200 OK" in curl["stdout"], curl
        monkeypatch.setattr(settings, "SANDBOX_NETWORK_ENABLED", False)
        disconnected = json.loads(bash.invoke({"command": command}))
        assert disconnected["exit_code"] != 0 and disconnected["cleanup_confirmed"], disconnected
        assert not disconnected["receipt"]["ok"]
    finally:
        executor.engine.remove(server)
