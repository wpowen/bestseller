"""「前几章给出…」和「前三章给出…」是同一句话，检测器只抓得到后者。

2026-08-24 真机（书9）落库的 reader_promise：

    「前几章给出主角被借走真气、第一次还力打脸、账本翻页引来高阶债主，
      以及上一轮盟友下一轮变讨债人的反转节奏。」

打分 **0.0**。而同一个 conception prompt 明确写着「写给读者听，不用行业词
（追读/爽点/留存/黄金三章），**不写章节节奏（每N章…）**」——产出违反了自己
prompt 的硬约束，专门的去行业词修复通道没开火。

根因是 directive_voice 规则的数词字符类 `[一二三四五六七八九十百千万\\d]`
**漏了「几」**（以及「数」）。「前三章给出」命中，「前几章给出」漏网——
同一句话换个含糊的数词就绕过了整道门。

⚠️ 这类修补的边界：模态词本身永远不够，必须瞄准**稿件**。
「他必须在三天内赶到」是叙事，必须保持干净。
"""

from bestseller.services.copy_flavor import detect_copy_flavor


class TestVagueCounts:
    def test_the_real_book9_reader_promise_is_flagged(self) -> None:
        r = detect_copy_flavor(
            "前几章给出主角被借走真气、第一次还力打脸、账本翻页引来高阶债主，"
            "以及上一轮盟友下一轮变讨债人的反转节奏。"
        )
        assert r.score > 0, r
        assert any("前几章" in s.matched for s in r.spans), [s.matched for s in r.spans]

    def test_explicit_counts_still_flagged(self) -> None:
        """回归：原本抓得到的不许因为这次改动漏掉。"""

        for text in ("前三章必须亮出金手指", "前10章给出完整世界观", "开篇必须抛出钩子"):
            assert detect_copy_flavor(text).score > 0, text

    def test_other_vague_quantifiers(self) -> None:
        for text in ("前数章给出主角的困境", "前几字必须拉住读者"):
            assert detect_copy_flavor(text).score > 0, text


class TestNoFalsePositives:
    def test_narrative_with_a_deadline_stays_clean(self) -> None:
        """模态词瞄准的是人物不是稿件——这是原设计的边界，不许改坏。"""

        for text in (
            "他必须在三天内赶到，否则那扇门就永远关上了",
            "她说完这句话就走了，谁也没拦",
            "少年握剑站在山门前，风很大",
        ):
            assert detect_copy_flavor(text).score == 0, text

    def test_reader_facing_copy_stays_clean(self) -> None:
        """真机对照：书9 的 logline 是合格文案，必须 0 分。"""

        assert detect_copy_flavor(
            "江湖练借力，他练还力；账本自己翻页，每还清一笔旧账，新债主比上一笔更强、"
            "上一轮的盟友转头就成讨债人——他只想烧账本躺平，账本偏把他拽进风暴中心。"
        ).score == 0

    def test_a_chapter_word_alone_is_not_enough(self) -> None:
        """「前几章」出现但没有对稿件下令，不该单独定罪。"""

        assert detect_copy_flavor("前几章他还只是个杂役").score == 0


# ── 2026-08-24 端到端验证书真机漏网 ──
# reader_promise 产出「前五章让读者看到：主角沈渡命悬一线、头顶雷云开口说话的
# 爆点开局，以及器灵第一次替他改写雷劫参数、反杀暗算师兄的越阶爽感」→ 打分 0.0。
# 数词「五」在表里、锚点「前五章」也命中，但动词表
# (必须|一定要|得先|亮出|抛出|给出|推进|证明|拉住) 里**没有「让读者看到」**。
# 同一形状第二次：锚点对了、动词枚举太窄。


class TestDeliveryVerbs:
    def test_the_real_book_reader_promise_is_flagged(self) -> None:
        r = detect_copy_flavor(
            "前五章让读者看到：主角沈渡命悬一线、头顶雷云开口说话的爆点开局，"
            "以及器灵第一次替他改写雷劫参数、反杀暗算师兄的越阶爽感"
        )
        assert r.score > 0, r
        assert any("前五章" in s.matched for s in r.spans), [s.matched for s in r.spans]

    def test_other_delivery_verbs(self) -> None:
        for text in (
            "开篇让读者感觉到主角的窘迫",
            "前三章交代清楚金手指的代价",
            "全书建立一个可复用的升级节奏",
            "章末铺垫下一卷的对手",
        ):
            assert detect_copy_flavor(text).score > 0, text


class TestDeliveryVerbsNoFalsePositives:
    def test_narrative_using_the_same_verbs_stays_clean(self) -> None:
        """动词单独出现永远不够——必须有瞄准稿件的锚点。"""

        for text in (
            "他让读者般的目光扫过全场",     # 无生产锚点
            "她交代完后事就走了",
            "两人铺垫了半天才说到正题",
            "他建立了自己的商队",
        ):
            assert detect_copy_flavor(text).score == 0, text

    def test_good_copy_still_scores_zero(self) -> None:
        assert detect_copy_flavor(
            "江湖练借力，他练还力；账本自己翻页，每还清一笔旧账，"
            "新债主比上一笔更强——他只想烧账本躺平，账本偏把他拽进风暴中心。"
        ).score == 0
