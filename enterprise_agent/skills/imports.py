"""Offline directory discovery and constrained HTTPS Git imports; never executes package code."""

import ipaddress
import os
import re
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tools.workspace import (
    get_workspace_base,
    is_operational_agent_path,
    is_sensitive_agent_path,
)
from enterprise_agent.skills.packages import (
    MAX_ARCHIVE_BYTES,
    MAX_REPOSITORY_BYTES,
    MAX_REPOSITORY_FILES,
    SkillError,
    read_directory,
    safe_path,
    validate_package,
    zip_candidates,
)


def authorized_directory(user_id: int, relative: str = "") -> Path:
    if not isinstance(user_id, int) or user_id <= 0:
        raise SkillError("Authenticated user is required")
    root = get_workspace_base().resolve() / f"user_{user_id}"
    if root.is_symlink():
        raise SkillError("Workspace cannot be a symbolic link")
    root.mkdir(parents=True, exist_ok=True)
    if relative in ("", "."):
        return root
    safe_path(relative)
    if is_sensitive_agent_path(relative) or is_operational_agent_path(relative):
        raise SkillError("Directory is not available for Skill access")
    path = root
    for part in Path(relative).parts:
        path /= part
        if path.is_symlink():
            raise SkillError("Workspace Skill path cannot contain symbolic links")
    if not path.is_dir() or not path.resolve().is_relative_to(root):
        raise SkillError("Project/workspace directory does not exist or is outside your workspace")
    return path


def directory_candidates(root):
    result = []
    count = 0
    total = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        parent = Path(current)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in {".git", "node_modules", ".venv"}
            and not is_sensitive_agent_path(str((parent / d).relative_to(root)))
            and not is_operational_agent_path(str((parent / d).relative_to(root)))
        )
        for name in dirs + files:
            path = parent / name
            if path.is_symlink():
                raise SkillError("Import directory contains symbolic links")
            count += 1
            total += path.stat().st_size if path.is_file() else 0
            if count > MAX_REPOSITORY_FILES or total > MAX_REPOSITORY_BYTES:
                raise SkillError("Import directory exceeds 2048 entries / 32 MiB")
        if "SKILL.md" in files:
            relative = parent.relative_to(root).as_posix()
            try:
                evidence = validate_package(read_directory(parent))
                result.append({"path": relative, "valid": True, **evidence})
            except SkillError as exc:
                result.append({"path": relative, "valid": False, "error": str(exc)})
            dirs[:] = []
        if len(result) > 64:
            raise SkillError("Repository contains more than 64 candidates")
    if not result:
        raise SkillError("No SKILL.md found in the selected directory")
    return result


