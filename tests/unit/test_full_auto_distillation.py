from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def _load_fad_module():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts/distillation/run_full_auto_distillation.py"
    name = "run_full_auto_distillation_test"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod, root


def test_discover_sources_empty(tmp_path: Path) -> None:
    mod, _root = _load_fad_module()
    dist = tmp_path / "distillation"
    dist.mkdir()
    assert mod._discover_sources(dist) == []


def test_discover_sources_sorted(tmp_path: Path) -> None:
    mod, _root = _load_fad_module()
    dist = tmp_path / "distillation"
    dist.mkdir()
    (dist / "source-0002").mkdir()
    (dist / "source-0001").mkdir()
    names = [p.name for p in mod._discover_sources(dist)]
    assert names == ["source-0001", "source-0002"]


def test_filter_sources_accepts_numeric_or_source_bounds(tmp_path: Path) -> None:
    mod, _root = _load_fad_module()
    dist = tmp_path / "distillation"
    dist.mkdir()
    for name in ("source-0001", "source-0240", "source-0241", "source-0480"):
        (dist / name).mkdir()

    sources = mod._discover_sources(dist)
    names = [
        p.name
        for p in mod._filter_sources(
            sources,
            source_start="source-0241",
            source_end="480",
        )
    ]

    assert names == ["source-0241", "source-0480"]


def test_load_state_defaults(tmp_path: Path) -> None:
    mod, _root = _load_fad_module()
    st = mod._load_state(tmp_path / "nope.json")
    assert st["book_complete_sources"] == []


def test_import_material_active_dry_run_zero_rejected(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    active = tmp_path / "material_entries.active.jsonl"
    row = {
        "dimension": "plot_patterns",
        "slug": "distillation-auto-test-mech",
        "name": "自动化测试机制",
        "narrative_summary": "以状态变量与代价约束组织冲突，不复述具体剧情。",
        "content_json": {
            "distillation_source_ids": ["source-0001"],
            "state_variables": ["tension"],
            "guardrail": "必须可见代价。",
        },
        "genre": "玄幻",
        "sub_genre": "test",
        "tags": ["distillation", "test"],
        "source_type": "user_curated",
        "confidence": 0.91,
        "status": "active",
    }
    active.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/import_material_jsonl.py"),
            str(active),
            "--dry-run",
            "--format",
            "json",
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    summary = json.loads(proc.stdout)
    assert summary.get("rejected") == 0
    assert summary.get("inserted_or_updated", 0) >= 1


def test_async_main_blocks_dry_run_promotion_without_review(monkeypatch, tmp_path: Path) -> None:
    mod, _root = _load_fad_module()
    args = argparse.Namespace(
        repo_root=tmp_path,
        private_root=Path(".distillation_private"),
        import_mode="dry-run",
        allow_reviewed_promotion=False,
        auto_install_grammar=False,
        corpus_dir=None,
    )

    assert asyncio.run(mod._async_main(args)) == 2


def test_async_main_blocks_reviewed_promotion_for_looping_daemon(tmp_path: Path) -> None:
    mod, _root = _load_fad_module()
    args = argparse.Namespace(
        repo_root=tmp_path,
        private_root=Path(".distillation_private"),
        import_mode="dry-run",
        allow_reviewed_promotion=True,
        single_pass=False,
        auto_install_grammar=False,
        corpus_dir=None,
    )

    assert asyncio.run(mod._async_main(args)) == 2


def test_run_one_pass_import_mode_none_does_not_write_active(monkeypatch, tmp_path: Path) -> None:
    mod, _root = _load_fad_module()
    repo_root = tmp_path / "repo"
    dist_root = repo_root / "data" / "distillation"
    package_dir = dist_root / "source-0001"
    package_dir.mkdir(parents=True)
    (package_dir / "source_manifest.json").write_text("{}", encoding="utf-8")
    private_root = tmp_path / "private"
    reports_dir = private_root / "reports"
    errors_dir = private_root / "errors"
    reports_dir.mkdir(parents=True)
    errors_dir.mkdir(parents=True)

    async def fake_chapter_jobs(**_kwargs):
        return (0, 0)

    class ValidationReport:
        ok = True

    class AggregateReport:
        material_rows = 3

    write_calls: list[Path] = []

    def fake_write_active(out_dir: Path, **_kwargs) -> None:
        write_calls.append(out_dir)
        (out_dir / "material_entries.active.jsonl").write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(mod, "run_pending_chapter_jobs_parallel", fake_chapter_jobs)
    monkeypatch.setattr(mod, "load_chapter_card_schema", lambda _repo_root: {})
    monkeypatch.setattr(mod, "expected_abs_chapters_from_index", lambda _index: set())
    monkeypatch.setattr(mod, "existing_chapter_card_keys", lambda _path: set())
    monkeypatch.setattr(mod, "package_book_phase_complete", lambda _package_dir: True)
    monkeypatch.setattr(mod, "validate_distillation_package", lambda _package_dir: ValidationReport())
    monkeypatch.setattr(mod, "infer_aggregate_key", lambda _manifest: "test-aggregate")
    monkeypatch.setattr(mod, "read_json", lambda _path: {})
    monkeypatch.setattr(mod, "get_settings", lambda: object())

    def fake_aggregate(_dirs, *, output_dir: Path, aggregate_key: str):
        output_dir.mkdir(parents=True, exist_ok=True)
        assert aggregate_key == "test-aggregate"
        return AggregateReport()

    monkeypatch.setattr(mod, "aggregate_distillation_packages", fake_aggregate)
    monkeypatch.setattr(mod, "write_aggregate_active_materials", fake_write_active)

    args = argparse.Namespace(
        resume=False,
        chapter_job_limit=None,
        max_chapter_chars=0,
        chapter_workers=1,
        skip_genre_classify=True,
        force_genre_reclassify=False,
        aggregate_key="auto",
        import_mode="none",
        auto_install_grammar=False,
    )
    report, code = asyncio.run(
        mod._run_one_pass(
            args,
            sources=[package_dir],
            repo_root=repo_root,
            private_root=private_root,
            dist_root=dist_root,
            reports_dir=reports_dir,
            errors_dir=errors_dir,
            state_path=reports_dir / "state.json",
            report_path=reports_dir / "report.json",
            iteration=1,
        )
    )

    assert code == 0
    assert report.material_entries_review_generated == 3
    assert report.material_entries_active_imported == 0
    assert write_calls == []
    assert not (dist_root / "aggregates" / "test-aggregate" / "material_entries.active.jsonl").exists()
