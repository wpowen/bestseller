"""Real SQLite retirement preserves complete original task history."""

from copy import deepcopy
import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from test_chapter_review_commercial_checkpoint import review_db  # noqa: F401

from bestseller.infra.db.models import ReviewReportModel, RewriteTaskModel
from bestseller.services.review_task_supersession import supersede_review_tasks_preserving_history

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def _reports(db):
    old = ReviewReportModel(
        project_id=db.project.id,
        target_type="chapter",
        target_id=db.chapter.id,
        reviewer_type="chapter_quality_v1",
        verdict="rewrite",
        structured_output={"draft_id": str(db.draft.id)},
    )
    new = ReviewReportModel(
        project_id=db.project.id,
        target_type="chapter",
        target_id=db.chapter.id,
        reviewer_type="chapter_quality_v1",
        verdict="pass",
        structured_output={"draft_id": str(db.draft.id)},
    )
    db.session.add_all([old, new])
    await db.session.flush()
    return old, new


def _task(
    db, source, *, status="pending", trigger="chapter_review", project_id=None, chapter_id=None
):
    return RewriteTaskModel(
        project_id=project_id or db.project.id,
        trigger_type=trigger,
        trigger_source_id=chapter_id or db.chapter.id,
        rewrite_strategy="chapter_coherence_bridge_rewrite",
        status=status,
        instructions="FULL ORIGINAL INSTRUCTIONS",
        attempts=4,
        error_log="FULL ORIGINAL ERROR",
        context_required=["all_original_context"],
        metadata_json={
            "chapter_id": str(chapter_id or db.chapter.id),
            "review_report_id": str(source.id),
            "draft_id": str(db.draft.id),
            "parent_draft_id": str(db.draft.id),
            "parent_draft_hash": hashlib.sha256(db.draft.content_md.encode()).hexdigest(),
            "raw_candidate_receipts": [{"body": "FULL REJECTED BODY", "audit": {"kept": True}}],
            "knowledge_receipt": {"unchanged": True},
            "attempt_budget": 5,
            "superseded_reason": "preserve prior reason",
            "supersession_receipts": [{"old": "raw history"}],
        },
    )


@pytest.mark.parametrize("disposition", ["superseded", "cancelled"])
async def test_retirement_preserves_every_original_field_and_deduplicates_without_autoflush(
    review_db,  # noqa: F811
    disposition,
):
    db = review_db
    old, new = await _reports(db)
    parent = _task(db, old, status="completed")
    db.session.add(parent)
    await db.session.flush()
    task = _task(db, old, status="queued")
    task.parent_task_id = parent.id
    db.session.add(task)
    await db.session.commit()
    before = deepcopy(task.metadata_json)
    details = {"attempts_used": 4, "budget": 5}
    flushes = []

    def listener(*_args):
        flushes.append(True)

    event.listen(db.session.sync_session, "before_flush", listener)
    try:
        result = await supersede_review_tasks_preserving_history(
            db.session,
            project_id=db.project.id,
            chapter_id=db.chapter.id,
            report_id=new.id,
            draft_id=db.draft.id,
            disposition=disposition,
            reason="current_review_replaces_old_task",
            details=details,
        )
        assert result["changed_count"] == 1
        repeated = await supersede_review_tasks_preserving_history(
            db.session,
            project_id=db.project.id,
            chapter_id=db.chapter.id,
            report_id=new.id,
            draft_id=db.draft.id,
            disposition=disposition,
            reason="current_review_replaces_old_task",
            details=details,
        )
        assert repeated["changed_count"] == 0
        assert flushes == []
    finally:
        event.remove(db.session.sync_session, "before_flush", listener)
    assert details == {"attempts_used": 4, "budget": 5}
    await db.session.commit()
    db.session.expire_all()
    # Read through SQL after expiration, without relying on the identity map.
    rows = list((await db.session.scalars(select(RewriteTaskModel))).all())
    saved = next(row for row in rows if row.status == disposition)
    assert saved.instructions == "FULL ORIGINAL INSTRUCTIONS"
    assert saved.attempts == 4 and saved.error_log == "FULL ORIGINAL ERROR"
    assert saved.context_required == ["all_original_context"]
    assert saved.parent_task_id is not None
    for key, value in before.items():
        if key != "supersession_receipts":
            assert saved.metadata_json[key] == value
    history = saved.metadata_json["supersession_receipts"]
    assert history[:-1] == before["supersession_receipts"]
    assert history[-1] == result["receipt"]
    assert history[-1]["details"] == details
    assert history[-1]["body_sha256"]


