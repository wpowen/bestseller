"""Deterministic long-form capacity proof.

The opening hook answers "would a reader click?".  This module answers a
different question: "what keeps producing materially new story after the
opening promise has been consumed?"  Keeping the two decisions separate stops
one-shot mysteries and costly abilities from being stretched into arbitrary
chapter targets merely because their hook copy sounds strong.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import re
from typing import Any

#: Chapter target at and above which the FULL seriality proof is generated —
#: and therefore the only band where it may be demanded.
#:
#: This constant exists because the two halves of the system disagreed and the
#: gap was a silent, deterministic book-killer. ``concept_tournament`` gates its
#: seriality expansion / repair / audit loop behind ``chapter_count >= 200``, so
#: below that the engine kernel is never asked for ``accumulation_tracks`` or
#: ``phase_transitions``. ``validate_concept_contract`` nevertheless ran the
#: full capacity proof at EVERY length — and without accumulation tracks the
#: ceiling is pinned at 50. Result: every book targeting 51–199 chapters died
#: with ``target_exceeds_capacity`` after a full conception run, 100% of the
#: time, including three shipped length presets (54 / 108 / 180). 50 chapters
#: survived only by landing exactly on the ceiling.
#:
#: Import this instead of writing ``>= 200`` again; the drift is what caused
#: the bug. Pinned by ``tests/unit/test_seriality_capacity_dead_band.py``.
SERIALITY_PROOF_REQUIRED_MIN_CHAPTERS = 200


@dataclass(frozen=True, slots=True)
class SerialityFinding:
    code: str
    message: str
    path: str
    repair_action: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "repair_action": self.repair_action,
        }


@dataclass(frozen=True, slots=True)
class SerialityCapacityReport:
    passed: bool
    target_chapters: int
    estimated_chapter_ceiling: int
    capacity_tier: str
    findings: tuple[SerialityFinding, ...] = field(default_factory=tuple)
    metrics: Mapping[str, object] = field(default_factory=dict)

    @property
    def blocking_codes(self) -> tuple[str, ...]:
        return tuple(finding.code for finding in self.findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "target_chapters": self.target_chapters,
            "estimated_chapter_ceiling": self.estimated_chapter_ceiling,
            "capacity_tier": self.capacity_tier,
            "blocking_codes": list(self.blocking_codes),
            "findings": [finding.to_dict() for finding in self.findings],
            "metrics": dict(self.metrics),
            "schema_version": "seriality-capacity.v1",
        }


def _text(value: object) -> str:
    return str(value or "").strip()


def _items(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, Sequence) or isinstance(value, bytes):
        return ()
    return tuple(dict.fromkeys(_text(item) for item in value if _text(item)))


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _cadence_is_obviously_sparse(value: str) -> bool:
    """Catch declared event cadence that cannot supply chapter-level motion."""

    compact = re.sub(r"\s+", "", value)
    if any(token in compact for token in ("每年", "每半年", "每季度", "每月")):
        return True
    match = re.search(r"每(\d+)(?:[-至到~～](\d+))?章", compact)
    return bool(match and int(match.group(1)) > 4)


def required_seriality_unit_count(target_chapters: int) -> int:
    """Return the minimum distinct-unit count with long-serial headroom.

    The proof contract defines epic micro-units as naturally recurring every
    2-4 chapters.  Counting only one unit per eight or ten chapters contradicts
    that contract and can cover less than half the requested book.  Epic
    targets therefore use the three-chapter midpoint; transitions and
    aftermath may overlap units, but cannot replace them.
    """

    denominator = 3 if target_chapters >= 500 else 10
    return max(1, (target_chapters + denominator - 1) // denominator)


def _finding(code: str, message: str, path: str, repair_action: str) -> SerialityFinding:
    return SerialityFinding(
        code=code,
        message=message,
        path=path,
        repair_action=repair_action,
    )


_PHASE_RANGE_RE = re.compile(
    r"(?:第\s*)?(\d{1,5})\s*(?:章)?\s*(?:-|—|–|~|～|至|到)\s*"
    r"(?:第\s*)?(\d{1,5})\s*章"
)
_EARLY_PHASE_RE = re.compile(r"前\s*(\d{1,5})\s*章")


def _phase_coverage(phases: Sequence[str]) -> tuple[int, tuple[tuple[int, int], ...]]:
    ranges: list[tuple[int, int]] = []
    for phase in phases:
        match = _PHASE_RANGE_RE.search(phase)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if start > end:
                start, end = end, start
            ranges.append((start, end))
            continue
        early = _EARLY_PHASE_RE.search(phase)
        if early:
            ranges.append((1, int(early.group(1))))
    if not ranges:
        return 0, ()
    ranges.sort()
    covered_until = 0
    for start, end in ranges:
        if start > covered_until + 1:
            break
        covered_until = max(covered_until, end)
    return covered_until, tuple(ranges)


def evaluate_seriality_capacity(
    proof: Mapping[str, Any] | None,
    *,
    target_chapters: int,
    require_phase_coverage: bool = False,
) -> SerialityCapacityReport:
    """Estimate an honest chapter ceiling from renewable story machinery.

    This is intentionally structural rather than lexical.  It does not award
    points for dramatic wording, costs, emotion vocabulary, or for simply
    claiming that an engine can escalate.  A premise earns capacity only by
    naming sources of new units, irreversible accumulation, phase changes,
    opposing ecology, and a question ladder.
    """

    payload = proof if isinstance(proof, Mapping) else {}
    repeatable_unit = _text(payload.get("repeatable_story_unit"))
    unit_frequency = _text(payload.get("unit_frequency"))
    unit_count_estimate = _nonnegative_int(payload.get("unit_count_estimate"))
    required_unit_count = required_seriality_unit_count(target_chapters)
    unit_families = _items(payload.get("unit_families"))
    renewal_sources = _items(payload.get("renewal_sources"))
    accumulation_tracks = _items(payload.get("accumulation_tracks"))
    phase_transitions = _items(payload.get("phase_transitions"))
    opposing_ecology = _items(payload.get("opposing_ecology"))
    mystery_ladder = _items(payload.get("mystery_ladder"))
    endgame_direction = _text(payload.get("endgame_direction"))
    phase_coverage_max, phase_ranges = _phase_coverage(phase_transitions)

    ceiling = 50 if repeatable_unit else 20
    renewable = bool(repeatable_unit and len(renewal_sources) >= 2)
    accumulating = len(accumulation_tracks) >= 2
    if renewable and accumulating:
        ceiling = 200
    if (
        renewable
        and accumulating
        and len(phase_transitions) >= 4
        and len(opposing_ecology) >= 2
        and len(mystery_ladder) >= 3
        and endgame_direction
    ):
        ceiling = 500
    if (
        len(renewal_sources) >= 3
        and len(accumulation_tracks) >= 3
        and len(phase_transitions) >= 5
        and len(opposing_ecology) >= 3
        and len(mystery_ladder) >= 5
        and endgame_direction
    ):
        ceiling = 1000
    if (
        len(renewal_sources) >= 4
        and len(accumulation_tracks) >= 4
        and len(phase_transitions) >= 7
        and len(opposing_ecology) >= 4
        and len(mystery_ladder) >= 7
        and endgame_direction
    ):
        ceiling = 1500
    if (
        len(renewal_sources) >= 5
        and len(accumulation_tracks) >= 5
        and len(phase_transitions) >= 9
        and len(opposing_ecology) >= 5
        and len(mystery_ladder) >= 9
        and endgame_direction
    ):
        ceiling = 2000
    if require_phase_coverage and target_chapters >= 200:
        ceiling = min(ceiling, phase_coverage_max or 50)
        required_family_count = 6 if target_chapters >= 1500 else 4
        if (
            not unit_frequency
            or len(unit_families) < required_family_count
            or _cadence_is_obviously_sparse(unit_frequency)
            or unit_count_estimate < required_unit_count
        ):
            ceiling = min(ceiling, 100)

    findings: list[SerialityFinding] = []
    if not repeatable_unit:
        findings.append(
            _finding(
                "repeatable_story_unit_missing",
                "No repeatable unit of story has been named.",
                "seriality_proof.repeatable_story_unit",
                "Name the case, mission, transaction, territory contest, or "
                "relationship move that can recur with new contents.",
            )
        )
    if target_chapters >= 200 and len(renewal_sources) < 2:
        findings.append(
            _finding(
                "renewal_source_missing",
                "The premise does not explain where materially new conflicts keep coming from.",
                "seriality_proof.renewal_sources",
                "Add at least two independent sources that continuously create new story units.",
            )
        )
    if target_chapters >= 200 and len(accumulation_tracks) < 2:
        findings.append(
            _finding(
                "accumulation_tracks_thin",
                "Repeated units do not leave enough irreversible change behind.",
                "seriality_proof.accumulation_tracks",
                "Name at least two visible tracks whose state cannot reset after each arc.",
            )
        )
    if target_chapters >= 200 and len(phase_transitions) < 3:
        findings.append(
            _finding(
                "phase_transitions_thin",
                "The book has too few rule-changing phases for the requested length.",
                "seriality_proof.phase_transitions",
                "Design at least three phases where arena, opponent, objective, or rules change.",
            )
        )
    if (
        require_phase_coverage
        and target_chapters >= 200
        and phase_coverage_max < target_chapters
    ):
        findings.append(
            _finding(
                "phase_coverage_incomplete",
                f"Phase plan only proves continuous coverage through chapter "
                f"{phase_coverage_max or 0}, below target {target_chapters}.",
                "seriality_proof.phase_transitions",
                "Give every phase an explicit chapter range; ranges must start at 1, "
                "be continuous, and the final range must reach the requested target.",
            )
        )
    if require_phase_coverage and target_chapters >= 200 and not unit_frequency:
        findings.append(
            _finding(
                "unit_density_unproven",
                "The proof does not state a natural chapter-level story cadence.",
                "seriality_proof.unit_frequency",
                "State how often the protagonist makes a consequential choice or faces a "
                "countermove; do not infer capacity from a claimed total.",
            )
        )
    if require_phase_coverage and target_chapters >= 200 and len(unit_families) < 4:
        findings.append(
            _finding(
                "unit_family_breadth_thin",
                "The engine has too few distinct action grammars to vary its story units.",
                "seriality_proof.unit_families",
                "Name at least four mechanism-native action families such as discovery, "
                "trade, relationship choice, construction, or public contest.",
            )
        )
    if (
        require_phase_coverage
        and target_chapters >= 1500
        and len(unit_families) < 6
    ):
        findings.append(
            _finding(
                "unit_family_breadth_ultra_thin",
                "Ultra-long fiction needs more distinct action grammars than a 500-chapter serial.",
                "seriality_proof.unit_families",
                "Name at least six genuinely different action families used by the same premise.",
            )
        )
    if (
        require_phase_coverage
        and target_chapters >= 200
        and _cadence_is_obviously_sparse(unit_frequency)
    ):
        findings.append(
            _finding(
                "unit_cadence_sparse",
                f"Declared cadence '{unit_frequency}' is too sparse for chapter-level motion.",
                "seriality_proof.unit_frequency",
                "Expose the 2-4 chapter choices and countermoves inside the larger arc; "
                "do not count only annual events or 8-12 chapter outcomes.",
            )
        )
    if (
        require_phase_coverage
        and target_chapters >= 200
        and unit_count_estimate < required_unit_count
    ):
        findings.append(
            _finding(
                "unit_count_below_target",
                f"The proof names {unit_count_estimate} story units, below the minimum "
                f"{required_unit_count} required by its declared cadence.",
                "seriality_proof.unit_count_estimate",
                "Provide a phase-by-phase unit budget consistent with the 2-4 chapter cadence, "
                "or reduce the target. A claimed total never substitutes for structural proof.",
            )
        )
    if target_chapters >= 500 and len(opposing_ecology) < 2:
        findings.append(
            _finding(
                "opposing_ecology_thin",
                "A single opponent cannot renew conflict across an epic-length book.",
                "seriality_proof.opposing_ecology",
                "Create multiple self-interested forces that also conflict with each other.",
            )
        )
    if target_chapters >= 500 and len(mystery_ladder) < 3:
        findings.append(
            _finding(
                "mystery_ladder_thin",
                "The central question has no layered replacement questions.",
                "seriality_proof.mystery_ladder",
                "Define local, systemic, and endgame questions; resolving one "
                "must expose the next.",
            )
        )
    if target_chapters >= 1000 and ceiling < 1000:
        findings.append(
            _finding(
                "millennial_capacity_unproven",
                "The proof supports a long serial, but not an honest thousand-chapter target.",
                "seriality_proof",
                "Either deepen all five renewal dimensions or downgrade the "
                "target before planning volumes.",
            )
        )
    if target_chapters >= 1500 and ceiling < target_chapters:
        findings.append(
            _finding(
                "ultra_long_capacity_unproven",
                "The proof does not support the requested 1500-2000 chapter scale.",
                "seriality_proof",
                "Add more independent renewal, accumulation, phase, opposition, and question "
                "layers, with continuous chapter ranges and a matching unit budget.",
            )
        )
    if target_chapters > ceiling and not any(
        finding.code == "millennial_capacity_unproven" for finding in findings
    ):
        findings.append(
            _finding(
                "target_exceeds_capacity",
                f"Requested {target_chapters} chapters exceeds the proven ceiling of {ceiling}.",
                "target_chapters",
                "Strengthen the renewable engine or reduce the target; do not "
                "manufacture volume count first.",
            )
        )

    if ceiling <= 100:
        tier = "short"
    elif ceiling <= 200:
        tier = "serial"
    elif ceiling <= 500:
        tier = "epic"
    elif ceiling <= 1000:
        tier = "mega"
    elif ceiling <= 1500:
        tier = "ultra"
    else:
        tier = "ultra_2000"
    return SerialityCapacityReport(
        passed=target_chapters <= ceiling and not findings,
        target_chapters=target_chapters,
        estimated_chapter_ceiling=ceiling,
        capacity_tier=tier,
        findings=tuple(findings),
        metrics={
            "renewal_source_count": len(renewal_sources),
            "unit_frequency": unit_frequency,
            "unit_count_estimate": unit_count_estimate,
            "required_unit_count": required_unit_count,
            "unit_count_is_claim_only": True,
            "unit_family_count": len(unit_families),
            "accumulation_track_count": len(accumulation_tracks),
            "phase_transition_count": len(phase_transitions),
            "phase_coverage_max": phase_coverage_max,
            "phase_ranges": [list(item) for item in phase_ranges],
            "opposing_ecology_count": len(opposing_ecology),
            "mystery_ladder_count": len(mystery_ladder),
            "has_endgame_direction": bool(endgame_direction),
        },
    )


__all__ = [
    "SerialityCapacityReport",
    "SerialityFinding",
    "evaluate_seriality_capacity",
    "required_seriality_unit_count",
]
