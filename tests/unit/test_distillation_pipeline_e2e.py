"""Lightweight end-to-end checks for distillation production gates (no real LLM)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bestseller.services.distillation_privacy_gate import privacy_violation_count_for_material_row
from bestseller.services.distillation_source_preparer import prepare_source


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_prepare_source_from_mini_txt_corpus(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    private_root = tmp_path / "private"
    book = tmp_path / "corpus" / "mini.txt"
    book.parent.mkdir(parents=True)
    book.write_text(
        "第一章 测\n" + ("叙述推进与悬念留白。" * 80) + "\n",
        encoding="utf-8",
    )
    res = prepare_source(
        book,
        "source-9901",
        repo_root,
        private_root,
        dedupe_policy="skip",
        rights_status="user_supplied_for_analysis",
        genre_hint="都市",
    )
    assert res.skipped is False
    assert res.chapter_count >= 1
    pkg = repo_root / "data" / "distillation" / "source-9901" / "source_manifest.json"
    assert pkg.is_file()


def test_privacy_gate_flags_active_style_material_row() -> None:
    ledger = {"blocked_combinations": ["FORBIDDEN_SUBSTRING_XYZ"]}
    row: dict = {
        "dimension": "plot_patterns",
        "slug": "safe-slug",
        "name": "name",
        "narrative_summary": "抽象机制，不复述剧情。",
        "genre": "都市",
        "sub_genre": "x",
        "tags": ["t"],
        "content_json": {"distillation_source_ids": ["source-0001"], "state_variables": ["sv"]},
        "confidence": 0.9,
    }
    assert privacy_violation_count_for_material_row(row, anti_copy_ledger=ledger)[0] == 0

    row_bad = dict(row)
    row_bad["content_json"] = {
        "distillation_source_ids": ["source-0001"],
        "state_variables": ["FORBIDDEN_SUBSTRING_XYZ in json"],
    }
    n, msgs = privacy_violation_count_for_material_row(row_bad, anti_copy_ledger=ledger)
    assert n >= 1
    assert any("blocked_combination" in m for m in msgs)


def test_import_material_jsonl_dry_run_subprocess(tmp_path: Path) -> None:
    repo_root = _repo_root()
    row = {
        "dimension": "plot_patterns",
        "slug": "distillation-e2e-import-row",
        "name": "e2e row",
        "narrative_summary": "抽象机制摘要，用于校验 dry-run 导入管线。",
        "genre": "都市",
        "sub_genre": "test",
        "tags": ["distillation-e2e"],
        "content_json": {
            "distillation_source_ids": ["source-0001"],
            "state_variables": ["sv1"],
            "guardrail": "测试护栏",
        },
        "source_type": "user_curated",
        "confidence": 0.86,
        "status": "active",
    }
    path = tmp_path / "material.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "import_material_jsonl.py"),
            str(path),
            "--dry-run",
            "--format",
            "json",
            "--source-type",
            "user_curated",
            "--default-status",
            "active",
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary.get("mode") == "dry-run"
    assert summary.get("inserted_or_updated") == 1
