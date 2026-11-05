"""Pure, conservative INBOX35 repetition checks, shared by generation and checkup.

Call ``clean_repetitions(text)`` after prose cleaning and before extraction; use
``remove_safe=False`` for checkup. Nothing reads files, calls models or mutates
input. All positions refer to the ORIGINAL input: zero-based half-open character
offsets, one-based paragraph/line/sentence IDs (paragraph hits have no sentence ID).
Re-run after any edit before passing positions to another repair/extraction round.
Sentence IDs count this module's complete sentences, including isolated speech;
map by span to precision._sentences IDs when using the existing amend protocol.

Normalization is NFKC with whitespace/punctuation ignored; numeric signs and
decimal points survive. Only complete sentences with >=10 normalized characters
are grouped. Adjacent paragraphs with >=10 characters and SequenceMatcher ratio
>=0.85 are reported. Similar paragraphs with unique content are never deleted.
Dependency checks are conservative lexical hints, not a semantic safety proof.
``needs_repair`` supplies original sentence IDs/spans for the existing amend loop.
"""

from collections import defaultdict
from difflib import SequenceMatcher
import re
import unicodedata

from .text import _paragraph_spans


_OPEN = {"“": "”", "「": "」", "『": "』", "‘": "’", '"': '"', "'": "'"}
_CLOSE = set(_OPEN.values())
_REFERENCE = re.compile(
    r"[他她它您你我咱俺]|自己|对方|彼此|前者|后者|此人|那人|这人|"
    r"[这那此其](?:个|些|位|名|件|枚|把|柄|条|种|一|句话|声音|动作|身影|东西|里|儿|边|处)|"
    r"刚才|方才|上述|因此|于是|所以|随即|随后|接着|继而|仍然|依旧|又一次"
)
# An omitted subject is unsafe even without an explicit pronoun. These common
# action/connective openings are hints; uncertainty is handed to model repair.
_ELIDED = re.compile(
    r"^(?:抬|低|垂|转|回|侧|点|摇|伸|收|抽|拔|握|抓|拿|取|放|推|拉|踢|踹|走|跑|冲|追|退|躲|避|"
    r"望|看|听|笑|哭|喊|问|答|说|骂|咬|攥|摸|按|捂|抱|翻|掀|掷|跳|跃|扑|蹲|站|坐|起身|"
    r"然后|而后|却|便|就|才|又|也|还|仍|只|正|不禁|立刻|马上|终于|继续)"
)
_ATTRIBUTION = re.compile(
    r"^[^。！？!?\r\n]{0,16}(?:说道?|问道?|答道?|喊道?|低声道|轻声道|提醒|嘀咕|嘟囔)[，,。！？!?]"
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
    for paragraph, (left, right) in enumerate(paragraphs, 1):
        start, i, quotes = left, left, []
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


def _dependency_reasons(text, occurrence, sentences, paragraphs):
    start, end = occurrence["start"], occurrence["end"]
    reasons = []
    neighbors = [node for node in sentences if node["end"] <= start or node["start"] >= end]
    before = next((node for node in reversed(neighbors) if node["end"] <= start), None)
    after = next((node for node in neighbors if node["start"] >= end), None)
    # Also inspect unfinished fragments: they have no sentence ID but may still
    # depend on the deleted sentence, or contain an attribution before speech.
    before_gap = text[before["end"] if before else 0 : start].strip()
    after_gap = text[end : after["start"] if after else len(text)].strip()
    context = [node["text"] for node in (occurrence, before, after) if node] + [before_gap, after_gap]
    if any(_REFERENCE.search(value) for value in context):
        reasons.append("reference_dependency")
    if after and _ELIDED.search(_normalize(after["text"])) or _ELIDED.search(_normalize(after_gap)):
        reasons.append("subject_dependency")
    if _ELIDED.search(_normalize(occurrence["text"])):
        reasons.append("subject_dependency")
    # Removing a spoken sentence must not strand its attribution or slice an
    # ongoing quotation. Keep these for the existing extract/amend round.
    paragraph_start, paragraph_end = paragraphs[occurrence["paragraph"] - 1]
    prefix = text[paragraph_start:start].rstrip()
    if prefix.endswith(("：", ":")) or occurrence.get("quoted") and before_gap:
        reasons.append("dialogue_attribution")
    if occurrence.get("whole_quote") and _ATTRIBUTION.search(text[end:paragraph_end].lstrip()):
        reasons.append("dialogue_attribution")
    if occurrence.get("quoted") and not occurrence.get("whole_quote"):
        reasons.append("dialogue_context")
    if end < len(text) and text[end] in _CLOSE:
        reasons.append("unbalanced_quote")
    return list(dict.fromkeys(reasons))


def clean_repetitions(text, *, remove_safe=True):
    """Return cleaned/records/removed/needs_repair/counts, all JSON serializable.

    Each record has kind, normalized, count, repetitions and occurrences. Sentence
    counts are groups and excess occurrences; paragraph counts are adjacent pairs,
    so they must NOT be added to sentence counts (the same text can hit both).
    Actions: reference (first), removed, repair (kept for amend), reported (safe
    candidate in detection-only mode). ``removed`` contains disjoint original
    spans only; every character outside those spans is preserved verbatim.
    """
    paragraphs = _paragraph_spans(text)
    sentences = _sentences(text, paragraphs)
    groups = defaultdict(list)
    for node in sentences:
        key = _normalize(node["text"])
        if len(key) >= 10:
            groups[key].append(node)
    records, candidates = [], []

    def decide(node, unique=False):
        node = dict(node)
        reasons = ["unique_content"] if unique else _dependency_reasons(text, node, sentences, paragraphs)
        node["reasons"] = reasons
        node["action"] = "repair" if reasons else "removed" if remove_safe else "reported"
        if node["action"] == "removed":
            candidates.append((node["start"], node["end"]))
        return node

    for key, nodes in groups.items():
        if len(nodes) > 1:
            records.append(
                dict(
                    kind="sentence",
                    normalized=key,
                    count=len(nodes),
                    repetitions=len(nodes) - 1,
                    occurrences=[dict(nodes[0])] + [decide(node) for node in nodes[1:]],
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
                        decide(_position(text, *b, index + 1), unique=first != second),
                    ],
                )
            )

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
            adjacent_paragraph_pairs=len(records) - len(sentence_records),
            removed_spans=len(spans),
            repair_spans=len(pending),
        ),
    )
