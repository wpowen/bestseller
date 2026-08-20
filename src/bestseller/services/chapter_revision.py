from __future__ import annotations

from bestseller.services.draft_promotion import draft_supersession_codes

# ruff: noqa: RUF001
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.litstyle_judge import LitStyleJudgeResult
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    RewriteTaskModel,
)
from bestseller.services.ai_flavor_gate import run_ai_flavor_gate
from bestseller.services.drafts import (
    has_meta_leak,
    sanitize_novel_markdown_content,
    strip_scaffolding_echoes,
    validate_and_clean_novel_content,
)
from bestseller.services.exports import build_markdown_reading_stats
from bestseller.services.litstyle_polish import build_litstyle_polish_prompt
from bestseller.services.litstyle_prose import load_litstyle_config
from bestseller.services.litstyle_prose_judge import judge_chapter_litstyle
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.projects import get_project_by_slug
from bestseller.services.quality_gates_config import get_quality_gates_config
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

ChapterRevisionOperation = Literal["polish", "humanize", "issue"]

# Candidate drafts shorter than this fraction of the source are treated as a
# failed generation (truncated/refused output), not a legitimate revision.
_MIN_CANDIDATE_LENGTH_RATIO = 0.6


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _load_current_chapter_draft(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
) -> tuple[ProjectModel, ChapterModel, ChapterDraftVersionModel]:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")
    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        raise ValueError(f"Chapter {chapter_number} was not found for '{project_slug}'.")
    current = await session.scalar(
        select(ChapterDraftVersionModel)
        .where(
            ChapterDraftVersionModel.chapter_id == chapter.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .order_by(ChapterDraftVersionModel.version_no.desc())
        .limit(1)
    )
    if current is None:
        raise ValueError(f"Chapter {chapter_number} does not have a current draft.")
    return project, chapter, current


async def create_chapter_revision_task(
    session: AsyncSession,
    project_slug: str,
    chapter_number: int,
    *,
    operation: str,
    instructions: str = "",
    requested_by: str = "web-ui",
) -> RewriteTaskModel:
    project, chapter, current = await _load_current_chapter_draft(
        session,
        project_slug,
        chapter_number,
    )
    strategy = {
        "polish": "chapter_polish",
        "humanize": "chapter_humanize",
        "issue": "chapter_issue_edit",
        "regenerate": "chapter_regenerate",
    }.get(operation)
    if strategy is None:
        raise ValueError("Unsupported chapter revision operation.")
    task = RewriteTaskModel(
        project_id=project.id,
        trigger_type="reader_chapter_revision",
        trigger_source_id=chapter.id,
        rewrite_strategy=strategy,
        priority=2,
        status="queued",
        instructions=instructions or operation,
        context_required=["current_chapter_draft", "reader_operation"],
        metadata_json={
            "requested_by": requested_by,
            "operation": operation,
            "chapter_id": str(chapter.id),
            "chapter_number": int(chapter.chapter_number),
            "source_chapter_draft_id": str(current.id),
            "source_chapter_draft_version_no": int(current.version_no),
            "source_word_count": int(current.word_count or 0),
        },
    )
    session.add(task)
    await session.flush()
    return task


def _neutral_polish_result() -> LitStyleJudgeResult:
    """Judge-unavailable fallback: full marks → generic light polish only.

    2026-07-04: the old fallback fabricated an all-zero judge reading, which
    made the polish prompt claim "FinalScore=0/100" and fire the 4 weakest-
    dimension directives + the AI-tone directive on every draft regardless of
    actual quality — good prose got blind-edited. A neutral (full-score)
    result yields zero low dimensions, so the prompt builder falls back to its
    own "light generic pass" directive instead of fabricated fixes.
    """

    config = load_litstyle_config()
    return LitStyleJudgeResult(
        dimension_scores={dim.key: dim.max for dim in config.dimensions},
        ai_tone_penalty=0,
        final_score=100,
        level="轻润色",
        revision_priority=("按原剧情做定点语言润色，不增删情节。",),
    )


async def _build_revision_candidate(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    current: ChapterDraftVersionModel,
    task: RewriteTaskModel,
    operation: ChapterRevisionOperation,
) -> tuple[str, dict[str, Any], UUID | None]:
    source = current.content_md or ""
    llm_run_id: UUID | None = None
    source_judge: LitStyleJudgeResult | None = None
    metadata: dict[str, Any] = {
        "operation_started_at": _now_iso(),
        "operation": operation,
    }
    if operation == "humanize":
        cfg = get_quality_gates_config().ai_flavor
        outcome = run_ai_flavor_gate(
            chapter_number=int(chapter.chapter_number),
            content_md=source,
            language=getattr(project, "language", None) or "zh-CN",
            config=cfg,
            project_output_dir=Path(settings.output.base_dir) / project.slug,
        )
        metadata["ai_flavor_gate"] = {
            "decision": outcome.decision,
            "before_score": outcome.before_score,
            "after_score": outcome.after_score,
            "metrics": outcome.metrics,
            "edit_count": len(outcome.edits),
        }
        return outcome.patched_text or source, metadata, None

    if operation == "polish":
        # Run the real advisory judge so the polish directives target this
        # draft's actual weak dimensions (module contract of litstyle_polish:
        # the loop is only safe with a real reading + caller keep-better).
        try:
            source_judge = await judge_chapter_litstyle(
                session,
                settings,
                chapter_number=int(chapter.chapter_number),
                content_md=source,
                language=getattr(project, "language", None) or "zh",
            )
        except Exception:  # pragma: no cover - judge outage must not kill polish
            logger.warning("litstyle judge failed for polish; using neutral pass", exc_info=True)
        result = source_judge or _neutral_polish_result()
        metadata["source_litstyle_score"] = int(result.final_score)
        system_prompt, user_prompt = build_litstyle_polish_prompt(
            draft=source,
            result=result,
        )
        prompt_template = "reader_chapter_polish"
        fallback = source
    else:
        system_prompt = (
            "你是中文小说编辑。根据读者/作者的问题描述修改本章正文。必须保留剧情连续性、"
            "人物设定和已发生事实，不要输出解释、标题或修改说明，只输出修改后的正文。"
        )
        user_prompt = (
            f"项目: {project.title} ({project.slug})\n"
            f"章节: 第{chapter.chapter_number}章 {chapter.title or ''}\n"
            f"问题描述:\n{task.instructions}\n\n"
            f"原文:\n{source}"
        )
        prompt_template = "reader_chapter_issue_edit"
        fallback = source

    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="editor",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=fallback,
            prompt_template=prompt_template,
            prompt_version="1.0",
            project_id=project.id,
            metadata={
                "project_slug": project.slug,
                "chapter_number": int(chapter.chapter_number),
                "rewrite_task_id": str(task.id),
                "operation": operation,
            },
        ),
    )
    candidate = sanitize_novel_markdown_content(completion.content) or fallback
    candidate = strip_scaffolding_echoes(candidate)
    if has_meta_leak(candidate):
        candidate = await validate_and_clean_novel_content(
            session,
            settings,
            candidate,
            project_id=project.id,
        )
    metadata.update(
        {
            "model_name": completion.model_name,
            "generation_mode": completion.provider,
            "llm_run_id": str(completion.llm_run_id) if completion.llm_run_id else None,
        }
    )
    llm_run_id = completion.llm_run_id

    # Keep-better (polish only): the litstyle polish loop is only safe when the
    # caller keeps the higher-scoring of {original, polished}. A candidate that
    # scores below the source is discarded — returning the source lets the
    # upstream no-change guard skip version creation.
    if (
        operation == "polish"
        and source_judge is not None
        and candidate.strip()
        and candidate.strip() != source.strip()
    ):
        try:
            candidate_judge = await judge_chapter_litstyle(
                session,
                settings,
                chapter_number=int(chapter.chapter_number),
                content_md=candidate,
                language=getattr(project, "language", None) or "zh",
            )
            metadata["candidate_litstyle_score"] = int(candidate_judge.final_score)
            if candidate_judge.final_score < source_judge.final_score:
                metadata["polish_rejected_lower_score"] = True
                return source, metadata, llm_run_id
        except Exception:  # pragma: no cover - judge outage: accept the candidate
            logger.warning(
                "litstyle judge failed on polish candidate; accepting without keep-better",
                exc_info=True,
            )
    return candidate, metadata, llm_run_id


