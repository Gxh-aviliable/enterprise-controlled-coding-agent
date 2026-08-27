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

import hashlib
import html
import json
import logging
import os
import re
import stat
from collections import deque
from pathlib import Path
from typing import Dict

from langchain_core.tools import tool

from enterprise_agent.config.settings import settings

logger = logging.getLogger("enterprise_agent")

PROMPT_SKILL_LIMIT = 64
PROMPT_SKILL_DESCRIPTION_CHARS = 500
PROMPT_SKILL_TOTAL_DESCRIPTION_CHARS = 16_000
PROMPT_SKILL_CATALOG_BYTES = 64_000
PROMPT_SKILL_NAME_CHARS = 128
PROMPT_SKILL_METADATA_CHARS = 128
MAX_SKILL_FILE_BYTES = 128_000
MAX_MANAGED_MANIFEST_BYTES = 16_000
MAX_SKILL_FILES_PER_SOURCE = 128
MAX_SKILL_SCAN_DEPTH = 6
MAX_SKILL_SCAN_DIRECTORIES = 256
MAX_SKILL_ENTRIES_PER_DIRECTORY = 512


def _read_bounded_regular_text(path: Path, root: Path, byte_limit: int) -> str:
    """Read a complete UTF-8 regular file without following workspace links."""
    root_absolute = Path(os.path.abspath(root))
    path_absolute = Path(os.path.abspath(path))
    try:
        root_mode = root_absolute.lstat().st_mode
    except OSError as exc:
        raise ValueError("skill source is unreadable") from exc
    if stat.S_ISLNK(root_mode):
        raise ValueError("skill source cannot be a symbolic link")
    if not stat.S_ISDIR(root_mode):
        raise ValueError("skill source is not a directory")
    try:
        relative = path_absolute.relative_to(root_absolute)
    except ValueError as exc:
        raise ValueError("skill path escapes its configured source") from exc

    cursor = root_absolute
    for part in relative.parts:
        cursor /= part
        try:
            if stat.S_ISLNK(cursor.lstat().st_mode):
                raise ValueError("skill path contains a symbolic link")
        except OSError as exc:
            raise ValueError("skill path is unreadable") from exc

    try:
        resolved_root = root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("skill path is unavailable") from exc
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError("skill path escapes its configured source")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("skill path is not a regular file")
        chunks = []
        remaining = byte_limit + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)

    payload = b"".join(chunks)
    if len(payload) > byte_limit:
        raise ValueError("skill file exceeds the configured byte limit")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("skill file is not valid UTF-8") from exc
    if "\x00" in text:
        raise ValueError("skill file contains NUL bytes")
    return text


def _discover_skill_files(root: Path) -> list[Path]:
    """Discover Skill files without following links or walking without bounds."""
    root_absolute = Path(os.path.abspath(root))
    try:
        root_mode = root_absolute.lstat().st_mode
    except OSError as exc:
        raise ValueError("skill source is unreadable") from exc
    if stat.S_ISLNK(root_mode):
        raise ValueError("skill source cannot be a symbolic link")
    if not stat.S_ISDIR(root_mode):
        raise ValueError("skill source is not a directory")

    pending = deque([(root_absolute, 0)])
    candidates: list[Path] = []
    visited_directories = 0
    scan_truncated = False

    while pending and len(candidates) < MAX_SKILL_FILES_PER_SOURCE:
        directory, depth = pending.popleft()
        if visited_directories >= MAX_SKILL_SCAN_DIRECTORIES:
            scan_truncated = True
            break
        visited_directories += 1

        entries = []
        try:
            with os.scandir(directory) as iterator:
                for index, entry in enumerate(iterator):
                    if index >= MAX_SKILL_ENTRIES_PER_DIRECTORY:
                        scan_truncated = True
                        break
                    entries.append(entry)
        except OSError as exc:
            logger.warning("Ignoring unreadable Skill directory %s: %s", directory, exc)
            continue

        for entry in sorted(entries, key=lambda item: (item.name.casefold(), item.name)):
            try:
                if entry.is_symlink():
                    continue
                if entry.name == "SKILL.md" and entry.is_file(follow_symlinks=False):
                    candidates.append(Path(entry.path))
                    if len(candidates) >= MAX_SKILL_FILES_PER_SOURCE:
                        scan_truncated = True
                        break
                elif depth < MAX_SKILL_SCAN_DEPTH and entry.is_dir(follow_symlinks=False):
                    pending.append((Path(entry.path), depth + 1))
            except OSError:
                continue

    if pending or scan_truncated:
        logger.warning(
            "Skill source scan limit reached; remaining entries were ignored: %s",
            root,
        )
    return candidates


