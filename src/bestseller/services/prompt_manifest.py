"""Load writer Prompt Compiler reports for the Web「生效片段」panel (B3)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import LlmRunModel, ProjectModel


async def load_chapter_prompt_manifest(
    session: AsyncSession,
    *,
    project_slug: str,
    chapter_number: int,
) -> dict[str, Any]:
    """Return the latest writer prompt_compiler_report for a chapter."""

    project = (
        await session.execute(
            select(ProjectModel).where(ProjectModel.slug == project_slug)
        )
    ).scalar_one_or_none()
    if project is None:
        raise ValueError(f"Project not found: {project_slug}")

    rows = (
        await session.execute(
            select(LlmRunModel)
            .where(
                LlmRunModel.project_id == project.id,
                LlmRunModel.logical_role == "writer",
            )
            .order_by(LlmRunModel.created_at.desc())
            .limit(40)
        )
    ).scalars().all()

    matched: LlmRunModel | None = None
    for row in rows:
        meta = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        try:
            ch = int(meta.get("chapter_number") or 0)
        except (TypeError, ValueError):
            ch = 0
        if ch == int(chapter_number) and isinstance(meta.get("prompt_compiler_report"), dict):
            matched = row
            break

    if matched is None:
        return {
            "ok": True,
            "project_slug": project_slug,
            "chapter_number": int(chapter_number),
            "found": False,
            "kept": [],
            "dropped": [],
            "drop_reasons": {},
            "block_sizes": {},
            "final_hash": None,
            "llm_run_id": None,
        }

    meta = matched.metadata_json if isinstance(matched.metadata_json, dict) else {}
    report = meta.get("prompt_compiler_report") or {}
    kept = list(report.get("kept") or [])
    dropped = list(report.get("dropped") or [])
    drop_reasons = report.get("drop_reasons") or {}
    block_sizes = report.get("block_sizes") or {}
    return {
        "ok": True,
        "project_slug": project_slug,
        "chapter_number": int(chapter_number),
        "found": True,
        "kept": kept,
        "dropped": dropped,
        "drop_reasons": drop_reasons if isinstance(drop_reasons, dict) else {},
        "block_sizes": block_sizes if isinstance(block_sizes, dict) else {},
        "final_hash": report.get("final_hash"),
        "prompt_mode": meta.get("prompt_mode"),
        "generation_mode": meta.get("generation_mode"),
        "llm_run_id": str(matched.id) if isinstance(matched.id, UUID) else str(matched.id),
        "created_at": matched.created_at.isoformat() if matched.created_at else None,
    }


__all__ = ["load_chapter_prompt_manifest"]
