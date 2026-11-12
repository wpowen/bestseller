"""Grounded Chinese gender-pronoun binding for named cast members."""

import re

from .precision import _sentences


GENDER = {"男": ("他", "她"), "女": ("她", "他")}
_FRONTED = re.compile(
    r"[\s“‘\"']*(?:[^，,。！？!?]{1,40}[，,]\s*)?"
    r"(?:(?:可|但|却|而|只是|然而|不过|随后|接着)\s*)?([他她])"
)
_EARLY_POSSESSIVE = re.compile(r"[^。！？!?]{0,12}?(?:把|将)([他她])(?:的)?[\u3400-\u9fff]")


def _subject_bound(sentence, name):
    stripped = sentence.strip(" \t\r\n“”‘’\"'")
    if re.search(r"(?:竟|原来|正|却)?是" + re.escape(name) + r"[。！？!?]?$", stripped):
        return True
    matches = list(re.finditer(re.escape(name), stripped))
    if not matches:
        return False
    match = next(
        (
            item
            for item in matches
            if item.start() == 0
            or stripped[: item.start()].endswith(("，", ",", "；", ";"))
            or re.search(r"(?:以为|认为|发现|看见)$", stripped[: item.start()])
        ),
        None,
    )
    if match is None:
        return False
    tail = stripped[match.end() :]
    if re.match(r"(?:和|与|跟|同|、)[\u4e00-\u9fff]{2,4}(?:一同|一起|都|也)?", tail) or re.search(
        r"(?:两人|二人|他们|她们)(?:一同|一起|都|也)?", tail
    ):
        return False
    return bool(
        re.search(r"被[^。！？]{0,30}(?:拽|拖|压|击|撞|抓|绑|困|推|拉|卷|刺|砸|扯)", tail)
        or re.match(r"(?:没有|没|未)(?:答|回答|接话|开口|出声|回应|再争)", tail)
        or re.match(r"(?:从|沿|穿过)[^。！？]{0,60}(?:探出|折返|返回|回到|离开|赶到|抵达|走|跑)", tail)
        or re.match(r"(?:握住|抓住|攥住)[^。！？]{0,40}(?:契约|契纹|纹路|筋骨)", tail)
        or re.match(r"收[^。！？]{0,10}时，(?:脑|记忆|意识|心神)", tail)
        or re.match(r"(?:仍|正|已经|正在)?(?:蹲|站|坐|跪|伏|躺|俯身|转身|停稳|把|将)", tail)
        or re.match(r"(?:亲自带|拔出|拖着|取出|点头|压低声音|守在|攥紧|催动)", tail)
        or re.match(r"(?:在[^。！？]{0,30})?(?:摸到|推开|翻开|拿起|拾起)", tail)
        or re.match(r"(?:正|正在|仍)?在?(?:借|操纵|控制|防着|追赶|搜捕)", tail)
        or re.match(r"脚下[^，,。！？]{0,40}(?:塌|裂|陷|断)", tail)
    )


def _mentioned(sentence, cards):
    return [card for card in cards if card["name"] in sentence]


def _explicit_subject(sentence, people):
    subjects = [card for card in people if _subject_bound(sentence, card["name"])]
    return subjects[0] if len(subjects) == 1 else None


def _pronoun_match(sentence, bound):
    stripped = sentence.lstrip()
    if stripped.startswith(("“", "‘", '"')):
        close = min((index for mark in ("”", "’", '"') if (index := stripped.find(mark, 1)) >= 0), default=-1)
        return _FRONTED.match(stripped[close + 1 :]) if close >= 0 else None
    match = _FRONTED.match(sentence)
    if match:
        return match
    if bound is not None:
        match = _EARLY_POSSESSIVE.match(sentence)
        if match:
            return match
    quote = min((index for mark in ("“", "‘", '"') if (index := sentence.find(mark)) >= 0), default=-1)
    if quote >= 0:
        return _FRONTED.match(sentence[quote + 1 :])
    return None


def _inline_pronoun_match(sentence, card):
    start = sentence.find(card["name"]) + len(card["name"])
    if start < len(card["name"]):
        return None
    tail = sentence[start:]
    return re.search(r"[，,；;]\s*(?:(?:却|而|随后|于是)\s*)?([他她])(?:被|把|将|仍|又|才|便)", tail) or re.search(
        r"[，,；;][^。！？!?]{0,50}?(?:听从|服从|受制于|归还给)([他她])的", tail
    )


def _inside_quote(text, position):
    before = text[:position]
    return before.rfind("“") > before.rfind("”") or before.rfind("‘") > before.rfind("’") or before.count('"') % 2 == 1


