"""Decide whether new observed facts justify reopening a saved outline window."""

import json
from pathlib import Path

from .adjacent_conflicts import adjacent_conflict_issues
from .chapter_rhythm import VERSION as RHYTHM_VERSION, normalize_chapter_type, rhythm_issues
from .core_ability import ability_issues
from .q1_schema import integer, rows
from .solution_substance import solution_issues
from .solution_sequences import solution_plot_history, solution_sequence_issues
from .stage_transition import planning_transition_issues
from .structure_planning import opponent_presence_issues
from .planning_policy import hard_replan_issues


def cached_contract_stale(bible, prior, cached, structure_version):
    """Return whether an old cache violates a current hard planning contract."""
    if not cached:
        return False
    chapters = cached.get("chapters", [])
    rhythm_stale = cached.get("rhythm_contract_version", 0) < RHYTHM_VERSION and rhythm_issues(bible, prior, chapters)
    structural = cached.get("structure_checks_version", 0) < structure_version and hard_replan_issues(
        planning_transition_issues(bible, chapters)
        + ability_issues(bible, prior, chapters)
        + solution_issues(bible, prior, chapters)
        + solution_sequence_issues(bible, prior, chapters)
        + adjacent_conflict_issues(prior, chapters)
    )
    return bool(rhythm_stale or structural)


def observe_plan_alignment(outline, observed):
    """Compare only explicit saved observations; absent event evidence stays Unknown."""
    checks, mismatches, unknown = {}, [], []
    for field in ("type", "hook_type", "structure"):
        expected, actual = outline.get(field), observed.get(field)
        if field == "type":
            expected, actual = normalize_chapter_type(expected), normalize_chapter_type(actual)
        if expected in (None, ""):
            continue
        if actual in (None, ""):
            unknown.append(field)
            continue
        checks[field] = dict(expected=expected, observed=actual)
        if expected != actual:
            mismatches.append(dict(field=field, expected=expected, observed=actual))
    if outline.get("required_event"):
        event_status = observed.get("required_event_fulfilled")
        if type(event_status) is not bool:
            unknown.append("required_event")
        elif not event_status:
            mismatches.append(dict(field="required_event", expected=outline["required_event"], observed="明确未发生"))
    status = "mismatch" if mismatches else "unknown" if unknown else "matched"
    return dict(status=status, checks=checks, mismatches=mismatches, unknown=unknown)


def _report(cached):
    path = cached.get("path") if isinstance(cached, dict) else None
    if not path:
        return {}
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _issue_key(issue):
    return json.dumps(issue, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _new_issues(current, saved):
    known = {_issue_key(issue) for issue in rows(saved)}
    return [issue for issue in rows(current) if _issue_key(issue) not in known]


def _saved_plan(cached, number, report):
    path = cached.get("path")
    if path:
        outline = Path(path).parent.parent / "writing_outlines" / f"{number:03d}.json"
        if outline.exists():
            try:
                value = json.loads(outline.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                value = None
            if isinstance(value, dict):
                return value
    source = report.get("planned_chapters", report.get("chapters", []))
    return next((row for row in rows(source) if integer(row.get("number")) == number), {})


def _observed_drift(cached, prior, report):
    completed = [row for row in rows(prior) if row.get("fact_source") == "final_body" and integer(row.get("number"))]
    if not completed or not cached.get("chapters"):
        return []
    observed = max(completed, key=lambda row: integer(row.get("number")))
    number = integer(observed.get("number"))
    planned = _saved_plan(cached, number, report)
    alignment = observed.get("plan_alignment")
    mismatches = []
    if isinstance(alignment, dict) and alignment.get("status") == "mismatch":
        mismatches.extend(rows(alignment.get("mismatches")))
    else:
        for field in ("type", "hook_type", "structure"):
            expected, actual = planned.get(field), observed.get(field)
            if expected not in (None, "") and actual not in (None, "") and expected != actual:
                mismatches.append(dict(field=field, expected=expected, observed=actual))
    if not mismatches:
        return []
    target = integer(cached["chapters"][0].get("number"))
    semantic = next(
        (mismatch.get("observed") for mismatch in mismatches if mismatch.get("field") == "type"),
        "",
    )
    return [
        dict(
            kind="rhythm_observed_plan_drift",
            targets=[target],
            allowed=[cached["chapters"][0].get("type", "")],
            observed=semantic,
            observed_mismatches=mismatches,
            requirement="上一章实际章型、关键事件或章纲明确偏离保存计划时，只重排受影响的未写后缀",
        )
    ]


def cache_replan_issues(bible, prior, cached):
    """Return only new cache-invalidating issues; missing observations remain Unknown."""
    if not cached:
        return []
    report = _report(cached)
    chapters = cached.get("chapters", [])
    plot = (
        solution_issues(bible, prior, chapters)
        + solution_sequence_issues(bible, prior, chapters)
        + ability_issues(bible, prior, chapters)
    )
    plot_history = solution_plot_history(bible, prior)
    rhythm = rhythm_issues(bible, prior, chapters)
    opponents = opponent_presence_issues(bible, chapters)
    return hard_replan_issues(
        [
            # Body type/hook/structure drift remains an alignment diagnostic. It must not reopen a paid plan window.
            *(
                _new_issues(plot, report.get("plot_issues_after", []))
                if report.get("plot_history") != plot_history
                else []
            ),
            *_new_issues(rhythm, report.get("rhythm_issues_after", [])),
            *_new_issues(opponents, report.get("opponent_issues_after", [])),
        ]
    )
