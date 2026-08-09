"""Tests for file_ops module (read_file, write_file, edit_file)."""

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from enterprise_agent.core.agent.tools.file_ops import (
    delete_paths,
    edit_file,
    read_file,
    write_file,
)


@pytest.fixture
def deletion_workspace(monkeypatch, tmp_path: Path):
    from enterprise_agent.core.agent.tools.workspace import (
        get_user_workspace,
        set_current_session_id,
        set_current_user_id,
    )

    monkeypatch.setenv("WORKSPACE_BASE", str(tmp_path))
    set_current_user_id(77)
    set_current_session_id("delete-session")
    yield get_user_workspace()
    set_current_user_id(None)
    set_current_session_id(None)


class TestReadFile:
    """Test read_file tool."""

    @patch('enterprise_agent.core.agent.tools.file_ops.resolve_path')
    def test_read_existing_file(self, mock_resolve, temp_workspace: Path):
        """Test reading an existing file."""
        # Setup mock to return path in temp workspace
        test_file = temp_workspace / "test.txt"
        test_file.write_text("Hello, World!")
        mock_resolve.return_value = test_file

        # Read file
        result = read_file.invoke({"path": "test.txt"})
        assert "Hello, World!" in result

    @patch('enterprise_agent.core.agent.tools.file_ops.resolve_path')
    def test_read_nonexistent_file(self, mock_resolve, temp_workspace: Path):
        """Test reading a file that doesn't exist."""
        mock_resolve.return_value = temp_workspace / "nonexistent.txt"
        result = read_file.invoke({"path": "nonexistent.txt"})
        assert "Error" in result

    @patch('enterprise_agent.core.agent.tools.file_ops.resolve_path')
    def test_read_with_limit(self, mock_resolve, temp_workspace: Path):
        """Test reading file with line limit."""
        # Create file with multiple lines
        test_file = temp_workspace / "multiline.txt"
        lines = ["Line " + str(i) for i in range(100)]
        test_file.write_text("\n".join(lines))
        mock_resolve.return_value = test_file

        # Read with limit
        result = read_file.invoke({"path": "multiline.txt", "limit": 10})
        assert "Line 0" in result
        assert "more lines)" in result  # truncation indicator

    @patch('enterprise_agent.core.agent.tools.file_ops.resolve_path')
    def test_read_binary_fails_gracefully(self, mock_resolve, temp_workspace: Path):
        """Test reading binary file returns error."""
        test_file = temp_workspace / "binary.bin"
        test_file.write_bytes(b"\x00\x01\x02")
        mock_resolve.return_value = test_file

        result = read_file.invoke({"path": "binary.bin"})
        # Should either read or return error gracefully
        assert isinstance(result, str)

    @pytest.mark.parametrize(
        "path",
        [
            ".agent/tool-artifacts/trace/call.txt",
            ".transcripts/transcript.jsonl",
            ".tasks/task_1.json",
            ".team/inbox/lead.jsonl",
        ],
    )
    def test_generic_file_tools_reject_operational_paths(self, path):
        read_result = read_file.invoke({"path": path})
        write_result = write_file.invoke({"path": path, "content": "tamper"})
        edit_result = edit_file.invoke({
            "path": path,
            "old_text": "before",
            "new_text": "after",
        })

        assert "dedicated" in read_result
        assert "dedicated" in write_result
        assert "dedicated" in edit_result


class TestWriteFile:
    """Test write_file tool."""

    @patch('enterprise_agent.core.agent.tools.file_ops.resolve_path')
    def test_write_new_file(self, mock_resolve, temp_workspace: Path):
        """Test writing a new file."""
        test_file = temp_workspace / "new_file.txt"
        mock_resolve.return_value = test_file

        result = write_file.invoke({
            "path": "new_file.txt",
            "content": "New content"
        })
        assert "Wrote" in result

        # Verify file exists
        assert test_file.exists()
        assert test_file.read_text() == "New content"

    @patch('enterprise_agent.core.agent.tools.file_ops.resolve_path')
    def test_write_overwrites_existing(self, mock_resolve, temp_workspace: Path):
        """Test that write_file overwrites existing file."""
        test_file = temp_workspace / "existing.txt"
        test_file.write_text("Old content")
        mock_resolve.return_value = test_file

        result = write_file.invoke({
            "path": "existing.txt",
            "content": "New content"
        })
        assert "Wrote" in result
        assert test_file.read_text() == "New content"

    @patch('enterprise_agent.core.agent.tools.file_ops.resolve_path')
    def test_write_creates_nested_directory(self, mock_resolve, temp_workspace: Path):
        """Test writing to nested path creates directories."""
        test_file = temp_workspace / "nested" / "dir" / "file.txt"
        mock_resolve.return_value = test_file

        result = write_file.invoke({
            "path": "nested/dir/file.txt",
            "content": "Nested content"
        })
        assert "Wrote" in result
        assert test_file.exists()

    @patch('enterprise_agent.core.agent.tools.file_ops.resolve_path')
    def test_write_unicode_content(self, mock_resolve, temp_workspace: Path):
        """Test writing Unicode content."""
        test_file = temp_workspace / "unicode.txt"
        mock_resolve.return_value = test_file

        unicode_content = "你好世界 🎉"
        result = write_file.invoke({
            "path": "unicode.txt",
            "content": unicode_content
        })
        assert "Wrote" in result
        assert test_file.read_text() == unicode_content

    @patch('enterprise_agent.core.agent.tools.file_ops.resolve_path')
    def test_write_takes_global_workspace_lock(self, mock_resolve, monkeypatch, temp_workspace):
        from enterprise_agent.core.agent.tools import file_ops

        target = temp_workspace / "locked.txt"
        mock_resolve.return_value = target
        lock_entries = []

        @contextmanager
        def recording_lock():
            lock_entries.append("entered")
            yield

        monkeypatch.setattr(file_ops, "workspace_write_lock", recording_lock)

        assert "Wrote" in write_file.invoke({"path": "locked.txt", "content": "safe"})
        assert lock_entries == ["entered"]


