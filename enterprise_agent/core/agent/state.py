from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph import add_messages


class AgentState(TypedDict):
    """LangGraph代理状态定义

    包含消息历史、用户信息、任务追踪、工具执行状态等。
    """

    # 消息历史（Reducer 会把 dict 规范化为 LangChain Message）
    messages: Annotated[List[Any], add_messages]

    # 用户和会话信息
    session_id: str
    user_id: int
    permissions: List[str]
    execution_mode: str
    current_user_request: str

    # Durable lifecycle for one user-requested task (separate from Session status)
    trace_id: str
    task_status: str
    execution_phase: str
    task_started_at: Optional[str]
    task_finished_at: Optional[str]
    failure_reason: Optional[str]
    continuation_receipt: Optional[Dict[str, Any]]

    # 任务追踪
    current_task: Optional[Dict[str, Any]]
    todos: List[Dict[str, Any]]
    created_task_ids: List[int]

    # 上下文管理
    context_summary: Optional[str]
    context_continuation_active: bool
    project_context_snapshot: str
    token_count: int  # Current active-context estimate; never cumulative model spend
    session_token_count: int  # Cumulative model usage across requests in this chat session
    transcript_path: Optional[str]  # Path to saved transcript after compression

    # 工具执行
    pending_tool_calls: List[Dict[str, Any]]
    tool_results: Dict[str, Any]
    tool_call_stats: Dict[str, int]  # 框架自动统计工具调用次数，避免LLM幻觉
    tool_execution_records: List[Dict[str, Any]]
    tool_call_count: int

    # 工作流控制
    round_count: int  # LLM调用轮次计数，防止无限循环
    task_token_count: int  # Per-task model usage, separate from total context estimate
    should_compress: bool
    context_overflow_recovery_attempts: int
    # Model completion integrity. Provider metadata is stored as scalar state,
    # never copied into replayed AI messages.
    last_model_stop_reason: Optional[str]
    incomplete_response_recovery_attempts: int
    completion_gate_recovery_attempts: int
    task_requires_execution: bool
    should_end: bool
    should_end_after_save: bool  # 标记：文本响应完成后应该结束（由 llm_call_node 设置）

    # Code-change validation gate
    changed_files: List[str]
    validation_results: List[Dict[str, Any]]
    verification_attempts: int
    confirmation_deadline: Optional[str]

    # TodoWrite nag reminder (s03)
    rounds_without_todo: int  # 计数：连续多少轮没有使用TodoWrite
    used_todo_last_round: bool  # 标记：上一轮是否使用了TodoWrite
    has_open_todos: bool  # 标记：是否有未完成的todo项

    # Memory accumulator (task-level storage, not per-round fragments)
    memory_accumulator: Dict[str, Any]  # 当前 trace 内积累的任务内容
    retrieved_memory_context: str  # 本次任务临时注入；不写入聊天消息历史
