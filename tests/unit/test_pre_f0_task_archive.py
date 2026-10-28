"""Self-consistent forged archive rejection; no synthetic authority is accepted."""

# ruff: noqa: F811 -- imported pytest fixture is injected by name
from copy import deepcopy

import pytest
from test_chapter_review_commercial_checkpoint import review_db  # noqa: F401
from test_review_task_supersession import _reports, _task

from bestseller.infra.db import models as m
from bestseller.services import pipelines
from bestseller.services import review_source_ordering as service
from bestseller.services.book_closure import settle_outstanding_rewrite_tasks
from bestseller.services.review_task_supersession import supersede_review_tasks_preserving_history

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def archive_fixture(db):
    old, new = await _reports(db)
    task = _task(db, old)
    current = _task(db, new)
    db.session.add_all([task, current])
    await db.session.commit()
    body = pipelines.chapter_review_body_fingerprint(db.draft.content_md)["digest"]
    cycle = {
        "source_draft_id": str(db.draft.id),
        "source_body_hash": body,
        "attempts": [{"review_report_id": str(old.id), "rewrite_task_id": str(task.id)}],
    }
    receipt = {
        "schema": service.SCHEMA,
        "status": "superseded",
        "code": service.CODE,
        "project_id": str(db.project.id),
        "chapter_id": str(db.chapter.id),
        "source_cycle_id": "a" * 64,
        "source_cycle_sha256": service.recorded._hash(cycle),
        "draft_id": str(db.draft.id),
        "body_sha256": body,
        "review_report_id": str(old.id),
        "rewrite_task_id": str(task.id),
        "review_report_sha256": service._row_hash(m.ReviewReportModel, old),
        "rewrite_task_sha256": service._row_hash(m.RewriteTaskModel, task),
        **service.DENIALS,
    }
    receipt["receipt_sha256"] = service.recorded._hash(receipt)
    db.chapter.metadata_json = {
        **(db.chapter.metadata_json or {}),
        "chapter_critic_cycles": {"a" * 64: cycle},
        service.HISTORY_KEY: [receipt],
    }
    await db.session.commit()
    return old, new, task, current


@pytest.mark.parametrize("consumer", ["readiness", "retirement", "closure", "repair"])
async def test_self_consistent_fake_archive_cannot_hide_pending_tasks(review_db, consumer):
    db = review_db
    _, new, old, current = await archive_fixture(db)
    before = [service._row_hash(m.RewriteTaskModel, row) for row in (old, current)]
    from bestseller.services.repair import _load_pending_rewrite_tasks

    with pytest.raises(service.ReviewSourceOrderingError) as caught:
        if consumer == "readiness":
            await pipelines._count_pending_chapter_rewrite_tasks(
                db.session, project_id=db.project.id, chapter_id=db.chapter.id
            )
        elif consumer == "retirement":
            await supersede_review_tasks_preserving_history(
                db.session,
                project_id=db.project.id,
                chapter_id=db.chapter.id,
                report_id=new.id,
                draft_id=db.draft.id,
            )
        elif consumer == "closure":
            await settle_outstanding_rewrite_tasks(
                db.session, project_id=db.project.id, reason="fixture"
            )
        else:
            await _load_pending_rewrite_tasks(db.session, project_id=db.project.id, limit=1)
    assert caught.value.differences[0]["field"] == "archived_tasks.cycle.cycle_id"
    assert [service._row_hash(m.RewriteTaskModel, row) for row in (old, current)] == before
    assert old.status == current.status == "pending"


@pytest.mark.parametrize("fault", ["cycle", "task", "receipt"])
async def test_archive_forgery_or_native_history_drift_has_field_differences(review_db, fault):
    db = review_db
    _, _, old, _ = await archive_fixture(db)
    if fault == "task":
        old.instructions += " tampered"
    else:
        metadata = deepcopy(db.chapter.metadata_json)
        if fault == "cycle":
            metadata["chapter_critic_cycles"]["a" * 64]["source_body_hash"] = "b" * 64
        else:
            metadata[service.HISTORY_KEY][0]["rewrite_task_id"] = "wrong"
        db.chapter.metadata_json = metadata
    await db.session.commit()
    with pytest.raises(service.ReviewSourceOrderingError) as caught:
        await service.archived_rewrite_task_ids(
            db.session, project_id=db.project.id, chapter_id=db.chapter.id
        )
    assert caught.value.differences
    assert all(set(d) == {"field", "expected", "actual"} for d in caught.value.differences)
