"""Database-authoritative shared versions; filesystem content is only a cache."""

import base64
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from enterprise_agent.admin.audit import add_audit_event
from enterprise_agent.models.admin import SharedSkill, SharedSkillVersion
from enterprise_agent.skills.packages import SkillError, encode_package, validate_package


def content_package(content, package=None):
    files = dict(package or {})
    files["SKILL.md"] = base64.b64encode(content.encode()).decode()
    return files


def package_validation(name, content, package=None):
    try:
        evidence = validate_package(content_package(content, package), name)
        return {
            "valid": True,
            "errors": [],
            "warnings": evidence["warnings"],
            "metadata": evidence["metadata"],
            "sha256": evidence["sha256"],
            "files": evidence["files"],
            "bytes": sum(f["bytes"] for f in evidence["files"]),
            "estimated_tokens": max(1, len(content) // 4),
        }
    except SkillError as exc:
        return {"valid": False, "errors": [str(exc)], "warnings": [], "metadata": {}}


async def publish(
    db, name, actor_id, request, *, changelog, expected_revision=None, expected_updated_at=None, rollback_version=None
):
    # Row lock serializes version allocation across processes and hosts.
    skill = await db.scalar(select(SharedSkill).where(SharedSkill.name == name).with_for_update())
    if not skill:
        raise HTTPException(404, "Managed Shared Skill not found")
    if expected_revision is not None and skill.revision != expected_revision:
        raise HTTPException(409, "Skill revision changed; reload before publishing")
    if expected_updated_at is not None:
        if skill.updated_at.replace(tzinfo=None) != expected_updated_at.replace(tzinfo=None):
            raise HTTPException(409, "Skill draft was changed by another administrator")
    source = None
    if rollback_version is not None:
        source = await db.scalar(
            select(SharedSkillVersion).where(
                SharedSkillVersion.skill_id == skill.id, SharedSkillVersion.version == rollback_version
            )
        )
        if not source:
            raise HTTPException(404, "Shared Skill version not found")
    package = historical_package(source) if source else skill.draft_package
    content = validate_package(package)["content"] if source else skill.draft_content
    try:
        evidence = validate_package(content_package(content, package), name)
    except SkillError as exc:
        raise HTTPException(422, str(exc)) from exc
    if skill.active_version is not None and source is None:
        active = await db.scalar(
            select(SharedSkillVersion).where(
                SharedSkillVersion.skill_id == skill.id, SharedSkillVersion.version == skill.active_version
            )
        )
        if active and active.content_sha256 == evidence["sha256"]:
            raise HTTPException(409, "This package is already published")
    number = (
        int(
            await db.scalar(select(func.max(SharedSkillVersion.version)).where(SharedSkillVersion.skill_id == skill.id))
            or 0
        )
        + 1
    )
    version = SharedSkillVersion(
        skill_id=skill.id,
        version=number,
        content=evidence["content"],
        package=evidence["package"],
        content_path=f"db://managed/{skill.id}/{number}",
        content_sha256=evidence["sha256"],
        validation_json=package_validation(name, content, package),
        changelog=changelog,
        created_by=actor_id,
    )
    db.add(version)
    skill.status = "published"
    skill.active_version = number
    skill.revision += 1
    skill.updated_at = datetime.now(timezone.utc)
    add_audit_event(
        db,
        actor_user_id=actor_id,
        action="shared_skill.rollback" if source else "shared_skill.publish",
        target_type="shared_skill",
        target_id=name,
        reason=changelog,
        after={"version": number, "sha256": evidence["sha256"], "source_version": rollback_version},
        request=request,
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Concurrent publication; reload the Skill") from exc
    return {
        "name": name,
        "status": "published",
        "version": number,
        "sha256": evidence["sha256"],
        "source_version": rollback_version,
        "revision": skill.revision,
        "loaders_refreshed": 0,
    }


def historical_package(version):
    """Legacy versions retain their original content hash until read, then upgrade in memory."""
    import hashlib

    if version.package is None:
        if hashlib.sha256(version.content.encode()).hexdigest() != version.content_sha256:
            raise SkillError("Historical Skill content hash mismatch")
        return encode_package({"SKILL.md": version.content.encode()})
    evidence = validate_package(version.package)
    if evidence["sha256"] != version.content_sha256:
        raise SkillError("Published Skill package hash mismatch")
    return evidence["package"]
