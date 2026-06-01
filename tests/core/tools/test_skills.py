"""Tests for skills module (load_skill, list_skills, reload_skills)."""

import tempfile
from pathlib import Path

import pytest

from enterprise_agent.core.agent.tools.skills import (
    SkillLoader,
    load_skill,
    list_skills,
    reload_skills,
)


class TestSkillLoader:
    """Test SkillLoader class."""

    @pytest.fixture
    def skills_dir(self, temp_workspace: Path):
        """Create skills directory with test skill."""
        skills_dir = temp_workspace / "skills"
        skills_dir.mkdir()

        # Create a test skill
        test_skill_dir = skills_dir / "test_skill"
        test_skill_dir.mkdir()

        skill_file = test_skill_dir / "SKILL.md"
        skill_file.write_text("""---
name: test_skill
description: A test skill for unit testing
---

# Test Skill Content

This is a test skill with some guidelines.

## Patterns

- Pattern 1: Do this
- Pattern 2: Do that
""")
        return skills_dir

    @pytest.fixture
    def skill_loader(self, skills_dir: Path):
        """Create SkillLoader with test skills directory."""
        return SkillLoader(skills_dir)

    def test_load_all_skills(self, skill_loader: SkillLoader):
        """Test that skills are loaded from directory."""
        assert len(skill_loader.skills) >= 1
        assert "test_skill" in skill_loader.skills

    def test_skill_has_metadata(self, skill_loader: SkillLoader):
        """Test that skill has metadata."""
        skill = skill_loader.skills.get("test_skill")
        assert skill is not None
        assert skill["meta"]["description"] == "A test skill for unit testing"

    def test_skill_has_body(self, skill_loader: SkillLoader):
        """Test that skill has body content."""
        skill = skill_loader.skills.get("test_skill")
        assert skill is not None
        assert "Test Skill Content" in skill["body"]

    def test_skill_has_path(self, skill_loader: SkillLoader):
        """Test that skill has file path."""
        skill = skill_loader.skills.get("test_skill")
        assert skill is not None
        assert "SKILL.md" in skill["path"]

    def test_load_existing_skill(self, skill_loader: SkillLoader):
        """Test loading an existing skill."""
        result = skill_loader.load("test_skill")
        assert "<skill" in result
        assert "Test Skill Content" in result

    def test_load_nonexistent_skill(self, skill_loader: SkillLoader):
        """Test loading nonexistent skill returns error."""
        result = skill_loader.load("nonexistent_skill")
        assert "Error" in result or "Unknown" in result

    def test_list_all_skills(self, skill_loader: SkillLoader):
        """Test listing all skills."""
        result = skill_loader.list_all()
        assert "test_skill" in result
        assert "test skill" in result.lower()

    def test_list_empty_skills(self, temp_workspace: Path):
        """Test listing when no skills."""
        empty_dir = temp_workspace / "empty_skills"
        empty_dir.mkdir()
        loader = SkillLoader(empty_dir)

        result = loader.list_all()
        assert "No skills" in result

    def test_reload_skills(self, skill_loader: SkillLoader):
        """Test reloading skills."""
        result = skill_loader.reload()
        assert "Reloaded" in result

    def test_descriptions_format(self, skill_loader: SkillLoader):
        """Test descriptions output format."""
        result = skill_loader.descriptions()
        assert "test_skill:" in result or "test_skill" in result


class TestSkillLoaderEdgeCases:
    """Test SkillLoader edge cases."""

    def test_skill_without_frontmatter(self, temp_workspace: Path):
        """Test loading skill without YAML frontmatter."""
        skills_dir = temp_workspace / "skills"
        skills_dir.mkdir()

        test_skill_dir = skills_dir / "no_frontmatter"
        test_skill_dir.mkdir()

        skill_file = test_skill_dir / "SKILL.md"
        skill_file.write_text("# Skill without frontmatter\n\nJust content.")

        loader = SkillLoader(skills_dir)
        # Should still load, using directory name
        assert "no_frontmatter" in loader.skills

    def test_skill_with_invalid_yaml(self, temp_workspace: Path):
        """Test skill with malformed YAML."""
        skills_dir = temp_workspace / "skills"
        skills_dir.mkdir()

        test_skill_dir = skills_dir / "bad_yaml"
        test_skill_dir.mkdir()

        skill_file = test_skill_dir / "SKILL.md"
        skill_file.write_text("""---
invalid yaml content here
---

# Bad YAML Skill
""")
        # Should handle gracefully
        loader = SkillLoader(skills_dir)
        # Either loads with empty meta or skips
        assert isinstance(loader.skills, dict)

    def test_nonexistent_skills_dir(self):
        """Test with nonexistent skills directory."""
        loader = SkillLoader(Path("/nonexistent/path"))
        assert loader.skills == {}


class TestSkillTools:
    """Test skill tools."""

    def test_list_skills_returns_string(self):
        """Test list_skills returns string."""
        result = list_skills.invoke({})
        assert isinstance(result, str)

    def test_load_skill_with_invalid_name(self):
        """Test load_skill with invalid name."""
        result = load_skill.invoke({"name": "nonexistent_skill"})
        assert "Error" in result or "Unknown" in result

    def test_reload_skills_returns_count(self):
        """Test reload_skills returns count."""
        result = reload_skills.invoke({})
        assert "Reloaded" in result


class TestSkillContentFormat:
    """Test skill content XML format."""

    @pytest.fixture
    def skill_loader_for_format(self, temp_workspace: Path):
        """Create SkillLoader with test skill for format testing."""
        skills_dir = temp_workspace / "skills"
        skills_dir.mkdir()

        test_skill_dir = skills_dir / "test_skill"
        test_skill_dir.mkdir()

        skill_file = test_skill_dir / "SKILL.md"
        skill_file.write_text("""---
name: test_skill
description: A test skill
---

# Test Skill Content
""")
        return SkillLoader(skills_dir)

    def test_skill_wrapped_in_xml_tag(self, skill_loader_for_format: SkillLoader):
        """Test skill content is wrapped in XML tag."""
        result = skill_loader_for_format.load("test_skill")
        assert 'test_skill' in result and result.startswith('<skill')
        assert result.endswith('</skill>')
        assert result.endswith('</skill>')