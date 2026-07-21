"""Tests for team module (spawn_teammate, message passing, etc.)."""

from pathlib import Path

import pytest

from enterprise_agent.core.agent.tools.team import (
    TEAM_DIR_NAME,
    TEAMMATE_SYSTEM_PROMPT_TEMPLATE,
    VALID_MSG_TYPES,
    AsyncMessageBus,
    TeammateConfig,
    broadcast,
    idle,
    list_teammates,
    read_inbox,
    send_message,
    shutdown_request,
    spawn_teammate,
)


class TestTeammateSystemPrompt:
    """Test teammate system prompt template."""

    def test_template_has_name_placeholder(self):
        """Test template has {name} placeholder."""
        assert "{name}" in TEAMMATE_SYSTEM_PROMPT_TEMPLATE

    def test_template_has_role_placeholder(self):
        """Test template has {role} placeholder."""
        assert "{role}" in TEAMMATE_SYSTEM_PROMPT_TEMPLATE

    def test_template_is_concise(self):
        """Test template is concise (under 250 chars)."""
        # After simplification, should be very short
        assert len(TEAMMATE_SYSTEM_PROMPT_TEMPLATE) < 250

    def test_template_mentions_idle(self):
        """Test template mentions idle tool."""
        assert "idle" in TEAMMATE_SYSTEM_PROMPT_TEMPLATE.lower()

    def test_template_formatting(self):
        """Test template can be formatted."""
        formatted = TEAMMATE_SYSTEM_PROMPT_TEMPLATE.format(
            name="test_agent",
            role="Tester"
        )
        assert "test_agent" in formatted
        assert "Tester" in formatted


class TestAsyncMessageBus:
    """Test message bus functionality."""

    @pytest.fixture
    def message_bus(self, temp_workspace: Path):
        """Create message bus with temp directory."""
        team_dir = temp_workspace / TEAM_DIR_NAME
        return AsyncMessageBus(team_dir)

    @pytest.mark.asyncio
    async def test_send_message(self, message_bus: AsyncMessageBus):
        """Test sending a message."""
        result = await message_bus.send(
            sender="lead",
            to="teammate",
            content="Test message"
        )
        assert "Sent" in result

    @pytest.mark.asyncio
    async def test_read_inbox(self, message_bus: AsyncMessageBus):
        """Test reading inbox."""
        # Send a message first
        await message_bus.send("lead", "test_agent", "Hello")

        # Read inbox
        messages = await message_bus.read_inbox("test_agent")
        assert len(messages) == 1
        assert messages[0]["content"] == "Hello"
        assert messages[0]["from"] == "lead"

    @pytest.mark.asyncio
    async def test_read_inbox_clears_messages(self, message_bus: AsyncMessageBus):
        """Test that reading inbox clears it."""
        await message_bus.send("lead", "test_agent", "Hello")

        # Read once
        messages1 = await message_bus.read_inbox("test_agent")
        assert len(messages1) == 1

        # Read again - should be empty
        messages2 = await message_bus.read_inbox("test_agent")
        assert len(messages2) == 0

    @pytest.mark.asyncio
    async def test_broadcast(self, message_bus: AsyncMessageBus):
        """Test broadcast to multiple recipients."""
        result = await message_bus.broadcast(
            sender="lead",
            content="Broadcast message",
            names=["agent1", "agent2", "agent3"]
        )
        assert "Broadcast" in result
        assert "3" in result or "3 teammates" in result.lower()

    @pytest.mark.asyncio
    async def test_invalid_msg_type_rejected(self, message_bus: AsyncMessageBus):
        """Test invalid message type is rejected."""
        result = await message_bus.send(
            sender="lead",
            to="agent",
            content="test",
            msg_type="invalid_type"
        )
        assert "Error" in result or "Invalid" in result


class TestTeammateConfig:
    """Test teammate configuration persistence."""

    @pytest.fixture
    def teammate_config(self, temp_workspace: Path):
        """Create config with temp directory."""
        team_dir = temp_workspace / TEAM_DIR_NAME
        return TeammateConfig(team_dir)

    @pytest.mark.asyncio
    async def test_save_and_load_config(self, teammate_config: TeammateConfig):
        """Test saving and loading config."""
        config = {"team_name": "test_team", "members": []}
        await teammate_config.save(config)

        loaded = await teammate_config.load()
        assert loaded["team_name"] == "test_team"

    @pytest.mark.asyncio
    async def test_add_member(self, teammate_config: TeammateConfig):
        """Test adding a member."""
        await teammate_config.add_member("coder", "Developer", "working")

        config = await teammate_config.load()
        assert len(config["members"]) == 1
        assert config["members"][0]["name"] == "coder"

    @pytest.mark.asyncio
    async def test_remove_member(self, teammate_config: TeammateConfig):
        """Test removing a member."""
        await teammate_config.add_member("coder", "Developer", "working")
        await teammate_config.remove_member("coder")

        config = await teammate_config.load()
        assert len(config["members"]) == 0

    @pytest.mark.asyncio
    async def test_update_member_status(self, teammate_config: TeammateConfig):
        """Test updating member status."""
        await teammate_config.add_member("coder", "Developer", "working")
        await teammate_config.update_member_status("coder", "idle")

        config = await teammate_config.load()
        assert config["members"][0]["status"] == "idle"

    @pytest.mark.asyncio
    async def test_find_member(self, teammate_config: TeammateConfig):
        """Test finding a member by name."""
        await teammate_config.add_member("coder", "Developer", "working")

        member = await teammate_config.find_member("coder")
        assert member is not None
        assert member["role"] == "Developer"

        not_found = await teammate_config.find_member("nonexistent")
        assert not_found is None


class TestValidMsgTypes:
    """Test valid message types constant."""

    def test_message_type_exists(self):
        """Test 'message' is valid type."""
        assert "message" in VALID_MSG_TYPES

    def test_broadcast_type_exists(self):
        """Test 'broadcast' is valid type."""
        assert "broadcast" in VALID_MSG_TYPES

    def test_shutdown_request_type_exists(self):
        """Test 'shutdown_request' is valid type."""
        assert "shutdown_request" in VALID_MSG_TYPES

    def test_shutdown_response_type_exists(self):
        """Test 'shutdown_response' is valid type."""
        assert "shutdown_response" in VALID_MSG_TYPES


class TestToolDefinitions:
    """Test tool definitions."""

    def test_spawn_teammate_has_name(self):
        """Test spawn_teammate tool name."""
        assert spawn_teammate.name == "spawn_teammate"

    def test_list_teammates_has_name(self):
        """Test list_teammates tool name."""
        assert list_teammates.name == "list_teammates"

    def test_send_message_has_name(self):
        """Test send_message tool name."""
        assert send_message.name == "send_message"

    def test_read_inbox_has_name(self):
        """Test read_inbox tool name."""
        assert read_inbox.name == "read_inbox"

    def test_broadcast_has_name(self):
        """Test broadcast tool name."""
        assert broadcast.name == "broadcast"

    def test_shutdown_request_has_name(self):
        """Test shutdown_request tool name."""
        assert shutdown_request.name == "shutdown_request"

    def test_idle_has_name(self):
        """Test idle tool name."""
        assert idle.name == "idle"

    def test_spawn_teammate_description_mentions_role(self):
        """Test spawn_teammate description mentions role."""
        desc = spawn_teammate.description.lower()
        assert "role" in desc


class TestIdleTool:
    """Test idle tool."""

    def test_idle_returns_confirmation(self):
        """Test idle tool returns confirmation."""
        result = idle.invoke({})
        assert "idle" in result.lower() or "Entering" in result