"""Deterministic introduction evidence; no model, disk or library access."""

from copy import deepcopy

import pytest

from bookengine.introductions import annotate_introductions, check_introductions, repair_issues


CARDS = [
    {"name": "沈照", "role": "protagonist", "identity": "码头账房", "aliases": ["阿照"]},
    {"name": "顾衡", "role": "support", "background": "来自北山药铺", "address": "顾先生"},
]


def introduction(text, cards=None, **kwargs):
    return [
        row
        for row in check_introductions(text, CARDS if cards is None else cards, **kwargs)
        if row["kind"] != "unregistered_alias"
    ]


def test_outline_annotations_are_ordered_first_appearances_without_mutation():
    outlines = [
        {"number": 1, "scenes": [{"characters": ["沈照", "顾衡"]}]},
        {"number": 2, "scenes": [{"characters": ["阿照", "顾衡"]}]},
    ]
    before = deepcopy((outlines, CARDS))
    result = annotate_introductions(outlines, CARDS)
    assert [r["name"] for r in result[0]["introductions"]] == ["沈照", "顾衡"]
    assert result[0]["introductions"][0]["origin"] == "码头账房"
    assert result[0]["introductions"][1]["origin"] == "来自北山药铺"
    assert result[1]["introductions"] == []
    result[0]["scenes"][0]["characters"].append("新客")
    assert (outlines, CARDS) == before


def test_prior_aliases_count_as_seen_and_unknown_outline_cast_is_not_approved():
    result = annotate_introductions([{"characters": ["沈照", "新来的访客"]}], CARDS, previously_seen=["阿照"])
    assert result[0]["introductions"] == [
        {"name": "新来的访客", "origin": "", "status": "Unknown", "source": "missing_card"}
    ]


def test_outline_finds_cards_in_scene_goals_but_does_not_treat_locations_as_cast():
    result = annotate_introductions([{"scenes": [{"where": "顾衡街", "goal": "沈照拿到账本"}]}], CARDS)
    assert [r["name"] for r in result[0]["introductions"]] == ["沈照"]


@pytest.mark.parametrize(
    "text",
    [
        "码头账房沈照走进门。",
        "沈照是码头账房，今天来核账。",
        "沈照走进门，他是码头账房。",
        "沈照走进门。他是码头账房。",
        "阿照是码头账房，今天来核账。",
    ],
)
def test_identity_near_first_mention_has_grounded_evidence(text):
    rows = introduction(text)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "Evidence"
    assert row["kind"] == "introduction_present"
    assert text[row["start"] : row["end"]] == row["quote"]
    assert "码头账房" in row["quote"]
    assert repair_issues(rows) == []


def test_background_or_descriptive_role_can_supply_origin():
    assert introduction("顾衡来自北山药铺，背着药箱。 ")[0]["status"] == "Evidence"
    cards = [{"name": "阮宁", "role": "驿站掌柜"}]
    assert introduction("驿站掌柜阮宁递来一封信。", cards)[0]["status"] == "Evidence"


@pytest.mark.parametrize(
    "text",
    [
        "沈照走进门。有人在谈论码头账房。",
        "沈照看见码头账房搬走了账本。",
        "沈照走进门，顾衡是码头账房。",
        "沈照走进门。\n\n他是码头账房。",
        "沈照不是码头账房。",
        "沈照问：‘谁是码头账房？’",
        "沈照是码头账房，还是驿站掌柜？",
        "沈照，码头账房顾衡的朋友，推开了门。",
    ],
)
def test_unbound_late_negated_or_questioned_identity_is_not_passed(text):
    row = next(r for r in introduction(text) if r["name"] == "沈照")
    assert row["kind"] == "missing_introduction"
    assert row["status"] == "Inference"
    assert repair_issues([row])


def test_seen_cast_does_not_require_reintroduction():
    assert introduction("沈照敲了敲门。", previously_seen=["阿照"]) == []


