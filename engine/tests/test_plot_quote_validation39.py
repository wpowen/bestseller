from bookengine.core_ability import verify_core_use
from bookengine.solution_substance import observe_solutions


BIBLE = {"title": "镜潮", "power": {"name": "镜潮", "rule": "镜潮复制水流的力量"}}


def test_whitespace_only_quote_difference_uses_original_source_slice():
    text = "她催动镜潮复制水流。\n\n镜潮让小舟驶离了漩涡。"
    raw = dict(
        decisive=True, mechanism="镜潮", action_quote="她催动镜潮复制水流。", result_quote="镜潮让小舟驶离了漩涡。"
    )
    assert verify_core_use(raw, text, BIBLE)["status"] == "verified"
    solution = dict(
        type="借力",
        content="复制水流避开漩涡",
        target_noun="小舟",
        method_noun="镜潮",
        quote=text.replace("\n\n", "\n"),
    )
    result = observe_solutions({"solutions": [solution]}, text)["solutions"][0]
    assert result["noun_status"] == "Evidence"
    assert result["quote"] == text
    assert solution["quote"] != text  # caller evidence was not overwritten


def test_ordinary_hesitation_negation_is_not_ability_non_use():
    text = "她没有再犹豫，催动镜潮复制水流的力量。镜潮使小舟终于驶离了漩涡。"
    raw = dict(
        decisive=True,
        mechanism="镜潮",
        action_quote="她没有再犹豫，催动镜潮复制水流的力量。",
        result_quote="镜潮使小舟终于驶离了漩涡。",
    )
    assert verify_core_use(raw, text, BIBLE)["status"] == "verified"


def test_explicit_non_use_and_failure_remain_unknown():
    for action in ("她没有催动镜潮，只是举起普通木桨。", "她不借用镜潮，只是举起普通木桨。"):
        outcome = "普通木桨让小舟终于驶离了漩涡。"
        raw = dict(decisive=True, mechanism="镜潮", action_quote=action, result_quote=outcome)
        assert verify_core_use(raw, action + outcome, BIBLE)["status"] == "unknown"
    text = "她催动镜潮复制水流。镜潮失效了，她改用普通木桨终于脱身。"
    raw = dict(
        decisive=True,
        mechanism="镜潮",
        action_quote="她催动镜潮复制水流。",
        result_quote="镜潮失效了，她改用普通木桨终于脱身。",
    )
    assert verify_core_use(raw, text, BIBLE)["status"] == "unknown"


def test_whitespace_matching_does_not_join_non_contiguous_or_changed_words():
    from bookengine.quote_text import literal_quote

    assert (
        literal_quote("水流推开木门。\n\n她救出了同伴。", "水流推开木门。她救出了同伴。")
        == "水流推开木门。\n\n她救出了同伴。"
    )
    assert literal_quote("水流推开木门。门后有人反锁。她救出了同伴。", "水流推开木门。她救出了同伴。") is None
    assert literal_quote("水流推开木门。", "水流撞开木门。") is None
    assert literal_quote("水流推开木门。", "水流推开木门！") is None
    assert literal_quote("水流推开木门。", "  ") is None


def test_non_use_forms_do_not_pass_after_ordinary_negation_fix():
    for action in (
        "她尚未唤出镜潮，只是举起普通木桨。",
        "她没有足够力量催动镜潮，只能举起木桨。",
        "她并未让镜潮发动，只是举起普通木桨。",
    ):
        outcome = "普通木桨让小舟终于驶离了漩涡。"
        raw = dict(decisive=True, mechanism="镜潮", action_quote=action, result_quote=outcome)
        assert verify_core_use(raw, action + outcome, BIBLE)["status"] == "unknown"
