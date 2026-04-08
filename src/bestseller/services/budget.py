from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import LlmRunModel
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)


class TokenUsageSummary(BaseModel):
    project_id: UUID
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    llm_call_count: int = 0


class BudgetCheckResult(BaseModel):
    within_budget: bool = True
    usage: TokenUsageSummary
    budget_limit: int = 0
    utilization_pct: float = 0.0
    warning_level: str | None = None  # "50%", "80%", "exceeded"


async def get_project_token_usage(
    session: AsyncSession,
    project_id: UUID,
    settings: AppSettings,
) -> TokenUsageSummary:
    """Aggregate token usage across all LLM runs for a project."""
    result = await session.execute(
        select(
            func.coalesce(func.sum(LlmRunModel.input_tokens), 0).label("total_input"),
            func.coalesce(func.sum(LlmRunModel.output_tokens), 0).label("total_output"),
            func.count(LlmRunModel.id).label("call_count"),
        ).where(LlmRunModel.project_id == project_id)
    )
    row = result.one()
    total_input = int(row.total_input)
    total_output = int(row.total_output)
    cost = (
        total_input / 1000 * settings.budget.cost_per_1k_input_tokens
        + total_output / 1000 * settings.budget.cost_per_1k_output_tokens
    )
    return TokenUsageSummary(
        project_id=project_id,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_tokens=total_input + total_output,
        estimated_cost_usd=round(cost, 4),
        llm_call_count=int(row.call_count),
    )


def estimate_project_cost(
    chapter_count: int,
    settings: AppSettings,
) -> dict[str, Any]:
    """Pre-generation cost estimation based on typical per-chapter token usage."""
    # Empirical averages: ~15k input + ~4k output per scene, ~3 scenes per chapter
    scenes_per_chapter = settings.generation.scenes_per_chapter.target
    avg_input_per_scene = 15_000
    avg_output_per_scene = 4_000
    # Add review + knowledge refresh overhead (~1.5x)
    overhead_factor = 1.5

    total_input = int(chapter_count * scenes_per_chapter * avg_input_per_scene * overhead_factor)
    total_output = int(chapter_count * scenes_per_chapter * avg_output_per_scene * overhead_factor)
    cost = (
        total_input / 1000 * settings.budget.cost_per_1k_input_tokens
        + total_output / 1000 * settings.budget.cost_per_1k_output_tokens
    )
    return {
        "chapter_count": chapter_count,
        "estimated_input_tokens": total_input,
        "estimated_output_tokens": total_output,
        "estimated_total_tokens": total_input + total_output,
        "estimated_cost_usd": round(cost, 2),
        "note": "Estimates based on typical usage; actual may vary by genre and complexity.",
    }


async def check_budget(
    session: AsyncSession,
    project_id: UUID,
    settings: AppSettings,
) -> BudgetCheckResult:
    """Check if project is within budget. Returns warning levels at configured thresholds."""
    usage = await get_project_token_usage(session, project_id, settings)
    limit = settings.budget.max_tokens_per_project

    if limit <= 0:
        return BudgetCheckResult(
            within_budget=True,
            usage=usage,
            budget_limit=0,
            utilization_pct=0.0,
            warning_level=None,
        )

    utilization = usage.total_tokens / limit if limit > 0 else 0.0
    warning_level: str | None = None
    within_budget = True

    for threshold in sorted(settings.budget.warning_thresholds):
        if utilization >= threshold:
            warning_level = f"{int(threshold * 100)}%"
            if threshold >= 1.0:
                within_budget = False

    if warning_level is not None:
        logger.warning(
            "Budget check for project %s: utilization=%.1f%%, warning=%s, within_budget=%s",
            project_id,
            utilization * 100,
            warning_level,
            within_budget,
        )

    return BudgetCheckResult(
        within_budget=within_budget,
        usage=usage,
        budget_limit=limit,
        utilization_pct=round(utilization * 100, 1),
        warning_level=warning_level,
    )
