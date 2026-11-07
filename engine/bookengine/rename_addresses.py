"""Carry name-derived addresses through a cast rename.

The cross-book name pool renames generated characters by full name and by the
two-character given name. A single-character given name (顾砚 -> 顾冬岚) is not
covered, so addresses built from it ("砚哥", "阿砺") survived the rename and
lost their link to the new name. Only the address labels and their explanation
are rewritten here; other prose is left untouched.
"""

from copy import deepcopy
import re


def _swaps(mapping):
    """Old given-name character -> new given-name character, per renamed person."""
    return {
        old[-1]: new[-1]
        for old, new in mapping.items()
        # Two-character names (one-character given name) are the only case the
        # full-name and given-name substitution cannot reach.
        if isinstance(old, str) and isinstance(new, str) and len(old) == 2 and new and old[-1] != new[-1]
    }


def _relabel(label, swaps):
    return "".join(swaps.get(ch, ch) for ch in label)


def _labels(address):
    return [part.strip() for part in re.split(r"[/／、，,；;]", address) if part.strip()]


def rename_addresses(bible, mapping):
    """Return a copy of ``bible`` whose name-derived addresses follow ``mapping``."""
    result = deepcopy(bible)
    swaps = _swaps(mapping or {})
    if not swaps:
        return result
    new_names = [n for n in mapping.values() if isinstance(n, str)]
    for card in result.get("characters", []):
        address = card.get("address")
        if not isinstance(address, str):
            continue
        changes = {
            label: _relabel(label, swaps)
            for label in _labels(address)
            if any(ch in swaps for ch in label)
            and _relabel(label, swaps) != label
            and not any(label in name for name in new_names)
        }
        if not changes:
            continue
        pattern = re.compile("|".join(re.escape(k) for k in sorted(changes, key=len, reverse=True)))
        card["address"] = pattern.sub(lambda m: changes[m[0]], address)
        origin = card.get("address_origin")
        if isinstance(origin, str):
            card["address_origin"] = pattern.sub(lambda m: changes[m[0]], origin)
    return result
