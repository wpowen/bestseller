"""Mid-length books must not be killed by a proof nobody was asked to produce.

Verified defect (2026-07-25): every book with 51–199 target chapters that got
a tournament winner died at ``validate_concept_contract`` with
``target_exceeds_capacity`` — deterministically, 100% of the time, after the
full ~15-call conception had already run.

The two halves of the system disagreed about who owns the seriality proof:

* GENERATION (``concept_tournament``) only runs the seriality expansion /
  repair / audit loop for ``chapter_count >= 200``. Below that, fields like
  ``accumulation_tracks`` and ``phase_transitions`` are never requested by the
  engine-kernel prompt and come back empty by design.
* VALIDATION (``concept_contract.validate_concept_contract``) ran
  ``evaluate_seriality_capacity(..., require_phase_coverage=True)`` at EVERY
  length — and that function caps the ceiling at 50 unless
  ``accumulation_tracks >= 2``.

So the validator demanded evidence the generator was never asked to produce.
50 chapters survived only by landing exactly on the ceiling; 51 did not. Three
shipped length presets sit in the dead band (54 / 108 / 180 chapters).

The durable fix is a single shared constant instead of a magic 200 copied into
both halves — the drift is what created the gap.
"""

from __future__ import annotations

import pytest

from bestseller.services.concept_contract import validate_concept_contract
from bestseller.services.seriality_capacity import (
    SERIALITY_PROOF_REQUIRED_MIN_CHAPTERS,
    evaluate_seriality_capacity,
)


pytestmark = pytest.mark.unit


# What the engine kernel actually produces below the expansion boundary:
# a repeatable unit and renewal sources, but no accumulation/phase fields.
_KERNEL_PROOF = {
    "repeatable_story_unit": "每替人验一具尸，就多知道一条不能说的规矩",
    "renewal_sources": ["新的委托来源", "对手层级升级"],
    "accumulation_tracks": [],
    "phase_transitions": [],
}


def test_boundary_constant_matches_the_generation_side() -> None:
    """One constant, so generation and validation cannot drift apart again."""

    assert SERIALITY_PROOF_REQUIRED_MIN_CHAPTERS == 200


def test_measurement_function_stays_a_pure_measurement() -> None:
    """``evaluate_seriality_capacity`` is deliberately NOT changed.

    It answers "what does this proof actually support?" — a kernel-only proof
    genuinely supports ~50 chapters. Corrupting the ruler to make a policy
    problem disappear would blind the ≥200 path too. The fix belongs in the
    consumer that turns a measurement into a book-killing violation.
    """

    report = evaluate_seriality_capacity(
        _KERNEL_PROOF, target_chapters=108, require_phase_coverage=True
    )

    assert not report.passed
    assert report.estimated_chapter_ceiling == 50


@pytest.mark.parametrize("chapters", [51, 54, 100, 108, 180, 199])
def test_mid_length_books_are_not_blocked_for_a_proof_never_requested(
    chapters: int,
) -> None:
    """THE dead band. Every one of these was a guaranteed kill."""

    contract = {
        "schema_version": "concept-contract.v2",
        "target_chapters": chapters,
        "seriality_proof": _KERNEL_PROOF,
    }

    capacity = [
        v
        for v in validate_concept_contract(contract, target_chapters=chapters)
        if "容量不足" in v
    ]

    assert not capacity, (
        f"{chapters} chapters blocked by {capacity} — the generator is not "
        f"asked for these fields below {SERIALITY_PROOF_REQUIRED_MIN_CHAPTERS}"
    )


@pytest.mark.parametrize("chapters", [10, 30, 50])
def test_short_books_still_pass(chapters: int) -> None:
    contract = {
        "schema_version": "concept-contract.v2",
        "target_chapters": chapters,
        "seriality_proof": _KERNEL_PROOF,
    }

    assert not [
        v
        for v in validate_concept_contract(contract, target_chapters=chapters)
        if "容量不足" in v
    ]


def test_long_books_still_require_real_proof() -> None:
    """No loosening where the proof IS generated: a 500-chapter claim backed by
    a kernel-only engine must still be rejected."""

    contract = {
        "schema_version": "concept-contract.v2",
        "target_chapters": 500,
        "seriality_proof": _KERNEL_PROOF,
    }

    assert [
        v
        for v in validate_concept_contract(contract, target_chapters=500)
        if "容量不足" in v
    ], "the ≥200 capacity bar must stay enforced"
