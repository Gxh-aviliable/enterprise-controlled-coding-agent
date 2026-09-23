"""One bounded, safe package format shared by import, publication and runtime.

Packages are JSON maps of relative POSIX paths to base64 bytes. Only SKILL.md
is normalized (UTF-8, LF, final newline) before hashing; assets remain exact.
"""

import base64
import hashlib
import io
import json
import os
import re
import stat
import sys
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

import yaml

MAX_FILES = 256
MAX_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_REPOSITORY_BYTES = 32 * 1024 * 1024
MAX_REPOSITORY_FILES = 2048
MAX_MARKDOWN_BYTES = 100_000
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
SECRET_RE = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"
)


class SkillError(ValueError):
    """Safe, actionable validation failure suitable for returning to clients."""


class StrictLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise SkillError("YAML aliases are not supported")
        self._skill_nodes = getattr(self, "_skill_nodes", 0) + 1
        self._skill_depth = getattr(self, "_skill_depth", 0) + 1
        if self._skill_nodes > 2000 or self._skill_depth > 32:
            raise SkillError("YAML metadata exceeds node/depth limits")
        try:
            return super().compose_node(parent, index)
        finally:
            self._skill_depth -= 1


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str) or key in result:
            raise SkillError("YAML keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def parse_markdown(content: str, *, fallback_name: str | None = None):
    if not isinstance(content, str) or "\x00" in content:
        raise SkillError("SKILL.md must be UTF-8 text without NUL")
    content = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"
    if len(content.encode()) > MAX_MARKDOWN_BYTES:
        raise SkillError("SKILL.md exceeds 100 KB")
    match = re.match(r"\A---\n(.*?)\n---(?:\n|$)(.*)\Z", content, re.S)
    warnings = []
    if not match:
        if content.startswith("---") or not fallback_name:
            raise SkillError("SKILL.md must start with valid YAML frontmatter")
        metadata = {"name": fallback_name, "description": "Legacy Skill; add YAML frontmatter"}
        body = content.strip()
        warnings.append("Legacy Markdown without frontmatter; add name and description before importing")
    else:
        try:
            metadata = yaml.load(match[1], Loader=StrictLoader)
        except (yaml.YAMLError, RecursionError) as exc:
            mark = getattr(exc, "problem_mark", None)
            location = f" at line {mark.line + 2}" if mark else ""
            raise SkillError(f"Invalid YAML frontmatter{location}") from exc
        if not isinstance(metadata, dict):
            raise SkillError("YAML frontmatter must be a mapping")
        body = match[2].strip()
    if fallback_name and (not metadata.get("name") or not metadata.get("description")):
        metadata.setdefault("name", fallback_name)
        metadata.setdefault("description", "Legacy Skill; add a description before publishing")
        warnings.append("Legacy frontmatter repaired in memory; add required name/description to the source")
    if fallback_name and (not match or warnings):
        content = "---\n" + yaml.safe_dump(metadata, allow_unicode=True) + "---\n\n" + body + "\n"
    if not isinstance(metadata.get("name"), str) or not NAME_RE.fullmatch(metadata["name"]):
        raise SkillError("Skill name must be a lowercase slug (1–80 characters)")
    if not isinstance(metadata.get("description"), str) or not metadata["description"].strip():
        raise SkillError("Frontmatter description is required and must be text")
    if not body:
        raise SkillError("Skill guidance body is empty")
    if "metadata" in metadata and not isinstance(metadata["metadata"], dict):
        raise SkillError("Frontmatter metadata must be a mapping")
    if SECRET_RE.search(content):
        raise SkillError("Potential credential or private key detected")
    # safe_load also supports dates and sets; expose only JSON metadata.
    try:
        json.dumps(metadata, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise SkillError("Metadata must contain JSON-compatible scalar, list or mapping values") from exc
    return content, metadata, body, warnings


def safe_path(name: str) -> str:
    if not isinstance(name, str) or len(name) > 240 or "\\" in name or ":" in name:
        raise SkillError("Invalid package path")
    from enterprise_agent.core.agent.tools.workspace import is_operational_agent_path, is_sensitive_agent_path

    if is_sensitive_agent_path(name) or is_operational_agent_path(name):
        raise SkillError("Credential and operational paths are not accepted in Skill packages")
    path = PurePosixPath(name)
    if not name or path.is_absolute() or any(p in ("", ".", "..") for p in name.split("/")):
        raise SkillError("Package path must be relative and cannot contain traversal")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise SkillError("Package path contains controls")
    if any(p in {".git", ".ssh", ".env", ".managed.json"} for p in path.parts):
        raise SkillError("Credential and platform paths are not accepted in Skill packages")
    return name


def encode_package(files: dict[str, bytes], *, legacy_name: str | None = None):
    if not files or len(files) > MAX_FILES or sum(len(b) for b in files.values()) > MAX_BYTES:
        raise SkillError("Skill package exceeds 256 files / 4 MiB or is empty")
    result = {}
    seen = set()
    normalized_paths = {unicodedata.normalize("NFC", name).casefold() for name in files}
    for name, data in sorted(files.items()):
        safe_path(name)
        canonical = unicodedata.normalize("NFC", name).casefold()
        if canonical in seen:
            raise SkillError("Duplicate case-insensitive package path")
        if any(
            unicodedata.normalize("NFC", str(parent)).casefold() in normalized_paths
            for parent in PurePosixPath(name).parents
            if str(parent) != "."
        ):
            raise SkillError("Package file conflicts with a parent directory")
        if name != "SKILL.md" and PurePosixPath(name).name == "SKILL.md":
            raise SkillError("Nested Skills must be selected as separate candidates")
        seen.add(canonical)
        if name == "SKILL.md":
            try:
                normalized, _, _, _ = parse_markdown(data.decode("utf-8"), fallback_name=legacy_name)
            except UnicodeDecodeError as exc:
                raise SkillError("SKILL.md is not valid UTF-8") from exc
            data = normalized.encode()
        result[name] = base64.b64encode(data).decode("ascii")
    if sum(len(base64.b64decode(data)) for data in result.values()) > MAX_BYTES:
        raise SkillError("Normalized Skill package exceeds 4 MiB")
    if "SKILL.md" not in result:
        raise SkillError("Package has no SKILL.md")
    return result


def decode_package(package: dict) -> dict[str, bytes]:
    if not isinstance(package, dict) or len(package) > MAX_FILES:
        raise SkillError("Invalid package map or too many files")
    result = {}
    total = 0
    for name, value in package.items():
        safe_path(name)
        if not isinstance(value, str) or len(value) > MAX_BYTES * 2:
            raise SkillError("Invalid base64 package file")
        try:
            data = base64.b64decode(value, validate=True)
        except ValueError as exc:
            raise SkillError("Invalid base64 package file") from exc
        total += len(data)
        if total > MAX_BYTES:
            raise SkillError("Skill package exceeds 4 MiB")
        result[name] = data
    return result


def validate_package(package: dict, expected_name=None, *, legacy_name=None):
    files = decode_package(package)
    normalized = encode_package(files, legacy_name=legacy_name)
    content = base64.b64decode(normalized["SKILL.md"]).decode()
    _, metadata, body, warnings = parse_markdown(content, fallback_name=legacy_name)
    if expected_name is not None and metadata["name"] != expected_name:
        raise SkillError("Frontmatter name must match the registry name")
    digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "package": normalized,
        "metadata": metadata,
        "body": body,
        "content": content,
        "sha256": digest,
        "warnings": warnings,
        "files": [{"path": name, "bytes": len(base64.b64decode(data))} for name, data in normalized.items()],
    }


def _open_directory(path: Path):
    """Anchor workspace reads before traversing user-controlled components (openat/no-follow)."""
    from enterprise_agent.core.agent.tools.workspace import get_workspace_base

    path = Path(os.path.abspath(path))
    if sys.platform == "darwin":
        # macOS owns these aliases; only these trusted ancestors may resolve links.
        for alias in (Path("/var"), Path("/tmp"), Path("/etc")):
            if path.is_relative_to(alias):
                path = alias.resolve() / path.relative_to(alias)
                break
    configured = Path(os.path.abspath(get_workspace_base()))
    anchor = Path(path.anchor)
    relative = path.relative_to(anchor)
    for base in (configured, configured.resolve()):
        if path.is_relative_to(base):
            anchor = configured.resolve()
            relative = path.relative_to(base)
            break
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(anchor, flags)
    try:
        for part in relative.parts:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def read_directory(root: Path):
    if os.open not in os.supports_dir_fd or not hasattr(os, "fwalk"):
        raise SkillError("Secure directory imports require macOS/Linux; use ZIP on this host")
    files = {}
    total = 0
    entries = 0
    try:
        descriptor = _open_directory(root)
        try:
            for current, dirs, names, directory_fd in os.fwalk(".", follow_symlinks=False, dir_fd=descriptor):
                entries += len(dirs) + len(names)
                if entries > MAX_REPOSITORY_FILES or len(Path(current).parts) > 16:
                    raise SkillError("Skill directory exceeds entry/depth limits")
                for name in dirs + names:
                    mode = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if stat.S_ISLNK(mode.st_mode):
                        raise SkillError("Skill packages cannot contain symbolic links")
                for name in names:
                    relative = (Path(current) / name).as_posix()
                    if relative == ".managed.json":
                        continue  # legacy service manifest is never package content
                    safe_path(relative)
                    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                    fd = os.open(name, flags, dir_fd=directory_fd)
                    with os.fdopen(fd, "rb") as stream:
                        mode = os.fstat(stream.fileno())
                        if not stat.S_ISREG(mode.st_mode) or mode.st_nlink != 1:
                            raise SkillError("Skill packages cannot contain special files or hard links")
                        data = stream.read(MAX_BYTES - total + 1)
                    total += len(data)
                    if total > MAX_BYTES or len(files) >= MAX_FILES:
                        raise SkillError("Skill package exceeds 256 files / 4 MiB")
                    files[relative] = data
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise SkillError("Skill directory is unreadable or contains a symbolic link") from exc
    return encode_package(files, legacy_name=root.name)


def zip_candidates(data: bytes):
    if len(data) > MAX_ARCHIVE_BYTES:
        raise SkillError("ZIP upload exceeds 16 MiB")
    files = {}
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > MAX_REPOSITORY_FILES:
                raise SkillError("ZIP contains too many entries")
            for info in archive.infolist():
                name = info.filename.rstrip("/")
                safe_path(name)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                    raise SkillError("ZIP links and special files are forbidden")
                total += info.file_size
                if total > MAX_REPOSITORY_BYTES or info.flag_bits & 1:
                    raise SkillError("ZIP exceeds 32 MiB expanded size or is encrypted")
                if info.is_dir():
                    continue
                if name in files:
                    raise SkillError("ZIP contains duplicate paths")
                files[name] = archive.read(info)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise SkillError("Invalid or unsupported ZIP archive") from exc
    candidates = []
    for path in sorted(files):
        if PurePosixPath(path).name != "SKILL.md":
            continue
        prefix = str(PurePosixPath(path).parent)
        prefix = "" if prefix == "." else prefix + "/"
        subset = {p[len(prefix) :]: b for p, b in files.items() if p.startswith(prefix)}
        try:
            package = encode_package(subset)
            result = validate_package(package)
            candidates.append({"path": prefix.rstrip("/") or ".", "valid": True, **result})
        except SkillError as exc:
            candidates.append({"path": prefix.rstrip("/") or ".", "valid": False, "error": str(exc)})
    if not candidates:
        raise SkillError("No SKILL.md found in ZIP")
    if len(candidates) > 64:
        raise SkillError("Repository contains more than 64 Skill candidates")
    return candidates
