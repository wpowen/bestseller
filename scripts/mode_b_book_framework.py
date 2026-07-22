#!/usr/bin/env python3
"""Prepare and trigger a complete Mode B book through the production framework.

This command never writes chapter prose itself. It imports the story bible and
chapter logic contracts, materializes framework chapter/hidden-node rows, then
invokes the normal project pipeline in chapter-first mode.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))

from bestseller.domain.project import ProjectCreate
from bestseller.domain.enums import ChapterStatus, ProjectStatus, WorkflowStatus
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterStateSnapshotModel,
    RewriteTaskModel,
    SceneDraftVersionModel,
    WorkflowRunModel,
)
from bestseller.infra.db.session import session_scope
from bestseller.services.mode_b_bridge import (
    ModeBBridgeError,
    load_mode_b_framework_package,
)
from bestseller.services.pipelines import (
    WORKFLOW_TYPE_CHAPTER_PIPELINE,
    run_project_pipeline,
)
from bestseller.services.projects import create_project, get_project_by_slug
from bestseller.services.workflows import (
    materialize_chapter_outline_batch,
    materialize_story_bible,
)
from bestseller.settings import load_settings


def _configure_chapter_first_framework_settings(settings: Any) -> None:
    """Isolate Mode B's bounded repair policy from legacy rewrite loops."""

    settings.pipeline.accept_on_stall = False
    # Mode B already spends its complete prose-repair budget before the
    # chapter critic: two local patches plus one optional full regeneration.
    # The generic review loop must judge the resulting draft once but must not
    # start an additional rewrite budget of its own.
    settings.quality.max_chapter_revisions = 0


