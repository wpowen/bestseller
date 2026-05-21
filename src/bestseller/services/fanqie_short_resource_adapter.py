# ruff: noqa: RUF001
"""Convert long-form planning assets into short-story-safe resources."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bestseller.domain.fanqie_short_v2 import FanqieShortResourceCard

_SERIAL_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("全书", "本篇"),
    ("本书", "本篇"),
    ("第一章", "第一段"),
    ("第1章", "第1段"),
    ("第二章", "第二段"),
    ("第2章", "第2段"),
    ("第三章", "第三段"),
    ("第3章", "第3段"),
    ("卷一", "本篇前半"),
    ("卷二", "本篇后半"),
    ("章节", "段落"),
    ("下章", "后续段落"),
    ("下一章", "后续段落"),
)

_SERIAL_RISK_TERMS = (
    "多卷",
    "长期伏笔",
    "未完待续",
    "且听下回",
    "真正的真相",
    "主线谜团贯穿",
)


def adapt_long_form_resources_for_short(
    *,
    book_spec: Mapping[str, Any] | None = None,
    cast_spec: Mapping[str, Any] | None = None,
    premise: str = "",
    max_cards: int = 8,
) -> list[FanqieShortResourceCard]:
    """Extract reusable long-form assets without importing long-form pacing."""

    book = _mapping(book_spec)
    cast = _mapping(cast_spec)
    cards: list[FanqieShortResourceCard] = []

    premise_text = _sanitize_for_short(premise or _text(book.get("premise")))
    if premise_text:
        cards.append(
            _card(
                "premise",
                "核心前提",
                premise_text,
                conflict_use="把长篇前提压缩成一个开局可见的当场危机。",
                payoff_use="短篇只兑现一次最清晰的情绪胜负，不展开多卷任务。",
            )
        )

    for key, label in (
        ("core_promise", "读者承诺"),
        ("reader_promise", "读者承诺"),
        ("main_thread", "主线压力"),
        ("central_conflict", "核心冲突"),
    ):
        value = _sanitize_for_short(_text(book.get(key)))
        if value:
            cards.append(
                _card(
                    key,
                    label,
                    value,
                    conflict_use="必须在前300字落到一个人、一句话、一份证据或一个动作上。",
                    payoff_use="前30%先给小胜，结尾给本篇胜负。",
                )
            )

    series_engine = _mapping(book.get("series_engine") or book.get("serialization"))
    for key, label in (
        ("payoff_rhythm", "爽点节奏"),
        ("chapter_arc", "段落弧线"),
        ("first_three_chapter_goal", "前段目标"),
    ):
        value = _sanitize_for_short(_text(series_engine.get(key)))
        if value:
            cards.append(
                _card(
                    key,
                    label,
                    value,
                    conflict_use="只取最强的一个起压点和一个反击点。",
                    payoff_use="改写为高密度短回报，禁止只做铺垫。",
                )
            )

    protagonist = _mapping(cast.get("protagonist"))
    protagonist_name = _text(protagonist.get("name") or protagonist.get("character_ref"))
    if protagonist_name:
        cards.append(
            _card(
                "protagonist",
                f"主角：{protagonist_name}",
                _sanitize_for_short(
                    _text(protagonist.get("role") or protagonist.get("core_wound"))
                    or "主角必须成为第一屏视角焦点。"
                ),
                conflict_use="第一屏必须让主角承压、失去、被误解或被迫选择。",
                payoff_use="主角亲手完成关键反击，不能由旁人替主角解决。",
            )
        )

    antagonist = _mapping(cast.get("antagonist"))
    antagonist_name = _text(antagonist.get("name") or antagonist.get("character_ref"))
    if antagonist_name:
        cards.append(
            _card(
                "antagonist",
                f"反派：{antagonist_name}",
                _sanitize_for_short(
                    _text(antagonist.get("role") or antagonist.get("pressure_method"))
                    or "反派必须制造可见压迫。"
                ),
                conflict_use="反派的压迫要在开局公开化，最好能留下可反咬的证据。",
                payoff_use="结尾必须让反派承担本篇代价，不留下一卷再算账。",
            )
        )

    golden_finger = _first_mapping(book, ("golden_finger", "ability_system", "power_system"))
    if golden_finger:
        name = _text(golden_finger.get("name") or golden_finger.get("type") or "能力规则")
        use = _sanitize_for_short(
            _text(golden_finger.get("rule") or golden_finger.get("description"))
            or "能力必须在开篇可见并参与当前冲突。"
        )
        cards.append(
            _card(
                "ability",
                name,
                use,
                conflict_use="50-200字内让能力可见，但同时暴露限制或代价。",
                payoff_use="能力先给小反馈，再制造更高代价。",
            )
        )

    return _dedupe_cards(cards)[:max_cards]


def render_short_resource_prompt_block(cards: Sequence[FanqieShortResourceCard]) -> str:
    if not cards:
        return ""
    lines = ["【短篇可复用长篇资源】"]
    lines.extend(card.to_prompt_line() for card in cards)
    lines.append("- 改写原则: 只借设定、人物压力和回报机制；不继承长篇铺垫、卷结构和下章钩子。")
    return "\n".join(lines)


def _card(
    resource_id: str,
    name: str,
    short_use: str,
    *,
    conflict_use: str = "",
    payoff_use: str = "",
) -> FanqieShortResourceCard:
    risk_notes = [
        "不得扩写为长篇设定说明。",
        "不得保留卷/章式悬念。",
    ]
    if any(term in short_use for term in _SERIAL_RISK_TERMS):
        risk_notes.append("原素材含长篇连载风险，短篇中只能抽象使用。")
    return FanqieShortResourceCard(
        resource_id=resource_id,
        card_type=resource_id,
        name=name,
        short_use=short_use,
        conflict_use=conflict_use,
        payoff_use=payoff_use,
        constraints=[
            "前300字必须转成当场冲突。",
            "前30%必须有小回报。",
            "末段必须本篇收束。",
        ],
        risk_notes=risk_notes,
    )


def _sanitize_for_short(value: str) -> str:
    text = " ".join(value.split())
    for old, new in _SERIAL_REPLACEMENTS:
        text = text.replace(old, new)
    return text.strip()


def _dedupe_cards(cards: Sequence[FanqieShortResourceCard]) -> list[FanqieShortResourceCard]:
    result: list[FanqieShortResourceCard] = []
    seen: set[tuple[str, str]] = set()
    for card in cards:
        key = (card.card_type, card.name)
        if key in seen:
            continue
        result.append(card)
        seen.add(key)
    return result


def _first_mapping(
    payload: Mapping[str, Any],
    keys: Sequence[str],
) -> Mapping[str, Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()
