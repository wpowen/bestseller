"""Gate material referential integrity for exported book packages."""

from __future__ import annotations

from pathlib import Path

from bestseller.domain.gate_verdict import GateFinding, GateVerdict
from bestseller.services.material_entity_registry import (
    EntityStatus,
    build_entity_registry,
)
from bestseller.services.material_reference_scanner import (
    ReferenceProblem,
    scan_material_references,
)

_PROBLEM_CODES = {
    "deprecated": ("MATERIAL_REFERENCES_DEPRECATED", "critical"),
    "unknown": ("MATERIAL_REFERENCES_UNKNOWN", "high"),
    "duplicate_canonical": ("MATERIAL_DUPLICATE_CANONICAL", "medium"),
}


def evaluate_material_referential_integrity(project_dir: Path) -> GateVerdict:
    """Build registry, scan material references, and return a lifecycle gate verdict."""

    registry = build_entity_registry(project_dir)
    problems = scan_material_references(project_dir, registry)
    findings = tuple(_finding_from_problem(problem) for problem in problems) + tuple(
        _finding_from_duplicate_record(record)
        for record in registry.records
        if record.status == EntityStatus.DUPLICATE
    )
    critical_count = sum(1 for finding in findings if finding.severity == "critical")
    high_count = sum(1 for finding in findings if finding.severity == "high")
    verdict = "blocked" if critical_count else "warn_only" if high_count or findings else "pass"
    scanned_records = len(registry.records)
    return GateVerdict(
        gate_name="material_referential_integrity",
        verdict=verdict,
        coverage=1.0 if not findings else 0.0,
        findings=findings,
        metrics={
            "entity_count": scanned_records,
            "problem_count": len(problems),
            "deprecated_count": sum(1 for p in problems if p.problem == "deprecated"),
            "unknown_count": sum(1 for p in problems if p.problem == "unknown"),
            "duplicate_canonical_count": sum(
                1 for p in problems if p.problem == "duplicate_canonical"
            ),
        },
        summary=(
            "Material references are internally consistent."
            if not findings
            else f"Material references contain {len(findings)} integrity finding(s)."
        ),
    )


def _finding_from_problem(problem: ReferenceProblem) -> GateFinding:
    code, severity = _PROBLEM_CODES.get(problem.problem, ("MATERIAL_REFERENCE_PROBLEM", "medium"))
    return GateFinding(
        code=code,
        severity=severity,
        message=(
            f"{problem.file}:{problem.line_no} references {problem.referenced_name!r} "
            f"as {problem.problem}."
        ),
        path=f"{problem.file}:{problem.line_no}",
        repair_action=_repair_action(problem),
    )


def _repair_action(problem: ReferenceProblem) -> str:
    if problem.problem == "deprecated":
        return (
            "Replace the deprecated name with the canonical active entity or "
            "remove the stale reference."
        )
    if problem.problem == "unknown":
        return (
            "Create the referenced entity in material files or remove the "
            "unsupported wikilink/rule reference."
        )
    if problem.problem == "duplicate_canonical":
        return "Merge duplicate character files and keep one canonical source."
    return "Review the material reference."


def _finding_from_duplicate_record(record: object) -> GateFinding:
    source = ", ".join(getattr(record, "source_files", ()))
    name = str(getattr(record, "canonical_name", ""))
    return GateFinding(
        code="MATERIAL_DUPLICATE_CANONICAL",
        severity="medium",
        message=f"Duplicate material file exists for canonical entity {name!r}: {source}.",
        path=source,
        repair_action="Merge duplicate character files and archive the redundant files.",
    )


__all__ = ["evaluate_material_referential_integrity"]
