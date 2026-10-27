"""Derive a bounded edit contract for verified fact-only repair tasks.

The caller authenticates the available report/cycle and paid return. This
contract narrows an authorized repair to exact current-body quotations; it
never makes a quotation Canon, waives another blocker or grants a grade.
"""
from copy import deepcopy
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
