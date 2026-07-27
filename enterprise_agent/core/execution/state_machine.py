"""Validated execution state machine for one user-requested Agent task.

Conversation session lifecycle is intentionally separate. A session can remain
active while many task runs transition independently through this state
machine and are checkpointed in Redis/recorded by the trace layer.
"""

from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_CONFIRMATION = "waiting_confirmation"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionPhase(str, Enum):
    PARSING = "parsing"
    PLANNING = "planning"
    EXECUTING = "executing"
    CHECKPOINTING = "checkpointing"
    VALIDATING = "validating"
    SUMMARIZING = "summarizing"


TERMINAL_STATUSES = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}

ALLOWED_TRANSITIONS = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.WAITING_CONFIRMATION,
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.WAITING_CONFIRMATION: {
        TaskStatus.RUNNING,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}


class InvalidTaskTransitionError(ValueError):
    """Raised when code attempts an illegal task lifecycle transition."""


def coerce_task_status(value: TaskStatus | str | None) -> TaskStatus:
    """Convert stored/string status to the canonical enum.

    Missing status is treated as pending to keep old Redis checkpoints
    backward-compatible after the new fields are introduced.
    """
    if value is None:
        return TaskStatus.PENDING
    if isinstance(value, TaskStatus):
        return value
    try:
        return TaskStatus(value)
    except ValueError as exc:
        raise InvalidTaskTransitionError(f"Unknown task status: {value!r}") from exc


def transition_task_status(
    current: TaskStatus | str | None,
    target: TaskStatus | str,
) -> str:
    """Validate and return a task status transition.

    Re-applying the same status is idempotent. This matters when a LangGraph
    node is replayed after an interrupt/checkpoint recovery.
    """
    source = coerce_task_status(current)
    destination = coerce_task_status(target)

    if source == destination:
        return destination.value
    if destination not in ALLOWED_TRANSITIONS[source]:
        raise InvalidTaskTransitionError(
            f"Illegal task status transition: {source.value} -> {destination.value}"
        )
    return destination.value
