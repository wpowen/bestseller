"""Repair outline-level structural repetition for paused 《道种破虚》.

This repairs planning inputs, not prose. It updates ChapterModel/SceneCardModel
from the latest approved chapter_outline_batch and stores a repaired
chapter_outline_batch artifact so future materialization does not reintroduce
the old template goals/purposes.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.domain.enums import ArtifactType, ChapterStatus, SceneStatus  # noqa: E402
from bestseller.domain.planning import PlanningArtifactCreate  # noqa: E402
from bestseller.infra.db.models import (  # noqa: E402
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    RewriteTaskModel,
    SceneDraftVersionModel,
    SceneCardModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.plan_fingerprint import (  # noqa: E402
    build_chapter_fingerprint,
    find_near_duplicate_chapters,
)
from bestseller.services.projects import import_planning_artifact  # noqa: E402


PROJECT_SLUG = "xianxia-upgrade-1776137730"
DEFAULT_START = 51
DEFAULT_END = 550
REPORT_DIR = Path("artifacts/daozhong_repair_audit")
REPAIR_SOURCE = "structural_outline_repair_v1"

GENERIC_HOOK_TEXT = (
    "采用三层悬念叠加：①当前冲突打断式悬念（如险胜后遭遇意外变故），"
    "②下一章反转预兆（变故背后的蛛丝马迹），③长期命运伏笔（关联主线进程的禁忌秘密）。"
    "每10章设置大钩子（机缘出世/强敌登场/身份暴露危机），每30章完成一次主角与核心反派的直接碰撞。"
)
GENERIC_GOAL_PATTERNS = (
    "宁尘需要在生存压力代表的势力角力中找到自己的位置和成长空间",
    "面对一个与外部冲突互为镜像的内心矛盾",
    "推进宁尘需要在生存压力代表",
)
GENERIC_CONFLICT_PATTERNS = (
    "生存压力",
    "完成本章的",
    "被「生存压力」的布局和自身目标夹在中间",
)


def clean(value: Any) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def compact(value: Any) -> str:
    return re.sub(r"\s+", "", clean(value).lower())


def is_generic_goal(value: str | None) -> bool:
    text = clean(value)
    return not text or any(token in text for token in GENERIC_GOAL_PATTERNS)


def is_generic_conflict(value: str | None) -> bool:
    text = clean(value)
    return not text or any(token in text for token in GENERIC_CONFLICT_PATTERNS)


def is_generic_hook(value: str | None) -> bool:
    text = clean(value)
    return not text or GENERIC_HOOK_TEXT in text


def chapter_list(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, dict):
        chapters = content.get("chapters")
    else:
        chapters = content
    return [item for item in chapters or [] if isinstance(item, dict)]


def by_chapter_number(content: Any) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for chapter in chapter_list(content):
        number = chapter.get("chapter_number")
        if isinstance(number, int) and number > 0:
            out[number] = chapter
    return out


def synthesize_goal(number: int, title: str, conflict: str, hook: str) -> str:
    title_part = f"《{title}》" if title else f"第{number}章"
    conflict_part = conflict or "当前主线压力"
    hook_part = hook or "下一章的新压力"
    return (
        f"第{number}章{title_part}：宁尘必须围绕「{conflict_part}」做出具体行动，"
        f"至少改变一项信息、资源或同盟状态，并把「{hook_part}」推成下一章不可回避的压力。"
    )


def synthesize_conflict(title: str, goal: str) -> str:
    title_part = f"《{title}》" if title else "本章"
    goal_part = goal or "推进当前主线目标"
    return f"{title_part}的核心阻力：{goal_part}"


def synthesize_hook(title: str, goal: str, scenes: list[Any]) -> str:
    for scene in reversed(scenes):
        if not isinstance(scene, dict):
            continue
        for key in ("story_task", "main_conflict", "goal", "title"):
            value = clean(scene.get(key))
            if value and not is_generic_hook(value) and not is_generic_goal(value):
                return f"{title}尾声把「{value}」转化为下一章必须处理的新压力。"
    goal_part = goal or title or "本章行动结果"
    return f"{title}尾声让「{goal_part}」产生反噬，迫使宁尘进入下一步行动。"


def synthesize_scene_story(
    *,
    chapter_number: int,
    scene_number: int,
    scene_count: int,
    scene_type: str,
    scene_title: str,
    participants: list[Any],
    chapter_title: str,
    goal: str,
    conflict: str,
    hook: str,
    scene_goal: str = "",
    story_task: str = "",
    scene_conflict: str = "",
    location: str = "",
) -> str:
    actors = "、".join(clean(item) for item in participants if clean(item)) or "宁尘"
    scene_label = scene_title or {
        1: "开场",
        2: "推进",
        3: "转折",
    }.get(scene_number, "变化")
    conflict = conflict or goal
    concrete_task = story_task or scene_goal or scene_conflict or scene_label
    concrete_clause = f"具体事件是「{concrete_task}」。"
    if scene_conflict and scene_conflict != concrete_task:
        concrete_clause += f"本场阻力是「{scene_conflict}」。"
    if location:
        concrete_clause += f"场景落点固定在「{location}」。"
    if scene_number <= 1:
        role = (
            f"开场把《{chapter_title}》的核心压力落到行动层：{actors}必须面对「{conflict}」，"
            f"并明确本章破局目标。{concrete_clause}"
        )
    elif scene_number >= scene_count:
        role = (
            f"尾场必须兑现本章关键变化，并将「{hook or conflict}」具象成下一章的直接钩子，"
            f"不得只停留在口号式危机。{concrete_clause}"
        )
    else:
        role = (
            f"中段通过「{scene_label}」推进「{conflict}」：{actors}要付出代价、获得新信息，"
            f"或迫使对手改变布置。{concrete_clause}"
        )
    return (
        f"第{chapter_number}章第{scene_number}场（{scene_type or scene_label}）：{role}"
        f"本场结束时必须留下独有状态变化，服务本章目标：{goal}"
    )


def artifact_scene_count(chapter_payload: dict[str, Any], db_scene_count: int) -> int:
    scenes = chapter_payload.get("scenes")
    if isinstance(scenes, list) and scenes:
        return len(scenes)
    return max(1, db_scene_count)


def repair_chapter_payload(
    chapter_payload: dict[str, Any],
    *,
    number: int,
    db_title: str,
    db_scene_count: int,
) -> tuple[dict[str, Any], dict[str, int]]:
    counts: Counter[str] = Counter()
    repaired = copy.deepcopy(chapter_payload)

    title = clean(repaired.get("title")) or clean(repaired.get("chapter_title")) or db_title or f"第{number}章"
    hook_type = clean(repaired.get("hook_type")) or "悬念推进"
    goal = clean(repaired.get("chapter_goal")) or clean(repaired.get("goal"))
    if is_generic_goal(goal):
        goal = synthesize_goal(
            number,
            title,
            clean(repaired.get("main_conflict")),
            clean(repaired.get("hook_description")),
        )
        counts["artifact_chapter_goals_repaired"] += 1
    repaired["chapter_goal"] = goal
    conflict = clean(repaired.get("main_conflict"))
    if is_generic_conflict(conflict):
        conflict = synthesize_conflict(title, goal)
        repaired["main_conflict"] = conflict
        counts["artifact_main_conflicts_repaired"] += 1
    hook = clean(repaired.get("hook_description"))
    scenes = repaired.get("scenes")
    if not isinstance(scenes, list):
        scenes = []
        repaired["scenes"] = scenes
    if is_generic_hook(hook):
        hook = synthesize_hook(title, goal, scenes)
        repaired["hook_description"] = hook
        counts["artifact_hook_descriptions_repaired"] += 1
    if title and clean(repaired.get("title")) != title:
        repaired["title"] = title
        counts["artifact_titles_repaired"] += 1
    if hook_type and clean(repaired.get("hook_type")) != hook_type:
        repaired["hook_type"] = hook_type
    if hook and clean(repaired.get("hook_description")) != hook:
        repaired["hook_description"] = hook

    scene_count = artifact_scene_count(repaired, db_scene_count)
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            continue
        scene_number = scene.get("scene_number")
        if not isinstance(scene_number, int) or scene_number <= 0:
            scene_number = index
            scene["scene_number"] = scene_number
        purpose = scene.get("purpose") if isinstance(scene.get("purpose"), dict) else {}
        story = clean(purpose.get("story"))
        if (
            not story
            or "推动本章局势前进" in story
            or "采用三层悬念叠加" in story
            or "承接上章后果并明确本章行动目标" in story
            or is_generic_goal(story)
        ):
            purpose = dict(purpose)
            purpose["story"] = synthesize_scene_story(
                chapter_number=number,
                scene_number=scene_number,
                scene_count=scene_count,
                scene_type=clean(scene.get("scene_type")),
                scene_title=clean(scene.get("title")),
                participants=list(scene.get("participants") or []),
                chapter_title=title,
                goal=goal,
                conflict=conflict,
                hook=hook,
                scene_goal=clean(scene.get("goal")),
                story_task=clean(scene.get("story_task")),
                scene_conflict=clean(scene.get("main_conflict")),
                location=clean(scene.get("location")),
            )
            purpose.setdefault("emotion", "压力、判断和代价同步升级")
            scene["purpose"] = purpose
            counts["artifact_scene_purposes_repaired"] += 1
    return repaired, dict(counts)


def plan_like(chapter: ChapterModel, scenes: list[SceneCardModel]) -> dict[str, Any]:
    return {
        "chapter_number": chapter.chapter_number,
        "main_conflict": chapter.main_conflict,
        "hook_type": chapter.hook_type,
        "hook_description": chapter.hook_description,
        "chapter_goal": chapter.chapter_goal,
        "scenes": scenes,
    }


async def repair(*, start: int, end: int, execute: bool, create_tasks: bool) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    counts: Counter[str] = Counter()
    changed_chapters: list[int] = []
    task_chapters: set[int] = set()
    latest_artifact_version: int | None = None
    repaired_artifact_version: int | None = None
    report_dir = REPORT_DIR

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
        latest_artifact_version = latest_artifact.version_no
        original_content = latest_artifact.content
        repaired_content = copy.deepcopy(original_content)
        repaired_by_number = by_chapter_number(repaired_content)

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
        for scene in scenes:
            scenes_by_chapter[scene.chapter_id].append(scene)
        current_scene_draft_ids = {
            draft.scene_card_id
            for draft in await session.scalars(
                select(SceneDraftVersionModel).where(
                    SceneDraftVersionModel.scene_card_id.in_([scene.id for scene in scenes]),
                    SceneDraftVersionModel.is_current.is_(True),
                )
            )
        }

        # Identify current near-duplicate targets so repaired chapters stay in
        # revision until prose is regenerated against the fixed outline.
        fingerprints = [
            build_chapter_fingerprint(plan_like(chapter, scenes_by_chapter.get(chapter.id, [])))
            for chapter in chapters
        ]
        fp_report = find_near_duplicate_chapters(
            fingerprints,
            warning_threshold=0.68,
            critical_threshold=0.82,
            max_chapter_distance=12,
        )
        duplicate_targets = {item.chapter_b for item in fp_report.findings}

        for chapter in chapters:
            artifact_chapter = repaired_by_number.get(chapter.chapter_number)
            if artifact_chapter is None:
                counts["chapters_missing_from_artifact"] += 1
                continue
            db_scenes = scenes_by_chapter.get(chapter.id, [])
            repaired_chapter, artifact_counts = repair_chapter_payload(
                artifact_chapter,
                number=chapter.chapter_number,
                db_title=clean(chapter.title),
                db_scene_count=len(db_scenes),
            )
            counts.update(artifact_counts)
            repaired_by_number[chapter.chapter_number] = repaired_chapter

            title = clean(repaired_chapter.get("title")) or clean(chapter.title)
            goal = clean(repaired_chapter.get("chapter_goal")) or synthesize_goal(
                chapter.chapter_number,
                title,
                clean(repaired_chapter.get("main_conflict")) or clean(chapter.main_conflict),
                clean(repaired_chapter.get("hook_description")) or clean(chapter.hook_description),
            )
            conflict = clean(repaired_chapter.get("main_conflict")) or clean(chapter.main_conflict)
            hook_type = clean(repaired_chapter.get("hook_type")) or clean(chapter.hook_type) or "悬念推进"
            hook = clean(repaired_chapter.get("hook_description")) or clean(chapter.hook_description)
            if is_generic_conflict(conflict):
                conflict = synthesize_conflict(title, goal)
            if is_generic_hook(hook):
                hook = synthesize_hook(title, goal, list(repaired_chapter.get("scenes") or []))

            chapter_changed = False
            for field, new_value in (
                ("title", title),
                ("chapter_goal", goal),
                ("main_conflict", conflict),
                ("hook_type", hook_type),
                ("hook_description", hook),
            ):
                if new_value and clean(getattr(chapter, field, None)) != new_value:
                    counts[f"db_{field}_repaired"] += 1
                    chapter_changed = True
                    if execute:
                        setattr(chapter, field, new_value)

            artifact_scenes = {
                item.get("scene_number"): item
                for item in repaired_chapter.get("scenes", [])
                if isinstance(item, dict)
            }
            scene_count = max(len(db_scenes), len(artifact_scenes), 1)
            for scene in db_scenes:
                if scene.id not in current_scene_draft_ids or scene.status == SceneStatus.NEEDS_REWRITE.value:
                    task_chapters.add(chapter.chapter_number)
                    counts["missing_or_rewrite_scene_targets"] += 1
                art_scene = artifact_scenes.get(scene.scene_number) or {}
                art_type = clean(art_scene.get("scene_type"))
                art_title = clean(art_scene.get("title"))
                art_purpose = art_scene.get("purpose") if isinstance(art_scene.get("purpose"), dict) else {}
                new_story = clean(art_purpose.get("story")) or synthesize_scene_story(
                    chapter_number=chapter.chapter_number,
                    scene_number=scene.scene_number,
                    scene_count=scene_count,
                    scene_type=art_type or clean(scene.scene_type),
                    scene_title=art_title or clean(scene.title),
                    participants=list(scene.participants or []),
                    chapter_title=title,
                    goal=goal,
                    conflict=conflict,
                    hook=hook,
                    scene_goal=clean(art_scene.get("goal")),
                    story_task=clean(art_scene.get("story_task")),
                    scene_conflict=clean(art_scene.get("main_conflict")),
                    location=clean(art_scene.get("location")),
                )
                if (
                    not clean(new_story)
                    or "推动本章局势前进" in new_story
                    or "采用三层悬念叠加" in new_story
                    or "承接上章后果并明确本章行动目标" in new_story
                    or is_generic_goal(new_story)
                ):
                    new_story = synthesize_scene_story(
                        chapter_number=chapter.chapter_number,
                        scene_number=scene.scene_number,
                        scene_count=scene_count,
                        scene_type=art_type or clean(scene.scene_type),
                        scene_title=art_title or clean(scene.title),
                        participants=list(scene.participants or []),
                        chapter_title=title,
                        goal=goal,
                        conflict=conflict,
                        hook=hook,
                        scene_goal=clean(art_scene.get("goal")),
                        story_task=clean(art_scene.get("story_task")),
                        scene_conflict=clean(art_scene.get("main_conflict")),
                        location=clean(art_scene.get("location")),
                    )
                new_purpose = dict(scene.purpose or {})
                new_purpose["story"] = new_story
                new_purpose.setdefault("emotion", clean(art_purpose.get("emotion")) or "压力、判断和代价同步升级")
                if (scene.purpose or {}) != new_purpose:
                    counts["db_scene_purposes_repaired"] += 1
                    chapter_changed = True
                    if execute:
                        scene.purpose = new_purpose
                if art_type and clean(scene.scene_type) != art_type:
                    counts["db_scene_types_repaired"] += 1
                    chapter_changed = True
                    if execute:
                        scene.scene_type = art_type
                if art_title and clean(scene.title) != art_title:
                    counts["db_scene_titles_repaired"] += 1
                    chapter_changed = True
                    if execute:
                        scene.title = art_title

            if chapter_changed:
                changed_chapters.append(chapter.chapter_number)
                if execute:
                    chapter.metadata_json = {
                        **(chapter.metadata_json or {}),
                        "structural_outline_repaired": True,
                        "structural_outline_repaired_at": now,
                        "structural_outline_source_artifact_version": latest_artifact_version,
                    }

            if chapter.chapter_number in duplicate_targets:
                task_chapters.add(chapter.chapter_number)
                if execute:
                    chapter.status = ChapterStatus.REVISION.value
                    chapter.metadata_json = {
                        **(chapter.metadata_json or {}),
                        "structural_repair_required": True,
                        "structural_repair_codes": sorted(
                            set((chapter.metadata_json or {}).get("structural_repair_codes", []))
                            | {"NEAR_DUPLICATE_CHAPTER_PLAN"}
                        ),
                    }

        if execute:
            artifact = await import_planning_artifact(
                session,
                PROJECT_SLUG,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
                    content=repaired_content,
                    notes=(
                        f"{REPAIR_SOURCE}: repaired outline goals/scene purposes "
                        f"for chapters {start}-{end}"
                    ),
                ),
            )
            repaired_artifact_version = artifact.version_no
            project.metadata_json = {
                **(project.metadata_json or {}),
                "structural_outline_repaired_at": now,
                "structural_outline_source_artifact_version": latest_artifact_version,
                "structural_outline_repaired_artifact_version": repaired_artifact_version,
                "generation_resume_blocked_until_repair_audit": True,
            }

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
            for chapter_number in sorted(task_chapters - existing_target_chapters):
                chapter = chapters_by_number.get(chapter_number)
                if chapter is None:
                    continue
                if execute:
                    session.add(
                        RewriteTaskModel(
                            project_id=project.id,
                            trigger_type="structural_outline_repair",
                            trigger_source_id=chapter.id,
                            rewrite_strategy="chapter_outline_regeneration",
                            priority=1,
                            status="pending",
                            instructions=(
                                f"第{chapter_number}章规划曾与邻近章节近重复。请基于已修复的 "
                                "chapter_goal/main_conflict/hook/scene purposes 重新生成本章场景与章节，"
                                "不得复用旧模板化目标、冲突或尾钩。"
                            ),
                            context_required=["story_bible", "identity_manifest", "chapter_outline"],
                            metadata_json={
                                "chapter_id": str(chapter.id),
                                "chapter_number": chapter_number,
                                "source": REPAIR_SOURCE,
                                "source_artifact_version": latest_artifact_version,
                                "created_at": now,
                            },
                        )
                    )
                created += 1
            counts["rewrite_tasks_created"] = created

        if execute:
            await session.flush()

        remaining_goal_duplicates = Counter(compact(ch.chapter_goal) for ch in chapters if compact(ch.chapter_goal))
        remaining_conflict_duplicates = Counter(compact(ch.main_conflict) for ch in chapters if compact(ch.main_conflict))
        report = {
            "project": {"slug": project.slug, "title": project.title, "status": project.status},
            "scope": {"chapter_from": start, "chapter_to": end},
            "execute": execute,
            "created_at": now,
            "latest_artifact_version": latest_artifact_version,
            "repaired_artifact_version": repaired_artifact_version,
            "counts": dict(counts),
            "changed_chapter_count": len(changed_chapters),
            "changed_chapters_sample": changed_chapters[:120],
            "rewrite_task_chapters": sorted(task_chapters),
            "remaining_duplicate_goal_values": sum(1 for _, count in remaining_goal_duplicates.items() if count >= 3),
            "remaining_duplicate_conflict_values": sum(
                1 for _, count in remaining_conflict_duplicates.items() if count >= 3
            ),
        }

    report_dir.mkdir(parents=True, exist_ok=True)
    suffix = "outline_repair_execute" if execute else "outline_repair_dry_run"
    json_path = report_dir / f"{PROJECT_SLUG}_{start}_{end}_{suffix}.json"
    md_path = report_dir / f"{PROJECT_SLUG}_{start}_{end}_{suffix}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    report["output"] = {"json": str(json_path), "markdown": str(md_path)}
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 《道种破虚》结构规划修复",
        "",
        f"- 执行写入：{report['execute']}",
        f"- 范围：第 {report['scope']['chapter_from']} - {report['scope']['chapter_to']} 章",
        f"- 来源 artifact v{report['latest_artifact_version']}",
        f"- 新 artifact v{report['repaired_artifact_version']}",
        "",
        "## 计数",
        "",
    ]
    for key, value in sorted(report["counts"].items()):
        lines.append(f"- `{key}`: {value}")
    lines.extend(
        [
            "",
            "## 后续必须重写章节",
            "",
            ", ".join(str(n) for n in report["rewrite_task_chapters"]) or "无",
            "",
        ]
    )
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
                "rewrite_task_chapters": report["rewrite_task_chapters"],
                "output": report["output"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
