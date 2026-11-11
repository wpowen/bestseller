"""Read-only comparison of planned and observed chapter types."""

import json
from collections import Counter
from pathlib import Path

import yaml

from .chapter_rhythm import chapter_type, combat_run_exemption, observed_chapter_type, rhythm_issues


def _plan(book, number):
    path = Path(book) / "writing_outlines" / f"{number:03d}.json"
    if not path.is_file():
        return {}, "missing_outline"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}, "invalid_outline"
    return value, "" if chapter_type(value) else "missing_planned_type"


def _plan_content(book):
    outlines = []
    for path in sorted((Path(book) / "writing_outlines").glob("[0-9][0-9][0-9].json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(value, dict):
            outlines.append(value)
    bible = {}
    try:
        value = yaml.safe_load((Path(book) / "story_bible.yaml").read_text(encoding="utf-8"))
        bible = value if isinstance(value, dict) else {}
    except (OSError, yaml.YAMLError, TypeError):
        pass
    issues = rhythm_issues(bible, [], outlines)
    return dict(
        checked=len(outlines),
        unresolved_chapters=sorted({number for issue in issues for number in issue.get("targets", [])}),
        issues=issues,
        basis="saved outline actions and labels; deterministic planning reference only",
    )


def _observed_rhythm(book, bible, chapters):
    types, counts, runs, current, previous = {}, Counter(), [], [], None
    for chapter in chapters:
        outline, _ = _plan(book, chapter["number"])
        kind = observed_chapter_type(chapter.get("meta", {}), chapter.get("text", ""), outline)
        types[chapter["number"]] = kind
        counts["关系" if kind == "情感" else kind or "Unknown"] += 1
        if kind == "对抗":
            if previous is None or chapter["number"] != previous + 1:
                if current:
                    runs.append(current)
                current = []
            current.append(chapter["number"])
        elif current:
            runs.append(current)
            current = []
        previous = chapter["number"]
    if current:
        runs.append(current)
    stages = []
    for stage in bible.get("volumes", []) if isinstance(bible.get("volumes"), list) else []:
        start, end = stage.get("start"), stage.get("end")
        if type(start) is not int or type(end) is not int:
            continue
        available = [chapter["number"] for chapter in chapters if start <= chapter["number"] <= end]
        relationships = [number for number in available if types.get(number) == "情感"]
        missing = [number for number in range(start, end + 1) if number not in available]
        stages.append(
            dict(
                stage=stage.get("number"),
                relationship_chapters=relationships,
                status="Unknown" if missing else "met" if relationships else "missing_relationship",
            )
        )
    return dict(
        distribution=dict(counts),
        longest_confrontation=max(map(len, runs), default=0),
        confrontation_runs=runs,
        excess_runs=[run for run in runs if len(run) > 3],
        stages=stages,
        unknown_chapters=[number for number, kind in types.items() if not kind],
        basis="saved observed type with conservative grounded relationship correction; 情感 mapped to 关系",
    )


def merge_rhythm_checkup(book, chapters, findings):
    """Add reference-only plan/body alignment without changing source files."""
    mismatches, unknown, matched = [], [], 0
    for chapter in chapters:
        number = chapter["number"]
        outline, reason = _plan(book, number)
        planned = chapter_type(outline)
        actual = observed_chapter_type(chapter.get("meta", {}), chapter.get("text", ""), outline)
        if reason or not actual:
            unknown.append(
                dict(
                    chapter=number,
                    reason=reason or "missing_actual_type",
                    planned_type=planned or None,
                    actual_type=actual or None,
                    status="Unknown",
                )
            )
        elif planned == actual:
            matched += 1
        else:
            mismatches.append(
                dict(
                    chapter=number,
                    planned_type=planned,
                    actual_type=actual,
                    reason=f"保存的正文章型为{actual}，章纲要求{planned}",
                    status="Inference",
                )
            )
    result = dict(findings)
    rhythm = dict(result.get("chapter_rhythm", {}))
    try:
        bible = yaml.safe_load((Path(book) / "story_bible.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError, TypeError):
        bible = {}
    rhythm.update(_observed_rhythm(book, bible, chapters))
    rhythm["outline_alignment"] = dict(
        matched=matched,
        compared=matched + len(mismatches),
        mismatch_chapters=[row["chapter"] for row in mismatches],
        mismatches=mismatches,
        unknown=unknown,
        basis="saved writing outline type versus saved observed body type; reference only",
    )
    rhythm["plan_content"] = _plan_content(book)
    exemption = combat_run_exemption(bible)
    excess = rhythm.get("excess_runs", [])
    rhythm["combat_exemptions"] = [exemption] if exemption else []
    rhythm["approved_excess_runs"] = (
        [exemption["chapters"]] if exemption and any(set(exemption["chapters"]).issubset(run) for run in excess) else []
    )
    rhythm["unapproved_excess_runs"] = [
        run for run in excess if not exemption or not set(run).issubset(exemption["chapters"])
    ]
    result["chapter_rhythm"] = rhythm
    return result
