"""Validate chapter-outline execution of the current volume seriality contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class SerialityOutlineFinding:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class SerialityOutlineReport:
    passed: bool
    findings: tuple[SerialityOutlineFinding, ...]

    @property
    def blocking_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.findings)


def _text(value: object) -> str:
    return str(value or "").strip()


def _items(value: object) -> list[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return list(value)


def _track_deltas(value: object) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for item in _items(value):
        if not isinstance(item, Mapping):
            continue
        track_ref = _text(item.get("track_ref"))
        delta = _text(item.get("delta"))
        if track_ref and delta:
            rows.append((track_ref, delta))
    return rows


def evaluate_seriality_outline_batch(
    chapters: Sequence[Mapping[str, object]],
    concept_contract: Mapping[str, object] | None,
    volume_entry: Mapping[str, object] | None,
) -> SerialityOutlineReport:
    """Fail only for v2 outlines; legacy projects remain unaffected."""

    if not isinstance(concept_contract, Mapping):
        return SerialityOutlineReport(passed=True, findings=())
    if not isinstance(volume_entry, Mapping):
        return SerialityOutlineReport(
            passed=False,
            findings=(
                SerialityOutlineFinding(
                    "seriality_volume_context_missing",
                    "The outline batch has no current-volume seriality context.",
                ),
            ),
        )

    # The conception contract is shared by short and long books. Short books
    # intentionally do not carry phase/unit-family/accumulation mappings, so
    # requiring a chapter-level seriality_contract here would reject a valid
    # ordinary outline after the volume-level gate has already skipped the
    # long-form mapping. Keep strict validation when target metadata is absent
    # for standalone validator callers and legacy tests.
    proof = concept_contract.get("seriality_proof")
    capacity_report = proof.get("capacity_report") if isinstance(proof, Mapping) else None
    target_chapters = 0
    capacity_tier = ""
    if isinstance(capacity_report, Mapping):
        try:
            target_chapters = int(capacity_report.get("target_chapters") or 0)
        except (TypeError, ValueError):
            target_chapters = 0
        capacity_tier = _text(capacity_report.get("capacity_tier"))
    if not target_chapters and isinstance(proof, Mapping):
        try:
            target_chapters = int(proof.get("target_chapters") or 0)
        except (TypeError, ValueError):
            target_chapters = 0
    if (target_chapters and target_chapters < 200) or capacity_tier == "short":
        return SerialityOutlineReport(passed=True, findings=())

    findings: list[SerialityOutlineFinding] = []
    contributions: list[str] = []
    phase_progressions: list[str] = []
    state_after_values: list[str] = []
    no_reset_values: list[str] = []
    unit_instance_ids: list[str] = []
    volume_phase_id = _text(volume_entry.get("seriality_phase_id"))
    volume_family = _text(volume_entry.get("unit_family_ref"))
    approved_tracks = {
        track
        for track, _delta in _track_deltas(volume_entry.get("accumulation_track_deltas"))
    }
    mapped_tracks: set[str] = set()
    for index, chapter in enumerate(chapters, start=1):
        raw = chapter.get("seriality_contract")
        contract = dict(raw) if isinstance(raw, Mapping) else {}
        missing = [
            key
            for key in (
                "phase_id",
                "unit_family_ref",
                "unit_instance_id",
                "unit_variant_contribution",
                "phase_progress",
                "prior_state_refs",
                "irreversible_state_after",
                "no_reset_evidence",
            )
            if not contract.get(key)
        ]
        if missing:
            findings.append(
                SerialityOutlineFinding(
                    "chapter_seriality_contract_missing",
                    f"Chapter {index} seriality_contract missing: {', '.join(missing)}",
                )
            )
        contribution = _text(contract.get("unit_variant_contribution"))
        if contribution:
            contributions.append(contribution)
        phase_progress = _text(contract.get("phase_progress"))
        if phase_progress:
            phase_progressions.append(phase_progress)
        state_after = _text(contract.get("irreversible_state_after"))
        if state_after:
            state_after_values.append(state_after)
        no_reset = _text(contract.get("no_reset_evidence"))
        if no_reset:
            no_reset_values.append(no_reset)
        unit_instance_id = _text(contract.get("unit_instance_id"))
        if unit_instance_id:
            unit_instance_ids.append(unit_instance_id)
        if _text(contract.get("phase_id")) != volume_phase_id:
            findings.append(
                SerialityOutlineFinding(
                    "chapter_phase_reference_mismatch",
                    f"Chapter {index} does not reference the current volume phase id exactly.",
                )
            )
        if _text(contract.get("unit_family_ref")) != volume_family:
            findings.append(
                SerialityOutlineFinding(
                    "chapter_unit_family_mismatch",
                    f"Chapter {index} does not reference the current volume unit family exactly.",
                )
            )
        raw_track_deltas = contract.get("accumulation_track_deltas")
        if not isinstance(raw_track_deltas, Sequence) or isinstance(
            raw_track_deltas, (str, bytes)
        ):
            findings.append(
                SerialityOutlineFinding(
                    "chapter_accumulation_mapping_missing",
                    f"Chapter {index} must output accumulation_track_deltas (an empty array is allowed while preparing a change).",
                )
            )
        for track_ref, _delta in _track_deltas(raw_track_deltas):
            if track_ref not in approved_tracks:
                findings.append(
                    SerialityOutlineFinding(
                        "chapter_accumulation_track_mismatch",
                        f"Chapter {index} references a track outside the current volume contract.",
                    )
                )
            else:
                mapped_tracks.add(track_ref)

    repeated_fields = (
        ("chapter_seriality_contribution_repeated", contributions, "story-unit contribution"),
        ("chapter_phase_progress_repeated", phase_progressions, "phase progress"),
        ("chapter_irreversible_state_repeated", state_after_values, "irreversible state"),
        ("chapter_no_reset_evidence_repeated", no_reset_values, "no-reset evidence"),
    )
    if len(chapters) > 1:
        for code, values, label in repeated_fields:
            if len(values) == len(chapters) and len(set(values)) <= 1:
                findings.append(
                    SerialityOutlineFinding(
                        code,
                        f"All chapters copy the same {label} instead of progressing it.",
                    )
                )
    try:
        volume_target = int(volume_entry.get("chapter_count_target") or 0)
    except (TypeError, ValueError):
        volume_target = 0
    is_full_volume_validation = volume_target <= 0 or len(chapters) >= volume_target
    if is_full_volume_validation and approved_tracks - mapped_tracks:
        findings.append(
            SerialityOutlineFinding(
                "chapter_accumulation_coverage_incomplete",
                "The completed volume never realizes every accumulation track assigned to it.",
            )
        )
    if len(chapters) >= 6:
        minimum_instances = math.ceil(len(chapters) / 6)
        if len(set(unit_instance_ids)) < minimum_instances:
            findings.append(
                SerialityOutlineFinding(
                    "chapter_story_unit_density_too_low",
                    f"{len(chapters)} chapters need at least {minimum_instances} distinct story-unit instances.",
                )
            )
    return SerialityOutlineReport(passed=not findings, findings=tuple(findings))


__all__ = ["SerialityOutlineReport", "evaluate_seriality_outline_batch"]
