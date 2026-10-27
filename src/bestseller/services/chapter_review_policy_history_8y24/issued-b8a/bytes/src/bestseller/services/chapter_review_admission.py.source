"""Produce current-body admission evidence without adopting, grading or dispatching.

The caller persists this new receipt and owns transaction completion. Existing
paid reviews, grades, tasks, budgets and publication decisions are not rewritten.
No missing verifier is represented by a synthetic passing wrapper.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from bestseller.infra.db import models as db

POLICY = "reviewed-chapter-admission.v1"


def _sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False, default=str).encode()).hexdigest()


async def _physical_return(session, run):
    """Read one exact completed root/attempt/transport; never infer ownership."""
    from bestseller.services.llm import LLMCompletionResult

    execution = (run.metadata_json or {}).get("execution_receipt") or {}
    attempts = execution.get("attempts") or []
    if len(attempts) != 1 or attempts[0].get("status") != "completed":
        raise ValueError("paid_attempt_unverified")
    scope = execution.get("scope_id")
    root = await session.get(db.ExecutionOperationModel, execution.get("operation_key"))
    attempt = await session.get(db.ExecutionAttemptModel, attempts[0]["attempt_id"])
    key = hashlib.sha256(f"transport-result.v1\n{scope}\n{attempts[0]['attempt_id']}".encode()).hexdigest()
    transport = await session.get(db.ExecutionOperationModel, key)
    if (root is None or attempt is None or transport is None or not scope
            or root.scope_id != scope or root.status != "completed" or root.input_hash != run.prompt_hash
            or attempt.scope_id != scope or attempt.operation_key != root.key
            or attempt.status != "completed" or attempt.input_hash != run.prompt_hash
            or attempt.model_name != run.model_name or transport.scope_id != scope
            or transport.status != "transport_result"):
        raise ValueError("paid_physical_binding_unverified")
    raw = transport.result_json or {}
    if any(raw.get(k) != v for k, v in {
        "attempt_id": attempt.id, "operation_key": root.key, "input_hash": run.prompt_hash,
    }.items()):
        raise ValueError("paid_transport_binding_unverified")
    paid = LLMCompletionResult.model_validate(raw.get("result"))
    completed = LLMCompletionResult.model_validate(root.result_json)
    fields = ("content", "tool_calls", "raw_message", "provider", "model_name", "finish_reason",
              "input_tokens", "output_tokens", "fallback_used")
    origin = (paid.metadata or {}).get("paid_call_origin") or {}
    if (any(getattr(paid, key) != getattr(completed, key) for key in fields)
            or paid.fallback_used or paid.provider != run.provider or paid.model_name != run.model_name
            or paid.finish_reason != run.finish_reason or any(origin.get(k) != v for k, v in {
                "project_id": str(run.project_id), "workflow_run_id": str(run.workflow_run_id),
                "prompt_hash": run.prompt_hash, "prompt_template": run.prompt_template,
                "prompt_version": run.prompt_version,
            }.items())):
        raise ValueError("paid_return_or_origin_unverified")
    return paid.model_copy(update={"llm_run_id": run.id})


async def build_reviewed_chapter_admission(
    session, settings, *, project, chapter, draft, report, quality, workflow_id,
    commercial_sources=None, body_gate_receipt=None,
):
    """Run existing source/body checks and bind their actual results to a review.

    Exact CommercialSources from the review callsite are required if commercial
    judging ran. An existing current-call RewriteQualityGateReceipt can avoid
    duplicate gate telemetry; otherwise run that real gate, except when doing
    so could auto-sign new override/debt contracts. Disabled gate configuration
    is positively recorded; required-but-unavailable evaluation is unknown.
    Optional judge no-verdicts remain optional_unknown, never passing grades;
    source/physical binding failures are not waived by that availability flag.
    Neither primary PASS nor an empty finding list proves all semantic truth.
    """
    from bestseller.services import pipelines, reviews, drafts
    from bestseller.services.quality_gates_config import get_quality_gates_config
    from bestseller.services.quality_report_claim import report_grades_text

    unknown, optional_unknown, blockers, receipts = [], [], [], {}
    result = {"schema": POLICY, "draft_eligible": False, "quality_passed": False,
              "effective_blockers": blockers, "unknown": unknown, "optional_unknown": optional_unknown,
              "gate_receipts": receipts,
              "scores_modified": False, "verdict_modified": False, "adoption_authorized": False,
              "publication_approved": False, "producer_source_sha256": hashlib.sha256(
                  Path(__file__).read_bytes()).hexdigest()}

    def blocked(gate, code, evidence=None):
        blockers.append({"gate": gate, "code": code, "evidence": deepcopy(evidence)})

    def unavailable(name, *, required, reason, raw, integrity_unknown=False):
        receipts[name] = {"status": "unknown", "required": required, "reason": reason,
            "raw_payload_sha256": _sha(raw), "quality_verdict_available": False,
            "market_acceptance_claimed": False, "financial_clearance_asserted": False}
        if integrity_unknown:
            receipts[name]["source_integrity_verified"] = False
        (unknown if required or integrity_unknown else optional_unknown).append(f"{name}:{reason}")

    def judge_unavailable(value):
        from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult, JUDGE_INFRASTRUCTURE_FAILURE_CODES
        typed = LLMQualityJudgeResult.model_validate(value)
        if typed.infrastructure_failure and any(
                issue.code.strip().upper() not in JUDGE_INFRASTRUCTURE_FAILURE_CODES
                for issue in typed.blocking_issues):
            raise ValueError("mixed_unavailable_and_quality_claims_unverified")
        return typed.infrastructure_failure

    async def check(name, operation):
        try:
            await operation()
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, ImportError) as exc:
            unknown.append(f"{name}:{type(exc).__name__}:{exc}")

    # Retain the caller's source bytes; refresh must never discard local edits.
    protected = (project, chapter, draft, report, quality)
    if any(obj in session.dirty for obj in protected):
        unknown.append("binding:uncommitted_source_or_review")
        return result
    try:
        current = list(await session.scalars(select(db.ChapterDraftVersionModel).where(
            db.ChapterDraftVersionModel.project_id == project.id,
            db.ChapterDraftVersionModel.chapter_id == chapter.id,
            db.ChapterDraftVersionModel.is_current.is_(True),
        ).execution_options(populate_existing=True).with_for_update()))
        workflow = await session.get(db.WorkflowRunModel, workflow_id)
        if (len(current) != 1 or current[0].id != draft.id or chapter.project_id != project.id
                or draft.project_id != project.id or draft.chapter_id != chapter.id
                or workflow is None or workflow.project_id != project.id
                or workflow.scope_type != "chapter" or workflow.scope_id != chapter.id
                or workflow.workflow_type != pipelines.WORKFLOW_TYPE_CHAPTER_PIPELINE):
            raise ValueError("current_draft_or_workflow_scope_mismatch")
        for obj in (project, chapter, report, quality):
            await session.refresh(obj, with_for_update=True)
        payload = report.structured_output or {}
        if (report.project_id != project.id or report.target_type != "chapter" or report.target_id != chapter.id
                or quality.project_id != project.id or quality.target_type != "chapter"
                or quality.target_id != chapter.id or quality.review_report_id != report.id
                or quality.chapter_draft_version_id != draft.id or quality.is_current is not True
                or pipelines._chapter_review_body_binding_code(report, draft)):
            raise ValueError("review_score_body_binding_mismatch")
        score_fields = {"score_goal": "goal", "score_conflict": "coverage", "score_emotion": "coherence",
                        "score_style": "style", "score_overall": "overall"}
        if (not quality.judge_key or any(getattr(quality, k) is None or
                                      Decimal(str(getattr(quality, k))) != Decimal(str((payload.get("scores") or {}).get(v)))
                                      for k, v in score_fields.items())):
            raise ValueError("raw_quality_score_changed")
        content = draft.content_md or ""
        body_hash = hashlib.sha256(content.encode()).hexdigest()
        policy = pipelines.chapter_review_recovery_source_hash()
        semantic = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
        candidates = []
        for cycle in ((chapter.metadata_json or {}).get("chapter_critic_cycles") or {}).values():
            if not isinstance(cycle, Mapping):
                continue
            if (cycle.get("state") == "completed" and cycle.get("source_draft_id") == str(draft.id)
                    and cycle.get("source_body_hash") == reviews.chapter_review_body_fingerprint(content)["digest"]
                    and cycle.get("policy_source_hash") == policy
                    and cycle.get("semantic_dependencies_sha256") == semantic
                    and not cycle.get("semantic_dependency_change_detected")):
                for attempt in cycle.get("attempts", []):
                    if (attempt.get("state") == "completed" and attempt.get("outcome") == "available"
                            and attempt.get("review_report_id") == str(report.id)
                            and attempt.get("quality_score_id") == str(quality.id)):
                        candidates.append((cycle, attempt))
        if len(candidates) != 1:
            raise ValueError("current_available_review_cycle_unverified")
        cycle, attempt = candidates[0]
        original_owner = await session.get(db.WorkflowRunModel, UUID(cycle["review_workflow_run_id"]))
        if (original_owner is None or original_owner.project_id != project.id
                or original_owner.scope_type != "chapter" or original_owner.scope_id != chapter.id
                or attempt.get("draft_id") != str(draft.id)
                or attempt.get("body_fingerprint") != reviews.chapter_review_body_fingerprint(content)):
            raise ValueError("original_review_cycle_owner_or_body_unverified")
        retained, _, _, _ = await pipelines._retained_chapter_critic_result(session, draft, attempt)
        if (retained.verdict != report.verdict or retained.scores.model_dump(mode="json") != payload.get("scores")
                or pipelines._chapter_critic_evidence_unknown(report)):
            raise ValueError("review_cycle_domain_or_availability_changed")
        result["binding"] = {"project_id": str(project.id), "chapter_id": str(chapter.id),
            "draft_id": str(draft.id), "body_sha256": body_hash, "review_report_id": str(report.id),
            "quality_score_id": str(quality.id), "workflow_id": str(workflow_id),
            "cycle_id": cycle["cycle_id"], "policy_source_hash": policy,
            "semantic_dependencies_sha256": semantic, "original_report_sha256": _sha(payload)}
        cfg = get_quality_gates_config()
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, InvalidOperation) as exc:
        unknown.append(f"binding:{type(exc).__name__}:{exc}")
        return result

    async def primary():
        from bestseller.services.primary_review_submission import parse_primary_review_submission
        from bestseller.services.critic_evidence_gate import validate_critic_commentary

        run = await session.get(db.LlmRunModel, report.llm_run_id) if report.llm_run_id else None
        if (run is None or run.project_id != project.id or run.prompt_template != "chapter_review"
                or str(run.workflow_run_id) != cycle.get("review_workflow_run_id")
                or (run.metadata_json or {}).get("primary_review_body_sha256") != body_hash):
            raise ValueError("primary_paid_current_source_unverified")
        paid = await _physical_return(session, run)
        if not reviews._is_real_chapter_review_completion(paid, allow_submission=True):
            raise ValueError("primary_provider_completion_unverified")
        text, proof, errors = parse_primary_review_submission(paid, content)
        evidence = None if errors else validate_critic_commentary(
            text, chapter_text=content, citation_policy=proof["citation_identity_policy"])
        stored = payload.get("primary_submission_receipt") or {}
        if errors or evidence is None or not evidence.passed or stored != proof:
            raise ValueError("primary_submission_or_citations_changed")
        receipts["primary"] = {"status": "verified", "llm_run_id": str(run.id),
            "submission_sha256": _sha(proof), "body_sha256": body_hash,
            "verdict": proof["parsed_payload"]["verdict"]}
        if proof["parsed_payload"]["verdict"] != "pass":
            raise ValueError("primary_rewrite_not_typed_for_soft_debt")

    await check("primary", primary)

    async def body_gate():
        nonlocal body_gate_receipt
        enabled = cfg.l6_enabled and (cfg.l4.enabled or cfg.l5.enabled)
        if not enabled:
            receipts["body_gate"] = {"status": "not_applicable", "reason": "actual_gate_config_disabled",
                "configuration_sha256": _sha(asdict(cfg)), "body_sha256": body_hash}
            return
        if not project.invariants_json:
            raise ValueError("enabled_gate_missing_invariants")
        if body_gate_receipt is None:
            if cfg.phase_c.enabled:
                raise ValueError("current_call_gate_receipt_required_no_new_override_contracts")
            body_gate_receipt = await reviews._evaluate_rewrite_quality_gate(
                session=session, project=project, chapter_number=chapter.chapter_number, content=content)
        gate = body_gate_receipt
        if (not isinstance(gate, reviews.RewriteQualityGateReceipt) or gate.outcome not in {"ok", "blocked"}
                or not isinstance(gate.report, db.ChapterQualityReportModel)
                or gate.report.id is None or gate.report.chapter_id != chapter.id
                or not report_grades_text(gate.report.report_json, content)
                or gate.report.report_json.get("rewrite_evaluation_id") != gate.evaluation_id
                or gate.graded != gate.report.report_json.get("graded")):
            raise ValueError("current_body_gate_evaluation_unavailable")
        expected_report = deepcopy(gate.report.report_json)
        with session.no_autoflush:
            persisted = await session.scalar(select(db.ChapterQualityReportModel).where(
                db.ChapterQualityReportModel.id == gate.report.id,
                db.ChapterQualityReportModel.chapter_id == chapter.id,
            ).execution_options(populate_existing=True).with_for_update())
        if persisted is None or persisted.report_json != expected_report:
            raise ValueError("body_gate_persisted_report_unverified")
        codes = gate.report.report_json.get("blocking_codes")
        if not isinstance(codes, list) or (gate.outcome == "blocked") != bool(codes):
            raise ValueError("body_gate_action_receipt_inconsistent")
        receipts["body_gate"] = {"status": "verified", "body_sha256": body_hash,
            "evaluation_id": gate.evaluation_id, "report_id": str(gate.report_id),
            "graded": deepcopy(gate.graded), "outcome": gate.outcome, "blocking_codes": list(codes)}
        for code in codes:
            blocked("body_gate", code)

    await check("body_gate", body_gate)

    scenes = list(await session.scalars(select(db.SceneCardModel).where(
        db.SceneCardModel.project_id == project.id, db.SceneCardModel.chapter_id == chapter.id)))

    async def deterministic():
        from bestseller.services.rewrite_candidate_evidence import blocking_deterministic_violations

        audit, band = reviews._audit_chapter_rewrite_candidate(
            project=project, chapter=chapter, scenes=scenes, content_md=content, settings=settings)
        if audit is None or type(audit.passed) is not bool or (not audit.passed and not audit.findings):
            raise ValueError("deterministic_audit_incomplete")
        hard = blocking_deterministic_violations(audit, band=band,
            word_count=drafts.count_words(content),
            chapter_number=chapter.chapter_number, config=cfg.l6_gate,
            translate=reviews._deterministic_rewrite_violations)
        receipts["deterministic"] = {"status": "verified", "body_sha256": body_hash,
            "report": audit.to_dict(), "effective_blockers": deepcopy(list(hard)),
            "configuration_sha256": _sha(asdict(cfg))}
        for issue in hard:
            blocked("deterministic", issue["code"], issue)

    await check("deterministic", deterministic)

    async def dedup():
        findings = await drafts._collect_post_assembly_duplicate_findings(
            session, project=project, chapter=chapter, content_md=content)
        receipts["dedup"] = {"status": "verified", "body_sha256": body_hash,
            "findings": [asdict(f) for f in findings]}
        for finding in findings:
            if finding.source == "post_assembly_duplicate_gate":
                blocked("dedup", finding.code, asdict(finding))
            else:
                from bestseller.services.quality_contract_registry import get_quality_contract
                from bestseller.services.write_gate import resolve_mode
                registered = get_quality_contract(finding.code)
                if registered is not None and resolve_mode(finding.code, cfg.l6_gate,
                        chapter_no=chapter.chapter_number) == "block":
                    blocked("dedup", finding.code, asdict(finding))

    await check("dedup", dedup)

    async def literals():
        from bestseller.services.chapter_predraft_quality_gate import _check_declared_constraint

        creation = (project.metadata_json or {}).get("creation_intent_contract") or {}
        sources = creation.get("field_sources") or {}
        clauses = []
        if sources.get("hard_constraints") in {"explicit", "user_explicit"}:
            clauses.extend(creation.get("hard_constraints") or [])
        if sources.get("hook_spec") == "explicit":
            clauses.extend((creation.get("hook_spec") or {}).get("hard_constraints") or [])
        applicable, findings = [], []
        for clause in clauses:
            if not isinstance(clause, Mapping):
                raise ValueError("literal_hard_clause_malformed")
            if clause.get("hard") is not True or clause.get("approved") is False or clause.get("status") in {"draft", "revoked", "superseded"}:
                continue
            scope = clause.get("scope") or {}
            if (not isinstance(scope, Mapping) or set(scope) - {"chapter_numbers"}
                    or ("chapter_numbers" in scope and (
                        not isinstance(scope["chapter_numbers"], list)
                        or any(type(n) is not int for n in scope["chapter_numbers"])) )):
                raise ValueError("literal_hard_clause_scope_unknown")
            if "chapter_numbers" in scope and chapter.chapter_number not in scope["chapter_numbers"]:
                continue
            applicable.append(deepcopy(dict(clause)))
            _check_declared_constraint(findings, {"draft": {"content_md": content},
                "content_md": content, "chapter": {"chapter_number": chapter.chapter_number}},
                clause, source_path="creation_intent_contract.literal_hard_constraints")
        if any(f.code == "PREDRAFT_CONSTRAINT_UNKNOWN" for f in findings):
            raise ValueError("applicable_literal_hard_clause_not_executable")
        receipts["explicit_plan"] = {"status": "verified" if applicable else "not_applicable",
            "reason": "actual_canonical_literal_inventory", "inventory_sha256": _sha(creation),
            "applicable_clauses": applicable, "body_sha256": body_hash,
            "not_a_complete_semantic_plan_verifier": True}
        for finding in findings:
            blocked("explicit_plan", finding.code, finding.to_dict() if hasattr(finding, "to_dict") else asdict(finding))

    await check("explicit_plan", literals)

    async def identity():
        from bestseller.services.identity_guard import load_identity_registry, validate_scene_text_identity
        registry = await load_identity_registry(session, project.id)
        findings = validate_scene_text_identity(content, registry,
            language=project.language or "zh-CN", chapter_number=chapter.chapter_number)
        receipts["identity"] = {"status": "verified", "body_sha256": body_hash,
            "registry_sha256": _sha([asdict(row) for row in registry]),
            "findings": [asdict(row) for row in findings]}
        for finding in findings:
            if finding.severity == "critical":
                blocked("identity", finding.violation_type, asdict(finding))

    await check("identity", identity)

    async def bible():
        if not cfg.l2.enabled:
            receipts["bible"] = {"status": "not_applicable", "reason": "actual_l2_config_disabled"}
            return
        from bestseller.services.bible_gate import validate_chapter_against_bible
        checked = await validate_chapter_against_bible(session, project_id=project.id,
            chapter_number=chapter.chapter_number, only_enforce_from_chapter=cfg.l2.only_enforce_from_chapter,
            language=project.language)
        receipts["bible"] = {"status": "verified", "body_sha256": body_hash,
            "check_scope": "existing_snapshot_stance_checks_not_full_semantic_bible_proof",
            "checks_run": checked.checks_run, "violations": [asdict(v) for v in checked.violations]}
        for finding in checked.violations:
            blocked("bible", getattr(finding, "check_type", "BIBLE_CONFLICT"), asdict(finding))

    await check("bible", bible)

    async def source_guards():
        effect = drafts.audit_persisted_story_effect_realization(project, chapter, content)
        if effect.get("selected_effects") and effect.get("passed") is not True:
            blocked("selected_effects", "STORY_EFFECT_REALIZATION_MISSING", effect)
        source_ok, mode = await pipelines._chapter_source_mode_is_promotable(session, chapter_draft=draft)
        if not source_ok:
            blocked("scene_source", mode)
        decision = await pipelines.resolve_story_engine_rollout_decision_from_db(
            session, project, settings, chapter_number=chapter.chapter_number)
        engine = decision.effective_mode
        if engine in {"canary", "canonical"}:
            core = (chapter.metadata_json or {}).get("story_engine_projection")
            observation = (draft.promotion_metadata or {}).get("story_engine_observation")
            if not isinstance(core, Mapping) or core.get("can_drive_generation") is not True or not isinstance(observation, Mapping):
                raise ValueError("applicable_story_engine_observation_required_no_dispatch")
            assessed = pipelines.review_story_engine_transition(creative_core=core, observation=observation,
                draft_content_md=content, project_id=project.id, chapter_id=chapter.id,
                draft_version_id=draft.id, workflow_run_id=workflow_id)
            if assessed.verdict is not pipelines.StoryEngineReceiptVerdict.MATCHED:
                for code in assessed.blocking_codes:
                    blocked("story_engine", code)
        receipts["source_guards"] = {"status": "verified", "body_sha256": body_hash,
            "selected_effects": deepcopy(effect), "source_mode": mode, "story_engine_mode": engine,
            "normal_promotion_guards_still_required": True}

    await check("source_guards", source_guards)

    async def commercial():
        from bestseller.services import commercial_judge_recovery as recovery
        from bestseller.services.chapter_llm_quality_judge import _commercial_effective_critic_configuration
        from bestseller.services.commercial_action_projection import (
            _decode, _sha as source_sha, _source_catalog, project_commercial_actions,
        )
        from bestseller.services.commercial_source_evidence import CommercialSources

        summary = payload.get("evidence_summary") or {}
        original = summary.get("llm_commercial_judge_original") or summary.get("llm_commercial_judge")
        enabled = bool(settings.pipeline.enable_chapter_llm_commercial_judge)
        required = enabled and bool(settings.pipeline.chapter_llm_commercial_judge_block_on_failure)
        if original is None:
            if enabled:
                unavailable("commercial", required=required, reason="commercial_review_missing",
                    raw=summary.get("llm_commercial_judge_exception"), integrity_unknown=True)
            else:
                receipts["commercial"] = {"status": "not_applicable", "reason": "actual_setting_disabled"}
            return
        no_verdict = judge_unavailable(original)
        if (not isinstance(commercial_sources, CommercialSources) or not commercial_sources.catalog
                or commercial_sources.receipt.get("body_sha256") != body_hash):
            raise ValueError("exact_current_commercial_sources_required")
        # Validate the actual full source even when there is no projectable
        # quality verdict. Reuse the projection's canonical hash domains.
        source_payload = _decode(commercial_sources.text)
        source_context = _source_catalog(source_payload, commercial_sources)
        source_receipt = commercial_sources.receipt
        if (source_payload.get("chapter_number") != chapter.chapter_number
                or source_payload.get("current_body") != content or source_receipt.get("truncated") is not False
                or hashlib.sha256(commercial_sources.text.encode()).hexdigest() != source_receipt.get("assembled_source_sha256")
                or source_sha(source_payload["previous_chapters"]) != source_receipt.get("previous_sha256")
                or source_sha(source_context) != source_receipt.get("source_sha256")
                or source_sha({k: v for k, v in source_context.items() if k != "source_selection_receipt"})
                    != source_receipt.get("semantic_source_sha256")):
            raise ValueError("commercial_full_source_changed")
        key = recovery._digest({"project_id": str(project.id), "chapter_id": str(chapter.id), "body_sha256": body_hash})
        pointer = ((chapter.metadata_json or {}).get("commercial_component_cohorts") or {}).get(key)
        owner = await session.get(db.WorkflowRunModel, UUID(pointer["workflow_run_id"])) if pointer else None
        journal = ((owner.metadata_json or {}).get("commercial_component_cohorts") or {}).get(key) if owner else None
        if not journal or journal.get("schema") != recovery.COHORT_SCHEMA or owner.project_id != project.id or owner.scope_type != "chapter" or owner.scope_id != chapter.id:
            raise ValueError("commercial_journal_scope_unverified")
        binding = journal["binding"]
        if type(binding.get("samples")) is not int or binding["samples"] < 1:
            raise ValueError("commercial_cohort_samples_invalid")
        critic = await _commercial_effective_critic_configuration(session, settings, project.id)
        alias_view = await recovery._verified_source_alias_cohort_view(
            session, settings, journal=journal, sources=commercial_sources,
            project_id=project.id, scope=binding["scope_id"], expected_model=critic["model"],
        )
        if alias_view is not None:
            journal = alias_view
        slots, aggregate = journal["slots"], journal["aggregate"]
        if (pointer["binding_sha256"] != recovery._digest(binding)
                or binding.get("project_id") != str(project.id) or binding.get("chapter_id") != str(chapter.id)
                or binding.get("draft_id") != str(draft.id)
                or binding.get("effective_critic_sha256") != recovery._digest(critic)
                or binding.get("source_binding") != {k: commercial_sources.receipt[k] for k in recovery._BINDING_KEYS}
                or aggregate.get("slots_sha256") != recovery._digest({s: e["report"] for s, e in slots.items()})
                or _sha(aggregate.get("report")) != _sha(original)
                or set(slots) != {f"chapter-quality.independent-v2:{i}" for i in range(binding["samples"])}):
            raise ValueError("commercial_closed_cohort_changed")
        factual_actions, unavailable_slots = [], []
        for slot, entry in slots.items():
            if entry.get("state") != "completed":
                raise ValueError("commercial_slot_incomplete")
            run = await session.get(db.LlmRunModel, UUID(entry["report"]["llm_run_id"]))
            if run is None:
                raise ValueError("commercial_physical_run_missing")
            verified, envelope, _ = await recovery._verified_paid_slot(session, report=entry["report"], sources=commercial_sources,
                contract=binding["evaluation_contract"], project_id=project.id, scope=binding["scope_id"],
                slot=slot, expected_model=critic["model"], settings=settings)
            if no_verdict:
                paid = await _physical_return(session, run)
                # Only a positively retained, empty content-filter response is
                # classified here. Generic unavailable/parse/source errors,
                # partial JSON, refusals in prose, and mixed quality claims
                # cannot acquire an optional availability waiver.
                if (not verified.infrastructure_failure or verified.passed or verified.overall_score != 0
                        or verified.dimension_scores or verified.audit_issues
                        or paid.finish_reason != "content_filter" or paid.content != "" or paid.tool_calls
                        or (paid.raw_message is not None and paid.raw_message != {"role": "assistant", "content": ""})):
                    raise ValueError("commercial_availability_unclassified")
                unavailable_slots.append({"sample_slot": slot, "llm_run_id": str(run.id),
                    "raw_report_sha256": envelope["raw_report_sha256"],
                    "finish_reason": paid.finish_reason})
            # The scalar representative may omit a non-critical finding from
            # another valid draw. Inspect all physically verified typed wires;
            # an invalid draw abstains under the existing quorum policy.
            if not verified.infrastructure_failure:
                from bestseller.services.commercial_review_wire import parse_commercial_wire
                typed, _, errors = parse_commercial_wire(envelope["raw_report"], commercial_sources)
                if errors:
                    raise ValueError("commercial_verified_wire_changed")
                for issue in typed["blocking_issues"]:
                    if issue.get("assessment_kind") in {"prior_fact_conflict", "current_fact_conflict", "constraint_violation"}:
                        factual_actions.append({"sample_slot": slot, "issue": deepcopy(issue),
                            "raw_report_sha256": envelope["raw_report_sha256"]})
        if no_verdict:
            from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
            typed = LLMQualityJudgeResult.model_validate(original)
            raw_aggregate = _decode(typed.raw_excerpt)
            expected_samples = [{"sample_slot": slot, "valid": False, "report": entry["report"]}
                for slot, entry in sorted(slots.items(), key=lambda item: int(item[0].rsplit(":", 1)[1]))]
            if (typed.passed or typed.overall_score != 0 or typed.dimension_scores or typed.audit_issues
                    or raw_aggregate.get("schema_version") != "chapter-quality-aggregation.v1"
                    or raw_aggregate.get("chapter_number") != chapter.chapter_number
                    or raw_aggregate.get("status") != "insufficient_valid_samples"
                    or raw_aggregate.get("requested_samples") != binding["samples"]
                    or raw_aggregate.get("required_valid_samples") != binding["samples"] // 2 + 1
                    or raw_aggregate.get("valid_samples") != 0
                    or _sha(raw_aggregate.get("samples")) != _sha(expected_samples)):
                raise ValueError("commercial_unavailable_aggregate_unverified")
            unavailable("commercial", required=required, reason="verified_provider_content_filter", raw=original)
            receipts["commercial"].update({"source_integrity_verified": True, "body_sha256": body_hash,
                "cohort_owner_id": str(owner.id), "cohort_key": key,
                "verified_unavailable_slots": unavailable_slots, "quorum_policy_unchanged": True})
            return
        view, proof = project_commercial_actions(original, sources=commercial_sources,
            gate_config=cfg.l6_gate, chapter_number=chapter.chapter_number)
        if view is None:
            raise ValueError("commercial_typed_projection_unverified")
        for action in factual_actions:
            blocked("commercial", action["issue"]["code"], action)
        for issue in proof["issue_actions"]:
            factual = issue["assessment_kind"] in {"prior_fact_conflict", "current_fact_conflict", "constraint_violation"}
            hard = (factual and any(r.get("mapped_group") == "blocking_issues" for r in issue["lineage"])) or (
                issue["assessment_kind"] == "narrative_quality" and issue["effective_action"] == "block")
            if hard:
                blocked("commercial", issue["code"], issue)
        receipts["commercial"] = {"status": "verified", "body_sha256": body_hash,
            "required": required,
            "cohort_owner_id": str(owner.id), "cohort_key": key, "projection": proof,
            "verified_factual_actions": factual_actions,
            "quorum_policy_unchanged": True, "raw_scalar_pass": view["pass"]}

    await check("commercial", commercial)

    async def window_actions():
        from bestseller.services.quality_contract_registry import get_quality_contract
        from bestseller.services.write_gate import resolve_mode
        window = (payload.get("evidence_summary") or {}).get("llm_window_quality_judge")
        enabled = bool(settings.pipeline.enable_chapter_window_llm_judge)
        required = bool(enabled
                        and settings.pipeline.chapter_window_llm_judge_block_on_failure)
        if window is None and enabled:
            previous = await reviews._recent_chapter_window_payload(session, project=project,
                chapter=chapter, draft=draft, window_size=settings.pipeline.chapter_window_llm_judge_size)
            if len(previous) >= settings.pipeline.chapter_window_llm_judge_min_chapters:
                unavailable("window", required=required, reason="applicable_window_review_missing", raw=None)
                return
        if window is not None and judge_unavailable(window):
            unavailable("window", required=required, reason="window_verdict_unavailable", raw=window)
            return
        receipts["window"] = {"status": "audit_only" if window else "not_applicable",
            "required": required,
            "reason": "model_severity_has_no_source_hard_authority", "raw_payload_sha256": _sha(window),
            "body_sha256": body_hash, "server_actions": []}
        if window is None or not required:
            return
        # Legacy window has no factual/RULE wire. Only registered server craft
        # actions already evidenced by this body's real gate can be mandatory.
        for issue in window.get("blocking_issues", []):
            registered = get_quality_contract(issue.get("code"))
            if (registered is None or registered.domain not in {"prose", "retention", "ending", "pov", "dialogue", "pacing"}
                    or resolve_mode(issue["code"], cfg.l6_gate, chapter_no=chapter.chapter_number) != "block"):
                continue
            if issue["code"] not in receipts.get("body_gate", {}).get("blocking_codes", []):
                raise ValueError("registered_window_action_requires_current_server_detector_evidence")
            receipts["window"]["server_actions"].append(issue["code"])
            blocked("window", issue["code"], issue)

    await check("window", window_actions)

    # Check freshness again after all awaited work. No pointer/report may drift.
    fresh = list(await session.scalars(select(db.ChapterDraftVersionModel).where(
        db.ChapterDraftVersionModel.chapter_id == chapter.id,
        db.ChapterDraftVersionModel.is_current.is_(True),
    ).execution_options(populate_existing=True)))
    if (len(fresh) != 1 or fresh[0].id != draft.id
            or hashlib.sha256((fresh[0].content_md or "").encode()).hexdigest() != body_hash
            or pipelines.chapter_review_recovery_source_hash() != policy
            or await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter) != semantic):
        unknown.append("binding:source_or_policy_changed_during_admission")
    result["draft_eligible"] = not blockers and not unknown
    core = (quality.score_goal, quality.score_conflict, quality.score_emotion, quality.score_style)
    result["quality_passed"] = bool(result["draft_eligible"] and report.verdict == "pass"
        and all(v is not None and float(v) >= .80 for v in core)
        and sum(map(float, core)) / len(core) >= .85)
    result["receipt_sha256"] = _sha({k: v for k, v in result.items() if k != "receipt_sha256"})
    return result
