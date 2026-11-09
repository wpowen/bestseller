"""Complete-sentence repetition findings shared by hygiene repair and checkup."""

from difflib import SequenceMatcher
import unicodedata

from .prose_repetition import _sentences as complete_sentences
from .prose_repetition import clean_repetitions


MIN_LENGTH = 15
NEAR_SIMILARITY = 0.92
SHORT_LINE_MAX = 9
MAX_SHORT_SENTENCE_DISTANCE = 4


def _normalize(value):
    value = unicodedata.normalize("NFKC", value)
    return "".join(char for char in value if not char.isspace() and unicodedata.category(char)[0] not in "PZC")


def _prefix_end(value, normalized):
    current = ""
    for offset, char in enumerate(value):
        current += _normalize(char)
        if len(current) < len(normalized):
            continue
        if current != normalized:
            return None
        end = offset + 1
        while end < len(value) and (value[end].isspace() or unicodedata.category(value[end])[0] in "PZC"):
            end += 1
        return end
    return None


def _short_exact_hits(text, spans, nodes):
    result = []
    paragraph_rows = []
    for paragraph, (start, end) in enumerate(spans, 1):
        normalized = _normalize(text[start:end])
        if not normalized or len(normalized) > SHORT_LINE_MAX:
            continue
        sentence = next((node["sentence_id"] for node in nodes if start <= node["start"] < end), None)
        if sentence is not None:
            paragraph_rows.append(
                dict(start=start, end=end, paragraph=paragraph, sentence=sentence, normalized=normalized)
            )
    paragraph_groups = {}
    for row in paragraph_rows:
        paragraph_groups.setdefault(row["normalized"], []).append(row)
    for normalized, occurrences in paragraph_groups.items():
        if len(occurrences) != 2:
            continue
        reference, current = occurrences
        if current["sentence"] - reference["sentence"] > MAX_SHORT_SENTENCE_DISTANCE:
            continue
        result.append(
            dict(
                kind="exact_short_sentence",
                normalized=normalized,
                reference_sentence=reference["sentence"],
                sentence=current["sentence"],
                reference_text=text[reference["start"] : reference["end"]],
                start=current["start"],
                end=current["end"],
                text=text[current["start"] : current["end"]],
                safe_delete=True,
                reasons=[],
            )
        )
    occupied = {(row["start"], row["end"]) for row in result}
    node_groups = {}
    for node in nodes:
        normalized = _normalize(node["text"])
        if normalized and len(normalized) < MIN_LENGTH:
            node_groups.setdefault(normalized, []).append(node)
    for normalized, occurrences in node_groups.items():
        if len(occurrences) != 2:
            continue
        reference, current = occurrences
        if current["sentence_id"] - reference["sentence_id"] > MAX_SHORT_SENTENCE_DISTANCE:
            continue
        if any(start <= current["start"] and current["end"] <= end for start, end in occupied):
            continue
        result.append(
            dict(
                kind="exact_short_sentence",
                normalized=normalized,
                reference_sentence=reference["sentence_id"],
                sentence=current["sentence_id"],
                reference_text=reference["text"],
                start=current["start"],
                end=current["end"],
                text=current["text"],
                safe_delete=True,
                reasons=[],
            )
        )
    return result


