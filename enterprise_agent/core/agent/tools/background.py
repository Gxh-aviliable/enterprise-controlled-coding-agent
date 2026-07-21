"""Background task management tools.

Provides tools for running long-running commands in background threads
and checking their status later.
"""

import os
import signal
import subprocess
import threading
import uuid
from pathlib import Path
from queue import Queue
from typing import Dict, Optional

from langchain_core.tools import tool

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tools.shell import _safe_subprocess_environment, validate_command
from enterprise_agent.core.agent.tools.workspace import get_user_workspace


class BackgroundManager:
    """Manages background task execution.

    Tasks run in separate threads, results stored in memory.
    Notifications queue for completed task alerts.
    """

    def __init__(self):
        self.tasks: dict = {}
        self.notifications: Queue = Queue()
        self._threads: dict[str, threading.Thread] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

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
            process = subprocess.Popen(
                command,
                shell=True,
                cwd=workdir,
                env=_safe_subprocess_environment(workdir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                **process_group_args,
            )
            with self._lock:
                self._processes[task_id] = process
            if self.tasks[task_id].get("status") == "cancelled":
                self._terminate_process(task_id)
                return
            stdout, stderr = process.communicate(timeout=timeout)
            output = ((stdout or "") + (stderr or "")).strip()
            if self.tasks[task_id].get("status") != "cancelled":
                self.tasks[task_id].update({
                    "status": "completed" if process.returncode == 0 else "error",
                    "result": output[:settings.TOOL_OUTPUT_MAX_CHARS] or "(no output)",
                    "exit_code": process.returncode,
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
                "result": self.tasks[task_id]["result"][:500]
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


# Per-session instances cache (to prevent cross-session pollution)
_bg_managers: Dict[str, BackgroundManager] = {}  # Key is session_id


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

    if session_id is None:
        # Return empty manager for operations that don't need session context
        return BackgroundManager()

    if session_id not in _bg_managers:
        _bg_managers[session_id] = BackgroundManager()
    return _bg_managers[session_id]


def clear_background_manager(session_id: str) -> None:
    """Clear BackgroundManager for a session.

    Called when starting a new session or when background tasks should be reset.
    """
    if session_id in _bg_managers:
        bg_mgr = _bg_managers[session_id]
        bg_mgr.shutdown()
        # Remove from cache
        del _bg_managers[session_id]


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
