"""机器枚举值不是「事件载荷」——它当然每章都一样。

2026-08-24 真机（书9）的 outline_semantic_gate_report，两条 high 全是这个：

    OUTLINE_REUSED_PAYLOAD_ANCHOR  anchor="close_third"     chapters=[1,2,3,…32]（19章）
    OUTLINE_REUSED_PAYLOAD_ANCHOR  anchor="first_sentence"  chapters=[1,2,3]

`close_third` 是人称视角枚举，`first_sentence` 是钩子锚点枚举——**每一章都该
一样**。`_QUOTED_ANCHOR_RE` 抓「引号里 6-80 字的片段」，把 JSON 里的枚举值
连引号一起抓走，于是每本书都稳定产两条 high 噪声。

该码已是 advisory（2026-07-25《仇人膝上养帝王》因它连failed 3次、自愈循环烧
掉约 88 万 token 后降级），所以毙不了书；但报告里的假 high 会误导读报告的人，
也稀释真正的发现。

判据：**纯 ASCII 的 snake_case / kebab-case 标识符不可能是中文小说的事件载荷。**
这不是「按词表排除」，是按**形态**排除一整类机器值。
"""

from bestseller.services.outline_semantic_gate import is_machine_enum_anchor


class TestMachineValues:
    def test_real_book9_anchors_are_recognised(self) -> None:
        assert is_machine_enum_anchor("close_third")
        assert is_machine_enum_anchor("first_sentence")

    def test_other_enum_shapes(self) -> None:
        for value in (
            "development",
            "third-limited",
            "hype_satisfaction_engine",
            "OUTLINE_REUSED",
            "scene_type",
            "zh-CN",
        ):
            assert is_machine_enum_anchor(value), value


class TestRealPayloads:
    def test_chinese_event_payloads_are_never_machine_values(self) -> None:
        for value in (
            "段缁衣笑着伸手要去按柳三娘的肩逼她下跪",
            "灶火被人抽走，锅底只剩一圈冷灰",
            "账本自己翻到下一页",
        ):
            assert not is_machine_enum_anchor(value), value

    def test_english_prose_is_not_a_machine_value(self) -> None:
        """英文书的事件载荷是**带空格的句子**，不是标识符。"""

        assert not is_machine_enum_anchor("the ledger turns its own page")
        assert not is_machine_enum_anchor("He burned the book at dawn")

    def test_mixed_content_with_chinese_stays(self) -> None:
        assert not is_machine_enum_anchor("close_third视角下的祝余")


def test_the_finding_builder_skips_machine_values() -> None:
    from pathlib import Path

    import bestseller.services.outline_semantic_gate as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    body = src.split("def _reused_anchor_findings(", 1)[1][:1200]
    assert "is_machine_enum_anchor" in body, body[:300]
