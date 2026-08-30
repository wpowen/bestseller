# -*- coding: utf-8 -*-
"""2026-08-30 去AI味融合批：七条新轴的 SF/SNF 成对回归。

每条轴一对测试：SF（该报的报）+ SNF（不该报的不报）——speak-human-tw 的
成对评测纪律移植。校准数据见 patterns_zh.json 各规则 why 与
scripts/deai_fusion_calibrate.py（1135 真实出版章 vs 245 被淘汰 AI 稿）。
全部新轴 advisory：进 _ADVISORY_STRUCTURAL 封顶 + DESLOP 触发集，无杀权。
"""

from bestseller.services.ai_flavor.detector import (
    detect,
    micro_action_rate,
    stock_reaction_rate,
)
from bestseller.services.ai_flavor_gate import DESLOP_DISCOURSE_CATEGORIES

NEW_CATEGORIES = frozenset(
    {
        "stock_reaction",
        "micro_action_tic",
        "reverse_contrast",
        "voice_contrast",
        "trailer_ending",
        "trailer_summary",
        "sentence_signature_run",
    }
)

# 中性垫料：句形彼此不同（逗号数/长度档交错），不含新轴的任何触发词面，
# 用来把文本撑过 min_chars 而不自己污染判定。
_PAD_SENTENCES = (
    "他把担子搁在灶台边。",
    "院里的老槐树落了半地叶子，扫帚靠在墙根没人动。",
    "水开了。",
    "隔壁的婆娘隔着矮墙喊了句什么，风把后半截话吹散在巷子里，只剩个尾音。",
    "灶膛里的火舔着锅底。",
    "他往里添了把柴，火星子蹿起来，又落回灰里，屋里比方才亮了些。",
    "粥香慢慢漫出来。",
    "巷口卖炭的车轱辘碾过石板，吱呀吱呀，由远及近，又由近及远。",
)


def _pad(n_chars: int = 1400) -> str:
    parts: list[str] = []
    i = 0
    while sum(len(p) for p in parts) < n_chars:
        parts.append(_PAD_SENTENCES[i % len(_PAD_SENTENCES)])
        if i % 3 == 2:
            parts.append("\n")
        i += 1
    return "".join(parts)


def _cats(text: str) -> set[str]:
    return {s.category for s in detect(text, language="zh-CN").spans}


# ── stock_reaction 罐头反应镜头 ──────────────────────────────────────────


def test_stock_reaction_fires_on_saturation() -> None:
    text = _pad() + "指节微微发白。喉结滚了滚。眼眶一下子发红。声音也跟着发紧。"
    assert "stock_reaction" in _cats(text)


def test_stock_reaction_ignores_sparse_and_dialogue() -> None:
    # 单处是人类常态（人类 13.4% 章有命中）；对白里的不算叙述层。
    sparse = _pad() + "她眼眶红了。"
    assert "stock_reaction" not in _cats(sparse)
    quoted = _pad() + "“他喉结滚了滚，指节都发白了，眼眶也红了，声音发紧。”"
    assert "stock_reaction" not in _cats(quoted)


# ── micro_action_tic 「了一下」尾巴 ──────────────────────────────────────


def test_micro_action_fires_on_density() -> None:
    tics = "他顿了一下。敲了两下。看了一眼。停了一下。咳了一声。愣了一下。转了一圈。应了一声。想了一会。缓了一口气。"
    assert "micro_action_tic" in _cats(_pad() + tics)


def test_micro_action_ignores_normal_usage() -> None:
    # 人类中位 0.70/千字：三五处正常用法不报。
    assert "micro_action_tic" not in _cats(_pad() + "他顿了一下，又看了一眼。")


# ── reverse_contrast 反序对比排比 ────────────────────────────────────────


def test_reverse_contrast_fires_on_litany() -> None:
    text = _pad() + "这是灵米，不是供香。那是头茬卤，不是二茬。闻到的是母膏，不是封泥。"
    assert "reverse_contrast" in _cats(text)


def test_reverse_contrast_spares_legit_comparison() -> None:
    # 「而不是」是人类合法议论比较（校准样例：是艺术，而不是生活）；
    # 「不是吗」是反问尾；两处以下密度不足——全部放行。
    legit = _pad() + "他要的是一队人，而不是一个人。这是个游戏，而不是历史。写的是艺术，而不是生活。"
    assert "reverse_contrast" not in _cats(legit)
    tag = _pad() + "这是自然而然的演出，不是吗。"
    assert "reverse_contrast" not in _cats(tag)
    below = _pad() + "这是灵米，不是供香。那是头茬卤，不是二茬。"
    assert "reverse_contrast" not in _cats(below)


