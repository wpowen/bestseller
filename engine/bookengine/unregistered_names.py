"""Conservative first-use candidates for names absent from the fixed cast."""

import re

from .identity import RULES
from .precision import _sentences


_SURNAMES = "".join(RULES["surname"])
_SUBJECT = re.compile(RULES["subject_pattern"].replace("SURNAME", _SURNAMES))
_VOCATIVE = re.compile(
    r"[“「『]\s*(阿[\u4e00-\u9fff]{1,2}|[" + _SURNAMES + r"][\u4e00-\u9fff]{1,2})\s*[。！？!?]?[”」』]"
)
_NAMED = re.compile(r"(?:名叫|名为|叫作|唤作|喊着|喊道)\s*([" + _SURNAMES + r"][\u4e00-\u9fff]{1,2})")
_NOT_NAMES = {"左手", "右手", "左边", "右边", "时限", "封石", "别怕", "别停", "能用", "麻了", "那便"}
_GRAMMAR_CHARS = set("边道路人兽手后前上下里外能再即要交")


def _strings(value):
    if isinstance(value, str):
        return [value] if value else []
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


def _known_labels(bible):
    return {
        label
        for card in [*(bible or {}).get("characters", []), *(bible or {}).get("antagonists", [])]
        if isinstance(card, dict)
        for label in [card.get("name"), card.get("address"), *_strings(card.get("aliases"))]
        if isinstance(label, str) and label
    }


def _derived(name, bible):
    """A nickname built from a registered character's name (阿棣 for 顾奕棣) is not new."""
    from .address_contracts import derived_address

    return any(
        derived_address(name, card.get("name"))
        for card in [*(bible or {}).get("characters", []), *(bible or {}).get("antagonists", [])]
        if isinstance(card, dict)
    )


def name_findings(text, bible, chapter=None):
    known = _known_labels(bible)
    spans = _sentences(text)
    candidates = []
    for match in _SUBJECT.finditer(text):
        candidates.append((match.start(1), match[1]))
    for match in _VOCATIVE.finditer(text):
        window = text[max(0, match.start() - 24) : min(len(text), match.end() + 40)]
        if re.search(r"旧名|名字|姓名|称呼|名落|(?:喊|叫|唤)[^。！？]{0,12}[“「『]", window):
            candidates.append((match.start(1), match[1]))
    candidates.extend((match.start(1), match[1]) for match in _NAMED.finditer(text))
    findings = []
    seen = set()
    for offset, name in sorted(candidates):
        if (
            name in seen
            or name in known
            or name in RULES["exclude"]
            or name in _NOT_NAMES
            or any(char in _GRAMMAR_CHARS for char in name[1:])
            or any(name.startswith(label) for label in known)
            or _derived(name, bible)
        ):
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
