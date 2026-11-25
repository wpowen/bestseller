"""Grounded Chinese gender-pronoun binding for named cast members."""

import re

from .precision import _sentences


GENDER = {"男": ("他", "她"), "女": ("她", "他")}
_FRONTED = re.compile(
    r"[\s“‘\"']*(?:[^，,。！？!?]{1,40}[，,]\s*)?"
    r"(?:(?:可|但|却|而|只是|然而|不过|随后|接着)\s*)?([他她])(?!们)"
)
_EARLY_POSSESSIVE = re.compile(r"[^。！？!?]{0,12}?(?:把|将)([他她])(?!们)(?:的)?[\u3400-\u9fff]")
_CLAUSE_PRONOUN = re.compile(r"[，,；;：:]\s*([他她])(?!们)(?:只|也|仍|又|便|才|已|正|会|能|可|却|把|将|被)")
_OBSERVER = re.compile(r"(?:看清|看出|发现|识破|明白|知道)[^。！？]{0,18}(?:手段|做法|计划|意图)[：:]$")
_RELATIVE_POSITION = re.compile(r"(?<!其)([他她])(?:的)?(?:身前|身后|身旁|身边|面前|背后|怀里|怀中)")


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
    match = _CLAUSE_PRONOUN.search(sentence)
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
    return re.search(
        r"[，,；;]\s*(?:(?:却|而|随后|于是)\s*)?([他她])(?!们)(?:被|把|将|仍|又|才|便)", tail
    ) or re.search(r"[，,；;][^。！？!?]{0,50}?(?:听从|服从|受制于|归还给)([他她])(?!们)的", tail)


def _inside_quote(text, position):
    before = text[:position]
    return before.rfind("“") > before.rfind("”") or before.rfind("‘") > before.rfind("’") or before.count('"') % 2 == 1


def _after_vocative(text, position):
    """The pronoun follows a short form of address that opens the quote (instruction 80, re-test ch21)."""
    opening = text.rfind("“", 0, position)
    return opening >= 0 and bool(re.fullmatch(r"“[^，,。！？“”]{1,6}[，,]\s*", text[opening:position]))


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


def _prior_explicit_other(text, span, cards, speaker):
    start, end = span
    previous = text[start:end]
    people = [card for card in _mentioned(previous, cards) if card.get("gender") in GENDER]
    resolved = _explicit_subject(previous, people)
    return resolved if resolved is not None and resolved is not speaker else None


def _clause_subject(sentence, people, stop=None):
    prefix = sentence[:stop] if type(stop) is int and stop >= 0 else sentence
    if type(stop) is int and re.search(r"若|如果|一旦|倘若", prefix):
        return None
    clauses = [part.strip(" \t\r\n“”‘’\"'") for part in re.split(r"[，,；;：:]", prefix)]
    for clause in reversed([part for part in clauses if part]):
        candidates = [card for card in people if card["name"] in clause]
        if len(candidates) != 1:
            continue
        card = candidates[0]
        before = clause[: clause.find(card["name"])]
        if re.search(r"(?:看向|望向|问|告诉|交给|递给|跟随|追赶|抓住|扶住|找|借)[^，,；;：:]*$", before):
            continue
        tail = clause[clause.find(card["name"]) + len(card["name"]) :]
        if not (
            _subject_bound(clause, card["name"])
            or re.match(r"(?:不必|不需要|(?:也)?仍能|只需|只要|若(?:只是|要|能|想|再))", tail)
        ):
            continue
        return card
    return None


def _prior_quoted_subject(text, span, cards):
    start, end = span
    previous = text[start:end]
    people = []
    for card in _mentioned(previous, cards):
        position = text.find(card["name"], start, end)
        if position >= 0 and _inside_quote(text, position):
            people.append(card)
    return _clause_subject(previous, people) if people else None


