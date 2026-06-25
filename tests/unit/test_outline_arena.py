"""L1 tests for the outline / opening blind arena (machinery, fake judge).

Mirrors the premise_appeal_arena test approach: a deterministic fake judge that
favours whichever slot holds the candidate marker, proving swap-consistent win
detection. Real reference corpora are hand-curated separately; here we monkey-
patch the loaders so the test is hermetic.
"""

from __future__ import annotations

import pytest

from bestseller.services import outline_arena


def _fake_judge_candidate_wins():
    async def _judge(system: str, user: str) -> str:
        head = user.split("·乙】")[0]  # the 甲 section
        if "CANDMARK" in head:
            return '{"winner": "甲", "reason": "candidate"}'
        return '{"winner": "乙", "reason": "candidate"}'

    return _judge


def _fake_judge_always_tie():
    async def _judge(system: str, user: str) -> str:
        return '{"winner": "持平", "reason": "tie"}'

    return _judge


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outline_arena_candidate_wins_swap_consistent(monkeypatch) -> None:
    monkeypatch.setattr(
        outline_arena,
        "load_reference_outlines",
        lambda: {
            "xuanhuan": [
                {"title": "真书A", "text": "参照梗概一"},
                {"title": "真书B", "text": "参照梗概二"},
            ]
        },
    )
    summary = await outline_arena.run_outline_arena(
        kind="outline",
        candidate_text="CANDMARK 这是本书的前三章梗概",
        genre="xuanhuan",
        judge=_fake_judge_candidate_wins(),
    )
    assert summary.pairs == 2
    assert summary.wins == 2
    assert summary.win_rate == 1.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outline_arena_tie_gives_half(monkeypatch) -> None:
    monkeypatch.setattr(
        outline_arena,
        "load_reference_openings",
        lambda: {"urban": [{"title": "真书", "text": "参照开头"}]},
    )
    summary = await outline_arena.run_outline_arena(
        kind="opening",
        candidate_text="CANDMARK 第一章开头",
        genre="urban",
        judge=_fake_judge_always_tie(),
    )
    assert summary.pairs == 1
    assert summary.win_rate == 0.5


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outline_arena_empty_corpus_is_no_pairs(monkeypatch) -> None:
    """No curated refs for the genre → pairs==0 (caller shows 暂无对标, not pass)."""
    monkeypatch.setattr(outline_arena, "load_reference_outlines", lambda: {})
    summary = await outline_arena.run_outline_arena(
        kind="outline",
        candidate_text="CANDMARK 梗概",
        genre="scifi",
        judge=_fake_judge_candidate_wins(),
    )
    assert summary.pairs == 0
    assert summary.win_rate == 0.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outline_arena_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        await outline_arena.run_outline_arena(
            kind="nonsense", candidate_text="x", genre="xuanhuan", judge=_fake_judge_always_tie()
        )


@pytest.mark.unit
def test_shipped_reference_scaffolds_load_empty() -> None:
    """The shipped YAML scaffolds are instructions-only → load to empty dicts."""
    outline_arena._load_reference.cache_clear()
    assert outline_arena.load_reference_outlines() == {}
    assert outline_arena.load_reference_openings() == {}
