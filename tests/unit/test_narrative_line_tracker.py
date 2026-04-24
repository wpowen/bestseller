"""Phase B1 unit tests for narrative_line_tracker."""

from __future__ import annotations

import pytest

from bestseller.services.genre_profile_thresholds import (
    PacingThresholds,
    resolve_thresholds,
)
from bestseller.services.narrative_line_tracker import (
    HISTORY_WINDOW_CAP,
    LineClassification,
    LineGap,
    LineGapReport,
    append_history,
    classify_chapter,
    render_rotation_nudge,
    report_gaps,
)
from bestseller.services.narrative_lines import (
    LINE_CORE_AXIS,
    LINE_HIDDEN,
    LINE_OVERT,
    LINE_UNDERCURRENT,
)


# ---------------------------------------------------------------------------
# classify_chapter
# ---------------------------------------------------------------------------


class TestClassifier:
    def test_empty_chapter_returns_none(self) -> None:
        c = classify_chapter("", chapter_no=1)
        assert c.dominant_line is None
        assert c.support_lines == ()
        assert c.line_intensity == 0.0

    def test_below_threshold_returns_none(self) -> None:
        # Only two marker hits is below the _MIN_TOTAL_MARKERS floor (3).
        text = "他发起了一次战斗，紧接着又一次交手。"
        c = classify_chapter(text, chapter_no=1)
        assert c.dominant_line is None

    def test_overt_dominates_on_action_markers(self) -> None:
        text = (
            "他拔剑出鞘,开始了激烈的战斗。"
            "一次次交手,招式如同暴雨倾盆。"
            "敌人接连倒下,追击、伏击、厮杀不断。"
            "他直面仇敌,一招秒杀对手,完成了反击。"
        )
        c = classify_chapter(text, chapter_no=5)
        assert c.dominant_line == LINE_OVERT
        assert c.line_intensity > 0.4
        assert c.chapter_no == 5

    def test_hidden_dominates_on_bloodline_markers(self) -> None:
        text = (
            "封印松动的那一刻,远古的血脉觉醒,"
            "上古传说中的禁术开始回荡。"
            "他的前世与今生交织,天机显现,"
            "因果轮回,宿命的齿轮终于转动。"
            "神域的梦境里,隐秘的真相若隐若现。"
        )
        c = classify_chapter(text, chapter_no=12)
        assert c.dominant_line == LINE_HIDDEN

    def test_undercurrent_dominates_on_faction_markers(self) -> None:
        text = (
            "几大势力暗中布局,各自的阴谋浮出水面。"
            "他的眼线送来了密报,幕后的操纵者终于露面。"
            "门派与宗门的暗斗背后,还有更大的阵营在谋划。"
            "一丝线索让他窥见了真相:内鬼就在身边。"
        )
        c = classify_chapter(text, chapter_no=20)
        assert c.dominant_line == LINE_UNDERCURRENT

    def test_core_axis_dominates_on_theme_markers(self) -> None:
        text = (
            "他再次站在抉择的路口,问自己的初心是什么。"
            "活着的意义,坚持的代价,都压在他肩头。"
            "承诺不能放下,誓言不可违背,道心要坚守到底。"
            "他终于明白,成为自己比超越他人更重要。"
        )
        c = classify_chapter(text, chapter_no=33)
        assert c.dominant_line == LINE_CORE_AXIS

    def test_support_lines_populated(self) -> None:
        # Dense overt with secondary hidden content
        text = (
            "战斗与交手持续进行,敌人一个接一个地被打倒。"
            "招式凌厉,剑气纵横,擂台之上他斩杀了数名仇敌。"
            "与此同时,血脉觉醒的征兆出现,前世的封印出现裂痕,"
            "上古的预言似乎应验了。"
        )
        c = classify_chapter(text, chapter_no=7)
        assert c.dominant_line == LINE_OVERT
        assert LINE_HIDDEN in c.support_lines

    def test_outline_hint_boosts_signal(self) -> None:
        body = "他走在山路上。"  # low-signal body
        hint = (
            "本章要点:封印松动、血脉觉醒、"
            "远古禁忌、前世因果、宿命显现"
        )
        c = classify_chapter(body, chapter_no=1, outline_hint=hint)
        assert c.dominant_line == LINE_HIDDEN

    def test_english_language_uses_en_markers(self) -> None:
        text = (
            "The fight erupted in the courtyard. Battle cries echoed "
            "as rival factions clashed. Each strike, each duel, each "
            "combat round brought new enemies into view. The target "
            "was clear: engage the mission head-on, no matter the cost."
        )
        c = classify_chapter(text, chapter_no=4, language="en")
        assert c.dominant_line == LINE_OVERT


