"""Credential-free source identity for diagnostic builds and release evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BUILD_MANIFEST = Path(__file__).parents[1] / "_build_manifest.json"
SOURCE_DIRS = (
    "enterprise_agent", "frontend", "docker", "migrations", "benchmarks", "tests", "scripts",
)
EXCLUDED = {"node_modules", "__pycache__", ".pytest_cache", ".venv", "dist", "results"}
SUFFIXES = {".py", ".js", ".ts", ".vue", ".json", ".css", ".html", ".yml", ".yaml", ".md", ".sql", ".sh"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_manifest(root: Path) -> dict:
    files = {}
    for directory in SOURCE_DIRS:
        for path in sorted((root / directory).rglob("*")):
            relative = path.relative_to(root)
            if (any(part in EXCLUDED or part.startswith(".") for part in relative.parts)
                    or path.name == "_build_manifest.json"):
                continue
            if path.is_symlink():
                raise ValueError(f"Source manifest refuses symlinks: {relative}")
            if path.is_file() and (path.suffix in SUFFIXES or "Dockerfile" in path.name):
                files[relative.as_posix()] = sha256(path)
    for name in ("pyproject.toml", "uv.lock", "alembic.ini", ".dockerignore"):
        path = root / name
        if path.is_symlink():
            raise ValueError(f"Source manifest refuses symlinks: {name}")
        if path.is_file():
            files[name] = sha256(path)
    source_hash = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()

    try:
        revision, branch = git("rev-parse", "HEAD"), git("branch", "--show-current")
        dirty = bool(git("status", "--porcelain", "--untracked-files=normal"))
    except (OSError, subprocess.CalledProcessError):
        revision, branch, dirty = None, None, None
    return {
        "schema": 1, "qualification": "diagnostic-snapshot", "official": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": source_hash, "files": files,
        "git": {"commit": revision, "branch": branch, "dirty": dirty},
        "lock_sha256": {p: digest for p, digest in files.items() if p in {"uv.lock", "frontend/package-lock.json"}},
        "images": {}, "database_revision": None, "reports": {},
    }


def build_identity() -> dict:
    try:
        manifest = json.loads(BUILD_MANIFEST.read_text())
        return {key: manifest[key] for key in ("source_sha256", "qualification", "git")}
    except (OSError, ValueError, KeyError):
        return {"qualification": "unidentified", "source_sha256": None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", action="append", default=[], help="Local image name; records its immutable ID")
    parser.add_argument("--docker-context", default="desktop-linux")
    parser.add_argument("--database-revision", help="Observed alembic revision; omitted until verified")
    parser.add_argument("--report", action="append", type=Path, default=[])
    args = parser.parse_args()
    manifest = source_manifest(args.root.resolve())
    for image in args.image:
        info = json.loads(subprocess.check_output([
            "docker", "--context", args.docker_context, "image", "inspect", image,
            "--format", "{{json .}}",
        ], text=True))
        manifest["images"][image] = {"id": info["Id"], "repo_digests": info.get("RepoDigests", [])}
    manifest["database_revision"] = args.database_revision
    for report in args.report:
        manifest["reports"][report.name] = {"sha256": sha256(report), "binding": "attached-evidence-not-current-score"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"source_sha256": manifest["source_sha256"], "qualification": manifest["qualification"]}))


if __name__ == "__main__":
    main()
