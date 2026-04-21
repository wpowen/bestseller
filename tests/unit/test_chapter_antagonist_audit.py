"""Tests for the chapter-level antagonist audit (B10).

Locks in the contract that already-written chapters are validated
against the volume's antagonist roster — catching the production
failure where volume-7 chapters kept using the volume-2 boss as the
present-tense antagonist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from bestseller.services.chapter_antagonist_audit import (
    DEFAULT_SALIENCE_THRESHOLD,
    ChapterAntagonistFinding,
    ChapterAntagonistReport,
    ChapterAudit,
    audit_chapter_against_volume,
    audit_novel_chapters,
    build_volume_antagonist_index,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _plan(
    *,
    name: str,
    scope: int | None = None,
    stages: list[tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """Build a minimal antagonist plan dict with the shape the audit
    module understands."""
    return {
        "name": name,
        "scope_volume_number": scope,
        "stages_of_relevance": stages or [],
    }


def _chapter(*, chapter_number: int, volume_number: int, text: str) -> dict[str, Any]:
    return {
        "chapter_number": chapter_number,
        "volume_number": volume_number,
        "text": text,
    }


# ---------------------------------------------------------------------------
# build_volume_antagonist_index
# ---------------------------------------------------------------------------


def test_build_index_uses_scope_volume_number() -> None:
    plans = [
        _plan(name="李元霸", scope=1),
        _plan(name="赵天罡", scope=2),
        _plan(name="孙悟空", scope=3),
    ]
    by_vol, all_names = build_volume_antagonist_index(plans, volume_count=3)
    assert by_vol[1] == {"李元霸"}
    assert by_vol[2] == {"赵天罡"}
    assert by_vol[3] == {"孙悟空"}
    assert all_names == {"李元霸", "赵天罡", "孙悟空"}


def test_build_index_supports_stages_of_relevance_ranges() -> None:
    plans = [
        _plan(name="元婴老者", stages=[(1, 3)]),
        _plan(name="化神真人", stages=[(4, 6)]),
    ]
    by_vol, _ = build_volume_antagonist_index(plans, volume_count=6)
    assert "元婴老者" in by_vol[1]
    assert "元婴老者" in by_vol[2]
    assert "元婴老者" in by_vol[3]
    assert "元婴老者" not in by_vol[4]
    assert "化神真人" in by_vol[5]


def test_build_index_merges_scope_and_stages() -> None:
    plans = [_plan(name="墨影", scope=1, stages=[(5, 7)])]
    by_vol, _ = build_volume_antagonist_index(plans, volume_count=8)
    assert "墨影" in by_vol[1]
    assert "墨影" in by_vol[5]
    assert "墨影" in by_vol[7]
    assert "墨影" not in by_vol[3]


def test_build_index_book_wide_when_no_scope() -> None:
    """A plan with no scope / stages is treated as book-wide — it's
    allowed in every volume, so it can never be out-of-scope."""
    plans = [_plan(name="大反派")]
    by_vol, _ = build_volume_antagonist_index(plans, volume_count=5)
    for v in range(1, 6):
        assert "大反派" in by_vol[v]


def test_build_index_strips_short_and_generic_names() -> None:
    plans = [
        _plan(name="王"),          # 1 CJK char — too short
        _plan(name="AB"),           # 2 ASCII chars — too short
        _plan(name="敌人", scope=1), # generic label
        _plan(name="villain", scope=2),  # generic label
        _plan(name="李元霸", scope=3),
    ]
    by_vol, all_names = build_volume_antagonist_index(plans, volume_count=3)
    assert "王" not in all_names
    assert "AB" not in all_names
    assert "敌人" not in all_names
    assert "villain" not in all_names
    assert all_names == {"李元霸"}


def test_build_index_accepts_dict_stages() -> None:
    plans = [_plan(name="苏皇", stages=[{"start": 2, "end": 4}])]
    by_vol, _ = build_volume_antagonist_index(plans, volume_count=5)
    assert "苏皇" in by_vol[2]
    assert "苏皇" in by_vol[3]
    assert "苏皇" in by_vol[4]
    assert "苏皇" not in by_vol[1]


# ---------------------------------------------------------------------------
# audit_chapter_against_volume
# ---------------------------------------------------------------------------


def test_chapter_with_only_scoped_antagonist_is_clean() -> None:
    audit = audit_chapter_against_volume(
        chapter_number=1,
        volume_number=1,
        chapter_text="李元霸率大军压境，少年迎面扛住三记重锤。李元霸冷笑。",
        allowed_in_volume=["李元霸"],
        all_antagonist_names=["李元霸", "赵天罡"],
    )
    assert audit.findings == ()
    assert "李元霸" in audit.mentioned_expected
    assert audit.mentioned_out_of_scope == ()


def test_single_foreign_mention_emits_warning_not_critical() -> None:
    """A one-off reference to a previous-volume boss is allowed
    (flashback), but still surfaces as a warning so the reviewer can
    check."""
    audit = audit_chapter_against_volume(
        chapter_number=42,
        volume_number=3,
        chapter_text="赵天罡现身，刀光逼近。想起当年元婴老者的一掌，他不寒而栗。",
        allowed_in_volume=["赵天罡"],
        all_antagonist_names=["元婴老者", "赵天罡"],
    )
    severities = [f.severity for f in audit.findings]
    codes = [f.code for f in audit.findings]
    assert "warning" in severities
    assert "critical" not in severities
    assert "passing_foreign_antagonist" in codes


def test_three_plus_foreign_mentions_emit_critical() -> None:
    """Dominant present-tense use of a foreign antagonist = critical
    (the original production bug)."""
    text = (
        "元婴老者逼近。元婴老者抬手便是一掌。"
        "元婴老者冷笑：『你逃不掉。』少年咬牙迎战元婴老者。"
    )
    audit = audit_chapter_against_volume(
        chapter_number=100,
        volume_number=7,
        chapter_text=text,
        allowed_in_volume=["赵天罡"],
        all_antagonist_names=["元婴老者", "赵天罡"],
    )
    critical_findings = [f for f in audit.findings if f.severity == "critical"]
    assert len(critical_findings) == 1
    finding = critical_findings[0]
    assert finding.code == "dominant_foreign_antagonist"
    assert finding.chapter_number == 100
    assert finding.volume_number == 7
    assert finding.payload["name"] == "元婴老者"
    assert finding.payload["count"] >= 4
    assert audit.is_critical is True


def test_custom_salience_threshold_is_respected() -> None:
    """A salience_threshold of 5 makes 4 mentions a warning, not
    critical."""
    text = "赵天罡现身。赵天罡笑。赵天罡出招。赵天罡败退。"
    audit = audit_chapter_against_volume(
        chapter_number=1,
        volume_number=1,
        chapter_text=text,
        allowed_in_volume=["李元霸"],
        all_antagonist_names=["李元霸", "赵天罡"],
        salience_threshold=5,
    )
    assert not audit.is_critical
    assert any(f.code == "passing_foreign_antagonist" for f in audit.findings)


def test_english_name_uses_word_boundary_matching() -> None:
    """Kai must NOT match Kairos. Important because LLMs often coin
    names like 'Kairos' that share prefixes with short names."""
    text = "Kairos laughed. Kairos drew his blade. Kairos struck."
    audit = audit_chapter_against_volume(
        chapter_number=1,
        volume_number=1,
        chapter_text=text,
        allowed_in_volume=["Kairos"],
        all_antagonist_names=["Kairos", "Kai"],
        language="en-US",
    )
    # 'Kai' must NOT be flagged, even though it's a substring of
    # 'Kairos'.
    assert all(f.payload.get("name") != "Kai" for f in audit.findings)


def test_mentioned_expected_reports_actual_hits() -> None:
    audit = audit_chapter_against_volume(
        chapter_number=1,
        volume_number=1,
        chapter_text="王夫子独自出场。",
        allowed_in_volume=["王夫子", "李元霸"],
        all_antagonist_names=["王夫子", "李元霸"],
    )
    assert audit.mentioned_expected == ("王夫子",)


def test_empty_chapter_text_produces_no_findings() -> None:
    audit = audit_chapter_against_volume(
        chapter_number=1,
        volume_number=1,
        chapter_text="",
        allowed_in_volume=["李元霸"],
        all_antagonist_names=["李元霸", "赵天罡"],
    )
    assert audit.findings == ()


# ---------------------------------------------------------------------------
# audit_novel_chapters (end-to-end)
# ---------------------------------------------------------------------------


def test_audit_novel_flags_cross_volume_leak() -> None:
    """The exact production failure: volume-7 chapter repeatedly uses
    volume-2 boss 元婴老者. Audit must flag it critical."""
    plans = [
        _plan(name="李元霸", scope=1),
        _plan(name="元婴老者", scope=2),
        _plan(name="化神真人", scope=3),
        _plan(name="陆沉渊", scope=7),
    ]
    chapters = [
        _chapter(
            chapter_number=1,
            volume_number=1,
            text="李元霸率大军压境。李元霸笑得狰狞。李元霸终被击退。",
        ),
        _chapter(
            chapter_number=10,
            volume_number=2,
            text="元婴老者亲自出手。元婴老者的气息弥漫。少年抗住。",
        ),
        _chapter(
            chapter_number=100,
            volume_number=7,
            # BUG — writer used the wrong antagonist
            text=(
                "元婴老者冷冷现身。元婴老者抬手便是一掌。"
                "元婴老者冷笑：『你逃不掉。』少年咬牙迎战元婴老者。"
            ),
        ),
    ]
    report = audit_novel_chapters(chapters, plans, volume_count=7)
    assert report.is_critical
    critical_chapter_numbers = report.critical_chapter_numbers
    assert 100 in critical_chapter_numbers
    assert 1 not in critical_chapter_numbers
    assert 10 not in critical_chapter_numbers


def test_audit_novel_is_empty_for_clean_book() -> None:
    plans = [
        _plan(name="李元霸", scope=1),
        _plan(name="赵天罡", scope=2),
    ]
    chapters = [
        _chapter(chapter_number=1, volume_number=1, text="李元霸现身。"),
        _chapter(chapter_number=2, volume_number=1, text="李元霸继续追击。"),
        _chapter(chapter_number=3, volume_number=2, text="赵天罡登场。"),
    ]
    report = audit_novel_chapters(chapters, plans, volume_count=2)
    assert report.critical_count == 0
    assert report.warning_count == 0
    assert report.total_chapters == 3


def test_audit_novel_respects_stages_of_relevance_overlap() -> None:
    """An antagonist with stages_of_relevance=[(1,5)] is valid across
    volumes 1-5; using his name in volume 3 must not flag."""
    plans = [_plan(name="冥界大帝", stages=[(1, 5)])]
    chapters = [
        _chapter(chapter_number=30, volume_number=3, text="冥界大帝三次冷笑。"),
    ]
    report = audit_novel_chapters(chapters, plans, volume_count=5)
    assert report.critical_count == 0


def test_audit_novel_report_sorts_critical_before_warning() -> None:
    plans = [
        _plan(name="李元霸", scope=1),
        _plan(name="赵天罡", scope=2),
    ]
    chapters = [
        _chapter(
            chapter_number=5,
            volume_number=2,
            text="赵天罡主战。偶然想起李元霸。",
        ),
        _chapter(
            chapter_number=20,
            volume_number=2,
            text=(
                "李元霸冷冷现身。李元霸抬手。李元霸冷笑。李元霸重归。"
            ),
        ),
    ]
    report = audit_novel_chapters(chapters, plans, volume_count=2)
    # First finding must be critical.
    assert report.findings[0].severity == "critical"
    # Critical findings must precede warning findings.
    seen_warning = False
    for f in report.findings:
        if f.severity == "warning":
            seen_warning = True
        elif f.severity == "critical":
            assert not seen_warning, "critical finding appeared after a warning"


def test_report_to_prompt_block_lists_critical_findings_zh() -> None:
    plans = [
        _plan(name="李元霸", scope=1),
        _plan(name="元婴老者", scope=2),
    ]
    chapters = [
        _chapter(
            chapter_number=99,
            volume_number=1,
            text=(
                "元婴老者出现。元婴老者冷笑。元婴老者重击。元婴老者倒下。"
            ),
        ),
    ]
    report = audit_novel_chapters(chapters, plans, volume_count=2, language="zh-CN")
    block = report.to_prompt_block(language="zh-CN")
    assert "章节敌人审查" in block
    assert "CRITICAL" in block
    assert "元婴老者" in block
    assert "99" in block


def test_report_to_prompt_block_english() -> None:
    plans = [
        _plan(name="Kaelvar", scope=1),
        _plan(name="Oriel", scope=2),
    ]
    chapters = [
        _chapter(
            chapter_number=99,
            volume_number=1,
            text="Oriel struck. Oriel laughed. Oriel retreated. Oriel vanished.",
        ),
    ]
    report = audit_novel_chapters(chapters, plans, volume_count=2, language="en-US")
    block = report.to_prompt_block(language="en-US")
    assert "CHAPTER ANTAGONIST AUDIT" in block
    assert "Oriel" in block


def test_report_critical_chapter_numbers_is_deduped_and_sorted() -> None:
    plans = [
        _plan(name="甲乙丙", scope=1),
        _plan(name="丁戊己", scope=2),
    ]
    # Two foreign mentions in the same chapter should produce ONE entry in
    # critical_chapter_numbers.
    chapters = [
        _chapter(
            chapter_number=50,
            volume_number=2,
            text="甲乙丙。甲乙丙。甲乙丙。甲乙丙。",
        ),
        _chapter(
            chapter_number=30,
            volume_number=2,
            text="甲乙丙。甲乙丙。甲乙丙。",
        ),
    ]
    report = audit_novel_chapters(chapters, plans, volume_count=2)
    assert report.critical_chapter_numbers == (30, 50)


def test_pydantic_like_objects_are_accepted() -> None:
    """Callers may pass pydantic objects or dataclasses; the audit
    must still normalise them."""

    @dataclass
    class FakePlan:
        name: str
        scope_volume_number: int | None
        stages_of_relevance: list

    @dataclass
    class FakeChapter:
        chapter_number: int
        volume_number: int
        text: str

    plans = [
        FakePlan(name="李元霸", scope_volume_number=1, stages_of_relevance=[]),
        FakePlan(name="赵天罡", scope_volume_number=2, stages_of_relevance=[]),
    ]
    chapters = [
        FakeChapter(
            chapter_number=50,
            volume_number=2,
            text="李元霸。李元霸。李元霸。李元霸。",
        ),
    ]
    report = audit_novel_chapters(chapters, plans, volume_count=2)
    assert report.is_critical
    assert report.total_chapters == 1


def test_default_salience_threshold_is_three() -> None:
    """The contract: 3 mentions is the critical trigger; 2 is warning."""
    assert DEFAULT_SALIENCE_THRESHOLD == 3


def test_chapter_missing_text_is_skipped() -> None:
    """Chapters without written content should not produce findings."""
    plans = [_plan(name="李元霸", scope=1)]
    chapters = [
        _chapter(chapter_number=1, volume_number=1, text=""),
        _chapter(chapter_number=2, volume_number=1, text=""),
    ]
    report = audit_novel_chapters(chapters, plans, volume_count=1)
    assert report.total_chapters == 0
    assert report.critical_count == 0


def test_chapter_with_invalid_volume_is_skipped() -> None:
    plans = [_plan(name="李元霸", scope=1)]
    chapters = [
        _chapter(chapter_number=1, volume_number=0, text="李元霸。"),
    ]
    report = audit_novel_chapters(chapters, plans, volume_count=2)
    assert report.total_chapters == 0


# ---------------------------------------------------------------------------
# Review-gate merge (B10d): _merge_antagonist_scope_into_review
# ---------------------------------------------------------------------------

def _review_result_with_verdict(verdict: str, severity_max: str = "major"):
    """Build a minimal ChapterReviewResult for merge testing."""
    from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores

    scores = ChapterReviewScores(
        overall=0.75,
        goal=0.8,
        coverage=0.8,
        coherence=0.8,
        continuity=0.8,
        main_plot_progression=0.8,
        subplot_progression=0.5,
        style=0.8,
        hook=0.7,
        ending_hook_effectiveness=0.7,
        volume_mission_alignment=0.8,
        pacing_rhythm=0.7,
        character_voice_distinction=0.7,
        thematic_resonance=0.6,
        contract_alignment=0.8,
    )
    return ChapterReviewResult(
        verdict=verdict,
        severity_max=severity_max,
        scores=scores,
        findings=[],
        evidence_summary={"existing": "data"},
        rewrite_instructions=None,
    )


def test_merge_empty_findings_returns_input_unchanged() -> None:
    from bestseller.services.reviews import _merge_antagonist_scope_into_review

    review = _review_result_with_verdict("pass", "major")
    out = _merge_antagonist_scope_into_review(review, [], {})
    assert out.verdict == "pass"
    assert out.severity_max == "major"
    assert out.rewrite_instructions is None


def test_merge_critical_finding_forces_rewrite_verdict() -> None:
    from bestseller.domain.review import ChapterReviewFinding
    from bestseller.services.reviews import _merge_antagonist_scope_into_review

    review = _review_result_with_verdict("pass", "major")
    finding = ChapterReviewFinding(
        category="antagonist_scope",
        severity="critical",
        message="第 100 章（第 7 卷）提到了不属于本卷的敌人『元婴老者』12 次——禁止以当下视角大段使用他卷敌人。",
    )
    out = _merge_antagonist_scope_into_review(
        review,
        [finding],
        {"chapter_antagonist_audit": {"volume_number": 7}},
    )
    assert out.verdict == "rewrite"
    assert out.severity_max == "critical"
    assert out.rewrite_instructions is not None
    # The writer must learn which antagonist to stop using.
    assert "元婴老者" in out.rewrite_instructions
    # Evidence is merged not replaced.
    assert out.evidence_summary.get("existing") == "data"
    assert "chapter_antagonist_audit" in out.evidence_summary


def test_merge_warning_only_does_not_flip_pass_verdict() -> None:
    from bestseller.domain.review import ChapterReviewFinding
    from bestseller.services.reviews import _merge_antagonist_scope_into_review

    review = _review_result_with_verdict("pass", "major")
    finding = ChapterReviewFinding(
        category="antagonist_scope",
        severity="major",  # ChapterReviewFinding uses "major" (= warning)
        message="第 5 章（第 3 卷）提到了非本卷敌人『赵天罡』1 次。",
    )
    out = _merge_antagonist_scope_into_review(review, [finding], {})
    # Warning-only must not force rewrite.
    assert out.verdict == "pass"
    # But severity_max stays ≥ major (it was already major).
    assert out.severity_max == "major"
    assert out.rewrite_instructions is None


def test_merge_english_rewrite_instructions() -> None:
    from bestseller.domain.review import ChapterReviewFinding
    from bestseller.services.reviews import _merge_antagonist_scope_into_review

    review = _review_result_with_verdict("pass", "major")
    finding = ChapterReviewFinding(
        category="antagonist_scope",
        severity="critical",
        message=(
            "Chapter 50 (volume 3) mentions out-of-scope antagonist "
            "'Kaelvar' 8 times — present-tense use of a foreign-volume "
            "antagonist is forbidden."
        ),
    )
    out = _merge_antagonist_scope_into_review(
        review, [finding], {}, language="en-US"
    )
    assert out.verdict == "rewrite"
    # English finding messages don't contain 『 brackets, so the bad-name
    # extractor returns empty — verify the instruction still renders with
    # a stable prefix.
    assert out.rewrite_instructions is not None
    assert "antagonist scope" in out.rewrite_instructions


def test_merge_preserves_existing_rewrite_instructions() -> None:
    from bestseller.domain.review import ChapterReviewFinding
    from bestseller.services.reviews import _merge_antagonist_scope_into_review

    from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores

    base_scores = ChapterReviewScores(
        overall=0.4,
        goal=0.4, coverage=0.4, coherence=0.4, continuity=0.4,
        main_plot_progression=0.4, subplot_progression=0.4,
        style=0.4, hook=0.4, ending_hook_effectiveness=0.4,
        volume_mission_alignment=0.4, pacing_rhythm=0.4,
        character_voice_distinction=0.4, thematic_resonance=0.4,
        contract_alignment=0.4,
    )
    review = ChapterReviewResult(
        verdict="rewrite",
        severity_max="critical",
        scores=base_scores,
        findings=[],
        evidence_summary={},
        rewrite_instructions="Existing: rewrite the chapter opening hook.",
    )
    finding = ChapterReviewFinding(
        category="antagonist_scope",
        severity="critical",
        message="第 10 章（第 2 卷）提到了不属于本卷的敌人『李元霸』5 次。",
    )
    out = _merge_antagonist_scope_into_review(review, [finding], {})
    assert out.rewrite_instructions is not None
    # Antagonist prefix comes FIRST, existing instructions follow.
    assert out.rewrite_instructions.startswith("【敌人范围】")
    assert "Existing: rewrite the chapter opening hook." in out.rewrite_instructions


# ---------------------------------------------------------------------------
# Forward-only scoping (B11a): the review-gate must skip canon chapters
# ---------------------------------------------------------------------------


@dataclass
class _StubChapter:
    chapter_number: int
    volume_id: Any
    status: str
    volume_number: int = 0  # unused; kept for symmetry with ChapterModel


@dataclass
class _StubDraft:
    content_md: str


@dataclass
class _StubProject:
    id: str = "proj-id"
    language: str = "zh-CN"
    metadata_json: dict = None

    def __post_init__(self) -> None:
        if self.metadata_json is None:
            self.metadata_json = {}


class _RecordingSession:
    """Minimal AsyncSession stub that records whether DB access happened.

    The forward-only guards in ``_compute_chapter_antagonist_scope_signal``
    must early-return BEFORE any DB call, so ``scalar()`` / ``scalars()``
    being untouched is the positive signal that the guard fired.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def scalar(self, *args, **kwargs):
        self.calls.append("scalar")
        return None

    async def scalars(self, *args, **kwargs):
        self.calls.append("scalars")

        class _Empty:
            def __iter__(self):
                return iter([])

        return _Empty()


