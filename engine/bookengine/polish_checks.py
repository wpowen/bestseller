import re
from decimal import Decimal
from difflib import SequenceMatcher
from .craft import surface_hits
from .precision import _sentences
from .text import _paragraph_spans
from .q1_quality import _dict as _mapping


def _number(value):
    value = str(value) if type(value) in (str, int, float) else ""
    value = value.translate(str.maketrans("零〇一二两三四五六七八九", "001223456789"))
    value = re.sub(
        r"^([1-9])百(?:([1-9])十([1-9])?|0([1-9]))?$",
        lambda m: str(int(m[1]) * 100 + int(m[2] or 0) * 10 + int(m[3] or m[4] or 0)),
        value,
    )
    value = re.sub(r"^([1-9]?)十([0-9]?)$", lambda m: str(int(m[1] or 1) * 10 + int(m[2] or 0)), value)
    return Decimal(value) if re.fullmatch(r"-?\d+(?:\.\d+)?", value) else None


def scan(text, cards=(), state=None, history=()):
    """Pure candidates, global sentence IDs, Arabic/Chinese tens/hundreds; history reserved, ownership never inferred."""
    if not isinstance(text, str) or not text.strip():
        return []
    cards = [
        c
        for c in (cards if isinstance(cards, (list, tuple)) else ())
        if isinstance(c, dict) and isinstance(c.get("name"), str)
    ]
    state = _mapping(state)
    protagonist = state.get("protagonist", "")
    names = [_mapping(protagonist).get("name") if isinstance(protagonist, dict) else protagonist] + [
        c["name"] for c in cards if c.get("role") in ("protagonist", "主角")
    ]
    lead = "|".join(re.escape(n) for n in ["主角", "我"] + names if isinstance(n, str) and n)
    spans, paragraphs = _sentences(text), _paragraph_spans(text)
    stack, bad, quoted = [], set(), []
    for m in re.finditer(r'(?<!\\)[“”「」"]', text):
        pos, char = m.start(), m[0]
        if char in "“「" or (char == '"' and (not stack or stack[-1][0] != '"')):
            if stack and stack[-1][0] == char:
                if not text[text.rfind("\n", 0, pos) + 1 : pos].strip():
                    continue
                bad.add(stack.pop()[1])
            stack.append((char, pos))
        elif stack and stack[-1][0] == {"”": "“", "」": "「", '"': '"'}[char]:
            quoted.append((stack.pop()[1], pos + 1))
        else:
            bad.add(pos)
    bad.update(pos + 1 if text[pos] == '"' and pos + 1 < len(text) else pos for _, pos in stack)
    quoted.extend((pos, len(text)) for _, pos in stack)
    found = {h["id"]: list(dict.fromkeys(h["categories"])) for h in surface_hits(text, cards)}
    continuity = _mapping(state.get("continuity"))
    blocked = continuity.get("blocked", ())
    disabled = [n for n in (blocked if isinstance(blocked, (list, tuple)) else ()) if isinstance(n, str) and n]
    disabled += [
        n
        for n, v in _mapping(continuity.get("limbs")).items()
        if isinstance(n, str)
        and n
        and isinstance(v, str)
        and re.search(r"不可用|已废|断裂|折断|无法(?:抬|动|使用)|失去(?:所有联系|行动能力|控制)", v)
    ]
    disabled += [n.replace("臂", "手") for n in disabled if n.endswith("臂")]
    balances = {
        k: dict(_mapping(v)) for k, v in _mapping(continuity.get("balances")).items() if isinstance(k, str) and k
    }
    recent, speaker, paragraph = [], None, None
    for i, (a, b) in enumerate(spans, 1):
        sentence = text[a:b]
        is_quote = any(left < b and right > a for left, right in quoted)
        current = next(left for left, right in paragraphs if left <= a < right)
        if current != paragraph:
            recent, speaker, paragraph = [], None, current
        categories = found.setdefault(i, [])
        if any(a <= pos < b for pos in bad):
            categories.append("quote_candidate")
        han = "".join(re.findall(r"[\u4e00-\u9fff]", sentence))
        if len(han) >= 8 and any(
            len(old) >= 8 and SequenceMatcher(None, old, han, autojunk=False).ratio() >= 0.72 for old in recent
        ):
            categories.append("similarity_candidate")
        recent = (recent + [han])[-3:]
        for name, verb in re.findall(
            r"(?:^|[，,])([\u4e00-\u9fff]{2,4}?)(说|答道|突然开口|问道|问|接话|回答|反问|喊道|笑道)", sentence
        ):
            if name == speaker and verb in ("说", "答道", "突然开口"):
                categories.append("speaker_candidate")
            speaker = name
        if re.search(
            r"没|未|不|无法|回忆|想起|记忆|记得|曾|从前|当年|如果|假如|要是|试图|尝试|失败|计划|打算|准备|险些|差点|据说|听说|梦|以为|猜测|大概",
            sentence,
        ):
            continue
        for tool in disabled:
            if not is_quote and re.search(
                rf"(?:^|[，,；;])(?:{lead})(?:主动|立刻|再次|直接|随即|竟然|又|便|仍|就)?(?:施展|使用|发动|启用|使出|动用|运起|运转|抬起|伸出|举起|用)了?(?:被封的|已废的|那只)?{re.escape(tool)}",
                sentence,
            ):
                categories.append("continuity_candidate")
        for key, value in balances.items():
            unit = value.get("unit")
            if not isinstance(unit, str) or not unit:
                continue
            number, unit = r"(\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百]+)", re.escape(unit)
            match = re.fullmatch(
                rf"(?:(?:{lead})的)?{re.escape(key)}(?:余额)?(?:从{number}{unit}[，,]?)?(?:(?:扣减|扣除|消耗|减少)了?{number}{unit}[，,]?)?(?:余额)?(?:剩余|还剩|剩下|为|：|:){number}{unit}[。！!]?",
                sentence.strip('“”「」"'),
            )
            if not match:
                if key in sentence:
                    value["amount"] = None
                continue
            old, spent, remaining = (_number(v) for v in match.groups())
            ceiling = _number(value.get("ceiling"))
            if _number(value.get("amount")) is None and ceiling is not None and remaining > ceiling:
                categories.append("balance_candidate")
            before = old if old is not None else _number(value.get("amount"))
            if (
                remaining is not None
                and (match[1] is None or old is not None)
                and (match[2] is None or spent is not None)
                and (
                    (old is not None and _number(value.get("amount")) not in (None, old))
                    or (before is not None and before - (spent or 0) != remaining)
                )
            ):
                categories.append("balance_candidate")
            value["amount"] = str(remaining)
    return [
        dict(id=i, start=a, end=b, text=text[a:b], categories=list(dict.fromkeys(found[i])))
        for i, (a, b) in enumerate(spans, 1)
        if found.get(i)
    ]
