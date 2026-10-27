"""Bounded, source-grounded repair for a concept card.

This module deliberately knows nothing about a particular book or model provider.
Callers supply the judge, generator, and full rejudge callbacks.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import math
from typing import Any

_ALLOWED_FIELDS = frozenset({"core_abnormality", "opening_crisis", "reader_promise"})


@dataclass(frozen=True)
class ConceptSourceRepairResult:
    edited_card: dict[str, Any] | None
    status: str
    reason: str
    diagnosis: dict[str, Any] | None = None
    changed_fields: tuple[str, ...] = field(default_factory=tuple)


JudgeFn = Callable[[Mapping[str, Any], str], Awaitable[dict[str, Any]]]
GenerateFn = Callable[[Mapping[str, Any], str, Mapping[str, Any]], Awaitable[dict[str, Any]]]
FullRejudgeFn = Callable[[Mapping[str, Any], str], Awaitable[bool]]


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        result: list[str] = []
        for child in value.values():
            result.extend(_strings(child))
        return result
    if isinstance(value, (list, tuple, set)):
        result = []
        for child in value:
            result.extend(_strings(child))
        return result
    return []


def _contains_text(value: object, needle: str) -> bool:
    return any(needle in text for text in _strings(value))


def _eligible(
    initial_failed_axes: object,
    initial_seed_fidelity_evidence: object,
    initial_scores: object,
    engine_floor: float,
) -> tuple[bool, str]:
    if not isinstance(initial_failed_axes, (list, tuple, set)) or set(initial_failed_axes) != {
        "seed_fidelity"
    }:
        return False, "failed_axes_must_be_exactly_seed_fidelity"
    if (
        not isinstance(initial_seed_fidelity_evidence, Mapping)
        or initial_seed_fidelity_evidence.get("status") != "verified"
    ):
        return False, "seed_fidelity_evidence_not_verified"
    if not isinstance(initial_scores, Mapping) or not initial_scores:
        return False, "initial_scores_missing"
    if "seed_fidelity" not in initial_scores or len(initial_scores) < 2:
        return False, "initial_scores_must_include_seed_and_another_axis"
    if not math.isfinite(engine_floor):
        return False, "engine_floor_must_be_finite"
    for axis, score in initial_scores.items():
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(score)
        ):
            return False, f"score_must_be_finite_number:{axis}"
        if axis == "seed_fidelity":
            continue
        if not isinstance(score, (int, float)) or isinstance(score, bool) or score < engine_floor:
            return False, f"score_below_engine_floor:{axis}"
    return True, "eligible"


def _valid_diagnosis(
    diagnosis: object, card: Mapping[str, Any], authority: str
) -> tuple[bool, str, dict[str, Any] | None, tuple[str, ...]]:
    if not isinstance(diagnosis, dict):
        return False, "diagnosis_not_object", None, ()
    if diagnosis.get("decision") != "missing_only":
        return False, "diagnosis_decision_must_be_missing_only", None, ()
    facts = diagnosis.get("missing_facts")
    if not isinstance(facts, list) or not 1 <= len(facts) <= 3:
        return False, "missing_facts_must_contain_one_to_three_items", None, ()
    fields: list[str] = []
    for fact in facts:
        if not isinstance(fact, Mapping):
            return False, "missing_fact_not_object", None, ()
        if set(fact) != {"source_quote", "candidate_field"}:
            return False, "missing_fact_keys_must_be_exact", None, ()
        field_name = fact.get("candidate_field")
        quote = fact.get("source_quote")
        if field_name not in _ALLOWED_FIELDS:
            return False, "candidate_field_not_allowed_or_duplicated", None, ()
        if not isinstance(quote, str) or not quote.strip() or quote != quote.strip():
            return False, "source_quote_must_be_nonempty_string", None, ()
        if not _contains_text(authority, quote):
            return False, "source_quote_not_in_authority", None, ()
        if _contains_text(card, quote):
            return False, "source_quote_already_exists_in_card", None, ()
        if field_name not in fields:
            fields.append(field_name)
    # Do not pass arbitrary model commentary onward as a repair contract.
    normalized = {
        "decision": "missing_only",
        "missing_facts": [
            {"source_quote": item["source_quote"], "candidate_field": item["candidate_field"]}
            for item in facts
        ],
    }
    return True, "diagnosis_valid", normalized, tuple(fields)


async def repair_concept_source(
    card: Mapping[str, Any],
    authority: str,
    judge_fn: JudgeFn,
    gen_fn: GenerateFn,
    full_rejudge_fn: FullRejudgeFn,
    *,
    initial_failed_axes: object,
    initial_seed_fidelity_evidence: object,
    initial_scores: object,
    engine_floor: float = 7,
) -> ConceptSourceRepairResult:
    """Attempt at most one source-grounded concept repair and one full rejudge."""
    if not isinstance(authority, str) or not authority.strip():
        return ConceptSourceRepairResult(None, "ineligible", "authority_must_be_nonempty_string")
    eligible, eligibility_reason = _eligible(
        initial_failed_axes, initial_seed_fidelity_evidence, initial_scores, engine_floor
    )
    if not eligible:
        return ConceptSourceRepairResult(None, "ineligible", eligibility_reason)

    try:
        diagnosis_raw = await judge_fn(deepcopy(card), authority)
    except Exception as exc:
        return ConceptSourceRepairResult(None, "failed", f"judge_error:{type(exc).__name__}")
    valid, reason, diagnosis, fields = _valid_diagnosis(diagnosis_raw, card, authority)
    if not valid or diagnosis is None:
        return ConceptSourceRepairResult(
            None, "rejected", reason, diagnosis_raw if isinstance(diagnosis_raw, dict) else None
        )

    try:
        patch = await gen_fn(deepcopy(card), authority, deepcopy(diagnosis))
    except Exception as exc:
        return ConceptSourceRepairResult(
            None, "failed", f"generation_error:{type(exc).__name__}", diagnosis, fields
        )
    if (
        not isinstance(patch, dict)
        or set(patch) != set(fields)
        or any(not isinstance(value, str) or not value.strip() for value in patch.values())
    ):
        return ConceptSourceRepairResult(
            None, "rejected", "patch_must_match_diagnosed_fields", diagnosis, fields
        )

    edited_card = deepcopy(dict(card))
    edited_card.update(patch)
    try:
        passed = await full_rejudge_fn(deepcopy(edited_card), authority)
    except Exception as exc:
        return ConceptSourceRepairResult(
            None, "failed", f"full_rejudge_error:{type(exc).__name__}", diagnosis, fields
        )
    if passed is not True:
        return ConceptSourceRepairResult(None, "rejected", "full_rejudge_failed", diagnosis, fields)
    return ConceptSourceRepairResult(
        edited_card, "repaired", "full_rejudge_passed", diagnosis, fields
    )
