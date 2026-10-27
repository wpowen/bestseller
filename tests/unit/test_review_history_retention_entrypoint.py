"""Task history retention through the real main review entry and SQLite readback.

Primary responses are inert typed fixtures. Task retirement, ORM selection,
report/quality persistence and physical-response validation are not stubbed.
These are integration contracts, not provider or literary acceptance evidence.
"""

from copy import deepcopy
import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select
from test_chapter_review_commercial_checkpoint import review_db as review_db
from test_primary_review_submission_entrypoint import _tool_completion
from test_primary_review_submission_entrypoint import primary_db as primary_db
from test_review_task_supersession import _task

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    LlmRunModel,
    QualityScoreModel,
    ReviewReportModel,
    RewriteTaskModel,
    SceneCardModel,
)
from bestseller.services import reviews

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def fields(row):
    return deepcopy({column.key: getattr(row, column.key)
                     for column in inspect(type(row)).column_attrs})


async def snapshots(db, tasks, report):
    async with db.factory() as reader:
        return {
            "tasks": {row.id: fields(await reader.get(RewriteTaskModel, row.id)) for row in tasks},
            "report": fields(await reader.get(ReviewReportModel, report.id)),
            "draft": fields(await reader.get(ChapterDraftVersionModel, db.draft.id)),
            "scenes": [fields(s) for s in await reader.scalars(select(SceneCardModel).where(
                SceneCardModel.chapter_id == db.chapter.id,
            ).order_by(SceneCardModel.scene_number))],
        }


async def seed_history(db, *, total):
    old_report = ReviewReportModel(
        project_id=db.project.id, target_type="chapter", target_id=db.chapter.id,
        reviewer_type="chapter_quality_v1", verdict="rewrite",
        structured_output={
            "draft_id": str(db.draft.id),
            "untrusted_critic_response": "FULL OLD REJECTED RAW RESPONSE",
            "scores": {"overall": .42},
            "source_receipt": {"original_hash": "unchanged", "old_protocol": "retained"},
        },
    )
    other_chapter = ChapterModel(project_id=db.project.id, chapter_number=3,
                                 chapter_goal="Unrelated chronicle chapter", metadata_json={})
    db.session.add_all([old_report, other_chapter])
    await db.session.flush()
    completed = _task(db, old_report, status="completed")
    db.session.add(completed)
    await db.session.flush()
    automatic = _task(db, old_report)
    queued = _task(db, old_report, status="queued")
    manual = _task(db, old_report, trigger="manual")
    unbound = _task(db, old_report)
    unbound.metadata_json = {**unbound.metadata_json, "review_report_id": str(uuid4())}
    unrelated = _task(db, old_report, chapter_id=other_chapter.id)
    automatic.parent_task_id = completed.id
    tasks = [automatic, queued, manual, unbound, unrelated, completed]
    db.session.add_all(tasks)
    db.chapter.metadata_json = {
        **db.chapter.metadata_json,
        "chapter_review_attempts_active": total,
        "chapter_review_active_cycle": {"attempts_base": 4, "keep_source_cycle": "original"},
        "previous_paid_history": {"attempts": 17, "raw": "KEEP ALL OLD RAW"},
    }
    db.settings.pipeline.chapter_auto_repair_max_attempts = 3
    await db.session.commit()
    return tasks, old_report