class TestEditFile:
    """Test edit_file tool."""

    @patch('enterprise_agent.core.agent.tools.file_ops.resolve_path')
    def test_edit_replace_text(self, mock_resolve, temp_workspace: Path):
        """Test replacing text in file."""
        test_file = temp_workspace / "edit_test.txt"
        test_file.write_text("Hello, World!")
        mock_resolve.return_value = test_file

        result = edit_file.invoke({
            "path": "edit_test.txt",
            "old_text": "World",
            "new_text": "Python"
        })
        assert "Edited" in result
        assert test_file.read_text() == "Hello, Python!"

    @patch('enterprise_agent.core.agent.tools.file_ops.resolve_path')
    def test_edit_text_not_found(self, mock_resolve, temp_workspace: Path):
        """Test editing when text not found."""
        test_file = temp_workspace / "edit_test.txt"
        test_file.write_text("Hello, World!")
        mock_resolve.return_value = test_file

        result = edit_file.invoke({
            "path": "edit_test.txt",
            "old_text": "NotFound",
            "new_text": "Python"
        })
        assert "Error" in result
        assert "not found" in result

    @patch('enterprise_agent.core.agent.tools.file_ops.resolve_path')
    def test_edit_nonexistent_file(self, mock_resolve, temp_workspace: Path):
        """Test editing nonexistent file."""
        mock_resolve.return_value = temp_workspace / "nonexistent.txt"
        result = edit_file.invoke({
            "path": "nonexistent.txt",
            "old_text": "text",
            "new_text": "new"
        })
        assert "Error" in result

    @patch('enterprise_agent.core.agent.tools.file_ops.resolve_path')
    def test_edit_replaces_first_occurrence_only(self, mock_resolve, temp_workspace: Path):
        """Test that edit_file only replaces first occurrence."""
        test_file = temp_workspace / "multiple.txt"
        test_file.write_text("foo foo foo")
        mock_resolve.return_value = test_file

        edit_file.invoke({
            "path": "multiple.txt",
            "old_text": "foo",
            "new_text": "bar"
        })
        assert test_file.read_text() == "bar foo foo"

    @patch('enterprise_agent.core.agent.tools.file_ops.resolve_path')
    def test_edit_read_modify_write_holds_global_lock(
        self,
        mock_resolve,
        monkeypatch,
        temp_workspace,
    ):
        from enterprise_agent.core.agent.tools import file_ops

        target = temp_workspace / "locked-edit.txt"
        target.write_text("before", encoding="utf-8")
        mock_resolve.return_value = target
        lock_entries = []

        @contextmanager
        def recording_lock():
            lock_entries.append("entered")
            yield

        monkeypatch.setattr(file_ops, "workspace_write_lock", recording_lock)

        result = edit_file.invoke({
            "path": "locked-edit.txt",
            "old_text": "before",
            "new_text": "after",
        })

        assert "Edited" in result
        assert target.read_text(encoding="utf-8") == "after"
        assert lock_entries == ["entered"]


