"""Workspace file management API routes.

Provides file browsing, reading, upload, download, delete, and move operations.
All paths are scoped to the authenticated user's workspace via resolve_path().
"""

import io
import logging
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from enterprise_agent.api.middleware.auth import get_current_user
from enterprise_agent.api.schemas.workspace import WorkspaceFileWriteRequest
from enterprise_agent.api.services.workspace_read import (
    WorkspaceBinaryFileError,
    WorkspaceFileTooLargeError,
    WorkspaceWriteConflictError,
    build_tree,
    read_workspace_text,
    write_workspace_text,
)
from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tools.workspace import (
    get_user_workspace,
    is_operational_agent_path,
    is_sensitive_agent_path,
    resolve_path,
)
from enterprise_agent.core.agent.tools.workspace_lock import (
    WorkspaceWriteLockTimeoutError,
    workspace_write_lock,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspace", tags=["workspace"])


def _workspace_busy_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=423,
        detail={
            "code": "workspace_busy",
            "message": "The workspace is busy with another write operation. Retry later.",
        },
    )


def _log_browser_write(
    *,
    user_id: int,
    path: str,
    before_sha256: str,
    after_sha256: str | None,
    size: int,
    outcome: str,
) -> None:
    """Emit metadata-only audit context; file content is deliberately excluded."""
    logger.info(
        "workspace_browser_write",
        extra={
            "workspace_user_id": user_id,
            "workspace_path": path,
            "workspace_before_sha256": before_sha256,
            "workspace_after_sha256": after_sha256,
            "workspace_size_bytes": size,
            "workspace_write_outcome": outcome,
        },
    )


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    """Atomically replace one upload target; callers hold the workspace lock."""
    previous_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if previous_mode is not None:
            os.chmod(temporary_path, previous_mode)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _plain_mutation_path(user_id: int, path: str) -> Path:
    """Resolve a mutation path lexically and reject every symlink component.

    Read-only APIs may expose a symlink entry, but browser mutations must not
    follow it and accidentally edit, move, or delete its target. Sensitive and
    Agent-managed paths use dedicated flows and remain outside this generic API.
    """
    normalized = str(path or "").strip()
    if is_sensitive_agent_path(normalized) or is_operational_agent_path(normalized):
        raise PermissionError("Sensitive or Agent-managed workspace paths cannot be changed")

    root = get_user_workspace(user_id).resolve()
    candidate = Path(normalized) if Path(normalized).is_absolute() else root / normalized
    lexical = Path(os.path.abspath(candidate))
    if not lexical.is_relative_to(root):
        raise ValueError(f"Path escapes workspace: {path}")

    current = root
    for part in lexical.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise PermissionError("Workspace paths reached through symlinks cannot be changed")
        if not current.exists():
            break
    return lexical


def _store_upload(user_id: int, path: str, filename: str, content: bytes) -> dict:
    with workspace_write_lock(user_id):
        if path:
            resolved_dir = _plain_mutation_path(user_id, path)
            if not resolved_dir.exists():
                resolved_dir.mkdir(parents=True, exist_ok=True)
            elif not resolved_dir.is_dir():
                raise NotADirectoryError(path)
        else:
            resolved_dir = get_user_workspace(user_id)

        workdir = get_user_workspace(user_id).resolve()
        target_relative = (resolved_dir / Path(filename or "untitled").name).relative_to(workdir)
        target = _plain_mutation_path(user_id, target_relative.as_posix())
        _atomic_write_bytes(target, content)
        return {
            "path": target.relative_to(workdir).as_posix(),
            "name": target.name,
            "size": len(content),
        }


def _create_directory(user_id: int, path: str) -> dict:
    with workspace_write_lock(user_id):
        resolved = _plain_mutation_path(user_id, path)
        if resolved.exists():
            raise FileExistsError(path)
        resolved.mkdir(parents=True)
        workdir = get_user_workspace(user_id).resolve()
        return {"path": resolved.relative_to(workdir).as_posix(), "created": True}


def _delete_item(user_id: int, path: str) -> dict:
    with workspace_write_lock(user_id):
        resolved = _plain_mutation_path(user_id, path)
        workdir = get_user_workspace(user_id).resolve()
        if resolved == workdir:
            raise ValueError("Cannot delete workspace root")
        if not resolved.exists():
            raise FileNotFoundError(path)
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink()
        return {"deleted": True, "path": path}


def _move_item(user_id: int, source: str, dest: str) -> dict:
    with workspace_write_lock(user_id):
        src_resolved = _plain_mutation_path(user_id, source)
        dst_resolved = _plain_mutation_path(user_id, dest)
        workdir = get_user_workspace(user_id).resolve()
        if src_resolved == workdir:
            raise ValueError("Cannot move workspace root")
        if not src_resolved.exists():
            raise FileNotFoundError(source)
        if dst_resolved.exists():
            raise FileExistsError(dest)
        dst_resolved.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_resolved), str(dst_resolved))
        return {"from": source, "to": dest, "moved": True}


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
    result = build_tree(root, resolved, depth, file_type)
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
    try:
        result = read_workspace_text(
            user_id,
            path,
            encoding=encoding,
            offset=offset,
            limit=limit,
            allow_sensitive=True,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"File not found: {path}") from exc
    except IsADirectoryError as exc:
        raise HTTPException(status_code=400, detail=f"Path is not a file: {path}") from exc
    if result["binary"]:
        result["content"] = f"[Binary file ({result['size']} bytes)]"
    return result


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