def _quote_external_target(text, start, end, people, speaker):
    outside = "".join(
        char if char not in "“”‘’\"'" and not _inside_quote(text, start + offset) else " "
        for offset, char in enumerate(text[start:end])
    )
    candidates = []
    for card in people:
        if card is speaker or card["name"] not in outside:
            continue
        name = re.escape(card["name"])
        if _subject_bound(outside, card["name"]) or re.search(
            rf"(?:指着|指向|看向|望向|提到|说起|问起|谈及)[^，,。！？]{{0,12}}{name}", outside
        ):
            candidates.append(card)
    return candidates[0] if len(candidates) == 1 else None


def _scan_bindings(text, cards, spans):
    rows = []
    bound = None
    neutral_gap = 0
    for index, (start, end) in enumerate(spans):
        sentence = text[start:end]
        people = [card for card in _mentioned(sentence, cards) if card.get("gender") in GENDER]
        explicit = _explicit_subject(sentence, people)
        match = _pronoun_match(sentence, bound)
        inline_match = _inline_pronoun_match(sentence, explicit) if explicit is not None else None
        if inline_match is not None:
            inline_offset = sentence.find(inline_match.string) + inline_match.start(1)
            if _inside_quote(text, start + inline_offset):
                inline_match = None
        if inline_match is not None:
            match = inline_match
        inline = inline_match is not None
        match_offset = sentence.find(match.string) if match and match.string is not sentence else 0
        pronoun_position = start + match_offset + match.start(1) if match else -1
        quoted = match is not None and _inside_quote(text, pronoun_position)
        resolved = explicit if inline else None
        unknown = False
        unknown_reason = ""
        if quoted:
            resolved = _quote_external_target(text, start, end, people, explicit)
            unknown = resolved is None
            unknown_reason = "引语内第三人称没有引语外另一具名人物的明确语法证据"
        elif match and resolved is None and index:
            previous = text[slice(*spans[index - 1])]
            prior_people = [card for card in _mentioned(previous, cards) if card.get("gender") in GENDER]
            prior_explicit = _explicit_subject(previous, prior_people)
            if prior_explicit is not None:
                resolved = prior_explicit
            elif not prior_people and bound is not None and neutral_gap <= 1:
                resolved = bound
            elif len(prior_people) > 1:
                unknown = True
                unknown_reason = "前句出现多名人物，代词指代不清"
        if match:
            rows.append(
                {
                    "index": index,
                    "start": start,
                    "end": end,
                    "resolved": resolved,
                    "unknown": unknown,
                    "unknown_reason": unknown_reason,
                    "observed": match[1],
                    "pronoun_start": pronoun_position,
                    "pronoun_end": start + match_offset + match.end(1),
                }
            )
        if explicit is not None:
            bound, neutral_gap = explicit, 0
        elif match and resolved is not None:
            bound, neutral_gap = resolved, 0
        elif not people and bound is not None and not match:
            neutral_gap += 1
            if neutral_gap > 1:
                bound = None
        else:
            bound, neutral_gap = None, 0
    return rows


def pronoun_bindings(text, cards, chapter, spans=None):
    """Expose resolved correct and incorrect uses without turning correct uses into repair findings."""
    spans = spans or _sentences(text)
    result = []
    for row in _scan_bindings(text, cards, spans):
        resolved = row["resolved"]
        if resolved is None:
            continue
        expected = GENDER[resolved["gender"]][0]
        result.append(
            {
                "chapter": chapter,
                "name": resolved["name"],
                "observed": row["observed"],
                "expected": expected,
                "correct": row["observed"] == expected,
                "status": "Evidence",
                "quote": text[row["start"] : row["end"]],
                "id": row["index"] + 1,
            }
        )
    return result


def pronoun_findings(text, cards, chapter, spans, finding):
    """Resolve only a named grammatical subject; ambiguous references remain Unknown."""
    results = []
    for row in _scan_bindings(text, cards, spans):
        index, start, end, resolved = row["index"], row["start"], row["end"], row["resolved"]
        if row["unknown"]:
            results.append(
                finding(
                    "gender_pronoun",
                    chapter,
                    "",
                    "Unknown",
                    row["observed"],
                    "",
                    row["unknown_reason"],
                    text,
                    index + 1,
                    start,
                    end,
                )
            )
        if resolved is not None:
            expected, wrong = GENDER[resolved["gender"]]
            if row["observed"] == wrong:
                result = finding(
                    "gender_pronoun",
                    chapter,
                    resolved["name"],
                    "Evidence",
                    wrong,
                    expected,
                    "明确具名主语与紧接代词的性别不符",
                    text,
                    index + 1,
                    start,
                    end,
                )
                result.update(pronoun_start=row["pronoun_start"], pronoun_end=row["pronoun_end"])
                results.append(result)
    return results
