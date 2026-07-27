"""Subagent tool for delegating work to specialized agents.

Provides task tool for spawning subagents with limited tool access
to perform isolated exploration or execution work.

Supports multi-provider: Anthropic, GLM, DeepSeek, OpenAI.
"""

from typing import Dict, Optional

from langchain_core.tools import tool

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.llm_factory import get_llm

# Available agent types and their tool sets
AGENT_TYPES = {
    "Explore": ["bash", "read_file"],
    "general-purpose": ["bash", "read_file", "write_file", "edit_file"],
    # A tool-free, isolated model context for planning, review, writing, and
    # other specialist opinions that should not mutate the workspace.
    "specialist": [],
}

# System prompts for each agent type
SUBAGENT_SYSTEM_PROMPTS = {
    "Explore": """You are an exploration agent. Your job is to quickly search and understand codebases.

## Capabilities
- Run shell commands (read-only: grep, find, ls, cat, etc.)
- Read files to understand their content

## Guidelines
- Be fast and focused — find the answer and report back
- Use grep/find to locate relevant files before reading them
- Summarize your findings clearly and concisely
- Do NOT modify any files — you are read-only""",

    "general-purpose": """You are a general-purpose agent capable of reading and writing code.

## Capabilities
- Run shell commands
- Read, write, and edit files

## Guidelines
- Complete the task described in the prompt
- Be careful with file modifications — read before editing
- Report a clear summary of what you did
- If you encounter errors, try to fix them before reporting failure""",

    "specialist": """You are an independent specialist subagent working in an isolated context.

## Capabilities
- Analyze the delegated prompt from the requested professional role
- Produce concrete plans, drafts, critiques, or recommendations
- Return your work to the lead agent for synthesis

## Guidelines
- Stay within the delegated role and task
- Do not claim to have used tools, read files, or contacted other agents
- Make the output self-contained and specific
- Clearly identify assumptions or uncertainty""",
}


def _execute_subagent_tool(tool_name: str, tool_input: Dict) -> str:
    """Execute a tool call within subagent context.

    Uses the actual tool implementations from other modules.
    """
    from enterprise_agent.core.agent.tools.file_ops import edit_file, read_file, write_file
    from enterprise_agent.core.agent.tools.shell import bash

    tool_map = {
        "bash": bash,
        "read_file": read_file,
        "write_file": write_file,
        "edit_file": edit_file,
    }

    tool = tool_map.get(tool_name)
    if not tool:
        return f"Unknown tool: {tool_name}"

    # Execute the tool
    try:
        result = tool.invoke(tool_input)
        return str(result)[:settings.TOOL_OUTPUT_MAX_CHARS]
    except Exception as e:
        return f"Error: {e}"


async def _run_subagent_async(prompt: str, agent_type: str) -> str:
    """Run subagent asynchronously using LangChain.

    Supports multi-provider via LLM factory.
    """
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    # Validate agent type
    if agent_type not in AGENT_TYPES:
        return f"Error: Unknown agent_type '{agent_type}'. Available: {', '.join(AGENT_TYPES.keys())}"

    # Get tool names for this agent type
    tool_names = AGENT_TYPES[agent_type]

    # Build LangChain tools
    from langchain_core.tools import Tool
    tools = []
    for name in tool_names:
        if name == "bash":
            tools.append(Tool(
                name="bash",
                func=lambda cmd: _execute_subagent_tool("bash", {"command": cmd}),
                description="Run shell command",
            ))
        elif name == "read_file":
            tools.append(Tool(
                name="read_file",
                func=lambda path: _execute_subagent_tool("read_file", {"path": path}),
                description="Read file",
            ))
        elif name == "write_file":
            tools.append(Tool(
                name="write_file",
                func=lambda args: _execute_subagent_tool("write_file", args),
                description="Write file",
            ))
        elif name == "edit_file":
            tools.append(Tool(
                name="edit_file",
                func=lambda args: _execute_subagent_tool("edit_file", args),
                description="Edit file",
            ))

    # Get LLM and bind tools
    try:
        llm = get_llm()
        llm_with_tools = llm.bind_tools(tools) if tools else llm
    except Exception as e:
        return f"Error initializing LLM: {e}"

    # Subagent messages with system prompt
    system_prompt = SUBAGENT_SYSTEM_PROMPTS.get(agent_type, "")
    messages = []
    if system_prompt:
        messages.append(SystemMessage(content=system_prompt))
    messages.append(HumanMessage(content=prompt))

    # Run subagent loop
    for _ in range(settings.SUBAGENT_MAX_ROUNDS):
        try:
            response = await llm_with_tools.ainvoke(messages)
        except Exception as e:
            return f"Subagent error: {e}"

        messages.append(response)

        # Check if done (no tool calls)
        if not hasattr(response, "tool_calls") or not response.tool_calls:
            break

        # Execute tool calls
        tool_results = []
        for tool_call in response.tool_calls:
            tool_name = tool_call.get("name")
            tool_args = tool_call.get("args", {})
            tool_id = tool_call.get("id", "")

            output = _execute_subagent_tool(tool_name, tool_args)
            tool_results.append(ToolMessage(content=output, tool_call_id=tool_id))

        messages.extend(tool_results)

    # Extract final summary
    if messages and len(messages) > 0:
        last_msg = messages[-1]
        if hasattr(last_msg, "content"):
            return str(last_msg.content) or "(no summary)"

    return "(subagent failed)"


@tool
async def task(prompt: str, agent_type: Optional[str] = "Explore") -> str:
    """Delegate work to a subagent for isolated execution. Returns a summary.

    Use when: (1) Search/explore large codebase (Explore agent, read-only)
              (2) Self-contained implementation task (general-purpose, read/write)
              (3) 3+ independent tasks — spawn multiple task() calls in parallel

    Examples:
        - Search patterns: task("Find database connection patterns", "Explore")
        - Implement feature: task("Add JWT auth to Flask app", "general-purpose")

    Args:
        prompt: Task description (be specific about what to do)
        agent_type: 'Explore' (read-only) or 'general-purpose' (read/write)

    Returns:
        Summary of what the subagent did and its findings
    """
    return await _run_subagent_async(prompt, agent_type or "Explore")


@tool
async def delegate_task(role: str, prompt: str) -> str:
    """Delegate analysis or creative work to a real isolated specialist subagent.

    This starts a separate model context and returns its result to the lead
    Agent. Use it only in explicit Multi-Agent mode. It is suitable for roles
    such as planner, writer, reviewer, security reviewer, or test strategist.
    It does not modify files or execute shell commands.

    Args:
        role: Specific professional role for the independent subagent
        prompt: Self-contained task, including any context the specialist needs

    Returns:
        The specialist subagent's real model response
    """
    role_name = role.strip() or "specialist"
    delegated_prompt = (
        f"Your delegated role is: {role_name}\n\n"
        f"Complete this task and return the result to the lead agent:\n{prompt}"
    )
    return await _run_subagent_async(delegated_prompt, "specialist")
