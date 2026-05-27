from __future__ import annotations

import csv
import importlib.util
from pathlib import Path
import sys


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts/_deprecated/qingnang_repair/repair_qingnang_story_bible_plans.py"
    spec = importlib.util.spec_from_file_location("repair_qingnang_story_bible_plans", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_builds_ten_volume_plan_and_recovery_batches(tmp_path):
    volumes = [
        {
            "volume_number": number,
            "chapter_range": [(number - 1) * 50 + 1, number * 50],
            "volume_goal": f"goal {number}",
        }
        for number in range(1, 11)
    ]

    module = _load_module()
    volume_rows = module.build_volume_plan_rows(volumes)
    batch_rows = module.build_batch_plan_rows(volume_rows)
    volume_path = tmp_path / "volume-plan.csv"
    batch_path = tmp_path / "batch-queue.csv"
    module.write_volume_plan(volume_path, volume_rows)
    module.write_batch_queue(batch_path, batch_rows)

    with volume_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["volume", "start_chapter", "end_chapter", "status", "goal"]
    assert rows[1][:4] == ["1", "1", "50", "closed_review"]
    assert rows[2][:4] == ["2", "51", "100", "recovery_required"]
    assert rows[10][:4] == ["10", "451", "500", "planned"]

    with batch_path.open(encoding="utf-8", newline="") as handle:
        batch_reader = csv.DictReader(handle)
        by_id = {row["batch"]: row for row in batch_reader}
    assert "2A" in by_id
    assert by_id["2A"]["start_chapter"] == "51"
    assert by_id["2A"]["end_chapter"] == "75"
    assert by_id["2A"]["status"] == "recovery"
