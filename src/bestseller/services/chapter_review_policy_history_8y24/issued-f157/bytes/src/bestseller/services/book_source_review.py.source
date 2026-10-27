"""Source-bound book review: existing safety parser/physical proof, provisional only."""

from uuid import UUID, uuid5, NAMESPACE_URL
from sqlalchemy import select
from bestseller.infra.db import models as m
from bestseller.services import (
    draft_continuity as dc,
    draft_continuity_applicable_review as applicable,
    llm,
)
from bestseller.services.prompt_source_policy import content_hash

SCHEMA = "book-qualified-source.v1"


async def _verified_replacement_parent(session, *, project, chapter, draft, book, prior, proof):
    previous = await session.get(m.WorkflowRunModel, UUID(prior), populate_existing=True)
    old = (previous.metadata_json or {}).get("proof") if previous else None
    selected = (book.metadata_json.get("selected") or {}).get(str(chapter.chapter_number), {})
    history = [
        h
        for h in book.metadata_json.get("repair_history", [])
        if h.get("old")
        == {
            "chapter_id": str(chapter.id),
            "draft_id": selected.get("draft_id"),
            "body_sha256": selected.get("body_sha256"),
        }
        and h.get("new")
        == {
            "chapter_id": str(chapter.id),
            "draft_id": str(draft.id),
            "body_sha256": proof["body_sha256"],
        }
    ]
    if (
        not old
        or previous.project_id != project.id
        or previous.scope_id != chapter.id
        or previous.workflow_type != "draft_continuity_reference"
        or previous.status != "completed"
        or old.get("schema") != SCHEMA
        or old.get("book_run_id") != str(book.id)
        or selected.get("reference", {}).get("receipt_id") != prior
        or old.get("draft_id") != selected.get("draft_id")
        or old.get("body_sha256") != selected.get("body_sha256")
        or len(history) != 1
    ):
        raise ValueError("book_reference_conflict_requires_reconciliation")
    repair = history[0]
    task = await session.get(m.RewriteTaskModel, UUID(repair["task_id"]), populate_existing=True)
    if (
        task is None
        or task.project_id != project.id
        or task.trigger_source_id != chapter.id
        or task.trigger_type != "book_source_hard_block"
        or task.metadata_json.get("book_run_id") != str(book.id)
        or not repair.get("evidence_sha256")
        or task.metadata_json.get("hard_evidence_sha256") != repair["evidence_sha256"]
        or task.metadata_json.get("book_hard_repair_source") != repair["old"]
        or task.metadata_json.get("book_hard_repair_result") != repair["new"]
    ):
        raise ValueError("book_reference_repair_parent_unverified")
    from bestseller.services.reviews import rewrite_source_binding

    old_draft = await session.get(
        m.ChapterDraftVersionModel, UUID(old["draft_id"]), populate_existing=True
    )
    if (
        old_draft is None
        or old_draft.project_id != project.id
        or old_draft.chapter_id != chapter.id
        or dc.body_hash(old_draft.content_md) != old["body_sha256"]
        or task.status != "completed"
        or any(task.metadata_json.get(k) != v for k, v in rewrite_source_binding(old_draft).items())
    ):
        raise ValueError("book_reference_repair_parent_unverified")
    binding = previous.metadata_json.get("binding") or {}
    if binding.get("proof_sha256") != content_hash(old):
        raise ValueError("book_reference_old_receipt_changed")
    from bestseller.services.operator_action_approval import require_operator_action_approval

    await require_operator_action_approval(
        session,
        approval_id=UUID(previous.metadata_json["approval_id"]),
        purpose="draft_continuity_reference",
        binding=binding,
        capability="reference_pending_manuscript",
    )
    return old, repair


