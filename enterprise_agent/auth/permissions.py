import logging
from enum import Enum
from typing import List


class Permission(str, Enum):
    """Permission enum for role-based access control"""
    # Basic permissions
    CHAT_BASIC = "chat:basic"
    CHAT_STREAMING = "chat:streaming"
    TOOLS_BASIC = "tools:basic"
    TOOLS_SHELL = "tools:shell"
    TOOLS_ADVANCED = "tools:advanced"

    # Session permissions
    SESSION_CREATE = "session:create"
    SESSION_ARCHIVE = "session:archive"

    # Admin permissions
    ADMIN_CONSOLE = "admin:console"
    ADMIN_USERS = "admin:users"
    ADMIN_ANALYTICS = "admin:analytics"
    ADMIN_QUOTAS_READ = "admin:quotas:read"
    ADMIN_QUOTAS_WRITE = "admin:quotas:write"
    ADMIN_WORKSPACE_METADATA = "admin:workspace:metadata"
    ADMIN_WORKSPACE_CONTENT = "admin:workspace:content"
    ADMIN_SKILLS_READ = "admin:skills:read"
    ADMIN_SKILLS_PUBLISH = "admin:skills:publish"
    ADMIN_TASKS_READ = "admin:tasks:read"
    ADMIN_TASKS_CANCEL = "admin:tasks:cancel"
    ADMIN_AUDIT_READ = "admin:audit:read"
    ADMIN_SYSTEM_READ = "admin:system:read"


# Role permission mappings
ROLE_PERMISSIONS = {
    "free": [
        Permission.CHAT_BASIC,
        Permission.TOOLS_BASIC,
        Permission.SESSION_CREATE,
    ],
    "pro": [
        Permission.CHAT_BASIC,
        Permission.CHAT_STREAMING,
        Permission.TOOLS_BASIC,
        Permission.TOOLS_SHELL,
        Permission.TOOLS_ADVANCED,
        Permission.SESSION_CREATE,
        Permission.SESSION_ARCHIVE,
    ],
    "admin": list(Permission),  # All permissions
}


def get_role_permissions(role: str) -> List[Permission]:
    """Get permissions for a role

    Args:
        role: Role name ('free', 'pro', 'admin')

    Returns:
        List of Permission enums
    """
    if role not in ROLE_PERMISSIONS:
        logging.warning(f"Unknown role '{role}', falling back to 'free'")
    return ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS["free"])


def has_permission(user_permissions: List[str], required: Permission) -> bool:
    """Check if user has required permission

    Args:
        user_permissions: List of permission strings from JWT
        required: Required Permission enum

    Returns:
        True if user has permission
    """
    return required.value in user_permissions
