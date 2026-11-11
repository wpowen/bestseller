"""Instruction 59 rework: repair prompts must retain exact source material."""

import pytest

from bookengine.promise_io import precise_ask
from bookengine.q1_extract import ask


class CaptureClient:
    context_limit = 10_000
    prefix = ""
    clip_context = True

    def __init__(self):
        self.prompts = []

    def stage(self, name, prompt, schema=None, **kwargs):
        self.prompts.append((name, prompt, schema, kwargs))
        return {"edits": []}


def test_real_ask_packing_keeps_target_original_and_protected_literals():
    client = CaptureClient()
    original = "矿车终于被撬离轨道，两根轨闩接连弹起。"
    protected = ["顾砚", "矿车", "两根轨闩"]

    precise_ask(
        ask,
        client,
        "r2-ch9-explanation-density-repair",
        "只改给出的目标段。",
        [
            ("只可修改的解释段", [{"id": 3, "original": original, "category": "causality"}]),
            ("必须逐字保留的实体、术语与数字", protected),
        ],
        '{"edits":[]}',
        required_sources=[original],
    )

    assert len(client.prompts) == 1
    prompt = client.prompts[0][1]
    assert original in prompt
    assert all(value in prompt for value in protected)


def test_repair_with_missing_target_source_is_skipped_before_request():
    client = CaptureClient()

    with pytest.warns(RuntimeWarning, match="empty_sources"):
        result = precise_ask(
            ask,
            client,
            "r2-ch9-explanation-density-repair",
            "只改给出的目标段。",
            [("只可修改的解释段", [])],
            '{"edits":[]}',
            required_sources=["本应出现的逐字原段"],
        )

    assert result == {"status": "skipped", "reason": "empty_sources"}
    assert client.prompts == []
