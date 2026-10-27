"""Bound active streams by the already configured role lifetime and idle gap."""
import asyncio
import time
from contextlib import suppress


def conception_stream_deadlines(role_seconds, conception_seconds, elapsed_seconds, *, active_seconds=None):
    # SDK read timeout remains configured. A live complete-production stream
    # can use the finite existing planner lifetime; scope deadline wins outside.
    if active_seconds is not None:
        idle = min(float(role_seconds), float(conception_seconds or role_seconds)) + 5.0
        return idle, max(.001, float(active_seconds) + 5.0 - elapsed_seconds)
    if not conception_seconds or role_seconds <= conception_seconds:
        return None
    return float(conception_seconds) + 5.0, max(.001, float(role_seconds) + 5.0 - elapsed_seconds)


async def bounded_stream(response, *, idle_seconds, total_seconds, progress, on_poll=None,
                         clock=time.monotonic):
    deadline = clock() + total_seconds
    idle_deadline = clock() + idle_seconds
    next_check = clock()
    iterator = response.__aiter__()
    while True:
        remaining = deadline - clock()
        idle_remaining = idle_deadline - clock()
        if remaining <= 0:
            raise TimeoutError('minimax_stream_total_timeout')
        if idle_remaining <= 0:
            raise TimeoutError('minimax_stream_idle_timeout')
        pending = asyncio.ensure_future(iterator.__anext__())
        try:
            while not pending.done():
                if on_poll is not None and clock() >= next_check:
                    await on_poll()
                    next_check = clock() + 1.0
                remaining = deadline - clock()
                idle_remaining = idle_deadline - clock()
                if remaining <= 0:
                    raise TimeoutError('minimax_stream_total_timeout')
                if idle_remaining <= 0:
                    raise TimeoutError('minimax_stream_idle_timeout')
                await asyncio.wait({pending}, timeout=min(.25, remaining, idle_remaining))
            chunk = pending.result()
        except StopAsyncIteration:
            return
        finally:
            if not pending.done():
                pending.cancel()
                with suppress(asyncio.CancelledError):
                    await pending
        if progress(chunk):
            idle_deadline = clock() + idle_seconds
        yield chunk