def sentence_hits(text, spans):
    nodes = complete_sentences(text, spans)
    actions = {}
    for record in clean_repetitions(text, remove_safe=False)["records"]:
        if record["kind"] != "sentence":
            continue
        for occurrence in record["occurrences"][1:]:
            actions[(occurrence["start"], occurrence["end"])] = occurrence
    result = _short_exact_hits(text, spans, nodes)
    for index in range(1, len(nodes) - 1):
        first, second = nodes[index], nodes[index + 1]
        if first.get("quoted") or second.get("quoted") or first["paragraph"] != second["paragraph"]:
            continue
        normalized = _normalize(first["text"]) + _normalize(second["text"])
        if len(normalized) < MIN_LENGTH:
            continue
        reference = next(
            (
                (left, right)
                for left, right in zip(nodes[:index], nodes[1:index])
                if not left.get("quoted")
                and not right.get("quoted")
                and left["paragraph"] == right["paragraph"]
                and _normalize(left["text"]) + _normalize(right["text"]) == normalized
            ),
            None,
        )
        if reference is None:
            continue
        action = actions.get((second["start"], second["end"]), {})
        result.append(
            dict(
                kind="sentence_sequence",
                normalized=normalized,
                reference_sentence=reference[0]["sentence_id"],
                sentence=first["sentence_id"],
                reference_text=text[reference[0]["start"] : reference[1]["end"]],
                start=first["start"],
                end=second["end"],
                text=text[first["start"] : second["end"]],
                safe_delete=action.get("action") != "unknown",
                reasons=action.get("reasons", []),
            )
        )
    occupied = {(row["start"], row["end"]) for row in result}
    groups = {}
    for node in nodes:
        normalized = _normalize(node["text"])
        if not node.get("quoted") and len(normalized) >= MIN_LENGTH:
            groups.setdefault(normalized, []).append(node)
    for normalized, occurrences in groups.items():
        if len(occurrences) < 2:
            continue
        reference = occurrences[0]
        for occurrence in occurrences[1:]:
            if any(start <= occurrence["start"] and occurrence["end"] <= end for start, end in occupied):
                continue
            action = actions.get((occurrence["start"], occurrence["end"]), {})
            result.append(
                dict(
                    kind="exact_sentence",
                    normalized=normalized,
                    reference_sentence=reference["sentence_id"],
                    sentence=occurrence["sentence_id"],
                    reference_text=reference["text"],
                    start=occurrence["start"],
                    end=occurrence["end"],
                    text=occurrence["text"],
                    safe_delete=action.get("action") == "reported",
                    reasons=action.get("reasons", []),
                )
            )
            occupied.add((occurrence["start"], occurrence["end"]))
    for index, current in enumerate(nodes):
        if current.get("quoted") or any(start <= current["start"] < end for start, end in occupied):
            continue
        current_normalized = _normalize(current["text"])
        candidates = []
        for earlier in nodes[:index]:
            earlier_normalized = _normalize(earlier["text"])
            if earlier.get("quoted") or min(len(earlier_normalized), len(current_normalized)) < MIN_LENGTH:
                continue
            length_ratio = min(len(earlier_normalized), len(current_normalized)) / max(
                len(earlier_normalized), len(current_normalized)
            )
            similarity = SequenceMatcher(None, earlier_normalized, current_normalized, autojunk=False).ratio()
            if length_ratio >= 0.8 and similarity >= NEAR_SIMILARITY:
                candidates.append((similarity, earlier))
        if candidates:
            similarity, earlier = max(candidates, key=lambda item: item[0])
            result.append(
                dict(
                    kind="near_sentence",
                    normalized=current_normalized,
                    reference_sentence=earlier["sentence_id"],
                    sentence=current["sentence_id"],
                    reference_text=earlier["text"],
                    start=current["start"],
                    end=current["end"],
                    text=current["text"],
                    similarity=round(similarity, 4),
                    safe_delete=False,
                    reasons=["near_match_may_contain_unique_content"],
                )
            )
            occupied.add((current["start"], current["end"]))
    for index, current in enumerate(nodes):
        if current.get("quoted") or any(start <= current["start"] < end for start, end in occupied):
            continue
        current_normalized = _normalize(current["text"])
        candidates = []
        for first, second in zip(nodes[:index], nodes[1:index]):
            if first.get("quoted") or second.get("quoted") or first["paragraph"] != second["paragraph"]:
                continue
            combined = _normalize(first["text"]) + _normalize(second["text"])
            if len(combined) < MIN_LENGTH or not current_normalized.startswith(combined):
                continue
            end = _prefix_end(current["text"], combined)
            if end is not None:
                candidates.append((len(combined), first, second, combined, current["start"] + end))
        if candidates:
            _, first, second, combined, end = max(candidates, key=lambda item: item[0])
            result.append(
                dict(
                    kind="fused_sentence",
                    normalized=combined,
                    reference_sentence=first["sentence_id"],
                    sentence=current["sentence_id"],
                    reference_text=text[first["start"] : second["end"]],
                    start=current["start"],
                    end=end,
                    text=text[current["start"] : end],
                    safe_delete=True,
                    reasons=[],
                )
            )
    result = [
        row
        for row in result
        if row["kind"] != "exact_short_sentence"
        or not any(
            other["kind"] != "exact_short_sentence" and other["start"] <= row["start"] and row["end"] <= other["end"]
            for other in result
        )
    ]
    return sorted(result, key=lambda row: (row["start"], row["end"], row["kind"]))
