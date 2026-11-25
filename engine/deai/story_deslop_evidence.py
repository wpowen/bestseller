"""Carry only locally grounded action evidence into a paragraph edit."""

import re

from .text import paragraphs


_PROMISE = re.compile(
    r'(?P<open>[“「『"])(?P<condition>(?:要是|如果|倘若|假如|只要|若是)[^，,。！？!?；;“”「」『』"\n]+)[，,]'
    r'我(?P<then>就|便|才)?(?P<act>(?:给你|替你|帮你|陪你|带你)[^，,。！？!?；;“”「」『』"\n]+)'
    r'(?P<end>[。！!]?[”」』"])'
)


def support_is_distinct(finding, index):
    """Both citations must resolve to specific, non-overlapping spans in the frozen source."""
    support = finding.get("supports")
    if not isinstance(support, dict) or not isinstance(support.get("id"), str):
        return False
    pid, other_pid = finding["id"], support["id"]
    if other_pid not in index:
        return False
    target, witness = finding["quote"], support.get("quote")
    if not isinstance(witness, str) or not witness.strip():
        return False
    # Check overlapping occurrences too, e.g. "哈哈" in "哈哈哈" has two possible positions.
    text, other = index[pid]["text"], index[other_pid]["text"]
    a, b = text.find(target), other.find(witness)
    if a < 0 or b < 0 or text.find(target, a + 1) != -1 or other.find(witness, b + 1) != -1:
        return False
    return other_pid != pid or a + len(target) <= b or b + len(witness) <= a


def scan_route(finding, index, lexical_hints):
    kind = finding.get("kind")
    if kind == "scene_structure":
        return "return_to_planning"
    if kind == "insufficient_context":
        return "insufficient_context"
    if kind not in ("redundant_explanation", "expression"):
        return "keep"
    if set(finding["gates"]) == {"A"} or finding["quote"].strip() in lexical_hints:
        return "keep"
    if kind == "redundant_explanation" and not support_is_distinct(finding, index):
        return "insufficient_context"
    return "local_edit"


def removed_promise_condition(before, after):
    """Witness only a direct offer whose entire prerequisite was deleted in place."""
    for match in _PROMISE.finditer(before):
        action = match["act"]
        if any(marker in action for marker in ("过", "了", "的", "着")):
            continue  # May be a recollection or a relative clause rather than an offer.
        for transition in (match["then"] or "", ""):
            bare = match["open"] + "我" + transition + action + match["end"]
            if before[: match.start()] + bare + before[match.end() :] == after:
                return {"before": match[0], "after": bare, "condition": match["condition"]}
    return None


def chapter_actions(record, text, number):
    if (
        not isinstance(record, dict)
        or record.get("number") != int(number)
        or record.get("status") != "complete"
        or record.get("fact_source") != "final_body"
        or record.get("state_failed")
        or not isinstance(record.get("solutions"), list)
    ):
        return {}
    parts = paragraphs(text)
    actions = {}
    for item in record["solutions"]:
        if not isinstance(item, dict) or item.get("chapter") != int(number) or item.get("noun_status") != "Evidence":
            continue
        quote = item.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            continue
        hits = [p for p in parts if quote in p["text"]]
        if len(hits) != 1:
            continue
        terms = [item.get(k) for k in ("method_noun", "target_noun")]
        terms = [t for t in terms if isinstance(t, str) and t.strip() and t in quote]
        if terms:
            saved = actions.setdefault(hits[0]["pid"], [])
            saved.extend(term for term in terms if term not in saved)
    return actions


def anchors_for(old, source_anchors, actions, in_ending):
    """An ungrounded scanner finding cannot authorize losing a fact or changing the ending."""
    supplied = [] if source_anchors is None else source_anchors
    if not isinstance(supplied, list) or any(
        not isinstance(s, str) or s != s.strip() or not 1 <= len(s) <= 24 or s not in old for s in supplied
    ):
        return [], "ungrounded_fact_anchor"
    anchors = list(dict.fromkeys([*actions, *supplied]))
    if in_ending and not anchors:
        return [], "ending_without_fact_anchor"
    return anchors, None


def changed_operator(before, after, anchors, names):
    """Only reject unambiguous changes of the named subject directly using the same tool or method."""
    if not anchors or not names:
        return False
    subjects = "|".join(re.escape(s) for s in sorted(names, key=len, reverse=True))
    for term in anchors:
        phrase = re.compile(rf"({subjects})(?:用|以|拿|持|握着){re.escape(term)}")
        old, new = phrase.findall(before), phrase.findall(after)
        if len(old) == len(new) == 1 and old != new:
            return True
    return False


def changed_action_outcome(before, after, anchors):
    """Catch the same grounded action changing between completed and attempted/failed."""
    for term in anchors:
        modal = re.compile(
            rf"(?:未能|没能|未曾|并未|没有|试图|打算|想要|企图|无法|难以)([\u3400-\u9fff]{{0,5}}?{re.escape(term)})"
        )
        old, new = set(modal.findall(before)), set(modal.findall(after))
        if any(phrase in before for phrase in new - old) or any(phrase in after for phrase in old - new):
            return True
    return False
