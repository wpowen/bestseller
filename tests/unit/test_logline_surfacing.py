"""The derived logline must reach ``metadata["logline"]`` (2026-08-10).

``conception._derive_logline_from_champion`` distils a short, market-calibrated
hook from the finalized blurb into ``writing_profile.market.logline``. The
metadata flattener copied six sibling market fields and skipped this one, so
every consumer that reads ``metadata["logline"]`` — exports,
``commercial_novel_gate``, ``imagery_system_design``, ``narrative``,
``narrative_tree``, ``book_listing``, and the dashboard's 一句话钩子 — saw
whatever else had landed in that slot.

Live 2026-08-09 《废脉炉子天天骂我》: derived hook 80 chars,
``metadata["logline"]`` the 258-char premise verbatim. The distillation ran on
every book and was thrown away.
"""

from __future__ import annotations

import pytest

from bestseller.domain.project import ProjectCreate
from bestseller.services.writing_profile import (
    build_project_metadata,
    resolve_writing_profile,
)

pytestmark = pytest.mark.unit

PREMISE = (
    "沈烬，十七岁，万器废渊最外层一座青铜小宗门的烧炉杂役，被宗门长辈断定器脉枯竭、"
    "这辈子炼不出东西。没人知道的是，他那口没人要的破炉子专吃被人丢弃的神器残片，"
    "每喂一块就吐一门不讲理的新口诀，再养出一只会骂人的器灵。器灵越多炉子越饿。"
)
DERIVED = "被宗门判了器脉枯竭的少年，拿破炉子偷偷喂神器残片，越喂炉子越饿"


def _metadata(*, derived: str | None, existing: str | None) -> dict:
    profile_payload = {"market": {"logline": derived}} if derived else None
    profile = resolve_writing_profile(profile_payload, genre="东方玄幻")
    meta: dict = {"premise": PREMISE}
    if existing is not None:
        meta["logline"] = existing
    payload = ProjectCreate(
        slug="logline-surfacing",
        title="废脉炉子天天骂我",
        genre="东方玄幻",
        target_word_count=100_000,
        target_chapters=50,
        metadata=meta,
    )
    return build_project_metadata(payload, profile)


def test_derived_logline_replaces_a_premise_sitting_in_the_slot() -> None:
    """The exact live shape: premise verbatim in the logline slot."""

    assert _metadata(derived=DERIVED, existing=PREMISE)["logline"] == DERIVED


def test_derived_logline_fills_an_empty_slot() -> None:
    assert _metadata(derived=DERIVED, existing=None)["logline"] == DERIVED


def test_a_real_hook_already_in_the_slot_is_preserved() -> None:
    """An editor/concept one-liner outranks the derived one — never clobber it."""

    assert _metadata(derived=DERIVED, existing="编辑手写的钩子")["logline"] == (
        "编辑手写的钩子"
    )


def test_premise_in_the_slot_is_dropped_when_there_is_nothing_to_promote() -> None:
    """Consumers treat this field as short copy; a premise there is worse than
    absence, and absence lets each reader apply its own fallback chain."""

    assert "logline" not in _metadata(derived=None, existing=PREMISE)


def test_no_logline_anywhere_stays_absent_rather_than_fabricated() -> None:
    assert "logline" not in _metadata(derived=None, existing=None)


def test_flattener_still_copies_its_other_market_fields() -> None:
    """Guard against the fix disturbing the siblings it sits among."""

    meta = _metadata(derived=DERIVED, existing=None)
    for key in (
        "platform_target",
        "reader_promise",
        "selling_points",
        "trope_keywords",
        "opening_strategy",
        "chapter_hook_strategy",
    ):
        assert key in meta, key
