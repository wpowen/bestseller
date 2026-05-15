from __future__ import annotations

import json
from pathlib import Path

import yaml

from bestseller.services.distilled_strategy_compiler import (
    compile_distilled_strategy_card,
    distilled_strategy_card_from_dict,
    distilled_strategy_card_to_dict,
    render_all_distilled_strategy_blocks,
    render_distilled_strategy_card_block,
)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _aggregate(tmp_path: Path) -> Path:
    root = tmp_path / "data" / "distillation" / "aggregates" / "otherworld-cross-system"
    root.mkdir(parents=True)
    _write_json(
        root / "aggregate_manifest.json",
        {
            "aggregate_key": "otherworld-cross-system",
            "source_ids": ["source-0001"],
            "source_count": 1,
            "material_rows": 4,
            "mechanism_rows": 5,
            "author_craft_rows": 1,
            "book_design_rows": 1,
            "volume_design_rows": 1,
            "fallback_volume_rows": 1,
            "maturity_score": 0.42,
            "maturity_status": "review",
            "anti_copy_blocked_combinations": 1,
            "grammar_state_variables": 2,
            "grammar_change_vectors": 2,
        },
    )
    (root / "grammar_patch.yaml").write_text(
        yaml.safe_dump(
            {
                "key": "otherworld-cross-system",
                "name": "异界跨体系规则套利",
                "state_variables": ["cross_system_understanding", "identity_debt"],
                "chapter_change_vectors": ["exploit_rule_gap", "pay_identity_debt"],
                "reader_rewards": ["knowledge_arbitrage", "power_misread_payoff"],
                "forbidden_defaults": ["copy_exact_opening"],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _write_json(
        root / "anti_copy_rules.json",
        {
            "blocked_combinations": ["exact-opening-chain"],
            "replacement_policy": ["change source-world profession"],
        },
    )
    _write_jsonl(
        root / "mechanism_registry.jsonl",
        [
            {
                "mechanism_id": "cross-system-rule-arbitrage",
                "candidate_type": "plot_pattern",
                "summary": "Use one rule system to exploit another.",
                "promotion_target": "material_library.plot_patterns",
                "max_confidence": 0.86,
            },
            {
                "mechanism_id": "dual-system-fusion-ladder",
                "candidate_type": "power_system",
                "summary": "Two rule systems become a measured escalation ladder.",
                "promotion_target": "material_library.power_systems",
                "max_confidence": 0.83,
            },
            {
                "mechanism_id": "professional-symbol-decoding",
                "candidate_type": "scene_template",
                "summary": "Decode a location into a long-term mystery.",
                "promotion_target": "material_library.scene_templates",
                "max_confidence": 0.76,
            },
            {
                "mechanism_id": "forbid-source-specific-opening-copy",
                "candidate_type": "anti_cliche",
                "summary": "Opening must be transformed.",
                "promotion_target": "material_library.anti_cliche_patterns",
                "max_confidence": 0.9,
            },
        ],
    )
    _write_jsonl(
        root / "material_entries.active.jsonl",
        [
            {
                "dimension": "scene_templates",
                "slug": "professional-symbol-decoding",
                "name": "专业符号破译",
                "narrative_summary": (
                    "Use professional knowledge to turn place detail into mystery."
                ),
                "content_json": {"state_variables": ["higher_authority_attention"]},
            },
            {
                "dimension": "power_systems",
                "slug": "dual-system-fusion-ladder",
                "name": "双体系融合阶梯",
                "narrative_summary": "Parallel systems create new ability thresholds.",
                "content_json": {"state_variables": ["old_system_adaptation"]},
            },
        ],
    )
    _write_jsonl(
        root / "author_craft_registry.jsonl",
        [
            {
                "dialogue_system": ["conflict-loaded dialogue"],
                "description_strategy": ["stakes-relevant detail"],
                "hooking_and_transitions": ["changed-state endings"],
            }
        ],
    )
    _write_jsonl(root / "book_design_registry.jsonl", [{"source_id": "source-0001"}])
    _write_jsonl(
        root / "volume_design_paths.jsonl",
        [
            {
                "volume_no": 1,
                "arc_function": "Fallback aggregation due LLM structure issue.",
                "distillation_fallback": True,
            },
            {
                "volume_no": 2,
                "arc_function": "Rules shift from local survival to political attention.",
                "dominant_engine": "world_pressure",
            },
        ],
    )
    return root


def test_compile_distilled_strategy_card_selects_project_bound_mechanisms(
    tmp_path: Path,
) -> None:
    _aggregate(tmp_path)

    card = compile_distilled_strategy_card(
        category_key="otherworld-cross-system",
        genre="异界",
        sub_genre="cross-system",
        project_context={"unique_hook": "主角用失效航图修复异界法则"},
        repo_root=tmp_path,
    )

    assert card is not None
    assert card.aggregate_key == "otherworld-cross-system"
    assert card.maturity_score == 0.42
    assert card.maturity_status == "review"
    roles = {item.design_role for item in card.selected_mechanisms}
    assert {"series_engine", "world_pressure", "chapter_rhythm", "anti_cliche"} <= roles
    assert "cross_system_understanding" in card.required_state_variables
    assert "higher_authority_attention" in card.required_state_variables
    assert "exploit_rule_gap" in card.required_change_vectors
    assert any("主角用失效航图" in item for item in card.transformation_requirements)
    assert any("exact-opening-chain" in item for item in card.anti_copy_boundaries)
    assert "Fallback aggregation" not in "\n".join(card.volume_design_paths)
    assert any("political attention" in item for item in card.volume_design_paths)


def test_compile_distilled_strategy_card_includes_world_mechanism_bindings(
    tmp_path: Path,
) -> None:
    _aggregate(tmp_path)

    card = compile_distilled_strategy_card(
        category_key="otherworld-cross-system",
        project_context={"unique_hook": "主角用失效航图修复异界法则"},
        repo_root=tmp_path,
    )
    assert card is not None

    binding_ids = {
        item["mechanism_id"] for item in card.world_mechanism_bindings
    }
    state_keys = {
        item["key"] for item in card.worldview_bindings["state_variables"]
    }
    world_block = render_distilled_strategy_card_block(card, phase="world")

    assert "dual-system-fusion-ladder" in binding_ids
    assert "cross_system_understanding" in state_keys
    assert "old_system_adaptation" in state_keys
    assert "dual-system-fusion-ladder" in world_block
    assert "cross_system_understanding" in world_block


def test_distilled_strategy_card_serializes_and_renders_phase_blocks(tmp_path: Path) -> None:
    _aggregate(tmp_path)
    card = compile_distilled_strategy_card(
        category_key="otherworld-cross-system",
        project_context={"reader_promise": "知识差制造持续误判和代价"},
        repo_root=tmp_path,
    )
    assert card is not None

    payload = distilled_strategy_card_to_dict(card)
    restored = distilled_strategy_card_from_dict(payload)
    block = render_distilled_strategy_card_block(restored, phase="volume_plan")
    blocks = render_all_distilled_strategy_blocks(restored)

    assert "蒸馏策略卡" in block
    assert "反抄袭边界" in block
    assert "Rules shift" in block
    assert "chapter_outline" in blocks
    assert "专业符号破译" in blocks["chapter_outline"]


def test_compile_distilled_strategy_card_returns_none_without_aggregate(tmp_path: Path) -> None:
    card = compile_distilled_strategy_card(
        category_key="missing-category",
        repo_root=tmp_path,
    )

    assert card is None
