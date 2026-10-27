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

import pytest

from test_planner_services import build_project, lock_design_snapshot

from bestseller.services import planner as planner_services


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
    book["cast"] = cast
    world = planner_services._compile_source_bound_world_spec(project, premise, book)
    return project, premise, book, world


def test_named_cast_from_book_spec_lands_in_the_roster() -> None:
    project, premise, book, world = _project_with_cast([
        {"name": "沈砚", "role": "主角", "function": "领航"},
        {"name": "赵同", "role": "明线反派", "function": "航线赌局牵头"},
        {"name": "周砚", "role": "同伴", "function": "通风报信"},
    ])
    cast = planner_services._compile_source_bound_cast_spec(project, premise, book, world)
    assert {member["name"] for member in cast["supporting_cast"]} == {"赵同", "周砚"}
    assert not cast["antagonist"]  # A role label does not choose the primary antagonist.
    assert cast["_meta"]["approved_complete"] is False


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


def test_partial_roster_does_not_claim_a_bible_gate_exemption() -> None:
    project, premise, book, world = _project_with_cast([
        {"name": "周砚", "role": "同伴", "function": "通风报信", "tag": "墙根听壁角"},
    ])
    cast = planner_services._compile_source_bound_cast_spec(project, premise, book, world)
    assert cast["_meta"]["planning_status"] == "projection_only"
    assert cast["_meta"]["approved_complete"] is False
    for member in cast["supporting_cast"]:
        assert not member.get("metadata", {}).get("source_bound_minimal")
        assert not member.get("goal")
        assert not member.get("secret")
    with pytest.raises(planner_services.PlannerFallbackError, match="theme statement"):
        planner_services._ensure_book_spec_bible_fields(project, premise, book)


def test_no_cast_in_book_spec_keeps_the_old_shape() -> None:
    """构思没点名时行为逐字不变——修复不该改变它不需要改变的东西。"""

    project, premise, book, world = _project_with_cast([])
    cast = planner_services._compile_source_bound_cast_spec(project, premise, book, world)
    assert not cast["antagonist"]
    assert cast["supporting_cast"] == []


def test_source_bound_book_spec_does_not_invent_a_target_scaled_serial_contract() -> None:
    project, premise, book, world = _project_with_cast([])
    engine = book["series_engine"]
    assert engine["unit_families"] == []
    assert engine["accumulation_tracks"] == []
    assert engine["phase_transitions"] == []
    assert engine["renewal_sources"] == []
    assert not engine.get("endgame_direction")
    assert "expected_character_count" not in book
    assert book["_meta"]["approved_complete"] is False


# ── 每个搬来的角色要带一条与主角的关系 ────────────────────────────────────
#
# 2026-08-22 真机（custom-xuanhuan-1787383584）：契约修好后 book_spec.cast=12、
# characters=12，**relationships 仍然是 0**。
#
# 追下去有两条路会生成关系，编译器一条都没走：
#   * `cast_spec.<character>.relationships`（主路径，`upsert_cast_spec`
#     遍历 `all_characters()` 逐条建）
#   * `cast_spec.conflict_map`（补充，只表达冲突）
# 编译器里 `conflict_map` 初始化成 `[]` 之后从未被填，搬来的配角也没有
# `relationships` 字段——又一个硬编码空数组。
#
# 后果是没有关系张力 → 对话没有动机（真机对话占比 7.6%，人类 20.7%）。
#
# ⚠️ 零发明：关系对象只要 character / type / tension 三个字段，全部取自
# 已批准构思里的 role 与 function，不新编任何情节。


def test_role_and_function_do_not_fabricate_personal_relationships() -> None:
    project, premise, book, world = _project_with_cast([
        {"name": "赵同", "role": "主要对手", "function": "升级封锁"},
        {"name": "周砚", "role": "同伴", "function": "通风报信"},
    ])
    cast = planner_services._compile_source_bound_cast_spec(project, premise, book, world)
    for member in cast["supporting_cast"]:
        assert "relationships" not in member
        assert "goal" not in member
        assert "strength" not in member
        assert member["function"] in {"升级封锁", "通风报信"}


def test_literal_relationship_fields_are_preserved_without_role_conversion() -> None:
    relation = {"character": "沈砚", "type": "航线竞争者", "tension": "争用唯一的修复窗口"}
    project, premise, book, world = _project_with_cast([
        {"name": "赵同", "role": "主要对手", "function": "升级封锁", "relationships": [relation]},
    ])
    cast = planner_services._compile_source_bound_cast_spec(project, premise, book, world)
    assert cast["supporting_cast"][0]["relationships"] == [relation]


def test_no_relationships_when_the_concept_named_nobody() -> None:
    project, premise, book, world = _project_with_cast([])
    cast = planner_services._compile_source_bound_cast_spec(project, premise, book, world)
    assert cast["supporting_cast"] == []
    assert not cast["antagonist"]


def _compile_gender_case(
    *,
    channel: str,
    premise: str,
    protagonist_name: str,
    explicit_gender: str | None = None,
) -> dict:
    project = build_project()
    project.metadata_json["creation_intent_contract"] = {
        "audience_orientation": channel,
    }
    lock_design_snapshot(
        project,
        protagonist_name=protagonist_name,
        reader_promise=premise,
        core_story_engine="推进已批准主线",
    )
    protagonist = {"name": protagonist_name}
    if explicit_gender is not None:
        protagonist["gender"] = explicit_gender
    return planner_services._compile_source_bound_cast_spec(
        project,
        premise,
        {"protagonist": protagonist},
        {},
    )


def test_prose_pronoun_is_not_an_unreviewed_structured_gender_fact() -> None:
    cast = _compile_gender_case(channel="male", premise="沈见微在档案馆工作，她决定查清火灾。", protagonist_name="沈见微")
    assert cast["protagonist"]["gender"] == "unknown"
    assert cast["protagonist"]["pronoun_set_zh"] is None
    assert "protagonist.gender" in cast["_meta"]["unresolved_fields"]


def test_audience_channel_and_name_do_not_supply_gender() -> None:
    cast = _compile_gender_case(channel="female", premise="顾沉在档案馆工作，他决定查清火灾。", protagonist_name="顾沉")
    assert cast["protagonist"]["gender"] == "unknown"
    assert cast["protagonist"]["pronoun_set_en"] is None


def test_source_bound_explicit_gender_field_beats_premise_and_channel() -> None:
    cast = _compile_gender_case(
        channel="male",
        premise="顾沉在档案馆工作，他决定查清火灾。",
        protagonist_name="顾沉",
        explicit_gender="female",
    )

    assert cast["protagonist"]["gender"] == "female"
    assert cast["protagonist"]["pronoun_set_zh"] == "她"


def test_source_bound_gender_stays_unknown_without_evidence() -> None:
    cast = _compile_gender_case(
        channel="male",
        premise="主角在档案馆工作，调查一场火灾。",
        protagonist_name="沈砚",
    )

    assert cast["protagonist"]["gender"] == "unknown"
    assert cast["protagonist"]["pronoun_set_zh"] is None
    assert cast["protagonist"]["pronoun_set_en"] is None
    assert "protagonist.gender" in cast["_meta"]["unresolved_fields"]
    assert cast["_meta"]["approved_complete"] is False
