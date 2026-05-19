from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from bestseller.services.distillation_book_aggregator import (
    aggregate_source_package_async,
    batch_chapter_cards,
    infer_aggregate_key,
    material_passes_active_gate,
    package_book_phase_complete,
    promote_review_rows_to_active,
)


def test_infer_aggregate_key_otherworld() -> None:
    assert infer_aggregate_key({"genre_hint": "异界穿越"}) == "otherworld-cross-system"


def test_infer_aggregate_key_base_building() -> None:
    assert infer_aggregate_key({"genre_hint": "基建经营"}) == "base-building"


def test_infer_aggregate_key_eastern_aesthetic() -> None:
    assert infer_aggregate_key({"genre_hint": "东方美学国风"}) == "eastern-aesthetic"


def test_infer_aggregate_key_prefers_distillation_bucket() -> None:
    assert (
        infer_aggregate_key(
            {
                "genre_hint": "异界穿越",
                "distillation_genre_bucket": "suspense-mystery",
            }
        )
        == "suspense-mystery"
    )


def test_material_active_gate_privacy_scans_slug() -> None:
    ledger = {"blocked_categories": [], "blocked_combinations": [], "replacement_policy": []}
    row = {
        "slug": "mech-/Users/leak",
        "name": "机制",
        "narrative_summary": "抽象机制：用状态变量驱动冲突，不复述剧情。",
        "confidence": 0.9,
        "content_json": {"distillation_source_ids": ["source-0001"], "state_variables": ["x"]},
    }
    ok, reason = material_passes_active_gate(row, anti_copy_ledger=ledger)
    assert ok is False
    assert reason is not None
    assert "privacy_or_anti_copy" in reason


def test_batch_chapter_cards_sizes() -> None:
    cards = [{"abs_chapter_no": i, "source_id": "source-t"} for i in range(1, 46)]
    batches = batch_chapter_cards(cards, batch_min=20, batch_max=30)
    assert sum(len(b) for b in batches) == 45
    assert all(20 <= len(b) <= 30 for b in batches[:-1]) or len(batches[-1]) <= 30


def test_material_active_gate_blocks_low_confidence() -> None:
    ledger = {"blocked_categories": [], "blocked_combinations": [], "replacement_policy": []}
    row = {
        "narrative_summary": "抽象机制：用状态变量驱动冲突，不复述剧情。",
        "confidence": 0.5,
        "content_json": {"distillation_source_ids": ["source-0001"], "state_variables": ["x"]},
    }
    ok, reason = material_passes_active_gate(row, anti_copy_ledger=ledger)
    assert ok is False
    assert reason == "confidence_below_threshold"


def test_promote_splits_active() -> None:
    ledger = {"blocked_categories": [], "blocked_combinations": [], "replacement_policy": []}
    rows = [
        {
            "dimension": "plot_patterns",
            "slug": "mech-a",
            "name": "机制A",
            "narrative_summary": "用状态变量与代价约束组织冲突，不展开具体剧情。",
            "confidence": 0.9,
            "content_json": {"distillation_source_ids": [], "state_variables": ["s1"]},
        }
    ]
    active, rej = promote_review_rows_to_active(rows, anti_copy_ledger=ledger, source_id="source-0009")
    assert len(active) == 1
    assert active[0]["status"] == "active"
    assert active[0]["content_json"]["distillation_source_ids"] == ["source-0009"]
    assert not rej


def test_package_book_phase_complete(tmp_path: Path) -> None:
    root = tmp_path / "source-0999"
    root.mkdir()
    for name, body in (
        ("volume_cards.jsonl", '{"x":1}\n'),
        ("book_design_card.json", "{}"),
        ("author_craft_card.json", "{}"),
        ("mechanism_candidates.jsonl", "{}\n"),
        ("material_entries.review.jsonl", "{}\n"),
        ("anti_copy_ledger.json", "{}"),
        ("grammar_patch.yaml", "key: k\n"),
    ):
        (root / name).write_text(body, encoding="utf-8")
    assert package_book_phase_complete(root) is True