@pytest.mark.parametrize("case", ["budget", "new_rewrite", "pass", "unknown"])
async def test_main_review_retains_old_history_across_all_retirement_branches(
    primary_db, monkeypatch, case,
):
    db = primary_db
    assert db.session.bind.dialect.name == "sqlite"
    total = 7 if case in {"budget", "unknown"} else 5
    tasks, old_report = await seed_history(db, total=total)
    automatic, queued, manual, unbound, _unrelated, _completed = tasks
    before = await snapshots(db, tasks, old_report)
    primary = _tool_completion()
    submitted = json.loads(primary.tool_calls[0]["function"]["arguments"])
    submitted["verdict"] = "pass" if case == "pass" else "rewrite"
    if case == "pass":
        submitted["rewrite_direction"] = ""
    if case == "unknown":
        submitted["citations"][0]["paragraph_number"] = 999
    primary.tool_calls[0]["function"]["arguments"] = json.dumps(submitted, ensure_ascii=False)
    primary.raw_message["tool_calls"] = deepcopy(primary.tool_calls)
    physical = deepcopy(vars(primary))
    requests, runs = [], []

    async def completion(session, settings, request):
        assert session is db.session
        requests.append(request)
        run = LlmRunModel(
            project_id=db.project.id, logical_role="critic", provider=primary.provider,
            model_name=primary.model_name, prompt_template=request.prompt_template,
            prompt_version=request.prompt_version, finish_reason=primary.finish_reason,
            metadata_json={"fixture_physical_completion": physical},
        )
        session.add(run)
        await session.flush()
        primary.llm_run_id = run.id
        runs.append(run.id)
        return primary

    monkeypatch.setattr(reviews, "complete_text", completion)
    result, report, quality, new_task = await reviews.review_chapter_draft(
        db.session, db.settings, db.project.slug, 2,
    )
    await db.session.commit()
    assert len(requests) == len(runs) == 1
    after = await snapshots(db, tasks, old_report)
    assert after["report"] == before["report"]
    assert after["draft"] == before["draft"]
    assert after["scenes"] == before["scenes"]
    changed = ({automatic.id, queued.id} if case in {"budget", "new_rewrite"}
               else {automatic.id, queued.id, manual.id, unbound.id} if case == "pass" else set())
    expected_status = "cancelled" if case == "budget" else "superseded"
    expected_reason = {
        "budget": "chapter_review_budget_exhausted",
        "new_rewrite": "replaced_by_current_verified_review",
        "pass": "current_chapter_review_passed",
    }.get(case)
    for task in tasks:
        original, saved = before["tasks"][task.id], after["tasks"][task.id]
        if task.id not in changed:
            assert saved == original
            continue
        assert saved["status"] == expected_status
        for key in original.keys() - {"status", "metadata_json", "updated_at"}:
            assert saved[key] == original[key]
        old_meta, new_meta = original["metadata_json"], saved["metadata_json"]
        assert {k: v for k, v in new_meta.items() if k != "supersession_receipts"} == {
            k: v for k, v in old_meta.items() if k != "supersession_receipts"
        }
        assert new_meta["supersession_receipts"][:-1] == old_meta["supersession_receipts"]
        receipt = new_meta["supersession_receipts"][-1]
        assert receipt["reason"] == expected_reason and receipt["disposition"] == expected_status
        assert receipt["project_id"] == str(db.project.id)
        assert receipt["chapter_id"] == str(db.chapter.id)
        assert receipt["report_id"] == str(report.id) and receipt["draft_id"] == str(db.draft.id)
        assert receipt["body_sha256"] == hashlib.sha256(db.draft.content_md.encode()).hexdigest()
        assert receipt["details"] == ({"attempts_used": 3, "budget": 3} if case == "budget" else {})
    async with db.factory() as reader:
        chapter = await reader.get(ChapterModel, db.chapter.id)
        stored = await reader.get(ReviewReportModel, report.id)
        stored_quality = await reader.get(QualityScoreModel, quality.id)
        run = await reader.get(LlmRunModel, runs[0])
        data = stored.structured_output
        assert stored.llm_run_id == runs[0]
        assert stored_quality.review_report_id == report.id
        assert data["draft_id"] == str(db.draft.id)
        assert data["reviewed_body_fingerprint"]["digest"] == hashlib.sha256(
            db.draft.content_md.encode()
        ).hexdigest()
        assert data["primary_submission_original"] == {
            "content": physical["content"], "tool_calls": physical["tool_calls"],
            "raw_message": physical["raw_message"], "finish_reason": physical["finish_reason"],
            "llm_run_id": str(runs[0]),
        }
        assert run.metadata_json == {"fixture_physical_completion": physical}
        assert chapter.metadata_json["previous_paid_history"] == {
            "attempts": 17, "raw": "KEEP ALL OLD RAW",
        }
        assert chapter.metadata_json["chapter_review_active_cycle"] == {
            "attempts_base": 4, "keep_source_cycle": "original",
        }
        if case == "new_rewrite":
            assert result.verdict == "rewrite" and new_task is not None
            current_task = await reader.get(RewriteTaskModel, new_task.id)
            assert current_task.status == "pending"
            assert current_task.id not in {t.id for t in tasks}
            assert current_task.metadata_json["review_report_id"] == str(report.id)
            assert current_task.metadata_json["attempt_index"] == 2
            assert "supersession_receipts" not in current_task.metadata_json
            assert chapter.metadata_json["chapter_review_attempts_active"] == 6
        else:
            assert new_task is None
            if case == "pass":
                assert result.verdict == "pass" and chapter.production_state == "quality_reviewed"
                assert "chapter_review_attempts_active" not in chapter.metadata_json
            else:
                assert chapter.metadata_json["chapter_review_attempts_active"] == total
                assert chapter.production_state == ("repair_exhausted" if case == "budget"
                                                    else "review_unknown")
        assert data["critic_evidence"]["passed"] is (case != "unknown")
        if case == "unknown":
            assert result.verdict == "unknown"
            assert changed == set()
