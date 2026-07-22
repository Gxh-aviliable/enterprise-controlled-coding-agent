"""Managed Shared Skill validation and persistence tests."""

from enterprise_agent.admin.skills import (
    materialize_skill,
    retire_materialized_skill,
    validate_skill_content,
)
from enterprise_agent.config.settings import settings

VALID_SKILL = """---
name: python-quality
description: Python quality checks for internal repositories
---

# Python quality

Run pytest and ruff after changing Python code.
"""


def test_skill_validation_emits_hash_and_cost_evidence():
    result = validate_skill_content("python-quality", VALID_SKILL)
    assert result["valid"] is True
    assert result["sha256"]
    assert result["estimated_tokens"] > 0


def test_skill_validation_rejects_name_mismatch_and_secrets():
    content = VALID_SKILL.replace("python-quality", "other-name") + "\nsk-secretvalue0123456789"
    result = validate_skill_content("python-quality", content)
    assert result["valid"] is False
    assert any("name" in error.lower() for error in result["errors"])
    assert any("credential" in error.lower() for error in result["errors"])


def test_managed_skill_materialization_is_retirable(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MANAGED_SHARED_SKILLS_DIR", str(tmp_path))
    path = materialize_skill("python-quality", VALID_SKILL, version=3)
    assert path.read_text(encoding="utf-8") == VALID_SKILL
    assert '"version": 3' in (path.parent / ".managed.json").read_text(encoding="utf-8")
    assert retire_materialized_skill("python-quality") is True
    assert not path.exists()
