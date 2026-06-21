"""L1 unit tests for the pairwise story/blurb appeal arena (vs real competitors).

The LLM judge is faked here (covered live in the validation script). These pin
the deterministic arena mechanics: verdict mapping, swap-consistency,
win-rate aggregation, reference resolution, and end-to-end win-rate with a
content-aware fake judge (candidate-always-wins → 1.0; position-biased → 0.5).
"""

from __future__ import annotations

import pytest

from bestseller.services.premise_appeal_arena import (
    _swap_consistent,
    load_reference_blurbs,
    parse_appeal_verdict,
    resolve_reference_set,
    run_appeal_arena,
    summarize_appeal,
)

_CANDIDATE_MARK = "★候选★"


@pytest.mark.unit
@pytest.mark.parametrize(
    "winner,candidate_is_a,expected",
    [
        ("甲", True, "win"),
        ("甲", False, "loss"),
        ("乙", True, "loss"),
        ("乙", False, "win"),
        ("持平", True, "tie"),
    ],
)
def test_parse_verdict_maps_token_to_candidate_outcome(winner, candidate_is_a, expected):
    raw = f'{{"winner": "{winner}", "reason": "x"}}'
    assert parse_appeal_verdict(raw, candidate_is_a=candidate_is_a) == expected


@pytest.mark.unit
def test_parse_verdict_unparseable_returns_none():
    assert parse_appeal_verdict("garbage no json", candidate_is_a=True) is None


@pytest.mark.unit
def test_swap_consistent_only_consistent_wins_count():
    assert _swap_consistent("win", "win") == "win"
    assert _swap_consistent("loss", "loss") == "loss"
    assert _swap_consistent("win", "loss") == "tie"   # position bias → neutralized
    assert _swap_consistent("win", "tie") == "tie"


@pytest.mark.unit
def test_reference_set_known_genre_and_generic_fallback():
    refs = load_reference_blurbs()
    assert "xuanhuan" in refs and len(refs["xuanhuan"]) >= 2
    # unknown genre → topped up from the generic pool (non-empty)
    fallback = resolve_reference_set("完全没有的题材zzz", None, min_refs=3)
    assert len(fallback) >= 3


@pytest.mark.unit
def test_summarize_winrate_math():
    from bestseller.services.premise_appeal_arena import AppealArenaPair, AppealMatchResult

    def _r(outcome):
        p = AppealArenaPair(pair_id="x", candidate_blurb="c", reference_blurb="r", genre="玄幻")
        return AppealMatchResult(pair=p, outcome=outcome)

    summary = summarize_appeal([_r("win"), _r("win"), _r("tie"), _r("loss")], genre="玄幻")
    assert summary.pairs == 4
    assert summary.wins == 2 and summary.losses == 1 and summary.ties == 1
    # (2 + 0.5*1) / 4 = 0.625
    assert summary.win_rate == pytest.approx(0.625)


async def _candidate_wins_judge(system, user):
    # The candidate (marked) always wins, whichever slot it is in.
    a_section = user.split("【简介·乙】")[0]
    cand_in_a = _CANDIDATE_MARK in a_section
    return '{"winner": "甲"}' if cand_in_a else '{"winner": "乙"}'


async def _candidate_loses_judge(system, user):
    a_section = user.split("【简介·乙】")[0]
    cand_in_a = _CANDIDATE_MARK in a_section
    return '{"winner": "乙"}' if cand_in_a else '{"winner": "甲"}'


async def _position_biased_judge(system, user):
    return '{"winner": "甲"}'  # always slot A → should neutralize to ties


@pytest.mark.unit
async def test_arena_candidate_always_wins_gives_winrate_1():
    s = await run_appeal_arena(
        candidate_blurb=f"{_CANDIDATE_MARK}一个极强的钩子简介", genre="玄幻",
        judge=_candidate_wins_judge, min_refs=3, max_refs=4,
    )
    assert s.pairs >= 3
    assert s.win_rate == pytest.approx(1.0)


@pytest.mark.unit
async def test_arena_candidate_always_loses_gives_winrate_0():
    s = await run_appeal_arena(
        candidate_blurb=f"{_CANDIDATE_MARK}很弱的简介", genre="玄幻",
        judge=_candidate_loses_judge, min_refs=3, max_refs=4,
    )
    assert s.win_rate == pytest.approx(0.0)


@pytest.mark.unit
async def test_arena_position_biased_judge_neutralized_to_half():
    s = await run_appeal_arena(
        candidate_blurb=f"{_CANDIDATE_MARK}简介", genre="玄幻",
        judge=_position_biased_judge, min_refs=3, max_refs=4,
    )
    # every pair: forward win + backward loss → tie → win_rate 0.5
    assert s.win_rate == pytest.approx(0.5)
