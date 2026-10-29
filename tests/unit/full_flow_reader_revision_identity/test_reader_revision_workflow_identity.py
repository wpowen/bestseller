"""RED: the marked apply entry must create and retain actual native identity.

All assertions below await main's isolated run. Scripted responses establish
identity/custody only; no real commercial, emotional or reader-quality claim.
"""

# ruff: noqa: RUF001

from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

from full_flow_emotional_preservation.support import equal, require, sha
import pytest
from sqlalchemy import select

from bestseller.domain.enums import WorkflowStatus
from bestseller.infra.db import models as m
from bestseller.services import budget, drafts, workflows

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

ANCHOR = "reader_revision_anchors"
VERIFY = "reader_revision_preservation"
EDITOR = "reader_chapter_issue_edit"
ACCEPTANCE = "reader_chapter_issue_acceptance"


async def native_receipts(case, expected):
    rows = await case.workflow_rows()
    equal("workflow.only_one", 1, len(rows))
    workflow = rows[0]
    require("workflow.produced_normally", workflow.id in case.created)
    require("native.send_boundaries", bool(case.boundaries))
    equal("native.same_workflow", {workflow.id}, {item["workflow_id"] for item in case.boundaries})
    equal("native.template_census", set(expected), {item["template"] for item in case.boundaries})
    root = Path(case.settings.artifact_store.local_dir)
    async with case.sessions() as session:
        runs = list(await session.scalars(select(m.LlmRunModel)))
        equal("run.template_census", set(expected), {row.prompt_template for row in runs})
        for run in runs:
            equal("run.workflow", workflow.id, run.workflow_run_id)
            equal("run.project", case.project_id, run.project_id)
            require("run.physical_request", bool(run.request_payload_ref))
            require("run.physical_response", bool(run.response_payload_ref))
            for ref in (run.request_payload_ref, run.response_payload_ref):
                payload = json.loads((root / ref).read_text())
                equal("physical.workflow", str(workflow.id), payload["workflow_run_id"])
                equal("physical.project", str(case.project_id), payload["project_id"])
                equal("physical.run", str(run.id), payload["llm_run_id"])
            receipt = run.metadata_json["execution_receipt"]
            operation = await session.get(m.ExecutionOperationModel, receipt["operation_key"])
            require("physical.operation_exists", operation is not None)
            equal("physical.scope", case.scope, operation.scope_id)
            equal("physical.hash", run.prompt_hash, operation.input_hash)
            equal("physical.completed", "completed", operation.status)
        source = await session.get(m.ChapterDraftVersionModel, case.source_id)
        equal("source.bytes_retained", case.body, source.content_md)
        equal("source.no_grade_invented", None, source.promotion_score)
        equal("source.no_approval_invented", "candidate", source.promotion_state)
        chapter = await session.get(m.ChapterModel, case.chapter_id)
        equal("chapter.blocker_retained", "blocked", chapter.production_state)
    for args in case.creation_arguments:
        equal(
            "creator.no_supplied_owner",
            set(),
            {"execution_owner", "native_pipeline_owner"} & set(args.get("metadata") or {}),
        )
    return workflow.id


async def test_apply_creates_real_chapter_workflow_before_freeze_and_all_issue_sends(revision_case):
    case = revision_case
    equal("fixture.no_workflow_precreated", [], await case.workflow_rows())
    result = await case.apply()
    equal("issue.accepted", True, result["ok"])
    workflow_id = await native_receipts(case, {ANCHOR, EDITOR, ACCEPTANCE, VERIFY})
    equal("first.send_is_freeze", ANCHOR, case.boundaries[0]["template"])
    equal("last.send_is_preservation", VERIFY, case.boundaries[-1]["template"])
    equal(
        "volta.both_native_apis",
        ["freeze_reader_revision_anchors", "verify_reader_revision_preservation"],
        [item["api"] for item in case.volta],
    )
    for item in case.volta:
        equal("volta.same_workflow", workflow_id, item["ids"]["workflow_run_id"])
        equal("volta.source", case.source_id, item["ids"]["source_draft_id"])
        equal("volta.no_adoption_authority", False, item["receipt"]["adoption_authority"])


