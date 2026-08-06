"""Ambient attribution: which work an LLM call belonged to.

Without this, "how much did this repair loop cost?" needs archaeology, and a
runaway loop is only visible once the bill arrives.
"""

from __future__ import annotations

import asyncio

import pytest

from bestseller.services.cost_attribution import (
    ATTRIBUTION_METADATA_KEY,
    attribution_scope,
    current_attribution,
    merge_attribution_into,
    rework_scope,
)


@pytest.mark.unit
def test_no_attribution_outside_a_scope() -> None:
    assert current_attribution() == {}
    assert merge_attribution_into({"a": 1}) == {"a": 1}


@pytest.mark.unit
def test_scope_tags_and_then_restores() -> None:
    with attribution_scope(chapter_number=7, gate="ai_flavor_gate"):
        assert current_attribution() == {"chapter_number": 7, "gate": "ai_flavor_gate"}
    assert current_attribution() == {}


@pytest.mark.unit
def test_nested_scopes_merge_with_inner_winning() -> None:
    with attribution_scope(chapter_number=7, gate="outer"):
        with attribution_scope(gate="inner", rework_round=2):
            attribution = current_attribution()
    assert attribution == {"chapter_number": 7, "gate": "inner", "rework_round": 2}


@pytest.mark.unit
def test_none_values_are_dropped_so_they_do_not_mask_outer_context() -> None:
    with attribution_scope(chapter_number=7):
        with attribution_scope(chapter_number=None, gate="g"):
            assert current_attribution() == {"chapter_number": 7, "gate": "g"}


@pytest.mark.unit
def test_rework_scope_yields_a_stable_id_shared_by_every_call_inside() -> None:
    with rework_scope(chapter_number=3, gate="length", round_index=1) as event_id:
        first = merge_attribution_into({})
        second = merge_attribution_into({"other": True})

    assert first[ATTRIBUTION_METADATA_KEY]["rework_event_id"] == event_id
    assert second[ATTRIBUTION_METADATA_KEY]["rework_event_id"] == event_id
    assert first[ATTRIBUTION_METADATA_KEY]["chapter_number"] == 3
    assert second["other"] is True


@pytest.mark.unit
def test_two_rework_events_get_distinct_ids() -> None:
    with rework_scope(chapter_number=1) as first:
        pass
    with rework_scope(chapter_number=1) as second:
        pass
    assert first != second


@pytest.mark.unit
def test_scope_is_restored_even_when_the_body_raises() -> None:
    with pytest.raises(ValueError):
        with attribution_scope(chapter_number=9):
            raise ValueError("boom")
    assert current_attribution() == {}


@pytest.mark.unit
def test_concurrent_tasks_do_not_leak_attribution_into_each_other() -> None:
    """Chapters, judges and rewrites run concurrently; tags must not cross."""

    async def tagged(chapter: int) -> dict[str, object]:
        with attribution_scope(chapter_number=chapter):
            await asyncio.sleep(0)
            return current_attribution()

    async def main() -> list[dict[str, object]]:
        return list(await asyncio.gather(*(tagged(n) for n in range(5))))

    results = asyncio.run(main())

    assert [r["chapter_number"] for r in results] == [0, 1, 2, 3, 4]


@pytest.mark.unit
def test_explicit_metadata_wins_over_ambient_context() -> None:
    with attribution_scope(chapter_number=7):
        merged = merge_attribution_into(
            {ATTRIBUTION_METADATA_KEY: {"chapter_number": 99}}
        )
    assert merged[ATTRIBUTION_METADATA_KEY]["chapter_number"] == 99


@pytest.mark.unit
def test_merge_never_raises_on_odd_input() -> None:
    """Attribution is diagnostics — it must never cost us an llm_runs row."""

    assert merge_attribution_into(None) == {}
