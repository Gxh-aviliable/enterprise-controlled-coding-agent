"""Tests for background module (background_run, check_background)."""

import hashlib
import os

import pytest

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.nodes import check_background_node
from enterprise_agent.core.agent.tools.background import (
    BackgroundManager,
    background_run,
    check_background,
    clear_background_manager,
    get_background_manager,
)
from enterprise_agent.core.agent.tools.workspace import (
    get_user_workspace,
    set_current_session_id,
    set_current_user_id,
)


class TestBackgroundManager:
    """Test BackgroundManager class."""

    def test_run_creates_task(self):
        """Test running a command creates a task."""
        manager = BackgroundManager()
        result = manager.run("echo test")
        assert "started" in result.lower()
        assert len(manager.tasks) == 1

    def test_run_blocked_command_returns_error(self):
        """Test running blocked command returns error."""
        manager = BackgroundManager()
        result = manager.run("rm -rf /")
        assert "Error" in result or "Blocked" in result

    def test_check_nonexistent_task(self):
        """Test checking nonexistent task."""
        manager = BackgroundManager()
        result = manager.check("nonexistent_id")
        assert "Unknown" in result

    def test_check_lists_all_tasks(self):
        """Test checking without task_id lists all tasks."""
        manager = BackgroundManager()
        manager.run("echo test1")
        manager.run("echo test2")

        result = manager.check(None)
        # Should mention both tasks
        assert "echo test" in result or len(manager.tasks) == 2

    def test_check_empty_tasks(self):
        """Test checking when no tasks."""
        manager = BackgroundManager()
        result = manager.check(None)
        assert "No background" in result

    def test_task_id_is_generated(self):
        """Test that task ID is generated."""
        manager = BackgroundManager()
        result = manager.run("echo test")
        # Task ID should be 8 character hex
        assert "task" in result.lower()

    def test_cancel_trace_only_stops_and_joins_exact_trace(self, monkeypatch):
        """Stop for one trace must leave sibling tasks in the same Session untouched."""
        manager = BackgroundManager(session_id="shared-session", user_id=8)
        manager.tasks = {
            "old-running": {
                "status": "running",
                "command": "python old.py",
                "result": None,
                "trace_id": "trace-old",
            },
            "new-running": {
                "status": "running",
                "command": "python new.py",
                "result": None,
                "trace_id": "trace-new",
            },
            "old-finished": {
                "status": "completed",
                "command": "echo done",
                "result": "done",
                "trace_id": "trace-old",
            },
        }
        joined = []

        class FakeThread:
            def __init__(self, task_id):
                self.task_id = task_id

            def join(self, timeout):
                joined.append((self.task_id, timeout))

        manager._threads = {
            "old-running": FakeThread("old-running"),
            "new-running": FakeThread("new-running"),
        }

        def fake_cancel(task_id):
            manager.tasks[task_id].update({
                "status": "cancelled",
                "result": "Cancelled by user",
            })
            return True

        monkeypatch.setattr(manager, "cancel", fake_cancel)

        count = manager.cancel_trace("trace-old", wait_seconds=0.25)

        assert count == 1
        assert manager.tasks["old-running"]["status"] == "cancelled"
        assert manager.tasks["new-running"]["status"] == "running"
        assert manager.tasks["old-finished"]["status"] == "completed"
        assert joined == [("old-running", 0.25)]


class TestBackgroundRunTool:
    """Test background_run tool."""

    def test_background_run_returns_task_id(self, mock_workspace_env):
        """Test background_run returns task ID."""
        result = background_run.invoke({"command": "echo test"})
        assert "task" in result.lower() or "started" in result.lower()

    def test_background_run_with_timeout(self, mock_workspace_env):
        """Test background_run with timeout parameter."""
        result = background_run.invoke({
            "command": "echo test",
            "timeout": 10
        })
        assert isinstance(result, str)


class TestCheckBackgroundTool:
    """Test check_background tool."""

    def test_check_background_lists_tasks(self, mock_workspace_env):
        """Test check_background lists tasks."""
        # Run a background task first
        background_run.invoke({"command": "echo test"})

        result = check_background.invoke({})
        assert isinstance(result, str)


