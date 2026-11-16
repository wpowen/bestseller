"""Instruction 68 (E5): three books shared 以宁、元铮、冬岚… because every name was forced into one table."""

from pathlib import Path

import yaml

from bookengine.name_pool import clashes, naming_profile, used_given_names

RULES = yaml.safe_load((Path(__file__).parents[1] / "data/polish_rules.yaml").read_text())
LIBRARY = [
    {"book": "webui/a", "names": ["秦野", "苏以宁", "石元铮", None]},
    {"book": "webui/b", "names": ["林遥", "顾以宁"]},
    {"book": "webui/current", "names": ["沈青砚"]},
]


def test_planner_gets_names_to_avoid_instead_of_a_menu():
    profile = naming_profile(RULES, LIBRARY, "webui/current")
    assert "name_pool" not in profile
    assert "自拟" in profile["naming"]
    assert {"以宁", "元铮"} <= set(profile["avoid_given_names"])
    assert set(RULES["given_names"]) <= set(profile["avoid_given_names"])
    assert "青砚" not in profile["avoid_given_names"]
    assert [row["book"] for row in profile["unavailable_names"]] == ["webui/a", "webui/b"]


def test_only_a_real_clash_with_another_book_forces_a_rename():
    used = {"苏以宁", "石元铮", "林遥"}
    assert clashes("苏以宁", used)
    assert clashes("白以宁", used)
    assert clashes("林遥", used)
    assert not clashes("沈青砚", used)
    assert not clashes("雷九", used)


def test_other_books_given_names_exclude_the_current_book():
    assert used_given_names(LIBRARY, "webui/current") == {"以宁", "元铮"}
