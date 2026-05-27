"""L0-L3 universal quality attribution loop orchestration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
import json
from pathlib import Path
from typing import Any, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.artifact_topology import ARTIFACT_TOPOLOGY, artifact_priority
from bestseller.domain.reader_panel import DEFAULT_PANEL, ReaderRole
from bestseller.services.artifact_health_audit import ArtifactHealth, audit_artifact_health
from bestseller.services.autonomous_book_repair import (
    AUTONOMOUS_REPAIR_STRATEGY,
    AUTONOMOUS_REPAIR_TRIGGER,
)
from bestseller.services.causal_attribution import AttributionRecord, attribute_root_causes
from bestseller.services.material_self_repair import plan_material_self_repair
from bestseller.services.reader_panel_judge import ReaderFeedback, run_reader_panel
from bestseller.settings import AppSettings


class LoopResult(TypedDict):
    iterations: int
    final_feedback: list[ReaderFeedback]
    converged: bool
    repair_log: list[dict[str, Any]]


ReaderPanelRunner = Callable[..., Awaitable[list[ReaderFeedback]]]
CausalAttributor = Callable[..., Awaitable[list[AttributionRecord]]]
ArtifactAuditor = Callable[..., Awaitable[ArtifactHealth]]
ArtifactRepairer = Callable[..., Awaitable[dict[str, Any]]]


async def run_quality_attribution_loop(
    session: AsyncSession,
    settings: AppSettings,
    book_root: Path,
    *,
    chapter_range: tuple[int, int],
    max_iterations: int = 5,
    panel: Sequence[ReaderRole] = DEFAULT_PANEL,
    distilled_refs: Sequence[Path] = (),
    reader_panel_runner: ReaderPanelRunner = run_reader_panel,
    causal_attributor: CausalAttributor = attribute_root_causes,
    artifact_auditor: ArtifactAuditor = audit_artifact_health,
    artifact_repairer: ArtifactRepairer | None = None,
    write_reports: bool = True,
) -> LoopResult:
    """Run reader feedback, attribution, artifact audit, and top-down repair planning."""

    root = book_root.resolve()
    repair_log: list[dict[str, Any]] = []
    final_feedback: list[ReaderFeedback] = []
    report_rows: dict[str, list[Mapping[str, Any]]] = {
        "reader_feedback": [],
        "attribution_report": [],
        "artifact_health": [],
        "repair_log": repair_log,
    }

    repairer = artifact_repairer or plan_quality_artifact_repair
    for iteration in range(1, max_iterations + 1):
        chapter_texts = load_chapter_texts(root, chapter_range=chapter_range)
        final_feedback = await reader_panel_runner(
            session,
            settings,
            chapter_texts,
            panel=panel,
            distilled_refs=distilled_refs,
            target_chapter_range=chapter_range,
        )
        report_rows["reader_feedback"].extend(final_feedback)
        if not final_feedback:
            if write_reports:
                _write_reports(root, report_rows)
            return {
                "iterations": iteration,
                "final_feedback": final_feedback,
                "converged": True,
                "repair_log": repair_log,
            }

        attributions = await causal_attributor(
            session,
            settings,
            final_feedback,
            topology=ARTIFACT_TOPOLOGY,
            book_root=root,
        )
        ordered = sorted(
            attributions,
            key=lambda item: (
                artifact_priority(item["root_layer"], ARTIFACT_TOPOLOGY),
                item["artifact_path"],
            ),
        )
        report_rows["attribution_report"].extend(ordered)

        health_by_path: dict[str, ArtifactHealth] = {}
        for record in ordered:
            path = Path(record["artifact_path"])
            if not path.is_absolute():
                path = root / path
            if path.as_posix() not in health_by_path:
                health_by_path[path.as_posix()] = await artifact_auditor(
                    session,
                    settings,
                    path,
                    upstream_context=_upstream_context(root, record["root_layer"]),
                    distilled_refs=distilled_refs,
                )
        health_records = list(health_by_path.values())
        report_rows["artifact_health"].extend(health_records)

        unhealthy_paths = {
            health["artifact_path"]
            for health in health_records
            if not health["is_healthy"]
        }
        repair_targets = [
            record
            for record in ordered
            if record["artifact_path"] in unhealthy_paths or not unhealthy_paths
        ]
        if not repair_targets:
            if write_reports:
                _write_reports(root, report_rows)
            return {
                "iterations": iteration,
                "final_feedback": final_feedback,
                "converged": False,
                "repair_log": repair_log,
            }

        for record in repair_targets:
            repair_log.append(
                await repairer(
                    root,
                    record,
                    health_by_path.get(record["artifact_path"]),
                )
            )

    if write_reports:
        _write_reports(root, report_rows)
    return {
        "iterations": max_iterations,
        "final_feedback": final_feedback,
        "converged": False,
        "repair_log": repair_log,
    }


def load_chapter_texts(
    book_root: Path,
    *,
    chapter_range: tuple[int, int],
) -> dict[int, str]:
    start, end = chapter_range
    chapters: dict[int, str] = {}
    for chapter_no in range(start, end + 1):
        for path in _chapter_candidates(book_root, chapter_no):
            if path.is_file():
                chapters[chapter_no] = path.read_text(encoding="utf-8")
                break
    return chapters


async def plan_quality_artifact_repair(
    book_root: Path,
    attribution: AttributionRecord,
    health: ArtifactHealth | None = None,
) -> dict[str, Any]:
    """Plan the L3 repair action without mutating story artifacts directly."""

    layer = attribution["root_layer"]
    base: dict[str, Any] = {
        "root_layer": layer,
        "artifact_path": attribution["artifact_path"],
        "repair_directive": attribution["repair_directive"],
        "health": dict(health or {}),
    }
    if layer in {"material_entry", "character_card", "rule_ledger", "world_bible"}:
        material_plan = plan_material_self_repair(book_root)
        base.update(
            {
                "action": "material_self_repair_plan",
                "material_plan": material_plan.to_dict(),
            }
        )
        return base
    if layer == "chapter_text":
        base.update(
            {
                "action": "autonomous_book_repair_task",
                "trigger_type": AUTONOMOUS_REPAIR_TRIGGER,
                "rewrite_strategy": AUTONOMOUS_REPAIR_STRATEGY,
            }
        )
        return base
    base["action"] = "upstream_artifact_repair_required"
    return base


def _chapter_candidates(book_root: Path, chapter_no: int) -> tuple[Path, ...]:
    return (
        book_root / f"chapter-{chapter_no:03d}.md",
        book_root / f"chapter-{chapter_no}.md",
        book_root / "revised" / f"chapter-{chapter_no:03d}.md",
        book_root / "revised" / f"chapter-{chapter_no}.md",
    )


def _upstream_context(book_root: Path, root_layer: str) -> dict[str, Path]:
    node = ARTIFACT_TOPOLOGY.get(root_layer, {"upstream": []})
    context: dict[str, Path] = {}
    for upstream in node["upstream"]:
        candidate = book_root / f"{upstream}.json"
        if candidate.is_file():
            context[upstream] = candidate
    return context


def _write_reports(
    book_root: Path,
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    audit_dir = book_root / "audits" / "quality-attribution-loop"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name, values in rows.items():
        path = audit_dir / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for row in values:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


__all__ = [
    "LoopResult",
    "load_chapter_texts",
    "plan_quality_artifact_repair",
    "run_quality_attribution_loop",
]
