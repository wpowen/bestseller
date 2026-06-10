from __future__ import annotations

import json
from pathlib import Path

from bestseller.services.benchmark_regression import (
    _window,
    auto_benchmark_regression_enabled,
    load_benchmark_report,
)


def test_auto_benchmark_regression_env_gate(monkeypatch) -> None:
    monkeypatch.delenv("AUTO_BENCHMARK_REGRESSION", raising=False)
    assert auto_benchmark_regression_enabled() is True
    monkeypatch.setenv("AUTO_BENCHMARK_REGRESSION", "0")
    assert auto_benchmark_regression_enabled() is False
    monkeypatch.setenv("AUTO_BENCHMARK_REGRESSION", "off")
    assert auto_benchmark_regression_enabled() is False


def test_window_keeps_head_and_tail_symmetrically() -> None:
    short = "短文本" * 10
    assert _window(short) == short.strip()
    long_text = "开" * 3000 + "中" * 3000 + "尾" * 3000
    windowed = _window(long_text)
    assert windowed.startswith("开")
    assert windowed.endswith("尾")
    assert "（中段省略）" in windowed
    assert len(windowed) < 5000


def test_load_benchmark_report_roundtrip(tmp_path: Path) -> None:
    assert load_benchmark_report("ghost", output_base_dir=tmp_path) is None
    audit_dir = tmp_path / "my-book" / "audits" / "benchmark"
    audit_dir.mkdir(parents=True)
    (audit_dir / "arena.json").write_text(
        json.dumps({"summaries": {"t1": {"win_rate": 0.6}}}), encoding="utf-8"
    )
    report = load_benchmark_report("my-book", output_base_dir=tmp_path)
    assert report is not None
    assert report["summaries"]["t1"]["win_rate"] == 0.6


def test_dossier_html_renders_benchmark_block() -> None:
    html = Path("src/bestseller/web/novel_design_dossier.html").read_text(
        encoding="utf-8"
    )
    assert "榜单对标（vs 真书盲评）" in html
    assert "benchmark_report" in html
    assert "judge_family_warning" in html
