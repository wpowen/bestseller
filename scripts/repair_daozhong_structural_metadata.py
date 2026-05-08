"""Repair structural metadata for paused 《道种破虚》.

This script intentionally avoids prose rewrites. It fixes metadata that the
future pipeline consumes as context:

* generic SceneCard time labels;
* generic SceneCard story purposes;
* missing or JSON-broken ChapterStateSnapshot rows;
* snapshot time anchors that deterministically regress.

Missing SceneDraft rows and near-duplicate chapter plans are left for the
rewrite phase; this script records them in project metadata instead of faking
content.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from json_repair import repair_json
from sqlalchemy import select

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.models import (  # noqa: E402
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterStateSnapshotModel,
    ProjectModel,
    SceneCardModel,
    SceneDraftVersionModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.continuity import _parse_time_anchor  # noqa: E402


PROJECT_SLUG = "xianxia-upgrade-1776137730"
DEFAULT_START = 51
DEFAULT_END = 550
REPAIR_MODEL = "structural_metadata_repair_v1"

GENERIC_TIME_LABELS = {
    "章节开场",
    "章节中段",
    "章节结尾",
    "章节补充钩子",
}
GENERIC_STORY_PURPOSES = {
    "承接本章主线，补足场景推进、信息释放与结尾钩子。",
    "推动本章剧情发展",
}


def clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def is_generic_time_label(value: str | None) -> bool:
    text = clean(value)
    return not text or text in GENERIC_TIME_LABELS or bool(re.fullmatch(r"章节场景\d+", text))


def is_generic_story_purpose(value: str | None) -> bool:
    text = clean(value)
    return not text or text in GENERIC_STORY_PURPOSES or len(text) < 12


def non_regressing_anchor(*, reason: str) -> str:
    return f"主线连续时段（承接上一章；{reason}）"


def default_span() -> str:
    return "本章内连续推进；具体耗时待精确校准"


def snapshot_payload(*, facts: list[dict[str, Any]], time_anchor: str, span: str, source: str) -> dict[str, Any]:
    return {
        "facts": facts,
        "time_anchor": time_anchor,
        "chapter_time_span": span,
        "repair_source": source,
    }


def repaired_scene_time_label(
    chapter: ChapterModel,
    scene: SceneCardModel,
    *,
    snapshot: ChapterStateSnapshotModel | None,
) -> str:
    anchor = clean(getattr(snapshot, "time_anchor", None)) or "主线连续时段"
    scene_role = {
        1: "开场推进",
        2: "中段转折",
        3: "收束与钩子",
        4: "补充尾钩",
    }.get(scene.scene_number, "场景推进")
    return f"{anchor} / 第{chapter.chapter_number}章第{scene.scene_number}场：{scene_role}"


def repaired_story_purpose(chapter: ChapterModel, scene: SceneCardModel) -> str:
    parts = []
    if clean(chapter.chapter_goal):
        parts.append(f"服务本章目标：{clean(chapter.chapter_goal)}")
    if clean(chapter.main_conflict):
        parts.append(f"推进核心冲突：{clean(chapter.main_conflict)}")
    if clean(scene.hook_requirement):
        parts.append(f"兑现/铺垫钩子：{clean(scene.hook_requirement)}")
    if not parts:
        parts.append("推进本章独有剧情事件，改变角色信息、资源或处境。")
    parts.append(f"本场必须形成第{scene.scene_number}场独有状态变化。")
    return "；".join(parts)


def repaired_facts_from_raw(raw: str | None) -> tuple[list[dict[str, Any]], str | None, str | None]:
    if not raw:
        return [], None, None
    try:
        payload = repair_json(raw, return_objects=True)
    except Exception:
        return [], None, None
    if not isinstance(payload, dict):
        return [], None, None
    facts = payload.get("facts")
    if not isinstance(facts, list):
        facts = []
    normalized_facts = [fact for fact in facts if isinstance(fact, dict)]
    anchor = clean(payload.get("time_anchor")) or None
    span = clean(payload.get("chapter_time_span")) or None
    return normalized_facts, anchor, span


def is_flashback(anchor: str | None) -> bool:
    text = (anchor or "").lower()
    return any(token in text for token in ("flashback", "回忆", "倒叙", "插叙", "梦境"))


async def repair(*, start: int, end: int, execute: bool) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    counts: defaultdict[str, int] = defaultdict(int)
    missing_scene_drafts: list[dict[str, Any]] = []
    time_regressions_repaired: list[dict[str, Any]] = []

    async with session_scope() as session:
        project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))
        if project is None:
            raise SystemExit(f"project not found: {PROJECT_SLUG}")

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
        chapter_by_id = {chapter.id: chapter for chapter in chapters}
        scenes = list(
            await session.scalars(
                select(SceneCardModel)
                .where(SceneCardModel.chapter_id.in_(list(chapter_by_id)))
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
        chapter_drafts = {
            draft.chapter_id: draft
            for draft in await session.scalars(
                select(ChapterDraftVersionModel).where(
                    ChapterDraftVersionModel.chapter_id.in_(list(chapter_by_id)),
                    ChapterDraftVersionModel.is_current.is_(True),
                )
            )
        }
        snapshots = {
            snapshot.chapter_number: snapshot
            for snapshot in await session.scalars(
                select(ChapterStateSnapshotModel)
                .where(
                    ChapterStateSnapshotModel.project_id == project.id,
                    ChapterStateSnapshotModel.chapter_number >= start,
                    ChapterStateSnapshotModel.chapter_number <= end,
                )
                .order_by(ChapterStateSnapshotModel.chapter_number.asc())
            )
        }

        # Create or repair snapshots first so scene labels can reference them.
        previous_snapshot: ChapterStateSnapshotModel | None = None
        for chapter in chapters:
            snapshot = snapshots.get(chapter.chapter_number)
            if snapshot is None:
                anchor = non_regressing_anchor(reason="缺失快照已占位，待精确日序校准")
                span = default_span()
                payload = snapshot_payload(facts=[], time_anchor=anchor, span=span, source="missing_snapshot_repair")
                counts["snapshots_created"] += 1
                if execute:
                    snapshot = ChapterStateSnapshotModel(
                        project_id=project.id,
                        chapter_id=chapter.id,
                        chapter_number=chapter.chapter_number,
                        facts={"facts": []},
                        raw_extraction=json.dumps(payload, ensure_ascii=False),
                        extraction_model=REPAIR_MODEL,
                        extraction_status="ok",
                        time_anchor=anchor,
                        chapter_time_span=span,
                    )
                    session.add(snapshot)
                    snapshots[chapter.chapter_number] = snapshot
            else:
                facts = (snapshot.facts or {}).get("facts")
                if not isinstance(facts, list):
                    facts = []
                source = "existing_snapshot_repair"
                if snapshot.extraction_status != "ok":
                    repaired_facts, raw_anchor, raw_span = repaired_facts_from_raw(snapshot.raw_extraction)
                    facts = repaired_facts
                    if raw_anchor:
                        snapshot.time_anchor = raw_anchor
                    if raw_span:
                        snapshot.chapter_time_span = raw_span
                    counts["failed_snapshots_repaired"] += 1
                    source = "failed_snapshot_json_repair"
                if not clean(snapshot.time_anchor):
                    snapshot.time_anchor = non_regressing_anchor(reason="原快照缺少时间锚")
                    counts["snapshot_time_anchors_filled"] += 1
                if not clean(snapshot.chapter_time_span):
                    snapshot.chapter_time_span = default_span()
                    counts["snapshot_time_spans_filled"] += 1

                current_parsed = _parse_time_anchor(snapshot.time_anchor)
                previous_parsed = _parse_time_anchor(previous_snapshot.time_anchor) if previous_snapshot else None
                if (
                    previous_snapshot is not None
                    and current_parsed is not None
                    and previous_parsed is not None
                    and current_parsed < previous_parsed
                    and not is_flashback(snapshot.time_anchor)
                ):
                    old_anchor = snapshot.time_anchor
                    snapshot.time_anchor = non_regressing_anchor(reason="原抽取 time_anchor 与上一章回退，待精确校准")
                    counts["time_regressions_neutralized"] += 1
                    time_regressions_repaired.append(
                        {
                            "chapter": chapter.chapter_number,
                            "old_anchor": old_anchor,
                            "previous_anchor": previous_snapshot.time_anchor,
                        }
                    )

                payload = snapshot_payload(
                    facts=facts,
                    time_anchor=snapshot.time_anchor or "",
                    span=snapshot.chapter_time_span or "",
                    source=source,
                )
                if execute:
                    snapshot.facts = {"facts": facts}
                    snapshot.raw_extraction = json.dumps(payload, ensure_ascii=False)
                    snapshot.extraction_model = REPAIR_MODEL
                    snapshot.extraction_status = "ok"

            previous_snapshot = snapshots.get(chapter.chapter_number)

        await session.flush() if execute else None

        for scene in scenes:
            chapter = chapter_by_id[scene.chapter_id]
            snapshot = snapshots.get(chapter.chapter_number)
            if scene.id not in current_scene_draft_ids:
                missing_scene_drafts.append(
                    {
                        "chapter": chapter.chapter_number,
                        "scene": scene.scene_number,
                        "title": scene.title,
                    }
                )

            changed = False
            if is_generic_time_label(scene.time_label):
                counts["scene_time_labels_repaired"] += 1
                changed = True
                if execute:
                    scene.time_label = repaired_scene_time_label(chapter, scene, snapshot=snapshot)
            purpose = dict(scene.purpose or {})
            story = clean(purpose.get("story"))
            if is_generic_story_purpose(story):
                counts["scene_story_purposes_repaired"] += 1
                changed = True
                if execute:
                    purpose["story"] = repaired_story_purpose(chapter, scene)
                    scene.purpose = purpose
            if changed and execute:
                scene.metadata_json = {
                    **(scene.metadata_json or {}),
                    "structural_metadata_repaired_at": now,
                    "structural_metadata_repair_source": REPAIR_MODEL,
                }

        counts["missing_scene_drafts_remaining"] = len(missing_scene_drafts)

        if execute:
            project.metadata_json = {
                **(project.metadata_json or {}),
                "structural_metadata_repaired_at": now,
                "structural_metadata_repair_scope": {"chapter_from": start, "chapter_to": end},
                "structural_metadata_repair_counts": dict(counts),
                "structural_repair_missing_scene_drafts": missing_scene_drafts,
                "structural_repair_time_regressions_neutralized": time_regressions_repaired,
                "generation_resume_blocked_until_repair_audit": True,
            }
            await session.commit()

    return {
        "execute": execute,
        "project_slug": PROJECT_SLUG,
        "scope": {"chapter_from": start, "chapter_to": end},
        "counts": dict(counts),
        "missing_scene_drafts": missing_scene_drafts,
        "time_regressions_neutralized_sample": time_regressions_repaired[:20],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=DEFAULT_START)
    parser.add_argument("--end", type=int, default=DEFAULT_END)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = asyncio.run(repair(start=args.start, end=args.end, execute=args.execute))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
