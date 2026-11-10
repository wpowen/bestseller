"""Nonhuman speaking contracts, writer context, and grounded prose findings."""

from copy import deepcopy
import re

from .precision import _sentences
from .text import _paragraph_spans


_NONHUMAN = re.compile(r"契兽|灵宠|幼兽|兽宠|宠兽|战宠|灵兽|妖兽|神兽|雪兽|兽形|兽身")
_SPEECH_VERB = r"(?:说|道|问|答|开口|叫|喊|顶回)"
_ANIMAL_ACTION = re.compile(r"咬|拱|顶住|蹭|扑|拍|甩|挠|舔|呜叫|兽鸣|低吼|发出|用(?:脑袋|身体|爪|鼻)")
_ORIGIN = re.compile(r"第一次|头一回|人声|人话|开口|发声|声音|喉间|契印[^。！？]{0,16}(?:补全|完整|变化)")
_QUOTE = re.compile(r"[“「『‘](.{1,160}?)[”」』’]")


def is_nonhuman(card):
    if not isinstance(card, dict):
        return False
    description = " ".join(str(card.get(key, "")) for key in ("identity", "species", "type"))
    return bool(_NONHUMAN.search(description))


def _can_speak(card):
    value = card.get("can_speak_human", "否")
    return value is True or str(value).strip().lower() in {"是", "可", "能", "yes", "true", "1"}


def _from_chapter(card):
    try:
        value = int(card.get("human_speech_from_chapter", 0))
    except (TypeError, ValueError):
        value = 0
    return max(0, value)


def normalize_speech_contracts(bible):
    """Give every nonhuman card an explicit, conservative speaking contract."""
    normalized = deepcopy(bible)
    cards = normalized.get("characters", []) if isinstance(normalized, dict) else []
    for card in cards if isinstance(cards, list) else []:
        if not is_nonhuman(card):
            continue
        allowed = _can_speak(card)
        card["can_speak_human"] = "是" if allowed else "否"
        card["human_speech_from_chapter"] = max(1, _from_chapter(card)) if allowed else 0
        if not allowed and card.get("address"):
            card["narrative_address"] = card.pop("address")
            for field in ("address_target", "address_origin"):
                if card.get(field):
                    card["narrative_" + field] = card[field]
                card.pop(field, None)
    return normalized


def writer_speech_bible(bible):
    """Hide legacy oral-address fields from the writer while preserving the saved bible."""
    current = deepcopy(bible)
    cards = current.get("characters", []) if isinstance(current, dict) else []
    for card in cards if isinstance(cards, list) else []:
        if not is_nonhuman(card) or _can_speak(card) or not card.get("address"):
            continue
        card["narrative_address"] = card.pop("address")
        card.pop("address_target", None)
        card.pop("address_origin", None)
    return current


def speech_context(bible, chapter):
    """Return short natural-language facts for the writer prompt."""
    lines = []
    for card in (bible or {}).get("characters", []):
        if not is_nonhuman(card) or not card.get("name"):
            continue
        name = card["name"]
        start = _from_chapter(card)
        if not _can_speak(card):
            lines.append(f"{name}：不能说人话，只能用兽鸣、动作和反应表达。")
        elif chapter < start:
            lines.append(f"{name}：第{start}章前不能说人话，只能用兽鸣、动作和反应表达。")
        elif chapter == start:
            lines.append(f"{name}：本章起可以说人话，首次开口前后必须自然交代声音变化的原因。")
        else:
            lines.append(f"{name}：从第{start}章起可以说人话。")
    return [("非人角色表达限制", "\n".join(lines))] if lines else []


def _finding(kind, chapter, card, observed, expected, reason, text, number, start, end):
    return dict(
        kind=kind,
        chapter=chapter,
        name=card.get("name", ""),
        status="Inference",
        observed=observed,
        expected=expected,
        reason=reason,
        quote=text[start:end],
        start=start,
        end=end,
        id=number,
    )


def _span_for(spans, offset):
    return next(((number, start, end) for number, (start, end) in enumerate(spans, 1) if start <= offset < end), None)


