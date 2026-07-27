"""Tests for workspace open-in-VSCode URL generation."""

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_open_url_returns_local_vscode_url(monkeypatch, tmp_path):
    from enterprise_agent.api.routes import workspace

    file_path = tmp_path / "hello world.py"
    file_path.write_text("print('hi')", encoding="utf-8")

    monkeypatch.setattr(workspace.settings, "FILE_OPEN_MODE", "local-vscode")
    monkeypatch.setattr(workspace, "resolve_path", lambda path, user_id: file_path)
    monkeypatch.setattr(workspace, "get_user_workspace", lambda user_id: tmp_path)

    result = await workspace.get_open_url(path="hello world.py", user_id=1)

    assert result["mode"] == "local-vscode"
    assert result["url"].startswith("vscode://file/")
    assert result["url"].endswith(_quoted_tmp_path(tmp_path))
    assert result["file_url"].endswith("hello%20world.py")


@pytest.mark.asyncio
async def test_open_url_uses_web_template(monkeypatch, tmp_path):
    from enterprise_agent.api.routes import workspace

    file_path = tmp_path / "src" / "main.py"
    file_path.parent.mkdir()
    file_path.write_text("print('hi')", encoding="utf-8")

    monkeypatch.setattr(workspace.settings, "FILE_OPEN_MODE", "web-vscode")
    monkeypatch.setattr(workspace.settings, "VSCODE_WEB_URL_TEMPLATE", "https://code.example.dev/open?folder={workspace}&file={path}")
    monkeypatch.setattr(workspace.settings, "VSCODE_WEB_BASE_URL", "")
    monkeypatch.setattr(workspace.settings, "VSCODE_WORKSPACE_PATH", "/srv/workspaces")
    monkeypatch.setattr(workspace, "resolve_path", lambda path, user_id: file_path)
    monkeypatch.setattr(workspace, "get_user_workspace", lambda user_id: tmp_path)

    result = await workspace.get_open_url(path="src/main.py", user_id=1)

    assert result == {
        "mode": "web-vscode",
        "url": "https://code.example.dev/open?folder=/srv/workspaces/user_1&file=/srv/workspaces/user_1/src/main.py",
    }


@pytest.mark.asyncio
async def test_open_url_builds_default_web_url(monkeypatch, tmp_path):
    from enterprise_agent.api.routes import workspace

    file_path = tmp_path / "app.py"
    file_path.write_text("print('hi')", encoding="utf-8")

    monkeypatch.setattr(workspace.settings, "FILE_OPEN_MODE", "web-vscode")
    monkeypatch.setattr(workspace.settings, "VSCODE_WEB_URL_TEMPLATE", "")
    monkeypatch.setattr(workspace.settings, "VSCODE_WEB_BASE_URL", "https://code.example.dev")
    monkeypatch.setattr(workspace.settings, "VSCODE_WORKSPACE_PATH", "/srv/workspaces")
    monkeypatch.setattr(workspace, "resolve_path", lambda path, user_id: file_path)
    monkeypatch.setattr(workspace, "get_user_workspace", lambda user_id: tmp_path)

    result = await workspace.get_open_url(path="app.py", user_id=1)

    assert result["mode"] == "web-vscode"
    assert result["url"] == (
        "https://code.example.dev?"
        "folder=%2Fsrv%2Fworkspaces%2Fuser_1&file=%2Fsrv%2Fworkspaces%2Fuser_1%2Fapp.py"
    )


@pytest.mark.asyncio
async def test_open_url_respects_workspace_template(monkeypatch, tmp_path):
    from enterprise_agent.api.routes import workspace

    file_path = tmp_path / "app.py"
    file_path.write_text("print('hi')", encoding="utf-8")

    monkeypatch.setattr(workspace.settings, "FILE_OPEN_MODE", "web-vscode")
    monkeypatch.setattr(workspace.settings, "VSCODE_WEB_URL_TEMPLATE", "")
    monkeypatch.setattr(workspace.settings, "VSCODE_WEB_BASE_URL", "https://code.example.dev")
    monkeypatch.setattr(workspace.settings, "VSCODE_WORKSPACE_PATH", "/isolated/{user_id}")
    monkeypatch.setattr(workspace, "resolve_path", lambda path, user_id: file_path)
    monkeypatch.setattr(workspace, "get_user_workspace", lambda user_id: tmp_path)

    result = await workspace.get_open_url(path="app.py", user_id=7)

    assert result["url"] == "https://code.example.dev?folder=%2Fisolated%2F7&file=%2Fisolated%2F7%2Fapp.py"


@pytest.mark.asyncio
async def test_open_url_rejects_directories(monkeypatch, tmp_path):
    from enterprise_agent.api.routes import workspace

    monkeypatch.setattr(workspace, "resolve_path", lambda path, user_id: tmp_path)
    monkeypatch.setattr(workspace, "get_user_workspace", lambda user_id: tmp_path)

    with pytest.raises(HTTPException) as exc:
        await workspace.get_open_url(path="", user_id=1)

    assert exc.value.status_code == 400


def _quoted_tmp_path(path):
    from urllib.parse import quote

    return quote(str(path.resolve()).replace("\\", "/"), safe="/:")
