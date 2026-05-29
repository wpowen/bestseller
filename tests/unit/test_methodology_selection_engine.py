from __future__ import annotations

import json
from pathlib import Path

import pytest

from bestseller.services.methodology_selection_engine import (
    derive_chapter_role,
    load_methodology_inventory,
    select_methodology_lineage,
)


def _write_inventory(path: Path) -> None:
    rows = [
        {
            "rule_id": "wm.opening.first",
            "source": "writing_methodology",
            "title": "Opening first paragraph",
            "craft_function": "opening_three_function",
            "binding_stage": ["outline_chapter", "review"],
            "binding_artifact": ["chapter_outline[0].first_paragraph_spec"],
            "indicator_targets": ["combined_quality_score"],
            "text_snippet": "Start with time, place, character, and concrete trouble.",
            "coverage_status": "runtime_active",
            "similarity_cluster_id": "open",
        },
        {
            "rule_id": "wm.hook.balance",
            "source": "writing_methodology",
            "title": "Hook balance",
            "craft_function": "hook_ledger",
            "binding_stage": ["outline_chapter", "review"],
            "binding_artifact": ["hook_ledger.entries"],
            "indicator_targets": ["hook_ledger_closure_rate"],
            "text_snippet": "Plant and resolve hooks every chapter.",
            "coverage_status": "runtime_active",
            "similarity_cluster_id": "hook",
        },
        {
            "rule_id": "wm.causality.but",
            "source": "writing_methodology",
            "title": "But rule",
            "craft_function": "scene_causality_engine",
            "binding_stage": ["outline_chapter", "prose_scene", "review"],
            "binding_artifact": ["scene_contract.causal_chain"],
            "indicator_targets": ["scene_causality_score"],
            "text_snippet": "Use but/therefore causality.",
            "coverage_status": "runtime_active",
            "similarity_cluster_id": "cause",
        },
        {
            "rule_id": "wm.payoff.reveal",
            "source": "writing_methodology",
            "title": "Payoff reveal",
            "craft_function": "payoff_ledger",
            "binding_stage": ["outline_chapter", "review"],
            "binding_artifact": ["payoff_ledger.entries"],
            "indicator_targets": ["setup_payoff_score", "payoff_ledger_closure_rate"],
            "text_snippet": "Reveals need earlier setup.",
            "coverage_status": "runtime_active",
            "similarity_cluster_id": "payoff",
        },
        {
            "rule_id": "wm.pov.camera",
            "source": "writing_methodology",
            "title": "Camera pull",
            "craft_function": "pov_distance_controller",
            "binding_stage": ["outline_chapter", "prose_scene", "review"],
            "binding_artifact": ["scene_contract.pov_spec"],
            "indicator_targets": ["pov_stability_score"],
            "text_snippet": "Control camera distance through action.",
            "coverage_status": "runtime_dormant",
            "similarity_cluster_id": "pov",
        },
        {
            "rule_id": "wm.dialogue.subtext",
            "source": "writing_methodology",
            "title": "Subtext dialogue",
            "craft_function": "dialogue_subtext_engine",
            "binding_stage": ["outline_chapter", "prose_scene", "review"],
            "binding_artifact": ["scene.dialogue_spec"],
            "indicator_targets": ["dialogue_subtext_score"],
            "text_snippet": "Dialogue should carry hidden intent.",
            "coverage_status": "runtime_dormant",
            "similarity_cluster_id": "dialogue",
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )


@pytest.mark.unit
def test_select_methodology_lineage_is_deterministic(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.jsonl"
    _write_inventory(inventory)

    first = select_methodology_lineage(
        chapter_no=1,
        chapter_role="opening",
        genre_profile="fantasy",
        weak_indicators={},
        inventory_path=inventory,
    )
    second = select_methodology_lineage(
        chapter_no=1,
        chapter_role="opening",
        genre_profile="fantasy",
        weak_indicators={},
        inventory_path=inventory,
    )

    assert first == second
    assert [item.slot for item in first.selected[:3]] == [
        "opening_three_function",
        "hook_ledger",
        "scene_causality_engine",
    ]
    assert first.selection_seed == second.selection_seed


@pytest.mark.unit
def test_select_methodology_lineage_changes_slots_by_role(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.jsonl"
    _write_inventory(inventory)

    opening = select_methodology_lineage(
        chapter_no=1,
        chapter_role="opening",
        genre_profile="fantasy",
        inventory_path=inventory,
    )
    climax = select_methodology_lineage(
        chapter_no=12,
        chapter_role="climax",
        genre_profile="fantasy",
        inventory_path=inventory,
    )

    assert opening.selected[0].slot == "opening_three_function"
    assert climax.selected[0].slot == "payoff_ledger"
    assert opening.selection_seed != climax.selection_seed


@pytest.mark.unit
def test_select_methodology_lineage_respects_budget(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.jsonl"
    _write_inventory(inventory)

    lineage = select_methodology_lineage(
        chapter_no=8,
        chapter_role="setup",
        genre_profile="fantasy",
        budget_cards=2,
        inventory_path=inventory,
    )

    assert len(lineage.selected) == 2
    assert lineage.budget_cards == 2


@pytest.mark.unit
def test_weak_indicators_force_slot_into_lineage(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.jsonl"
    _write_inventory(inventory)

    lineage = select_methodology_lineage(
        chapter_no=1,
        chapter_role="opening",
        genre_profile="fantasy",
        budget_cards=4,
        weak_indicators={"setup_payoff_score": 0.4},
        inventory_path=inventory,
    )

    slots = [item.slot for item in lineage.selected]
    payoff = next(item for item in lineage.selected if item.slot == "payoff_ledger")
    assert "payoff_ledger" in slots
    assert payoff.gate_mode == "block"
    assert "reinforced" in payoff.why_selected


@pytest.mark.unit
def test_load_inventory_maps_ending_hook_to_hook_slot(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.jsonl"
    row = {
        "rule_id": "wm.hook.break",
        "source": "writing_methodology",
        "title": "Break chapter",
        "craft_function": "ending_hook_engine",
        "binding_stage": ["outline_chapter"],
        "binding_artifact": ["chapter_outline.ending_hook"],
        "indicator_targets": ["ending_hook_score"],
        "text_snippet": "Cut before the reveal.",
        "coverage_status": "runtime_active",
    }
    inventory.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")

    rules = load_methodology_inventory(inventory)

    assert rules[0].slot == "hook_ledger"


@pytest.mark.unit
def test_derive_chapter_role_defaults_opening_for_first_three_chapters() -> None:
    assert derive_chapter_role(chapter_no=3) == "opening"
    assert derive_chapter_role(chapter_no=12) == "climax"
