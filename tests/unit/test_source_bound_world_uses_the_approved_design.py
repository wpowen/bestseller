"""source-bound 造世界时必须用构思已经批准的力量体系，而不是框架术语。

2026-08-24 真机（书9，勾了「无代价/minimal」→ 走 source-bound 编译器）：

  构思 metadata.growth_curve 里已经有一条完整的境界阶梯——
    起步=杂役级目视范围；第10章=还力诀残页解锁，目视扩至三丈；
    第25章=还力触发借方反噬；第35章=清债令牌解锁执簿境；
    第45章=进入账本识海位面；第50章=账本合拢，获得清债之钥
  writing_profile.world.power_system_style 里有硬规则（九笔上限／三十章硬时限）。

  而编译器写进 world_spec 的是：tiers = ["确认","复现","转化","扩张"]，
  name = "已批准核心机制的阶段兑现" —— 框架自己的方法论穿了件戏服。

后果直达用户提的两个问题：「角色等级的变化」和「人界/灵界/仙界这种世界观」。
character_state_snapshots 里 480 份快照只有 16 份有 power_tier，因为阶梯本身
不是故事阶梯，没人能在上面定位。

素材一直在 project.metadata 里，编译器不读它——「能力长在书不走的那条路上」
的反面：路走到了，材料没拿。
"""

from __future__ import annotations

from types import SimpleNamespace

from bestseller.services.planner import derive_source_bound_power_system

_BOOK9_CURVE = (
    "起步=杂役级目视范围（仅能见本人被借之债）；第一章后=能替同门把借走的真气"
    "送回原主；第10章=还力诀残页解锁，目视范围扩至三丈；第25章=还力触发借方"
    "反噬，反噬冲击由债主承受；第35章=清债令牌解锁执簿境，可召唤旧债主助战；"
    "第45章=进入账本识海位面，还力范围覆盖整条借力链；第50章=账本合拢，"
    "获得清债之钥"
)
_BOOK9_STYLE = (
    "借力体系——所有武者以借力术为基，向他人借来一缕真气暂存丹田；"
    "硬规则包括九笔上限、还力只能替被借者还、三十章硬时限、五十页天花板"
)


def _project(**meta):
    return SimpleNamespace(metadata_json=meta, language="zh-CN", slug="s", title="t")


def test_tiers_come_from_the_approved_growth_curve() -> None:
    ps = derive_source_bound_power_system(
        _project(growth_curve=_BOOK9_CURVE), engine="还力兑现"
    )
    assert ps is not None
    tiers = ps["tiers"]
    # 框架术语一个都不许出现
    assert not ({"确认", "复现", "转化", "扩张"} & set(tiers)), tiers
    # 故事自己的台阶必须在
    joined = "".join(tiers) + "".join(
        str(t.get("bottleneck", "")) + str(t.get("breakthrough_cost", ""))
        for t in ps["tier_progression"]
    )
    assert "执簿境" in joined
    assert "账本识海" in joined


def test_progression_keeps_the_chapter_each_tier_unlocks_at() -> None:
    """第N章是阶梯的位置信息——丢了它，「现在该到哪一阶」就无法判定。"""

    ps = derive_source_bound_power_system(
        _project(growth_curve=_BOOK9_CURVE), engine="还力兑现"
    )
    chapters = [t.get("unlocks_at_chapter") for t in ps["tier_progression"]]
    assert 10 in chapters and 35 in chapters and 50 in chapters
    # 单调递增：阶梯就是顺序
    seen = [c for c in chapters if isinstance(c, int)]
    assert seen == sorted(seen), seen


def test_starting_tier_is_the_first_step_not_a_process_word() -> None:
    ps = derive_source_bound_power_system(
        _project(growth_curve=_BOOK9_CURVE), engine="还力兑现"
    )
    assert ps["protagonist_starting_tier"] == ps["tiers"][0]
    assert ps["protagonist_starting_tier"] != "确认"


