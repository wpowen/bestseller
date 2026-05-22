"""Unit tests for the P1 Originality Engine wire-up.

Covers the integration surface that ``pipelines.py`` depends on:
    * OriginalityEngineConfig defaults + YAML parsing
    * SceneWriterContextPacket / ChapterWriterContextPacket carry the new
      four blocks as ``str | None`` (default None)
    * prepare_chapter_context → render_* → packet stamping round-trip
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.domain.context import (
    ChapterWriterContextPacket,
    SceneWriterContextPacket,
)
from bestseller.services.chapter_orchestrator import (
    prepare_chapter_context,
    save_signature_plan,
)
from bestseller.services.market_constraint_compiler import (
    render_chapter_constraints_block,
)
from bestseller.services.quality_gates_config import (
    OriginalityEngineConfig,
    load_quality_gates_config,
)
from bestseller.services.reader_persona_simulator import (
    render_persona_feedback_block,
)
from bestseller.services.signature_scene_planner import (
    plan_signature_scenes,
    render_signature_scene_block,
)
from bestseller.services.voice_dna_repository import save_voice_dna
from bestseller.services.voice_signature import (
    extract_voice_dna_from_text,
    render_voice_dna_block,
)

pytestmark = pytest.mark.unit


_SAMPLE_TEXT = (
    "夜色如墨，山风扑过，火光在崖边一闪而灭。\n"
    "他握紧剑柄，心中暗想：今夜若不退，便是死路一条。\n"
    "“你当真敢杀我？”那人冷冷一笑。\n"
    "他不答，只是出剑。剑光如电。\n"
    "下一刻，门外脚步声响起，名单还在他怀中。\n"
) * 80


# ---------- OriginalityEngineConfig ----------


def test_originality_engine_config_defaults_enabled() -> None:
    cfg = OriginalityEngineConfig()

    assert cfg.enabled is True
    assert cfg.persist_persona_feedback is True
    assert cfg.mode_b_override is None
    assert cfg.grading_text_cap_chars == 12_000
    assert cfg.retention_max_retries == 5
    assert cfg.retention_escalate_after == 3


def test_originality_engine_config_is_frozen() -> None:
    cfg = OriginalityEngineConfig()
    with pytest.raises(Exception):
        cfg.enabled = False  # type: ignore[misc]


def test_originality_engine_yaml_parsing_full(tmp_path: Path) -> None:
    yaml_path = tmp_path / "qg.yaml"
    yaml_path.write_text(
        """
originality_engine:
  enabled: false
  persist_persona_feedback: false
  mode_b_override: true
  grading_text_cap_chars: 8000
  retention_max_retries: 7
  retention_escalate_after: 4
