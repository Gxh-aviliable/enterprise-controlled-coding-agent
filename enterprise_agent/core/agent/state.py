from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph import add_messages


class AgentState(TypedDict):
    """LangGraph代理状态定义

    包含消息历史、用户信息、任务追踪、工具执行状态等。
    """

    # 消息历史（LangGraph自动合并）
    messages: Annotated[List[Dict], add_messages]

    # 用户和会话信息
    session_id: str
    user_id: int

    # 任务追踪
    current_task: Optional[Dict[str, Any]]
    todos: List[Dict[str, Any]]

    # 上下文管理
    context_summary: Optional[str]
    token_count: int
    transcript_path: Optional[str]  # Path to saved transcript after compression

    # 工具执行
    pending_tool_calls: List[Dict[str, Any]]
    tool_results: Dict[str, Any]
    tool_call_stats: Dict[str, int]  # 框架自动统计工具调用次数，避免LLM幻觉

    # 工作流控制
    round_count: int  # LLM调用轮次计数，防止无限循环
    should_compress: bool
    should_end: bool
    should_end_after_save: bool  # 标记：文本响应完成后应该结束（由 llm_call_node 设置）

    # TodoWrite nag reminder (s03)
    rounds_without_todo: int  # 计数：连续多少轮没有使用TodoWrite
    used_todo_last_round: bool  # 标记：上一轮是否使用了TodoWrite
    has_open_todos: bool  # 标记：是否有未完成的todo项

    # Memory accumulator (task-level storage, not per-round fragments)
    memory_accumulator: Dict[str, Any]  # 跨轮积累的任务内容（user_request, assistant_responses, tool_actions, etc.）
    pending_memory_flush: bool  # 标记：是否需要将积累内容写入 Chroma