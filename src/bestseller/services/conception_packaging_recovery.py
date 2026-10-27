"""Collect complete retained proposals without granting review or spending credit.

The caller validates the original task/scope and trusted checkpoint before
calling. Legacy generation-source compatibility can remain unknown: these are
creative proposals, not inherited grades. Current source hashes bind this new
collection only. Every chosen proposal requires the full normal current-source
audit. This module does not authenticate checkpoints, query paid rows or spend.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
from typing import Any

SCHEMA = "conception-packaging-recovery.v2"
_AGENT = "final_packaging_promotion_repair"


def _json(value: object) -> str:
    # Match checkpoint wire semantics: no tuples, opaque values or NaN/Inf.
    def check(item: object) -> None:
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                check(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child)
            return
        raise ValueError("packaging_recovery_input_not_strict_json")

    check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def packaging_recovery_source_identity(state: Mapping[str, Any]) -> dict[str, Any]:
    """Bind full current assets and controls, without promoting AI to author.

    Hash whole assets rather than reusing the narrower materialization hash.
    The latter is recorded as a witness only, never proof of a historical input.
    Missing required sources are errors, not empty/default source projections.
    """
    clean = json.loads(_json(dict(state)))
    for field in ("writing_profile", "story_spine", "world_model_payload",
                  "concept_contract", "ctx"):
        if not isinstance(clean.get(field), dict):
            raise ValueError(f"packaging_recovery_source_missing:{field}")
    if not isinstance(clean.get("premise"), str) or not clean["premise"].strip():
        raise ValueError("packaging_recovery_source_missing:premise")
    for field in ("explicit_concept_seed", "genre_key", "genre_intent_contract"):
        if field not in clean:
            raise ValueError(f"packaging_recovery_source_missing:{field}")
    # Full assets, without rendering/truncation. These hashes identify the NEW
    # exploration baseline, not an old request or an approval of these assets.
    canonical = {key: clean[key] for key in ("premise", "writing_profile", "story_spine")}
    canonical["world_model"] = clean["world_model_payload"]
    canonical["story_architecture"] = clean["concept_contract"].get("story_architecture", {})
    controls = {key: clean[key] for key in (
        "ctx", "explicit_concept_seed", "genre_key", "genre_intent_contract",
    )}
    for key in ("chapter_count", "tags", "selected_hook_spec", "_primary_source_effect_contract",
                "_ap_genre", "_ap_sub", "_ap_language", "_ap_platform"):
        # Preserve absence vs explicit null; never infer creator provenance.
        if key in clean:
            controls[key] = clean[key]
    return {
        "schema": SCHEMA,
        "canonical_sha256": _hash(canonical),
        "creator_genre_sha256": _hash(controls),
        "recorded_body_fingerprint": clean.get("_contract_body_fingerprint"),
    }


def collect_retained_packaging_proposals(
    state: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return whole proposal families and a derived, strict-JSON receipt.

    The caller must have validated checkpoint integrity and original task/scope
    and creator controls. This receipt does not attest that validation. The
    state hash is computed from the supplied state, not copied from a possibly
    different export's checkpoint header. Old canonical compatibility is unknown
    and does not prevent exploration; it can never grant a passing audit.
    Selected/cropped diagnostics, incumbent fields, grades and acceptance flags
    are never used as proposal sources. Nothing in ``state`` is mutated.
    """
    clean = json.loads(_json(dict(state)))
    identity = packaging_recovery_source_identity(clean)
    state_hash = _hash(clean)
    logs = clean.get("conception_log")
    if not isinstance(logs, list):
        raise ValueError("packaging_recovery_logs_invalid")
    pool: list[dict[str, Any]] = []
    retained: list[dict[str, Any]] = []
    for index, entry in enumerate(logs):
        if not isinstance(entry, dict) or entry.get("agent") != _AGENT:
            continue
        titles, synopses = entry.get("title_candidates"), entry.get("synopsis_candidates")
        attempt = entry.get("attempt")
        valid = (
            isinstance(titles, list) and isinstance(synopses, list)
            and bool(titles or synopses)
            and all(isinstance(text, str) and text.strip() for text in [*titles, *synopses])
        )
        row = {
            "log_index": index,
            "source_checkpoint_state_sha256": state_hash,
            "log_entry_sha256": _hash(entry),
            "content_sha256": _hash({"title_candidates": titles, "synopsis_candidates": synopses}),
            "attempt": attempt,
            "title_candidates": titles,
            "synopsis_candidates": synopses,
            "status": "requires_normal_audit" if valid else "invalid_proposal_family",
            "proposal_only": True,
            "fresh_full_audit_required": True,
            "grade_reused": False,
            "old_canonical_compatibility": "unknown",
        }
        retained.append(row)
        if valid:
            # Roundtrip isolates nested lists from both return values.
            pool.append(json.loads(_json(row)))
    unsupported = [row["log_index"] for row in retained if row["status"] != "requires_normal_audit"]
    receipt = {
        "schema": SCHEMA,
        "status": ("unsupported" if unsupported else "ready_for_normal_audit" if pool
                   else "no_retained_proposals"),
        "source_checkpoint_state_sha256": state_hash,
        "source_identity": identity,
        "proposal_only": True,
        "fresh_full_audit_required": True,
        "grade_reused": False,
        "old_canonical_compatibility": "unknown",
        "retained_proposals": retained,
        "unsupported_log_indexes": unsupported,
        "proposal_family_count": len(pool),
        "limitations": {
            "task_scope_checkpoint_validation_required_from": "caller",
            "paid_row_linkage_proved": False,
            "grade_reused": False,
            "adoption_authorized": False,
            "new_credit": False,
            "quota_reset": False,
            "normal_full_source_audit_required": True,
        },
    }
    return pool, receipt


