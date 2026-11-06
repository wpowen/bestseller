from bookengine.solution_substance import solution_nouns, solution_issues, solution_context, observe_solutions
from bookengine.contracts import observations
from bookengine.outline_diversity import repair_batch


def bible():
    return {"volumes": [{"number": 1, "start": 1, "end": 10}, {"number": 2, "start": 11, "end": 20}]}


def test_different_labels_do_not_hide_same_array_means():
    history = [
        dict(number=n, solution_type=str(n), solution_method=method)
        for n, method in enumerate(["阵眼", "阵钉", "阵纹"], 1)
    ]
    batch = [dict(number=4, solution_type="谋略", solution_method="阵枢")]
    issues = solution_issues(bible(), history, batch)
    assert issues[0]["noun"] == "阵"
    assert issues[0]["targets"] == [4]
    assert not solution_issues(bible(), history, [dict(batch[0], number=11)])
    assert solution_nouns({"content": "绕开阵眼，以钥匙开启禁门"}) == ["钥匙", "阵"]


def test_explicit_extraction_requires_verbatim_nouns_and_keeps_receipt():
    text = "他用铜秤压住门锁，闸门终于打开。"
    raw = {
        "solutions": [
            {
                "type": "借力",
                "content": "压开闸门",
                "quote": text,
                "target_noun": "门锁",
                "method_noun": "铜秤",
                "primary": True,
            }
        ]
    }
    enriched = observe_solutions(raw, text)
    rows = observations(enriched, text, 4)["solutions"]
    assert rows[0]["method_noun"] == "铜秤"
    assert rows[0]["quote"] == text
    assert set(solution_nouns(rows[0])) == {"门锁", "铜秤"}
    raw["solutions"][0]["method_noun"] = "神符"
    assert observe_solutions(raw, text)["solutions"][0]["method_noun"] is None


def test_recent_ten_context_and_stage_cap_are_independent():
    history = [dict(number=n, solution_method="钥匙") for n in [1, 2, 3]]
    context = solution_context(bible(), history, 15)
    assert context["recent_ten"] == []
    assert context["stage_counts"] == {}
    assert solution_context(bible(), history, 10)["stage_counts"] == {"钥匙": 3}


def test_batch_rewrites_actual_substance_before_persisting(tmp_path):
    history = [dict(number=n, solution_method="阵眼") for n in range(1, 4)]
    batch = [dict(number=4, title="远航", solution_method="阵钉")]
    calls = []

    def rewrite(current, issues, targets, used, attempt):
        calls.append(issues)
        return {"chapters": [dict(batch[0], solution_method="账本")]}

    result = repair_batch(tmp_path, "004-004", history, batch, rewrite, bible=bible())
    assert result[0]["solution_method"] == "账本"
    assert any(i["kind"] == "solution_substance" for i in calls[0])


def test_new_plan_cannot_evade_cap_by_omitting_substance():
    value = dict(bible(), plot_contract_version=1)
    issues = solution_issues(value, [], [dict(number=1, solution_type="智取")])
    assert issues[0]["kind"] == "solution_substance_missing"
    assert issues[0]["targets"] == [1]


def test_array_family_excludes_weather_and_injury_words():
    assert solution_nouns({"content": "趁一阵风起，用阵痛诱敌"}) == []
    assert solution_nouns({"method_noun": "阵风"}) == ["阵风"]
