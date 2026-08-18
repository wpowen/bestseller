"""万物拟人腔检测（2026-08-18《矿脉认主》用户终审定罪）。

病灶：无生命主语 + 强施动动词（「石头自己拱了一下」「凉意顺着脊椎爬」），
且**逐句如此**——每个名词都被安排一个"生动"动词。单处拟人是合法重锤，
过密是 AI 腔（用户原话「动词总是用错，一个字都不想读」）。

病根链：紧凑纪律「同一个高冲击动词≤4次」只限复读不限错配 → 模型为服从
限额轮换生僻强动词 → 搭配越来越错。所以修复是三件套：检测器（本文件）+
紧凑纪律改总量措辞 + deslop 自查表第 15 条。

语料校准（.distillation_private 400 章抽样，同一正则）：人类中位 0.00/千字、
p90 0.31、p99 0.95；《矿脉认主》中位 1.25、ch1=5.94。阈值=密度 ≥1.0/千字
且绝对数 ≥4；实测人类误报率 0.5%、我们 10/26 章命中。
warn-only 无杀权——只挣重生和留痕（2026-08-15 铁律）。
"""

from __future__ import annotations

from bestseller.services.ai_flavor.detector import detect
from bestseller.services.ai_flavor_gate import DESLOP_DISCOURSE_CATEGORIES
from bestseller.services.anti_ai_voice_discipline import (
    render_compact_writer_discipline,
)
from bestseller.services.deslop_revise import _EXTRA_SELF_CHECK, _QUOTE_FREE_CATEGORIES


def _agency_spans(text: str):
    return [
        s for s in detect(text, language="zh").spans if s.category == "inanimate_agency"
    ]


# 干净填充：撑过 min_chars=800 且不触发本检测器（平实动词搭配）
PAD = (
    "他把窗关上，回头看灶台，锅里的水开了，白汽把锅盖顶得啪啪作响。"
    "院外有人挑担经过，扁担吱呀作响，由近及远。她把抹布搭在盆沿上，"
    "转身添柴，柴堆边的老猫抬了抬眼皮。灯亮着，影子落在墙上。"
) * 10

# 病文：真机 ch1 的搭配形状（无生命主语+肢体动词，密度远超人类 p99）
DISEASED = PAD + (
    "石头自己拱了一下。凉意顺着脊椎往下爬。声音从掌心往骨头里钻。"
    "影子扑到墙角。哨声弹了三下。寒意咬着他的后颈。"
    "石头又拱了一下。光从门缝里挤进来。"
)


def test_diseased_density_flags():
    spans = _agency_spans(DISEASED)
    assert spans, "ch1 同款密度必须命中"
    span = spans[0]
    assert span.severity == "warn", "无杀权：只挣重生和留痕"
    assert not span.remove_sentence_on_block
    assert "平实动词" in span.why, "why 必须带正例改法"


def test_clean_prose_passes():
    assert _agency_spans(PAD) == [], "平实搭配不许误伤"


def test_sparse_anthropomorphism_is_legal_hammer():
    # 全章只有 2-3 处拟人 = 合法修辞重锤，不是病
    text = PAD + "石头自己拱了一下。凉意顺着脊椎往下爬。"
    assert _agency_spans(text) == []


def test_stative_verbs_not_counted():
    # 「石头烫」是状态不是施动——《矿脉认主》核心设定就是滚烫石头，
    # 把状态词算成拟人会让 deslop 去改合法句。
    text = PAD + ("石头滚烫。石头烫得他掌心发红。石头很烫。石头烫。" * 3)
    assert _agency_spans(text) == []


def test_min_chars_guard():
    short = "石头拱了一下。凉意爬上来。声音钻进骨头。影子扑过来。" * 3
    assert len(short) < 800
    assert _agency_spans(short) == [], "短文本不计密度（折叠计数量级失明教训）"


def test_wired_into_deslop_trigger_and_quote_free():
    # patcher 改不了动词骨架 → 必须进 deslop 触发集（verb_tic_spam 判例）；
    # 引病句原文=喂骨架 → 必须 quote-free（2026-08-15 写手侧引文证伪）。
    assert "inanimate_agency" in DESLOP_DISCOURSE_CATEGORIES
    assert "inanimate_agency" in _QUOTE_FREE_CATEGORIES
    assert "万物拟人" in _EXTRA_SELF_CHECK, "自查表第 15 条必须在"


def test_compact_discipline_closes_rotation_loophole():
    # 旧措辞「同一个高冲击动词别超过 N 次」= 逼模型轮换生僻动词的许可证。
    text = render_compact_writer_discipline(language="zh-CN", scope="chapter")
    assert "合计" in text, "必须是总量限制"
    assert "同一个高冲击动词" not in text, "轮换漏洞措辞必须删除"
    assert "物理上真会做的事" in text
