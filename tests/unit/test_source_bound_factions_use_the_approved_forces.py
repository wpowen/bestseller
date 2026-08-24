"""派系不许叫「主角的当前行动单元」——构思已经给了结构化的势力表。

2026-08-24 真机（书9）source-bound 编译器写出去的 world_spec.factions：

    ["祝余的当前行动单元", "当前直接阻力", "升级后的外部压力"]

而 `writing_profile.character.conflict_forces` 里躺着**结构化**的真势力：

    {"name": "杂役班借力成风同辈", "force_type": "faction", "active_volumes": [1],
     "threat_description": "青云宗杂役班里人人靠互相借真气攒修为，祝余刚入门
       就被人借走全部真气、被嘲为'天生欠债命'",
     "escalation_path": "他每还清一个同辈的借债，那人就被反噬一次，杂役班从
       嘲笑转为忌惮，最后合伙孤立他，逼他离开杂役班进入外门市集"}

这次连解析都不用，材料本来就是结构化的。与力量阶梯（d4e8db73）同一形状：
路走到了，材料没拿。

**同批教训立即套用**：同一天在 characters.goal 上确诊了「拿剧情功能描述冒充
欲望」，产出的病句是「为了内门借力天才师兄，演示盟友轮换机制的核心对照角色，
是否越过…」。所以这里的 `goal` 没有诚实来源就**空着**——不许拿
threat_description 冒充目标。
"""

from __future__ import annotations

from types import SimpleNamespace

from bestseller.services.planner import derive_source_bound_factions

_FORCES = [
    {
        "name": "杂役班借力成风同辈",
        "force_type": "faction",
        "active_volumes": [1],
        "threat_description": "杂役班里人人靠互相借真气攒修为，祝余刚入门就被借走全部真气",
        "escalation_path": "他每还清一个同辈的借债，那人就被反噬一次，最后合伙孤立他",
    },
    {
        "name": "内门借力网段缁衣",
        "force_type": "faction",
        "active_volumes": [1, 2],
        "threat_description": "内门弟子靠三层借力链堆修为",
        "escalation_path": "派小辈找茬→亲自下场→串联借力网集体施压",
    },
]


def _project(forces):
    return SimpleNamespace(
        metadata_json={"writing_profile": {"character": {"conflict_forces": forces}}},
        language="zh-CN",
    )


def test_factions_come_from_the_approved_forces() -> None:
    out = derive_source_bound_factions(_project(_FORCES))
    assert [f["name"] for f in out] == ["杂役班借力成风同辈", "内门借力网段缁衣"]


def test_no_framework_role_labels_survive() -> None:
    out = derive_source_bound_factions(_project(_FORCES))
    blob = " ".join(f["name"] for f in out)
    for banned in ("当前行动单元", "当前直接阻力", "升级后的外部压力"):
        assert banned not in blob


def test_escalation_is_the_method_and_threat_is_the_relationship() -> None:
    out = derive_source_bound_factions(_project(_FORCES))
    assert "合伙孤立他" in out[0]["method"]
    assert "借真气攒修为" in out[0]["relationship_to_protagonist"]


def test_goal_is_left_empty_rather_than_faked() -> None:
    """没有诚实来源的字段空着。今天刚在 characters.goal 上确诊过反例。"""

    out = derive_source_bound_factions(_project(_FORCES))
    for f in out:
        assert not f.get("goal"), f


def test_only_group_level_forces_become_factions() -> None:
    """契约的 force_type 有 5 种，只有群体性外部势力属于 factions。

    真机书9 的 7 个 force：3 个 faction、2 个 character（属于 cast）、
    1 个 systemic（账本本身）、1 个 internal（还力成瘾心结）。把心结塞进
    派系表就是同一天刚在 characters.goal 上确诊的字段语义错位。
    """

    out = derive_source_bound_factions(
        _project(
            [
                *_FORCES,
                {"name": "真传第一人沈惊", "force_type": "character"},
                {"name": "账本本身", "force_type": "systemic"},
                {"name": "还力成瘾心结", "force_type": "internal"},
            ]
        )
    )
    names = [f["name"] for f in out]
    assert "还力成瘾心结" not in names
    assert "真传第一人沈惊" not in names
    assert "账本本身" not in names
    assert "杂役班借力成风同辈" in names


