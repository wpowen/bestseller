"""Grounded Chinese gender-pronoun binding for named cast members."""

import re


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
    if not stripped.startswith(name):
        return False
    tail = stripped[len(name) :]
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
        or re.match(r"(?:正)?(?:蹲|站|坐|跪|伏|躺|俯身|转身|停稳|把|将)", tail)
        or re.match(r"(?:亲自带|拔出|拖着|取出|点头|压低声音|守在|攥紧|催动)", tail)
        or re.match(r"(?:在[^。！？]{0,30})?(?:摸到|推开|翻开|拿起|拾起)", tail)
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


def pronoun_findings(text, cards, chapter, spans, finding):
    """Resolve only a named grammatical subject; ambiguous references remain Unknown."""
    results = []
    bound = None
    neutral_gap = 0
    for index, (start, end) in enumerate(spans):
        sentence = text[start:end]
        match = _pronoun_match(sentence, bound)
        match_offset = 0
        if match and match.string is not sentence:
            match_offset = sentence.find(match.string)
        resolved = None
        if match and index:
            previous = text[slice(*spans[index - 1])]
            people = [card for card in _mentioned(previous, cards) if card.get("gender") in GENDER]
            explicit = _explicit_subject(previous, people)
            if explicit is not None:
                resolved = explicit
            elif not people and bound is not None and neutral_gap <= 1:
                resolved = bound
            elif len(people) > 1:
                results.append(
                    finding(
                        "gender_pronoun",
                        chapter,
                        "",
                        "Unknown",
                        match[1],
                        "",
                        "前句出现多名人物，代词指代不清",
                        text,
                        index + 1,
                        start,
                        end,
                    )
                )
        if resolved is not None:
            expected, wrong = GENDER[resolved["gender"]]
            if match[1] == wrong:
                row = finding(
                    "gender_pronoun",
                    chapter,
                    resolved["name"],
                    "Evidence",
                    match[1],
                    expected,
                    "明确具名主语与紧接代词的性别不符",
                    text,
                    index + 1,
                    start,
                    end,
                )
                row.update(
                    pronoun_start=start + match_offset + match.start(1), pronoun_end=start + match_offset + match.end(1)
                )
                results.append(row)
        people = [card for card in _mentioned(sentence, cards) if card.get("gender") in GENDER]
        explicit = _explicit_subject(sentence, people)
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
    return results
