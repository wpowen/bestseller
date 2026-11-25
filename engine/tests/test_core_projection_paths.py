from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from bookengine import q1_extract, q1_plan
from bookengine.solution_sequences import solution_plot_history
from bookengine.storage import save
from test_chapter_rhythm39 import OfflineClient, chapter


FUTURE = "从塔顶取回旧钟，三年后才把它交给城主"
ACTION = "循钟声找出卡在门缝的簧片"


def scene_and_design():
    development = dict(new_use=FUTURE, new_cost="忘记故乡的钟声", new_limit="仅能隔墙辨音")
    stage = dict(number=1, start=1, end=10, core_development=development)
    bible = dict(
        power={"name": "听钟", "rule": "从钟声辨认暗处"},
        volumes=[stage],
        canon=dict(ladder={}, hook_types=[], payoff_menu=[], solution_menu=[]),
    )
    row = dict(chapter(1, "对抗"), core_development=development, core_required=True, core_application=ACTION)
    row["scenes"][0].update(action=ACTION, ability_use=ACTION)
    return bible, row


def assert_local_contract(contract):
    assert "core_development" not in contract
    assert FUTURE not in json.dumps(contract, ensure_ascii=False)
    assert ACTION in contract["core_application"]


def test_writer_context_projects_saved_outline_without_changing_the_plan():
    bible, row = scene_and_design()
    saved = deepcopy(row)
    sections = dict(q1_extract.context(bible, {}, bible["volumes"][0], row, {}, [], [], ""))
    writer_outline, _ = sections["本章章纲"].split("\n阶段退场约束：")
    assert_local_contract(json.loads(writer_outline))
    assert row == saved


def test_chapter_entry_projects_legacy_actions_before_all_writer_sections():
    from support_minimal_b import CaptureClient, MinimalBTests

    class DraftCaptured(Exception):
        pass

    fixture = MinimalBTests()
    fixture.setUp()
    try:
        bible, old = scene_and_design()
        fixture.bible.update(power=bible["power"], volumes=bible["volumes"])
        old["number"] = 2
        legacy_action = f"用{FUTURE}解决：{ACTION}"
        old["core_application"] = legacy_action
        old["scenes"][0].update(action=legacy_action, choice=legacy_action, ability_use=FUTURE)
        saved = deepcopy(old)
        client = CaptureClient(fixture.out, [], {"r2-ch2-draft": DraftCaptured()})
        with pytest.raises(DraftCaptured):
            fixture.run_chapter(client, outline=old)
        prompt = client.calls[-1]["prompt"]
        assert ACTION in prompt
        assert FUTURE not in prompt
        assert "core_development" not in prompt
        assert old == saved
    finally:
        fixture.doCleanups()


@pytest.mark.parametrize("final", [False, True])
def test_initial_and_final_extraction_use_a_chapter_contract(tmp_path, monkeypatch, final):
    bible, row = scene_and_design()
    saved = deepcopy(row)
    contracts = []

    def capture(client, stage, prompt, sections, schema, **options):
        contracts.append(json.loads(dict(sections)["本章要求"]))
        return {}

    monkeypatch.setattr(q1_extract, "ask", capture)
    client = SimpleNamespace(out=tmp_path, prefix="")
    q1_extract.extract_state(client, "test-state", "钟声停了。", {}, bible, row, [], final=final)
    assert len(contracts) == 1
    assert_local_contract(contracts[0])
    assert row == saved


@pytest.mark.parametrize("normalized", [False, True])
def test_resume_projects_old_plan_without_rewriting_its_checkpoint(tmp_path, normalized):
    bible, row = scene_and_design()
    stage = bible["volumes"][0]
    path = tmp_path / "outline-diversity/001-001.json"
    report = dict(
        status="passed",
        chapters=[row],
        structure_checks_version=q1_plan.STRUCTURE_CHECKS_VERSION,
        rhythm_contract_version=q1_plan.RHYTHM_VERSION,
        rhythm_issues_after=q1_plan.rhythm_issues(bible, [], [row]),
        plot_history=solution_plot_history(bible, []),
    )
    if normalized:
        report["planned_chapters"] = [row]
    save(path, report)
    original = path.read_bytes()
    client = OfflineClient(tmp_path)
    result = q1_plan.outlines(client, bible, {}, {}, stage, 1, 1, {}, [], [], [])
    assert_local_contract(result[0])
    assert path.read_bytes() == original
    assert client.calls == []


def test_fresh_planner_receives_full_stage_and_returns_only_local_action(tmp_path, monkeypatch):
    bible, row = scene_and_design()
    calls = []

    def respond(client, kind, key, sections, redo=False):
        calls.append(dict(sections)[q1_plan.CORE_RULE])
        return {"chapters": [deepcopy(row)]}

    monkeypatch.setattr(q1_plan, "_ask", respond)
    result = q1_plan.outlines(OfflineClient(tmp_path), bible, {}, {}, bible["volumes"][0], 1, 1, {}, [], [], [])
    assert calls[0]["required"][1]["core_development"] == bible["volumes"][0]["core_development"]
    assert_local_contract(result[0])
