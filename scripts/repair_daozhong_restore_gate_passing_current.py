"""Restore known gate-passing chapter drafts for 《道种破虚》.

The 51/70 repair runs produced valid assembled drafts, then later
chapter-level rewrites generated invalid candidates that were promoted to
``is_current``. This script switches those chapters back to the latest known
gate-passing draft and re-runs the chapter quality gate before committing.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import select, update  # noqa: E402

from bestseller.domain.enums import ChapterStatus  # noqa: E402
from bestseller.infra.db.models import (  # noqa: E402
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.drafts import _evaluate_chapter_quality_gate  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402


PROJECT_SLUG = "xianxia-upgrade-1776137730"

RESTORE_VERSIONS: dict[int, int] = {
    51: 14,
    70: 14,
    389: 11,
}


async def _load_candidate(
    session: Any,
    *,
    project: ProjectModel,
    chapter_number: int,
    version_no: int,
) -> tuple[ChapterModel, ChapterDraftVersionModel]:
    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number,
        )
    )
    if chapter is None:
        raise RuntimeError(f"chapter not found: {chapter_number}")

    draft = await session.scalar(
        select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.chapter_id == chapter.id,
            ChapterDraftVersionModel.version_no == version_no,
        )
    )
    if draft is None:
        raise RuntimeError(f"draft not found: chapter={chapter_number} version={version_no}")
    return chapter, draft


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    async with session_scope(settings) as session:
        project = await session.scalar(
            select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG)
        )
        if project is None:
            raise SystemExit(f"project not found: {PROJECT_SLUG}")

        results: list[dict[str, Any]] = []
        for chapter_number, version_no in RESTORE_VERSIONS.items():
            chapter, draft = await _load_candidate(
                session,
                project=project,
                chapter_number=chapter_number,
                version_no=version_no,
            )
            result = {
                "chapter": chapter_number,
                "restore_version": version_no,
                "word_count": draft.word_count,
                "execute": args.execute,
            }
            if args.execute:
                outcome = await _evaluate_chapter_quality_gate(
                    session=session,
                    project=project,
                    chapter_number=chapter_number,
                    content=draft.content_md or "",
                )
                result["quality_gate_outcome"] = outcome
                if outcome != "ok":
                    raise RuntimeError(
                        f"candidate failed quality gate: chapter={chapter_number} "
                        f"version={version_no} outcome={outcome}"
                    )
                await session.execute(
                    update(ChapterDraftVersionModel)
                    .where(ChapterDraftVersionModel.chapter_id == chapter.id)
                    .values(is_current=False)
                )
                draft.is_current = True
                chapter.current_word_count = int(draft.word_count or 0)
                chapter.status = ChapterStatus.COMPLETE.value
                chapter.production_state = "ok"
                chapter.metadata_json = {
                    **(chapter.metadata_json or {}),
                    "restored_gate_passing_current_by": (
                        "repair_daozhong_restore_gate_passing_current_v1"
                    ),
                    "restored_gate_passing_version_no": version_no,
                }
            results.append(result)

        if args.execute:
            await session.flush()

        print({"results": results})


if __name__ == "__main__":
    asyncio.run(main())
