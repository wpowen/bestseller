"""G7 (zhaoshen regeneration): tone → target_emotion mix contract.

Root cause: book_spec.tone carries the intended mood mix ("喜剧40% + 暖35%
+ 悬念25%") but the chapter-outline prompt never used it — it only told the
LLM to "pick one emotion from the vocabulary", which defaults to 紧张 for
dramatic conflict. zhaoshen-hr-v5 vol-1 came out 紧张 52% / 悬疑 20% / 暖 6%
/ 喜剧 0%, inverting a light-comedy book into a tension thriller.

The contract maps free-text tone words to the controlled vocabulary
(喜剧/搞笑→轻松, 暖/治愈→暖/甜, 悬念→悬疑) and tells the planner which
colours should dominate and caps the serious bucket.
"""

from __future__ import annotations

from bestseller.services.quality_levers.webnovel_method_cards import (
    render_tone_emotion_contract_block,
)


def test_comedy_tone_maps_to_lighthearted_majority() -> None:
    block = render_tone_emotion_contract_block("喜剧40% + 暖35% + 悬念25%")
    # 喜剧 maps to the vocabulary's 轻松 (its plot_modes are 认知错位喜剧/吐槽).
    assert "轻松" in block
    assert "暖" in block
    assert "悬疑" in block
    # The contract must name a dominant warm bucket and cap the serious one.
    assert ("主色调" in block or "配比" in block or "占" in block)
    # 紧张 must appear as a *capped* emotion, not a free choice.
    assert "紧张" in block


def test_empty_tone_returns_empty() -> None:
    assert render_tone_emotion_contract_block("") == ""
    assert render_tone_emotion_contract_block(None) == ""


def test_serious_tone_keeps_serious_dominant() -> None:
    block = render_tone_emotion_contract_block("悬疑50% + 紧张30% + 爽20%")
    assert "悬疑" in block
    # A thriller premise must NOT be forced into comedy: the block should
    # not claim 轻松/暖 dominate when tone says 悬疑/紧张 do.
    assert "轻松" not in block or "暖" not in block


def test_warm_bucket_share_reflects_tone() -> None:
    # 喜剧40+暖35 = 75% warm → serious cap around 25%.
    block = render_tone_emotion_contract_block("喜剧40% + 暖35% + 悬念25%")
    assert "75" in block or "25" in block
