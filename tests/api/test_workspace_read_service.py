"""Shared workspace metadata/read service security tests."""

import hashlib

import pytest

from enterprise_agent.api.services.workspace_read import get_workspace_tree, read_workspace_text


def test_admin_read_service_blocks_sensitive_content(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    workspace = tmp_path / "user_9"
    workspace.mkdir()
    (workspace / ".env").write_text("SECRET=value", encoding="utf-8")

    with pytest.raises(PermissionError):
        read_workspace_text(9, ".env")


def test_workspace_tree_marks_sensitive_files_without_exposing_content(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    workspace = tmp_path / "user_10"
    workspace.mkdir()
    (workspace / ".env").write_text("SECRET=value", encoding="utf-8")
    (workspace / "app.py").write_text("print('ok')", encoding="utf-8")

    tree = get_workspace_tree(10, "", 2)
    entries = {item["path"]: item for item in tree["children"]}
    assert entries[".env"]["sensitive"] is True
    assert entries["app.py"]["sensitive"] is False
    assert "content" not in entries[".env"]


def test_workspace_read_returns_full_file_sha_for_each_page(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    workspace = tmp_path / "user_11"
    workspace.mkdir()
    content = b"first\nsecond\nthird\n"
    (workspace / "notes.txt").write_bytes(content)

    first_page = read_workspace_text(11, "notes.txt", offset=0, limit=1)
    second_page = read_workspace_text(11, "notes.txt", offset=1, limit=1)

    expected_sha = hashlib.sha256(content).hexdigest()
    assert first_page["content"] == "first\n"
    assert second_page["content"] == "second\n"
    assert first_page["sha256"] == expected_sha
    assert second_page["sha256"] == expected_sha


def test_workspace_read_marks_nul_containing_file_as_binary(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    workspace = tmp_path / "user_12"
    workspace.mkdir()
    content = b"header\x00payload"
    (workspace / "payload.bin").write_bytes(content)

    result = read_workspace_text(12, "payload.bin")

    assert result["binary"] is True
    assert result["content"] == ""
    assert result["sha256"] == hashlib.sha256(content).hexdigest()
