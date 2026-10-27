"""Retained paid inputs stay literal while separately published views are current."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest
from sqlalchemy import select
from test_snapshot_dependency_recovery import database_image, sha
from test_snapshot_dependency_recovery import db as db
from test_snapshot_dependency_recovery import offline as offline
from test_snapshot_dependency_recovery import retained as retained

from bestseller.infra.db.models import ChapterStateSnapshotModel
from bestseller.services import continuity, snapshot_dependency_recovery

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def literal_case(retained):
    # SAVEPOINT rollback expires mutated ORM objects. Capture scalar identities
    # before fault injection, then read SQL truth without implicit attribute IO.
    retained.snapshot_ids = tuple(row.id for row in retained.rows)
    retained.chapter_ids = tuple(row.id for row in retained.chapters)
    retained.draft_ids = tuple(row.id for row in retained.drafts)
    retained.project_id = retained.project.id
    return retained


async def reconcile(data, number=2, *, dry_run=False):
    # Call production directly: the old fixture's recover helper also applies a
    # view, which could conceal a missing publication inside this operator.
    return await snapshot_dependency_recovery.reconcile_retained_snapshot_dependencies(
        data.session, data.settings, snapshot_id=data.snapshot_ids[number - 1],
        actor="literal-input-test", reason="Retain original paid evidence", dry_run=dry_run,
    )


def physical_files(data):
    return {str(path.relative_to(data.root)): path.read_bytes()
            for path in data.root.rglob("*") if path.is_file()}


def protected(image):
    return {key: value for key, value in image.items() if key != "chapter_state_snapshots"}


async def stored_row(data, number=2):
    table = ChapterStateSnapshotModel.__table__
    return deepcopy(dict((await data.session.execute(
        select(table).where(table.c.id == data.snapshot_ids[number - 1]),
    )).mappings().one()))


async def test_original_paid_input_and_new_views_remain_separate_across_three_chapters(
    literal_case,
):
    data = literal_case
    original_image = await database_image(data)
    files = physical_files(data)
    with pytest.raises(ValueError, match="predecessor_snapshot_not_current"):
        await reconcile(data, 3)
    assert await database_image(data) == original_image

    for number in (2, 3):
        previous = await continuity._current_snapshot_input(
            data.session, project_id=data.project_id, chapter_number=number - 1,
            include_source=True,
        )
        assert previous.context is not None and previous.literal_context is not None
        literal = continuity._snapshot_dependencies(number, continuity._SnapshotInput(
            previous.chapter_id, previous.source, previous.literal_context,
        ))
        admitted = continuity._snapshot_dependencies(number, previous)
        assert literal != admitted
        assert "epistemic_policy" not in literal and "epistemic_policy" in admitted
        old = await stored_row(data, number)
        request = json.loads(files[data.runs[number - 1].request_payload_ref])
        system = request["messages"]["system"]["text"]
        user = request["messages"]["user"]["text"]
        assert user == continuity._build_extraction_user_prompt(
            chapter=data.chapters[number - 1], chapter_md=data.drafts[number - 1].content_md,
            previous_snapshot=previous.literal_context,
        )
        assert user != continuity._build_extraction_user_prompt(
            chapter=data.chapters[number - 1], chapter_md=data.drafts[number - 1].content_md,
            previous_snapshot=previous.context,
        )
        contract = old["facts"]["snapshot_contract"]
        assert sha(system + "\n" + user) == contract["prompt_sha256"]
        before_preview = await database_image(data)
        preview = await reconcile(data, number, dry_run=True)
        assert preview["status"] == "proof_only"
        assert preview["proof"]["snapshot_dependencies"] == literal
        assert preview["proof"]["physical_prompt_sha256"] == contract["prompt_sha256"]
        assert await database_image(data) == before_preview

        result = await reconcile(data, number)
        assert result["status"] == "reconciled"
        assert all(result[key] == result["receipt"][key] == 0 for key in (
            "provider_calls", "new_grade", "new_adoption", "credit_released",
        ))
        row = await stored_row(data, number)
        for key in ("raw_extraction", "extraction_model", "extraction_status",
                    "time_anchor", "chapter_time_span"):
            assert row[key] == old[key]
        facts = row["facts"]
        for key in ("facts", "unknown_root", "story_engine"):
            assert facts[key] == old["facts"][key]
        assert facts["snapshot_history"][:-1] == old["facts"]["snapshot_history"]
        assert facts["snapshot_history"][-1]["facts"] == {
            key: value for key, value in old["facts"].items() if key != "snapshot_history"
        }
        current_contract = facts["snapshot_contract"]
        assert all(current_contract[key] == value for key, value in contract.items())
        assert current_contract["snapshot_dependencies"] == literal
        assert current_contract["snapshot_dependencies"] != admitted
        view = facts[continuity._EPISTEMIC_VIEW]
        application = facts[continuity._EPISTEMIC_HISTORY][-1]
        assert view["view_dependencies"]["previous_view_sha256"] == (
            previous.context.epistemic_view_sha256
        )
        assert view["view_dependencies"]["previous_application_sha256"] == (
            previous.context.epistemic_application_sha256
        )
        assert view["view_dependencies"]["previous_source"] == literal["previous_source"]
        assert view["processing_complete"] is True
        assert all(view[key] is False for key in (
            "truth_approved", "grade_granted", "credit_granted",
        ))
        assert application["provider_calls"] == 0
        assert application["grade_granted"] is application["credit_granted"] is False
        assert view["application_receipt_sha256"] == application["receipt_sha256"]
        assert await continuity.snapshot_evidence_current(
            data.session, project_id=data.project_id, chapter_number=number,
        )
        image = await database_image(data)
        assert protected(image) == protected(original_image)
        assert physical_files(data) == files
        for dry_run in (True, False):
            assert (await reconcile(data, number, dry_run=dry_run))["status"] == "already_current"
            assert await database_image(data) == image
    for number in (1, 2, 3):
        assert await continuity.snapshot_evidence_current(
            data.session, project_id=data.project_id, chapter_number=number,
        )


@pytest.mark.parametrize("stage", ["publication", "after_flush"])
async def test_publication_failure_rolls_back_the_entire_row_view_and_history(
    literal_case, monkeypatch, stage,
):
    data = literal_case
    before, row, files = await database_image(data), await stored_row(data), physical_files(data)
    original_publish = continuity._publish_epistemic_view
    original_flush = data.session.flush
    published = []

    def publish(*args, **kwargs):
        result = original_publish(*args, **kwargs)
        published.append(deepcopy(args[0].facts))
        if stage == "publication":
            raise RuntimeError("derived publication failed")
        return result

    async def flush(*args, **kwargs):
        await original_flush(*args, **kwargs)
        if published:
            persisted = await stored_row(data)
            assert continuity._EPISTEMIC_VIEW in persisted["facts"]
            assert len(persisted["facts"]["snapshot_history"]) == (
                len(row["facts"]["snapshot_history"]) + 1
            )
            raise RuntimeError("derived publication failed after flush")

    with monkeypatch.context() as patch:
        patch.setattr(continuity, "_publish_epistemic_view", publish)
        patch.setattr(data.session, "flush", flush)
        with pytest.raises(RuntimeError, match="derived publication failed"):
            await reconcile(data)
    assert len(published) == 1
    assert continuity._EPISTEMIC_VIEW in published[0]
    assert continuity._EPISTEMIC_HISTORY in published[0]
    assert await stored_row(data) == row
    assert await database_image(data) == before
    assert physical_files(data) == files
    assert (await reconcile(data))["status"] == "reconciled"
    assert protected(await database_image(data)) == protected(before)


@pytest.mark.parametrize("fault,code", [
    ("file", "retained_payload_not_replayable"),
    ("operation", "paid_operation_not_completed"),
    ("body", "snapshot_source_mismatch"),
    ("raw", "derived_snapshot_not_current"),
])
async def test_physical_evidence_is_reproved_after_the_new_view_is_flushed(
    literal_case, monkeypatch, fault, code,
):
    data = literal_case
    before, row = await database_image(data), await stored_row(data)
    original_flush = data.session.flush
    injected = []

    async def flush(*args, **kwargs):
        await original_flush(*args, **kwargs)
        if injected or continuity._EPISTEMIC_VIEW not in data.rows[1].facts:
            return
        persisted = await stored_row(data)
        assert continuity._EPISTEMIC_VIEW in persisted["facts"]
        assert len(persisted["facts"]["snapshot_history"]) == (
            len(row["facts"]["snapshot_history"]) + 1
        )
        injected.append(fault)
        if fault == "file":
            path = data.root / data.runs[1].response_payload_ref
            payload = json.loads(path.read_bytes())
            payload["replayable"] = False
            path.write_text(json.dumps(payload))
        elif fault == "operation":
            data.operations[1].status = "unknown"
        elif fault == "body":
            data.drafts[1].content_md += " changed after publication"
        else:
            data.rows[1].raw_extraction += " changed after publication"
        await original_flush()

    with monkeypatch.context() as patch:
        patch.setattr(data.session, "flush", flush)
        with pytest.raises(ValueError, match=code):
            await reconcile(data)
    assert injected == [fault]
    assert await stored_row(data) == row
    assert await database_image(data) == before


@pytest.mark.parametrize("dry_run", [True, False])
async def test_current_admitted_view_cannot_substitute_for_missing_literal_input(
    literal_case, monkeypatch, dry_run,
):
    data = literal_case
    original = continuity._current_snapshot_input
    previous = await original(
        data.session, project_id=data.project_id, chapter_number=1, include_source=True,
    )
    assert previous.context is not None and previous.literal_context is not None
    before, files = await database_image(data), physical_files(data)

    async def missing_literal(*args, **kwargs):
        actual = await original(*args, **kwargs)
        # Fault injection removes only the literal witness from an otherwise
        # actually validated SQL result; no admitted view or receipt is forged.
        return replace(actual, literal_context=None) if kwargs["chapter_number"] == 1 else actual

    with monkeypatch.context() as patch:
        patch.setattr(continuity, "_current_snapshot_input", missing_literal)
        assert await continuity.snapshot_evidence_current(
            data.session, project_id=data.project_id, chapter_number=1,
        )
        with pytest.raises(ValueError, match="predecessor_literal_input_missing"):
            await reconcile(data, dry_run=dry_run)
    assert await database_image(data) == before
    assert physical_files(data) == files
