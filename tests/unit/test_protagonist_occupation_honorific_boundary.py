from __future__ import annotations

import pytest

from bestseller.services.book_design import extract_creation_protagonist_name
from bestseller.services.conception import _materialized_story_identity_conflicts


@pytest.mark.parametrize("honorific", ["师傅", "师父"])
def test_occupation_honorific_is_not_split_into_a_surname(honorific: str) -> None:
    premise = f"主桌客人质疑改菜，姚岑没有拿受伤{honorific}挡锅。"
    assert extract_creation_protagonist_name({"premise": premise}) == ""


def test_actual_paid_candidate_does_not_invent_a_second_protagonist() -> None:
    premise = (
        "主桌客人当众质疑改款扣肉，姚岑没有拿受伤师傅挡锅，"
        "而是亲自承认降本改菜、核对数量和上桌时间，"
        "把临时主厨的责任真正担到自己名下。"
    )
    assert _materialized_story_identity_conflicts(
        {"premise": premise, "story_spine": {
            "who": "姚岑，24岁的年轻厨师", "question": "姚岑能否完成婚宴？",
        }}
    ) == []


def test_a_real_name_after_an_explicit_title_is_preserved() -> None:
    assert extract_creation_protagonist_name(
        {"premise": "画师傅明，必须收拾残局。"}
    ) == "傅明"


def test_ambiguous_honorific_requires_explicit_identity() -> None:
    assert extract_creation_protagonist_name({"premise": "工程师傅明，必须收拾残局。"}) == ""
    assert extract_creation_protagonist_name(
        {"premise": "工程师傅明，必须收拾残局。", "creation_protagonist_name": "傅明"}
    ) == "傅明"
    assert extract_creation_protagonist_name({"premise": "工程师陈明，必须收拾残局。"}) == "陈明"


def test_a_real_protagonist_identity_conflict_still_blocks() -> None:
    conflicts = _materialized_story_identity_conflicts(
        {"premise": "画师傅明，必须收拾残局。", "story_spine": {"who": "姚岑，年轻厨师"}}
    )
    assert len(conflicts) == 1
    assert conflicts[0]["fact"] == "终稿简介主角：傅明"
    assert conflicts[0]["generated"] == "story_spine.who 主角：姚岑"


@pytest.mark.parametrize("name", ["傅云川", "傅明远", "傅红梅"])
def test_honorific_ambiguity_never_swallows_a_surname(name: str) -> None:
    assert extract_creation_protagonist_name({"premise": f"工程师{name}，必须收拾残局。"}) == ""
    assert extract_creation_protagonist_name({"premise": f"画师{name}，必须收拾残局。"}) == name
    assert extract_creation_protagonist_name(
        {"premise": f"工程师{name}，必须收拾残局。", "creation_protagonist_name": name}
    ) == name


def test_complete_honorific_does_not_promote_an_action_to_a_name() -> None:
    assert extract_creation_protagonist_name({"premise": "受伤师傅包扎伤口，随后返回。"}) == ""
