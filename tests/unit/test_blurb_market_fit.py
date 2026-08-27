"""简介的市场分布落位（T2，2026-08-27）。

用户报简介「没有吸引力、不通顺」，框架自评 83.9 分判通过——分歧在标准，
不在机器。本模块换个问法：不判「好不好」，只判**「像不像榜单书」**。

判据有**真正的负对照**（这是本轮调研的关键突破——负样本一直在手边：
框架自己生成的、用户已逐本判差的简介）：

    n=1139 榜单男频          vs   n=32 框架产出
    含「我」      54.3%              0%
    含感叹号      64.4%              0%
    含对白        46.1%             12.5%
    【】开头      50.6%              0%
    段落数中位       9                4

框架写的是**第三人称书面陈述体（内容提要）**，榜单写的是**对读者说话的
口播腔**。不是文笔差距，是语体错位。

两个设计教训写在这里，避免重犯：

1. **第一版按分位判，永远不触发**。这几个标记的 p05 恰好是 0（不少榜单书
   也缺其中某项），单看一项没有意义。有意义的是**复合计数**：
   榜单中位 3 项、0 项的只占 9.5%；框架中位 0 项、0 项的占 78%。
2. **连续量按分位判是纯噪声**。实测 chars / digit_density 对框架命中 0%、
   对榜单误伤各 10%（按构造必然如此）——一条都没抓到问题，只贡献误伤。
   已降为观测量。

最终分离度：框架命中 78%，榜单误伤 9%，**8.2 倍**。
"""

from __future__ import annotations

import pytest

from bestseller.services.blurb_market_fit import (
    VOICE_MIN,
    blurb_voice_score,
    evaluate_blurb_market_fit,
    load_baseline,
)

pytestmark = pytest.mark.unit

# 框架产出的真实形状：第三人称陈述，零语体标记
_FRAMEWORK = (
    "灵米一舔，三成陈粮——他一句话砸了外门二十家饭堂的招牌。\n"
    "霍七，万法宗浮空城没灵根的底层跑腿，全凭一条舌头辨真假。\n"
    "他把掺假的米摊在案上，管事的脸白了。"
)
# 榜单的真实形状：标签行 + 口播腔 + 第一人称 + 感叹
_BESTSELLER = (
    "【快节奏+从部落到帝国+西幻史诗】\n"
    "没有人比我更懂建国！\n"
    "“建国前期困难？”\n"
    "无妨，自然有大冤种前来助阵。"
)


class TestTheRealContrast:
    def test_framework_style_is_flagged(self):
        r = evaluate_blurb_market_fit(_FRAMEWORK, channel="男频")
        assert r["checked"] is True
        assert r["passed"] is False
        assert r["voice_score"] == 0
        assert [f["feature"] for f in r["findings"]] == ["voice_register"]

    def test_bestseller_style_passes(self):
        r = evaluate_blurb_market_fit(_BESTSELLER, channel="男频")
        assert r["passed"] is True
        assert r["voice_score"] >= VOICE_MIN

    def test_vacuity_a_percentile_rule_would_never_have_fired(self):
        """空转检验：第一版按 p05 判——而这些标记的 p05 就是 0，
        所以「等于 0」不算「低于 p05」，规则恒不触发。"""
        band = load_baseline().get("男频") or {}
        for name in ("exclamation", "question", "dialogue", "first_person"):
            assert float(band[name]["p05"]) == 0.0, name


class TestVoiceScore:
    def test_it_counts_the_five_markers(self):
        assert blurb_voice_score("")[0] == 0
        assert blurb_voice_score("【标签】")[0] == 1
        n, hits = blurb_voice_score('【标签】“我来了！”真的？')
        assert n == 5 and set(hits) == {
            "tagline_bracket", "exclamation", "question", "dialogue", "first_person",
        }

    def test_a_single_marker_clears_the_conservative_floor(self):
        """阈值取 ≥1：榜单里只有 9.5% 达不到，是保守线，不是理想线。"""
        assert evaluate_blurb_market_fit("他走了。真的吗？")["passed"] is True


class TestItFailsQuietly:
    def test_missing_baseline_is_skipped_not_crashed(self):
        r = evaluate_blurb_market_fit(_FRAMEWORK, baseline={})
        assert r["checked"] is False and r["findings"] == []

    def test_empty_blurb_is_skipped(self):
        assert evaluate_blurb_market_fit("", channel="男频")["checked"] is False

    def test_the_receipt_is_always_shaped(self):
        for text in ("", _FRAMEWORK, _BESTSELLER):
            r = evaluate_blurb_market_fit(text, channel="男频")
            assert {"checked", "channel", "findings"} <= set(r)


class TestContinuousFeaturesAreObservationsOnly:
    def test_they_never_produce_findings(self):
        """实测它们对框架命中 0%、对榜单误伤 10%——纯噪声。"""
        long_blurb = "【标签】" + "他走了。" * 400
        r = evaluate_blurb_market_fit(long_blurb, channel="男频")
        assert all(f["feature"] == "voice_register" for f in r["findings"])

    def test_but_they_are_still_recorded(self):
        r = evaluate_blurb_market_fit(_FRAMEWORK, channel="男频")
        assert {"chars", "digit_density", "paragraphs"} <= set(r["features"])


class TestBaselineProvenance:
    def test_the_baseline_declares_its_snapshot_nature(self):
        """单日快照。热度是活数据，跨日重复采样后再固化（2026-08-08 定案）。"""
        meta = load_baseline().get("_meta") or {}
        assert meta.get("source") == "fanqiehub"
        assert "快照" in str(meta.get("note", ""))

    def test_both_channels_are_calibrated(self):
        base = load_baseline()
        for ch in ("男频", "女频"):
            assert base[ch]["_sample"] > 500
