"""Tournament domain diversity + creation-option fit axes (2026-08-09).

Live probe with the ledger book's exact params (东方玄幻/男频/light/minimal):
the model spent 62% (25/40) of the raw-idea pool on one domain family and
ranking kept the share (65% of selected) — 3 of 4 same-parameter conceptions
crowned the same family. The prompts were verified clean of that family's
vocabulary, so this is the model's own prior collapsing, and the counter must
be structural: a diversity requirement stated by category (never by token), a
judge-authored domain label, a deterministic one-slot-per-domain cap, and
floored fit axes for the tone/cost options the user explicitly ticked.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from bestseller.services import concept_tournament as ct

pytestmark = pytest.mark.unit


def _rank_item(index: int, domain: str, score: float, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "index": index,
        "domain": domain,
        "freshness": score,
        "click_seed": score,
        "character_logic": score,
        "action_seed": score,
        "promise_survival": score,
        "genre_fidelity": score,
        "ai_assembly": 0.0,
        "dumb_cost": False,
        "after_opening_promise": "开局之后仍有承诺",
        "action_families": ["行动一", "行动二", "行动三"],
        "growth_surface": "持续积累面",
    }
    item.update(overrides)
    return item


# ── selection: one expansion slot per domain ────────────────────────────────


def test_selection_caps_one_slot_per_domain() -> None:
    ranking = [
        _rank_item(0, "殡葬", 9.5),
        _rank_item(1, "殡葬", 9.2),
        _rank_item(2, "殡葬", 9.0),
        _rank_item(3, "牧羊", 8.2),
        _rank_item(4, "面摊", 8.0),
        _rank_item(5, "镖局", 7.8),
    ]
    selected = ct._select_raw_ideas_for_expansion(
        ranking, raw_floor=7.0, progression_floor=5.0, limit=4
    )
    domains = [item["domain"] for item in selected]
    # Without the cap, score order alone gives 殡葬×3 in 4 slots.
    assert domains == ["殡葬", "牧羊", "面摊", "镖局"], domains


def test_selection_relaxes_cap_when_domains_cannot_fill_the_limit() -> None:
    ranking = [
        _rank_item(0, "殡葬", 9.5),
        _rank_item(1, "殡葬", 9.0),
        _rank_item(2, "牧羊", 8.0),
    ]
    selected = ct._select_raw_ideas_for_expansion(
        ranking, raw_floor=7.0, progression_floor=5.0, limit=3
    )
    # Diversity first, then the best duplicate rather than an empty slot.
    assert [item["index"] for item in selected] == [0, 2, 1]


def test_selection_never_groups_unlabeled_ideas() -> None:
    """A judge that omits domain must not accidentally merge everything."""

    ranking = [
        _rank_item(0, "", 9.5),
        _rank_item(1, "", 9.0),
        _rank_item(2, "", 8.5),
    ]
    selected = ct._select_raw_ideas_for_expansion(
        ranking, raw_floor=7.0, progression_floor=5.0, limit=3
    )
    assert [item["index"] for item in selected] == [0, 1, 2]


# ── rank schema carries the judge-authored domain label ─────────────────────


def test_rank_prompt_requests_domain_and_parser_keeps_it() -> None:
    _, user = ct._build_raw_idea_rank_messages(
        genre="东方玄幻", sub_genre="东方玄幻",
        ideas=[("纯题材直觉", "一个牧羊少年发现自己踩出的路只有自己能走")],
        audience_orientation="男频",
    )
    assert '"domain"' in user
    parsed = ct._parse_raw_idea_ranking(
        '{"ranked":[{"index":0,"freshness":8,"click_seed":8,"character_logic":8,'
        '"action_seed":8,"promise_survival":8,"genre_fidelity":8,"ai_assembly":1,'
        '"dumb_cost":false,"domain":"牧羊","after_opening_promise":"承诺",'
        '"action_families":["a","b","c"],"growth_surface":"积累"}]}'
    )
    assert parsed and parsed[0]["domain"] == "牧羊"
    # Absent label degrades to "", never to a shared bucket.
    parsed_missing = ct._parse_raw_idea_ranking(
        '{"ranked":[{"index":0,"freshness":8,"click_seed":8,"character_logic":8,'
        '"action_seed":8,"promise_survival":8,"genre_fidelity":8,"ai_assembly":1}]}'
    )
    assert parsed_missing and parsed_missing[0]["domain"] == ""


# ── pool prompt: category-level diversity, no seeded vocabulary ─────────────


def test_pool_prompt_demands_distinct_domains_without_naming_any() -> None:
    system, _ = ct._build_raw_idea_pool_messages(
        genre="东方玄幻", sub_genre="东方玄幻", count=8,
        audience_orientation="男频", tone_preference="light",
        effect_skills=("comedy_engine",), prompt_arm="author_pitch",
    )
    assert "彼此不同的生活场域" in system
    # The requirement must stay categorical: naming a domain seeds it.
    for token in ("殡", "尸", "亡", "坟", "地府", "债", "账"):
        assert token not in system, token


# ── creation-option fit axes on the engine judge ────────────────────────────


def test_intent_axes_derive_only_from_explicit_options() -> None:
    axes = ct.creation_intent_judge_axes(tone_preference="light", cost_style="minimal")
    assert [key for key, _ in axes] == ["tone_fit", "cost_style_fit"]
    assert ct.creation_intent_judge_axes(tone_preference="hot", cost_style="standard") == ()
    assert ct.creation_intent_judge_axes() == ()


def test_engine_judges_score_and_schema_carry_the_fit_axes() -> None:
    axes = ct.creation_intent_judge_axes(tone_preference="light", cost_style="minimal")
    _, single = ct._build_engine_judge_messages(
        kernel={"concept": "x"}, genre="东方玄幻", sub_genre="东方玄幻",
        chapter_count=50, seed_concept="种子", intent_axes=axes,
    )
    _, batch = ct._build_engine_batch_judge_messages(
        cards=[("lane", "种子", {"concept": "x"})], genre="东方玄幻",
        sub_genre="东方玄幻", chapter_count=50, intent_axes=axes,
    )
    for blob in (single, batch):
        assert '"tone_fit":0-10' in blob
        assert '"cost_style_fit":0-10' in blob
        assert "爽文无代价" in blob
    # Without options the prompts are byte-identical to the old contract.
    _, plain = ct._build_engine_judge_messages(
        kernel={"concept": "x"}, genre="东方玄幻", sub_genre="东方玄幻",
        chapter_count=50, seed_concept="种子",
    )
    assert "tone_fit" not in plain and "cost_style_fit" not in plain


def test_fit_axes_are_wired_into_the_tournament_floor() -> None:
    """The axes must reach engine_axes (floored) and every judge call site."""

    source = inspect.getsource(ct.run_concept_tournament)
    assert "creation_intent_judge_axes(" in source
    assert "*(key for key, _ in intent_axes)" in source
    assert source.count("intent_axes=intent_axes") == 3


def test_fit_axis_instructions_never_name_props() -> None:
    """Category-level only — a prop list in a judge instruction is still a
    token the pipeline carries around (《雾街债主》 lesson)."""

    for _, text in ct.creation_intent_judge_axes(
        tone_preference="light", cost_style="minimal"
    ):
        for token in ("折寿", "失忆", "寿元", "殡", "棺", "尸体", "账本", "欠条"):
            assert token not in text, token
