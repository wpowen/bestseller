"""Separate declared powers from ordinary tools without guessing target classes."""

import re


def ability_terms(bible):
    power = bible.get("power")
    if not isinstance(power, dict):
        return set()
    terms = {power[k].strip() for k in ("name", "object") if isinstance(power.get(k), str) and power[k].strip()}
    for key in ("abilities", "subabilities", "skills"):
        values = power.get(key, [])
        if isinstance(values, list):
            terms.update(v.strip() for v in values if isinstance(v, str) and 1 < len(v.strip()) <= 16)
    for value in power.values():
        if not isinstance(value, str):
            continue
        for match in re.finditer(r"能力[：:]([^；;。\n]+)", value):
            terms.update(v.strip() for v in re.split(r"[、，,或及与]", match[1]) if 1 < len(v.strip()) <= 16)
    return terms


def ability_combination(bible, noun):
    terms = ability_terms(bible)
    if noun in terms:
        return (noun,)
    parts = [p.strip() for p in re.split(r"[、，,+/与及]", noun)]
    return tuple(sorted(set(parts))) if parts and all(p in terms for p in parts) else ()


def partition_methods(bible, nouns):
    ordinary, abilities = [], []
    for noun in nouns:
        combo = ability_combination(bible, noun)
        if combo:
            abilities.extend(combo)
        else:
            ordinary.append(noun)
    return ordinary, tuple(sorted(set(abilities)))


def primary_target(row):
    solutions = row.get("solutions")
    if isinstance(solutions, list):
        candidates = [s for s in solutions if isinstance(s, dict)]
        primary = next((s for s in candidates if s.get("primary") is True), next(iter(candidates), {}))
        return primary.get("target_noun")
    return row.get("solution_target")


def target_class(noun):
    """Surface object kinds, not guessed identity or geography; unknown kinds keep their literal noun."""
    if not isinstance(noun, str) or not noun.strip():
        return None
    noun = noun.strip()
    for kind, endings in (
        ("门", ("门", "门板")),
        ("石", ("石", "石块")),
        ("墙", ("墙", "墙壁")),
        ("绳", ("绳", "绳索")),
        ("锁", ("锁", "锁链")),
        ("阵", ("阵", "阵眼", "阵钉")),
    ):
        if noun.endswith(endings):
            return kind
    return noun
