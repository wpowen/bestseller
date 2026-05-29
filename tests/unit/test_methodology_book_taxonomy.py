from __future__ import annotations

from bestseller.services.methodology_book_taxonomy import (
    canonical_domain_for_card,
    infer_verifiability,
    normalized_claim_key,
    taxonomy_for_card,
)
from bestseller.services.methodology_cards import MethodologyCard, MethodologyGateBinding


def _card(**overrides: object) -> MethodologyCard:
    payload = {
        "id": "writing_books.source-9001.sec-0001.test_card",
        "source_ids": ("source-9001.sec-0001",),
        "title": "Scene causality",
        "category": "scene_design",
        "scope": ("scene",),
        "stage": ("drafting",),
        "core_claim": "A scene should expose goal, obstacle, action, cost, and result.",
        "anti_patterns": (),
        "required_contract_fields": ("goal", "obstacle", "result"),
        "framework_bindings": ("scene_card",),
        "gate_bindings": (
            MethodologyGateBinding(gate="scene_contract_validation", default_mode="advisory"),
        ),
        "maturity": "draft",
    }
    payload.update(overrides)
    return MethodologyCard.model_validate(payload)


def test_canonical_domain_maps_category_and_alignment_terms() -> None:
    assert canonical_domain_for_card(_card(category="character")) == "character_arc"
    assert (
        canonical_domain_for_card(
            _card(category="longform_control"),
            alignment_terms=("setup/payoff",),
        )
        == "project_health"
    )


def test_verifiability_marks_contract_fields_as_strict() -> None:
    verifiability, reason = infer_verifiability(_card())

    assert verifiability == "strict"
    assert "structural" in reason or "contract" in reason


def test_verifiability_keeps_author_process_cards_advisory_only() -> None:
    card = _card(
        category="mainline",
        required_contract_fields=("intuition", "flow_state", "creative_impulse"),
        gate_bindings=(),
    )

    verifiability, reason = infer_verifiability(card)

    assert verifiability == "advisory_only"
    assert "author process" in reason


def test_taxonomy_assigns_book_core_priority_only_to_observable_cards() -> None:
    strict_card = _card()
    advisory_card = _card(required_contract_fields=("intuition",), gate_bindings=())

    assert taxonomy_for_card(strict_card, core_deck=True).priority_lane.value == "book_core_deck"
    assert taxonomy_for_card(advisory_card, core_deck=True).priority_lane.value == "book_advisory"


def test_normalized_claim_key_clusters_punctuation_variants() -> None:
    left = normalized_claim_key("A story should expose goal, obstacle, and result.")
    right = normalized_claim_key("Story exposes: goal / obstacle / result!")

    assert left == right
