from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from bestseller.services.methodology_cards import (
    MethodologyCardDeck,
    MethodologySourceSet,
    load_methodology_cards,
    load_methodology_source_set,
    methodology_coverage_summary,
    validate_card_sources,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PLOVA_MANIFEST = REPO_ROOT / "data" / "methodology_sources" / "plova" / "manifest.yaml"
PLOVA_CARDS = REPO_ROOT / "data" / "methodology_sources" / "plova" / "cards.yaml"


def _write_yaml(path: Path, content: str) -> Path:
    path.write_text(dedent(content).strip() + "\n", encoding="utf-8")
    return path


def test_plova_cards_cover_all_verified_ocr_sources() -> None:
    source_set = load_methodology_source_set(PLOVA_MANIFEST)
    deck = load_methodology_cards(PLOVA_CARDS)

    assert validate_card_sources(deck, source_set) == ()

    summary = methodology_coverage_summary(deck, source_set)
    assert summary["source_items"] == 38
    assert summary["verified_sources"] == 35
    assert summary["pending_sources"] == 3
    assert summary["pending_source_ids"] == ["plova.36", "plova.37", "plova.38"]
    assert summary["cards"] == 35
    assert summary["verified_cards"] == 35
    assert summary["pending_cards"] == 0
    assert summary["covered_source_count"] == 35
    assert summary["uncovered_verified_source_ids"] == []
    assert summary["unknown_source_ids"] == []
    assert summary["verified_source_coverage_ratio"] == pytest.approx(1.0)
    assert all(card.framework_bindings for card in deck.cards)
    assert all(card.scope and card.stage and card.category for card in deck.cards)


def test_card_deck_query_helpers_return_expected_methodology_slices() -> None:
    deck = load_methodology_cards(PLOVA_CARDS)

    opening_ids = {card.id for card in deck.cards_by_category("opening")}
    assert {
        "plova.opening.anti_pitfall",
        "plova.opening.three_chapter_function",
        "plova.opening.reader_desire_over_noise",
    } <= opening_ids

    action_structure = deck.get_card("plova.action_scene.structure")
    assert action_structure.source_ids == ("plova.11",)
    assert "scene" in action_structure.scope
    assert "planning" in action_structure.stage
    assert action_structure.gate_bindings[0].gate == "action_scene_structure"
    assert deck.cards_for_source("plova.11") == (action_structure,)

    review_cards = {card.id for card in deck.cards_for_stage("review")}
    assert "plova.chekhov.emphasized_item_must_function" in review_cards
    assert "plova.longform.after_100k_chaos" in review_cards


def test_validate_card_sources_reports_unknown_and_pending_sources() -> None:
    source_set = MethodologySourceSet.model_validate(
        {
            "source_set_id": "test",
            "author": "tester",
            "source_markdown": "test.md",
            "total_items": 2,
            "ocr_items": 1,
            "pending_items": 1,
            "total_images": 1,
            "ocr_images": 1,
            "items": [
                {
                    "source_id": "plova.01",
                    "aweme_id": "1",
                    "title": "verified",
                    "image_count": 1,
                    "ocr_image_count": 1,
                    "ocr_status": "ok",
                },
                {
                    "source_id": "plova.02",
                    "aweme_id": "2",
                    "title": "pending",
                    "image_count": 0,
                    "ocr_image_count": 0,
                    "ocr_status": "pending",
                },
            ],
        }
    )
    deck = MethodologyCardDeck.model_validate(
        {
            "cards": [
                {
                    "id": "plova.test.unknown",
                    "source_ids": ["plova.99"],
                    "title": "unknown",
                    "category": "mainline",
                    "scope": ["chapter"],
                    "stage": ["review"],
                    "core_claim": "unknown source should be reported",
                    "framework_bindings": ["test.binding"],
                    "maturity": "verified",
                },
                {
                    "id": "plova.test.pending",
                    "source_ids": ["plova.02"],
                    "title": "pending",
                    "category": "mainline",
                    "scope": ["chapter"],
                    "stage": ["review"],
                    "core_claim": "pending source should not back a verified card",
                    "framework_bindings": ["test.binding"],
                    "maturity": "verified",
                },
            ]
        }
    )

    findings = validate_card_sources(deck, source_set)
    assert {
        (finding.code, finding.card_id, finding.source_id) for finding in findings
    } >= {
        ("METHODOLOGY_CARD_SOURCE_MISSING", "plova.test.unknown", "plova.99"),
        ("METHODOLOGY_CARD_PENDING_SOURCE_VERIFIED", "plova.test.pending", "plova.02"),
        ("METHODOLOGY_VERIFIED_SOURCE_UNCOVERED", None, "plova.01"),
    }


def test_loader_rejects_duplicate_card_ids(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path / "cards.yaml",
        """
        cards:
          - id: plova.test.duplicate
            source_ids: ["plova.01"]
            title: "first"
            category: mainline
            scope: ["chapter"]
            stage: ["review"]
            core_claim: "first"
            framework_bindings: ["test.binding"]
          - id: plova.test.duplicate
            source_ids: ["plova.02"]
            title: "second"
            category: mainline
            scope: ["chapter"]
            stage: ["review"]
            core_claim: "second"
            framework_bindings: ["test.binding"]
        """,
    )

    with pytest.raises(ValueError, match="unique"):
        load_methodology_cards(path)


def test_loader_rejects_cards_missing_required_fields(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path / "cards.yaml",
        """
        cards:
          - id: plova.test.missing_scope
            source_ids: ["plova.01"]
            title: "missing scope"
            category: mainline
            stage: ["review"]
            core_claim: "scope is required"
            framework_bindings: ["test.binding"]
        """,
    )

    with pytest.raises(ValueError, match="scope"):
        load_methodology_cards(path)
