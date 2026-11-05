"""Pure S4 memory normalization and bounded context; no model or storage access."""

from collections.abc import Mapping
import re

MAX_CONTEXT_CHARS = 30_000
_ALIASES = {
    "characters": ("who_is_where", "people", "characters"),
    "objects": ("items", "objects"),
    "clock": ("time", "clock"),
    "foreshadows": ("unresolved", "foreshadowing", "foreshadows"),
    "relationships": ("relations", "relationships"),
}
_END = re.compile(r"\n\s*\n|[。！？!?]|\.(?=\s|$)")


def _string(value):
    return value.strip() if isinstance(value, str) else ""


def place_only(value, owner=""):
    """Keep the stated place/container; discard appended action/condition notes."""
    owner = _string(owner)
    value = re.split(r"[，,；;。]", _string(value), maxsplit=1)[0].strip()
    if owner and value.startswith(owner) and re.search(r"贴身(?:保管|保存|携带)", value):
        return owner + "随身"
    return value


def _field(value, *keys):
    return next((value[k] for k in keys if k in value), None)


def _records(value):
    if isinstance(value, Mapping):
        if "name" in value and isinstance(value["name"], str):
            yield value["name"], value
        else:
            yield from value.items()
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, Mapping):
                name = _field(item, "name", "who", "character", "person", "object", "item")
                if isinstance(name, str):
                    yield name, item
                else:
                    yield from item.items()


def _entities(target, value, character):
    for name, record in _records(value):
        name = _string(name)
        if not name:
            continue
        if isinstance(record, str):
            record = {"location": record}
        if not isinstance(record, Mapping):
            continue
        patch = {}
        place = _field(record, "location", "where", "place")
        if _string(place):
            patch["location"] = place_only(place, record.get("owner", record.get("holder", "")))
        if character:
            present = _field(record, "present", "is_present")
            if type(present) is bool:
                patch["present"] = present
        else:
            owner = _field(record, "owner", "holder")
            if isinstance(owner, str):
                patch["owner"] = owner.strip()
        if patch:
            base = {"location": None, "present": None} if character else {"location": None}
            target[name] = dict(base, **target.get(name, {}))
            target[name].update(patch)


def _clock(value, previous):
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return previous
    result = dict(previous or {})
    for key, aliases in (("day", ("day",)), ("minute", ("minute", "minutes"))):
        number = _field(value, *aliases)
        if type(number) is int and number >= 0 and (key == "day" or number < 1440):
            result[key] = number
    return result if len(result) == 2 else previous


def _foreshadows(target, value, unresolved=False):
    if unresolved and isinstance(value, (list, tuple)):
        value = [{"name": item, "status": "open"} if isinstance(item, str) else item for item in value]
    for name, record in _records(value):
        status = _field(record, "status", "state") if isinstance(record, Mapping) else record
        if _string(name) and status in ("open", "closed"):
            target[name.strip()] = status


def _relationships(value):
    if isinstance(value, str):
        value = [value]
    elif isinstance(value, Mapping):
        value = [dict(pair=name, status=status) for name, status in value.items()]
    if not isinstance(value, (list, tuple)):
        return None
    result = []
    for item in value:
        if isinstance(item, Mapping):
            status = _string(_field(item, "status", "state", "relationship", "description"))
            pair = _field(item, "pair", "characters", "name")
            if isinstance(pair, (list, tuple)):
                pair = " / ".join(p for p in pair if isinstance(p, str) and p.strip())
            item = f"{pair}：{status}" if _string(pair) and status else status
        sentence = _string(item)
        boundary = _END.search(sentence)
        sentence = sentence[: boundary.end()].strip() if boundary else sentence
        if sentence and sentence not in result:
            result.append(sentence)
    return result if result or not value else None


def compact_state(value, previous=None):
    """Return only characters, objects, clock, foreshadows and relationships.

    Accept mappings or ordered lists of mapping deltas. Entity collections accept
    name-keyed maps, named records, or lists of either; location/where/place,
    present/is_present and owner/holder are aliases. Unknown location/presence is
    None, never inferred. Only explicit booleans and valid integer clocks count.
    Missing/invalid fields preserve previous facts; clock=None clears the clock.
    Supplied relationships replace the current snapshot (empty list clears it),
    each as one sentence. Other entity collections merge by name; empty means no
    delta. Summary, quote, paragraph and all other metadata are discarded.
    Inputs are never mutated; this normalizer does not verify narrative evidence.
    """
    state = dict(characters={}, objects={}, clock=None, foreshadows={}, relationships=[])
    for source in (previous, value):
        fragments = source if isinstance(source, (list, tuple)) else [source]
        for fragment in fragments:
            if not isinstance(fragment, Mapping):
                continue
            for key, aliases in _ALIASES.items():
                for alias in aliases:
                    if alias not in fragment:
                        continue
                    data = fragment[alias]
                    if key in ("characters", "objects"):
                        _entities(state[key], data, key == "characters")
                    elif key == "clock":
                        state[key] = _clock(data, state[key])
                    elif key == "foreshadows":
                        _foreshadows(state[key], data, alias == "unresolved")
                    else:
                        relationships = _relationships(data)
                        if relationships is not None:
                            state[key] = relationships
    return state


def extract_summary(value):
    """Read summary/chapter_summary separately; return '' for absent/invalid data."""
    if not isinstance(value, Mapping):
        return ""
    return _string(_field(value, "summary", "chapter_summary"))


def long_context(sections, budget=MAX_CONTEXT_CHARS):
    """Pack ordered (heading, text) pairs into at most min(budget, 30000) chars.

    Caller orders: chapter plan > previous ending > relevant characters > latest
    three summaries > volume plan > others, and subtracts request overhead from
    budget first. Headings and separators count. The first oversized section ends
    packing at a sentence/paragraph boundary, falling back to a hard prefix cut;
    there is no required-section overflow error or truncation marker. Invalid
    budgets return ''; malformed sections are skipped. No automatic reordering.
    """
    if type(budget) is not int or budget <= 0:
        return ""
    budget = min(budget, MAX_CONTEXT_CHARS)
    if isinstance(sections, Mapping):
        sections = sections.items()
    try:
        sections = iter(sections)
    except TypeError:
        return ""
    packed = ""
    for section in sections:
        if not isinstance(section, (list, tuple)) or len(section) != 2:
            continue
        heading, body = section
        if not isinstance(heading, str) or not isinstance(body, str) or not body.strip():
            continue
        prefix = ("\n\n" if packed else "") + (f"## {heading}\n" if heading else "")
        block = prefix + body
        available = budget - len(packed)
        if len(block) <= available:
            packed += block
            continue
        cut = block[:available]
        boundaries = [m.end() for m in _END.finditer(cut) if m.end() > len(prefix)]
        return packed + (cut[: boundaries[-1]] if boundaries else cut)
    return packed
