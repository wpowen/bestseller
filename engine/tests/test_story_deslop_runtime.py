import json

from deai.io import process, summarize


def test_unreadable_scan_keeps_source_after_one_request_and_resumes(tmp_path):
    text = "秋禾弯下腰，把散落的种子拢回布袋里。\n\n“带上吧。”\n"
    path = tmp_path / "chapter-001.body.md"
    path.write_text(text)
    stages = []

    class BrokenScan:
        def __init__(self, *_args, **_kwargs):
            self.calls = []

        def __call__(self, _messages, *, stage, **_kwargs):
            self.calls.append({"stage": stage})
            stages.append(stage)
            return {"findings": "bad response shape"}

        def estimate(self):
            return 0

    def run():
        return process(tmp_path, "001", path, None, "local", None, BrokenScan, editor="story-deslop", preserve=True)

    assert run() == "retained"
    assert run() == "cached"
    assert stages == ["story-deslop-scan-chapter"]
    assert path.read_text() == (tmp_path / "final/chapter-001.md").read_text() == text
    report = json.loads((tmp_path / "final/chapter-001.deai.json").read_text())
    assert report["checks"]["story_deslop"]["failure"]["stage"] == stages[0].removesuffix("-chapter")
    assert report["cumulative"]["requests"] == 1


def test_original_mode_publishes_without_constructing_the_editor(tmp_path):
    source = tmp_path / "chapter-001.body.md"
    source.write_text("“出门吗？”\n\n“再等等。”\n")

    def no_client(*_args, **_kwargs):
        raise AssertionError("Original mode should not construct a model client")

    assert process(tmp_path, "001", source, None, "local", None, no_client, original=True) == "original"
    assert source.read_bytes() == (tmp_path / "final/chapter-001.md").read_bytes()


def test_chapter_summary_separates_edit_counts_and_planning_evidence(tmp_path):
    source = tmp_path / "chapter-001.body.md"
    source.write_text("茶已经凉了。")
    final = tmp_path / "final"
    final.mkdir()
    (final / "chapter-001.md").write_text(source.read_text())
    scan = {
        "proposed_paragraphs": 3,
        "prose_edits": 1,
        "punctuation_edits": 1,
        "findings": [{"id": "P1", "quote": "茶已经凉了", "route": "return_to_planning"}],
    }
    (final / "chapter-001.deai.json").write_text(json.dumps({"checks": {"story_deslop": scan}}))
    summarize(tmp_path, {"001": "polished"})
    summary = (final / "SUMMARY.md").read_text()
    assert "提案 3 段，实质改写 1 段，仅标点或空白 1 段" in summary
    assert "P1 回查章纲：茶已经凉了" in summary
