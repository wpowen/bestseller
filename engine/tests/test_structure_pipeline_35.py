"""Planning, writer context and diagnostics use the same stage rules."""

import json
import pytest

from bookengine import q1_plan
from bookengine.q1_extract import context
from tools.book_checkup import check_book
from test_retired_opponents_35 import design


def test_planning_repairs_missing_transition_and_context_retains_it(tmp_path, monkeypatch):
    requests = []

    def ask(client, kind, key, sections, redo=False):
        requests.append((key, sections))
        item = dict(number=11, title="新程", scenes=[dict(characters=["何晏"])], conflict="何晏夺船")
        if "diversity" in key:
            item["transition"] = "从旧港乘船一夜来到山城，为救被扣的友人。"
        return dict(chapters=[item])

    monkeypatch.setattr(q1_plan, "_ask", ask)

    class Client:
        out = tmp_path

    bible = design()
    result = q1_plan.outlines(Client(), bible, {}, {}, bible["volumes"][1], 11, 11, {}, [], [], [])
    assert len(requests) == 2
    assert "transition" in result[0]
    sections = context(bible, {}, {"number": 2}, result[0], {}, [], [], "")
    assert result[0]["transition"] in json.dumps(sections, ensure_ascii=False)
    assert "已离开" in json.dumps(requests, ensure_ascii=False)


def test_checkup_reports_stage_jump_without_grounded_metadata(tmp_path):
    import yaml

    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(design(), allow_unicode=True))
    (tmp_path / "chapter-010.body.md").write_text("林舟冲向旧港的界碑。")
    (tmp_path / "chapter-011.body.md").write_text("林舟伏在山城的城楼背后。")
    report = check_book(tmp_path)
    assert report["stage_transitions"]["missing"] == [[10, 11]]


@pytest.mark.parametrize("version", [0, 1])
def test_cached_future_window_cannot_bypass_new_stage_constraints(tmp_path, monkeypatch, version):
    folder = tmp_path / "outline-diversity"
    folder.mkdir()
    (folder / "011-011.json").write_text(
        json.dumps(
            dict(
                status="passed",
                structure_checks_version=version,
                planned_chapters=[dict(number=11, title="新程", conflict="石远封城", scenes=[])],
                chapters=[dict(number=11, title="新程", conflict="石远封城", scenes=[])],
            )
        )
    )

    class Client:
        out = tmp_path

    def ask(*args, **kwargs):
        return dict(
            chapters=[dict(number=11, title="新程", conflict="何晏拦船", scenes=[], transition="一夜后赶到山城。")]
        )

    monkeypatch.setattr(q1_plan, "_ask", ask)
    before = (folder / "011-011.json").read_bytes()
    actual = q1_plan.outlines(Client(), design(), {}, {}, design()["volumes"][1], 11, 11, {}, [], [], [])
    assert actual[0]["conflict"] == "何晏拦船"
    assert actual[0]["transition"]
    assert (folder / "011-011.json").read_bytes() == before
