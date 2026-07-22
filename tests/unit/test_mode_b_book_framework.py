from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "mode_b_book_framework.py"
_SPEC = importlib.util.spec_from_file_location("mode_b_book_framework", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)
_chapter_requires_bounded_restart = _MODULE._chapter_requires_bounded_restart
_configure_chapter_first_framework_settings = (
    _MODULE._configure_chapter_first_framework_settings
)
_evaluate_framework_closure = _MODULE._evaluate_framework_closure


def test_bounded_restart_preserves_completed_quality_reviewed_checkpoint() -> None:
    chapter = SimpleNamespace(status="complete", production_state="quality_reviewed")

    assert _chapter_requires_bounded_restart(chapter) is False


def test_bounded_restart_resets_failed_or_in_progress_chapter() -> None:
    blocked = SimpleNamespace(status="revision", production_state="repair_exhausted")
    interrupted = SimpleNamespace(status="drafting", production_state="pending")

    assert _chapter_requires_bounded_restart(blocked) is True
    assert _chapter_requires_bounded_restart(interrupted) is True


def test_framework_disables_legacy_review_rewrite_budget() -> None:
    settings = SimpleNamespace(
        pipeline=SimpleNamespace(accept_on_stall=True),
        quality=SimpleNamespace(max_chapter_revisions=2),
    )

    _configure_chapter_first_framework_settings(settings)

    assert settings.pipeline.accept_on_stall is False
    assert settings.quality.max_chapter_revisions == 0


def test_framework_closure_requires_all_ten_chapter_first_checkpoints(
    tmp_path: Path,
) -> None:
    chapters = [
        SimpleNamespace(
            id=f"chapter-{number}",
            chapter_number=number,
            status="complete",
            production_state="ok",
            metadata_json={"whole_chapter_logic_contract": {"chapter": number}},
        )
        for number in range(1, 11)
    ]
    drafts = {
        chapter.id: SimpleNamespace(
            word_count=2800,
            assembled_from_scene_draft_ids=[f"chapter_first_scene:node-{chapter.id}"],
        )
        for chapter in chapters
    }
    reader_edition = tmp_path / "reader-edition.md"
    reader_edition.write_text(
        "# 测试书\n\n"
        + "\n\n".join(f"## 第{number}章 标题" for number in range(1, 11))
        + "\n",
        encoding="utf-8",
    )

    report = _evaluate_framework_closure(
        expected_chapter_numbers=list(range(1, 11)),
        chapters=chapters,
        current_drafts_by_chapter_id=drafts,
        snapshot_chapter_numbers=set(range(1, 11)),
        chapter_first_run_scope_ids={chapter.id for chapter in chapters},
        scene_draft_count=0,
        historical_scene_draft_count=19,
        publish_min=2500,
        publish_max=3500,
        pipeline_requires_human_review=False,
        final_verdict="pass",
        reader_edition_path=reader_edition,
    )

    assert report.passed is True
    assert report.state_interface_count == 9
    assert report.missing_chapter_first_run_chapters == ()
    assert report.historical_scene_draft_count == 19
    assert report.reader_edition_chapter_heading_count == 10


def test_framework_closure_rejects_reader_scaffolding(tmp_path: Path) -> None:
    chapter = SimpleNamespace(
        id="chapter-1",
        chapter_number=1,
        status="complete",
        production_state="ok",
        metadata_json={"whole_chapter_logic_contract": {"chapter": 1}},
    )
    reader_edition = tmp_path / "reader-edition.md"
    reader_edition.write_text(
        "# 测试书\n\n## 第1章 标题\n\n### 场景一\n\nentry_state: hidden\n",
        encoding="utf-8",
    )

    report = _evaluate_framework_closure(
        expected_chapter_numbers=[1],
        chapters=[chapter],
        current_drafts_by_chapter_id={
            chapter.id: SimpleNamespace(
                word_count=2800,
                assembled_from_scene_draft_ids=["chapter_first_scene:node-1"],
            )
        },
        snapshot_chapter_numbers={1},
        chapter_first_run_scope_ids={chapter.id},
        scene_draft_count=0,
        publish_min=2500,
        publish_max=3500,
        pipeline_requires_human_review=False,
        final_verdict="pass",
        reader_edition_path=reader_edition,
    )

    assert report.passed is False
    assert report.reader_edition_visible_scene_heading_count == 1
    assert report.reader_edition_scaffolding_marker_count == 1


def test_framework_closure_rejects_scene_stitching_and_partial_book(
    tmp_path: Path,
) -> None:
    chapter = SimpleNamespace(
        id="chapter-1",
        chapter_number=1,
        status="complete",
        production_state="ok",
        metadata_json={"whole_chapter_logic_contract": {"chapter": 1}},
    )
    reader_edition = tmp_path / "reader-edition.md"
    reader_edition.write_text("# 不完整\n", encoding="utf-8")

    report = _evaluate_framework_closure(
        expected_chapter_numbers=list(range(1, 11)),
        chapters=[chapter],
        current_drafts_by_chapter_id={
            chapter.id: SimpleNamespace(
                word_count=2800,
                assembled_from_scene_draft_ids=["scene-draft-1"],
            )
        },
        snapshot_chapter_numbers={1},
        chapter_first_run_scope_ids={chapter.id},
        scene_draft_count=1,
        publish_min=2500,
        publish_max=3500,
        pipeline_requires_human_review=False,
        final_verdict="pass",
        reader_edition_path=reader_edition,
    )

    assert report.passed is False
    assert report.scene_assembled_chapters == (1,)
    assert report.missing_state_snapshot_chapters == tuple(range(2, 11))
