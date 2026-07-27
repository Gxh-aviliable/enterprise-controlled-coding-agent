"""Shared workspace metadata/read service security tests."""

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
