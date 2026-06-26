"""Tests for per-user workspace initialization."""

import json


def test_get_user_workspace_creates_vscode_settings(monkeypatch, tmp_path):
    from enterprise_agent.core.agent.tools import workspace

    monkeypatch.setattr(workspace, "WORKSPACE_BASE", tmp_path)

    user_workspace = workspace.get_user_workspace(42)

    settings_path = user_workspace / ".vscode" / "settings.json"
    assert user_workspace == tmp_path / "user_42"
    assert settings_path.exists()

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["ruff.enable"] is False
    assert settings["ruff.lint.args"] == []
    assert settings["ruff.format.args"] == []
    assert settings["ruff.configuration"] is None
    assert settings["python.analysis.autoSearchPaths"] is False
    assert settings["python.analysis.useLibraryCodeForTypes"] is False
    assert settings["files.exclude"]["**/.agent_internal"] is True


def test_get_user_workspace_repairs_managed_vscode_settings(monkeypatch, tmp_path):
    from enterprise_agent.core.agent.tools import workspace

    monkeypatch.setattr(workspace, "WORKSPACE_BASE", tmp_path)
    settings_path = tmp_path / "user_7" / ".vscode" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "editor.fontSize": 15,
                "ruff.enable": True,
                "ruff.lint.args": ["--config=pyproject.toml"],
                "files.exclude": {"**/.cache": True},
            }
        ),
        encoding="utf-8",
    )

    workspace.get_user_workspace(7)

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["editor.fontSize"] == 15
    assert settings["ruff.enable"] is False
    assert settings["ruff.lint.args"] == []
    assert settings["ruff.format.args"] == []
    assert settings["ruff.configuration"] is None
    assert settings["files.exclude"] == {
        "**/.cache": True,
        "**/.agent_internal": True,
    }
