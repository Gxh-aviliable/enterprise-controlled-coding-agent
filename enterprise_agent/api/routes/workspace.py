"""Workspace file management API routes.

Provides file browsing, reading, upload, download, delete, and move operations.
All paths are scoped to the authenticated user's workspace via resolve_path().
"""

import io
import logging
import shutil
import zipfile
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from enterprise_agent.api.middleware.auth import get_current_user
from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tools.workspace import get_user_workspace, resolve_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspace", tags=["workspace"])


def _quote_file_url_path(path: str) -> str:
    """Quote a filesystem path for vscode://file URLs while keeping separators."""
    return quote(path.replace("\\", "/"), safe="/:")


def _server_workspace_path(root: Path, user_id: int) -> str:
    """Return the server-side VSCode workspace path scoped to one user."""
    configured = settings.VSCODE_WORKSPACE_PATH.strip().replace("\\", "/")
    workspace_name = f"user_{user_id}"
    if not configured:
        return root.as_posix()

    if "{" in configured:
        return configured.format(user_id=user_id, workspace_name=workspace_name).rstrip("/")

    configured = configured.rstrip("/")
    if configured.endswith(f"/{workspace_name}") or configured == workspace_name:
        return configured
    return f"{configured}/{workspace_name}"


def _build_open_url(resolved: Path, root: Path, relative_path: str, user_id: int) -> dict:
    """Build open-in-editor URL for configured mode."""
    mode = settings.FILE_OPEN_MODE
    if mode == "local-vscode":
        workspace_url = f"vscode://file/{_quote_file_url_path(str(root.resolve()))}"
        file_url = f"vscode://file/{_quote_file_url_path(str(resolved.resolve()))}"
        return {
            "mode": mode,
            "url": workspace_url,
            "file_url": file_url,
        }

    if mode == "web-vscode":
        workspace_path = _server_workspace_path(root, user_id).rstrip("/")
        server_file_path = f"{workspace_path}/{relative_path.strip('/')}" if relative_path else workspace_path

        if settings.VSCODE_WEB_URL_TEMPLATE:
            return {
                "mode": mode,
                "url": settings.VSCODE_WEB_URL_TEMPLATE.format(
                    path=server_file_path,
                    workspace=workspace_path,
                    relative_path=relative_path,
                    user_id=user_id,
                    workspace_name=f"user_{user_id}",
                ),
            }

        if not settings.VSCODE_WEB_BASE_URL:
            raise HTTPException(status_code=500, detail="VSCODE_WEB_BASE_URL is not configured")

        query = urlencode({"folder": workspace_path, "file": server_file_path})
        return {"mode": mode, "url": f"{settings.VSCODE_WEB_BASE_URL.rstrip('/')}?{query}"}

    raise HTTPException(status_code=500, detail=f"Unsupported FILE_OPEN_MODE: {mode}")


def _build_tree(root: Path, current: Path, depth: int, file_type: str) -> Optional[dict]:
    """Recursively build a directory tree dict.

    Args:
        root: The workspace root for relative path calculation.
        current: Current path to scan.
        depth: Remaining recursion depth.
        file_type: "all", "file", or "dir" filter.

    Returns:
        A dict with path, name, type, size, children, or None if filtered out.
    """
    name = current.name or str(current)
    try:
        rel = str(current.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        rel = name

    if rel == ".":
        rel = ""

    if current.is_dir():
        entry = {"path": rel, "name": name, "type": "dir", "children": []}
        if depth > 0:
            try:
                items = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except PermissionError:
                return entry
            for child in items:
                child_entry = _build_tree(root, child, depth - 1, file_type)
                if child_entry:
                    entry["children"].append(child_entry)
        return entry
    else:
        if file_type == "dir":
            return None
        return {
            "path": rel,
            "name": name,
            "type": "file",
            "size": current.stat().st_size if current.exists() else 0,
        }


@router.get("/tree")
async def get_tree(
    path: str = Query(default="", description="Relative path within workspace"),
    depth: int = Query(default=2, ge=0, le=10, description="Recursion depth"),
    file_type: str = Query(default="all", pattern="^(all|file|dir)$"),
    user_id: int = Depends(get_current_user),
):
    """Get directory tree for the user's workspace.

    Returns a nested JSON structure representing the file tree.
    """
    resolved = resolve_path(path, user_id)
    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")

    root = get_user_workspace(user_id).resolve()
    result = _build_tree(root, resolved, depth, file_type)
    if result is None:
        return {"path": path, "name": resolved.name, "type": "file", "children": []}
    return result


@router.get("/read")
async def read_file(
    path: str = Query(..., description="Relative path to file"),
    encoding: str = Query(default="utf-8", description="File encoding"),
    offset: int = Query(default=0, ge=0, description="Line offset for pagination"),
    limit: int = Query(default=500, ge=1, le=5000, description="Max lines to return"),
    user_id: int = Depends(get_current_user),
):
    """Read file contents from user workspace.

    Supports pagination via offset/limit for large files.
    """
    resolved = resolve_path(path, user_id)

    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {path}")

    try:
        with open(resolved, "r", encoding=encoding) as f:
            all_lines = f.readlines()
    except UnicodeDecodeError:
        # Binary file — return info only
        return {
            "path": path,
            "content": f"[Binary file ({resolved.stat().st_size} bytes)]",
            "size": resolved.stat().st_size,
            "lines": 0,
            "binary": True,
        }

    total_lines = len(all_lines)
    paginated = all_lines[offset : offset + limit]
    return {
        "path": path,
        "content": "".join(paginated),
        "size": resolved.stat().st_size,
        "lines": total_lines,
        "offset": offset,
        "limit": limit,
        "binary": False,
    }


@router.get("/open-url")
async def get_open_url(
    path: str = Query(..., description="Relative path to file"),
    user_id: int = Depends(get_current_user),
):
    """Return a URL that opens the file in local or web VSCode."""
    resolved = resolve_path(path, user_id)

    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {path}")

    root = get_user_workspace(user_id).resolve()
    try:
        relative_path = resolved.resolve().relative_to(root).as_posix()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file path")

    return _build_open_url(resolved, root, relative_path, user_id)


@router.get("/download")
async def download_file(
    path: str = Query(..., description="Relative path to file"),
    user_id: int = Depends(get_current_user),
):
    """Download a single file from user workspace."""
    resolved = resolve_path(path, user_id)

    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {path}")

    return FileResponse(resolved, filename=resolved.name, media_type="application/octet-stream")


@router.get("/download-zip")
async def download_zip(
    paths: str = Query(..., description="Comma-separated relative paths"),
    name: str = Query(default="workspace", description="Zip file name (without extension)"),
    user_id: int = Depends(get_current_user),
):
    """Download multiple files/directories as a zip archive."""
    selected = [p.strip() for p in paths.split(",") if p.strip()]
    if not selected:
        raise HTTPException(status_code=400, detail="No paths specified")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path in selected:
            resolved = resolve_path(rel_path, user_id)
            if not resolved.exists():
                continue
            if resolved.is_dir():
                for file in resolved.rglob("*"):
                    if file.is_file():
                        arcname = str(file.relative_to(resolved).as_posix())
                        zf.write(file, arcname)
            else:
                zf.write(resolved, resolved.name)

    buf.seek(0)
    safe_name = "".join(c for c in name if c.isalnum() or c in "._- ")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.zip"'},
    )


