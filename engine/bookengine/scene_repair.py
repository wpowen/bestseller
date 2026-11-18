"""E7: exact material corrections under explicit ownership and object continuity.

Restraints and all ambiguous material findings remain reference-only in checkup.
No actor, action, result, pronoun or closing paragraph is eligible for rewriting.
"""

import re

from .checks import high_confidence
from .scene_consistency import _MATERIAL_OBJECT, material_findings
from .text import _paragraph_spans, _sentences

_HOLD = r"(?:握紧|握着|握住|攥紧|攥着|拿着|持着|举着)"
_USE = r"(?:握紧|握着|握住|攥紧|攥着|拿着|持着|举着|举起)"
_MEASURE = r"(?:这|那|一)(?:根|把|柄|支|面)"
_SAME = r"(?:同一|原先那)(?:根|把|柄|支|面)"
_UNCERTAIN = re.compile(
    r"并未|未曾|不再|没有|没能|未能|不能|不曾|尚未|试图|想要|如果|假如|可能|似乎|"
    r"回想|回忆|当年|曾经|据说|仿佛|如同|像|换|丢|放下|搁下|递|交给|另一|另外|变成|化作|化为|化成|"
    r"[“”「」『』\"']"
)


def _closing(text):
    spans = _paragraph_spans(text)
    return text[spans[-1][0] :] if spans else text


def scene_repair_candidates(text, bible):
    """Only adjacent, explicit same-holder/same-object material claims, outside the last paragraph."""
    spans = _sentences(text)
    paragraphs = _paragraph_spans(text)
    final_start = paragraphs[-1][0] if paragraphs else 0
    sentences = [text[start:end] for start, end in spans]
    names = [row["name"] for row in bible.get("characters", []) if isinstance(row, dict) and row.get("name")]
    found = []
    for hit in material_findings(text):
        first, later = hit["first_quote"], hit["quote"]
        if sentences.count(first) != 1 or sentences.count(later) != 1:
            continue
        earlier, index = sentences.index(first), sentences.index(later)
        if index != earlier + 1 or spans[index][1] > final_start or _UNCERTAIN.search(first + later):
            continue
        owners = [name for name in names if name in first]
        if len(owners) != 1 or any(name in later for name in names if name != owners[0]):
            continue
        name = owners[0]
        if not re.fullmatch(rf"{re.escape(name)}{_HOLD}(?:{_MEASURE})?{re.escape(hit['first'])}[。！？!?]+", first):
            continue
        if not re.match(rf"^{re.escape(name)}{_USE}{_SAME}{re.escape(hit['later'])}(?=[，。！？!?；])", later):
            continue
        # A second object with the same head makes both identity and the replacement ambiguous.
        if any(
            sum(head == hit["object"] for _, head in _MATERIAL_OBJECT.findall(sentence)) != 1
            for sentence in (first, later)
        ):
            continue
        found.append(
            dict(
                hit,
                id=index + 1,
                start=spans[index][0],
                end=spans[index][1],
                name=name,
                qualification="explicit_same_object_same_holder",
                replacement=later.replace(hit["later"], hit["first"], 1),
            )
        )
    return sorted(found, key=lambda row: row["start"])


def _payoff_kept(raw, checked, text):
    prior = raw.get("payoff") if isinstance(raw, dict) else None
    if not isinstance(prior, dict) or prior.get("fulfilled") is not True or not prior.get("quote"):
        return True
    payoff = checked.get("payoff")
    quote = payoff.get("quote") if isinstance(payoff, dict) else None
    return bool(
        isinstance(payoff, dict)
        and payoff.get("fulfilled") is True
        and isinstance(quote, str)
        and quote.strip()
        and quote in text
        and prior["quote"] in text
    )


def repair_scene_consistency(client, prefix, text, raw, failed, state, bible, outline, extract, ask):
    """Correct material deterministically; ask remains an unused caller-compatible argument."""
    hits = scene_repair_candidates(text, bible)
    note = dict(status="checked", before=hits, after=hits, edits=[])
    if not hits:
        return text, raw, failed, note
    edits = [dict(id=hit["id"], text=hit["replacement"]) for hit in hits]
    candidate = text
    for hit in reversed(hits):
        candidate = candidate[: hit["start"]] + hit["replacement"] + candidate[hit["end"] :]
    reason = (
        "closing_paragraph_changed"
        if _closing(candidate) != _closing(text)
        else "source_fact_changed"
        if any(hit["first_quote"] not in candidate for hit in hits)
        else "contradiction_remains"
        if scene_repair_candidates(candidate, bible)
        else None
    )
    if reason:
        return text, raw, failed, dict(note, status="rolled_back", reason=reason)
    checked, invalid, _ = extract(
        client, prefix + "-scene-consistency-state", candidate, state, bible, outline, [], final=True
    )
    if (
        invalid
        or high_confidence(state, checked.get("events", []), candidate)
        or not _payoff_kept(raw, checked, candidate)
    ):
        return text, raw, failed, dict(note, status="rolled_back", reason="state_recheck_failed")
    return candidate, checked, False, dict(note, status="repaired", after=[], edits=edits)
