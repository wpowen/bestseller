from __future__ import annotations

import logging
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    CanonFactModel,
    ChapterModel,
    TimelineEventModel,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)


class RollingSummaryResult(BaseModel):
    project_id: UUID
    from_chapter: int
    to_chapter: int
    fact_count_before: int = 0
    summary_fact_created: bool = False
    summary_text: str = ""


async def compress_knowledge_window(
    session: AsyncSession,
    settings: AppSettings,
    project_id: UUID,
    from_chapter: int,
    to_chapter: int,
    *,
    workflow_run_id: UUID | None = None,
) -> RollingSummaryResult:
    """Compress canon facts and timeline events from a chapter range into a rolling summary.

    This creates a single CanonFact with fact_type='rolling_summary' that condenses
    the knowledge from the specified chapter range. Original facts are preserved but
    can be deprioritized in context assembly for chapters far beyond the summary range.
    """
    # Gather existing facts in range
    facts = list(
        await session.scalars(
            select(CanonFactModel).where(
                CanonFactModel.project_id == project_id,
                CanonFactModel.is_current.is_(True),
                CanonFactModel.valid_from_chapter_no >= from_chapter,
                CanonFactModel.valid_from_chapter_no <= to_chapter,
                CanonFactModel.fact_type != "rolling_summary",
            )
        )
    )
    # Gather timeline events in range
    events = list(
        await session.scalars(
            select(TimelineEventModel).where(
                TimelineEventModel.project_id == project_id,
                TimelineEventModel.chapter_id.in_(
                    select(ChapterModel.id).where(
                        ChapterModel.project_id == project_id,
                        ChapterModel.chapter_number >= from_chapter,
                        ChapterModel.chapter_number <= to_chapter,
                    )
                ),
            ).order_by(TimelineEventModel.story_order.asc())
        )
    )

    if not facts and not events:
        return RollingSummaryResult(
            project_id=project_id,
            from_chapter=from_chapter,
            to_chapter=to_chapter,
        )

    # Build summarization prompt
    fact_lines = [
        f"- [{f.subject_label}] {f.predicate}: {f.value_json}"
        for f in facts[:200]  # Cap to avoid prompt overflow
    ]
    event_lines = [
        f"- Ch{e.story_order}: {e.event_name}"
        for e in events[:100]
    ]

    user_prompt = (
        f"Summarize the following canon facts and timeline events from "
        f"chapters {from_chapter}-{to_chapter} into a concise narrative summary "
        f"(max 800 words). Preserve key character developments, plot milestones, "
        f"relationship changes, and world-state changes. Output ONLY the summary text.\n\n"
        f"## Canon Facts ({len(facts)} total, showing up to 200)\n"
        + "\n".join(fact_lines)
        + f"\n\n## Timeline Events ({len(events)} total, showing up to 100)\n"
        + "\n".join(event_lines)
    )

    response = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="summarizer",
            system_prompt="You are a novel knowledge compressor. Your task is to condense story knowledge into concise summaries.",
            user_prompt=user_prompt,
            fallback_response=f"Rolling summary for chapters {from_chapter}-{to_chapter}: {len(facts)} facts and {len(events)} events.",
            prompt_template="rolling_summary",
            project_id=project_id,
            workflow_run_id=workflow_run_id,
        ),
    )
    summary_text = response.content.strip()

    # Create a rolling_summary canon fact
    summary_fact = CanonFactModel(
        project_id=project_id,
        subject_type="project",
        subject_label=f"rolling_summary_ch{from_chapter}_to_ch{to_chapter}",
        predicate="rolling_summary",
        fact_type="rolling_summary",
        value_json={"summary": summary_text, "from_chapter": from_chapter, "to_chapter": to_chapter},
        confidence=0.9,
        source_type="generated",
        valid_from_chapter_no=from_chapter,
        valid_to_chapter_no=to_chapter,
        is_current=True,
        tags=["rolling_summary"],
    )
    session.add(summary_fact)
    await session.flush()

    logger.info(
        "Created rolling summary for project %s chapters %d-%d (%d facts, %d events compressed)",
        project_id,
        from_chapter,
        to_chapter,
        len(facts),
        len(events),
    )

    return RollingSummaryResult(
        project_id=project_id,
        from_chapter=from_chapter,
        to_chapter=to_chapter,
        fact_count_before=len(facts),
        summary_fact_created=True,
        summary_text=summary_text,
    )
