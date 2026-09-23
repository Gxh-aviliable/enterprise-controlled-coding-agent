"""Validation and atomic materialization for managed shared Skills."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from enterprise_agent.config.settings import settings
from enterprise_agent.skills.packages import NAME_RE as SKILL_NAME_RE

FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", re.DOTALL)
SENSITIVE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
)


def validate_skill_content(expected_name: str, content: str) -> dict[str, Any]:
    """Compatibility response backed by the same parser as import and runtime."""
    from enterprise_agent.skills.packages import SkillError, parse_markdown

    errors = []
    metadata = {}
    warnings = []
    normalized = content
    try:
        normalized, metadata, _, warnings = parse_markdown(content)
        if metadata["name"] != expected_name:
            errors.append("Frontmatter name must match the registry name")
    except SkillError as exc:
        errors.append(str(exc))
        # Collect name mismatch as well when credential scanning rejected the body.
        redacted = content
        for pattern in SENSITIVE_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        try:
            _, metadata, _, _ = parse_markdown(redacted)
            if metadata["name"] != expected_name:
                errors.append("Frontmatter name must match the registry name")
        except SkillError:
            pass
    if any(pattern.search(content) for pattern in SENSITIVE_PATTERNS):
        errors.append("Potential credential or private key detected")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "metadata": metadata,
        "bytes": len(normalized.encode()),
        "estimated_tokens": max(1, len(normalized) // 4),
        "sha256": hashlib.sha256(normalized.encode()).hexdigest(),
    }


def managed_skill_path(name: str) -> Path:
    if not SKILL_NAME_RE.fullmatch(name):
        raise ValueError("Invalid Skill name")
    return Path(settings.MANAGED_SHARED_SKILLS_DIR) / name / "SKILL.md"


def materialize_skill(name: str, content: str, version: int | None = None) -> Path:
    """Atomically publish one active managed Skill to the runtime directory."""
    from enterprise_agent.skills.packages import parse_markdown

    content, _, _, _ = parse_markdown(content)
    target = managed_skill_path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".skill-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            if not content.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    manifest = target.parent / ".managed.json"
    if version is None:
        manifest.unlink(missing_ok=True)
    else:
        manifest.write_text(
            json.dumps(
                {
                    "source": "managed",
                    "version": version,
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    return target


def retire_materialized_skill(name: str) -> bool:
    """Remove only the active materialization; version bodies remain in MySQL."""
    target = managed_skill_path(name)
    if not target.exists():
        return False
    target.unlink()
    (target.parent / ".managed.json").unlink(missing_ok=True)
    try:
        target.parent.rmdir()
    except OSError:
        pass
    return True


def validation_json(value: dict[str, Any]) -> dict[str, Any]:
    """Ensure validation evidence is JSON serializable before persistence."""
    return json.loads(json.dumps(value, ensure_ascii=False))
