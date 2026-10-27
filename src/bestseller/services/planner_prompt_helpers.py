"""Planner prompt assembly helpers."""

from __future__ import annotations

from typing import Any

from bestseller.services.methodology_compiler import (
    ChapterPosition,
    MethodologyStage,
)
from bestseller.services.methodology_bridge import render_methodology_applicability


def attach_planner_methodology(
    user_prompt: str,
    *,
    stage: MethodologyStage,
    project_ctx: Any,
    chapter_no: int | None = None,
    chapter_position: ChapterPosition | None = None,
    token_budget: int = 800,
) -> str:
    """Preserve the task and add optional craft within its source authority.

    Stage/position/pack taxonomy must not silently select a mandatory recipe.
    The existing arguments remain compatible with all planner entry points.
    """
    if token_budget <= 0:
        return user_prompt
    boundary = render_methodology_applicability(
        language=_language(project_ctx), phase="planner",
    )
    return f"{boundary}\n\n---\n\n{user_prompt}"


def _language(ctx: Any) -> str:
    return str(getattr(ctx, "language", None) or "zh-CN")


__all__ = ["attach_planner_methodology"]
