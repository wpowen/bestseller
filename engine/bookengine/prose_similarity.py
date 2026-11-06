"""Read-only R4 candidates; lexical overlap is Inference, never a deletion rule.

Offsets refer to the original input, paragraph numbers are one-based. Compare
only wholly narrative paragraphs (mixed speech is conservatively excluded).
The two overlap gates were calibrated against eval-33/eval-35, including the
unequal-length pair in eval-33 chapter 36. A single identity introduction is
report-only; more than one template sentence per chapter invites model repair.
"""

import re

from .prose_repetition import _normalize, _position, _sentences
from .text import _paragraph_spans


RULES = dict(
    ngram=3, jaccard_min=0.32, containment_min=0.7, paragraph_distance=3, min_characters=10, card_repair_above=1
)
_QUOTES = set("“”‘’「」『』\"'«»＂＇")
_AS_ROLE = re.compile(r"作为[^，,。！？!?；;：:\n]{2,30}[，,]\s*(?:他|她|其)")
_ROLE = r"[^，,。！？!?；;：:\n]{0,20}(?:师|者|人|将|使|官|卫|主|长|管事|掌柜)"
_IDENTITY = re.compile(r"^[\u3400-\u9fff]{2,4}是[^，,。！？!?；;：:\n]{1,30}的" + _ROLE + r"(?:与|和|兼)" + _ROLE)


def analyze_prose(text):
    """Locate nearby paraphrases and card templates without changing any bytes.

    Exact normalized paragraphs are left to prose_repetition. Candidate records
    carry measurable scores and source spans, not a claim of semantic equality.
    """
    paragraphs = _paragraph_spans(text)
    sentences = _sentences(text, paragraphs)
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
            # The conjunction of occupational roles avoids object definitions;
            # ordinary "A is a shopkeeper" introductions stay report-free.
            template = (
                "as_role"
                if _AS_ROLE.search(node["text"])
                else "identity_roles"
                if _IDENTITY.search(node["text"])
                else None
            )
            if template:
                cards.append(dict(node, kind="card_explanation", template=template, status="Inference"))
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