async def test_completed_task_retry_reuses_workflow_without_new_request_or_draft(revision_case):
    case = revision_case
    await case.apply()
    workflow_id = await native_receipts(case, {ANCHOR, EDITOR, ACCEPTANCE, VERIFY})
    before = await case.protected()
    ledger, files, wires = await case.ledger(), case.files(), len(case.wires)
    await case.apply()
    equal(
        "retry.only_original_workflow",
        [workflow_id],
        [row.id for row in await case.workflow_rows()],
    )
    equal("retry.no_new_send", wires, len(case.wires))
    equal("retry.same_ledger", ledger, await case.ledger())
    equal("retry.same_physical_payloads", files, case.files())
    equal("retry.same_drafts_and_blockers", before, await case.protected())


async def test_actual_sanitized_return_retains_raw_custody_on_the_same_workflow(revision_case):
    case = revision_case
    case.writer_text = "<!-- native response note -->\n" + case.candidate_text
    result = await case.apply()
    equal("sanitized.accepted", True, result["ok"])
    await native_receipts(case, {ANCHOR, EDITOR, ACCEPTANCE, VERIFY})
    receipt = case.volta[-1]["receipt"]
    equal("sanitized.raw_body", sha(case.writer_text), receipt["raw_body_sha256"])
    equal("sanitized.final_body", sha(case.candidate_text), receipt["candidate_body_sha256"])
    equal(
        "sanitized.normalizer",
        "bestseller.services.drafts.sanitize_novel_markdown_content",
        receipt["derivation"]["normalizer"],
    )
    equal("sanitized.no_extra_authority", False, receipt["adoption_authority"])


async def test_native_preservation_rejection_keeps_original_draft_and_blockers(revision_case):
    case = revision_case
    case.lost_anchors = ("a1",)
    before = await case.protected()
    result = await case.apply()
    equal("lost_anchor.rejected", "reader_revision_not_preserved", result["rejected_reason"])
    await native_receipts(case, {ANCHOR, EDITOR, ACCEPTANCE, VERIFY})
    equal("lost_anchor.original_retained", before, await case.protected())


async def test_joined_segments_cannot_borrow_last_segment_as_full_body_proof(revision_case):
    case = revision_case
    async with case.sessions.begin() as session:
        task = await session.get(m.RewriteTaskModel, case.task_id)
        task.instructions = "目标字数：100-120字\n保持人物的共同决定。"
        source = await session.get(m.ChapterDraftVersionModel, case.source_id)
        source.assembled_from_scene_draft_ids = [str(uuid4()), str(uuid4())]
    before = await case.protected()
    from bestseller.services.chapter_revision import ReaderRevisionIdentityError

    with pytest.raises(ReaderRevisionIdentityError) as caught:
        await case.apply()
    equal("joined.exact_denial", "candidate.native_full_body", caught.value.differences[0]["field"])
    equal("joined.no_preservation_send", False, VERIFY in [r.prompt_template for r in case.calls])
    equal(
        "joined.two_body_calls",
        2,
        sum(r.prompt_template == "reader_issue_segment_writer" for r in case.calls),
    )
    equal("joined.original_retained", before, await case.protected())


