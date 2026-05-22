"""User workspace management with context variable for user isolation.

Provides per-user workspace directories to ensure different users
have isolated file systems.
"""

import os
from contextvars import ContextVar
from pathlib import Path

# Context variable to store current user_id
_current_user_id: ContextVar[int] = ContextVar('current_user_id', default=None)

# Context variable to store current session_id
_current_session_id: ContextVar[str] = ContextVar('current_session_id', default=None)

# Base workspace directory
WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_BASE", "/workspaces"))


def set_current_user_id(user_id: int) -> None:
    """Set the current user ID in context.

    Args:
        user_id: The user ID to set
    """
    _current_user_id.set(user_id)


def get_current_user_id() -> int:
    """Get the current user ID from context.

    Returns:
        User ID or None if not set
    """
    return _current_user_id.get()


def set_current_session_id(session_id: str) -> None:
    """Set the current session ID in context.

    Args:
        session_id: The session ID to set
    """
    _current_session_id.set(session_id)


def get_current_session_id() -> str:
    """Get the current session ID from context.

    Returns:
        Session ID or None if not set
    """
    return _current_session_id.get()


def get_user_workspace(user_id: int = None) -> Path:
    """Get the workspace directory for a user.

    Creates the directory if it doesn't exist.

    Args:
        user_id: User ID, or None to use current context

    Returns:
        Path to user's workspace directory
    """
    if user_id is None:
        user_id = get_current_user_id()

    if user_id is None:
        # Fallback to a default workspace (for backward compatibility)
        workspace = WORKSPACE_BASE / "default"
    else:
        workspace = WORKSPACE_BASE / f"user_{user_id}"

    # Create workspace if it doesn't exist
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def resolve_path(path: str, user_id: int = None) -> Path:
    """Resolve a path relative to user workspace.

    Ensures the path doesn't escape the workspace.

    Args:
        path: Relative path within workspace
        user_id: User ID, or None to use current context

    Returns:
        Resolved absolute path

    Raises:
        ValueError: If path escapes workspace
    """
    workdir = get_user_workspace(user_id).resolve()

    # Handle absolute paths
    if Path(path).is_absolute():
        resolved = Path(path).resolve()
    else:
        resolved = (workdir / path).resolve()

    # Security check: ensure path is within workspace
    # .resolve() on both sides ensures consistent drive letters on Windows
    if not resolved.is_relative_to(workdir):
        raise ValueError(f"Path escapes workspace: {path}")

    return resolved