def test_missing_card_and_missing_identity_are_explicit_unknowns():
    assert introduction("阮宁推门进来。", [], names=["阮宁"])[0]["kind"] == "missing_card"
    assert introduction("门开了。", [])[0]["status"] == "Unknown"
    row = introduction("阮宁推门进来。", [{"name": "阮宁", "role": "support"}])[0]
    assert row["status"] == "Unknown"
    assert row["kind"] == "missing_origin"
    assert repair_issues([row])


def test_absent_expected_cast_does_not_get_positive_introduction_evidence():
    rows = introduction("院里没有人。", names=["沈照"])
    assert rows[0]["kind"] == "not_mentioned"
    assert rows[0]["status"] == "Unknown"


@pytest.mark.parametrize("text,alias", [("“跟铁哥走！”沈照喊。", "铁哥"), ("“叫我夜鸦。”沈照说。", "夜鸦")])
def test_unregistered_nickname_or_self_name_is_a_repair_candidate(text, alias):
    rows = check_introductions(text, CARDS, previously_seen=["沈照"])
    row = next(r for r in rows if r["kind"] == "unregistered_alias")
    assert row["name"] == alias
    assert row["status"] == "Inference"
    assert text[row["start"] : row["end"]] == row["quote"]
    assert alias in repair_issues(rows)[0]


def test_legal_aliases_address_and_ordinary_pronouns_are_not_flagged():
    cards = deepcopy(CARDS)
    cards[0]["aliases"] += ["铁哥", "夜鸦"]
    rows = check_introductions(
        "“跟铁哥走！叫我夜鸦。阿照，等一等，顾先生来了。我去找我哥哥。”", cards, previously_seen=["沈照", "顾衡"]
    )
    assert rows == []


def test_alias_string_is_one_alias_not_individual_characters():
    cards = [{"name": "阮宁", "identity": "驿站掌柜", "aliases": "夜鸦"}]
    assert introduction("夜鸦是驿站掌柜。", cards)[0]["status"] == "Evidence"
    assert any(r["kind"] == "unregistered_alias" for r in check_introductions("叫我夜。", cards))


def test_empty_body_and_ambiguous_aliases_remain_unknown():
    assert introduction("", names=["沈照"])[0]["status"] == "Unknown"
    cards = [{"name": "阮宁", "identity": "驿站掌柜", "aliases": ["小宁"]}, {"name": "宁远", "aliases": ["小宁"]}]
    assert all(r["status"] == "Unknown" for r in introduction("小宁推门进来。", cards))


def test_checks_do_not_mutate_cards_or_previous_seen():
    cards, seen = deepcopy(CARDS), ["顾衡"]
    snapshot = deepcopy((cards, seen))
    check_introductions("沈照是码头账房。", cards, previously_seen=seen)
    assert (cards, seen) == snapshot


@pytest.mark.parametrize("text", ["“铁哥，等等！”", "“我叫夜鸦。”", "“大家叫我夜鸦。”"])
def test_vocative_and_explicit_self_introduction_are_alias_candidates(text):
    assert any(r["kind"] == "unregistered_alias" for r in check_introductions(text, CARDS))


def test_appositive_identity_and_structured_background_are_grounded():
    cards = [{"name": "阮宁", "background": {"身份": "驿站掌柜"}}]
    assert introduction("阮宁，驿站掌柜，今天来收账。", cards)[0]["status"] == "Evidence"


def test_identity_about_the_named_character_is_not_enough_if_negated():
    rows = introduction("沈照，早已不是码头账房。")
    assert rows[0]["status"] == "Inference"


def test_bare_card_field_cannot_supply_identity_to_another_character():
    cards = [{"name": "阮宁", "identity": "掌柜"}, {"name": "宁远", "identity": "医师"}]
    rows = introduction("阮宁跟着医师宁远进了门。", cards)
    assert rows[0]["name"] == "阮宁"
    assert rows[0]["status"] == "Inference"
    assert rows[1]["name"] == "宁远"
    assert rows[1]["status"] == "Evidence"
