"""Background task management tools.

Provides tools for running long-running commands in background threads
and checking their status later.
"""

import logging
import os
import signal
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from queue import Queue
from typing import Dict, Optional

from langchain_core.tools import tool

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tool_artifacts import ToolArtifactStore, format_tool_output
from enterprise_agent.core.agent.tools.shell import (
    _read_captured_stream,
    _safe_subprocess_environment,
    _shell_execution_kwargs,
    validate_command,
)
from enterprise_agent.core.agent.tools.workspace import get_user_workspace


class BackgroundManager:
    """Manages background task execution.

    Tasks run in separate threads, results stored in memory.
    Notifications queue for completed task alerts.
    """

    def __init__(self, *, session_id: str | None = None, user_id: int | None = None):
        self.tasks: dict = {}
        self.notifications: Queue = Queue()
        self._threads: dict[str, threading.Thread] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()
        self.session_id = session_id or "background"
        self.user_id = user_id

    def run(self, command: str, timeout: int = None) -> str:
        """Start a background task.

        Args:
            command: Shell command to run
            timeout: Maximum execution time in seconds

        Returns:
            Task ID and status message
        """
        if timeout is None:
            timeout = settings.COMMAND_TIMEOUT_SECONDS
        error = validate_command(command)
        if error:
            return error

        task_id = str(uuid.uuid4())[:8]
        self.tasks[task_id] = {
            "status": "running",
            "command": command,
            "result": None,
            "timeout": timeout
        }

        # Freeze the user/workspace context before spawning. ContextVars are
        # not reliably propagated to arbitrary threads, and resolving the
        # workspace inside the worker races with request/test cleanup.
        from enterprise_agent.core.agent.tools.workspace import get_current_user_id

        user_id = get_current_user_id()
        workdir = get_user_workspace(user_id)

        # Start execution thread with explicit user_id
        thread = threading.Thread(
            target=self._execute,
            args=(task_id, command, timeout, workdir),
            daemon=True
        )
        with self._lock:
            self._threads[task_id] = thread
        thread.start()

        return f"Background task {task_id} started: {command[:80]}..."

    def _execute(self, task_id: str, command: str, timeout: int, workdir: Path) -> None:
        """Execute command in thread.

        Args:
            task_id: Task identifier
            command: Shell command to execute
            timeout: Maximum execution time
            workdir: Resolved workspace captured in the request thread
        """
        try:
            if self.tasks[task_id].get("status") == "cancelled":
                return
            process_group_args = (
                {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                if os.name == "nt"
                else {"start_new_session": True}
            )
            with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(
                mode="w+b"
            ) as stderr_file:
                process = subprocess.Popen(
                    command,
                    cwd=workdir,
                    env=_safe_subprocess_environment(workdir),
                    stdout=stdout_file,
                    stderr=stderr_file,
                    **_shell_execution_kwargs(),
                    **process_group_args,
                )
                with self._lock:
                    self._processes[task_id] = process
                if self.tasks[task_id].get("status") == "cancelled":
                    self._terminate_process(task_id)
                    return
                process.communicate(timeout=timeout)
                stdout, stdout_truncated, _ = _read_captured_stream(stdout_file)
                stderr, stderr_truncated, _ = _read_captured_stream(stderr_file)
            output = "\n".join(part for part in (stdout, stderr) if part).strip() or "(no output)"
            source_truncated = stdout_truncated or stderr_truncated
            if self.tasks[task_id].get("status") != "cancelled":
                status = "success" if process.returncode == 0 else "error"
                receipt = None
                artifact_error = None
                if len(output) > 100:
                    try:
                        receipt = ToolArtifactStore(workdir=workdir).save(
                            output,
                            trace_id=f"background-{self.session_id}",
                            tool_call_id=task_id,
                            source_already_truncated=source_truncated,
                        )
                    except Exception:
                        artifact_error = "artifact_write_failed"
                        logging.exception(
                            "Background artifact persistence failed for task %s",
                            task_id,
                        )
                if artifact_error and len(output) > settings.TOOL_OUTPUT_MAX_CHARS:
                    # A large result may only be shortened after its recoverable
                    # evidence is persisted. Fail closed if that prerequisite fails.
                    self.tasks[task_id].update({
                        "status": "error",
                        "result": (
                            "Background result withheld because its evidence artifact "
                            "could not be stored (artifact_write_failed)."
                        ),
                        "exit_code": process.returncode,
                        "artifact": None,
                        "artifact_error": artifact_error,
                    })
                    return
                if receipt is not None or len(output) > settings.TOOL_OUTPUT_MAX_CHARS or artifact_error:
                    model_output, _ = format_tool_output(
                        output,
                        receipt=receipt,
                        status=status,
                        error_code="nonzero_exit" if process.returncode else None,
                        exit_code=process.returncode,
                        artifact_error=artifact_error,
                    )
                else:
                    model_output = output
                self.tasks[task_id].update({
                    "status": "completed" if process.returncode == 0 else "error",
                    "result": model_output,
                    "exit_code": process.returncode,
                    "artifact": receipt.to_dict() if receipt else None,
                    "artifact_error": artifact_error,
                })
        except subprocess.TimeoutExpired:
            self._terminate_process(task_id)
            self.tasks[task_id].update({
                "status": "error",
                "result": f"Timeout after {timeout} seconds"
            })
        except Exception as e:
            self.tasks[task_id].update({
                "status": "error",
                "result": str(e)
            })

        finally:
            with self._lock:
                self._processes.pop(task_id, None)
                self._threads.pop(task_id, None)

            # Send notification
            self.notifications.put({
                "task_id": task_id,
                "status": self.tasks[task_id]["status"],
                "result": self.tasks[task_id]["result"][:500],
                "artifact": self.tasks[task_id].get("artifact"),
                "artifact_error": self.tasks[task_id].get("artifact_error"),
            })

    def _terminate_process(self, task_id: str) -> None:
        with self._lock:
            process = self._processes.get(task_id)
        if not process or process.poll() is not None:
            return
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=1)

    def cancel(self, task_id: str) -> bool:
        """Cancel a running child process and retain a truthful terminal result."""
        task = self.tasks.get(task_id)
        if not task or task.get("status") != "running":
            return False
        task.update({"status": "cancelled", "result": "Cancelled by user"})
        self._terminate_process(task_id)
        return True

    def shutdown(self, wait_seconds: float = 2.0) -> None:
        """Cancel all running processes and briefly join worker threads."""
        for task_id in list(self.tasks):
            self.cancel(task_id)
        with self._lock:
            threads = list(self._threads.values())
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(timeout=wait_seconds)

    def check(self, task_id: Optional[str] = None) -> str:
        """Check background task status.

        Args:
            task_id: Specific task ID, or None to list all

        Returns:
            Task status and result
        """
        if task_id:
            task = self.tasks.get(task_id)
            if not task:
                return f"Unknown task: {task_id}"
            status = task["status"]
            result = task.get("result") or "(running)"
            return f"[{status}] {result}"
        else:
            # List all tasks
            if not self.tasks:
                return "No background tasks."

            lines = []
            for tid, task in self.tasks.items():
                status = task["status"]
                cmd_preview = task["command"][:60]
                lines.append(f"{tid}: [{status}] {cmd_preview}")
            return "\n".join(lines)

    def drain_notifications(self) -> list:
        """Drain all pending notifications.

        Returns:
            List of notification dicts
        """
        notifications = []
        while not self.notifications.empty():
            notifications.append(self.notifications.get_nowait())
        return notifications