def test_coerce_book_design_card_fills_required_defaults() -> None:
    from bestseller.services import distillation_book_aggregator as mod

    row = mod._coerce_book_design_card(
        {},
        source_id="source-0003",
        source_ref="distillation:abc123",
    )

    assert row["book_id"] == "source-0003"
    assert row["source_ref"] == "distillation:abc123"
    assert row["source_type"] == "distillation_package"
    assert row["status"] == "draft_review"
    assert isinstance(row["parsed_profile"], dict)
    assert row["genre_tags"]
    assert row["core_engine"] == "unknown"
    assert row["state_variables"]


@pytest.mark.asyncio
async def test_aggregate_source_package_async_with_mock_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bestseller.services import distillation_book_aggregator as mod

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    schema_dir = repo_root / "data/distillation/schemas"
    schema_dir.mkdir(parents=True)
    (schema_dir / "chapter_card.schema.json").write_text(
        json.dumps(
            {
                "required": [
                    "source_id",
                    "abs_chapter_no",
                    "chapter_function",
                    "state_changes",
                    "reader_rewards",
                    "open_hooks",
                    "reusable_mechanisms",
                    "non_reusable_specifics",
                    "risk_flags",
                    "confidence",
                ]
            }
        ),
        encoding="utf-8",
    )

    pkg = repo_root / "data/distillation/source-0998"
    pkg.mkdir(parents=True)
    (pkg / "source_manifest.json").write_text(
        json.dumps(
            {
                "source_id": "source-0998",
                "source_hash_sha256": "a" * 64,
                "genre_hint": "玄幻",
                "encoding": "utf-8",
                "redaction_policy": {
                    "store_source_title_in_repo": False,
                    "store_author_in_repo": False,
                    "store_raw_text_in_repo": False,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (pkg / "chapters.index.json").write_text(
        json.dumps({"chapter_count": 1, "chapters": [{"abs_chapter_no": 1}]}),
        encoding="utf-8",
    )

    cc = {
        "source_id": "source-0998",
        "abs_chapter_no": 1,
        "volume_no": 1,
        "chapter_function": "establish abstract conflict axes",
        "state_changes": [
            {"axis": "tension", "before": "low", "after": "rising", "story_value": "curiosity"}
        ],
        "reader_rewards": ["pattern_payoff"],
        "open_hooks": ["mechanism_debt"],
        "reusable_mechanisms": ["state_variable_pressure"],
        "non_reusable_specifics": ["source_specific_named_entity"],
        "risk_flags": [],
        "confidence": 0.81,
    }
    (pkg / "chapter_cards.jsonl").write_text(json.dumps(cc, ensure_ascii=False) + "\n", encoding="utf-8")

    calls: list[str] = []

    async def fake_complete_json(*_a: Any, user_prompt: str, **_k: Any) -> Any:
        if "DISTILLATION_TASK: volume_card" in user_prompt:
            calls.append("volume")
            return {
                "source_id": "source-0998",
                "volume_no": 1,
                "chapter_range": "1-1",
                "arc_function": "arc",
                "dominant_engine": "engine",
                "state_progression": ["s1"],
                "turning_points": [{"abs_chapter_no": 1, "function": "turn"}],
                "setup_payoff_rhythm": "tight",
                "reusable_mechanisms": ["m1"],
                "failure_modes": ["f1"],
            }
        if "DISTILLATION_TASK: book_design_card" in user_prompt:
            calls.append("book")
            return {
                "book_id": "source-0998",
                "source_ref": "distillation:aaaaaaaaaaaaaaaa",
                "source_type": "distillation_package",
                "status": "draft_review",
                "parsed_profile": {"chapter_count": 1, "volume_count": 1, "encoding": "utf-8"},
                "genre_tags": ["玄幻"],
                "reader_promise": "抽象升级与规则代价。",
                "core_engine": "状态变量驱动冲突。",
                "state_variables": ["sv1"],
                "reader_rewards": ["r1"],
                "reusable_mechanisms": ["m1"],
                "non_reusable_specifics": ["named_entities_forbidden"],
                "risk_patterns": ["rp1"],
            }
        if "DISTILLATION_TASK: author_craft_card" in user_prompt:
            calls.append("craft")
            return {
                "source_id": "source-0998",
                "source_type": "distillation_package",
                "status": "draft_review",
                "style_safety_policy": "abstract craft only; no author imitation",
                "pov_and_distance": "close third with controlled access",
                "sentence_rhythm": ["short action beats", "medium causal explanation"],
                "paragraphing": ["one turn per paragraph"],
                "dialogue_system": ["conflict-loaded dialogue"],
                "description_strategy": ["stakes-relevant sensory detail"],
                "exposition_strategy": ["explain after visible need"],
                "emotional_temperature": ["contained pressure"],
                "hooking_and_transitions": ["changed-state endings"],
                "adaptation_guidelines": ["change imagery, cast, and scenario chains"],
                "taboo_copy_signals": ["exact phrases", "named entities"],
                "confidence": 0.83,
            }
        if "DISTILLATION_TASK: book_tail_bundle" in user_prompt:
            calls.append("tail")
            return {
                "mechanism_candidates": [
                    {
                        "source_id": "source-0998",
                        "mechanism_id": "mech-1",
                        "candidate_type": "plot_pattern",
                        "summary": "抽象机制说明。",
                        "evidence_scope": "chapters-1-1",
                        "promotion_target": "material_library.plot_patterns",
                        "status": "review",
                        "confidence": 0.85,
                    }
                ],
                "anti_copy_ledger": {
                    "source_id": "source-0998",
                    "blocked_categories": [{"category": "named_chain", "policy": "block"}],
                    "blocked_combinations": [],
                    "replacement_policy": ["use_role_labels"],
                },
                "material_entries_review": [
                    {
                        "dimension": "plot_patterns",
                        "slug": "mech-1",
                        "name": "机制一",
                        "narrative_summary": "以状态变量与代价约束组织冲突，不复述章节剧情。",
                        "content_json": {
                            "distillation_source_ids": ["source-0998"],
                            "state_variables": ["sv1"],
                            "guardrail": "必须付出可见代价。",
                        },
                        "genre": "玄幻",
                        "sub_genre": "progression",
                        "tags": ["distillation"],
                        "source_type": "user_curated",
                        "confidence": 0.88,
                        "status": "review",
                    }
                ],
            }
        if "DISTILLATION_TASK: grammar_patch" in user_prompt:
            calls.append("grammar")
            return {
                "key": "eastern-progression-fantasy",
                "name": "test",
                "source_ids": ["source-0998"],
                "status": "review",
                "applies_to_categories": ["xuanhuan"],
                "required_contracts": ["c1"],
                "state_variables": ["sv1"],
                "chapter_change_vectors": ["v1"],
                "reader_rewards": ["r1"],
                "hook_or_aftereffect_types": ["h1"],
                "forbidden_defaults": ["f1"],
            }
        raise AssertionError(f"unexpected prompt: {user_prompt[:120]}")

    monkeypatch.setattr(mod, "complete_distillation_json", fake_complete_json)

    class _DummySession:
        pass

    class _DummySettings:
        pass

    result = await mod.aggregate_source_package_async(
        _DummySession(),
        _DummySettings(),
        package_dir=pkg,
        repo_root=repo_root,
        private_errors_dir=tmp_path / "errors",
        chapter_batch_size=1,
        write_active_artifacts=True,
    )
    assert result.source_id == "source-0998"
    assert set(calls) >= {"volume", "book", "craft", "tail", "grammar"}
    assert (pkg / "volume_cards.jsonl").is_file()
    assert (pkg / "book_design_card.json").is_file()
    assert (pkg / "author_craft_card.json").is_file()
    assert (pkg / "material_entries.active.jsonl").is_file()
    loaded = yaml.safe_load((pkg / "grammar_patch.yaml").read_text(encoding="utf-8"))
    assert loaded.get("key") == "eastern-progression-fantasy"
