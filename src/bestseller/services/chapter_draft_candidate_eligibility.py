"""Source-bound eligibility for stall selection; never promote or regrade prose."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math

POLICY = "chapter-best-draft-source-eligibility.v1"
AUDIT_KEY = "deterministic_audit_best_draft_selection"


def _proof_sha(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _verified_commercial_actions(summaries, body):
    """Consume the retained action proof, without rejudging or changing its policy.

    The completed cycle/retained-result loader owns report and paid-result
    availability. Here the immutable original, projected view and slot lineage
    must agree before an action can count as fact evidence. Severity and a raw
    rewrite/threshold miss alone do not establish an effective fact blocker.
    """
    from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
    from bestseller.services.commercial_action_projection import (
        _decode,
        _json,
        _slot,
        resolve_source_record_alias_capability,
    )
    from bestseller.services.commercial_source_evidence import CommercialSources

    def require(condition):
        if not condition:
            raise ValueError("commercial_action_proof_unverified")

    def digest(value):
        return (isinstance(value, str) and len(value) == 64
                and all(c in "0123456789abcdef" for c in value))

    keys = ("commercial_action_projection", "llm_commercial_judge", "llm_commercial_judge_original")
    require(all(isinstance(summary, Mapping) for summary in summaries))
    if not any(key in summary for summary in summaries for key in keys):
        return []
    summary = summaries[0]
    require(all(isinstance(summary.get(key), Mapping) for key in keys))
    for other in summaries[1:]:
        require(all(other[key] == summary[key] for key in keys if key in other))
    proof, view, original = (summary[key] for key in keys)
    require(proof.get("schema") == "commercial-action-projection.v1"
            and proof.get("status") == "projected"
            and all(proof.get(key) is False for key in (
                "scores_modified", "raw_cohort_modified", "grants_slots", "authorizes_replay")))
    binding = proof.get("source_binding")
    require(isinstance(binding, Mapping)
            and all(digest(binding.get(key)) for key in (
                "body_sha256", "previous_sha256", "semantic_source_sha256", "source_protocol_sha256"))
            and binding["body_sha256"] == body["digest"])
    require(digest(proof.get("source_text_sha256"))
            and proof.get("projection_sha256") == _proof_sha(view))
    retained = LLMQualityJudgeResult.model_validate(original)
    require(not retained.infrastructure_failure and bool(retained.llm_run_id)
            and proof.get("original_aggregate_sha256") == _proof_sha(retained.model_dump(mode="json")))
    projected = LLMQualityJudgeResult.model_validate(view)
    require(all(getattr(retained, key) == getattr(projected, key) for key in (
        "scope", "passed", "overall_score", "dimension_scores", "raw_excerpt", "llm_run_id", "rewrite_plan")))
    if proof.get("unknown_sample_slots") != []:
        # A native abstention contributes no vote, score, issue or edit authority.
        # Only the exact truncated-return protocol can extend fact-only repair;
        # other unavailable/quarantined evidence keeps its existing denial.
        from bestseller.services.chapter_verified_fact_disposition import _quorum_actions

        neutral_slots = proof.get("slots")
        require(isinstance(neutral_slots, list) and bool(neutral_slots))
        for neutral in neutral_slots:
            require(isinstance(neutral, Mapping))
            if neutral.get("status") == "unknown":
                require(isinstance(neutral.get("adapter_receipt"), Mapping)
                        and neutral["adapter_receipt"].get("status") == "not_run_output_truncated")
                require(all(type(neutral[key]) is int and neutral[key] == 0 for key in (
                    "domain_issues_contributed", "actions_contributed", "scores_contributed")))
                require(neutral.get("abstains") is True)
        actions = _quorum_actions(summary, body["digest"])
        # Majority/median/action lineage alone cannot grant the stricter edit
        # scope. Replay every slot with the native parser and compare exact JSON
        # receipts, including the retained zero-contribution length response.
        catalog = {}
        for action in actions:
            for origin in action["lineage"]:
                for key in ("body_citations", "source_citations"):
                    for citation in origin["issue"].get(key, []):
                        entry = {"kind": citation["kind"], "text": citation["quote"]}
                        require(citation["id"] not in catalog or catalog[citation["id"]] == entry)
                        catalog[citation["id"]] = entry
        source_packet = resolve_source_record_alias_capability(proof, body_sha256=body["digest"])
        aggregate = _decode(retained.raw_excerpt)
        rebuilt_slots = []
        for sample in aggregate["samples"]:
            result = LLMQualityJudgeResult.model_validate(sample["report"])
            require(_json(sample["report"]) == _json(result.model_dump(mode="json")))
            envelope = _decode(result.raw_excerpt)
            sources = (source_packet if source_packet is not None else CommercialSources(
                text="", catalog=catalog, receipt=envelope["adapter_receipt"]["source_binding"]))
            _slot(sample["report"], sample["sample_slot"], sources, {}, rebuilt_slots)
        require(_json(rebuilt_slots) == _json(neutral_slots))
        return actions
    slots = proof.get("slots")
    require(isinstance(slots, list) and bool(slots) and proof.get("unknown_sample_slots") == [])
    by_slot = {}
    for slot in slots:
        require(isinstance(slot, Mapping) and slot.get("status") == "verified"
                and isinstance(slot.get("sample_slot"), str) and bool(slot["sample_slot"])
                and slot["sample_slot"] not in by_slot and bool(slot.get("llm_run_id"))
                and digest(slot.get("raw_report_sha256")) and digest(slot.get("envelope_sha256")))
        by_slot[slot["sample_slot"]] = slot
    envelope = _decode(retained.raw_excerpt)
    require(isinstance(envelope, Mapping))
    if envelope.get("schema_version") == "commercial-slot-evidence.v1":
        samples = [{"sample_slot": envelope.get("sample_slot"), "valid": True,
                    "report": retained.model_dump(mode="json")}]
    else:
        require(envelope.get("schema_version") == "chapter-quality-aggregation.v1"
                and envelope.get("status") == "aggregated")
        samples = envelope.get("samples")
    require(isinstance(samples, list) and len(samples) == len(slots))
    require([s.get("sample_slot") for s in samples] == list(by_slot))
    actions = proof.get("issue_actions")
    require(isinstance(actions, list))
    # Reconstruct only the cited catalog. It is NOT fresh source authority:
    # the existing producer must reproduce its exact immutable adapter/domain
    # hash from raw wire bytes before any of these quotations can be trusted.
    catalog = {}
    for action in actions:
        require(isinstance(action, Mapping) and isinstance(action.get("lineage"), list))
        for origin in action["lineage"]:
            require(isinstance(origin, Mapping) and isinstance(origin.get("issue"), Mapping))
            for key in ("body_citations", "source_citations"):
                citations = origin["issue"].get(key, [])
                require(isinstance(citations, list))
                for citation in citations:
                    require(isinstance(citation, Mapping) and isinstance(citation.get("id"), str)
                            and isinstance(citation.get("quote"), str))
                    entry = {"kind": citation.get("kind"), "text": citation["quote"]}
                    require(citation["id"] not in catalog or catalog[citation["id"]] == entry)
                    catalog[citation["id"]] = entry
    domains, rebuilt_slots = {}, []
    alias_sources = resolve_source_record_alias_capability(proof, body_sha256=body["digest"])
    try:
        for sample in samples:
            require(sample.get("valid") is True)
            raw = _decode(sample["report"]["raw_excerpt"])
            require(raw.get("source_binding") == binding)
            sources = (alias_sources if alias_sources is not None else
                       CommercialSources(text="", catalog=catalog,
                                         receipt=raw["adapter_receipt"]["source_binding"]))
            _slot(sample["report"], sample["sample_slot"], sources, domains, rebuilt_slots)
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        raise ValueError("commercial_action_proof_unverified") from None
    require(rebuilt_slots == slots)
    entries = [(group, index, issue.model_dump(mode="json"))
               for group in ("blocking_issues", "audit_issues")
               for index, issue in enumerate(getattr(retained, group))]
    represented = {_json(issue) for _, _, issue in entries}
    entries.extend((None, None, json.loads(key)) for key in domains if key not in represented)
    require(len(entries) == len(actions))
    indexed = {}
    for group, action in (("blocking_issues", "block"), ("audit_issues", "audit_only")):
        issues = view.get(group)
        require(isinstance(issues, list))
        for issue in issues:
            require(isinstance(issue, Mapping))
            index = issue.get("action_receipt_index")
            require(type(index) is int and 0 <= index < len(actions) and index not in indexed)
            indexed[index] = (issue, action)
    require(len(indexed) == len(actions)
            and type(proof.get("effective_blocking_count")) is int
            and proof["effective_blocking_count"] == len(view["blocking_issues"]))
    authorities = {"current_fact_conflict": "verified_source", "prior_fact_conflict": "verified_source",
                   "constraint_violation": "verified_source", "narrative_quality": "craft_only",
                   "retained_threshold_policy": "retained_evaluation_policy"}
    for index, action in enumerate(actions):
        require(isinstance(action, Mapping))
        kind = action.get("assessment_kind")
        issue, effective_action = indexed[index]
        require(isinstance(kind, str) and kind in authorities
                and action.get("authority") == authorities[kind]
                and action.get("effective_action") == effective_action
                and all(action.get(key) == issue.get(key) for key in ("code", "severity", "assessment_kind")))
        group, aggregate_index, typed = entries[index]
        lineage = domains.get(_json(typed))
        require(bool(lineage) and action.get("lineage") == lineage
                and action.get("aggregate_group") == group
                and action.get("aggregate_index") == aggregate_index
                and all(row["issue"]["assessment_kind"] == kind for row in lineage))
        require(lineage[0].get("issue") == {k: v for k, v in issue.items() if k != "action_receipt_index"})
        if kind != "narrative_quality" and group == "blocking_issues":
            require(effective_action == "block")
    return deepcopy(actions)


def _commercial_effective_fact_blockers(summaries, body):
    actions = _verified_commercial_actions(summaries, body)
    return [{key: action[key] for key in (
        "code", "assessment_kind", "severity", "authority", "effective_action",
    )} for action in actions
        if action["effective_action"] == "block" and action["authority"] == "verified_source"]


def verified_commercial_actions(evidence_summary, body_text):
    """Return ALL detached full action rows, or reject unsupported provenance.

    This pure API proves retained raw issue/action provenance and body binding,
    never an edit authorization, fresh grade or current semantic dependencies.
    The caller must attest the report/cycle and available PASS primary/window,
    check ALL effective blockers for a fact-only repair, require unique current
    body quotations, derive a backend edit-scope contract and obtain a fresh
    review after repair. Threshold/craft actions remain present for that check.
    A configured craft audit lacks its full gate config in this receipt; this
    API rejects that unsupported repair scope without vetoing stall soft debt.
    """
    from bestseller.services.commercial_action_projection import _CRAFT_DOMAINS
    from bestseller.services.quality_contract_registry import get_quality_contract

    if (not isinstance(evidence_summary, Mapping)
            or "commercial_action_projection" not in evidence_summary
            or not isinstance(body_text, str) or not body_text.strip()):
        raise ValueError("commercial_action_proof_unverified")
    try:
        actions = _verified_commercial_actions(
            (evidence_summary,), {"digest": hashlib.sha256(body_text.encode('utf-8')).hexdigest()})
    except (KeyError, TypeError, AttributeError, OverflowError):
        raise ValueError("commercial_action_proof_unverified") from None
    for action in actions:
        if action["assessment_kind"] == "narrative_quality" and action["effective_action"] == "audit_only":
            registration = get_quality_contract(action["code"])
            if registration is not None and (
                registration.domain in _CRAFT_DOMAINS or action["code"] == "OPENING_SCENE_DRIFT"
            ):
                raise ValueError("commercial_action_policy_unverified")
    return tuple(actions)


def record_selection(chapter, receipt):
    metadata = dict(chapter.metadata_json or {})
    history = metadata.get(AUDIT_KEY, [])
    if not isinstance(history, list):
        raise ValueError("chapter_best_draft_selection_audit_malformed")
    if not history or history[-1] != receipt:
        chapter.metadata_json = {**metadata, AUDIT_KEY: [*history, deepcopy(receipt)]}


async def eligible_reviewed_candidates(
    session, *, chapter, rows, semantic_hash, policy_hash,
    fingerprint, retained_result, evidence_unknown,
):
    """Only a completed, available, currently bound review may supply a score.

    Use the existing cycle's semantic fence for approved plans, adopted context
    and evaluation configuration. Legacy scores never acquire inferred lineage.
    """
    eligible, decisions = [], []
    cycles = (chapter.metadata_json or {}).get("chapter_critic_cycles")
    for draft, quality in rows:
        decision = {"draft_id": str(draft.id), "quality_score_id": str(quality.id) if quality is not None else None,
                    "eligible": False, "reasons": []}
        decisions.append(decision)
        reasons = decision["reasons"]
        if draft.project_id != chapter.project_id or draft.chapter_id != chapter.id:
            reasons.append("candidate_scope_mismatch")
            continue
        if draft.promotion_state not in {"candidate", "under_review", "eligible"}:
            reasons.append("candidate_state_not_selectable")
            continue
        if quality is None or quality.review_report_id is None:
            reasons.append("quality_review_binding_missing")
            continue
        try:
            score = float(quality.score_overall)
        except (TypeError, ValueError, OverflowError):
            score = float("nan")
        if not math.isfinite(score) or not 0 <= score <= 1:
            reasons.append("quality_score_invalid")
            continue
        if not isinstance(cycles, Mapping):
            reasons.append("review_cycle_missing_or_malformed")
            continue
        body = fingerprint(draft.content_md or "")
        decision["body_fingerprint"] = body
        matching = []
        for key, cycle in cycles.items():
            if not isinstance(cycle, Mapping) or cycle.get("source_draft_id") != str(draft.id):
                continue
            if cycle.get("schema_version") != "chapter-critic-cycle.v1" or cycle.get("cycle_id") != key:
                reasons.append("review_cycle_malformed")
                continue
            if cycle.get("source_body_hash") != body["digest"]:
                reasons.append("review_body_changed")
                continue
            if cycle.get("policy_source_hash") != policy_hash:
                reasons.append("evaluation_policy_changed")
                continue
            if cycle.get("semantic_dependencies_sha256") != semantic_hash or cycle.get("semantic_dependency_change_detected"):
                reasons.append("source_or_evaluation_dependencies_changed")
                continue
            attempts = cycle.get("attempts")
            if cycle.get("state") != "completed" or not isinstance(attempts, list) or not 1 <= len(attempts) <= 2:
                reasons.append("review_cycle_not_completed")
                continue
            for index, attempt in enumerate(attempts, 1):
                if not isinstance(attempt, Mapping):
                    reasons.append("review_attempt_malformed")
                    continue
                if attempt.get("quality_score_id") != str(quality.id):
                    continue
                if (attempt.get("state") != "completed" or attempt.get("outcome") != "available"
                        or type(attempt.get("attempt")) is not int or attempt["attempt"] != index
                        or attempt.get("draft_id") != str(draft.id) or attempt.get("body_fingerprint") != body
                        or attempt.get("review_report_id") != str(quality.review_report_id)):
                    reasons.append("review_attempt_unavailable_or_unbound")
                    continue
                matching.append((key, attempt))
        if len(matching) != 1:
            reasons.append("review_attestation_missing" if not matching else "review_attestation_ambiguous")
            continue
        key, attempt = matching[0]
        try:
            result, report, saved_quality, _task = await retained_result(session, draft, attempt)
            payload = report.structured_output
            evidence = payload.get("critic_evidence") or {}
            if (evidence_unknown(report) or result.verdict not in {"pass", "rewrite"}
                    or report.verdict != result.verdict
                    or evidence.get("status") not in {"verified", "not_run"}
                    or (evidence.get("status") == "verified" and evidence.get("passed") is not True)):
                raise ValueError("review_evidence_unknown")
            report_score = float(payload["scores"]["overall"])
            if (saved_quality.id != quality.id or float(saved_quality.score_overall) != score
                    or abs(report_score - score) > 0.005001
                    or result.scores.overall != report_score):
                raise ValueError("review_score_binding_mismatch")
            hard_severities = {"critical", "block", "blocker", "fatal"}
            summaries = (payload.get("evidence_summary") or {}, quality.evidence_summary or {})
            if (str(result.severity_max).lower() in hard_severities
                    or str(report.severity_max).lower() in hard_severities
                    or any(payload.get(k) for k in ("blocking_codes", "blockers", "blocking_issues"))
                    or any(summary.get(k) for summary in summaries for k in ("blocking_codes", "blockers"))
                    or any(f.severity.lower() in hard_severities for f in result.findings)
                    or any(isinstance(f, Mapping) and (f.get("blocking") or f.get("blocks_write")
                           or str(f.get("severity", "")).lower() in hard_severities)
                           for f in payload.get("findings", []))
                    or any(isinstance(value, Mapping) and (value.get("has_critical") is True
                           or any(str(issue.get("severity", "")).lower() in hard_severities
                                  for issue in value.get("blocking_issues", [])))
                           for summary in summaries for value in summary.values())):
                raise ValueError("known_hard_or_critical_blocker")
            fact_blockers = _commercial_effective_fact_blockers((
                payload.get("evidence_summary", {}), quality.evidence_summary or {}, result.evidence_summary,
            ), {"digest": hashlib.sha256(draft.content_md.encode('utf-8')).hexdigest()})
            if fact_blockers:
                decision["effective_fact_blockers"] = fact_blockers
                raise ValueError("known_effective_fact_blocker")
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
            reasons.append(str(exc) if isinstance(exc, ValueError) and str(exc) in {
                "review_evidence_unknown", "review_score_binding_mismatch",
                "known_hard_or_critical_blocker",
                "known_effective_fact_blocker", "commercial_action_proof_unverified",
                "chapter_critic_cycle_result_binding_mismatch", "chapter_critic_cycle_rewrite_binding_mismatch",
            } else "review_receipt_malformed")
            continue
        decision.update(eligible=True, cycle_id=key, review_report_id=str(report.id))
        decision["reasons"] = []
        eligible.append((draft, quality))
    return eligible, decisions
