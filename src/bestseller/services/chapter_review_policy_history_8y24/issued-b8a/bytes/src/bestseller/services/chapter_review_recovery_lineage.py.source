"""Read-only proof that a native recovery consumed a failed review origin.

This proves lineage only. It never returns a grade, allowance or send authority.
Historical unknown reports remain unknown and historical failures remain failed.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
    WorkflowRunModel,
)
from bestseller.services.budget import completion_authorization, execution_scope_block_code
from bestseller.services.chapter_review_prerequisite import (
    WAITING,
    validate_waiting_record,
    wait_binding,
    waiting_history,
)
from bestseller.settings import AppSettings

SCHEMA = "chapter-review-recovery-lineage.v1"
_DENIALS = (
    "grade_reuse_allowed",
    "adoption_allowed",
    "attempt_reset_allowed",
    "new_credit_allowed",
    "dispatch_allowed",
)


def _need(value: object, code: str) -> None:
    if not value:
        raise ValueError("chapter_review_recovery_lineage_" + code)


def _sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _checked_proof(proof: object, schema: str) -> None:
    _need(isinstance(proof, Mapping) and proof.get("schema") == schema, "proof_schema")
    plain = dict(proof)
    recorded = plain.pop("proof_sha256", None)
    _need(_sha(recorded) and _digest(plain) == recorded, "proof_digest")
    _need(all(proof.get(k) is False for k in _DENIALS), "proof_authority")


def _prepared(edge: Mapping) -> dict:
    """Only these two fields change after the native prepared checkpoint."""
    snapshot = deepcopy(dict(edge))
    snapshot["state"] = "prepared"
    snapshot.pop("result_workflow_run_id", None)
    return snapshot


def _aggregate_shape(journal: Mapping) -> None:
    from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult

    aggregate, slots = journal["aggregate"], journal["slots"]
    n = journal["binding"]["samples"]
    _need(
        type(n) is int
        and n > 0
        and set(slots) == {f"chapter-quality.independent-v2:{i}" for i in range(n)}
        and isinstance(aggregate, Mapping)
        and set(aggregate) == {"report", "slots_sha256"}
        and aggregate["slots_sha256"] == _digest({k: e["report"] for k, e in slots.items()}),
        "aggregate_projection",
    )
    result = LLMQualityJudgeResult.model_validate(aggregate["report"])
    if n == 1:
        _need(aggregate["report"] == next(iter(slots.values()))["report"], "aggregate_projection")
        return
    receipt = json.loads(result.raw_excerpt)
    children = [
        {
            "sample_slot": f"chapter-quality.independent-v2:{i}",
            "report": slots[f"chapter-quality.independent-v2:{i}"]["report"],
            "valid": not LLMQualityJudgeResult.model_validate(
                slots[f"chapter-quality.independent-v2:{i}"]["report"]
            ).infrastructure_failure,
        }
        for i in range(n)
    ]
    valid = sum(child["valid"] for child in children)
    _need(
        receipt.get("schema_version") == "chapter-quality-aggregation.v1"
        and receipt.get("requested_samples") == n
        and receipt.get("required_valid_samples") == n // 2 + 1
        and receipt.get("valid_samples") == valid
        and receipt.get("samples") == children
        and (valid >= n // 2 + 1 or (result.infrastructure_failure and not result.passed)),
        "aggregate_projection",
    )


def _journal_preimages(metadata: dict) -> list[dict]:
    """Enumerate only native quarantine projections and aggregate absent/null.

    These are metadata preimages, not financial/source admission. The exact
    committed old digest must select one; normal commercial readers still own
    full live custody and source validation before any primary dispatch.
    """
    variants = [deepcopy(metadata)]
    cohorts = metadata.get("commercial_component_cohorts") or {}
    _need(isinstance(cohorts, Mapping), "journal_shape")
    for key, journal in cohorts.items():
        if not isinstance(journal, Mapping):
            continue
        slots = journal.get("slots") or {}
        _need(isinstance(slots, Mapping), "journal_slots")
        changed = False
        for slot, entry in slots.items():
            if not isinstance(entry, Mapping) or entry.get("state") != "quarantined":
                continue
            history = entry.get("quarantine_history")
            _need(isinstance(history, list) and history, "quarantine_history")
            saved = history[-1]
            prepared, proof = saved["prepared_entry"], saved["proof"]
            _need(
                isinstance(prepared, Mapping)
                and prepared.get("state") == "prepared"
                and isinstance(proof, Mapping)
                and proof.get("schema") == "commercial-slot-quarantine.v1"
                and proof.get("cohort_key") == key
                and proof.get("slot") == slot,
                "quarantine_preimage",
            )
            envelope = json.loads(entry["report"]["raw_excerpt"])
            _need(
                envelope.get("schema") == "commercial-slot-quarantine.v1"
                and envelope.get("proof_sha256") == _digest(proof)
                and envelope.get("sample_slot") == slot
                and entry
                == {
                    **deepcopy(dict(prepared)),
                    "state": "quarantined",
                    "quarantine_history": [*(prepared.get("quarantine_history") or []), saved],
                    "report": entry["report"],
                },
                "quarantine_projection",
            )
            added = []
            for value in variants:
                value = deepcopy(value)
                value["commercial_component_cohorts"][key]["slots"][slot] = deepcopy(dict(prepared))
                added.append(value)
            variants.extend(added)
            _need(len(variants) <= 256, "metadata_reverse_candidate_limit")
            changed = True
        if changed and journal.get("aggregate") is not None:
            _aggregate_shape(journal)
            added = []
            for value in variants:
                for state in ("absent", "null"):
                    value2 = deepcopy(value)
                    target = value2["commercial_component_cohorts"][key]
                    if state == "absent":
                        target.pop("aggregate", None)
                    else:
                        target["aggregate"] = None
                    added.append(value2)
            variants.extend(added)
            _need(len(variants) <= 256, "metadata_reverse_candidate_limit")
    return list({_digest(value): value for value in variants}.values())


def _prior_metadata(owner: WorkflowRunModel, edge: Mapping, proof: Mapping) -> dict:
    metadata = deepcopy(owner.metadata_json or {})
    _need(metadata.get("review_recovery_policy") == _prepared(edge), "owner_recovery_receipt")
    history = metadata.get("terminal_state_history")
    _need(
        isinstance(history, list)
        and history
        and history[-1] == {"status": proof["previous_status"], "step": proof["previous_step"]},
        "terminal_history",
    )
    metadata.pop("review_recovery_policy")
    prior = history[:-1]
    metadata["terminal_state_history"] = prior
    candidates = [metadata]
    if not prior:
        absent = deepcopy(metadata)
        absent.pop("terminal_state_history")
        candidates.extend([absent, {**metadata, "terminal_state_history": None}])
    variants = {
        _digest(value): value for candidate in candidates for value in _journal_preimages(candidate)
    }
    _need(proof.get("previous_metadata_sha256") in variants, "previous_metadata_digest")
    return {
        "recorded_sha256": proof["previous_metadata_sha256"],
        "observed_sha256": _digest(owner.metadata_json or {}),
    }


async def validate_failed_review_recovery_lineage(
    session: AsyncSession,
    *,
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    previous: WorkflowRunModel,
    execution_scope_id: str,
    current_policy_hash: str,
) -> dict:
    """Validate the latest waiting owner and every native edge back to failure.

    The caller still owns locking, current policy admission and the existing
    allowance/checkpoint path. An old failed helper is deliberately not rerun
    against reports produced by its legitimate successors.
    """
    try:
        with session.no_autoflush:
            return await _validate(
                session,
                settings,
                project,
                chapter,
                draft,
                previous,
                execution_scope_id,
                current_policy_hash,
            )
    except (TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise ValueError("chapter_review_recovery_lineage_invalid_shape") from None
    except ValueError as error:
        if str(error).startswith("chapter_review_recovery_lineage_"):
            raise
        raise ValueError("chapter_review_recovery_lineage_invalid_source") from None


async def _validate(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    previous: WorkflowRunModel,
    scope: str,
    policy: str,
) -> dict:
    from bestseller.services import pipelines

    _need(not session.new and not session.dirty and not session.deleted, "uncommitted_source")
    _need(
        _sha(policy) and policy == pipelines.chapter_review_recovery_source_hash(), "current_policy"
    )
    _need(
        isinstance(scope, str)
        and 0 < len(scope) <= 256
        and chapter.project_id == draft.project_id == previous.project_id == project.id
        and draft.chapter_id == previous.scope_id == chapter.id
        and previous.scope_type == "chapter"
        and previous.workflow_type == "chapter_pipeline"
        and previous.status == "machine_blocked"
        and previous.current_step == WAITING,
        "latest_owner_binding",
    )
    for model, row, fields in (
        (ProjectModel, project, ("metadata_json",)),
        (ChapterModel, chapter, ("project_id", "metadata_json")),
        (
            WorkflowRunModel,
            previous,
            (
                "project_id",
                "scope_id",
                "scope_type",
                "workflow_type",
                "status",
                "current_step",
                "metadata_json",
                "error_message",
            ),
        ),
    ):
        stored = (
            await session.execute(
                select(*(getattr(model, k) for k in fields)).where(model.id == row.id)
            )
        ).one_or_none()
        _need(
            stored is not None and all(getattr(stored, k) == getattr(row, k) for k in fields),
            "stored_source",
        )
    current = (
        await session.execute(
            select(
                ChapterDraftVersionModel.id,
                ChapterDraftVersionModel.content_md,
                ChapterDraftVersionModel.promotion_state,
                ChapterDraftVersionModel.promoted_at,
            ).where(
                ChapterDraftVersionModel.project_id == project.id,
                ChapterDraftVersionModel.chapter_id == chapter.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
        )
    ).all()
    _need(
        len(current) == 1
        and current[0].id == draft.id
        and draft.is_current
        and current[0].content_md == draft.content_md
        and current[0].promotion_state == draft.promotion_state
        and current[0].promoted_at is None
        and draft.promoted_at is None
        and draft.promotion_state
        in {"candidate", "under_review", "eligible", "rejected", "superseded", "quarantined"}
        and isinstance(draft.content_md, str)
        and draft.content_md.strip(),
        "current_body",
    )
    fingerprint = pipelines.chapter_review_body_fingerprint(draft.content_md)
    cm = chapter.metadata_json or {}
    _need((project.metadata_json or {}).get("execution_scope_id") == scope, "project_scope")
    cycles, history = cm.get("chapter_critic_cycles"), cm.get("chapter_review_recovery_cycles")
    _need(isinstance(cycles, Mapping) and isinstance(history, list), "history_required")
    related = {}
    owners = {}
    for key, cycle in cycles.items():
        if not isinstance(cycle, Mapping) or cycle.get("source_draft_id") != str(draft.id):
            continue
        _need(cycle.get("source_body_hash") == fingerprint["digest"], "cycle_body")
        _need(
            cycle.get("cycle_id") == key
            and cycle.get("schema_version") == "chapter-critic-cycle.v1"
            and _sha(cycle.get("policy_source_hash")),
            "cycle_identity",
        )
        owner = await session.get(WorkflowRunModel, UUID(cycle["review_workflow_run_id"]))
        _need(
            owner is not None
            and owner.project_id == project.id
            and owner.scope_id == chapter.id
            and owner.scope_type == "chapter"
            and owner.workflow_type == "chapter_pipeline"
            and (owner.metadata_json or {}).get("execution_scope_id") == scope
            and (owner.metadata_json or {}).get("critic_cycle_id") == key,
            "cycle_owner",
        )
        _need(str(owner.id) not in related, "cycle_ambiguity")
        related[str(owner.id)], owners[str(owner.id)] = cycle, owner
    _need(str(previous.id) in related, "latest_cycle_missing")
    latest = related[str(previous.id)]
    active = cm.get("chapter_review_active_cycle")
    _need(
        isinstance(active, Mapping)
        and active.get("cycle_id") == latest["cycle_id"]
        and active.get("result_workflow_run_id") == str(previous.id),
        "latest_active_owner",
    )
    _need(
        latest.get("state") == WAITING and latest["policy_source_hash"] != policy,
        "waiting_policy_change_required",
    )
    edges, parents = {}, {}
    for edge in history:
        _need(isinstance(edge, Mapping), "recovery_shape")
        if edge.get("source_draft_id") != str(draft.id):
            continue
        _need(edge.get("source_body_hash") == fingerprint["digest"], "recovery_body")
        parent, child = edge.get("previous_workflow_run_id"), edge.get("result_workflow_run_id")
        _need(
            parent in related
            and child in related
            and child != parent
            and edge.get("state")
            == (WAITING if related[child].get("state") == WAITING else "completed")
            and related[child].get("state") in {WAITING, "completed"}
            and edge.get("execution_scope_id") == scope
            and edge.get("cycle_id") == related[child]["cycle_id"]
            and edge.get("policy_source_hash") == related[child]["policy_source_hash"]
            and edge["cycle_id"]
            == pipelines._chapter_critic_cycle_id(
                project.id, chapter.id, fingerprint["digest"], edge["policy_source_hash"]
            )
            and edge["policy_source_hash"] != related[parent]["policy_source_hash"]
            and type(edge.get("attempts_base")) is int
            and edge["attempts_base"] >= 0,
            "recovery_edge_binding",
        )
        _need(child not in edges and parent not in parents, "nonlinear_chain")
        edges[child], parents[parent] = edge, child
    _need(
        edges.get(str(previous.id)) == active and str(previous.id) not in parents,
        "latest_recovery_receipt",
    )
    chain, visited, cursor = [], set(), str(previous.id)
    while cursor in edges:
        _need(cursor not in visited, "cyclic_chain")
        visited.add(cursor)
        edge = edges[cursor]
        chain.append(edge)
        cursor = edge["previous_workflow_run_id"]
    _need(
        cursor not in visited and len(chain) == len(edges) and set(related) == visited | {cursor},
        "unconsumed_or_disconnected_owner",
    )
    chain.reverse()
    root = owners[cursor]
    origin = chain[0].get("failed_origin_proof") if chain else None
    _checked_proof(origin, "failed-review-policy-source.v1")
    _need(
        origin.get("status") == "verified_failed_review_origin"
        and origin.get("origin_only") is True
        and root.status == origin.get("previous_status") == "failed"
        and root.current_step == origin.get("previous_step")
        and root.error_message == "workflow_failed"
        and origin.get("previous_workflow_run_id") == str(root.id)
        and origin.get("project_id") == str(project.id)
        and origin.get("chapter_id") == str(chapter.id)
        and origin.get("draft_id") == str(draft.id)
        and origin.get("execution_scope_id") == scope
        and origin.get("source_body_fingerprint") == fingerprint
        and origin.get("current_policy_source_hash") == chain[0]["policy_source_hash"]
        and origin.get("source_cycle") == related[cursor]
        and origin.get("source_cycle_sha256") == _digest(related[cursor])
        and origin.get("source_cycle_id") == related[cursor]["cycle_id"]
        and origin.get("previous_policy_source_hash") == related[cursor]["policy_source_hash"],
        "failed_origin_binding",
    )
    failures = (root.metadata_json or {}).get("execution_failure_receipts")
    _need(
        isinstance(failures, list) and failures and failures[-1] == origin.get("failure_receipt"),
        "failure_receipt_changed",
    )
    semantic = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
    _need(
        _sha(semantic)
        and origin.get("semantic_dependencies_sha256") == semantic
        and all(
            c.get("semantic_dependencies_sha256") == semantic
            and not c.get("semantic_dependency_change_detected")
            for c in related.values()
        ),
        "semantic_dependencies_changed",
    )
    hops = []
    for index, edge in enumerate(chain):
        parent = owners[edge["previous_workflow_run_id"]]
        if index == 0:
            source = origin
        else:
            source = edge.get("failed_lineage_proof")
            _checked_proof(source, SCHEMA)
            _need(
                source.get("status") == "verified_failed_review_lineage"
                and source.get("lineage_only") is True
                and source.get("hops") == hops
                and source.get("origin_proof_sha256") == origin["proof_sha256"]
                and source.get("previous_workflow_run_id") == str(parent.id)
                and source.get("project_id") == str(project.id)
                and source.get("chapter_id") == str(chapter.id)
                and source.get("draft_id") == str(draft.id)
                and source.get("execution_scope_id") == scope
                and source.get("source_body_fingerprint") == fingerprint
                and source.get("source_cycle_id") == related[str(parent.id)]["cycle_id"]
                and source.get("source_cycle_sha256") == _digest(related[str(parent.id)])
                and source.get("previous_policy_source_hash")
                == related[str(parent.id)]["policy_source_hash"]
                and source.get("current_policy_source_hash") == edge["policy_source_hash"]
                and source.get("semantic_dependencies_sha256") == semantic,
                "successor_proof_binding",
            )
        metadata_reverse = _prior_metadata(parent, edge, source)
        hops.append(
            {
                "cycle_id": edge["cycle_id"],
                "previous_workflow_run_id": str(parent.id),
                "result_workflow_run_id": edge["result_workflow_run_id"],
                "receipt_sha256": _digest(edge),
                "metadata_reverse": metadata_reverse,
            }
        )
    report_ids, quality_ids, last = set(), set(), None
    for owner_id, cycle in related.items():
        owner, records = owners[owner_id], []
        _need(
            cycle["cycle_id"]
            in {
                pipelines._chapter_critic_cycle_id(project.id, chapter.id, fingerprint["digest"]),
                pipelines._chapter_critic_cycle_id(
                    project.id, chapter.id, fingerprint["digest"], cycle["policy_source_hash"]
                ),
            },
            "canonical_cycle",
        )
        if owner is not root:
            _need(
                owner.status == "machine_blocked"
                and owner.current_step
                in {WAITING, "chapter_adoption_required", "critic_evidence_unknown"}
                and (owner.metadata_json or {}).get("review_recovery_source")
                == {"draft_id": str(draft.id), "body_hash": fingerprint["digest"]},
                "successor_owner",
            )
        attempts = cycle.get("attempts")
        _need(isinstance(attempts, list) and 1 <= len(attempts) <= 2, "attempts")
        for index, entry in enumerate(attempts, 1):
            _need(
                isinstance(entry, Mapping)
                and type(entry.get("attempt")) is int
                and entry["attempt"] == index
                and entry.get("operation_namespace")
                == f"chapter-critic:{cycle['cycle_id']}:{index}",
                "attempt_binding",
            )
            if entry.get("state") == WAITING:
                _need(index == len(attempts), "waiting_tail")
                binding = wait_binding(
                    cycle,
                    entry,
                    project_id=project.id,
                    chapter_id=chapter.id,
                    body_fingerprint=fingerprint,
                )
                for record in waiting_history(cycle, entry):
                    report = await session.get(ReviewReportModel, UUID(record["review_report_id"]))
                    validate_waiting_record(report, record=record, binding=binding)
                    records.append(record)
            elif entry.get("state") == "completed":
                records.append(entry)
            else:
                _need(
                    owner is root
                    and index == len(attempts)
                    and records
                    and entry.get("state") in {"prepared", "running"}
                    and all(
                        entry.get(k) is None
                        for k in (
                            "outcome",
                            "review_report_id",
                            "quality_score_id",
                            "rewrite_task_id",
                        )
                    ),
                    "unreturned_attempt",
                )
        _need(records, "unknown_record_required")
        for record in records:
            _need(
                record.get("outcome") == "unknown"
                and record.get("draft_id") == str(draft.id)
                and record.get("body_fingerprint") == fingerprint
                and record.get("rewrite_task_id") is None,
                "unknown_record_binding",
            )
            report = await session.get(ReviewReportModel, UUID(record["review_report_id"]))
            quality = await session.get(QualityScoreModel, UUID(record["quality_score_id"]))
            payload = report.structured_output or {} if report is not None else {}
            evidence = payload.get("critic_evidence") or {}
            _need(
                report is not None
                and report.project_id == project.id
                and report.target_type == "chapter"
                and report.target_id == chapter.id
                and report.verdict == "unknown"
                and report.llm_run_id is None
                and payload.get("draft_id") == str(draft.id)
                and payload.get("reviewed_body_fingerprint") == fingerprint
                and evidence.get("status") in {"unavailable", "evidence_invalid", "fallback"}
                and evidence.get("passed") is False
                and all(
                    payload.get(k) is None
                    for k in (
                        "rewrite_instructions",
                        "primary_submission_receipt",
                        "primary_submission_original",
                    )
                )
                and quality is not None
                and quality.project_id == project.id
                and quality.target_type == "chapter"
                and quality.target_id == chapter.id
                and quality.chapter_draft_version_id == draft.id
                and quality.review_report_id == report.id,
                "unknown_report_quality",
            )
            _need(
                str(report.id) not in report_ids and str(quality.id) not in quality_ids,
                "duplicate_result",
            )
            report_ids.add(str(report.id))
            quality_ids.add(str(quality.id))
            if owner_id == str(previous.id):
                last = report, quality
    _need(
        last is not None
        and cm.get("chapter_review_evidence_status") == "unknown"
        and cm.get("chapter_review_evidence_report_id") == str(last[0].id),
        "latest_unknown_report",
    )
    wm = previous.metadata_json or {}
    waiting = wm.get("review_prerequisite_wait")
    _need(
        isinstance(waiting, Mapping)
        and all(
            waiting.get(k) == value
            for k, value in {
                "schema": "chapter-review-first-reuse.v1",
                "project_id": str(project.id),
                "chapter_id": str(chapter.id),
                "draft_id": str(draft.id),
                "body_sha256": fingerprint["digest"],
                "cycle_id": latest["cycle_id"],
                "policy_source_hash": latest["policy_source_hash"],
                "semantic_dependencies_sha256": semantic,
                "report_id": str(last[0].id),
                "quality_score_id": str(last[1].id),
                "review_workflow_run_id": str(previous.id),
                "verdict": "unknown",
                "reuse_kind": WAITING,
                "task_id": None,
                "dispatch_authorized": False,
                "credit_released": False,
            }.items()
        )
        and wm.get("review_report_id") == str(last[0].id)
        and wm.get("quality_score_id") == str(last[1].id)
        and wm.get("chapter_critic_evidence_unknown") is True
        and wm.get("requires_human_review") is False
        and waiting.get("requires_human_review") is False
        and waiting.get("dispatch_authorized") is False
        and waiting.get("credit_released") is False,
        "latest_waiting_receipt",
    )
    reports = list(
        await session.scalars(
            select(ReviewReportModel).where(
                ReviewReportModel.project_id == project.id,
                ReviewReportModel.target_type == "chapter",
                ReviewReportModel.target_id == chapter.id,
            )
        )
    )
    current_reports = [
        r
        for r in reports
        if (r.structured_output or {}).get("draft_id") == str(draft.id)
        or (r.structured_output or {}).get("reviewed_body_fingerprint") == fingerprint
    ]
    _need(
        {str(r.id) for r in current_reports} == report_ids
        and all(r.created_at <= last[0].created_at for r in current_reports),
        "report_inventory",
    )
    stored_quality = set(
        await session.scalars(
            select(QualityScoreModel.id).where(
                QualityScoreModel.project_id == project.id,
                QualityScoreModel.target_type == "chapter",
                QualityScoreModel.target_id == chapter.id,
                QualityScoreModel.chapter_draft_version_id == draft.id,
            )
        )
    )
    _need({str(k) for k in stored_quality} == quality_ids, "quality_inventory")
    known_prior = {
        r.llm_run_id
        for r in reports
        if r.llm_run_id is not None
        and r not in current_reports
        and (r.structured_output or {}).get("draft_id") is not None
    }
    primary = list(
        await session.scalars(
            select(LlmRunModel).where(
                LlmRunModel.project_id == project.id,
                LlmRunModel.workflow_run_id.in_([o.id for o in owners.values()]),
                LlmRunModel.prompt_template == "chapter_review",
            )
        )
    )
    raw_body = hashlib.sha256(draft.content_md.encode()).hexdigest()
    _need(
        all(
            (r.metadata_json or {}).get("primary_review_body_sha256") != raw_body
            and (
                r.id in known_prior
                or _sha((r.metadata_json or {}).get("primary_review_body_sha256"))
            )
            for r in primary
        ),
        "primary_returned_or_unattributed",
    )
    closure = await _live_closure(session, project, scope, origin, set(owners))
    from bestseller.services.feedback import validate_adopted_knowledge_source

    _, rejection = await validate_adopted_knowledge_source(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id,
        chapter_md=draft.content_md,
        lock=False,
    )
    _need(rejection == "source_not_adopted", "source_adopted_or_ambiguous")
    proof = {
        "schema": SCHEMA,
        "status": "verified_failed_review_lineage",
        "lineage_only": True,
        "project_id": str(project.id),
        "chapter_id": str(chapter.id),
        "draft_id": str(draft.id),
        "previous_workflow_run_id": str(previous.id),
        "previous_status": previous.status,
        "previous_step": previous.current_step,
        "previous_metadata_sha256": _digest(wm),
        "execution_scope_id": scope,
        "source_body_fingerprint": fingerprint,
        "source_cycle_id": latest["cycle_id"],
        "source_cycle_sha256": _digest(latest),
        "previous_policy_source_hash": latest["policy_source_hash"],
        "current_policy_source_hash": policy,
        "semantic_dependencies_sha256": semantic,
        "origin_proof_sha256": origin["proof_sha256"],
        "hops": hops,
        "latest_report_id": str(last[0].id),
        "latest_quality_score_id": str(last[1].id),
        **closure,
        **dict.fromkeys(_DENIALS, False),
    }
    proof["proof_sha256"] = _digest(proof)
    return proof


async def _live_closure(
    session: AsyncSession,
    project: ProjectModel,
    scope: str,
    origin: Mapping,
    owners: set[str],
) -> dict:
    _need(
        await session.scalar(
            select(WorkflowRunModel.id)
            .where(
                WorkflowRunModel.project_id == project.id,
                WorkflowRunModel.status.in_(("pending", "queued", "running")),
            )
            .limit(1)
        )
        is None,
        "active_workflow",
    )
    control = await session.get(BookProductionControlModel, project.id)
    _need(
        control is not None
        and control.desired_state == "run"
        and type(control.command_serial) is int
        and control.command_serial > 0,
        "explicit_run",
    )
    budget = await session.get(ExecutionBudgetModel, scope)
    auth = completion_authorization(budget) if budget is not None else None
    _need(
        budget is not None
        and budget.status in {"active", "exhausted"}
        and auth is not None
        and auth.get("status") == "active"
        and execution_scope_block_code(budget) is None,
        "completion_authorization",
    )
    operations = list(
        await session.scalars(
            select(ExecutionOperationModel).where(
                ExecutionOperationModel.scope_id == scope,
            )
        )
    )
    attempts = list(
        await session.scalars(
            select(ExecutionAttemptModel).where(
                ExecutionAttemptModel.scope_id == scope,
            )
        )
    )
    _need(
        not any(o.status in {"pending", "unknown", "reserved", "running"} for o in operations)
        and not any(a.status in {"pending", "unknown", "reserved", "running"} for a in attempts),
        "unsettled_finance",
    )
    by_op, by_attempt = {o.key: o for o in operations}, {a.id: a for a in attempts}
    stopped = []
    for transport in operations:
        t = transport.result_json or {}
        if (
            transport.status != "transport_failure_receipt"
            or t.get("workflow_run_id") not in owners
        ):
            continue
        op, attempt = by_op.get(t.get("operation_key")), by_attempt.get(t.get("attempt_id"))
        _need(
            t.get("schema") == "transport-failure.v1"
            and t.get("project_id") == str(project.id)
            and op is not None
            and attempt is not None
            and op.status == "stopped"
            and op.result_json is None
            and attempt.operation_key == op.key
            and attempt.input_hash == op.input_hash
            and attempt.status == "reconciled_charged",
            "charged_root",
        )
        receipts = [
            o.result_json
            for o in operations
            if o.status == "reconciliation_receipt"
            and isinstance(o.result_json, Mapping)
            and (o.result_json.get("receipt") or {}).get("attempt_id") == attempt.id
        ]
        _need(len(receipts) == 1, "exit_receipt")
        record = receipts[0]
        r = record.get("receipt") or {}
        _need(
            record.get("evidence_kind") == "dispatcher_exit"
            and record.get("local_dispatcher_stopped") is True
            and record.get("remote_operation_closed") is False
            and _sha(record.get("evidence_sha256"))
            and record.get("actor")
            and r.get("scope_id") == scope
            and r.get("decision") == "quarantine_charged"
            and r.get("remaining_unknown") == 0
            and r.get("credit_released") is False
            and r.get("automatic_resume") is False
            and r.get("remote_outcome") == "unknown_quarantined"
            and r.get("operation_replay_allowed") is False
            and type(r.get("calls_used")) is int
            and 0 < r["calls_used"] <= budget.calls_used
            and type(r.get("tokens_charged")) is int
            and type(attempt.reserved_tokens) is int
            and 0 < attempt.reserved_tokens <= r["tokens_charged"] <= budget.tokens_charged,
            "charged_exit",
        )
        stopped.append(
            {
                "operation_key": op.key,
                "attempt_id": attempt.id,
                "reserved_tokens": attempt.reserved_tokens,
                "receipt_sha256": _digest(record),
            }
        )
    _need(
        isinstance(origin.get("stopped_transports"), list)
        and origin["stopped_transports"]
        and all(s in stopped for s in origin["stopped_transports"]),
        "origin_finance_changed",
    )
    usage = origin.get("budget_usage") or {}
    _need(
        type(usage.get("calls_used")) is int
        and usage["calls_used"] <= budget.calls_used
        and type(usage.get("tokens_charged")) is int
        and usage["tokens_charged"] <= budget.tokens_charged,
        "charge_regression",
    )
    return {
        "control_command_serial": control.command_serial,
        "authorization_ref": auth["authorization_ref"],
        "authorization_sha256": _digest(auth),
        "budget_usage": {"calls_used": budget.calls_used, "tokens_charged": budget.tokens_charged},
        "stopped_transports": sorted(stopped, key=lambda s: (s["operation_key"], s["attempt_id"])),
    }
