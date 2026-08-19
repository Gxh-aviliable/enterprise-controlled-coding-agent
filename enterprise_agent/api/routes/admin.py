"""Governed administration APIs for the intranet control room."""

import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.admin.audit import add_audit_event
from enterprise_agent.admin.quotas import period_usage_from_traces
from enterprise_agent.admin.skills import (
    managed_skill_path,
    materialize_skill,
    retire_materialized_skill,
    validate_skill_content,
    validation_json,
)
from enterprise_agent.api.middleware.auth import require_admin
from enterprise_agent.api.schemas.admin import (
    AccessGrantCreate,
    AdminReasonRequest,
    SharedSkillDraftRequest,
    SharedSkillPublishRequest,
    SharedSkillRollbackRequest,
    UserQuotaUpdate,
    UserStatusUpdate,
)
from enterprise_agent.api.services.workspace_read import get_workspace_tree, read_workspace_text
from enterprise_agent.auth.permissions import Permission
from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tools.skills import reload_all_skill_loaders
from enterprise_agent.core.agent.tools.workspace import get_user_workspace
from enterprise_agent.db.mysql import get_db
from enterprise_agent.db.redis import get_redis
from enterprise_agent.models.admin import (
    AdminAccessGrant,
    AdminAuditLog,
    SharedSkill,
    SharedSkillVersion,
    UserQuota,
    UserUsageDaily,
)
from enterprise_agent.models.api_key import APIKey
from enterprise_agent.models.user import User
from enterprise_agent.observability.trace_store import get_trace_store

router = APIRouter(prefix="/admin", tags=["admin"])


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
        "role": "admin" if user.is_superuser else "free",
        "created_at": _iso(user.created_at),
        "updated_at": _iso(user.updated_at),
        "last_login_at": _iso(user.last_login_at),
    }


def _quota_payload(quota: UserQuota) -> dict:
    return {
        "user_id": quota.user_id,
        "daily_task_limit": quota.daily_task_limit,
        "daily_token_limit": quota.daily_token_limit,
        "monthly_token_limit": quota.monthly_token_limit,
        "concurrent_task_limit": quota.concurrent_task_limit,
        "workspace_bytes_limit": quota.workspace_bytes_limit,
        "enabled": quota.enabled,
        "version": quota.version,
        "updated_at": _iso(quota.updated_at),
    }


