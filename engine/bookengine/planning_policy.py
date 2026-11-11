"""Bounded planning policy for story-level outline generation and repair."""

from .q1_schema import integer, rows

MAX_PLAN_CHAPTERS = 5
MAX_REPLAN_TARGETS = 2
MAX_REWRITE_CALLS = 1

HARD_REPLAN_KINDS = {
    "rhythm_stage_relation",
    "rhythm_combat_run",
    "reader_promise_payoff",
    "opponent_resolution",
}


def planning_window_end(start, requested_end, stage_end):
    return min(integer(requested_end, start), integer(stage_end, requested_end), start + MAX_PLAN_CHAPTERS - 1)


def hard_replan_issues(issues):
    """Return only stage-contract failures that justify another paid outline call."""
    result, seen = [], set()
    for issue in rows(issues):
        kind = issue.get("kind")
        if kind not in HARD_REPLAN_KINDS or kind in seen:
            continue
        seen.add(kind)
        normalized = dict(issue)
        normalized["targets"] = sorted(set(issue.get("targets", [])))[:1]
        if normalized["targets"]:
            result.append(normalized)
    return result


def replan_targets(issues, locked=()):
    blocked = set(locked)
    return sorted(
        {number for issue in hard_replan_issues(issues) for number in issue.get("targets", []) if number not in blocked}
    )[:MAX_REPLAN_TARGETS]
