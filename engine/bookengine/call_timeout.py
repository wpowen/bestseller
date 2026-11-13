"""Phase-specific wall-clock deadlines for one provider HTTP call."""

import socket
import time


DEFAULT_TOTAL_TIMEOUTS = {"planning": 300.0, "finalization": 180.0}
_PLANNING_PREFIXES = ("plan-q1-", "concept-", "selected-card-")


class CallTimeout(TimeoutError):
    """One HTTP call exceeded its total wall-clock allowance."""


def stage_timeout(stage, configured=None):
    phase = "planning" if stage.startswith(_PLANNING_PREFIXES) else "finalization"
    limits = dict(DEFAULT_TOTAL_TIMEOUTS)
    if isinstance(configured, dict):
        limits.update({key: float(value) for key, value in configured.items() if key in limits and float(value) > 0})
    return phase, limits[phase]


def _set_socket_timeout(response, seconds):
    candidates = [
        getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None),
        getattr(getattr(response, "raw", None), "_sock", None),
    ]
    sock = next((value for value in candidates if value is not None), None)
    if sock is not None and hasattr(sock, "settimeout"):
        sock.settimeout(max(0.001, seconds))


def read_with_deadline(response, total_timeout, idle_timeout):
    """Read a response body while bounding the sum of all successful slow chunks."""
    deadline = time.monotonic() + total_timeout
    read = getattr(response, "read1", None)
    if not callable(read):
        body = response.read()
        if time.monotonic() > deadline:
            raise CallTimeout("call_total_timeout")
        return body
    chunks = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CallTimeout("call_total_timeout")
        _set_socket_timeout(response, min(idle_timeout, remaining))
        try:
            chunk = read(65536)
        except socket.timeout:
            if time.monotonic() >= deadline:
                raise CallTimeout("call_total_timeout") from None
            raise
        if time.monotonic() > deadline:
            raise CallTimeout("call_total_timeout")
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
