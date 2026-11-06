import json
from types import SimpleNamespace

from bookengine.core_evidence import find_core_evidence, observe_core_use

BIBLE = {"power": {"name": "听风", "abilities": ["读影"]}}
PLAN = {"number": 1, "core_required": True, "core_application": "听风辨明暗道并救出同伴", "solution_target": "暗道"}


def test_plan_guides_search_but_only_literal_action_and_result_count():
    text = "他催动听风，听风让暗道里的脚步清晰起来。"
    found = find_core_evidence(text, BIBLE, PLAN)
    assert found["status"] == "verified"
    assert found["action_quote"] in text and found["result_quote"] in text
    assert found["evidence_method"] == "plan_guided_literal"


def test_direct_physical_outcome_does_not_need_a_magic_causal_word():
    text = "她借读影辨出了锁芯里的缺口，沿着缺口推开了暗门。"
    found = find_core_evidence(text, BIBLE, dict(PLAN, core_application="读影辨认锁芯", solution_target="锁芯"))
    assert found["status"] == "verified"
    assert found["result_quote"] in text


def test_mentions_negations_failure_and_unrelated_result_are_not_verified():
    for text in [
        "他想起听风，接着用普通钥匙打开了门。",
        "他没有催动听风，普通钥匙让铁门终于打开。",
        "他催动听风却失效，改用木桨终于脱身。",
        "他催动听风。另一边，守卫用钥匙打开了门。",
        "“他催动听风，让暗门打开了。”她猜测。",
        "他打算借听风，听风让暗门打开的故事他听过。",
        "他谈起听风，听风使他害怕起来。",
        "他借听风，守卫用普通钥匙推开了暗门。",
    ]:
        assert find_core_evidence(text, BIBLE, PLAN)["status"] == "unknown"


def test_empty_response_is_audited_and_unknown_not_invented(tmp_path):
    calls = []
    client = SimpleNamespace(out=tmp_path, prefix="")

    def ask(*args, **kwargs):
        calls.append(args)
        return {}

    found = observe_core_use({}, "他坐在门口休息。", BIBLE, PLAN, client, "ch1", ask)
    assert len(calls) == 1
    assert found["status"] == "unknown"
    assert found["unknown_reason"] == "empty_response"
    assert json.loads((tmp_path / "steps/ch1-core-evidence.json").read_text()) == found


def test_plan_match_avoids_model_call_and_keeps_source_quotes(tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("matching body must not cause model request")

    text = "他催动听风，听风让暗道里的脚步清晰起来。"
    found = observe_core_use({}, text, BIBLE, PLAN, SimpleNamespace(out=tmp_path), "ch1", forbidden)
    assert found["status"] == "verified"
    assert found["plan"]["core_application"] == PLAN["core_application"]


def test_legacy_and_no_power_chapters_keep_zero_extra_io():
    def forbidden(*args, **kwargs):
        raise AssertionError("legacy plan must not create a new request")

    client = SimpleNamespace()
    assert observe_core_use({}, "他坐在门口休息。", {}, PLAN, client, "legacy", forbidden)["status"] == "unknown"
    assert observe_core_use({}, "他坐在门口休息。", BIBLE, {}, client, "legacy", forbidden)["status"] == "unknown"


def test_minor_use_cannot_override_main_conflict_or_explicit_model_nondecisive():
    from bookengine.core_evidence import read_core_use

    plan = dict(PLAN, solution_target="同伴", core_application="听风救出被困同伴")
    for text in ["他催动听风，听风让茶杯里的水纹清晰起来。", "他借听风看见落叶，但同伴仍被困。"]:
        assert find_core_evidence(text, BIBLE, plan)["status"] == "unknown"
    text = "他催动听风，听风让暗道里的脚步清晰起来。"
    raw = dict(decisive=False, mechanism="听风", action_quote=text, result_quote=text)
    assert read_core_use(raw, text, BIBLE, PLAN)["status"] == "not_decisive"
