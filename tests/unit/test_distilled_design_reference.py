from __future__ import annotations

import json
from pathlib import Path

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
            "material_rows": 2,
            "mechanism_rows": 2,
        },
    )
    (root / "grammar_patch.yaml").write_text(
        yaml.safe_dump(
            {
                "key": key,
                "name": "异界跨体系规则套利",
                "state_variables": ["cross_system_understanding", "identity_debt"],
                "chapter_change_vectors": ["learn_local_rule", "exploit_rule_gap"],
                "reader_rewards": ["knowledge_arbitrage", "power_misread_payoff"],
                "hook_or_aftereffect_types": ["misread_reassessment"],
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
                "max_confidence": 0.91,
            },
            {
                "mechanism_id": "social-venue-world-display",
                "candidate_type": "scene_template",
                "summary": "用高互动社会场景展示规则, 同时制造资源或势力变化。",
                "promotion_target": "material_library.scene_templates",
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
                "confidence": 0.8,
            },
            {
                "dimension": "character_archetypes",
                "slug": "irreverent-specialist",
                "name": "冒犯权威的专业外来者",
                "narrative_summary": "主角用职业判断挑战本地权威, 也持续付出社会代价。",
                "confidence": 0.86,
            },
        ],
    )
    _write_jsonl(
        root / "author_craft_registry.jsonl",
        [
            {
                "dialogue_system": ["对白必须带有冲突意图"],
                "description_strategy": ["只描写会改变判断的信息"],
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

    assert "成熟小说设计参考" in block
    assert "cross-system-rule-arbitrage" in block
    assert "cross_system_understanding" in block
    assert "不得复用源书专名" in block
    assert "specific_portal_accident" in block


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
