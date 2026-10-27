"""Native adapter. Connections are created only by an explicit CLI/API invocation.

No init_db/DDL, no SDK, no budget update, no title-specific shared-source change.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from uuid import UUID

import title_operator as op


def native_messages(*args, **kwargs):
    from bestseller.services.platform_title_workflow import build_story_grounded_title_revision_messages
    return build_story_grounded_title_revision_messages(*args, **kwargs)


class NativeRuntime:
    def __init__(self):
        self.engines = {}
        self.settings = None
        self._role = None

    async def __aenter__(self):
        from bestseller.settings import apply_runtime_llm_profile, load_settings
        self.settings = apply_runtime_llm_profile(load_settings())
        return self

    async def __aexit__(self, *_exc):
        for engine in self.engines.values():
            await engine.dispose()

    def session(self, *, write=False):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        if write not in self.engines:
            options = {"statement_timeout": "30000", "lock_timeout": "10000"}
            if not write:
                options["default_transaction_read_only"] = "on"
            self.engines[write] = create_async_engine(
                self.settings.database.url,
                connect_args={"server_settings": options, "command_timeout": 40},
            )
        return async_sessionmaker(self.engines[write], expire_on_commit=False, autoflush=False)()

    async def _readonly(self, session):
        from sqlalchemy import text
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        op.need(await session.scalar(text("SHOW transaction_read_only")) == "on", "readonly_required")

    async def _recovery_evidence(self, session, manifest, chapters, drafts, workflows, reports, *, lock=False):
        """Read source-bound evidence, without recovery writes or model calls.

        Called after the strict adopted-assembly reader and before budget locks.
        No cached active/completed flag substitutes for a native KG readback.
        """
        from sqlalchemy import select
        from bestseller.infra.db.models import QualityScoreModel, LlmRunModel
        from bestseller.services.adopted_knowledge_resume import read_adopted_knowledge
        from bestseller.services.reviews import chapter_review_body_fingerprint

        def text_id(value):
            return str(value) if value is not None else None

        def fields(row, names):
            return {k: text_id(getattr(row, k)) for k in names}

        # No recovery history means no exception is possible; old behavior stays.
        if not any((c.metadata_json or {}).get("chapter_review_recovery_cycles")
                   or (c.metadata_json or {}).get("chapter_review_active_cycle") for c in chapters):
            return None
        source_ids = {s["chapter_id"] for s in manifest["sources"]}
        relevant = [r for r in reports if r.target_type == "chapter" and str(r.target_id) in source_ids]
        q_query = select(QualityScoreModel).where(
            QualityScoreModel.project_id == UUID(op.PROJECT_ID),
            QualityScoreModel.review_report_id.in_([r.id for r in relevant]),
        ).order_by(QualityScoreModel.id).execution_options(populate_existing=True)
        qualities = list(await session.scalars(q_query.with_for_update() if lock else q_query))
        l_query = select(LlmRunModel).where(
            LlmRunModel.project_id == UUID(op.PROJECT_ID),
            LlmRunModel.id.in_([r.llm_run_id for r in relevant if r.llm_run_id]),
        ).order_by(LlmRunModel.id).execution_options(populate_existing=True)
        runs = list(await session.scalars(l_query.with_for_update() if lock else l_query))
        draft_map = {str(d.id): d for d in drafts}
        knowledge = []
        for source in manifest["sources"]:
            draft = draft_map[source["draft_id"]]
            lineage, status = await read_adopted_knowledge(
                session, project_id=UUID(op.PROJECT_ID), chapter_id=UUID(source["chapter_id"]),
                source_chapter_draft_version_id=draft.id, chapter_md=draft.content_md,
                expected_source=source["lineage"], require_bible=True,
            )
            knowledge.append({"id": source["chapter_id"], "draft_id": str(draft.id),
                "raw_body_sha256": op.sha(draft.content_md),
                "reviewed_body_fingerprint": chapter_review_body_fingerprint(draft.content_md),
                "lineage": deepcopy(lineage), "status": deepcopy(status)})
        wf_keys = ("execution_scope_id", "critic_cycle_id", "review_report_id", "quality_score_id",
                   "final_verdict", "requires_human_review", "chapter_critic_evidence_unknown",
                   "actual_adoption_receipt", "review_prerequisite_wait", "review_recovery_policy",
                   "terminal_state_history")
        report_keys = ("draft_id", "reviewed_body_fingerprint", "critic_evidence", "rewrite_instructions")
        return {
            "schema": "title-census-recovery-evidence.v1",
            "workflows": [{**fields(w, ("id", "project_id", "scope_id")),
                "scope_type": w.scope_type, "workflow_type": w.workflow_type,
                "status": w.status, "current_step": w.current_step,
                "metadata": {k: deepcopy(w.metadata_json[k]) for k in wf_keys if k in (w.metadata_json or {})}}
                for w in workflows if w.scope_type == "chapter" and str(w.scope_id) in source_ids],
            "reports": [{**fields(r, ("id", "project_id", "target_id", "llm_run_id")),
                "target_type": r.target_type, "verdict": r.verdict,
                "structured_output": {k: deepcopy(r.structured_output[k]) for k in report_keys
                                      if k in (r.structured_output or {})}} for r in relevant],
            "quality_scores": [{**fields(q, ("id", "project_id", "target_id", "chapter_draft_version_id", "review_report_id")),
                "target_type": q.target_type, "is_current": q.is_current} for q in qualities],
            "llm_runs": [fields(r, ("id", "project_id", "workflow_run_id")) for r in runs],
            "knowledge": knowledge,
        }

    async def _snapshot(self, session, *, lock=False, ignore_workflow=None):
        from sqlalchemy import select
        from bestseller.infra.db.models import (
            BookPackagingContractModel, ChapterDraftVersionModel, ChapterModel,
            ExecutionBudgetModel, PlanningArtifactVersionModel, ProjectModel,
            ReviewReportModel, RewriteTaskModel, WorkflowRunModel,
        )
        from bestseller.domain.book_design_snapshot import BookDesignSnapshot
        from bestseller.services.budget import execution_scope_block_code
        from bestseller.services.fanqie_short_source_export import read_adopted_assembly
        from bestseller.services.reviews import chapter_review_body_fingerprint
        from bestseller.services import llm, model_catalog
        from bestseller.settings import apply_runtime_llm_profile

        project_id = UUID(op.PROJECT_ID)
        # Source reader locks control -> project -> chapters -> drafts -> snapshots.
        # Follow production_control's control/project-before-budget direction;
        # NEVER take the budget first (the rejected R05 ordering).
        assembly = await read_adopted_assembly(session, project_id, lock=lock)
        query = select(ProjectModel).where(ProjectModel.id == project_id).execution_options(populate_existing=True)
        project = await session.scalar(query.with_for_update() if lock else query)
        meta = deepcopy(project.metadata_json or {})
        canonical_query = select(PlanningArtifactVersionModel).where(
            PlanningArtifactVersionModel.id == UUID(meta["creation_canonical_snapshot_id"]),
            PlanningArtifactVersionModel.project_id == project_id,
        )
        canonical = await session.scalar(canonical_query.with_for_update() if lock else canonical_query)
        op.need(canonical is not None and canonical.artifact_type == "conception_snapshot", "canonical_missing")
        design = BookDesignSnapshot.model_validate(meta["book_design_snapshot"])
        design_valid = (design.source_hash == design.canonical_hash() == meta.get("book_design_snapshot_hash")
                        and design.snapshot_id == design.canonical_hash()[:16] == meta.get("book_design_snapshot_id"))
        chapters = list(await session.scalars(select(ChapterModel).where(
            ChapterModel.project_id == project_id).order_by(ChapterModel.chapter_number)))
        drafts = list(await session.scalars(select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.project_id == project_id).order_by(ChapterDraftVersionModel.id)))
        workflow_query = select(WorkflowRunModel).where(WorkflowRunModel.project_id == project_id).order_by(
            WorkflowRunModel.id).execution_options(populate_existing=True)
        workflows = list(await session.scalars(workflow_query.with_for_update() if lock else workflow_query))
        task_query = select(RewriteTaskModel).where(RewriteTaskModel.project_id == project_id).order_by(
            RewriteTaskModel.id).execution_options(populate_existing=True)
        tasks = list(await session.scalars(task_query.with_for_update() if lock else task_query))
        # Review reports have verdict/structured_output, not a status column.
        report_query = select(ReviewReportModel).where(ReviewReportModel.project_id == project_id).order_by(
            ReviewReportModel.id).execution_options(populate_existing=True)
        reports = list(await session.scalars(report_query.with_for_update() if lock else report_query))
        packaging_query = select(BookPackagingContractModel).where(BookPackagingContractModel.project_id == project_id)
        packaging = list(await session.scalars(packaging_query.with_for_update() if lock else packaging_query))
        recovery_evidence = await self._recovery_evidence(
            session, assembly.manifest, chapters, drafts, workflows, reports, lock=lock)
        budget_query = select(ExecutionBudgetModel).where(ExecutionBudgetModel.scope_id == op.SCOPE)
        budget = await session.scalar(budget_query.with_for_update() if lock else budget_query)
        current_settings = apply_runtime_llm_profile(self.settings)
        op.need(not current_settings.llm.mock, "real_editor_configuration_required")
        op.need(current_settings.artifact_store.retain_prompt_payloads is True
                and current_settings.artifact_store.mode == "local", "local_retention_required")
        role = llm._get_role_settings(current_settings, "editor")
        entry = model_catalog.resolve_project_model_entry(meta)
        if meta.get("llm_model_id"):
            op.need(entry is not None, "selected_model_unavailable")
        if entry is not None:
            role = llm._apply_model_override(role, entry)
        elif not meta.get("llm_model_id"):
            role = model_catalog.legacy_project_role(current_settings, project.id, "editor") or role
        op.need(role.n_candidates == 1 and role.max_tokens >= 4096, "editor_configuration_limits")
        self._role = role
        chapter_data = [{"id": str(c.id), "chapter_number": c.chapter_number,
                         "status": c.status, "production_state": c.production_state,
                         "metadata": {k: deepcopy(c.metadata_json[k]) for k in
                                      ("chapter_review_active_cycle", "chapter_critic_cycles", "chapter_review_recovery_cycles", "requires_human_review")
                                      if k in (c.metadata_json or {})}} for c in chapters]
        draft_data = [{"id": str(d.id), "chapter_id": str(d.chapter_id), "version_no": d.version_no,
                       "promotion_state": d.promotion_state, "is_current": d.is_current,
                       "body_sha256": op.sha(d.content_md),
                       "reviewed_body_fingerprint": chapter_review_body_fingerprint(d.content_md)} for d in drafts]
        workflow_data = [{"id": str(w.id), "workflow_type": w.workflow_type, "status": w.status,
                          "current_step": w.current_step, "metadata_sha256": op.fingerprint(w.metadata_json)}
                         for w in workflows if str(w.id) != ignore_workflow]
        task_data = [{"id": str(t.id), "status": t.status, "metadata_sha256": op.fingerprint(t.metadata_json),
                      "trigger_source_id": str(t.trigger_source_id) if t.trigger_source_id else None,
                      "instructions_sha256": op.sha(t.instructions or ""), "attempts": t.attempts,
                      "source_binding": {k: deepcopy(t.metadata_json[k]) for k in (
                          "chapter_id", "draft_id", "parent_draft_id", "parent_draft_hash",
                          "source_chapter_draft_version_id", "source_body_sha256", "assembly_sha256")
                          if k in (t.metadata_json or {})}}
                     for t in tasks]
        report_data = [{"id": str(r.id), "verdict": r.verdict,
                        "structured_output_sha256": op.fingerprint(r.structured_output)} for r in reports]
        writing_market = (meta.get("writing_profile") or {}).get("market") or {}
        block_code = execution_scope_block_code(budget) if budget is not None else "execution_scope_missing"
        native_files = [Path(llm.__file__).with_name(name) for name in (
            "llm.py", "planner.py", "platform_title_workflow.py", "fanqie_short_source_export.py",
            "adopted_chapter_source.py", "adopted_knowledge_resume.py", "budget.py", "production_strategy.py")]
        result = {
            "schema": "title-operator-source.v1", "project_id": str(project.id), "project_slug": project.slug,
            "manifest": assembly.manifest, "full_text": assembly.full_text,
            "knowledge_closed": [s["chapter_number"] for s in assembly.manifest["sources"]],
            "canonical_snapshot": {"id": str(canonical.id), "project_id": str(canonical.project_id),
                                   "artifact_type": canonical.artifact_type, "status": canonical.status,
                                   "content": deepcopy(canonical.content)},
            "book_design_snapshot": deepcopy(meta["book_design_snapshot"]),
            "old_metadata": {k: deepcopy(meta[k]) for k in op.OLD_FIELDS if k in meta},
            "design_hash_verified": design_valid,
            "target_platform": str(writing_market.get("platform_target") or meta.get("platform_target") or ""),
            "packaging_rows": [str(r.id) for r in packaging],
            "active_packaging_contract_id": meta.get("active_packaging_contract_id"),
            "chapters": chapter_data, "drafts": draft_data,
            "recovery_evidence": recovery_evidence,
            "open_workflows": op.active_workflows(workflow_data),
            "open_rewrite_tasks": op.active_rewrites(task_data, draft_data, assembly.manifest),
            "review_fence_sha256": op.fingerprint({"chapters": chapter_data, "drafts": draft_data,
                                                   "workflows": workflow_data, "tasks": task_data,
                                                   "reports": report_data, "recovery_evidence": recovery_evidence}),
            "project_lock_version": project.lock_version,
            "dispatch_admissible": block_code is None,
            "adoption_admissible": block_code in {None, "execution_deadline_exhausted", "execution_budget_exhausted"},
            "native_source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in native_files},
            "model_binding": {"model": role.model, "role_sha256": op.fingerprint(role.model_dump(mode="json")),
                              "production_profile": meta.get("production_profile")},
        }
        return result

    def _finalize_plan(self, snapshot, expected, *, paid=False):
        from bestseller.services import llm
        from bestseller.services.llm_capacity import assert_effective_request_capacity
        from bestseller.services.production_strategy import apply_production_stream_settings
        plan = op.prepare(snapshot, expected, native_messages, paid=paid)
        request = llm.LLMCompletionRequest(**plan["request"])
        role = apply_production_stream_settings(request, self._role,
                                               profile=plan["binding"]["model_binding"]["production_profile"])
        # Native M3.1 editor currently has a 16000-token minimum. Preserve that
        # configured policy, expose it to the witness, keep the REQUEST at 4096.
        effective_cap = llm._effective_request_max_tokens(role, request)
        op.need(4096 <= effective_cap <= role.max_tokens, "effective_output_outside_original_role")
        assert_effective_request_capacity(llm._effective_input_payload(request, role))
        plan["effective_max_tokens"] = effective_cap
        plan["effective_input_hash"] = llm._effective_input_hash(request, role)
        plan["request"]["metadata"]["prepared_effective_input"] = {
            "schema": "effective-input.v1", "hash": plan["effective_input_hash"]}
        plan["plan_sha256"] = op.fingerprint({k: v for k, v in plan.items() if k != "plan_sha256"})
        return plan

    async def preflight(self, expected, *, ignore_workflow=None, require_unspent=False, paid=False):
        async with self.session() as session:
            await self._readonly(session)
            snapshot = await self._snapshot(session, ignore_workflow=ignore_workflow)
            plan = self._finalize_plan(snapshot, expected, paid=paid)
            if require_unspent:
                ledger = await self._ledger(session, plan)
                op.need(ledger["root"] is None and ledger["attempts"] == [], "operation_exists_use_paid_audit_only")
            await session.rollback()
            return plan

    async def assert_fresh(self, plan, *, paid=False):
        current = await self.preflight(plan["binding"]["manifest"],
                                       ignore_workflow=plan["workflow_run_id"], paid=paid)
        op.need(current == plan, "source_or_configuration_changed")

    async def assert_no_existing_operation(self, plan):
        ledger = await self.ledger(plan)
        op.need(ledger["root"] is None and ledger["attempts"] == [], "operation_exists_use_paid_audit_only")

    async def _ledger(self, session, plan, *, lock=False):
        from sqlalchemy import select
        from bestseller.infra.db.models import ExecutionAttemptModel, ExecutionOperationModel
        root_query = select(ExecutionOperationModel).where(ExecutionOperationModel.key == plan["operation_key"])
        root = await session.scalar(root_query.with_for_update() if lock else root_query)
        attempt_query = select(ExecutionAttemptModel).where(
            ExecutionAttemptModel.operation_key == plan["operation_key"]).order_by(ExecutionAttemptModel.id)
        attempts = list(await session.scalars(attempt_query.with_for_update() if lock else attempt_query))
        transport_results = []
        for attempt in attempts:
            key = op.sha("transport-result.v1\n" + op.SCOPE + "\n" + attempt.id)
            row = await session.get(ExecutionOperationModel, key)
            if row is not None:
                op.need(row.status == "transport_result", "unexpected_transport_record")
                record = deepcopy(row.result_json)
                if isinstance(record, dict) and isinstance(record.get("result"), dict):
                    record["result"] = op.safe_completion(record["result"])
                transport_results.append(record)
        return {
            "root": None if root is None else {
                "key": root.key, "scope_id": root.scope_id, "input_hash": root.input_hash,
                "status": root.status, "result_json": (op.safe_completion(root.result_json)
                    if isinstance(root.result_json, dict) else None)},
            "attempts": [{"id": a.id, "operation_key": a.operation_key, "scope_id": a.scope_id,
                          "model_name": a.model_name, "input_hash": a.input_hash,
                          "actual_tokens": a.actual_tokens, "status": a.status} for a in attempts],
            "transports": transport_results,
        }

    async def ledger(self, plan):
        async with self.session() as session:
            await self._readonly(session)
            result = await self._ledger(session, plan)
            await session.rollback()
            return result

    def read_retained(self, completion):
        root = Path(self.settings.artifact_store.local_dir).expanduser().resolve()
        receipt = completion.get("metadata", {}).get("prompt_payload_receipt", {})
        docs = {}
        for kind in ("request", "response"):
            expected = f"runtime/llm_payloads/{UUID(completion['llm_run_id'])}.{kind}.json"
            op.need(receipt.get(kind + "_ref") == expected, "retained_reference")
            path = root / expected
            op.need(not path.is_symlink() and path.resolve().is_relative_to(root)
                    and path.is_file() and path.stat().st_size <= 1_000_000, "retained_file_missing_or_size")
            docs[kind] = op.read_json(path)
        return docs

    async def dispatch(self, plan, witness, retain_return):
        from bestseller.infra.db.models import WorkflowRunModel
        from bestseller.services import llm
        from bestseller.services.budget import bind_execution_scope
        # Configure one attempt on a local settings copy. Never change files or
        # models. Runtime profile application preserves this retry restriction.
        settings = self.settings.model_copy(deep=True)
        settings.llm.retry = settings.llm.retry.model_copy(update={
            "max_attempts": 1, "rate_limit_max_attempts": 1,
            "rate_limit_fallback_enabled": False, "max_attempts_per_class": {"default": 1, "repair": 1}})
        async with self.session(write=True) as session:
            op.need(await session.get(WorkflowRunModel, UUID(plan["workflow_run_id"])) is None,
                    "workflow_exists_no_resend")
            session.add(WorkflowRunModel(
                id=UUID(plan["workflow_run_id"]), project_id=UUID(op.PROJECT_ID),
                workflow_type="title_repair_operator", scope_type="project", scope_id=UUID(op.PROJECT_ID),
                status="running", current_step="title_story_grounded_revision", requested_by=witness["actor"][:64],
                metadata_json={"schema": op.SCHEMA, "plan_sha256": plan["plan_sha256"],
                               "execution_scope_id": op.SCOPE, "original_task_id": op.TASK_ID,
                               "witness": deepcopy(witness), "literal_source": plan["literal_source"]},
            ))
            await session.commit()  # Durable workflow before provider; no source locks across network.
        await self.assert_fresh(plan)
        async with self.session(write=True) as session:
            try:
                request = llm.LLMCompletionRequest(**plan["request"])
                with bind_execution_scope(op.SCOPE), llm.bind_conception_model(None, task_id=op.TASK_ID), \
                        llm.bind_project_prompt_audit(UUID(op.PROJECT_ID), reason=op.SCHEMA):
                    completion = await llm.complete_text(session, settings, request)  # exactly once
                    safe = op.safe_completion(completion)
                    try:
                        retained = self.read_retained(safe)
                    except (ValueError, OSError, KeyError, TypeError):
                        retained = {}  # Retain paid return before rejecting missing physical custody.
                    # Retain before bind_conception_model's exit stop check too.
                    retain_return(safe, retained, await self.ledger(plan))
                await session.commit()  # Telemetry only; title adoption is a later transaction.
            except BaseException:
                await session.rollback()
                raise

    @staticmethod
    def finalize(*args, **kwargs):
        from bestseller.services.platform_title_workflow import finalize_revised_title
        return finalize_revised_title(*args, **kwargs)

    @staticmethod
    def full_workflow(*args, **kwargs):
        from bestseller.services.platform_title_workflow import build_platform_title_workflow
        return build_platform_title_workflow(*args, **kwargs)

    async def committed_result(self, plan, audit, selection):
        from bestseller.infra.db.models import WorkflowRunModel
        async with self.session() as session:
            await self._readonly(session)
            workflow = await session.get(WorkflowRunModel, UUID(plan["workflow_run_id"]))
            completed = workflow is not None and workflow.status == "completed"
            if completed:
                op.need(workflow.project_id == UUID(op.PROJECT_ID)
                        and workflow.metadata_json.get("plan_sha256") == plan["plan_sha256"],
                        "completed_workflow_conflict")
            await session.rollback()
        # Recover an ambiguous post-commit/readback failure without a second title
        # mutation or step insert. Original paid origin/files are checked by caller.
        return await self.readback(plan, audit, selection) if completed else None

    async def commit_title(self, plan, witness, audit, selection):
        from sqlalchemy import select
        from bestseller.domain.enums import WorkflowStatus
        from bestseller.infra.db.models import LlmRunModel, ProjectModel, WorkflowRunModel
        from bestseller.services.planner import _apply_project_title_workflow_metadata
        from bestseller.services.workflows import create_workflow_step_run
        async with self.session(write=True) as session:
            try:
                snapshot = await self._snapshot(session, lock=True, ignore_workflow=plan["workflow_run_id"])
                op.need(self._finalize_plan(snapshot, plan["binding"]["manifest"], paid=True) == plan,
                        "commit_source_changed")
                ledger = await self._ledger(session, plan, lock=True)
                op.validate_paid(plan, audit["completion"], self.read_retained(audit["completion"]), ledger)
                project = await session.get(ProjectModel, UUID(op.PROJECT_ID))
                workflow = await session.scalar(select(WorkflowRunModel).where(
                    WorkflowRunModel.id == UUID(plan["workflow_run_id"])).with_for_update())
                op.need(workflow is not None and workflow.status == "running"
                        and workflow.metadata_json.get("plan_sha256") == plan["plan_sha256"], "workflow_binding")
                completion = audit["completion"]
                run_id = UUID(completion["llm_run_id"])
                run = await session.get(LlmRunModel, run_id)
                if run is None:
                    # Restore only the already-paid, physically retained telemetry,
                    # with the ORIGINAL UUID/origin. Never invoke complete_text.
                    receipt = completion["metadata"]["prompt_payload_receipt"]
                    run = LlmRunModel(
                        id=run_id, project_id=UUID(op.PROJECT_ID), workflow_run_id=workflow.id,
                        logical_role="editor", provider=completion["provider"], model_name=completion["model_name"],
                        prompt_template="title_story_grounded_revision", prompt_version="1.0",
                        prompt_hash=plan["effective_input_hash"], input_tokens=completion["input_tokens"],
                        output_tokens=completion["output_tokens"], latency_ms=completion["latency_ms"],
                        finish_reason=completion["finish_reason"], request_payload_ref=receipt["request_ref"],
                        response_payload_ref=receipt["response_ref"], metadata_json=deepcopy(completion["metadata"]),
                    )
                    session.add(run)
                op.need(run.project_id == project.id and run.workflow_run_id == workflow.id
                        and run.prompt_hash == plan["effective_input_hash"] and run.logical_role == "editor"
                        and run.prompt_template == "title_story_grounded_revision" and run.prompt_version == "1.0"
                        and run.model_name == completion["model_name"]
                        and run.provider == completion["provider"] and run.finish_reason == completion["finish_reason"]
                        and run.input_tokens == completion["input_tokens"] and run.output_tokens == completion["output_tokens"]
                        and run.request_payload_ref == completion["metadata"]["prompt_payload_receipt"]["request_ref"]
                        and run.response_payload_ref == completion["metadata"]["prompt_payload_receipt"]["response_ref"]
                        and run.metadata_json.get("paid_call_origin") == completion["metadata"]["paid_call_origin"],
                        "telemetry_conflict")
                before = deepcopy(project.metadata_json or {})
                history = deepcopy(before.get("title_revision_history", []))
                op.need(isinstance(history, list), "history_shape")
                history.append({"schema": op.SCHEMA, "workflow_run_id": str(workflow.id),
                                "previous_title": project.title,
                                "previous_native_metadata": {k: deepcopy(before[k]) for k in
                                    ("title_revision_status", "title_workflow_primary", "title_workflow",
                                     "title_candidates", "writing_profile", "title_repair_lineage") if k in before},
                                "audit_sha256": audit["audit_sha256"]})
                _apply_project_title_workflow_metadata(project, metadata=before,
                    title_record=selection["record"], title_workflow=selection["workflow"])
                lineage = {
                    "schema": op.SCHEMA, "project_id": op.PROJECT_ID, "original_task_id": op.TASK_ID,
                    "workflow_run_id": str(workflow.id), "llm_run_id": str(run.id),
                    "previous_title": plan["binding"]["old_title"], "adopted_title": selection["title"],
                    "source_manifest": deepcopy(plan["binding"]["manifest"]),
                    "canonical_id": plan["binding"]["manifest"]["canonical_id"],
                    "canonical_hash": plan["binding"]["manifest"]["canonical_hash"],
                    "book_design_snapshot_hash": before["book_design_snapshot_hash"],
                    "operation_key": plan["operation_key"], "effective_input_hash": plan["effective_input_hash"],
                    "plan_sha256": plan["plan_sha256"], "paid_audit_sha256": audit["audit_sha256"],
                    "literal_offset": selection["literal_offset"], "title_evaluation": selection["title_evaluation"],
                    "packaging_created": False, "historical_promise_fulfillment_granted": False,
                }
                project.title = selection["title"]
                project.metadata_json = {**project.metadata_json, "title_revision_history": history,
                    "title_repair_lineage": lineage,
                    "title_revision_status": {"previous_title": plan["binding"]["old_title"],
                        "adopted_title": selection["title"], "was_revised": True, "llm_run_id": str(run.id),
                        "quality_status": "unchanged_or_revised", "retryable": False, "readability_issue": "",
                        "title_evaluation": selection["title_evaluation"], "native_title_lineage": lineage}}
                op.need(project.lock_version == plan["binding"]["project_lock_version"], "project_version_changed")
                # Old Canonical/design payloads and all unrelated metadata remain untouched.
                for key in ("book_design_snapshot", "book_design_snapshot_hash", "book_design_snapshot_id",
                            "book_design_snapshot_status", "creation_canonical_snapshot_id",
                            "creation_canonical_snapshot_hash"):
                    op.need(project.metadata_json.get(key) == before.get(key), "protected_metadata_changed")
                await create_workflow_step_run(session, workflow_run_id=workflow.id,
                    step_name="story_grounded_title_revision", step_order=1, status=WorkflowStatus.COMPLETED,
                    input_ref={"plan_sha256": plan["plan_sha256"], "source_manifest": plan["binding"]["manifest"],
                               "previous_title_revision_status": before.get("title_revision_status")},
                    output_ref={**lineage, "title_record": selection["record"],
                                "rejected_candidates": selection["rejected_candidates"]})
                workflow.status = "completed"
                workflow.current_step = "title_repair_adopted"
                workflow.metadata_json = {**workflow.metadata_json, "title_repair_lineage": lineage}
                await session.flush()
                await session.commit()
            except BaseException:
                await session.rollback()
                raise
        return await self.readback(plan, audit, selection)

    async def readback(self, plan, audit, selection):
        from sqlalchemy import select
        from bestseller.infra.db.models import BookPackagingContractModel, ProjectModel, WorkflowRunModel
        from bestseller.services.fanqie_short_source_export import read_adopted_assembly
        async with self.session() as session:
            await self._readonly(session)
            project = await session.get(ProjectModel, UUID(op.PROJECT_ID))
            assembly = await read_adopted_assembly(session, project.id)
            expected_after = deepcopy(plan["binding"]["manifest"])
            expected_after["project"]["title"] = selection["title"]
            op.need(assembly.manifest == expected_after, "readback_source_changed")
            metadata = project.metadata_json or {}
            # This is a new-session read of actual immutable authority, not a
            # comparison of our in-memory project object against itself.
            snapshot = await self._snapshot(session, ignore_workflow=plan["workflow_run_id"])
            op.need(snapshot["canonical_snapshot"] == plan["literal_source"]["canonical_snapshot"]
                    and snapshot["book_design_snapshot"] == plan["literal_source"]["book_design_snapshot"]
                    and snapshot["design_hash_verified"] is True, "readback_authority_changed")
            op.need(not op.open_review_reasons(snapshot), "readback_new_review_not_closed")
            op.need(project.title == selection["title"]
                    and metadata["title_revision_status"]["adopted_title"] == selection["title"]
                    and metadata["title_workflow_primary"]["title"] == selection["title"]
                    and metadata["title_workflow"]["recommended_primary_title"]["title"] == selection["title"]
                    and metadata["title_repair_lineage"]["paid_audit_sha256"] == audit["audit_sha256"],
                    "readback_title_projection")
            market = metadata.get("writing_profile", {}).get("market", {})
            op.need(market.get("title_workflow_primary", {}).get("title") == selection["title"]
                    and market.get("title_workflow_summary", {}).get("recommended_primary_title", {}).get("title")
                    == selection["title"], "readback_market_projection")
            old = plan["literal_source"]["old_native_title_metadata"]
            history = metadata.get("title_revision_history", [])
            old_history = old.get("title_revision_history", [])
            op.need(len(history) == len(old_history) + 1 and history[:-1] == old_history
                    and history[-1].get("workflow_run_id") == plan["workflow_run_id"]
                    and history[-1].get("audit_sha256") == audit["audit_sha256"]
                    and history[-1].get("previous_native_metadata") == {k: old[k] for k in (
                        "title_revision_status", "title_workflow_primary", "title_workflow",
                        "title_candidates", "writing_profile", "title_repair_lineage") if k in old},
                    "readback_history_changed")
            op.need(not metadata.get("active_packaging_contract_id")
                    and not list(await session.scalars(select(BookPackagingContractModel.id).where(
                        BookPackagingContractModel.project_id == project.id))), "unexpected_packaging")
            workflow = await session.get(WorkflowRunModel, UUID(plan["workflow_run_id"]))
            op.need(workflow.status == "completed"
                    and workflow.metadata_json.get("literal_source") == plan["literal_source"]
                    and workflow.metadata_json.get("plan_sha256") == plan["plan_sha256"], "readback_workflow")
            result = {"schema": op.SCHEMA, "status": "native_title_workflow_committed",
                      "project_id": op.PROJECT_ID, "workflow_run_id": str(workflow.id),
                      "title_repair_lineage": deepcopy(metadata["title_repair_lineage"]),
                      "source_manifest_after": assembly.manifest, "packaging_created": False,
                      "whole_book_pass_granted": False, "export_authority_granted": False}
            await session.rollback()
            return result
