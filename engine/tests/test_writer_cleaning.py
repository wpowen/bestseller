"""Writer cleanup happens before extraction and preserves old checkpoints."""

from support_minimal_b import MinimalBTests, CaptureClient, body
from tools.book_checkup import clean_prose


def test_writer_records_all_removed_structure_before_fact_extraction():
    fixture = MinimalBTests()
    fixture.setUp()
    try:
        dirty = "### 本章标题\n\n" + body(60) + "\n\n---\n\n### 第二场\n\n阿宁🏼站在院门。\ue011"
        client = CaptureClient(fixture.out, [dirty])
        row = fixture.run_chapter(client)
        saved = (fixture.out / "chapter-002.body.md").read_text()
        assert clean_prose(saved)[1]["total"] == 0
        assert row["cleaning"]["draft"]["total"] == 5
        assert row["cleaning"]["total"] == 5
        finish = next(c for c in client.calls if c["stage"] == "r2-ch2-finish")["prompt"]
        assert "### 第二场" not in finish and "🏼" not in finish
    finally:
        fixture.doCleanups()
