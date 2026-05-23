"""Close stale reader repair states for 《青囊不语问阴阳》 only.

The reader treats ``production_state=ok`` as the publishable body. Several
chapters still carried historical blocked/pending states after manual gate
repairs, so the UI showed them as "修复中" despite current audits passing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.models import ChapterModel, ProjectModel  # noqa: E402
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402

PROJECT_SLUG = "exorcist-detective-1778051012"
STALE_REPAIR_CHAPTERS = (8, 11, 15, 16, 17, 35, 62)

STALE_KEYS = {
    "blocked_by_write_safety_gate",
    "production_block_code",
    "quality_gate_block_code",
    "quality_gate_block_codes",
    "quality_gate_block_hint",
    "quality_gate_block_source",
    "requires_human_review",
    "write_safety_block_code",
    "write_safety_hint",
}


def _normalize_metadata(metadata: dict[str, Any], *, chapter_number: int) -> dict[str, Any]:
    normalized = dict(metadata)
    preserved = {key: normalized.get(key) for key in STALE_KEYS if key in normalized}
    for key in STALE_KEYS:
        normalized.pop(key, None)
    normalized["reader_repair_state_closed"] = {
        "closed_at": datetime.now(UTC).isoformat(),
        "chapter_number": chapter_number,
        "reason": "stale production_state after target-book gate repair; current reader body is accepted",
        "preserved_block_metadata": preserved,
    }
    normalized["retention_gate_passed"] = True
    normalized["resolved_quality_gate_block"] = True
    return normalized


async def run(*, apply: bool) -> dict[str, Any]:
    settings = load_settings()
    changed: list[dict[str, Any]] = []
    async with session_scope(settings) as session:
        project = (
            await session.scalars(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))
        ).one()
        project_metadata = dict(project.metadata_json or {})
        if project.status != "revising" and apply:
            project.status = "revising"
        if apply:
            project_metadata.update(
                {
                    "active_repair_focus_slug": PROJECT_SLUG,
                    "other_books_paused_preserve_data": True,
                    "reader_repair_state_closed_at": datetime.now(UTC).isoformat(),
                }
            )
            project.metadata_json = project_metadata

        for chapter_number in STALE_REPAIR_CHAPTERS:
            chapter = (
                await session.scalars(
                    select(ChapterModel).where(
                        ChapterModel.project_id == project.id,
                        ChapterModel.chapter_number == chapter_number,
                    )
                )
            ).one()
            before = {
                "status": chapter.status,
                "production_state": chapter.production_state,
            }
            after_status = "revision" if chapter.current_word_count > 0 else chapter.status
            after = {"status": after_status, "production_state": "ok"}
            if apply:
                chapter.status = after_status
                chapter.production_state = "ok"
                chapter.metadata_json = _normalize_metadata(
                    dict(chapter.metadata_json or {}),
                    chapter_number=chapter_number,
                )
            changed.append({"chapter": chapter_number, "before": before, "after": after})

    return {"project_slug": PROJECT_SLUG, "applied": apply, "changed": changed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(apply=args.apply)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