@pytest.mark.parametrize("template", [ANCHOR, EDITOR, ACCEPTANCE, VERIFY, "litstyle_prose_judge"])
async def test_unknown_native_attempt_never_mints_workflow_or_resends_on_retry(
    revision_case, template
):
    case = revision_case
    if template == "litstyle_prose_judge":
        case.operation = "polish"
        async with case.sessions.begin() as session:
            task = await session.get(m.RewriteTaskModel, case.task_id)
            task.rewrite_strategy = "chapter_polish"
            task.metadata_json = {**task.metadata_json, "operation": "polish"}
            case.original_task_metadata = deepcopy(task.metadata_json)
    case.fail_template = template
    before = await case.protected()
    try:
        await case.apply()
    except Exception:
        pass  # The native ledger below must prove Unknown; an arbitrary error cannot satisfy RED.
    ledger = await case.ledger()
    equal("unknown.native_parent", "unknown", ledger["status"])
    require(
        "unknown.actual_attempt", any(row[0] == "unknown" for row in ledger["attempts"].values())
    )
    equal("unknown.last_wire", template, case.boundaries[-1]["template"])
    rows = await case.workflow_rows()
    equal("unknown.one_workflow", 1, len(rows))
    identity = (rows[0].id, rows[0].created_at, deepcopy(rows[0].metadata_json))
    wires, files = len(case.wires), case.files()
    case.fail_template = None
    try:
        await case.apply()
    except Exception:
        pass
    after = await case.workflow_rows()
    equal(
        "unknown.same_original_workflow",
        [identity],
        [(row.id, row.created_at, row.metadata_json) for row in after],
    )
    equal("unknown.no_new_wire", wires, len(case.wires))
    equal("unknown.no_new_calls_or_attempts", ledger, await case.ledger())
    equal("unknown.physical_retained", files, case.files())
    equal("unknown.no_adoption_or_grade", before, await case.protected())


async def test_polish_passes_same_workflow_to_source_and_candidate_litstyle(revision_case):
    case = revision_case
    case.operation = "polish"
    async with case.sessions.begin() as session:
        task = await session.get(m.RewriteTaskModel, case.task_id)
        task.rewrite_strategy = "chapter_polish"
        task.metadata_json = {**task.metadata_json, "operation": "polish"}
        case.original_task_metadata = deepcopy(task.metadata_json)
    await case.apply()
    workflow_id = await native_receipts(
        case,
        {
            ANCHOR,
            "litstyle_prose_judge",
            "reader_chapter_polish",
            VERIFY,
        },
    )
    judges = [
        request for request in case.calls if request.prompt_template == "litstyle_prose_judge"
    ]
    equal("litstyle.both_calls", 2, len(judges))
    equal(
        "litstyle.workflow_census", [workflow_id, workflow_id], [r.workflow_run_id for r in judges]
    )
    equal(
        "litstyle.project_census",
        [case.project_id, case.project_id],
        [r.project_id for r in judges],
    )


async def test_native_cleanup_inherits_workflow_and_unknown_prevents_later_send(revision_case):
    case = revision_case
    case.writer_text = (
        case.candidate_text + "\n她又看了一遍纸条，third limited 几个字夹在修屋记录里。"
    )
    normalized = drafts.strip_scaffolding_echoes(
        drafts.sanitize_novel_markdown_content(case.writer_text)
    )
    require("fixture.real_cleanup_trigger", drafts.has_meta_leak(normalized))
    case.fail_template = "content_validation"
    before = await case.protected()
    try:
        await case.apply()
    except Exception:
        pass
    equal(
        "cleanup.reached_native_wire",
        [ANCHOR, EDITOR, "content_validation"],
        [item["template"] for item in case.boundaries],
    )
    rows = await case.workflow_rows()
    equal("cleanup.one_actual_workflow", 1, len(rows))
    equal("cleanup.same_workflow", {rows[0].id}, {item["workflow_id"] for item in case.boundaries})
    ledger, files, wires = await case.ledger(), case.files(), len(case.wires)
    equal("cleanup.native_unknown", "unknown", ledger["status"])
    case.fail_template = None
    try:
        await case.apply()
    except Exception:
        pass
    equal("cleanup.no_new_attempt", ledger, await case.ledger())
    equal("cleanup.no_new_wire", wires, len(case.wires))
    equal("cleanup.physical_retained", files, case.files())
    equal("cleanup.no_adoption", before, await case.protected())


