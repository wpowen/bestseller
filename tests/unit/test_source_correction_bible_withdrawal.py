"""Real SQLite withdrawal of an approved, retained Bible source; no paid calls.

Only the existing closure seed may install deterministic extraction stubs. The
admission, source chooser, correction, projection and progress readers are real.
"""

# Chinese source observations intentionally use fullwidth punctuation.
# ruff: noqa: RUF001

from copy import deepcopy
import socket
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.schema import CreateTable
from test_adopted_bible_lineage import payload
from test_adopted_source_correction_sqlite import BODY, EVIDENCE, approve, inventory, row_data
import test_bible_projection_closure as closure
from test_bible_projection_closure import db as closure_db  # noqa: F401
from test_bible_projection_closure import knowledge_db as knowledge_db
from test_knowledge_downstream_source_dependencies import _next_chapter

from bestseller.infra.db.models import (
    CanonFactModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterStateSnapshotModel,
    CharacterModel,
    LlmRunModel,
    ProjectModel,
    QualityScoreModel,
    RelationshipModel,
    ReviewReportModel,
    TimelineEventModel,
    WorkflowRunModel,
    WorldRuleModel,
)
from bestseller.services import (
    adopted_source_correction as correction,
)
from bestseller.services import (
    continuity,
    execution_windows,
    feedback,
    llm,
)
from bestseller.services import (
    story_bible_projection as projection,
)
from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
from bestseller.services.creation_configuration import ExecutionWindow
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.rewrite_patch import text_hash
from bestseller.web.server import _load_adopted_chapter_progress

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

BIBLE_MODELS = (CharacterModel, RelationshipModel, WorldRuleModel)
ARCHIVES = (
    LlmRunModel,
    QualityScoreModel,
    ReviewReportModel,
    ChapterStateSnapshotModel,
    CanonFactModel,
    TimelineEventModel,
)
ALL_MODELS = (
    ProjectModel,
    ChapterModel,
    ChapterDraftVersionModel,
    WorkflowRunModel,
    *BIBLE_MODELS,
    *ARCHIVES,
)
PREMIUM_FIELDS = (projection.PREMIUM_VIEW, "premium_state_snapshot", "premium_state_ledger_report")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("BESTSELLER_LINEAGE_PG", "0")
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("forbidden network/provider attempt")
        raise AssertionError("Bible withdrawal forbids every network/paid gateway call")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(continuity, "complete_text", forbidden)
    monkeypatch.setattr(feedback, "complete_text", forbidden)
    yield forbidden
    assert calls == []


@pytest.fixture
async def db(closure_db):  # noqa: F811
    session = closure_db[0]
    assert session.bind.dialect.name == "sqlite"
    assert "test" in session.bind.engine.url.database
    for model in (WorkflowRunModel, LlmRunModel):
        ddl = str(CreateTable(model.__table__).compile(dialect=session.bind.dialect))
        await session.execute(text(ddl.replace("::jsonb", "")))
    await session.commit()
    return closure_db


async def _bible_rows(session, project_id):
    return [
        row
        for model in BIBLE_MODELS
        for row in await session.scalars(
            select(model)
            .where(model.project_id == project_id)
            .execution_options(populate_existing=True)
        )
    ]


async def _derived(session, project):
    """Only actual derived fields, never contribution logs or baselines."""
    return {
        "entities": {
            str(row.id): projection._fields(row) for row in await _bible_rows(session, project.id)
        },
        "premium": {key: deepcopy(project.metadata_json[key]) for key in PREMIUM_FIELDS},
    }


async def _restore_derived(session, project, saved):
    # Deliberately emulate a committed old-8Y cache *after* current real apply.
    # No block, candidate, raw contribution, baseline, grade or proposal changes.
    for row in await _bible_rows(session, project.id):
        projection._apply_fields(row, deepcopy(saved["entities"][str(row.id)]))
    project.metadata_json = {**project.metadata_json, **deepcopy(saved["premium"])}
    await session.commit()


