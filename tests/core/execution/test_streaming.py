import asyncio
from contextlib import aclosing

import pytest

from enterprise_agent.core.execution.streaming import stream_with_heartbeats


async def test_silent_graph_ticks_and_closes_without_reexecuting():
    closed = asyncio.Event()
    calls = []

    async def source():
        try:
            calls.append('started')
            yield ('updates', {'step': 1})
            await asyncio.Event().wait()
        finally:
            closed.set()

    async with aclosing(stream_with_heartbeats(source(), interval=0.01)) as stream:
        assert await anext(stream) == ('updates', {'step': 1})
        assert await anext(stream) is None
    assert closed.is_set()
    assert calls == ['started']


async def test_stream_propagates_failure_and_preserves_event_order():
    async def source():
        yield ('messages', 'first')
        yield ('messages', 'second')
        raise ValueError('fixture failure')

    received = []
    with pytest.raises(ValueError, match='fixture failure'):
        async for event in stream_with_heartbeats(source()):
            received.append(event)
    assert received == [('messages', 'first'), ('messages', 'second')]
