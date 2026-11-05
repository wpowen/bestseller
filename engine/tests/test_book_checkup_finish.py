"""Automatic checkup uses the saved book and runs without additional model calls."""

import json
from unittest.mock import patch

from test_run_to_finish import q1_fixture, good


def test_finished_writer_generates_reference_report_and_resume_never_sends(tmp_path, monkeypatch):
    q1, client, config, draft, extracted = q1_fixture(tmp_path, monkeypatch)
    with patch("urllib.request.urlopen", side_effect=[good(draft), good(extracted)] * 2):
        result = q1.run_q1(config, tmp_path, tmp_path / "unused.env", tmp_path)
    assert result["status"] == "complete"
    report = json.loads((tmp_path / "book_checkup.json").read_text())
    assert report["reference_only"] is True and report["chapter_count"] == 2
    assert report["source_preference"] == "draft"
    original = {p: p.read_bytes() for p in tmp_path.glob("chapter-*.body.md")}
    with patch("urllib.request.urlopen", side_effect=AssertionError("completed book must not send")):
        q1.run_q1(config, tmp_path, tmp_path / "unused.env", tmp_path)
    assert all(p.read_bytes() == value for p, value in original.items())
    assert len(client.calls) == 4