@router.put("/write")
async def write_file(
    payload: WorkspaceFileWriteRequest,
    user_id: int = Depends(get_current_user),
):
    """Atomically replace an existing UTF-8 file if its read version is current."""
    try:
        result = await run_in_threadpool(
            write_workspace_text,
            user_id,
            payload.path,
            payload.content,
            expected_sha256=payload.expected_sha256,
        )
        _log_browser_write(
            user_id=user_id,
            path=result["path"],
            before_sha256=payload.expected_sha256,
            after_sha256=result["sha256"],
            size=result["size"],
            outcome="succeeded",
        )
        return result
    except FileNotFoundError as exc:
        _log_browser_write(
            user_id=user_id,
            path=payload.path,
            before_sha256=payload.expected_sha256,
            after_sha256=None,
            size=len(payload.content.encode("utf-8", errors="replace")),
            outcome="not_found",
        )
        raise HTTPException(status_code=404, detail=f"File not found: {payload.path}") from exc
    except IsADirectoryError as exc:
        _log_browser_write(
            user_id=user_id,
            path=payload.path,
            before_sha256=payload.expected_sha256,
            after_sha256=None,
            size=len(payload.content.encode("utf-8", errors="replace")),
            outcome="not_a_file",
        )
        raise HTTPException(status_code=400, detail=f"Path is not a file: {payload.path}") from exc
    except WorkspaceWriteLockTimeoutError as exc:
        _log_browser_write(
            user_id=user_id,
            path=payload.path,
            before_sha256=payload.expected_sha256,
            after_sha256=None,
            size=len(payload.content.encode("utf-8", errors="replace")),
            outcome="workspace_busy",
        )
        raise _workspace_busy_error(exc) from exc
    except PermissionError as exc:
        _log_browser_write(
            user_id=user_id,
            path=payload.path,
            before_sha256=payload.expected_sha256,
            after_sha256=None,
            size=len(payload.content.encode("utf-8", errors="replace")),
            outcome="permission_denied",
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except WorkspaceBinaryFileError as exc:
        _log_browser_write(
            user_id=user_id,
            path=payload.path,
            before_sha256=payload.expected_sha256,
            after_sha256=None,
            size=len(payload.content.encode("utf-8", errors="replace")),
            outcome="unsupported_media",
        )
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except WorkspaceFileTooLargeError as exc:
        _log_browser_write(
            user_id=user_id,
            path=payload.path,
            before_sha256=payload.expected_sha256,
            after_sha256=None,
            size=len(payload.content.encode("utf-8", errors="replace")),
            outcome="too_large",
        )
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except WorkspaceWriteConflictError as exc:
        _log_browser_write(
            user_id=user_id,
            path=payload.path,
            before_sha256=payload.expected_sha256,
            after_sha256=exc.current_sha256,
            size=len(payload.content.encode("utf-8", errors="replace")),
            outcome="version_conflict",
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "version_conflict",
                "message": "The file changed after it was opened. Reload before saving.",
                "current_sha256": exc.current_sha256,
            },
        ) from exc
    except ValueError as exc:
        _log_browser_write(
            user_id=user_id,
            path=payload.path,
            before_sha256=payload.expected_sha256,
            after_sha256=None,
            size=len(payload.content.encode("utf-8", errors="replace")),
            outcome="invalid_request",
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        _log_browser_write(
            user_id=user_id,
            path=payload.path,
            before_sha256=payload.expected_sha256,
            after_sha256=None,
            size=len(payload.content.encode("utf-8", errors="replace")),
            outcome="failed",
        )
        raise


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
    workspace_root = get_user_workspace(user_id).resolve()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path in selected:
            resolved = resolve_path(rel_path, user_id)
            if not resolved.exists():
                continue
            if resolved.is_dir():
                for file in resolved.rglob("*"):
                    # Never follow a workspace symlink into host or another
                    # tenant's data while building an archive.
                    if file.is_symlink():
                        continue
                    try:
                        safe_file = file.resolve()
                    except OSError:
                        continue
                    if safe_file.is_file() and safe_file.is_relative_to(workspace_root):
                        arcname = str(file.relative_to(resolved).as_posix())
                        zf.write(safe_file, arcname)
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
    content = await file.read()
    try:
        return await run_in_threadpool(
            _store_upload,
            user_id,
            path,
            file.filename or "untitled",
            content,
        )
    except NotADirectoryError as exc:
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {path}") from exc
    except WorkspaceWriteLockTimeoutError as exc:
        raise _workspace_busy_error(exc) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/mkdir")
async def create_directory(
    path: str = Query(..., description="Directory path to create"),
    user_id: int = Depends(get_current_user),
):
    """Create a directory in user workspace."""
    try:
        return await run_in_threadpool(_create_directory, user_id, path)
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"Path already exists: {path}")
    except WorkspaceWriteLockTimeoutError as exc:
        raise _workspace_busy_error(exc) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/delete")
async def delete_item(
    path: str = Query(..., description="Path to delete"),
    user_id: int = Depends(get_current_user),
):
    """Delete a file or directory from user workspace."""
    try:
        return await run_in_threadpool(_delete_item, user_id, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    except WorkspaceWriteLockTimeoutError as exc:
        raise _workspace_busy_error(exc) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/move")
async def move_item(
    source: str = Query(..., alias="from", description="Source path"),
    dest: str = Query(..., alias="to", description="Destination path"),
    user_id: int = Depends(get_current_user),
):
    """Move or rename a file/directory in user workspace."""
    try:
        return await run_in_threadpool(_move_item, user_id, source, dest)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Source not found: {source}")
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"Destination already exists: {dest}")
    except WorkspaceWriteLockTimeoutError as exc:
        raise _workspace_busy_error(exc) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
