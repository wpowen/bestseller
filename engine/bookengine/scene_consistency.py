"""Instruction 68 (E7, E8): reference-only findings inside one chapter.

E7: an object whose material changes (铁棍 … 木棍) and a bound character who acts before being freed.
E8: a first appearance where affiliation, role and name are run together as one label.
All three need two quoted sentences or a quoted label; none of them triggers a repair.
"""

import re

from .character_context import identity_keywords
from .precision import _sentences

_MATERIALS = "铁木铜石玉银金竹骨"
_OBJECTS = "棍杖刀剑枪锤盾"  # hand-held only: 桩、钉、链 are routinely several different objects
_WINDOW = 25  # sentences
_MATERIAL_OBJECT = re.compile(f"([{_MATERIALS}])([{_OBJECTS}])")
_BOUND = re.compile(
    r"(?:绑|缚|捆|勒)(?:住|在|紧|上)?[^。！？]{0,12}(?:双臂|双手|手腕|两臂|两手)|(?:双臂|双手|手腕)[^。！？]{0,8}被(?:绑|缚|捆)"
)
_FREED = re.compile(r"磨断|挣断|挣开|挣脱|割断|松绑|解开|绳[^。！？]{0,6}断")
_HAND_ACT = re.compile(r"一拳|出拳|挥拳|拳锋|抬手|伸手|挥手|抓住|拔出")
_HAN = r"[㐀-鿿]"


def material_findings(text: str) -> list[dict]:
    """The same object head with two materials, held by the same hands within a few sentences."""
    spans = _sentences(text)
    seen: dict[str, tuple[str, int, str]] = {}
    findings = []
    for number, (start, end) in enumerate(spans, 1):
        sentence = text[start:end]
        for material, head in _MATERIAL_OBJECT.findall(sentence):
            first = seen.setdefault(head, (material, number, sentence))
            if first[0] != material and number - first[1] > _WINDOW:
                seen[head] = (material, number, sentence)
            elif first[0] != material and not any(row["object"] == head for row in findings):
                findings.append(dict(kind="material_change", object=head, first=first[0] + head, later=material + head,
                                     first_quote=first[2], quote=sentence, status="Inference"))  # fmt: skip
    return findings


def restraint_findings(text: str, protagonist: str | None) -> list[dict]:
    """A character whose arms are bound uses them before any sentence frees them."""
    bound = None
    for start, end in _sentences(text):
        sentence = text[start:end]
        if _FREED.search(sentence):
            if bound and _HAND_ACT.search(sentence[: _FREED.search(sentence).start()]) is None:
                bound = None
                continue
        if bound and _HAND_ACT.search(sentence) and (not protagonist or protagonist in sentence):
            return [dict(kind="acts_while_bound", bound_quote=bound, quote=sentence, status="Inference")]
        if _BOUND.search(sentence) and (not protagonist or protagonist in sentence):
            bound = sentence
    return []


def identity_label_findings(text: str, bible: dict) -> list[dict]:
    """First mention written as 所属+身份+姓名 or 姓名+这个+身份 instead of a person doing something."""
    findings = []
    for card in bible.get("characters", []) if isinstance(bible, dict) else []:
        name = card.get("name") if isinstance(card, dict) else None
        if not isinstance(name, str) or not name or name not in text:
            continue
        first = text.index(name)
        start, end = next(((a, b) for a, b in _sentences(text) if a <= first < b), (first, first + len(name)))
        sentence = text[start:end]
        keywords = [
            word for word in identity_keywords(str(card.get("identity") or ""), card.get("occupation", "")) if word
        ]
        before = re.search(f"(?:^|[，。！？\\s“])({_HAN}{{4,16}}){re.escape(name)}", sentence)
        after = re.search(re.escape(name) + f"这个({_HAN}{{3,16}})", sentence)
        label = after[1] if after else before[1] if before and any(word in before[1] for word in keywords) else None
        if label:
            findings.append(dict(kind="identity_label", name=name, label=label, quote=sentence, status="Inference"))
    return findings


def scene_consistency_checkup(bible: dict, chapters: list[dict]) -> dict:
    protagonist = next((c.get("name") for c in bible.get("characters", []) if c.get("role") == "protagonist"), None)
    seen: set[str] = set()
    rows = dict(material_change=[], acts_while_bound=[], identity_label=[])
    for chapter in sorted(chapters, key=lambda row: row.get("number", 0)):
        text, number = chapter.get("text", ""), chapter.get("number")
        found = material_findings(text) + restraint_findings(text, protagonist)
        found += [row for row in identity_label_findings(text, bible) if row["name"] not in seen]
        seen |= {c["name"] for c in bible.get("characters", []) if isinstance(c.get("name"), str) and c["name"] in text}
        for row in found:
            rows[row["kind"]].append(dict(row, chapter=number))
    return dict(
        counts={kind: len(items) for kind, items in rows.items()}, **rows, status="Inference", reference_only=True
    )
