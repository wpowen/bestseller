"""跨书回声门：只有逐字重合片段能毙书，零散 bigram 只挣重生。

2026-08-23 真机 custom-xuanhuan-1787461150（验证书 8）：整本书在构思阶段
被判「跨书机制回声污染」处决——project_created=false，21 分钟白跑。而它的
全部证据是：

    {"title": "香火炼神位", "shared_span": "", "shared_bigrams": ["悬疑","权谋","里那"]}

`shared_span` 为空（零逐字重合），三条 bigram 里两条是**标签词**（悬疑/权谋
——同题材书按设计共享），一条是**跨词边界碎片**（「里那」，来自「城里那个」）。
同一份日志里 debt_dominated 与母题放大都在，但它们按既定规矩只挣一次重生、
不进 detected；唯一握有杀权的就是这条回声。

我试过两条统计判据想把噪声 bigram 单独筛掉，都被自己的测量证伪：
  * PMI（词性）：噪声「里那」-0.39，真机制词「神位」-0.34，而边界碎片
    「了一」2.62 反而更高 —— 不可分。
  * 文档频率（12000 章人类语料）：「里那」2.9%，而真机制词「阵法」5.5%、
    「丹田」2.1% —— 切点无论定在哪都会切掉真词。

结论不是「换个更好的词表」，而是**证据等级本身错了**：单个 2 字 bigram 在
统计上无法与噪声区分，所以「3 个零散 bigram」不足以证明「可见地复用了某一
本旧书的材料」，更不足以判死刑。逐字 ≥5 字的重合片段才是硬证据。

修法沿用本函数里已有的两条同族先例（debt_hit / motif_hits 的注释）：软证据
挣一次重生 + 留痕，硬证据才有杀权。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
from bestseller.services import conception

_OLD_BOOK = {
    "title": "香火炼神位",
    "premise": "城隍庙里那支笔每划一下就吸一口气，小道士想往上爬。",
    "golden_finger": "判官笔",
    "trope_keywords": ["悬疑", "权谋", "阴司"],
}

#: 逐字复现真机定罪证据的候选：与旧书零重合片段，共享的三条 bigram 分别是
#: 两个标签词（悬疑/权谋，只出现在双方 trope_keywords 里）和一个跨词边界
#: 碎片（里那）。未修复前 `_mechanism_echo_report` 对它报出的正是：
#: {"shared_span": "", "shared_bigrams": ["悬疑", "权谋", "里那"]}
_REAL_SHAPE_PREMISE = "边关守将开在城里那个面摊，每夜只卖十碗。"


def _candidate(premise: str, tropes: list[str] | None = None) -> dict:
    return {
        "title": "寒陵面摊",
        "premise": premise,
        "writing_profile": {
            "character": {"golden_finger": "气机感应"},
            "market": {"trope_keywords": tropes or ["悬疑", "权谋", "边关"]},
        },
    }


class TestLabelVocabularyIsNotAFingerprint:
    def test_label_vocabulary_is_collected_from_both_sides(self) -> None:
        labels = conception._label_bigrams(_candidate("无关正文。"), [_OLD_BOOK])
        # 候选侧标签
        assert "悬疑" in labels and "边关" in labels
        # 旧书侧标签
        assert "阴司" in labels

    def test_real_machine_shape_no_longer_produces_a_finding(self) -> None:
        # 减掉标签词后只剩「里那」一条 < 阈值 3 —— 真机那条定罪整个不成案。
        report = conception._mechanism_echo_report(
            _candidate(_REAL_SHAPE_PREMISE),
            [_OLD_BOOK],
            genre="玄幻",
        )
        assert report == []

    def test_prose_level_reuse_still_produces_a_finding(self) -> None:
        """对照组：修复不得把检测器整个弄哑。

        真正复用旧书散文材料（城隍庙/小道士/支笔）时照旧报出来——证明上一条
        的「空报告」是标签被正确减掉，不是管线失灵。
        """

        report = conception._mechanism_echo_report(
            _candidate("守将带着一支笔，去城隍庙前卖面，小道士排队。"),
            [_OLD_BOOK],
            genre="玄幻",
        )
        assert report, "散文级复用必须照旧留痕"
        assert len(report[0]["shared_bigrams"]) >= 3


class TestEvidenceGrade:
    def test_bigram_only_finding_is_marked_soft(self) -> None:
        report = conception._mechanism_echo_report(
            _candidate("守将带着一支笔，去城隍庙前卖面，小道士排队。"),
            [_OLD_BOOK],
            genre="玄幻",
        )
        assert report, "本例应仍然留痕（给重写用），只是不该带杀权"
        assert all(not r["shared_span"] for r in report)
        assert conception._echo_report_has_hard_evidence(report) is False

    def test_verbatim_span_finding_is_hard(self) -> None:
        # 逐字搬过来一整句机制描述 —— 这才是可见复用。
        report = conception._mechanism_echo_report(
            _candidate("这支判官笔每划一下就吸一口气，他却拿它去卖面。"),
            [_OLD_BOOK],
            genre="玄幻",
        )
        assert report and any(r["shared_span"] for r in report)
        assert conception._echo_report_has_hard_evidence(report) is True

    def test_empty_report_has_no_hard_evidence(self) -> None:
        assert conception._echo_report_has_hard_evidence([]) is False


class TestRealMachineCase:
    """验证书 8 的真实证据形状必须不再判死刑。"""

    def test_the_real_verdict_shape_is_not_fatal(self) -> None:
        real = [
            {
                "title": "香火炼神位",
                "shared_span": "",
                "shared_bigrams": ["悬疑", "权谋", "里那"],
            }
        ]
        assert conception._echo_report_has_hard_evidence(real) is False


class TestKillPowerWiring:
    """接线钉：detected 里出现「跨书机制回声污染」的条件必须是硬证据。

    只测判定本身（纯函数），不驱动整条构思管线——但断言的是生产代码里
    真正决定 `_detected_concept_guard` 的那个表达式所依赖的函数。
    """

    def test_soft_report_does_not_arm_the_guard(self) -> None:
        soft = [{"title": "旧书", "shared_span": "", "shared_bigrams": ["甲乙", "丙丁", "戊己"]}]
        assert conception._echo_report_has_hard_evidence(soft) is False

    def test_hard_report_arms_the_guard(self) -> None:
        hard = [{"title": "旧书", "shared_span": "每划一下就吸", "shared_bigrams": []}]
        assert conception._echo_report_has_hard_evidence(hard) is True

    def test_guard_source_uses_the_predicate_not_raw_truthiness(self) -> None:
        """生产代码必须按硬证据判，而不是 `if echo_report:` 一律定罪。"""

        import inspect

        src = inspect.getsource(conception.run_conception_pipeline)
        assert "_echo_report_has_hard_evidence(echo_report)" in src
