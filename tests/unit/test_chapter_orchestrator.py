from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from bestseller.domain.fanqie_market import (
    FanqieCategoryProfile,
    FanqieCompetitorProfile,
    FanqieCraftProfile,
    FanqieMarketAnalysisBundle,
    FanqieRankingSnapshot,
)
from bestseller.services.chapter_orchestrator import (
    grade_chapter,
    prepare_chapter_context,
    save_market_bundle,
    save_signature_plan,
)
from bestseller.services.signature_scene_planner import plan_signature_scenes
from bestseller.services.voice_dna_repository import save_voice_dna
from bestseller.services.voice_signature import extract_voice_dna_from_text

pytestmark = pytest.mark.unit


_SAMPLE_TEXT = (
    "夜色如墨，山风扑过，火光在崖边一闪而灭。\n"
    "他握紧剑柄，心中暗想：今夜若不退，便是死路一条。\n"
    "“你当真敢杀我？”那人冷冷一笑。\n"
    "他不答，只是出剑。剑光如电。\n"
    "下一刻，门外脚步声响起，名单还在他怀中。\n"
) * 80


def _make_bundle() -> FanqieMarketAnalysisBundle:
    return FanqieMarketAnalysisBundle(
        snapshot=FanqieRankingSnapshot(
            category="武侠",
            data_date=date(2026, 5, 20),
            fetched_at=datetime.now(UTC),
        ),
        competitor_profiles=[
            FanqieCompetitorProfile(
                source_book_id="b1",
                title="t",
                rank=1,
                hook_patterns=["下一刻", "名单"],
                structure_patterns=["case_to_action"],
            )
        ],
        category_profile=FanqieCategoryProfile(
            category="武侠",
            data_date=date(2026, 5, 20),
            sample_size=1,
            hook_patterns=["下一刻", "名单", "opening_crisis"],
            payoff_patterns=["终于明白"],
            confidence=0.6,
        ),
        craft_profile=FanqieCraftProfile(
            category="武侠",
            safety_boundary="只复用类目机制",
            confidence=0.6,
        ),
    )


def test_prepare_chapter_context_with_no_artifacts(tmp_path: Path) -> None:
    ctx = prepare_chapter_context("nothing", 1, output_base_dir=tmp_path)

    assert ctx.slug == "nothing"
    assert ctx.chapter_position == 1
    assert ctx.voice_dna is None
    # market constraints always built (with empty bundle fallback)
    assert ctx.market_constraints is not None
    assert ctx.prior_persona_feedback is None
    assert ctx.signature_scene_mandate is None


