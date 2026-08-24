"""逐卷世界揭示不许是三个空列表——构思逐卷写好了揭示表。

2026-08-24 真机（书9）：`volume_world_disclosure` 6 份，每一份都是

    new_locations: []   faction_movements: []   new_rules_revealed: []
    frontier_summary: "祝余在本阶段持续运行已批准核心机制…每3章一次小兑现…"

三个数组全空、摘要六卷同一句框架方法论（六份的差别只在 metadata 的 hash）。
`_compile_source_bound_world_disclosure` 的 docstring 写着「Keep per-volume
world disclosure inside the already approved world」，而它**根本没读那个世界**，
直接硬编码三个 []。

而已批准的材料逐卷都写好了：

  info_reveal_strategy:
    「…卷一揭清债者一脉守秘人，卷二揭母系血脉源头，卷三揭坊市黑市与遗族守墓人，
      卷四揭六大宗借力协议，卷五揭上古借力源头」
  conflict_forces[*].active_volumes:
    [1] 杂役班借力成风同辈 ／ [1,2] 内门借力网段缁衣 ／ [2,3] 坊市黑市霍帖骨 …
    每条都带 escalation_path

这是同一形状的第四例（力量阶梯 d4e8db73／派系 614d93f7／势力表落库 9aad7e4b）：
路走到了，材料没拿。用户问的「随角色发展的世界观变更（人界/灵界/仙界）」
落在的正是这一层。
"""

from __future__ import annotations

from types import SimpleNamespace

from bestseller.services.planner import derive_source_bound_volume_disclosure

_REVEAL = (
    "账本翻页式释放——信息不靠主角主动查探；卷一揭清债者一脉守秘人，"
    "卷二揭母系血脉源头，卷三揭坊市黑市与遗族守墓人，卷四揭六大宗借力协议，"
    "卷五揭上古借力源头"
)
_FORCES = [
    {"name": "杂役班借力成风同辈", "force_type": "faction", "active_volumes": [1],
     "escalation_path": "他每还清一个同辈的借债，那人就被反噬一次，最后合伙孤立他"},
    {"name": "内门借力网段缁衣", "force_type": "faction", "active_volumes": [1, 2],
     "escalation_path": "派小辈找茬→亲自下场→串联借力网集体施压"},
    {"name": "坊市黑市霍帖骨", "force_type": "faction", "active_volumes": [2, 3],
     "escalation_path": "先派打手→改为悬赏买命→亲自下场谈合作翻脸"},
]


def _project():
    return SimpleNamespace(
        metadata_json={
            "writing_profile": {
                "world": {"info_reveal_strategy": _REVEAL},
                "character": {"conflict_forces": _FORCES},
            }
        },
        language="zh-CN",
    )


class TestRules:
    def test_volume_one_gets_its_own_reveal(self) -> None:
        out = derive_source_bound_volume_disclosure(_project(), volume_number=1)
        assert out["new_rules_revealed"] == ["清债者一脉守秘人"]

    def test_each_volume_gets_a_different_reveal(self) -> None:
        reveals = [
            derive_source_bound_volume_disclosure(_project(), volume_number=v)[
                "new_rules_revealed"
            ]
            for v in (1, 2, 3, 4, 5)
        ]
        flat = [r[0] for r in reveals]
        assert len(set(flat)) == 5, flat
        assert "六大宗借力协议" in flat[3]

    def test_a_volume_with_no_scheduled_reveal_is_empty_not_invented(self) -> None:
        out = derive_source_bound_volume_disclosure(_project(), volume_number=9)
        assert out["new_rules_revealed"] == []


class TestFactionMovements:
    def test_only_forces_active_in_this_volume(self) -> None:
        out = derive_source_bound_volume_disclosure(_project(), volume_number=3)
        assert [m["name"] for m in out["faction_movements"]] == ["坊市黑市霍帖骨"]

    def test_movement_carries_the_escalation_path(self) -> None:
        out = derive_source_bound_volume_disclosure(_project(), volume_number=1)
        names = [m["name"] for m in out["faction_movements"]]
        assert names == ["杂役班借力成风同辈", "内门借力网段缁衣"]
        assert "合伙孤立他" in out["faction_movements"][0]["movement"]

    def test_a_force_without_an_escalation_path_is_skipped(self) -> None:
        """没有异动内容就不是异动——不许拿名字凑数。"""

        p = SimpleNamespace(
            metadata_json={
                "writing_profile": {
                    "character": {
                        "conflict_forces": [
                            {"name": "无路径势力", "force_type": "faction",
                             "active_volumes": [1]}
                        ]
                    }
                }
            },
            language="zh-CN",
        )
        assert derive_source_bound_volume_disclosure(p, volume_number=1)[
            "faction_movements"
        ] == []


