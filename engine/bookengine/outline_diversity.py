"""Deterministic global title/conflict repetition and bounded targeted rewrites."""

from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import re

from .q1_schema import integer, rows
from .storage import save
from .structure_planning import opponent_presence_issues
from .stage_transition import planning_transition_issues
from .solution_substance import solution_issues
from .core_ability import ability_issues
from .chapter_rhythm import VERSION as RHYTHM_VERSION, rhythm_issues, repair_rhythm

# Without a segmenter, title words are conservative surface candidates, not a semantic verdict.
FUNCTION_WORDS = set(
    "的了着过得地吗呢吧啊呀哦与和或及而却也又再才就都很更最不没无是有在把被让给向从为将以于其之乎者也我你他她它们这那哪谁几一二三四五六七八九十百千万两"
)
STRUCTURE_CHECKS_VERSION = 6


def title_terms(title, supplied=()):
    title = re.sub(r"^\s*第[\d一二三四五六七八九十百千]+[章回节卷]\s*", "", str(title or ""))
    terms = set()
    for word in re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]+", title):
        if word.isascii():
            if word.lower() not in {"a", "an", "the", "of", "to", "and", "in", "is"}:
                terms.add(word.casefold())
            continue
        fragments = re.split("[" + "".join(sorted(FUNCTION_WORDS)) + "]+", word)
        for fragment in fragments:
            for size in range(1, min(4, len(fragment)) + 1):
                terms.update(fragment[n : n + size] for n in range(len(fragment) - size + 1))
    if isinstance(supplied, list):
        terms.update(
            t.strip().casefold()
            for t in supplied
            if isinstance(t, str) and t.strip() and t in title and not set(t) <= FUNCTION_WORDS
        )
    return sorted(terms)


def conflict_type(row):
    value = row.get("conflict_type") or row.get("core_conflict_type") or row.get("structure") or ""
    return re.sub(r"[\s，,。！!？?、:：]+", "", value).casefold() if isinstance(value, str) else ""


def used_vocabulary(history):
    keywords, conflicts = Counter(), Counter()
    for row in history:
        keywords.update(title_terms(row.get("title"), row.get("title_keywords")))
        kind = conflict_type(row)
        if kind:
            conflicts[kind] += 1
    return dict(conflict_types=dict(sorted(conflicts.items())), title_keywords=dict(sorted(keywords.items())))


def prompt_vocabulary(used, limit=1200):
    """A bounded prompt view; the persisted used vocabulary always remains complete."""
    result = dict(conflict_types={}, title_keywords={})
    omitted = dict(conflict_types=0, title_keywords=0)
    for field in ("conflict_types", "title_keywords"):
        ordered = sorted(used[field].items(), key=lambda pair: (-pair[1], -len(pair[0]), pair[0]))
        for term, count in ordered:
            result[field][term] = count
            field_limit = limit // 3 if field == "conflict_types" else limit
            if len(json.dumps(result, ensure_ascii=False)) > field_limit:
                del result[field][term]
                omitted[field] += 1
    result["omitted"] = omitted
    result["complete_list"] = "outline-diversity/*.json 的 used 字段"
    return result


def diversity_issues(history, batch):
    targets = {integer(r.get("number")) for r in batch}
    combined = {integer(r.get("number")): r for r in [*history, *batch]}
    keywords, issues = defaultdict(list), []
    for n, row in sorted(combined.items()):
        for term in title_terms(row.get("title"), row.get("title_keywords")):
            keywords[term].append(n)
    repeated = {term: ns for term, ns in keywords.items() if len(ns) >= 3 and targets.intersection(ns)}
    for term, ns in sorted(repeated.items()):
        # Report the longest repeated form when it has exactly the same chapter hits.
        if any(term in other and term != other and ns == hits for other, hits in repeated.items()):
            continue
        issues.append(dict(kind="title_keyword", keyword=term, chapters=ns, targets=sorted(targets.intersection(ns))))
    ordered = sorted(combined)
    for offset in range(len(ordered) - 4):
        window = ordered[offset : offset + 5]
        if window[-1] - window[0] != 4 or not targets.intersection(window):
            continue
        kinds = [conflict_type(combined[n]) for n in window]
        if kinds[0] and len(set(kinds)) == 1:
            issues.append(
                dict(
                    kind="conflict_run",
                    conflict_type=kinds[0],
                    chapters=window,
                    targets=sorted(targets.intersection(window)),
                )
            )
    return issues


def historical_outlines(out, start, recent=()):
    """Read ALL saved windows and chapters, retaining metadata absent from old chapter rows."""
    merged = {}

    def include(values):
        for row in rows(values):
            number = integer(row.get("number"))
            if 0 < number < start:
                previous = merged.setdefault(number, {})
                previous.update({k: v for k, v in row.items() if v not in (None, "", [])})

    include(list(recent))
    if out is not None:
        out = Path(out)
        for pattern in (
            "steps/*chapters-*.json",
            "outline-diversity/*.json",
            "writing_outlines/*.json",
            "chapters/*.json",
        ):
            for path in sorted(out.glob(pattern), key=lambda p: p.stat().st_mtime_ns):
                if path.name.endswith(".retry.json"):
                    continue
                value = json.loads(path.read_text())
                include(
                    value.get("planned_chapters", value.get("chapters", [value])) if isinstance(value, dict) else value
                )
    return [merged[n] for n in sorted(merged)]


