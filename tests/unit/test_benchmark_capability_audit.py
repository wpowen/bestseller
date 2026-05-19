from __future__ import annotations

import json
from pathlib import Path

from bestseller.services.benchmark_capability_audit import (
    build_benchmark_audit_artifacts,
    find_repo_privacy_violations,
    infer_benchmark_category,
    select_benchmark_samples,
    write_benchmark_audit_artifacts,
)


def _write_book(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("第一章 开始\n这是测试内容。\n第二章 推进\n继续推进。\n", encoding="utf-8")


def test_infer_benchmark_category_from_title_signals() -> None:
    assert infer_benchmark_category("凡人修仙传") == "action-progression"
    assert infer_benchmark_category("三国权谋录") == "strategy-worldbuilding"
    assert infer_benchmark_category("民俗诡案") == "suspense-mystery"
    assert infer_benchmark_category("异界系统领主") == "otherworld-cross-system"
    assert infer_benchmark_category("全职电竞冠军") == "esports-competition"


def test_select_benchmark_samples_keeps_private_fields_out_of_repo_dict(tmp_path: Path) -> None:
    corpus = tmp_path / "Ebook"
    high_score = corpus / "高评分小说"
    _write_book(high_score / "凡人修仙传.txt")
    _write_book(high_score / "大奉打更人.txt")
    _write_book(corpus / "电竞冠军.txt")
    _write_book(corpus / "末世基地.txt")
    _write_book(corpus / "异界系统.txt")
    _write_book(corpus / "东宫.txt")
    _write_book(corpus / "武侠江湖.txt")

    samples = select_benchmark_samples(
        corpus_dir=corpus,
        high_score_dir=high_score,
        target_count=6,
        seed_limit=2,
    )

    assert len(samples) == 6
    assert samples[0].source_id == "benchmark-source-0001"
    assert samples[0].sample_reason == "high_score_seed"
    repo_row = samples[0].to_repo_dict()
    assert "source_path" not in repo_row
    assert "title_key" not in repo_row
    assert "凡人修仙传" not in json.dumps(repo_row, ensure_ascii=False)
    assert samples[0].to_private_dict()["title_key"]


def test_build_benchmark_audit_artifacts_outputs_matrix_and_no_private_terms(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "Ebook"
    high_score = corpus / "高评分小说"
    for name in (
        "凡人修仙传.txt",
        "大奉打更人.txt",
        "电竞冠军.txt",
        "末世基地.txt",
        "异界系统.txt",
        "东宫.txt",
        "武侠江湖.txt",
    ):
        _write_book((high_score if name in {"凡人修仙传.txt", "大奉打更人.txt"} else corpus) / name)

    artifacts = build_benchmark_audit_artifacts(
        corpus_dir=corpus,
        high_score_dir=high_score,
        target_count=6,
        seed_limit=2,
    )

    assert artifacts.repo_sample_set["actual_count"] == 6
    assert artifacts.privacy_violations == ()
    assert artifacts.capability_report["benchmark_findings"]
    assert artifacts.capability_report["category_hard_engine_contracts"]
    assert artifacts.capability_report["category_engine_fixture_benchmark"]
    assert artifacts.capability_report["sample_quality_parity_gate"]["required_for_ready"] is True
    assert artifacts.capability_report["capability_matrix"]
    assert artifacts.capability_report["gap_register"]
    assert "Capability Matrix" in artifacts.markdown_report
    assert "Sample Quality Parity Gate" in artifacts.markdown_report
    matrix_by_category = {
        row["canonical_category"]: row
        for row in artifacts.capability_report["capability_matrix"]
    }
    otherworld_row = matrix_by_category["otherworld-cross-system"]
    assert otherworld_row["overall_support"] == "partial"
    assert otherworld_row["dimension_support"]["category_coverage"] == "ready"
    assert otherworld_row["dimension_support"]["state_engine"] == "partial"
    assert otherworld_row["dimension_support"]["chapter_execution"] == "partial"
    assert otherworld_row["dimension_support"]["quality_gates"] == "partial"
    assert all(
        "缺少一等 novel category" not in gap and "缺少 genre review profile" not in gap
        for gap in otherworld_row["gaps"]
    )
    assert all(
        "缺少对齐的 distillation bucket" not in gap
        for category in ("base-building", "eastern-aesthetic")
        for gap in matrix_by_category[category]["gaps"]
    )
    for category in ("base-building", "eastern-aesthetic", "esports-competition"):
        assert matrix_by_category[category]["dimension_support"]["state_engine"] == "partial"
        assert matrix_by_category[category]["dimension_support"]["quality_gates"] == "partial"
    fixture_rows = artifacts.capability_report["category_engine_fixture_benchmark"]
    assert all(row["good_fixture_passed"] for row in fixture_rows)
    assert all(row["bad_fixture_blocked"] for row in fixture_rows)

    repo_output = tmp_path / "repo-out"
    private_path = tmp_path / "private" / "benchmark_sample_set.private.json"
    markdown_path = tmp_path / "report.md"
    write_benchmark_audit_artifacts(
        artifacts,
        repo_output_dir=repo_output,
        private_sample_path=private_path,
        markdown_report_path=markdown_path,
    )

    repo_json = (repo_output / "benchmark_sample_set.repo.json").read_text(encoding="utf-8")
    assert "凡人修仙传" not in repo_json
    assert "source_path" not in repo_json
    assert private_path.exists()
    assert markdown_path.exists()


def test_privacy_violation_detector_flags_private_terms() -> None:
    violations = find_repo_privacy_violations(
        {"samples": [{"source_id": "benchmark-source-0001", "leak": "秘密书名"}]},
        forbidden_terms=["秘密书名", "x"],
    )
    assert violations == ("秘密书名",)
