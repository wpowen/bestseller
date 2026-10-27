from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from bestseller.services.distilled_design_reference import (
    find_distilled_design_aggregate_dir,
    render_all_distilled_design_reference_blocks,
    render_distilled_design_reference_block,
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


def _aggregate(tmp_path: Path, key: str = "otherworld-cross-system") -> Path:
    root = tmp_path / "data" / "distillation" / "aggregates" / key
    _write_json(
        root / "aggregate_manifest.json",
        {
            "aggregate_key": key,
            "source_count": 3,
            "source_ids": ["source-0001", "source-0002", "source-0003"],
            "material_rows": 2,
            "mechanism_rows": 2,
        },
    )
    for source_id in ("source-0001", "source-0002", "source-0003"):
        package = tmp_path / "data" / "distillation" / source_id
        _write_json(
            package / "source_manifest.json",
            {
                "source_id": source_id,
                "source_hash_sha256": "a" * 64,
                "rights_status": "user_supplied_for_analysis",
            },
        )
        _write_json(package / "book_design_card.json", {"core_engine": "verified"})
        _write_json(package / "author_craft_card.json", {"dialogue_system": []})
        _write_json(package / "anti_copy_ledger.json", {})
        _write_jsonl(package / "volume_cards.jsonl", [{"volume_no": 1}])
        _write_jsonl(package / "mechanism_candidates.jsonl", [{"mechanism_id": "m1"}])
        _write_jsonl(package / "material_entries.review.jsonl", [{"dimension": "craft"}])
        (package / "grammar_patch.yaml").write_text("key: verified\n", encoding="utf-8")
    (root / "grammar_patch.yaml").write_text(
        yaml.safe_dump(
            {
                "key": key,
                "name": "异界跨体系规则套利",
                "state_variables": ["cross_system_understanding", "identity_debt"],
                "chapter_change_vectors": ["learn_local_rule", "exploit_rule_gap"],
                "reader_rewards": ["knowledge_arbitrage", "power_misread_payoff"],
                "hook_or_aftereffect_types": ["misread_reassessment"],
                "prompt_safe_fields": ["chapter_change_vectors"],
                "forbidden_defaults": ["copy_exact_opening"],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _write_jsonl(
        root / "mechanism_registry.jsonl",
        [
            {
                "mechanism_id": "cross-system-rule-arbitrage",
                "candidate_type": "plot_pattern",
                "summary": "用旧体系解释并绕开新世界规则, 每次收益都带来新关注。",
                "promotion_target": "material_library.plot_patterns",
                "reuse_scope": "abstract_only",
                "max_confidence": 0.91,
            },
            {
                "mechanism_id": "social-venue-world-display",
                "candidate_type": "scene_template",
                "summary": "用高互动社会场景展示规则, 同时制造资源或势力变化。",
                "promotion_target": "material_library.scene_templates",
                "reuse_scope": "abstract_only",
                "max_confidence": 0.82,
            },
        ],
    )
    _write_jsonl(
        root / "material_entries.active.jsonl",
        [
            {
                "dimension": "world_settings",
                "slug": "dual-rule-city",
                "name": "双规则城邦",
                "narrative_summary": "城市秩序由公开等级和隐藏职业规则共同决定。",
                "reuse_scope": "abstract_only",
                "confidence": 0.8,
            },
            {
                "dimension": "character_archetypes",
                "slug": "irreverent-specialist",
                "name": "冒犯权威的专业外来者",
                "narrative_summary": "主角用职业判断挑战本地权威, 也持续付出社会代价。",
                "reuse_scope": "abstract_only",
                "confidence": 0.86,
            },
        ],
    )
    _write_jsonl(
        root / "author_craft_registry.jsonl",
        [
            {
                "craft_observation_scope": "abstract craft_profile fields only",
                "style_safety_policy": "abstract_craft_only",
                "sentence_rhythm": ["tighten syntax under pressure"],
                "dialogue_system": ["对白必须带有冲突意图"],
                "description_strategy": ["只描写会改变判断的信息"],
                "exposition_strategy": ["由当前需要触发背景信息"],
                "hooking_and_transitions": ["段落以状态变化收束"],
            }
        ],
    )
    _write_json(
        root / "anti_copy_rules.json",
        {
            "source_ids": ["source-0001", "source-0002", "source-0003"],
            "blocked_combinations": ["specific_portal_accident"],
            "replacement_policy": ["更换职业来源和身份债"],
        },
    )
    return root


def test_render_craft_includes_abstract_rhythm_and_exposition(tmp_path: Path) -> None:
    _aggregate(tmp_path)

    block = render_distilled_design_reference_block(
        category_key="otherworld-cross-system",
        phase="craft",
        repo_root=tmp_path,
    )

    assert "tighten syntax under pressure" in block
    assert "由当前需要触发背景信息" in block


def test_find_distilled_design_aggregate_dir_resolves_category(tmp_path: Path) -> None:
    aggregate = _aggregate(tmp_path)

    found = find_distilled_design_aggregate_dir(
        category_key="otherworld-cross-system",
        repo_root=tmp_path,
    )

    assert found == aggregate


def test_render_distilled_design_reference_block_surfaces_design_not_source(tmp_path: Path) -> None:
    _aggregate(tmp_path)

    block = render_distilled_design_reference_block(
        category_key="otherworld-cross-system",
        phase="architecture",
        repo_root=tmp_path,
    )

    assert "小说设计参考" in block
    assert "来源核验不证明市场表现" in block
    assert "cross-system-rule-arbitrage" in block
    assert "cross_system_understanding" not in block
    assert "learn_local_rule" in block
    assert "不得复用源书专名" in block
    assert "specific_portal_accident" not in block


def test_render_all_distilled_design_reference_blocks_are_phase_specific(
    tmp_path: Path,
) -> None:
    _aggregate(tmp_path)

    blocks = render_all_distilled_design_reference_blocks(
        category_key="otherworld-cross-system",
        repo_root=tmp_path,
        phases=["world", "cast"],
    )

    assert "双规则城邦" in blocks["world"]
    assert "冒犯权威的专业外来者" not in blocks["world"]
    assert "冒犯权威的专业外来者" in blocks["cast"]


def test_source_bound_observations_never_render_without_reuse_marker(tmp_path: Path) -> None:
    root = _aggregate(tmp_path)
    _write_jsonl(
        root / "mechanism_registry.jsonl",
        [
            {
                "mechanism_id": "source-bound-confession-chain",
                "candidate_type": "plot_pattern",
                "summary": "A source-specific confession, trial, and restoration chain.",
                "promotion_target": "cross_project_library",
                "status": "review",
            }
        ],
    )
    _write_jsonl(
        root / "volume_design_paths.jsonl",
        [
            {
                "volume_no": 1,
                "arc_function": "A source-specific child narrator and court restoration arc.",
            }
        ],
    )
    _write_json(
        root / "anti_copy_rules.json",
        {"blocked_combinations": ["source_specific_trial_restoration"]},
    )

    block = render_distilled_design_reference_block(
        category_key="otherworld-cross-system",
        phase="architecture",
        repo_root=tmp_path,
    )

    assert "source-bound-confession-chain" not in block
    assert "source-specific child narrator" not in block
    assert "source_specific_trial_restoration" not in block


def test_generic_distillation_never_enters_generation_prompts(tmp_path: Path) -> None:
    root = _aggregate(tmp_path, key="distillation-generic")
    _write_jsonl(
        root / "volume_design_paths.jsonl",
        [
            {
                "volume_no": 1,
                "arc_function": "A debtor rises through repeated financial opportunities.",
            }
        ],
    )

    block = render_distilled_design_reference_block(
        category_key="action-progression",
        genre="仙侠",
        phase="architecture",
        repo_root=tmp_path,
    )

    assert block == ""


def test_unverified_zero_source_aggregate_never_enters_generation_prompts(
    tmp_path: Path,
) -> None:
    root = _aggregate(tmp_path)
    _write_json(
        root / "aggregate_manifest.json",
        {
            "aggregate_key": "otherworld-cross-system",
            "source_count": 0,
            "maturity_score": 0.6,
            "maturity_status": "review",
        },
    )

    block = render_distilled_design_reference_block(
        category_key="otherworld-cross-system",
        phase="volume_plan",
        repo_root=tmp_path,
    )

    assert block == ""


def test_anonymous_ids_without_source_manifests_never_enter_generation_prompts(
    tmp_path: Path,
) -> None:
    root = _aggregate(tmp_path)
    _write_json(
        root / "aggregate_manifest.json",
        {
            "aggregate_key": "otherworld-cross-system",
            "source_count": 2,
            "source_ids": ["anonymous-suspense-0001", "anonymous-suspense-0002"],
        },
    )
    assert (
        render_distilled_design_reference_block(
            category_key="otherworld-cross-system", repo_root=tmp_path
        )
        == ""
    )


@pytest.mark.parametrize(
    "source_id",
    ["source-../../escape", "/tmp/source-absolute", "source-evil..path"],
)
def test_source_id_path_escape_is_rejected(tmp_path: Path, source_id: str) -> None:
    root = _aggregate(tmp_path)
    _write_json(
        root / "aggregate_manifest.json",
        {
            "aggregate_key": "otherworld-cross-system",
            "source_count": 1,
            "source_ids": [source_id],
        },
    )
    assert (
        render_distilled_design_reference_block(
            category_key="otherworld-cross-system", repo_root=tmp_path
        )
        == ""
    )


def test_source_package_symlink_outside_root_is_rejected(tmp_path: Path) -> None:
    root = _aggregate(tmp_path)
    outside = tmp_path / "outside-source"
    outside.mkdir()
    (outside / "source_manifest.json").write_text(
        '{"source_id":"source-outside","source_hash_sha256":"'
        + "a" * 64
        + '","rights_status":"ok"}\n',
        encoding="utf-8",
    )
    package_link = tmp_path / "data" / "distillation" / "source-outside"
    package_link.symlink_to(outside, target_is_directory=True)
    _write_json(
        root / "aggregate_manifest.json",
        {
            "aggregate_key": "otherworld-cross-system",
            "source_count": 1,
            "source_ids": ["source-outside"],
        },
    )
    assert (
        render_distilled_design_reference_block(
            category_key="otherworld-cross-system", repo_root=tmp_path
        )
        == ""
    )