async def review(session, settings, *, project, chapter, draft, book, prepared_scope=None):
    from bestseller.services.book_continuity_index import projected_sources
    from bestseller.services.draft_continuity_supplement import applicable_plan

    expected_pointer = chapter.metadata_json.get(dc.POINTER)
    expected_owner = book.metadata_json.get("owner")
    plan = await applicable_plan(session, settings, project=project, chapter=chapter)
    artifact = await session.get(m.PlanningArtifactVersionModel, UUID(plan["artifact_id"]))
    approved = next(
        c for c in artifact.content["chapters"] if c["chapter_number"] == chapter.chapter_number
    )
    if prepared_scope is not None:
        parent = await dc.current_sources(session, settings, project=project, chapter=chapter)
        if prepared_scope["body_sha256"] != dc.body_hash(draft.content_md) or prepared_scope[
            "parent_source_sha256"
        ] != content_hash(parent):
            raise ValueError("book_prepared_frontier_changed")
        sources, manifest = applicable.make_projection(
            parent,
            exclusions=prepared_scope["applicability_manifest"]["exclusions"],
            approved_chapter=approved,
            expected_parent_sha256=content_hash(parent),
        )
        view = {
            "schema": "existing-exact-frontier-view.v1",
            "parent_sha256": content_hash(parent),
            "projected_sha256": content_hash(sources),
            "manifest": manifest,
        }
    else:
        indexes = {
            n: v["index"]
            for key, v in book.metadata_json["outputs"].items()
            if key.endswith(":knowledge")
            for n in [key.split(":")[0]]
        }
        parent, sources, view = await projected_sources(
            session,
            settings,
            project=project,
            chapter=chapter,
            draft=draft,
            selected=book.metadata_json["selected"],
            indexes=indexes,
        )
        sources = {**sources, "approved_current_chapter": approved}
        manifest = {
            "schema": applicable.POLICY,
            "parent_source_sha256": content_hash(parent),
            "exclusions": view["omissions"],
            "approved_chapter_sha256": content_hash(approved),
            "projected_source_sha256": content_hash(sources),
            "excluded_sources_assessed": False,
        }
    wid, request, role, digest = applicable.prepare_request(
        settings, project=project, draft=draft, sources=sources, manifest=manifest
    )
    if (
        prepared_scope is not None
        and prepared_scope.get("effective_hash") is not None
        and digest != prepared_scope["effective_hash"]
    ):
        raise ValueError("book_prepared_frontier_request_changed")
    from bestseller.services.drafts import (
        _evaluate_chapter_quality_gate,
        _collect_post_assembly_duplicate_findings,
    )

    tag = {
        "schema": "book-source-gate.v1",
        "draft_id": str(draft.id),
        "body_sha256": dc.body_hash(draft.content_md),
        "source_sha256": content_hash(parent),
        "workflow_id": str(wid),
    }
    outcome = await _evaluate_chapter_quality_gate(
        session=session,
        project=project,
        chapter_number=chapter.chapter_number,
        content=draft.content_md,
        register_diversity=False,
        extra_report_payload={"draft_continuity_hard_safety": tag},
    )
    duplicates = await _collect_post_assembly_duplicate_findings(
        session, project=project, chapter=chapter, content_md=draft.content_md
    )
    if outcome != "ok" or duplicates:
        raise ValueError("book_deterministic_gate_unknown_or_blocked")
    quality = await session.scalar(
        select(m.ChapterQualityReportModel)
        .where(
            m.ChapterQualityReportModel.chapter_id == chapter.id,
            m.ChapterQualityReportModel.report_json["draft_continuity_hard_safety"][
                "workflow_id"
            ].as_string()
            == str(wid),
            m.ChapterQualityReportModel.report_json["draft_continuity_hard_safety"][
                "draft_id"
            ].as_string()
            == str(draft.id),
            m.ChapterQualityReportModel.report_json["draft_continuity_hard_safety"][
                "body_sha256"
            ].as_string()
            == tag["body_sha256"],
            m.ChapterQualityReportModel.report_json["draft_continuity_hard_safety"][
                "source_sha256"
            ].as_string()
            == tag["source_sha256"],
        )
        .order_by(m.ChapterQualityReportModel.created_at.desc())
        .limit(1)
    )
    if quality is None or quality.report_json.get("draft_continuity_hard_safety") != tag:
        raise ValueError("book_deterministic_receipt_missing")
    # Reuse the already implemented native source/whole-body safety protocol.
    wf = await session.get(m.WorkflowRunModel, wid, populate_existing=True)
    identity = content_hash(
        {
            "draft_id": str(draft.id),
            "body": dc.body_hash(draft.content_md),
            "source": content_hash(parent),
            "manifest": manifest,
            "view": view,
            "digest": digest,
        }
    )
    if wf is None:
        wf = m.WorkflowRunModel(
            id=wid,
            project_id=project.id,
            workflow_type="book_source_safety",
            scope_type="chapter",
            scope_id=chapter.id,
            status="running",
            metadata_json={"identity": identity},
        )
        session.add(wf)
        await session.commit()
    if wf.metadata_json.get("identity") != identity:
        raise ValueError("book_safety_workflow_changed")
    if wf.status == "completed":
        run_id = UUID(wf.metadata_json["run_id"])
    else:
        result = await llm.complete_text(session, settings, request)
        run_id = result.llm_run_id
    report, physical, args_hash = await applicable.verify_paid(
        session,
        settings,
        project=project,
        workflow_id=wid,
        draft=draft,
        sources=sources,
        manifest=manifest,
        run_id=run_id,
    )
    await session.refresh(project)
    fresh_parent = await dc.current_sources(session, settings, project=project, chapter=chapter)
    if content_hash(fresh_parent) != content_hash(parent):
        raise ValueError("book_source_changed_during_review")
    if not dc.safety_passed(report):
        wf.status = "completed"
        wf.current_step = "source_safety_not_passed"
        wf.metadata_json = {
            "identity": identity,
            "run_id": str(run_id),
            "report": report,
            "physical": physical,
            "arguments_sha256": args_hash,
        }
        await session.commit()
        if report["unknown_issues"] or any(c["status"] == "unknown" for c in report["checks"]):
            raise ValueError("book_source_safety_unknown")
        from bestseller.services.book_hard_repair import KnownHardBlock

        raise KnownHardBlock(
            "book_source_hard_contradiction",
            {
                "workflow_id": str(wid),
                "report": report,
                "body_sha256": dc.body_hash(draft.content_md),
            },
        )
    proof = {
        "schema": SCHEMA,
        "draft_id": str(draft.id),
        "body_sha256": dc.body_hash(draft.content_md),
        "source_sha256": content_hash(parent),
        "review_workflow_id": str(wid),
        "book_run_id": str(book.id),
        "review_identity": identity,
        "run_id": str(run_id),
        "source_view": view,
        "manifest": manifest,
        "projected_sources": sources,
        "report": report,
        "arguments_sha256": args_hash,
        "physical": physical,
        "quality_report_id": str(quality.id),
        "deterministic_outcome": outcome,
        "deterministic_binding": tag,
        "hard_policy_sha256": dc.hard_policy_hash(),
        "formal_adoption": False,
        "canon_authority": False,
    }
    saved = {"identity": identity, "run_id": str(run_id), "proof": proof}
    if wf.status == "completed" and wf.metadata_json != saved:
        raise ValueError("book_paid_review_changed")
    wf.status = "completed"
    wf.metadata_json = saved
    await session.flush()
    from bestseller.services.operator_action_approval import record_operator_action_approval

    budget = await session.get(
        m.ExecutionBudgetModel, project.metadata_json["execution_scope_id"], populate_existing=True
    )
    auth = budget.metadata_json["finite_book_runs"][str(book.id)]["binding"]["authorization_ref"]
    identity_ref = uuid5(NAMESPACE_URL, "book-source-reference:" + content_hash(proof))
    binding = {
        "project_id": str(project.id),
        "target_id": str(identity_ref),
        "proof_sha256": content_hash(proof),
        "draft_id": str(draft.id),
        "body_sha256": proof["body_sha256"],
        "source_sha256": proof["source_sha256"],
        "formal_adoption": False,
    }
    approval = await record_operator_action_approval(
        session,
        purpose="draft_continuity_reference",
        binding=binding,
        capabilities=["reference_pending_manuscript"],
        actor="finite-book-run",
        authorization_ref=auth,
        explicit_user_approval=True,
    )
    await session.execute(
        select(m.WorkflowRunModel.id).where(m.WorkflowRunModel.id == book.id).with_for_update()
    )
    await session.execute(
        select(m.ChapterModel.id).where(m.ChapterModel.id == chapter.id).with_for_update()
    )
    await session.refresh(chapter)
    await session.refresh(draft)
    await session.refresh(book)
    if (
        chapter.metadata_json.get(dc.POINTER) != expected_pointer
        or book.metadata_json.get("owner") != expected_owner
        or book.status not in {"running", "pending"}
        or dc.body_hash(draft.content_md) != proof["body_sha256"]
        or not draft.is_current
    ):
        raise ValueError("book_reference_pointer_or_owner_changed")
    prior = chapter.metadata_json.get(dc.POINTER)
    if prior not in (None, str(identity_ref)):
        old, repair = await _verified_replacement_parent(
            session,
            project=project,
            chapter=chapter,
            draft=draft,
            book=book,
            prior=prior,
            proof=proof,
        )
        audit_id = uuid5(book.id, "source-selection:" + prior + ":" + str(identity_ref))
        audit = await session.get(m.WorkflowRunModel, audit_id)
        if audit is None:
            session.add(
                m.WorkflowRunModel(
                    id=audit_id,
                    project_id=project.id,
                    workflow_type="book_source_selection",
                    scope_type="chapter",
                    scope_id=chapter.id,
                    status="completed",
                    metadata_json={
                        "old_reference_id": prior,
                        "old_proof_sha256": content_hash(old),
                        "known_hard_repair": repair,
                        "new_reference_id": str(identity_ref),
                        "new_proof_sha256": content_hash(proof),
                        "reason": "Known hard repair within unchanged finite book authority",
                        "old_grade_changed": False,
                        "formal_adoption": False,
                    },
                )
            )
    ref = await session.get(m.WorkflowRunModel, identity_ref)
    if ref is None:
        ref = m.WorkflowRunModel(
            id=identity_ref,
            project_id=project.id,
            workflow_type="draft_continuity_reference",
            scope_type="chapter",
            scope_id=chapter.id,
            status="completed",
            current_step="pending_literary_review",
            metadata_json={"proof": proof, "binding": binding, "approval_id": str(approval)},
        )
        session.add(ref)
    chapter.metadata_json = {**chapter.metadata_json, dc.POINTER: str(identity_ref)}
    await session.flush()
    return {
        "chapter_id": str(chapter.id),
        "draft_id": str(draft.id),
        "body_sha256": proof["body_sha256"],
        "reference": {
            "receipt_id": str(identity_ref),
            "draft_id": str(draft.id),
            "body_sha256": proof["body_sha256"],
            "reference_state": "pending_literary_review",
            "formal_adoption": False,
            "canon_authority": False,
        },
    }


