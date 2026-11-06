"""Pure, conservative INBOX35 repetition checks, shared by generation and checkup.

Call ``clean_repetitions(text)`` after prose cleaning and before extraction; use
``remove_safe=False`` for checkup. Nothing reads files, calls models or mutates
input. All positions refer to the ORIGINAL input: zero-based half-open character
offsets, one-based paragraph/line/sentence IDs (paragraph hits have no sentence ID).
Re-run after any edit before passing positions to another repair/extraction round.
Sentence IDs count this module's complete sentences, including isolated speech;
map by span to precision._sentences IDs when using the existing amend protocol.

Normalized (NFKC, punctuation-insensitive) repetition remains diagnostic.
Only whitespace-identical narration is deleted. Speech is report-only; exact
narration with a dependent following sentence is kept as Unknown. Near paragraph
matches remain candidates for the bounded amend loop. Positions always refer to
the original input, including separators removed with empty paragraphs.
"""

from collections import defaultdict
from difflib import SequenceMatcher
import re
import unicodedata

from .text import _paragraph_spans


_OPEN = {"“": "”", "「": "」", "『": "』", "‘": "’", '"': '"', "'": "'"}
_CLOSE = set(_OPEN.values())
_REFERENCE = re.compile(
    r"^(?:[他她它您你我咱俺这那此其]|自己|对方|彼此|前者|后者|刚才|方才|因此|于是|所以|随即|随后|接着|继而|仍然|依旧|又一次)"
)
# An omitted subject is unsafe even without an explicit pronoun. These common
# action/connective openings are hints; uncertainty is handed to model repair.
_ELIDED = re.compile(
    r"^(?:抬|低|垂|转|回|侧|点|摇|伸|收|抽|拔|握|抓|拿|取|放|推|拉|踢|踹|走|跑|冲|追|退|躲|避|"
    r"望|看|听|笑|哭|喊|问|答|说|骂|咬|攥|摸|按|捂|抱|翻|掀|掷|跳|跃|扑|蹲|站|坐|起身|"
    r"然后|而后|却|便|就|才|又|也|还|仍|只|正|不禁|立刻|马上|终于|继续)"
)


def _normalize(text):
    value = unicodedata.normalize("NFKC", text)
    return "".join(
        char
        for i, char in enumerate(value)
        if char in "+-−"
        or char == "."
        and 0 < i < len(value) - 1
        and value[i - 1].isdigit()
        and value[i + 1].isdigit()
        or not char.isspace()
        and unicodedata.category(char)[0] not in "PZC"
    )


def _position(text, start, end, paragraph, sentence_id=None):
    return dict(
        start=start,
        end=end,
        paragraph=paragraph,
        line=text.count("\n", 0, start) + 1,
        sentence_id=sentence_id,
        text=text[start:end],
        action="reference",
        reasons=[],
    )


def _sentences(text, paragraphs):
    """Separate spoken sentences from attribution; include only balanced quotes."""
    result = []
    quotes = []
    for paragraph, (left, right) in enumerate(paragraphs, 1):
        start, i = left, left
        while i < right:
            char = text[i]
            if char in "'’" and i > left and i + 1 < right and re.fullmatch(r"[A-Za-z]{2}", text[i - 1] + text[i + 1]):
                i += 1
                continue
            if quotes and char == _OPEN[quotes[-1][0]]:
                quotes.pop()
                start = i + 1
            elif char in _OPEN:
                quotes.append((char, i))
                start = i + 1
            elif char in _CLOSE:
                start = i + 1
            else:
                terminal = char in "。！？!?" or (
                    char == "."
                    and not (i > left and text[i - 1] == "." or i + 1 < right and text[i + 1] == ".")
                    and not (i > left and text[i - 1].isdigit() and i + 1 < right and text[i + 1].isdigit())
                )
                if terminal:
                    end = i + 1
                    while end < right and text[end] in "。！？!?":
                        end += 1
                    while start < i and text[start].isspace():
                        start += 1
                    a, b = start, end
                    for opener, pos in reversed(quotes):
                        if pos == a - 1 and b < right and text[b] == _OPEN[opener]:
                            a, b = pos, b + 1
                        else:
                            break
                    if _normalize(text[start:end]):
                        node = _position(text, a, b, paragraph, len(result) + 1)
                        node["quoted"] = bool(quotes)
                        node["whole_quote"] = bool(quotes) and a == quotes[0][1] and b > end
                        result.append(node)
                    start, i = end, end - 1
            i += 1
    return result


def _exact(text):
    return re.sub(r"\s+", "", text)


def _dependency_reasons(text, occurrence, sentences, candidates):
    # Check the next surviving sentence, not an earlier pronoun or the removed
    # subject. Right-to-left evaluation protects dependencies in deletion chains.
    end = occurrence["end"]
    tail = text[end:]
    for node in reversed(sentences):
        if node["start"] >= end and (node["start"], node["end"]) in candidates:
            a, b = node["start"] - end, node["end"] - end
            tail = tail[:a] + tail[b:]
    opening = tail.lstrip()
    if opening and opening[0] in _CLOSE:
        return ["unbalanced_quote"]
    if _REFERENCE.search(opening):
        return ["reference_dependency"]
    if _ELIDED.search(opening):
        return ["subject_dependency"]
    return []