def test_hard_limits_come_from_the_approved_power_style() -> None:
    ps = derive_source_bound_power_system(
        _project(
            growth_curve=_BOOK9_CURVE,
            writing_profile={"world": {"power_system_style": _BOOK9_STYLE}},
        ),
        engine="还力兑现",
    )
    assert "九笔上限" in ps["hard_limits"]


def test_no_curve_means_no_claim() -> None:
    """没有素材就交还给既有兜底——不许凭空编一条阶梯出来。"""

    assert derive_source_bound_power_system(_project(), engine="e") is None
    assert derive_source_bound_power_system(
        _project(growth_curve="他会变强"), engine="e"
    ) is None


def test_compiled_world_spec_carries_the_story_ladder() -> None:
    """端到端：编译器产出的 world_spec 里不再有框架术语阶梯。"""

    from bestseller.services.planner import _compile_source_bound_world_spec

    project = _project(
        growth_curve=_BOOK9_CURVE,
        writing_profile={"world": {"power_system_style": _BOOK9_STYLE}},
        book_design_snapshot={"protagonist": {"name": "祝余"}},
    )
    spec = _compile_source_bound_world_spec(project, "青云宗杂役祝余……", {})
    tiers = spec["power_system"]["tiers"]
    assert not ({"确认", "复现", "转化", "扩张"} & set(tiers)), tiers


# ── 第二种真机格式（2026-08-24 末日验证书 custom-apocalypse-1787538561）──
# 两本零种子书，构思写出了**两种不同格式**的成长曲线。只解析其中一种，
# 修复在另一半书上就是 no-op —— 本仓库栽过一次的「验证走的路径和真实
# 路径不是同一条」。
_APOC_CURVE = (
    "L1 驾驶室独居（严格遵守应急避险规程，仅靠应急食品维持，拒绝他人靠近）"
    "→ L2 经地面信号司索何守信配合作业（限载核定+落点警戒），用吊篮完成"
    "首次人员垂直运输，掌握'垂直运输技术许可'但仍无物资分配权"
    "→ L3 原项目部、施工班组长、社区商户组成临时中转协调链，他获得"
    "'调度确认权'但每次起吊仍需班组长签字"
    "→ L4 形成以塔吊司机、信号司索、地面警戒为核心的临时秩序雏形"
)


def test_the_arrow_and_level_format_is_parsed_too() -> None:
    ps = derive_source_bound_power_system(
        _project(growth_curve=_APOC_CURVE), engine="垂直运输"
    )
    assert ps is not None, "L1→L2→L3 格式必须也能解析"
    assert len(ps["tiers"]) == 4, ps["tiers"]
    assert not ({"确认", "复现", "转化", "扩张"} & set(ps["tiers"]))
    joined = " ".join(ps["tiers"])
    assert "驾驶室独居" in joined or "独居" in joined


def test_level_format_keeps_order() -> None:
    ps = derive_source_bound_power_system(
        _project(growth_curve=_APOC_CURVE), engine="垂直运输"
    )
    assert ps["protagonist_starting_tier"] == ps["tiers"][0]
    bodies = [t["bottleneck"] for t in ps["tier_progression"]]
    assert "独居" in bodies[0]
    assert "临时秩序" in bodies[-1]


def test_power_system_name_is_a_name_not_half_a_sentence() -> None:
    """2026-08-24 接线测试：真机产出过「借力体系——所有武者以借力术」这种半截话。"""

    ps = derive_source_bound_power_system(
        _project(
            growth_curve=_BOOK9_CURVE,
            writing_profile={"world": {"power_system_style": _BOOK9_STYLE}},
        ),
        engine="还力兑现",
    )
    assert ps["name"] == "借力体系", ps["name"]
    assert "——" not in ps["name"]


def test_a_style_without_a_nameable_phrase_falls_back_readably() -> None:
    ps = derive_source_bound_power_system(
        _project(
            growth_curve=_BOOK9_CURVE,
            writing_profile={"world": {"power_system_style": ""}},
        ),
        engine="e",
    )
    assert ps["name"] and "确认" not in ps["name"]
