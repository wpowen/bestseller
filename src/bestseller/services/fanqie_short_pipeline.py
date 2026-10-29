# ruff: noqa: ANN401, RUF002
"""番茄短故事专用 autowrite pipeline。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import ArtifactType, ProjectType
from bestseller.domain.fanqie_short import (
    DEFAULT_UNLOCK_LINE_RATIO,
    is_fanqie_short_project,
)
from bestseller.domain.planning import AutowriteResult
from bestseller.domain.project import ProjectCreate
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    RewriteTaskModel,
)
from bestseller.services.drafts import count_words, sanitize_novel_markdown_content
from bestseller.services.fanqie_short_gate_v2 import (
    build_fanqie_short_v2_rewrite_instructions,
    build_fanqie_short_v2_rewrite_routes,
)
from bestseller.services.fanqie_short_planner import (
    build_fanqie_segment_outline_batch,
    generate_fanqie_beat_sheet,
    persist_fanqie_chapter_outline,
    prepare_reader_first_short_outline,
)
from bestseller.services.fanqie_short_quality import review_whole_fanqie_short_story
from bestseller.services.fanqie_short_ranking_gate import (
    FanqieRankingFinding,
    FanqieRankingGateReport,
    build_fanqie_ranking_rewrite_instructions,
)
from bestseller.services.ideology_kernel import derive_ideology_kernel
from bestseller.services.production_prompt_audit import (
    bind_resolved_project_prompt_audit,
    project_prompt_audit_scope,
)
from bestseller.services.judge_genre_context import resolve_judge_genre_context
from bestseller.services.story_enhancers import resolve_cost_style
from bestseller.services.litstyle_prose_judge import judge_chapter_litstyle_stable
from bestseller.services.pipelines import (
    ProgressCallback,
    _checkpoint_commit,
    _emit_progress,
    _ensure_project_invariants,
    run_chapter_pipeline,
)
from bestseller.services.planner import generate_foundation_plan
from bestseller.services.projects import create_project, get_project_by_slug
from bestseller.services.workflows import (
    get_latest_planning_artifact,
    materialize_latest_chapter_outline_batch,
    materialize_latest_narrative_graph,
    materialize_latest_story_bible,
)
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)


def _ranking_finding_chapter_numbers(
    finding: FanqieRankingFinding,
    *,
    segment_total: int,
    unlock_segment: int,
) -> list[int]:
    target = finding.target
    phase = finding.phase
    if phase == "opening" or target.startswith("opening"):
        return [1]
    if phase == "unlock" or target.startswith("unlock"):
        return list(range(1, max(1, min(unlock_segment, segment_total)) + 1))
    if phase == "closure" or target == "ending":
        return [segment_total]
    return list(range(1, segment_total + 1))


def _short_v2_finding_chapter_numbers(
    finding: FanqieRankingFinding,
    *,
    segment_total: int,
    unlock_segment: int,
) -> list[int]:
    """Map v2 whole-piece findings to concrete segment repair targets."""
    target = finding.target
    phase = finding.phase
    code = finding.code
    if (
        target == "title"
        or target.startswith("opening")
        or phase in {"title", "opening"}
        or code.startswith("first_screen")
    ):
        return [1]
    if phase == "unlock" or target.startswith("unlock"):
        return list(range(1, max(1, min(unlock_segment, segment_total)) + 1))
    if phase == "closure" or target == "ending":
        return [segment_total]
    if phase in {"payoff", "anti_longform"} or target == "whole_story":
        return list(range(1, segment_total + 1))
    return _ranking_finding_chapter_numbers(
        finding,
        segment_total=segment_total,
        unlock_segment=unlock_segment,
    )


def _short_v2_rewrite_strategy(code: str) -> str:
    safe_code = "".join(char if char.isalnum() or char == "_" else "_" for char in code)
    return f"fix_{safe_code}"[:64]


async def _chapter_by_number(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_number: int,
) -> ChapterModel | None:
    return await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project_id,
            ChapterModel.chapter_number == chapter_number,
        )
    )


async def _persist_fanqie_ranking_gate_failure(
    session: AsyncSession,
    project: ProjectModel,
    *,
    report: FanqieRankingGateReport,
    unlock_segment: int,
) -> int:
    """Persist ranking-gate failures as project metadata and rewrite tasks."""
    meta = dict(project.metadata_json or {})
    meta["fanqie_short_ranking_gate_report"] = report.to_dict()
    meta["fanqie_short_ready_for_upload"] = report.passed
    project.metadata_json = meta
    if report.passed:
        return 0

    critical_findings = [finding for finding in report.findings if finding.severity == "critical"]
    if not critical_findings:
        return 0

    created = 0
    segment_total = max(int(getattr(project, "target_chapters", 0) or 0), 1)
    source_bindings = {}
    source_metadata = {}
    numbers = {
        number
        for finding in critical_findings
        for number in _ranking_finding_chapter_numbers(
            finding,
            segment_total=segment_total,
            unlock_segment=unlock_segment,
        )
    }
    # Lock all target chapters before any drafts. Validate every marked source
    # before the first task is staged; never refresh/rebind an old task.
    with session.no_autoflush:
        if any(
            isinstance(row, ChapterModel)
            and row.project_id == project.id
            and row.chapter_number in numbers
            and "outline_source_admission" in (row.metadata_json or {})
            for row in session.new
        ):
            raise ValueError("fanqie_ranking_rewrite_source_unflushed")
        sources = (
            await session.execute(
                select(
                    ChapterModel.id,
                    ChapterModel.project_id,
                    ChapterModel.chapter_number,
                    ChapterModel.metadata_json,
                )
                .where(
                    ChapterModel.project_id == project.id, ChapterModel.chapter_number.in_(numbers)
                )
                .order_by(ChapterModel.chapter_number, ChapterModel.id)
                .with_for_update()
            )
        ).all()
        for source in sources:
            marked = "outline_source_admission" in (source.metadata_json or {})
            cached_marked = any(
                isinstance(row, ChapterModel)
                and row.id == source.id
                and "outline_source_admission" in (row.__dict__.get("metadata_json") or {})
                for row in session.identity_map.values()
            )
            if not marked and not cached_marked:
                continue
            if any(
                (isinstance(row, ChapterModel) and row.id == source.id)
                or (isinstance(row, ChapterDraftVersionModel) and row.chapter_id == source.id)
                for row in (*session.dirty, *session.new, *session.deleted)
            ):
                raise ValueError("fanqie_ranking_rewrite_source_unflushed")
            source_metadata[source.id] = source.metadata_json
            if not marked:
                continue  # A stale ORM cache cannot restore a removed source marker.
            drafts = (
                await session.execute(
                    select(
                        ChapterDraftVersionModel.id,
                        ChapterDraftVersionModel.project_id,
                        ChapterDraftVersionModel.chapter_id,
                        ChapterDraftVersionModel.content_md,
                    )
                    .where(
                        ChapterDraftVersionModel.chapter_id == source.id,
                        ChapterDraftVersionModel.is_current.is_(True),
                    )
                    .order_by(ChapterDraftVersionModel.id)
                    .with_for_update()
                )
            ).all()
            if (
                len(drafts) != 1
                or not isinstance(drafts[0].content_md, str)
                or not drafts[0].content_md.strip()
            ):
                raise ValueError("fanqie_ranking_rewrite_current_source_required")
            from bestseller.services.reviews import rewrite_source_binding

            source_bindings[source.id] = rewrite_source_binding(drafts[0], chapter=source)
    for finding in critical_findings:
        for chapter_number in _ranking_finding_chapter_numbers(
            finding,
            segment_total=segment_total,
            unlock_segment=unlock_segment,
        ):
            chapter = await _chapter_by_number(
                session,
                project_id=project.id,
                chapter_number=chapter_number,
            )
            if chapter is None:
                continue
            task = RewriteTaskModel(
                project_id=project.id,
                parent_task_id=None,
                trigger_type="fanqie_ranking_gate",
                trigger_source_id=chapter.id,
                rewrite_strategy=f"fix_{finding.phase}",
                priority=1,
                status="pending",
                instructions=build_fanqie_ranking_rewrite_instructions(
                    FanqieRankingGateReport(
                        passed=False,
                        phase=finding.phase,
                        findings=(finding,),
                    )
                ),
                context_required=["chapter_draft", "fanqie_short_ranking_contract"],
                metadata_json={
                    **source_bindings.get(chapter.id, {}),
                    "chapter_id": str(chapter.id),
                    "chapter_number": chapter.chapter_number,
                    "finding": finding.to_dict(),
                    "source": "fanqie_short_ranking_gate",
                },
            )
            session.add(task)
            chapter.status = "revision"
            chapter.production_state = "blocked"
            chapter.metadata_json = {
                **source_metadata.get(chapter.id, chapter.metadata_json or {}),
                "blocked_by_fanqie_ranking_gate": True,
                "fanqie_ranking_gate_code": finding.code,
                "fanqie_ranking_gate_message": finding.message,
            }
            created += 1
    await session.flush()
    return created


async def _persist_fanqie_short_v2_gate_failure(
    session: AsyncSession,
    project: ProjectModel,
    *,
    report: FanqieRankingGateReport,
    unlock_segment: int,
) -> int:
    """Persist v2 short-story failures as worker-routed repair tasks."""
    routes = build_fanqie_short_v2_rewrite_routes(report)
    route_by_code = {route.finding_code: route for route in routes}
    meta = dict(project.metadata_json or {})
    meta["fanqie_short_v2_gate_report"] = report.to_dict()
    meta["fanqie_short_v2_rewrite_routes"] = [route.model_dump(mode="json") for route in routes]
    if not report.passed:
        meta["fanqie_short_ready_for_upload"] = False
    project.metadata_json = meta
    if report.passed:
        return 0

    critical_findings = [finding for finding in report.findings if finding.severity == "critical"]
    if not critical_findings:
        return 0

    created = 0
    segment_total = max(int(getattr(project, "target_chapters", 0) or 0), 1)
    source_bindings = {}
    source_metadata = {}
    numbers = {
        number
        for finding in critical_findings
        for number in _short_v2_finding_chapter_numbers(
            finding,
            segment_total=segment_total,
            unlock_segment=unlock_segment,
        )
    }
    with session.no_autoflush:
        if any(
            isinstance(row, ChapterModel)
            and row.project_id == project.id
            and row.chapter_number in numbers
            and "outline_source_admission" in (row.metadata_json or {})
            for row in session.new
        ):
            raise ValueError("fanqie_ranking_rewrite_source_unflushed")
        sources = (
            await session.execute(
                select(
                    ChapterModel.id,
                    ChapterModel.project_id,
                    ChapterModel.chapter_number,
                    ChapterModel.metadata_json,
                )
                .where(
                    ChapterModel.project_id == project.id, ChapterModel.chapter_number.in_(numbers)
                )
                .order_by(ChapterModel.chapter_number, ChapterModel.id)
                .with_for_update()
            )
        ).all()
        for source in sources:
            marked = "outline_source_admission" in (source.metadata_json or {})
            cached_marked = any(
                isinstance(row, ChapterModel)
                and row.id == source.id
                and "outline_source_admission" in (row.__dict__.get("metadata_json") or {})
                for row in session.identity_map.values()
            )
            if not marked and not cached_marked:
                continue
            if any(
                (isinstance(row, ChapterModel) and row.id == source.id)
                or (isinstance(row, ChapterDraftVersionModel) and row.chapter_id == source.id)
                for row in (*session.dirty, *session.new, *session.deleted)
            ):
                raise ValueError("fanqie_ranking_rewrite_source_unflushed")
            source_metadata[source.id] = source.metadata_json
            if not marked:
                continue
            drafts = (
                await session.execute(
                    select(
                        ChapterDraftVersionModel.id,
                        ChapterDraftVersionModel.project_id,
                        ChapterDraftVersionModel.chapter_id,
                        ChapterDraftVersionModel.content_md,
                    )
                    .where(
                        ChapterDraftVersionModel.chapter_id == source.id,
                        ChapterDraftVersionModel.is_current.is_(True),
                    )
                    .order_by(ChapterDraftVersionModel.id)
                    .with_for_update()
                )
            ).all()
            if (
                len(drafts) != 1
                or not isinstance(drafts[0].content_md, str)
                or not drafts[0].content_md.strip()
            ):
                raise ValueError("fanqie_ranking_rewrite_current_source_required")
            from bestseller.services.reviews import rewrite_source_binding

            source_bindings[source.id] = rewrite_source_binding(drafts[0], chapter=source)
    for finding in critical_findings:
        route = route_by_code.get(finding.code)
        route_payload = route.model_dump(mode="json") if route is not None else None
        instructions = build_fanqie_short_v2_rewrite_instructions(
            FanqieRankingGateReport(
                passed=False,
                phase=finding.phase,
                findings=(finding,),
            )
        )
        for chapter_number in _short_v2_finding_chapter_numbers(
            finding,
            segment_total=segment_total,
            unlock_segment=unlock_segment,
        ):
            chapter = await _chapter_by_number(
                session,
                project_id=project.id,
                chapter_number=chapter_number,
            )
            if chapter is None:
                continue
            task = RewriteTaskModel(
                project_id=project.id,
                parent_task_id=None,
                trigger_type="fanqie_short_v2_gate",
                trigger_source_id=chapter.id,
                rewrite_strategy=_short_v2_rewrite_strategy(finding.code),
                priority=route.priority if route is not None else 1,
                status="pending",
                instructions=instructions,
                context_required=[
                    "chapter_draft",
                    "fanqie_short_v2_contract",
                    "fanqie_short_worker_route",
                ],
                metadata_json={
                    **source_bindings.get(chapter.id, {}),
                    "chapter_id": str(chapter.id),
                    "chapter_number": chapter.chapter_number,
                    "finding": finding.to_dict(),
                    "source": "fanqie_short_v2_gate",
                    "source_report_phase": report.phase,
                    "worker_route": route_payload,
                    "worker": route.worker if route is not None else "RankingGateWorker",
                },
            )
            session.add(task)
            chapter.status = "revision"
            chapter.production_state = "blocked"
            chapter.metadata_json = {
                **source_metadata.get(chapter.id, chapter.metadata_json or {}),
                "blocked_by_fanqie_short_v2_gate": True,
                "fanqie_short_v2_gate_code": finding.code,
                "fanqie_short_v2_gate_message": finding.message,
                "fanqie_short_v2_worker": (
                    route.worker if route is not None else "RankingGateWorker"
                ),
            }
            created += 1
    await session.flush()
    return created


async def _load_chapter_drafts(
    session: AsyncSession,
    project_id: UUID,
) -> list[tuple[ChapterModel, ChapterDraftVersionModel]]:
    from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
    from bestseller.services.adopted_knowledge_resume import read_adopted_knowledge

    chapters = list(
        await session.scalars(
            select(ChapterModel)
            .where(ChapterModel.project_id == project_id)
            .order_by(ChapterModel.chapter_number.asc())
        )
    )
    project = await session.get(ProjectModel, project_id, populate_existing=True)
    if project is None or {row.chapter_number for row in chapters} != set(
        range(1, project.target_chapters + 1)
    ):
        raise ValueError('fanqie_segments_incomplete')
    payloads: list[tuple[ChapterModel, ChapterDraftVersionModel]] = []
    for chapter in chapters:
        await session.refresh(chapter)
        draft, _ = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
        if draft is None:
            raise ValueError(f"fanqie_segment_not_adopted:{chapter.chapter_number}")
        _, knowledge = await read_adopted_knowledge(
            session, project_id=project_id, chapter_id=chapter.id,
            source_chapter_draft_version_id=draft.id, chapter_md=draft.content_md,
            require_bible=True,
        )
        if not knowledge['closed']:
            raise ValueError(f"fanqie_segment_knowledge_not_closed:{chapter.chapter_number}")
        payloads.append((chapter, draft))
    return payloads


async def _require_fanqie_production_running(
    session: AsyncSession, *, project_id: UUID, slug: str,
    segment_number: int, progress: ProgressCallback | None,
) -> None:
    from bestseller.services.production_control import (
        ProductionControlHaltedError, load_control_state,
    )

    # No exception fallback: an unavailable control read cannot authorize work.
    control = await load_control_state(session, project_id)
    if control.halted:
        _emit_progress(progress, "fanqie_halted_by_operator", {
            "project_slug": slug, "next_segment_number": segment_number,
            **control.to_payload(),
        })
        raise ProductionControlHaltedError(control)


async def _run_fanqie_segments(
    session: AsyncSession, settings: AppSettings, project: ProjectModel, *,
    requested_by: str, progress: ProgressCallback | None = None,
) -> None:
    """Reuse adopted segments and stop at the first unclosed real source."""
    from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
    from bestseller.services.adopted_knowledge_resume import (
        read_adopted_knowledge,
        resume_adopted_knowledge,
    )

    project_id, slug, segment_count = project.id, project.slug, project.target_chapters

    async def adopted_source(
        number: int,
    ) -> tuple[ChapterModel | None, ChapterDraftVersionModel | None]:
        chapter = await session.scalar(select(ChapterModel).where(
            ChapterModel.project_id == project_id, ChapterModel.chapter_number == number,
        ).execution_options(populate_existing=True))
        if chapter is None:
            return None, None
        draft, _ = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
        return chapter, draft

    for chapter_no in range(1, segment_count + 1):
        await _require_fanqie_production_running(
            session, project_id=project_id, slug=slug,
            segment_number=chapter_no, progress=progress,
        )
        _emit_progress(progress, "fanqie_segment_writing_started", {
            "project_slug": slug, "segment_number": chapter_no, "segment_total": segment_count,
        })
        chapter, draft = await adopted_source(chapter_no)
        if draft is None:
            review_only = {}
            if chapter is not None:
                pending = await session.scalar(select(ChapterDraftVersionModel).where(
                    ChapterDraftVersionModel.project_id == project_id,
                    ChapterDraftVersionModel.chapter_id == chapter.id,
                    ChapterDraftVersionModel.is_current.is_(True),
                ).execution_options(populate_existing=True))
                if pending is not None:
                    from bestseller.services.adopted_source_correction import read_adopted_source_correction_context

                    correction = await read_adopted_source_correction_context(
                        session, project_id=project_id, chapter_id=chapter.id,
                        content_md=pending.content_md,
                    )
                    if correction is not None:
                        # An approved exact edit needs its own real review, not
                        # another writer/style transform before that review.
                        review_only = {
                            "chapter_first": True, "resume_existing_draft_only": True,
                            "supersede_pending_rewrites": False,
                            "review_source_binding": {
                                "draft_id": str(pending.id),
                                "body_hash": correction["candidate_body_sha256"],
                            },
                        }
            await run_chapter_pipeline(
                session, settings, slug, chapter_no, requested_by=requested_by,
                export_markdown=False, progress=progress, **review_only,
            )
            # A candidate, rewrite task or a returned result is not adoption.
            await _checkpoint_commit(session)
            chapter, draft = await adopted_source(chapter_no)
            if draft is None:
                raise ValueError(f"fanqie_segment_not_adopted:{chapter_no}")
            _, knowledge = await read_adopted_knowledge(
                session, project_id=project_id, chapter_id=chapter.id,
                source_chapter_draft_version_id=draft.id, chapter_md=draft.content_md,
                require_bible=True,
            )
            if not knowledge['closed']:
                raise ValueError(f"fanqie_segment_knowledge_not_closed:{chapter_no}")
        else:
            await resume_adopted_knowledge(
                session, settings, project=project, chapter=chapter, draft=draft,
                workflow_type='fanqie_segment_knowledge_resume',
                require_bible=True,
            )
        _emit_progress(progress, "fanqie_segment_writing_completed", {
            "project_slug": slug, "segment_number": chapter_no,
        })
        # Preserve a completed chapter/knowledge return before observing pause.
        # The final boundary also prevents falling through to whole-book export.
        await _checkpoint_commit(session)
        await _require_fanqie_production_running(
            session, project_id=project_id, slug=slug,
            segment_number=chapter_no + 1, progress=progress,
        )


def _write_fanqie_source_artifacts(
    output_dir: Path,
    *,
    project: ProjectModel,
    premise: str,
    beat_sheet: Any,
    outline_payload: dict[str, Any],
    book_spec: dict[str, Any] | None = None,
    cast_spec: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Write planning/source artifacts so repair audit can operate autonomously."""
    output_dir.mkdir(parents=True, exist_ok=True)
    project_path = output_dir / "project.md"
    bible_path = output_dir / "story-bible.md"
    outline_path = output_dir / "outline.md"

    project_path.write_text(
        "\n".join(
            [
                f"# {project.title}",
                "",
                f"- slug: {project.slug}",
                f"- genre: {project.genre}",
                f"- sub_genre: {getattr(project, 'sub_genre', None) or ''}",
                f"- target_words: {project.target_word_count}",
                f"- segment_count: {project.target_chapters}",
                "",
                "## Premise",
                premise or "",
            ]
        ),
        encoding="utf-8",
    )
    bible_path.write_text(
        json.dumps(
            {
                "book_spec": book_spec or {},
                "cast_spec": cast_spec or {},
                "fanqie_beat_sheet": (
                    beat_sheet.model_dump(mode="json")
                    if hasattr(beat_sheet, "model_dump")
                    else beat_sheet
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    outline_path.write_text(
        json.dumps(outline_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "project_source_path": str(project_path.resolve()),
        "story_bible_source_path": str(bible_path.resolve()),
        "outline_source_path": str(outline_path.resolve()),
    }


def assemble_fanqie_whole_story(
    project: ProjectModel,
    chapter_payloads: list[tuple[ChapterModel, ChapterDraftVersionModel]],
) -> str:
    """拼接为单篇正文（段间空行，不加连载章标题）。"""
    parts: list[str] = []
    for _chapter, draft in chapter_payloads:
        body = sanitize_novel_markdown_content(draft.content_md or "")
        body = body.strip()
        if body.startswith("#"):
            lines = body.splitlines()
            body = "\n".join(lines[1:]).strip()
        if body:
            parts.append(body)
    return "\n\n".join(parts).strip()


async def _deslop_whole_short_story(
    session: AsyncSession,
    settings: AppSettings,
    full_text: str,
    *,
    project: Any,
    progress: ProgressCallback | None = None,
    workflow_run_id: UUID | None = None,
    observe_candidate: Any = None,
) -> str:
    """Whole-piece 去AI味 pass for a finished short story.

    Mirrors the long-novel ``run_chapter_pipeline`` cleanup but on the assembled
    piece: detect → if it carries the discourse tells the span patcher can't fix
    (or blocks) → one bounded ``revise_prose_deslop``. The unlock line is added
    later by the exporter from a word ratio (not embedded in the prose), so a
    sentence-level rewrite never disturbs it. Failures keep the original text.
    The returned candidate grants no adoption or publication authority.
    """

    from bestseller.services.source_bound_planning_content import reader_first_binding

    if reader_first_binding(project) is not None:
        # New-protocol edits need a chapter source, frozen anchors and the same
        # persistent two-round budget. This advisory full-book candidate cannot
        # be adopted; all normal chapter and whole-book quality gates still run.
        _emit_progress(progress, "fanqie_deslop_chapter_scope_required", {
            "project_slug": project.slug, "quality_pass_asserted": False,
            "reason": "use_bound_chapter_revision_budget",
        })
        return full_text
    if not full_text or not full_text.strip():
        return full_text
    try:
        from bestseller.services.ai_flavor_gate import (
            needs_deslop_revise,
            run_ai_flavor_gate,
        )
        from bestseller.services.deslop_revise import revise_prose_deslop
        from bestseller.services.quality_gates_config import get_quality_gates_config

        cfg = get_quality_gates_config().ai_flavor
        if not cfg.enabled or not getattr(cfg, "deslop_revise_enabled", True):
            return full_text
        lang = getattr(project, "language", None) or "zh-CN"
        outcome = run_ai_flavor_gate(
            chapter_number=0,
            content_md=full_text,
            language=lang,
            config=cfg,
            llm_rewriter=None,
            project_output_dir=None,
        )
        if not needs_deslop_revise(outcome):
            return full_text
        _emit_progress(
            progress,
            "fanqie_deslop_started",
            {"project_slug": project.slug, "before_score": outcome.before_score},
        )
        from bestseller.services import llm

        retained_settings = settings.model_copy(deep=True)
        retained_settings.artifact_store.retain_prompt_payloads = True
        with llm.bind_project_prompt_audit(
            project.id, reason="Whole-story candidate; adoption required",
        ):
            cleaned = await revise_prose_deslop(
                session, retained_settings, content=full_text, language=lang,
                project_id=project.id, workflow_run_id=workflow_run_id,
                target_chars=count_words(full_text), rounds=3, observe_candidate=observe_candidate,
            )
        _emit_progress(
            progress,
            "fanqie_deslop_completed",
            {"project_slug": project.slug, "changed": cleaned != full_text},
        )
        return cleaned or full_text
    except Exception:
        logger.warning(
            "Fanqie whole-story deslop failed for %s (non-fatal)",
            getattr(project, "slug", "?"),
            exc_info=True,
        )
        return full_text


@project_prompt_audit_scope
async def run_fanqie_short_pipeline(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project_payload: ProjectCreate,
    premise: str,
    requested_by: str = "system",
    export_markdown: bool = True,
    progress: ProgressCallback | None = None,
) -> AutowriteResult:
    if project_payload.project_type != ProjectType.FANQIE_SHORT:
        raise ValueError("run_fanqie_short_pipeline requires project_type=fanqie_short")

    slug = project_payload.slug
    _emit_progress(progress, "fanqie_short_pipeline_started", {"project_slug": slug})

    project = await get_project_by_slug(session, slug)
    if project is None:
        project = await create_project(session, project_payload, settings)
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "project_creation_completed",
            {"project_slug": slug, "project_type": ProjectType.FANQIE_SHORT.value},
        )
    else:
        if not is_fanqie_short_project(project):
            raise ValueError(f"Project {slug} is not a fanqie short story project")

    bind_resolved_project_prompt_audit(
        project.id, reason="Short-story autowrite project request and paid-return custody",
    )
    from bestseller.services.fanqie_short_plan_resume import (
        ReusedFanqieAutowriteResult,
        require_or_read_existing_complete_fanqie_plan,
    )

    retained_plan = await require_or_read_existing_complete_fanqie_plan(
        session, settings, project=project,
    )
    from bestseller.services.fanqie_foundation_stage_resume import SCHEMA as FOUNDATION_STAGE

    retained_foundation = (
        retained_plan if retained_plan and retained_plan.get("schema") == FOUNDATION_STAGE else None
    )
    if retained_foundation is not None:
        retained_plan = None
    if retained_plan is None:
        await _ensure_project_invariants(session, project, settings)

    meta = dict(project.metadata_json or {})
    unlock_ratio = float(meta.get("unlock_line_ratio") or DEFAULT_UNLOCK_LINE_RATIO)
    pov = str(meta.get("pov") or "first_person")
    protagonist_name = "我" if pov == "first_person" else None
    output_dir = (Path(settings.output.base_dir) / slug).resolve()

    if retained_plan is None:
        _emit_progress(progress, "fanqie_foundation_plan_started", {"project_slug": slug})
        if retained_foundation is None:
            planning_result = await generate_foundation_plan(
                session,
                settings,
                slug,
                premise,
                requested_by=requested_by,
                progress=progress,
            )
        else:
            from types import SimpleNamespace

            planning_result = SimpleNamespace(
                workflow_run_id=UUID(retained_foundation["planning_workflow_run_id"]),
            )
            _emit_progress(progress, "fanqie_selected_foundation_reused", {
                "project_slug": slug,
                "workflow_run_id": str(planning_result.workflow_run_id),
                "foundation_artifact_ids": retained_foundation["foundation_artifact_ids"],
                "materialization_reused": False,
            })
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "fanqie_foundation_plan_completed",
            {
                "project_slug": slug,
                "workflow_run_id": str(planning_result.workflow_run_id),
            },
        )

        if retained_foundation is None:
            book_artifact = await get_latest_planning_artifact(
                session, project_id=project.id, artifact_type=ArtifactType.BOOK_SPEC
            )
            cast_artifact = await get_latest_planning_artifact(
                session, project_id=project.id, artifact_type=ArtifactType.CAST_SPEC
            )
            book_spec = book_artifact.content if book_artifact else {}
            cast_spec = cast_artifact.content if cast_artifact else {}
        else:
            book_spec = retained_foundation["foundation_contents"]["book_spec"]
            cast_spec = retained_foundation["foundation_contents"]["cast_spec"]

        # 母题/故事内核（精简版，volumes=1）。advisory：失败一律降级为 None，绝不阻断成书。
        ideology_kernel = None
        _emit_progress(progress, "fanqie_ideology_kernel_started", {"project_slug": slug})
        try:
            ideology_kernel = await derive_ideology_kernel(
                session,
                settings,
                premise=premise,
                genre=project.genre,
                book_spec=book_spec if isinstance(book_spec, dict) else None,
                volumes=1,
                title=project.title or "",
                language="zh",
                cost_style=resolve_cost_style(getattr(project, "metadata_json", None) or {}),
                project_id=project.id,
                workflow_run_id=planning_result.workflow_run_id,
                metadata=getattr(project, "metadata_json", None) or {},
            )
            _emit_progress(
                progress,
                "fanqie_ideology_kernel_completed",
                {
                    "project_slug": slug,
                    "primary_motif": getattr(
                        getattr(ideology_kernel, "primary_motif", None), "motif_key", ""
                    ),
                    "thesis_statement": getattr(ideology_kernel, "thesis_statement", ""),
                },
            )
        except Exception:
            logger.warning(
                "Fanqie ideology kernel derivation failed for %s; continuing without it",
                slug,
                exc_info=True,
            )
            _emit_progress(
                progress, "fanqie_ideology_kernel_skipped", {"project_slug": slug}
            )

        _emit_progress(progress, "fanqie_beat_sheet_started", {"project_slug": slug})
        beat_sheet = await generate_fanqie_beat_sheet(
            session,
            settings,
            slug,
            premise,
            book_spec=book_spec if isinstance(book_spec, dict) else None,
            cast_spec=cast_spec if isinstance(cast_spec, dict) else None,
            ideology_kernel=ideology_kernel,
            requested_by=requested_by,
        )
        # The paid Beat and its producer outcome survive a later projection
        # failure. A failed consumer must not roll back the durable paid stage.
        await _checkpoint_commit(session)
        outline_payload = build_fanqie_segment_outline_batch(
            project,
            beat_sheet,
            book_spec=book_spec if isinstance(book_spec, dict) else None,
            cast_spec=cast_spec if isinstance(cast_spec, dict) else None,
        )
        from bestseller.services.fanqie_outline_contract_completion import (
            complete_missing_outline_contract,
        )

        outline_payload = await complete_missing_outline_contract(
            session, settings, project_id=project.id, outline_payload=outline_payload,
            requested_by=requested_by,
        )
        outline_payload = await prepare_reader_first_short_outline(
            session, settings, project_id=project.id, outline_payload=outline_payload,
            workflow_run_id=planning_result.workflow_run_id, requested_by=requested_by,
        )
        source_paths = _write_fanqie_source_artifacts(
            output_dir,
            project=project,
            premise=premise,
            beat_sheet=beat_sheet,
            outline_payload=outline_payload,
            book_spec=book_spec if isinstance(book_spec, dict) else None,
            cast_spec=cast_spec if isinstance(cast_spec, dict) else None,
        )
        await persist_fanqie_chapter_outline(session, slug, outline_payload)
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "fanqie_beat_sheet_completed",
            {
                "project_slug": slug,
                "segment_count": len(beat_sheet.beats),
                "unlock_milestone_segment": beat_sheet.unlock_milestone_segment,
                "source_paths": source_paths,
            },
        )

        _emit_progress(progress, "fanqie_materialization_started", {"project_slug": slug})
        bible_result = await materialize_latest_story_bible(
            session, slug, requested_by=requested_by
        )
        outline_result = await materialize_latest_chapter_outline_batch(
            session, slug, requested_by=requested_by
        )
        await _checkpoint_commit(session)
        from bestseller.services.fanqie_outline_source_selection import (
            admit_and_select_outline_source,
        )

        await admit_and_select_outline_source(
            session, settings, project_id=project.id,
            artifact_id=outline_result.source_artifact_id,
            materialization_workflow_id=outline_result.workflow_run_id,
            requested_by=requested_by,
        )
        await _checkpoint_commit(session)
        _emit_progress(progress, "narrative_graph_materialization_started", {"project_slug": slug})
        narrative_graph_result = await materialize_latest_narrative_graph(
            session, slug, requested_by=requested_by
        )
        await _checkpoint_commit(session)
        _emit_progress(
            progress,
            "narrative_graph_materialization_completed",
            {
                "project_slug": slug,
                "workflow_run_id": str(narrative_graph_result.workflow_run_id),
                "plot_arc_count": narrative_graph_result.plot_arc_count,
                "clue_count": narrative_graph_result.clue_count,
            },
        )
        _emit_progress(
            progress,
            "fanqie_materialization_completed",
            {
                "project_slug": slug,
                "segments_created": outline_result.chapters_created,
                "chapters_created": outline_result.chapters_created,
                "scenes_created": outline_result.scenes_created,
            },
        )

    else:
        from types import SimpleNamespace

        planning_result = SimpleNamespace(
            workflow_run_id=retained_plan["planning_workflow_run_id"],
        )
        outline_result = SimpleNamespace(
            workflow_run_id=retained_plan["outline_workflow_run_id"],
            chapters_created=0, scenes_created=0,
        )
        bible_result = SimpleNamespace(
            workflow_run_id=retained_plan["story_bible_workflow_run_id"],
        )
        narrative_graph_result = SimpleNamespace(
            workflow_run_id=retained_plan["narrative_graph_workflow_run_id"],
        )
        beat_sheet = SimpleNamespace(unlock_milestone_segment=retained_plan["unlock_segment"])
        _emit_progress(
            progress, "fanqie_materialized_plan_reused",
            {"project_slug": slug, **{
                key: str(value) if isinstance(value, UUID) else value
                for key, value in retained_plan.items()
            }},
        )
        _emit_progress(
            progress, "fanqie_materialization_completed",
            {"project_slug": slug, "planning_reused": True, "materialization_reused": True,
             "segments_created": 0, "chapters_created": 0, "scenes_created": 0,
             "workflow_run_id": str(outline_result.workflow_run_id)},
        )

    from bestseller.services.fanqie_short_source_export import (
        checkpoint_writing,
        preserve_whole_candidate,
        publish_adopted_assembly,
        read_adopted_assembly,
    )
    await checkpoint_writing(session, project.id)
    await _run_fanqie_segments(
        session, settings, project, requested_by=requested_by, progress=progress,
    )

    # Finish the persisted active round before issuing a new whole review or
    # new tasks. A restart must not fork tasks around a partially adopted round.
    repair_state = (project.metadata_json or {}).get("fanqie_whole_repair") or {}
    if repair_state.get("status") == "running":
        from bestseller.services.fanqie_whole_repair import execute_round

        await execute_round(
            session, settings, project_id=project.id, requested_by=requested_by,
            premise=premise, progress=progress,
        )

    adopted = await read_adopted_assembly(session, project.id)
    full_text = adopted.full_text
    # End the read transaction before any advisory model request.
    await _checkpoint_commit(session)

    async def observe_whole_candidate(observation: dict[str, Any]) -> None:
        preserve_whole_candidate(
            output_dir, producer="deslop_round", body=observation["content"],
            source_manifest=adopted.manifest,
            evidence={
                "llm_run_id": (
                    str(observation["llm_run_id"]) if observation.get("llm_run_id") else None
                ),
                "round": observation.get("round"),
                "parent_content": observation.get("parent_content"),
                "information_preservation": observation.get("information_preservation"),
                "physical_proof_asserted": False,
            },
        )

    candidate_text = await _deslop_whole_short_story(
        session, settings, full_text, project=project, progress=progress,
        workflow_run_id=planning_result.workflow_run_id, observe_candidate=observe_whole_candidate,
    )
    if candidate_text != full_text:
        preserve_whole_candidate(
            output_dir, producer="deslop_selected", body=str(candidate_text),
            source_manifest=adopted.manifest,
        )
    # Only the adopted assembly is reviewed and offered to the official exporter.
    total_words = count_words(full_text)

    _emit_progress(progress, "fanqie_whole_review_started", {"project_slug": slug})
    whole_review = review_whole_fanqie_short_story(
        full_text,
        title=project.title,
        unlock_line_ratio=unlock_ratio,
        protagonist_name=protagonist_name,
    )
    _emit_progress(
        progress,
        "fanqie_whole_review_completed",
        {"project_slug": slug, "passed": whole_review.passed, **whole_review.to_dict()},
    )

    # 文采 advisory：评全文文采分，仅记录/上报，绝不影响 passed 或阻断成书。
    await _require_fanqie_production_running(
        session, project_id=project.id, slug=slug,
        segment_number=project.target_chapters + 1, progress=progress,
    )
    _emit_progress(progress, "fanqie_litstyle_advisory_started", {"project_slug": slug})
    try:
        litstyle_result = await judge_chapter_litstyle_stable(
            session,
            settings,
            chapter_number=0,
            content_md=full_text,
            genre_context=resolve_judge_genre_context(
                genre=project.genre,
                sub_genre=project.sub_genre,
            ),
            language="zh",
            workflow_run_id=planning_result.workflow_run_id,
            project_id=project.id,
        )
        _emit_progress(
            progress,
            "fanqie_litstyle_advisory_completed",
            {
                "project_slug": slug,
                "litstyle_score": getattr(litstyle_result, "final_score", None),
                "litstyle_level": getattr(litstyle_result, "level", ""),
            },
        )
    except Exception:
        logger.warning(
            "Fanqie litstyle advisory judge failed for %s (non-fatal)",
            slug,
            exc_info=True,
        )
        _emit_progress(
            progress, "fanqie_litstyle_advisory_skipped", {"project_slug": slug}
        )

    await _require_fanqie_production_running(
        session, project_id=project.id, slug=slug,
        segment_number=project.target_chapters + 1, progress=progress,
    )
    outcome = await publish_adopted_assembly(
        session, project_id=project.id, output_dir=output_dir, expected=adopted,
        unlock_line_ratio=unlock_ratio, protagonist_name=protagonist_name,
        export_markdown=export_markdown,
        unlock_segment=beat_sheet.unlock_milestone_segment,
        settings=settings,
    )
    # Normal autowrite closes issued whole-book work for both new and legacy
    # projects. Each candidate retains its own length protocol, and every send
    # still needs the original native financial and control admission.
    if export_markdown:
        from bestseller.services.fanqie_whole_repair import execute_round

        while not outcome["effective_passed"]:
            repaired = await execute_round(
                session, settings, project_id=project.id, outcome=outcome,
                requested_by=requested_by, premise=premise, progress=progress,
            )
            if not repaired:
                break
            adopted = await read_adopted_assembly(session, project.id)
            await _checkpoint_commit(session)
            outcome = await publish_adopted_assembly(
                session, project_id=project.id, output_dir=output_dir, expected=adopted,
                unlock_line_ratio=unlock_ratio, protagonist_name=protagonist_name,
                export_markdown=export_markdown,
                unlock_segment=beat_sheet.unlock_milestone_segment, settings=settings,
            )
    full_text, whole_review = outcome["full_text"], outcome["review"]
    effective_passed = outcome.get("effective_passed", whole_review.passed)
    semantic_gates = outcome.get("current_semantic") or {}
    total_words = count_words(full_text)
    export_paths, rejected_paths = outcome["export_paths"], outcome["rejected_paths"]
    ranking_rewrite_task_count = outcome["revision_task_counts"]["ranking"]
    short_v2_rewrite_task_count = outcome["revision_task_counts"]["short_v2"]
    _emit_progress(
        progress, "fanqie_export_completed" if export_paths else "fanqie_export_skipped",
        {"project_slug": slug, "status": outcome["status"], "total_words": total_words,
         "candidate_paths": outcome["candidate_paths"], **export_paths, **rejected_paths},
    )

    _emit_progress(
        progress,
        "fanqie_short_pipeline_completed",
        {
            "project_slug": slug,
            "total_words": total_words,
            "whole_review_passed": effective_passed,
            "heuristic_whole_review_passed": whole_review.passed,
            "current_semantic": outcome.get("current_semantic"),
            "ranking_gate_passed": semantic_gates.get(
                "ranking_gate_passed", whole_review.ranking_passed,
            ),
            "short_v2_gate_passed": semantic_gates.get(
                "short_v2_gate_passed", whole_review.short_v2_passed,
            ),
            "ranking_rewrite_task_count": ranking_rewrite_task_count,
            "short_v2_rewrite_task_count": short_v2_rewrite_task_count,
        },
    )

    result_type = ReusedFanqieAutowriteResult if retained_plan else AutowriteResult
    reuse_fields = {"plan_reuse_receipt": retained_plan} if retained_plan else {}
    return result_type(
        project_id=project.id,
        project_slug=slug,
        planning_workflow_run_id=planning_result.workflow_run_id,
        story_bible_workflow_run_id=bible_result.workflow_run_id,
        outline_workflow_run_id=outline_result.workflow_run_id,
        narrative_graph_workflow_run_id=narrative_graph_result.workflow_run_id,
        project_workflow_run_id=planning_result.workflow_run_id,
        chapter_count=len(adopted.manifest["sources"]),
        whole_book_complete=outcome["whole_book_complete"],
        export_status=(
            "exported"
            if export_paths
            else "rejected_quality_gate"
            if rejected_paths
            else "not_exported"
        ),
        output_path=export_paths.get("markdown_path"),
        output_dir=str(output_dir),
        output_files=(
            list(export_paths.values()) if export_paths else list(rejected_paths.values())
        ),
        final_verdict="pass" if effective_passed else "needs_revision",
        requires_human_review=not effective_passed,
        **reuse_fields,
    )
