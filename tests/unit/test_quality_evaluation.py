from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from bestseller.services.quality_evaluation import (
    build_blind_review_packet,
    build_evaluation_manifest,
    canonical_hash,
    evaluate_release_gate,
    load_quality_evaluation_config,
    write_evaluation_run_skeleton,
)

pytestmark = pytest.mark.unit


def test_default_config_is_versioned_deterministic_and_has_thirty_drafts() -> None:
    config = load_quality_evaluation_config(Path("config/quality_evaluation.yaml"))

    assert config.schema_version == "quality-evaluation/v1"
    assert config.evaluation_version
    assert {case.genre for case in config.cases} == {
        "修仙升级",
        "都市职场",
        "悬疑推理",
        "情感言情",
        "奇幻冒险",
    }
    assert {1, 3, 10} <= {case.chapter_number for case in config.cases}
    assert any(case.chapter_number > 10 for case in config.cases)
    assert config.samples_per_strategy >= 2
    assert config.expected_draft_count >= 30
    assert [case.case_id for case in config.cases] == sorted(
        case.case_id for case in config.cases
    )


def test_manifest_and_hashes_are_deterministic() -> None:
    config = load_quality_evaluation_config(Path("config/quality_evaluation.yaml"))
    kwargs = {
        "run_id": "baseline-001",
        "git_sha": "a" * 40,
        "docker_image_id": "sha256:image",
        "writer_catalog_key": "writer-primary",
        "writer_actual_model": "writer/model-v1",
        "judge_catalog_key": "judge-primary",
        "judge_actual_model": "judge/model-v1",
    }

    first = build_evaluation_manifest(config, **kwargs)
    second = build_evaluation_manifest(config, **kwargs)

    assert first == second
    assert first["manifest_id"] == canonical_hash(first["identity"])
    assert first["expected"]["draft_count"] >= 30
    assert len(first["expected"]["draft_ids"]) == first["expected"]["draft_count"]
    assert first["expected"]["draft_ids"][0].endswith("__s1")
    assert first["cases"][0]["seed"] == config.cases[0].seed


def test_blind_packet_has_no_candidate_provenance_and_mapping_is_private() -> None:
    packet, private_mapping = build_blind_review_packet(
        packet_seed="fixed-packet",
        candidates={
            "production__writer-x__s1": "甲稿正文",
            "candidate__writer-y__s1": "乙稿正文",
        },
    )

    serialized = json.dumps(packet, ensure_ascii=False)
    assert {item["label"] for item in packet["candidates"]} == {"A", "B"}
    assert "甲稿正文" in serialized and "乙稿正文" in serialized
    for leaked in (
        "production",
        "candidate__",
        "writer-x",
        "writer-y",
        "strategy_id",
        "draft_id",
        "model",
        "provider",
        "source_path",
    ):
        assert leaked not in serialized
    assert set(private_mapping["labels"].values()) == {
        "production__writer-x__s1",
        "candidate__writer-y__s1",
    }


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda manifest: manifest["drafts"].pop(), "missing_drafts"),
        (lambda manifest: manifest["llm_judgements"].pop(), "missing_judgements"),
        (lambda manifest: manifest["human_ballots"].pop(), "missing_human_ballots"),
        (
            lambda manifest: manifest["drafts"][0].update({"fallback_used": True}),
            "fallback_samples",
        ),
        (
            lambda manifest: manifest["drafts"][0].update({"degraded": True}),
            "degraded_samples",
        ),
    ],
)
def test_release_gate_is_inconclusive_when_required_evidence_is_invalid(
    mutation: object,
    reason: str,
) -> None:
    manifest = _complete_manifest()
    mutation(manifest)  # type: ignore[operator]

    decision = evaluate_release_gate(manifest, require_human=True)

    assert decision["status"] == "inconclusive"
    assert reason in decision["reasons"]


def test_release_gate_passes_only_with_complete_real_evidence() -> None:
    decision = evaluate_release_gate(_complete_manifest(), require_human=True)

    assert decision == {
        "schema_version": "quality-release-gate/v1",
        "status": "pass",
        "reasons": [],
        "counts": {
            "expected_drafts": 1,
            "valid_drafts": 1,
            "expected_judgements": 1,
            "valid_judgements": 1,
            "expected_human_ballots": 1,
            "valid_human_ballots": 1,
        },
    }


def test_release_gate_detects_missing_expected_draft_even_when_count_matches() -> None:
    manifest = _complete_manifest()
    manifest["expected"] = {
        "draft_count": 2,
        "draft_ids": ["draft-1", "draft-2"],
        "judgement_count": 0,
        "human_ballot_count": 0,
    }
    duplicate = dict(manifest["drafts"][0])  # type: ignore[index]
    manifest["drafts"] = [manifest["drafts"][0], duplicate]  # type: ignore[index]
    manifest["llm_judgements"] = []
    manifest["human_ballots"] = []

    decision = evaluate_release_gate(manifest, require_human=False)

    assert decision["status"] == "inconclusive"
    assert "missing_drafts" in decision["reasons"]


