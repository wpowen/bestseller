"""Prove request purpose from original retained rows, without attributing an owner.

Callers supply the root operation and ALL its actual attempts, original linked
runs, transport results and failure receipts. This is not a financial closure,
replay, grade, project/workflow/body attribution or budget-credit authorization.
Missing, malformed and conflicting evidence all fail closed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any
from uuid import UUID

# Known producer routes, not a permissive planner_/critic_ prefix exemption.
_ROUTES = {
    'critic': frozenset({
        'draft_continuity_hard_safety',
        'blurb_coherence_judge',
        'blurb_logic_axis_prosecutor',
        'chapter_window_quality_judge',
        'concept_tournament_finalist_judge',
        'concept_tournament_finalist_seriality_judge',
        'concept_tournament_judge',
        'concept_tournament_premise_judge',
        'concept_tournament_raw_idea_rank',
        'concept_tournament_story_judge',
        'conception_cast_reality_audit',
        'conception_candidate_effect_contract',
        'conception_preproject_materialized_seed_fidelity_judge',
        'conception_final_packaging_canonical_quote_recovery',
        'conception_final_packaging_seed_fidelity_arbitration',
        'conception_final_packaging_seed_fidelity_judge',
        'conception_intent_alignment',
        'conception_repair_guard',
        'conception_review',
        'hook_defect_judge',
        'hook_pull_judge',
        'litstyle_prose_judge',
        'logline_gate',
        'outline_commercial_judge',
        'outline_commercial_judge_consistency_adjudication',
        'outline_reader_experience_judge',
        'market_validation_collision_judge',
        'persona_click_judge',
        'planner_source_bound_expansion_evidence_repair',
        'planner_source_bound_expansion_semantic_review',
        'planning_draft_admission',
        'premise_appeal_judge',
        'world_law_consistency',
    }),
    'planner': frozenset({
        'chapter_outline_prefix_surgical_repair',
        'concept_methodology',
        'concept_tournament_candidate',
        'concept_tournament_engine_kernel',
        'concept_tournament_raw_idea_pool',
        'concept_tournament_seriality',
        'conception_character',
        'conception_commercial_positioning',
        'conception_concept_seed_final_premise_reconciliation',
        'conception_market',
        'conception_world',
        'ideology_kernel',
        'planner_book_spec',
        'planner_cast_spec',
        'planner_cast_spec_antagonist_lifecycle_repair',
        'planner_cast_spec_relationship_scaling_repair',
        'planner_cast_spec_source_bound_expansion',
        'planner_cast_spec_source_bound_identity_completion',
        'planner_cast_person_part',
        'planner_cast_links_part',
        'planner_cast_field_correction',
        'planner_structured_field_correction',
        'planner_fanqie_beat_sheet',
        'planner_story_design_kernel',
        'planner_volume_plan',
        'planner_world_spec',
        'story_architect_v1',
    }),
    'editor': frozenset({
        'blurb_copywriter_candidate',
        'blurb_copywriter_polish',
        'canon_contradiction_repair',
        'chapter_rewrite_repair',
        'conception_final_packaging_seed_fidelity_repair',
        'conception_final_packaging_seed_fidelity_repair_v2',
        'conception_finalize',
        'conception_intent_repair',
        'conception_logline_from_champion',
        'conception_logline_regen',
        'conception_structure_repair',
        'title_story_grounded_revision',
        'title_tournament_candidates',
    }),
}
_VOLUME_ROUTE = re.compile(
    r"planner_volume_[1-9][0-9]*_chapter_outline_(?:"
    r"batch_[1-9][0-9]*_[1-9][0-9]*(?:_story_enhancer_route_repair)?|"
    r"commercial_surgical_repair)"
)
# Append exact producer versions; do not widen a template prefix or infer a
# legacy version. These classify purpose only, with all physical/fee guards.
_VERSIONED_ROUTES = frozenset({
    ('critic', 'chapter_review', '7.0-citation-tuple-submission'),
    ('critic', 'fanqie_short_semantic_review', 'stage.3'),
    ('critic', 'fanqie_short_semantic_review', 'stage.4'),
    ('critic', 'fanqie_short_semantic_review', 'stage.5'),
    ('planner', 'planner_outline_domain_field_corrections', '3.6-domain-fields'),
    ('planner', 'planner_outline_domain_flat_field_corrections', '3.7-domain-fields-flat'),
    ('planner', 'planner_outline_source_consistency', '3.8-source-consistency'),
})
_OWNER_KEYS = ("project_id", "workflow_run_id", "step_run_id")


def _require(condition: Any) -> None:
    if not condition:
        raise ValueError("noncommercial_request_unproven")


def _sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _metadata(row: Mapping) -> Mapping:
    if "metadata" in row and "metadata_json" in row:
        _require(row["metadata"] == row["metadata_json"])
    meta = row.get("metadata_json", row.get("metadata", {}))
    _require(isinstance(meta, Mapping))
    return meta


def _owners(row: Mapping) -> None:
    for key in _OWNER_KEYS:
        if row.get(key) is not None:
            _require(isinstance(row[key], str) and str(UUID(row[key])) == row[key])


def _route(row: Mapping) -> tuple[str, str, str | None]:
    role, template, version = (row.get(k) for k in (
        "logical_role", "prompt_template", "prompt_version"
    ))
    _require(isinstance(role, str) and isinstance(template, str))
    _require(version is None or (isinstance(version, str) and bool(version.strip())))
    _require((role, template, version) in _VERSIONED_ROUTES or template in _ROUTES.get(role, ()) or (
        role == "planner" and _VOLUME_ROUTE.fullmatch(template) is not None
    ) or (
        role == 'critic' and re.fullmatch(
            r'conception_final_packaging_seed_fidelity_judge_candidate_v2_[0-9a-f]{20}',template) is not None
    ))
    _owners(row)
    return role, template, version


def _no_commercial_markers(value: Any) -> None:
    # Check structured request/result provenance and tool arguments, including
    # nested raw_message tool calls. Ordinary prose is not route evidence.
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {"content", "text", "body"}:
                continue
            _require(not str(key).startswith("commercial_"))
            if isinstance(item, str):
                _require("submit_chapter_review" not in item)
                _require("chapter_commercial_quality_judge" not in item)
            _no_commercial_markers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _no_commercial_markers(item)
    elif isinstance(value, str):
        _require("submit_chapter_review" not in value)
        _require("chapter_commercial_quality_judge" not in value)


def _origin(meta: Mapping, h: str) -> Mapping | None:
    origin = meta.get("paid_call_origin")
    if origin is None:
        return None
    _require(isinstance(origin, Mapping) and origin.get("schema") == "paid-call-origin.v1")
    if origin.get("status") == "unknown":
        # The canonical legacy marker is absence of evidence, not a route.
        _require(origin.get("legacy") is True)
        _require(all(origin.get(k) is None for k in (
            *_OWNER_KEYS, "logical_role", "prompt_template", "prompt_version", "prompt_hash"
        )))
        return None
    _require(origin.get("status") == "recorded" and origin.get("prompt_hash") == h)
    _require(set((*_OWNER_KEYS, "logical_role", "prompt_template", "prompt_version")) <= origin.keys())
    _route(origin)
    return origin


def _execution(meta: Mapping, scope: str, key: str, attempts: Mapping) -> None:
    receipt = meta.get("execution_receipt")
    _require(isinstance(receipt, Mapping))
    _require(receipt.get("scope_id") == scope and receipt.get("operation_key") == key)
    declared = receipt.get("attempts")
    _require(isinstance(declared, list) and len(declared) == len(attempts))
    seen = set()
    for item in declared:
        _require(isinstance(item, Mapping))
        aid = item.get("attempt_id")
        _require(isinstance(aid, str) and aid in attempts and aid not in seen)
        seen.add(aid)
        actual = attempts[aid]
        _require(item.get("model") == actual["model_name"])
        _require(item.get("effective_input_hash") == actual["input_hash"])
        if actual.get("status") == "rejected":
            _require(item.get("status") == "rejected")


def _effective(meta: Mapping, h: str, model: str) -> None:
    effective = meta.get("effective_input_receipt")
    _require(isinstance(effective, Mapping))
    _require(effective.get("schema") == "effective-input.v1")
    _require(effective.get("hash") == h and effective.get("model") == model)


def prove_known_noncommercial_request(
    *,
    operation: Mapping,
    actual_attempts: Sequence[Mapping],
    original_runs: Sequence[Mapping] = (),
    transport_results: Sequence[Mapping] = (),
    failure_receipts: Sequence[Mapping] = (),
    equivalent_requests: Sequence[Mapping] = (),
) -> bool:
    """Return purpose proof only; NULL ownership stays NULL, never inferred.

    A stopped 422 request may be proved by its original physical failure
    receipt. It remains a sent/rejected request, never a free/replayable one.
    The caller, not this pure helper, queries the complete original row sets.
    Equivalent completed native-primary requests can prove only the purpose
    of an unversioned charged failure; no result, owner or version is copied.
    """
    try:
        return _prove(operation, actual_attempts, original_runs, transport_results, failure_receipts,
                      equivalent_requests)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return False


def _prove(operation, actual_attempts, original_runs, transport_results, failure_receipts,
           equivalent_requests=()) -> bool:
    _require(isinstance(operation, Mapping))
    for rows in (actual_attempts, original_runs, transport_results, failure_receipts, equivalent_requests):
        _require(isinstance(rows, (list, tuple)))
        _require(all(isinstance(row, Mapping) for row in rows))
    # Validate JSON data without modifying it; reject NaN and unsupported values.
    json.dumps([operation, actual_attempts, original_runs, transport_results, failure_receipts,
                equivalent_requests],
               allow_nan=False)
    scope, key, h = (operation.get(k) for k in ("scope_id", "key", "input_hash"))
    _require(isinstance(scope, str) and bool(scope) and _sha(key) and _sha(h))
    attempts = {}
    for attempt in actual_attempts:
        aid = attempt.get("id")
        _require(isinstance(aid, str) and aid and aid not in attempts)
        _require(attempt.get("scope_id") == scope and attempt.get("operation_key") == key)
        _require(attempt.get("input_hash") == h)
        _require(isinstance(attempt.get("model_name"), str) and bool(attempt["model_name"]))
        attempts[aid] = attempt
    _require(attempts)
    for row in [operation, *original_runs, *transport_results, *failure_receipts]:
        _no_commercial_markers({k: v for k, v in row.items() if k != "content"})
    if operation.get("status") == "stopped":
        _require(operation.get("result_json") is None and not original_runs and not transport_results)
        _require(len(failure_receipts) == len(attempts))
        seen = set()
        routes = []
        for row in failure_receipts:
            receipt = _transport(row, "transport-failure.v1", scope, key, h, attempts, seen)
            attempt = attempts[receipt["attempt_id"]]
            _require(type(receipt.get("physical_sends")) is int and receipt["physical_sends"] == 1)
            _require(receipt.get("operator_interruption") is False)
            if attempt.get("status") == "reconciled_charged":
                # The original failure descriptor binds purpose through the
                # exact root/hash/attempt/control key. Billing/outcome remain
                # unknown and charged; this proves no result, closure or credit.
                _require(attempt.get("actual_tokens") is None)
                _require(isinstance(receipt.get("exception_type"), str)
                         and bool(receipt["exception_type"]))
                if (receipt.get("logical_role"), receipt.get("prompt_template"),
                        receipt.get("prompt_version")) == ("critic", "chapter_review", None):
                    _require(len(attempts) == 1)
                    _prove_equivalent_primary(operation, attempts, equivalent_requests)
                    routes.append(("critic", "chapter_review", None))
                else:
                    routes.append(_route(receipt))
            else:
                _require(attempt.get("status") == "rejected")
                _require(type(attempt.get("actual_tokens")) is int and attempt["actual_tokens"] == 0)
                _require(type(receipt.get("http_status")) is int and receipt["http_status"] == 422)
                _require(receipt.get("logical_role") == "editor")
                _require(receipt.get("prompt_template") == "chapter_rewrite_repair")
                routes.append(_route(receipt))
            _owners(receipt)
        _require(len({r[:2] for r in routes}) == 1)
        return True
    _require(operation.get("status") == "completed" and not failure_receipts)
    _require(all(
        a.get("status") in {"completed", "reconciled_charged"}
        or (a.get("status") == "rejected" and type(a.get("actual_tokens")) is int
            and a["actual_tokens"] == 0)
        for a in attempts.values()
    ))
    _require(any(a.get("status") in {"completed", "reconciled_charged"} for a in attempts.values()))
    paid = operation.get("result_json")
    _require(isinstance(paid, Mapping))
    model = paid.get("model_name")
    _require(isinstance(model, str) and model and all(a["model_name"] == model for a in attempts.values()))
    results = [paid]
    seen = set()
    for row in transport_results:
        receipt = _transport(row, "transport-result.v1", scope, key, h, attempts, seen)
        _require(attempts[receipt["attempt_id"]].get("status") in {"completed", "reconciled_charged"})
        _require(receipt.get("input_hash") == h and isinstance(receipt.get("result"), Mapping))
        results.append(receipt["result"])
    origins, routes = [], []
    for result in results:
        _require(result.get("model_name") == model)
        meta = _metadata(result)
        _effective(meta, h, model)
        _execution(meta, scope, key, attempts)
        origin = _origin(meta, h)
        if origin is not None:
            origins.append(origin)
            routes.append(_route(origin))
    run_ids = set()
    for run in original_runs:
        rid = run.get("id")
        _require(isinstance(rid, str) and rid and rid not in run_ids)
        run_ids.add(rid)
        _require(run.get("prompt_hash") == h and run.get("model_name") == model)
        meta = _metadata(run)
        _effective(meta, h, model)
        _execution(meta, scope, key, attempts)
        route = _route(run)
        origin = _origin(meta, h)
        # A recovered telemetry row alone cannot invent the original route.
        _require(origin is not None or "execution_recovery" not in meta or origins)
        if origin is not None:
            origin_route = _route(origin)
            _require(origin_route[:2] == route[:2])
            _require(origin_route[2] is None or route[2] is None or origin_route[2] == route[2])
            routes.append(origin_route)
        routes.append(route)
    _require(routes and len({r[:2] for r in routes}) == 1)
    _require(len({r[2] for r in routes if r[2] is not None}) <= 1)
    # Owners may differ on restored telemetry. Their validity is checked but
    # purpose proof neither reconciles nor asserts project/workflow ownership.
    return True


def _prove_equivalent_primary(operation, attempts, requests) -> None:
    """Same effective wire input proves purpose, never another send's outcome.

    Every supplied witness must independently pass the original proof. Alias
    chains are forbidden, and the original paid origin must explicitly name
    native7; restored run attribution alone cannot supply that assertion.
    """
    _require(requests)
    fields = {"operation", "actual_attempts", "original_runs", "transport_results", "failure_receipts"}
    for request in requests:
        _require(set(request) <= fields)
        root = request.get("operation")
        _require(isinstance(root, Mapping) and root.get("status") == "completed")
        _require(root.get("key") != operation["key"])
        _require(root.get("scope_id") == operation["scope_id"]
                 and root.get("input_hash") == operation["input_hash"])
        _require(_prove(root, request.get("actual_attempts"), request.get("original_runs", ()),
                        request.get("transport_results", ()), request.get("failure_receipts", ())))
        paid = root["result_json"]
        _require(all(a["model_name"] == paid["model_name"] for a in attempts.values()))
        origin = _origin(_metadata(paid), operation["input_hash"])
        _require(origin is not None and _route(origin) == (
            "critic", "chapter_review", "7.0-citation-tuple-submission"))


def _transport(row, schema, scope, key, h, attempts, seen) -> Mapping:
    status = "transport_result" if schema == "transport-result.v1" else "transport_failure_receipt"
    _require(row.get("status") == status and row.get("scope_id") == scope)
    _require(row.get("input_hash") == h)
    receipt = row.get("result_json")
    _require(isinstance(receipt, Mapping) and receipt.get("schema") == schema)
    aid = receipt.get("attempt_id")
    _require(isinstance(aid, str) and aid in attempts and aid not in seen)
    seen.add(aid)
    _require(receipt.get("operation_key") == key)
    expected = hashlib.sha256(f"{schema}\n{scope}\n{aid}".encode()).hexdigest()
    _require(row.get("key") == expected)
    return receipt