def git_endpoint(url):
    try:
        parsed = urlsplit(url)
        port = parsed.port or 443
    except ValueError as exc:
        raise SkillError("Invalid Git URL") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or any(ord(c) < 33 for c in url)
        or "\\" in url
        or not re.fullmatch(r"/[A-Za-z0-9_./~-]+", parsed.path)
    ):
        raise SkillError("Git URL must be HTTPS without credentials, query parameters or fragments")
    host = parsed.hostname.lower()
    endpoint = f"{host}:{port}"
    public = {h.strip().lower() for h in settings.SKILL_GIT_HOSTS.split(",") if h.strip()}
    internal = {h.strip().lower() for h in settings.SKILL_GIT_INTERNAL_HOSTS.split(",") if h.strip()}
    if endpoint not in internal and not (host in public and port == 443):
        raise SkillError("Git host is not allowlisted; ask an administrator or use ZIP/workspace import")
    try:
        addresses = sorted({row[4][0] for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except OSError as exc:
        raise SkillError("Git DNS lookup failed; check intranet DNS or use ZIP/workspace import") from exc
    if not addresses or (endpoint not in internal and any(not ipaddress.ip_address(a).is_global for a in addresses)):
        raise SkillError("Git address is private/reserved and not explicitly trusted")
    pinned = ",".join(f"[{a}]" if ":" in a else a for a in addresses)
    return f"{host}:{port}:{pinned}"


def git_failure(stderr: str):
    """Return an actionable category without echoing server text, URLs or credentials."""
    lowered = stderr.lower()
    categories = (
        (("certificate", "ssl peer"), "Git TLS verification failed; install the trusted CA or import an offline ZIP"),
        (("could not resolve",), "Git DNS lookup failed; check DNS or use ZIP/workspace import"),
        (("couldn't find remote ref", "not a valid object"), "Git ref/subdirectory was not found; verify it and retry"),
        (("redirect",), "Git redirects are blocked; use the canonical allowlisted HTTPS repository URL"),
        (
            ("authentication", "could not read username", "repository not found"),
            "Git repository unavailable or requires credentials; use an accessible URL or an offline ZIP",
        ),
        (
            ("connect", "timed out", "rpc failed", "network", "http/2"),
            "Git HTTPS transport failed; check network access and retry, or use ZIP/workspace import",
        ),
    )
    for needles, message in categories:
        if any(needle in lowered for needle in needles):
            return message
    return "Git import failed; check URL/ref and HTTPS access, or use ZIP/workspace import"


def git_candidates(url, ref="HEAD", subdirectory=""):
    pinned = git_endpoint(url)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,199}", ref) or ".." in ref:
        raise SkillError("Invalid Git ref; specify a branch, tag or commit")
    if subdirectory:
        safe_path(subdirectory)
    with tempfile.TemporaryDirectory(prefix="skill-import-") as temp:
        root = Path(temp)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "https",
            "GIT_LFS_SKIP_SMUDGE": "1",
        }
        args = [
            "git",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "credential.helper=",
            "-c",
            "http.followRedirects=false",
            "-c",
            "http.proxy=",
            "-c",
            "http.sslVerify=true",
            "-c",
            "http.curloptResolve=" + pinned,
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.https.allow=always",
            "-c",
            "http.lowSpeedLimit=1000",
            "-c",
            "http.lowSpeedTime=15",
            "-c",
            "fetch.unpackLimit=1",
            "-c",
            "transfer.unpackLimit=1",
        ]
        if settings.SKILL_GIT_CA_BUNDLE:
            args.extend(["-c", "http.sslCAInfo=" + settings.SKILL_GIT_CA_BUNDLE])
        deadline = time.monotonic() + settings.SKILL_GIT_TIMEOUT_SECONDS

        def run(command, output_limit=MAX_ARCHIVE_BYTES):
            output = root / "output"
            with output.open("wb") as stdout, (root / "stderr").open("w+b") as stderr:
                try:
                    process = subprocess.Popen(
                        args + command,
                        cwd=root,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout,
                        stderr=stderr,
                        start_new_session=os.name != "nt",
                    )
                except OSError as exc:
                    raise SkillError("Git executable unavailable; install Git or use ZIP/workspace import") from exc
                try:
                    while process.poll() is None:
                        size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
                        if (
                            size > MAX_REPOSITORY_BYTES
                            or output.stat().st_size > output_limit
                            or os.fstat(stderr.fileno()).st_size > 65_536
                        ):
                            raise SkillError("Git download exceeds 32 MiB; import a smaller offline ZIP")
                        if time.monotonic() > deadline:
                            raise SkillError("Git import timed out; check connectivity or use ZIP/workspace import")
                        time.sleep(0.05)
                    if process.returncode:
                        stderr.seek(0)
                        raise SkillError(git_failure(stderr.read(32_768).decode("utf-8", errors="replace")))
                finally:
                    if process.poll() is None:
                        if os.name == "nt":
                            process.kill()
                        else:
                            os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
            if sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) > MAX_REPOSITORY_BYTES:
                raise SkillError("Git repository exceeds 32 MiB; import a smaller offline ZIP")
            if output.stat().st_size > output_limit:
                raise SkillError("Git output exceeds the import limit")
            return output.read_bytes()

        capabilities = run(["help", "--config"], 100_000)
        if b"http.curloptResolve" not in capabilities:
            raise SkillError("Git lacks pinned DNS support; upgrade Git or use ZIP/workspace import")
        run(["init", "--bare", "."])
        run(["fetch", "--depth=1", "--no-tags", "--no-recurse-submodules", url, ref])
        commit = run(["rev-parse", "--verify", "FETCH_HEAD^{commit}"], 200).decode().strip()
        tree = commit + (":" + subdirectory if subdirectory else "")
        data = run(["archive", "--format=zip", tree])
        return zip_candidates(data), {
            "kind": "git",
            "url": url,
            "ref": ref,
            "commit": commit,
            "subdirectory": subdirectory,
        }
