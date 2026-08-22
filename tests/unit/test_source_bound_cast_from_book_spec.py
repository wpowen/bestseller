"""source-bound 编译器必须把已批准构思点过名的角色搬进 CastSpec。

2026-08-22 真机定罪（《书院笔仙》custom-xuanhuan-1787328262）：一本完本
50 章的书，`characters` 表**只有 1 行**（全库其余书都是 0 行）。

    cast_spec.antagonist      = null
    cast_spec.supporting_cast = []
    book_spec.cast            = 14 个具名角色（含 role「明线反派」的赵同）

`_compile_source_bound_cast_spec` 读了 book_spec 的 protagonist /
unique_hook / stakes / power_system / antagonist_ladder 和 world_spec 的
factions，**唯独没读 book_spec.cast**，两个槽是硬编码的字面量
`None` 与 `[]`。

后果不是「少几行数据」，是配角没有 goal / voice_profile → 纸片人 →
主角没有对手压力、对话没有区分度（真机：对话占比 7.6%，人类 20.7%）。

⚠️ 编译器的原则是「**不发明**」，不是「不搬」：docstring 里禁的是童年、
亲属、秘密出身、年龄这类凭空传记。book_spec.cast 里的名字是**已批准
构思自己点的名**，搬它恰恰是 source-bound 的本意。所以本修复只搬
name / role / function / tag，一个字段都不新编。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。

import json

from bestseller.services import planner as planner_services

from test_planner_services import build_project, lock_design_snapshot


def _project_with_cast(cast: list[dict[str, str]]) -> tuple[object, str, dict, dict]:
    project = build_project()
    project.title = "回声航线"
    project.genre = "科幻"
    project.sub_genre = "太空冒险"
    project.target_chapters = 50
    premise = "巡航员沈砚发现封锁航线会回应他的导航脉冲，每次修复都会让下一段航路主动改道。"
    project.metadata_json["story_enhancers"] = {"cost_style": "minimal"}
    lock_design_snapshot(
        project,
        protagonist_name="沈砚",
        reader_promise=premise,
        core_story_engine="每轮用导航脉冲确认一段航路并面对升级的阻力。",
    )
    book = planner_services._compile_source_bound_book_spec(project, premise)
    book = planner_services._ensure_book_spec_bible_fields(project, premise, book)
    book["cast"] = cast
    world = planner_services._compile_source_bound_world_spec(project, premise, book)
    return project, premise, book, world


def test_named_cast_from_book_spec_lands_in_the_roster() -> None:
    project, premise, book, world = _project_with_cast(
        [
            {"name": "沈砚", "role": "主角", "function": "领航", "tag": "袖口常沾墨"},
            {"name": "赵同", "role": "明线反派", "function": "航线赌局牵头", "tag": "逼稿成瘾"},
            {"name": "周砚", "role": "同伴", "function": "通风报信", "tag": "墙根听壁角"},
        ]
    )
    cast = planner_services._compile_source_bound_cast_spec(project, premise, book, world)

    assert cast["antagonist"] is not None, "book_spec 点了名的明线反派必须落到 antagonist"
    assert cast["antagonist"]["name"] == "赵同"
    roster = {c["name"] for c in cast["supporting_cast"]}
    assert "周砚" in roster
    # 主角不许在配角名册里重复出现——重复会让命名池与身份清单各自认一份。
    assert "沈砚" not in roster
    assert "赵同" not in roster


def test_compiler_carries_only_what_the_concept_named_and_invents_nothing() -> None:
    """搬 name/role/function/tag，不新编年龄、童年、亲属、秘密出身。"""

    project, premise, book, world = _project_with_cast(
        [
            {"name": "沈砚", "role": "主角", "function": "领航", "tag": "袖口常沾墨"},
            {"name": "周砚", "role": "同伴", "function": "通风报信", "tag": "墙根听壁角"},
        ]
    )
    cast = planner_services._compile_source_bound_cast_spec(project, premise, book, world)
    ally = next(c for c in cast["supporting_cast"] if c["name"] == "周砚")

    # 已批准构思里有的，必须搬过来（否则配角就是纸片人）。
    assert "通风报信" in json.dumps(ally, ensure_ascii=False)
    assert "墙根听壁角" in json.dumps(ally, ensure_ascii=False)
    # 构思里没有的，一个都不许长出来。
    assert ally.get("age") is None
    for invented in ("童年", "父母", "生于", "自幼", "多年前"):
        assert invented not in json.dumps(ally, ensure_ascii=False)


def test_roster_entries_are_flagged_source_bound_so_the_bible_gate_exempts_them() -> None:
    """新搬的配角必须带 source_bound_minimal 标记。

    没有它，`bible_gate._is_source_bound_minimal_character` 不豁免，
    这些只有 name/role/function 的条目会因缺 quirk / 缺 history 被判违规
    ——修一个空名册反而把书卡死，正是本项目反复定罪的门禁自伤。
    """

    project, premise, book, world = _project_with_cast(
        [
            {"name": "沈砚", "role": "主角", "function": "领航", "tag": "袖口常沾墨"},
            {"name": "周砚", "role": "同伴", "function": "通风报信", "tag": "墙根听壁角"},
        ]
    )
    cast = planner_services._compile_source_bound_cast_spec(project, premise, book, world)
    for entry in cast["supporting_cast"]:
        meta = entry.get("metadata") or {}
        assert meta.get("source_bound_design") is True
        assert meta.get("source_bound_minimal") is True
        assert "book_spec.cast" in (meta.get("source_fields") or [])


def test_no_cast_in_book_spec_keeps_the_old_shape() -> None:
    """构思没点名时行为逐字不变——修复不该改变它不需要改变的东西。"""

    project, premise, book, world = _project_with_cast([])
    cast = planner_services._compile_source_bound_cast_spec(project, premise, book, world)
    assert cast["antagonist"] is None
    assert cast["supporting_cast"] == []
