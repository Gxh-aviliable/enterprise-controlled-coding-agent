"""Request-scoped graph streaming with bounded buffering and cancellation ticks."""
import asyncio
from contextlib import aclosing, suppress

import anyio


async def stream_with_heartbeats(source, *, interval=1.0):
    """Keep one producer context; never restart/replay the graph on reconnect."""
    queue = asyncio.Queue(maxsize=1)

    async def produce():
        try:
            async with aclosing(source):
                async for event in source:
                    await queue.put((True, event))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await queue.put((False, exc))
        else:
            await queue.put((False, None))

    producer = asyncio.create_task(produce())
    try:
        while True:
            try:
                available, value = await asyncio.wait_for(queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                yield None
                continue
            if not available:
                if value is not None:
                    raise value
                return
            yield value
    finally:
        producer.cancel()
        with anyio.move_on_after(10, shield=True):
            with suppress(asyncio.CancelledError):
                await producer
