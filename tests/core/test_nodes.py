"""Tests for nodes module (LangGraph agent nodes)."""

import asyncio

from enterprise_agent.core.agent.nodes import (
    IDEMPOTENT_TOOLS,
    MAIN_SYSTEM_PROMPT,
    RETRYABLE_ERROR_PATTERNS,
    _build_environment_info,
    _convert_from_langchain_messages,
    _convert_to_langchain_messages,
    _drain_memory_flush_tasks,
    _extract_text,
    _memory_flush_tasks,
    _schedule_memory_flush,
    route_after_llm,
    route_after_tool,
)


class TestMainSystemPrompt:
    """Test MAIN_SYSTEM_PROMPT constant."""

    def test_prompt_exists(self):
        """Test that MAIN_SYSTEM_PROMPT exists."""
        assert MAIN_SYSTEM_PROMPT is not None
        assert len(MAIN_SYSTEM_PROMPT) > 100

    def test_prompt_has_environment_placeholder(self):
        """Test that prompt has environment_info placeholder."""
        assert "{environment_info}" in MAIN_SYSTEM_PROMPT

    def test_prompt_mentions_tools(self):
        """Test prompt mentions tool access."""
        assert "powerful tools" in MAIN_SYSTEM_PROMPT

    def test_prompt_has_decision_framework(self):
        """Test prompt has decision framework section."""
        assert "Decision Framework" in MAIN_SYSTEM_PROMPT

    def test_prompt_mentions_parallel_tools(self):
        """Test prompt mentions tools for parallel or delegated work."""
        assert "spawn_teammate" in MAIN_SYSTEM_PROMPT

    def test_prompt_mentions_skills(self):
        """Test prompt mentions skills."""
        assert "Available Skills" in MAIN_SYSTEM_PROMPT

    def test_prompt_is_concise(self):
        """Test prompt is concise after simplification."""
        # Should be less than 100 lines (roughly 3000 chars)
        assert len(MAIN_SYSTEM_PROMPT) < 3000

    def test_prompt_can_be_formatted(self):
        """Test prompt can be formatted with environment_info."""
        formatted = MAIN_SYSTEM_PROMPT.format(
            environment_info="Test Environment",
            available_skills="Test Skills",
        )
        assert "Test Environment" in formatted
        assert "Test Skills" in formatted
        # Placeholder should be replaced
        assert "{environment_info}" not in formatted
        assert "{available_skills}" not in formatted


class TestBuildEnvironmentInfo:
    """Test _build_environment_info function."""

    def test_returns_string(self):
        """Test returns a string."""
        result = _build_environment_info()
        assert isinstance(result, str)

    def test_contains_os_info(self):
        """Test contains OS information."""
        result = _build_environment_info()
        assert "OS:" in result

    def test_contains_workspace_info(self):
        """Test contains workspace information."""
        result = _build_environment_info()
        assert "Workspace:" in result

    def test_contains_python_info(self):
        """Test contains Python version."""
        result = _build_environment_info()
        assert "Python:" in result


class TestExtractText:
    """Test _extract_text function."""

    def test_extract_from_string(self):
        """Test extracting from plain string."""
        result = _extract_text("Hello World")
        assert result == "Hello World"

    def test_extract_from_text_block(self):
        """Test extracting from text block dict."""
        content = [{"type": "text", "text": "Hello"}]
        result = _extract_text(content)
        assert result == "Hello"

    def test_extract_from_multiple_blocks(self):
        """Test extracting from multiple blocks."""
        content = [
            {"type": "text", "text": "Hello"},
            {"type": "text", "text": "World"}
        ]
        result = _extract_text(content)
        assert "Hello" in result
        assert "World" in result

    def test_extract_from_object_with_text_attr(self):
        """Test extracting from object with .text attribute."""
        class MockBlock:
            text = "Mock text"

        content = [MockBlock()]
        result = _extract_text(content)
        assert result == "Mock text"


class TestConvertToLangchainMessages:
    """Test _convert_to_langchain_messages function."""

    def test_convert_user_message(self):
        """Test converting user message."""
        messages = [{"role": "user", "content": "Hello"}]
        result = _convert_to_langchain_messages(messages)
        assert len(result) == 1
        assert result[0].type == "human"

    def test_convert_assistant_message(self):
        """Test converting assistant message."""
        messages = [{"role": "assistant", "content": "Hi there"}]
        result = _convert_to_langchain_messages(messages)
        assert len(result) == 1
        assert result[0].type == "ai"

    def test_convert_system_message(self):
        """Test converting system message."""
        messages = [{"role": "system", "content": "System prompt"}]
        result = _convert_to_langchain_messages(messages)
        assert len(result) == 1
        assert result[0].type == "system"

    def test_convert_tool_message(self):
        """Test converting tool message."""
        messages = [{
            "role": "tool",
            "content": "Tool result",
            "tool_call_id": "call_123"
        }]
        result = _convert_to_langchain_messages(messages)
        assert len(result) == 1
        assert result[0].type == "tool"

    def test_preserves_tool_calls(self):
        """Test that tool_calls are preserved."""
        messages = [{
            "role": "assistant",
            "content": "Response",
            "tool_calls": [{"id": "1", "name": "bash", "args": {}}]
        }]
        result = _convert_to_langchain_messages(messages)
        assert hasattr(result[0], "tool_calls")
        assert len(result[0].tool_calls) == 1