@dataclass(frozen=True)
class ModeBFrameworkClosureReport:
    """Hard evidence that a chapter-first book completed through the framework."""

    expected_chapter_numbers: tuple[int, ...]
    actual_chapter_numbers: tuple[int, ...]
    missing_logic_contract_chapters: tuple[int, ...]
    missing_current_draft_chapters: tuple[int, ...]
    out_of_band_chapters: tuple[int, ...]
    non_complete_chapters: tuple[int, ...]
    scene_assembled_chapters: tuple[int, ...]
    missing_state_snapshot_chapters: tuple[int, ...]
    missing_chapter_first_run_chapters: tuple[int, ...]
    state_interface_count: int
    scene_draft_count: int
    historical_scene_draft_count: int
    pipeline_requires_human_review: bool
    final_verdict: str | None
    reader_edition_path: str | None
    reader_edition_exists: bool
    reader_edition_chapter_heading_count: int
    reader_edition_visible_scene_heading_count: int
    reader_edition_frontmatter_count: int
    reader_edition_scaffolding_marker_count: int

    @property
    def passed(self) -> bool:
        return not any(
            (
                set(self.expected_chapter_numbers)
                != set(self.actual_chapter_numbers),
                self.missing_logic_contract_chapters,
                self.missing_current_draft_chapters,
                self.out_of_band_chapters,
                self.non_complete_chapters,
                self.scene_assembled_chapters,
                self.missing_state_snapshot_chapters,
                self.missing_chapter_first_run_chapters,
                self.state_interface_count
                != max(0, len(self.expected_chapter_numbers) - 1),
                self.scene_draft_count != 0,
                self.pipeline_requires_human_review,
                self.final_verdict != "pass",
                not self.reader_edition_exists,
                self.reader_edition_chapter_heading_count
                != len(self.expected_chapter_numbers),
                self.reader_edition_visible_scene_heading_count != 0,
                self.reader_edition_frontmatter_count != 0,
                self.reader_edition_scaffolding_marker_count != 0,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "expected_chapter_numbers": list(self.expected_chapter_numbers),
            "actual_chapter_numbers": list(self.actual_chapter_numbers),
            "missing_logic_contract_chapters": list(
                self.missing_logic_contract_chapters
            ),
            "missing_current_draft_chapters": list(
                self.missing_current_draft_chapters
            ),
            "out_of_band_chapters": list(self.out_of_band_chapters),
            "non_complete_chapters": list(self.non_complete_chapters),
            "scene_assembled_chapters": list(self.scene_assembled_chapters),
            "missing_state_snapshot_chapters": list(
                self.missing_state_snapshot_chapters
            ),
            "missing_chapter_first_run_chapters": list(
                self.missing_chapter_first_run_chapters
            ),
            "state_interface_count": self.state_interface_count,
            "expected_state_interface_count": max(
                0, len(self.expected_chapter_numbers) - 1
            ),
            "scene_draft_count": self.scene_draft_count,
            "historical_scene_draft_count": self.historical_scene_draft_count,
            "pipeline_requires_human_review": self.pipeline_requires_human_review,
            "final_verdict": self.final_verdict,
            "reader_edition_path": self.reader_edition_path,
            "reader_edition_exists": self.reader_edition_exists,
            "reader_edition_chapter_heading_count": self.reader_edition_chapter_heading_count,
            "reader_edition_visible_scene_heading_count": (
                self.reader_edition_visible_scene_heading_count
            ),
            "reader_edition_frontmatter_count": self.reader_edition_frontmatter_count,
            "reader_edition_scaffolding_marker_count": (
                self.reader_edition_scaffolding_marker_count
            ),
        }


def _reader_edition_evidence(path: Path | None) -> dict[str, int]:
    """Measure reader-visible closure evidence instead of trusting file existence."""

    if path is None or not path.is_file():
        return {
            "chapter_heading_count": 0,
            "visible_scene_heading_count": 0,
            "frontmatter_count": 0,
            "scaffolding_marker_count": 0,
        }
    text = path.read_text(encoding="utf-8")
    return {
        "chapter_heading_count": len(
            re.findall(r"(?im)^#{1,3}\s*(?:第\s*\d+\s*章|chapter\s+\d+)", text)
        ),
        "visible_scene_heading_count": len(
            re.findall(r"(?im)^#{1,6}\s*(?:场景|scene)(?:\s|\d|[一二三四五六七八九十])", text)
        ),
        "frontmatter_count": len(re.findall(r"(?m)^---\s*$", text)) // 2,
        "scaffolding_marker_count": sum(
            text.count(marker)
            for marker in (
                "entry_state",
                "exit_state",
                "scene_type",
                "【弱场景逻辑地图】",
                "【整章逻辑合同",
            )
        ),
    }


def _evaluate_framework_closure(
    *,
    expected_chapter_numbers: list[int],
    chapters: list[Any],
    current_drafts_by_chapter_id: dict[Any, Any],
    snapshot_chapter_numbers: set[int],
    chapter_first_run_scope_ids: set[Any],
    scene_draft_count: int,
    historical_scene_draft_count: int = 0,
    publish_min: int,
    publish_max: int,
    pipeline_requires_human_review: bool,
    final_verdict: str | None,
    reader_edition_path: Path | None,
) -> ModeBFrameworkClosureReport:
    actual_numbers = tuple(sorted(int(chapter.chapter_number) for chapter in chapters))
    missing_logic_contracts: list[int] = []
    missing_drafts: list[int] = []
    out_of_band: list[int] = []
    non_complete: list[int] = []
    scene_assembled: list[int] = []
    missing_chapter_first_runs: list[int] = []

    for chapter in chapters:
        number = int(chapter.chapter_number)
        metadata = dict(getattr(chapter, "metadata_json", None) or {})
        if not metadata.get("whole_chapter_logic_contract"):
            missing_logic_contracts.append(number)
        draft = current_drafts_by_chapter_id.get(chapter.id)
        if draft is None:
            missing_drafts.append(number)
        else:
            word_count = int(getattr(draft, "word_count", 0) or 0)
            if word_count < publish_min or word_count > publish_max:
                out_of_band.append(number)
            source_ids = [
                str(item)
                for item in (
                    getattr(draft, "assembled_from_scene_draft_ids", None) or []
                )
            ]
            if source_ids and not all(
                item.startswith("chapter_first_scene:") for item in source_ids
            ):
                scene_assembled.append(number)
        if not (
            str(getattr(chapter, "status", "")) == ChapterStatus.COMPLETE.value
            and str(getattr(chapter, "production_state", ""))
            in {"ok", "quality_reviewed"}
        ):
            non_complete.append(number)
        if chapter.id not in chapter_first_run_scope_ids:
            missing_chapter_first_runs.append(number)

    expected = tuple(expected_chapter_numbers)
    missing_snapshots = tuple(
        number for number in expected if number not in snapshot_chapter_numbers
    )
    state_interface_count = sum(
        1
        for number in expected[:-1]
        if number in snapshot_chapter_numbers and number + 1 in snapshot_chapter_numbers
    )
    reader_evidence = _reader_edition_evidence(reader_edition_path)
    return ModeBFrameworkClosureReport(
        expected_chapter_numbers=expected,
        actual_chapter_numbers=actual_numbers,
        missing_logic_contract_chapters=tuple(missing_logic_contracts),
        missing_current_draft_chapters=tuple(missing_drafts),
        out_of_band_chapters=tuple(out_of_band),
        non_complete_chapters=tuple(non_complete),
        scene_assembled_chapters=tuple(scene_assembled),
        missing_state_snapshot_chapters=missing_snapshots,
        missing_chapter_first_run_chapters=tuple(missing_chapter_first_runs),
        state_interface_count=state_interface_count,
        scene_draft_count=int(scene_draft_count),
        historical_scene_draft_count=int(historical_scene_draft_count),
        pipeline_requires_human_review=bool(pipeline_requires_human_review),
        final_verdict=final_verdict,
        reader_edition_path=(
            str(reader_edition_path.resolve()) if reader_edition_path is not None else None
        ),
        reader_edition_exists=bool(
            reader_edition_path is not None and reader_edition_path.is_file()
        ),
        reader_edition_chapter_heading_count=reader_evidence["chapter_heading_count"],
        reader_edition_visible_scene_heading_count=reader_evidence[
            "visible_scene_heading_count"
        ],
        reader_edition_frontmatter_count=reader_evidence["frontmatter_count"],
        reader_edition_scaffolding_marker_count=reader_evidence[
            "scaffolding_marker_count"
        ],
    )


async def _audit_framework_closure(
    session: Any,
    *,
    project: Any,
    package: Any,
    pipeline_result: Any,
    scene_draft_count_before_run: int,
) -> ModeBFrameworkClosureReport:
    chapters = list(
        await session.scalars(
            select(ChapterModel)
            .where(ChapterModel.project_id == project.id)
            .order_by(ChapterModel.chapter_number)
        )
    )
    current_drafts = list(
        await session.scalars(
            select(ChapterDraftVersionModel).where(
                ChapterDraftVersionModel.project_id == project.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
        )
    )
    snapshots = list(
        await session.scalars(
            select(ChapterStateSnapshotModel).where(
                ChapterStateSnapshotModel.project_id == project.id
            )
        )
    )
    chapter_runs = list(
        await session.scalars(
            select(WorkflowRunModel).where(
                WorkflowRunModel.project_id == project.id,
                WorkflowRunModel.workflow_type == WORKFLOW_TYPE_CHAPTER_PIPELINE,
            )
        )
    )
    scene_draft_count_after_run = int(
        await session.scalar(
            select(func.count(SceneDraftVersionModel.id)).where(
                SceneDraftVersionModel.project_id == project.id
            )
        )
        or 0
    )

    reader_edition_path: Path | None = None
    source_path = Path(pipeline_result.output_path) if pipeline_result.output_path else None
    if source_path is not None and source_path.is_file():
        reader_edition_path = package.root / "reader-edition.md"
        reader_edition_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, reader_edition_path)

    state_ledger_path = package.root / "knowledge" / "framework-state-ledger.json"
    state_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    state_ledger_path.write_text(
        json.dumps(
            {
                "project_slug": package.slug,
                "snapshots": [
                    {
                        "chapter_number": int(snapshot.chapter_number),
                        "facts": snapshot.facts,
                        "time_anchor": snapshot.time_anchor,
                        "chapter_time_span": snapshot.chapter_time_span,
                        "extraction_status": snapshot.extraction_status,
                    }
                    for snapshot in sorted(
                        snapshots, key=lambda item: int(item.chapter_number)
                    )
                ],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )

    words_per_chapter = package.meta.get("words_per_chapter") or {}
    report = _evaluate_framework_closure(
        expected_chapter_numbers=[
            int(chapter.chapter_number) for chapter in package.outline_batch.chapters
        ],
        chapters=chapters,
        current_drafts_by_chapter_id={draft.chapter_id: draft for draft in current_drafts},
        snapshot_chapter_numbers={int(snapshot.chapter_number) for snapshot in snapshots},
        chapter_first_run_scope_ids={
            run.scope_id
            for run in chapter_runs
            if bool((run.metadata_json or {}).get("chapter_first"))
        },
        scene_draft_count=max(0, scene_draft_count_after_run - scene_draft_count_before_run),
        historical_scene_draft_count=scene_draft_count_before_run,
        publish_min=int(words_per_chapter.get("min") or 2500),
        publish_max=int(words_per_chapter.get("max") or 3500),
        pipeline_requires_human_review=pipeline_result.requires_human_review,
        final_verdict=pipeline_result.final_verdict,
        reader_edition_path=reader_edition_path,
    )
    audit_path = package.root / "audits" / "framework-closure.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _chapter_requires_bounded_restart(chapter: ChapterModel) -> bool:
    """Return whether a retry may reset this chapter's production checkpoint."""

    production_state = str(getattr(chapter, "production_state", None) or "")
    status = str(getattr(chapter, "status", None) or "")
    # A chapter that already crossed the chapter gate is a durable checkpoint.
    # ``quality_reviewed`` is the normal post-review state in some pipelines;
    # resetting it merely because the enum is not named ``ok`` caused a later
    # chapter failure to regenerate chapter 1 from scratch.
    if status == ChapterStatus.COMPLETE.value and production_state in {
        "ok",
        "quality_reviewed",
    }:
        return False
    return production_state in {
        "blocked",
        "quality_debt",
        "needs_human_review",
        "repair_exhausted",
    } or status in {
        ChapterStatus.DRAFTING.value,
        ChapterStatus.REVIEW.value,
        ChapterStatus.REVISION.value,
    }


def _project_payload(package: Any) -> ProjectCreate:
    meta = package.meta
    story_bible = package.story_bible
    book_spec = story_bible.get("book_spec") or {}
    target_words = int(meta.get("target_total_words") or 0)
    if target_words <= 0:
        per_chapter = int(((meta.get("words_per_chapter") or {}).get("target")) or 2800)
        target_words = per_chapter * int(meta.get("target_chapters") or 1)
    target_platform = str(meta.get("target_platform") or "").strip()
    return ProjectCreate.model_validate(
        {
            "slug": package.slug,
            "title": meta.get("title") or book_spec.get("title") or package.slug,
            "genre": meta.get("genre") or book_spec.get("genre") or "fiction",
            "sub_genre": meta.get("sub_genre") or book_spec.get("subgenre"),
            "audience": "商业长篇小说读者",
            "language": meta.get("language") or "zh-CN",
            "target_word_count": target_words,
            "target_chapters": int(meta.get("target_chapters") or 1),
            "metadata": {
                "mode_b": True,
                "generation_mode": "chapter_first_single_pass",
                "chapter_first_generation": True,
                "chapter_review_warn_only": False,
                "stop_on_chapter_failure": True,
                "self_heal_suppressed": True,
                "self_heal_suppressed_reason": "chapter_first_framework_owned",
                "chapter_first_local_repair_max_attempts": 2,
                "chapter_first_full_regeneration_max_attempts": 1,
                "chapter_first_stop_after_repair_exhaustion": True,
                "whole_chapter_logic_contract_required": True,
                "framework_package_schema": 1,
                "control_project": meta.get("control_project"),
                "llm_model_id": meta.get("llm_model_id"),
                "chapter_first_writer_aim": meta.get("chapter_first_writer_aim"),
                "words_per_chapter": meta.get("words_per_chapter"),
                "primary_pov": meta.get("primary_pov") or "close_third",
                "target_platform": target_platform or None,
                "opening_quality_gate_disabled": target_platform == "internal_review",
                "anti_ai_contract": {
                    "conclusion_first_templates": "blocked",
                    "body_reaction_templates": "blocked",
                    "emotion_delivery": "choice_error_avoidance_dialogue_cost",
                    "visible_scene_scaffolding": "blocked",
                },
            },
        }
    )


async def _run(slug: str, *, prepare_only: bool, restart_blocked: bool) -> int:
    settings = load_settings()
    # This experiment's acceptance contract requires every chapter to clear
    # its gates.  The generic production default may accept a best draft after
    # repair exhaustion; doing that here would make a 10-chapter run look
    # complete even though the A/B validation contract says it failed.
    _configure_chapter_first_framework_settings(settings)
    package = load_mode_b_framework_package(
        slug,
        output_base_dir=settings.output.base_dir,
    )
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        created = project is None
        if project is None:
            project = await create_project(session, _project_payload(package), settings)
        project.metadata_json = {
            **(project.metadata_json or {}),
            "mode_b": True,
            "generation_mode": "chapter_first_single_pass",
            "chapter_first_generation": True,
            "chapter_review_warn_only": False,
            "stop_on_chapter_failure": True,
            "self_heal_suppressed": True,
            "self_heal_suppressed_reason": "chapter_first_framework_owned",
            "whole_chapter_logic_contract_required": True,
            "llm_model_id": package.meta.get("llm_model_id"),
            "chapter_first_writer_aim": package.meta.get("chapter_first_writer_aim"),
            "words_per_chapter": package.meta.get("words_per_chapter"),
            "target_platform": package.meta.get("target_platform"),
            "opening_quality_gate_disabled": (
                str(package.meta.get("target_platform") or "").strip()
                == "internal_review"
            ),
            "chapter_first_local_repair_max_attempts": 2,
            "chapter_first_full_regeneration_max_attempts": 1,
            "chapter_first_stop_after_repair_exhaustion": True,
        }

        chapter_count = int(
            await session.scalar(
                select(func.count(ChapterModel.id)).where(
                    ChapterModel.project_id == project.id
                )
            )
            or 0
        )
        materialized = False
        if chapter_count == 0:
            story_bible = package.story_bible
            await materialize_story_bible(
                session,
                slug,
                requested_by="mode-b-book-framework",
                book_spec_content=story_bible.get("book_spec"),
                world_spec_content=story_bible.get("world_spec"),
                cast_spec_content=story_bible.get("cast_spec"),
                volume_plan_content=story_bible.get("volume_plan"),
            )
            await materialize_chapter_outline_batch(
                session,
                slug,
                package.outline_batch,
                requested_by="mode-b-book-framework",
            )
            materialized = True

        expected_targets = {
            chapter.chapter_number: chapter.target_word_count
            for chapter in package.outline_batch.chapters
        }
        persisted_chapters = list(
            await session.scalars(
                select(ChapterModel)
                .where(ChapterModel.project_id == project.id)
                .order_by(ChapterModel.chapter_number)
            )
        )
        if len(persisted_chapters) != len(expected_targets):
            raise ModeBBridgeError(
                f"Framework project '{slug}' has {len(persisted_chapters)} chapters; "
                f"package requires {len(expected_targets)}"
            )
        for chapter in persisted_chapters:
            chapter.target_word_count = expected_targets[chapter.chapter_number]

        if restart_blocked:
            for workflow in await session.scalars(
                select(WorkflowRunModel).where(
                    WorkflowRunModel.project_id == project.id,
                    WorkflowRunModel.status == WorkflowStatus.RUNNING.value,
                )
            ):
                workflow.status = WorkflowStatus.MACHINE_BLOCKED.value
                workflow.current_step = "interrupted_before_bounded_restart"
                workflow.metadata_json = {
                    **(workflow.metadata_json or {}),
                    "interrupted_before_bounded_restart": True,
                }
            reset_chapter_ids = []
            for chapter in persisted_chapters:
                if not _chapter_requires_bounded_restart(chapter):
                    continue
                metadata = dict(chapter.metadata_json or {})
                for key in list(metadata):
                    if key.startswith("auto_repair") or key.startswith(
                        "chapter_first_full_regeneration"
                    ) or key.startswith(
                        "chapter_first_local_repair"
                    ) or key.startswith("retention_retry"):
                        metadata.pop(key, None)
                for key in (
                    "chapter_first_generation",
                    "chapter_quality_debt",
                    "chapter_quality_debt_reason",
                    "chapter_review_attempts_active",
                    "deterministic_audit_latest",
                    "fanqie_long_ranking_block_attempts",
                    "post_assembly_duplicate_gate",
                    "retention_gate_last_findings",
                    "retention_gate_passed",
                    "requires_machine_repair",
                    "requires_human_review",
                    "production_block_code",
                    "rewrite_attempts_by_kind",
                    "rewrite_convergence_exhausted",
                    "rewrite_escalation",
                    "rewrite_history",
                    "blocked_by_write_safety_gate",
                    "write_safety_block_code",
                    "write_safety_hint",
                    "chapter_first_resume_existing_draft_id",
                    "chapter_first_resume_attempted_draft_ids",
                ):
                    metadata.pop(key, None)
                chapter.metadata_json = metadata
                chapter.status = ChapterStatus.PLANNED.value
                chapter.production_state = "pending"
                chapter.current_word_count = 0
                reset_chapter_ids.append(chapter.id)
            if reset_chapter_ids:
                current_drafts = list(
                    await session.scalars(
                        select(ChapterDraftVersionModel).where(
                            ChapterDraftVersionModel.chapter_id.in_(reset_chapter_ids),
                            ChapterDraftVersionModel.is_current.is_(True),
                        )
                    )
                )
                for draft in current_drafts:
                    draft.is_current = False
            pending_rewrite_tasks = list(
                await session.scalars(
                    select(RewriteTaskModel).where(
                        RewriteTaskModel.project_id == project.id,
                        RewriteTaskModel.status.in_(["pending", "queued"]),
                    )
                )
            )
            for task in pending_rewrite_tasks:
                task.status = "cancelled"
                task.metadata_json = {
                    **(task.metadata_json or {}),
                    "cancelled_by": "mode_b_bounded_restart",
                }
            project.status = ProjectStatus.PLANNING.value
            project.current_chapter_number = 0

        prepared = {
            "slug": slug,
            "project_created": created,
            "planning_materialized": materialized,
            "chapters": len(package.outline_batch.chapters),
            "generation_mode": "chapter_first_single_pass",
            "stop_on_chapter_failure": True,
            "restart_blocked": restart_blocked,
        }
        if prepare_only:
            print(json.dumps({"prepared": prepared}, ensure_ascii=False, indent=2))
            return 0

        scene_draft_count_before_run = int(
            await session.scalar(
                select(func.count(SceneDraftVersionModel.id)).where(
                    SceneDraftVersionModel.project_id == project.id
                )
            )
            or 0
        )
        result = await run_project_pipeline(
            session,
            settings,
            slug,
            requested_by="mode-b-book-framework",
            export_markdown=True,
            chapter_first=True,
            stop_on_chapter_failure=True,
        )
        closure = await _audit_framework_closure(
            session,
            project=project,
            package=package,
            pipeline_result=result,
            scene_draft_count_before_run=scene_draft_count_before_run,
        )
        print(
            json.dumps(
                {
                    "prepared": prepared,
                    "pipeline": {
                        "project_slug": result.project_slug,
                        "chapters_processed": len(result.chapter_results),
                        "chapters_requiring_human_review": [
                            chapter.chapter_number
                            for chapter in result.chapter_results
                            if chapter.requires_human_review
                        ],
                        "final_verdict": result.final_verdict,
                        "requires_human_review": result.requires_human_review,
                        "output_path": result.output_path,
                    },
                    "closure": closure.to_dict(),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 0 if closure.passed else 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize Mode B contracts and trigger the full framework pipeline"
    )
    parser.add_argument("--slug", required=True)
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Create/materialize framework rows without generating prose.",
    )
    parser.add_argument(
        "--restart-blocked",
        action="store_true",
        help="Close interrupted runs and reset blocked chapter counters before retrying.",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(
            _run(
                args.slug,
                prepare_only=args.prepare_only,
                restart_blocked=args.restart_blocked,
            )
        )
    except ModeBBridgeError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
