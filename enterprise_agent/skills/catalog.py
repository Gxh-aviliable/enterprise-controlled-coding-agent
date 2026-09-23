"""Administrator publications and user-owned Skills, with optional project scope."""

import hashlib
import re
from pathlib import Path

from sqlalchemy import select

from enterprise_agent.core.agent.tools.skills import SkillLoader
from enterprise_agent.models.admin import SharedSkill, SharedSkillVersion, SkillInstallation
from enterprise_agent.skills.imports import authorized_directory
from enterprise_agent.skills.packages import SkillError, validate_package
from enterprise_agent.skills.registry import historical_package
from enterprise_agent.skills.runtime import cache_package


async def catalog(db, user_id, project=""):
    root = authorized_directory(user_id)
    project_root = authorized_directory(user_id, project) if project else None
    loader = SkillLoader([])
    sources = [(root / ".skills", "personal")]
    if project_root:
        sources.append((project_root / ".agents" / "skills", "project"))
    for directory, scope in sources:
        # Reject links anywhere below the authorized root, including .agents.
        if any(
            p.is_symlink() for p in [directory, *directory.parents] if p.is_relative_to(root)
        ):
            loader.errors.append({"path": scope, "error": "Skill source contains a symbolic link"})
            continue
        discovered = SkillLoader(directory)
        loader.errors.extend(discovered.errors)
        for item in discovered.skills.values():
            identity = hashlib.sha256(str(Path(item["path"]).relative_to(directory)).encode()).hexdigest()[:24]
            item.update(
                id=f"{scope}:{user_id}:"
                f"{hashlib.sha256(project.encode()).hexdigest()[:8] if scope == 'project' else 'root'}:{identity}",
                source="personal",
                scope=scope,
                editable=False,
            )
            loader.add(item)
    rows = (
        await db.execute(
            select(SharedSkill, SharedSkillVersion)
            .outerjoin(
                SharedSkillVersion,
                (SharedSkillVersion.skill_id == SharedSkill.id)
                & (SharedSkillVersion.version == SharedSkill.active_version),
            )
            .where(SharedSkill.status == "published")
        )
    ).all()
    for skill, version in rows:
        try:
            if version is None:
                raise SkillError("Published Skill version is missing")
            evidence = validate_package(historical_package(version), skill.name)
            loader.add(
                entry(
                    evidence,
                    id=f"managed:{skill.id}",
                    source="managed",
                    scope="global",
                    version=version.version,
                    editable=False,
                    enabled=True,
                    implicit_allowed=True,
                )
            )
        except SkillError as exc:
            loader.errors.append({"id": f"managed:{skill.id}", "name": skill.name, "error": str(exc)})
    installed = (
        await db.scalars(
            select(SkillInstallation).where(
                SkillInstallation.user_id == user_id, SkillInstallation.project.in_(["", project])
            )
        )
    ).all()
    for install in installed:
        if install.project not in {"", project}:
            continue  # MySQL may use a case-insensitive collation; project paths are exact.
        try:
            evidence = validate_package(install.package, install.name)
            if evidence["sha256"] != install.content_sha256:
                raise SkillError("Installed Skill content hash mismatch")
            loader.add(
                entry(
                    evidence,
                    id="installed:" + install.id,
                    source="personal",
                    scope="project" if install.project else "personal",
                    version=install.version,
                    enabled=install.enabled,
                    implicit_allowed=install.implicit_allowed,
                    editable=True,
                    origin=install.source_json,
                    project=install.project,
                    installed_at=install.installed_at.isoformat(),
                )
            )
        except SkillError as exc:
            loader.errors.append({"id": "installed:" + install.id, "name": install.name, "error": str(exc)})
    return loader


def entry(evidence, **fields):
    return {
        "name": evidence["metadata"]["name"],
        "meta": evidence["metadata"],
        "sha256": evidence["sha256"],
        "package": evidence["package"],
        "body": evidence["body"],
        "files": evidence["files"],
        "warnings": evidence["warnings"],
        **fields,
    }


def public_item(item, *, detail=False):
    result = {k: v for k, v in item.items() if k not in {"package", "body", "path", "meta"}}
    result["description"] = item["meta"]["description"]
    result["metadata"] = item["meta"]
    if detail:
        evidence = validate_package(item["package"])
        result["content"] = evidence["content"]
        result["package"] = evidence["package"]
        result["files"] = evidence["files"]
    return result


async def task_snapshot(db, user_id, project, skill_ids, content, implicit_allowed=True):
    loader = await catalog(db, user_id, project)
    selected = []
    selectors = list(skill_ids) + re.findall(r"(?<![\w$])\$([a-z0-9][a-z0-9_-]{0,79})(?![\w-])", content)
    for selector in selectors:
        try:
            item = loader.resolve(selector)
        except ValueError as exc:
            raise SkillError(str(exc)) from exc
        if item["id"] not in selected:
            selected.append(item["id"])
    if len(selected) > 8:
        raise SkillError("Select at most 8 Skills per request")
    if sum(len(loader.resolve(identity)["body"].encode()) for identity in selected) > 128_000:
        raise SkillError("Explicit Skill guidance exceeds 128 KB; select fewer Skills")
    items = []
    total = 0
    ordered = sorted(loader.skills.values(), key=lambda item: (item["id"] not in selected, item["id"]))
    for item in ordered:
        if not item.get("enabled", True):
            continue
        if item["id"] not in selected and (not implicit_allowed or not item.get("implicit_allowed", True)):
            continue
        evidence = validate_package(item["package"], legacy_name=item["name"])
        size = sum(f["bytes"] for f in evidence["files"])
        if len(items) >= 64 or total + size > 32 * 1024 * 1024:
            if item["id"] in selected:
                raise SkillError("Selected Skill packages exceed the task resource budget")
            continue
        cache_package(evidence["package"])
        total += size
        items.append({k: v for k, v in item.items() if k not in {"package", "body", "path"}})
    return {
        "user_id": user_id,
        "project": project,
        "selected": selected,
        "items": items,
        "implicit_allowed": implicit_allowed,
        "errors": loader.errors,
    }
