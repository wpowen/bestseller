"""卖点不许带 prompt 的字段标签，也不许是同一条的改写版。

2026-08-24 真机（书9 custom-xuanhuan-1787493501）落库的 7 条卖点：

    1. 别人练的是'抢'，他练的是'还'——你以为他是冤大头，结果每还清一笔账，
       欠他人情的人排着队替他挡刀
    ...
    5. **卖点1：**满世界都在'借'，就他在'还'——你以为他是冤大头，结果他每还
       一笔，旧债主自动变成新保镖，欠他人情的人排着队替他挡刀
    6. **卖点2：**账本会自己长——…
    7. **卖点3：**上一轮帮他挡刀的人，下一轮就是来找他要账的人——…

两个独立根因：

① **prompt 的示例值自己带标签**。conception.py 的 JSON 示例写的是
   `"selling_points": ["卖点1：用故事本身的人、事、反常之处说，像跟朋友安利
   一本书", "卖点2：同上", ...]` —— 占位符把「标签」写成了字面内容，模型照抄。
   本仓库定过案：**prompt 只许给类别和正例，不许给会被逐字复制的壳**。

② **合并只按逐字去重**。conception.py:7231 用 `dict.fromkeys` 拼 market 卖点
   与 concept_bundle.hype_targets——改写过的近义重复全部存活。而同一份数据在
   1714 行用的是 `fold_near_duplicate_points`（2026-08-07 用真机数据校准过
   阈值）。同一件事两套实现，先折叠后合并 = 折叠白做。
"""

from bestseller.services.writing_profile import (
    fold_near_duplicate_points,
    strip_enumeration_label,
)

_BOOK9 = [
    "别人练的是'抢'，他练的是'还'——你以为他是冤大头，结果每还清一笔账，欠他人情的人排着队替他挡刀",
    "账本会自己长——主角越想躺平退出江湖，账本越拽着他往风暴中心拖，新债主比旧债主更强",
    "上一轮的盟友，下一轮就是来找他要账的人——你刚觉得他结交了个靠山，三章后这哥们就哭着求他还人情",
    "还力代价极小（替还时需目视债务链、不得闭眼）但换来的是整页账本清账+多名债主同时护主，爽点密集",
    "卖点1：满世界都在'借'，就他在'还'——你以为他是冤大头，结果他每还一笔，旧债主自动变成新保镖，欠他人情的人排着队替他挡刀",
    "卖点2：账本会自己长——主角越想退，账本越拽着他往江湖中心拖，每一次想躺平都被新债主找上门，笑点打脸两不误",
    "卖点3：上一轮帮他挡刀的人，下一轮就是来找他要账的人——你以为结了个盟友，三章后他就得哭着求你还人情，反转密集到停不下来",
]


class TestLabel:
    def test_chinese_enumeration_label_is_stripped(self) -> None:
        assert strip_enumeration_label("卖点1：满世界都在借") == "满世界都在借"
        assert strip_enumeration_label("卖点3、上一轮的盟友") == "上一轮的盟友"
        assert strip_enumeration_label("point 2: the ledger grows") == "the ledger grows"

    def test_content_without_a_label_is_untouched(self) -> None:
        for text in ("别人练的是'抢'，他练的是'还'", "账本会自己长", "3年后他回来了"):
            assert strip_enumeration_label(text) == text

    def test_a_colon_inside_real_content_survives(self) -> None:
        """只剥『标签+序号+分隔符』这个头，不许吃掉正文里的冒号。"""

        text = "他只说了一句：还完就走"
        assert strip_enumeration_label(text) == text


class TestFold:
    def test_restatements_collapse(self) -> None:
        folded = fold_near_duplicate_points([strip_enumeration_label(p) for p in _BOOK9])
        assert len(folded) == 4, folded

    def test_the_surviving_four_are_the_distinct_ideas(self) -> None:
        folded = fold_near_duplicate_points([strip_enumeration_label(p) for p in _BOOK9])
        assert not any(p.startswith("卖点") for p in folded)
        # 先到先留：留下的是原始那四条
        assert folded[0].startswith("别人练的是")

    def test_genuinely_different_points_all_survive(self) -> None:
        """别把去重开成压路机——不同的卖点必须全留。"""

        distinct = [
            "他能听见铁器还记得自己被锻打时的形状",
            "每唤醒一件兵器，前主人的死法就在他手上重演",
            "全城的锁都认得他，唯独他家那把不认",
        ]
        assert fold_near_duplicate_points(distinct) == distinct


def test_merge_site_folds_instead_of_exact_dedupe() -> None:
    """conception 合并 hype_targets 时必须折叠近重复，不能只 dict.fromkeys。

    先折叠后合并 = 折叠白做（真机书9 就是这样漏出 3 条改写重复的）。
    """

    from pathlib import Path

    import bestseller.services.conception as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    merge = src.split('market_profile["selling_points"] = ', 1)[1][:400]
    assert "fold_near_duplicate_points" in merge, merge[:200]
