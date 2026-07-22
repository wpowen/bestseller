"""R3: negated-definition "不是X，而是Y" + conclusion-first / epiphany.

The goal names these explicitly. They are near-absent in the already-
remediated benchmark book, so the acceptance bar is twofold:
* the patterns DO fire on synthetic model-ism text, and
* they do NOT regress (false-positive) on the clean book — verified
  separately in the diagnostic, asserted here on clean snippets.
"""

from __future__ import annotations

from bestseller.services.ai_flavor.detector import detect


def _cats(text: str) -> list[str]:
    return [s.category for s in detect(text, language="zh").spans]


def test_not_x_but_y_fires() -> None:
    text = "他终于明白，这不是一场考核，而是一场针对他的清算。"
    assert "negated_definition" in _cats(text)


def test_not_x_period_shi_y_variant_fires() -> None:
    # 句号/跨句变体——真实生成里逃过只认逗号的规则的写法。
    assert "negated_definition" in _cats("废种动了。不是发芽。是皮壳先裂开。")
    assert "negated_definition" in _cats("那点光浮上来。不是墨。是光阴。")


def test_mei_shenme_shi_y_self_explanation_fires() -> None:
    text = "她摸了摸玉佩。其实没什么舍不得，是已经舍不得了。"
    assert "negated_definition" in _cats(text)


def test_yu_qi_shuo_fires() -> None:
    text = "与其说她在劝他，不如说她在替自己开脱。"
    assert "negated_definition" in _cats(text)


def test_epiphany_announcement_over_reliance_fires() -> None:
    # Density tell: 3+ announcements in a chapter (research: 每章 >2 次).
    text = (
        "他突然明白了卫荆的用意。\n\n"
        "走到巷口，他忽然意识到自己被人盯上了。\n\n"
        "等看清那张脸，他这才瞬间懂了整件事的来龙去脉。"
    )
    assert "epiphany_announcement" in _cats(text)


def test_single_epiphany_is_allowed() -> None:
    # One legit realization is fine — only over-reliance is flagged.
    text = "他盯着那行字看了很久，突然明白过来，手心已经全是汗。"
    assert "epiphany_announcement" not in _cats(text)


def test_negation_inside_dialogue_not_flagged() -> None:
    # Characters may legitimately speak the formula; dialogue is protected.
    text = "“这不是钱的事，而是规矩。”他把茶碗往桌上一搁。"
    assert "negated_definition" not in _cats(text)


def test_plain_negation_not_flagged() -> None:
    # Ordinary "不是…" without the 而是/更是 turn is NOT the formula.
    text = "桌上那封信不是他写的。他盯着落款看了很久，没敢拆。"
    cats = _cats(text)
    assert "negated_definition" not in cats


def test_clean_narration_no_false_positive() -> None:
    text = (
        "焦糊味裹着湿灰从地窖口灌进来，谢迟把妹妹往墙角又挪了挪，"
        "才敢回头看那束手电光。卫荆站在光里，灰绳束腰。"
    )
    cats = _cats(text)
    assert "negated_definition" not in cats
    assert "epiphany_announcement" not in cats
