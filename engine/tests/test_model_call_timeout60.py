"""Instruction 60 C6: one HTTP call has a phase-specific wall-clock deadline."""

import json
from unittest.mock import patch

import pytest

from bookengine.call_timeout import CallTimeout, stage_timeout
from bookengine.client import Client, NetworkRetry


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class SlowReply:
    def __init__(self, value, clock, step):
        self.data = json.dumps(value).encode()
        self.clock = clock
        self.step = step
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read1(self, _size):
        self.clock.value += self.step
        if self.offset >= len(self.data):
            return b""
        chunk = self.data[self.offset : self.offset + 8]
        self.offset += len(chunk)
        return chunk


class FastReply:
    def __init__(self, value):
        self.data = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.data


def answer(text="完成"):
    return {
        "model": "offline",
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def client(tmp_path, limits):
    with patch("bookengine.client.credentials", return_value=("https://fake.invalid/v1", "fake-key")):
        return Client(tmp_path, tmp_path / "unused.env", "", total_timeouts=limits)


def test_c6_planning_and_finalization_have_separate_total_deadlines():
    assert stage_timeout("plan-q1-rhythm-skeleton-11-20-growth2") == ("planning", 300.0)
    assert stage_timeout("r2-ch15-conflict-repair") == ("finalization", 180.0)


def test_c6_slow_chunked_planning_call_is_timeout_then_uses_existing_network_retry(tmp_path, monkeypatch):
    clock = Clock()
    c = client(tmp_path, {"planning": 5.0, "finalization": 2.0})
    slow = SlowReply(answer(), clock, 2.0)

    def clear_wait(_name, state, _path):
        state.pop("delay", None)
        state.pop("not_before", None)

    monkeypatch.setattr("bookengine.call_timeout.time.monotonic", clock)
    with (
        patch("urllib.request.urlopen", side_effect=[slow, FastReply(answer())]),
        patch.object(c, "wait_network", clear_wait),
    ):
        assert c.stage("plan-q1-rhythm-skeleton-11-20-growth2", "规划") == "完成"

    assert [row["status"] for row in c.calls] == ["timeout", "ok"]
    assert c.calls[0]["timeout_phase"] == "planning" and c.calls[0]["total_timeout_seconds"] == 5.0
    assert c.calls[1]["attempt"] == 2
    assert not any(row["status"] in ("invalid_output", "invalid_stage_output") for row in c.calls)
    retry = json.loads((tmp_path / "steps/plan-q1-rhythm-skeleton-11-20-growth2.retry.json").read_text())
    assert retry["failures"] == 1


def test_c6_slow_finalization_stream_uses_shorter_limit_and_records_timeout(tmp_path, monkeypatch):
    clock = Clock()
    c = client(tmp_path, {"planning": 5.0, "finalization": 2.0})
    monkeypatch.setattr("bookengine.call_timeout.time.monotonic", clock)

    with patch("urllib.request.urlopen", return_value=SlowReply(answer(), clock, 1.1)):
        with pytest.raises(NetworkRetry, match="call_timeout"):
            c.request("r2-ch15-conflict-repair", "定向修复", 1000, "low", 1)

    assert c.calls[0]["status"] == "timeout"
    assert c.calls[0]["timeout_phase"] == "finalization"
    assert c.calls[0]["total_timeout_seconds"] == 2.0
    assert c.calls[0]["error_type"] == CallTimeout.__name__