async def _raw(session, books):
    rows = await _bible_rows(session, books[0][1].id)
    return {
        "archives": await inventory(session, ARCHIVES),
        "bible_receipts": {
            str(book[2].id): deepcopy(book[2].metadata_json["story_bible_update_receipts"])
            for book in books
        },
        "entity_logs": {
            str(row.id): {
                "baseline": deepcopy(row.metadata_json.get(projection.BASE)),
                "history": deepcopy(
                    row.metadata_json.get(
                        "events"
                        if isinstance(row, RelationshipModel)
                        else "story_bible_update_history",
                        [],
                    )
                ),
            }
            for row in rows
        },
        "raw_premium": deepcopy(books[0][1].metadata_json["premium_state_ledger"]),
    }


async def _seed(db, monkeypatch, offline):
    session, project, chapter, _ = db
    await closure.seed(db, monkeypatch)
    first_derived = await _derived(session, project)
    second = await _next_chapter(db, BODY)
    source = second[3]
    source.version_no = 9
    # Inert historical V9 artifacts, not freshly manufactured candidate grades.
    paid = LlmRunModel(
        project_id=project.id,
        logical_role="chapter_critic",
        provider="archive",
        model_name="historical-only",
        input_tokens=321,
        output_tokens=123,
        metadata_json={"raw_response": "RETAIN_V9_RAW_GRADE"},
    )
    session.add(paid)
    await session.flush()
    source.llm_run_id = paid.id
    source.promotion_score = 0.91
    source.promotion_metadata = {"old_grade": "RETAIN_V9_RAW_GRADE"}
    source.assembled_from_scene_draft_ids = [str(uuid4())]
    report = ReviewReportModel(
        project_id=project.id,
        target_type="chapter",
        target_id=second[2].id,
        reviewer_type="chapter",
        verdict="pass",
        llm_run_id=paid.id,
        structured_output={"draft_id": str(source.id), "raw": "RETAIN_V9_RAW_GRADE"},
    )
    session.add(report)
    await session.flush()
    session.add(
        QualityScoreModel(
            project_id=project.id,
            target_type="chapter",
            target_id=second[2].id,
            chapter_draft_version_id=source.id,
            review_report_id=report.id,
            judge_key="archive",
            score_overall=0.91,
            evidence_summary={"raw": "RETAIN_V9_RAW_GRADE"},
        )
    )
    await session.commit()
    later_payload = payload()
    later_payload["character_updates"][0].update(
        emotional_state="警惕", knowledge_gained=["铜钥匙在窗边"]
    )
    later_payload["relationship_updates"][0].update(change="决裂", affinity_direction="down")
    later_payload["world_updates"][0]["description"] = "旧钥匙已失效"
    later_payload["premium_engine_updates"]["progression_events"][0].update(
        delta=4, cause="第二章取得钥匙"
    )
    original_stub = closure.bible_stub
    with monkeypatch.context() as fixture_patch:
        fixture_patch.setattr(closure, "bible_stub", lambda mp: original_stub(mp, later_payload))
        await closure.seed(second, fixture_patch)
    # Close the fixture-only extraction window. No stub remains during apply,
    # migration, readback, repeat or rollback (including any re-extraction of Ch1).
    for module in (llm, continuity, feedback):
        monkeypatch.setattr(module, "complete_text", offline)
    chapter.status = chapter.production_state = "completed"
    second[2].status = second[2].production_state = "completed"
    foreign = ProjectModel(
        slug=f"untouched-bible-test-{uuid4().hex}",
        title="Unrelated",
        genre="test",
        target_word_count=1000,
        target_chapters=1,
        metadata_json={"authored": "leave untouched"},
    )
    session.add(foreign)
    await session.flush()
    foreign_chapter = ChapterModel(
        project_id=foreign.id,
        chapter_number=1,
        chapter_goal="unrelated",
        metadata_json={"raw": ["unrelated receipt"]},
    )
    foreign_character = CharacterModel(
        project_id=foreign.id,
        name="Other",
        knowledge_state_json={"knows": ["unrelated authored knowledge"]},
        metadata_json={"author": "unrelated"},
    )
    session.add_all([foreign_chapter, foreign_character])
    await session.commit()
    assert (await closure.read(db))["closed"]
    assert (await closure.read(second))["closed"]
    monkeypatch.setattr(execution_windows, "read_window", lambda _: ExecutionWindow(start=1, end=2))
    window = await execution_windows.window_progress(session, project)
    page = (await _load_adopted_chapter_progress(session, [project.id]))[project.id]
    assert window["knowledge_closed"] == page["knowledge"]["closed_chapter_numbers"] == [1, 2]
    old = await _derived(session, project)
    for model in BIBLE_MODELS:
        rows = [row for row in await _bible_rows(session, project.id) if isinstance(row, model)]
        assert any(
            old["entities"][str(row.id)] != first_derived["entities"][str(row.id)] for row in rows
        )
    assert (
        old["premium"]["premium_state_snapshot"]
        != first_derived["premium"]["premium_state_snapshot"]
    )
    return {
        "books": (db, second),
        "first": first_derived,
        "preimages": old,
        "raw": await _raw(session, (db, second)),
        "parent": row_data(source),
        "first_chapter": row_data(chapter),
        "foreign": [(row, row_data(row)) for row in (foreign, foreign_chapter, foreign_character)],
    }


