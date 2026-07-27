"""Tests for state module (AgentState definition)."""

from enterprise_agent.core.agent.state import AgentState


class TestAgentStateDefinition:
    """Test AgentState TypedDict definition."""

    def test_state_has_messages_field(self):
        """Test state has messages field."""
        assert "messages" in AgentState.__annotations__

    def test_state_has_token_count_field(self):
        """Test state has token_count field."""
        assert "token_count" in AgentState.__annotations__

    def test_state_has_pending_tool_calls_field(self):
        """Test state has pending_tool_calls field."""
        assert "pending_tool_calls" in AgentState.__annotations__

    def test_state_has_round_count_field(self):
        """Test state has round_count field."""
        assert "round_count" in AgentState.__annotations__

    def test_state_has_user_id_field(self):
        """Test state has user_id field."""
        assert "user_id" in AgentState.__annotations__

    def test_state_has_session_id_field(self):
        """Test state has session_id field."""
        assert "session_id" in AgentState.__annotations__

    def test_state_has_reliable_execution_fields(self):
        required = {
            "trace_id",
            "current_user_request",
            "task_status",
            "execution_phase",
            "task_token_count",
            "session_token_count",
            "tool_call_count",
            "tool_execution_records",
            "changed_files",
            "validation_results",
            "confirmation_deadline",
        }
        assert required.issubset(AgentState.__annotations__)

    def test_state_does_not_keep_unused_pending_memory_flush(self):
        """Memory flushing is managed by the accumulator, not a stale flag."""
        assert "pending_memory_flush" not in AgentState.__annotations__


class TestAgentStateUsage:
    """Test AgentState can be used as a dict."""

    def test_state_accepts_messages(self):
        """Test state accepts messages list."""
        state: AgentState = {
            "messages": [{"role": "user", "content": "Hello"}],
            "token_count": 0,
            "pending_tool_calls": [],
            "round_count": 0,
            "user_id": 1,
            "session_id": "test",
            "tool_results": {},
            "tool_call_stats": {},
            "should_compress": False,
            "should_end": False,
            "rounds_without_todo": 0,
            "used_todo_last_round": False,
            "has_open_todos": False,
        }
        assert len(state["messages"]) == 1

    def test_state_accepts_tool_calls(self):
        """Test state accepts tool calls."""
        state: AgentState = {
            "messages": [],
            "pending_tool_calls": [{"name": "bash", "args": {"command": "echo"}}],
            "token_count": 0,
            "round_count": 0,
        }
        assert len(state["pending_tool_calls"]) == 1

    def test_state_optional_fields(self):
        """Test state can have optional fields missing."""
        # Minimal state
        state: AgentState = {
            "messages": [],
        }
        assert state["messages"] == []


class TestStateFieldTypes:
    """Test state field type annotations."""

    def test_messages_is_list(self):
        """Test messages annotation is List type."""
        ann = AgentState.__annotations__["messages"]
        # Should be List or list type
        assert "List" in str(ann) or "list" in str(ann).lower()

    def test_token_count_is_int(self):
        """Test token_count annotation is int."""
        ann = AgentState.__annotations__["token_count"]
        assert "int" in str(ann)

    def test_pending_tool_calls_is_list(self):
        """Test pending_tool_calls annotation is List."""
        ann = AgentState.__annotations__["pending_tool_calls"]
        assert "List" in str(ann) or "list" in str(ann).lower()
