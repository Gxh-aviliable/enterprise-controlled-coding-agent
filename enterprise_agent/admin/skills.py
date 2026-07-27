"""Validation and atomic materialization for managed shared Skills."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from enterprise_agent.config.settings import settings

SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,79}$")
FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", re.DOTALL)
SENSITIVE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
)


def validate_skill_content(expected_name: str, content: str) -> dict[str, Any]:
    """Return deterministic validation evidence for one SKILL.md body."""
    errors: list[str] = []
    warnings: list[str] = []
    normalized_name = expected_name.strip().lower()

    if not SKILL_NAME_RE.fullmatch(normalized_name):
        errors.append("Skill name must be a lowercase slug")
    if len(content.encode("utf-8")) > 100_000:
        errors.append("Skill content exceeds 100 KB")

    match = FRONTMATTER_RE.match(content)
    metadata: dict[str, str] = {}
    body = content
    if not match:
        errors.append("SKILL.md must start with YAML frontmatter")
    else:
        for line in match.group(1).strip().splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip().strip('"\'')
        body = match.group(2).strip()

    declared_name = metadata.get("name", "")
    if declared_name != normalized_name:
        errors.append("Frontmatter name must match the registry name")
    if not metadata.get("description"):
        errors.append("Frontmatter description is required")
    if len(body) < 20:
        errors.append("Skill guidance body is too short")
    if content.count("```") % 2:
        warnings.append("Markdown contains an unclosed fenced code block")
    if any(pattern.search(content) for pattern in SENSITIVE_PATTERNS):
        errors.append("Potential credential or private key detected")

    estimated_tokens = max(1, len(content) // 4)
    if estimated_tokens > 12_000:
        warnings.append("Skill is large and may materially increase model cost")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "metadata": metadata,
        "bytes": len(content.encode("utf-8")),
        "estimated_tokens": estimated_tokens,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def managed_skill_path(name: str) -> Path:
    if not SKILL_NAME_RE.fullmatch(name):
        raise ValueError("Invalid Skill name")
    return Path(settings.MANAGED_SHARED_SKILLS_DIR) / name / "SKILL.md"


def materialize_skill(name: str, content: str, version: int | None = None) -> Path:
    """Atomically publish one active managed Skill to the runtime directory."""
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