def clean_repetitions(text, *, remove_safe=True):
    """Return cleaned/records/removed/needs_repair/counts, all JSON serializable.

    Each record has kind, normalized, count, repetitions and occurrences. Sentence
    counts are groups and excess occurrences; paragraph counts are adjacent pairs,
    so they must NOT be added to sentence counts (the same text can hit both).
    Actions: reference, removed, repair (near match), reported, unknown. Speech
    is reported only; Unknown exact narration is never handed to model repair. ``removed`` contains disjoint original
    spans only; every character outside those spans is preserved verbatim.
    """
    paragraphs = _paragraph_spans(text)
    sentences = _sentences(text, paragraphs)
    groups = defaultdict(list)
    for node in sentences:
        key = _normalize(node["text"])
        if len(key) >= 10:
            groups[(key, node["quoted"])].append(node)
    records, candidates = [], []
    exact_candidates = set()
    for (_, quoted), nodes in groups.items():
        seen = set()
        for node in nodes:
            key = _exact(node["text"])
            if key in seen and not quoted:
                exact_candidates.add((node["start"], node["end"]))
            seen.add(key)
    unsafe = {}
    for node in reversed(sentences):
        span = (node["start"], node["end"])
        if span in exact_candidates:
            reasons = _dependency_reasons(text, node, sentences, exact_candidates)
            if reasons:
                unsafe[span] = reasons
                exact_candidates.remove(span)

    def decide(node, *, exact=False, quoted=False, unique=False):
        node = dict(node)
        span = (node["start"], node["end"])
        reasons = unsafe.get(span, []) if exact else ["unique_content"] if unique else ["non_exact_match"]
        if quoted:
            node.update(action="reported", reasons=["对白重复，可能有意"])
        elif exact and reasons:
            node.update(action="unknown", status="Unknown", reasons=reasons)
        elif exact:
            node.update(action="removed" if remove_safe else "reported", reasons=[])
        else:
            node.update(action="repair", reasons=reasons)
        if node["action"] == "removed":
            candidates.append(span)
        return node

    for (key, quoted), nodes in groups.items():
        if len(nodes) > 1:
            seen = {_exact(nodes[0]["text"])}
            occurrences = [dict(nodes[0])]
            for node in nodes[1:]:
                exact = _exact(node["text"]) in seen
                occurrences.append(decide(node, exact=exact, quoted=quoted))
                seen.add(_exact(node["text"]))
            records.append(
                dict(
                    kind="sentence",
                    normalized=key,
                    quoted=quoted,
                    count=len(nodes),
                    repetitions=len(nodes) - 1,
                    occurrences=occurrences,
                )
            )
    for index in range(1, len(paragraphs)):
        a, b = paragraphs[index - 1], paragraphs[index]
        first, second = _normalize(text[a[0] : a[1]]), _normalize(text[b[0] : b[1]])
        if min(len(first), len(second)) < 10:
            continue
        similarity = SequenceMatcher(None, first, second, autojunk=False).ratio()
        if similarity >= 0.85:
            records.append(
                dict(
                    kind="paragraph",
                    normalized=second,
                    similarity=similarity,
                    count=2,
                    repetitions=1,
                    occurrences=[
                        _position(text, *a, index),
                        dict(_position(text, *b, index + 1), action="reported", reasons=["sentence_checks_only"])
                        if any(
                            n.get("quoted") or (n["start"], n["end"]) in unsafe
                            for n in sentences
                            if a[0] <= n["start"] < b[1]
                        )
                        or _exact(text[a[0] : a[1]]) == _exact(text[b[0] : b[1]])
                        else decide(_position(text, *b, index + 1), unique=True),
                    ],
                )
            )

    # Expand actual removal spans to include separators of emptied paragraphs;
    # surviving paragraphs retain their original bytes.
    for index, (left, right) in enumerate(paragraphs):
        rest = text[left:right]
        for a, b in reversed(sorted(candidates)):
            if left <= a and b <= right:
                rest = rest[: a - left] + rest[b - left :]
        if not rest.strip():
            candidates.append((left, paragraphs[index + 1][0] if index + 1 < len(paragraphs) else right))

    # A paragraph proposal may contain several sentence proposals. Merge ranges
    # once, without ever stripping whitespace or rebuilding untouched paragraphs.
    spans = []
    for start, end in sorted(set(candidates)):
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
        else:
            spans.append((start, end))
    pending = {}
    for record in records:
        for node in record["occurrences"][1:]:
            if any(a <= node["start"] and node["end"] <= b for a, b in spans):
                node["action"], node["reasons"] = "removed", []
            if node["action"] == "repair":
                pending[(node["start"], node["end"])] = node
    cleaned = text
    for start, end in reversed(spans):
        cleaned = cleaned[:start] + cleaned[end:]
    sentence_records = [record for record in records if record["kind"] == "sentence"]
    return dict(
        cleaned=cleaned,
        records=records,
        removed=[dict(start=a, end=b) for a, b in spans],
        needs_repair=sorted(pending.values(), key=lambda node: node["start"]),
        counts=dict(
            repeated_sentences=len(sentence_records),
            sentence_repetitions=sum(record["repetitions"] for record in sentence_records),
            narrative_repetitions=sum(r["repetitions"] for r in sentence_records if not r["quoted"]),
            dialogue_repetitions=sum(r["repetitions"] for r in sentence_records if r["quoted"]),
            unknown_spans=sum(n["action"] == "unknown" for r in sentence_records for n in r["occurrences"]),
            adjacent_paragraph_pairs=len(records) - len(sentence_records),
            removed_spans=len(spans),
            repair_spans=len(pending),
        ),
    )