class TestPathSecurity:
    """Test path security - ensure files can't escape workspace."""

    @patch('enterprise_agent.core.agent.tools.file_ops.resolve_path')
    def test_path_escape_blocked(self, mock_resolve):
        """Test that path escaping workspace is blocked."""
        # resolve_path raises ValueError for escaping paths
        mock_resolve.side_effect = ValueError("Path escapes workspace")
        result = read_file.invoke({"path": "../../../etc/passwd"})
        assert "Error" in result or "escapes" in result.lower()

    @patch('enterprise_agent.core.agent.tools.file_ops.resolve_path')
    def test_absolute_path_blocked(self, mock_resolve):
        """Test absolute paths outside workspace."""
        mock_resolve.side_effect = ValueError("Path escapes workspace")
        result = read_file.invoke({"path": "/etc/passwd"})
        assert "Error" in result or "escapes" in result.lower()

    @pytest.mark.parametrize(
        "path",
        [
            ".env",
            ".env.local",
            ".git/config",
            ".ssh/id_rsa",
            "deploy/private.key",
            "credentials.json",
        ],
    )
    def test_sensitive_credential_paths_are_blocked(self, path):
        result = read_file.invoke({"path": path})
        assert "Sensitive credential path" in result

    def test_env_example_remains_available(self, mock_workspace_env):
        from enterprise_agent.core.agent.tools.workspace import get_user_workspace

        (get_user_workspace() / ".env.example").write_text("SAFE=value\n", encoding="utf-8")
        assert read_file.invoke({"path": ".env.example"}) == "SAFE=value"


class TestDeletePaths:
    """Deletion must be exact, recoverable, workspace-scoped, and auditable."""

    def test_moves_exact_paths_to_recovery_trash(self, deletion_workspace: Path):
        workspace = deletion_workspace
        (workspace / "obsolete.txt").write_text("old", encoding="utf-8")
        (workspace / "generated").mkdir()
        (workspace / "generated" / "result.txt").write_text("result", encoding="utf-8")

        receipt = json.loads(delete_paths.invoke({
            "paths": ["obsolete.txt", "generated"],
            "reason": "Remove generated artifacts",
        }))

        assert receipt["status"] == "moved_to_recovery_trash"
        assert receipt["paths"] == ["obsolete.txt", "generated"]
        assert not (workspace / "obsolete.txt").exists()
        assert not (workspace / "generated").exists()
        manifest_path = workspace / receipt["recovery_manifest"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["user_id"] == 77
        assert manifest["session_id"] == "delete-session"
        assert manifest["reason"] == "Remove generated artifacts"
        trash_items = manifest_path.parent / "items"
        assert (trash_items / "obsolete.txt").read_text(encoding="utf-8") == "old"
        assert (trash_items / "generated" / "result.txt").exists()

    def test_delete_holds_global_workspace_lock(self, deletion_workspace, monkeypatch):
        from enterprise_agent.core.agent.tools import file_ops

        target = deletion_workspace / "locked-delete.txt"
        target.write_text("old", encoding="utf-8")
        lock_entries = []

        @contextmanager
        def recording_lock():
            lock_entries.append("entered")
            yield

        monkeypatch.setattr(file_ops, "workspace_write_lock", recording_lock)

        receipt = json.loads(delete_paths.invoke({
            "paths": ["locked-delete.txt"],
            "reason": "Verify serialized deletion",
        }))

        assert receipt["status"] == "moved_to_recovery_trash"
        assert lock_entries == ["entered"]

    @pytest.mark.parametrize(
        "path",
        [
            ".agent",
            ".tasks/item.json",
            ".vscode/settings.json",
            ".env",
            "../outside.txt",
            "/tmp/outside.txt",
            "generated/*",
        ],
    )
    def test_rejects_protected_ambiguous_or_escaping_paths(self, deletion_workspace: Path, path):
        result = delete_paths.invoke({"paths": [path], "reason": "Unsafe deletion test"})
        assert result.startswith("Error:")

    def test_rejects_overlapping_targets(self, deletion_workspace: Path):
        workspace = deletion_workspace
        (workspace / "generated").mkdir()
        (workspace / "generated" / "result.txt").write_text("result", encoding="utf-8")

        result = delete_paths.invoke({
            "paths": ["generated", "generated/result.txt"],
            "reason": "Overlapping deletion test",
        })

        assert "Overlapping delete paths" in result
        assert (workspace / "generated" / "result.txt").exists()

    def test_moves_symlink_without_touching_external_target(
        self, deletion_workspace: Path, tmp_path: Path
    ):
        workspace = deletion_workspace
        external = tmp_path / "external.txt"
        external.write_text("keep", encoding="utf-8")
        link = workspace / "external-link"
        try:
            link.symlink_to(external)
        except OSError:
            pytest.skip("Symlinks are unavailable on this platform")

        receipt = json.loads(delete_paths.invoke({
            "paths": ["external-link"],
            "reason": "Remove workspace link only",
        }))

        assert not link.exists()
        assert external.read_text(encoding="utf-8") == "keep"
        assert receipt["status"] == "moved_to_recovery_trash"