# Per-user/session instances cache (session IDs alone are not a tenant boundary).
_bg_managers: Dict[tuple[int | None, str], BackgroundManager] = {}


def get_background_manager(session_id: str = None) -> BackgroundManager:
    """Get or create BackgroundManager instance for current session.

    Args:
        session_id: Session ID to get manager for. If None, uses context variable.

    Note: BackgroundManager is now per-session to prevent cross-session pollution.
    Each session should have its own background tasks, isolated from other sessions.
    """
    if session_id is None:
        from enterprise_agent.core.agent.tools.workspace import get_current_session_id
        session_id = get_current_session_id()

    from enterprise_agent.core.agent.tools.workspace import get_current_user_id

    user_id = get_current_user_id()

    # Keep a deterministic default bucket for CLI/tests that do not install a
    # session ContextVar. Returning a fresh manager here made background_run and
    # check_background observe different task registries and left orphan threads.
    session_id = session_id or "__default__"

    key = (user_id, session_id)
    if key not in _bg_managers:
        _bg_managers[key] = BackgroundManager(session_id=session_id, user_id=user_id)
    return _bg_managers[key]


def clear_background_manager(session_id: str | None) -> None:
    """Clear BackgroundManager for a session.

    Called when starting a new session or when background tasks should be reset.
    """
    from enterprise_agent.core.agent.tools.workspace import get_current_user_id

    key = (get_current_user_id(), session_id or "__default__")
    if key in _bg_managers:
        bg_mgr = _bg_managers[key]
        bg_mgr.shutdown()
        # Remove from cache
        del _bg_managers[key]


def shutdown_background_managers() -> None:
    """Cancel/reap every cached worker, used by process and test shutdown."""
    for manager in list(_bg_managers.values()):
        manager.shutdown()
    _bg_managers.clear()


# === Tool Definitions ===

@tool
def background_run(command: str, timeout: int = None) -> str:
    """Run a command in background thread.

    Use check_background to get results later.

    Args:
        command: Shell command to execute
        timeout: Maximum execution time in seconds (default from settings)

    Returns:
        Task ID and start message
    """
    return get_background_manager().run(command, timeout)


@tool
def check_background(task_id: Optional[str] = None) -> str:
    """Check background task status or list all tasks.

    Args:
        task_id: Specific task ID to check, or None to list all

    Returns:
        Task status and result, or list of all tasks
    """
    return get_background_manager().check(task_id)
