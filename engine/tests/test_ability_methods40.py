import json
from pathlib import Path

from bookengine.plot_diagnostics import plot_findings
from bookengine.solution_substance import solution_issues, solution_context


def test_saved_subabilities_do_not_compete_with_ordinary_methods():
    fixture = json.loads((Path(__file__).parent / "fixtures/ability_methods40.json").read_text())
    report = plot_findings(fixture["bible"], fixture["chapters"])
    assert "蛮力" not in report["solution_substance"]["distribution"]
    assert report["core_ability"]["applications"]["distribution"]["蛮力"] == 11
    assert report["solution_substance"]["excess_count"] == 0


def test_ability_target_combination_has_own_stage_cap_in_plan_and_observation():
    bible = dict(
        power={"name": "借影", "abilities": ["听风", "读影"]},
        volumes=[dict(number=1, start=1, end=5), dict(number=2, start=6, end=8)],
    )
    batch = [dict(number=n, solution_method="听风", solution_target="守卫") for n in range(1, 4)]
    issues = solution_issues(bible, [], batch)
    assert len(issues) == 1 and issues[0]["kind"] == "ability_repetition"
    assert issues[0]["targets"] == [3]
    assert solution_context(bible, batch, 4)["stage_counts"] == {}
    assert solution_context(bible, batch, 4)["ability_cap"] == 2
    assert not solution_issues(bible, batch[:2], [dict(batch[2], solution_target="暗门")])
    assert not solution_issues(bible, batch, [dict(batch[2], number=6)])
    ordinary = [dict(number=n, solution_method="铜秤", solution_target="守卫") for n in range(1, 5)]
    assert solution_issues(bible, [], ordinary)[0]["kind"] == "solution_substance"


def test_unknown_target_does_not_fabricate_repeated_ability_or_hide_ordinary_tool():
    bible = dict(power={"name": "借影"}, volumes=[dict(number=1, start=1, end=4)])
    batch = [dict(number=n, solution_method="借影") for n in range(1, 5)]
    assert not solution_issues(bible, [], batch)
    result = plot_findings(
        bible,
        [
            dict(
                number=1,
                meta={
                    "solutions": [
                        dict(primary=True, method_noun="铜秤", target_noun="铁门", quote="他将铜秤抛向铁门。")
                    ]
                },
                text="他将铜秤抛向铁门。",
            )
        ],
    )
    assert result["solution_substance"]["distribution"] == {"铜秤": 1}


def test_ability_names_survive_tool_family_normalization_and_conjunction_characters():
    bible = dict(power={"name": "破阵", "abilities": ["与影同行"]}, volumes=[dict(number=1, start=1, end=4)])
    from bookengine.solution_substance import chapter_nouns
    from bookengine.ability_methods import ability_combination

    assert chapter_nouns(dict(solution_method="破阵"), bible=bible) == ["破阵"]
    assert not solution_issues(bible, [], [dict(number=n, solution_method="破阵") for n in range(1, 5)])
    assert ability_combination(bible, "与影同行") == ("与影同行",)
    assert solution_issues(bible, [], [dict(number=n, solution_method="阵钉") for n in range(1, 5)])


def test_old_plan_cache_rechecks_new_ability_cap_and_preserves_exhaustion(tmp_path):
    from bookengine import q1_plan
    from bookengine.q1_schema import DATA
    from bookengine.storage import save, load
    from test_chapter_rhythm39 import OfflineClient, chapter

    bible = dict(power={"name": "听风"}, volumes=[dict(number=1, start=1, end=5)])
    rows = [
        dict(
            chapter(n, "情感"),
            solution_method="听风",
            solution_target="暗门",
            core_required=True,
            core_application="运用听风辨认暗门的具体缺口",
        )
        for n in range(1, 4)
    ]
    save(
        tmp_path / "outline-diversity/001-003.json",
        dict(status="passed", structure_checks_version=5, chapters=rows, planned_chapters=rows, plot_history=[]),
    )
    client = OfflineClient(tmp_path, [dict(chapters=[dict(rows[2], solution_target="铁窗")])] * 3)
    result = q1_plan.outlines(client, bible, {}, DATA["profile"], dict(number=1, start=1, end=5), 1, 3, {}, [], [], [])
    assert client.calls and not solution_issues(bible, [], result)
    original = load(tmp_path / "outline-diversity/001-003.json")
    assert original["chapters"] == rows
    resumed = OfflineClient(tmp_path)
    assert (
        q1_plan.outlines(resumed, bible, {}, DATA["profile"], dict(number=1, start=1, end=5), 1, 3, {}, [], [], [])
        == result
    )
    assert not resumed.calls


def test_ability_limit_groups_same_kind_of_object_without_renaming_capability():
    bible = dict(power={"name": "听风"}, volumes=[dict(number=1, start=1, end=5)])
    batch = [
        dict(number=n, solution_method="听风", solution_target=target)
        for n, target in enumerate(["侧门", "木门", "封门"], 1)
    ]
    issues = solution_issues(bible, [], batch)
    assert len(issues) == 1 and issues[0]["kind"] == "ability_repetition"
    assert issues[0]["target_class"] == "门"
    assert issues[0]["targets"] == [3]
    assert not solution_issues(bible, batch[:2], [dict(batch[2], solution_target="账本")])

    chapters = []
    for row in batch:
        text = f"他借听风辨明{row['solution_target']}的机关。"
        chapters.append(
            dict(
                number=row["number"],
                text=text,
                meta={
                    "solutions": [
                        dict(primary=True, method_noun="听风", target_noun=row["solution_target"], quote=text)
                    ]
                },
            )
        )
    excess = plot_findings(bible, chapters)["core_ability"]["applications"]["excess"]
    assert len(excess) == 1 and excess[0]["target_class"] == "门"
    assert excess[0]["excess_chapters"] == [3]
