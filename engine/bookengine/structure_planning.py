"""Deterministic stage boundaries, opponent contracts and bounded design repair."""

from copy import deepcopy

from .q1_schema import defaults, integer, rows, DATA
from .storage import save


def stage_ranges(total):
    """Cover new books in 10–12 chapters; an indivisible short tail is allowed."""
    total = max(1, integer(total, 1))
    count = (total + 11) // 12
    size, remainder = divmod(total, count)
    lengths = [size + (n < remainder) for n in range(count)]
    if size < 10 and count > 1:
        lengths = [12] * (count - 1) + [total - 12 * (count - 1)]
    start, result = 1, []
    for number, length in enumerate(lengths, 1):
        result.append(dict(number=number, start=start, end=start + length - 1))
        start += length
    return result


def stage_by_number(bible, number, total):
    """Return a saved boundary, or legacy 25-chapter fallback without migrating it."""
    found = next((s for s in rows(bible.get("volumes")) if integer(s.get("number")) == number), {})
    if integer(found.get("start")) > 0 and integer(found.get("end")) >= integer(found.get("start")):
        return dict(found)
    ranges = stage_ranges(total) if bible.get("structure_version") else []
    fallback = next(
        (s for s in ranges if s["number"] == number),
        dict(number=number, start=(number - 1) * 25 + 1, end=min(number * 25, total)),
    )
    return dict(found, **fallback)


