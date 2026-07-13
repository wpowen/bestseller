"""Planner prompt assembly helpers."""

from __future__ import annotations

from typing import Any

from bestseller.services.methodology_compiler import (
    ChapterPosition,
    MethodologyStage,
    compile_methodology,
)
from bestseller.services.prompt_packs import infer_default_prompt_pack_key


def attach_planner_methodology(
    user_prompt: str,
    *,
    stage: MethodologyStage,
    project_ctx: Any,
    chapter_no: int | None = None,
    chapter_position: ChapterPosition | None = None,
    token_budget: int = 800,
) -> str:
    """Prepend stage-aware methodology to a planner user prompt."""

    language = _language(project_ctx)
    if language.lower().startswith("en"):
        return user_prompt
    compiled = compile_methodology(
        stage=stage,
        prompt_pack_key=_prompt_pack_key(project_ctx),
        language=language,
        chapter_no=chapter_no,
        chapter_position=chapter_position,
        token_budget=token_budget,
    )
    if not compiled.text:
        return user_prompt
    return f"{compiled.text}\n\n---\n\n{user_prompt}"


def _language(ctx: Any) -> str:
    return str(getattr(ctx, "language", None) or "zh-CN")


def _prompt_pack_key(ctx: Any) -> str | None:
    explicit = getattr(ctx, "prompt_pack_key", None)
    if explicit:
        return str(explicit)
    metadata = getattr(ctx, "metadata_json", None) or {}
    if isinstance(metadata, dict):
        contract = metadata.get("genre_intent_contract")
        if isinstance(contract, dict):
            contract_pack = contract.get("prompt_pack_key")
            if contract_pack:
                return str(contract_pack)
        explicit = (
            metadata.get("prompt_pack_key")
            or (metadata.get("market") or {}).get("prompt_pack_key")
        )
        if explicit:
            return str(explicit)
    genre = getattr(ctx, "genre", None)
    sub_genre = getattr(ctx, "sub_genre", None)
    return infer_default_prompt_pack_key(str(genre or ""), str(sub_genre) if sub_genre else None)


__all__ = ["attach_planner_methodology"]
