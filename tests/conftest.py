"""Pytest configuration and fixtures for enterprise_agent tests."""

import os
import tempfile
from pathlib import Path
from typing import Generator

import pytest

# === Workspace Fixtures ===

@pytest.fixture
def temp_workspace() -> Generator[Path, None, None]:
    """Create a temporary workspace directory for file operations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        yield workspace


@pytest.fixture
def mock_workspace_env(temp_workspace: Path) -> Generator[None, None, None]:
    """Set WORKSPACE_BASE environment variable to temp directory."""
    original = os.environ.get("WORKSPACE_BASE")
    os.environ["WORKSPACE_BASE"] = str(temp_workspace)
    yield
    from enterprise_agent.core.agent.tools.background import shutdown_background_managers

    shutdown_background_managers()
    if original:
        os.environ["WORKSPACE_BASE"] = original
    else:
        os.environ.pop("WORKSPACE_BASE", None)


@pytest.fixture
def full_workspace_setup(temp_workspace: Path) -> Generator[Path, None, None]:
    """Complete workspace setup: environment variable + user context + user_id=0 for default."""
    from enterprise_agent.core.agent.tools.workspace import set_current_user_id

    # Set user_id to 0 so workspace becomes WORKSPACE_BASE/default (which we'll mock)
    original_env = os.environ.get("WORKSPACE_BASE")
    os.environ["WORKSPACE_BASE"] = str(temp_workspace.parent)  # Set base to temp's parent

    # Set user_id to None/0 to use default workspace
    set_current_user_id(None)

    yield temp_workspace

    # Cleanup
    if original_env:
        os.environ["WORKSPACE_BASE"] = original_env
    else:
        os.environ.pop("WORKSPACE_BASE", None)
    set_current_user_id(None)


# === User Context Fixtures ===

@pytest.fixture
def mock_user_id() -> int:
    """Provide a mock user ID for tests."""
    return 1


@pytest.fixture
def set_user_context(mock_user_id: int) -> Generator[None, None, None]:
    """Set the current user ID in context variable."""
    from enterprise_agent.core.agent.tools.workspace import set_current_user_id
    set_current_user_id(mock_user_id)
    yield
    set_current_user_id(None)


# === Mock LLM Fixtures ===

@pytest.fixture
def mock_llm_response():
    """Create a mock LLM response object."""
    class MockResponse:
        content = "Test response"
        tool_calls = []
        usage_metadata = {"total_tokens": 100}

    return MockResponse()


@pytest.fixture
def mock_llm_with_tools(mock_llm_response):
    """Create a mock LLM with tools bound."""
    class MockLLM:
        async def ainvoke(self, messages):
            return mock_llm_response

        def bind_tools(self, tools):
            return self

    return MockLLM()


# === Test Data Fixtures ===

@pytest.fixture
def sample_file_content() -> str:
    """Provide sample file content for tests."""
    return """# Sample Python File
def hello():
    print("Hello, World!")

def add(a, b):
    return a + b
"""


@pytest.fixture
def sample_task_items() -> list:
    """Provide sample task items for TodoWrite tests."""
    return [
        {"content": "Write tests", "status": "pending", "activeForm": "Writing tests"},
        {"content": "Run tests", "status": "pending", "activeForm": "Running tests"},
    ]


# === Async Test Support ===

@pytest.fixture
def async_test_runner():
    """Provide helper for running async tests."""
    import asyncio

    def run_async(coro):
        return asyncio.run(coro)

    return run_async
