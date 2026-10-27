"""Introduce a scoped derivative of a paid return, requiring fresh real review.

Prepare is read only. Apply owns a savepoint, never the caller's transaction.
The paid request may be unavailable: this is body derivation, not request reuse,
grade transfer, adoption, policy migration or financial credit.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select

from bestseller.infra.db import models as db
from bestseller.services.retained_rewrite_projection import project_retained_rewrite
from bestseller.services.rewrite_fact_scope import derive_fact_repair_scope
from bestseller.services.rewrite_patch import text_hash

SCHEMA = "retained-fact-candidate.v1"
_PHYSICAL = ("content", "model_name", "provider", "tool_calls", "raw_message",
             "input_tokens", "output_tokens", "finish_reason", "fallback_used", "fallback_source")


def _need(condition, code):
    if not condition:
        raise ValueError("retained_fact_candidate_" + code)


def _json(value):
    def encode(item):
        if isinstance(item, (UUID, date, datetime, Decimal)):
            return str(item)
        raise TypeError("unsupported witness value")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, default=encode)


def _columns(row):
    return json.loads(_json({p.key: getattr(row, p.key) for p in row.__mapper__.column_attrs}))


def _clean(session):
    _need(session.is_active and not (session.new or session.dirty or session.deleted), "clean_session_required")


async def _rows(session, model, *conditions, lock=False):
    query = select(model).where(*conditions).execution_options(populate_existing=True)
    rows = list(await session.scalars(query.with_for_update() if lock else query))
    return sorted(rows, key=lambda row: _json(_columns(row)))


async def _get(session, model, identity, *, lock=False):
    key = model.__mapper__.primary_key[0]
    rows = await _rows(session, model, key == identity, lock=lock)
    _need(len(rows) == 1, "missing_" + model.__tablename__)
    return rows[0]


async def _prepare(session, settings, *, project_id, chapter_id, task_id, lock=False):
    from bestseller.services.pipelines import _chapter_critic_semantic_dependencies
    from bestseller.services.reviews import chapter_review_body_fingerprint
    from bestseller.settings import apply_runtime_llm_profile
    from bestseller.services.drafts import count_words

    chapter = await _get(session, db.ChapterModel, chapter_id, lock=lock)
    project = await _get(session, db.ProjectModel, project_id, lock=lock)
    task = await _get(session, db.RewriteTaskModel, task_id, lock=lock)
    _need(chapter.project_id == task.project_id == project.id and task.trigger_source_id == chapter.id
          and task.trigger_type == "chapter_review" and task.status == "completed"
          and type(task.attempts) is int and task.attempts > 0, "task_scope")
    meta = task.metadata_json
    parent = await _get(session, db.ChapterDraftVersionModel, UUID(meta["parent_draft_id"]), lock=lock)
    returned = await _get(session, db.ChapterDraftVersionModel, UUID(meta["candidate_chapter_draft_id"]), lock=lock)
    report = await _get(session, db.ReviewReportModel, UUID(meta["review_report_id"]), lock=lock)
    _need(parent.id != returned.id and all(row.project_id == project.id for row in (parent, returned, report))
          and parent.chapter_id == returned.chapter_id == report.target_id == chapter.id
          and report.target_type == "chapter" and report.verdict == "rewrite", "source_scope")
    _need(parent.promotion_state == returned.promotion_state == "candidate" and not returned.is_current
          and parent.promoted_at is None and returned.promoted_at is None
          and meta["parent_draft_hash"] == text_hash(parent.content_md)
          and meta["candidate_body_sha256"] == text_hash(returned.content_md)
          and returned.llm_run_id is not None and meta["candidate_llm_run_id"] == str(returned.llm_run_id),
          "source_binding")
    source_key = f"{SCHEMA}/{project.id}/{chapter.id}/{task.id}"
    candidate_id = uuid5(NAMESPACE_URL, source_key)
    drafts = await _rows(session, db.ChapterDraftVersionModel,
                        db.ChapterDraftVersionModel.chapter_id == chapter.id, lock=lock)
    _need(all(row.project_id == project.id and row.promotion_state != "promoted"
              and row.promoted_at is None for row in drafts)
          and not (chapter.metadata_json or {}).get("numeric_promotion_debt"), "chapter_adopted")
    existing = next((row for row in drafts if row.id == candidate_id), None)
    current = [row for row in drafts if row.is_current]
    _need(len(current) == 1 and current[0].id == (candidate_id if existing else parent.id), "current_pointer")
    _need(parent.is_current is (existing is None), "parent_not_current")
    payload = report.structured_output
    fingerprint = chapter_review_body_fingerprint(parent.content_md)
    _need(payload["draft_id"] == str(parent.id) and payload["reviewed_body_fingerprint"] == fingerprint,
          "report_body")
    cycles = chapter.metadata_json["chapter_critic_cycles"]
    _need(isinstance(cycles, dict), "cycles_malformed")
    matches = [(cycle, attempt) for cycle in cycles.values()
               if cycle.get("source_draft_id") == str(parent.id)
               for attempt in cycle.get("attempts", []) if attempt.get("review_report_id") == str(report.id)]
    _need(len(matches) == 1, "cycle_ambiguous")
    cycle, review_attempt = matches[0]
    _need(cycle.get("state") == review_attempt.get("state") == "completed"
          and cycle.get("source_body_hash") == fingerprint["digest"]
          and not cycle.get("semantic_dependency_change_detected")
          and review_attempt.get("outcome") == "available"
          and review_attempt.get("draft_id") == str(parent.id)
          and review_attempt.get("body_fingerprint") == fingerprint
          and review_attempt.get("rewrite_task_id") == str(task.id), "cycle_binding")
    quality = await _get(session, db.QualityScoreModel, UUID(review_attempt["quality_score_id"]), lock=lock)
    _need(quality.project_id == project.id and quality.target_type == "chapter"
          and quality.target_id == chapter.id and quality.chapter_draft_version_id == parent.id
          and quality.review_report_id == report.id, "quality_source")
    effective_settings = apply_runtime_llm_profile(settings)
    semantic = await _chapter_critic_semantic_dependencies(session, effective_settings, chapter)
    _need(semantic == cycle.get("semantic_dependencies_sha256"), "semantic_changed")
    scope = derive_fact_repair_scope(parent.content_md, payload)
    _need(scope is not None, "fact_scope_unavailable")
    run = await _get(session, db.LlmRunModel, returned.llm_run_id, lock=lock)
    _need(run.project_id == project.id and run.logical_role == "editor"
          and run.prompt_template == "chapter_rewrite" and run.workflow_run_id is not None
          and run.finish_reason == "stop" and run.provider.lower() not in {"", "mock", "fallback", "test", "unknown"},
          "paid_run")
    workflow = await _get(session, db.WorkflowRunModel, run.workflow_run_id, lock=lock)
    _need(workflow.project_id == project.id, "paid_workflow")
    origin = {"schema": "paid-call-origin.v1", "status": "recorded", **{
        key: str(getattr(run, key)) if getattr(run, key) is not None else None
        for key in ("project_id", "workflow_run_id", "step_run_id", "logical_role",
                    "prompt_template", "prompt_version", "prompt_hash")}}
    run_meta = run.metadata_json
    _need(run_meta["paid_call_origin"] == origin, "paid_origin")
    receipt, effective = run_meta["execution_receipt"], run_meta["effective_input_receipt"]
    _need(effective.get("schema") == "effective-input.v1" and effective.get("model") == run.model_name
          and effective.get("hash") == run.prompt_hash, "effective_input")
    operation = await _get(session, db.ExecutionOperationModel, receipt["operation_key"], lock=lock)
    budget = await _get(session, db.ExecutionBudgetModel, receipt["scope_id"], lock=lock)
    _need(operation.status == "completed" and operation.scope_id == receipt["scope_id"]
          and operation.input_hash == run.prompt_hash, "operation_binding")
    attempts = await _rows(session, db.ExecutionAttemptModel,
                           db.ExecutionAttemptModel.operation_key == operation.key, lock=lock)
    recorded = receipt["attempts"]
    _need(attempts and len(attempts) == len(recorded)
          and {a.id for a in attempts} == {a["attempt_id"] for a in recorded}, "attempt_set")
    result, transports = operation.result_json, []
    _need(result["content"] == returned.content_md and result["provider"] == run.provider
          and result["model_name"] == run.model_name and result["finish_reason"] == run.finish_reason
          and result["fallback_used"] is False and result["fallback_source"] is None
          and result.get("llm_run_id") in (None, str(run.id))
          and all(type(result[key]) is int and result[key] >= 0 and result[key] == getattr(run, key)
                  for key in ("input_tokens", "output_tokens")), "paid_body")
    for attempt in attempts:
        recorded_attempt = next(a for a in recorded if a["attempt_id"] == attempt.id)
        _need(attempt.status == "completed" and attempt.scope_id == operation.scope_id
              and attempt.model_name == run.model_name and attempt.input_hash == run.prompt_hash
              and attempt.actual_tokens == run.input_tokens + run.output_tokens
              and attempt.actual_tokens > 0 and attempt.reserved_tokens >= attempt.actual_tokens
              and recorded_attempt["status"] == "completed" and recorded_attempt["model"] == attempt.model_name
              and recorded_attempt["effective_input_hash"] == attempt.input_hash
              and recorded_attempt["reserved_tokens"] == attempt.reserved_tokens, "attempt_binding")
        key = text_hash("transport-result.v1\n" + operation.scope_id + "\n" + attempt.id)
        transport = await _get(session, db.ExecutionOperationModel, key, lock=lock)
        wire = transport.result_json
        _need(transport.status == "transport_result" and transport.scope_id == operation.scope_id
              and transport.input_hash == operation.input_hash and wire["schema"] == "transport-result.v1"
              and wire["attempt_id"] == attempt.id and wire["operation_key"] == operation.key
              and wire["input_hash"] == operation.input_hash, "transport_binding")
        physical = wire["result"]
        _need(all(_json(physical[key]) == _json(result[key]) for key in _PHYSICAL), "physical_mismatch")
        for entry in (physical["metadata"], result["metadata"]):
            original = entry["effective_input_receipt"]
            _need(entry["paid_call_origin"] == origin
                  and all(original.get(key) == effective[key] for key in ("schema", "hash", "model"))
                  and all(key in effective and _json(effective[key]) == _json(value)
                          for key, value in original.items()), "transport_metadata")
        transports.append(_columns(transport))
    _need(budget.calls_used >= len(attempts) and budget.tokens_charged >= sum(a.actual_tokens for a in attempts),
          "charge_missing")
    projection = project_retained_rewrite(parent.content_md, result["content"],
        authorized_spans=scope["authorized_spans"], expected_parent_sha256=text_hash(parent.content_md),
        expected_return_sha256=text_hash(result["content"]))
    _need(projection.accepted and projection.candidate_text != parent.content_md, "projection_rejected")
    chapter_source = _columns(chapter)
    original_fields = {key: chapter_source[key] for key in ("current_word_count", "production_state", "updated_at")}
    if existing:
        saved = existing.promotion_metadata
        _need(saved["schema"] == SCHEMA and saved["source_key"] == source_key and existing.llm_run_id is None
              and existing.promotion_state == "candidate" and existing.promotion_score is None
              and existing.content_md == projection.candidate_text
              and existing.word_count == count_words(projection.candidate_text)
              and chapter.production_state == "review_unknown"
              and chapter.current_word_count == existing.word_count, "existing_candidate_changed")
        original_fields = saved["original_chapter_fields"]
        chapter_source.update(original_fields)
    source_drafts = [_columns(row) for row in drafts if row.id != candidate_id]
    next_version = max(row.version_no for row in drafts if row.id != candidate_id) + 1
    for row in source_drafts:
        if row["id"] == str(parent.id):
            row["is_current"] = True
    witness = {"schema": SCHEMA, "source_key": source_key, "project": _columns(project),
        "chapter": chapter_source, "drafts": source_drafts, "task": _columns(task),
        "report": _columns(report), "quality": _columns(quality), "paid_run": _columns(run),
        "paid_workflow": _columns(workflow), "operation": _columns(operation), "budget": _columns(budget),
        "attempts": [_columns(a) for a in attempts], "transports": transports,
        "semantic_dependencies_sha256": semantic, "edit_scope": scope, "projection": asdict(projection),
        "effective_config_sha256": text_hash(_json({key: getattr(effective_settings, key).model_dump(mode="json")
            for key in ("llm", "pipeline", "quality", "generation")})),
        "source_sha256": {name: text_hash(Path(__file__).with_name(name).read_text()) for name in
            ("retained_fact_candidate.py", "rewrite_fact_scope.py", "retained_rewrite_projection.py")}}
    digest = text_hash(_json(witness))
    if existing:
        _need(existing.version_no == next_version and saved["witness_sha256"] == digest
              and _json(saved["projection"]) == _json(asdict(projection)["receipt"]) and saved["edit_scope"] == scope,
              "existing_witness_changed")
    return {"schema": SCHEMA, "witness": witness, "witness_sha256": digest,
        "candidate_id": str(candidate_id), "candidate_text": projection.candidate_text,
        "candidate_sha256": projection.receipt.candidate_sha256, "version_no": next_version,
        "word_count": count_words(projection.candidate_text), "source_key": source_key,
        "original_chapter_fields": original_fields, "fresh_review_required": True,
        "full_request_retained": bool(run.request_payload_ref),
        "unknowns": [] if run.request_payload_ref else ["full_original_request_payload_not_retained"],
        "adoption_authorized": False, "grades_modified": False, "grants_credit": False,
        "already_applied": existing is not None}, chapter, parent


async def prepare_retained_fact_candidate(session, settings, *, project_id, chapter_id, task_id):
    """Return a detached, complete source witness and candidate; no DML or commit."""
    _clean(session)
    with session.no_autoflush:
        try:
            prepared, _, _ = await _prepare(session, settings, project_id=project_id,
                                            chapter_id=chapter_id, task_id=task_id)
            return deepcopy(prepared)
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("retained_fact_candidate_malformed_source") from error


async def apply_retained_fact_candidate(session, settings, *, project_id, chapter_id, task_id,
                                       expected_witness_sha256, actor, evidence_ref):
    """Revalidate under row locks and atomically introduce one working candidate."""
    _clean(session)
    _need(isinstance(expected_witness_sha256, str) and len(expected_witness_sha256) == 64
          and all(c in "0123456789abcdef" for c in expected_witness_sha256), "expected_witness_required")
    _need(isinstance(actor, str) and actor.strip() and isinstance(evidence_ref, str) and evidence_ref.strip(),
          "operator_evidence_required")
    async with session.begin_nested():
        try:
            prepared, chapter, parent = await _prepare(session, settings, project_id=project_id,
                                                       chapter_id=chapter_id, task_id=task_id, lock=True)
            _need(prepared["witness_sha256"] == expected_witness_sha256, "witness_changed")
            if prepared["already_applied"]:
                return deepcopy(prepared)
            promotion_metadata = {key: deepcopy(prepared[key]) for key in (
                "schema", "source_key", "witness_sha256", "original_chapter_fields", "unknowns",
                "full_request_retained", "fresh_review_required", "adoption_authorized", "grades_modified", "grants_credit")}
            promotion_metadata.update(actor=actor, evidence_ref=evidence_ref,
                paid_llm_run_id=prepared["witness"]["paid_run"]["id"],
                parent_draft_id=str(parent.id), returned_draft_id=prepared["witness"]["task"]["metadata_json"]["candidate_chapter_draft_id"],
                edit_scope=prepared["witness"]["edit_scope"], projection=prepared["witness"]["projection"]["receipt"])
            candidate = db.ChapterDraftVersionModel(id=UUID(prepared["candidate_id"]), project_id=project_id,
                chapter_id=chapter_id, version_no=prepared["version_no"], content_md=prepared["candidate_text"],
                word_count=prepared["word_count"], is_current=True, llm_run_id=None, promotion_state="candidate",
                promotion_score=None, promotion_reason_codes=["origin:retained_fact_derivative", "fresh_review_required"],
                assembled_from_scene_draft_ids=deepcopy(parent.assembled_from_scene_draft_ids),
                promotion_metadata=promotion_metadata)
            parent.is_current = False
            await session.flush()  # Release the unique working pointer before inserting its successor.
            session.add(candidate)
            chapter.current_word_count = prepared["word_count"]
            chapter.production_state = "review_unknown"
            await session.flush()
            _need(candidate.llm_run_id is None and candidate.is_current, "write_readback")
            return deepcopy(prepared)
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("retained_fact_candidate_malformed_source") from error