def terminal_window(out, start, end):
    """Reuse a complete final window covering start, including exhausted repairs."""
    if out is None:
        return None
    paths = (Path(out) / "outline-diversity").glob("*.json")
    for path in sorted(paths, key=lambda p: p.stat().st_mtime_ns, reverse=True):
        bounds = re.fullmatch(r"(\d+)-(\d+)(?:-redo.+)?", path.stem)
        if not bounds or not int(bounds[1]) <= start <= int(bounds[2]):
            continue
        report = json.loads(path.read_text())
        if report.get("status") not in ("passed", "failed_continued"):
            continue
        normalized = isinstance(report.get("planned_chapters"), list)
        source = report["planned_chapters"] if normalized else report.get("chapters")
        chapters = rows(source)
        mapped = {integer(row.get("number")): row for row in chapters}
        expected = set(range(int(bounds[1]), int(bounds[2]) + 1))
        if set(mapped) != expected or len(chapters) != len(expected):
            continue
        stop = min(end, int(bounds[2]))
        return dict(
            chapters=[deepcopy(mapped[n]) for n in range(start, stop + 1)],
            normalized=normalized,
            end=stop,
            structure_checks_version=report.get("structure_checks_version", 0),
            rhythm_contract_version=report.get("rhythm_contract_version", 0),
            path=str(path),
        )
    return None


def repair_batch(out, key, history, batch, rewrite, bible=None):
    """Only replace offending current chapters; persist decisions for no-call recovery."""
    path = Path(out) / "outline-diversity" / (key + ".json") if out is not None else None
    current, attempts, resume, rhythm_repairs = deepcopy(batch), [], 0, []
    if path and path.exists():
        report = json.loads(path.read_text())
        if report["status"] != "repairing":
            return report["chapters"]
        current = report["chapters"]
        resume = report["attempts"][-1]["regeneration"]
        attempts = report["attempts"][:resume]
        rhythm_repairs = report.get("rhythm_repairs", [])
    used = used_vocabulary(history)
    for retry in range(resume, 4):
        issues = (
            diversity_issues(history, current)
            + opponent_presence_issues(bible or {}, current)
            + planning_transition_issues(bible or {}, current)
            + solution_issues(bible or {}, history, current)
            + ability_issues(bible or {}, history, current)
            + rhythm_issues(bible or {}, history, current)
        )
        targets = sorted({n for issue in issues for n in issue["targets"]})
        attempts.append(dict(regeneration=retry, issues=issues, targets=targets))
        if retry == 3:
            for issue in issues:
                if issue["kind"] != "opponent_scope":
                    continue
                for chapter in current:
                    if chapter["number"] not in issue["targets"]:
                        continue
                    for scene in [chapter, *rows(chapter.get("scenes"))]:
                        if isinstance(scene.get("characters"), list):
                            scene["characters"] = [n for n in scene["characters"] if n not in issue["labels"]]
                    chapter.setdefault("background_only", []).append(issue["name"])
            current, rhythm_repairs = repair_rhythm(bible or {}, history, current)
            # Recheck every contract after scene rewriting, without laundering old failures as passed.
            final_issues = (
                diversity_issues(history, current)
                + opponent_presence_issues(bible or {}, current)
                + planning_transition_issues(bible or {}, current)
                + solution_issues(bible or {}, history, current)
                + ability_issues(bible or {}, history, current)
                + rhythm_issues(bible or {}, history, current)
            )
            # Existing cleanup warnings stay auditable even if removing a retired name cleared them.
            issues = [i for i in issues if not i["kind"].startswith("rhythm_")]
            issues += [i for i in final_issues if i not in issues]
        report = dict(
            structure_checks_version=STRUCTURE_CHECKS_VERSION,
            rhythm_contract_version=RHYTHM_VERSION,
            rhythm_repairs=rhythm_repairs,
            status="passed" if not issues else "failed_continued" if retry == 3 else "repairing",
            used=used,
            attempts=attempts,
            issues=issues,
            chapters=current,
        )
        if path:
            save(path, report)
        if not issues or retry == 3:
            break
        raw = rewrite(current, issues, targets, used, retry + 1)
        source = rows(raw.get("chapters")) if isinstance(raw, dict) else rows(raw)
        replacements = {integer(r.get("number")): r for r in source}
        conflict_targets = {n for issue in issues if issue["kind"] == "conflict_run" for n in issue["targets"]}
        for index, old in enumerate(current):
            number = integer(old.get("number"))
            if number not in targets or number not in replacements:
                continue
            proposed = dict(old, **replacements[number])
            if number in conflict_targets and all(
                proposed.get(k) == old.get(k) for k in ("opening_conflict", "scenes", "payoff", "hook")
            ):
                continue
            proposed["title_keywords"] = title_terms(proposed.get("title"), replacements[number].get("title_keywords"))
            current[index] = proposed
    return current
