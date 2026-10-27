"""Blind, bounded diagnostic of the production partition classifier.

Agent-authored references are comparison data, never production authority.
No function in this module changes a book or selects a replacement model.
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.ideology_kernel import cost_style_directive
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.model_catalog import get_model_catalog_entry
from bestseller.services.outline_llm_judge import _parse_json_object
from bestseller.services.world_authority import MAX_SOURCE_CHARS, world_authority_source_hash
from bestseller.services.world_source_partition import (
    PARTITION_SYSTEM_PROMPT,
    PARTITION_VERSION,
    _labels,
)
from bestseller.services.world_source_patch import SOURCE_PATCH_POLICY
from bestseller.settings import AppSettings

EVAL_VERSION = "1.0-semantic-contrast"
GRADER_VERSION = "1.1-per-case-diagnostic"
REFERENCE_STATUS = "agent_authored_requires_human_validation"


def prepare_eval(fixture: Mapping[str, Any]) -> dict[str, Any]:
    if (
        fixture.get("version") != EVAL_VERSION
        or fixture.get("reference_status") != REFERENCE_STATUS
    ):
        raise ValueError("PARTITION_EVAL_REFERENCE_VERSION")
    cases = fixture.get("cases")
    if not isinstance(cases, list) or len(cases) != 6:
        raise ValueError("PARTITION_EVAL_REQUIRES_SIX_CASES")
    targets: dict[str, dict[str, Any]] = {}
    rules: dict[str, Any] = {}
    reference = []
    case_ids = []
    for index, case in enumerate(cases, 1):
        if not isinstance(case, Mapping) or set(case) != {"id", "axis", "segments"}:
            raise ValueError("PARTITION_EVAL_INVALID_CASE")
        if not isinstance(case["id"], str) or not case["id"] or case["id"] in case_ids:
            raise ValueError("PARTITION_EVAL_DUPLICATE_CASE")
        if not isinstance(case["axis"], str) or not case["axis"].strip():
            raise ValueError("PARTITION_EVAL_MISSING_AXIS")
        segments = case["segments"]
        if not isinstance(segments, list) or not 1 <= len(segments) <= 64:
            raise ValueError("PARTITION_EVAL_INVALID_REFERENCE")
        clean = []
        for segment in segments:
            if not isinstance(segment, Mapping) or set(segment) != {"quote", "kind", "rationale"}:
                raise ValueError("PARTITION_EVAL_INVALID_REFERENCE")
            if not isinstance(segment["rationale"], str) or not segment["rationale"].strip():
                raise ValueError("PARTITION_EVAL_MISSING_RATIONALE")
            if not isinstance(segment["quote"], str):
                raise ValueError("PARTITION_EVAL_INVALID_REFERENCE")
            clean.append({"quote": segment["quote"], "kind": segment["kind"]})
        quote = "".join(segment["quote"] for segment in clean)
        if not 1 <= len(quote) <= 150:
            raise ValueError("PARTITION_EVAL_CASE_BUDGET")
        clause_id = f"C{index:03d}"
        rules[clause_id] = {"description": quote}
        targets[clause_id] = {
            "clause_id": clause_id,
            "quote": quote,
            "start": 0,
            "end": len(quote),
            "path": ["world", "rules", clause_id, "description"],
        }
        reference.append({"clause_id": clause_id, "segments": clean})
        case_ids.append(case["id"])
    sources = {
        "creation": {"cost_style": "minimal", "cost_rule": cost_style_directive("minimal")},
        "world": {"rules": rules},
    }
    source_hash = world_authority_source_hash(sources)
    expected = _labels(
        {"complete": True, "source_hash": source_hash, "clauses": reference},
        targets=targets,
        source_hash=source_hash,
    )
    context = json.dumps(
        {
            "sources": sources,
            "targets": list(targets.values()),
            "repair_policy": SOURCE_PATCH_POLICY,
        },
        ensure_ascii=False,
    )
    if len(context) > MAX_SOURCE_CHARS:
        raise ValueError("PARTITION_EVAL_INPUT_BUDGET")
    return {
        "source_hash": source_hash,
        "context": context,
        "targets": targets,
        "expected": expected,
        "reference_clauses": reference,
        "case_ids": case_ids,
        "fixture_hash": hashlib.sha256(json.dumps(fixture, sort_keys=True).encode()).hexdigest(),
    }


def grade_eval(payload: object, *, prepared: Mapping[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"complete", "source_hash", "clauses"}
        or payload.get("complete") is not True
        or payload.get("source_hash") != prepared["source_hash"]
    ):
        raise ValueError("PARTITION_EVAL_INVALID_ENVELOPE")
    rows = payload["clauses"]
    if not isinstance(rows, list) or len(rows) != len(prepared["targets"]):
        raise ValueError("PARTITION_EVAL_INCOMPLETE_COVERAGE")
    by_id = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("PARTITION_EVAL_INVALID_CLAUSE")
        clause_id = row.get("clause_id")
        if (
            not isinstance(clause_id, str)
            or clause_id not in prepared["targets"]
            or clause_id in by_id
        ):
            raise ValueError("PARTITION_EVAL_DUPLICATE_OR_UNKNOWN_CLAUSE")
        by_id[clause_id] = row
    cases: list[dict[str, Any]] = []
    for case_id, (clause_id, expected) in zip(
        prepared["case_ids"], prepared["expected"].items(), strict=True
    ):
        try:
            actual = _labels(
                {**payload, "clauses": [by_id[clause_id]]},
                targets={clause_id: prepared["targets"][clause_id]},
                source_hash=prepared["source_hash"],
            )[clause_id]
        except ValueError as exc:
            cases.append(
                {
                    "case_id": case_id,
                    "clause_id": clause_id,
                    "passed": None,
                    "status": "invalid",
                    "error": str(exc),
                }
            )
            continue
        mismatches = [(e, a) for e, a in zip(expected, actual, strict=True) if e != a]
        cases.append(
            {
                "case_id": case_id,
                "clause_id": clause_id,
                "passed": not mismatches,
                "status": "scored",
                "mismatch_chars": len(mismatches),
                "protected_removal_chars": sum(
                    e == "keep" and a == "remove_self_weakening" for e, a in mismatches
                ),
                "missed_removal_chars": sum(e == "remove_self_weakening" for e, _ in mismatches),
                "wrong_unknown_chars": sum(e == "unknown" or a == "unknown" for e, a in mismatches),
            }
        )
    scored = [row for row in cases if row["status"] == "scored"]
    invalid = len(cases) - len(scored)
    passed = sum(row["passed"] for row in scored)
    return {
        "status": "evaluation_failed"
        if invalid
        else ("reference_match" if passed == len(cases) else "reference_mismatch"),
        "grader_version": GRADER_VERSION,
        "passed_cases": passed,
        "total_cases": len(cases),
        "scored_cases": len(scored),
        "invalid_cases": invalid,
        "partial": bool(invalid and scored),
        "metrics_scope": "valid_cases_only" if invalid else "all_cases",
        "cases": cases,
        **{
            key: sum(row[key] for row in scored)
            for key in ("protected_removal_chars", "missed_removal_chars", "wrong_unknown_chars")
        },
        "promotion_allowed": False,
        "production_ready": False,
    }


def replay_partition_eval(
    report: Mapping[str, Any], *, fixture: Mapping[str, Any]
) -> dict[str, Any]:
    """Regrade a persisted response without another model call or source write."""
    prepared = prepare_eval(fixture)
    if (
        report.get("version") != EVAL_VERSION
        or report.get("fixture_hash") != prepared["fixture_hash"]
        or report.get("source_hash") != prepared["source_hash"]
    ):
        raise ValueError("PARTITION_EVAL_REPLAY_BINDING")
    if (
        report.get("fallback_used") is True
        or report.get("error") == "PARTITION_EVAL_PROVIDER_UNAVAILABLE"
        or report.get("provider") in {"mock", "fallback", "placeholder"}
        or report.get("finish_reason") in {"fallback", "length"}
    ):
        raise ValueError("PARTITION_EVAL_REPLAY_PROVIDER_UNAVAILABLE")
    if report.get("error") == "PARTITION_EVAL_MODEL_ROUTE_MISMATCH" or (
        report.get("requested_model") is not None
        and report["requested_model"] != report.get("model_name")
    ):
        raise ValueError("PARTITION_EVAL_REPLAY_MODEL_ROUTE_MISMATCH")
    run_ids = report.get("llm_run_ids")
    if not isinstance(run_ids, list) or len(run_ids) != 1:
        raise ValueError("PARTITION_EVAL_REPLAY_RECEIPT")
    UUID(str(run_ids[0]))
    raw = report.get("raw_response")
    if not isinstance(raw, str) or not raw:
        raise ValueError("PARTITION_EVAL_REPLAY_NO_RAW")
    response = _parse_json_object(raw)
    if report.get("response") is not None and report["response"] != response:
        raise ValueError("PARTITION_EVAL_REPLAY_RESPONSE_MISMATCH")
    result = copy.deepcopy(dict(report))
    result["original_status"] = report.get("status")
    result["original_error"] = result.pop("error", None)
    result.update(grade_eval(response, prepared=prepared))
    result["new_llm_calls"] = 0
    result["execution_kind"] = "offline_replay"
    return result


async def run_partition_eval(
    session: AsyncSession,
    settings: AppSettings,
    *,
    fixture: Mapping[str, Any],
    project_id: UUID,
    shadow_judge: bool = False,
) -> dict[str, Any]:
    prepared = prepare_eval(fixture)
    catalog_key = settings.llm.independent_judge_primary_model_key if shadow_judge else None
    expected_model = None
    if shadow_judge:
        entry = get_model_catalog_entry(catalog_key) if catalog_key else None
        if entry is None or not entry.available:
            raise ValueError("PARTITION_EVAL_SHADOW_UNAVAILABLE")
        expected_model = entry.model
    report: dict[str, Any] = {
        "version": EVAL_VERSION,
        "reference_status": REFERENCE_STATUS,
        "judge_route": "configured_primary_shadow" if shadow_judge else "project_route",
        "requested_catalog_key": catalog_key,
        "requested_model": expected_model,
        "fixture_hash": prepared["fixture_hash"],
        "source_hash": prepared["source_hash"],
        "prompt_version": PARTITION_VERSION,
        "system_prompt_hash": hashlib.sha256(PARTITION_SYSTEM_PROMPT.encode()).hexdigest(),
        "status": "evaluation_failed",
        "production_ready": False,
        "promotion_allowed": False,
        "raw_response": "",
        "llm_run_ids": [],
        "total_cases": 6,
        "passed_cases": None,
        "cases": [
            {"case_id": case_id, "passed": None, "status": "not_scored"}
            for case_id in prepared["case_ids"]
        ],
    }
    try:
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="critic",
                system_prompt=PARTITION_SYSTEM_PROMPT,
                user_prompt=f"source_hash={prepared['source_hash']}\n{prepared['context']}",
                fallback_response='{"complete":false}',
                prompt_template="world_partition_eval",
                prompt_version=EVAL_VERSION,
                project_id=project_id,
                model_catalog_key=catalog_key,
                max_tokens_override=3000,
                temperature_override=0.1,
                metadata={
                    "fixture_hash": prepared["fixture_hash"],
                    "diagnostic_only": True,
                    "judge_route": report["judge_route"],
                },
            ),
        )
    except Exception as exc:
        report["error"] = "PARTITION_EVAL_PROVIDER_ERROR"
        report["error_type"] = type(exc).__name__
        return report
    report.update(
        {
            "raw_response": completion.content[:12000],
            "provider": completion.provider,
            "model_name": completion.model_name,
            "finish_reason": completion.finish_reason,
            "fallback_used": completion.fallback_used,
            "fallback_source": completion.fallback_source,
            "llm_run_ids": [str(completion.llm_run_id)] if completion.llm_run_id else [],
        }
    )
    try:
        if not completion.llm_run_id:
            raise ValueError("PARTITION_EVAL_MISSING_RECEIPT")
        UUID(str(completion.llm_run_id))
        if expected_model is not None and completion.model_name != expected_model:
            raise ValueError("PARTITION_EVAL_MODEL_ROUTE_MISMATCH")
        if (
            completion.fallback_used
            or completion.finish_reason in {"fallback", "length"}
            or completion.provider.lower() in {"fallback", "mock", "placeholder"}
        ):
            raise ValueError("PARTITION_EVAL_PROVIDER_UNAVAILABLE")
        payload = _parse_json_object(completion.content)
        report["response"] = payload
        report.update(grade_eval(payload, prepared=prepared))
    except ValueError as exc:
        report["error"] = str(exc)
    return report
