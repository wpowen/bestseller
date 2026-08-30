# -*- coding: utf-8 -*-
"""2026-08-30 去AI味融合批：copy_flavor 新增五族的 SF/SNF 成对回归。

校准语料：2218 条真实番茄榜单简介（.benchmark/fanqie_rank.json）。
SNF 侧样本直接取自真实简介里「看起来像、实际是人类常态」的形态——
粗形词表在这批语料上的误杀教训见 copy_flavor.py 模块 docstring。
"""

from bestseller.services.copy_flavor import detect_copy_flavor, pick_reader_facing


def _cats(text: str) -> set[str]:
    return {s.category for s in detect_copy_flavor(text).spans}


# ── colon_hat 擂鼓帽句 ──────────────────────────────────────────────────


def test_colon_hat_fires_on_drumroll() -> None:
    assert "colon_hat" in _cats("他一路查到县衙。真相：师父根本没有死。")
    assert "colon_hat" in _cats("答案是：从来没有什么天才。")


def test_colon_hat_spares_character_goal() -> None:
    # 真实简介形态：人物目标的合法叙述（韩渊的目标很简单：吃、睡、变强）。
    assert "colon_hat" not in _cats("他的目标很简单：吃、睡、变强。")


# ── fake_interaction 助手式假互动 ───────────────────────────────────────


def test_fake_interaction_fires_on_assistant_cta() -> None:
    assert "fake_interaction" in _cats("一文读懂修仙界的隐秘规则，建议收藏。")
    assert "fake_interaction" in _cats("这样的选择，你觉得呢？")


def test_fake_interaction_spares_platform_norms() -> None:
    # 「评论区见/欢迎催更」是真实平台简介的人类常态（0.36% 在用），不收。
    assert "fake_interaction" not in _cats("评分刚出会涨，详见评论区，欢迎催更。")


# ── uplift_closer 万金油升华 ────────────────────────────────────────────


def test_uplift_closer_fires() -> None:
    assert "uplift_closer" in _cats("少年从大山中走出，未来可期，让我们拭目以待。")


def test_uplift_closer_spares_concrete_hook() -> None:
    assert "uplift_closer" not in _cats("他把最后一枚铜钱压在案上：明天开城门，先杀谁？")


# ── milestone_hype 里程碑腔 ─────────────────────────────────────────────


def test_milestone_hype_fires() -> None:
    assert "milestone_hype" in _cats("这一战标志着旧时代的终结，是修仙史上的里程碑。")


def test_milestone_hype_spares_narrative_witness() -> None:
    # 「见证了」是叙事动词（真实简介：亲眼见证了封腾的出轨），刻意不收。
    assert "milestone_hype" not in _cats("她亲眼见证了封腾的出轨，重生后活出自己的一片天。")


# ── contrast_saturation 翻案腔饱和（≥2 才报）────────────────────────────


def test_contrast_saturation_needs_two() -> None:
    one = "他们说，爷爷不是病死的，而是被秦岭里的东西点了名。"
    assert "contrast_saturation" not in _cats(one)
    two = one + "我后来才懂，那三个字不是遗言，而是警告。"
    assert "contrast_saturation" in _cats(two)


# ── pick_reader_facing 联动：新族参与选稿 ───────────────────────────────


def test_pick_reader_facing_skips_new_family_hits() -> None:
    dirty = "一文读懂本书设定，建议收藏。"
    clean = "他把最后一枚铜钱压在案上，抬头问掌柜：先杀谁？"
    assert pick_reader_facing(dirty, clean) == clean
