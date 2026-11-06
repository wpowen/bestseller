"""One smaller chapter attempt, with durable failure history and unchanged source."""

import dataclasses
import re

from .io import save
from .validation import validate_response
from tools.runtime_support import ProjectError, QuotaExhausted


def ask_narrow(session, stage, prompt, data, cap, partial_coverage):
    """Keep identifiers and guards while reviewing one paragraph/target at a time."""
    field = next((key for key in ("paragraphs", "targets", "items", "slots") if isinstance(data.get(key), list)), None)
    parts = [dict(data, **{field: [item]}) for item in data[field]] if field and data[field] else [data]
    if field == "paragraphs" and "examples" in data and len(data[field]) == 1:
        parts = [
            dict(part, paragraphs=[dict(p, sentences=[sentence])], examples=[])
            for part in parts
            for p in part["paragraphs"]
            for sentence in p["sentences"]
        ] or [data]
    values = [
        session.ask(stage, prompt, part, cap, partial_coverage=False, scope=f"-narrow{i}")
        for i, part in enumerate(parts)
    ]
    result = {}
    for value in values:
        for key, item in value.items():
            if isinstance(item, list):
                result.setdefault(key, []).extend(item)
            elif key != "chapter":
                result[key] = item
    if stage == "dialogue-scene":
        # Reassemble complete variants before the original combined fact check.
        result["variants"] = [
            dict(lines=[line for variant in group for line in variant["lines"]])
            for group in zip(*(value["variants"] for value in values))
        ]
    if "checked_pids" in result:
        result["checked_pids"] = list(dict.fromkeys(result["checked_pids"]))
    if any("chapter" in value for value in values):
        # Paragraph diagnostics cannot claim to be a new whole-chapter judgement.
        result["chapter"] = dict(
            scope="paragraph",
            segments=[value["chapter"] for value in values if "chapter" in value],
            risks=[risk for value in values for risk in value.get("chapter", {}).get("risks", [])],
        )
    validate_response(stage, result, data, partial_coverage=partial_coverage)
    return result


def _attempt(result, text, ctx):
    if result.status in ("polished", "unchanged"):
        size = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", result.text))
        if not ctx["length_range"][0] <= size <= ctx["length_range"][1]:
            result.status = "retained"
            result.checks = getattr(result, "checks", {})
            result.checks.setdefault("failure", dict(stage="publication", reason="word_band"))
    detail = dict(status=result.status)
    if result.status in ("retained", "partial"):
        failure = getattr(result, "checks", {}).get("failure")
        warnings = [
            warning
            for warning in getattr(result, "warnings", [])
            if isinstance(warning, dict) and (warning.get("rollback") or warning.get("error"))
        ]
        if failure:
            detail["failure"] = failure
        elif warnings:
            detail["failure"] = dict(stage="quality", reason="quality_rejected", warnings=warnings)
        result.text, result.edits = text, []
    return detail


def run(polish, text, ctx, llm, work, recovery):
    first = polish(text, ctx, llm)
    attempt = _attempt(first, text, ctx)
    if first.status not in ("retained", "partial") or "failure" not in attempt:
        return first
    recovery.update(strategy="single_item", attempts=[attempt], status="retrying")
    save(work, "attempt-initial.json", dataclasses.asdict(first) if dataclasses.is_dataclass(first) else vars(first))
    save(work, "recovery.json", recovery)
    # A terminal quota/project/interruption error propagates; the saved first
    # attempt remains available even when the chapter cannot publish a report.
    stage = re.sub(r"-format\d+$", "", str(attempt["failure"].get("stage", "")))
    targeted = None if stage in ("quality", "publication", "") else stage
    try:
        second = polish(text, dict(ctx, _narrow_retry=True, _narrow_stage=targeted), llm)
    except (ProjectError, QuotaExhausted, InterruptedError) as exc:
        recovery["status"] = "interrupted" if isinstance(exc, InterruptedError) else "error"
        if isinstance(exc, QuotaExhausted):
            recovery["status"] = "quota_exhausted"
        recovery["attempts"].append(
            dict(
                status=recovery["status"],
                failure=dict(stage=getattr(exc, "stage", "retry"), reason=getattr(llm, "redact", str)(str(exc))),
            )
        )
        save(work, "recovery.json", recovery)
        raise
    recovery["attempts"].append(_attempt(second, text, ctx))
    recovery["status"] = "retained" if second.status in ("retained", "partial") else "recovered"
    save(work, "recovery.json", recovery)
    second.checks = getattr(second, "checks", {})
    second.checks["recovery"] = recovery
    for name in ("calls", "prompt_tokens", "completion_tokens", "seconds"):
        setattr(second, name, getattr(first, name, 0) + getattr(second, name, 0))
    second.checks["unknown_usage"] = getattr(first, "checks", {}).get("unknown_usage", 0) + second.checks.get(
        "unknown_usage", 0
    )
    return second
