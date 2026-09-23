from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from enterprise_agent.core.agent.message_history import complete_tool_results


def test_interrupted_tool_groups_are_closed_before_followups_without_reexecution():
    request = AIMessage(content="", tool_calls=[
        {"id": "done", "name": "bash", "args": {}},
        {"id": "unknown", "name": "bash", "args": {}},
    ])
    recorded = ToolMessage(content="real output", tool_call_id="done")
    followup = HumanMessage(content="continue")
    second_request = AIMessage(content="", tool_calls=[
        {"id": "second", "name": "read_file", "args": {}},
    ])
    original = [request, recorded, followup, second_request]
    repaired = complete_tool_results(original)
    assert len(original) == 4
    assert repaired[:2] == [request, recorded]
    assert repaired[3:5] == [followup, second_request]
    assert [repaired[i].tool_call_id for i in (2, 5)] == ["unknown", "second"]
    for i in (2, 5):
        assert repaired[i].status == "error"
        assert "outcome is unknown" in repaired[i].content
    assert complete_tool_results(repaired) == repaired


def test_complete_history_is_unchanged():
    messages = [
        HumanMessage(content="hello"),
        AIMessage(content="", tool_calls=[{"id": "ok", "name": "bash", "args": {}}]),
        ToolMessage(content="output", tool_call_id="ok"),
        AIMessage(content="done"),
    ]
    assert complete_tool_results(messages) == messages
