"""Task management tools for persistent file-based task tracking.

Includes:
- todo_update: Short-lived checklist tracking (TodoWrite)
- task_create: Create persistent task with ID
- task_get: Get task details by ID
- task_update: Update task status/dependencies
- task_list: List all tasks
- claim_task: Claim a task by ID
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

from langchain_core.tools import tool

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tools.workspace import get_user_workspace

# Task storage directory - relative to workspace
TASKS_DIR_NAME = ".tasks"


class TaskManager:
    """Persistent file-based task manager.

    Tasks are stored as JSON files in .tasks/ directory.
    Each task has: id, subject, description, status, owner, blockedBy
    """

    def __init__(self, workdir: Path = None):
        self.workdir = workdir or get_user_workspace()
        self.tasks_dir = self.workdir / TASKS_DIR_NAME
        self.tasks_dir.mkdir(exist_ok=True)

    def _next_id(self) -> int:
        """Get next available task ID."""
        ids = []
        for f in self.tasks_dir.glob("task_*.json"):
            try:
                ids.append(int(f.stem.split("_")[1]))
            except (ValueError, IndexError):
                pass
        return max(ids, default=0) + 1

    def _load(self, tid: int) -> dict:
        """Load task by ID."""
        path = self.tasks_dir / f"task_{tid}.json"
        if not path.exists():
            raise ValueError(f"Task {tid} not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def _save(self, task: dict) -> None:
        """Save task to file."""
        path = self.tasks_dir / f"task_{task['id']}.json"
        path.write_text(json.dumps(task, indent=2), encoding="utf-8")

    def create(self, subject: str, description: str = "") -> str:
        """Create a new task."""
        task = {
            "id": self._next_id(),
            "subject": subject,
            "description": description,
            "status": "pending",
            "owner": None,
            "blockedBy": []
        }
        self._save(task)
        return json.dumps(task, indent=2)

    def get(self, tid: int) -> str:
        """Get task details."""
        return json.dumps(self._load(tid), indent=2)

    def update(
        self,
        tid: int,
        status: Optional[str] = None,
        add_blocked_by: Optional[List[int]] = None,
        remove_blocked_by: Optional[List[int]] = None,
        owner: Optional[str] = None
    ) -> str:
        """Update task status, dependencies, or owner."""
        task = self._load(tid)

        if status:
            valid_statuses = ("pending", "in_progress", "completed", "deleted")
            if status not in valid_statuses:
                raise ValueError(f"Invalid status: {status}")
            task["status"] = status

            # When completed, remove from other tasks' blockedBy
            if status == "completed":
                for f in self.tasks_dir.glob("task_*.json"):
                    t = json.loads(f.read_text(encoding="utf-8"))
                    if tid in t.get("blockedBy", []):
                        t["blockedBy"].remove(tid)
                        self._save(t)

            # When deleted, remove file
            if status == "deleted":
                (self.tasks_dir / f"task_{tid}.json").unlink(missing_ok=True)
                return f"Task {tid} deleted"

        if owner:
            task["owner"] = owner

        if add_blocked_by:
            task["blockedBy"] = list(set(task.get("blockedBy", []) + add_blocked_by))

        if remove_blocked_by:
            task["blockedBy"] = [x for x in task.get("blockedBy", []) if x not in remove_blocked_by]

        self._save(task)
        return json.dumps(task, indent=2)

    def list_all(self) -> str:
        """List all tasks."""
        tasks = []
        for f in sorted(self.tasks_dir.glob("task_*.json")):
            try:
                tasks.append(json.loads(f.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue

        if not tasks:
            return "No tasks."

        lines = []
        for t in tasks:
            status_markers = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
                "deleted": "[D]"
            }
            marker = status_markers.get(t.get("status", "pending"), "[?]")
            owner = f" @{t.get('owner')}" if t.get("owner") else ""
            blocked = f" (blocked by: {t.get('blockedBy', [])})" if t.get("blockedBy") else ""
            lines.append(f"{marker} #{t['id']}: {t['subject']}{owner}{blocked}")

        return "\n".join(lines)

    def claim(self, tid: int, owner: str) -> str:
        """Claim a task for an owner."""
        task = self._load(tid)
        task["owner"] = owner
        task["status"] = "in_progress"
        self._save(task)
        return f"Claimed task #{tid} for {owner}"


class TodoManager:
    """Short-lived checklist tracking (TodoWrite).

    Different from TaskManager - todos are ephemeral in-session checklists.
    Max items and in_progress limits configured in settings.
    """

    def __init__(self):
        self.items: List[Dict] = []

    def update(self, items: List[Dict]) -> str:
        """Update todo list with validation."""
        validated = []
        in_progress_count = 0

        for i, item in enumerate(items):
            content = str(item.get("content", "")).strip()
            status = str(item.get("status", "pending")).lower()
            active_form = str(item.get("activeForm", "")).strip()

            # Validation
            if not content:
                raise ValueError(f"Item {i}: content required")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {i}: invalid status '{status}'")
            if not active_form:
                raise ValueError(f"Item {i}: activeForm required")

            if status == "in_progress":
                in_progress_count += 1

            validated.append({
                "content": content,
                "status": status,
                "activeForm": active_form
            })

        # Constraints
        if len(validated) > settings.TODO_MAX_ITEMS:
            raise ValueError(f"Maximum {settings.TODO_MAX_ITEMS} todos allowed")
        if in_progress_count > settings.TODO_MAX_IN_PROGRESS:
            raise ValueError(f"Only {settings.TODO_MAX_IN_PROGRESS} in_progress item(s) allowed at a time")

        self.items = validated
        return self.render()

    def render(self) -> str:
        """Render todo list as formatted string."""
        if not self.items:
            return "No todos."

        lines = []
        for item in self.items:
            markers = {
                "completed": "[x]",
                "in_progress": "[>]",
                "pending": "[ ]"
            }
            marker = markers.get(item["status"], "[?]")
            suffix = f" <- {item['activeForm']}" if item["status"] == "in_progress" else ""
            lines.append(f"{marker} {item['content']}{suffix}")

        done_count = sum(1 for t in self.items if t["status"] == "completed")
        lines.append(f"\n({done_count}/{len(self.items)} completed)")

        return "\n".join(lines)

    def has_open_items(self) -> bool:
        """Check if there are uncompleted items."""
        return any(item.get("status") != "completed" for item in self.items)


# Per-session instances cache (to prevent cross-session todo pollution)
_task_managers: Dict[int, TaskManager] = {}
_todo_managers: Dict[str, TodoManager] = {}  # Key is session_id, not user_id


def get_task_manager() -> TaskManager:
    """Get or create TaskManager instance for current user."""
    from enterprise_agent.core.agent.tools.workspace import get_current_user_id
    user_id = get_current_user_id()

    if user_id not in _task_managers:
        _task_managers[user_id] = TaskManager()
    return _task_managers[user_id]


def get_todo_manager(session_id: str = None) -> TodoManager:
    """Get or create TodoManager instance for current session.

    Args:
        session_id: Session ID to get todo manager for. If None, creates empty manager.

    Note: TodoManager is now per-session to prevent cross-session todo pollution.
    Each session should have its own todo list, managed via AgentState.todos.
    """
    if session_id is None:
        # Return empty manager for operations that don't need session context
        return TodoManager()

    if session_id not in _todo_managers:
        _todo_managers[session_id] = TodoManager()
    return _todo_managers[session_id]


def clear_todo_manager(session_id: str) -> None:
    """Clear TodoManager for a session.

    Called when starting a new session or when todos should be reset.
    """
    if session_id in _todo_managers:
        _todo_managers[session_id].items = []
        # Optionally remove from cache entirely
        del _todo_managers[session_id]


# === Tool Definitions ===

@tool
def todo_update(todos: List[Dict]) -> str:
    """Update todo list with status tracking.

    Args:
        todos: List of todo items with 'content', 'status', and 'activeForm' fields

    Returns:
        Rendered todo list string
    """
    return get_todo_manager().update(todos)


@tool
def task_create(subject: str, description: str = "") -> str:
    """Create a persistent file task.

    Args:
        subject: Brief task title
        description: Detailed task description

    Returns:
        Created task as JSON
    """
    return get_task_manager().create(subject, description)


@tool
def task_get(task_id: int) -> str:
    """Get task details by ID.

    Args:
        task_id: Task ID number

    Returns:
        Task details as JSON
    """
    return get_task_manager().get(task_id)


@tool
def task_update(
    task_id: int,
    status: Optional[str] = None,
    add_blocked_by: Optional[List[int]] = None,
    remove_blocked_by: Optional[List[int]] = None
) -> str:
    """Update task status or dependencies.

    Args:
        task_id: Task ID number
        status: New status (pending, in_progress, completed, deleted)
        add_blocked_by: Task IDs to add as blockers
        remove_blocked_by: Task IDs to remove from blockers

    Returns:
        Updated task as JSON
    """
    return get_task_manager().update(task_id, status, add_blocked_by, remove_blocked_by)


@tool
def task_list() -> str:
    """List all persistent file-based task-tracking items from the .tasks/ directory.

    CRITICAL: These are OPERATIONAL task-tracking records (created by `task_create`),
    NOT user long-term memory. Do NOT use this tool when the user asks about their
    memory, preferences, history, or "what do you remember about me". For long-term
    memory queries, use `search_memory` instead — it searches the ChromaDB vector
    database where actual user memories (conversations, preferences, decisions) are stored.

    Returns:
        Formatted list of all tasks
    """
    return get_task_manager().list_all()


@tool
def claim_task(task_id: int, owner: str) -> str:
    """Claim a task from the board.

    Args:
        task_id: Task ID to claim
        owner: Name of the claimer

    Returns:
        Success message
    """
    return get_task_manager().claim(task_id, owner)