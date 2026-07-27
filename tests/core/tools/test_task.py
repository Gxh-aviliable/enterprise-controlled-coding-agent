"""Tests for task module (todo_update, task_create, etc.)."""

import json
from pathlib import Path

import pytest

from enterprise_agent.core.agent.tools.task import (
    TaskManager,
    TodoManager,
    claim_task,
    clear_task_managers,
    task_create,
    task_list,
    todo_update,
)


class TestTodoManager:
    """Test TodoManager class."""

    def test_update_with_valid_items(self):
        """Test updating with valid todo items."""
        manager = TodoManager()
        items = [
            {"content": "Task 1", "status": "pending", "activeForm": "Doing Task 1"},
            {"content": "Task 2", "status": "completed", "activeForm": "Doing Task 2"},
        ]
        result = manager.update(items)
        assert "Task 1" in result
        assert "[x]" in result  # completed marker

    def test_update_requires_content(self):
        """Test that update requires content field."""
        manager = TodoManager()
        items = [
            {"content": "", "status": "pending", "activeForm": "test"},
        ]
        with pytest.raises(ValueError, match="content required"):
            manager.update(items)

    def test_update_requires_active_form(self):
        """Test that update requires activeForm field."""
        manager = TodoManager()
        items = [
            {"content": "Task", "status": "pending", "activeForm": ""},
        ]
        with pytest.raises(ValueError, match="activeForm required"):
            manager.update(items)

    def test_update_validates_status(self):
        """Test that update validates status values."""
        manager = TodoManager()
        items = [
            {"content": "Task", "status": "invalid", "activeForm": "test"},
        ]
        with pytest.raises(ValueError, match="invalid status"):
            manager.update(items)

    def test_valid_statuses(self):
        """Test valid status values."""
        manager = TodoManager()
        for status in ["pending", "in_progress", "completed"]:
            items = [
                {"content": f"Task {status}", "status": status, "activeForm": f"Doing {status}"},
            ]
            result = manager.update(items)
            assert isinstance(result, str)

    def test_render_shows_progress(self):
        """Test that render shows completion progress."""
        manager = TodoManager()
        items = [
            {"content": "Task 1", "status": "completed", "activeForm": "Done 1"},
            {"content": "Task 2", "status": "pending", "activeForm": "Doing 2"},
        ]
        manager.update(items)
        result = manager.render()
        assert "1/2" in result  # 1 completed out of 2

    def test_has_open_items(self):
        """Test has_open_items detection."""
        manager = TodoManager()

        # All completed
        manager.update([
            {"content": "Task", "status": "completed", "activeForm": "Done"},
        ])
        assert not manager.has_open_items()

        # Has pending
        manager.update([
            {"content": "Task", "status": "pending", "activeForm": "Doing"},
        ])
        assert manager.has_open_items()


class TestTodoUpdateTool:
    """Test todo_update tool."""

    def test_todo_update_returns_formatted_result(self):
        """Test todo_update returns formatted string."""
        result = todo_update.invoke({
            "todos": [
                {"content": "Write code", "status": "pending", "activeForm": "Writing code"},
            ]
        })
        assert isinstance(result, str)
        assert "Write code" in result


