"""Prompt/rendering helpers for Fanqie market profiles."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bestseller.domain.fanqie_market import FanqieCategoryProfile, FanqieCraftProfile


def render_fanqie_market_profile_block(
    profile: FanqieCategoryProfile | Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
    max_items: int = 4,
) -> str:
    """Render compact category-level market signals for planner prompts."""

    payload = _payload(profile)
    if not payload:
        return ""

    category = _text(payload.get("category"))
    if not category:
        return ""

    hook_patterns = _list(payload.get("hook_patterns"), max_items=max_items)
    structure_patterns = _list(payload.get("structure_patterns"), max_items=max_items)
    payoff_patterns = _list(payload.get("payoff_patterns"), max_items=max_items)
    settings = _list(payload.get("dominant_settings"), max_items=max_items)
    style_guidelines = _list(payload.get("style_guidelines"), max_items=max_items)
    safety_notes = _list(payload.get("safety_notes"), max_items=max_items)

    if language.lower().startswith("zh"):
        lines = [
            "【番茄榜单市场画像】",
            f"- 类目: {category}",
        ]
        _append(lines, "主流设定", settings)
        _append(lines, "入口钩子", hook_patterns)
        _append(lines, "结构循环", structure_patterns)
        _append(lines, "回报模式", payoff_patterns)
        _append(lines, "风格约束", style_guidelines)
        _append(
            lines,
            "安全边界",
            safety_notes
            or ["只使用类目级抽象规律, 不复刻书名、角色、作者文风或专属设定。"],
        )
        return "\n".join(lines)

    lines = [
        "[Fanqie Market Profile]",
        f"- Category: {category}",
    ]
    _append(lines, "Dominant settings", settings)
    _append(lines, "Hook patterns", hook_patterns)
    _append(lines, "Structure loops", structure_patterns)
    _append(lines, "Payoff patterns", payoff_patterns)
    _append(lines, "Style controls", style_guidelines)
    _append(
        lines,
        "Copy boundary",
        safety_notes
        or ["Use category-level abstractions only; do not copy titles, characters, or prose."],
    )
    return "\n".join(lines)


def render_fanqie_craft_profile_block(
    profile: FanqieCraftProfile | Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
    max_items: int = 4,
) -> str:
    """Render anonymous craft controls without source titles or author names."""

    payload = _payload(profile)
    if not payload:
        return ""

    category = _text(payload.get("category"))
    confidence = payload.get("confidence")
    allowed = _list(payload.get("allowed_style_principles"), max_items=max_items)
    disallowed = _list(payload.get("disallowed_copy_targets"), max_items=max_items)
    hook_rules = _list(payload.get("hook_rules"), max_items=max_items)
    pacing_rules = _list(payload.get("pacing_rules"), max_items=max_items)
    structure_rules = _list(payload.get("structure_rules"), max_items=max_items)
    sentence_style = _text(payload.get("sentence_style"))
    paragraph_style = _text(payload.get("paragraph_style"))
    safety_boundary = _text(payload.get("safety_boundary"))

    if not any(
        (
            allowed,
            disallowed,
            hook_rules,
            pacing_rules,
            structure_rules,
            sentence_style,
            paragraph_style,
            safety_boundary,
        )
    ):
        return ""

    if language.lower().startswith("zh"):
        lines = ["【番茄榜单匿名工艺卡】"]
        if category:
            lines.append(f"- 类目: {category}")
        if confidence is not None:
            lines.append(f"- 置信度: {confidence}")
        _append(lines, "可采用的抽象写法", allowed)
        _append(lines, "禁止复刻边界", disallowed)
        _append(lines, "开篇/标题钩子规则", hook_rules)
        _append(lines, "节奏与反馈规则", pacing_rules)
        _append(lines, "结构规则", structure_rules)
        if sentence_style:
            lines.append(f"- 句式建议: {sentence_style}")
        if paragraph_style:
            lines.append(f"- 段落建议: {paragraph_style}")
        if safety_boundary:
            lines.append(f"- 安全边界: {safety_boundary}")
        return "\n".join(lines)

    lines = ["[Fanqie Anonymous Market Craft Card]"]
    if category:
        lines.append(f"- Category: {category}")
    if confidence is not None:
        lines.append(f"- Confidence: {confidence}")
    _append(lines, "Allowed abstract craft principles", allowed)
    _append(lines, "Do-not-copy boundaries", disallowed)
    _append(lines, "Hook rules", hook_rules)
    _append(lines, "Pacing and feedback rules", pacing_rules)
    _append(lines, "Structure rules", structure_rules)
    if sentence_style:
        lines.append(f"- Sentence style: {sentence_style}")
    if paragraph_style:
        lines.append(f"- Paragraph style: {paragraph_style}")
    if safety_boundary:
        lines.append(f"- Safety boundary: {safety_boundary}")
    return "\n".join(lines)


def _payload(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, FanqieCraftProfile):
        return value.to_prompt_card()
    if isinstance(value, FanqieCategoryProfile):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


def _list(value: object, *, max_items: int) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [
            item.strip()
            for item in value.replace("\uff0c", ",").replace("\u3001", ",").split(",")
        ]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        values = [str(item).strip() for item in value if str(item).strip()]
    else:
        values = [str(value).strip()] if str(value).strip() else []

    result: list[str] = []
    for item in values:
        if item and item not in result:
            result.append(item)
        if len(result) >= max_items:
            break
    return result


def _text(value: object) -> str:
    return str(value or "").strip()


def _append(lines: list[str], label: str, items: Sequence[str]) -> None:
    if items:
        lines.append(f"- {label}: {'; '.join(items)}")