async def apply_chapter_revision_task(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    chapter_number: int,
    *,
    rewrite_task_id: UUID,
    operation: ChapterRevisionOperation,
) -> dict[str, Any]:
    project, chapter, current = await _load_current_chapter_draft(
        session,
        project_slug,
        chapter_number,
    )
    task = await session.get(RewriteTaskModel, rewrite_task_id)
    if task is None or task.project_id != project.id:
        raise ValueError("Rewrite task was not found for this project.")
    content_md, op_metadata, llm_run_id = await _build_revision_candidate(
        session,
        settings,
        project=project,
        chapter=chapter,
        current=current,
        task=task,
        operation=operation,
    )
    source_md = current.content_md or ""

    # No-change guard: when the candidate equals the source (LLM fallback,
    # humanize with zero edits, judge-rejected polish) do not mint a new
    # version and do not touch production_state.
    if content_md.strip() == source_md.strip():
        task.status = "completed"
        task.attempts = int(task.attempts or 0) + 1
        task.metadata_json = {
            **(task.metadata_json or {}),
            **op_metadata,
            "no_change": True,
            "completed_at": _now_iso(),
        }
        return {
            "ok": True,
            "rewrite_task_id": str(task.id),
            "chapter_number": int(chapter.chapter_number),
            "operation": operation,
            "source_version_no": int(current.version_no),
            "result_version_no": int(current.version_no),
            "no_change": True,
            "word_count": int(current.word_count or 0),
        }

    # Truncation guard: a candidate that lost >40% of the source is a broken
    # generation — keep the original instead of publishing it.
    if len(content_md.strip()) < len(source_md.strip()) * _MIN_CANDIDATE_LENGTH_RATIO:
        task.status = "failed"
        task.attempts = int(task.attempts or 0) + 1
        task.metadata_json = {
            **(task.metadata_json or {}),
            **op_metadata,
            "rejected_reason": "candidate_too_short",
            "candidate_length": len(content_md.strip()),
            "source_length": len(source_md.strip()),
            "completed_at": _now_iso(),
        }
        return {
            "ok": False,
            "rewrite_task_id": str(task.id),
            "chapter_number": int(chapter.chapter_number),
            "operation": operation,
            "source_version_no": int(current.version_no),
            "result_version_no": int(current.version_no),
            "rejected_reason": "candidate_too_short",
            "word_count": int(current.word_count or 0),
        }

    word_count = int(build_markdown_reading_stats(content_md)["word_count"])
    max_existing_version = int(
        (
            await session.scalar(
                select(func.coalesce(func.max(ChapterDraftVersionModel.version_no), 0)).where(
                    ChapterDraftVersionModel.chapter_id == chapter.id
                )
            )
        )
        or 0
    )
    next_version = max(max_existing_version, int(current.version_no or 0)) + 1
    await session.execute(
        update(ChapterDraftVersionModel)
        .where(
            ChapterDraftVersionModel.chapter_id == chapter.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .values(is_current=False)
    )
    new_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=next_version,
        content_md=content_md,
        word_count=word_count,
        assembled_from_scene_draft_ids=list(current.assembled_from_scene_draft_ids or []),
        is_current=True,
        promotion_reason_codes=draft_supersession_codes(
            origin="revision",
            took_current=True,
            chars=len(content_md or ""),
            supersedes_version=next_version - 1 if next_version > 1 else None,
        ),
        llm_run_id=llm_run_id,
    )
    session.add(new_draft)
    await session.flush()
    task.status = "completed"
    task.attempts = int(task.attempts or 0) + 1
    task.metadata_json = {
        **(task.metadata_json or {}),
        **op_metadata,
        "result_chapter_draft_id": str(new_draft.id),
        "result_chapter_draft_version_no": int(new_draft.version_no),
        "result_word_count": word_count,
        "completed_at": _now_iso(),
    }
    chapter.current_word_count = word_count
    # 2026-07-04: do NOT force production_state="ok" — the revision path runs
    # no quality gates, so promoting a candidate must not whitewash a chapter
    # the pipeline marked blocked/revision. The repair sweep re-evaluates and
    # clears the state once the report is clean.
    return {
        "ok": True,
        "rewrite_task_id": str(task.id),
        "chapter_number": int(chapter.chapter_number),
        "operation": operation,
        "source_version_no": int(current.version_no),
        "result_version_no": int(new_draft.version_no),
        "word_count": word_count,
    }
