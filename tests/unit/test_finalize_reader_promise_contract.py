"""Finalization must preserve story-specific appeal on normal and repair paths."""

# ruff: noqa: RUF001 — Chinese fixtures are intentional.
import copy

import pytest

from bestseller.services import conception

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "genre,sub_genre,promise",
    [
        ("都市", "日常经营", "一起做事逐渐建立信任"),
        ("悬疑", "推理", "凭线索逐步找出隐瞒的真相"),
        ("言情", "现代言情", "两人通过选择改变彼此的关系"),
        ("玄幻", "东方玄幻", "通过有代价的成长赢得自主权"),
    ],
)
@pytest.mark.parametrize("is_en", [False, True])
@pytest.mark.parametrize("retry", [False, True])
def test_finalize_preserves_appeal_without_universal_escalation(
    genre, sub_genre, promise, is_en, retry
):
    ctx = {
        "genre": genre,
        "sub_genre": sub_genre,
        "chapter_count": 20,
        "description": promise,
        "user_hints": {},
        "high_concept": {"emotional_promise": promise},
    }
    original = copy.deepcopy(ctx)
    if retry:
        prompt = conception._pollution_retry_finalize_prompt(ctx, genre_profile=None, is_en=is_en)
    else:
        fn = conception._finalize_user_prompt_en if is_en else conception._finalize_user_prompt
        prompt = fn(ctx, {}, {}, {}, {})
    assert ctx == original
    assert promise in prompt
    for obsolete in (
        "爽点会持续升级",
        "最强的高唤起情绪事件",
        "主角身份反差 + 开局冲突事件 + 失败代价",
        "真榜单只有 6%",
        "End with a cliffhanger question",
    ):
        assert obsolete not in prompt
    if is_en:
        assert "Do not invent deadlines" in prompt
        assert "different choices and lasting consequences" in prompt
    else:
        assert "不得为了文案制造期限" in prompt
        assert "不同选择及其持续后果" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "seed",
    [
        "想看温暖的小镇生活，不要死人",
        "女木匠林禾回乡修桥",
        "雨天，两个不熟悉的人慢慢建立信任",
    ],
)
@pytest.mark.parametrize("attempt", [1, 3])
async def test_seed_repair_allows_completion_without_overriding_explicit_facts(
    monkeypatch, seed, attempt
):
    captured = {}

    async def fake_call(*args, **kwargs):
        captured.update(kwargs)
        return {"title": "修复方案"}, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_call)
    await conception._repair_final_result_to_explicit_seed(
        None,
        None,
        concept_seed=seed,
        final_result={},
        report={"hard_conflicts": []},
        ctx={"genre": "都市", "sub_genre": "日常经营", "language": "zh-CN"},
        attempt=attempt,
        max_attempts=3,
    )
    prompt = captured["system_prompt"] + captured["user_prompt"]
    assert seed in prompt
    assert "明确事实和禁止项必须保留" in prompt
    assert "未指定的人物、目标和事件可由系统补全" in prompt
    assert "premise 必须直接包含用户原文" not in prompt
    assert "任何新增事实只能由原文" not in prompt
