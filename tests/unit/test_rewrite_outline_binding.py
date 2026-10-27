"""Real SQLite source freshness and history tests; no PG or network access."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, update

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    ReviewReportModel,
    RewriteTaskModel,
)
from bestseller.services import reviews
from bestseller.services import rewrite_outline_binding as binding_service
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.quality_report_claim import graded_text_fingerprint
from bestseller.services.rewrite_patch import text_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def marker():
    return {
        "artifact_id": str(uuid4()),
        "workflow_run_id": str(uuid4()),
        "projection_hash": text_hash("current approved outline projection"),
        "source_content_sha256": text_hash("complete approved source content"),
    }


async def seed(session, *, marked=True, bound=False):
    project = ProjectModel(
        id=uuid4(),
        slug=f"outline-test-{uuid4().hex}",
        title="Independent transport diary",
        genre="travel",
        language="en",
        target_word_count=8000,
        target_chapters=4,
        metadata_json={"global_ledger": {"attempts_used": 17}},
    )
    current = marker()
    chapter = ChapterModel(
        id=uuid4(),
        project_id=project.id,
        chapter_number=1,
        chapter_goal="Return the map",
        production_state="blocked",
        status="revision",
        revision_count=6,
        metadata_json={
            **({"outline_source_admission": current} if marked else {}),
            "outline_prefix_readiness": {"artifact_id": "legacy prefix is not authority"},
            "auto_repair_total_attempts": 8,
            "rewrite_history": [{"version": 1, "block_codes": ["ORIGINAL"]}],
            "rewrite_escalation_failures": 2,
        },
    )
    draft = ChapterDraftVersionModel(
        id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="A cartographer compared the map with the road before returning it.",
        word_count=13,
        is_current=True,
        promotion_state="under_review",
    )
    task = RewriteTaskModel(
        id=uuid4(),
        project_id=project.id,
        trigger_type="chapter_review",
        trigger_source_id=chapter.id,
        rewrite_strategy="chapter_coherence_bridge_rewrite",
        status="pending",
        priority=3,
        instructions="FULL ORIGINAL OBSOLETE INSTRUCTIONS",
        attempts=4,
        error_log="FULL ORIGINAL ERROR",
        context_required=["original_context"],
        metadata_json={
            **reviews.rewrite_source_binding(draft),
            **({"source_plan_binding": deepcopy(current)} if bound else {}),
            "raw_candidate_receipts": [{"body": "retained rejected candidate", "attempt": 4}],
            "attempt_budget": 5,
            "supersession_receipts": [{"prior": "history"}],
        },
    )
    session.add_all([project, chapter, draft, task])
    await session.commit()
    return SimpleNamespace(
        project=project, chapter=chapter, draft=draft, task=task, current=current, session=session
    )


def migration_args(case):
    return dict(
        project_id=case.project.id,
        chapter_id=case.chapter.id,
        draft_id=case.draft.id,
        expected_body_sha256=text_hash(case.draft.content_md),
        expected_plan_binding=deepcopy(case.current),
    )


async def rewrite_entry(case, monkeypatch):
    monkeypatch.setattr(
        reviews,
        "_load_chapter_context",
        AsyncMock(return_value=(case.project, case.chapter, None, [], case.draft)),
    )
    return await reviews.rewrite_chapter_from_task(
        case.session, case.project.slug, 1, rewrite_task_id=case.task.id
    )


@pytest.mark.parametrize("status", ["pending", "queued"])
async def test_old_unbound_task_fails_before_provider_without_spending_or_rebinding(
    db, monkeypatch, status
):
    async with db() as session:
        case = await seed(session)
        case.task.status = status
        await session.commit()
        original = deepcopy(case.task.metadata_json)
        with pytest.raises(reviews.RewriteSourceRejected) as caught:
            await rewrite_entry(case, monkeypatch)
        assert caught.value.receipt["reason"] == "outline_source_binding_not_current"
        await session.commit()
    async with db() as session:
        saved = await session.get(RewriteTaskModel, case.task.id)
        assert saved.status == "stale" and saved.attempts == 4
        assert saved.instructions == "FULL ORIGINAL OBSOLETE INSTRUCTIONS"
        assert "source_plan_binding" not in saved.metadata_json
        assert all((saved.metadata_json[k] == v for k, v in original.items()))
        assert await session.scalar(select(func.count()).select_from(ChapterDraftVersionModel)) == 1


@pytest.mark.parametrize(
    "key", ["artifact_id", "workflow_run_id", "projection_hash", "source_content_sha256"]
)
async def test_every_marker_field_is_exact_even_when_body_is_unchanged(db, key):
    async with db() as session:
        case = await seed(session, bound=True)
        old = deepcopy(case.task.metadata_json)
        changed = deepcopy(case.current)
        changed[key] = str(uuid4()) if key.endswith("id") else text_hash("other outline")
        await session.execute(
            update(ChapterModel)
            .where(ChapterModel.id == case.chapter.id)
            .values(metadata_json={"outline_source_admission": changed})
            .execution_options(synchronize_session=False)
        )
        assert case.chapter.metadata_json["outline_source_admission"] == case.current
        with pytest.raises(reviews.RewriteSourceRejected):
            await reviews._validate_rewrite_task_parent(session, case.task, case.draft)
        assert case.task.metadata_json["source_plan_binding"] == old["source_plan_binding"]
        assert case.task.attempts == 4


@pytest.mark.parametrize("invalid", [None, {}, {"artifact_id": "claim"}])
async def test_present_invalid_marker_cannot_fall_back_to_legacy(db, invalid):
    async with db() as session:
        case = await seed(session)
        case.chapter.metadata_json = {"outline_source_admission": invalid}
        await session.flush()
        with pytest.raises(reviews.RewriteSourceRejected) as caught:
            await reviews._validate_rewrite_task_parent(session, case.task, case.draft)
        assert caught.value.receipt["reason"] == "outline_source_marker_invalid"


async def test_exact_new_binding_passes_entry_and_commit_without_making_grade(db):
    async with db() as session:
        case = await seed(session, bound=True)
        original = deepcopy(case.task.metadata_json)
        await reviews._validate_rewrite_task_parent(session, case.task, case.draft)
        await reviews._recheck_rewrite_parent(session, case.task, case.draft, parent=case.chapter)
        assert case.task.metadata_json == original
        assert case.task.status == "pending" and case.task.attempts == 4
        assert await session.scalar(select(func.count()).select_from(ReviewReportModel)) == 0


@pytest.mark.parametrize("change", ["marker", "body", "removed_marker", "task_rebound"])
async def test_commit_rejects_changed_source_and_retains_unpromoted_candidate(db, change):
    async with db() as session:
        case = await seed(session, bound=True)
        await reviews._validate_rewrite_task_parent(session, case.task, case.draft)
        candidate = "An unpromoted candidate against the original plan."
        if change == "body":
            await session.execute(
                update(ChapterDraftVersionModel)
                .where(ChapterDraftVersionModel.id == case.draft.id)
                .values(content_md="In-place replacement of the parent body")
                .execution_options(synchronize_session=False)
            )
        else:
            replacement = marker()
            await session.execute(
                update(ChapterModel)
                .where(ChapterModel.id == case.chapter.id)
                .values(
                    metadata_json={}
                    if change == "removed_marker"
                    else {"outline_source_admission": replacement}
                )
                .execution_options(synchronize_session=False)
            )
            if change == "task_rebound":
                case.task.metadata_json = {
                    **case.task.metadata_json,
                    "source_plan_binding": replacement,
                }
        with pytest.raises(reviews.RewriteSourceRejected) as caught:
            await reviews._recheck_rewrite_parent(
                session, case.task, case.draft, parent=case.chapter, candidate_text=candidate
            )
        error = caught.value
        assert error.source_identity["source_plan_binding"] == case.current
        evidence = error.receipt["unpromoted_stale_candidate"]
        assert evidence["content_md"] == candidate and evidence["sha256"] == text_hash(candidate)
        if change == "task_rebound":
            assert error.receipt["reason"] == "outline_source_changed_before_commit"
        await session.commit()
    async with db() as session:
        saved = await session.get(RewriteTaskModel, case.task.id)
        assert saved.status == "stale" and saved.attempts == 4
        assert saved.metadata_json["unpromoted_stale_candidate"] == evidence
        assert (await session.get(ChapterDraftVersionModel, case.draft.id)).is_current
        assert await session.scalar(select(func.count()).select_from(ChapterDraftVersionModel)) == 1


@pytest.mark.parametrize("initially_marked", [True, False])
async def test_actual_rewrite_provider_return_cannot_promote_after_outline_change(
    db, monkeypatch, rewrite_runtime, initially_marked
):
    from bestseller.services import lossless_rewrite_prompt, novel_expression_guidance
    from bestseller.services.deterministic_post_write_audit import DeterministicAuditReport

    async with db() as session:
        case = await seed(session, marked=initially_marked, bound=initially_marked)
        original_binding = deepcopy(case.task.metadata_json.get("source_plan_binding"))
        monkeypatch.setattr(
            reviews,
            "_load_chapter_context",
            AsyncMock(return_value=(case.project, case.chapter, None, [], case.draft)),
        )
        monkeypatch.setattr(
            reviews, "render_rewritten_chapter_markdown", lambda *a, **k: case.draft.content_md
        )
        monkeypatch.setattr(
            reviews,
            "build_chapter_rewrite_prompts",
            lambda *a, **k: ("Fixture editor", "Complete fixture source"),
        )
        monkeypatch.setattr(
            lossless_rewrite_prompt,
            "prepare_source_complete_prompt",
            lambda **kw: (kw["system_prompt"], kw["user_prompt"], {}),
        )
        monkeypatch.setattr(
            novel_expression_guidance,
            "attach_revision_guidance",
            lambda **kw: (kw["system_prompt"], kw["user_prompt"], {}),
        )
        monkeypatch.setattr(
            reviews, "_collect_post_assembly_duplicate_findings", AsyncMock(return_value=())
        )
        monkeypatch.setattr(
            reviews,
            "_audit_chapter_rewrite_candidate",
            lambda **kw: (DeterministicAuditReport(1, (), True, {}), rewrite_runtime),
        )
        monkeypatch.setattr(reviews, "_quality_retrofit_candidate_findings", lambda *a, **k: [])
        candidate = "The cartographer returned the map after recording the changed road."
        monkeypatch.setattr(
            reviews,
            "_evaluate_rewrite_quality_gate",
            AsyncMock(
                return_value=reviews.RewriteQualityGateReceipt(
                    "ok", "fixture-only", graded_text_fingerprint(candidate)
                )
            ),
        )
        calls = []

        async def provider(_session, _settings, request):
            calls.append(request)
            async with db() as concurrent:
                await concurrent.execute(
                    update(ChapterModel)
                    .where(ChapterModel.id == case.chapter.id)
                    .values(metadata_json={"outline_source_admission": marker()})
                )
                await concurrent.commit()
            return LLMCompletionResult(
                content=candidate, provider="fixture", model_name="fixture", llm_run_id=None
            )

        monkeypatch.setattr(reviews, "complete_text", provider)
        with pytest.raises(reviews.RewriteSourceRejected) as caught:
            await reviews.rewrite_chapter_from_task(
                session,
                case.project.slug,
                1,
                rewrite_task_id=case.task.id,
                settings=load_settings(env={}),
            )
        assert len(calls) == 1
        assert case.task.metadata_json.get("source_plan_binding") == original_binding
        assert caught.value.receipt["unpromoted_stale_candidate"]["content_md"] == candidate
        assert await session.scalar(select(func.count()).select_from(ChapterDraftVersionModel)) == 1
        await session.commit()
    async with db() as session:
        saved = await session.get(RewriteTaskModel, case.task.id)
        assert saved.status == "stale" and saved.attempts == 4
        assert saved.metadata_json["unpromoted_stale_candidate"]["content_md"] == candidate


async def test_legacy_unmarked_body_and_report_migration_are_unchanged(db):
    async with db() as session:
        case = await seed(session, marked=False)
        report = ReviewReportModel(
            id=uuid4(),
            project_id=case.project.id,
            target_type="chapter",
            target_id=case.chapter.id,
            reviewer_type="fixture",
            verdict="rewrite",
            structured_output={
                "draft_id": str(case.draft.id),
                "reviewed_body_fingerprint": reviews.chapter_review_body_fingerprint(
                    case.draft.content_md
                ),
            },
        )
        session.add(report)
        case.task.metadata_json = {
            "draft_id": str(case.draft.id),
            "review_report_id": str(report.id),
        }
        await session.flush()
        await reviews._validate_rewrite_task_parent(session, case.task, case.draft)
        migrated = deepcopy(case.task.metadata_json)
        await reviews._validate_rewrite_task_parent(session, case.task, case.draft)
        await reviews._recheck_rewrite_parent(session, case.task, case.draft, parent=case.chapter)
        assert case.task.metadata_json == migrated
        assert migrated["parent_draft_hash"] == text_hash(case.draft.content_md)
        assert "source_plan_binding" not in migrated
        assert case.task.attempts == 4


@pytest.mark.parametrize("stale_bound", [False, True])
async def test_migration_preserves_history_lineage_ledgers_and_is_idempotent(db, stale_bound):
    async with db() as session:
        case = await seed(session, bound=stale_bound)
        if stale_bound:
            case.current = marker()
            case.chapter.metadata_json = {
                **case.chapter.metadata_json,
                "outline_source_admission": case.current,
            }
        parent = RewriteTaskModel(
            project_id=case.project.id,
            trigger_type="chapter_review",
            trigger_source_id=case.chapter.id,
            rewrite_strategy="original",
            status="failed",
            instructions="Retained parent",
            attempts=7,
            metadata_json={"old": "ledger"},
        )
        session.add(parent)
        await session.flush()
        case.task.parent_task_id = parent.id
        case.task.status = "queued"
        await session.commit()
        old = deepcopy(case.task.metadata_json)
        chapter_meta = deepcopy(case.chapter.metadata_json)
        project_meta = deepcopy(case.project.metadata_json)
        flushes = []
        event.listen(session.sync_session, "before_flush", lambda *_: flushes.append(True))
        args = migration_args(case)
        result = await binding_service.stage_outline_revision_rewrite_tasks(session, **args)
        repeated = await binding_service.stage_outline_revision_rewrite_tasks(session, **args)
        assert result["created"] and (not repeated["created"]) and (not flushes)
        assert result["replacement_task_id"] == repeated["replacement_task_id"]
        assert repeated["superseded_task_ids"] == []
        await session.commit()
    async with db() as session:
        original = await session.get(RewriteTaskModel, case.task.id)
        replacement = await session.get(RewriteTaskModel, UUID(result["replacement_task_id"]))
        assert original.instructions == "FULL ORIGINAL OBSOLETE INSTRUCTIONS"
        assert original.context_required == ["original_context"]
        assert original.error_log == "FULL ORIGINAL ERROR"
        assert original.parent_task_id == parent.id and original.status == "superseded"
        assert original.attempts == 4
        assert all(
            (original.metadata_json[k] == v for k, v in old.items() if k != "supersession_receipts")
        )
        history = original.metadata_json["supersession_receipts"]
        assert history[:-1] == old["supersession_receipts"]
        assert history[-1]["previous_status"] == "queued"
        assert history[-1]["previous_attempts"] == 4
        assert replacement.parent_task_id == original.id and replacement.attempts == 0
        assert replacement.status == "pending"
        assert replacement.metadata_json["source_plan_binding"] == case.current
        assert replacement.metadata_json["parent_draft_hash"] == text_hash(case.draft.content_md)
        assert replacement.metadata_json["source_alignment"]["evidence_status"] == "UNGRADED"
        assert replacement.metadata_json["source_alignment"]["requires_formal_review"] is True
        assert replacement.metadata_json["source_alignment"]["requires_formal_adoption"] is True
        assert replacement.metadata_json["source_alignment"]["prior_task_attempts"] == {
            str(original.id): 4
        }
        assert "review_report_id" not in replacement.metadata_json
        assert "UNGRADED" in replacement.instructions
        assert "FULL ORIGINAL OBSOLETE INSTRUCTIONS" not in replacement.instructions
        assert (await session.get(ChapterModel, case.chapter.id)).metadata_json == chapter_meta
        assert (await session.get(ProjectModel, case.project.id)).metadata_json == project_meta
        assert await session.scalar(select(func.count()).select_from(ReviewReportModel)) == 0
        await reviews._validate_rewrite_task_parent(session, replacement, case.draft)
        await reviews._recheck_rewrite_parent(session, replacement, case.draft, parent=case.chapter)
        replacement.status = "completed"
        replacement.attempts = 1
        again = await binding_service.stage_outline_revision_rewrite_tasks(session, **args)
        assert not again["created"] and again["replacement_task_id"] == str(replacement.id)
        assert replacement.attempts == 1 and replacement.status == "completed"
        await session.commit()
        persisted_again = await binding_service.stage_outline_revision_rewrite_tasks(
            session, **args
        )
        assert not persisted_again["created"]
        assert persisted_again["replacement_task_id"] == str(replacement.id)


@pytest.mark.parametrize("invalid", ["marker", "body", "otherproject", "adopted", "debt"])
async def test_migration_independently_rechecks_sources_and_adoption(db, invalid):
    async with db() as session:
        case = await seed(session)
        args = migration_args(case)
        if invalid == "marker":
            args["expected_plan_binding"] = marker()
        elif invalid == "body":
            await session.execute(
                update(ChapterDraftVersionModel)
                .where(ChapterDraftVersionModel.id == case.draft.id)
                .values(content_md="Body changed retaining the ID")
                .execution_options(synchronize_session=False)
            )
        elif invalid == "otherproject":
            args["project_id"] = uuid4()
        elif invalid == "adopted":
            case.draft.promotion_state = "promoted"
            await session.flush()
        else:
            case.chapter.metadata_json = {
                **case.chapter.metadata_json,
                "numeric_promotion_debt": {},
            }
            await session.flush()
        original = deepcopy(case.task.metadata_json)
        with pytest.raises(ValueError):
            await binding_service.stage_outline_revision_rewrite_tasks(session, **args)
        assert case.task.status == "pending" and case.task.attempts == 4
        assert case.task.metadata_json == original
        assert await session.scalar(select(func.count()).select_from(RewriteTaskModel)) == 1


async def test_only_affected_exact_parent_pending_tasks_are_retired(db):
    async with db() as session:
        case = await seed(session)
        extras = []
        for kind in (
            "otherproject",
            "otherchapter",
            "wrongbody",
            "wrongparent",
            "terminal",
            "alreadybound",
            "conflicting_draft_id",
        ):
            task = RewriteTaskModel(
                project_id=uuid4() if kind == "otherproject" else case.project.id,
                trigger_source_id=uuid4() if kind == "otherchapter" else case.chapter.id,
                trigger_type="manual",
                rewrite_strategy="targeted",
                instructions=kind,
                status="completed" if kind == "terminal" else "pending",
                attempts=6,
                metadata_json={
                    **reviews.rewrite_source_binding(case.draft),
                    **({"source_plan_binding": case.current} if kind == "alreadybound" else {}),
                },
            )
            if kind == "wrongbody":
                task.metadata_json["parent_draft_hash"] = text_hash("different body")
            elif kind == "wrongparent":
                task.metadata_json["parent_draft_id"] = str(uuid4())
            elif kind == "conflicting_draft_id":
                task.metadata_json["draft_id"] = str(uuid4())
            extras.append(task)
        session.add_all(extras)
        await session.commit()
        before = [(task.status, deepcopy(task.metadata_json), task.attempts) for task in extras]
        result = await binding_service.stage_outline_revision_rewrite_tasks(
            session, **migration_args(case)
        )
        assert result["superseded_task_ids"] == [str(case.task.id)]
        assert [(task.status, task.metadata_json, task.attempts) for task in extras] == before
        await session.commit()


async def test_helper_stages_no_commits_and_caller_rollback_restores_everything(db):
    async with db() as session:
        case = await seed(session)
        result = await binding_service.stage_outline_revision_rewrite_tasks(
            session, **migration_args(case)
        )
        assert result["created"]
        await session.rollback()
    async with db() as session:
        tasks = list(await session.scalars(select(RewriteTaskModel)))
        assert len(tasks) == 1 and tasks[0].status == "pending" and (tasks[0].attempts == 4)


async def test_source_identity_separates_plan_bindings_for_recovery(db):
    async with db() as session:
        case = await seed(session, bound=True)
        before = deepcopy(reviews._rewrite_task_source_identity(case.task))
        case.task.metadata_json = {**case.task.metadata_json, "source_plan_binding": marker()}
        assert reviews._rewrite_task_source_identity(case.task) != before


async def test_foreign_task_cannot_borrow_another_projects_marker(db):
    async with db() as session:
        case = await seed(session, bound=True)
        case.task.project_id = uuid4()
        await session.flush()
        with pytest.raises(reviews.RewriteSourceRejected) as caught:
            await reviews._validate_rewrite_task_parent(session, case.task, case.draft)
        assert caught.value.receipt["reason"] == "outline_source_scope_invalid"


@pytest.mark.parametrize("rebound", [False, True])
async def test_rollback_recovery_uses_original_plan_identity_and_keeps_candidate_evidence(
    db, rebound
):
    async with db() as session:
        case = await seed(session, bound=True)
        task_id, chapter_id = (case.task.id, case.chapter.id)
        await reviews._validate_rewrite_task_parent(session, case.task, case.draft)
        async with db() as concurrent:
            await concurrent.execute(
                update(ChapterModel)
                .where(ChapterModel.id == chapter_id)
                .values(metadata_json={"outline_source_admission": marker()})
            )
            await concurrent.commit()
        with pytest.raises(reviews.RewriteSourceRejected) as caught:
            await reviews._recheck_rewrite_parent(
                session,
                case.task,
                case.draft,
                parent=case.chapter,
                candidate_text="Candidate preserved outside the rolled-back transaction",
            )
        error = caught.value
        await session.rollback()
    async with db() as recovery:
        task = await recovery.get(RewriteTaskModel, task_id)
        assert task.status == "pending" and task.attempts == 4
        if rebound:
            task.metadata_json = {**task.metadata_json, "source_plan_binding": marker()}
            await recovery.commit()
        assert await reviews.persist_rewrite_rejection(recovery, error)
        await recovery.commit()
    async with db() as readback:
        task = await readback.get(RewriteTaskModel, task_id)
        assert task.attempts == 4 and task.instructions == "FULL ORIGINAL OBSOLETE INSTRUCTIONS"
        history = task.metadata_json["source_binding_rejections"]
        assert history[-1] == error.receipt
        if rebound:
            assert task.status == "pending"
            assert "unpromoted_stale_candidate" not in task.metadata_json
        else:
            assert task.status == "stale"
            assert task.metadata_json["unpromoted_stale_candidate"]["content_md"].startswith(
                "Candidate preserved"
            )


async def test_legacy_unmarked_nullable_trigger_keeps_exact_parent_contract(db):
    async with db() as session:
        case = await seed(session, marked=False)
        case.task.trigger_source_id = None
        await session.commit()
        original = deepcopy(case.task.metadata_json)
        await reviews._validate_rewrite_task_parent(session, case.task, case.draft)
        await reviews._recheck_rewrite_parent(session, case.task, case.draft, parent=case.chapter)
        assert case.task.status == "pending" and case.task.metadata_json == original
        assert case.task.attempts == 4


@pytest.mark.parametrize("b_status", ["pending", "completed"])
@pytest.mark.parametrize("old_a_status", ["superseded", "stale"])
async def test_outline_reversion_appends_executable_successor_preserving_lineage_and_attempts(
    db, b_status, old_a_status
):
    async with db() as session:
        case = await seed(session)
        original_a_marker = deepcopy(case.current)
        result_a = await binding_service.stage_outline_revision_rewrite_tasks(
            session, **migration_args(case)
        )
        await session.commit()
        a_task = await session.get(RewriteTaskModel, UUID(result_a["replacement_task_id"]))
        a_task.attempts = 2
        case.current = marker()
        case.chapter.metadata_json = {
            **case.chapter.metadata_json,
            "outline_source_admission": case.current,
        }
        await session.commit()
        result_b = await binding_service.stage_outline_revision_rewrite_tasks(
            session, **migration_args(case)
        )
        await session.commit()
        b_task = await session.get(RewriteTaskModel, UUID(result_b["replacement_task_id"]))
        assert a_task.status == "superseded"
        a_task.status = old_a_status
        b_task.status = b_status
        b_task.attempts = 3
        case.current = original_a_marker
        case.chapter.metadata_json = {
            **case.chapter.metadata_json,
            "outline_source_admission": case.current,
        }
        await session.commit()
        original_history = deepcopy(a_task.metadata_json)
        chapter_ledger = deepcopy(case.chapter.metadata_json)
        returned = await binding_service.stage_outline_revision_rewrite_tasks(
            session, **migration_args(case)
        )
        assert returned["created"]
        assert returned["replacement_task_id"] not in {str(a_task.id), str(b_task.id)}
        await session.commit()
        fresh = await session.get(RewriteTaskModel, UUID(returned["replacement_task_id"]))
        assert fresh.status == "pending" and fresh.attempts == 0
        assert fresh.parent_task_id == b_task.id
        assert fresh.metadata_json["source_alignment"]["prior_task_attempts"] == {str(b_task.id): 3}
        assert a_task.status == old_a_status and a_task.attempts == 2
        assert a_task.metadata_json == original_history
        assert b_task.attempts == 3
        assert b_task.status == ("superseded" if b_status == "pending" else "completed")
        assert case.chapter.metadata_json == chapter_ledger
        await reviews._validate_rewrite_task_parent(session, fresh, case.draft)
        await reviews._recheck_rewrite_parent(session, fresh, case.draft, parent=case.chapter)
        again = await binding_service.stage_outline_revision_rewrite_tasks(
            session, **migration_args(case)
        )
        assert not again["created"] and again["replacement_task_id"] == str(fresh.id)


@pytest.fixture(autouse=True)
def forbid_external_execution(monkeypatch):
    import socket

    from bestseller.services import llm

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Rewrite binding tests forbid PG/provider/network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(reviews, "complete_text", forbidden)


@pytest.fixture
async def db(tmp_path):
    from sqlalchemy import JSON, MetaData
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.schema import CreateTable

    from bestseller.infra.db.models import (
        ChapterDraftVersionModel,
        ChapterModel,
        ProjectModel,
        ReviewReportModel,
        RewriteTaskModel,
    )

    path = tmp_path / "rewrite_outline_binding_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    def ddl(connection):
        for model in (
            ProjectModel,
            ChapterModel,
            ChapterDraftVersionModel,
            ReviewReportModel,
            RewriteTaskModel,
        ):
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX stage_test_current "
            "ON chapter_draft_versions(chapter_id) WHERE is_current = 1"
        )

    async with engine.begin() as connection:
        await connection.run_sync(ddl)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def rewrite_runtime(monkeypatch):
    from bestseller.services import drafts, review_source_context

    band = SimpleNamespace(hard_min=1, hard_max=10000, hard_target=2000, safe_min=1, safe_max=10000)
    band.effect_checked_bodies = []

    def effects(_project, _chapter, text, **kw):
        band.effect_checked_bodies.append(text)
        return {"selected_effects": [], "passed": True, "tested_body_sha256": text_hash(text)}

    monkeypatch.setattr(reviews, "audit_persisted_story_effect_realization", effects)
    monkeypatch.setattr(reviews, "chapter_rewrite_length_band", lambda *a, **k: band)
    monkeypatch.setattr(reviews, "_rewrite_output_max_tokens_override", lambda *a, **k: 5000)
    monkeypatch.setattr(drafts, "_prepare_quality_uplift_prompt_blocks", AsyncMock())
    monkeypatch.setattr(
        reviews, "_try_localized_chapter_first_opening_repair", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        reviews, "_try_localized_chapter_first_ending_repair", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(reviews, "_clean_generated_chapter_text", lambda text, **kw: (text, {}))
    monkeypatch.setattr(
        review_source_context, "load_review_source_context", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        review_source_context,
        "prepare_source_complete_prompt",
        lambda **kw: (kw["system_prompt"], kw["user_prompt"], {}),
    )
    context = SimpleNamespace(
        previous_scene_summaries=[],
        recent_timeline_events=[],
        chapter_scenes=[],
        retrieval_chunks=[],
    )
    monkeypatch.setattr(reviews, "build_chapter_writer_context", AsyncMock(return_value=context))
    return band