# ---------------------------------------------------------------------------
# report_gaps
# ---------------------------------------------------------------------------


def _entry(chapter_no: int, line: str, intensity: float = 0.6) -> dict:
    return {
        "chapter_no": chapter_no,
        "dominant_line": line,
        "line_intensity": intensity,
    }


class TestReportGaps:
    def test_empty_history_treats_gap_as_current_chapter(self) -> None:
        rep = report_gaps(
            project_id="p1",
            current_chapter=3,
            history=[],
            genre_id="action-progression",
        )
        for g in rep.gaps:
            assert g.last_dominant_chapter is None
            assert g.current_gap == 3

    def test_over_gap_fires_when_threshold_exceeded(self) -> None:
        # Action-progression strand_max_gap.overt = 5
        history = [_entry(1, LINE_OVERT)]
        rep = report_gaps(
            project_id="p1",
            current_chapter=10,
            history=history,
            genre_id="action-progression",
        )
        overt = next(g for g in rep.gaps if g.line_id == LINE_OVERT)
        assert overt.current_gap == 9
        assert overt.severity == "over"
        assert overt.is_over

    def test_warn_gap_fires_at_80_percent(self) -> None:
        # overt threshold = 5, warn at 0.8 × 5 = 4. Gap of 4 is warn;
        # gap of 6 would be over.
        history = [_entry(5, LINE_OVERT)]
        rep = report_gaps(
            project_id="p1",
            current_chapter=9,
            history=history,
            genre_id="action-progression",
        )
        overt = next(g for g in rep.gaps if g.line_id == LINE_OVERT)
        assert overt.current_gap == 4
        assert overt.severity == "warn"

    def test_low_intensity_not_counted_as_dominance(self) -> None:
        # Intensity below default 0.3 threshold → doesn't reset the gap.
        history = [_entry(5, LINE_OVERT, intensity=0.1)]
        rep = report_gaps(
            project_id="p1",
            current_chapter=7,
            history=history,
            genre_id="action-progression",
        )
        overt = next(g for g in rep.gaps if g.line_id == LINE_OVERT)
        assert overt.last_dominant_chapter is None

    def test_needs_nudge_when_any_gap_warn_or_over(self) -> None:
        rep = report_gaps(
            project_id="p1",
            current_chapter=20,
            history=[_entry(1, LINE_OVERT)],
            genre_id="action-progression",
        )
        assert rep.needs_nudge is True

    def test_needs_nudge_false_when_all_recent(self) -> None:
        history = [
            _entry(4, LINE_OVERT),       # gap 1
            _entry(3, LINE_UNDERCURRENT),  # gap 2
            _entry(2, LINE_HIDDEN),      # gap 3
            _entry(1, LINE_CORE_AXIS),   # gap 4
        ]
        rep = report_gaps(
            project_id="p1",
            current_chapter=5,
            history=history,
            genre_id="action-progression",
        )
        assert rep.needs_nudge is False

    def test_pacing_config_wins_over_genre(self) -> None:
        # Custom pacing config with tiny thresholds
        cfg = PacingThresholds(
            stagnation_threshold=2,
            strand_max_gap={
                LINE_OVERT: 1,
                LINE_UNDERCURRENT: 1,
                LINE_HIDDEN: 1,
                LINE_CORE_AXIS: 1,
            },
            transition_max_consecutive=1,
        )
        rep = report_gaps(
            project_id="p1",
            current_chapter=3,
            history=[_entry(1, LINE_OVERT)],
            pacing_config=cfg,
        )
        overt = next(g for g in rep.gaps if g.line_id == LINE_OVERT)
        assert overt.threshold == 1
        assert overt.severity == "over"

    def test_unknown_line_id_ignored(self) -> None:
        rep = report_gaps(
            project_id="p1",
            current_chapter=5,
            history=[
                {
                    "chapter_no": 1,
                    "dominant_line": "nonsense_layer",
                    "line_intensity": 1.0,
                },
                _entry(2, LINE_OVERT),
            ],
            genre_id="action-progression",
        )
        overt = next(g for g in rep.gaps if g.line_id == LINE_OVERT)
        assert overt.last_dominant_chapter == 2


# ---------------------------------------------------------------------------
# append_history — rolling window
# ---------------------------------------------------------------------------


