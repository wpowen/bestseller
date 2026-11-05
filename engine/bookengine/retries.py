"""Pure backoff shared by disconnects, timeouts, connection errors, 429 and 5xx.

The caller counts all network failures together and records completed waits.
"""

from __future__ import annotations

from math import isfinite


def _count(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("network failures must be a nonnegative integer")


def _seconds(value: float) -> None:
    if type(value) not in (int, float) or value < 0 or (type(value) is float and not isfinite(value)):
        raise ValueError("seconds must be finite nonnegative numbers")


def delay(network_failures: int) -> int:
    """Return 10, 60, 300, then 600 seconds; counts 0 and 1 both use 10."""
    _count(network_failures)
    return (10, 60, 300, 600)[min(max(network_failures - 1, 0), 3)]


def next_network_wait(
    failures: int,
    waited: float,
    retry_after: float = 0,
) -> float | None:
    """Wait indefinitely across failures; individual delays never exceed 600s."""
    _count(failures)
    _seconds(waited)
    _seconds(retry_after)
    return min(600, max(delay(failures), retry_after))
