from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.gate_verdict import GateFinding, GateVerdict
from bestseller.services.autonomous_book_repair import (
    QualityRepairPlan,
    QualityRepairTaskSpec,
    TaskSyncResult,
    create_quality_retrofit_rewrite_tasks,
)
from bestseller.services.book_quality_closure import (
    MissingChapterContinuationPlan,
    build_missing_chapter_continuation_plan,
)
from bestseller.services.chapter_splice_coherence_gate import (
    evaluate_chapter_splice_coherence,
)
from bestseller.services.material_self_repair import (
    MaterialSelfRepairPlan,
    plan_material_self_repair,
)
from bestseller.services.projects import get_project_by_slug
from bestseller.settings import AppSettings

WIP_REPAIR_SOURCE = "wip_repair_closure"


@dataclass(frozen=True, slots=True)
class WIPRepairClosureReport:
    slug: str
    status: str
    next_action: str
    repair_start: int
    repair_end: int
    project_dir: str
    material_repair: Mapping[str, Any] = field(default_factory=dict)
    chapter_splice_gates: tuple[Mapping[str, Any], ...] = ()
    repair_plan: Mapping[str, Any] = field(default_factory=dict)
    task_sync: Mapping[str, Any] = field(default_factory=dict)
    continuation_plan: Mapping[str, Any] = field(default_factory=dict)
    execution: Mapping[str, Any] = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "status": self.status,
            "next_action": self.next_action,
            "repair_start": self.repair_start,
            "repair_end": self.repair_end,
            "project_dir": self.project_dir,
            "material_repair": dict(self.material_repair),
            "chapter_splice_gates": [dict(item) for item in self.chapter_splice_gates],
            "repair_plan": dict(self.repair_plan),
            "task_sync": dict(self.task_sync),
            "continuation_plan": dict(self.continuation_plan),
            "execution": dict(self.execution),
            "errors": list(self.errors),
        }


def collect_wip_chapter_splice_gates(
    project_dir: Path,
    *,
    repair_start: int,
    repair_end: int,
) -> tuple[GateVerdict, ...]:
    gates: list[GateVerdict] = []
    for chapter_number in range(repair_start, repair_end + 1):
        path = project_dir / f"chapter-{chapter_number:03d}.md"
        if not path.is_file():
            continue
        gates.append(
            evaluate_chapter_splice_coherence(
                path.read_text(encoding="utf-8"),
                chapter_number=chapter_number,
            )
        )
    return tuple(gates)


def build_wip_repair_plan_from_gates(
    *,
    slug: str,
    repair_start: int,
    repair_end: int,
    splice_gates: Sequence[GateVerdict],
    material_plan: MaterialSelfRepairPlan,
) -> QualityRepairPlan:
    specs: list[QualityRepairTaskSpec] = []
    material_patch_points = _material_patch_points(material_plan)
    material_blocking = bool(material_plan.blocking)
    seen_chapters: set[int] = set()

    for gate in splice_gates:
        chapter_number = _chapter_number_from_gate(gate)
        if chapter_number is None:
            continue
        blocking_findings = tuple(
            finding
            for finding in gate.findings
            if finding.severity in {"critical", "high"}
        )
        if not blocking_findings:
            continue
        seen_chapters.add(chapter_number)
        cause_ids = tuple(dict.fromkeys(finding.code for finding in blocking_findings))
        if material_blocking:
            cause_ids = (*cause_ids, "MATERIAL_SELF_REPAIR_BLOCKING")
        specs.append(
            QualityRepairTaskSpec(
                slug=slug,
                chapter_number=chapter_number,
                priority="critical"
                if any(finding.severity == "critical" for finding in blocking_findings)
                else "high",
                task_priority=1
                if any(finding.severity == "critical" for finding in blocking_findings)
                else 2,
                cause_ids=cause_ids,
                patch_points=(
                    *_splice_patch_points(blocking_findings),
                    *material_patch_points[:3],
                ),
                audit_row={
                    "source": WIP_REPAIR_SOURCE,
                    "chapter_number": chapter_number,
                    "repair_start": repair_start,
                    "repair_end": repair_end,
                    "material_blocking": material_blocking,
                    "material_action_count": material_plan.metrics.get(
                        "action_count",
                        0,
                    ),
                },
            )
        )

    if material_blocking and repair_start not in seen_chapters:
        specs.insert(
            0,
            QualityRepairTaskSpec(
                slug=slug,
                chapter_number=repair_start,
                priority="critical",
                task_priority=1,
                cause_ids=("MATERIAL_SELF_REPAIR_BLOCKING", "WIP_FRONT_WINDOW_REPAIR"),
                patch_points=material_patch_points[:8],
                audit_row={
                    "source": WIP_REPAIR_SOURCE,
                    "chapter_number": repair_start,
                    "repair_start": repair_start,
                    "repair_end": repair_end,
                    "material_blocking": True,
                    "material_action_count": material_plan.metrics.get(
                        "action_count",
                        0,
                    ),
                },
            ),
        )

    return QualityRepairPlan(
        slug=slug,
        specs=tuple(specs),
        priority_counts=Counter(spec.priority for spec in specs),
        cause_counts=Counter(cause for spec in specs for cause in spec.cause_ids),
    )