class TestHistoryAppend:
    def test_appends_and_sorts(self) -> None:
        existing = [_entry(3, LINE_OVERT), _entry(1, LINE_UNDERCURRENT)]
        rolled = append_history(
            existing,
            LineClassification(
                chapter_no=2,
                dominant_line=LINE_HIDDEN,
                support_lines=(),
                line_intensity=0.5,
            ),
        )
        assert [r["chapter_no"] for r in rolled] == [1, 2, 3]
        assert rolled[1]["dominant_line"] == LINE_HIDDEN

    def test_caps_at_window_size(self) -> None:
        existing = [_entry(i, LINE_OVERT) for i in range(1, HISTORY_WINDOW_CAP + 1)]
        rolled = append_history(
            existing,
            LineClassification(
                chapter_no=HISTORY_WINDOW_CAP + 1,
                dominant_line=LINE_HIDDEN,
                support_lines=(),
                line_intensity=0.7,
            ),
        )
        assert len(rolled) == HISTORY_WINDOW_CAP
        # Oldest (chapter 1) was evicted.
        assert rolled[0]["chapter_no"] == 2
        assert rolled[-1]["chapter_no"] == HISTORY_WINDOW_CAP + 1

    def test_replaces_same_chapter(self) -> None:
        existing = [_entry(5, LINE_OVERT)]
        rolled = append_history(
            existing,
            LineClassification(
                chapter_no=5,
                dominant_line=LINE_HIDDEN,
                support_lines=(),
                line_intensity=0.5,
            ),
        )
        assert len(rolled) == 1
        assert rolled[0]["dominant_line"] == LINE_HIDDEN

    def test_none_existing_ok(self) -> None:
        rolled = append_history(
            None,
            LineClassification(
                chapter_no=1,
                dominant_line=LINE_OVERT,
                support_lines=(),
                line_intensity=0.6,
            ),
        )
        assert len(rolled) == 1


# ---------------------------------------------------------------------------
# render_rotation_nudge
# ---------------------------------------------------------------------------


class TestNudgeRenderer:
    def test_no_nudge_when_all_ok(self) -> None:
        history = [
            _entry(4, LINE_OVERT),
            _entry(3, LINE_UNDERCURRENT),
            _entry(2, LINE_HIDDEN),
            _entry(1, LINE_CORE_AXIS),
        ]
        rep = report_gaps(
            project_id="p1",
            current_chapter=5,
            history=history,
            genre_id="action-progression",
        )
        assert render_rotation_nudge(rep) == ""

    def test_nudges_most_overdue_layer(self) -> None:
        # Overt hasn't appeared in 20 chapters → will be "over" by a wide
        # margin; hidden hasn't either but its threshold is larger.
        rep = report_gaps(
            project_id="p1",
            current_chapter=25,
            history=[_entry(1, LINE_CORE_AXIS)],
            genre_id="action-progression",
        )
        nudge = render_rotation_nudge(rep)
        assert nudge
        # Overt has the tightest budget so it will be the most over-target.
        assert "明线" in nudge

    def test_english_nudge(self) -> None:
        rep = report_gaps(
            project_id="p1",
            current_chapter=25,
            history=[_entry(1, LINE_CORE_AXIS)],
            genre_id="action-progression",
        )
        nudge = render_rotation_nudge(rep, language="en")
        assert "Line rotation nudge" in nudge
        assert "overt line" in nudge


# ---------------------------------------------------------------------------
# LineGapCheck — integration with chapter_validator's Violation pipeline.
# ---------------------------------------------------------------------------


class TestLineGapCheckValidator:
    """Smoke-test the Phase B1 chapter_validator check reads the report."""

    def _make_ctx(self, report):
        import uuid

        from bestseller.services.invariants import LengthEnvelope, ProjectInvariants
        from bestseller.services.output_validator import ValidationContext

        invariants = ProjectInvariants(
            project_id=uuid.uuid4(),
            language="zh-CN",
            length_envelope=LengthEnvelope(
                min_chars=3000, target_chars=3500, max_chars=4000
            ),
        )
        return ValidationContext(
            invariants=invariants,
            chapter_no=25,
            scope="chapter",
            line_gap_report=report,
        )

    def test_no_report_is_noop(self) -> None:
        from bestseller.services.chapter_validator import LineGapCheck

        ctx = self._make_ctx(None)
        assert list(LineGapCheck().run("any text", ctx)) == []

    def test_over_gap_emits_block(self) -> None:
        from bestseller.services.chapter_validator import LineGapCheck

        rep = report_gaps(
            project_id="p1",
            current_chapter=25,
            history=[_entry(1, LINE_OVERT)],
            genre_id="action-progression",
        )
        ctx = self._make_ctx(rep)
        vios = list(LineGapCheck().run("本章内容", ctx))
        assert any(v.code == "LINE_GAP_OVER" and v.severity == "block" for v in vios)

    def test_warn_gap_emits_warn(self) -> None:
        from bestseller.services.chapter_validator import LineGapCheck

        # Gap of 4 on overt threshold 5 → warn
        rep = report_gaps(
            project_id="p1",
            current_chapter=9,
            history=[_entry(5, LINE_OVERT)],
            genre_id="action-progression",
        )
        ctx = self._make_ctx(rep)
        vios = list(LineGapCheck().run("any", ctx))
        assert any(v.code == "LINE_GAP_WARN" and v.severity == "warn" for v in vios)

    def test_factory_includes_line_gap_check(self) -> None:
        from bestseller.services.chapter_validator import (
            LineGapCheck,
            build_chapter_validator_checks,
        )

        checks = build_chapter_validator_checks()
        assert any(isinstance(c, LineGapCheck) for c in checks)


