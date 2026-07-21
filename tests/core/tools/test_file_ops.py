"""Tests for file_ops module (read_file, write_file, edit_file)."""

from pathlib import Path
from unittest.mock import patch

import pytest

from enterprise_agent.core.agent.tools.file_ops import (
    edit_file,
    read_file,
    write_file,
)


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