def _attributed_card(text, match, cards, all_cards=()):
    before_start = max(0, match.start() - 180)
    before = text[before_start : match.start()]
    after = text[match.end() : match.end() + 80]
    direct = []
    for card in cards:
        name = card["name"]
        after_match = re.match(r"[ \t]*" + re.escape(name) + r"[^。！？!?\n]{0,24}" + _SPEECH_VERB, after)
        before_match = re.search(
            r"(?:^|[。！？!?\n])\s*"
            + re.escape(name)
            + r"(?:[^。！？!?\n]{0,24}"
            + _SPEECH_VERB
            + r"[：:,，]?|\s*[：:])\s*$",
            before,
        )
        after_segment = after[: after_match.end()] if after_match else ""
        before_segment = before[before_match.start() :] if before_match else ""
        other_after = any(
            other.get("name") in after_segment for other in all_cards if other is not card and other.get("name")
        )
        other_before = any(
            other.get("name") in before_segment for other in all_cards if other is not card and other.get("name")
        )
        if after_match and not other_after and not _ANIMAL_ACTION.search(after_segment):
            direct.append((card, match.end() + after_match.end()))
        elif before_match and not other_before and not _ANIMAL_ACTION.search(before_segment):
            direct.append((card, match.end()))
    if len(direct) == 1:
        return direct[0]
    pronoun = re.search(r"^\s*[，,。！？!?]?[它][^。！？!?\n]{0,12}" + _SPEECH_VERB, after)
    if not pronoun:
        return None
    mentioned = [card for card in cards if card["name"] in before]
    if len(mentioned) != 1:
        return None
    return mentioned[0], match.end() + pronoun.end()


def _address_attributed_card(text, match, cards, all_cards):
    content = re.sub(r"[\s。！？!?，,]+", "", match[1])
    before = text[max(0, match.start() - 100) : match.start()]
    if any(
        not is_nonhuman(card)
        and re.search(re.escape(card.get("name", "")) + r"[^。！？!?\n]{0,20}" + _SPEECH_VERB + r"[：:]?\s*$", before)
        for card in all_cards
        if card.get("name")
    ):
        return None
    matches = []
    for card in cards:
        labels = re.split(r"[/／、，,；;]", str(card.get("address") or card.get("narrative_address") or ""))
        if content not in {label.strip() for label in labels if label.strip()}:
            continue
        after = text[match.end() : match.end() + 100]
        attributed = re.match(
            r"\s*" + re.escape(card["name"]) + r"[^。！？!?\n]{0,36}(?:用|拱|顶|蹭|咬|扯|拉|推|低鸣|急叫|回头)",
            after,
        )
        if attributed:
            matches.append((card, match.end() + attributed.end()))
    return matches[0] if len(matches) == 1 else None


def speech_findings(text, bible, chapter=None):
    all_cards = [card for card in (bible or {}).get("characters", []) if isinstance(card, dict)]
    cards = [card for card in all_cards if is_nonhuman(card) and card.get("name")]
    spans = _sentences(text)
    paragraphs = _paragraph_spans(text)
    findings = []
    seen = set()
    for match in _QUOTE.finditer(text):
        content = re.sub(r"\s+", "", match[1])
        if len(content) < 2 or re.fullmatch(r"[呜嗷吼嗯啊唔哼呀哈]+", content):
            continue
        attributed = _attributed_card(text, match, cards, all_cards) or _address_attributed_card(
            text, match, cards, all_cards
        )
        if attributed is None:
            continue
        card, attribution_end = attributed
        first = _span_for(spans, match.start())
        last = _span_for(spans, max(match.start(), attribution_end - 1)) or first
        if first is None or last is None or (card["name"], first[0]) in seen:
            continue
        seen.add((card["name"], first[0]))
        start, end = next(
            ((start, end) for start, end in paragraphs if start <= match.start() < end),
            (first[1], last[2]),
        )
        allowed_from = _from_chapter(card)
        if not _can_speak(card) or chapter is None or chapter < allowed_from:
            findings.append(
                _finding(
                    "nonhuman_speech",
                    chapter,
                    card,
                    match[1],
                    "兽鸣、动作或反应",
                    "人物卡不允许该非人角色在本章说完整人话",
                    text,
                    first[0],
                    start,
                    end,
                )
            )
        elif chapter == allowed_from and not _ORIGIN.search(text[max(0, match.start() - 100) : match.end() + 100]):
            findings.append(
                _finding(
                    "nonhuman_speech_origin",
                    chapter,
                    card,
                    match[1],
                    f"第{allowed_from}章首次开口须交代原因",
                    "首次说人话前后没有交代声音变化来源",
                    text,
                    first[0],
                    start,
                    end,
                )
            )
    return findings


def identity_literal_findings(text, bible, chapter=None):
    spans = _sentences(text)
    findings = []
    for number, (start, end) in enumerate(spans, 1):
        sentence = text[start:end]
        for card in (bible or {}).get("characters", []):
            identity = card.get("identity", "") if isinstance(card, dict) else ""
            if not isinstance(identity, str) or len(identity.strip()) < 8 or identity not in sentence:
                continue
            findings.append(
                _finding(
                    "identity_literal",
                    chapter,
                    card,
                    identity,
                    "自然动作或具体来历",
                    "人物卡identity被逐字贴入正文",
                    text,
                    number,
                    start,
                    end,
                )
            )
    return findings
