"""Targeted rhythm replanning context and bounded semantic review."""

from copy import deepcopy
import json

from .chapter_rhythm import RULE, VERSION, chapter_type
from .q1_schema import DATA, integer, model_value, rows
from .text import pack


def _outline_summary(row):
    fields = (
        "number",
        "title",
        "type",
        "rhythm_type",
        "summary",
        "opening_conflict",
        "conflict",
        "scenes",
        "hook",
    )
    result = {field: deepcopy(row[field]) for field in fields if row.get(field) not in (None, "", [])}
    result["summary"] = row.get("summary") or row.get("opening_conflict") or row.get("conflict") or ""
    return result


def replan_context(history, batch, targets, observed=(), required=(), *, stage_start=0, stage_end=0):
    """Return bounded neighboring plans plus already observed facts for targeted rewrites."""
    combined = {integer(row.get("number")): row for row in [*history, *batch] if integer(row.get("number"))}
    neighbor_numbers = {
        number for target in targets for number in (target - 1, target, target + 1) if number in combined
    }
    facts = [
        deepcopy(row) for row in observed if stage_start <= integer(row.get("number", row.get("chapter"))) <= stage_end
    ]
    requirements = {integer(row.get("number")): chapter_type(row) or row.get("rhythm_type", "") for row in required}
    target_replans = []
    for number in sorted(targets):
        original = _outline_summary(combined.get(number, {}))
        target_replans.append(
            dict(
                number=number,
                required_type=requirements.get(number, ""),
                original_content=original,
                before_summary=_outline_summary(combined.get(number - 1, {})).get("summary", ""),
                after_summary=_outline_summary(combined.get(number + 1, {})).get("summary", ""),
                must_preserve={
                    field: deepcopy(combined.get(number, {}).get(field))
                    for field in ("threads", "required_event", "payoff", "payoff_type", "hook", "ending_content")
                    if combined.get(number, {}).get(field) not in (None, "", [])
                },
            )
        )
    return dict(
        neighbor_outlines=[_outline_summary(combined[number]) for number in sorted(neighbor_numbers)],
        target_replans=target_replans,
        observed_stage_facts=facts,
    )


def observed_rows(recent, history):
    result = rows(list(recent))
    source = history.get("events", history.get("chapters", [])) if isinstance(history, dict) else history
    return result + rows(source)


def model_review_issues(batch, review):
    """Convert a model's content classification into reference-only planning issues."""
    planned = {integer(row.get("number")): chapter_type(row) for row in batch}
    issues = []
    for row in rows(review):
        number = integer(row.get("number"))
        expected = planned.get(number, "")
        observed = "情感" if row.get("content_type") == "关系" else row.get("content_type", "")
        if not expected or row.get("status") != "mismatch" or observed == expected:
            continue
        issues.append(
            dict(
                kind="rhythm_model_mismatch",
                targets=[number],
                allowed=[expected],
                observed=observed,
                reason=str(row.get("reason") or "模型复核认为主要场景与章型标签不一致")[:160],
                requirement=RULE,
            )
        )
    return issues


def semantic_review(client, key, batch, deterministic=()):
    """Run one compact semantic review when the client is a configured model client."""
    if not (getattr(client, "model", None) and getattr(client, "provider", None)):
        return []
    chapters = [
        {
            "number": integer(row.get("number")),
            "planned_type": chapter_type(row),
            "opening_conflict": row.get("opening_conflict", ""),
            "conflict": row.get("conflict", ""),
            "scenes": [
                {
                    field: scene.get(field, "")
                    for field in ("characters", "goal", "obstacle", "choice", "action", "result")
                }
                for scene in rows(row.get("scenes"))
            ],
        }
        for row in batch
    ]
    payload = {"deterministic_issues": list(deterministic), "chapters": chapters}
    prompt = (
        DATA["instructions"]["rhythm_review"]
        + "\n待复核章纲：\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    schema = pack(DATA["schemas"]["rhythm_review"])
    raw = client.stage(
        f"plan-q1-rhythm-review-v{VERSION}-" + key,
        prompt,
        schema,
        parser=model_value,
        max_attempts=6,
        cap=1800,
        effort="low",
    )
    value = model_value(raw)
    if not isinstance(value, dict):
        return []
    source = rows(value.get("chapters")) or rows(value.get("reviews"))
    return [
        {
            "number": integer(row.get("number", row.get("chapter"))),
            "content_type": row.get("content_type", ""),
            "status": row.get("status", "unknown"),
            "reason": row.get("reason", ""),
        }
        for row in source
        if integer(row.get("number", row.get("chapter")))
    ]