def test_release_gate_rejects_duplicate_and_out_of_scope_judgements() -> None:
    manifest = _complete_manifest()
    manifest["expected"]["draft_ids"] = ["draft-1", "draft-2"]  # type: ignore[index]
    manifest["expected"]["draft_count"] = 2  # type: ignore[index]
    manifest["expected"]["judgement_count"] = 2  # type: ignore[index]
    second_draft = dict(manifest["drafts"][0])  # type: ignore[index]
    second_draft["draft_id"] = "draft-2"
    manifest["drafts"] = [manifest["drafts"][0], second_draft]  # type: ignore[index]
    duplicate = dict(manifest["llm_judgements"][0])  # type: ignore[index]
    manifest["llm_judgements"] = [manifest["llm_judgements"][0], duplicate]  # type: ignore[index]

    duplicate_decision = evaluate_release_gate(manifest, require_human=False)

    assert duplicate_decision["status"] == "inconclusive"
    assert "duplicate_judgements" in duplicate_decision["reasons"]
    assert "missing_judgement_coverage" in duplicate_decision["reasons"]

    duplicate["draft_id"] = "not-an-expected-draft"
    out_of_scope_decision = evaluate_release_gate(manifest, require_human=False)
    assert "out_of_scope_judgements" in out_of_scope_decision["reasons"]


def test_release_gate_rejects_duplicate_and_out_of_scope_human_ballots() -> None:
    manifest = _complete_manifest()
    manifest["expected"]["human_ballot_count"] = 2  # type: ignore[index]
    manifest["expected"]["human_ballot_slots"] = ["draft-1::h1", "draft-1::h2"]  # type: ignore[index]
    duplicate = dict(manifest["human_ballots"][0])  # type: ignore[index]
    manifest["human_ballots"] = [manifest["human_ballots"][0], duplicate]  # type: ignore[index]

    duplicate_decision = evaluate_release_gate(manifest, require_human=True)

    assert duplicate_decision["status"] == "inconclusive"
    assert "duplicate_human_ballots" in duplicate_decision["reasons"]
    assert "missing_human_ballot_coverage" in duplicate_decision["reasons"]

    duplicate["target_id"] = "not-an-expected-target"
    out_of_scope_decision = evaluate_release_gate(manifest, require_human=True)
    assert "out_of_scope_human_ballots" in out_of_scope_decision["reasons"]


def test_write_run_skeleton_creates_fixed_layout_and_inconclusive_gate(tmp_path: Path) -> None:
    config = load_quality_evaluation_config(Path("config/quality_evaluation.yaml"))
    root = tmp_path / "baseline-run"

    paths = write_evaluation_run_skeleton(
        root,
        build_evaluation_manifest(
            config,
            run_id="baseline-run",
            git_sha="a" * 40,
            docker_image_id="sha256:image",
            writer_catalog_key="writer-primary",
            writer_actual_model="writer/model-v1",
            judge_catalog_key="judge-primary",
            judge_actual_model="judge/model-v1",
        ),
    )

    assert set(paths) == {
        "cases",
        "drafts",
        "llm_judgements",
        "human_packets",
        "manifest",
        "report",
        "release_gate",
    }
    assert all(Path(path).exists() for path in paths.values())
    gate = json.loads(Path(paths["release_gate"]).read_text(encoding="utf-8"))
    assert gate["status"] == "inconclusive"
    assert "missing_drafts" in gate["reasons"]


def test_release_gate_cli_initializes_baseline_without_calling_llm(tmp_path: Path) -> None:
    script_path = Path("scripts/quality_release_gate.py")
    spec = importlib.util.spec_from_file_location("quality_release_gate_test", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "cli-baseline"

    exit_code = module.main(
        [
            "baseline",
            "--config",
            "config/quality_evaluation.yaml",
            "--output",
            str(output),
            "--git-sha",
            "a" * 40,
            "--docker-image-id",
            "sha256:image",
            "--writer-model",
            "writer/model-v1",
            "--judge-model",
            "judge/model-v1",
        ]
    )

    assert exit_code == 2
    gate = json.loads((output / "release-gate.json").read_text(encoding="utf-8"))
    assert gate["status"] == "inconclusive"
    assert not list((output / "drafts").iterdir())


def _complete_manifest() -> dict[str, object]:
    return {
        "schema_version": "quality-evaluation-manifest/v1",
        "identity": {
            "git_sha": "a" * 40,
            "docker_image_id": "sha256:image",
            "writer": {"catalog_key": "writer", "actual_model": "writer-v1"},
            "judge": {"catalog_key": "judge", "actual_model": "judge-v1"},
        },
        "expected": {
            "draft_count": 1,
            "judgement_count": 1,
            "human_ballot_count": 1,
        },
        "drafts": [
            {
                "draft_id": "draft-1",
                "case_id": "case-1",
                "strategy_id": "control",
                "sample_index": 1,
                "writer": {"catalog_key": "writer", "actual_model": "writer-v1"},
                "prompt_hashes": {
                    "system": "a",
                    "user": "b",
                    "final": "c",
                    "context": "d",
                },
                "generation": {
                    "temperature": 0.7,
                    "max_tokens": 4096,
                    "input_tokens": 100,
                    "output_tokens": 1000,
                    "cost": 0.1,
                },
                "fallback_used": False,
                "degraded": False,
                "compiler_mode": "legacy",
            }
        ],
        "llm_judgements": [
            {
                "draft_id": "draft-1",
                "evaluation_slot": 1,
                "judge": {"catalog_key": "judge", "actual_model": "judge-v1"},
                "scores": {"overall": 8.0},
                "fallback_used": False,
                "degraded": False,
            }
        ],
        "human_ballots": [
            {
                "packet_id": "packet-1",
                "target_id": "draft-1",
                "ballot_slot": 1,
                "rater_id": "rater-1",
                "choice": "A",
            }
        ],
    }