# ---------------------------------------------------------------------------
# write_gate integration (Phase B1 warm-up rule)
# ---------------------------------------------------------------------------


class TestWriteGateLineGapWarmup:
    def test_line_gap_over_is_audit_only_during_warmup(self) -> None:
        from bestseller.services.write_gate import resolve_mode

        for ch in range(1, 11):
            assert resolve_mode("LINE_GAP_OVER", chapter_no=ch) == "audit_only"

    def test_line_gap_over_blocks_after_warmup(self) -> None:
        from bestseller.services.write_gate import resolve_mode

        assert resolve_mode("LINE_GAP_OVER", chapter_no=11) == "block"
        assert resolve_mode("LINE_GAP_OVER", chapter_no=42) == "block"

    def test_line_gap_warn_never_blocks(self) -> None:
        from bestseller.services.write_gate import resolve_mode

        for ch in (1, 5, 11, 100):
            assert resolve_mode("LINE_GAP_WARN", chapter_no=ch) == "audit_only"


# ---------------------------------------------------------------------------
# prompt_constructor.build_line_rotation_nudge
# ---------------------------------------------------------------------------


class TestPromptConstructorNudge:
    def test_none_report_returns_empty(self) -> None:
        from bestseller.services.prompt_constructor import (
            build_line_rotation_nudge,
        )

        assert build_line_rotation_nudge(None) == ""

    def test_report_with_over_gap_returns_nudge(self) -> None:
        from bestseller.services.prompt_constructor import (
            build_line_rotation_nudge,
        )

        rep = report_gaps(
            project_id="p1",
            current_chapter=25,
            history=[_entry(1, LINE_CORE_AXIS)],
            genre_id="action-progression",
        )
        nudge = build_line_rotation_nudge(rep)
        assert nudge
        assert "叙事线轮换提示" in nudge


# ---------------------------------------------------------------------------
# Phase B2 persistence helpers.
# ---------------------------------------------------------------------------


class TestHistoryPersistence:
    def test_load_history_handles_missing(self) -> None:
        from bestseller.services.narrative_line_tracker import load_history

        assert load_history(None) == []
        assert load_history({}) == []
        assert load_history({"something_else": 1}) == []

    def test_load_history_filters_bad_entries(self) -> None:
        from bestseller.services.narrative_line_tracker import (
            METADATA_HISTORY_KEY,
            load_history,
        )

        meta = {
            METADATA_HISTORY_KEY: [
                _entry(1, LINE_OVERT),
                {"no_chapter_no": True},
                _entry(2, LINE_HIDDEN),
                None,
            ]
        }
        rows = load_history(meta)
        assert [r["chapter_no"] for r in rows] == [1, 2]

    def test_persist_history_returns_fresh_dict(self) -> None:
        from bestseller.services.narrative_line_tracker import (
            METADATA_HISTORY_KEY,
            persist_history,
        )

        meta = {"keep_me": "ok"}
        out = persist_history(
            meta,
            LineClassification(
                chapter_no=1,
                dominant_line=LINE_OVERT,
                support_lines=(),
                line_intensity=0.6,
            ),
        )
        assert out is not meta
        assert out["keep_me"] == "ok"
        assert len(out[METADATA_HISTORY_KEY]) == 1
        # Original metadata remains untouched.
        assert METADATA_HISTORY_KEY not in meta

    def test_persist_history_caps_and_sorts(self) -> None:
        from bestseller.services.narrative_line_tracker import (
            METADATA_HISTORY_KEY,
            persist_history,
        )

        base: dict = {METADATA_HISTORY_KEY: [_entry(i, LINE_OVERT) for i in range(1, 55)]}
        out = persist_history(
            base,
            LineClassification(
                chapter_no=55,
                dominant_line=LINE_HIDDEN,
                support_lines=(),
                line_intensity=0.7,
            ),
        )
        hist = out[METADATA_HISTORY_KEY]
        assert len(hist) == HISTORY_WINDOW_CAP
        assert hist[-1]["chapter_no"] == 55
        # Oldest entries evicted.
        assert hist[0]["chapter_no"] == 55 - HISTORY_WINDOW_CAP + 1