@pytest.mark.asyncio
async def test_gate_skips_chapter_with_status_complete() -> None:
    """Already-finalized chapters (canon) must not be retroactively
    flagged — the guard must early-return before touching the DB."""
    from bestseller.services.reviews import (
        _compute_chapter_antagonist_scope_signal,
    )

    session = _RecordingSession()
    project = _StubProject()
    chapter = _StubChapter(
        chapter_number=42,
        volume_id="vol-3-id",
        status="complete",
    )
    draft = _StubDraft(content_md="陆骁 " * 50)  # clearly foreign-antagonist

    findings, evidence = await _compute_chapter_antagonist_scope_signal(
        session=session,
        project=project,
        chapter=chapter,
        draft=draft,
    )
    assert findings == []
    assert evidence == {}
    # CRITICAL: no DB call must have happened — the guard fires first.
    assert session.calls == [], (
        f"Guard did not short-circuit for status=complete (DB calls: {session.calls})"
    )


@pytest.mark.asyncio
async def test_gate_skips_chapter_with_status_revision() -> None:
    """Chapters flagged by a prior review cycle (status=revision) must
    not be re-flagged here — the user asked for no retroactive
    changes."""
    from bestseller.services.reviews import (
        _compute_chapter_antagonist_scope_signal,
    )

    session = _RecordingSession()
    project = _StubProject()
    chapter = _StubChapter(
        chapter_number=99,
        volume_id="vol-5-id",
        status="revision",
    )
    draft = _StubDraft(content_md="元婴老者 " * 50)

    findings, evidence = await _compute_chapter_antagonist_scope_signal(
        session=session,
        project=project,
        chapter=chapter,
        draft=draft,
    )
    assert findings == []
    assert evidence == {}
    assert session.calls == []


