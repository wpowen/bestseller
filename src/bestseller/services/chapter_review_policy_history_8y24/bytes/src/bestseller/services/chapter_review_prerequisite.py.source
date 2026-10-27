"""Pure waiting receipts: retain an attempt, never authorize dispatch or release credit.

A producer's zero-call marker is a lifecycle observation, not financial proof.
Only newly returned prerequisite failures create these receipts. Historical
completed UNKNOWN attempts remain consumed and are never reclassified here.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json

WAITING = "waiting_infrastructure"
SCHEMA = "chapter-review-prerequisite-wait.v1"
REASONS = frozenset({"required_judge_evidence_unavailable", "primary_source_capacity_unavailable"})


def _sha(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode()
    ).hexdigest()


def wait_binding(cycle, entry, *, project_id, chapter_id, body_fingerprint):
    """Bind the original logical slot; do not invent a gateway operation key."""
    return {
        "project_id": str(project_id),
        "chapter_id": str(chapter_id),
        "cycle_id": cycle["cycle_id"],
        "attempt": entry["attempt"],
        "operation_namespace": entry["operation_namespace"],
        "review_workflow_run_id": cycle["review_workflow_run_id"],
        "source_draft_id": cycle["source_draft_id"],
        "source_body_hash": cycle["source_body_hash"],
        "body_fingerprint": deepcopy(body_fingerprint),
        "policy_source_hash": cycle["policy_source_hash"],
        "semantic_dependencies_sha256": cycle["semantic_dependencies_sha256"],
    }


def prerequisite_wait_receipt(report, *, binding):
    """Return a conservative WAIT observation, never evidence that no money was spent."""
    payload = getattr(report, "structured_output", None)
    if not isinstance(payload, Mapping) or getattr(report, "verdict", None) != "unknown":
        return None
    evidence = payload.get("evidence_summary")
    marker = (
        evidence.get("primary_review_not_dispatched") if isinstance(evidence, Mapping) else None
    )
    if (
        not isinstance(marker, Mapping)
        or not isinstance(marker.get("reason"), str)
        or marker["reason"] not in REASONS
    ):
        return None
    fingerprint = payload.get("reviewed_body_fingerprint")
    critic = payload.get("critic_evidence")
    if (
        type(marker.get("provider_calls")) is not int
        or marker["provider_calls"] != 0
        or not isinstance(fingerprint, Mapping)
        or dict(fingerprint) != binding["body_fingerprint"]
        or not isinstance(marker.get("body_fingerprint"), Mapping)
        or dict(marker["body_fingerprint"]) != dict(fingerprint)
        or str(getattr(report, "project_id", None)) != binding["project_id"]
        or getattr(report, "target_type", None) != "chapter"
        or str(getattr(report, "target_id", None)) != binding["chapter_id"]
        or payload.get("draft_id") != binding["source_draft_id"]
        or getattr(report, "llm_run_id", None) is not None
        or "primary_submission_original" not in payload
        or payload["primary_submission_original"] is not None
        or "primary_submission_receipt" not in payload
        or payload["primary_submission_receipt"] is not None
        or payload.get("rewrite_instructions") is not None
        or payload.get("untrusted_critic_response") not in (None, "")
        or not isinstance(critic, Mapping)
        or critic.get("status") != "unavailable"
        or critic.get("passed") is not False
    ):
        return None
    return {
        "schema": SCHEMA,
        **deepcopy(binding),
        "reason": marker["reason"],
        "report_id": str(report.id),
        "report_payload_sha256": _sha(payload),
        "body_fingerprint": deepcopy(dict(fingerprint)),
        "state": WAITING,
        "financial_proof": False,
        "dispatch_authorized": False,
        "credit_released": False,
    }


def waiting_history(cycle, entry):
    """Shape guard only; caller must load and validate every referenced DB result."""
    history = entry.get("infrastructure_history")
    if (
        cycle.get("state") != WAITING
        or entry.get("state") != WAITING
        or type(entry.get("attempt")) is not int
        or entry["attempt"] not in (1, 2)
        or entry.get("operation_namespace")
        != f"chapter-critic:{cycle['cycle_id']}:{entry['attempt']}"
        or any(
            key in entry
            for key in (
                "outcome",
                "review_result",
                "review_report_id",
                "quality_score_id",
                "rewrite_task_id",
            )
        )
        or not isinstance(history, list)
        or not history
        or any(
            not isinstance(item, Mapping) or not isinstance(item.get("review_report_id"), str)
            for item in history
        )
        or len({item.get("review_report_id") for item in history}) != len(history)
    ):
        raise ValueError("chapter_critic_waiting_receipt_invalid")
    return history


def validate_waiting_record(report, *, record, binding):
    receipt = prerequisite_wait_receipt(report, binding=binding)
    result = record.get("review_result")
    if (
        receipt is None
        or record.get("waiting_receipt") != receipt
        or record.get("attempt") != binding["attempt"]
        or record.get("outcome") != "unknown"
        or record.get("draft_id") != binding["source_draft_id"]
        or record.get("body_fingerprint") != receipt["body_fingerprint"]
        or record.get("review_report_id") != receipt["report_id"]
        or record.get("rewrite_task_id") is not None
        or not isinstance(result, Mapping)
        or result.get("verdict") != "unknown"
        or result.get("rewrite_instructions") is not None
    ):
        raise ValueError("chapter_critic_waiting_receipt_invalid")
    return receipt
