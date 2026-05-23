from __future__ import annotations

import csv

from bestseller.services.exports import _metadata_batch_queue_rows, _metadata_volume_plan_rows


def test_export_sidecars_use_metadata_volume_plan_instead_of_collapsing_full_book():
    metadata = {
        "volume_plan": [
            {"volume_number": 1, "chapter_range": "1-50", "volume_goal": "十七栋困魂镜局"},
            {"volume_number": 2, "chapter_range": "51-100", "volume_goal": "旧城阴宅门"},
            {"volume_number": 10, "chapter_range": "451-500", "volume_goal": "镜中旧世"},
        ]
    }

    volume_lines = _metadata_volume_plan_rows(
        metadata,
        max_generated=71,
        fallback_target=500,
        reader_promise="promise",
    )
    rows = list(csv.reader(volume_lines))

    assert rows[0][:4] == ["1", "1", "50", "drafted"]
    assert rows[1][:4] == ["2", "51", "100", "active"]
    assert rows[2][:4] == ["10", "451", "500", "planned"]


def test_export_sidecars_use_volume_plan_for_batch_queue():
    metadata = {
        "volume_plan": [
            {"volume_number": 1, "chapter_range": [1, 50], "volume_goal": "十七栋困魂镜局"},
            {"volume_number": 2, "chapter_range": [51, 100], "reader_hook_to_next": "老宅井口"},
        ]
    }

    batch_lines = _metadata_batch_queue_rows(metadata, max_generated=71, reader_promise="promise")
    rows = list(csv.reader(batch_lines))

    assert rows[0][:3] == ["1A", "1", "50"]
    assert rows[0][4] == "drafted"
    assert rows[1][:3] == ["2A", "51", "100"]
    assert rows[1][4] == "active"
