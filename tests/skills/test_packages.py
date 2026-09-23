import io
import stat
import zipfile

import pytest

from enterprise_agent.skills.imports import authorized_directory, git_endpoint
from enterprise_agent.skills.packages import (
    SkillError,
    encode_package,
    parse_markdown,
    read_directory,
    validate_package,
    zip_candidates,
)

CONTENT = """---
name: sample
description: |
  中文 first line
  second line
metadata:
  nested:
    flag: true
---

Follow the project instructions.
"""


def package(body=CONTENT):
    return encode_package(
        {
            "SKILL.md": body.encode(),
            "references/guide.txt": b"reference-v1",
            "scripts/example.py": b"print('skill-ok')\n",
            "assets/raw.bin": b"\x00\xff",
        }
    )


def archive(files):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as zip_file:
        for name, content in files.items():
            zip_file.writestr(name, content)
    return result.getvalue()


def test_yaml_and_normalization_and_resources_hash():
    evidence = validate_package(package())
    assert evidence["metadata"]["description"] == "中文 first line\nsecond line\n"
    assert evidence["metadata"]["metadata"]["nested"]["flag"] is True
    assert validate_package(package(CONTENT.replace("\n", "\r\n")))["sha256"] == evidence["sha256"]
    altered = package()
    altered["references/guide.txt"] = "Y2hhbmdlZA=="
    assert validate_package(altered)["sha256"] != evidence["sha256"]


@pytest.mark.parametrize(
    "yaml",
    [
        "name: sample\nname: duplicate",
        "name: [bad]",
        "name: &x sample\ndescription: *x",
        "name: sample\ndescription: !!python/object:os.system {}",
        "name: sample\ndescription: [bad]",
    ],
)
def test_reject_yaml(yaml):
    with pytest.raises(SkillError):
        parse_markdown("---\n" + yaml + "\n---\ntext")


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "/absolute",
        "C:/escape",
        "a/../escape",
        "a\\escape",
        ".git/config",
        "references/.env.local",
        ".aws/credentials",
        ".agent_internal/state",
    ],
)
def test_reject_zip_paths(path):
    with pytest.raises(SkillError):
        zip_candidates(archive({path: b"bad", "SKILL.md": CONTENT.encode()}))


def test_zip_multi_candidates_and_invalid_candidate():
    candidates = zip_candidates(archive({"a/SKILL.md": CONTENT, "a/assets/file": b"data", "b/SKILL.md": "broken"}))
    assert len(candidates) == 2
    assert candidates[0]["valid"] and not candidates[1]["valid"]
    assert set(candidates[0]["package"]) == {"SKILL.md", "assets/file"}


def test_zip_symlink_and_limits():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        link = zipfile.ZipInfo("scripts/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(link, "../../outside")
    with pytest.raises(SkillError, match="links"):
        zip_candidates(output.getvalue())
    with pytest.raises(SkillError, match="256"):
        encode_package({f"file{i}": b"x" for i in range(257)})
    with pytest.raises(SkillError, match="4 MiB"):
        encode_package({"SKILL.md": CONTENT.encode(), "assets/huge": b"x" * (4 * 1024 * 1024)})


def test_directory_escape_and_legacy(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    root = authorized_directory(1)
    (root / "demo").mkdir()
    (root / "link").symlink_to(tmp_path, target_is_directory=True)
    for path in ("../user_2", "link", "/tmp"):
        with pytest.raises(SkillError):
            authorized_directory(1, path)
    (root / "demo" / "SKILL.md").write_text("legacy instructions")
    assert validate_package(read_directory(root / "demo"))["metadata"]["name"] == "demo"
    (root / "demo" / "ref").symlink_to(tmp_path / "secret")
    with pytest.raises(SkillError):
        read_directory(root / "demo")


def test_git_address_policy(monkeypatch):
    from enterprise_agent.config.settings import settings

    monkeypatch.setattr(settings, "SKILL_GIT_HOSTS", "git.example")
    monkeypatch.setattr(settings, "SKILL_GIT_INTERNAL_HOSTS", "")
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))])
    for url in (
        "file:///tmp/repo",
        "ssh://git.example/repo",
        "https://token@git.example/repo",
        "https://git.example/repo?token=secret",
        "https://git.example/repo",
        "https://evil.example/repo",
    ):
        with pytest.raises(SkillError):
            git_endpoint(url)
    monkeypatch.setattr(settings, "SKILL_GIT_INTERNAL_HOSTS", "git.example:443")
    assert git_endpoint("https://git.example/repo") == "git.example:443:127.0.0.1"


def test_reject_file_directory_and_unicode_collisions():
    for files in (
        {"SKILL.md": CONTENT.encode(), "Scripts/tool.py": b"code", "scripts": b"file"},
        {"SKILL.md": CONTENT.encode(), "assets/é": b"one", "assets/e\u0301": b"two"},
        {"SKILL.md": CONTENT.encode(), "nested/SKILL.md": CONTENT.encode()},
    ):
        with pytest.raises(SkillError):
            encode_package(files)


def test_directory_parent_symlink_cannot_cross_users(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    user_a, user_b = authorized_directory(1), authorized_directory(2)
    (user_b / "sample").mkdir()
    (user_b / "sample" / "SKILL.md").write_text(CONTENT)
    (user_a / "linked").symlink_to(user_b, target_is_directory=True)
    with pytest.raises(SkillError):
        read_directory(user_a / "linked" / "sample")
