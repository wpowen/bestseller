from __future__ import annotations

import json
from pathlib import Path

import yaml

from bestseller.services.distillation_assets import (
    aggregate_distillation_packages,
    install_story_design_grammar_patch,
    validate_distillation_package,
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


def _package(tmp_path: Path, source_id: str = "source-0001") -> Path:
    root = tmp_path / source_id
    _write_json(
        root / "source_manifest.json",
        {
            "source_id": source_id,
            "pipeline_version": "distillation-v1",
            "source_hash_sha256": "a" * 64,
            "source_format": "txt",
            "encoding": "utf-8",
            "title_key_hmac_sha256": "b" * 64,
            "rights_status": "user_supplied_for_analysis",
            "redaction_policy": {
                "store_source_title_in_repo": False,
                "store_author_in_repo": False,
                "store_raw_text_in_repo": False,
            },
            "parse_profile": {
                "chapter_count": 1,
                "volume_count": 1,
                "average_chapter_chars": 1000,
            },
        },
    )
    _write_json(
        root / "chapters.index.json",
        {"source_id": source_id, "chapters": [{"abs_chapter_no": 1}]},
    )
    _write_json(root / "book_design_card.json", {"book_id": source_id, "source_ref": "local"})
    _write_json(
        root / "author_craft_card.json",
        {
            "source_id": source_id,
            "source_type": "distillation_package",
            "status": "draft_review",
            "style_safety_policy": "abstract craft only; no author imitation",
            "pov_and_distance": "close third",
            "sentence_rhythm": ["short action beats"],
            "paragraphing": ["single-purpose paragraphs"],
            "dialogue_system": ["conflict-loaded dialogue"],
            "description_strategy": ["stakes-relevant detail"],
            "exposition_strategy": ["explain after need"],
            "emotional_temperature": ["controlled pressure"],
            "hooking_and_transitions": ["changed-state endings"],
            "adaptation_guidelines": ["change imagery and scenario chains"],
            "taboo_copy_signals": ["exact phrases"],
            "confidence": 0.8,
        },
    )
    _write_json(
        root / "anti_copy_ledger.json",
        {
            "source_id": source_id,
            "blocked_categories": [
                {"category": "opening", "policy": "do not copy", "examples_redacted": []}
            ],
            "blocked_combinations": ["exact-opening-chain"],
            "replacement_policy": ["change profession"],
        },
    )
    _write_jsonl(root / "volume_cards.jsonl", [{"source_id": source_id, "volume_no": 1}])
    _write_jsonl(
        root / "mechanism_candidates.jsonl",
        [
            {
                "source_id": source_id,
                "mechanism_id": "cross-system-rule-arbitrage",
                "candidate_type": "plot_pattern",
                "summary": "Use one rule system to exploit another.",
                "promotion_target": "material_library.plot_patterns",
                "status": "review",
                "confidence": 0.8,
            }
        ],
    )
    _write_jsonl(
        root / "material_entries.review.jsonl",
        [
            {
                "dimension": "plot_patterns",
                "slug": "cross-system-rule-arbitrage",
                "name": "跨体系规则套利",
                "narrative_summary": "A reusable abstract mechanism.",
                "content_json": {},
                "source_type": "user_curated",
                "status": "review",
            }
        ],
    )
    (root / "llm_jobs").mkdir(parents=True)
    _write_jsonl(
        root / "llm_jobs" / "chapter_jobs.index.jsonl",
        [{"job_id": "j1", "source_id": source_id, "abs_chapter_no": 1}],
    )
    (root / "grammar_patch.yaml").write_text(
        yaml.safe_dump(
            {
                "key": "otherworld-cross-system",
                "name": "异界跨体系规则套利",
                "source_ids": [source_id],
                "state_variables": ["cross_system_understanding"],
                "chapter_change_vectors": ["exploit_rule_gap"],
                "reader_rewards": ["knowledge_arbitrage"],
                "forbidden_defaults": ["copy_exact_opening"],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return root


def test_validate_distillation_package_accepts_redacted_package(tmp_path: Path) -> None:
    package = _package(tmp_path)

    report = validate_distillation_package(package)

    assert report.ok
    assert report.source_id == "source-0001"
    assert report.material_rows == 1
    assert report.mechanism_rows == 1
    assert report.chapter_jobs == 1


def test_aggregate_distillation_package_writes_system_assets(tmp_path: Path) -> None:
    package = _package(tmp_path)
    output = tmp_path / "aggregate"

    report = aggregate_distillation_packages(
        [package],
        output_dir=output,
        aggregate_key="otherworld-cross-system",
    )

    assert report.material_rows == 1
    assert report.mechanism_rows == 1
    assert report.author_craft_rows == 1
    assert report.book_design_rows == 1
    assert report.volume_design_rows == 1
    assert report.fallback_volume_rows == 0
    assert report.maturity_score > 0
    assert report.maturity_status in {"pilot", "review", "production"}
    material = (output / "material_entries.review.jsonl").read_text(encoding="utf-8")
    assert "distillation_source_ids" in material
    craft = (output / "author_craft_registry.jsonl").read_text(encoding="utf-8")
    assert "abstract craft only" in craft
    book_design = (output / "book_design_registry.jsonl").read_text(encoding="utf-8")
    assert "source-0001" in book_design
    volume_paths = (output / "volume_design_paths.jsonl").read_text(encoding="utf-8")
    assert '"volume_no": 1' in volume_paths
    manifest = json.loads((output / "aggregate_manifest.json").read_text(encoding="utf-8"))
    assert manifest["fallback_volume_rows"] == 0
    assert manifest["maturity_score"] == report.maturity_score
    grammar = yaml.safe_load((output / "grammar_patch.yaml").read_text(encoding="utf-8"))
    assert grammar["key"] == "otherworld-cross-system"
    assert grammar["state_variables"] == ["cross_system_understanding"]


def test_aggregate_distillation_package_quarantines_fallback_volume_rows(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path)
    _write_jsonl(
        package / "volume_cards.jsonl",
        [
            {
                "source_id": "source-0001",
                "volume_no": 1,
                "arc_function": "Fallback aggregation due LLM structure issue.",
                "dominant_engine": "unknown",
                "distillation_fallback": True,
            },
            {
                "source_id": "source-0001",
                "volume_no": 2,
                "arc_function": "Rules turn into political pressure.",
                "dominant_engine": "world_pressure",
            },
        ],
    )
    output = tmp_path / "aggregate"

    report = aggregate_distillation_packages(
        [package],
        output_dir=output,
        aggregate_key="otherworld-cross-system",
    )

    assert report.fallback_volume_rows == 1
    assert report.volume_design_rows == 1
    volume_paths = (output / "volume_design_paths.jsonl").read_text(encoding="utf-8")
    assert "Fallback aggregation" not in volume_paths
    assert "world_pressure" in volume_paths
    manifest = json.loads((output / "aggregate_manifest.json").read_text(encoding="utf-8"))
    assert manifest["fallback_volume_rows"] == 1


def test_install_story_design_grammar_patch_dry_run_and_apply(tmp_path: Path) -> None:
    package = _package(tmp_path)
    grammar_dir = tmp_path / "grammars"

    dry_target = install_story_design_grammar_patch(
        package / "grammar_patch.yaml",
        grammar_dir=grammar_dir,
        dry_run=True,
    )
    assert dry_target.name == "otherworld-cross-system.yaml"
    assert not dry_target.exists()

    target = install_story_design_grammar_patch(
        package / "grammar_patch.yaml",
        grammar_dir=grammar_dir,
        dry_run=False,
    )
    assert target.exists()
