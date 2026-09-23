"""Bind a single HITL decision to its exact owner, calls and workspace version."""

import hashlib
import json
from datetime import datetime, timezone

from enterprise_agent.core.agent.tools.workspace import get_user_workspace
from enterprise_agent.core.execution.evidence import workspace_state
from enterprise_agent.observability.trace_store import redact_text, redact_value


def approval_scope(state):
    root = get_user_workspace(state.get("user_id"))
    scope = {
        "user_id": state.get("user_id"),
        "session_id": state.get("session_id"),
        "trace_id": state.get("trace_id"),
        "permissions": sorted(state.get("permissions", [])),
        "workspace": str(root.absolute()),
        "workspace_stamp": workspace_state(root)["stamp"],
        "calls": [
            {key: call.get(key) for key in ("id", "name", "args")} for call in state.get("pending_tool_calls", [])
        ],
    }
    return hashlib.sha256(json.dumps(scope, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def approval_is_current(state, signature, deadline):
    if not signature or not deadline:
        return False
    try:
        return datetime.fromisoformat(deadline) > datetime.now(timezone.utc) and signature == approval_scope(state)
    except (OSError, ValueError, TypeError):
        return False


def approval_details(name, arguments):
    shell = name in {"bash", "background_run"}
    paths = arguments.get("paths") or ([arguments["path"]] if arguments.get("path") else [])
    return {
        "command": redact_text(str(arguments.get("command", "")), limit=None) if shell else None,
        "paths": paths,
        "arguments_preview": redact_value(arguments, limit=1000),
        "risk_reason": (
            "Executes project code; scripts and test configuration can modify workspace files."
            if shell
            else "Changes the listed files or persistent task state."
        ),
        "valid_scope": "Listed calls, exact arguments, current user/task/permissions/workspace version, until expiry. "
        "A workspace change invalidates remaining approvals in this batch.",
    }


async def refresh_authorization(state):
    """API tasks re-read the live role; offline harnesses use their fixed policy."""
    if state.get("authorization_source") != "database":
        return state
    from enterprise_agent.auth.permissions import get_role_permissions
    from enterprise_agent.db.mysql import async_session_factory
    from enterprise_agent.models.user import User

    async with async_session_factory() as db:
        user = await db.get(User, int(state["user_id"]))
        permissions = (
            []
            if user is None or not user.is_active
            else [permission.value for permission in get_role_permissions("admin" if user.is_superuser else "free")]
        )
    return {**state, "permissions": permissions}


def require_current_approval():
    """Recheck after acquiring the mutation/snapshot lock, including background work."""
    from enterprise_agent.core.execution.evidence import current_evidence

    approval = (current_evidence.get() or {}).get('approval')
    if approval and not approval_is_current(*approval):
        raise PermissionError('approval_stale: workspace or approval changed before execution; request fresh approval')
