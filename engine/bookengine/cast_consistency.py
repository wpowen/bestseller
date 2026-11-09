"""Conservative address, gender and cast-identity consistency checks."""

from collections import Counter
import json
import re

from .address_contracts import address_issues, address_rows
from .precision import _sentences, apply as sentence_edits


_GENDER = {"男": ("他", "她"), "女": ("她", "他")}
_GENDERED = {
    "师兄": ("男", "师姐"),
    "师弟": ("男", "师妹"),
    "哥哥": ("男", "姐姐"),
    "大哥": ("男", "大姐"),
    "哥": ("男", "姐"),
    "公子": ("男", "姑娘"),
    "先生": ("男", "小姐"),
    "师姐": ("女", "师兄"),
    "师妹": ("女", "师弟"),
    "姐姐": ("女", "哥哥"),
    "大姐": ("女", "大哥"),
    "姐": ("女", "哥"),
    "姑娘": ("女", "公子"),
    "小姐": ("女", "先生"),
}
_LEADER = re.compile(r"最前方|领头|首领|头目|为首|带队|率众|指挥|下令")
_HOSTILE = re.compile(
    r"敌方|敌军|追兵|追捕|活捉|药引|截杀|围杀|追回|封死(?:出口|矿道|退路)|"
    r"(?:出口|矿道|退路)[^。！？]{0,8}封死|杀(?:掉|死)"
)
_FRONTED_PRONOUN = re.compile(
    r"[\s“‘\"']*(?:[^，,。！？!?]{1,40}[，,]\s*)?"
    r"(?:(?:可|但|却|而|只是|然而|不过|随后|接着)\s*)?([他她])"
)


def _cards(bible):
    return [
        card
        for card in bible.get("characters", [])
        if isinstance(card, dict) and isinstance(card.get("name"), str) and card["name"]
    ]


def _finding(kind, chapter, name, status, observed, expected, reason, text, number, start, end):
    return dict(
        kind=kind,
        chapter=chapter,
        name=name,
        status=status,
        observed=observed,
        expected=expected,
        reason=reason,
        quote=text[start:end],
        start=start,
        end=end,
        id=number,
    )


def _mentioned(sentence, cards):
    return [card for card in cards if card["name"] in sentence]


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
    predicate = stripped[len(name) :]
    return bool(
        re.search(r"被[^。！？]{0,30}(?:拽|拖|压|击|撞|抓|绑|困|推|拉|卷|刺|砸|扯)", predicate)
        or re.match(r"(?:没有|没|未)(?:答|回答|接话|开口|出声|回应|再争)", predicate)
        or re.match(r"(?:从|沿|穿过)[^。！？]{0,60}(?:折返|返回|回到|离开|赶到|抵达|走|跑)", predicate)
        or re.match(r"(?:握住|抓住|攥住)[^。！？]{0,40}(?:契约|契纹|纹路|筋骨)", predicate)
        or re.match(r"收[^。！？]{0,10}时，(?:脑|记忆|意识|心神)", predicate)
        or re.match(r"(?:正)?(?:蹲|站|坐|跪|伏|躺|俯身|转身|停稳|把)", predicate)
    )


def _explicit_subject(sentence, people):
    subjects = [card for card in people if _subject_bound(sentence, card["name"])]
    return subjects[0] if len(subjects) == 1 else None


def _dialogue_only(sentence):
    return bool(re.fullmatch(r"\s*[“‘\"][\s\S]*[”’\"]\s*", sentence))


