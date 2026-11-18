"""Bound one effective model response while retaining engine transport waits and recovery."""

from .client import NetworkRetry
from tools.runtime_support import ProjectError


class RhythmCallLimit(ProjectError):
    pass


class SingleResponseClient:
    """Use the real engine stage/request/accounting, refusing a second generated candidate."""

    def __init__(self, client):
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_consumed", False)

    def __getattr__(self, name):
        return getattr(self._client, name)

    def __setattr__(self, name, value):
        setattr(self._client, name, value)

    def stage(self, name, *args, **kwargs):
        consumed = any(
            isinstance(row, dict)
            and row.get("stage") == name
            and row.get("status") in ("ok", "invalid_stage_output", "invalid_output", "truncated")
            for row in getattr(self._client, "calls", [])
        )
        object.__setattr__(self, "_consumed", consumed)
        return type(self._client).stage(self, name, *args, **kwargs)

    def request(self, *args, **kwargs):
        if self._consumed:
            raise RhythmCallLimit("rhythm_source_response_limit")
        try:
            result = self._client.request(*args, **kwargs)
        except NetworkRetry:
            # Transport/queue waits have not supplied a generated candidate.
            raise
        except BaseException:
            object.__setattr__(self, "_consumed", True)
            raise
        object.__setattr__(self, "_consumed", True)
        return result


def ask_once(ask, client, *args, **kwargs):
    kwargs["max_attempts"] = 1
    try:
        return ask(SingleResponseClient(client) if client is not None else None, *args, **kwargs)
    except RhythmCallLimit:
        return dict(rhythm_model_failure="response_limit_exhausted")
