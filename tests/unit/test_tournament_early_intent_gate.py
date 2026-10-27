"""Reject explicit option contradictions before paying for optional reviews."""

import json

import pytest

from bestseller.services import concept_tournament as ct

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_raw_supporting_story_fields_are_checked_before_expansion():
    calls = []

    async def generate(system, user):
        calls.append(user)
        return json.dumps(
            {
                "ideas": [
                    {
                        "lane": "职业处境",
                        "seed": "少年接管山门灵田。",
                        "graft": "尸体旁的尸首散发阴冷气息。",
                    }
                ]
            }
        ), None

    result = await ct.run_concept_tournament(
        None,
        None,
        genre="东方玄幻",
        sub_genre="东方玄幻",
        chapter_count=50,
        tone_preference="light",
        generator=generate,
        config={
            "candidate_prompt_mode": "engine_first",
            "n_candidates": 2,
            "raw_idea_pool_topup_calls": 0,
        },
    )
    assert result.raw_ideas == []
    assert any("原始创意反污染" in r["reason"] for r in result.engine_rejections)
    assert len(calls) == 1


async def test_polluted_complete_card_skips_story_editor_reviews(monkeypatch):
    reviewed = []
    # This test isolates gate ordering; completeness itself has separate tests.
    monkeypatch.setattr(ct, "_premise_card_audit", lambda card: [])

    async def generate(system, user):
        if '"ideas"' in user:
            return json.dumps({"ideas": [{"seed": "少年接管山门灵田。"}]}), None
        return json.dumps({"opening_crisis": "尸体旁的尸首散发阴冷气息。"}), None

    async def judge(system, user):
        reviewed.append(user)
        return "{}", None

    result = await ct.run_concept_tournament(
        None,
        None,
        genre="东方玄幻",
        sub_genre="东方玄幻",
        chapter_count=50,
        tone_preference="light",
        generator=generate,
        judge=judge,
        premise_judge=judge,
        config={
            "candidate_prompt_mode": "engine_first",
            "n_candidates": 2,
            "raw_idea_pool_topup_calls": 0,
        },
    )
    assert reviewed == []
    assert result.winner is None
    assert result.premise_cards[0]["early_intent_gate"]["passed"] is False
