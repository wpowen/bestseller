"""Persistent shared rolling-window request reservations."""

import fcntl
import json
import math
import os
import time


def acquire(path, input_tokens, output_tokens, rpm=200, tpm=10000000):
    """Block until reserved; return None after flushing the reservation to disk.

    All users of a quota must share this path and retain its inode. JSON holds
    [[Unix timestamp, input_tokens + output_tokens], ...], one row per request.
    Reservations expire after 60 seconds, including those for failed requests.
    rpm must be positive; other counts may be zero. Invalid arguments raise
    ValueError, corrupt state raises a generic RuntimeError without its content.
    An empty file initializes state. No request data is accepted or stored.
    """
    values = (input_tokens, output_tokens, rpm, tpm)
    if any(type(value) is not int or value < 0 for value in values) or rpm == 0:
        raise ValueError("Invalid rate limiter parameters")
    tokens = input_tokens + output_tokens
    if tokens > tpm:
        raise ValueError("Request exceeds token limit")

    # No O_TRUNC or O_APPEND: update the locked inode, then truncate the tail.
    with os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+", encoding="utf-8") as state:
        while True:
            fcntl.flock(state, fcntl.LOCK_EX)
            try:
                state.seek(0)
                try:
                    raw = state.read()
                    records = json.loads(raw) if raw else []
                    if type(records) is not list:
                        raise ValueError
                    for row in records:
                        if (
                            type(row) is not list
                            or len(row) != 2
                            or type(row[0]) not in (int, float)
                            or not math.isfinite(row[0])
                            or row[0] < 0
                            or type(row[1]) is not int
                            or row[1] < 0
                        ):
                            raise ValueError
                except (ValueError, TypeError, OverflowError, RecursionError):
                    raise RuntimeError("Invalid rate limiter state") from None

                now = time.time()
                records = [row for row in records if row[0] > now - 60]
                if len(records) < rpm and sum(row[1] for row in records) + tokens <= tpm:
                    records.append([now, tokens])
                    payload = json.dumps(records, separators=(",", ":"), allow_nan=False)
                    state.seek(0)
                    state.write(payload)
                    state.flush()
                    state.truncate()
                    os.fsync(state.fileno())
                    return None
                delay = min(30, min(row[0] for row in records) + 60 - now)
            finally:
                fcntl.flock(state, fcntl.LOCK_UN)
            time.sleep(delay)
