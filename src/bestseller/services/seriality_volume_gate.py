"""Verify that a VolumePlan implements the approved SerialityProof."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SerialityVolumeFinding:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class SerialityVolumeReport:
    passed: bool
    findings: tuple[SerialityVolumeFinding, ...]

    @property
    def blocking_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "blocking_codes": list(self.blocking_codes),
            "findings": [
                {"code": item.code, "message": item.message} for item in self.findings
            ],
            "schema_version": "seriality-volume-mapping.v1",
        }


def _text(value: object) -> str:
    return str(value or "").strip()


def _items(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, Sequence) or isinstance(value, bytes):
        return []
    return [text for item in value if (text := _text(item))]


def _phase_id(index: int) -> str:
    return f"phase-{index:02d}"


def _track_deltas(value: object) -> list[tuple[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    rows: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        track_ref = _text(item.get("track_ref"))
        delta = _text(item.get("delta"))
        if track_ref and delta:
            rows.append((track_ref, delta))
    return rows


def _is_concrete_delta(track_ref: str, delta: str) -> bool:
    generic = {
        "变化",
        "推进",
        "升级",
        "增加",
        "提升",
        "发生变化",
        "progress",
        "increase",
        "upgrade",
        "change",
    }
    remainder = delta.replace(track_ref, "").strip(" ：:，,。.;；-—")
    return len(remainder) >= 4 and remainder.casefold() not in generic


def evaluate_seriality_volume_mapping(
    volume_plan: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    concept_contract: Mapping[str, Any] | None,
) -> SerialityVolumeReport:
    if not isinstance(concept_contract, Mapping):
        return SerialityVolumeReport(passed=True, findings=())
    proof = concept_contract.get("seriality_proof")
    if not isinstance(proof, Mapping):
        return SerialityVolumeReport(
            passed=False,
            findings=(
                SerialityVolumeFinding(
                    "seriality_proof_missing",
                    "ConceptContract has no SerialityProof.",
                ),
            ),
        )

    # SerialityProof is persisted for short books as well so the conception
    # contract keeps one stable shape. Short books intentionally have no
    # phase/unit-family/accumulation mapping; applying this long-form gate to
    # them creates false phase_reference_invalid failures on ordinary plans.
    # Only enforce mapping for true long-form targets. When target metadata is
    # absent, retain strict standalone-validator behavior for existing tests.
    capacity_report = proof.get("capacity_report")
    target_chapters = 0
    capacity_tier = ""
    if isinstance(capacity_report, Mapping):
        try:
            target_chapters = int(capacity_report.get("target_chapters") or 0)
        except (TypeError, ValueError):
            target_chapters = 0
        capacity_tier = _text(capacity_report.get("capacity_tier"))
    if not target_chapters:
        try:
            target_chapters = int(proof.get("target_chapters") or 0)
        except (TypeError, ValueError):
            target_chapters = 0
    if (target_chapters and target_chapters < 200) or capacity_tier == "short":
        return SerialityVolumeReport(passed=True, findings=())

    raw_volumes = (
        volume_plan.get("volumes")
        if isinstance(volume_plan, Mapping)
        else volume_plan
    )
    volumes = (
        [dict(item) for item in raw_volumes if isinstance(item, Mapping)]
        if isinstance(raw_volumes, Sequence)
        else []
    )
    findings: list[SerialityVolumeFinding] = []
    if not volumes:
        return SerialityVolumeReport(
            passed=False,
            findings=(
                SerialityVolumeFinding(
                    "volume_plan_empty",
                    "No volumes exist to implement SerialityProof.",
                ),
            ),
        )

    phases = _items(proof.get("phase_transitions"))
    phase_ids = {_phase_id(index): phase for index, phase in enumerate(phases, start=1)}
    mapped_phase_ids: list[str] = []
    for index, item in enumerate(volumes, start=1):
        phase_id = _text(item.get("seriality_phase_id"))
        phase_ref = _text(item.get("seriality_phase_ref"))
        expected_ref = phase_ids.get(phase_id)
        if expected_ref is None or phase_ref != expected_ref:
            findings.append(
                SerialityVolumeFinding(
                    "phase_reference_invalid",
                    f"Volume {index} must use one exact approved phase id/ref pair.",
                )
            )
            continue
        mapped_phase_ids.append(phase_id)
    missing_phases = [
        phase for phase_id, phase in phase_ids.items() if phase_id not in mapped_phase_ids
    ]
    if missing_phases:
        findings.append(
            SerialityVolumeFinding(
                "phase_mapping_incomplete",
                "Unmapped phase transformations: " + " / ".join(missing_phases),
            )
        )
    mapped_phase_indexes = [int(item.split("-")[-1]) for item in mapped_phase_ids]
    if mapped_phase_indexes != sorted(mapped_phase_indexes):
        findings.append(
            SerialityVolumeFinding(
                "phase_order_invalid",
                "Volume phases move backwards instead of following the approved order.",
            )
        )

    tracks = _items(proof.get("accumulation_tracks"))
    all_track_deltas = [row for item in volumes for row in _track_deltas(item.get("accumulation_track_deltas"))]
    mapped_tracks = [track for track, _delta in all_track_deltas]
    missing_tracks = [
        track for track in tracks if track not in mapped_tracks
    ]
    if missing_tracks:
        findings.append(
            SerialityVolumeFinding(
                "accumulation_mapping_incomplete",
                "Unmapped permanent accumulation tracks: " + " / ".join(missing_tracks),
            )
        )
    for index, item in enumerate(volumes, start=1):
        missing = [
            key
            for key in (
                "seriality_phase_id",
                "seriality_phase_ref",
                "unit_family_ref",
                "renewable_unit_variant",
                "accumulation_track_deltas",
            )
            if not item.get(key)
        ]
        if missing:
            findings.append(
                SerialityVolumeFinding(
                    "volume_seriality_fields_missing",
                    f"Volume {index} is missing: {', '.join(missing)}",
                )
            )
        family = _text(item.get("unit_family_ref"))
        families = _items(proof.get("unit_families"))
        if family and family not in families:
            findings.append(
                SerialityVolumeFinding(
                    "unit_family_reference_invalid",
                    f"Volume {index} unit_family_ref is not an exact approved family.",
                )
            )
        for track_ref, delta in _track_deltas(item.get("accumulation_track_deltas")):
            if track_ref not in tracks:
                findings.append(
                    SerialityVolumeFinding(
                        "accumulation_track_reference_invalid",
                        f"Volume {index} references an unapproved accumulation track.",
                    )
                )
            elif not _is_concrete_delta(track_ref, delta):
                findings.append(
                    SerialityVolumeFinding(
                        "accumulation_delta_generic",
                        f"Volume {index} gives no concrete irreversible state change for {track_ref}.",
                    )
                )
    variants = [_text(item.get("renewable_unit_variant")) for item in volumes]
    if any(
        variants[index] and variants[index] == variants[index - 1]
        for index in range(1, len(variants))
    ):
        findings.append(
            SerialityVolumeFinding(
                "renewable_unit_repeated",
                "Consecutive volumes reuse the same renewable unit variant.",
            )
        )
    families = [_text(item.get("unit_family_ref")) for item in volumes]
    if any(
        families[index] and families[index] == families[index - 1]
        for index in range(1, len(families))
    ):
        findings.append(
            SerialityVolumeFinding(
                "unit_family_repeated",
                "Consecutive volumes reuse the same story-unit family.",
            )
        )
    required_family_coverage = min(len(volumes), len(_items(proof.get("unit_families"))))
    if len(set(families) - {""}) < required_family_coverage:
        findings.append(
            SerialityVolumeFinding(
                "unit_family_coverage_incomplete",
                "The volume plan does not exercise enough approved story-unit families.",
            )
        )
    delta_signatures = [(track, delta.casefold()) for track, delta in all_track_deltas]
    if len(delta_signatures) != len(set(delta_signatures)):
        findings.append(
            SerialityVolumeFinding(
                "accumulation_delta_repeated",
                "The same permanent state delta is copied across volumes.",
            )
        )
    return SerialityVolumeReport(passed=not findings, findings=tuple(findings))


__all__ = ["SerialityVolumeReport", "evaluate_seriality_volume_mapping"]
