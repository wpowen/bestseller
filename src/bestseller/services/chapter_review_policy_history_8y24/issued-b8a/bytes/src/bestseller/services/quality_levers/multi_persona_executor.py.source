"""Multi-persona critic executor — orchestration glue layer.

This module **does not** call an LLM directly. It accepts a
``persona_runner`` callable supplied by the pipeline (the actual
``services.llm.complete_text`` wrapper) and orchestrates:

#. Iterating the 4 critic personas
#. Building each persona's system prompt
#. Parsing the returned JSON into :class:`PersonaResult`
#. Aggregating the four results via
   :func:`aggregate_persona_results`

The design keeps :mod:`services.llm` separate so the executor can be
unit-tested with a mock callable, exactly mirroring the established
pattern in :mod:`services.reviews`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from bestseller.services.quality_levers.critic_personas import (
    AggregatedCritique,
    PersonaIssue,
    PersonaResult,
    aggregate_persona_results,
    load_critic_personas,
    render_persona_system_prompt,
)


# ``persona_runner`` is any callable that, given a ``system_prompt`` and
# a ``user_prompt`` (both ``str``), returns a parsed JSON dict.
PersonaRunner = Callable[[str, str], dict[str, Any]]


@dataclass(frozen=True)
class PersonaInvocation:
    """Captures one persona's LLM round-trip outcome.

    ``error`` is populated on failures so the orchestrator can decide
    whether to fall back to the remaining personas or escalate.
    """

    persona_id: str
    raw_response: dict[str, Any] | None
    result: PersonaResult | None
    error: str = ""


@dataclass(frozen=True)
class MultiPersonaExecution:
    """Bundles every per-persona invocation + the aggregated verdict."""

    invocations: tuple[PersonaInvocation, ...]
    aggregate: AggregatedCritique


def _parse_persona_response(
    persona_id: str, response: dict[str, Any]
) -> PersonaResult:
    """Coerce a raw JSON dict into the typed :class:`PersonaResult`."""

    overall = response.get("overall_score")
    try:
        overall_score = float(overall) if overall is not None else 0.0
    except (TypeError, ValueError):
        overall_score = 0.0

    must_rewrite_raw = response.get("must_rewrite", False)
    must_rewrite = bool(must_rewrite_raw)

    issues_raw = response.get("top_3_issues") or response.get("issues") or []
    issues: list[PersonaIssue] = []
    if isinstance(issues_raw, list):
        for entry in issues_raw:
            if not isinstance(entry, dict):
                continue
            issue_text = str(entry.get("issue", "")).strip()
            if not issue_text:
                continue
            issues.append(
                PersonaIssue(
                    issue=issue_text,
                    severity=str(entry.get("severity", "medium")).strip() or "medium",
                    suggested_cause_id=str(
                        entry.get("suggested_cause_id") or ""
                    ).strip(),
                    evidence=str(entry.get("evidence") or "").strip(),
                    specific_fix=str(entry.get("specific_fix") or "").strip(),
                )
            )

    return PersonaResult(
        persona_id=persona_id,
        overall_score=overall_score,
        must_rewrite=must_rewrite,
        issues=tuple(issues),
        verdict=str(response.get("verdict") or "").strip(),
        one_line_takeaway=str(response.get("one_line_takeaway") or "").strip(),
    )


def run_multi_persona_critique(
    *,
    chapter_text: str,
    persona_runner: PersonaRunner,
    persona_ids: tuple[str, ...] | None = None,
    extra_user_context: str = "",
) -> MultiPersonaExecution:
    """Run all configured personas against ``chapter_text``.

    ``persona_runner`` must return a JSON-compatible ``dict``. If the
    pipeline's LLM wrapper returns a raw string, callers can wrap it
    via :func:`_decode_runner_result` below.
    """

    config = load_critic_personas()
    selected = persona_ids or tuple(config.personas.keys())
    invocations: list[PersonaInvocation] = []
    results: list[PersonaResult] = []
    for persona_id in selected:
        system_prompt = render_persona_system_prompt(persona_id)
        if not system_prompt:
            invocations.append(
                PersonaInvocation(
                    persona_id=persona_id,
                    raw_response=None,
                    result=None,
                    error="persona_not_found",
                )
            )
            continue
        user_prompt = _compose_user_prompt(chapter_text, extra_user_context)
        try:
            raw = persona_runner(system_prompt, user_prompt)
        except Exception as exc:  # noqa: BLE001 — surface in the invocation log
            invocations.append(
                PersonaInvocation(
                    persona_id=persona_id,
                    raw_response=None,
                    result=None,
                    error=str(exc),
                )
            )
            continue
        if not isinstance(raw, dict):
            invocations.append(
                PersonaInvocation(
                    persona_id=persona_id,
                    raw_response=None,
                    result=None,
                    error="non_dict_response",
                )
            )
            continue
        parsed = _parse_persona_response(persona_id, raw)
        invocations.append(
            PersonaInvocation(
                persona_id=persona_id,
                raw_response=raw,
                result=parsed,
                error="",
            )
        )
        results.append(parsed)

    aggregate = aggregate_persona_results(results)
    return MultiPersonaExecution(
        invocations=tuple(invocations),
        aggregate=aggregate,
    )


def _compose_user_prompt(chapter_text: str, extra_user_context: str) -> str:
    parts: list[str] = []
    if extra_user_context.strip():
        parts.append(extra_user_context.strip())
    parts.append("===== 章节正文 begin =====")
    parts.append(chapter_text)
    parts.append("===== 章节正文 end =====")
    parts.append(
        "请仅以本 persona 的视角输出 JSON，含字段："
        " overall_score / must_rewrite / verdict / top_3_issues "
        "(issue, severity, suggested_cause_id, evidence, specific_fix) / "
        "one_line_takeaway."
    )
    return "\n\n".join(parts)


def decode_runner_result(raw: Any) -> dict[str, Any]:
    """Helper for adapting different ``persona_runner`` return shapes.

    Accepts:

    * ``dict`` — passed through
    * ``str`` — parsed as JSON
    * anything else — empty dict so the executor records an error
    """

    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
    return {}