async def build_wip_repair_closure_report(
    session: AsyncSession,
    settings: AppSettings,
    *,
    slug: str,
    repair_start: int = 1,
    repair_end: int = 10,
    continuation_size: int = 0,
    create_tasks: bool = False,
    replace_existing: bool = False,
    max_attempts_per_chapter: int | None = 2,
) -> WIPRepairClosureReport:
    project = await get_project_by_slug(session, slug)
    project_dir = Path(settings.output.base_dir) / slug
    if project is None:
        return WIPRepairClosureReport(
            slug=slug,
            status="blocked",
            next_action="project_not_found",
            repair_start=repair_start,
            repair_end=repair_end,
            project_dir=str(project_dir),
            errors=("project_not_found",),
        )
    if not project_dir.exists():
        return WIPRepairClosureReport(
            slug=slug,
            status="blocked",
            next_action="project_output_dir_not_found",
            repair_start=repair_start,
            repair_end=repair_end,
            project_dir=str(project_dir),
            errors=("project_output_dir_not_found",),
        )

    material_plan = plan_material_self_repair(
        project_dir,
        chapter_number=repair_start,
    )
    splice_gates = collect_wip_chapter_splice_gates(
        project_dir,
        repair_start=repair_start,
        repair_end=repair_end,
    )
    repair_plan = build_wip_repair_plan_from_gates(
        slug=slug,
        repair_start=repair_start,
        repair_end=repair_end,
        splice_gates=splice_gates,
        material_plan=material_plan,
    )
    task_sync = (
        await create_quality_retrofit_rewrite_tasks(
            session,
            project,
            repair_plan.specs,
            replace_existing=replace_existing,
            max_attempts_per_chapter=max_attempts_per_chapter,
        )
        if create_tasks and repair_plan.specs
        else TaskSyncResult(0, 0, 0, (), ())
    )
    continuation_plan = await build_missing_chapter_continuation_plan(
        session,
        project,
        limit=max(int(continuation_size or 0), 1),
    )
    status, next_action = _next_action(
        material_plan=material_plan,
        repair_plan=repair_plan,
        task_sync=task_sync,
        continuation_plan=continuation_plan,
        create_tasks=create_tasks,
        continuation_size=continuation_size,
    )
    return WIPRepairClosureReport(
        slug=slug,
        status=status,
        next_action=next_action,
        repair_start=repair_start,
        repair_end=repair_end,
        project_dir=str(project_dir),
        material_repair=_material_summary(material_plan),
        chapter_splice_gates=tuple(
            _gate_summary(gate, chapter_number=_chapter_number_from_gate(gate))
            for gate in splice_gates
        ),
        repair_plan=repair_plan.to_dict(),
        task_sync=task_sync.to_dict(),
        continuation_plan=continuation_plan.to_dict(),
    )


