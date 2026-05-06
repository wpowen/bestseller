"""Tests for the post-write premature-death scanner added to
``services/contradiction.py``.

The scanner addresses the inverse failure mode of ``_check_resurrection``:

* ``_check_resurrection`` flags scenes that stage a character whose
  ``death_chapter_number`` is *less than* the current chapter (already
  dead — no resurrection allowed).
* ``check_premature_death_in_prose`` flags chapters whose prose describes
  a character dying when their ``death_chapter_number`` is *greater than*
  the current chapter (the planner scheduled their death later — the
  writer LLM jumped the gun).

This is the failure mode that produced the ch6 苏瑶 / 陆沉 incident: both
had planned deaths far later in the book (ch435 / ch458) but ch6 prose
still wrote them dying.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bestseller.services.contradiction import (
    _scan_premature_death_in_text,
    check_premature_death_in_prose,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Pure-text scanner — no DB
# ---------------------------------------------------------------------------


class TestScanPrematureDeathInText:
    """Locks down the regex/window heuristics of the prose scanner."""

    def test_zh_strong_match_flags_death_verb_near_name(self) -> None:
        prose = "宁尘转过头，看见苏瑶缓缓倒下，再也没有起来。"
        findings = _scan_premature_death_in_text(
            chapter_md=prose,
            protected_names=["苏瑶"],
            language="zh-CN",
        )
        kinds = [k for _, k, _ in findings]
        assert "strong" in kinds
        assert findings[0][0] == "苏瑶"

    def test_zh_implied_match_flags_post_mortem_framing(self) -> None:
        prose = "他还记得陆沉死前最后的眼神。"
        findings = _scan_premature_death_in_text(
            chapter_md=prose,
            protected_names=["陆沉"],
            language="zh-CN",
        )
        kinds = [k for _, k, _ in findings]
        # "死前" lands inside the implied catalogue, never the strong.
        assert "implied" in kinds
        assert "strong" not in kinds

    def test_strong_match_overrides_implied_for_same_name(self) -> None:
        prose = "苏瑶咽下最后一口气，葬礼定在三天后。"
        findings = _scan_premature_death_in_text(
            chapter_md=prose,
            protected_names=["苏瑶"],
            language="zh-CN",
        )
        # Both keywords match; once the strong hit is recorded the
        # scanner must not record a duplicate implied entry for the
        # same name (we de-dupe on (name, kind), so strong wins by
        # appearing first in the catalogue).
        kinds_for_su = [k for n, k, _ in findings if n == "苏瑶"]
        assert "strong" in kinds_for_su

    def test_no_match_when_name_far_from_death_verb(self) -> None:
        # Name appears 200 chars away from "死亡" — outside the window.
        prose = "苏瑶站在悬崖边。" + "他静静地看着远方。" * 50 + "新闻里说：今天死亡人数破纪录。"
        findings = _scan_premature_death_in_text(
            chapter_md=prose,
            protected_names=["苏瑶"],
            language="zh-CN",
        )
        assert findings == []

    def test_no_false_positive_on_metaphor_alone(self) -> None:
        # Word "死" appears via "像死人一样" but that's far from the name.
        prose = "苏瑶笑了一下。他像死人一样睡了过去，那是另一个房间的事。"
        findings = _scan_premature_death_in_text(
            chapter_md=prose,
            protected_names=["苏瑶"],
            language="zh-CN",
        )
        # The window is small (30 chars CJK), and the death-verb here is
        # "像死人" which is not in the catalogue. No hit.
        assert findings == []

    def test_proximity_attributes_death_to_closer_name(self) -> None:
        """Co-located names: the death keyword should attach to whichever
        character name is closest, not to whichever name is protected.
        Without this attribution the scanner mis-flags 苏瑶 just because
        her name appears in the same sentence as 叶长青's death.
        """
        prose = "苏瑶冷冷一笑，叶长青已死。"
        findings = _scan_premature_death_in_text(
            chapter_md=prose,
            protected_names=["苏瑶"],  # 叶长青 NOT protected
            other_names=["叶长青"],     # but the scanner knows 叶长青 exists
            language="zh-CN",
        )
        # 已死 is closer to 叶长青 than 苏瑶, so the death attaches to
        # 叶长青 — and 叶长青 is not in protected_names → no finding.
        assert findings == []

    def test_en_strong_match_casefolded(self) -> None:
        prose = "Su Yao stopped breathing as the door slammed shut."
        findings = _scan_premature_death_in_text(
            chapter_md=prose,
            protected_names=["Su Yao"],
            language="en-US",
        )
        kinds = [k for _, k, _ in findings]
        assert "strong" in kinds

    def test_empty_protected_list_is_noop(self) -> None:
        findings = _scan_premature_death_in_text(
            chapter_md="苏瑶死了。",
            protected_names=[],
            language="zh-CN",
        )
        assert findings == []

    def test_empty_prose_is_noop(self) -> None:
        findings = _scan_premature_death_in_text(
            chapter_md="",
            protected_names=["苏瑶"],
            language="zh-CN",
        )
        assert findings == []

    def test_flashback_passage_is_exempt(self) -> None:
        """A protected character whose name appears next to a death verb
        inside a clear flashback / reminiscence frame must NOT be flagged
        — the prose is remembering the planned future death, not staging
        it now. The user-facing rule: deceased characters can be
        remembered, quoted, mourned, but not appear in present-tense
        action — and the inverse for not-yet-deceased: they can be
        invoked in flash-forward / vision / dream sequences without the
        scanner treating it as a leak."""
        prose = "他闭上眼，脑海中浮现苏瑶咽下最后一口气的画面——那是他多年以后才会面对的事。"
        findings = _scan_premature_death_in_text(
            chapter_md=prose,
            protected_names=["苏瑶"],
            language="zh-CN",
        )
        assert findings == []

    def test_funeral_imagery_is_exempt(self) -> None:
        prose = "葬礼那天，他想起苏瑶最后倒下的样子。"
        findings = _scan_premature_death_in_text(
            chapter_md=prose,
            protected_names=["苏瑶"],
            language="zh-CN",
        )
        assert findings == []

    def test_present_tense_death_still_flagged(self) -> None:
        # Sanity: the flashback exemption must not silence real
        # present-tense leaks. Same death verb, no memory framing.
        prose = "苏瑶倒下不起，眼神空洞。叶长青冷冷地走开。"
        findings = _scan_premature_death_in_text(
            chapter_md=prose,
            protected_names=["苏瑶"],
            language="zh-CN",
        )
        assert any(k == "strong" for _, k, _ in findings)


# ---------------------------------------------------------------------------
# DB-aware wrapper — with mocked session
# ---------------------------------------------------------------------------


def _make_protected_session(rows: list[tuple[str, int]]) -> AsyncMock:
    """Mock session.execute that returns the protected-roster rows."""

    session = AsyncMock()

    async def _execute(stmt: Any) -> Any:
        # `execute` returns an iterable of rows in our usage.
        result = AsyncMock()

        # Force iteration to return the configured rows. The production
        # caller iterates with ``for row in rows`` so this needs to be
        # iterable. We use list() in tests below.
        def _iter() -> Any:
            return iter(rows)

        result.__iter__ = lambda self=result: _iter()
        return result

    session.execute = AsyncMock(side_effect=_execute)
    return session


class TestCheckPrematureDeathInProse:
    """End-to-end check: protected roster comes from the DB, scan runs
    against the supplied chapter markdown."""

    @pytest.mark.asyncio
    async def test_no_protected_characters_returns_empty(self) -> None:
        session = _make_protected_session([])
        violations, warnings = await check_premature_death_in_prose(
            session,
            project_id=uuid.uuid4(),
            chapter_number=6,
            chapter_md="苏瑶咽下最后一口气。",
            language="zh-CN",
        )
        assert violations == []
        assert warnings == []

    @pytest.mark.asyncio
    async def test_protected_with_strong_match_produces_critical_violation(
        self,
    ) -> None:
        session = _make_protected_session([("苏瑶", 435), ("陆沉", 458)])
        violations, warnings = await check_premature_death_in_prose(
            session,
            project_id=uuid.uuid4(),
            chapter_number=6,
            chapter_md="苏瑶缓缓倒下，再也没有醒来。",
            language="zh-CN",
        )
        assert len(violations) == 1
        assert violations[0].check_type == "character_premature_death"
        assert violations[0].severity == "error"
        assert "苏瑶" in violations[0].message
        assert "435" in violations[0].message  # planned death cited

    @pytest.mark.asyncio
    async def test_protected_with_implied_framing_produces_warning(
        self,
    ) -> None:
        session = _make_protected_session([("陆沉", 458)])
        violations, warnings = await check_premature_death_in_prose(
            session,
            project_id=uuid.uuid4(),
            chapter_number=6,
            chapter_md="他还记得陆沉死前最后的眼神。",
            language="zh-CN",
        )
        assert violations == []
        assert len(warnings) == 1
        assert warnings[0].check_type == "character_premature_death_implied"
        assert "陆沉" in warnings[0].message
        assert "458" in warnings[0].message

    @pytest.mark.asyncio
    async def test_clean_prose_passes(self) -> None:
        session = _make_protected_session([("苏瑶", 435)])
        violations, warnings = await check_premature_death_in_prose(
            session,
            project_id=uuid.uuid4(),
            chapter_number=6,
            chapter_md="苏瑶站起身，眼神锐利地看向叶长青。",
            language="zh-CN",
        )
        assert violations == []
        assert warnings == []

    @pytest.mark.asyncio
    async def test_empty_chapter_md_returns_empty(self) -> None:
        session = _make_protected_session([("苏瑶", 435)])
        violations, warnings = await check_premature_death_in_prose(
            session,
            project_id=uuid.uuid4(),
            chapter_number=6,
            chapter_md="",
            language="zh-CN",
        )
        assert violations == []
        assert warnings == []
