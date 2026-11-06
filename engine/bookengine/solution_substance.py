"""Surface solution substance; plans and legacy inference never become prose evidence."""

from collections import Counter, defaultdict
from .quote_text import literal_quote
from .ability_methods import partition_methods, primary_target, ability_combination, target_class

# Stable families across genres, extended by explicit quote-grounded extracted nouns.
FAMILIES = {
    "阵": (
        "阵眼",
        "阵钉",
        "阵纹",
        "阵枢",
        "阵盘",
        "阵势",
        "阵法",
        "法阵",
        "大阵",
        "剑阵",
        "杀阵",
        "军阵",
        "破阵",
        "拆阵",
        "敌阵",
        "旧阵",
        "外阵",
        "内阵",
        "阵心",
        "阵脚",
        "阵册",
        "阵门",
        "阵台",
        "阵网",
    ),
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
    return next(
        (key for key, names in FAMILIES.items() if noun.strip() == key or any(n in noun for n in names)), noun.strip()
    )


def solution_nouns(solution, *, include_target=True):
    fields = (
        ("target_noun", "method_noun", "solution_target", "solution_method")
        if include_target
        else ("method_noun", "solution_method")
    )
    explicit = [solution.get(k) for k in fields]
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
        quote = literal_quote(text, row.get("quote"))
        grounded = isinstance(quote, str) and bool(quote.strip()) and quote in text
        value = dict(row)
        if quote is not None:
            value["quote"] = quote
        for key in ("target_noun", "method_noun"):
            noun = row.get(key)
            value[key] = noun if grounded and isinstance(noun, str) and 0 < len(noun) <= 16 and noun in quote else None
        value["noun_status"] = (
            "Evidence" if grounded and any(value.get(k) for k in ("target_noun", "method_noun")) else "Unknown"
        )
        result["solutions"].append(value)
    return result


def _method_nouns(solution, bible):
    for key in ("method_noun", "solution_method"):
        noun = solution.get(key)
        if isinstance(noun, str) and ability_combination(bible or {}, noun):
            return [noun]
    return solution_nouns(solution, include_target=False)


def chapter_nouns(row, bible=None):
    # Completed body observations supersede their old plan, including an Unknown observation.
    if row.get("fact_source") != "final_body" and "solutions" not in row:
        return _method_nouns({"solution_method": row.get("solution_method")}, bible)
    solutions = rows(row.get("solutions"))
    primary = [s for s in solutions if s.get("primary") is True]
    chosen = primary[:1] or solutions[:1]
    return sorted({noun for solution in chosen for noun in _method_nouns(solution, bible)})


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
        dict(chapter=r["number"], nouns=chapter_nouns(r, bible))
        for r in history
        if 0 < start - r.get("number", 0) <= 10
    ]
    stage = stage_key(bible, start)
    counts = Counter(
        n
        for r in history
        if stage is not None and stage_key(bible, r.get("number", 0)) == stage and r.get("number", 0) < start
        for n in partition_methods(bible, chapter_nouns(r, bible))[0]
    )
    ability_counts = Counter(
        (target_class(primary_target(r)), partition_methods(bible, chapter_nouns(r, bible))[1])
        for r in history
        if stage is not None
        and stage_key(bible, r.get("number", 0)) == stage
        and r.get("number", 0) < start
        and primary_target(r)
        and partition_methods(bible, chapter_nouns(r, bible))[1]
    )
    return dict(
        recent_ten=recent,
        stage_counts=dict(counts),
        stage_cap=3,
        ability_cap=2,
        ability_counts=[dict(target_class=t, combination=list(c), count=n) for (t, c), n in ability_counts.items()],
    )


def solution_issues(bible, history, batch):
    combined = {r["number"]: r for r in [*history, *batch] if type(r.get("number")) is int}
    targets = {r["number"] for r in batch}
    counts, ability_counts, issues = defaultdict(list), defaultdict(list), []
    for number, row in sorted(combined.items()):
        stage = stage_key(bible, number)
        if stage is None:
            continue
        nouns, combo = partition_methods(bible, chapter_nouns(row, bible))
        target = target_class(primary_target(row))
        if combo and target:
            used = ability_counts[stage, target, combo]
            used.append(number)
            if len(used) > 2 and number in targets:
                issues.append(
                    dict(
                        kind="ability_repetition",
                        stage=stage,
                        target_class=target,
                        combination=list(combo),
                        chapters=list(used),
                        targets=[number],
                    )
                )
        if (
            bible.get("plot_contract_version")
            and number in targets
            and (not row.get("solution_method") or not row.get("solution_target"))
        ):
            issues.append(
                dict(
                    kind="solution_substance_missing",
                    targets=[number],
                    requirement="分别补solution_target作用对象与solution_method手段核心名词，作用对象不占手段次数",
                )
            )
        for noun in nouns:
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
