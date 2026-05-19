from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ChapterModel, ProjectModel, RewriteTaskModel


AUTONOMOUS_REPAIR_TRIGGER = "autonomous_quality_retrofit"
AUTONOMOUS_REPAIR_STRATEGY = "quality_retrofit_chapter_rewrite"

_PRIORITY_WEIGHT: dict[str, int] = {
    "critical": 1,
    "high": 2,
    "medium": 4,
    "ok": 5,
}


@dataclass(frozen=True, slots=True)
class QualityRepairTaskSpec:
    slug: str
    chapter_number: int
    priority: str
    task_priority: int
    cause_ids: tuple[str, ...]
    patch_points: tuple[Mapping[str, object], ...] = field(default_factory=tuple)
    audit_row: Mapping[str, object] = field(default_factory=dict)

    @property
    def repair_id(self) -> str:
        raw = json.dumps(
            {
                "slug": self.slug,
                "chapter_number": self.chapter_number,
                "priority": self.priority,
                "cause_ids": list(self.cause_ids),
                "patch_points": list(self.patch_points),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        return f"quality-retrofit:{self.slug}:ch{self.chapter_number:03d}:{digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "repair_id": self.repair_id,
            "slug": self.slug,
            "chapter_number": self.chapter_number,
            "priority": self.priority,
            "task_priority": self.task_priority,
            "cause_ids": list(self.cause_ids),
            "patch_points": [dict(point) for point in self.patch_points],
            "audit_row": dict(self.audit_row),
            "instructions": build_quality_repair_instructions(self),
        }


@dataclass(frozen=True, slots=True)
class QualityRepairPlan:
    slug: str
    specs: tuple[QualityRepairTaskSpec, ...]
    priority_counts: Mapping[str, int]
    cause_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "task_count": len(self.specs),
            "priority_counts": dict(self.priority_counts),
            "cause_counts": dict(self.cause_counts),
            "tasks": [spec.to_dict() for spec in self.specs],
        }


@dataclass(frozen=True, slots=True)
class TaskSyncResult:
    created: int
    skipped_existing: int
    superseded: int
    missing_chapters: tuple[int, ...]
    task_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "created": self.created,
            "skipped_existing": self.skipped_existing,
            "superseded": self.superseded,
            "missing_chapters": list(self.missing_chapters),
            "task_ids": list(self.task_ids),
        }


def discover_output_book_slugs(output_dir: Path) -> list[str]:
    if not output_dir.exists():
        return []
    slugs: list[str] = []
    for path in sorted(output_dir.iterdir(), key=lambda item: item.name):
        if path.is_dir() and any(path.glob("chapter-*.md")):
            slugs.append(path.name)
    return slugs


def load_quality_retrofit_rows(csv_path: Path) -> list[dict[str, str]]:
    import csv

    if not csv_path.is_file():
        return []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def load_patch_plan(plan_path: Path) -> list[dict[str, Any]]:
    if not plan_path.is_file():
        return []
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, Mapping)]
    return []


def latest_quality_retrofit_csv(slug: str, *, output_dir: Path) -> Path | None:
    audit_dir = output_dir / slug / "audits" / "quality-retrofit"
    files = sorted(audit_dir.glob("window-*.csv"))
    return files[-1] if files else None


