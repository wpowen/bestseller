"""境界阶梯要契约里的**结构化列表**，不要我去解析散文。

2026-08-25 真机：五本零种子书写出了**五种**成长曲线格式——

    书9        「起步=杂役级…；第10章=还力诀残页解锁…」        等号+章号
    末日书      「L1 驾驶室独居 → L2 …」                      层级标号+箭头
    端到端书    「散修→内门弟子→核心弟子→…」                   纯箭头
    F验收书     「修为＝当月碾碎的废符数量×符箓品级」            **公式，无阶梯**
    本书        「从替差生抄普通灵课作业起步，进阶到替老先生抄
                封禁古考，再到替废灵少年抄学政抽查卷」          散文连接词

我为前三种各补过一次解析，**每补一种下一本就换一种**。根因在契约层：
prompt 要的是 `"growth_curve": "成长曲线描述"` —— 一段自由散文，模型照做。
这是「词表白名单对自由文本必然漏」在**结构层**的同一形状。

修法与同日 conflict_forces 一致：契约里加一个结构化字段，编译器优先读它，
散文解析降为兜底。**不发明**——模型没给就退回既有路径。
"""

from __future__ import annotations

from types import SimpleNamespace

from bestseller.domain.project import CharacterEngineConfig
from bestseller.services.planner import derive_source_bound_power_system


class TestContract:
    def test_power_tiers_survives_the_contract(self) -> None:
        cfg = CharacterEngineConfig(power_tiers=["炼气", "筑基", "金丹", "元婴"])
        assert cfg.power_tiers == ["炼气", "筑基", "金丹", "元婴"]

    def test_absent_is_empty_not_none(self) -> None:
        assert CharacterEngineConfig().power_tiers == []

    def test_malformed_entries_are_dropped(self) -> None:
        cfg = CharacterEngineConfig(power_tiers=["炼气", "", None, 42, "  ", "筑基"])
        assert cfg.power_tiers == ["炼气", "筑基"]

    def test_a_non_list_is_tolerated(self) -> None:
        assert CharacterEngineConfig(power_tiers="炼气筑基").power_tiers == []
        assert CharacterEngineConfig(power_tiers=None).power_tiers == []

    def test_round_trip_through_dump(self) -> None:
        dumped = CharacterEngineConfig(power_tiers=["炼气", "筑基"]).model_dump(mode="json")
        assert dumped["power_tiers"] == ["炼气", "筑基"]


def _project(**meta):
    return SimpleNamespace(metadata_json=meta, language="zh-CN")


class TestCompilerPrefersTheList:
    def test_structured_tiers_win_over_prose_parsing(self) -> None:
        ps = derive_source_bound_power_system(
            _project(
                growth_curve="从替差生抄普通灵课作业起步，进阶到替老先生抄封禁古考",
                writing_profile={"character": {"power_tiers": ["代抄杂役", "封考抄手", "学政抽查"]}},
            ),
            engine="e",
        )
        assert ps["tiers"] == ["代抄杂役", "封考抄手", "学政抽查"]
        assert ps["protagonist_starting_tier"] == "代抄杂役"

    def test_prose_parsing_still_works_when_the_list_is_absent(self) -> None:
        """既有三种格式的解析必须原样保留——这是兜底不是替代。"""

        ps = derive_source_bound_power_system(
            _project(growth_curve="散修→内门弟子→核心弟子→宗门之主"), engine="e"
        )
        assert ps["tiers"] == ["散修", "内门弟子", "核心弟子", "宗门之主"]

    def test_a_single_tier_list_is_not_a_ladder(self) -> None:
        assert derive_source_bound_power_system(
            _project(writing_profile={"character": {"power_tiers": ["炼气"]}}), engine="e"
        ) is None

    def test_no_material_at_all_returns_none(self) -> None:
        assert derive_source_bound_power_system(_project(), engine="e") is None


def test_the_prompt_asks_for_it_without_a_copyable_shell() -> None:
    """prompt 里要这个字段，但**不能给会被逐字抄走的假阶名**。

    2026-08-24 真机教训：`"selling_points": ["卖点1：…", "卖点2：同上"]` 让模型
    把「卖点1：」当成内容抄了出去。占位符只许描述形状。
    """

    from pathlib import Path

    import bestseller.services.conception as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert '"power_tiers"' in src, "构思 prompt 没有要这个字段"
    assert "境界名1" not in src and "阶名1" not in src, "占位符会被逐字抄走"
