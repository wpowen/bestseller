from __future__ import annotations

import json
from pathlib import Path

import yaml

from bestseller.services.methodology_book_selector import (
    BookMethodologySelectionContext,
    select_book_methodology_cards,
)


def _write_card_source(root: Path) -> None:
    source_dir = root / "source-9001"
    source_dir.mkdir(parents=True)
    cards = {
        "cards": [
            {
                "id": "writing_books.source-9001.sec-0001.scene_contract",
                "source_ids": ["source-9001.sec-0001"],
                "title": "Scene Contract",
                "category": "scene_design",
                "scope": ["scene", "chapter"],
                "stage": ["drafting", "review", "planning"],
                "core_claim": "Each scene must expose goal, obstacle, action, cost, and result.",
                "anti_patterns": [],
                "required_contract_fields": ["goal", "obstacle", "result"],
                "framework_bindings": ["scene_card"],
                "gate_bindings": [
                    {"gate": "scene_contract_validation", "default_mode": "advisory"}
                ],
                "maturity": "draft",
            },
            {
                "id": "writing_books.source-9001.sec-0001.prose_detail",
                "source_ids": ["source-9001.sec-0001"],
                "title": "Concrete Detail",
                "category": "prose_style",
                "scope": ["scene"],
                "stage": ["drafting", "review"],
                "core_claim": (
                    "Use concrete action and sensory evidence instead of abstract "
                    "explanation."
                ),
                "anti_patterns": [],
                "required_contract_fields": ["sensory_detail"],
                "framework_bindings": ["draft_prompt"],
                "gate_bindings": [],
                "maturity": "draft",
            },
            {
                "id": "writing_books.source-9001.sec-0001.payoff_bridge",
                "source_ids": ["source-9001.sec-0001"],
                "title": "Payoff Bridge",
                "category": "foreshadowing",
                "scope": ["scene", "chapter"],
                "stage": ["drafting", "planning", "review"],
                "core_claim": "Every visible clue should create or close a setup/payoff loop.",
                "anti_patterns": [],
                "required_contract_fields": ["setup", "payoff"],
                "framework_bindings": ["hook_ledger", "payoff_ledger"],
                "gate_bindings": [
                    {"gate": "setup_payoff_validation", "default_mode": "advisory"}
                ],
                "maturity": "draft",
            },
        ]
    }
    (source_dir / "methodology_cards.review.yaml").write_text(
        yaml.safe_dump(cards, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    candidates = {
        "candidates": [
            {
                "candidate_id": "scene_contract",
                "source_id": "source-9001",
                "section_id": "sec-0001",
                "confidence": 0.95,
                "alignment_terms": ["goal-obstacle-result"],
            },
            {
                "candidate_id": "prose_detail",
                "source_id": "source-9001",
                "section_id": "sec-0001",
                "confidence": 0.9,
                "alignment_terms": ["show-don't-tell"],
            },
            {
                "candidate_id": "payoff_bridge",
                "source_id": "source-9001",
                "section_id": "sec-0001",
                "confidence": 0.82,
                "alignment_terms": ["setup/payoff"],
            },
        ]
    }
    (source_dir / "methodology_candidates.review.jsonl").write_text(
        json.dumps(candidates, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def test_selector_prefers_stage_scope_and_renders_lineage(tmp_path: Path) -> None:
    _write_card_source(tmp_path)

    selection = select_book_methodology_cards(
        BookMethodologySelectionContext(
            stage="prose_scene",
            scope="scene",
            max_cards=2,
        ),
        root=tmp_path,
    )

    assert selection.card_ids[0] == "books_core.source-9001.sec-0001.scene_contract"
    block = selection.render_prompt_block()
    assert "书籍方法论选卡" in block
    assert "books_core.source-9001.sec-0001.scene_contract" in block
    assert "落点" in block


def test_selected_card_can_build_application_contract_entry(tmp_path: Path) -> None:
    _write_card_source(tmp_path)

    selection = select_book_methodology_cards(
        BookMethodologySelectionContext(stage="outline_chapter", scope="chapter"),
        root=tmp_path,
    )
    application = selection.cards[0].to_application(node_path="chapter.methodology_contract")

    assert application["profile_id"] == "books_core_v1"
    assert application["source_card_id"] == "writing_books.source-9001.sec-0001.scene_contract"
    assert application["mode"] == "audit_only"
    assert application["required_contract_fields"] == ["goal", "obstacle", "result"]


def test_selector_uses_quality_deficit_domains_before_general_stage_bias(
    tmp_path: Path,
) -> None:
    _write_card_source(tmp_path)

    selection = select_book_methodology_cards(
        BookMethodologySelectionContext(
            stage="prose_scene",
            scope="scene",
            max_cards=2,
            project_context={"metric_scores": {"setup_payoff_score": 0.4}},
        ),
        root=tmp_path,
    )

    assert selection.deficit_domains == ("setup_payoff",)
    assert selection.cards[0].canonical_domain == "setup_payoff"
    assert selection.card_ids[0] == "books_core.source-9001.sec-0001.payoff_bridge"
