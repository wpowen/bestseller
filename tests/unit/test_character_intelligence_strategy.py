from __future__ import annotations

import pytest

from bestseller.services.character_intelligence.strategy import (
    build_character_strategy_from_distillation,
    character_strategy_from_project_metadata,
)

pytestmark = pytest.mark.unit


def test_build_character_strategy_from_grammar_materials_and_craft() -> None:
    strategy = build_character_strategy_from_distillation(
        grammar={
            "key": "otherworld-cross-system",
            "required_contracts": [
                "Protagonist must demonstrate active agency through knowledge application "
                "within first three chapters",
                "Power system exposition must occur through active scene application.",
                "Identity crisis beats require external pressure forcing visible choice "
                "between predecessor loyalty and self-determination.",
                "Group formation must include reciprocal commitment moment.",
                "Power revelation sequences must create visible antagonist reaction.",
            ],
            "state_variables": [
                {"var_id": "knowledge_asymmetry"},
                {"var_id": "identity_integration"},
                {"var_id": "group_commitment"},
                {"var_id": "antagonist_reckoning"},
            ],
            "reader_rewards": ["Strategic satisfaction from information advantage"],
            "forbidden_defaults": [
                "Protagonist receiving information or power through passive reception.",
                "Identity crisis resolved through internal reflection without external pressure.",
            ],
        },
        material_entries=[
            {
                "dimension": "plot_patterns",
                "slug": "cross-system-rule-arbitrage",
                "narrative_summary": "Use old-world knowledge to exploit rule gaps.",
                "content_json": {
                    "state_variables": ["cross_system_understanding"],
                    "required_cost": "Every exploit increases exposure.",
                },
            },
            {
                "dimension": "plot_patterns",
                "slug": "host-identity-debt",
                "narrative_summary": "Inherited identity creates family and social obligations.",
                "content_json": {"state_variables": ["identity_debt"]},
            },
            {
                "dimension": "scene_templates",
                "slug": "local-expert-misreads-outsider",
                "narrative_summary": "Local expert misreads outsider methods and must reassess.",
            },
            {
                "dimension": "anti_cliche_patterns",
                "slug": "do-not-make-objectification-core-reward",
                "narrative_summary": "Do not make objectification the core reward loop.",
            },
        ],
        author_craft_entries=[
            {
                "dialogue_system": [
                    "reader_surrogate questions naturalize exposition",
                    "no single dialogue exchange exceeds three revelations without physical break",
                ]
            }
        ],
    )

    assert set(strategy["required_axes"]) >= {
        "agency",
        "identity_pressure",
        "relationship_debt",
        "antagonist_misread",
        "dialogue_function",
    }
    assert strategy["agency_policy"]["must_act_within_chapters"] == 3
    assert (
        "cross_system_rule_arbitrage" in strategy["agency_policy"]["default_problem_solving_modes"]
    )
    assert strategy["identity_pressure"]["required_external_pressure"] is True
    assert strategy["relationship_policy"]["reciprocal_commitment_required"] is True
    assert strategy["antagonist_policy"]["visible_reaction_required"] is True
    assert strategy["antagonist_policy"]["misread_payoff_required"] is True
    assert strategy["dialogue_policy"]["exposition_through_conflict"] is True
    assert strategy["dialogue_policy"]["reader_surrogate_questions_allowed"] is True
    assert strategy["dialogue_policy"]["max_revelations_before_break"] == 3
    assert any("passive reception" in item for item in strategy["risk_controls"])
    assert strategy["evidence"][0]["id"] == "otherworld-cross-system"


def test_character_strategy_from_project_metadata_prefers_explicit_strategy() -> None:
    card = {
        "aggregate_key": "otherworld-cross-system",
        "required_state_variables": ["identity_debt", "group_commitment"],
        "reader_reward_mix": ["Identity payoff with visible cost"],
        "character_design_paths": ["Host identity debt must become visible choice."],
        "selected_mechanisms": [
            {
                "mechanism_id": "host-identity-debt",
                "design_role": "character_pressure",
                "adaptation_instruction": "Bind inherited identity debt to current cast.",
            }
        ],
    }

    inferred = character_strategy_from_project_metadata({"distilled_strategy_card": card})

    assert "identity_pressure" in inferred["required_axes"]
    assert inferred["reader_reward_contracts"] == ["Identity payoff with visible cost"]
    assert inferred["evidence"][-1]["id"] == "host-identity-debt"

    explicit = character_strategy_from_project_metadata(
        {
            "character_strategy": {
                "required_axes": ["agency"],
                "agency_policy": {"choice_with_cost_required": False},
            },
            "distilled_strategy_card": card,
        }
    )

    assert explicit["required_axes"] == ["agency"]
    assert explicit["agency_policy"]["choice_with_cost_required"] is False