def _valid_skill_name(name: str) -> bool:
    return (
        bool(name)
        and len(name) <= PROMPT_SKILL_NAME_CHARS
        and not any(ord(character) < 32 or ord(character) == 127 for character in name)
    )


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
            try:
                candidates = _discover_skill_files(search_dir)
            except ValueError:
                continue
            for skill_file in sorted(
                candidates,
                key=lambda path: (str(path).casefold(), str(path)),
            ):
                self._load_skill_file(skill_file, search_dir)

    def _load_skill_file(self, skill_file: Path, search_dir: Path) -> None:
        """Parse a single SKILL.md file."""
        try:
            text = _read_bounded_regular_text(
                skill_file,
                search_dir,
                MAX_SKILL_FILE_BYTES,
            )

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

            name = str(meta.get("name", skill_file.parent.name)).strip()
            if not _valid_skill_name(name):
                raise ValueError("skill name is empty, oversized, or contains controls")

            # Determine scope: first directory = user/personal (highest priority)
            # If only one directory, treat as global
            is_user_dir = len(self.search_dirs) > 1 and search_dir == self.search_dirs[0]
            scope = "personal" if is_user_dir else "global"
            is_managed_dir = search_dir.resolve() == Path(settings.MANAGED_SHARED_SKILLS_DIR).resolve()
            manifest_path = skill_file.parent / ".managed.json"
            manifest = {}
            if is_managed_dir and manifest_path.exists():
                try:
                    manifest = json.loads(
                        _read_bounded_regular_text(
                            manifest_path,
                            search_dir,
                            MAX_MANAGED_MANIFEST_BYTES,
                        )
                    )
                    if not isinstance(manifest, dict):
                        manifest = {}
                except (OSError, ValueError, json.JSONDecodeError):
                    logger.warning("Ignoring invalid managed Skill manifest: %s", manifest_path)

            # Override detection: warn if user skill overrides global
            if name in self.skills and scope == "personal":
                logger.info("User skill '%s' overrides global skill", name)

            computed_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
            declared_sha256 = str(manifest.get("sha256") or "").lower()
            if not re.fullmatch(r"[0-9a-f]{64}", declared_sha256):
                declared_sha256 = computed_sha256
            version = manifest.get("version")
            if version is not None:
                version = str(version)[:PROMPT_SKILL_METADATA_CHARS]

            self.skills[name] = {
                "meta": meta,
                "body": body,
                "path": str(skill_file),
                "scope": scope,
                "source": "managed" if is_managed_dir else scope,
                "version": version,
                "sha256": declared_sha256,
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

    def prompt_catalog(self) -> str:
        """Return deterministic JSON metadata for low-trust prompt context."""
        entries = []
        description_chars = 0
        descriptions_truncated = False
        ordered_names = sorted(self.skills, key=lambda value: (value.casefold(), value))
        for name in ordered_names[:PROMPT_SKILL_LIMIT]:
            skill = self.skills[name]
            description = str(skill["meta"].get("description", ""))
            remaining = max(
                0,
                PROMPT_SKILL_TOTAL_DESCRIPTION_CHARS - description_chars,
            )
            description_limit = min(PROMPT_SKILL_DESCRIPTION_CHARS, remaining)
            bounded_description = description[:description_limit]
            description_chars += len(bounded_description)
            description_truncated = len(description) > len(bounded_description)
            descriptions_truncated = descriptions_truncated or description_truncated
            bounded_name = str(name)[:PROMPT_SKILL_NAME_CHARS]
            version = skill.get("version")
            bounded_version = None if version is None else str(version)[:PROMPT_SKILL_METADATA_CHARS]
            sha256 = str(skill.get("sha256") or "")
            bounded_sha256 = sha256[:PROMPT_SKILL_METADATA_CHARS]
            entries.append(
                {
                    "name": bounded_name,
                    "scope": str(skill["scope"])[:32],
                    "source": str(skill["source"])[:32],
                    "version": bounded_version,
                    "sha256": bounded_sha256,
                    "description": bounded_description,
                    "description_truncated": description_truncated,
                    "metadata_truncated": bool(
                        len(str(name)) > len(bounded_name)
                        or (version is not None and len(str(version)) > len(bounded_version or ""))
                        or len(sha256) > len(bounded_sha256)
                    ),
                }
            )
        omitted_count = max(0, len(ordered_names) - len(entries))

        def render_catalog() -> str:
            return json.dumps(
                {
                    "schema_version": 1,
                    "skills": entries,
                    "catalog_truncated": bool(
                        omitted_count or descriptions_truncated or any(entry["metadata_truncated"] for entry in entries)
                    ),
                    "omitted_count": omitted_count,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )

        rendered = render_catalog()
        while entries and len(rendered.encode("utf-8")) > PROMPT_SKILL_CATALOG_BYTES:
            entries.pop()
            omitted_count += 1
            rendered = render_catalog()
        return rendered

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
        version_attr = ""
        escaped_name = html.escape(name, quote=True)
        escaped_scope = html.escape(str(skill["scope"]), quote=True)
        escaped_source = html.escape(str(skill["source"]), quote=True)
        escaped_sha256 = html.escape(str(skill["sha256"]), quote=True)
        if skill.get("version") is not None:
            version_attr = f' version="{html.escape(str(skill["version"]), quote=True)}"'
        return (
            f'<skill name="{escaped_name}" scope="{escaped_scope}" source="{escaped_source}"'
            f'{version_attr} sha256="{escaped_sha256}">\n'
            f"<!-- {skill['scope']} skill{scope_note} -->\n"
            f"{skill['body']}\n"
            f"</skill>"
        )

    def reload(self) -> str:
        """Reload all skills from directories."""
        self.skills.clear()
        self._load_all()
        global_count = sum(1 for s in self.skills.values() if s["scope"] == "global")
        personal_count = sum(1 for s in self.skills.values() if s["scope"] == "personal")
        return f"Reloaded {len(self.skills)} skills ({global_count} global, {personal_count} personal)"


# Per-user SkillLoader cache
_skill_loaders: Dict[int, SkillLoader] = {}


def get_skill_loader(user_id: int = None) -> SkillLoader:
    """Get or create SkillLoader for a user.

    Returns a SkillLoader that searches:
      1. User's personal .skills directory (highest priority)
      2. Administrator-managed shared skills
      3. Bundled shared skills (lowest priority)

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
            # Administrator-managed shared skills
            Path(settings.MANAGED_SHARED_SKILLS_DIR),
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


def reload_all_skill_loaders() -> int:
    """Refresh every cached per-user loader after a managed Skill publish."""
    for loader in _skill_loaders.values():
        loader.reload()
    return len(_skill_loaders)
