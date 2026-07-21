import os
import tempfile
from typing import Optional

from langchain_core.tools import tool

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tools.workspace import is_sensitive_agent_path, resolve_path


def _validate_agent_file_path(path: str) -> None:
    if is_sensitive_agent_path(path):
        raise ValueError(f"Sensitive credential path is not available to Agent tools: {path}")


def _atomic_write_text(path, content: str) -> None:
    """Replace a file atomically so interrupted writes do not leave partial content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    previous_mode = path.stat().st_mode if path.exists() else None
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        if previous_mode is not None:
            os.chmod(temp_name, previous_mode)
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


@tool
def read_file(path: str, limit: Optional[int] = None) -> str:
    """Read file contents from workspace.

    Args:
        path: Relative path to file within workspace
        limit: Maximum number of lines to read (optional)

    Returns:
        File contents as string
    """
    try:
        _validate_agent_file_path(path)
        fp = resolve_path(path)
        lines = fp.read_text(encoding="utf-8").splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:settings.TOOL_OUTPUT_MAX_CHARS]
    except Exception as e:
        return f"Error: {e}"


@tool
def write_file(path: str, content: str) -> str:
    """Write content to file in workspace.

    Args:
        path: Relative path to file within workspace
        content: Content to write

    Returns:
        Success message with first N lines preview (trust but verify)
    """
    try:
        _validate_agent_file_path(path)
        fp = resolve_path(path)
        _atomic_write_text(fp, content)

        # Auto-verify: re-read and show preview
        verified = fp.read_text(encoding="utf-8")
        lines = verified.splitlines()
        preview_lines = lines[:settings.VERIFICATION_PREVIEW_LINES]
        preview = "\n".join(preview_lines)
        if len(lines) > settings.VERIFICATION_PREVIEW_LINES:
            preview += f"\n... ({len(lines) - settings.VERIFICATION_PREVIEW_LINES} more lines)"

        return f"Wrote {len(content)} bytes to {path}\n\nVerified preview:\n{preview}"
    except Exception as e:
        return f"Error: {e}"


@tool
def edit_file(path: str, old_text: str, new_text: str) -> str:
    """Replace exact text in file.

    Args:
        path: Relative path to file within workspace
        old_text: Exact text to find and replace
        new_text: New text to insert

    Returns:
        Success message with diff preview (trust but verify)
    """
    try:
        _validate_agent_file_path(path)
        fp = resolve_path(path)
        content = fp.read_text(encoding="utf-8")
        if old_text not in content:
            return f"Error: Text not found in {path}"

        # Perform edit
        new_content = content.replace(old_text, new_text, 1)
        _atomic_write_text(fp, new_content)

        # Auto-verify: re-read and show context around edit
        verified = fp.read_text(encoding="utf-8")
        if new_text in verified:
            # Show ~5 lines of context around the edit
            lines = verified.splitlines()
            for i, line in enumerate(lines):
                if new_text in line:
                    start = max(0, i - 3)
                    end = min(len(lines), i + 4)
                    context = "\n".join(f"{j+1}: {lines[j]}" for j in range(start, end))
                    return f"Edited {path}\n\nVerified content around edit:\n{context}"
            # new_text spans multiple lines
            for i in range(len(lines)):
                chunk = "\n".join(lines[i:i+5])
                if new_text[:50] in chunk:  # Check first 50 chars
                    start = max(0, i - 2)
                    end = min(len(lines), i + 6)
                    context = "\n".join(f"{j+1}: {lines[j]}" for j in range(start, end))
                    return f"Edited {path}\n\nVerified content around edit:\n{context}"
            return f"Edited {path}\n(Edit verified successfully)"

        return f"Edited {path}\nWarning: Could not verify edit"
    except Exception as e:
        return f"Error: {e}"