async def _get_user_or_404(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def _get_or_create_quota(db: AsyncSession, user_id: int, actor_id: int | None = None) -> UserQuota:
    quota = await db.get(UserQuota, user_id)
    if quota:
        return quota
    quota = UserQuota(user_id=user_id, updated_by=actor_id)
    db.add(quota)
    await db.flush()
    return quota


async def _usage_payload(db: AsyncSession, user_id: int) -> dict:
    store = get_trace_store()
    traces = store.list_traces(user_id, limit=500)
    usage = store.aggregate_metrics(user_id)
    period = period_usage_from_traces(traces)
    daily_row = await db.scalar(
        select(UserUsageDaily).where(
            UserUsageDaily.user_id == user_id,
            UserUsageDaily.usage_date == datetime.now(timezone.utc).date(),
        )
    )
    period["daily_tasks"] = max(period["daily_tasks"], int(daily_row.task_count if daily_row else 0))
    usage.update(period)
    usage["total_tokens"] = sum(
        int((trace.get("metrics") or {}).get("total_tokens") or 0)
        for trace in traces
    )
    usage["tracking_window"] = "latest_500_traces"
    return usage


@router.get("/overview")
async def admin_overview(
    admin: User = Depends(require_admin(Permission.ADMIN_CONSOLE.value)),
    db: AsyncSession = Depends(get_db),
):
    """Return fleet-level counts without exposing user content."""
    total_users = await db.scalar(select(func.count()).select_from(User)) or 0
    active_users = await db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0
    admin_users = await db.scalar(select(func.count()).select_from(User).where(User.is_superuser.is_(True))) or 0
    user_ids = list((await db.scalars(select(User.id))).all())

    totals = {
        "task_count": 0,
        "succeeded": 0,
        "failed": 0,
        "cancelled": 0,
        "total_tokens": 0,
        "tool_calls": 0,
        "safety_interceptions": 0,
        "confirmation_count": 0,
    }
    recent_tasks = []
    store = get_trace_store()
    for user_id in user_ids:
        metrics = store.aggregate_metrics(user_id)
        for key in totals:
            if key == "total_tokens":
                continue
            totals[key] += int(metrics.get(key, 0) or 0)
        user_traces = store.list_traces(user_id, limit=500)
        for trace in user_traces:
            totals["total_tokens"] += int(trace.get("metrics", {}).get("total_tokens", 0) or 0)
        for trace in user_traces[:5]:
            recent_tasks.append({**trace, "user_id": user_id})
    recent_tasks.sort(key=lambda item: item.get("started_at", ""), reverse=True)

    return {
        "users": {"total": total_users, "active": active_users, "admins": admin_users},
        "tasks": totals,
        "recent_tasks": recent_tasks[:10],
        "scope": "metadata_only",
        "requested_by": admin.id,
    }


@router.get("/users")
async def list_users(
    q: str = Query(default="", max_length=100),
    active: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=100),
    _admin: User = Depends(require_admin(Permission.ADMIN_USERS.value)),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if q:
        pattern = f"%{q.strip()}%"
        filters.append(or_(User.username.like(pattern), User.email.like(pattern), User.full_name.like(pattern)))
    if active is not None:
        filters.append(User.is_active.is_(active))

    count_query = select(func.count()).select_from(User)
    query = select(User)
    for condition in filters:
        count_query = count_query.where(condition)
        query = query.where(condition)
    total = await db.scalar(count_query) or 0
    users = list((await db.scalars(query.order_by(User.id).offset((page - 1) * limit).limit(limit))).all())
    return {"items": [_user_payload(user) for user in users], "total": total, "page": page, "limit": limit}


@router.get("/users/{user_id}")
async def get_user_detail(
    user_id: int,
    _admin: User = Depends(require_admin(Permission.ADMIN_USERS.value)),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user_or_404(db, user_id)
    quota = await _get_or_create_quota(db, user_id)
    await db.commit()
    workspace = get_user_workspace(user_id)
    workspace_bytes = sum(path.stat().st_size for path in workspace.rglob("*") if path.is_file())
    return {
        "user": _user_payload(user),
        "quota": _quota_payload(quota),
        "usage": await _usage_payload(db, user_id),
        "workspace": {"bytes": workspace_bytes, "root_name": workspace.name},
    }


@router.patch("/users/{user_id}/status")
async def update_user_status(
    user_id: int,
    payload: UserStatusUpdate,
    request: Request,
    admin: User = Depends(require_admin(Permission.ADMIN_USERS.value)),
    db: AsyncSession = Depends(get_db),
):
    target = await _get_user_or_404(db, user_id)
    if target.id == admin.id and not payload.is_active:
        raise HTTPException(status_code=409, detail="Administrators cannot disable their own account")
    if target.is_superuser and not payload.is_active:
        active_admins = await db.scalar(
            select(func.count()).select_from(User).where(User.is_superuser.is_(True), User.is_active.is_(True))
        ) or 0
        if active_admins <= 1:
            raise HTTPException(status_code=409, detail="Cannot disable the last active administrator")

    before = {"is_active": target.is_active}
    target.is_active = payload.is_active
    if not payload.is_active:
        target.auth_version = int(target.auth_version or 0) + 1
        api_keys = list((await db.scalars(select(APIKey).where(APIKey.user_id == target.id))).all())
        for api_key in api_keys:
            api_key.is_active = False
    add_audit_event(
        db,
        actor_user_id=admin.id,
        action="user.status.update",
        target_type="user",
        target_id=user_id,
        reason=payload.reason,
        before=before,
        after={"is_active": target.is_active},
        request=request,
    )
    await db.commit()
    return _user_payload(target)


@router.post("/users/{user_id}/revoke-sessions")
async def revoke_user_sessions(
    user_id: int,
    payload: AdminReasonRequest,
    request: Request,
    admin: User = Depends(require_admin(Permission.ADMIN_USERS.value)),
    db: AsyncSession = Depends(get_db),
):
    """Invalidate all JWT generations and deactivate API keys for one user."""
    target = await _get_user_or_404(db, user_id)
    if target.id == admin.id:
        raise HTTPException(status_code=409, detail="Cannot revoke the current administrator session")
    before_version = int(target.auth_version or 0)
    target.auth_version = before_version + 1
    api_keys = list((await db.scalars(select(APIKey).where(APIKey.user_id == target.id))).all())
    revoked_api_keys = 0
    for api_key in api_keys:
        if api_key.is_active:
            api_key.is_active = False
            revoked_api_keys += 1
    add_audit_event(
        db,
        actor_user_id=admin.id,
        action="user.sessions.revoke",
        target_type="user",
        target_id=user_id,
        reason=payload.reason,
        before={"auth_version": before_version},
        after={"auth_version": target.auth_version, "api_keys_revoked": revoked_api_keys},
        request=request,
    )
    await db.commit()
    return {
        "user_id": user_id,
        "sessions_revoked": True,
        "api_keys_revoked": revoked_api_keys,
        "auth_version": target.auth_version,
    }


@router.get("/users/{user_id}/usage")
async def get_user_usage(
    user_id: int,
    _admin: User = Depends(require_admin(Permission.ADMIN_QUOTAS_READ.value)),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_or_404(db, user_id)
    return await _usage_payload(db, user_id)


@router.get("/users/{user_id}/quota")
async def get_user_quota(
    user_id: int,
    admin: User = Depends(require_admin(Permission.ADMIN_QUOTAS_READ.value)),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_or_404(db, user_id)
    quota = await _get_or_create_quota(db, user_id, admin.id)
    await db.commit()
    return _quota_payload(quota)


@router.patch("/users/{user_id}/quota")
async def update_user_quota(
    user_id: int,
    payload: UserQuotaUpdate,
    request: Request,
    admin: User = Depends(require_admin(Permission.ADMIN_QUOTAS_WRITE.value)),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_or_404(db, user_id)
    quota = await _get_or_create_quota(db, user_id, admin.id)
    if payload.expected_version and quota.version != payload.expected_version:
        raise HTTPException(status_code=409, detail="Quota was changed by another administrator")

    before = _quota_payload(quota)
    updates = payload.model_dump(exclude_none=True, exclude={"reason", "expected_version"})
    for field, value in updates.items():
        setattr(quota, field, value)
    quota.updated_by = admin.id
    quota.version += 1
    add_audit_event(
        db,
        actor_user_id=admin.id,
        action="quota.update",
        target_type="user_quota",
        target_id=user_id,
        reason=payload.reason,
        before=before,
        after=_quota_payload(quota),
        request=request,
    )
    await db.commit()
    return _quota_payload(quota)


@router.get("/users/{user_id}/workspace/tree")
async def admin_workspace_tree(
    user_id: int,
    path: str = Query(default=""),
    depth: int = Query(default=2, ge=0, le=6),
    _admin: User = Depends(require_admin(Permission.ADMIN_WORKSPACE_METADATA.value)),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_or_404(db, user_id)
    try:
        return get_workspace_tree(user_id, path, depth)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Workspace path not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/access-grants", status_code=status.HTTP_201_CREATED)
async def create_access_grant(
    payload: AccessGrantCreate,
    request: Request,
    admin: User = Depends(require_admin(Permission.ADMIN_WORKSPACE_CONTENT.value)),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_or_404(db, payload.target_user_id)
    now = datetime.now(timezone.utc)
    grant = AdminAccessGrant(
        id=str(uuid.uuid4()),
        actor_user_id=admin.id,
        target_user_id=payload.target_user_id,
        scope=payload.scope,
        reason=payload.reason,
        expires_at=now + timedelta(minutes=payload.ttl_minutes),
    )
    db.add(grant)
    add_audit_event(
        db,
        actor_user_id=admin.id,
        action="workspace.access_grant.create",
        target_type="user_workspace",
        target_id=payload.target_user_id,
        reason=payload.reason,
        after={"grant_id": grant.id, "scope": grant.scope, "expires_at": _iso(grant.expires_at)},
        request=request,
    )
    await db.commit()
    return {
        "id": grant.id,
        "target_user_id": grant.target_user_id,
        "scope": grant.scope,
        "reason": grant.reason,
        "expires_at": _iso(grant.expires_at),
    }


def _grant_is_active(grant: AdminAccessGrant, *, actor_id: int, target_user_id: int) -> bool:
    expires_at = grant.expires_at
    now = datetime.now(timezone.utc)
    if expires_at and expires_at.tzinfo is None:
        now = now.replace(tzinfo=None)
    return bool(
        grant.actor_user_id == actor_id
        and grant.target_user_id == target_user_id
        and grant.scope == "workspace:content"
        and grant.revoked_at is None
        and grant.expires_at > now
    )


@router.get("/users/{user_id}/workspace/read")
async def admin_workspace_read(
    user_id: int,
    request: Request,
    path: str = Query(...),
    grant_id: str = Query(..., min_length=36, max_length=36),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
    admin: User = Depends(require_admin(Permission.ADMIN_WORKSPACE_CONTENT.value)),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_or_404(db, user_id)
    grant = await db.get(AdminAccessGrant, grant_id)
    if not grant or not _grant_is_active(grant, actor_id=admin.id, target_user_id=user_id):
        raise HTTPException(status_code=403, detail="A valid temporary access grant is required")
    try:
        result = read_workspace_text(user_id, path, offset=offset, limit=limit, allow_sensitive=False)
    except PermissionError as exc:
        add_audit_event(
            db,
            actor_user_id=admin.id,
            action="workspace.content.read",
            target_type="workspace_file",
            target_id=f"{user_id}:{path}",
            reason=grant.reason,
            after={"grant_id": grant.id},
            outcome="blocked",
            request=request,
        )
        await db.commit()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Workspace file not found") from exc
    except IsADirectoryError as exc:
        raise HTTPException(status_code=400, detail="Path is not a file") from exc

    add_audit_event(
        db,
        actor_user_id=admin.id,
        action="workspace.content.read",
        target_type="workspace_file",
        target_id=f"{user_id}:{path}",
        reason=grant.reason,
        after={"grant_id": grant.id, "size": result["size"], "binary": result["binary"]},
        request=request,
    )
    await db.commit()
    result["grant_id"] = grant.id
    result["grant_expires_at"] = _iso(grant.expires_at)
    return result


@router.get("/skills")
async def list_shared_skills(
    _admin: User = Depends(require_admin(Permission.ADMIN_SKILLS_READ.value)),
    db: AsyncSession = Depends(get_db),
):
    managed = list((await db.scalars(select(SharedSkill).order_by(SharedSkill.name))).all())
    managed_names = {skill.name for skill in managed}
    items = [
        {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "status": skill.status,
            "active_version": skill.active_version,
            "source": "managed",
            "updated_at": _iso(skill.updated_at),
        }
        for skill in managed
    ]
    bundled_root = Path(settings.SHARED_SKILLS_DIR)
    if bundled_root.exists():
        for path in sorted(bundled_root.glob("*/SKILL.md")):
            if path.parent.name in managed_names:
                continue
            validation = validate_skill_content(path.parent.name, path.read_text(encoding="utf-8"))
            items.append(
                {
                    "id": None,
                    "name": path.parent.name,
                    "description": validation["metadata"].get("description", ""),
                    "status": "builtin",
                    "active_version": None,
                    "source": "builtin",
                    "sha256": validation["sha256"],
                }
            )
    items.sort(key=lambda item: item["name"])
    return {"items": items}


@router.post("/skills", status_code=status.HTTP_201_CREATED)
async def save_shared_skill_draft(
    payload: SharedSkillDraftRequest,
    request: Request,
    admin: User = Depends(require_admin(Permission.ADMIN_SKILLS_PUBLISH.value)),
    db: AsyncSession = Depends(get_db),
):
    skill = await db.scalar(select(SharedSkill).where(SharedSkill.name == payload.name))
    before = None
    if skill:
        before = {"description": skill.description, "status": skill.status}
        skill.description = payload.description
        skill.draft_content = payload.content
        if skill.status == "retired":
            skill.status = "draft"
    else:
        skill = SharedSkill(
            name=payload.name,
            description=payload.description,
            draft_content=payload.content,
            status="draft",
            created_by=admin.id,
        )
        db.add(skill)
    validation = validate_skill_content(payload.name, payload.content)
    add_audit_event(
        db,
        actor_user_id=admin.id,
        action="shared_skill.draft.save",
        target_type="shared_skill",
        target_id=payload.name,
        before=before,
        after={"description": payload.description, "validation": validation},
        request=request,
    )
    await db.commit()
    await db.refresh(skill)
    return {"name": skill.name, "status": skill.status, "validation": validation, "updated_at": _iso(skill.updated_at)}


@router.get("/skills/{name}")
async def get_shared_skill(
    name: str,
    _admin: User = Depends(require_admin(Permission.ADMIN_SKILLS_READ.value)),
    db: AsyncSession = Depends(get_db),
):
    skill = await db.scalar(select(SharedSkill).where(SharedSkill.name == name))
    if not skill:
        raise HTTPException(status_code=404, detail="Managed Shared Skill not found")
    versions = list(
        (
            await db.scalars(
                select(SharedSkillVersion)
                .where(SharedSkillVersion.skill_id == skill.id)
                .order_by(SharedSkillVersion.version.desc())
            )
        ).all()
    )
    return {
        "name": skill.name,
        "description": skill.description,
        "status": skill.status,
        "draft_content": skill.draft_content,
        "active_version": skill.active_version,
        "updated_at": _iso(skill.updated_at),
        "validation": validate_skill_content(skill.name, skill.draft_content),
        "versions": [
            {
                "version": version.version,
                "sha256": version.content_sha256,
                "changelog": version.changelog,
                "published_at": _iso(version.published_at),
            }
            for version in versions
        ],
    }


@router.post("/skills/{name}/validate")
async def validate_shared_skill(
    name: str,
    _admin: User = Depends(require_admin(Permission.ADMIN_SKILLS_PUBLISH.value)),
    db: AsyncSession = Depends(get_db),
):
    skill = await db.scalar(select(SharedSkill).where(SharedSkill.name == name))
    if not skill:
        raise HTTPException(status_code=404, detail="Managed Shared Skill not found")
    return validate_skill_content(skill.name, skill.draft_content)


@router.post("/skills/{name}/publish")
async def publish_shared_skill(
    name: str,
    payload: SharedSkillPublishRequest,
    request: Request,
    admin: User = Depends(require_admin(Permission.ADMIN_SKILLS_PUBLISH.value)),
    db: AsyncSession = Depends(get_db),
):
    skill = await db.scalar(select(SharedSkill).where(SharedSkill.name == name))
    if not skill:
        raise HTTPException(status_code=404, detail="Managed Shared Skill not found")
    if payload.expected_updated_at and skill.updated_at != payload.expected_updated_at:
        raise HTTPException(status_code=409, detail="Skill draft was changed by another administrator")
    validation = validate_skill_content(skill.name, skill.draft_content)
    if not validation["valid"]:
        raise HTTPException(status_code=422, detail={"message": "Skill validation failed", **validation})

    previous = managed_skill_path(name)
    previous_content = previous.read_text(encoding="utf-8") if previous.exists() else None
    previous_active_version = skill.active_version
    max_version = await db.scalar(
        select(func.max(SharedSkillVersion.version)).where(SharedSkillVersion.skill_id == skill.id)
    ) or 0
    version_number = int(max_version) + 1
    target = materialize_skill(name, skill.draft_content, version_number)
    try:
        version = SharedSkillVersion(
            skill_id=skill.id,
            version=version_number,
            content=skill.draft_content,
            content_path=str(target),
            content_sha256=validation["sha256"],
            validation_json=validation_json(validation),
            changelog=payload.changelog,
            created_by=admin.id,
        )
        db.add(version)
        skill.status = "published"
        skill.active_version = version_number
        add_audit_event(
            db,
            actor_user_id=admin.id,
            action="shared_skill.publish",
            target_type="shared_skill",
            target_id=name,
            reason=payload.changelog,
            after={"version": version_number, "sha256": validation["sha256"]},
            request=request,
        )
        await db.commit()
    except Exception:
        if previous_content is None:
            retire_materialized_skill(name)
        else:
            materialize_skill(name, previous_content, previous_active_version)
        raise
    refreshed = reload_all_skill_loaders()
    return {
        "name": name,
        "status": "published",
        "version": version_number,
        "sha256": validation["sha256"],
        "loaders_refreshed": refreshed,
    }


@router.post("/skills/{name}/retire")
async def retire_shared_skill(
    name: str,
    request: Request,
    reason: str = Query(..., min_length=3, max_length=500),
    admin: User = Depends(require_admin(Permission.ADMIN_SKILLS_PUBLISH.value)),
    db: AsyncSession = Depends(get_db),
):
    skill = await db.scalar(select(SharedSkill).where(SharedSkill.name == name))
    if not skill:
        raise HTTPException(status_code=404, detail="Managed Shared Skill not found")
    active_path = managed_skill_path(name)
    previous_content = active_path.read_text(encoding="utf-8") if active_path.exists() else None
    previous_version = skill.active_version
    retire_materialized_skill(name)
    try:
        skill.status = "retired"
        skill.active_version = None
        add_audit_event(
            db,
            actor_user_id=admin.id,
            action="shared_skill.retire",
            target_type="shared_skill",
            target_id=name,
            reason=reason,
            before={"active_version": previous_version},
            after={"status": "retired"},
            request=request,
        )
        await db.commit()
    except Exception:
        if previous_content is not None:
            materialize_skill(name, previous_content, previous_version)
        raise
    refreshed = reload_all_skill_loaders()
    return {"name": name, "status": "retired", "loaders_refreshed": refreshed}


@router.post("/skills/{name}/rollback")
async def rollback_shared_skill(
    name: str,
    payload: SharedSkillRollbackRequest,
    request: Request,
    admin: User = Depends(require_admin(Permission.ADMIN_SKILLS_PUBLISH.value)),
    db: AsyncSession = Depends(get_db),
):
    """Publish a new immutable version using a historical version's body."""
    skill = await db.scalar(select(SharedSkill).where(SharedSkill.name == name))
    if not skill:
        raise HTTPException(status_code=404, detail="Managed Shared Skill not found")
    source_version = await db.scalar(
        select(SharedSkillVersion).where(
            SharedSkillVersion.skill_id == skill.id,
            SharedSkillVersion.version == payload.version,
        )
    )
    if not source_version:
        raise HTTPException(status_code=404, detail="Shared Skill version not found")
    validation = validate_skill_content(name, source_version.content)
    if not validation["valid"]:
        raise HTTPException(status_code=422, detail="Historical Skill version no longer passes validation")

    active_path = managed_skill_path(name)
    previous_content = active_path.read_text(encoding="utf-8") if active_path.exists() else None
    previous_active_version = skill.active_version
    max_version = await db.scalar(
        select(func.max(SharedSkillVersion.version)).where(SharedSkillVersion.skill_id == skill.id)
    ) or 0
    version_number = int(max_version) + 1
    target = materialize_skill(name, source_version.content, version_number)
    try:
        db.add(SharedSkillVersion(
            skill_id=skill.id,
            version=version_number,
            content=source_version.content,
            content_path=str(target),
            content_sha256=validation["sha256"],
            validation_json=validation_json(validation),
            changelog=f"Rollback to v{payload.version}: {payload.reason}",
            created_by=admin.id,
        ))
        skill.draft_content = source_version.content
        skill.status = "published"
        skill.active_version = version_number
        add_audit_event(
            db,
            actor_user_id=admin.id,
            action="shared_skill.rollback",
            target_type="shared_skill",
            target_id=name,
            reason=payload.reason,
            before={"active_version": previous_active_version},
            after={
                "active_version": version_number,
                "source_version": payload.version,
                "sha256": validation["sha256"],
            },
            request=request,
        )
        await db.commit()
    except Exception:
        if previous_content is None:
            retire_materialized_skill(name)
        else:
            materialize_skill(name, previous_content, previous_active_version)
        raise
    refreshed = reload_all_skill_loaders()
    return {
        "name": name,
        "status": "published",
        "version": version_number,
        "source_version": payload.version,
        "sha256": validation["sha256"],
        "loaders_refreshed": refreshed,
    }


@router.get("/tasks")
async def list_admin_tasks(
    limit: int = Query(default=50, ge=1, le=200),
    _admin: User = Depends(require_admin(Permission.ADMIN_TASKS_READ.value)),
    db: AsyncSession = Depends(get_db),
):
    user_ids = list((await db.scalars(select(User.id))).all())
    tasks = []
    for user_id in user_ids:
        tasks.extend({**trace, "user_id": user_id} for trace in get_trace_store().list_traces(user_id, limit=limit))
    tasks.sort(key=lambda item: item.get("started_at", ""), reverse=True)
    return {"items": tasks[:limit]}


@router.post("/tasks/{trace_id}/cancel")
async def cancel_admin_task(
    trace_id: str,
    payload: AdminReasonRequest,
    request: Request,
    admin: User = Depends(require_admin(Permission.ADMIN_TASKS_CANCEL.value)),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a non-terminal cross-user task using its owning checkpoint."""
    store = get_trace_store()
    user_ids = list((await db.scalars(select(User.id))).all())
    trace = None
    owner_id = None
    for candidate_id in user_ids:
        try:
            trace = store.get_trace(candidate_id, trace_id)
            owner_id = candidate_id
            break
        except (FileNotFoundError, ValueError):
            continue
    if trace is None or owner_id is None:
        raise HTTPException(status_code=404, detail="Task trace not found")
    if trace.get("status") in {"succeeded", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Task is already terminal")

    from enterprise_agent.api.routes.chat import request_task_cancellation

    result = await request_task_cancellation(
        trace["session_id"],
        owner_id,
        payload.reason,
        trace_id=trace_id,
    )
    try:
        current = store.get_trace(owner_id, trace_id)
        if current.get("status") not in {"succeeded", "failed", "cancelled"}:
            store.finish_trace(
                user_id=owner_id,
                trace_id=trace_id,
                status="cancelled",
                error=payload.reason,
            )
    except (FileNotFoundError, ValueError):
        pass
    add_audit_event(
        db,
        actor_user_id=admin.id,
        action="task.cancel",
        target_type="task_trace",
        target_id=trace_id,
        reason=payload.reason,
        before={"status": trace.get("status"), "user_id": owner_id},
        after={"status": "cancelled"},
        request=request,
    )
    await db.commit()
    return {**result, "trace_id": trace_id, "user_id": owner_id}


@router.get("/audit-logs")
async def list_audit_logs(
    action: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    _admin: User = Depends(require_admin(Permission.ADMIN_AUDIT_READ.value)),
    db: AsyncSession = Depends(get_db),
):
    query = select(AdminAuditLog)
    count_query = select(func.count()).select_from(AdminAuditLog)
    if action:
        query = query.where(AdminAuditLog.action == action)
        count_query = count_query.where(AdminAuditLog.action == action)
    total = await db.scalar(count_query) or 0
    rows = list(
        (
            await db.scalars(
                query.order_by(AdminAuditLog.created_at.desc()).offset((page - 1) * limit).limit(limit)
            )
        ).all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "actor_user_id": row.actor_user_id,
                "action": row.action,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "reason": row.reason,
                "before": row.before_json,
                "after": row.after_json,
                "outcome": row.outcome,
                "created_at": _iso(row.created_at),
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.get("/system/health")
async def admin_system_health(
    _admin: User = Depends(require_admin(Permission.ADMIN_SYSTEM_READ.value)),
    db: AsyncSession = Depends(get_db),
):
    checks = {"mysql": "ok", "redis": "ok", "workspace": "ok", "managed_skills": "ok"}
    try:
        await db.execute(select(1))
    except Exception:
        checks["mysql"] = "error"
    try:
        redis = await get_redis()
        await redis.ping()
    except Exception:
        checks["redis"] = "error"
    workspace_base = Path(settings.WORKSPACE_BASE)
    managed_base = Path(settings.MANAGED_SHARED_SKILLS_DIR)
    for key, path in (("workspace", workspace_base), ("managed_skills", managed_base)):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            checks[key] = "error"
    usage = shutil.disk_usage(workspace_base)
    return {
        "status": "healthy" if all(value == "ok" for value in checks.values()) else "degraded",
        "checks": checks,
        "version": settings.APP_VERSION,
        "storage": {"total": usage.total, "used": usage.used, "free": usage.free},
    }
