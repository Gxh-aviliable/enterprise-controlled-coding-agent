from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph import add_messages


class SessionState(TypedDict, total=False):
    """Identity and configuration that survive across task traces."""

    session_id: str
    user_id: int
    permissions: List[str]
    authorization_source: str
    execution_mode: str
    current_user_request: str
    skill_snapshot: Optional[Dict[str, Any]]


class LifecycleState(TypedDict, total=False):
    """Durable lifecycle for one user-requested task."""

    trace_id: str
    task_status: str
    execution_phase: str
    task_started_at: Optional[str]
    task_finished_at: Optional[str]
    failure_reason: Optional[str]
    continuation_receipt: Optional[Dict[str, Any]]


class TaskTrackingState(TypedDict, total=False):
    """Trace-scoped work items and persistent task handles."""

    current_task: Optional[Dict[str, Any]]
    todos: List[Dict[str, Any]]
    created_task_ids: List[int]


class ContextState(TypedDict, total=False):
    """Active-context estimates and compression continuation evidence."""

    context_summary: Optional[str]
    context_continuation_active: bool
    project_context_snapshot: str
    token_count: int  # Current active-context estimate; never cumulative model spend
    session_token_count: int  # Cumulative model usage across requests in this chat session
    transcript_path: Optional[str]  # Path to saved transcript after compression
    artifact_manifest_path: Optional[str]  # Transcript's artifact dependency manifest


class ToolState(TypedDict, total=False):
    """Current tool batch plus trace-level execution evidence."""

    pending_tool_calls: List[Dict[str, Any]]
    child_tasks: Dict[str, Dict[str, Any]]  # Trace-scoped structured terminal receipts
    tool_results: Dict[str, Any]
    tool_call_stats: Dict[str, int]  # 框架自动统计工具调用次数，避免LLM幻觉
    tool_execution_records: List[Dict[str, Any]]
    tool_call_count: int
    artifact_read_state: Dict[str, Dict[str, Any]]


class WorkflowControlState(TypedDict, total=False):
    """Routing flags, budgets, and bounded model-recovery counters."""

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
    should_end_after_save: bool  # 标记：文本响应完成后应该结束（由 llm_call_node 设置）


class ValidationState(TypedDict, total=False):
    """Code-change verification and HITL confirmation state."""

    changed_files: List[str]
    validation_results: List[Dict[str, Any]]
    change_receipts: List[Dict[str, Any]]
    pending_execution: bool
    validation_satisfied: bool
    validation_scope: List[str]
    behavioral_validation: bool
    verification_attempts: int
    confirmation_deadline: Optional[str]
    confirmation_scope: Optional[str]


class TodoReminderState(TypedDict, total=False):
    """TodoWrite reminder counters maintained across model rounds."""

    rounds_without_todo: int  # 计数：连续多少轮没有使用TodoWrite
    used_todo_last_round: bool  # 标记：上一轮是否使用了TodoWrite
    has_open_todos: bool  # 标记：是否有未完成的todo项


class MemoryState(TypedDict, total=False):
    """Ephemeral recall and task-level durable-memory admission input."""

    memory_accumulator: Dict[str, Any]  # 当前 trace 内积累的任务内容
    retrieved_memory_context: str  # 本次任务临时注入；不写入聊天消息历史
    memory_query_mode: str  # semantic 普通回忆；listing 显式分页清单


class AgentState(
    SessionState,
    LifecycleState,
    TaskTrackingState,
    ContextState,
    ToolState,
    WorkflowControlState,
    ValidationState,
    TodoReminderState,
    MemoryState,
):
    """Complete LangGraph state with flat runtime keys grouped by responsibility.

    Only ``messages`` is required at type level. Nodes intentionally accept and
    return partial state updates, while Redis checkpoints may come from older
    schema versions that do not contain every optional key. Keeping the runtime
    keys flat avoids nested-dict replacement semantics and checkpoint migration.
    """

    # Reducer normalizes dict payloads into LangChain messages and appends them.
    messages: Annotated[List[Any], add_messages]
