"""地点必须是**地方**：不许叫「X的账面」，也不许拿动词短语当地名。

2026-08-24 真机（用户报「书籍的设定不符合实际的逻辑」）：

    书9        青云宗 / 青云宗外围 / 青云宗深处 / **青云宗的账面** / 青云宗之外
    端到端书    **用余雷劈开宗门** / 用余雷劈开宗门外围 / 用余雷劈开宗门深处 /
               用余雷劈开宗门的账面 / 用余雷劈开宗门之外

两条独立缺陷：

① `f"{anchor}的账面"` **硬编码在模板里**。账面不是地方；更糟的是它把债务族
   的「账」直接注进了每一本 source-bound 书的世界设定——框架一边用反默认族
   的门管着母题，一边在自己的世界模板里种它。

② 地名锚点没有校验。`extract_place_names` 从 premise 里切出了动词短语
   「用余雷劈开宗门」，五个地点全是它加后缀。代码注释里已经写着「真机从
   『敢接镇店符』切出过『敢接镇』」——知道抽取不可靠，却只防了第二第三个，
   **没防第一个本身就是脏数据**。

数量保持 5 个：world_richness 门按数量判「饥饿世界」，减少会触发误修。
改的是名字不是数量（原注释的约束继续遵守）。
"""

from bestseller.services.planner import _source_bound_locations, looks_like_place_name


class TestNoLedgerLocation:
    def test_the_hardcoded_ledger_place_is_gone(self) -> None:
        names = [loc["name"] for loc in _source_bound_locations(["青云宗"])]
        assert not any("账面" in n for n in names), names
        assert not any("账" in n for n in names), names

    def test_still_five_locations(self) -> None:
        """world_richness 门按数量判饥饿世界——数量不许变。"""

        assert len(_source_bound_locations(["青云宗"])) == 5
        assert len(_source_bound_locations([])) == 5

    def test_derived_names_are_spatial(self) -> None:
        names = [loc["name"] for loc in _source_bound_locations(["青云宗"])]
        assert names[0] == "青云宗"
        assert all(n.startswith("青云宗") for n in names), names


class TestAnchorValidation:
    def test_a_verb_phrase_is_not_a_place_name(self) -> None:
        assert not looks_like_place_name("用余雷劈开宗门")
        assert not looks_like_place_name("敢接镇店符")
        assert not looks_like_place_name("把账本烧了")
        assert not looks_like_place_name("被借走全部真气")

    def test_real_place_names_pass(self) -> None:
        for name in ("青云宗", "悬壶镇", "云海栈桥", "坊市", "藏经楼", "临水镇"):
            assert looks_like_place_name(name), name

    def test_too_long_or_too_short_is_rejected(self) -> None:
        assert not looks_like_place_name("一")
        assert not looks_like_place_name("青云宗外门杂役弟子聚居的那一片地方")
        assert not looks_like_place_name("")

    def test_punctuation_is_rejected(self) -> None:
        assert not looks_like_place_name("青云宗，外围")
        assert not looks_like_place_name("青云宗(旧)")


class TestDirtyAnchorFallsBack:
    def test_verb_phrase_anchor_uses_the_generic_template(self) -> None:
        """脏锚点宁可用通用模板，也不要五个「用余雷劈开宗门X」。"""

        names = [loc["name"] for loc in _source_bound_locations(["用余雷劈开宗门"])]
        assert not any("用余雷" in n for n in names), names
        assert len(names) == 5

    def test_a_clean_anchor_still_wins_over_the_template(self) -> None:
        names = [loc["name"] for loc in _source_bound_locations(["悬壶镇"])]
        assert names[0] == "悬壶镇"
