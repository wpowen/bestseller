from __future__ import annotations

import json
from pathlib import Path

import yaml

from bestseller.services.methodology_book_baseline import (
    default_baseline_suite,
    write_default_baseline_metric_spec,
)
from bestseller.services.methodology_book_corpus import (
    load_book_methodology_corpus,
    write_book_methodology_analysis,
)


def _write_source(root: Path, source_key: str) -> None:
    source_dir = root / source_key
    source_dir.mkdir(parents=True)
    cards = {
        "cards": [
            {
                "id": f"writing_books.{source_key}.sec-0001.scene_contract",
                "source_ids": [f"{source_key}.sec-0001"],
                "title": "Scene Contract",
                "category": "scene_design",
                "scope": ["scene"],
                "stage": ["drafting", "review"],
                "core_claim": "A scene should expose goal, obstacle, action, cost, and result.",
                "anti_patterns": ["Scene only explains background."],
                "required_contract_fields": ["goal", "obstacle", "result"],
                "framework_bindings": ["scene_card", "chapter_review"],
                "gate_bindings": [
                    {"gate": "scene_contract_validation", "default_mode": "advisory"}
                ],
                "maturity": "draft",
            },
            {
                "id": f"writing_books.{source_key}.sec-0001.author_process",
                "source_ids": [f"{source_key}.sec-0001"],
                "title": "Author Process",
                "category": "mainline",
                "scope": ["project_health"],
                "stage": ["planning"],
                "core_claim": "A writer should find a process that preserves flow state.",
                "anti_patterns": [],
                "required_contract_fields": ["intuition", "flow_state"],
                "framework_bindings": ["methodology_compiler"],
                "gate_bindings": [],
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
                "source_id": source_key,
                "section_id": "sec-0001",
                "confidence": 0.91,
                "alignment_terms": ["goal-obstacle-result"],
                "operating_steps": ["Bind scene goal", "Check result"],
                "conflicts_with": [],
            },
            {
                "candidate_id": "author_process",
                "source_id": source_key,
                "section_id": "sec-0001",
                "confidence": 0.61,
                "alignment_terms": ["revision_pass"],
                "operating_steps": [],
                "conflicts_with": [],
            },
        ]
    }
    (source_dir / "methodology_candidates.review.jsonl").write_text(
        json.dumps(candidates, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def test_load_book_methodology_corpus_merges_cards_and_candidate_signals(tmp_path: Path) -> None:
    _write_source(tmp_path, "source-9001")

    corpus = load_book_methodology_corpus(tmp_path)
    inventory = corpus.inventory()

    assert len(corpus.cards) == 2
    assert inventory.total_cards == 2
    assert inventory.domain_counts["scene_causality"] == 1
    assert inventory.verifiability_counts["strict"] == 1
    assert inventory.verifiability_counts["advisory_only"] == 1
    assert inventory.low_confidence_cards == (
        "writing_books.source-9001.sec-0001.author_process",
    )


def test_write_book_methodology_analysis_outputs_inventory_and_clusters(tmp_path: Path) -> None:
    _write_source(tmp_path, "source-9001")

    inventory_path, clusters_path = write_book_methodology_analysis(root=tmp_path)

    payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    clusters = yaml.safe_load(clusters_path.read_text(encoding="utf-8"))

    assert payload["total_cards"] == 2
    assert payload["prompt_cost_estimate"]["three_card_injection_tokens"] > 0
    assert clusters["scene_causality"]["count"] == 1


def test_default_baseline_metric_spec_is_serializable(tmp_path: Path) -> None:
    suite = default_baseline_suite()
    path = write_default_baseline_metric_spec(tmp_path / "baseline_metric_spec.yaml")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert suite.minimum_projects == 3
    assert payload["suite_id"] == "book_methodology_baseline_v1"
    assert {item["metric_id"] for item in payload["metrics"]} >= {
        "scene_causality_completeness",
        "prompt_token_delta",
    }
