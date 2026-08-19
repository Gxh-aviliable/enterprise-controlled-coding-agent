"""Workspace-scoped evidence storage for Agent tool outputs.

Tool output is often much larger than the model should receive.  This module
stores a private, bounded and redacted copy before any model-facing preview is
created, then returns a receipt that can be linked from messages and Trace.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from enterprise_agent.config.settings import settings

ARTIFACT_ROOT = Path(".agent") / "tool-artifacts"
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
ARTIFACT_READ_MAX_BYTES = 32_000


def read_utf8_range(
    path: Path,
    *,
    offset_bytes: int,
    limit_bytes: int,
    max_limit_bytes: int = ARTIFACT_READ_MAX_BYTES,
) -> dict[str, Any]:
    """Read a bounded page without splitting valid UTF-8 code points."""
    requested_offset = max(0, int(offset_bytes))
    limit = max(1, min(int(limit_bytes), int(max_limit_bytes)))
    total_bytes = path.stat().st_size
    with path.open("rb") as handle:
        actual_offset = min(requested_offset, total_bytes)
        while actual_offset < total_bytes:
            handle.seek(actual_offset)
            current = handle.read(1)
            if not current or current[0] & 0b1100_0000 != 0b1000_0000:
                break
            actual_offset += 1
        handle.seek(actual_offset)
        candidate = handle.read(limit + 4)

    decoded = candidate.decode("utf-8", errors="ignore")
    characters: list[str] = []
    returned_bytes = 0
    for character in decoded:
        character_bytes = len(character.encode("utf-8"))
        if characters and returned_bytes + character_bytes > limit:
            break
        characters.append(character)
        returned_bytes += character_bytes
        if returned_bytes >= limit:
            break
    next_offset = min(total_bytes, actual_offset + returned_bytes)
    return {
        "offset_bytes": actual_offset,
        "returned_bytes": returned_bytes,
        "next_offset_bytes": next_offset,
        "total_bytes": total_bytes,
        "eof": next_offset >= total_bytes,
        "content": "".join(characters),
    }


def safe_path_component(value: Any, prefix: str) -> str:
    """Return a path-safe identifier without trusting model supplied IDs."""
    raw = str(value or "")
    if SAFE_COMPONENT.fullmatch(raw):
        return raw
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _bounded_capture(value: str, limit: int) -> tuple[str, bool]:
    """Keep useful head and tail evidence when a defensive storage cap applies."""
    if limit <= 0 or len(value) <= limit:
        return value, False
    marker = f"\n... [artifact source clipped {len(value) - limit} chars] ...\n"
    available = max(0, limit - len(marker))
    head_size = available * 3 // 5
    tail_size = available - head_size
    return value[:head_size] + marker + (value[-tail_size:] if tail_size else ""), True


@dataclass(frozen=True)
class ToolArtifactReceipt:
    """Verifiable metadata for one persisted tool-output artifact."""

    path: str
    sha256: str
    original_chars: int
    stored_chars: int
    stored_bytes: int
    source_truncated: bool
    redacted: bool
    recovered_from_message: bool = False
    limited_original: bool = False
    encoding: str = "utf-8"
    storage_status: str = "stored"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolArtifactStore:
    """Atomically persist tool evidence inside one authenticated workspace."""

    def __init__(self, *, user_id: int | None = None, workdir: Path | None = None):
        if workdir is None:
            from enterprise_agent.core.agent.tools.workspace import get_user_workspace

            workspace = get_user_workspace(user_id)
        else:
            workspace = Path(workdir)
        self.workspace = workspace.resolve()

    def _secure_directory(self, trace_id: str) -> Path:
        trace_component = safe_path_component(trace_id, "trace")
        current = self.workspace
        for part in (*ARTIFACT_ROOT.parts, trace_component):
            current = current / part
            if current.exists() and current.is_symlink():
                raise ValueError(f"Artifact directory cannot be a symlink: {current.name}")
            current.mkdir(mode=0o700, exist_ok=True)
            resolved = current.resolve()
            if not resolved.is_relative_to(self.workspace):
                raise ValueError("Artifact directory escapes the user workspace")
            try:
                os.chmod(current, 0o700)
            except OSError:
                # Windows does not provide POSIX permission semantics.
                pass
        return current

    def _resolve_existing_artifact(self, relative_path: str) -> Path:
        """Resolve one stored artifact without trusting a receipt-provided path."""
        raw_path = str(relative_path or "")
        candidate_relative = Path(raw_path)
        expected_parts = len(ARTIFACT_ROOT.parts) + 2  # root / trace / file
        if (
            not raw_path
            or candidate_relative.is_absolute()
            or "\\" in raw_path
            or ".." in candidate_relative.parts
            or candidate_relative.parts[: len(ARTIFACT_ROOT.parts)] != ARTIFACT_ROOT.parts
            or len(candidate_relative.parts) != expected_parts
        ):
            raise ValueError("invalid_artifact_path")

        current = self.workspace
        for part in candidate_relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("artifact_symlink_rejected")

        root = (self.workspace / ARTIFACT_ROOT).resolve()
        resolved = (self.workspace / candidate_relative).resolve()
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError("artifact_not_found")
        return resolved

    @staticmethod
    def _file_digest(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        stored_bytes = 0
        with path.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                digest.update(chunk)
                stored_bytes += len(chunk)
        return digest.hexdigest(), stored_bytes

    def validate_receipt(self, receipt: dict[str, Any]) -> tuple[bool, str | None]:
        """Verify location, checksum and optional size before evidence is discarded."""
        if receipt.get("storage_status") != "stored":
            return False, "artifact_not_stored"
        original_chars = receipt.get("original_chars")
        if (
            isinstance(original_chars, bool)
            or not isinstance(original_chars, int)
            or original_chars < 0
        ):
            return False, "artifact_invalid_metadata"
        expected_sha = str(receipt.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            return False, "artifact_invalid_sha256"
        try:
            path = self._resolve_existing_artifact(str(receipt.get("path") or ""))
            actual_sha, actual_bytes = self._file_digest(path)
        except ValueError as exc:
            return False, str(exc)
        except OSError:
            return False, "artifact_read_failed"
        if actual_sha != expected_sha:
            return False, "artifact_hash_mismatch"
        expected_bytes = receipt.get("stored_bytes")
        if expected_bytes is not None:
            if (
                isinstance(expected_bytes, bool)
                or not isinstance(expected_bytes, int)
                or expected_bytes < 0
            ):
                return False, "artifact_invalid_size"
            if expected_bytes != actual_bytes:
                return False, "artifact_size_mismatch"
        return True, None

    def save(
        self,
        raw_output: Any,
        *,
        trace_id: str,
        tool_call_id: str,
        recovered_from_message: bool = False,
        limited_original: bool = False,
        source_already_truncated: bool = False,
    ) -> ToolArtifactReceipt:
        """Save a restricted-original output and return a workspace-relative receipt."""
        source = str(raw_output)
        captured, source_truncated = _bounded_capture(
            source,
            settings.TOOL_ARTIFACT_MAX_CHARS,
        )
        from enterprise_agent.observability.trace_store import redact_text

        stored = redact_text(captured, limit=None)
        encoded = stored.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()

        directory = self._secure_directory(trace_id)
        call_component = safe_path_component(tool_call_id, "call")
        # Content-address the filename so a replayed/missing tool-call ID can
        # never overwrite evidence referenced by an older checkpoint.
        path = directory / f"{call_component}-{digest[:16]}.txt"
        if path.exists() and path.is_symlink():
            raise ValueError("Artifact target cannot be a symlink")
        if not path.parent.resolve().is_relative_to(self.workspace):
            raise ValueError("Artifact target escapes the user workspace")

        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=directory,
                prefix=f".{call_component}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
                temporary_name = handle.name
            try:
                os.chmod(temporary_name, 0o600)
            except OSError:
                pass
            os.replace(temporary_name, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        finally:
            if temporary_name and os.path.exists(temporary_name):
                os.unlink(temporary_name)

        relative_path = path.relative_to(self.workspace).as_posix()
        return ToolArtifactReceipt(
            path=relative_path,
            sha256=digest,
            original_chars=len(source),
            stored_chars=len(stored),
            stored_bytes=len(encoded),
            source_truncated=source_truncated or source_already_truncated,
            redacted=stored != captured,
            recovered_from_message=recovered_from_message,
            limited_original=limited_original,
        )

    def read_range(
        self,
        relative_path: str,
        *,
        expected_sha256: str,
        offset_bytes: int = 0,
        limit_bytes: int = ARTIFACT_READ_MAX_BYTES,
    ) -> dict[str, Any]:
        """Read a bounded UTF-8 range after verifying the receipt checksum."""
        if not re.fullmatch(r"[0-9a-f]{64}", str(expected_sha256 or "")):
            raise ValueError("artifact_invalid_sha256")
        path = self._resolve_existing_artifact(relative_path)
        sha256, _ = self._file_digest(path)
        if sha256 != expected_sha256:
            raise ValueError("artifact_hash_mismatch")
        page = read_utf8_range(
            path,
            offset_bytes=offset_bytes,
            limit_bytes=limit_bytes,
        )
        verified_sha256, _ = self._file_digest(path)
        if verified_sha256 != expected_sha256:
            raise ValueError("artifact_hash_mismatch")
        return {
            "path": Path(relative_path).as_posix(),
            "sha256": sha256,
            **page,
        }

    def read_range_json(
        self,
        relative_path: str,
        *,
        expected_sha256: str,
        offset_bytes: int = 0,
        limit_bytes: int = ARTIFACT_READ_MAX_BYTES,
    ) -> str:
        return json.dumps(
            self.read_range(
                relative_path,
                expected_sha256=expected_sha256,
                offset_bytes=offset_bytes,
                limit_bytes=limit_bytes,
            ),
            ensure_ascii=False,
        )


def format_tool_output(
    raw_output: Any,
    *,
    receipt: ToolArtifactReceipt | None,
    status: str,
    error_code: str | None = None,
    exit_code: int | None = None,
    artifact_error: str | None = None,
) -> tuple[str, bool]:
    """Create a bounded model/UI preview while retaining explicit evidence status."""
    from enterprise_agent.observability.trace_store import redact_text

    source = redact_text(str(raw_output), limit=None)
    status_bits = [f"status={status}"]
    if error_code:
        status_bits.append(f"error_code={error_code}")
    if exit_code is not None:
        status_bits.append(f"exit_code={exit_code}")
    header = "[tool-result " + " ".join(status_bits) + "]"

    if receipt is not None:
        footer = (
            f'[artifact path="{receipt.path}" sha256="{receipt.sha256}" '
            f"original_chars={receipt.original_chars} stored_chars={receipt.stored_chars} "
            f"source_truncated={str(receipt.source_truncated).lower()} "
            f"redacted={str(receipt.redacted).lower()}]"
        )
    elif artifact_error:
        footer = f"[artifact unavailable: {artifact_error}]"
    else:
        footer = ""

    fixed_size = len(header) + len(footer) + (2 if footer else 1)
    body_limit = max(0, settings.TOOL_OUTPUT_MAX_CHARS - fixed_size)
    if len(source) <= body_limit:
        body = source
        model_truncated = False
    elif body_limit <= 0:
        body = ""
        model_truncated = True
    else:
        marker = f"\n... [model preview clipped {len(source) - body_limit} chars] ...\n"
        if len(marker) >= body_limit:
            body = marker[:body_limit]
            model_truncated = True
            parts = [header, body]
            if footer:
                parts.append(footer)
            return "\n".join(parts), model_truncated
        available = max(0, body_limit - len(marker))
        head_size = available * 3 // 5
        tail_size = available - head_size
        body = source[:head_size] + marker + (source[-tail_size:] if tail_size else "")
        model_truncated = True

    parts = [header]
    if body:
        parts.append(body)
    if footer:
        parts.append(footer)
    return "\n".join(parts), model_truncated