# ── voice_contrast 音量反差腔 ────────────────────────────────────────────


def test_voice_contrast_fires_on_repeat() -> None:
    text = _pad() + "声音不大，却压住了全场。声音不高，但每个人都听见了。"
    assert "voice_contrast" in _cats(text)


def test_voice_contrast_spares_single_use() -> None:
    # 单次是人类常见写法（人类 1.1% 章有 1 次）——聚集才报。
    assert "voice_contrast" not in _cats(_pad() + "声音不大，却压住了全场。")


# ── trailer_ending / trailer_summary 章末收尾腔（末600字窗口） ───────────


def test_trailer_ending_fires_only_in_tail() -> None:
    line = "谁也没想到，一场大祸正朝着小巷压来。"
    assert "trailer_ending" in _cats(_pad() + line)
    # 同一句在章中（非末 600 字）是叙述，不是收尾腔——位置门控。
    assert "trailer_ending" not in _cats(line + _pad())


def test_trailer_summary_fires_only_in_tail() -> None:
    line = "这一夜注定无人入眠。"
    assert "trailer_summary" in _cats(_pad() + line)
    assert "trailer_summary" not in _cats(line + _pad())


def test_trailer_spares_in_scene_curtain() -> None:
    # 场内报幕（真人语料形态：钟声响起，比赛正式拉开序幕）不是叙述者预告。
    text = _pad() + "钟声再度响起，比赛正式拉开序幕。"
    assert "trailer_ending" not in _cats(text)


# ── sentence_signature_run 相邻句同构 ────────────────────────────────────


def test_signature_run_fires_on_three_clones() -> None:
    run = (
        "他往前走了两步，把灯挂在门边。"
        "她回头看了一眼，把伞收在墙角。"
        "风从巷口吹过来，把幌子掀了一角。"
    )
    assert "sentence_signature_run" in _cats(_pad() + "\n" + run)


def test_signature_run_spares_two_clones_and_varied_prose() -> None:
    two = "他往前走了两步，把灯挂在门边。她回头看了一眼，把伞收在墙角。"
    assert "sentence_signature_run" not in _cats(_pad() + "\n" + two)
    # 垫料本身句形交错——0/1135 真实出版章命中的轴不许在正常叙述上开火。
    assert "sentence_signature_run" not in _cats(_pad(3000))


# ── 契约：advisory 封顶 + 触发集 + 公开量具 ──────────────────────────────


def test_new_axes_are_advisory_capped_and_deslop_wired() -> None:
    # 全部进 deslop 触发集（挣重生）……
    assert NEW_CATEGORIES <= DESLOP_DISCOURSE_CATEGORIES
    # ……但单独堆再多也推不过 block 线（advisory 封顶=无杀权）。
    diseased = (
        _pad()
        + "指节微微发白。喉结滚了滚。眼眶一下子发红。声音也发紧。"
        + "他顿了一下。敲了两下。看了一眼。停了一下。咳了一声。愣了一下。转了一圈。应了一声。想了一会。"
        + "这是灵米，不是供香。那是头茬卤，不是二茬。闻到的是母膏，不是封泥。"
        + "声音不大，却压住了全场。声音不高，但每个人都听见了。"
        + "\n他往前走了两步，把灯挂在门边。她回头看了一眼，把伞收在墙角。风从巷口吹过来，把幌子掀了一角。\n"
        + "谁也没想到，这一夜注定无人入眠，一切才刚刚开始。"
    )
    report = detect(diseased, language="zh-CN")
    hit = {s.category for s in report.spans} & NEW_CATEGORIES
    assert len(hit) >= 6, hit
    new_axis_weight = sum(
        4.0 for s in report.spans if s.category in NEW_CATEGORIES
    )
    # 封顶 24：新轴合计贡献不可能超过 cap（block 线 cn=38）。
    assert min(new_axis_weight, 24.0) < 38.0


def test_public_gauges_track_density() -> None:
    clean = _pad()
    sick = _pad() + "指节微微发白。喉结滚了滚。眼眶一下子发红。声音也发紧。" * 3
    assert stock_reaction_rate(sick) > stock_reaction_rate(clean)
    tics = "他顿了一下。敲了两下。看了一眼。停了一下。咳了一声。愣了一下。" * 3
    assert micro_action_rate(_pad() + tics) > micro_action_rate(clean)


def test_debt_metaphor_leak_chain_is_gone() -> None:
    # 2026-08-30 死链清理：退役检测器的触发集入口一并拆除。
    assert "debt_metaphor_leak" not in DESLOP_DISCOURSE_CATEGORIES
