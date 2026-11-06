"""Surface solution substance; plans and legacy inference never become prose evidence."""

from collections import Counter, defaultdict
import re

# Stable families across genres, extended by explicit quote-grounded extracted nouns.
FAMILIES = {
    "阵": ("阵",),
    "钥匙": ("钥匙", "锁匙"),
    "账本": ("账本", "账册", "账簿"),
    "借剑": ("借剑", "借招"),
    "契约": ("契约", "契书"),
    "证词": ("证词", "口供"),
    "药": ("药", "丹丸", "丹药"),
    "符": ("符箓", "符纸", "符咒"),
}


def rows(value):
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def normalize_noun(noun):
    return next((key for key, names in FAMILIES.items() if any(n in noun for n in names)), noun.strip())


def solution_nouns(solution):
    explicit = [solution.get(k) for k in ("target_noun", "method_noun", "solution_target", "solution_method")]
    explicit = [n.strip() for n in explicit if isinstance(n, str) and 0 < len(n.strip()) <= 16]
    if explicit:
        return sorted({normalize_noun(n) for n in explicit})
    content = solution.get("content", "")
    return sorted(
        key for key, names in FAMILIES.items() if isinstance(content, str) and any(n in content for n in names)
    )


def observe_solutions(raw, text):
    result = dict(raw)
    result["solutions"] = []
    for row in rows(raw.get("solutions")):
        quote = row.get("quote")
        grounded = isinstance(quote, str) and bool(quote.strip()) and quote in text
        value = dict(row)
        for key in ("target_noun", "method_noun"):
            noun = row.get(key)
            value[key] = noun if grounded and isinstance(noun, str) and 0 < len(noun) <= 16 and noun in quote else None
        value["noun_status"] = (
            "Evidence" if grounded and any(value.get(k) for k in ("target_noun", "method_noun")) else "Unknown"
        )
        result["solutions"].append(value)
    return result


def chapter_nouns(row):
    explicit = solution_nouns({k: row.get(k) for k in ("solution_target", "solution_method")})
    if explicit:
        return explicit
    solutions = rows(row.get("solutions"))
    primary = [s for s in solutions if s.get("primary") is True]
    chosen = primary[:1] or solutions[:1]
    return sorted({noun for solution in chosen for noun in solution_nouns(solution)})


def stage_key(bible, number):
    return next(
        (
            s.get("number", i)
            for i, s in enumerate(rows(bible.get("volumes")), 1)
            if type(s.get("start")) is int and type(s.get("end")) is int and s["start"] <= number <= s["end"]
        ),
        None,
    )


def solution_context(bible, history, start):
    recent = [
        dict(chapter=r["number"], nouns=chapter_nouns(r)) for r in history if 0 < start - r.get("number", 0) <= 10
    ]
    stage = stage_key(bible, start)
    counts = Counter(
        n
        for r in history
        if stage is not None and stage_key(bible, r.get("number", 0)) == stage and r.get("number", 0) < start
        for n in chapter_nouns(r)
    )
    return dict(recent_ten=recent, stage_counts=dict(counts), stage_cap=3)


def solution_issues(bible, history, batch):
    combined = {r["number"]: r for r in [*history, *batch] if type(r.get("number")) is int}
    targets = {r["number"] for r in batch}
    counts, issues = defaultdict(list), []
    for number, row in sorted(combined.items()):
        stage = stage_key(bible, number)
        if stage is None:
            continue
        for noun in chapter_nouns(row):
            counts[stage, noun].append(number)
            if len(counts[stage, noun]) > 3 and number in targets:
                issues.append(
                    dict(
                        kind="solution_substance",
                        stage=stage,
                        noun=noun,
                        chapters=list(counts[stage, noun]),
                        targets=[number],
                    )
                )
    return issues
