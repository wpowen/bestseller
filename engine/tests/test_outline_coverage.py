import json

from bookengine.outline_coverage import complete_details, indexed_rows
from test_outline_diversity import Fixture, outline, plan


def test_partial_window_supplements_only_missing_and_reuses_report(tmp_path):
    def card(n):
        return outline(n, f"河口{n}", str(n))

    first = {"chapters": [card(1), card(3), card(5)]}
    supplement = {"chapters": [dict(card(1), title="须忽略的改写"), card(4), card(2)]}
    client = Fixture(tmp_path, [first, supplement])
    result = plan(client, 1, 5)
    assert [r["title"] for r in result] == [f"河口{n}" for n in range(1, 6)]
    assert len(client.calls) == 2
    assert "missing-2-4" in client.calls[1][0]
    report = tmp_path / "outline-diversity/001-005.json"
    before = report.read_bytes(), report.stat().st_mtime_ns
    resumed = Fixture(tmp_path, [])
    assert plan(resumed, 2, 5) == result[1:] and not resumed.calls
    assert (report.read_bytes(), report.stat().st_mtime_ns) == before


def test_partial_supplement_keeps_specific_unresolved_numbers(tmp_path):
    client = Fixture(tmp_path, [{"chapters": [outline(1, "过河")]}, {"chapters": [outline(3, "回信")]}])
    result = plan(client, 1, 3)
    assert [r.get("detail_outline_missing", False) for r in result] == [False, True, False]
    assert result[2]["title"] == "回信"
    report = json.loads((tmp_path / "outline-diversity/001-003.json").read_text())
    assert report["status"] == "failed_continued"
    assert {"kind": "missing_detail_outline", "targets": [2]} in report["issues"]
    assert len(client.calls) == 2


def test_unnumbered_partial_reply_is_not_mistaken_for_a_missing_number():
    assert indexed_rows([{"title": "未编号"}], [2, 5]) == {}
    full = [{"title": "首章"}, {"title": "次章"}]
    assert indexed_rows(full, [2, 3]) == {2: full[0], 3: full[1]}
    assert indexed_rows(full, [2, 5], positional=False) == {}


def test_sparse_numbered_row_stays_diagnostic_without_an_extra_call():
    first = {
        "chapters": [{"number": 1, "title": "晚渡", "scenes": [{}]}, {"number": 2, "scenes": [{"action": "收起跳板"}]}]
    }

    def no_call(*args):
        raise AssertionError("Both chapter numbers are present")

    result = complete_details(None, no_call, first, 1, 2, [], False)
    assert result[1] == dict(first["chapters"][0], detail_outline_missing=True)
    assert result[2] == first["chapters"][1]


def test_all_numbers_but_no_story_is_recorded_and_resumes_without_supplement(tmp_path):
    client = Fixture(tmp_path, [{"chapters": [{"number": 1}, {"number": 2}]}])
    result = plan(client, 1, 2)
    report_path = tmp_path / "outline-diversity/001-002.json"
    before = report_path.read_bytes()
    report = json.loads(before)
    assert len(client.calls) == 1 and report["status"] == "failed_continued"
    assert {"kind": "missing_detail_outline", "targets": [1, 2]} in report["issues"]
    resumed = Fixture(tmp_path, [])
    assert plan(resumed, 2, 2) == result[1:]
    assert report_path.read_bytes() == before and not resumed.calls
