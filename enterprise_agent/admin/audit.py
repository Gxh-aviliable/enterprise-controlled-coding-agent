"""Append-only privileged action audit helpers."""

from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.models.admin import AdminAuditLog


def add_audit_event(
    db: AsyncSession,
    *,
    actor_user_id: int | None,
    action: str,
    target_type: str,
    target_id: str | int | None = None,
    reason: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    outcome: str = "succeeded",
    request: Request | None = None,
) -> AdminAuditLog:
    """Add an audit row to the caller's transaction without auto-committing."""
    source_ip = request.client.host if request and request.client else None
    user_agent = request.headers.get("user-agent", "")[:255] if request else None
    request_id = request.headers.get("x-request-id") if request else None
    event = AdminAuditLog(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        reason=reason,
        before_json=before,
        after_json=after,
        request_id=request_id,
        source_ip=source_ip,
        user_agent=user_agent,
        outcome=outcome,
    )
    db.add(event)
    return event
