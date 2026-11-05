"""Validate public queue input before creating book jobs."""

import json
import math


def finite(value, low, high):
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError("数值超出允许范围")
    return value


def whole(value, low, high, label):
    if isinstance(value, bool):
        raise ValueError(f"{label}须为 {low}–{high} 的整数")
    try:
        number = finite(value, low, high)
    except (ValueError, TypeError):
        raise ValueError(f"{label}须为 {low}–{high} 的整数") from None
    if not number.is_integer():
        raise ValueError(f"{label}须为 {low}–{high} 的整数")
    return int(number)


def check_card(card, chapters, legacy=False):
    if not isinstance(card, dict) or len(json.dumps(card, ensure_ascii=False)) > 10000:
        raise ValueError("候选字段无效或过长")
    if legacy:
        if set(card) != {"title", "premise"} or any(
            not isinstance(card.get(k), str) or not card[k].strip() or len(card[k]) > cap
            for k, cap in (("title", 80), ("premise", 120))
        ):
            raise ValueError("请填写有效的书名和简介")
    else:
        from ideation.filters import validate

        errors = validate(card, chapters, allow_names=True)
        if errors:
            raise ValueError("候选无效：" + "；".join(errors))
    return card
