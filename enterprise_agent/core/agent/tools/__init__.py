"""Tool registry for Enterprise Agent.

Imports and registers all available tools for use with LangGraph.
Tools are organized by module:
- file_ops: read_file, write_file, edit_file
- shell: bash
- task: todo_update, task_create, task_get, task_update, task_list, claim_task
- subagent: task (subagent delegation)
- background: background_run, check_background
- skills: load_skill, list_skills, reload_skills
- team: spawn_teammate, list_teammates, send_message, read_inbox,
        broadcast, shutdown_request, plan_approval, idle
- context_tools: compress, list_transcripts, get_transcript, context_status
- memory: search_memory
"""

# File operations
# Background tasks
from enterprise_agent.core.agent.tools.background import (
    background_run,
    check_background,
)

# Context management
from enterprise_agent.core.agent.tools.context_tools import (
    compress,
    context_status,
    get_transcript,
    list_transcripts,
)
from enterprise_agent.core.agent.tools.file_ops import (
    edit_file,
    read_file,
    write_file,
)

# Shell execution
from enterprise_agent.core.agent.tools.shell import bash

# Skills
from enterprise_agent.core.agent.tools.skills import (
    list_skills,
    load_skill,
    reload_skills,
)

# Memory query
from enterprise_agent.core.agent.tools.memory import search_memory

# Subagent delegation
from enterprise_agent.core.agent.tools.subagent import task as subagent_task

# Task management
from enterprise_agent.core.agent.tools.task import (
    claim_task,
    task_create,
    task_get,
    task_list,
    task_update,
    todo_update,
)

# Team collaboration
from enterprise_agent.core.agent.tools.team import (
    broadcast,
    idle,
    list_teammates,
    plan_approval,
    read_inbox,
    send_message,
    shutdown_request,
    spawn_teammate,
)

# === Human-in-the-loop: Sensitive Tools ===
# These tools require user confirmation before execution
SENSITIVE_TOOLS = {
    "bash",           # Shell commands (delete, modify files, etc.)
    "write_file",     # Write/create files
    "edit_file",      # Edit existing files
    "task_create",    # Create background tasks
    "spawn_teammate", # Spawn subagent
    "send_message",   # Send message to teammate
    "broadcast",      # Broadcast to all teammates
}

# Read-only tools that never require confirmation
SAFE_TOOLS = {
    "read_file",
    "list_skills",
    "list_teammates",
    "list_transcripts",
    "get_transcript",
    "context_status",
    "check_background",
    "read_inbox",
    "task_list",
    "task_get",
    "todo_update",  # Task tracking is safe
    "search_memory",  # Read-only memory query
}


def tool_requires_confirmation(tool_name: str) -> bool:
    """Check if tool requires user confirmation before execution.

    Args:
        tool_name: Name of the tool

    Returns:
        True if tool requires confirmation
    """
    return tool_name in SENSITIVE_TOOLS


def get_sensitive_tool_info(tool_name: str, tool_args: dict) -> str:
    """Get human-readable description of sensitive tool action.

    Args:
        tool_name: Name of the tool
        tool_args: Tool arguments

    Returns:
        Human-readable description for confirmation dialog
    """
    if tool_name == "bash":
        cmd = tool_args.get("command", "")
        # Truncate long commands
        if len(cmd) > 100:
            cmd = cmd[:100] + "..."
        return f"Execute shell command: `{cmd}`"
    elif tool_name == "write_file":
        path = tool_args.get("path", "")
        content_preview = tool_args.get("content", "")[:50]
        return f"Write file: `{path}` (content: {content_preview}...)"
    elif tool_name == "edit_file":
        path = tool_args.get("path", "")
        old = tool_args.get("old_text", "")[:30]
        new = tool_args.get("new_text", "")[:30]
        return f"Edit file: `{path}` (replace `{old}` with `{new}`)"
    elif tool_name == "task_create":
        desc = tool_args.get("description", "")
        return f"Create background task: {desc[:50]}..."
    elif tool_name == "spawn_teammate":
        role = tool_args.get("role", "")
        return f"Spawn teammate agent: {role}"
    elif tool_name == "send_message":
        to = tool_args.get("to", "")
        msg = tool_args.get("message", "")[:50]
        return f"Send message to {to}: {msg}..."
    elif tool_name == "broadcast":
        msg = tool_args.get("message", "")[:50]
        return f"Broadcast to all teammates: {msg}..."
    else:
        return f"Execute {tool_name}"


# === Tool Registry ===

ALL_TOOLS = [
    # File operations
    read_file,
    write_file,
    edit_file,

    # Shell
    bash,

    # Task management
    todo_update,
    task_create,
    task_get,
    task_update,
    task_list,
    claim_task,

    # Subagent
    subagent_task,

    # Background
    background_run,
    check_background,

    # Skills
    load_skill,
    list_skills,
    reload_skills,

    # Team
    spawn_teammate,
    list_teammates,
    send_message,
    read_inbox,
    broadcast,
    shutdown_request,
    plan_approval,
    idle,

    # Context management
    compress,
    list_transcripts,
    get_transcript,
    context_status,

    # Memory
    search_memory,
]


def get_tools_for_permissions(user_permissions: list) -> list:
    """Filter tools based on user permissions.

    Args:
        user_permissions: List of permission strings from JWT

    Returns:
        List of tools the user is allowed to use
    """
    # Permission mapping
    # Format: 'tools:<category>' grants access to that category
    permission_map = {
        "tools:file": [read_file, write_file, edit_file],
        "tools:shell": [bash],
        "tools:task": [todo_update, task_create, task_get, task_update, task_list, claim_task],
        "tools:subagent": [subagent_task],
        "tools:background": [background_run, check_background],
        "tools:skills": [load_skill, list_skills, reload_skills],
        "tools:team": [
            spawn_teammate, list_teammates, send_message, read_inbox,
            broadcast, shutdown_request, plan_approval, idle,
        ],
        "tools:context": [compress, list_transcripts, get_transcript, context_status],
        "tools:memory": [search_memory],
        "tools:all": ALL_TOOLS,
    }

    # If no permissions, return basic tools (file + task + context)
    if not user_permissions:
        return [
            read_file, write_file, edit_file,
            todo_update, task_create, task_get, task_update, task_list,
            load_skill, list_skills,
            compress, context_status
        ]

    # Collect tools for each permission
    allowed_tools = []
    for perm in user_permissions:
        if perm in permission_map:
            allowed_tools.extend(permission_map[perm])

    # Remove duplicates while preserving order
    seen = set()
    unique_tools = []
    for tool in allowed_tools:
        if tool.name not in seen:
            seen.add(tool.name)
            unique_tools.append(tool)

    return unique_tools


def get_tool_by_name(name: str):
    """Get a specific tool by name.

    Args:
        name: Tool name

    Returns:
        Tool function or None if not found
    """
    for tool in ALL_TOOLS:
        if tool.name == name:
            return tool
    return None