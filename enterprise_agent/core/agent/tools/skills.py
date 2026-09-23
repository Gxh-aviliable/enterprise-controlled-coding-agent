"""Bounded Skill catalog and tools.

HTTP requests merge administrator publications and user-owned packages through
skills.catalog. Each task pins content hashes; direct legacy callers discover
on-disk Skills afresh. Unique names remain aliases; collisions require IDs.
"""

import hashlib
import html
import json
import logging
import os
import stat
from collections import deque
from pathlib import Path
from typing import Dict

from langchain_core.tools import tool

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

    Retains each source under a stable identity, including duplicate names.
    """

    def __init__(self, search_dirs):
        # Accept single Path or list of Paths
        if isinstance(search_dirs, (str, Path)):
            search_dirs = [Path(search_dirs)]
        self.search_dirs = [Path(d) for d in search_dirs]
        self.skills: Dict[str, Dict] = {}
        self.errors = []
        self._load_all()

    def _load_all(self) -> None:
        """Discover every configured source; preserve collisions for exact selection."""
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
        from enterprise_agent.skills.packages import read_directory, validate_package

        try:
            evidence = validate_package(read_directory(skill_file.parent), legacy_name=skill_file.parent.name)
            text, meta, body = evidence["content"], evidence["metadata"], evidence["body"]
            if len(text.encode()) > MAX_SKILL_FILE_BYTES:
                raise ValueError("Skill file exceeds the configured byte limit")
            warnings = evidence["warnings"]
            source = "personal"
            version = None
            relative = skill_file.relative_to(search_dir).as_posix()
            identity = (
                source + ":" + hashlib.sha256((str(search_dir.absolute()) + "/" + relative).encode()).hexdigest()[:24]
            )
            self.add(
                {
                    "id": identity,
                    "name": meta["name"],
                    "meta": meta,
                    "body": body,
                    "path": str(skill_file),
                    "scope": "personal",
                    "source": source,
                    "version": version,
                    "sha256": evidence["sha256"],
                    "package": evidence["package"],
                    "warnings": warnings,
                    "enabled": True,
                    "implicit_allowed": True,
                }
            )
        except Exception as exc:
            self.errors.append({"path": str(skill_file.relative_to(search_dir)), "error": str(exc)})
            logger.warning("Failed to load Skill %s: %s", skill_file, exc)

    def add(self, skill):
        name = skill["name"]
        # Preserve historical dictionary access for unique names, never overwrite a collision.
        if name in self.skills:
            previous = self.skills.pop(name)
            self.skills[previous["id"]] = previous
        duplicate = any(item.get("name") == name for item in self.skills.values())
        self.skills[skill["id"] if duplicate else name] = skill

    def resolve(self, selector):
        matches = [s for s in self.skills.values() if s.get("id") == selector]
        if not matches:
            matches = [s for key, s in self.skills.items() if s.get("name", key) == selector]
        if not matches:
            raise ValueError("Unknown or inaccessible Skill: " + selector)
        if len(matches) != 1:
            raise ValueError("Ambiguous Skill name; choose a stable ID: " + ", ".join(s["id"] for s in matches))
        if not matches[0].get("enabled", True):
            raise ValueError("Skill is disabled: " + selector)
        return matches[0]

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
        ordered_names = sorted(
            (
                key
                for key, value in self.skills.items()
                if value.get("enabled", True) and value.get("implicit_allowed", True)
            ),
            key=lambda value: (value.casefold(), value),
        )
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
            display_name = skill.get("name", name) if len(name) <= 128 else name
            bounded_name = str(display_name)[:PROMPT_SKILL_NAME_CHARS]
            version = skill.get("version")
            bounded_version = None if version is None else str(version)[:PROMPT_SKILL_METADATA_CHARS]
            sha256 = str(skill.get("sha256") or "")
            bounded_sha256 = sha256[:PROMPT_SKILL_METADATA_CHARS]
            entries.append(
                {
                    "name": bounded_name,
                    "id": str(skill.get("id", name))[:128],
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
        """Load a Skill by stable ID or an unambiguous legacy alias.

        Args:
            name: Skill name to load

        Returns:
            Skill content wrapped in <skill> tags
        """
        try:
            skill = self.resolve(name)
        except ValueError as exc:
            return "Error: " + str(exc)
        if "body" not in skill:
            from enterprise_agent.skills.packages import validate_package
            from enterprise_agent.skills.runtime import read_cached_package

            try:
                evidence = validate_package(read_cached_package(skill["sha256"]))
                skill = {**skill, "body": evidence["body"]}
            except ValueError as exc:
                return "Error: " + str(exc)
        name = skill.get("name", name)
        scope_note = ""
        if skill["scope"] == "personal":
            scope_note = " (your personal version)"
        version_attr = ""
        escaped_id = html.escape(str(skill.get("id", name)), quote=True)
        escaped_name = html.escape(name, quote=True)
        escaped_scope = html.escape(str(skill["scope"]), quote=True)
        escaped_source = html.escape(str(skill["source"]), quote=True)
        escaped_sha256 = html.escape(str(skill["sha256"]), quote=True)
        if skill.get("version") is not None:
            version_attr = f' version="{html.escape(str(skill["version"]), quote=True)}"'
        return (
            f'<skill id="{escaped_id}" name="{escaped_name}" scope="{escaped_scope}" source="{escaped_source}"'
            f'{version_attr} sha256="{escaped_sha256}">\n'
            f"<!-- {skill['scope']} skill{scope_note} -->\n"
            f"Resources: /workspace/.skill-resources/{skill['sha256']}/ (sandbox); "
            f"read_skill_resource with this Skill ID for text resources.\n"
            f"{skill['body']}\n"
            f"</skill>"
        )

    def reload(self) -> str:
        """Reload all skills from directories."""
        self.skills.clear()
        self.errors.clear()
        self._load_all()
        global_count = sum(1 for s in self.skills.values() if s["scope"] == "global")
        personal_count = sum(1 for s in self.skills.values() if s["scope"] == "personal")
        return f"Reloaded {len(self.skills)} skills ({global_count} global, {personal_count} personal)"


# Legacy cache maintenance hook; new requests do not reuse process-local loaders.
_skill_loaders: Dict[int, SkillLoader] = {}


def get_skill_loader(user_id: int = None) -> SkillLoader:
    """Use the task's database snapshot; standalone calls only discover the owner's directory."""
    from enterprise_agent.core.agent.tools.workspace import get_current_user_id, get_workspace_base

    if user_id is None:
        user_id = get_current_user_id()

    from enterprise_agent.skills.runtime import current_loader

    pinned = current_loader(user_id)
    if pinned is not None:
        return pinned
    # No process-local cache: a new direct caller sees current on-disk legacy Skills.
    # HTTP/Agent calls use the database catalog pinned to their task instead.
    search_dirs = []
    if user_id is not None:
        search_dirs.insert(0, get_workspace_base() / f"user_{user_id}" / ".skills")
    return SkillLoader(search_dirs)


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

    Use a stable ID when names collide; ambiguous aliases fail explicitly.

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
    from enterprise_agent.skills.runtime import _snapshot

    if _snapshot.get():
        return "This task keeps its pinned Skill versions; start a new request to refresh."
    return get_skill_loader().reload()


def reload_all_skill_loaders() -> int:
    """Refresh every cached per-user loader after a managed Skill publish."""
    for loader in _skill_loaders.values():
        loader.reload()
    return len(_skill_loaders)


@tool
def read_skill_resource(skill_id: str, path: str, offset: int = 0, limit: int = 16000) -> str:
    """Read bounded UTF-8 references/scripts from a visible pinned Skill by stable ID.

    Does not execute code. Use bash under existing approval and sandbox policy to run scripts.
    """
    from enterprise_agent.skills.packages import decode_package, safe_path
    from enterprise_agent.skills.runtime import read_cached_package

    try:
        safe_path(path)
        skill = get_skill_loader().resolve(skill_id)
        package = skill.get("package") or read_cached_package(skill["sha256"])
        files = decode_package(package)
        if path not in files:
            return "Error: Skill resource not found"
        if offset < 0 or not 1 <= limit <= 32000:
            return "Error: Invalid resource read range"
        text = files[path].decode("utf-8")
        return json.dumps(
            {
                "id": skill["id"],
                "source": skill["source"],
                "version": skill["version"],
                "sha256": skill["sha256"],
                "path": path,
                "offset": offset,
                "total_chars": len(text),
                "content": text[offset : offset + limit],
            },
            ensure_ascii=False,
        )
    except (ValueError, UnicodeDecodeError) as exc:
        return "Error: " + str(exc)
