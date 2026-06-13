from __future__ import annotations

# ruff: noqa: RUF001
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
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.projects import get_project_by_slug
from bestseller.services.quality_gates_config import get_quality_gates_config
from bestseller.settings import AppSettings

ChapterRevisionOperation = Literal["polish", "humanize", "issue"]


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


def _fallback_polish_result() -> LitStyleJudgeResult:
    config = load_litstyle_config()
    return LitStyleJudgeResult(
        dimension_scores={dim.key: 0 for dim in config.dimensions},
        ai_tone_penalty=max(0, int(config.ai_tone_mature_ceiling) + 1),
        final_score=0,
        level="需润色",
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
        system_prompt, user_prompt = build_litstyle_polish_prompt(
            draft=source,
            result=_fallback_polish_result(),
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
    chapter.production_state = "ok"
    return {
        "ok": True,
        "rewrite_task_id": str(task.id),
        "chapter_number": int(chapter.chapter_number),
        "operation": operation,
        "source_version_no": int(current.version_no),
        "result_version_no": int(new_draft.version_no),
        "word_count": word_count,
    }
