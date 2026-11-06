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
