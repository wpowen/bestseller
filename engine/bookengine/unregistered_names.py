"""Conservative first-use candidates for names absent from the fixed cast."""

import re

from .identity import RULES
from .precision import _sentences


_SURNAMES = "".join(RULES["surname"])
_SUBJECT = re.compile(RULES["subject_pattern"].replace("SURNAME", _SURNAMES))
_VOCATIVE = re.compile(r"[“「『]\s*(阿[\u4e00-\u9fff]{1,2}|[" + _SURNAMES + r"][\u4e00-\u9fff]{1,2})\s*[。！？!?]?[”」』]")
_NAMED = re.compile(r"(?:名叫|名为|叫作|唤作|喊着|喊道)\s*([" + _SURNAMES + r"][\u4e00-\u9fff]{1,2})")


def _strings(value):
    if isinstance(value, str):
        return [value] if value else []
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


def _known_labels(bible):
    return {
        label
        for card in (bible or {}).get("characters", [])
        if isinstance(card, dict)
        for label in [card.get("name"), card.get("address"), *_strings(card.get("aliases"))]
        if isinstance(label, str) and label
    }


def name_findings(text, bible, chapter=None):
    known = _known_labels(bible)
    spans = _sentences(text)
    candidates = []
    for pattern in (_SUBJECT, _VOCATIVE, _NAMED):
        for match in pattern.finditer(text):
            name = match[1]
            if name in known or name in RULES["exclude"]:
                continue
            candidates.append((match.start(1), name))
    findings = []
    seen = set()
    for offset, name in sorted(candidates):
        if name in seen:
            continue
        span = next(
            ((number, start, end) for number, (start, end) in enumerate(spans, 1) if start <= offset < end),
            None,
        )
        if span is None:
            continue
        seen.add(name)
        number, start, end = span
        findings.append(
            dict(
                kind="unregistered_name",
                chapter=chapter,
                name=name,
                status="Inference",
                observed=name,
                expected="人物卡name、aliases或address中的登记称呼",
                reason="像人名或称呼，但未登记在人物卡；首次出现需核对",
                quote=text[start:end],
                start=start,
                end=end,
                id=number,
            )
        )
    return findings
