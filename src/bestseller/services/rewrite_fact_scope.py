"""Derive a bounded edit contract for verified fact-only repair tasks.

The caller authenticates the available report/cycle and paid return. This
contract narrows an authorized repair to exact current-body quotations; it
never makes a quotation Canon, waives another blocker or grants a grade.
"""
from copy import deepcopy
import hashlib
import json

from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
from bestseller.services.chapter_draft_candidate_eligibility import verified_commercial_actions
from bestseller.services.critic_evidence_gate import build_critic_body_anchors
from bestseller.services.primary_review_submission import parse_primary_review_submission
from bestseller.services.rewrite_patch import text_hash


def derive_fact_repair_scope(parent_text, report_payload):
    """Return a backend scope only when all effective blockers are cited facts.

    A primary/window rejection, quality threshold miss, legacy unbound report,
    or missing citation cannot be silently converted into fact-only authority.
    Those tasks retain their existing repair route. After primary/window PASS
    establishes applicability, corrupt commercial proof raises before dispatch
    rather than authorizing an unrestricted rewrite.
    """
    if not isinstance(report_payload, dict):
        return None
    summary = report_payload.get('evidence_summary', {})
    if not isinstance(summary, dict) or 'commercial_action_projection' not in summary:
        return None
    projection = summary['commercial_action_projection']
    binding = projection.get('source_binding') if isinstance(projection, dict) else None
    if (not isinstance(binding, dict) or any(
            not isinstance(binding.get(key), str) or len(binding[key]) != 64
            or any(char not in '0123456789abcdef' for char in binding[key])
            for key in ('body_sha256', 'previous_sha256', 'semantic_source_sha256', 'source_protocol_sha256'))
            or binding['body_sha256'] != text_hash(parent_text)):
        raise ValueError('commercial_action_proof_unverified')
    # Establish applicability before probing the stricter optional scope proof.
    canonical, primary, errors = parse_primary_review_submission(
        report_payload.get('primary_submission_original'), parent_text)
    if (errors or primary != report_payload.get('primary_submission_receipt')
            or primary.get('parsed_payload', {}).get('verdict') != 'pass'
            or canonical != report_payload.get('critic_response')):
        return None
    window = summary.get('llm_window_quality_judge')
    if not isinstance(window, dict):
        return None
    window = LLMQualityJudgeResult.model_validate(window)
    if window.infrastructure_failure or not window.passed or window.blocking_issues or not window.llm_run_id:
        return None
    actions = verified_commercial_actions(summary, parent_text)
    blocking = [a for a in actions if a['effective_action'] == 'block']
    if not blocking or any(a['authority'] != 'verified_source' for a in blocking):
        return None
    catalog = {c['id']: c for c in build_critic_body_anchors(parent_text)}
    spans = {}
    for action in blocking:
        for origin in action['lineage']:
            citations = origin['issue'].get('body_citations', [])
            if not citations:
                return None
            for citation in citations:
                identity, quote = citation.get('id'), citation.get('quote')
                anchor = catalog.get(identity)
                if (citation.get('kind') != 'current' or anchor is None
                        or quote != anchor['text'] or parent_text.count(quote) != 1):
                    return None
                spans[identity] = {'id': identity, 'start': anchor['start'], 'end': anchor['end'],
                                   'quote': quote, 'sha256': text_hash(quote)}
    spans = sorted(spans.values(), key=lambda row: (row['start'], row['end']))
    if not spans or any(a['end'] > b['start'] for a, b in zip(spans, spans[1:])):
        return None
    if sum(len(row['quote'].encode()) for row in spans) > len(parent_text.encode()) * .25:
        return None
    return {
        'schema': 'verified-fact-repair-scope.v1', 'parent_sha256': text_hash(parent_text),
        'report_payload_sha256': text_hash(json.dumps(report_payload, ensure_ascii=False,
            sort_keys=True, separators=(',', ':'), allow_nan=False)),
        'action_projection_sha256': summary['commercial_action_projection']['projection_sha256'],
        'authorized_spans': deepcopy(spans), 'blocking_actions': deepcopy(blocking),
        'authority': 'backend_edit_scope_for_verified_fact_repair',
        'outside_scope': 'preserve_exact_parent_bytes', 'fresh_review_required': True,
        'adoption_authorized': False, 'grades_modified': False, 'grants_credit': False,
    }


