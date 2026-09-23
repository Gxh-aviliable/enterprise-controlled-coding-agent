"""Background task management tools.

Provides tools for running long-running commands in background threads
and checking their status later.
"""

import json
import logging
import subprocess
import threading
import uuid
from pathlib import Path
from queue import Queue
from typing import Dict, Optional

from langchain_core.tools import tool

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tool_artifacts import ToolArtifactStore, format_tool_output
from enterprise_agent.core.agent.tools.contracts import (
    get_tool_contract,
    should_persist_artifact,
)
from enterprise_agent.core.agent.tools.shell import validate_command
from enterprise_agent.core.agent.tools.workspace import get_user_workspace
from enterprise_agent.sandbox.executor import ExecutionRequest, execute


class BackgroundManager:
    """Manages background task execution.

    Tasks run in separate threads, results stored in memory.
    Notifications queue for completed task alerts.
    """

    def __init__(self, *, session_id: str | None = None, user_id: int | None = None):
        self.tasks: dict = {}
        self.notifications: Queue = Queue()
        self._threads: dict[str, threading.Thread] = {}
        self._cancel_events: dict[str, threading.Event] = {}
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
        timeout = min(timeout, settings.COMMAND_TIMEOUT_SECONDS)
        error = validate_command(command)
        if error:
            return error

        try:
            from enterprise_agent.core.execution.interrupt_control import (
                get_current_task_control_identity,
            )

            control_identity = get_current_task_control_identity()
        except ImportError:
            control_identity = None
        trace_id = control_identity[2] if control_identity else None

        task_id = str(uuid.uuid4())[:8]
        self._cancel_events[task_id] = threading.Event()
        self.tasks[task_id] = {
            "status": "running",
            "command": command,
            "result": None,
            "timeout": timeout,
            "trace_id": trace_id,
        }

        # Freeze the user/workspace context before spawning. ContextVars are
        # not reliably propagated to arbitrary threads, and resolving the
        # workspace inside the worker races with request/test cleanup.
        from enterprise_agent.core.agent.tools.workspace import get_current_user_id

        user_id = get_current_user_id()
        workdir = get_user_workspace(user_id)
        if self.user_id is None:
            self.user_id = user_id
        from enterprise_agent.core.execution.evidence import save_receipt

        save_receipt({"execution_id": "background-" + task_id, "trace_id": trace_id,
                      "phase": "running", "changes": []}, self.user_id, workdir)

        # Start execution thread with explicit user_id
        from contextvars import copy_context

        thread = threading.Thread(
            target=copy_context().run,
            args=(self._execute, task_id, command, timeout, workdir, trace_id),
            daemon=True
        )
        with self._lock:
            self._threads[task_id] = thread
        thread.start()

        return f"Background task {task_id} started: {command[:80]}..."

    def _execute(
        self,
        task_id: str,
        command: str,
        timeout: int,
        workdir: Path,
        trace_id: str | None = None,
    ) -> None:
        """Execute command in thread.

        Args:
            task_id: Task identifier
            command: Shell command to execute
            timeout: Maximum execution time
            workdir: Resolved workspace captured in the request thread
            trace_id: Exact Agent trace that owns the process, when available
        """
        control_token = None
        self._cancel_events.setdefault(task_id, threading.Event())
        try:
            if trace_id is not None and self.user_id is not None:
                from enterprise_agent.core.execution.interrupt_control import (
                    set_current_task_control_identity,
                )

                control_token = set_current_task_control_identity(
                    self.user_id,
                    self.session_id,
                    trace_id,
                )
            from enterprise_agent.core.execution.interrupt_control import is_current_task_cancel_requested_sync

            def cancelled():
                return self._cancel_events[task_id].is_set() or is_current_task_cancel_requested_sync()

            execution = execute(ExecutionRequest(command, workdir, self.user_id, trace_id, timeout,
                                                 execution_id="background-" + task_id), cancelled)
            self.tasks[task_id]["execution"] = execution
            self.tasks[task_id].update({key: execution[key] for key in
                                       ("exit_code", "error_code", "source_truncated", "cleanup_confirmed")
                                       if key in execution})
            if execution.get("error_code"):
                code = execution["error_code"]
                self.tasks[task_id].update(
                    status="cancelled" if code == "task_cancelled" else "error",
                    result=execution.get("stderr") or (f"Timeout after {timeout} seconds" if code == "tool_timeout"
                                                        else code),
                    cancellation=execution.get("cancellation"),
                    termination_mode=execution.get("termination_mode"),
                )
                return
            output = "\n".join(part for part in (execution["stdout"], execution["stderr"]) if part).strip()
            source_truncated = execution.get("source_truncated", False)
            if self.tasks[task_id].get("status") != "cancelled":
                status = "success" if execution["exit_code"] == 0 else "error"
                receipt = None
                artifact_error = None
                if should_persist_artifact(
                    get_tool_contract("check_background"),
                    raw_chars=len(output),
                    source_truncated=source_truncated,
                ):
                    try:
                        receipt = ToolArtifactStore(workdir=workdir).save(
                            output,
                            trace_id=trace_id or f"background-{self.session_id}",
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
                        "exit_code": execution["exit_code"],
                        "artifact": None,
                        "artifact_error": artifact_error,
                    })
                    return
                if receipt is not None or len(output) > settings.TOOL_OUTPUT_MAX_CHARS or artifact_error:
                    model_output, _ = format_tool_output(
                        output,
                        receipt=receipt,
                        status=status,
                        error_code="nonzero_exit" if execution["exit_code"] else None,
                        exit_code=execution["exit_code"],
                        artifact_error=artifact_error,
                    )
                else:
                    model_output = output
                self.tasks[task_id].update({
                    "status": "completed" if execution["exit_code"] == 0 else "error",
                    "result": model_output,
                    "exit_code": execution["exit_code"],
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
            if control_token is not None:
                from enterprise_agent.core.execution.interrupt_control import (
                    reset_current_task_control_identity,
                )

                reset_current_task_control_identity(control_token)
            with self._lock:
                self._cancel_events.pop(task_id, None)
                self._threads.pop(task_id, None)

            # Send notification
            self.notifications.put({
                "task_id": task_id,
                "status": self.tasks[task_id]["status"],
                "result": (self.tasks[task_id]["result"] or "")[:500],
                "artifact": self.tasks[task_id].get("artifact"),
                "artifact_error": self.tasks[task_id].get("artifact_error"),
            })

    def _terminate_process(self, task_id: str) -> str:
        event = self._cancel_events.get(task_id)
        if event:
            event.set()
        return "cleanup_requested"

    def cancel(self, task_id: str) -> bool:
        """Request cleanup; worker confirms terminal state only after executor returns."""
        task = self.tasks.get(task_id)
        if not task or task.get("status") != "running":
            return False
        self._terminate_process(task_id)
        task.update(result="Cancellation requested; waiting for execution cleanup", cancellation="requested")
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

    def cancel_trace(self, trace_id: str, wait_seconds: float = 2.0) -> int:
        """Cancel only processes launched by one exact Agent trace."""
        task_ids = [
            task_id
            for task_id, task in self.tasks.items()
            if task.get("trace_id") == trace_id and task.get("status") == "running"
        ]
        for task_id in task_ids:
            self.cancel(task_id)
        with self._lock:
            threads = [self._threads.get(task_id) for task_id in task_ids]
        for thread in threads:
            if thread and thread is not threading.current_thread():
                thread.join(timeout=wait_seconds)
        return len(task_ids)

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
            execution = task.get("execution")
            metadata = ""
            if execution:
                metadata = "\n" + json.dumps({k: v for k, v in execution.items()
                                              if k not in {"stdout", "stderr"}}, ensure_ascii=False)
            return f"[{status}] {result}{metadata}"
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


def clear_background_manager(session_id: str | None, trace_id: str | None = None) -> None:
    """Clear BackgroundManager for a session.

    Called when starting a new session or when background tasks should be reset.
    """
    from enterprise_agent.core.agent.tools.workspace import get_current_user_id

    key = (get_current_user_id(), session_id or "__default__")
    if key in _bg_managers:
        bg_mgr = _bg_managers[key]
        if trace_id:
            bg_mgr.cancel_trace(trace_id)
            return
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
