"""Keep interrupted tool exchanges valid without claiming they succeeded."""

from langchain_core.messages import AIMessage, ToolMessage


def complete_tool_results(messages):
    """Fill missing results before the next turn, preserving recorded results.

    This repairs model input/terminal history only; it never executes a tool.
    A missing result cannot tell us whether side effects already occurred.
    """
    result, pending = [], {}

    def close_pending():
        for call_id, name in pending.items():
            result.append(ToolMessage(
                content=(
                    "No recorded result is available: the previous execution was interrupted "
                    "before this tool result was saved. Execution outcome is unknown. "
                    "Inspect workspace and external state before retrying; do not assume "
                    "the tool succeeded or that it had no side effects."
                ),
                tool_call_id=call_id,
                name=name,
                status="error",
                id=f"unavailable-tool-result:{call_id}",
            ))
        pending.clear()

    for message in messages:
        if not isinstance(message, ToolMessage):
            close_pending()
        result.append(message)
        if isinstance(message, AIMessage):
            pending.update({call["id"]: call["name"] for call in message.tool_calls})
        elif isinstance(message, ToolMessage):
            pending.pop(message.tool_call_id, None)
    close_pending()
    return result
