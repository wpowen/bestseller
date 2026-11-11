"""Read-only R4 candidates; lexical overlap is Inference, never a deletion rule.

Offsets refer to the original input, paragraph numbers are one-based. Compare
only wholly narrative paragraphs (mixed speech is conservatively excluded).
The two overlap gates were calibrated against eval-33/eval-35, including the
unequal-length pair in eval-33 chapter 36. A single identity introduction is
report-only here; the chapter repair routes first-chapter opening hits too.
"""

import re
import json
from difflib import SequenceMatcher
from pathlib import Path

from .prose_repetition import _normalize, _position, _sentences
from .text import _paragraph_spans


RULES = dict(
    ngram=3, jaccard_min=0.32, containment_min=0.7, paragraph_distance=3, min_characters=10, card_repair_above=1
)
_QUOTES = set("“”‘’「」『』\"'«»＂＇")
_AS_ROLE = re.compile(r"作为[^，,。！？!?；;：:\n]{2,30}[，,]\s*(?:他|她|其)")
_ROLE = r"[^，,。！？!?；;：:\n]{0,20}(?:师|者|人|将|使|官|卫|主|长|管事|掌柜)"
_IDENTITY = re.compile(r"^[\u3400-\u9fff]{2,4}是[^，,。！？!?；;：:\n]{1,30}的" + _ROLE + r"(?:与|和|兼)" + _ROLE)
_IS_ALSO = re.compile(r"[他她]是[^，,。！？!?；;：:\n]+[，,]\s*也是[^，,。！？!?；;：:\n]+")
_RETURN_REASON = re.compile(r"折返|返回|重回|重返|再到|再次来到|原路|回去|因.{0,24}(?:救|追|取|查|找)")
_PLACE_SUFFIX = r"(?:桥|城|镇|谷|洞|窟|宫|殿|门|道|巷|寨|关|塔|井|台|坡|峰|河|湖|岛|港|村|院|馆|楼|仓|营|阵口|风沟)"
_ARRIVALS = (
    re.compile(r"(?P<place>[\u3400-\u9fff]{1,6}" + _PLACE_SUFFIX + r")(?=到了|已到|就在眼前)"),
    re.compile(r"(?:抵达|来到|进入)(?P<place>[\u3400-\u9fff]{1,6}" + _PLACE_SUFFIX + r")"),
)
_LOCATION_MENTIONS = (
    re.compile(
        r"(?:抵达|进入|走到|来到|回到|重返|离开|横着一座|横着|跨过)([\u3400-\u9fff]{1,6}" + _PLACE_SUFFIX + r")"
    ),
    re.compile(r"([\u3400-\u9fff]{1,6}" + _PLACE_SUFFIX + r")(?=到了|已到|就在眼前|从)"),
)


def _identity_windows(bible):
    """Index literal eight-character windows; never join punctuation or spaces."""
    result = []
    for character in (bible or {}).get("characters", []):
        identity = character.get("identity", "")
        if not isinstance(identity, str):
            continue
        windows = [identity[i : i + 8] for i in range(len(identity) - 7) if identity[i : i + 8].isalnum()]
        if windows:
            result.append((character.get("name", ""), identity, windows))
    return result


def _identity_matches(node, identities):
    matches = []
    for name, identity, windows in identities:
        quote = next((part for part in windows if part in node["text"]), None)
        if quote:
            start = node["start"] + node["text"].index(quote)
            matches.append(dict(character=name, identity=identity, quote=quote, start=start, end=start + len(quote)))
    return matches


