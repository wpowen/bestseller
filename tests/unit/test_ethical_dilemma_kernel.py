from __future__ import annotations

from uuid import uuid4

from pydantic import ValidationError
import pytest

from bestseller.domain.ethical_dilemma import EthicalDilemmaKernel, EthicalDilemmaSlot
from bestseller.domain.review import SceneReviewScores
from bestseller.services.ethical_dilemma_slot_gate import scan_ethical_dilemma_slots

pytestmark = pytest.mark.unit


def _slot() -> EthicalDilemmaSlot:
    return EthicalDilemmaSlot(
        chapter_window=(10, 12),
        dilemma_kind="law_vs_compassion",
        competing_values=("依法处置", "救下无辜者"),
        involved_characters=[uuid4()],
        intended_choice="A",
        consequence_for_unchosen="被放弃的一方在后续五章失去庇护。",
    )


def test_slot_must_have_competing_values() -> None:
    with pytest.raises(ValidationError):
        EthicalDilemmaSlot(
            chapter_window=(1, 2),
            dilemma_kind="one_vs_many",
            competing_values=("救人", "救人"),
            involved_characters=[uuid4()],
            intended_choice="open",
            consequence_for_unchosen="另一方付出代价。",
        )


def test_cadence_enforcement() -> None:
    report = scan_ethical_dilemma_slots(
        EthicalDilemmaKernel(slots=[], minimum_cadence_chapters=12),
        total_chapters=30,
        landed_chapters=[],
    )
    assert any(f.code == "dilemma_cadence_gap" for f in report.findings)


def test_consequence_echo() -> None:
    report = scan_ethical_dilemma_slots(
        EthicalDilemmaKernel(slots=[_slot()], minimum_cadence_chapters=12),
        total_chapters=30,
        landed_chapters=[10],
        consequence_echoes={10: []},
    )
    assert any(f.code == "dilemma_consequence_echo_missing" for f in report.findings)


def test_dilemma_review_evidence_link() -> None:
    scores = SceneReviewScores(
        overall=0.8,
        goal=0.8,
        conflict=0.8,
        conflict_clarity=0.8,
        emotion=0.8,
        emotional_movement=0.8,
        dialogue=0.8,
        style=0.8,
        hook=0.8,
        hook_strength=0.8,
        payoff_density=0.8,
        voice_consistency=0.8,
        character_voice_distinction=0.8,
        thematic_resonance=0.8,
        worldbuilding_integration=0.8,
        prose_variety=0.8,
        moral_complexity=0.8,
        contract_alignment=0.8,
        moral_complexity_evidence=["dilemma:slot-1"],
    )
    assert scores.moral_complexity_evidence == ["dilemma:slot-1"]