async def test_segment_plan_writer_and_compress_keep_identity_without_budget_escape(revision_case):
    case = revision_case
    case.first_segment_oversize = True
    async with case.sessions.begin() as session:
        task = await session.get(m.RewriteTaskModel, case.task_id)
        task.instructions = "目标字数：100-120字\n保持人物的共同决定。"
        source = await session.get(m.ChapterDraftVersionModel, case.source_id)
        source.assembled_from_scene_draft_ids = [str(uuid4()), str(uuid4())]
    before = await case.protected()
    result = await case.apply()
    equal("segmented.no_unproved_adoption", False, result["ok"])
    await native_receipts(
        case,
        {
            ANCHOR,
            "reader_issue_segment_plan",
            "reader_issue_segment_writer",
            "reader_issue_segment_compress",
        },
    )
    # Writer + compression consume the existing two-body limit. Never enlarge
    # that limit or forge a full-body receipt to make the segment flow succeed.
    equal("segmented.protected_source", before, await case.protected())
    chapter = await case.chapter_metadata()
    equal(
        "segmented.existing_round_limit",
        2,
        len(chapter["chapter_automatic_revision_budget"]["history"]),
    )


async def test_existing_binding_to_foreign_chapter_workflow_is_not_overwritten(revision_case):
    case = revision_case
    with budget.bind_execution_scope(case.scope):
        async with case.sessions.begin() as session:
            foreign = await workflows.create_workflow_run(
                session,
                project_id=case.project_id,
                workflow_type="reader_chapter_revision",
                status=WorkflowStatus.RUNNING,
                scope_type="chapter",
                scope_id=uuid4(),
                requested_by="offline foreign source",
                idempotency_key=f"reader-chapter-revision:{case.task_id}",
            )
            task = await session.get(m.RewriteTaskModel, case.task_id)
            task.metadata_json = {
                **task.metadata_json,
                "reader_revision_workflow_run_id": str(foreign.id),
            }
            identity = (foreign.id, foreign.created_at, deepcopy(foreign.metadata_json))
    try:
        result = await case.apply()
    except Exception:
        result = {"ok": False}
    equal("foreign.rejected", False, result["ok"])
    equal("foreign.no_send", [], case.wires)
    rows = await case.workflow_rows()
    equal(
        "foreign.original_identity_retained",
        [identity],
        [(row.id, row.created_at, row.metadata_json) for row in rows],
    )


@pytest.mark.parametrize("guard", ["humanize", "stale_source"])
async def test_existing_entry_guards_still_precede_workflow_creation(revision_case, guard):
    case = revision_case
    if guard == "humanize":
        case.operation = "humanize"
        reason = "automatic_local_edit_requires_native_budget"
    else:
        reason = "stale_source_draft"
        async with case.sessions.begin() as session:
            task = await session.get(m.RewriteTaskModel, case.task_id)
            task.metadata_json = {**task.metadata_json, "source_chapter_draft_id": str(uuid4())}
    result = await case.apply()
    equal("guard.reason", reason, result["rejected_reason"])
    equal("guard.no_workflow", [], await case.workflow_rows())
    equal("guard.no_send", [], case.wires)
    equal("guard.no_creator", [], case.created)


async def test_unmarked_legacy_entry_does_not_backfill_workflow_or_volta(revision_case):
    case = revision_case
    case.marked, case.accept = False, False
    async with case.sessions.begin() as session:
        project = await session.get(m.ProjectModel, case.project_id)
        project.metadata_json = {"execution_scope_id": case.scope}
    before = await case.protected()
    result = await case.apply()
    equal(
        "legacy.original_acceptance_refusal", "issue_acceptance_failed", result["rejected_reason"]
    )
    equal("legacy.no_workflow_backfill", [], await case.workflow_rows())
    equal("legacy.no_new_volta", [], case.volta)
    equal("legacy.source_unchanged", before, await case.protected())
    equal(
        "legacy.original_templates", [EDITOR, ACCEPTANCE], [r.prompt_template for r in case.calls]
    )
    equal("legacy.original_missing_wf", [None, None], [r.workflow_run_id for r in case.calls])