def _scan_bindings(text, cards, spans):
    rows = []
    bound = None
    neutral_gap = 0
    paragraph_names = set()
    for index, (start, end) in enumerate(spans):
        sentence = text[start:end]
        people = [card for card in _mentioned(sentence, cards) if card.get("gender") in GENDER]
        previous_paragraph = set()
        if index and re.search(r"\n\s*\n", text[spans[index - 1][1] : start]):
            previous_paragraph, paragraph_names = paragraph_names, set()
            if len(previous_paragraph) > 1:
                bound = None
        paragraph_names.update(card["name"] for card in people)
        explicit = _clause_subject(sentence, people) or _explicit_subject(sentence, people)
        match = _pronoun_match(sentence, bound)
        inline_match = _inline_pronoun_match(sentence, explicit) if explicit is not None else None
        if inline_match is not None:
            inline_offset = sentence.find(inline_match.string) + inline_match.start(1)
            if _inside_quote(text, start + inline_offset) or re.search(r"若|如果|一旦|倘若", sentence[:inline_offset]):
                inline_match = None
        if inline_match is not None:
            match = inline_match
        inline = inline_match is not None
        match_offset = sentence.find(match.string) if match and match.string is not sentence else 0
        pronoun_position = start + match_offset + match.start(1) if match else -1
        quoted = match is not None and _inside_quote(text, pronoun_position)
        relative_pronoun = pronoun_position - start if match else None
        observer = bool(match and _OBSERVER.search(sentence[:relative_pronoun]))
        local = None if quoted or observer else _clause_subject(sentence, people, relative_pronoun)
        resolved = explicit if inline else local
        unknown = False
        unknown_reason = ""
        if quoted:
            resolved = _quote_external_target(text, start, end, people, explicit)
            # “沈首席，他仍是杂役！”: the addressee is not the 他, so the earlier subject is no evidence.
            guess = bool(index) and not _after_vocative(text, pronoun_position)
            if resolved is None and guess:
                resolved = _prior_quoted_subject(text, spans[index - 1], cards)
            if resolved is None and guess:
                resolved = _prior_explicit_other(text, spans[index - 1], cards, explicit)
            unknown = resolved is None
            unknown_reason = "引语内第三人称没有引语外另一具名人物的明确语法证据"
        elif match and resolved is None and index:
            previous = text[slice(*spans[index - 1])]
            prior_people = [card for card in _mentioned(previous, cards) if card.get("gender") in GENDER]
            prior_explicit = _explicit_subject(previous, prior_people)
            if observer and bound is not None:
                resolved = bound
            elif not people and bound is not None and neutral_gap <= 1:
                resolved = bound
            elif prior_explicit is not None:
                resolved = prior_explicit
            elif len(prior_people) > 1:
                unknown = True
                unknown_reason = "前句出现多名人物，代词指代不清"
            if resolved is not None and (
                len(previous_paragraph) > 1
                or sum(_subject_bound(previous, card["name"]) for card in prior_people) > 1
                or (
                    prior_explicit is resolved
                    and any(
                        m[1] != GENDER[resolved["gender"]][0]
                        and not _inside_quote(text, spans[index - 1][0] + m.start(1))
                        for m in _RELATIVE_POSITION.finditer(previous)
                    )
                )
            ):
                # Across a cut, the last named subject may simply be watching the other actor.
                resolved = None
                unknown = True
                unknown_reason = "上文有多名人物，本句没有明确动作执行者"
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
        if match and resolved is not None:
            bound, neutral_gap = resolved, 0
        elif not match and explicit is not None:
            bound, neutral_gap = explicit, 0
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
    seen = {row.get("pronoun_start") for row in results}
    results.extend(row for row in _self_named(text, cards, chapter, spans, finding) if row["pronoun_start"] not in seen)
    return results


def _self_named(text, cards, chapter, spans, finding):
    """A speaker naming themself (“由我沈照雪拿”) binds the narration pronoun next to that line (eval-75 ch2)."""
    gendered = [card for card in cards if card.get("gender") in GENDER and card.get("name")]
    results = []
    for paragraph in re.finditer(r"[^\n]+", text):
        body, offset = paragraph.group(), paragraph.start()
        quotes = list(re.finditer(r"“[^”]*”", body))
        named = {card["name"]: card for card in gendered for q in quotes if "我" + card["name"] in q.group()}
        narration = re.sub(r"“[^”]*”", "", body)
        if len(named) != 1 or any(card["name"] in narration for card in gendered):
            continue
        card = next(iter(named.values()))
        expected, wrong = GENDER[card["gender"]]
        for match in re.finditer(r"(?:^|”)([他她])(?!们)|([他她])(?!们)[^“”]{0,12}[：:，,]?“", body):
            position = match.start(1) if match.group(1) else match.start(2)
            if body[position] != wrong or _inside_quote(body, position):
                continue
            start = offset + position
            index, (a, b) = next((i, span) for i, span in enumerate(spans) if span[0] <= start < span[1])
            row = finding(
                "gender_pronoun", chapter, card["name"], "Inference", wrong, expected,
                "说话人在台词中自报姓名，旁边的叙述代词与其性别不符", text, index + 1, a, b,
            )  # fmt: skip
            row.update(pronoun_start=start, pronoun_end=start + 1)
            results.append(row)
    return results
