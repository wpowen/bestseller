"""Packaging must preserve the web form's distinction between default and user tags."""

# ruff: noqa: RUF001 — exact Chinese production prompt labels are intentional.

import json

import pytest

from bestseller.services import conception

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("user_tags", [[], ["群像"]])
async def test_generated_tags_never_become_explicit_user_requirements(monkeypatch, user_tags):
    captured = {}

    async def fake(*args, **kwargs):
        captured.update(kwargs)
        return {"title_candidates": ["守城人"], "synopsis_candidates": ["守住城门。"]}, []

    monkeypatch.setattr(conception, "_llm_call_json", fake)
    await conception._repair_final_packaging_to_explicit_seed(
        object(),
        object(),
        concept_seed="守城人救出孩子。",
        title="守城人",
        premise="守城人救出孩子。",
        synopsis="守住城门。",
        report={},
        ctx={
            "genre_intent_contract": {
                "tag_source": "user",
                "user_tags": user_tags,
                "default_tags": ["升级流"],
                "tags": ["升级流", *user_tags],
            }
        },
        attempt=1,
        tags=["血脉觉醒", "生成标签"],
        target_platform="fanqie",
    )
    prompt = captured["user_prompt"]
    actual = prompt.split("用户明确选择的标签（空列表表示没有额外标签要求）：", 1)[1].split(
        "\n", 1
    )[0]
    assert json.loads(actual) == user_tags
    assert "不同的具体开局事件" not in prompt
    assert "同一已批准开局的不同侧面" in prompt
    assert "故事分类与生成标签（仅供参考" in prompt
