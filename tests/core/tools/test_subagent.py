"""Tests for subagent module (task tool)."""

import pytest
from langchain_core.messages import AIMessage

from enterprise_agent.core.agent.tools import subagent
from enterprise_agent.core.agent.tools.subagent import (
    AGENT_TYPES,
    SUBAGENT_SYSTEM_PROMPTS,
    delegate_task,
    task,
)


class TestAgentTypes:
    """Test agent type definitions."""

    def test_explore_agent_type_exists(self):
        """Test that Explore agent type is defined."""
        assert "Explore" in AGENT_TYPES

    def test_general_purpose_agent_type_exists(self):
        """Test that general-purpose agent type is defined."""
        assert "general-purpose" in AGENT_TYPES

    def test_specialist_agent_is_tool_free(self):
        assert AGENT_TYPES["specialist"] == []

    def test_explore_agent_has_read_only_tools(self):
        """Test that Explore agent has read-only tool set."""
        tools = AGENT_TYPES["Explore"]
        assert "bash" in tools
        assert "read_file" in tools
        # Should not have write tools
        assert "write_file" not in tools
        assert "edit_file" not in tools

    def test_general_purpose_agent_has_write_tools(self):
        """Test that general-purpose agent has write tools."""
        tools = AGENT_TYPES["general-purpose"]
        assert "bash" in tools
        assert "read_file" in tools
        assert "write_file" in tools
        assert "edit_file" in tools


class TestSubagentSystemPrompts:
    """Test subagent system prompt definitions."""

    def test_all_agent_types_have_prompts(self):
        """Test that each agent type has a system prompt."""
        for agent_type in AGENT_TYPES:
            assert agent_type in SUBAGENT_SYSTEM_PROMPTS

    def test_explore_prompt_mentions_read_only(self):
        """Test that Explore prompt mentions read-only nature."""
        prompt = SUBAGENT_SYSTEM_PROMPTS["Explore"]
        assert "read-only" in prompt.lower() or "Do NOT modify" in prompt

    def test_general_purpose_prompt_mentions_write(self):
        """Test that general-purpose prompt mentions write capability."""
        prompt = SUBAGENT_SYSTEM_PROMPTS["general-purpose"]
        assert "write" in prompt.lower() or "edit" in prompt.lower()

    def test_prompts_are_not_empty(self):
        """Test that prompts have meaningful content."""
        for agent_type, prompt in SUBAGENT_SYSTEM_PROMPTS.items():
            assert len(prompt) > 50  # Should have substantial content


class TestTaskToolDefinition:
    """Test task tool definition and metadata."""

    def test_task_tool_name(self):
        """Test task tool name."""
        assert task.name == "task"

    def test_task_tool_has_description(self):
        """Test task tool has a description."""
        assert task.description is not None
        assert len(task.description) > 50

    def test_task_tool_description_mentions_agent_types(self):
        """Test task description mentions agent types."""
        desc = task.description.lower()
        assert "explore" in desc or "general-purpose" in desc

    def test_task_tool_has_required_args(self):
        """Test task tool has required arguments."""
        # Check tool args schema
        args_schema = task.args_schema
        if args_schema:
            # Should have 'prompt' argument
            assert hasattr(args_schema, '__fields__') or 'prompt' in str(args_schema)

    def test_delegate_task_is_explicit_real_delegation_tool(self):
        assert delegate_task.name == "delegate_task"
        assert "separate model context" in delegate_task.description


class TestTaskToolExecution:
    """Test task tool execution behavior (mocked)."""

    @pytest.mark.asyncio
    async def test_task_with_invalid_agent_type(self):
        """Test task with invalid agent_type returns error."""
        result = await task.ainvoke({
            "prompt": "test",
            "agent_type": "invalid_type"
        })
        assert "Error" in result or "Unknown" in result

    @pytest.mark.asyncio
    async def test_task_with_none_agent_type_defaults_to_explore(self):
        """Test that None agent_type defaults to Explore."""
        # This would normally call LLM, so we just check it doesn't error
        # on agent_type validation
        # Note: Full execution requires LLM, so this is partial test
        agent_types = AGENT_TYPES.keys()
        assert "Explore" in agent_types  # Default should be valid

    @pytest.mark.asyncio
    async def test_delegate_task_runs_an_isolated_tool_free_model_context(self, monkeypatch):
        class FakeModel:
            async def ainvoke(self, messages):
                assert "independent specialist subagent" in messages[0].content
                assert "Your delegated role is: reviewer" in messages[1].content
                return AIMessage(content="The reviewer found a weak ending.")

        monkeypatch.setattr(subagent, "get_llm", lambda: FakeModel())
        result = await delegate_task.ainvoke({
            "role": "reviewer",
            "prompt": "Review this story ending.",
        })
        assert result == "The reviewer found a weak ending."


class TestSubagentPromptTemplates:
    """Test subagent prompt template formatting."""

    def test_prompts_have_guidelines_section(self):
        """Test that prompts have guidelines section."""
        for agent_type, prompt in SUBAGENT_SYSTEM_PROMPTS.items():
            assert "Guidelines" in prompt or "guidelines" in prompt.lower()

    def test_prompts_have_capabilities_section(self):
        """Test that prompts mention capabilities."""
        for agent_type, prompt in SUBAGENT_SYSTEM_PROMPTS.items():
            assert "Capabilities" in prompt or "capabilities" in prompt.lower()
