"""Pure promotion action classification; no grades, adoption, credit or storage.

BoundGateReport is an in-process SERVER boundary, not a serialized approval
format. Its caller must already have run the named source/plan/identity/canon
verifier and supply the actual required-gate inventory and fresh source hashes.
Hashes only bind that result: they do not prove authority, semantic truth or
physical paid-cohort verification. Raw metadata dictionaries are not accepted
as gate reports. Missing complete post-body plan evidence stays unknown.

EXPERIMENTAL/UNWIRED: no complete post-body plan verifier is introduced here.
Consumers must preserve their existing policy when replacement_supported=False;
they must not turn this optional projection's missing adapters into a new veto.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from bestseller.services.commercial_action_projection import project_commercial_actions
from bestseller.services.commercial_source_evidence import CommercialSources
from bestseller.services.critic_evidence_gate import validate_critic_commentary
from bestseller.services.deterministic_audit_actions import effective_deterministic_audit_blockers
from bestseller.services.primary_review_submission import parse_primary_review_submission
from bestseller.services.quality_contract_registry import get_quality_contract
from bestseller.services.write_gate import GateConfig, resolve_mode

POLICY = "promotion-hard-actions.v1"
_SOFT_CATEGORIES = frozenset({
    "coverage", "coherence", "contract_alignment", "ending_hook_effectiveness",
    "continuity", "main_plot_progression", "opening_contract", "subplot_progression",
    "volume_mission_alignment", "hook", "style", "pacing_rhythm",
})
_CRAFT_DOMAINS = frozenset({"prose", "retention", "ending", "pov", "dialogue", "pacing"})
_SOURCE_KINDS = frozenset({"constraint_violation", "prior_fact_conflict", "current_fact_conflict"})


def _sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class BoundGateReport:
    """A completed current-body check supplied by a trusted server verifier.

    ``report`` must carry status='verified', coverage='complete', exact body,
    source, chapter and verifier bindings, and findings. Non-deterministic hard
    verifiers additionally return blocking_issues and unknown_issues explicitly.
    These are effective server actions, never model severity or author labels.
    No source verifier is fabricated by constructing this value.
    """

    verifier: str
    body_sha256: str
    chapter_number: int
    source: Mapping[str, Any]
    report: Mapping[str, Any]


@dataclass(frozen=True)
class CommercialPromotionEvidence:
    aggregate: Any
    sources: CommercialSources
    # Produced after the existing strict physical stable-cohort verifier.
    cohort: BoundGateReport


def classify_promotion_hard_actions(
    review_result: Any, *, content_md: str, chapter_number: int, gate_config: GateConfig,
    primary_completion: Any, gate_reports: Mapping[str, BoundGateReport],
    required_gate_verifiers: Mapping[str, str], current_source_hashes: Mapping[str, str],
    commercial: CommercialPromotionEvidence | None = None,
    window: BoundGateReport | None = None,
) -> dict[str, Any]:
    """Return necessary hard-gate evidence, never a promotion/debt authorization.

    The inventory MUST include all applicable runtime hard gates; primary,
    deterministic and explicit_plan are always required here. Identity/canon/continuity/bible/
    length checks cannot be omitted by an LLM or by this helper. The caller owns
    authenticating server reports and that inventory against actual settings.
    A commercial/window entry in the inventory makes its evidence mandatory.
    Unknown evidence and known hard blockers are retained independently.
    """
    unknown: list[str] = []
    actions: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "schema": POLICY, "status": "unknown", "hard_gates_passed": False,
        "blocking_codes": [], "unknown_reasons": unknown, "issue_actions": actions,
        "soft_debt_evidence_eligible": False, "scores_modified": False,
        "replacement_supported": False,
        "verdict_modified": False, "promotion_approved": False,
        "debt_authorized": False, "grants_credit": False,
    }

    def mark(origin: str, issue: Mapping[str, Any], action: str, reason: str):
        actions.append({"origin": origin, "issue_sha256": _sha(dict(issue)),
                        "code": issue.get("code"), "effective_action": action, "reason": reason})

    def bound(name: str, value: Any) -> Mapping[str, Any] | None:
        if not isinstance(value, BoundGateReport):
            unknown.append(f"{name}:bound_server_report_missing")
            return None
        report = value.report
        expected = required_gate_verifiers.get(name)
        if not expected or value.verifier != expected:
            unknown.append(f"{name}:verifier_not_registered")
            return None
        if not isinstance(value.source, Mapping) or not value.source:
            unknown.append(f"{name}:current_source_missing")
            return None
        source_hash = _sha(dict(value.source))
        if (value.body_sha256 != body_hash or value.chapter_number != chapter_number
                or current_source_hashes.get(name) != source_hash
                or not isinstance(report, Mapping)
                or report.get("body_sha256") != body_hash
                or report.get("chapter_number") != chapter_number
                or report.get("source_sha256") != source_hash
                or report.get("verifier") != expected
                or report.get("status") != "verified" or report.get("coverage") != "complete"):
            unknown.append(f"{name}:binding_or_coverage_unverified")
            return None
        result.setdefault("verified_sources", {})[name] = {
            "source_sha256": source_hash, "report_sha256": _sha(dict(report)), "verifier": expected,
        }
        return report

    try:
        if (not isinstance(content_md, str) or not content_md.strip()
                or type(chapter_number) is not int or chapter_number < 1
                or not isinstance(gate_config, GateConfig)
                or gate_config.default not in {"block", "audit_only"}
                or any(v not in {"block", "audit_only"} for v in gate_config.mode_by_violation.values())):
            raise ValueError("current_body_or_gate_config_invalid")
        body_hash = hashlib.sha256(content_md.encode()).hexdigest()
        result.update(body_sha256=body_hash, chapter_number=chapter_number)
        raw_review = review_result.model_dump(mode="json") if hasattr(review_result, "model_dump") else dict(review_result)
        result["original_review_sha256"] = _sha(raw_review)
        result["gate_config_sha256"] = _sha({"default": gate_config.default,
                                            "mode_by_violation": dict(gate_config.mode_by_violation)})
        if not {"primary", "deterministic", "explicit_plan"} <= set(required_gate_verifiers):
            unknown.append("required_server_gate_inventory_incomplete")
        if (not isinstance(gate_reports, Mapping) or not isinstance(current_source_hashes, Mapping)
                or any(not isinstance(k, str) or not isinstance(v, str) or not v
                       for k, v in required_gate_verifiers.items())):
            raise ValueError("server_gate_inventory_invalid")
        result["required_gate_verifiers"] = dict(required_gate_verifiers)
        if set(gate_reports) & {"commercial", "window"}:
            unknown.append("reserved_evaluators_require_typed_evidence_arguments")

        # Reuse the producer's real-completion guard and both pure body checks.
        from bestseller.services.reviews import _is_real_chapter_review_completion

        if not _is_real_chapter_review_completion(primary_completion, allow_submission=True):
            unknown.append("primary:provider_completion_unverified")
        else:
            text, receipt, errors = parse_primary_review_submission(primary_completion, content_md)
            checked = None if errors else validate_critic_commentary(
                text, chapter_text=content_md, citation_policy=receipt["citation_identity_policy"])
            if errors or checked is None or not checked.passed:
                unknown.append("primary:submission_or_body_evidence_unverified")
            else:
                result["primary_receipt_sha256"] = _sha(receipt)
                result["primary_verdict"] = receipt["parsed_payload"]["verdict"]
                primary_source = bound("primary", gate_reports.get("primary"))
                run_id = str(getattr(primary_completion, "llm_run_id", None) or "")
                if primary_source is not None and (
                    not run_id or primary_source.get("llm_run_id") != run_id
                    or primary_source.get("submission_sha256") != _sha(receipt)
                ):
                    unknown.append("primary:current_source_submission_not_bound")
                if result["primary_verdict"] != "pass":
                    unknown.append("primary:untyped_rewrite_authority_unresolved")

        for name in dict.fromkeys([*required_gate_verifiers, *gate_reports]):
            if name in {"primary", "commercial", "window"}:
                continue
            report = bound(name, gate_reports.get(name))
            if report is None:
                continue
            findings = report.get("findings")
            if not isinstance(findings, (list, tuple)):
                unknown.append(f"{name}:findings_missing")
                continue
            if name == "deterministic":
                if type(report.get("passed")) is not bool:
                    unknown.append("deterministic:completion_status_missing")
                blockers = effective_deterministic_audit_blockers(
                    report, settings=gate_config, chapter_number=chapter_number)
                for issue in findings:
                    mark(name, issue, "block" if issue in blockers else "audit_only",
                         "existing_deterministic_action_policy")
                if report.get("passed") is False and not findings:
                    unknown.append("deterministic:unexplained_failure")
            else:
                hard, unresolved = report.get("blocking_issues"), report.get("unknown_issues")
                if not isinstance(hard, list) or not isinstance(unresolved, list):
                    unknown.append(f"{name}:effective_server_actions_missing")
                    continue
                if unresolved or (report.get("passed") is not True and not hard):
                    unknown.append(f"{name}:source_checks_unresolved")
                for issue in hard:
                    if not isinstance(issue, Mapping) or not str(issue.get("code") or "").strip():
                        raise ValueError("server_hard_action_invalid")
                    mark(name, issue, "block", "current_source_verifier_hard_action")

        if commercial is not None or "commercial" in required_gate_verifiers:
            cohort = bound("commercial", commercial.cohort if isinstance(commercial, CommercialPromotionEvidence) else None)
            if cohort is not None:
                original = (commercial.aggregate.model_dump(mode="json")
                            if hasattr(commercial.aggregate, "model_dump") else dict(commercial.aggregate))
                if (cohort.get("aggregate_sha256") != _sha(original)
                        or commercial.sources.receipt.get("body_sha256") != body_hash):
                    unknown.append("commercial:cohort_or_body_changed")
                else:
                    projected, receipt = project_commercial_actions(
                        commercial.aggregate, sources=commercial.sources,
                        gate_config=gate_config, chapter_number=chapter_number)
                    if projected is None or receipt.get("status") != "projected":
                        unknown.append("commercial:typed_source_projection_unverified")
                    else:
                        result["commercial_projection_sha256"] = receipt["projection_sha256"]
                        # The original strict stable policy owns quorum. An
                        # invalid retained slot abstains and stays unknown; it
                        # cannot veto an aggregate whose quorum was verified.
                        result["commercial_unknown_sample_slots"] = list(receipt["unknown_sample_slots"])
                        for issue in receipt["issue_actions"]:
                            action = issue["effective_action"]
                            if issue["assessment_kind"] == "retained_threshold_policy":
                                action = "audit_only"
                            # Aggregate representatives cannot erase another
                            # verified sample's factual or literal RULE blocker.
                            if issue["assessment_kind"] in _SOURCE_KINDS and any(
                                row.get("mapped_group") == "blocking_issues" for row in issue["lineage"]
                            ):
                                action = "block"
                            mark("commercial", issue, action, "verified_typed_issue_lineage")

        if window is not None or "window" in required_gate_verifiers:
            report = bound("window", window)
            if report is not None:
                if (not isinstance(report.get("blocking_issues"), list)
                        or not isinstance(report.get("audit_issues"), list)
                        or type(report.get("passed")) is not bool):
                    raise ValueError("window_evidence_incomplete")
                for issue in report["blocking_issues"]:
                    code = issue["code"]
                    registration = get_quality_contract(code)
                    # Model code/severity never gains canon or plan authority.
                    craft = registration is not None and registration.domain in _CRAFT_DOMAINS
                    action = resolve_mode(code, gate_config, chapter_no=chapter_number) if craft else "audit_only"
                    if action == "block":
                        proof = issue.get("source_evidence")
                        if (not isinstance(proof, Mapping)
                                or any(key not in proof for key in registration.required_evidence)
                                or not issue.get("evidence") or issue["evidence"] not in content_md):
                            unknown.append(f"window:{code}:registered_action_source_unverified")
                            action = "unknown"
                    mark("window", issue, action, "server_registered_craft_action_only")

        # These display categories are not source-hard findings. Any other
        # unmatched category remains unknown, regardless of its raw severity.
        for issue in raw_review.get("findings", []):
            category = issue.get("category")
            if category not in _SOFT_CATEGORIES and category not in {
                "llm_commercial_quality", "llm_window_quality", "llm_volume_checkpoint",
            }:
                unknown.append(f"review:unclassified_finding:{category}")
            if category == "llm_commercial_quality" and commercial is None:
                unknown.append("commercial:existing_finding_not_classified")
            if category == "llm_window_quality" and window is None:
                unknown.append("window:existing_finding_not_classified")
            if category == "llm_volume_checkpoint":
                unknown.append("volume:source_action_adapter_missing")
        summary = raw_review.get("evidence_summary", {})
        if summary.get("llm_commercial_judge") and commercial is None:
            unknown.append("commercial:existing_evidence_not_classified")
        if summary.get("llm_window_quality_judge") and window is None:
            unknown.append("window:existing_evidence_not_classified")
        if summary.get("llm_volume_checkpoint_judge"):
            unknown.append("volume:source_action_adapter_missing")
        if raw_review.get("verdict") in {"unknown", "unavailable"}:
            unknown.append("review:evidence_unknown")
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError) as exc:
        unknown.append(f"classification_input_unverified:{type(exc).__name__}")

    unknown[:] = list(dict.fromkeys(unknown))
    result["blocking_codes"] = list(dict.fromkeys(
        str(a["code"]) for a in actions if a["effective_action"] == "block"))
    clear = not unknown and not result["blocking_codes"]
    result.update(status="clear" if clear else "unknown" if unknown else "blocked",
                  hard_gates_passed=clear, soft_debt_evidence_eligible=clear,
                  replacement_supported=not unknown)
    return result
