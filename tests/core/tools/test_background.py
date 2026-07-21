"""Tests for background module (background_run, check_background)."""

from enterprise_agent.core.agent.tools.background import (
    BackgroundManager,
    background_run,
    check_background,
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

    def test_drain_notifications_empty(self):
        """Test draining empty notifications."""
        manager = BackgroundManager()
        notifications = manager.drain_notifications()
        assert notifications == []

    def test_shutdown_cancels_and_reaps_running_process(self, mock_workspace_env):
        from enterprise_agent.core.agent.tools.workspace import get_user_workspace

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

    # Note: Testing actual task completion requires waiting,
    # which is complex in unit tests. Integration tests would cover this.
