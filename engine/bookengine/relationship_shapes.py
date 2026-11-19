"""Instruction 68 (E4): read the relationship change in the shapes the planner actually returns.

Of 31 relationship chapters in five saved books, 14 had no usable contract: nine carried the content under
other keys (a "甲与乙" key, from/to/trigger, Chinese field names) and five were empty. Without a contract the
writer gets no relationship duty and the repair is skipped, which is how planned relationship chapters were
written as fights.
"""

import re

_ALIASES = {
    "people": ("people", "characters", "pair", "人物"),
    "cause": ("cause", "trigger", "turning_point", "起因"),
    "before": ("before", "from", "变化前", "前"),
    "after": ("after", "to", "变化后", "后"),
}
_SEPARATORS = ("与", "和", "、")  # in this order: 韩和笙 is one name, 秦野与韩和笙 is two


def _pair(key: str) -> list[str]:
    for separator in _SEPARATORS:
        parts = [part.strip() for part in key.split(separator)]
        if len(parts) == 2 and all(parts):
            return parts
    return []


def _from_to(text: str) -> dict | str:
    """Read “由甲，转为乙” / “从甲到乙” written as one sentence (instruction 77c)."""
    match = re.match(r"^\s*[由从](.+?)[，,]?\s*(?:转为|转成|变为|变成|到)(.+?)[。.]?\s*$", text)
    return dict(before=match.group(1).strip(), after=match.group(2).strip()) if match else text


def _first(value: dict, keys: tuple[str, ...]):
    return next((value[key] for key in keys if value.get(key)), None)


def canonical_relationship_change(value: object) -> object:
    """Return people/cause/before/after when they can be read; otherwise the value unchanged."""
    if not isinstance(value, dict) or not value:
        return value
    people = None
    if not any(key in value for keys in _ALIASES.values() for key in keys):
        for key, inner in value.items():
            names = _pair(key) if isinstance(key, str) else []
            inner = _from_to(inner) if isinstance(inner, str) else inner
            if isinstance(inner, dict) and len(names) == 2:
                people, value = names, inner
                break
        else:
            return value
    result = {field: _first(value, keys) for field, keys in _ALIASES.items()}
    result["people"] = people or result["people"]
    extras = {key: item for key, item in value.items() if not any(key in keys for keys in _ALIASES.values())}
    return {**extras, **{field: item for field, item in result.items() if item}}