def _pronoun_findings(text, cards, chapter, spans):
    findings = []
    bound = None
    for index, (start, end) in enumerate(spans):
        sentence = text[start:end]
        match = _FRONTED_PRONOUN.match(sentence)
        resolved = None
        if match and index:
            previous = text[slice(*spans[index - 1])]
            people = [card for card in _mentioned(previous, cards) if card.get("gender") in _GENDER]
            explicit = _explicit_subject(previous, people)
            if explicit is not None:
                resolved = explicit
            elif len(people) > 1:
                findings.append(
                    _finding(
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
            elif len(people) == 1 and _subject_bound(previous, people[0]["name"]):
                resolved = people[0]
            elif not people and bound is not None:
                resolved = bound
        if resolved is not None:
            card = resolved
            expected, wrong = _GENDER[card["gender"]]
            if match[1] == wrong:
                findings.append(
                    _finding(
                        "gender_pronoun",
                        chapter,
                        card["name"],
                        "Inference",
                        match[1],
                        expected,
                        "前句只有一名已登记人物，后句性别代词与人物卡不符",
                        text,
                        index + 1,
                        start,
                        end,
                    )
                )
        people = [card for card in _mentioned(sentence, cards) if card.get("gender") in _GENDER]
        explicit = _explicit_subject(sentence, people)
        if explicit is not None:
            bound = explicit
        elif match and resolved is not None:
            bound = resolved
        elif bound is not None and _dialogue_only(sentence):
            pass
        else:
            bound = None
    return findings


def _name_prefixes(name):
    return sorted({name, name[0], name[1:], name[-1]}, key=len, reverse=True)


def _gendered_address_findings(text, cards, chapter, spans):
    candidates = {}
    for card in cards:
        if card.get("gender") not in _GENDER:
            continue
        for prefix in _name_prefixes(card["name"]):
            for suffix, (observed_gender, counterpart) in _GENDERED.items():
                for match in re.finditer(re.escape(prefix + suffix), text):
                    candidates.setdefault(match.span(), []).append((card, prefix, suffix, observed_gender, counterpart))
    findings = []
    occupied = []
    for (start, end), matches in sorted(candidates.items(), key=lambda item: (item[0][0], item[0][0] - item[0][1])):
        if any(start < used_end and end > used_start for used_start, used_end in occupied):
            continue
        names = {row[0]["name"] for row in matches}
        if len(names) != 1:
            continue
        card, prefix, suffix, observed_gender, counterpart = max(matches, key=lambda row: len(row[1]))
        if suffix in {"哥", "哥哥", "姐", "姐姐"} and text[end : end + 1] == "的":
            occupied.append((start, end))
            continue
        if card["gender"] == observed_gender:
            continue
        occupied.append((start, end))
        number, sentence_start, sentence_end = next(
            (index, a, b) for index, (a, b) in enumerate(spans, 1) if a <= start < b
        )
        findings.append(
            _finding(
                "gendered_address",
                chapter,
                card["name"],
                "Inference",
                text[start:end],
                prefix + counterpart,
                "性别化称呼与人物卡性别不符",
                text,
                number,
                sentence_start,
                sentence_end,
            )
        )
    return findings


def _identity_findings(text, bible, cards, chapter, spans):
    antagonists = {
        row.get("name")
        for row in bible.get("antagonists", [])
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }
    findings = []
    for index, (start, end) in enumerate(spans):
        sentence = text[start:end]
        for card in _mentioned(sentence, cards):
            if card["name"] in antagonists or card.get("role") in {"antagonist", "opponent", "villain", "反派", "对手"}:
                continue
            previous = text[slice(*spans[index - 1])] if index else ""
            named_leader = (
                re.search(
                    r"(?:最前方的人|为首之人|领头人|首领|头目)[^。！？]{0,30}(?:竟|原来|正|却)?是"
                    + re.escape(card["name"]),
                    sentence,
                )
                or re.search(
                    r"(?:领头人|首领|头目)[，,：:\s]*" + re.escape(card["name"]),
                    sentence,
                )
                or re.search(
                    re.escape(card["name"]) + r"[^。！？]{0,20}(?:为首|领头|首领|头目|带队|率众|指挥|下令)",
                    sentence,
                )
            )
            linked = named_leader or (
                _LEADER.search(previous)
                and re.fullmatch(
                    r"[\s“”‘’\"']*(?:竟|原来|正|却)?是" + re.escape(card["name"]) + r"[。！？!?]?[\s“”‘’\"']*",
                    sentence,
                )
            )
            if not linked:
                continue
            window = text[spans[max(0, index - 1)][0] : spans[min(len(spans) - 1, index + 2)][1]]
            if not (_LEADER.search(window) and _HOSTILE.search(window)):
                continue
            target_index = next(
                (
                    offset
                    for offset in range(index, min(len(spans), index + 3))
                    if _LEADER.search(text[spans[offset][0] : spans[offset][1]])
                    or _HOSTILE.search(text[spans[offset][0] : spans[offset][1]])
                ),
                index,
            )
            a, b = spans[target_index]
            findings.append(
                _finding(
                    "identity_drift",
                    chapter,
                    card["name"],
                    "Inference",
                    "敌方领头人",
                    card.get("identity", card.get("role", "已登记身份")),
                    "非敌方人物被写成追捕方领头人；需结合上下文定向核对",
                    text,
                    target_index + 1,
                    a,
                    b,
                )
            )
    unique = {}
    for row in findings:
        unique[(row["kind"], row["name"], row["id"])] = row
    return list(unique.values())


def cast_findings(text, bible, *, chapter=None):
    """Return grounded Inference findings and explicit Unknown coreference cases."""
    cards = _cards(bible)
    spans = _sentences(text)
    findings = [
        *_gendered_address_findings(text, cards, chapter, spans),
        *_pronoun_findings(text, cards, chapter, spans),
        *_identity_findings(text, bible, cards, chapter, spans),
    ]
    return sorted(findings, key=lambda row: (row["start"], row["kind"], row["name"]))


def cast_checkup(bible, chapters):
    findings = [row for chapter in chapters for row in cast_findings(chapter["text"], bible, chapter=chapter["number"])]
    contract_issues = address_issues(bible)
    logical_contract_issues = {
        (
            row.get("reason", row.get("kind")),
            row.get("originator", row.get("speaker")),
            row.get("expected_target", row.get("name")),
            row.get("expected_address", row.get("address")),
        )
        for row in contract_issues
    }
    counts = Counter(row["kind"] for row in findings if row["status"] == "Inference")
    counts["address_contract"] = len(logical_contract_issues)
    return dict(
        authority="agent_editorial_not_human",
        counts=dict(counts),
        unknown_counts=dict(Counter(row["kind"] for row in findings if row["status"] == "Unknown")),
        findings=findings,
        address_contract=dict(
            semantic_rows=list(address_rows(bible)), issue_count=len(logical_contract_issues), issues=contract_issues
        ),
    )


def _repair_targets(text, findings):
    """Include the local mention chain when a named ally was cast as an enemy leader."""
    targets = {(row["id"], row["kind"], row["name"]): row for row in findings}
    spans = _sentences(text)
    for finding in [row for row in findings if row["kind"] == "identity_drift"]:
        center = finding["id"] - 1
        nearby = range(max(0, center - 12), min(len(spans), center + 11))
        named = [index for index in nearby if finding["name"] in text[slice(*spans[index])]]
        if not named:
            continue
        for index in range(min(named), max(named) + 1):
            start, end = spans[index]
            sentence = text[start:end]
            if finding["name"] not in sentence and not re.match(r"[\s“‘\"']*[他她]", sentence):
                continue
            row = _finding(
                "identity_chain",
                finding["chapter"],
                finding["name"],
                "Inference",
                "串入追兵身份链",
                finding["expected"],
                "同一串角链中的姓名或代词须与章纲一起修正",
                text,
                index + 1,
                start,
                end,
            )
            targets.setdefault((row["id"], row["kind"], row["name"]), row)
    return sorted(targets.values(), key=lambda row: (row["id"], row["kind"]))


def repair_cast_consistency(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    """One sentence-only model repair; any failed deterministic recheck rolls back."""
    from .checks import high_confidence
    from .prose_repetition import clean_repetitions
    from tools.book_checkup import clean_prose

    chapter = outline.get("number")
    observed = cast_findings(text, bible, chapter=chapter)
    targets = [row for row in observed if row["status"] == "Inference"]
    note = dict(status="checked", before=observed, after=observed, edits=[])
    if not targets:
        return text, raw, failed, note
    repair_targets = _repair_targets(text, targets)
    response = ask(
        client,
        prefix + "-cast-consistency-repair",
        "只修改待修句S编号，修正人物性别代词、性别化称呼或被写串的阵营身份。"
        "不得改正确人物的姓名、已发生事件、人物关系、胜负、物件、地点或章末结果。"
        "若人物被串成敌方领头人，依章纲把错误人物从整段追兵身份链移除，恢复为章纲中的匿名对手；"
        "不得只添加人物身份同位语。每个edit.id必须逐字复制待修句中的真实id，不得使用示例id；"
        "不确定不改，其他句逐字保留。",
        [
            ("固定人物卡", json.dumps(_cards(bible), ensure_ascii=False)),
            ("本章固定章纲", json.dumps(outline, ensure_ascii=False)),
            ("待修句", json.dumps(repair_targets, ensure_ascii=False)),
            ("只读正文", text),
        ],
        '{"edits":[{"id":1,"text":"替换后的完整句"}]}',
        cap=2500,
        input_limit=10000,
    )
    proposed = response.get("edits", []) if isinstance(response, dict) else []
    proposed = [row for row in proposed if isinstance(row, dict)]
    for target in repair_targets:
        if target["kind"] != "gender_pronoun":
            continue
        match = _FRONTED_PRONOUN.match(target["quote"])
        if not match or match[1] != target["observed"]:
            continue
        replacement = target["quote"][: match.start(1)] + target["expected"] + target["quote"][match.end(1) :]
        proposed = [row for row in proposed if row.get("id") != target["id"]]
        proposed.append(dict(id=target["id"], text=replacement))
    candidate, edits = sentence_edits(
        text,
        proposed,
        repair_targets,
        allow_empty=False,
        allow_style=True,
    )
    after = cast_findings(candidate, bible, chapter=chapter)
    remaining = {(row["kind"], row["name"]) for row in after if row["status"] == "Inference"}
    required = {(row["kind"], row["name"]) for row in targets}
    before_repetition = clean_repetitions(text, remove_safe=False)["counts"]
    after_repetition = clean_repetitions(candidate, remove_safe=False)["counts"]
    regression = any(
        after_repetition[key] > before_repetition[key] for key in ("sentence_repetitions", "adjacent_paragraph_pairs")
    )
    unbalanced = any(
        abs(candidate.count(left) - candidate.count(right)) > abs(text.count(left) - text.count(right))
        for left, right in (("“", "”"), ("「", "」"), ("『", "』"))
    )
    note.update(after=after, edits=edits)
    if (
        not edits
        or candidate == text
        or required & remaining
        or regression
        or unbalanced
        or clean_prose(candidate)[0] != candidate
    ):
        return text, raw, failed, dict(note, status="rolled_back", reason="deterministic_recheck")
    checked, invalid, _ = extract(
        client, prefix + "-cast-consistency-recheck", candidate, state, bible, outline, [], final=True
    )
    if invalid or high_confidence(state, checked.get("events", []), candidate):
        return text, raw, failed, dict(note, status="rolled_back", reason="state_recheck")
    return candidate, checked, False, dict(note, status="repaired")