def test_declared_relationship_field_wins_over_the_threat_text() -> None:
    out = derive_source_bound_factions(
        _project(
            [
                {
                    "name": "坊市黑市",
                    "force_type": "faction",
                    "relationship_to_protagonist": "第一个被主角动到蛋糕的人",
                    "threat_description": "三帖骨堂庄家手攥三张阴契",
                }
            ]
        )
    )
    assert out[0]["relationship_to_protagonist"] == "第一个被主角动到蛋糕的人"


def test_no_forces_means_no_claim() -> None:
    assert derive_source_bound_factions(_project([])) == []
    assert derive_source_bound_factions(SimpleNamespace(metadata_json={})) == []


def test_unnamed_or_malformed_entries_are_skipped_not_invented() -> None:
    out = derive_source_bound_factions(
        _project(
            [
                {"threat_description": "无名压力", "force_type": "faction"},
                "不是字典",
                _FORCES[0],
            ]
        )
    )
    assert [f["name"] for f in out] == ["杂役班借力成风同辈"]


def test_compiled_world_spec_carries_the_real_factions() -> None:
    from bestseller.services.planner import _compile_source_bound_world_spec

    project = SimpleNamespace(
        metadata_json={
            "writing_profile": {"character": {"conflict_forces": _FORCES}},
            "book_design_snapshot": {"protagonist": {"name": "祝余"}},
        },
        language="zh-CN",
        slug="s",
        title="t",
    )
    spec = _compile_source_bound_world_spec(project, "青云宗杂役祝余……", {})
    names = [f["name"] for f in spec["factions"]]
    assert "杂役班借力成风同辈" in names, names


# ── 2026-08-24 验证书 custom-xuanhuan-1787543232 抓到的真机缺口 ──
# 模型给 force_type 写的是**描述性中文短语**而不是契约枚举：
#   丹炉阁七长老    force_type="内部逼压势力"
#   云州药盟巡检官  force_type="外部制度压力"
# 两个都是实打实的外部势力（各带升级路径），而精确匹配的白名单把两个都丢了
# → 派系退回占位符模板 → 整条修复在这本书上是 no-op。
# 词表类过滤对自由文本必然漏，判据要按「是不是群体」而不是「是不是我列过的词」。
_DESCRIPTIVE = [
    {"name": "丹炉阁七长老", "force_type": "内部逼压势力", "active_volumes": [1, 2, 3],
     "escalation_path": "从跪求→围堵→威胁封铺→勾结巡检官→直接对哑娘下手",
     "threat_description": "靠祖传方吃了几十年，一被试吃令打中就现原形"},
    {"name": "云州药盟巡检官", "force_type": "外部制度压力", "active_volumes": [1, 2, 3, 4],
     "escalation_path": "从出怪题→当面试丹→查出哑娘旧档→直接带人上门",
     "threat_description": "每月底下来试丹的都带着同一个任务"},
]


def test_descriptive_chinese_force_types_are_kept() -> None:
    out = derive_source_bound_factions(_project(_DESCRIPTIVE))
    assert [f["name"] for f in out] == ["丹炉阁七长老", "云州药盟巡检官"]


def test_the_canonical_non_group_enums_are_still_dropped() -> None:
    """契约枚举里明确不是势力的那几个，照旧不进。"""

    out = derive_source_bound_factions(
        _project(
            [
                *_DESCRIPTIVE,
                {"name": "还力成瘾心结", "force_type": "internal"},
                {"name": "账本本身", "force_type": "systemic"},
                {"name": "真传第一人沈惊", "force_type": "character"},
            ]
        )
    )
    names = [f["name"] for f in out]
    assert "还力成瘾心结" not in names and "账本本身" not in names
    assert "真传第一人沈惊" not in names


def test_descriptive_psyche_and_object_labels_are_dropped_too() -> None:
    """描述性短语里明确指向心理/物件/单个角色的，同样不是势力。"""

    out = derive_source_bound_factions(
        _project(
            [
                *_DESCRIPTIVE,
                {"name": "主角的心魔", "force_type": "内心挣扎"},
                {"name": "会自己翻页的账本", "force_type": "系统性物件"},
            ]
        )
    )
    names = [f["name"] for f in out]
    assert "主角的心魔" not in names and "会自己翻页的账本" not in names
    assert "丹炉阁七长老" in names
