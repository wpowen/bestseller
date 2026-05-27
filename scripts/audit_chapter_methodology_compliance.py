"""Audit existing chapters against methodology-aware LLM judges."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.models import (  # noqa: E402
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    SceneCardModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.chapter_llm_quality_judge import (  # noqa: E402
    judge_chapter_commercial_quality,
)
from bestseller.services.outline_llm_judge import (  # noqa: E402
    judge_outline_commercial_readiness,
)
from bestseller.services.outline_reader_experience_judge import (  # noqa: E402
    judge_outline_reader_experience,
)
from bestseller.services.prompt_packs import resolve_prompt_pack  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-slug", required=True)
    parser.add_argument("--chapters", required=True, help="e.g. '1-10' or '1,3,5'")
    parser.add_argument("--output", required=True, help="JSON output path")
    parser.add_argument(
        "--judges",
        default="chapter,outline,reader_experience",
        help="Comma-separated judges to run",
    )
    args = parser.parse_args()

    settings = load_settings()
    chapter_numbers = _parse_range(args.chapters)
    requested_judges = {item.strip() for item in args.judges.split(",") if item.strip()}
    findings: dict[str, Any] = {"by_chapter": {}, "summary": {}}

    async with session_scope(settings) as session:
        project = await _load_project(session, args.project_slug)
        pack = _resolve_pack(project)
        chapters = await _load_chapters(session, project, chapter_numbers)
        outline_payload = [_chapter_to_payload(ch, await _load_scenes(session, ch)) for ch in chapters]
        project_brief = _project_brief(project)

        outline_result_payload: dict[str, Any] | None = None
        if "outline" in requested_judges and outline_payload:
            outline_result = await judge_outline_commercial_readiness(
                session,
                settings,
                outline_payload={"chapters": outline_payload},
                project_brief=project_brief,
                pack=pack,
            )
            outline_result_payload = outline_result.model_dump(mode="json", by_alias=True)

        reader_result_payload: dict[str, Any] | None = None
        if "reader_experience" in requested_judges and outline_payload:
            reader_result = await judge_outline_reader_experience(
                session,
                settings,
                chapters_payload=outline_payload,
                project_brief=project_brief,
                pack=pack,
            )
            reader_result_payload = reader_result.model_dump(mode="json", by_alias=True)

        for chapter in chapters:
            ch_findings: dict[str, Any] = {}
            if "chapter" in requested_judges:
                draft = await _load_current_draft(session, chapter)
                result = await judge_chapter_commercial_quality(
                    session,
                    settings,
                    chapter_number=chapter.chapter_number,
                    content_md=draft.content_md,
                    generation_input=_chapter_to_payload(
                        chapter,
                        await _load_scenes(session, chapter),
                    ),
                    pack=pack,
                )
                ch_findings["chapter_judge"] = result.model_dump(mode="json", by_alias=True)
            if outline_result_payload is not None:
                ch_findings["outline_judge"] = outline_result_payload
            if reader_result_payload is not None and chapter.chapter_number <= 10:
                ch_findings["reader_experience_judge"] = reader_result_payload
            findings["by_chapter"][str(chapter.chapter_number)] = ch_findings

    findings["summary"] = _summarize(findings["by_chapter"])
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Audit complete. Report saved to {output_path}")


def _parse_range(spec: str) -> list[int]:
    out: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start, end = (int(part.strip()) for part in token.split("-", maxsplit=1))
            out.update(range(start, end + 1))
        else:
            out.add(int(token))
    return sorted(out)


async def _load_project(session: AsyncSession, slug: str) -> ProjectModel:
    project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == slug))
    if project is None:
        raise SystemExit(f"Project not found: {slug}")
    return project


async def _load_chapters(
    session: AsyncSession,
    project: ProjectModel,
    chapter_numbers: list[int],
) -> list[ChapterModel]:
    result = await session.scalars(
        select(ChapterModel)
        .where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number.in_(chapter_numbers),
        )
        .order_by(ChapterModel.chapter_number)
    )
    chapters = list(result)
    found = {chapter.chapter_number for chapter in chapters}
    missing = [number for number in chapter_numbers if number not in found]
    if missing:
        raise SystemExit(f"Chapter(s) not found: {missing}")
    return chapters


async def _load_current_draft(
    session: AsyncSession,
    chapter: ChapterModel,
) -> ChapterDraftVersionModel:
    draft = await session.scalar(
        select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.chapter_id == chapter.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
    )
    if draft is None:
        raise SystemExit(f"Current draft not found for chapter {chapter.chapter_number}")
    return draft


async def _load_scenes(session: AsyncSession, chapter: ChapterModel) -> list[SceneCardModel]:
    result = await session.scalars(
        select(SceneCardModel)
        .where(SceneCardModel.chapter_id == chapter.id)
        .order_by(SceneCardModel.scene_number)
    )
    return list(result)


def _chapter_to_payload(chapter: ChapterModel, scenes: list[SceneCardModel]) -> dict[str, Any]:
    return {
        "chapter_number": chapter.chapter_number,
        "title": chapter.title,
        "chapter_goal": chapter.chapter_goal,
        "opening_situation": chapter.opening_situation,
        "main_conflict": chapter.main_conflict,
        "hook_type": chapter.hook_type,
        "hook_description": chapter.hook_description,
        "information_revealed": chapter.information_revealed or [],
        "information_withheld": chapter.information_withheld or [],
        "metadata": chapter.metadata_json or {},
        "scenes": [
            {
                "scene_number": scene.scene_number,
                "scene_type": scene.scene_type,
                "title": scene.title,
                "time_label": scene.time_label,
                "participants": scene.participants or [],
                "purpose": scene.purpose or {},
                "entry_state": scene.entry_state or {},
                "exit_state": scene.exit_state or {},
                "hook_requirement": scene.hook_requirement,
                "metadata": scene.metadata_json or {},
            }
            for scene in scenes
        ],
    }


def _resolve_pack(project: ProjectModel):
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    return resolve_prompt_pack(
        metadata.get("prompt_pack_name") or metadata.get("prompt_pack_key"),
        genre=project.genre or "general-fiction",
        sub_genre=project.sub_genre,
    )


def _project_brief(project: ProjectModel) -> dict[str, Any]:
    return {
        "slug": project.slug,
        "title": project.title,
        "genre": project.genre,
        "sub_genre": project.sub_genre,
        "target_chapters": project.target_chapters,
        "reader_contract": project.reader_contract_json or {},
        "hype_scheme": project.hype_scheme_json or {},
        "metadata": project.metadata_json or {},
    }


def _summarize(by_chapter: dict[str, Any]) -> dict[str, Any]:
    pass_count = 0
    block_count = 0
    issue_freq: dict[str, int] = {}
    for ch_data in by_chapter.values():
        for judge_result in ch_data.values():
            if judge_result.get("pass") or judge_result.get("passed"):
                pass_count += 1
            else:
                block_count += 1
            for issue in judge_result.get("blocking_issues", []):
                code = issue.get("code", "unknown")
                issue_freq[code] = issue_freq.get(code, 0) + 1
    total = pass_count + block_count
    return {
        "total_judge_runs": total,
        "pass_count": pass_count,
        "block_count": block_count,
        "pass_rate": pass_count / max(total, 1),
        "top_issues": sorted(issue_freq.items(), key=lambda item: -item[1])[:10],
    }


if __name__ == "__main__":
    asyncio.run(main())
