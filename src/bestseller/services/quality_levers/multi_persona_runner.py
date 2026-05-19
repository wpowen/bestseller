"""Async LLM-backed runner for the 4-persona critic pass.

:func:`multi_persona_executor.run_multi_persona_critique` is the pure
orchestration core — it accepts any synchronous ``persona_runner``
callable. This module supplies the production wiring: an async
``services.llm``-backed runner that calls the framework's pooled
``complete_text`` four times (once per persona) and threads the
results back through the aggregator.

By keeping the LLM glue in its own module we preserve the
unit-testability of the executor (mock the runner) while still
giving the pipeline a one-liner to call in :mod:`reviews`.

Design:

* The four persona calls share a single ``project_id`` /
  ``workflow_run_id`` / ``step_run_id`` so the LLM ledger keeps the
  4 round-trips bucketed under one critic step.
* JSON parsing is delegated to :func:`decode_runner_result` so a
  badly-formed response is captured as a typed
  :class:`PersonaInvocation` error rather than crashing the pipeline.
* Concurrency: we use ``asyncio.gather`` so the four personas run
  in parallel — wall clock matches the slowest persona, not the sum.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.quality_levers.critic_personas import (
    AggregatedCritique,
    PersonaIssue,
    PersonaResult,
    aggregate_persona_results,
    load_critic_personas,
    render_persona_system_prompt,
)
from bestseller.services.quality_levers.multi_persona_executor import (
    MultiPersonaExecution,
    PersonaInvocation,
    decode_runner_result,
)


_FALLBACK_RESPONSE = json.dumps(
    {
        "overall_score": 0.0,
        "must_rewrite": True,
        "verdict": "rewrite",
        "top_3_issues": [
            {
                "issue": "LLM fallback fired; persona response unavailable",
                "severity": "medium",
            }
        ],
        "one_line_takeaway": "persona pass failed",
    },
    ensure_ascii=False,
)


@dataclass(frozen=True)
class MultiPersonaCallContext:
    """Identifiers threaded through every persona LLM call."""

    project_id: UUID | None = None
    workflow_run_id: UUID | None = None
    step_run_id: UUID | None = None
    prompt_template: str = "multi_persona_critique"
    prompt_version: str | None = None


async def _invoke_persona(
    persona_id: str,
    *,
    chapter_text: str,
    extra_user_context: str,
    call_context: MultiPersonaCallContext,
) -> PersonaInvocation:
    """Run one persona's LLM round-trip and parse the response."""

    system_prompt = render_persona_system_prompt(persona_id)
    if not system_prompt:
        return PersonaInvocation(
            persona_id=persona_id,
            raw_response=None,
            result=None,
            error="persona_not_found",
        )

    user_prompt = _compose_user_prompt(chapter_text, extra_user_context)
    try:
        result = await complete_text(
            LLMCompletionRequest(
                logical_role="critic",
                model_tier="standard",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=_FALLBACK_RESPONSE,
                prompt_template=call_context.prompt_template,
                prompt_version=call_context.prompt_version,
                project_id=call_context.project_id,
                workflow_run_id=call_context.workflow_run_id,
                step_run_id=call_context.step_run_id,
                metadata={"persona_id": persona_id},
            )
        )
    except Exception as exc:  # noqa: BLE001
        return PersonaInvocation(
            persona_id=persona_id,
            raw_response=None,
            result=None,
            error=f"llm_error: {exc!r}",
        )

    parsed = decode_runner_result(result.content)
    if not parsed:
        try:
            from bestseller.services.llm_closed_loop import (
                LLMGateFinding,
                build_repair_user_prompt,
            )

            repair = await complete_text(
                LLMCompletionRequest(
                    logical_role="critic",
                    model_tier="standard",
                    system_prompt=system_prompt,
                    user_prompt=build_repair_user_prompt(
                        original_user_prompt=user_prompt,
                        findings=[
                            LLMGateFinding(
                                code="PERSONA_CRITIQUE_JSON_PARSE_FAILED",
                                severity="major",
                                path="$",
                                message="Persona critique did not return parseable JSON.",
                                expected=(
                                    "JSON with overall_score, must_rewrite, verdict, "
                                    "top_3_issues, and one_line_takeaway."
                                ),
                                actual=(result.content or "")[:240],
                                repair_action="Return only valid JSON matching the requested persona critique schema.",
                            )
                        ],
                        language=None,
                    ),
                    fallback_response=_FALLBACK_RESPONSE,
                    prompt_template=f"{call_context.prompt_template}_repair",
                    prompt_version=call_context.prompt_version,
                    project_id=call_context.project_id,
                    workflow_run_id=call_context.workflow_run_id,
                    step_run_id=call_context.step_run_id,
                    metadata={
                        "persona_id": persona_id,
                        "semantic_repair": True,
                    },
                )
            )
            parsed = decode_runner_result(repair.content)
            if not parsed:
                return PersonaInvocation(
                    persona_id=persona_id,
                    raw_response=result.content,
                    result=None,
                    error="json_parse_failed",
                )
            result = repair
        except Exception as exc:  # noqa: BLE001
            return PersonaInvocation(
                persona_id=persona_id,
                raw_response=result.content,
                result=None,
                error=f"json_parse_repair_failed: {exc!r}",
            )
    return PersonaInvocation(
        persona_id=persona_id,
        raw_response=parsed,
        result=_parse_persona_payload(persona_id, parsed),
        error="",
    )


