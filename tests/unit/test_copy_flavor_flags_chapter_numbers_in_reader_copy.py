"""读者文案里出现章号，本身就是生产口吻——不必再配一个指令动词。

2026-08-24 真机（用户报「上架资料简介 AI 味重」）：

    书9 简介   「……**第50章**，账本只剩最后几页，他要替真传第一人沈惊鹊还清
                命里最后一笔债」                                  → 打分 0.0
    书9 synopsis「上一章替他挡刀的人，**下一章**就得哭着求他还人情」  → 打分 0.0

`directive_voice` 规则要求「生产锚点 + 指令动词」**同时**命中。这两句有章号
锚点却没有指令动词，于是整句无罪。但读者简介里根本不该出现「第几章」——
读者买书时还没有章的概念，说「第50章如何如何」的只有写手和编辑。

与 2026-08-24 上午那两条是同一族第三次：
  · 量词枚举太窄（漏「几」）
  · 动词枚举太窄（漏「让读者看到」）
  · 这次是**规则形状太窄**：把「必须配动词」当成了普适前提。

⚠️ 边界：**正文**里说「上一章」是叙述者口吻问题，不归这条管；这条只在
对外文案（简介/一句话/读者承诺/卖点）上使用，所以规则本身可以更严。
"""

from bestseller.services.copy_flavor import detect_copy_flavor


class TestChapterNumbers:
    def test_real_book9_blurb_tail_is_flagged(self) -> None:
        r = detect_copy_flavor(
            "他想烧账本退出江湖，账本却在识海里把他往风暴中心拖；"
            "第50章，账本只剩最后几页，他要替真传第一人沈惊鹊还清命里最后一笔债。"
        )
        assert r.score > 0, r
        assert any("第50章" in s.matched for s in r.spans), [s.matched for s in r.spans]

    def test_relative_chapter_references_are_flagged(self) -> None:
        r = detect_copy_flavor("上一章替他挡刀的人，下一章就得哭着求他还人情")
        assert r.score > 0, r

    def test_various_forms(self) -> None:
        for text in (
            "第三章他就会拿到第一件法宝",
            "本章末尾留了一个钩子",
            "到了第 120 章，他终于登顶",
            "下一卷主角将踏入仙界",
        ):
            assert detect_copy_flavor(text).score > 0, text


class TestNoFalsePositives:
    def test_in_world_numbers_are_not_chapter_numbers(self) -> None:
        """故事里的数字不是章号。这条最容易开过头。"""

        for text in (
            "他在第三层塔里待了三年",
            "九笔上限，还完第九笔账本就合拢",
            "第五个债主来的那天下了雨",
            "三章鱼干挂在梁上",          # 「三章鱼」——不许把量词切错
        ):
            assert detect_copy_flavor(text).score == 0, text

    def test_good_copy_stays_clean(self) -> None:
        for text in (
            "江湖练借力，他练还力；账本自己翻页，每还清一笔旧账，新债主比上一笔更强。",
            "杂役弟子祝余刚被人当众借走全部真气，反手替那人把欠的真气还回了原主。",
            "雷劫生出了嘴，跟沈渡讨价还价。",
        ):
            assert detect_copy_flavor(text).score == 0, text
