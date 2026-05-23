from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bestseller.domain.gate_verdict import GateFinding, GateVerdict

CORE_GATE_BATCH_A: tuple[str, ...] = (
    "commercial_novel_gate",
    "lifecycle_quality",
    "whole_book_quality",
    "paragraph_coherence_gate",
    "chapter_seam_inheritance_gate",
    "duplicate_passage_gate",
    "identity_freezer_gate",
    "forbidden_terms_drift_gate",
    "outline_density_gate",
    "chapter_entry_exit_gate",
    "prewrite_contract_coverage",
    "repair_batch",
)


def normalize_gate_payload(
    gate_name: str,
    payload: Mapping[str, Any],
) -> GateVerdict:
    verdict_payload = payload.get("gate_verdict")
    if isinstance(verdict_payload, Mapping):
        return GateVerdict.model_validate(verdict_payload)
    findings = tuple(_finding_from_mapping(item) for item in _findings(payload))
    passed = bool(payload.get("passed"))
    coverage = _coverage(payload, findings=findings, passed=passed)
    verdict = (
        "pass"
        if passed
        else ("blocked" if any(f.critical for f in findings) else "warn_only")
    )
    return GateVerdict(
        gate_name=gate_name,
        verdict=verdict,
        coverage=coverage,
        findings=findings,
        metrics={
            key: value
            for key, value in payload.items()
            if key not in {"findings", "issues", "gate_verdict"}
        },
    )


def normalize_core_gate_batch(
    payloads: Mapping[str, Mapping[str, Any]],
) -> tuple[GateVerdict, ...]:
    return tuple(
        normalize_gate_payload(gate_name, payloads.get(gate_name, {}))
        for gate_name in CORE_GATE_BATCH_A
    )


def _findings(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = payload.get("findings") or payload.get("issues") or []
    return [item for item in raw if isinstance(item, Mapping)]


def _finding_from_mapping(item: Mapping[str, Any]) -> GateFinding:
    return GateFinding(
        code=str(item.get("code") or item.get("id") or "finding"),
        severity=str(item.get("severity") or "medium"),  # type: ignore[arg-type]
        message=str(item.get("message") or item.get("detail") or item.get("description") or ""),
        path=str(item.get("path") or item.get("scope") or item.get("location") or ""),
        repair_action=str(item.get("repair_action") or item.get("suggestion") or ""),
    )


def _coverage(
    payload: Mapping[str, Any],
    *,
    findings: tuple[GateFinding, ...],
    passed: bool,
) -> float:
    if payload.get("coverage") is not None:
        return _float(payload.get("coverage"), 0.0)
    score = payload.get("overall_score") or payload.get("quality_score")
    if score is not None:
        return max(0.0, min(1.0, _float(score, 0.0) / 100))
    return 1.0 if passed and not findings else 0.0


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