class TestTaskManager:
    """Test TaskManager class."""

    @pytest.fixture
    def task_manager(self, temp_workspace: Path):
        """Create TaskManager with temp directory."""
        return TaskManager(temp_workspace)

    def test_create_task(self, task_manager: TaskManager):
        """Test creating a task."""
        result = task_manager.create("Test Task", "Description")
        task = json.loads(result)
        assert task["subject"] == "Test Task"
        assert task["status"] == "pending"
        assert task["id"] > 0

    def test_get_task(self, task_manager: TaskManager):
        """Test getting a task by ID."""
        create_result = task_manager.create("Test Task", "Description")
        task = json.loads(create_result)
        task_id = task["id"]

        get_result = task_manager.get(task_id)
        retrieved = json.loads(get_result)
        assert retrieved["subject"] == "Test Task"

    def test_get_nonexistent_task(self, task_manager: TaskManager):
        """Test getting nonexistent task raises error."""
        with pytest.raises(ValueError, match="not found"):
            task_manager.get(99999)

    def test_update_task_status(self, task_manager: TaskManager):
        """Test updating task status."""
        create_result = task_manager.create("Test Task", "Description")
        task = json.loads(create_result)
        task_id = task["id"]

        update_result = task_manager.update(task_id, status="in_progress")
        updated = json.loads(update_result)
        assert updated["status"] == "in_progress"

    def test_update_task_invalid_status(self, task_manager: TaskManager):
        """Test updating with invalid status raises error."""
        create_result = task_manager.create("Test Task", "Description")
        task = json.loads(create_result)
        task_id = task["id"]

        with pytest.raises(ValueError, match="Invalid status"):
            task_manager.update(task_id, status="invalid_status")

    def test_delete_task(self, task_manager: TaskManager):
        """Test deleting a task."""
        create_result = task_manager.create("Test Task", "Description")
        task = json.loads(create_result)
        task_id = task["id"]

        result = task_manager.update(task_id, status="deleted")
        assert "deleted" in result

        # Task file should be removed
        with pytest.raises(ValueError):
            task_manager.get(task_id)

    def test_claim_task(self, task_manager: TaskManager):
        """Test claiming a task."""
        create_result = task_manager.create("Test Task", "Description")
        task = json.loads(create_result)
        task_id = task["id"]

        claim_result = task_manager.claim(task_id, "alice")
        assert "Claimed" in claim_result

        # Check task is now claimed
        get_result = task_manager.get(task_id)
        retrieved = json.loads(get_result)
        assert retrieved["owner"] == "alice"
        assert retrieved["status"] == "in_progress"

    def test_add_blocked_by(self, task_manager: TaskManager):
        """Test adding blockedBy dependency."""
        # Create two tasks
        task1_result = task_manager.create("Task 1", "Description")
        task1 = json.loads(task1_result)
        task1_id = task1["id"]

        task2_result = task_manager.create("Task 2", "Description")
        task2 = json.loads(task2_result)
        task2_id = task2["id"]

        # Make task2 blocked by task1
        task_manager.update(task2_id, add_blocked_by=[task1_id])

        get_result = task_manager.get(task2_id)
        retrieved = json.loads(get_result)
        assert task1_id in retrieved["blockedBy"]

    def test_list_all_tasks(self, task_manager: TaskManager):
        """Test listing all tasks."""
        task_manager.create("Task 1", "Description")
        task_manager.create("Task 2", "Description")

        result = task_manager.list_all()
        assert "Task 1" in result
        assert "Task 2" in result

    def test_list_empty_tasks(self, task_manager: TaskManager):
        """Test listing when no tasks."""
        result = task_manager.list_all()
        assert "No tasks" in result


class TestTaskTools:
    """Test task tools."""

    def test_task_create_tool(self, mock_workspace_env):
        """Test task_create tool."""
        result = task_create.invoke({
            "subject": "Test Task",
            "description": "Test Description"
        })
        task = json.loads(result)
        assert task["subject"] == "Test Task"

    def test_task_list_tool(self, mock_workspace_env):
        """Test task_list tool."""
        # Create a task first
        task_create.invoke({"subject": "Test Task"})
        result = task_list.invoke({})
        assert isinstance(result, str)

    def test_claim_task_tool(self, mock_workspace_env):
        """Test claim_task tool."""
        # Create a task
        create_result = task_create.invoke({"subject": "Test Task"})
        task = json.loads(create_result)
        task_id = task["id"]

        result = claim_task.invoke({"task_id": task_id, "owner": "test_user"})
        assert "Claimed" in result

    def test_task_manager_tracks_workspace_changes(self, temp_workspace, monkeypatch):
        """A cached user manager must not retain a deleted/old workspace path."""
        workspace_a = temp_workspace / "workspace-a"
        workspace_b = temp_workspace / "workspace-b"
        clear_task_managers()

        monkeypatch.setenv("WORKSPACE_BASE", str(workspace_a))
        first = json.loads(task_create.invoke({"subject": "First"}))
        assert first["id"] == 1

        monkeypatch.setenv("WORKSPACE_BASE", str(workspace_b))
        second = json.loads(task_create.invoke({"subject": "Second"}))
        assert second["id"] == 1
        assert (workspace_b / "default" / ".tasks" / "task_1.json").exists()

        clear_task_managers()
