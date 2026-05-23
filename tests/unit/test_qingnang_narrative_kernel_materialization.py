from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


def _load_materializer():
    path = Path(__file__).resolve().parents[2] / "scripts/audit_repair_qingnang_narrative_richness.py"
    spec = importlib.util.spec_from_file_location("audit_repair_qingnang_narrative_richness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_materialize_qingnang_kernel_files(tmp_path):
    story_bible = tmp_path / "story-bible"
    context_payload = {
        "geography_kernel": {"regions": [{"name": "十七栋镜局"}]},
        "mystery_anchor_kernel": {"anchors": [{"question": "困魂镜第一笔镜债究竟是谁欠下的？"}]},
        "ethical_dilemma_kernel": {"slots": []},
        "calendar_module": {"calendar_type": "mixed"},
    }

    paths = _load_materializer().materialize_qingnang_kernel_files(story_bible, context_payload)

    assert (story_bible / "kernels/geography-kernel.json").exists()
    assert json.loads((story_bible / "kernels/mystery-anchor-kernel.json").read_text(encoding="utf-8"))[
        "anchors"
    ]
    assert set(paths) == {
        "geography-kernel.json",
        "mystery-anchor-kernel.json",
        "ethical-dilemma-kernel.json",
        "calendar-system.json",
    }


def test_dry_run_zero_findings_is_warn_only_not_pass():
    module = _load_materializer()

    status = module.classify_narrative_richness_report(
        applied=False,
        repair_target_count=0,
        materialized_kernel_count=0,
    )

    assert status["verdict"] == "warn_only"
    assert status["passed"] is False
    assert status["coverage"] < 0.95


def test_applied_materialized_zero_findings_passes():
    module = _load_materializer()

    status = module.classify_narrative_richness_report(
        applied=True,
        repair_target_count=0,
        materialized_kernel_count=6,
    )

    assert status["verdict"] == "pass"
    assert status["passed"] is True
    assert status["coverage"] == 1.0
