"""Propagate cancellation of async tool invocations to synchronous executor threads."""

from contextvars import ContextVar
from threading import Event

current_execution_stop: ContextVar[Event | None] = ContextVar("execution_stop", default=None)
