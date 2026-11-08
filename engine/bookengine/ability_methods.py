"""Separate declared powers from ordinary tools without guessing target classes."""

import re


_ABILITY_ENDING = re.compile(r"(?:感知|身法|力量|蛮力|之力)$")


def _declared_texts(bible):
    power = bible.get("power")
    if isinstance(power, dict):
        for value in power.values():
            if isinstance(value, str):
                yield value
            elif isinstance(value, list):
                yield from (item for item in value if isinstance(item, str))
    volumes = bible.get("volumes")
    if not isinstance(volumes, list):
        return
    for volume in volumes:
        development = volume.get("core_development") if isinstance(volume, dict) else None
        if isinstance(development, dict):
            yield from (value for value in development.values() if isinstance(value, str))


def _listed_terms(text):
    terms = {"借力"} if "借力" in text else set()
    lists = []
    for pattern in (r"借用([^。；]{2,40}?)中的一项", r"一项能力[：:]([^。；]+)"):
        lists.extend(match[1] for match in re.finditer(pattern, text))
    for value in lists:
        for item in re.split(r"[、，,或与及/]", value):
            item = re.sub(r"^(?:只能|最多|可|雪耳的|霜砚秋的)", "", item.strip())
            if 1 < len(item) <= 8 and _ABILITY_ENDING.search(item):
                terms.add(item)
    terms.update(match[1] for match in re.finditer(r"借(?:用)?[^，。；]{0,8}?的([\u4e00-\u9fff]{1,6}之力)", text))
    return terms


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
    for text in _declared_texts(bible):
        terms.update(_listed_terms(text))
    return terms


def ability_combination(bible, noun):
    terms = ability_terms(bible)
    if noun in terms:
        return (noun,)
    parts = [p.strip() for p in re.split(r"[、，,+/与及]", noun)]
    return tuple(sorted(set(parts))) if parts and all(p in terms for p in parts) else ()


def normalized_ability_term(bible, reported, action):
    if not isinstance(reported, str) or not isinstance(action, str):
        return None
    terms = ability_terms(bible)
    specific = sorted(
        (term for term in terms if term in action and term != reported), key=lambda term: (-len(term), term)
    )
    if reported in terms and reported in action:
        if reported == "力量":
            return next((term for term in specific if term.endswith("之力")), reported)
        if reported == "借力":
            return next((term for term in specific if term != "力量"), reported)
        return reported
    if reported == "力量":
        return next((term for term in specific if term.endswith("之力")), None)
    if reported == "借力":
        return next((term for term in specific if term != "力量"), None)
    return None


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
