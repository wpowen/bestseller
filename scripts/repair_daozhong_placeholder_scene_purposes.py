"""Repair placeholder scene purposes for paused 《道种破虚》.

This is a deterministic planning-data repair. It removes scene-card placeholders
such as ``具体事件是「开场」`` and chapter hooks like ``尾声把「尾钩」...`` so
the strengthened pre-draft gates can run repair generation without admitting
template text.
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

from bestseller.domain.enums import ArtifactType, ChapterStatus  # noqa: E402
from bestseller.domain.planning import PlanningArtifactCreate  # noqa: E402
from bestseller.infra.db.models import (  # noqa: E402
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    RewriteTaskModel,
    SceneCardModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.projects import import_planning_artifact  # noqa: E402


PROJECT_SLUG = "xianxia-upgrade-1776137730"
DEFAULT_START = 51
DEFAULT_END = 550
REPORT_DIR = Path("artifacts/daozhong_repair_audit")
REPAIR_SOURCE = "placeholder_scene_purpose_repair_v1"

PLACEHOLDER_MARKERS = (
    "具体事件是「开场」",
    "具体事件是「推进」",
    "具体事件是「尾钩」",
    "用更深一层的代价、真相或变化把局势再往前推",
)


def clean(value: Any) -> str:
    return str(value).strip() if isinstance(value, str) else ""


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


def is_placeholder_story(value: str) -> bool:
    return any(marker in value for marker in PLACEHOLDER_MARKERS)


def is_placeholder_hook(value: str) -> bool:
    return "尾声把「尾钩」转化为下一章必须处理的新压力" in value


def concrete_hook(title: str, goal: str, conflict: str) -> str:
    pressure = conflict or goal or f"《{title}》的本章代价"
    return f"《{title}》收束时，本章行动结果反向暴露「{pressure}」留下的下一步代价。"


def concrete_story(
    *,
    chapter_number: int,
    scene_number: int,
    scene_count: int,
    scene_type: str,
    title: str,
    goal: str,
    conflict: str,
    hook: str,
    participants: list[Any],
    original: str,
) -> str:
    actors = "、".join(clean(item) for item in participants if clean(item)) or "宁尘"
    scene_label = title or scene_type or f"第{scene_number}场"
    if "具体事件是「开场」" in original:
        task = f"{scene_label}落地本章冲突：{conflict or goal}"
    elif "具体事件是「推进」" in original:
        task = f"{scene_label}推进本章阻力：{actors}必须付出代价、获得新信息或改变行动路线"
    elif "具体事件是「尾钩」" in original:
        task = hook or f"{scene_label}把本章结果转成下一章直接压力"
    else:
        task = f"{scene_label}围绕「{conflict or goal}」推进一项代价、真相或关系变化"
    if scene_number >= scene_count:
        role = f"尾场兑现本章关键变化，并把「{task}」落成下一章直接压力。"
    elif scene_number <= 1:
        role = f"开场让{actors}面对「{conflict or goal}」，并把行动目标落到具体选择。"
    else:
        role = f"中段通过「{task}」推动局势，使{actors}获得新信息或承担明确代价。"
    return (
        f"第{chapter_number}章第{scene_number}场（{scene_type or scene_label}）："
        f"{role}本场结束时必须留下独有状态变化，服务本章目标：{goal}"
    )


def patch_artifact_scene(
    scene: dict[str, Any],
    *,
    chapter_number: int,
    scene_count: int,
    title: str,
    goal: str,
    conflict: str,
    hook: str,
) -> bool:
    purpose = scene.get("purpose") if isinstance(scene.get("purpose"), dict) else {}
    story = clean(purpose.get("story"))
    if not is_placeholder_story(story):
        return False
    scene_number = scene.get("scene_number")
    if not isinstance(scene_number, int) or scene_number <= 0:
        return False
    purpose = dict(purpose)
    purpose["story"] = concrete_story(
        chapter_number=chapter_number,
        scene_number=scene_number,
        scene_count=scene_count,
        scene_type=clean(scene.get("scene_type")),
        title=clean(scene.get("title")),
        goal=goal,
        conflict=conflict,
        hook=hook,
        participants=list(scene.get("participants") or []),
        original=story,
    )
    purpose.setdefault("emotion", "压力、判断和代价同步升级")
    scene["purpose"] = purpose
    return True


async def repair(*, start: int, end: int, execute: bool, create_tasks: bool) -> dict[str, Any]:
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
        chapters_by_number = {chapter.chapter_number: chapter for chapter in chapters}
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
            if is_placeholder_hook(hook):
                hook = concrete_hook(title, goal, conflict)
                counts["db_hook_description_repaired"] += 1
                changed_chapters.add(chapter.chapter_number)
                if execute:
                    chapter.hook_description = hook

            db_scenes = scenes_by_chapter.get(chapter.id, [])
            scene_count = max(len(db_scenes), 1)
            for scene_card in db_scenes:
                purpose = scene_card.purpose if isinstance(scene_card.purpose, dict) else {}
                story = clean(purpose.get("story"))
                if not is_placeholder_story(story):
                    continue
                new_purpose = dict(purpose)
                new_purpose["story"] = concrete_story(
                    chapter_number=chapter.chapter_number,
                    scene_number=scene_card.scene_number,
                    scene_count=scene_count,
                    scene_type=clean(scene_card.scene_type),
                    title=clean(scene_card.title),
                    goal=goal,
                    conflict=conflict,
                    hook=hook,
                    participants=list(scene_card.participants or []),
                    original=story,
                )
                new_purpose.setdefault("emotion", "压力、判断和代价同步升级")
                counts["db_scene_purpose_repaired"] += 1
                changed_chapters.add(chapter.chapter_number)
                if execute:
                    scene_card.purpose = new_purpose
                    scene_card.metadata_json = {
                        **(scene_card.metadata_json or {}),
                        "placeholder_scene_purpose_repaired": True,
                        "placeholder_scene_purpose_repaired_at": now,
                    }

            artifact_chapter = artifact_by_number.get(chapter.chapter_number)
            if isinstance(artifact_chapter, dict):
                if is_placeholder_hook(clean(artifact_chapter.get("hook_description"))):
                    artifact_chapter["hook_description"] = hook
                    counts["artifact_hook_description_repaired"] += 1
                artifact_scenes = artifact_chapter.get("scenes")
                if isinstance(artifact_scenes, list):
                    for scene_payload in artifact_scenes:
                        if not isinstance(scene_payload, dict):
                            continue
                        if patch_artifact_scene(
                            scene_payload,
                            chapter_number=chapter.chapter_number,
                            scene_count=max(len(artifact_scenes), scene_count),
                            title=title,
                            goal=goal,
                            conflict=conflict,
                            hook=hook,
                        ):
                            counts["artifact_scene_purpose_repaired"] += 1

        if execute and changed_chapters:
            artifact = await import_planning_artifact(
                session,
                PROJECT_SLUG,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
                    content=repaired_content,
                    notes=(
                        f"{REPAIR_SOURCE}: repaired placeholder scene purposes "
                        f"for chapters {start}-{end}"
                    ),
                ),
            )
            repaired_artifact_version = artifact.version_no

        if create_tasks:
            existing_tasks = list(
                await session.scalars(
                    select(RewriteTaskModel).where(
                        RewriteTaskModel.project_id == project.id,
                        RewriteTaskModel.status.in_(["pending", "queued"]),
                    )
                )
            )
            existing_target_chapters = {
                int((task.metadata_json or {}).get("chapter_number"))
                for task in existing_tasks
                if isinstance((task.metadata_json or {}).get("chapter_number"), int)
            }
            created = 0
            for chapter_number in sorted(changed_chapters - existing_target_chapters):
                chapter = chapters_by_number.get(chapter_number)
                if chapter is None:
                    continue
                if execute:
                    session.add(
                        RewriteTaskModel(
                            project_id=project.id,
                            trigger_type="placeholder_scene_purpose_repair",
                            trigger_source_id=chapter.id,
                            rewrite_strategy="chapter_outline_regeneration",
                            priority=1,
                            status="pending",
                            instructions=(
                                f"第{chapter_number}章存在旧占位式场景任务，已修复场景卡。请重新生成本章，"
                                "不得复用「开场/推进/尾钩」作为具体事件。"
                            ),
                            context_required=["story_bible", "identity_manifest", "chapter_outline"],
                            metadata_json={
                                "chapter_id": str(chapter.id),
                                "chapter_number": chapter_number,
                                "source": REPAIR_SOURCE,
                                "source_artifact_version": latest_artifact.version_no,
                                "created_at": now,
                            },
                        )
                    )
                created += 1
            counts["rewrite_tasks_created"] = created

        if execute:
            for chapter_number in sorted(changed_chapters):
                chapter = chapters_by_number.get(chapter_number)
                if chapter is None:
                    continue
                chapter.status = ChapterStatus.REVISION.value
                chapter.metadata_json = {
                    **(chapter.metadata_json or {}),
                    "structural_repair_required": True,
                    "placeholder_scene_purpose_repaired": True,
                    "placeholder_scene_purpose_repaired_at": now,
                    "structural_repair_codes": sorted(
                        set((chapter.metadata_json or {}).get("structural_repair_codes", []))
                        | {"PLACEHOLDER_SCENE_PURPOSE"}
                    ),
                }
            project.status = "paused"
            project.metadata_json = {
                **(project.metadata_json or {}),
                "production_paused": True,
                "production_pause_reason": "structural_repair_before_continuation",
                "generation_resume_blocked_until_repair_audit": True,
                "placeholder_scene_purpose_repaired_at": now,
                "placeholder_scene_purpose_repaired_artifact_version": repaired_artifact_version,
            }
            await session.flush()

        report = {
            "project": {"slug": project.slug, "title": project.title, "status": project.status},
            "scope": {"chapter_from": start, "chapter_to": end},
            "execute": execute,
            "created_at": now,
            "latest_artifact_version": latest_artifact.version_no,
            "repaired_artifact_version": repaired_artifact_version,
            "changed_chapters": sorted(changed_chapters),
            "changed_chapter_count": len(changed_chapters),
            "counts": dict(counts),
        }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "placeholder_scene_repair_execute" if execute else "placeholder_scene_repair_dry_run"
    json_path = REPORT_DIR / f"{PROJECT_SLUG}_{start}_{end}_{suffix}.json"
    md_path = REPORT_DIR / f"{PROJECT_SLUG}_{start}_{end}_{suffix}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    report["output"] = {"json": str(json_path), "markdown": str(md_path)}
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 《道种破虚》占位场景任务修复",
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
    lines.extend(["", "## 变更章节", "", ", ".join(str(n) for n in report["changed_chapters"]) or "无"])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=DEFAULT_START)
    parser.add_argument("--end", type=int, default=DEFAULT_END)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--create-tasks", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(
        repair(
            start=args.start,
            end=args.end,
            execute=args.execute,
            create_tasks=args.create_tasks,
        )
    )
    print(
        json.dumps(
            {
                "execute": report["execute"],
                "counts": report["counts"],
                "changed_chapter_count": report["changed_chapter_count"],
                "changed_chapters": report["changed_chapters"],
                "output": report["output"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
