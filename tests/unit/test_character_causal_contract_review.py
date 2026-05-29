from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.reviews import (
    _chapter_contract_expectations,
    _methodology_lineage_evidence_summary,
)

pytestmark = pytest.mark.unit


def test_chapter_contract_expectations_include_character_and_causal_fields() -> None:
    expectations = _chapter_contract_expectations(
        chapter_contract=SimpleNamespace(
            contract_summary="沈砚追查暗门信号。",
            core_conflict=None,
            emotional_shift=None,
            information_release=None,
            closing_hook=None,
            conflict_stakes=None,
            conflict_buffs=[],
            pacing_mode=None,
            emotion_phase=None,
            hooks_to_resolve=[],
            hooks_to_plant=[],
            relationship_debts=[],
            character_delta="沈砚从旁观者变成承担调查责任的人。",
            protagonist_choice="沈砚选择进入暗门。",
            causal_contract={
                "pressure": "封港命令一小时后生效。",
                "visible_action_or_reaction": "他接下港务官的任务。",
                "next_reader_desire": "读者想知道第二枚印记是谁留下的。",
            },
            methodology_lineage=None,
        )
    )

    labels = {label for label, _ in expectations}
    assert "character_delta" in labels
    assert "protagonist_choice" in labels
    assert "causal_contract.pressure" in labels
    assert "causal_contract.next_reader_desire" in labels


def test_methodology_lineage_evidence_summary_attributes_scores_by_rule() -> None:
    chapter_contract = {
        "causal_contract": {
            "protagonist_choice": "沈砚选择进入暗门。",
            "next_reader_desire": "读者想知道第二枚印记是谁留下的。",
        },
        "methodology_lineage": {
            "chapter_no": 2,
            "genre_profile": "suspense",
            "chapter_role": "setup",
            "selection_seed": "seed",
            "budget_tokens": 900,
            "budget_cards": 6,
            "selected": [
                {
                    "rule_id": "wm.causality.choice",
                    "slot": "scene_causality_engine",
                    "craft_function": "scene_causality_engine",
                    "target_artifact_path": "causal_contract.protagonist_choice",
                    "application_hint": "Make the choice visible.",
                    "evidence_fields": [
                        "causal_contract.protagonist_choice",
                        "causal_contract.next_reader_desire",
                    ],
                    "verifiability": "heuristic",
                    "gate_mode": "warn",
                    "indicator_targets": ["scene_causality_score"],
                    "source_lineage": "writing_methodology",
                    "why_selected": "test",
                }
            ],
        },
    }

    evidence = _methodology_lineage_evidence_summary(
        "沈砚选择进入暗门。第二枚印记是谁留下的？",
        chapter_contract,
    )

    assert evidence["missing_rule_ids"] == []
    assert evidence["rules"][0]["rule_id"] == "wm.causality.choice"
    assert evidence["rules"][0]["fields"][0]["matched"] is True
