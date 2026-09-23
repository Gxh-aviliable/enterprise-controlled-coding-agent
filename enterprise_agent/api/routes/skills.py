"""Authenticated self-service Skills. Import is preview-only until an explicit install."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.api.middleware.auth import get_current_user
from enterprise_agent.db.mysql import get_db
from enterprise_agent.models.admin import SkillImportPreview, SkillInstallation
from enterprise_agent.skills.catalog import catalog, public_item
from enterprise_agent.skills.imports import authorized_directory, directory_candidates, git_candidates
from enterprise_agent.skills.packages import (
    MAX_ARCHIVE_BYTES,
    SkillError,
    encode_package,
    validate_package,
    zip_candidates,
)

router = APIRouter(prefix="/skills", tags=["skills"])


class ImportRequest(BaseModel):
    kind: Literal["git", "workspace", "template"]
    url: str = Field(default="", max_length=1000)
    ref: str = Field(default="HEAD", max_length=200)
    subdirectory: str = Field(default="", max_length=240)
    path: str = Field(default="", max_length=240)
    guidance: str = Field(default="Describe the steps and checks.", min_length=1, max_length=90_000)
    name: str = Field(default="new-skill", pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    description: str = Field(default="Explain when to use this Skill", min_length=1, max_length=1000)


class InstallRequest(BaseModel):
    preview_id: str = Field(min_length=1, max_length=36)
    candidate: int = Field(ge=0, le=63)
    project: str = Field(default="", max_length=240)
    replace_id: str | None = Field(default=None, max_length=128)
    expected_version: int | None = Field(default=None, ge=1)


class SkillUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    enabled: bool | None = None
    implicit_allowed: bool | None = None


async def save_preview(db, user_id, candidates, source):
    now = datetime.now(timezone.utc)
    await db.execute(delete(SkillImportPreview).where(SkillImportPreview.expires_at < now))
    count = await db.scalar(
        select(func.count()).select_from(SkillImportPreview).where(SkillImportPreview.user_id == user_id)
    )
    if count >= 5:
        raise HTTPException(429, "At most five pending imports; install one or wait 30 minutes")
    preview = SkillImportPreview(
        id=str(uuid.uuid4()),
        user_id=user_id,
        candidates=candidates,
        source_json=source,
        expires_at=now + timedelta(minutes=30),
    )
    db.add(preview)
    await db.commit()
    return {
        "preview_id": preview.id,
        "source": source,
        "candidates": [{k: v for k, v in item.items() if k not in {"package", "body"}} for item in candidates],
    }


@router.post("/imports/zip")
async def preview_zip(request: Request, user_id: int = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Raw ZIP stream avoids multipart spooling an unbounded body to disk first.
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_ARCHIVE_BYTES:
            raise HTTPException(413, "ZIP upload exceeds 16 MiB")
    try:
        candidates = await asyncio.to_thread(zip_candidates, bytes(data))
    except SkillError as exc:
        raise HTTPException(422, str(exc)) from exc
    return await save_preview(db, user_id, candidates, {"kind": "zip"})


@router.post("/imports")
async def preview_import(
    payload: ImportRequest, user_id: int = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    try:
        if payload.kind == "git":
            candidates, source = await asyncio.to_thread(git_candidates, payload.url, payload.ref, payload.subdirectory)
        elif payload.kind == "workspace":
            path = authorized_directory(user_id, payload.path)
            candidates = await asyncio.to_thread(directory_candidates, path)
            source = {"kind": "workspace", "path": payload.path}
        else:
            import yaml

            content = (
                "---\n"
                + yaml.safe_dump({"name": payload.name, "description": payload.description}, allow_unicode=True)
                + "---\n\n"
                + payload.guidance
                + "\n"
            )
            evidence = validate_package(encode_package({"SKILL.md": content.encode()}))
            candidates = [{"path": ".", "valid": True, **evidence}]
            source = {"kind": "template"}
    except (SkillError, OSError) as exc:
        raise HTTPException(422, str(exc) if isinstance(exc, SkillError) else "Import directory unavailable") from exc
    return await save_preview(db, user_id, candidates, source)


@router.get("/imports/{preview_id}/{candidate}")
async def preview_candidate(
    preview_id: str, candidate: int, user_id: int = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    preview = await db.scalar(
        select(SkillImportPreview).where(SkillImportPreview.id == preview_id, SkillImportPreview.user_id == user_id)
    )
    if not preview or preview.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(404, "Import preview expired or not found")
    if not 0 <= candidate < len(preview.candidates):
        raise HTTPException(404, "Candidate not found")
    return preview.candidates[candidate]


async def owned_install(db, user_id, skill_id, *, lock=False):
    if not skill_id.startswith("installed:"):
        raise HTTPException(403, "Only your installed Skills can be modified; public content requires an administrator")
    query = select(SkillInstallation).where(
        SkillInstallation.id == skill_id.removeprefix("installed:"), SkillInstallation.user_id == user_id
    )
    row = await db.scalar(query.with_for_update() if lock else query)
    if not row:
        raise HTTPException(404, "Installed Skill not found")
    if row.project:
        try:
            authorized_directory(user_id, row.project)
        except SkillError as exc:
            raise HTTPException(403, str(exc)) from exc
    return row


@router.post("/install", status_code=201)
async def install(
    payload: InstallRequest, user_id: int = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    preview = await db.scalar(
        select(SkillImportPreview)
        .where(SkillImportPreview.id == payload.preview_id, SkillImportPreview.user_id == user_id)
        .with_for_update()
    )
    if not preview or preview.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(404, "Import preview expired or not found; preview the source again")
    if payload.candidate >= len(preview.candidates):
        raise HTTPException(422, "Candidate does not exist")
    candidate = preview.candidates[payload.candidate]
    if not candidate["valid"]:
        raise HTTPException(422, candidate["error"])
    try:
        if payload.project:
            authorized_directory(user_id, payload.project)
        evidence = validate_package(candidate["package"])
    except SkillError as exc:
        raise HTTPException(422, str(exc)) from exc
    source = {**preview.source_json, "candidate": candidate["path"]}
    if payload.replace_id:
        row = await owned_install(db, user_id, payload.replace_id, lock=True)
        if row.version != payload.expected_version:
            raise HTTPException(409, "Skill changed; reload before updating")
        if row.project != payload.project or row.name != evidence["metadata"]["name"]:
            raise HTTPException(422, "Update must retain the original scope and Skill name")
        row.version += 1
    else:
        count = await db.scalar(
            select(func.count()).select_from(SkillInstallation).where(SkillInstallation.user_id == user_id)
        )
        if count >= 128:
            raise HTTPException(409, "Installation limit reached (128 Skills)")
        row = SkillInstallation(
            id=str(uuid.uuid4()),
            user_id=user_id,
            project=payload.project,
            name=evidence["metadata"]["name"],
            version=1,
            enabled=True,
            implicit_allowed=True,
        )
        db.add(row)
    row.package = evidence["package"]
    row.content_sha256 = evidence["sha256"]
    row.source_json = source
    await db.delete(preview)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            409, "Skill already installed in this scope; choose Update with its current version"
        ) from exc
    return {"id": "installed:" + row.id, "version": row.version, "sha256": row.content_sha256}


@router.get("")
async def list_skills(
    project: str = "", q: str = "", user_id: int = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    try:
        loader = await catalog(db, user_id, project)
    except SkillError as exc:
        raise HTTPException(403, str(exc)) from exc
    items = [public_item(item) for item in loader.skills.values()]
    if q:
        items = [item for item in items if q.casefold() in (item["name"] + item["description"]).casefold()]
    return {"items": items, "errors": loader.errors}


@router.get("/{skill_id}")
async def detail(
    skill_id: str, project: str = "", user_id: int = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    try:
        loader = await catalog(db, user_id, project)
    except SkillError as exc:
        raise HTTPException(403, str(exc)) from exc
    item = next((s for s in loader.skills.values() if s["id"] == skill_id), None)
    if item is None:
        raise HTTPException(404, "Skill is not visible in the selected scope")
    return public_item(item, detail=True)


@router.patch("/{skill_id}")
async def update(
    skill_id: str, payload: SkillUpdate, user_id: int = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    row = await owned_install(db, user_id, skill_id, lock=True)
    if row.version != payload.expected_version:
        raise HTTPException(409, "Skill changed; reload before updating")
    for key in ("enabled", "implicit_allowed"):
        if getattr(payload, key) is not None:
            setattr(row, key, getattr(payload, key))
    row.version += 1
    await db.commit()
    return {"id": skill_id, "version": row.version}


@router.delete("/{skill_id}")
async def uninstall(
    skill_id: str, expected_version: int, user_id: int = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    row = await owned_install(db, user_id, skill_id, lock=True)
    if row.version != expected_version:
        raise HTTPException(409, "Skill changed; reload before uninstalling")
    await db.delete(row)
    await db.commit()
    return {"id": skill_id, "uninstalled": True}