def analyze_prose(text, bible=None):
    """Locate nearby paraphrases and card templates without changing any bytes.

    Exact normalized paragraphs are left to prose_repetition. Candidate records
    carry measurable scores and source spans, not a claim of semantic equality.
    Optional bible supplies character identities; a literal eight-character
    overlap is recorded once per sentence, even when several rules match.
    """
    paragraphs = _paragraph_spans(text)
    sentences = _sentences(text, paragraphs)
    identities = _identity_windows(bible)
    narrative = {}
    cards = []
    for number, (start, end) in enumerate(paragraphs, 1):
        passage = text[start:end]
        nodes = [n for n in sentences if start <= n["start"] < end]
        if not any(c in _QUOTES for c in passage) and not any(n["quoted"] for n in nodes):
            normalized = _normalize(passage)
            if len(normalized) >= RULES["min_characters"]:
                size = RULES["ngram"]
                grams = {normalized[i : i + size] for i in range(len(normalized) - size + 1)}
                narrative[number] = (normalized, grams)
        for node in nodes:
            if node["quoted"] or any(c in _QUOTES for c in node["text"]):
                continue
            matches = _identity_matches(node, identities)
            # Role conjunctions avoid object definitions; literal identity reuse
            # is independent evidence and may also occur inside an action.
            template = (
                "as_role"
                if _AS_ROLE.search(node["text"])
                else "identity_roles"
                if _IDENTITY.search(node["text"])
                else "is_also"
                if _IS_ALSO.search(node["text"])
                else "identity_literal"
                if matches
                else None
            )
            if template:
                card = dict(node, kind="card_explanation", template=template, status="Inference")
                if matches:
                    card["identity_matches"] = matches
                cards.append(card)
    pairs, pending = [], []
    for second, (normalized, right) in narrative.items():
        for first in range(max(1, second - RULES["paragraph_distance"]), second):
            if first not in narrative:
                continue
            old, left = narrative[first]
            if old == normalized:
                continue
            intersection = len(left & right)
            jaccard = intersection / len(left | right)
            containment = intersection / min(len(left), len(right))
            if jaccard < RULES["jaccard_min"] or containment < RULES["containment_min"]:
                continue
            occurrences = [_position(text, *paragraphs[n - 1], n) for n in (first, second)]
            occurrences[-1].update(action="repair", reasons=["near_paragraph"], status="Inference")
            pairs.append(
                dict(
                    kind="near_paragraph",
                    jaccard=jaccard,
                    containment=containment,
                    occurrences=occurrences,
                    status="Inference",
                )
            )
            pending.append(occurrences[-1])
    if len(cards) > RULES["card_repair_above"]:
        pending.extend(dict(c, action="repair", reasons=["card_explanation"]) for c in cards)
    return dict(
        counts=dict(near_paragraph_pairs=len(pairs), card_explanations=len(cards)),
        records=[*pairs, *cards],
        needs_repair=sorted(pending, key=lambda n: n["start"]),
        rules=dict(RULES),
        status="Inference",
    )


def _opening_sentences(text, paragraph_limit=2):
    paragraphs = _paragraph_spans(text)[:paragraph_limit]
    end = paragraphs[-1][1] if paragraphs else 0
    return [node for node in _sentences(text, paragraphs) if node["start"] < end]


def adjacent_opening_hits(previous, current):
    """Compare the current opening with any completed action in the previous chapter."""
    if _normalize(previous) == _normalize(current):
        return []  # Whole-body duplicates belong to the existing repetition repair.
    old_nodes = _sentences(previous, _paragraph_spans(previous))
    result = []
    for current_node in _opening_sentences(current):
        right = _normalize(current_node["text"])
        if len(right) < 10:
            continue
        candidates = []
        for old in old_nodes:
            left = _normalize(old["text"])
            if len(left) < 10 or previous.count(old["text"]) > 1 or current.count(current_node["text"]) > 1:
                continue
            match = SequenceMatcher(None, left, right, autojunk=False).find_longest_match()
            containment = match.size / min(len(left), len(right))
            if containment >= 0.72:
                candidates.append((containment, old))
        if candidates:
            containment, old = max(candidates, key=lambda row: row[0])
            same_time = re.match(r"^([^，,。]{2,8}(?:后|前|时))[，,]", old["text"])
            kind = (
                "repeated_time_opening"
                if same_time and current_node["text"].startswith(same_time[1])
                else "repeated_action"
            )
            result.append(
                dict(
                    kind=kind,
                    previous_text=old["text"],
                    current_text=current_node["text"],
                    current_start=current_node["start"],
                    current_end=current_node["end"],
                    current_paragraph=current_node["paragraph"],
                    containment=round(containment, 4),
                    status="Inference",
                )
            )
            break
    return result


def _arrival_places(text):
    opening = text[: (_paragraph_spans(text)[:3][-1][1] if _paragraph_spans(text)[:3] else 0)]
    return [
        (match["place"], match.start("place"), opening) for pattern in _ARRIVALS for match in pattern.finditer(opening)
    ]


def repeated_arrival_hits(history, chapter, current):
    """Flag an arrival presented as new when the place was visited and no return reason is given."""
    result = []
    for place, start, opening in _arrival_places(current):
        if _RETURN_REASON.search(opening[: start + len(place) + 40]):
            continue
        visited = re.compile(
            r"(?:抵达|来到|进入|走上|穿过|跨过|跑到)[^。！？\n]{0,24}"
            + re.escape(place)
            + r"|"
            + re.escape(place)
            + r"(?:上|中|里|内|入口|尽头|脚下|对面|桥面|门前|门后|从)"
        )
        previous = next(((number, text) for number, text in reversed(history) if visited.search(text)), None)
        if previous:
            result.append(
                dict(
                    kind="repeated_arrival",
                    chapter=chapter,
                    place=place,
                    previous_chapter=previous[0],
                    quote=opening[max(0, start - 40) : start + len(place) + 20],
                    status="Inference",
                )
            )
    return result


