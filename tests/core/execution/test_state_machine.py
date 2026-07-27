"""Task execution state-machine tests."""

import pytest

from enterprise_agent.core.execution.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidTaskTransitionError,
    TaskStatus,
    transition_task_status,
)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (TaskStatus.PENDING, TaskStatus.RUNNING),
        (TaskStatus.PENDING, TaskStatus.CANCELLED),
        (TaskStatus.RUNNING, TaskStatus.WAITING_CONFIRMATION),
        (TaskStatus.WAITING_CONFIRMATION, TaskStatus.RUNNING),
        (TaskStatus.WAITING_CONFIRMATION, TaskStatus.FAILED),
        (TaskStatus.RUNNING, TaskStatus.SUCCEEDED),
        (TaskStatus.RUNNING, TaskStatus.FAILED),
        (TaskStatus.RUNNING, TaskStatus.CANCELLED),
    ],
)
def test_legal_transitions(source, target):
    assert transition_task_status(source, target) == target.value


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
def test_terminal_states_cannot_restart(terminal):
    with pytest.raises(InvalidTaskTransitionError, match="Illegal task status transition"):
        transition_task_status(terminal, "running")


def test_same_transition_is_idempotent_for_graph_replay():
    assert transition_task_status("waiting_confirmation", "waiting_confirmation") == "waiting_confirmation"


def test_missing_legacy_status_is_pending():
    assert transition_task_status(None, "running") == "running"


def test_every_status_has_an_explicit_transition_set():
    assert set(ALLOWED_TRANSITIONS) == set(TaskStatus)