def _next_action(
    *,
    material_plan: MaterialSelfRepairPlan,
    repair_plan: QualityRepairPlan,
    task_sync: TaskSyncResult,
    continuation_plan: MissingChapterContinuationPlan,
    create_tasks: bool,
    continuation_size: int,
) -> tuple[str, str]:
    if repair_plan.specs and not create_tasks:
        return "needs_repair", "sync_wip_repair_tasks"
    if repair_plan.specs:
        created_or_refreshed = (
            task_sync.created
            + task_sync.skipped_existing
            + task_sync.superseded
            + len(task_sync.task_ids)
        )
        if created_or_refreshed:
            return "repairing", "execute_wip_repair_tasks"
        return "blocked", "repair_tasks_not_created"
    if material_plan.blocking:
        return "blocked", "material_self_repair_required"
    if continuation_size > 0 and continuation_plan.has_executable_outline_batch:
        return "continuing", "generate_next_chapters_under_wip_gates"
    return "ready", "no_wip_repair_needed"


def _chapter_number_from_gate(gate: GateVerdict) -> int | None:
    value = gate.metrics.get("chapter_number")
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _splice_patch_points(findings: Sequence[GateFinding]) -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "cause_id": finding.code,
            "location": finding.path,
            "issue_summary": finding.message,
            "snippet": "",
            "repair_action_summary": finding.repair_action,
        }
        for finding in findings
    )


def _material_patch_points(
    material_plan: MaterialSelfRepairPlan,
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "cause_id": "MATERIAL_SELF_REPAIR_BLOCKING",
            "location": action.source_path or action.target,
            "issue_summary": f"{action.action_type}: {action.target}; {action.reason}",
            "snippet": str(action.payload.get("context") or "")[:180],
            "repair_action_summary": _material_repair_action_summary(action.to_dict()),
        }
        for action in material_plan.actions
        if action.action_type
        in {
            "replace_deprecated_reference",
            "create_missing_entity_placeholder",
            "expand_missing_chapter_material",
            "complete_placeholder_entity",
        }
    )


def _material_repair_action_summary(action: Mapping[str, Any]) -> str:
    payload = action.get("payload")
    payload_map = payload if isinstance(payload, Mapping) else {}
    replacement = payload_map.get("replacement")
    if replacement:
        return f"改为正典引用 {replacement}，并删除过期称谓。"
    minimum_fields = payload_map.get("minimum_fields")
    if isinstance(minimum_fields, Sequence) and not isinstance(
        minimum_fields,
        (str, bytes),
    ):
        return "补齐物料字段: " + ", ".join(str(item) for item in minimum_fields)
    return str(payload_map.get("instruction") or "补齐物料后再重写章节，不用正文掩盖缺失正典。")


def _material_summary(plan: MaterialSelfRepairPlan) -> dict[str, Any]:
    action_counts = Counter(action.action_type for action in plan.actions)
    top_targets = Counter(action.target for action in plan.actions).most_common(20)
    return {
        "blocking": plan.blocking,
        "metrics": dict(plan.metrics),
        "action_counts": dict(action_counts),
        "top_targets": [
            {"target": target, "count": count} for target, count in top_targets
        ],
    }


def _gate_summary(
    gate: GateVerdict,
    *,
    chapter_number: int | None,
) -> dict[str, Any]:
    findings = [
        finding.model_dump(mode="json")
        for finding in gate.findings
        if finding.severity in {"critical", "high"}
    ]
    return {
        "chapter_number": chapter_number,
        "verdict": gate.verdict,
        "coverage": gate.coverage,
        "metrics": dict(gate.metrics),
        "blocking_findings": findings,
    }


__all__ = [
    "WIP_REPAIR_SOURCE",
    "WIPRepairClosureReport",
    "build_wip_repair_closure_report",
    "build_wip_repair_plan_from_gates",
    "collect_wip_chapter_splice_gates",
]
