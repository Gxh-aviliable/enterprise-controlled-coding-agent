"""Skill loader — multi-tenant with global + user-scoped skills.

Skills are stored as SKILL.md files:
- Global: shared_skills/<name>/SKILL.md (available to all users)
- User:   user_{id}/.skills/<name>/SKILL.md (personal, overrides global)

Each SKILL.md has YAML frontmatter:
  ---
  name: my-skill
  description: What this skill does
  ---
  Skill content in markdown...

Priority: user skill overrides global skill with the same name.
"""

import logging
import re
from pathlib import Path
from typing import Dict

from langchain_core.tools import tool

from enterprise_agent.config.settings import settings

logger = logging.getLogger("enterprise_agent")


class SkillLoader:
    """Multi-source skill loader with user isolation.

    Loads skills from a priority-ordered list of directories.
    Later directories override earlier ones by skill name.
    """

    def __init__(self, search_dirs):
        # Accept single Path or list of Paths
        if isinstance(search_dirs, (str, Path)):
            search_dirs = [Path(search_dirs)]
        self.search_dirs = [Path(d) for d in search_dirs]
        self.skills: Dict[str, Dict] = {}
        self._load_all()

    def _load_all(self) -> None:
        """Load skills from all search directories.

        Directories are loaded from lowest to highest priority,
        so higher-priority skills override lower-priority ones.
        search_dirs[0] = user (highest), search_dirs[-1] = global (lowest)
        """
        # Load from end to start: global first, then user (overwrites)
        for search_dir in list(reversed(self.search_dirs)):
            if not search_dir.exists():
                continue
            for skill_file in sorted(search_dir.rglob("SKILL.md")):
                self._load_skill_file(skill_file, search_dir)

    def _load_skill_file(self, skill_file: Path, search_dir: Path) -> None:
        """Parse a single SKILL.md file."""
        try:
            text = skill_file.read_text(encoding="utf-8")

            # Parse YAML frontmatter
            meta = {}
            body = text
            match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)", text, re.DOTALL)
            if match:
                for line in match.group(1).strip().splitlines():
                    if ":" in line:
                        key, value = line.split(":", 1)
                        meta[key.strip()] = value.strip()
                body = match.group(2).strip()

            name = meta.get("name", skill_file.parent.name)

            # Determine scope: first directory = user/personal (highest priority)
            # If only one directory, treat as global
            is_user_dir = (
                len(self.search_dirs) > 1 and search_dir == self.search_dirs[0]
            )
            scope = "personal" if is_user_dir else "global"

            # Override detection: warn if user skill overrides global
            if name in self.skills and scope == "personal":
                logger.info("User skill '%s' overrides global skill", name)

            self.skills[name] = {
                "meta": meta,
                "body": body,
                "path": str(skill_file),
                "scope": scope,
            }
        except Exception as e:
            logger.warning("Failed to load skill %s: %s", skill_file, e)

    def descriptions(self) -> str:
        """Get formatted skill list for system prompt injection.

        Returns short summary suitable for embedding in the system prompt.
        """
        if not self.skills:
            return "(no skills available)"

        globals = []
        personals = []
        for name, skill in self.skills.items():
            desc = skill["meta"].get("description", "-")
            if skill["scope"] == "personal":
                personals.append(f"  - {name} [personal]: {desc}")
            else:
                globals.append(f"  - {name}: {desc}")

        lines = []
        if globals:
            lines.append("## Global Skills")
            lines.extend(globals)
        if personals:
            lines.append("\n## Your Skills")
            lines.extend(personals)
        return "\n".join(lines)

    def list_all(self) -> str:
        """List all available skills with scope labels."""
        if not self.skills:
            return "No skills available."

        lines = ["Available skills:"]
        for name, skill in self.skills.items():
            desc = skill["meta"].get("description", "")
            tag = "[personal]" if skill["scope"] == "personal" else "[global]"
            lines.append(f"  - {name} {tag}: {desc}")
        return "\n".join(lines)

    def load(self, name: str) -> str:
        """Load a skill by name (user version takes priority).

        Args:
            name: Skill name to load

        Returns:
            Skill content wrapped in <skill> tags
        """
        skill = self.skills.get(name)
        if not skill:
            available = ", ".join(self.skills.keys()) if self.skills else "none"
            return f"Error: Unknown skill '{name}'. Available skills: {available}"

        scope_note = ""
        if skill["scope"] == "personal":
            scope_note = " (your personal version)"
        return (
            f'<skill name="{name}" scope="{skill["scope"]}">\n'
            f"<!-- {skill['scope']} skill{scope_note} -->\n"
            f'{skill["body"]}\n'
            f"</skill>"
        )

    def reload(self) -> str:
        """Reload all skills from directories."""
        self.skills.clear()
        self._load_all()
        global_count = sum(1 for s in self.skills.values() if s["scope"] == "global")
        personal_count = sum(1 for s in self.skills.values() if s["scope"] == "personal")
        return (
            f"Reloaded {len(self.skills)} skills "
            f"({global_count} global, {personal_count} personal)"
        )


# Per-user SkillLoader cache
_skill_loaders: Dict[int, SkillLoader] = {}


def get_skill_loader(user_id: int = None) -> SkillLoader:
    """Get or create SkillLoader for a user.

    Returns a SkillLoader that searches:
      1. User's personal .skills directory (highest priority)
      2. Shared global skills directory

    Args:
        user_id: User ID. If None, tries context variable.

    Returns:
        SkillLoader instance for the user
    """
    from enterprise_agent.core.agent.tools.workspace import get_current_user_id, get_workspace_base

    if user_id is None:
        user_id = get_current_user_id()

    if user_id not in _skill_loaders:
        search_dirs = [
            # User personal skills (highest priority — index 0)
            get_workspace_base() / f"user_{user_id}" / ".skills",
            # Shared global skills
            Path(settings.SHARED_SKILLS_DIR),
        ]
        _skill_loaders[user_id] = SkillLoader(search_dirs)

    return _skill_loaders[user_id]


@tool
def list_skills() -> str:
    """List available skill modules. Shows both global and your personal skills.

    Use when: Working with specific technology/framework (LangGraph, FastAPI, React)
              or need patterns/best practices before coding.
              [personal] skills are your own; [global] are shared by all users.

    Example: Building LangGraph project -> list_skills() -> load_skill("langgraph")

    Returns:
        List of skill names with scope markers
    """
    return get_skill_loader().list_all()


@tool
def load_skill(name: str) -> str:
    """Load a skill module to gain expert knowledge.

    Your personal skills override global skills with the same name.

    Use when: list_skills() shows a relevant skill for your task.

    Example: list_skills() shows "langgraph" -> load_skill("langgraph")

    Args:
        name: Skill name from list_skills()

    Returns:
        Skill content in <skill> tags with scope marker
    """
    return get_skill_loader().load(name)


@tool
def reload_skills() -> str:
    """Reload all skills (both global and personal).

    Use after editing or creating SKILL.md files.

    Returns:
        Count of skills loaded by scope
    """
    return get_skill_loader().reload()