def test_new_locations_stays_empty_rather_than_faked() -> None:
    """没有诚实的结构化来源就空着——今天已经在 characters.goal 上确诊过反例。"""

    out = derive_source_bound_volume_disclosure(_project(), volume_number=1)
    assert out["new_locations"] == []


def test_no_material_means_all_empty() -> None:
    out = derive_source_bound_volume_disclosure(
        SimpleNamespace(metadata_json={}, language="zh-CN"), volume_number=1
    )
    assert out == {"new_locations": [], "new_rules_revealed": [], "faction_movements": []}


def test_the_compiler_actually_uses_it() -> None:
    from pathlib import Path

    import bestseller.services.planner as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    body = src.split("def _compile_source_bound_world_disclosure(", 1)[1][:1400]
    assert "derive_source_bound_volume_disclosure" in body, body[:300]


def test_internal_and_systemic_forces_are_not_faction_movements() -> None:
    """字段叫 faction_movements，装的就该是势力的异动。

    与 derive_source_bound_factions 用同一条 force_type 过滤——同一条原则
    不能只落在一处（本仓库反复复发的元病）。
    """

    p = SimpleNamespace(
        metadata_json={
            "writing_profile": {
                "character": {
                    "conflict_forces": [
                        *_FORCES,
                        {"name": "还力成瘾心结", "force_type": "internal",
                         "active_volumes": [1], "escalation_path": "从本能出手→涉险还大债"},
                        {"name": "账本本身", "force_type": "systemic",
                         "active_volumes": [1], "escalation_path": "杂役小债→内门中债"},
                        {"name": "真传第一人沈惊", "force_type": "character",
                         "active_volumes": [1], "escalation_path": "表面感激→视为隐患"},
                    ]
                }
            }
        },
        language="zh-CN",
    )
    names = [
        m["name"]
        for m in derive_source_bound_volume_disclosure(p, volume_number=1)[
            "faction_movements"
        ]
    ]
    assert "还力成瘾心结" not in names
    assert "账本本身" not in names
    assert "真传第一人沈惊" not in names
    assert "杂役班借力成风同辈" in names


def test_chinese_ordinal_active_volumes_are_matched() -> None:
    """active_volumes 真机写的是「第一卷」不是 1。

    2026-08-24 端到端验证书 custom-xuanhuan-1787557783：三条势力的
    active_volumes 全是 ["第一卷","第二卷"] 这种中文序数串，_chapter_ordinal
    遇到「第」「卷」直接返回 None，于是一条势力异动都没匹配上。
    同一天第三次栽在「解析器假定一种规范形态，模型写的是自然中文」。
    """

    p = SimpleNamespace(
        metadata_json={
            "writing_profile": {
                "character": {
                    "conflict_forces": [
                        {"name": "宗门真传师兄派系", "force_type": "同阶天才压制",
                         "active_volumes": ["第一卷", "第二卷"],
                         "escalation_path": "单人对线→派系围剿→借宗门大势施压"},
                        {"name": "天道审计司", "force_type": "机械规则碾压",
                         "active_volumes": ["第三卷", "第四卷"],
                         "escalation_path": "例行抽账→专项审查→换届替换"},
                    ]
                }
            }
        },
        language="zh-CN",
    )
    v1 = derive_source_bound_volume_disclosure(p, volume_number=1)
    assert [m["name"] for m in v1["faction_movements"]] == ["宗门真传师兄派系"]
    v3 = derive_source_bound_volume_disclosure(p, volume_number=3)
    assert [m["name"] for m in v3["faction_movements"]] == ["天道审计司"]


def test_mixed_ordinal_forms_all_work() -> None:
    for form in (["卷一"], ["第1卷"], [1], ["1"], ["第一卷"]):
        p = SimpleNamespace(
            metadata_json={"writing_profile": {"character": {"conflict_forces": [
                {"name": "势力甲", "force_type": "faction", "active_volumes": form,
                 "escalation_path": "甲的升级路径"}]}}},
            language="zh-CN",
        )
        out = derive_source_bound_volume_disclosure(p, volume_number=1)
        assert [m["name"] for m in out["faction_movements"]] == ["势力甲"], form