def test_prepare_chapter_context_rejects_invalid_position(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        prepare_chapter_context("x", 0, output_base_dir=tmp_path)


def test_prepare_chapter_context_loads_voice_dna(tmp_path: Path) -> None:
    dna = extract_voice_dna_from_text(_SAMPLE_TEXT, source_id="ctx", source_label="ctx-test")
    save_voice_dna(dna, "with-dna", output_base_dir=tmp_path)

    ctx = prepare_chapter_context("with-dna", 1, output_base_dir=tmp_path)

    assert ctx.voice_dna is not None
    assert ctx.voice_dna.source_label == "ctx-test"
    assert ctx.diagnostics["voice_dna"] == "loaded"


def test_prepare_chapter_context_loads_market_bundle(tmp_path: Path) -> None:
    save_market_bundle(_make_bundle(), "with-market", output_base_dir=tmp_path)

    ctx = prepare_chapter_context("with-market", 5, output_base_dir=tmp_path)

    assert ctx.market_constraints is not None
    assert ctx.market_constraints.category == "武侠"
    assert ctx.diagnostics["market_constraints"] == "compiled from bundle"


def test_prepare_chapter_context_loads_signature_plan(tmp_path: Path) -> None:
    plan = plan_signature_scenes(total_chapters=30, cadence=10)
    save_signature_plan(plan, "with-sig", output_base_dir=tmp_path)

    ctx = prepare_chapter_context("with-sig", 10, output_base_dir=tmp_path)

    assert ctx.signature_scene_mandate is not None
    assert ctx.signature_scene_mandate.chapter_position == 10

    block = ctx.signature_scene_block()
    assert "招牌场景" in block


def test_prepare_chapter_context_signature_mandate_absent_outside_slots(
    tmp_path: Path,
) -> None:
    plan = plan_signature_scenes(total_chapters=30, cadence=10)
    save_signature_plan(plan, "no-sig", output_base_dir=tmp_path)

    ctx = prepare_chapter_context("no-sig", 7, output_base_dir=tmp_path)

    assert ctx.signature_scene_mandate is None
    assert ctx.signature_scene_block() == ""


def test_grade_chapter_persists_feedback(tmp_path: Path) -> None:
    ctx = prepare_chapter_context("grade", 1, output_base_dir=tmp_path)

    result = grade_chapter(
        ctx, _SAMPLE_TEXT, output_base_dir=tmp_path
    )

    assert result.chapter_position == 1
    assert 0 <= result.weighted_score <= 1
    assert 0 <= result.abandon_rate <= 1
    feedback_file = (
        tmp_path / "grade" / "knowledge" / "persona-feedback" / "after-ch-001.json"
    )
    assert feedback_file.exists()


def test_grade_chapter_no_persist(tmp_path: Path) -> None:
    ctx = prepare_chapter_context("nopersist", 1, output_base_dir=tmp_path)

    grade_chapter(
        ctx, _SAMPLE_TEXT, output_base_dir=tmp_path, persist=False
    )

    feedback_dir = (
        tmp_path / "nopersist" / "knowledge" / "persona-feedback"
    )
    assert not feedback_dir.exists()


def test_grade_chapter_propagates_to_next_prep(tmp_path: Path) -> None:
    save_voice_dna(
        extract_voice_dna_from_text(_SAMPLE_TEXT, source_id="prop"),
        "loop",
        output_base_dir=tmp_path,
    )

    # Chapter 1: prep + grade + persist
    ctx1 = prepare_chapter_context("loop", 1, output_base_dir=tmp_path)
    grade_chapter(ctx1, _SAMPLE_TEXT, output_base_dir=tmp_path)

    # Chapter 2: prep should pick up chapter 1's feedback
    ctx2 = prepare_chapter_context("loop", 2, output_base_dir=tmp_path)
    assert ctx2.prior_persona_feedback is not None
    assert ctx2.prior_persona_feedback.chapter_position == 1


def test_save_market_bundle_round_trip(tmp_path: Path) -> None:
    save_market_bundle(_make_bundle(), "rt", output_base_dir=tmp_path)

    ctx = prepare_chapter_context("rt", 3, output_base_dir=tmp_path)

    assert ctx.market_constraints is not None
    assert ctx.diagnostics["market_constraints"] == "compiled from bundle"


def test_target_length_override(tmp_path: Path) -> None:
    ctx = prepare_chapter_context(
        "tl", 1, output_base_dir=tmp_path, target_length=5000
    )

    assert ctx.market_constraints is not None
    assert ctx.market_constraints.optimal_chapter_length_min < 5000
    assert ctx.market_constraints.optimal_chapter_length_max > 5000


def test_extra_safety_notes_propagate(tmp_path: Path) -> None:
    ctx = prepare_chapter_context(
        "sn",
        1,
        output_base_dir=tmp_path,
        extra_safety_notes=["不出现 X", "保留 Y"],
    )

    assert "不出现 X" in ctx.market_constraints.safety_boundary


# ---------------------------------------------------------------------------
# ensure_signature_plan / ensure_voice_dna — platform self-bootstrap
# ---------------------------------------------------------------------------


def test_ensure_signature_plan_creates_when_absent(tmp_path: Path) -> None:
    from bestseller.services.chapter_orchestrator import ensure_signature_plan

    plan = ensure_signature_plan(
        "boot-sig", total_chapters=30, output_base_dir=tmp_path
    )

    assert plan is not None
    assert len(plan.mandates) > 0
    plan_path = tmp_path / "boot-sig" / "story-bible" / "signature-scene-plan.json"
    assert plan_path.exists()

    # The freshly persisted plan is immediately visible to the pre-write
    # loader — golden-three means chapter 1 carries a mandate.
    ctx = prepare_chapter_context("boot-sig", 1, output_base_dir=tmp_path)
    assert ctx.signature_scene_mandate is not None


def test_ensure_signature_plan_never_overwrites_existing(tmp_path: Path) -> None:
    from bestseller.services.chapter_orchestrator import ensure_signature_plan

    original = plan_signature_scenes(total_chapters=30, cadence=10)
    save_signature_plan(original, "keep-sig", output_base_dir=tmp_path)

    plan = ensure_signature_plan(
        "keep-sig", total_chapters=500, output_base_dir=tmp_path
    )

    assert plan is not None
    assert len(plan.mandates) == len(original.mandates)


def test_ensure_signature_plan_rejects_invalid_total(tmp_path: Path) -> None:
    from bestseller.services.chapter_orchestrator import ensure_signature_plan

    plan = ensure_signature_plan(
        "bad-sig", total_chapters=0, output_base_dir=tmp_path
    )

    assert plan is None
    assert not (tmp_path / "bad-sig" / "story-bible" / "signature-scene-plan.json").exists()


def test_ensure_voice_dna_self_extracts_when_absent(tmp_path: Path) -> None:
    from bestseller.services.chapter_orchestrator import ensure_voice_dna

    dna = ensure_voice_dna(
        "boot-dna",
        sample_text=_SAMPLE_TEXT,
        source_id="self-ch1",
        output_base_dir=tmp_path,
    )

    assert dna is not None
    assert dna.source_id == "self-ch1"
    assert dna.sample_chars > 0
    assert (tmp_path / "boot-dna" / "story-bible" / "voice-dna.json").exists()

    ctx = prepare_chapter_context("boot-dna", 2, output_base_dir=tmp_path)
    assert ctx.voice_dna is not None
    assert ctx.diagnostics["voice_dna"] == "loaded"


def test_ensure_voice_dna_never_overwrites_existing(tmp_path: Path) -> None:
    from bestseller.services.chapter_orchestrator import ensure_voice_dna

    first = extract_voice_dna_from_text(
        _SAMPLE_TEXT, source_id="first", source_label="first"
    )
    save_voice_dna(first, "keep-dna", output_base_dir=tmp_path)

    dna = ensure_voice_dna(
        "keep-dna",
        sample_text="完全不同的文本。" * 500,
        source_id="self-ch9",
        output_base_dir=tmp_path,
    )

    assert dna is not None
    assert dna.source_id == "first"


def test_ensure_voice_dna_skips_short_sample(tmp_path: Path) -> None:
    from bestseller.services.chapter_orchestrator import ensure_voice_dna

    dna = ensure_voice_dna(
        "short-dna",
        sample_text="太短了。",
        source_id="self-ch1",
        min_sample_chars=2000,
        output_base_dir=tmp_path,
    )

    assert dna is None
    assert not (tmp_path / "short-dna" / "story-bible" / "voice-dna.json").exists()


def test_ensure_voice_dna_excludes_character_names(tmp_path: Path) -> None:
    from bestseller.services.chapter_orchestrator import ensure_voice_dna

    text = (
        "林晚舟握紧剑柄。林晚舟回头看了一眼。\n"
        "“林晚舟，你当真敢杀我？”那人冷冷一笑。\n"
        "林晚舟不答，只是出剑，剑光如电。\n"
    ) * 120

    dna = ensure_voice_dna(
        "excl-dna",
        sample_text=text,
        source_id="self-ch1",
        excluded_phrases=["林晚舟"],
        output_base_dir=tmp_path,
    )

    assert dna is not None
    assert not any("林晚舟" in cp for cp in dna.catchphrases)
