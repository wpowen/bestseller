"""Character identity resolution — alias-aware name matching.

Prevents the "王守真 / 王守真(三叔) / 三叔 become 3 separate characters" failure
mode by providing a single resolver that all character-upsert sites can use
to find an existing entry before creating a duplicate.

Match order (first hit wins):
    1. Exact name match
    2. Candidate name appears in an existing entry's aliases list
    3. Any candidate alias equals an existing entry's canonical name
    4. canonical_character_key() of both names are equal AND non-empty

``canonical_character_key`` strips trailing parenthetical notes
(``王守真（三叔）`` → ``王守真``), slash/pipe suffix tags (``王守真/三叔``
→ ``王守真``), and surrounding whitespace. It does **not** do prefix or fuzzy
matching — "张三" and "张三丰" remain distinct.

The resolver never mutates inputs. ``merge_character_with_aliases`` returns a
new dict that folds incoming name + aliases into the existing entry's alias
list (deduplicated, preserving insertion order).
"""

from __future__ import annotations

import copy
import re
from typing import Any, Iterable, Mapping

__all__ = [
    "canonical_character_key",
    "collect_entry_aliases",
    "resolve_character_match",
    "merge_character_with_aliases",
]


# Matches a trailing parenthetical group — both ASCII and CJK fullwidth.
_TRAILING_PAREN_RE = re.compile(r"[（(][^）)]*[）)]\s*$")

# Any of these characters split an inline suffix note (``王守真/三叔`` →
# ``王守真``). We only strip the first split onwards, never mid-name.
_SUFFIX_DELIMITERS = ("/", "／", "|", "｜", "·", "・")


def canonical_character_key(name: Any) -> str:
    """Normalize a character name for identity comparison.

    Returns an empty string for non-string / empty input.
    """
    if not isinstance(name, str):
        return ""
    stripped = name.strip()
    if not stripped:
        return ""
    # Peel trailing parentheticals like ``王守真（三叔）`` → ``王守真``.
    # Repeat in case of nested or chained parens.
    while True:
        new_stripped = _TRAILING_PAREN_RE.sub("", stripped).strip()
        if new_stripped == stripped:
            break
        stripped = new_stripped
    # Split on suffix delimiters — keep only the head.
    for delim in _SUFFIX_DELIMITERS:
        if delim in stripped:
            stripped = stripped.split(delim, 1)[0].strip()
    return stripped


def collect_entry_aliases(entry: Mapping[str, Any] | None) -> list[str]:
    """Return the alias list declared on a character dict.

    Accepts either a list-of-strings or a single-string alias field; always
    returns a de-duplicated list of stripped strings. Silent on bad types.
    """
    if not isinstance(entry, Mapping):
        return []
    raw = entry.get("aliases")
    collected: list[str] = []
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped:
            collected.append(stripped)
    elif isinstance(raw, Iterable):
        for item in raw:
            if isinstance(item, str):
                trimmed = item.strip()
                if trimmed and trimmed not in collected:
                    collected.append(trimmed)
    return collected


def _extract_name(entry: Mapping[str, Any] | None) -> str:
    if not isinstance(entry, Mapping):
        return ""
    raw = entry.get("name")
    if isinstance(raw, str):
        return raw.strip()
    return ""


def resolve_character_match(
    candidate: Mapping[str, Any],
    registry: Mapping[str, Mapping[str, Any]],
) -> str | None:
    """Return the registry key of the matching character, or ``None``.

    ``registry`` maps canonical name → character dict. Match order is
    documented in the module docstring.
    """
    if not isinstance(candidate, Mapping) or not isinstance(registry, Mapping):
        return None
    cand_name = _extract_name(candidate)
    if not cand_name:
        return None

    # 1. Exact name match.
    if cand_name in registry:
        return cand_name

    cand_aliases = collect_entry_aliases(candidate)
    cand_canonical = canonical_character_key(cand_name)

    for existing_key, existing_entry in registry.items():
        if not existing_key:
            continue
        existing_aliases = collect_entry_aliases(existing_entry)
        # 2. Candidate name appears in existing aliases.
        if cand_name in existing_aliases:
            return existing_key
        # 3. Any candidate alias equals existing name.
        if existing_key in cand_aliases:
            return existing_key
        # 4. Canonical keys match (strips parens / slashes).
        if cand_canonical and cand_canonical == canonical_character_key(existing_key):
            return existing_key
        # Also cover the reverse: existing entry was registered under a raw
        # name while candidate is the canonical form.
        for alias in existing_aliases:
            if cand_canonical and cand_canonical == canonical_character_key(alias):
                return existing_key
    return None


def merge_character_with_aliases(
    existing: Mapping[str, Any],
    incoming: Mapping[str, Any],
) -> dict[str, Any]:
    """Fold ``incoming`` into ``existing``, preserving existing name + adding aliases.

    * Existing ``name`` and ``role`` are preserved verbatim.
    * Incoming ``name`` and its aliases are appended to the alias list
      (deduplicated, order preserving).
    * Any non-empty field in ``incoming`` that is absent / empty on
      ``existing`` is copied over.
    """
    if not isinstance(existing, Mapping):
        raise TypeError("existing must be a mapping")
    if not isinstance(incoming, Mapping):
        return copy.deepcopy(dict(existing))

    merged = copy.deepcopy(dict(existing))
    existing_name = _extract_name(merged)
    alias_list = list(collect_entry_aliases(merged))

    incoming_name = _extract_name(incoming)
    incoming_aliases = collect_entry_aliases(incoming)
    for alias_candidate in [incoming_name, *incoming_aliases]:
        alias_candidate = alias_candidate.strip() if isinstance(alias_candidate, str) else ""
        if not alias_candidate:
            continue
        if alias_candidate == existing_name:
            continue
        if alias_candidate not in alias_list:
            alias_list.append(alias_candidate)

    for key, value in incoming.items():
        if key in ("name", "aliases"):
            continue
        if value in (None, "", [], {}):
            continue
        current = merged.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            merged[key] = _merge_mapping(current, value)
        elif current in (None, "", [], {}):
            merged[key] = copy.deepcopy(value)
        # else: keep existing value — existing characters are authoritative.

    if alias_list:
        merged["aliases"] = alias_list

    return merged


def _merge_mapping(base: dict[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    """Shallow-recursive merge: incoming only fills holes in base."""
    merged = copy.deepcopy(base)
    for key, value in incoming.items():
        if value in (None, "", [], {}):
            continue
        current = merged.get(key)
        if isinstance(value, Mapping) and isinstance(current, Mapping):
            merged[key] = _merge_mapping(dict(current), value)
        elif current in (None, "", [], {}):
            merged[key] = copy.deepcopy(value)
    return merged
