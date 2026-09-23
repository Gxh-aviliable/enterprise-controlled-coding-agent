"""Replaceable execution boundary shared by foreground and background tools."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from enterprise_agent.config.settings import settings
from enterprise_agent.core.execution.evidence import (
    finish_receipt,
    save_receipt,
    snapshot_text_before,
    workspace_state,
)
from enterprise_agent.sandbox.engine import Engine, SandboxError
from enterprise_agent.sandbox.storage import remove_snapshot
from enterprise_agent.sandbox.workspace import publish, snapshot

logger = logging.getLogger(__name__)
_active_lock = threading.Lock()
_active: dict[str, tuple[threading.Event, threading.Event]] = {}


@dataclass(frozen=True)
class ExecutionRequest:
    command: str
    workspace: Path
    user_id: int | None = None
    trace_id: str | None = None
    timeout: float = 120
    execution_id: str | None = None


class Executor(Protocol):
    def run(self, request: ExecutionRequest, cancelled: Callable[[], bool]) -> dict: ...


class LocalExecutor:
    """Explicit development-only host execution; NOT a security sandbox."""

    def run(self, request, cancelled):
        from enterprise_agent.core.agent.tools.shell import _run_local

        return _run_local(request, cancelled)


class DockerExecutor:
    def __init__(self):
        self.engine = Engine(settings.SANDBOX_DOCKER_SOCKET)

    def _validate(self):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", settings.SANDBOX_DEPLOYMENT):
            raise SandboxError("Invalid sandbox deployment identity")
        if settings.SANDBOX_UID <= 0 or settings.SANDBOX_GID <= 0:
            raise SandboxError("Sandbox requires non-root UID and GID")
        if os.getuid() != settings.SANDBOX_UID or os.getgid() != settings.SANDBOX_GID:
            raise SandboxError("Sandbox UID/GID must equal API workspace owner UID/GID")
        values = (
            settings.SANDBOX_CPUS,
            settings.SANDBOX_MEMORY_MB,
            settings.SANDBOX_PIDS,
            settings.SANDBOX_TMP_MB,
            settings.COMMAND_TIMEOUT_SECONDS,
            settings.TOOL_SOURCE_CAPTURE_MAX_BYTES,
            settings.SANDBOX_WORKSPACE_MAX_BYTES,
            settings.SANDBOX_WORKSPACE_MAX_FILES,
        )
        if any(v <= 0 for v in values):
            raise SandboxError("Sandbox limits must be positive")

    def spec(self, request, source, name, timeout):
        uid, gid = settings.SANDBOX_UID, settings.SANDBOX_GID
        memory = settings.SANDBOX_MEMORY_MB * 1024 * 1024
        return {
            "Image": settings.SANDBOX_IMAGE,
            "User": f"{uid}:{gid}",
            "WorkingDir": "/workspace",
            "Entrypoint": ["/usr/bin/timeout"],
            "Cmd": ["--signal=KILL", str(timeout), "/bin/bash", "--noprofile", "--norc", "-c", request.command],
            "Env": [
                "HOME=/tmp",
                "TMPDIR=/tmp",
                "PATH=/opt/python-user/bin:/usr/local/bin:/usr/bin:/bin",
                "PYTHONUSERBASE=/opt/python-user",
                "PIP_USER=1",
                "PIP_DISABLE_PIP_VERSION_CHECK=1",
                "PIP_NO_CACHE_DIR=1",
                "npm_config_cache=/opt/npm-cache",
                "npm_config_audit=false",
                "npm_config_fund=false",
                "LANG=C.UTF-8",
                "PYTHONIOENCODING=utf-8",
                "PYTHONDONTWRITEBYTECODE=1",
            ],
            "NetworkDisabled": not settings.SANDBOX_NETWORK_ENABLED,
            "Tty": False,
            "Labels": {
                "enterprise.sandbox": settings.SANDBOX_DEPLOYMENT,
                "enterprise.execution": name,
                "enterprise.trace": request.trace_id or "direct",
                "enterprise.user": str(request.user_id),
                "enterprise.deadline": str(time.time() + timeout + 15),
            },
            "HostConfig": {
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
                "NetworkMode": "bridge" if settings.SANDBOX_NETWORK_ENABLED else "none",
                "IpcMode": "private",
                "Memory": memory,
                "MemorySwap": memory,
                "NanoCpus": int(settings.SANDBOX_CPUS * 1_000_000_000),
                "PidsLimit": settings.SANDBOX_PIDS,
                "RestartPolicy": {"Name": "no"},
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": str(source),
                        "Target": "/workspace",
                        "ReadOnly": False,
                        "BindOptions": {"Propagation": "rprivate"},
                    }
                ],
                "Tmpfs": {"/tmp": f"rw,nosuid,nodev,size={settings.SANDBOX_TMP_MB}m,mode=1777"},
                "LogConfig": {"Type": "none"},
                "Ulimits": [{"Name": "nofile", "Soft": 256, "Hard": 256}],
            },
        }

    def run(self, request, cancelled):
        name = "agent-sandbox-" + uuid.uuid4().hex
        result = {
            "stdout": "",
            "stderr": "",
            "exit_code": -1,
            "executor": "docker",
            "execution_id": name,
            "trace_id": request.trace_id,
            "source_truncated": False,
        }
        staging = None
        removal_confirmed = False
        created = False
        capture = None
        try:
            self._validate()
            from enterprise_agent.core.agent.tools.workspace import get_user_workspace
            from enterprise_agent.core.agent.tools.workspace_lock import workspace_write_lock

            authorized = get_user_workspace(request.user_id)
            if request.workspace.absolute() != authorized.absolute() or authorized.is_symlink():
                raise SandboxError("Execution workspace is not the authorized user workspace")
            info = self.engine.request("GET", "/info")
            if not all(info.get(key) for key in ("MemoryLimit", "SwapLimit", "CpuCfsQuota", "PidsLimit")):
                raise SandboxError("Docker daemon cannot enforce required resource limits")
            image = self.engine.request("GET", "/images/" + settings.SANDBOX_IMAGE + "/json")
            if image.get("Config", {}).get("Volumes"):
                raise SandboxError("Sandbox image must not declare additional VOLUME mounts")
            timeout = min(float(request.timeout), settings.COMMAND_TIMEOUT_SECONDS)
            if timeout <= 0:
                raise SandboxError("Execution timeout must be positive")
            if cancelled():
                result.update(error_code="task_cancelled", cancellation="terminated", cleanup_confirmed=True)
                return result
            from enterprise_agent.sandbox.preflight import check_runtime

            result["preflight"] = check_runtime(self, request, image)
            root = Path(settings.SANDBOX_STAGING_BASE).absolute()
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if root.is_symlink() or root.stat().st_mode & 0o022 or root.stat().st_uid != os.getuid():
                raise SandboxError("Sandbox staging root must be a service-owned non-writable-by-others directory")
            # Fixed server configuration is the only source of daemon-side paths.
            host_root = Path(settings.SANDBOX_HOST_STAGING_BASE or str(root))
            if not host_root.is_absolute():
                raise SandboxError("Daemon staging root must be absolute")
            staging = root / name
            staging.mkdir(mode=0o700)
            limits = (settings.SANDBOX_WORKSPACE_MAX_BYTES, settings.SANDBOX_WORKSPACE_MAX_FILES)
            with workspace_write_lock(request.user_id):
                from enterprise_agent.core.execution.approvals import require_current_approval

                require_current_approval()
                baseline = snapshot(request.workspace, staging, limits)
                from enterprise_agent.sandbox.dependencies import dependency_mounts

                mounts = dependency_mounts(request.workspace, baseline)
                # Create nested bind targets as the service user; Docker would
                # otherwise leave root-owned directories in the host snapshot.
                for mount in mounts:
                    if mount["Target"].startswith("/workspace/"):
                        (staging / Path(mount["Target"]).relative_to("/workspace")).mkdir(parents=True, exist_ok=True)
                input_state = workspace_state(staging, dependency_workspace=request.workspace)
                result["input_version"] = input_state["version"]
            spec = self.spec(request, host_root / name, name, timeout)
            from enterprise_agent.skills.runtime import sandbox_resources
            mounts.extend(sandbox_resources(staging, host_root / name, request.user_id))
            spec["HostConfig"]["Mounts"].extend(mounts)
            spec["Image"] = image["Id"]
            # No image pull and no fallback. Unknown image / socket error is terminal.
            created = True  # Also clean up an ambiguous create response by unique name.
            response = self.engine.request("POST", f"/containers/create?name={name}", spec)
            result["container_id"] = response["Id"]
            if response.get("Warnings"):
                raise SandboxError("Docker did not accept sandbox constraints: " + str(response["Warnings"]))
            capture = self.engine.attach(name, settings.TOOL_SOURCE_CAPTURE_MAX_BYTES, timeout)
            deadline = time.monotonic() + timeout
            self.engine.request("POST", f"/containers/{name}/start")
            state = {}
            while True:
                if cancelled():
                    result.update(
                        error_code="task_cancelled",
                        cancellation="terminated",
                        termination_mode="container_force_remove",
                    )
                    break
                state = self.engine.request("GET", f"/containers/{name}/json")["State"]
                if not state["Running"]:
                    result["exit_code"] = state["ExitCode"]
                    if state.get("OOMKilled"):
                        result["error_code"] = "sandbox_oom"
                    elif state["ExitCode"] == 137 and time.monotonic() >= deadline:
                        result["error_code"] = "tool_timeout"
                    break
                if time.monotonic() >= deadline:
                    result.update(error_code="tool_timeout", termination_mode="container_force_remove")
                    break
                time.sleep(0.05)
            if state.get("Running", True):
                self.engine.request("POST", f"/containers/{name}/kill?signal=KILL", missing_ok=True, stopped_ok=True)
                state = self.engine.request("GET", f"/containers/{name}/json")["State"]
                result["exit_code"] = state["ExitCode"]
            streams, counts = capture.finish()
            result.update(
                stdout=streams[0] or "(no output)",
                stderr=streams[1],
                stdout_original_bytes=counts[0],
                stderr_original_bytes=counts[1],
                output_counts_scope="complete_attached_stream",
                source_truncated=any(n > settings.TOOL_SOURCE_CAPTURE_MAX_BYTES for n in counts),
            )
            self.engine.remove(name)
            removal_confirmed = True
            result["validation_input_changed"] = (
                workspace_state(staging, dependency_workspace=request.workspace)["stamp"] != input_state["stamp"]
            )
            if cancelled():
                result.update(error_code="task_cancelled", cancellation="terminated")
            if not result.get("error_code"):
                with workspace_write_lock(request.user_id):
                    result["published_paths"] = []
                    publish(staging, request.workspace, baseline, limits, published_paths=result["published_paths"])
        except Exception as exc:
            from enterprise_agent.sandbox.preflight import EnvironmentUnavailableError

            result.update(
                error_code=("environment_unavailable" if isinstance(exc, EnvironmentUnavailableError)
                            else "sandbox_unavailable" if not result.get("container_id") else "sandbox_error"),
                stderr=str(exc),
            )
        finally:
            if capture is not None:
                capture.close()
            if created and not removal_confirmed:
                try:
                    self.engine.remove(name)
                    removal_confirmed = True
                except Exception:
                    logger.exception("Sandbox cleanup pending: %s", name)
                    result.update(error_code="sandbox_cleanup_failed", cancellation="unconfirmed")
            result["cleanup_confirmed"] = removal_confirmed or not created
            if staging is not None and (removal_confirmed or not created):
                try:
                    remove_snapshot(staging)
                except OSError:
                    logger.exception("Snapshot cleanup pending: %s", name)
                    result["snapshot_cleanup_pending"] = True
        from enterprise_agent.sandbox.preflight import dependency_diagnostic
        if diagnostic := dependency_diagnostic(result):
            result['environment_diagnostic'] = diagnostic
        return result


def get_executor() -> Executor:
    if settings.AGENT_EXECUTOR == "docker":
        return DockerExecutor()
    if settings.AGENT_EXECUTOR == "local":
        return LocalExecutor()
    raise SandboxError("AGENT_EXECUTOR must be docker or explicitly enabled local")


def execute(request, cancelled):
    from enterprise_agent.sandbox.control import current_execution_stop

    stop = current_execution_stop.get() or threading.Event()
    done = threading.Event()
    identifier = request.execution_id or uuid.uuid4().hex
    with _active_lock:
        _active[identifier] = (stop, done)
    try:
        return _execute_receipted(request, cancelled, stop, identifier)
    finally:
        # Shutdown must wait for final receipt persistence, not just the process.
        done.set()
        with _active_lock:
            _active.pop(identifier, None)


def _execute_receipted(request, cancelled, stop, identifier):
    started = time.monotonic()
    before = None
    try:
        before = workspace_state(request.workspace)
        snapshot_text_before(request.workspace, request.user_id, before, request.trace_id)
        save_receipt({"execution_id": identifier, "trace_id": request.trace_id,
                      "phase": "running", "recorded_at": time.time(), "changes": []},
                     request.user_id, request.workspace)
        result = get_executor().run(request, lambda: stop.is_set() or cancelled())
    except Exception as exc:
        result = {
            "stdout": "",
            "stderr": str(exc),
            "exit_code": -1,
            "error_code": "sandbox_unavailable",
            "executor": settings.AGENT_EXECUTOR,
        }
    if before is not None:
        try:
            result["receipt"] = finish_receipt(
                root=request.workspace, user_id=request.user_id, before=before, result=result,
                command=request.command, execution_id=identifier, trace_id=request.trace_id,
            )
        except Exception:
            # A running receipt remains unresolved when final evidence cannot be
            # stored. Never report success or automatically repeat the operation.
            logger.exception("Execution evidence persistence failed")
            result.update(error_code="evidence_unavailable", reconciliation_required=True)
    if request.trace_id and request.user_id is not None:
        try:
            from enterprise_agent.observability.trace_store import get_trace_store

            get_trace_store().record_event(
                user_id=request.user_id,
                trace_id=request.trace_id,
                event_type="execution",
                name="agent_executor",
                status="error" if result.get("error_code") or result["exit_code"] != 0 else "success",
                duration_ms=int((time.monotonic() - started) * 1000),
                data={k: v for k, v in result.items() if k not in {"stdout", "stderr"}},
            )
        except FileNotFoundError:
            pass  # Direct tools/tests may not have an Agent trace.
        except Exception:
            logger.exception("Execution trace persistence failed")
    return result


def shutdown_executions(timeout=15):
    with _active_lock:
        active = list(_active.values())
    for stop, _ in active:
        stop.set()
    deadline = time.monotonic() + timeout
    for _, done in active:
        done.wait(max(0, deadline - time.monotonic()))