class TestBackgroundTaskCompletion:
    """Test background task completion behavior."""

    def test_notifications_queue_exists(self):
        """Test notifications queue exists."""
        manager = BackgroundManager()
        assert manager.notifications is not None

    @pytest.mark.skipif(os.name == "nt", reason="POSIX runtime uses explicit Bash")
    def test_posix_background_commands_use_bash(self, mock_workspace_env):
        manager = BackgroundManager()
        manager.run("echo {background,bash}")
        task_id = next(iter(manager.tasks))
        thread = manager._threads[task_id]

        thread.join(timeout=2)

        assert manager.tasks[task_id]["status"] == "completed"
        assert manager.tasks[task_id]["result"] == "background bash"

    def test_drain_notifications_empty(self):
        """Test draining empty notifications."""
        manager = BackgroundManager()
        notifications = manager.drain_notifications()
        assert notifications == []

    def test_shutdown_cancels_and_reaps_running_process(self, mock_workspace_env):
        manager = BackgroundManager()
        (get_user_workspace() / "sleep_task.py").write_text(
            "import time\ntime.sleep(10)\n",
            encoding="utf-8",
        )
        manager.run("python sleep_task.py", timeout=20)
        task_id = next(iter(manager.tasks))

        manager.shutdown(wait_seconds=2)

        assert manager.tasks[task_id]["status"] == "cancelled"
        assert manager._processes == {}
        assert manager._threads == {}

    def test_worker_observes_redis_cancel_without_local_clear(
        self,
        tmp_path,
        monkeypatch,
    ):
        """A remote-worker tombstone must terminate the owning background process."""
        from enterprise_agent.core.execution.interrupt_control import (
            get_current_task_control_identity,
        )

        manager = BackgroundManager(session_id="remote-stop-session", user_id=601)
        task_id = "remote-stop"
        trace_id = "trace-remote-stop"
        manager.tasks[task_id] = {
            "status": "running",
            "command": "python long_running.py",
            "result": None,
            "timeout": 30,
            "trace_id": trace_id,
        }
        checked_identities = []
        terminated = []

        class FakeProcess:
            pid = 2468
            returncode = -15

            def poll(self):
                return None

        def redis_cancel_checker():
            checked_identities.append(get_current_task_control_identity())
            return True

        monkeypatch.setattr(
            "enterprise_agent.core.agent.tools.background.subprocess.Popen",
            lambda *_args, **_kwargs: FakeProcess(),
        )
        monkeypatch.setattr(
            "enterprise_agent.core.execution.interrupt_control."
            "is_current_task_cancel_requested_sync",
            redis_cancel_checker,
        )
        monkeypatch.setattr(
            "enterprise_agent.core.agent.tools.background._terminate_process_group",
            lambda process: terminated.append(process.pid) or "process_group_term",
        )

        # No local clear_background_manager/cancel_trace call is made. The
        # worker must discover the Redis tombstone through its bound identity.
        manager._execute(
            task_id,
            "python long_running.py",
            30,
            tmp_path,
            trace_id,
        )

        task = manager.tasks[task_id]
        assert checked_identities == [(601, "remote-stop-session", trace_id)]
        assert terminated == [2468]
        assert task["status"] == "cancelled"
        assert task["cancellation"] == "terminated"
        assert task["termination_mode"] == "process_group_term"
        assert task["result"] == (
            "Cancelled by user "
            "(terminated; termination_mode=process_group_term)"
        )
        assert manager._processes == {}
        notification = manager.notifications.get_nowait()
        assert notification["task_id"] == task_id
        assert notification["status"] == "cancelled"
        assert "termination_mode=process_group_term" in notification["result"]
        assert get_current_task_control_identity() is None

    @pytest.mark.asyncio
    async def test_long_result_receipt_survives_notification_and_node_injection(
        self,
        mock_workspace_env,
        monkeypatch,
    ):
        """A bounded source capture keeps exit status and a real recovery path."""
        monkeypatch.setattr(settings, "TOOL_SOURCE_CAPTURE_MAX_BYTES", 240)
        monkeypatch.setattr(settings, "TOOL_ARTIFACT_MAX_CHARS", 2_000)
        monkeypatch.setattr(settings, "TOOL_OUTPUT_MAX_CHARS", 500)
        user_id = 501
        session_id = "background-artifact-receipt"
        set_current_user_id(user_id)
        set_current_session_id(session_id)

        try:
            manager = get_background_manager(session_id)
            workspace = get_user_workspace(user_id)
            (workspace / "emit_long_background.py").write_text(
                "import sys\n"
                "sys.stdout.write('HEAD_FACT=' + ('x' * 4000) + '_TAIL_FACT\\n')\n"
                "sys.stderr.write('FAILURE_FACT=background failed\\n')\n"
                "raise SystemExit(7)\n",
                encoding="utf-8",
            )
            task_id = "bgreceipt"
            manager.tasks[task_id] = {
                "status": "running",
                "command": "python emit_long_background.py",
                "result": None,
                "timeout": 10,
            }

            manager._execute(
                task_id,
                "python emit_long_background.py",
                10,
                workspace,
            )

            task = manager.tasks[task_id]
            receipt = task["artifact"]
            assert task["status"] == "error"
            assert task["exit_code"] == 7
            assert isinstance(receipt, dict)
            assert receipt["storage_status"] == "stored"
            assert receipt["source_truncated"] is True
            assert receipt["path"].startswith(
                ".agent/tool-artifacts/background-background-artifact-receipt/"
            )

            artifact_path = workspace / receipt["path"]
            stored = artifact_path.read_bytes()
            assert artifact_path.is_file()
            assert hashlib.sha256(stored).hexdigest() == receipt["sha256"]
            assert b"HEAD_FACT=" in stored
            assert b"_TAIL_FACT" in stored
            assert b"FAILURE_FACT=background failed" in stored

            notifications = list(manager.drain_notifications())
            assert len(notifications) == 1
            notification = notifications[0]
            assert notification["status"] == "error"
            assert notification["artifact"] == receipt
            assert notification["artifact_error"] is None

            # Requeue the authoritative notification so the graph node consumes
            # the same structured receipt produced by the worker.
            manager.notifications.put(notification)
            update = await check_background_node({
                "session_id": session_id,
                "user_id": user_id,
            })
            content = update["messages"][0]["content"]
            assert receipt["path"] in content
            assert receipt["sha256"] in content
            assert "artifact unavailable" not in content
        finally:
            clear_background_manager(session_id)
            set_current_session_id(None)
            set_current_user_id(None)

    @pytest.mark.asyncio
    async def test_large_result_fails_closed_when_background_artifact_write_fails(
        self,
        mock_workspace_env,
        monkeypatch,
    ):
        """A storage outage must not downgrade a large result to an untracked preview."""
        monkeypatch.setattr(settings, "TOOL_SOURCE_CAPTURE_MAX_BYTES", 2_000)
        monkeypatch.setattr(settings, "TOOL_OUTPUT_MAX_CHARS", 200)
        user_id = 502
        session_id = "background-artifact-failure"
        set_current_user_id(user_id)
        set_current_session_id(session_id)

        def fail_artifact_write(*_args, **_kwargs):
            raise OSError("private server path must not reach the model")

        monkeypatch.setattr(
            "enterprise_agent.core.agent.tools.background.ToolArtifactStore.save",
            fail_artifact_write,
        )

        try:
            manager = get_background_manager(session_id)
            workspace = get_user_workspace(user_id)
            (workspace / "emit_unstored_background.py").write_text(
                "print('UNTRACKED_RAW_OUTPUT=' + ('z' * 1000))\n",
                encoding="utf-8",
            )
            task_id = "bgfailure"
            manager.tasks[task_id] = {
                "status": "running",
                "command": "python emit_unstored_background.py",
                "result": None,
                "timeout": 10,
            }

            manager._execute(
                task_id,
                "python emit_unstored_background.py",
                10,
                workspace,
            )

            task = manager.tasks[task_id]
            assert task["status"] == "error"
            assert task["result"] == (
                "Background result withheld because its evidence artifact "
                "could not be stored (artifact_write_failed)."
            )
            assert task["exit_code"] == 0
            assert task["artifact"] is None
            assert task["artifact_error"] == "artifact_write_failed"
            assert "UNTRACKED_RAW_OUTPUT" not in task["result"]
            assert "private server path" not in task["result"]

            update = await check_background_node({
                "session_id": session_id,
                "user_id": user_id,
            })
            content = update["messages"][0]["content"]
            assert "artifact unavailable: artifact_write_failed" in content
            assert "UNTRACKED_RAW_OUTPUT" not in content
            assert "private server path" not in content
        finally:
            clear_background_manager(session_id)
            set_current_session_id(None)
            set_current_user_id(None)
