"""BookSpec 契约必须点名要角色清单，否则人物能力永远没有输入。

2026-08-22 真机定罪，分两步：

**第一步**（已修）：`_compile_source_bound_cast_spec` 把 `antagonist` 和
`supporting_cast` 硬编码成 `None` / `[]`，没读 `book_spec.cast`。
《书院笔仙》的 book_spec 里有 14 个具名角色，一个都没被搬进去。

**第二步**（本修复）：修完之后用同参数建第二本书，`characters` 仍然
只有 1 行——因为**这本书的 book_spec 根本没有 cast 字段**。

    《书院笔仙》book_spec 键：… cast, antagonist_pipeline, writing_constraints …
    新书 book_spec 键：      … chapter_plan …（无 cast）

查 `_book_spec_prompts` 才发现：契约要求的是
title / logline / genre / … / naming_pool / protagonist / stakes /
series_engine / unique_hook / benchmark_works——**唯独没有角色清单**。
《书院笔仙》那 14 个是模型自己多给的 extra 字段。

所以上一条修复只在「模型碰巧输出 cast」时有效。这是假绿的经典形状：
单测里我手写 `book["cast"] = [...]` 所以通过，真机路径根本不产这个字段。

⚠️ 契约只要求**结构**（name / role / function），不给任何具体角色词表——
给词表就是种词。数量锚在已有的 `expected_character_count` 上。
"""

from __future__ import annotations

from test_planner_services import build_project

# ruff: noqa: RUF001, RUF002 — 中文标点是刻意的。
from bestseller.services import planner as planner_services


def _user_prompt(language: str) -> str:
    project = build_project()
    project.genre = "玄幻"
    project.metadata_json["language"] = language
    _system, user = planner_services._book_spec_prompts(
        project, "巡航员沈砚发现封锁航线会回应他的导航脉冲。", {}
    )
    return user


def test_zh_contract_names_the_cast_roster() -> None:
    user = _user_prompt("zh-CN")
    assert "cast" in user, "契约必须点名 cast——没点名，模型就只是偶尔给"


def test_zh_contract_demands_structure_not_a_vocabulary() -> None:
    """要的是形状（姓名/定位/职能），不是给一份角色词表——给词表就是种词。"""

    user = _user_prompt("zh-CN")
    for field in ("name", "role", "function"):
        assert field in user
    # 不许出现具体的角色名/原型词表
    for seeded in ("师姐", "管家", "老者", "反派甲"):
        assert seeded not in user


def test_roster_size_is_anchored_to_the_existing_field() -> None:
    """数量锚在 expected_character_count 上，不另立一个新常数。"""

    user = _user_prompt("zh-CN")
    idx = user.find("cast")
    assert idx != -1
    assert "expected_character_count" in user


def test_en_contract_names_it_too() -> None:
    user = _user_prompt("en-US")
    assert "cast" in user