async def _approved(fixture):
    session, project, chapter, source = fixture["books"][1]
    plan = await correction.prepare_adopted_source_correction(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        draft_id=source.id,
        body_sha256=text_hash(source.content_md),
        edits=[{"before": "放进播放机", "after": "放在播放机旁"}],
        evidence=deepcopy(EVIDENCE),
        reason="核验装带与首次拆袋的前置条件，仅提出精确小修。",
    )
    assert len(plan["source_bound_evidence"]) == 2
    assert plan["changed_ratio"] < 0.25
    approval_id = await approve(session, plan)
    await session.commit()
    return plan, approval_id


async def _apply(fixture):
    plan, approval_id = await _approved(fixture)
    session = fixture["books"][0][0]
    result = await correction.apply_adopted_source_correction(
        session, plan=plan, approval_id=approval_id
    )
    await session.commit()
    return plan, approval_id, result


async def _assert_withdrawn(fixture, plan):
    first, second = fixture["books"]
    session, project, chapter, parent = second
    assert (await closure.read(first))["closed"]
    assert row_data(first[2]) == fixture["first_chapter"]
    assert (await select_adopted_chapter_source(session, chapter=chapter, lock=False)) == (
        None,
        None,
    )
    with pytest.raises(ValueError, match="adopted_knowledge_source_changed"):
        await closure.read(second)
    changes, legacy, active, witnesses = await projection._plan(session, project.id)
    assert not legacy and set(active) == {str(first[2].id)}
    assert len(witnesses) == 2  # Blocked V9 is still known raw history.
    assert any(
        w["source_lineage"]["source_chapter_draft_version_id"] == str(parent.id)
        for w in witnesses.values()
    )
    assert all(projection._fields(row) == desired for row, desired in changes)
    current = await _derived(session, project)
    assert current["entities"] == fixture["first"]["entities"]
    assert (
        current["premium"]["premium_state_snapshot"]
        == fixture["first"]["premium"]["premium_state_snapshot"]
    )
    view = projection.validated_premium_projection(project.metadata_json)
    assert view is not None
    assert (
        view["active_ledger"]
        == fixture["first"]["premium"][projection.PREMIUM_VIEW]["active_ledger"]
    )
    assert (
        view["receipt"]["provider_calls"]
        == view["receipt"]["new_grade"]
        == view["receipt"]["new_adoption"]
        == 0
    )
    assert await _raw(session, fixture["books"]) == fixture["raw"]
    preserved = row_data(parent)
    assert not parent.is_current and parent.promotion_state == "promoted" and parent.version_no == 9
    preserved["is_current"] = fixture["parent"]["is_current"]
    preserved["promotion_metadata"].pop("source_correction_links")
    assert preserved == fixture["parent"]
    for row, before in fixture["foreign"]:
        await session.refresh(row)
        assert row_data(row) == before
    candidate = await session.get(ChapterDraftVersionModel, UUID(plan["candidate_id"]))
    assert (
        candidate.is_current
        and candidate.version_no == 10
        and candidate.promotion_state == "candidate"
    )
    assert candidate.content_md == parent.content_md.replace("放进播放机", "放在播放机旁")
    assert candidate.llm_run_id is None and candidate.promotion_score is None
    assert candidate.assembled_from_scene_draft_ids == parent.assembled_from_scene_draft_ids
    assert not list(
        await session.scalars(
            select(QualityScoreModel).where(
                QualityScoreModel.chapter_draft_version_id == candidate.id
            )
        )
    )
    receipts = chapter.metadata_json[correction.PROJECTION_HISTORY]
    receipt = receipts[-1]
    assert receipt["parent_draft_id"] == str(parent.id)
    assert receipt["parent_body_sha256"] == plan["source_body_sha256"]
    assert receipt["candidate_id"] == str(candidate.id)
    assert receipt["candidate_body_sha256"] == plan["result_body_sha256"]
    assert receipt["provider_calls"] == 0 and receipt["raw_receipts_retained"] is True
    assert (
        receipt["old_source_active"]
        is receipt["grade_asserted"]
        is receipt["adoption_authorized"]
        is False
    )
    assert set(receipt["changed_entity_ids"]) == {
        key
        for key, value in fixture["preimages"]["entities"].items()
        if value != current["entities"][key]
    }
    assert receipt["receipt_sha256"] == content_hash(
        {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    )
    # Both UI consumers must use the same real source block and remaining closure.
    before_read = await inventory(session, ALL_MODELS)
    window = await execution_windows.window_progress(session, project)
    page = (await _load_adopted_chapter_progress(session, [project.id]))[project.id]
    assert window["adopted"] == window["knowledge_closed"] == [1]
    assert window["pending"] == [2] and not window["window_complete"]
    assert page["adopted_chapters"] == page["promoted_chapters"] == 1
    assert page["candidate_chapters"] == 1
    assert page["knowledge"]["closed_chapter_numbers"] == [1]
    assert page["knowledge"]["bible"]["closed_chapters"] == 1
    assert await inventory(session, ALL_MODELS) == before_read


async def test_initial_apply_withdraws_v9_effects_keeps_ch1_closed_and_raw_histories(
    db, monkeypatch, offline
):
    fixture = await _seed(db, monkeypatch, offline)
    plan, _, _ = await _apply(fixture)
    assert len(fixture["books"][1][2].metadata_json[correction.PROJECTION_HISTORY]) == 1
    await _assert_withdrawn(fixture, plan)


async def test_committed_proposal_migration_repairs_only_preimages_and_is_idempotent(
    db, monkeypatch, offline
):
    fixture = await _seed(db, monkeypatch, offline)
    session, project, chapter, _ = fixture["books"][1]
    plan, approval_id, result = await _apply(fixture)
    await _restore_derived(session, project, fixture["preimages"])
    status = await closure.read(db)
    assert status["feedback_complete"] and status["snapshot_complete"]
    assert not status["bible_complete"] and not status["closed"]
    retained = deepcopy(chapter.metadata_json)
    before_rows = await inventory(session, (ChapterDraftVersionModel, WorkflowRunModel, *ARCHIVES))
    receipt = await correction.reconcile_adopted_source_correction_projection(
        session, plan=plan, approval_id=approval_id
    )
    await session.commit()
    assert receipt["status"] == "reprojected"
    assert (
        chapter.metadata_json[correction.PROJECTION_HISTORY][:-1]
        == retained[correction.PROJECTION_HISTORY]
    )
    assert chapter.metadata_json[correction.PROJECTION_HISTORY][-1] == receipt
    assert (
        await inventory(session, (ChapterDraftVersionModel, WorkflowRunModel, *ARCHIVES))
        == before_rows
    )
    await _assert_withdrawn(fixture, plan)
    stable = await inventory(session, ALL_MODELS)
    assert (
        await correction.reconcile_adopted_source_correction_projection(
            session, plan=plan, approval_id=approval_id
        )
        == receipt
    )
    assert (
        await correction.apply_adopted_source_correction(
            session, plan=plan, approval_id=approval_id
        )
        == result
    )
    await session.commit()
    assert await inventory(session, ALL_MODELS) == stable


async def test_migration_and_initial_apply_obey_caller_rollback(db, monkeypatch, offline):
    fixture = await _seed(db, monkeypatch, offline)
    session, project, _, _ = fixture["books"][1]
    plan, approval_id = await _approved(fixture)
    before = await inventory(session, ALL_MODELS)
    await correction.apply_adopted_source_correction(session, plan=plan, approval_id=approval_id)
    assert await inventory(session, ALL_MODELS) != before
    await session.rollback()
    assert await inventory(session, ALL_MODELS) == before
    await correction.apply_adopted_source_correction(session, plan=plan, approval_id=approval_id)
    await session.commit()
    await _restore_derived(session, project, fixture["preimages"])
    stale = await inventory(session, ALL_MODELS)
    await correction.reconcile_adopted_source_correction_projection(
        session, plan=plan, approval_id=approval_id
    )
    assert (await closure.read(db))["closed"]
    await session.rollback()
    assert await inventory(session, ALL_MODELS) == stale
    assert not (await closure.read(db))["closed"]
    await correction.reconcile_adopted_source_correction_projection(
        session, plan=plan, approval_id=approval_id
    )
    await session.commit()
    await _assert_withdrawn(fixture, plan)


@pytest.mark.parametrize(
    "fault", ["missing_without_block", "latest_hash_mismatch", "ambiguous_promoted"]
)
async def test_unknown_source_never_silently_skips_retained_history(
    db, monkeypatch, offline, fault
):
    fixture = await _seed(db, monkeypatch, offline)
    session, project, chapter, source = fixture["books"][1]
    if fault == "missing_without_block":
        source.promotion_state, source.is_current = "superseded", False
    else:
        plan, approval_id, _ = await _apply(fixture)
        if fault == "latest_hash_mismatch":
            metadata = deepcopy(chapter.metadata_json)
            metadata["story_bible_update_receipts"][-1]["source_lineage"]["source_body_sha256"] = (
                "0" * 64
            )
            chapter.metadata_json = metadata
        else:
            session.add(
                ChapterDraftVersionModel(
                    project_id=project.id,
                    chapter_id=chapter.id,
                    version_no=8,
                    content_md="Ambiguous retained promotion",
                    promotion_state="promoted",
                    is_current=False,
                    promotion_metadata={},
                )
            )
    await session.commit()
    before = await inventory(session, ALL_MODELS)
    with pytest.raises(projection.BibleProjectionAttributionError, match="attribution_incomplete"):
        await projection._plan(session, project.id)
    first_lineage = fixture["books"][0][2].metadata_json["story_bible_update_receipts"][-1][
        "source_lineage"
    ]
    assert (
        await projection.adopted_bible_projection_status(
            session, project_id=project.id, source_lineage=first_lineage
        )
        == "unknown"
    )
    status = await closure.read(db)
    assert status["feedback_complete"] and status["snapshot_complete"]
    assert not status["bible_complete"] and not status["closed"]
    if fault != "missing_without_block":
        with pytest.raises(projection.BibleProjectionAttributionError):
            await correction.reconcile_adopted_source_correction_projection(
                session, plan=plan, approval_id=approval_id
            )
    assert await inventory(session, ALL_MODELS) == before
