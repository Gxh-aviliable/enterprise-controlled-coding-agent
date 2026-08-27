"""User workspace management with context variable for user isolation.

Provides per-user workspace directories to ensure different users
have isolated file systems.
"""

import json
import os
from contextvars import ContextVar
from pathlib import Path

# Context variable to store current user_id
_current_user_id: ContextVar[int] = ContextVar('current_user_id', default=None)

# Context variable to store current session_id
_current_session_id: ContextVar[str] = ContextVar('current_session_id', default=None)

# Base workspace directory. Kept as a module-level value so tests and local
# tooling can still monkeypatch it, while get_workspace_base() reads env/.env.
DEFAULT_WORKSPACE_BASE = Path("/workspaces")
WORKSPACE_BASE = DEFAULT_WORKSPACE_BASE

DEFAULT_VSCODE_SETTINGS = {
    "ruff.enable": False,
    "ruff.lint.args": [],
    "ruff.format.args": [],
    "ruff.configuration": None,
    "python.analysis.autoSearchPaths": False,
    "python.analysis.useLibraryCodeForTypes": False,
    "files.exclude": {
        "**/.agent_internal": True,
    },
}

SENSITIVE_AGENT_PATH_PARTS = {".git", ".ssh", ".aws", ".gnupg"}
SENSITIVE_AGENT_FILENAMES = {
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
}
SENSITIVE_AGENT_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
OPERATIONAL_AGENT_PATH_PARTS = {
    ".agent",
    ".agent_internal",
    ".agent_tmp",
    ".tasks",
    ".team",
    ".transcripts",
    ".vscode",
}


def _ensure_vscode_settings(workspace: Path) -> None:
    """Create or repair safe default VSCode settings for a user workspace."""
    settings_path = workspace / ".vscode" / "settings.json"
    existing: dict = {}

    if settings_path.exists():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except json.JSONDecodeError:
            existing = {}

    merged = dict(existing)
    file_excludes = merged.get("files.exclude")
    if not isinstance(file_excludes, dict):
        file_excludes = {}

    for key, value in DEFAULT_VSCODE_SETTINGS.items():
        if key == "files.exclude":
            continue
        merged[key] = value
    merged["files.exclude"] = {
        **file_excludes,
        **DEFAULT_VSCODE_SETTINGS["files.exclude"],
    }

    if merged != existing:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


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


def get_workspace_base() -> Path:
    """Return the effective base directory for user workspaces."""
    env_value = os.environ.get("WORKSPACE_BASE")
    if env_value:
        return Path(env_value)

    if WORKSPACE_BASE != DEFAULT_WORKSPACE_BASE:
        return WORKSPACE_BASE

    from enterprise_agent.config.settings import settings

    return Path(settings.WORKSPACE_BASE)


def is_sensitive_agent_path(path: str) -> bool:
    """Return whether an Agent tool path could expose mutable credentials."""
    normalized = path.replace("\\", "/").strip()
    parts = [part.lower() for part in Path(normalized).parts]
    if any(part in SENSITIVE_AGENT_PATH_PARTS for part in parts):
        return True
    if not parts:
        return False
    name = parts[-1]
    if name == ".env.example":
        return False
    if name in SENSITIVE_AGENT_FILENAMES or name.startswith(".env."):
        return True
    return Path(name).suffix.lower() in SENSITIVE_AGENT_SUFFIXES


def is_operational_agent_path(path: str) -> bool:
    """Return whether a path belongs to Agent-owned operational storage.

    These directories have dedicated tools that enforce ownership, integrity,
    and bounded reads. Generic file or shell tools must not silently bypass
    those contracts.
    """
    normalized = str(path or "").replace("\\", "/").strip()
    return any(
        part.lower() in OPERATIONAL_AGENT_PATH_PARTS
        for part in Path(normalized).parts
    )


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

    workspace_base = get_workspace_base()
    if user_id is None:
        # Fallback to a default workspace (for backward compatibility)
        workspace = workspace_base / "default"
    else:
        workspace = workspace_base / f"user_{user_id}"

    # Create workspace if it doesn't exist
    workspace.mkdir(parents=True, exist_ok=True)
    _ensure_vscode_settings(workspace)
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
