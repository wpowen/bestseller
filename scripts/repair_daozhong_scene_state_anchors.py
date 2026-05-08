"""Backfill scene entry/exit state anchors for 《道种破虚》.

The runtime scene-richness gate correctly blocks drafting when entry_state or
exit_state is empty. Historical scene cards predate that stricter contract, so
this script backfills deterministic state anchors from existing chapter/scene
planning data. It does not edit prose.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.domain.enums import ArtifactType  # noqa: E402
from bestseller.domain.planning import PlanningArtifactCreate  # noqa: E402
from bestseller.infra.db.models import (  # noqa: E402
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    SceneCardModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.projects import import_planning_artifact  # noqa: E402


PROJECT_SLUG = "xianxia-upgrade-1776137730"
DEFAULT_START = 1
DEFAULT_END = 551
REPORT_DIR = Path("artifacts/daozhong_repair_audit")
REPAIR_SOURCE = "scene_state_anchor_repair_v1"


def clean(value: Any) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def story_purpose(scene: Any) -> str:
    purpose = scene.get("purpose") if isinstance(scene, dict) else getattr(scene, "purpose", None)
    if not isinstance(purpose, dict):
        return ""
    return clean(purpose.get("story"))


def participants_text(value: Any) -> str:
    items = value or []
    if not isinstance(items, list):
        return "宁尘"
    return "、".join(clean(item) for item in items if clean(item)) or "宁尘"


def state_is_empty(value: Any) -> bool:
    return not isinstance(value, dict) or not value


def chapter_items(content: Any) -> list[dict[str, Any]]:
    chapters = content.get("chapters") if isinstance(content, dict) else content
    return [item for item in chapters or [] if isinstance(item, dict)]


def artifact_chapters_by_number(content: Any) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for chapter in chapter_items(content):
        number = chapter.get("chapter_number")
        if isinstance(number, int):
            out[number] = chapter
    return out


def build_entry_state(
    *,
    chapter_number: int,
    scene_number: int,
    actors: str,
    title: str,
    goal: str,
    conflict: str,
) -> dict[str, str]:
    pressure = conflict or goal or title or f"第{chapter_number}章目标"
    return {
        "scene_position": f"第{chapter_number}章第{scene_number}场开始",
        "active_pressure": f"{actors}正面对「{pressure}」带来的即时压力。",
        "starting_state": f"本场开局必须承接《{title}》的目标，角色尚未拿到本场关键变化。",
    }


def build_exit_state(
    *,
    chapter_number: int,
    scene_number: int,
    actors: str,
    title: str,
    goal: str,
    hook: str,
    story: str,
) -> dict[str, str]:
    result = story[:96] if story else goal or hook or title
    next_pressure = hook or goal or f"第{chapter_number}章后续压力"
    return {
        "scene_position": f"第{chapter_number}章第{scene_number}场结束",
        "state_delta": f"{actors}通过本场获得或失去一项明确筹码：{result}",
        "next_pressure": f"本场结尾必须把局面推向「{next_pressure}」。",
    }


def patch_artifact_scene(
    scene: dict[str, Any],
    *,
    chapter_number: int,
    title: str,
    goal: str,
    conflict: str,
    hook: str,
) -> bool:
    changed = False
    scene_number = scene.get("scene_number")
    if not isinstance(scene_number, int) or scene_number <= 0:
        return False
    actors = participants_text(scene.get("participants"))
    story = story_purpose(scene)
    if state_is_empty(scene.get("entry_state")):
        scene["entry_state"] = build_entry_state(
            chapter_number=chapter_number,
            scene_number=scene_number,
            actors=actors,
            title=title,
            goal=goal,
            conflict=conflict,
        )
        changed = True
    if state_is_empty(scene.get("exit_state")):
        scene["exit_state"] = build_exit_state(
            chapter_number=chapter_number,
            scene_number=scene_number,
            actors=actors,
            title=title,
            goal=goal,
            hook=hook,
            story=story,
        )
        changed = True
    return changed


async def repair(*, start: int, end: int, execute: bool) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    counts: Counter[str] = Counter()
    changed_chapters: set[int] = set()
    repaired_artifact_version: int | None = None

    async with session_scope() as session:
        project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))
        if project is None:
            raise SystemExit(f"project not found: {PROJECT_SLUG}")

        latest_artifact = await session.scalar(
            select(PlanningArtifactVersionModel)
            .where(
                PlanningArtifactVersionModel.project_id == project.id,
                PlanningArtifactVersionModel.artifact_type == ArtifactType.CHAPTER_OUTLINE_BATCH.value,
            )
            .order_by(PlanningArtifactVersionModel.version_no.desc())
            .limit(1)
        )
        if latest_artifact is None:
            raise SystemExit("latest chapter_outline_batch artifact not found")

        chapters = list(
            await session.scalars(
                select(ChapterModel)
                .where(
                    ChapterModel.project_id == project.id,
                    ChapterModel.chapter_number >= start,
                    ChapterModel.chapter_number <= end,
                )
                .order_by(ChapterModel.chapter_number.asc())
            )
        )
        scenes = list(
            await session.scalars(
                select(SceneCardModel)
                .where(SceneCardModel.chapter_id.in_([chapter.id for chapter in chapters]))
                .order_by(SceneCardModel.chapter_id.asc(), SceneCardModel.scene_number.asc())
            )
        )
        scenes_by_chapter: dict[Any, list[SceneCardModel]] = defaultdict(list)
        for scene_card in scenes:
            scenes_by_chapter[scene_card.chapter_id].append(scene_card)

        repaired_content = copy.deepcopy(latest_artifact.content)
        artifact_by_number = artifact_chapters_by_number(repaired_content)

        for chapter in chapters:
            title = clean(chapter.title) or f"第{chapter.chapter_number}章"
            goal = clean(chapter.chapter_goal)
            conflict = clean(chapter.main_conflict)
            hook = clean(chapter.hook_description)
            for scene_card in scenes_by_chapter.get(chapter.id, []):
                actors = participants_text(scene_card.participants)
                story = story_purpose(scene_card)
                scene_changed = False
                if state_is_empty(scene_card.entry_state):
                    counts["db_entry_state_repaired"] += 1
                    scene_changed = True
                    if execute:
                        scene_card.entry_state = build_entry_state(
                            chapter_number=chapter.chapter_number,
                            scene_number=scene_card.scene_number,
                            actors=actors,
                            title=title,
                            goal=goal,
                            conflict=conflict,
                        )
                if state_is_empty(scene_card.exit_state):
                    counts["db_exit_state_repaired"] += 1
                    scene_changed = True
                    if execute:
                        scene_card.exit_state = build_exit_state(
                            chapter_number=chapter.chapter_number,
                            scene_number=scene_card.scene_number,
                            actors=actors,
                            title=title,
                            goal=goal,
                            hook=hook,
                            story=story,
                        )
                if scene_changed:
                    changed_chapters.add(chapter.chapter_number)
                    if execute:
                        scene_card.metadata_json = {
                            **(scene_card.metadata_json or {}),
                            "scene_state_anchor_repaired": True,
                            "scene_state_anchor_repaired_at": now,
                            "scene_state_anchor_repair_source": REPAIR_SOURCE,
                        }

            artifact_chapter = artifact_by_number.get(chapter.chapter_number)
            if isinstance(artifact_chapter, dict):
                artifact_scenes = artifact_chapter.get("scenes")
                if isinstance(artifact_scenes, list):
                    for scene_payload in artifact_scenes:
                        if not isinstance(scene_payload, dict):
                            continue
                        if patch_artifact_scene(
                            scene_payload,
                            chapter_number=chapter.chapter_number,
                            title=title,
                            goal=goal,
                            conflict=conflict,
                            hook=hook,
                        ):
                            counts["artifact_scene_state_repaired"] += 1

        if execute and changed_chapters:
            artifact = await import_planning_artifact(
                session,
                PROJECT_SLUG,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
                    content=repaired_content,
                    notes=(
                        f"{REPAIR_SOURCE}: repaired empty scene entry/exit states "
                        f"for chapters {start}-{end}"
                    ),
                ),
            )
            repaired_artifact_version = artifact.version_no
            project.status = "paused"
            project.metadata_json = {
                **(project.metadata_json or {}),
                "production_paused": True,
                "production_pause_reason": "structural_repair_before_continuation",
                "generation_resume_blocked_until_repair_audit": True,
                "scene_state_anchor_repaired_at": now,
                "scene_state_anchor_repaired_artifact_version": repaired_artifact_version,
            }
            await session.flush()

        report = {
            "project": {"slug": project.slug, "title": project.title, "status": project.status},
            "scope": {"chapter_from": start, "chapter_to": end},
            "execute": execute,
            "created_at": now,
            "latest_artifact_version": latest_artifact.version_no,
            "repaired_artifact_version": repaired_artifact_version,
            "changed_chapter_count": len(changed_chapters),
            "changed_chapters_sample": sorted(changed_chapters)[:160],
            "counts": dict(counts),
        }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "scene_state_repair_execute" if execute else "scene_state_repair_dry_run"
    json_path = REPORT_DIR / f"{PROJECT_SLUG}_{start}_{end}_{suffix}.json"
    md_path = REPORT_DIR / f"{PROJECT_SLUG}_{start}_{end}_{suffix}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    report["output"] = {"json": str(json_path), "markdown": str(md_path)}
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 《道种破虚》场景状态锚点修复",
        "",
        f"- 执行写入：{report['execute']}",
        f"- 范围：第 {report['scope']['chapter_from']} - {report['scope']['chapter_to']} 章",
        f"- 来源 artifact v{report['latest_artifact_version']}",
        f"- 新 artifact v{report['repaired_artifact_version']}",
        f"- 变更章节数：{report['changed_chapter_count']}",
        "",
        "## 计数",
        "",
    ]
    for key, value in sorted(report["counts"].items()):
        lines.append(f"- `{key}`: {value}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=DEFAULT_START)
    parser.add_argument("--end", type=int, default=DEFAULT_END)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(repair(start=args.start, end=args.end, execute=args.execute))
    print(
        json.dumps(
            {
                "execute": report["execute"],
                "counts": report["counts"],
                "changed_chapter_count": report["changed_chapter_count"],
                "output": report["output"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