class TestConvertFromLangchainMessages:
    """Test _convert_from_langchain_messages function."""

    def test_convert_back_to_dict(self):
        """Test converting back to dict format."""
        from langchain_core.messages import HumanMessage

        messages = [HumanMessage(content="Hello")]
        result = _convert_from_langchain_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "human"
        assert result[0]["content"] == "Hello"


class TestRoutingFunctions:
    """Test routing functions."""

    def test_route_after_llm_returns_save_memory_when_no_tools(self):
        """Test route_after_llm returns 'save_memory' when no tool calls."""
        state = {"pending_tool_calls": [], "round_count": 0, "token_count": 0}
        result = route_after_llm(state)
        assert result == "save_memory"
        assert "should_end_after_save" not in state

    def test_route_after_llm_returns_tool_call_when_has_tools(self):
        """Test route_after_llm returns 'tool_call' when has tool calls."""
        state = {
            "pending_tool_calls": [{"name": "bash"}],
            "round_count": 0,
            "token_count": 0
        }
        result = route_after_llm(state)
        assert result == "tool_call"

    def test_route_after_llm_ends_at_max_rounds(self):
        """Test route_after_llm sets end flag at max rounds."""
        from enterprise_agent.config.settings import settings
        state = {
            "pending_tool_calls": [{"name": "bash"}],
            "round_count": settings.MAX_AGENT_ROUNDS,
            "token_count": 0
        }
        result = route_after_llm(state)
        assert result == "save_memory"
        assert "should_end_after_save" not in state

    def test_route_after_tool_returns_llm_call(self):
        """Test route_after_tool returns 'llm_call' normally."""
        state = {
            "round_count": 0,
            "token_count": 0,
            "should_compress": False,
            "should_end_after_save": False
        }
        result = route_after_tool(state)
        assert result == "llm_call"

    def test_route_after_tool_ends_when_flag_set(self):
        """Test route_after_tool returns 'end' when flag set."""
        state = {
            "round_count": 0,
            "token_count": 0,
            "should_end_after_save": True
        }
        result = route_after_tool(state)
        assert result == "end"


class TestMemoryFlushTaskTracking:
    """Test background memory flush task lifecycle tracking."""

    def test_scheduled_memory_flush_tasks_are_drained(self, monkeypatch):
        """Scheduled flush tasks should be tracked and drainable on shutdown."""
        ran = False

        async def fake_background_flush(*args):
            nonlocal ran
            await asyncio.sleep(0)
            ran = True

        monkeypatch.setattr(
            "enterprise_agent.core.agent.nodes._background_flush",
            fake_background_flush,
        )

        async def run():
            _schedule_memory_flush("acc", {"user_request": "x"}, "session-1", 1, [])
            assert len(_memory_flush_tasks) == 1
            await _drain_memory_flush_tasks(timeout=1)

        asyncio.run(run())

        assert ran is True
        assert len(_memory_flush_tasks) == 0


class TestIdempotentTools:
    """Test IDEMPOTENT_TOOLS constant."""

    def test_read_file_is_idempotent(self):
        """Test read_file is in idempotent tools."""
        assert "read_file" in IDEMPOTENT_TOOLS

    def test_list_skills_is_idempotent(self):
        """Test list_skills is idempotent."""
        assert "list_skills" in IDEMPOTENT_TOOLS

    def test_write_file_is_not_idempotent(self):
        """Test write_file is NOT idempotent."""
        assert "write_file" not in IDEMPOTENT_TOOLS

    def test_edit_file_is_not_idempotent(self):
        """Test edit_file is NOT idempotent."""
        assert "edit_file" not in IDEMPOTENT_TOOLS

    def test_bash_is_not_idempotent(self):
        """Test bash is NOT idempotent (has side effects)."""
        assert "bash" not in IDEMPOTENT_TOOLS


class TestRetryableErrorPatterns:
    """Test RETRYABLE_ERROR_PATTERNS constant."""

    def test_timeout_is_retryable(self):
        """Test timeout pattern is retryable."""
        assert "timeout" in RETRYABLE_ERROR_PATTERNS

    def test_connection_is_retryable(self):
        """Test connection pattern is retryable."""
        assert "connection" in RETRYABLE_ERROR_PATTERNS

    def test_rate_limit_is_retryable(self):
        """Test rate limit pattern is retryable."""
        assert "rate limit" in RETRYABLE_ERROR_PATTERNS
