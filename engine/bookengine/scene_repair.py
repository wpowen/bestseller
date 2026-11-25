"""E7: exact material corrections under explicit ownership and object continuity.

Restraints and all ambiguous material findings remain reference-only in checkup.
A local handoff needs a later return by the same named parties. The closing paragraph is protected.
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
    r"命令|要求|让|叫|曾|[“”「」『』\"‘’]"
)
_PICKUP = re.compile(r"捡起|拾起|拾取|捞起|抄起|拿起|取起|捧起|抱起|抓起|拎起")
_HANDOFF = re.compile(r"递给|交给|还给|塞给|递到|交到|接过|接下|接住|收下|交还|转交")
_RESET = re.compile(r"放下|放回|归还|搁下|丢下|掉落|脱手|落地|跌落|滑落|摔落|又掉|再掉|落在")
_SCENE_BREAK = re.compile(r"翌日|次日|第二天|片刻后|不久后|另一边|转眼|回到|来到|离开|出了|进入|换到|天亮|夜深")
_RETURN = re.compile(r"还给|递给|交给|塞回|放回|归还|交还")
_CLASSIFIER = re.compile(r"^(?:一|这|那|原先那|同一)(?:本|册|把|柄|支|件|张|块|枚)?")
_AMBIGUOUS_OBJECT = re.compile(r"另一|另外|第二|两(?:本|把|柄|件|张|块|枚)|多(?:本|把|件)|各自|不同")
_EXPLICIT_SAME_OBJECT = re.compile(r"同一|原先那|刚才那|这(?:本|把|柄|件|张|块|枚)")


def _closing(text):
    spans = _paragraph_spans(text)
    return text[spans[-1][0] :] if spans else text


def _object_text(raw):
    """Keep the lexical object head while retaining ambiguity markers separately."""
    raw = re.sub(r"^(?:了|着|住)", "", raw.strip())
    raw = raw.rsplit("的", 1)[-1].strip("，, ")
    raw = re.sub(r"(?:时|后|了|着|住)$", "", raw)
    raw = _CLASSIFIER.sub("", raw)
    return raw[-8:] if raw else ""


def _pickup_object(sentence, action):
    """Extract a forward or backward pickup object; ambiguous wording is retained."""
    after = sentence[action.end() :]
    forward = re.match(r"(?:了|着|住)?([\u4e00-\u9fffA-Za-z·]{1,12})", after)
    if forward:
        raw = re.split(r"[，。！？；,!?；]", forward.group(1), 1)[0]
        object_name = _object_text(raw)
        object_end = action.end() + raw.rfind(object_name) + len(object_name)
        return object_name, action.end(), object_end, raw
    before = sentence[: action.start()]
    clause = re.split(r"[，,。！？；]", before)[-1]
    raw = re.split(r"(?:将|把)", clause)[-1].strip()
    if not raw:
        return "", action.start(), action.start(), raw
    object_name = _object_text(raw)
    start = sentence.rfind(raw, 0, action.start())
    return object_name, max(start, 0), action.end(), raw


def _pickup_actor(sentence, action, names):
    candidates = [(sentence.rfind(name, 0, action.start()), name) for name in names]
    candidates = [(position, name) for position, name in candidates if position >= 0]
    candidates = [
        (position, name)
        for position, name in candidates
        if not re.search(rf"{re.escape(name)}的", sentence[position : action.start()])
    ]
    clause = re.split(r"[，,；]", sentence[: action.start()])[-1]
    clause_names = [name for name in names if name in clause and not re.search(rf"{re.escape(name)}的", clause)]
    if len(clause_names) == 1:
        return clause_names[0]
    if len(candidates) == 1:
        return candidates[0][1]
    return None


def _pickup_action(sentence, names, fallback=None):
    for action in _PICKUP.finditer(sentence):
        if _UNCERTAIN.search(sentence):
            continue
        actor = _pickup_actor(sentence, action, names)
        if not actor and fallback and re.match(r"^\s*(?:他|她|对方|那人)", sentence):
            actor = fallback
        object_name, object_start, object_end, raw = _pickup_object(sentence, action)
        if actor and object_name:
            yield dict(
                actor=actor,
                object=object_name,
                raw_object=raw,
                start=action.start(),
                end=action.end(),
                object_start=object_start,
                object_end=object_end,
                verb=action.group(0),
            )


def _inside_dialogue(text, offset):
    prefix = text[:offset]
    return (
        any(
            prefix.rfind(left) > prefix.rfind(right)
            for left, right in (("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』"))
        )
        or prefix.count('"') % 2 == 1
    )


def _return_supported(text, first, later, spans):
    # A direct return establishes which hand held the object. A second transfer,
    # a scene change, or an utterance cannot establish the same chain.
    direct = re.compile(
        rf"{re.escape(later['actor'])}(?:立刻|马上|连忙|随即|当即)?(?:把|将)"
        rf"{re.escape(later['object'])}(?:{_RETURN.pattern}){re.escape(first['actor'])}"
        rf"(?:手里|手中)?(?=[，,。！？!?；]|$)"
    )
    for start, end in spans[later["sentence_index"] + 1 : later["sentence_index"] + 41]:
        sentence = text[start:end]
        if direct.match(sentence) and not _inside_dialogue(text, start):
            return True
        if (
            _SCENE_BREAK.search(sentence)
            or _HANDOFF.search(sentence)
            or _RESET.search(sentence)
            or _PICKUP.search(sentence)
        ):
            return False
    return False


def object_handoff_findings(text, bible):
    """Find a named actor picking up the same object without a handoff or reset."""
    spans = _sentences(text)
    names = [row["name"] for row in bible.get("characters", []) if isinstance(row, dict) and row.get("name")]
    actions = []
    previous_names = []
    previous_sentence = ""
    for index, (start, end) in enumerate(spans):
        sentence = text[start:end]
        sentence_names = [name for name in names if name in sentence]
        fallback = (
            previous_names[0]
            if len(previous_names) == 1 and not sentence_names and not _SCENE_BREAK.search(previous_sentence + sentence)
            else None
        )
        for action in _pickup_action(sentence, names, fallback):
            if _inside_dialogue(text, start + action["start"]):
                continue
            actions.append(
                dict(
                    action, sentence_index=index, sentence=sentence, sentence_start=start, start=start + action["start"]
                )
            )
        previous_names = sentence_names
        previous_sentence = sentence
    findings = []
    for first, later in zip(actions, actions[1:]):
        distance = later["sentence_index"] - first["sentence_index"]
        if distance <= 0 or distance > 12 or later["actor"] == first["actor"]:
            continue
        if first["object"] != later["object"]:
            continue
        combined_raw = first["raw_object"] + later["raw_object"]
        if _AMBIGUOUS_OBJECT.search(combined_raw):
            continue
        if not _EXPLICIT_SAME_OBJECT.search(combined_raw) and re.search(
            r"(?:一(?:本|把|柄|件|张|块|枚)|多)", combined_raw
        ):
            continue
        between = (
            first["sentence"][first["end"] :]
            + text[first["sentence_start"] + len(first["sentence"]) : later["sentence_start"]]
        )
        if _HANDOFF.search(between) or _RESET.search(between):
            continue
        if _SCENE_BREAK.search(between):
            continue
        sentence_start = spans[later["sentence_index"]][0]
        sentence_end = spans[later["sentence_index"]][1]
        later_sentence = later["sentence"]
        verb_start = later["start"] - sentence_start
        prefix = later_sentence[:verb_start]
        simple = bool(
            re.fullmatch(rf"{re.escape(later['actor'])}(?:弯腰|俯身|低头|蹲下|探身)?", prefix)
            and re.match(r"^时(?:[，,。！？；]|$)|^[，,。！？；]|^$", later_sentence[later["object_end"] :])
        )
        returned = _return_supported(text, first, later, spans)
        repairable = returned and simple and first["verb"] in {"捡起", "拾起"} and later["verb"] in {"捡起", "拾起"}
        replacement = None
        if repairable:
            replacement = (
                f"{later['actor']}从{first['actor']}手里取过{later['object']}" + later_sentence[later["object_end"] :]
            )
        findings.append(
            dict(
                kind="object_handoff",
                first_quote=first["sentence"],
                quote=later_sentence,
                object=later["object"],
                first_actor=first["actor"],
                actor=later["actor"],
                id=later["sentence_index"] + 1,
                start=sentence_start,
                end=sentence_end,
                qualification=(
                    "same_object_repicked_with_later_return"
                    if returned
                    else "same_object_repicked_without_return_evidence"
                ),
                repairable=repairable,
                replacement=replacement,
            )
        )
    return findings


def scene_repair_candidates(text, bible, *, handoffs=None):
    """Return explicit material or same-object handoff candidates outside the last paragraph."""
    spans = _sentences(text)
    paragraphs = _paragraph_spans(text)
    final_start = paragraphs[-1][0] if paragraphs else 0
    sentences = [text[start:end] for start, end in spans]
    names = [row["name"] for row in bible.get("characters", []) if isinstance(row, dict) and row.get("name")]
    found = []
    if handoffs is None:
        handoffs = object_handoff_findings(text, bible)
    for hit in [*material_findings(text), *handoffs]:
        if hit["kind"] == "object_handoff":
            if not hit.get("repairable") or spans[hit["id"] - 1][1] > final_start:
                continue
            found.append(hit)
            continue
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
    handoffs = object_handoff_findings(text, bible)
    hits = scene_repair_candidates(text, bible, handoffs=handoffs)
    note = dict(status="checked", before=hits, after=hits, edits=[])
    review = [hit for hit in handoffs if hit not in hits]
    if review:
        note["review_only"] = review
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