@router.post("/upload")
async def upload_file(
    file: UploadFile,
    path: str = Query(default="", description="Target subdirectory within workspace"),
    user_id: int = Depends(get_current_user),
):
    """Upload a file to user workspace."""
    # Resolve target directory
    if path:
        resolved_dir = resolve_path(path, user_id)
        if not resolved_dir.exists():
            resolved_dir.mkdir(parents=True, exist_ok=True)
        elif not resolved_dir.is_dir():
            raise HTTPException(status_code=400, detail=f"Path is not a directory: {path}")
    else:
        resolved_dir = get_user_workspace(user_id)

    # Security: sanitize filename (warn but keep)
    safe_filename = file.filename or "untitled"
    target = (resolved_dir / Path(safe_filename).name).resolve()

    # Ensure target is within workspace
    workdir = get_user_workspace(user_id).resolve()
    if not target.is_relative_to(workdir):
        raise HTTPException(status_code=400, detail="Invalid file path")

    content = await file.read()
    target.write_bytes(content)

    return {
        "path": str(target.relative_to(workdir)).replace("\\", "/"),
        "name": target.name,
        "size": len(content),
    }


@router.post("/mkdir")
async def create_directory(
    path: str = Query(..., description="Directory path to create"),
    user_id: int = Depends(get_current_user),
):
    """Create a directory in user workspace."""
    resolved = resolve_path(path, user_id)

    if resolved.exists():
        raise HTTPException(status_code=409, detail=f"Path already exists: {path}")

    resolved.mkdir(parents=True)
    workdir = get_user_workspace(user_id).resolve()
    return {
        "path": str(resolved.relative_to(workdir)).replace("\\", "/"),
        "created": True,
    }


@router.delete("/delete")
async def delete_item(
    path: str = Query(..., description="Path to delete"),
    user_id: int = Depends(get_current_user),
):
    """Delete a file or directory from user workspace."""
    resolved = resolve_path(path, user_id)

    # Protect workspace root
    workdir = get_user_workspace(user_id).resolve()
    if resolved == workdir:
        raise HTTPException(status_code=400, detail="Cannot delete workspace root")

    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")

    if resolved.is_dir():
        shutil.rmtree(resolved)
    else:
        resolved.unlink()

    return {"deleted": True, "path": path}


@router.put("/move")
async def move_item(
    source: str = Query(..., alias="from", description="Source path"),
    dest: str = Query(..., alias="to", description="Destination path"),
    user_id: int = Depends(get_current_user),
):
    """Move or rename a file/directory in user workspace."""
    src_resolved = resolve_path(source, user_id)
    dst_resolved = resolve_path(dest, user_id)

    if not src_resolved.exists():
        raise HTTPException(status_code=404, detail=f"Source not found: {source}")
    if dst_resolved.exists():
        raise HTTPException(status_code=409, detail=f"Destination already exists: {dest}")

    dst_resolved.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src_resolved), str(dst_resolved))

    return {
        "from": source,
        "to": dest,
        "moved": True,
    }