""",
        encoding="utf-8",
    )

    cfg = load_quality_gates_config(yaml_path)

    assert cfg.originality_engine.enabled is False
    assert cfg.originality_engine.persist_persona_feedback is False
    assert cfg.originality_engine.mode_b_override is True
    assert cfg.originality_engine.grading_text_cap_chars == 8000
    assert cfg.originality_engine.retention_max_retries == 7
    assert cfg.originality_engine.retention_escalate_after == 4


def test_originality_engine_yaml_parsing_partial(tmp_path: Path) -> None:
    """Missing keys fall back to defaults — backward compat."""

    yaml_path = tmp_path / "qg.yaml"
    yaml_path.write_text(
        "originality_engine:\n  enabled: false\n",
        encoding="utf-8",
    )

    cfg = load_quality_gates_config(yaml_path)

    assert cfg.originality_engine.enabled is False
    # Unset keys retain defaults:
    assert cfg.originality_engine.persist_persona_feedback is True
    assert cfg.originality_engine.mode_b_override is None
    assert cfg.originality_engine.grading_text_cap_chars == 12_000
    assert cfg.originality_engine.retention_max_retries == 5
    assert cfg.originality_engine.retention_escalate_after == 3


def test_originality_engine_yaml_no_block_uses_defaults(tmp_path: Path) -> None:
    """Existing YAMLs without the new block are unaffected."""

    yaml_path = tmp_path / "qg.yaml"
    yaml_path.write_text("l1_invariants:\n  enabled: true\n", encoding="utf-8")

    cfg = load_quality_gates_config(yaml_path)

    assert cfg.originality_engine.enabled is True  # default
    assert isinstance(cfg.originality_engine, OriginalityEngineConfig)


def test_originality_engine_yaml_clamps_grading_cap(tmp_path: Path) -> None:
    """Implausibly-small grading cap gets clamped to a safe floor."""

    yaml_path = tmp_path / "qg.yaml"
    yaml_path.write_text(
        "originality_engine:\n  grading_text_cap_chars: 10\n",
        encoding="utf-8",
    )

    cfg = load_quality_gates_config(yaml_path)

    assert cfg.originality_engine.grading_text_cap_chars >= 500


# ---------- Context packets ----------


def test_scene_packet_originality_fields_default_none() -> None:
    from uuid import uuid4

    packet = SceneWriterContextPacket(
        project_id=uuid4(),
        project_slug="test",
        chapter_id=uuid4(),
        scene_id=uuid4(),
        scene_number=1,
        chapter_number=1,
        query_text="x",
    )

    assert packet.voice_dna_block is None
    assert packet.chapter_market_constraints_block is None
    assert packet.signature_scene_block is None
    assert packet.prior_persona_feedback_block is None


def test_scene_packet_originality_fields_assignable() -> None:
    from uuid import uuid4

    packet = SceneWriterContextPacket(
        project_id=uuid4(),
        project_slug="test",
        chapter_id=uuid4(),
        scene_id=uuid4(),
        scene_number=1,
        chapter_number=1,
        query_text="x",
    )
    packet.voice_dna_block = "【作者声纹 DNA】"
    packet.chapter_market_constraints_block = "【市场硬约束】"
    packet.signature_scene_block = "【招牌场景指令】"
    packet.prior_persona_feedback_block = "【上章读者画像反馈】"

    assert packet.voice_dna_block == "【作者声纹 DNA】"
    assert packet.chapter_market_constraints_block == "【市场硬约束】"
    assert packet.signature_scene_block == "【招牌场景指令】"
    assert packet.prior_persona_feedback_block == "【上章读者画像反馈】"


def test_chapter_packet_originality_fields_default_none() -> None:
    from uuid import uuid4

    packet = ChapterWriterContextPacket(
        project_id=uuid4(),
        project_slug="slug",
        chapter_id=uuid4(),
        chapter_number=1,
        query_text="x",
        chapter_goal="x",
    )

    assert packet.voice_dna_block is None
    assert packet.chapter_market_constraints_block is None
    assert packet.signature_scene_block is None
    assert packet.prior_persona_feedback_block is None


# ---------- Integration: orchestrator → render → packet ----------


def test_orchestrator_to_packet_round_trip(tmp_path: Path) -> None:
    """Simulates exactly what pipelines.py does: prepare context, render
    each block via the render_* helpers, stamp onto the packet."""

    from uuid import uuid4

    # Seed DNA
    dna = extract_voice_dna_from_text(
        _SAMPLE_TEXT, source_id="rt", source_label="round-trip"
    )
    save_voice_dna(dna, "rtbook", output_base_dir=tmp_path)

    # Seed signature plan
    plan = plan_signature_scenes(total_chapters=30, cadence=10)
    save_signature_plan(plan, "rtbook", output_base_dir=tmp_path)

    # Prepare context
    ctx = prepare_chapter_context("rtbook", 10, output_base_dir=tmp_path)

    # Build a Scene packet and apply the same logic pipelines.py does
    packet = SceneWriterContextPacket(
        project_id=uuid4(),
        project_slug="rtbook",
        chapter_id=uuid4(),
        scene_id=uuid4(),
        scene_number=1,
        chapter_number=10,
        query_text="x",
    )

    if ctx.voice_dna is not None:
        packet.voice_dna_block = (
            render_voice_dna_block(ctx.voice_dna, language="zh-CN") or None
        )
    if ctx.market_constraints is not None:
        packet.chapter_market_constraints_block = (
            render_chapter_constraints_block(
                ctx.market_constraints, language="zh-CN"
            )
            or None
        )
    if ctx.signature_scene_mandate is not None:
        packet.signature_scene_block = (
            render_signature_scene_block(
                ctx.signature_scene_mandate, language="zh-CN"
            )
            or None
        )
    if ctx.prior_persona_feedback is not None:
        packet.prior_persona_feedback_block = (
            render_persona_feedback_block(
                ctx.prior_persona_feedback, language="zh-CN"
            )
            or None
        )

    assert packet.voice_dna_block is not None
    assert "作者声纹" in packet.voice_dna_block
    assert packet.chapter_market_constraints_block is not None
    assert "市场硬约束" in packet.chapter_market_constraints_block
    assert packet.signature_scene_block is not None
    assert "招牌场景指令" in packet.signature_scene_block
    # No prior chapter yet → no feedback block
    assert packet.prior_persona_feedback_block is None


def test_orchestrator_skips_blocks_when_no_artifacts(tmp_path: Path) -> None:
    """When no DNA / signature plan / feedback exist, all four block
    fields stay None — confirming the wire-up is no-op for fresh projects."""

    from uuid import uuid4

    ctx = prepare_chapter_context("empty", 1, output_base_dir=tmp_path)

    packet = SceneWriterContextPacket(
        project_id=uuid4(),
        project_slug="test",
        chapter_id=uuid4(),
        scene_id=uuid4(),
        scene_number=1,
        chapter_number=1,
        query_text="x",
    )

    if ctx.voice_dna is not None:
        packet.voice_dna_block = render_voice_dna_block(ctx.voice_dna)
    if ctx.market_constraints is not None:
        packet.chapter_market_constraints_block = (
            render_chapter_constraints_block(ctx.market_constraints) or None
        )
    if ctx.signature_scene_mandate is not None:
        packet.signature_scene_block = (
            render_signature_scene_block(ctx.signature_scene_mandate) or None
        )
    if ctx.prior_persona_feedback is not None:
        packet.prior_persona_feedback_block = render_persona_feedback_block(
            ctx.prior_persona_feedback
        )

    assert packet.voice_dna_block is None
    # market_constraints is always built (with empty bundle fallback) →
    # constraints block IS rendered (band info + length range) even with
    # no bundle. This is by design.
    assert packet.chapter_market_constraints_block is not None
    assert "市场硬约束" in packet.chapter_market_constraints_block
    assert packet.signature_scene_block is None
    assert packet.prior_persona_feedback_block is None


def test_orchestrator_builds_hook_echo_block_from_prev_chapter_text(
    tmp_path: Path,
) -> None:
    prev_text = (
        "下一刻，门外脚步声响起。突然，墙后传来一声低咳——"
        "竟是他以为已死之人。未完——"
    )

    ctx = prepare_chapter_context(
        "echo",
        2,
        output_base_dir=tmp_path,
        prev_chapter_text=prev_text,
    )

    assert ctx.hook_echo_report is not None
    block = ctx.hook_echo_block(language="zh-CN")
    assert "钩子回环" in block
    assert "脚步声" in block or "低咳" in block