def _scope_input_diagnostic(field: str, value: object) -> dict:
    """Safe input fingerprints only; unencodable data keeps an unknown digest."""
    encoding = "utf8" if field == "parent_text" and isinstance(value, str) else "canonical_json"
    summary = {"field": field, "type": type(value).__name__, "encoding": encoding}
    try:
        wire = value if encoding == "utf8" else json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        raw = wire.encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return {**summary, "encoding": "unavailable", "length": None, "sha256": None}
    return {**summary, "length": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


class FactRepairScopeUnavailableError(ValueError):
    """No edit authority; diagnostics contain no body, quotation or report text."""

    def __init__(self, parent_text: object, report_payload: object) -> None:
        self.code = "fact_repair_scope_unavailable"
        self.diagnostics = [
            _scope_input_diagnostic("parent_text", parent_text),
            _scope_input_diagnostic("report_payload", report_payload),
        ]
        super().__init__(self.code)


def require_fact_repair_scope(parent_text: str, report_payload: object) -> dict:
    """Require a bounded fact-only contract; None never selects another route.

    The caller authenticates and classifies the task. All existing proof errors
    propagate unchanged, and the original optional derivation remains available.
    """
    scope = derive_fact_repair_scope(parent_text, report_payload)
    if scope is None:
        raise FactRepairScopeUnavailableError(parent_text, report_payload)
    return scope


class ReaderFirstRepairRouteUnavailableError(ValueError):
    """Missing native classification evidence is never editorial permission."""

    def __init__(self, parent_text: object, report_payload: object) -> None:
        self.code = "reader_first_repair_route_unavailable"
        self.diagnostics = [
            _scope_input_diagnostic("parent_text", parent_text),
            _scope_input_diagnostic("report_payload", report_payload),
        ]
        super().__init__(self.code)


def reader_first_repair_route(parent_text: str, report_payload: object) -> dict:
    """Classify all verified proposals and debts without granting a body edit.

    The caller still authenticates the native task and current parent. A valid
    primary rewrite is an independent editorial proposal, even when commercial
    actions are audit-only. Neither this result nor unavailable fact scope can
    authorize an unrestricted rewrite or replace primary/window PASS in derive.
    """
    unavailable = ReaderFirstRepairRouteUnavailableError(parent_text, report_payload)
    if not isinstance(parent_text, str) or not parent_text.strip() or not isinstance(report_payload, dict):
        raise unavailable
    try:
        canonical, primary, errors = parse_primary_review_submission(
            report_payload.get("primary_submission_original"), parent_text,
        )
        if (errors or primary.get("status") != "verified"
                or primary != report_payload.get("primary_submission_receipt")
                or canonical != report_payload.get("critic_response")):
            raise unavailable
        payload = primary["parsed_payload"]
        if payload["verdict"] not in {"pass", "rewrite"}:
            raise unavailable
        primary_ids = [primary["catalog"][item["paragraph_number"] - 1]["id"]
                       for item in payload["citations"]]
        hashes = {name: text_hash(json.dumps(value, ensure_ascii=False, sort_keys=True,
                  separators=(",", ":"), allow_nan=False)) for name, value in (
                      ("report_payload_sha256", report_payload), ("primary_receipt_sha256", primary),
                  )}
    except (KeyError, IndexError, TypeError, ValueError, UnicodeError, RecursionError):
        raise unavailable from None

    summary = report_payload.get("evidence_summary")
    # Keep public proof/policy errors intact. Do not classify cached action JSON
    # or substitute the weaker private action reader for this complete proof.
    actions = verified_commercial_actions(summary, parent_text)
    groups = {name: [] for name in (
        "fact_action_indexes", "editorial_action_indexes",
        "audit_action_indexes", "unresolved_action_indexes",
    )}
    reasons = []
    for index, action in enumerate(actions):
        kind = (action["effective_action"], action["authority"], action["assessment_kind"])
        if action["effective_action"] == "audit_only":
            group = "audit_action_indexes"
        elif kind in {
            ("block", "verified_source", "current_fact_conflict"),
            ("block", "verified_source", "prior_fact_conflict"),
            ("block", "verified_source", "constraint_violation"),
        }:
            group = "fact_action_indexes"
        elif kind == ("block", "craft_only", "narrative_quality"):
            group = "editorial_action_indexes"
        else:
            group = "unresolved_action_indexes"
            if kind == ("block", "retained_evaluation_policy", "retained_threshold_policy"):
                if "commercial_threshold_blocker" not in reasons:
                    reasons.append("commercial_threshold_blocker")
        groups[group].append(index)

    editorial = payload["verdict"] == "rewrite" or bool(groups["editorial_action_indexes"])
    facts = bool(groups["fact_action_indexes"])
    mixed = facts and editorial
    if mixed:
        reasons.insert(0, "mixed_fact_editorial")
    window = summary.get("llm_window_quality_judge")
    if not isinstance(window, dict) or not window.get("llm_run_id"):
        reasons.append("window_unavailable")
    else:
        try:
            parsed_window = LLMQualityJudgeResult.model_validate(window)
            if parsed_window.infrastructure_failure or not parsed_window.passed or parsed_window.blocking_issues:
                reasons.append("window_not_passed")
        except ValueError:
            reasons.append("window_not_passed")
    if groups["unresolved_action_indexes"] or {"window_unavailable", "window_not_passed"}.intersection(reasons):
        route = "unknown"
    elif mixed:
        route = "mixed"
    elif facts:
        route = "fact_only"
    elif editorial:
        route = "editorial_only"
    else:
        route = "no_body_edit"
    return {
        "schema": "reader-first-repair-routing.v1", "route": route,
        "parent_sha256": text_hash(parent_text), **hashes,
        "action_projection_sha256": summary["commercial_action_projection"]["projection_sha256"],
        "primary_editorial_proposal": payload["verdict"] == "rewrite",
        "primary_citation_ids": primary_ids, **groups,
        "requires_explicit_split": mixed, "deferred_reasons": reasons,
        "body_edit_authorized": False, "unrestricted_fallback_allowed": False,
        "adoption_authorized": False, "grades_modified": False, "grants_credit": False,
    }
