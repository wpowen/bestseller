"""A missing review is unknown, never evidence that a repair improved a card."""

import json

import pytest

from bestseller.services import concept_tournament as ct

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("layer", ["story_layer", "expansion_editor"])
@pytest.mark.parametrize("evidence", ["zero", "one", "missing_axes", "valid", "unchanged"])
async def test_repair_is_adopted_only_after_complete_improved_rechecks(
    monkeypatch,
    layer,
    evidence,
):
    monkeypatch.setattr(ct, "_premise_card_audit", lambda card: [])
    axes = ct._STORY_LAYER_AXES if layer == "story_layer" else ct._EXPANSION_EDITOR_AXES
    rechecks = 0

    async def generate(system, user):
        if '"ideas"' in user:
            return json.dumps({"ideas": [{"seed": "少年接管山门灵田。"}]}), None
        revised = "按方向修正" in user
        return json.dumps({"current_goal": "修复卡" if revised else "原卡"}), None

    async def judge(system, user):
        nonlocal rechecks
        if axes[0] not in user:
            return "{}", None
        if "修复卡" in user:
            rechecks += 1
            if evidence == "zero" or (evidence == "one" and rechecks > 1):
                return "invalid JSON", None
            if evidence == "missing_axes":
                return "{}", None
            payload = {axis: {"pass": True} for axis in axes}
            if evidence == "unchanged":
                payload[axes[0]]["pass"] = False
        else:
            payload = {axis: {"pass": axis != axes[0]} for axis in axes}
        return json.dumps(payload), None

    result = await ct.run_concept_tournament(
        None,
        None,
        genre="东方玄幻",
        sub_genre="东方玄幻",
        chapter_count=50,
        generator=generate,
        judge=judge,
        premise_judge=judge,
        config={
            "candidate_prompt_mode": "engine_first",
            "n_candidates": 2,
            "raw_idea_pool_topup_calls": 0,
            "story_layer_votes": 2,
            "story_layer_judge_enabled": layer == "story_layer",
            "expansion_editor_judge_enabled": layer == "expansion_editor",
        },
    )
    receipt = result.premise_cards[0]
    assert result.premise_review_fallback["reason"] == "batch_verdict_contract_incomplete"
    assert result.premise_review_fallback["card_count"] == 1
    adopted = evidence == "valid"
    assert receipt[layer]["revised"] is adopted
    assert receipt["card"]["current_goal"] == ("修复卡" if adopted else "原卡")
    if evidence in {"zero", "one", "missing_axes"}:
        assert receipt[layer]["revise_rejected_reason"] == "recheck_insufficient_evidence"