async def validate_reference(session, settings, *, project, chapter, draft, proof, _memo=None):
    from bestseller.services import book_continuity_index

    review = await session.get(
        m.WorkflowRunModel, UUID(proof["review_workflow_id"]), populate_existing=True
    )
    if review is None or review.status != "completed" or review.metadata_json.get("proof") != proof:
        raise ValueError("book_reference_review_changed")
    await dc._verify_deterministic_receipt(session, draft=draft, proof=proof)
    parent = await dc.current_sources(
        session, settings, project=project, chapter=chapter, _memo=_memo
    )
    if (
        content_hash(parent) != proof["source_sha256"]
        or dc.body_hash(draft.content_md) != proof["body_sha256"]
    ):
        raise ValueError("book_reference_source_changed")
    # Every stored source scalar is re-bound to the parent. Older bodies may be
    # replaced only by their immutable physically verified continuity indexes.
    book = await session.get(m.WorkflowRunModel, UUID(proof["book_run_id"]))
    view = proof["source_view"]
    if view["schema"] == "existing-exact-frontier-view.v1":
        approved = proof["projected_sources"]["approved_current_chapter"]
        sources, manifest = applicable.make_projection(
            parent,
            exclusions=view["manifest"]["exclusions"],
            approved_chapter=approved,
            expected_parent_sha256=content_hash(parent),
        )
    else:
        indexes = {
            key.split(":")[0]: value["index"]
            for key, value in book.metadata_json["outputs"].items()
            if key.endswith(":knowledge")
        }
        _, sources, current_view = await book_continuity_index.projected_sources(
            session,
            settings,
            project=project,
            chapter=chapter,
            draft=draft,
            selected=book.metadata_json["selected"],
            indexes=indexes,
        )
        if current_view != view:
            raise ValueError("book_reference_capacity_view_changed")
        sources = {
            **sources,
            "approved_current_chapter": proof["projected_sources"]["approved_current_chapter"],
        }
        manifest = proof["manifest"]
    if sources != proof["projected_sources"]:
        raise ValueError("book_reference_projection_changed")
    report, physical, args_hash = await applicable.verify_paid(
        session,
        settings,
        project=project,
        workflow_id=review.id,
        draft=draft,
        sources=sources,
        manifest=manifest,
        run_id=UUID(proof["run_id"]),
    )
    if (
        report != proof["report"]
        or not dc.safety_passed(report)
        or physical != proof["physical"]
        or args_hash != proof["arguments_sha256"]
    ):
        raise ValueError("book_reference_physical_changed")