async def test_automatic_only_requires_original_producer_and_real_report_binding(review_db):  # noqa: F811
    db = review_db
    old, new = await _reports(db)
    auto = _task(db, old)
    manual = _task(db, old, trigger="manual")
    unbound = _task(db, old)
    unbound.metadata_json["review_report_id"] = str(uuid4())
    unrelated = _task(db, old, chapter_id=uuid4())
    foreign = _task(db, old, project_id=uuid4())
    excluded = _task(db, old)
    finished = _task(db, old, status="completed")
    all_tasks = [auto, manual, unbound, unrelated, foreign, excluded, finished]
    db.session.add_all(all_tasks)
    await db.session.flush()
    snapshots = {row.id: (row.status, deepcopy(row.metadata_json)) for row in all_tasks}
    result = await supersede_review_tasks_preserving_history(
        db.session,
        project_id=db.project.id,
        chapter_id=db.chapter.id,
        report_id=new.id,
        draft_id=db.draft.id,
        except_task_id=excluded.id,
        automatic_only=True,
        disposition="cancelled",
        reason="chapter_review_budget_exhausted",
        details={"attempts_used": 4, "budget": 4},
    )
    assert result["changed_task_ids"] == [str(auto.id)]
    for row in all_tasks[1:]:
        assert (row.status, row.metadata_json) == snapshots[row.id]
    assert set(result["skipped_task_ids"]) == {str(manual.id), str(unbound.id)}
    await db.session.flush()
    again = await supersede_review_tasks_preserving_history(
        db.session,
        project_id=db.project.id,
        chapter_id=db.chapter.id,
        report_id=new.id,
        draft_id=db.draft.id,
        except_task_id=excluded.id,
        automatic_only=True,
        disposition="cancelled",
    )
    assert again["changed_count"] == 0


async def test_pass_mode_preserves_existing_all_pending_behavior_including_manual(review_db):  # noqa: F811
    db = review_db
    old, new = await _reports(db)
    rows = [_task(db, old, trigger="manual"), _task(db, old, status="queued")]
    db.session.add_all(rows)
    await db.session.flush()
    result = await supersede_review_tasks_preserving_history(
        db.session,
        project_id=db.project.id,
        chapter_id=db.chapter.id,
        report_id=new.id,
        draft_id=db.draft.id,
    )
    assert result["changed_count"] == 2
    assert all(row.status == "superseded" for row in rows)


async def test_wrong_target_or_malformed_history_changes_nothing(review_db):  # noqa: F811
    db = review_db
    old, new = await _reports(db)
    task = _task(db, old)
    task.metadata_json["supersession_receipts"] = {"malformed_but_preserve": "raw"}
    db.session.add(task)
    await db.session.flush()
    before = deepcopy(task.metadata_json)
    with pytest.raises(ValueError, match="binding_invalid"):
        await supersede_review_tasks_preserving_history(
            db.session,
            project_id=db.project.id,
            chapter_id=uuid4(),
            report_id=new.id,
            draft_id=db.draft.id,
        )
    with pytest.raises(ValueError, match="history_not_list"):
        await supersede_review_tasks_preserving_history(
            db.session,
            project_id=db.project.id,
            chapter_id=db.chapter.id,
            report_id=new.id,
            draft_id=db.draft.id,
        )
    assert task.status == "pending" and task.metadata_json == before