def patch_plan_by_chapter(patch_plan: Sequence[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    rows: dict[int, Mapping[str, Any]] = {}
    for item in patch_plan:
        try:
            chapter_number = int(item.get("chapter_number") or 0)
        except (TypeError, ValueError):
            continue
        if chapter_number > 0:
            rows[chapter_number] = item
    return rows


def build_quality_repair_plan(
    *,
    slug: str,
    audit_rows: Sequence[Mapping[str, Any]],
    patch_plan: Sequence[Mapping[str, Any]] = (),
    priorities: set[str] | None = None,
    limit: int | None = None,
) -> QualityRepairPlan:
    wanted = priorities or {"critical", "high"}
    patch_rows = patch_plan_by_chapter(patch_plan)
    specs: list[QualityRepairTaskSpec] = []
    for row in audit_rows:
        priority = str(row.get("priority") or "ok")
        if priority not in wanted:
            continue
        try:
            chapter_number = int(row.get("chapter_number") or 0)
        except (TypeError, ValueError):
            continue
        if chapter_number <= 0:
            continue
        patch_row = patch_rows.get(chapter_number, {})
        patch_points = tuple(
            dict(point)
            for point in _sequence(patch_row.get("patch_points"))
            if isinstance(point, Mapping)
        )
        cause_ids = _split_causes(row.get("cause_ids")) or tuple(
            str(item) for item in _sequence(patch_row.get("cause_ids")) if str(item)
        )
        specs.append(
            QualityRepairTaskSpec(
                slug=slug,
                chapter_number=chapter_number,
                priority=priority,
                task_priority=_PRIORITY_WEIGHT.get(priority, 5),
                cause_ids=cause_ids,
                patch_points=patch_points,
                audit_row=dict(row),
            )
        )
        if limit is not None and limit > 0 and len(specs) >= limit:
            break
    return QualityRepairPlan(
        slug=slug,
        specs=tuple(specs),
        priority_counts=Counter(spec.priority for spec in specs),
        cause_counts=Counter(cause for spec in specs for cause in spec.cause_ids),
    )


def build_quality_repair_instructions(spec: QualityRepairTaskSpec) -> str:
    row = spec.audit_row
    lines = [
        "按整书同标质量门修补本章。不要只做润色；必须让本章重新通过章节质量、吸引力、节奏和连续性检查。",
        f"章节: ch{spec.chapter_number:03d}",
        f"优先级: {spec.priority}",
        "问题类型: " + (", ".join(spec.cause_ids) if spec.cause_ids else "unknown"),
        "",
        "硬性修补目标:",
    ]
    if "flat_narration" in spec.cause_ids:
        lines.append("- 补出可见章节功能：主动推进、反应转折、揭露、兑现或蓄压至少一项清晰成立。")
        lines.append("- 字数不足时扩写有效场景，不要用设定解释凑字。")
    if "weak_attraction" in spec.cause_ids:
        lines.append("- 提升心率密度：每 300-500 字至少有压力、危险、证物变化、行动阻断或新代价。")
    if "weak_prose" in spec.cause_ids:
        lines.append("- 把抽象感官词换成具体物件、动作、温度、声音、触感或可拍镜头。")
    if "ai_voice" in spec.cause_ids:
        lines.append("- 删除 AI 句式、套话比喻、解释型对白和“他意识到/这意味着”等结论句。")
    if "weak_immersion" in spec.cause_ids:
        lines.append("- 拆掉心理灌水，把背景信息改成动作触发、证物变化或人物对抗。")

    if row:
        lines.extend(
            [
                "",
                "审计数据:",
                f"- char_count={row.get('char_count')}",
                f"- word_count={row.get('word_count_reason')}",
                f"- pulse_density={row.get('pulse_density')}",
                f"- banned_patterns={row.get('banned_pattern_breakdown')}",
                f"- abstract_sensory={row.get('abstract_sensory_words')}",
                f"- rhythm_anchors={row.get('rhythm_total_anchors')}",
            ]
        )

    if spec.patch_points:
        lines.extend(["", "必须处理的精确 patch points:"])
        for index, point in enumerate(spec.patch_points, start=1):
            lines.append(
                "{idx}. {cause} @ {location}: {issue}; 修法: {repair}; 片段: {snippet}".format(
                    idx=index,
                    cause=point.get("cause_id", ""),
                    location=point.get("location", ""),
                    issue=point.get("issue_summary", ""),
                    repair=point.get("repair_action_summary", ""),
                    snippet=str(point.get("snippet", ""))[:180],
                )
            )

    lines.extend(
        [
            "",
            "输出要求:",
            "- 保留本章既有正典事实、人物目标、线索账本和章节标题。",
            "- 不引入新体系、新旧设定冲突或无代价解决。",
            "- 修后必须比原章更具体、更有行动压力、更少解释性旁白。",
        ]
    )
    return "\n".join(lines)


async def create_quality_retrofit_rewrite_tasks(
    session: AsyncSession,
    project: ProjectModel,
    specs: Sequence[QualityRepairTaskSpec],
    *,
    replace_existing: bool = False,
) -> TaskSyncResult:
    created = 0
    skipped = 0
    superseded = 0
    task_ids: list[str] = []
    missing_chapters: list[int] = []
    for spec in specs:
        chapter = await session.scalar(
            select(ChapterModel).where(
                ChapterModel.project_id == project.id,
                ChapterModel.chapter_number == spec.chapter_number,
            )
        )
        if chapter is None:
            missing_chapters.append(spec.chapter_number)
            continue

        existing = list(
            (
                await session.execute(
                    select(RewriteTaskModel).where(
                        RewriteTaskModel.project_id == project.id,
                        RewriteTaskModel.trigger_source_id == chapter.id,
                        RewriteTaskModel.trigger_type == AUTONOMOUS_REPAIR_TRIGGER,
                        RewriteTaskModel.status.in_(["pending", "queued"]),
                    )
                )
            ).scalars()
        )
        if existing and not replace_existing:
            skipped += 1
            task_ids.extend(str(task.id) for task in existing)
            continue
        if existing and replace_existing:
            await session.execute(
                update(RewriteTaskModel)
                .where(RewriteTaskModel.id.in_([task.id for task in existing]))
                .values(status="superseded")
            )
            superseded += len(existing)

        task = RewriteTaskModel(
            project_id=project.id,
            trigger_type=AUTONOMOUS_REPAIR_TRIGGER,
            trigger_source_id=chapter.id,
            rewrite_strategy=AUTONOMOUS_REPAIR_STRATEGY,
            priority=spec.task_priority,
            status="pending",
            instructions=build_quality_repair_instructions(spec),
            context_required=[
                "chapter_context",
                "current_chapter_draft",
                "quality_retrofit_audit_row",
                "quality_retrofit_patch_points",
                "whole_book_quality_gate",
                "premium_category_hard_engine",
            ],
            metadata_json={
                "autonomous_repair_id": spec.repair_id,
                "source": "quality_levers_retrofit_audit",
                "slug": spec.slug,
                "chapter_number": spec.chapter_number,
                "priority": spec.priority,
                "cause_ids": list(spec.cause_ids),
                "patch_points": [dict(point) for point in spec.patch_points],
                "audit_row": dict(spec.audit_row),
            },
        )
        session.add(task)
        await session.flush()
        task_ids.append(str(task.id))
        created += 1
    return TaskSyncResult(
        created=created,
        skipped_existing=skipped,
        superseded=superseded,
        missing_chapters=tuple(missing_chapters),
        task_ids=tuple(task_ids),
    )


def _split_causes(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item for item in (part.strip() for part in value.split(";")) if item)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(str(item) for item in value if str(item))
    return ()


def _sequence(value: object) -> list[object]:
    if value is None or isinstance(value, str | bytes):
        return []
    if isinstance(value, Sequence):
        return list(value)
    return []


__all__ = [
    "AUTONOMOUS_REPAIR_STRATEGY",
    "AUTONOMOUS_REPAIR_TRIGGER",
    "QualityRepairPlan",
    "QualityRepairTaskSpec",
    "TaskSyncResult",
    "build_quality_repair_instructions",
    "build_quality_repair_plan",
    "create_quality_retrofit_rewrite_tasks",
    "discover_output_book_slugs",
    "latest_quality_retrofit_csv",
    "load_patch_plan",
    "load_quality_retrofit_rows",
]