def _history(out, chapter):
    root = Path(out)
    rows = []
    for path in sorted(root.glob("chapter-*.body.md")):
        match = re.fullmatch(r"chapter-(\d+)\.body\.md", path.name)
        if match and int(match[1]) < chapter:
            rows.append((int(match[1]), path.read_text("utf-8")))
    return rows


def _places(text):
    return sorted({match[1] for pattern in _LOCATION_MENTIONS for match in pattern.finditer(text)})


def with_completed_action_context(sections, out, chapter):
    """Add completed-action guidance inside the existing previous-ending block."""
    history = _history(out, chapter)
    if not history:
        return list(sections)
    previous = history[-1][1]
    spans = _paragraph_spans(previous)
    tail = previous[spans[-3][0] :] if len(spans) >= 3 else previous
    places = sorted({place for _, text in history for place in _places(text)})
    value = "上一章末场已发生，开头不得重写同一动作或时间起句：\n" + tail[-1200:]
    if places:
        value += "\n此前到过的地点（再次到达必须写明折返理由）：" + "、".join(places[-20:])
    result = list(sections)
    for index, (title, current) in enumerate(result):
        if title == "上章结尾":
            result[index] = (title, current + "\n" + value)
            break
    return result


def repair_cross_chapter_opening(client, prefix, text, raw, failed, state, bible, outline, out, extract, ask):
    history = _history(out, outline.get("number", 0))
    previous = history[-1][1] if history else ""
    adjacent = adjacent_opening_hits(previous, text) if previous else []
    arrivals = repeated_arrival_hits(history, outline.get("number"), text)
    issues = [*adjacent, *arrivals]
    note = {"status": "checked", "before": len(issues), "after": len(issues), "issues": issues}
    if not issues:
        return text, raw, failed, note
    target_paragraph = max(
        [row.get("current_paragraph", 1) for row in adjacent]
        + [
            next((i for i, (a, b) in enumerate(_paragraph_spans(text), 1) if row["place"] in text[a:b]), 1)
            for row in arrivals
        ]
    )
    end = _paragraph_spans(text)[target_paragraph - 1][1]
    value = ask(
        client,
        prefix + "-cross-chapter-opening",
        "只改本章开头命中段：不要重复上一章已完成的动作或相同时间起句；再次到旧地点必须写明返回原因。"
        "保留本章既定人物、目标、事实和开头之后的原文，不增事件。返回JSON。",
        [
            ("问题", json.dumps(issues, ensure_ascii=False)),
            ("历史正文", "\n\n".join(t for _, t in history)[-3000:]),
            ("本章", text),
        ],
        '{"text":"改写后的开头段"}',
        cap=1400,
        input_limit=7000,
        max_attempts=1,
    )
    replacement = value.get("text") if isinstance(value, dict) else None
    if not isinstance(replacement, str) or not replacement.strip() or len(_paragraph_spans(replacement)) > 3:
        return text, raw, failed, dict(note, status="rolled_back", reason="invalid_opening_patch")
    candidate = replacement.strip() + text[end:]
    remaining = [
        *(adjacent_opening_hits(previous, candidate) if previous else []),
        *repeated_arrival_hits(history, outline.get("number"), candidate),
    ]
    if remaining:
        return text, raw, failed, dict(note, status="rolled_back", reason="opening_recheck_failed", remaining=remaining)
    checked, invalid, _ = extract(
        client, prefix + "-cross-chapter-recheck", candidate, state, bible, outline, [], final=True
    )
    from .checks import high_confidence

    if invalid or high_confidence(state, checked.get("events", []), candidate):
        return text, raw, failed, dict(note, status="rolled_back", reason="fact_recheck_failed", after=0)
    return candidate, checked, False, dict(note, status="repaired", after=0, target_paragraphs=target_paragraph)


def cross_chapter_checkup(chapters):
    adjacent, arrivals, history = [], [], []
    previous = None
    for chapter in chapters:
        if previous and chapter["number"] == previous["number"] + 1:
            hits = adjacent_opening_hits(previous["text"], chapter["text"])
            if hits:
                adjacent.append({"chapters": [previous["number"], chapter["number"]], "hits": hits})
        arrivals.extend(repeated_arrival_hits(history, chapter["number"], chapter["text"]))
        history.append((chapter["number"], chapter["text"]))
        previous = chapter
    return (
        {"count": len(adjacent), "issues": adjacent, "status": "Inference"},
        {"count": len(arrivals), "issues": arrivals, "status": "Inference"},
    )