def stage_for_chapter(bible, chapter, total=None):
    """Locate a chapter for scheduling/extraction; saved old books retain their ranges."""
    total = integer(total, max([integer(s.get("end")) for s in rows(bible.get("volumes"))] + [chapter]))
    for stage in rows(bible.get("volumes")):
        if integer(stage.get("start")) <= chapter <= integer(stage.get("end")):
            return dict(stage)
    if bible.get("structure_version"):
        return next(s for s in stage_ranges(total) if s["start"] <= chapter <= s["end"])
    return stage_by_number(bible, (chapter - 1) // 25 + 1, total)


def final_stage_start(bible, total):
    return stage_for_chapter(bible, total, total)["start"]


def structure_issues(bible, total):
    """Check supplied planning, never promote plans to observed story facts."""
    stages, opponents = rows(bible.get("volumes")), rows(bible.get("antagonists"))
    issues, previous, boundary = [], "", 0
    if not stages or (total >= 30 and len(stages) < (total + 11) // 12):
        issues.append(f"volumes: {total}章至少需要{(total + 11) // 12}个阶段")
    by_stage, by_name = {}, {}
    for n, stage in enumerate(stages, 1):
        start, end = integer(stage.get("start")), integer(stage.get("end"))
        by_stage[n] = stage
        if integer(stage.get("number")) != n or start != boundary + 1 or end < start or end > total:
            issues.append(f"stage {n}: start/end必须连续覆盖1–{total}")
        length = end - start + 1
        if length > 12 or length < 1 or (n < len(stages) and length < 10):
            issues.append(f"stage {n}: length应为10–12，仅末尾短阶段可不足10")
        for field in ("location", "antagonist", "goal", "climax"):
            if not isinstance(stage.get(field), str) or not stage[field].strip():
                issues.append(f"stage {n}: {field}缺失")
        location = str(stage.get("location", "")).strip()
        if location and location == previous:
            issues.append(f"stage {n}: location与上一阶段相同")
        if integer(stage.get("exit_chapter")) != end:
            issues.append(f"stage {n}: exit_chapter必须为{end}")
        previous, boundary = location, end
    if boundary != total:
        issues.append(f"volumes: end必须为{total}")
    for opponent in opponents:
        name = opponent.get("name")
        if not isinstance(name, str) or not name.strip() or name in by_name:
            issues.append("antagonists: name必须非空且唯一")
            continue
        by_name[name] = opponent
        tier, number, exit_at = (integer(opponent.get(k)) for k in ("tier", "stage", "exit"))
        stage = by_stage.get(number, {})
        if tier < 1:
            issues.append(f"antagonist {name}: tier必须为正整数")
        if not stage:
            issues.append(f"antagonist {name}: stage不存在")
        elif not integer(stage.get("start")) <= exit_at <= integer(stage.get("end")):
            issues.append(f"antagonist {name}: exit必须在stage {number}内，之后仅作背景提及")
        for field in ("start", "first_chapter", "last_chapter"):
            if field in opponent and stage and not integer(stage.get("start")) <= integer(opponent[field]) <= exit_at:
                issues.append(f"antagonist {name}: {field}超出stage/exit出场范围")
    for n, stage in by_stage.items():
        name = stage.get("antagonist")
        if not isinstance(name, str) or name not in by_name or integer(by_name[name].get("stage")) != n:
            issues.append(f"stage {n}: antagonist缺分级或被安排跨阶段出场")
    return issues


def repair_structure(bible, threads, total, ask, out):
    """Regenerate structural fields at most three times, recording a nonfatal failure."""
    current, lines = deepcopy(bible), deepcopy(threads)
    attempts = []
    for retry in range(4):
        issues = structure_issues(current, total)
        attempts.append(dict(regeneration=retry, issues=issues))
        save(
            out / "structure-planning.json",
            dict(
                status="passed" if not issues else "failed_continued" if retry == 3 else "repairing",
                attempts=attempts,
                issues=issues,
            ),
        )
        if not issues or retry == 3:
            break
        raw = ask(
            "structure",
            "structure",
            [
                ("指定阶段边界", stage_ranges(total)),
                ("逐项错误清单，只重生volumes/antagonists/threads，不改人物和用户锁定事实", issues),
                ("当前结构", current),
            ],
            retry + 1,
        )
        patch = raw.get("bible", raw) if isinstance(raw, dict) else {}
        if isinstance(patch, dict):
            for key in ("volumes", "antagonists"):
                if isinstance(patch.get(key), list):
                    current[key] = deepcopy(patch[key])
        if isinstance(raw, dict) and isinstance(raw.get("threads"), (dict, list)):
            lines = deepcopy(raw["threads"])
    # Fixed boundaries are executable even when content checks exhausted their repair budget.
    supplied = rows(current.get("volumes"))
    template = DATA["schemas"]["prepare"]["bible"]["volumes"][0]
    current["volumes"] = [
        dict(defaults(next((s for s in supplied if integer(s.get("number"), i) == i), {}), template), **s)
        for i, s in enumerate(stage_ranges(total), 1)
    ]
    current["structure_version"] = 1
    return current, lines


def opponent_presence_issues(bible, batch):
    """Only check explicit planned presence, allowing background mentions after exit."""
    issues = []
    canonical_names = {card.get("name") for card in rows(bible.get("characters")) if isinstance(card.get("name"), str)}
    for chapter in batch:
        number = integer(chapter.get("number"))
        present = set()
        for scene in [chapter, *rows(chapter.get("scenes"))]:
            people = scene.get("characters")
            if isinstance(people, list):
                present.update(n for n in people if isinstance(n, str))
        for opponent in rows(bible.get("antagonists")):
            name, exit_at = opponent.get("name"), integer(opponent.get("exit"))
            stage = next(
                (s for s in rows(bible.get("volumes")) if integer(s.get("number")) == integer(opponent.get("stage"))),
                {},
            )
            card = next((c for c in rows(bible.get("characters")) if c.get("name") == name), {})
            aliases = card.get("aliases", [])
            aliases = [aliases] if isinstance(aliases, str) else aliases if isinstance(aliases, list) else []
            labels = [name, *[alias for alias in aliases if isinstance(alias, str) and alias not in canonical_names]]
            if (
                any(n in present for n in labels if isinstance(n, str))
                and stage
                and (number < integer(stage.get("start")) or number > exit_at)
            ):
                issues.append(
                    dict(
                        kind="opponent_scope",
                        name=name,
                        stage=opponent.get("stage"),
                        exit=exit_at,
                        labels=[n for n in labels if isinstance(n, str) and n],
                        chapters=[number],
                        targets=[number],
                    )
                )
    return issues
