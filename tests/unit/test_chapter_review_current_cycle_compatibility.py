"""Current automatic-cycle aliases never restore an older recovery pointer.

Real SQLite reconciliation, row locks/queries and retained report binding are
used. Source AST/effective-request proof boundaries use the existing controlled
migration fixture; this suite does not claim physical production acceptance.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_chapter_review_reconciliation import migration  # noqa: F401

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    QualityScoreModel,
    ReviewReportModel,
    RewriteTaskModel,
    WorkflowRunModel,
)
from bestseller.services import chapter_review_reconciliation as reconciliation
from bestseller.services import pipelines

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def current_cycle(migration):  # noqa: F811 - imported pytest fixture
    rig = migration
    rig.wrong.is_current = False
    await rig.session.flush()
    rig.source.is_current = True
    rig.source.version_no = 20
    rig.wrong.version_no = 16
    rig.chapter.current_word_count = rig.source.word_count
    cycle = deepcopy(rig.original_cycle)
    cycle_id = pipelines._chapter_critic_cycle_id(
        rig.project.id,
        rig.chapter.id,
        cycle["source_body_hash"],
    )
    cycle["cycle_id"] = cycle_id
    active = deepcopy(rig.original_cycle)
    active.update(
        cycle_id="retained-recovery-v16",
        source_draft_id=str(rig.wrong.id),
        source_body_hash=pipelines.chapter_review_body_fingerprint(rig.wrong.content_md)["digest"],
        retained_marker={"attempts_consumed": 2, "preserve": "exact bytes in JSON"},
    )
    rig.chapter.updated_at = datetime(2020, 1, 1)  # Force the real mutation audit timestamp edge.
    rig.chapter.metadata_json = {
        "chapter_review_active_cycle": active,
        "chapter_critic_cycles": {cycle_id: cycle, active["cycle_id"]: deepcopy(active)},
        "chapter_first_resume_existing_draft_id": str(rig.wrong.id),
        "protected_runtime": {"keep": [1, 2, 3]},
    }
    await rig.session.commit()
    rig.cycle_id = cycle_id
    return rig


async def _apply(rig, **changes):
    kwargs = {
        "project_id": rig.project.id,
        "chapter_id": rig.chapter.id,
        "expected_cycle_id": rig.cycle_id,
        "expected_current_draft_id": rig.source.id,
        "previous_pipeline_source": b"trusted snapshot",
        "actor": "authorized test repair",
        "evidence_ref": "executor-only-compatible-current-cycle",
        "allow_current_automatic_cycle": True,
    }
    kwargs.update(changes)
    return await reconciliation.reconcile_retained_chapter_review(
        rig.session,
        SimpleNamespace(),
        **kwargs,
    )


async def _rows(session):
    result = {}
    for model in (
        ChapterModel,
        ChapterDraftVersionModel,
        ReviewReportModel,
        QualityScoreModel,
        RewriteTaskModel,
        WorkflowRunModel,
    ):
        rows = list(await session.scalars(select(model).execution_options(populate_existing=True)))
        result[model.__tablename__] = {
            str(row.id): deepcopy(
                {attr.key: getattr(row, attr.key) for attr in inspect(model).column_attrs}
            )
            for row in rows
        }
    return result


async def test_explicit_current_cycle_alias_is_durable_idempotent_and_changes_only_target_cycle(
    current_cycle,
    monkeypatch,
):
    rig = current_cycle
    before = await _rows(rig.session)
    inputs = []

    async def physical_proof(session, settings, **kwargs):
        inputs.append((kwargs["draft"].id, kwargs["report"].id, deepcopy(kwargs["cycle"])))
        return {"synthetic_no_provider": True, "effective_input_unchanged": True}

    monkeypatch.setattr(
        sys.modules["bestseller.services.retained_review_input_proof"],
        "verify_retained_review_inputs",
        physical_proof,
    )
    first = await _apply(rig)
    assert first["working_pointer_changed"] is False
    assert first["active_cycle_unchanged"] is True
    assert first["attempts_unchanged"] is True and first["adoption_created"] is False
    assert rig.session.in_transaction()  # Caller still owns commit.
    await rig.session.commit()
    factory = async_sessionmaker(rig.session.bind, expire_on_commit=False)
    async with factory() as observer:
        after = await _rows(observer)
    first_snapshot = deepcopy(after)
    old_chapter = before["chapters"][str(rig.chapter.id)]
    chapter = after["chapters"][str(rig.chapter.id)]
    metadata = deepcopy(chapter["metadata_json"])
    new_cycle = metadata["chapter_critic_cycles"][rig.cycle_id]
    assert new_cycle["policy_source_hash"] == "current-policy"
    assert len(new_cycle["candidate_selection_reconciliations"]) == 1
    assert (
        new_cycle["attempts"]
        == old_chapter["metadata_json"]["chapter_critic_cycles"][rig.cycle_id]["attempts"]
    )
    metadata["chapter_critic_cycles"][rig.cycle_id] = deepcopy(
        old_chapter["metadata_json"]["chapter_critic_cycles"][rig.cycle_id]
    )
    assert metadata == old_chapter["metadata_json"]
    chapter["metadata_json"] = deepcopy(old_chapter["metadata_json"])
    # A real metadata update advances the chapter audit time. All business
    # fields remain exact; the second idempotent call below must not touch it.
    assert chapter["updated_at"] > old_chapter["updated_at"]
    chapter["updated_at"] = old_chapter["updated_at"]
    assert chapter == old_chapter
    after["chapters"] = before["chapters"]
    assert after == before
    second = await _apply(rig)
    await rig.session.commit()
    assert second == first
    assert len(inputs) == 2 and all(item[:2] == (rig.source.id, rig.report.id) for item in inputs)
    async with factory() as observer:
        assert await _rows(observer) == first_snapshot
    assert (
        len(
            rig.chapter.metadata_json["chapter_critic_cycles"][rig.cycle_id][
                "candidate_selection_reconciliations"
            ]
        )
        == 1
    )


async def test_default_still_requires_the_active_cycle_and_does_not_restore_old_pointer(
    current_cycle,
):
    rig = current_cycle
    before = await _rows(rig.session)
    with pytest.raises(ValueError, match="completed_cycle_required"):
        async with rig.session.begin_nested():
            await _apply(rig, allow_current_automatic_cycle=False)
    assert await _rows(rig.session) == before


@pytest.mark.parametrize(
    "invalid",
    [
        "different_draft",
        "wrong_expected_current",
        "body_changed",
        "noncompleted",
        "attempt_noncompleted",
        "unknown_outcome",
        "unknown_verdict",
        "unknown_report",
        "cycle_id",
        "noncanonical_cycle_key",
        "report_binding",
        "quality_binding",
        "semantic_changed",
        "dependency_dirty",
        "physical_input_changed",
        "policy_incompatible",
        "dependency_changes_during_validation",
        "active_writer",
        "ambiguous_adoption",
        "nonboolean_mode",
    ],
)
async def test_current_cycle_migration_fails_closed_without_changes(
    current_cycle, monkeypatch, invalid
):
    rig = current_cycle
    metadata = deepcopy(rig.chapter.metadata_json)
    cycle = metadata["chapter_critic_cycles"][rig.cycle_id]
    changes = {}
    if invalid == "different_draft":
        cycle["source_draft_id"] = str(rig.wrong.id)
    elif invalid == "wrong_expected_current":
        changes["expected_current_draft_id"] = rig.wrong.id
    elif invalid == "body_changed":
        rig.source.content_md += " Changed after review."
    elif invalid == "noncompleted":
        cycle["state"] = "running"
    elif invalid == "attempt_noncompleted":
        cycle["attempts"][-1]["state"] = "prepared"
    elif invalid == "unknown_outcome":
        cycle["attempts"][-1]["outcome"] = "unknown"
    elif invalid == "unknown_verdict":
        cycle["attempts"][-1]["review_result"]["verdict"] = "unknown"
    elif invalid == "unknown_report":
        payload = deepcopy(rig.report.structured_output)
        payload["critic_evidence"]["status"] = "unavailable"
        rig.report.structured_output = payload
    elif invalid == "cycle_id":
        cycle["cycle_id"] = "different-cycle"
    elif invalid == "noncanonical_cycle_key":
        cycle["cycle_id"] = "arbitrary-explicit-key"
        metadata["chapter_critic_cycles"][cycle["cycle_id"]] = cycle
        changes["expected_cycle_id"] = cycle["cycle_id"]
    elif invalid == "report_binding":
        rig.report.structured_output = {
            **rig.report.structured_output,
            "draft_id": str(rig.wrong.id),
        }
    elif invalid == "quality_binding":
        rig.quality.chapter_draft_version_id = rig.wrong.id
    elif invalid == "dependency_dirty":
        cycle["semantic_dependency_change_detected"] = True
    elif invalid == "semantic_changed":

        async def semantic(*args):
            return "different-data"

        monkeypatch.setattr(pipelines, "_chapter_critic_semantic_dependencies", semantic)
    elif invalid == "physical_input_changed":

        async def physical_proof(*args, **kwargs):
            raise ValueError("physical input proof rejected")

        monkeypatch.setattr(
            sys.modules["bestseller.services.retained_review_input_proof"],
            "verify_retained_review_inputs",
            physical_proof,
        )
    elif invalid == "policy_incompatible":

        def incompatible(**kwargs):
            raise ValueError("review_compatibility_review_evaluator_changed")

        monkeypatch.setattr(
            reconciliation, "candidate_selection_policy_compatibility", incompatible
        )
    elif invalid == "dependency_changes_during_validation":
        seen = []

        async def semantic(*args):
            seen.append(True)
            return "same-data" if len(seen) == 1 else "different-data"

        monkeypatch.setattr(pipelines, "_chapter_critic_semantic_dependencies", semantic)
    elif invalid == "active_writer":
        rig.session.add(
            WorkflowRunModel(
                project_id=rig.project.id,
                workflow_type="chapter_pipeline",
                scope_type="chapter",
                scope_id=rig.chapter.id,
                status="running",
                metadata_json={},
            )
        )
    elif invalid == "ambiguous_adoption":
        metadata["numeric_promotion_debt"] = {}
    elif invalid == "nonboolean_mode":
        changes["allow_current_automatic_cycle"] = "true"
    rig.chapter.metadata_json = metadata
    await rig.session.commit()
    before = await _rows(rig.session)
    with pytest.raises(ValueError):
        async with rig.session.begin_nested():
            await _apply(rig, **changes)
    assert await _rows(rig.session) == before
