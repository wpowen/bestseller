from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from bestseller.cli.book_writer import book_app
from bestseller.services.chapter_orchestrator import save_signature_plan
from bestseller.services.signature_scene_planner import plan_signature_scenes
from bestseller.services.voice_dna_repository import save_voice_dna
from bestseller.services.voice_signature import extract_voice_dna_from_text

pytestmark = pytest.mark.unit


runner = CliRunner()


_CHAPTER_TEXT = (
    "夜色如墨，山风扑过，火光在崖边一闪而灭。\n"
    "他握紧剑柄，心中暗想：今夜若不退，便是死路一条。\n"
    "“你当真敢杀我？”那人冷冷一笑。\n"
    "他不答，只是出剑。剑光如电。\n"
    "下一刻，门外脚步声响起，名单还在他怀中。\n"
) * 60


def test_plan_concept_emits_and_persists(tmp_path: Path) -> None:
    result = runner.invoke(
        book_app,
        [
            "plan-concept",
            "--slug",
            "demo",
            "--seed",
            "42",
            "--samples",
            "30",
            "--top",
            "2",
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "concept leap written" in result.output
    assert "概念跨界候选" in result.output

    path = tmp_path / "demo" / "story-bible" / "concept-leap.json"
    assert path.exists()


def test_plan_concept_refuses_overwrite(tmp_path: Path) -> None:
    args = [
        "plan-concept",
        "--slug",
        "ow",
        "--seed",
        "1",
        "--samples",
        "20",
        "--top",
        "1",
        "--output-base-dir",
        str(tmp_path),
    ]
    first = runner.invoke(book_app, args)
    assert first.exit_code == 0

    second = runner.invoke(book_app, args)
    assert second.exit_code == 1
    assert "already exists" in second.output


def test_plan_signatures_emits_mandates_and_file(tmp_path: Path) -> None:
    result = runner.invoke(
        book_app,
        [
            "plan-signatures",
            "--slug",
            "sigdemo",
            "--total",
            "30",
            "--cadence",
            "10",
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "signature scene plan written" in result.output

    path = tmp_path / "sigdemo" / "story-bible" / "signature-scene-plan.json"
    assert path.exists()
    # Each slot line should be printed
    assert "ch  10" in result.output or "ch10" in result.output or " ch" in result.output


def test_plan_signatures_overwrite_required(tmp_path: Path) -> None:
    base = [
        "plan-signatures",
        "--slug",
        "sigow",
        "--total",
        "20",
        "--output-base-dir",
        str(tmp_path),
    ]
    runner.invoke(book_app, base)
    second = runner.invoke(book_app, base)
    assert second.exit_code == 1


def test_prepare_chapter_empty_project(tmp_path: Path) -> None:
    result = runner.invoke(
        book_app,
        [
            "prepare-chapter",
            "--slug",
            "empty",
            "--chapter",
            "1",
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "chapter context" in result.output
    assert "diagnostics" in result.output
    # market constraints always render
    assert "市场硬约束" in result.output


def test_prepare_chapter_with_voice_and_signatures(tmp_path: Path) -> None:
    # Seed DNA
    save_voice_dna(
        extract_voice_dna_from_text(_CHAPTER_TEXT, source_id="x", source_label="X 风"),
        "rich",
        output_base_dir=tmp_path,
    )
    # Seed signature plan via CLI — no anchors/outline → skeleton mandates.
    runner.invoke(
        book_app,
        [
            "plan-signatures",
            "--slug",
            "rich",
            "--total",
            "30",
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    result = runner.invoke(
        book_app,
        [
            "prepare-chapter",
            "--slug",
            "rich",
            "--chapter",
            "10",
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "作者声纹" in result.output  # voice DNA block rendered
    # R25: a skeleton mandate (no concrete target) must NOT reach the writer.
    assert "招牌场景指令" not in result.output

    # Re-save the plan with outline-derived concrete targets → block renders.
    concrete_plan = plan_signature_scenes(
        total_chapters=30,
        chapter_outline={
            10: {
                "title": "断剑之约",
                "goal": "他在崖边立誓夺回名单",
                "signature_images": ["崖边火光中折断的剑"],
            }
        },
    )
    save_signature_plan(concrete_plan, "rich", output_base_dir=tmp_path)

    rich_result = runner.invoke(
        book_app,
        [
            "prepare-chapter",
            "--slug",
            "rich",
            "--chapter",
            "10",
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    assert rich_result.exit_code == 0, rich_result.output
    assert "招牌场景指令" in rich_result.output  # ready mandate rendered
    assert "崖边火光中折断的剑" in rich_result.output


def test_grade_chapter_persists_and_reports(tmp_path: Path) -> None:
    chapter_file = tmp_path / "ch1.md"
    chapter_file.write_text(_CHAPTER_TEXT, encoding="utf-8")

    result = runner.invoke(
        book_app,
        [
            "grade-chapter",
            "--slug",
            "grade",
            "--chapter",
            "1",
            "--text-file",
            str(chapter_file),
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "persona verdict" in result.output
    assert "weighted score" in result.output

    feedback_path = (
        tmp_path / "grade" / "knowledge" / "persona-feedback" / "after-ch-001.json"
    )
    assert feedback_path.exists()


def test_grade_chapter_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(
        book_app,
        [
            "grade-chapter",
            "--slug",
            "missing",
            "--chapter",
            "1",
            "--text-file",
            str(tmp_path / "nope.md"),
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 2
    assert "not found" in result.output


def test_grade_chapter_no_persist_flag(tmp_path: Path) -> None:
    chapter_file = tmp_path / "ch1.md"
    chapter_file.write_text(_CHAPTER_TEXT, encoding="utf-8")

    result = runner.invoke(
        book_app,
        [
            "grade-chapter",
            "--slug",
            "noper",
            "--chapter",
            "1",
            "--text-file",
            str(chapter_file),
            "--no-persist",
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    feedback_dir = (
        tmp_path / "noper" / "knowledge" / "persona-feedback"
    )
    assert not feedback_dir.exists()


def test_grade_chapter_feedback_feeds_next_prep(tmp_path: Path) -> None:
    chapter_file = tmp_path / "ch1.md"
    chapter_file.write_text(_CHAPTER_TEXT, encoding="utf-8")

    # Grade chapter 1
    runner.invoke(
        book_app,
        [
            "grade-chapter",
            "--slug",
            "loop",
            "--chapter",
            "1",
            "--text-file",
            str(chapter_file),
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    # Prepare chapter 2 — should include persona feedback block
    result = runner.invoke(
        book_app,
        [
            "prepare-chapter",
            "--slug",
            "loop",
            "--chapter",
            "2",
            "--output-base-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "读者画像反馈" in result.output