def _compose_user_prompt(chapter_text: str, extra_user_context: str) -> str:
    parts: list[str] = []
    if extra_user_context.strip():
        parts.append(extra_user_context.strip())
    parts.append("===== 章节正文 begin =====")
    parts.append(chapter_text)
    parts.append("===== 章节正文 end =====")
    parts.append(
        "请仅以本 persona 的视角输出 JSON, 含字段 overall_score / must_rewrite / "
        "verdict / top_3_issues (issue, severity, suggested_cause_id, evidence, "
        "specific_fix) / one_line_takeaway. JSON 必须可被 Python json.loads 解析。"
    )
    return "\n\n".join(parts)


def _parse_persona_payload(persona_id: str, payload: dict[str, Any]) -> PersonaResult:
    overall = payload.get("overall_score")
    try:
        overall_score = float(overall) if overall is not None else 0.0
    except (TypeError, ValueError):
        overall_score = 0.0

    issues_raw = payload.get("top_3_issues") or payload.get("issues") or []
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
                    severity=str(entry.get("severity") or "medium").strip() or "medium",
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
        must_rewrite=bool(payload.get("must_rewrite", False)),
        issues=tuple(issues),
        verdict=str(payload.get("verdict") or "").strip(),
        one_line_takeaway=str(payload.get("one_line_takeaway") or "").strip(),
    )


async def run_async_multi_persona_critique(
    *,
    chapter_text: str,
    extra_user_context: str = "",
    call_context: MultiPersonaCallContext | None = None,
    persona_ids: tuple[str, ...] | None = None,
) -> MultiPersonaExecution:
    """Production runner — calls ``services.llm`` four times in parallel.

    The aggregator is then run on whatever personas returned a usable
    :class:`PersonaResult`; personas that errored out are still
    captured in the ``invocations`` log so the orchestrator can audit
    failures.
    """

    config = load_critic_personas()
    targets = persona_ids or tuple(config.personas.keys())
    ctx = call_context or MultiPersonaCallContext()

    invocation_tasks = [
        _invoke_persona(
            persona_id,
            chapter_text=chapter_text,
            extra_user_context=extra_user_context,
            call_context=ctx,
        )
        for persona_id in targets
    ]
    invocations = await asyncio.gather(*invocation_tasks)
    results = [inv.result for inv in invocations if inv.result is not None]
    aggregate: AggregatedCritique = aggregate_persona_results(results)
    return MultiPersonaExecution(
        invocations=tuple(invocations),
        aggregate=aggregate,
    )