async def collect_completed_scope_packaging_proposals(session: Any) -> list[dict[str, Any]]:
    """Recover paid creative proposals after a later pre-send review stop.

    These are unapproved candidates, not cached grades. Only the same active
    execution scope's completed, fully charged, audited v2 results qualify.
    Unknown/partial responses never enter this proposal pool.
    """
    from bestseller.services.budget import current_execution_scope, ExecutionBudgetError
    scope = current_execution_scope()
    if not scope or not scope.startswith("conception-continuation:"):
        return []
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from bestseller.infra.db.models import ExecutionOperationModel, ExecutionAttemptModel, LlmRunModel
    from bestseller.services.independent_acceptance import MODEL
    pool = []
    async with async_sessionmaker(session.bind)() as read:
        rows = list(await read.scalars(select(ExecutionOperationModel).where(
            ExecutionOperationModel.scope_id == scope, ExecutionOperationModel.status == "completed")))
        for op in rows:
            result = op.result_json or {}
            origin = (result.get("metadata") or {}).get("paid_call_origin") or {}
            if (origin.get("prompt_template") != "conception_final_packaging_seed_fidelity_repair_v2"
                    or result.get("model_name") != MODEL):
                continue
            attempt = await read.scalar(select(ExecutionAttemptModel).where(
                ExecutionAttemptModel.operation_key == op.key, ExecutionAttemptModel.scope_id == scope,
                ExecutionAttemptModel.status == "completed"))
            runs = list(await read.scalars(select(LlmRunModel).where(
                LlmRunModel.metadata_json["execution_receipt"]["operation_key"].as_string() == op.key)))
            if (attempt is None or len(runs) != 1 or runs[0].model_name != MODEL
                    or runs[0].prompt_template != origin["prompt_template"]
                    or runs[0].input_tokens != result.get("input_tokens")
                    or runs[0].output_tokens != result.get("output_tokens")
                    or attempt.actual_tokens != runs[0].input_tokens + runs[0].output_tokens
                    or result.get("finish_reason") != "stop"):
                raise ExecutionBudgetError("execution_success_audit_missing", scope)
            content = str(result.get("content") or "").strip()
            if content.startswith("```"):
                content = content[content.find("\n")+1:content.rfind("```")].strip()
            try:
                proposal = json.loads(content)
            except (ValueError, TypeError):
                continue
            if not isinstance(proposal, dict):
                continue
            titles, synopses = proposal.get("title_candidates"), proposal.get("synopsis_candidates")
            if (not isinstance(titles, list) or not isinstance(synopses, list)
                    or not titles or not synopses
                    or not all(isinstance(x, str) and x.strip() for x in [*titles, *synopses])):
                continue
            pool.append({"title_candidates": titles, "synopsis_candidates": synopses,
                "source_scope": scope, "source_operation": op.key,
                "source_result_sha256": _hash(result), "source_llm_run_id": str(runs[0].id),
                "grade_reused": False, "requires_full_current_audit": True})
    return pool