@pytest.mark.asyncio
async def test_gate_skips_empty_draft() -> None:
    from bestseller.services.reviews import (
        _compute_chapter_antagonist_scope_signal,
    )

    session = _RecordingSession()
    project = _StubProject()
    chapter = _StubChapter(
        chapter_number=1,
        volume_id="vol-1-id",
        status="drafting",
    )
    draft = _StubDraft(content_md="")

    findings, _ = await _compute_chapter_antagonist_scope_signal(
        session=session,
        project=project,
        chapter=chapter,
        draft=draft,
    )
    assert findings == []
    assert session.calls == []


@pytest.mark.asyncio
async def test_gate_skips_chapter_with_no_volume() -> None:
    from bestseller.services.reviews import (
        _compute_chapter_antagonist_scope_signal,
    )

    session = _RecordingSession()
    project = _StubProject()
    chapter = _StubChapter(
        chapter_number=1,
        volume_id=None,  # unlinked
        status="drafting",
    )
    draft = _StubDraft(content_md="有敌人。")

    findings, _ = await _compute_chapter_antagonist_scope_signal(
        session=session,
        project=project,
        chapter=chapter,
        draft=draft,
    )
    assert findings == []
    assert session.calls == []


def test_merge_multiple_critical_findings_aggregates_bad_names() -> None:
    from bestseller.domain.review import ChapterReviewFinding
    from bestseller.services.reviews import _merge_antagonist_scope_into_review

    review = _review_result_with_verdict("pass", "major")
    findings = [
        ChapterReviewFinding(
            category="antagonist_scope",
            severity="critical",
            message="第 100 章（第 7 卷）提到了不属于本卷的敌人『元婴老者』12 次。",
        ),
        ChapterReviewFinding(
            category="antagonist_scope",
            severity="critical",
            message="第 100 章（第 7 卷）提到了不属于本卷的敌人『李元霸』4 次。",
        ),
    ]
    out = _merge_antagonist_scope_into_review(review, findings, {})
    assert out.verdict == "rewrite"
    assert "元婴老者" in out.rewrite_instructions
    assert "李元霸" in out.rewrite_instructions
