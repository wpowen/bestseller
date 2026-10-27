"""Actual SQLite admitted snapshot views; original raw/grades/ledgers stay intact."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import event
from test_adopted_knowledge_lineage import db as db

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterStateSnapshotModel,
)
from bestseller.services import continuity, llm
from bestseller.services.feedback import validate_adopted_knowledge_source
from bestseller.services.knowledge_epistemic_admission import POLICY
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def no_models(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("snapshot epistemic tests forbid providers/models")

    for module, name in (
        (llm, "complete_text"),
        (llm, "_call_litellm"),
        (continuity, "complete_text"),
    ):
        monkeypatch.setattr(module, name, forbidden)


@pytest.fixture
async def case(db):
    session, project, chapter, draft = db
    draft.content_md = (
        "Mira said, “The observatory has been empty for seven years.”\n\n"
        "The iron gate was locked. The display read 20 hours.\n\n"
        "Jonas replied, “I saw workers there yesterday.”"
    )
    await session.commit()
    source, rejection = await validate_adopted_knowledge_source(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id,
        chapter_md=draft.content_md,
        lock=False,
    )
    assert rejection is None
    items = [
        {
            "name": "vacancy duration",
            "value": "7",
            "unit": "years",
            "kind": "elapsed_story_time",
            "subject": "observatory",
            "notes": "Mira reports this duration",
            "source_quote": "empty for seven years",
        },
        {
            "name": "gate",
            "value": "locked",
            "kind": "location",
            "source_quote": "The iron gate was locked.",
            "unknown_original": {"retain": True},
        },
    ]
    raw = json.dumps(
        {
            "facts": items,
            "time_anchor": "seven years",
            "chapter_time_span": "an invented hour",
            "whole_candidate": [None, True],
        },
        ensure_ascii=False,
    )
    row = ChapterStateSnapshotModel(
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=chapter.chapter_number,
        facts={
            "facts": deepcopy(items),
            "snapshot_contract": {
                **source,
                "is_usable": True,
                "operation_id": "original-paid-snapshot",
                "llm_run_id": str(uuid4()),
                "unknown_original": {"keep": True},
            },
            "snapshot_history": [{"old": [None, {"preserved": True}]}],
            "story_engine": {"authored_canon": "unchanged"},
        },
        raw_extraction=raw,
        extraction_model="fixture-retained-model",
        extraction_status="ok_promoted",
        time_anchor="seven years",
        chapter_time_span="an invented hour",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return SimpleNamespace(
        session=session,
        project=project,
        chapter=chapter,
        draft=draft,
        row=row,
        source=source,
        items=items,
        settings=load_settings(env={}),
    )


def args(r):
    return {
        "project_id": r.project.id,
        "chapter_id": r.chapter.id,
        "source_chapter_draft_version_id": r.draft.id,
        "chapter_md": r.draft.content_md,
    }


async def prepare(r):
    return await continuity.validate_snapshot_epistemic_view(r.session, **args(r))


async def apply(r, proof):
    return await continuity.apply_snapshot_epistemic_view(
        r.session,
        **args(r),
        expected_snapshot_sha256=proof["snapshot_sha256"],
        admission_receipt=proof["admission_receipt"],
        actor="explicit-test-operator",
        reason="Retain raw, publish same-body derived view.",
    )


async def current(r):
    return await continuity.load_previous_chapter_snapshot(
        r.session, project_id=r.project.id, current_chapter_number=r.chapter.chapter_number + 1
    )


async def test_readonly_preflight_does_not_publish_or_lock(case, monkeypatch):
    r = case
    before = continuity.snapshot_epistemic_witness(r.row)
    locks, writes = [], []

    def sql(_conn, _cursor, statement, *_args):
        if not statement.lstrip().upper().startswith("SELECT"):
            writes.append(statement)

    def orm(state):
        if getattr(state.statement, "_for_update_arg", None) is not None:
            locks.append(state.statement)

    async def forbidden(*args, **kwargs):
        raise AssertionError("read-only proof cannot flush/commit")

    monkeypatch.setattr(r.session, "flush", forbidden)
    monkeypatch.setattr(r.session, "commit", forbidden)
    event.listen(r.session.bind.sync_connection, "before_cursor_execute", sql)
    event.listen(r.session.sync_session, "do_orm_execute", orm)
    try:
        proof = await prepare(r)
        assert proof == await prepare(r)
        assert proof["snapshot_sha256"] == before
        with pytest.raises(RuntimeError, match="source_context_unavailable"):
            await current(r)
        assert not await continuity.snapshot_evidence_current(
            r.session, project_id=r.project.id, chapter_number=1
        )
        assert await continuity.snapshot_evidence_statuses(
            r.session, project_id=r.project.id, chapter_numbers=[1]
        ) == {1: False}
        assert continuity.snapshot_evidence_for_sources(
            {1: (r.chapter.id, r.source, r.row)}, source_bodies={1: r.draft.content_md}
        ) == {1: False}
        assert continuity.snapshot_epistemic_witness(r.row) == before
        assert not writes and not locks
    finally:
        event.remove(r.session.bind.sync_connection, "before_cursor_execute", sql)
        event.remove(r.session.sync_session, "do_orm_execute", orm)


async def test_apply_preserves_raw_history_authority_and_reuses_without_models(case, monkeypatch):
    r = case
    before = deepcopy(r.row.facts), r.row.raw_extraction, r.row.time_anchor, r.row.chapter_time_span
    source_before = (
        deepcopy(r.draft.promotion_metadata),
        r.draft.promotion_state,
        r.draft.content_md,
    )
    proof = await prepare(r)
    first = await apply(r, proof)
    assert first["status"] == "applied"
    assert r.session.in_transaction() and not r.session.dirty
    assert (await apply(r, proof))["status"] == "already_current"
    for key, value in before[0].items():
        assert r.row.facts[key] == value
    assert (r.row.raw_extraction, r.row.time_anchor, r.row.chapter_time_span) == before[1:]
    assert (
        r.draft.promotion_metadata,
        r.draft.promotion_state,
        r.draft.content_md,
    ) == source_before
    context = await current(r)
    assert context.epistemic_policy == POLICY
    assert [f.name for f in context.facts] == ["gate"]
    view = r.row.facts[continuity._EPISTEMIC_VIEW]
    assert view["facts"] == [r.items[1]]  # Exact original unquoted fact, including unknown fields.
    assert context.time_anchor is None and context.chapter_time_span is None
    assert context.reported_observations[0]["speaker_resolution"] == "not_inferred"
    assert "Mira said" in context.reported_observations[0]["text"]
    rendered = continuity.render_hard_fact_snapshot_block(context)
    assert "gate: locked" in rendered
    assert "vacancy duration: 7" not in rendered
    assert "未确认" in rendered and "Mira said" in rendered
    assert (
        await continuity.extract_chapter_state_snapshot(
            r.session,
            r.settings,
            project_id=r.project.id,
            chapter=r.chapter,
            chapter_md=r.draft.content_md,
            source_chapter_draft_version_id=r.draft.id,
        )
        is r.row
    )
    assert len(r.row.facts[continuity._EPISTEMIC_HISTORY]) == 1
    sources = {1: (r.chapter.id, r.source, r.row)}
    assert continuity.snapshot_evidence_for_sources(sources) == {1: False}
    assert continuity.snapshot_evidence_for_sources(
        sources, source_bodies={1: r.draft.content_md}
    ) == {1: True}
    assert continuity.snapshot_evidence_for_sources(sources, source_bodies={1: "foreign"}) == {
        1: False
    }


@pytest.mark.parametrize("fault", ["raw", "body", "item", "origin", "receipt", "history", "policy"])
async def test_stale_or_tampered_origin_and_view_cannot_authorize(case, fault):
    r = case
    proof = await prepare(r)
    if fault == "raw":
        r.row.raw_extraction += " "
    elif fault == "body":
        r.draft.content_md += " Changed source."
    elif fault in {"item", "origin"}:
        data = deepcopy(r.row.facts)
        if fault == "item":
            data["facts"][0]["value"] = "8"
        else:
            data["snapshot_contract"]["operation_id"] = "foreign-paid-operation"
        r.row.facts = data
    elif fault == "receipt":
        proof["admission_receipt"]["facts"].append(r.items[0])
    elif fault == "history":
        r.row.facts = {**r.row.facts, continuity._EPISTEMIC_HISTORY: [{"forged": True}]}
    else:
        proof["admission_receipt"]["policy"] = "obsolete-policy"
    await r.session.commit()
    with pytest.raises(ValueError):
        await apply(r, proof)
    with pytest.raises(RuntimeError, match="source_context_unavailable"):
        await current(r)


async def test_unmigrated_cache_never_triggers_paid_reextraction(case):
    r = case
    with pytest.raises(ValueError, match="epistemic_reconciliation_required"):
        await continuity.extract_chapter_state_snapshot(
            r.session,
            r.settings,
            project_id=r.project.id,
            chapter=r.chapter,
            chapter_md=r.draft.content_md,
            source_chapter_draft_version_id=r.draft.id,
        )


@pytest.mark.parametrize(
    "body,quote",
    [
        ("甲说‘乙已经离开七年’。", "已经离开七年"),  # noqa: RUF001 - quotation regression
        ("甲说'乙已经离开七年'。", "已经离开七年"),
        ("The pilot said, “The moon is empty.”", "The moon is empty."),
    ],
)
async def test_unrelated_quotation_forms_are_observations_not_numeric_facts(case, body, quote):
    r = case
    r.draft.content_md = body
    await r.session.commit()
    source, rejection = await validate_adopted_knowledge_source(r.session, **args(r), lock=False)
    assert rejection is None
    item = {"name": "assertion", "value": "7", "kind": "elapsed_story_time", "source_quote": quote}
    r.row.facts = {"facts": [item], "snapshot_contract": {**source, "is_usable": True}}
    r.row.raw_extraction = json.dumps({"facts": [item]})
    r.row.time_anchor = r.row.chapter_time_span = None
    await r.session.commit()
    await apply(r, await prepare(r))
    context = await current(r)
    assert context.facts == [] and context.reported_observations
    rendered = continuity.render_hard_fact_snapshot_block(context)
    assert "未确认" in rendered and "必须严格遵守" not in rendered
    assert r.row.facts["facts"] == [item]


async def test_apply_flushes_only_snapshot_and_never_commits(case, monkeypatch):
    r = case
    proof = await prepare(r)
    writes = []

    def sql(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "COMMIT")):
            writes.append(statement)

    async def forbidden(*args, **kwargs):
        raise AssertionError("caller owns commit")

    monkeypatch.setattr(r.session, "commit", forbidden)
    event.listen(r.session.bind.sync_connection, "before_cursor_execute", sql)
    try:
        await apply(r, proof)
        assert len(writes) == 1 and writes[0].startswith("UPDATE chapter_state_snapshots")
    finally:
        event.remove(r.session.bind.sync_connection, "before_cursor_execute", sql)


async def test_old_admitted_view_cannot_be_replayed_over_later_application(case):
    r = case
    await apply(r, await prepare(r))
    old_view = deepcopy(r.row.facts[continuity._EPISTEMIC_VIEW])
    # A later explicit publication binds a new application link, retaining A.
    new = continuity.build_snapshot_epistemic_view(r.row, source_body=r.draft.content_md)
    continuity._publish_epistemic_view(
        r.row,
        new,
        actor="operator",
        reason="new application",
        before_sha256=continuity.snapshot_epistemic_witness(r.row),
    )
    await r.session.commit()
    r.row.facts = {**r.row.facts, continuity._EPISTEMIC_VIEW: old_view}
    await r.session.commit()
    with pytest.raises(RuntimeError, match="source_context_unavailable"):
        await current(r)


async def test_literal_paid_dependencies_are_not_relabelled_by_view(case):
    r = case
    original = deepcopy(r.row.facts["snapshot_contract"])
    await apply(r, await prepare(r))
    assert r.row.facts["snapshot_contract"] == original
    view = r.row.facts[continuity._EPISTEMIC_VIEW]
    assert view["view_dependencies"]["policy"] == POLICY
    assert view["view_dependencies"]["previous_view_sha256"] is None
    assert (
        view["item_dispositions"][0]["raw_sha256"]
        == hashlib.sha256(r.row.raw_extraction.encode()).hexdigest()
    )


async def test_native_producer_reuses_paid_preview_and_publishes_only_admitted_subset(case):
    from bestseller.services.chapter_continuity_admission import PREVIEW_KEY, PREVIEW_SCHEMA

    r = case
    raw = r.row.raw_extraction
    await r.session.delete(r.row)
    dependencies = continuity._snapshot_dependencies(1, continuity._SnapshotInput())
    user_prompt = continuity._build_extraction_user_prompt(
        chapter=r.chapter, chapter_md=r.draft.content_md, previous_snapshot=None
    )
    preview = {
        "schema": PREVIEW_SCHEMA,
        "status": "extracted",
        "fallback_used": False,
        "source": r.source,
        "snapshot_dependencies": dependencies,
        "raw": raw,
        "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": "retained-preview-model",
        "prompt_sha256": hashlib.sha256(
            (continuity._EXTRACTION_SYSTEM_PROMPT + "\n" + user_prompt).encode()
        ).hexdigest(),
    }
    r.draft.promotion_metadata = {**(r.draft.promotion_metadata or {}), PREVIEW_KEY: preview}
    await r.session.commit()
    row = await continuity.extract_chapter_state_snapshot(
        r.session,
        r.settings,
        project_id=r.project.id,
        chapter=r.chapter,
        chapter_md=r.draft.content_md,
        source_chapter_draft_version_id=r.draft.id,
    )
    assert (
        row.raw_extraction == raw and row.facts["snapshot_contract"]["pre_adoption_preview_reused"]
    )
    context = await current(r)
    assert [f.name for f in context.facts] == ["gate"]
    assert context.reported_observations and context.time_anchor is None


@pytest.mark.parametrize("paid_input", ["legacy_raw", "admitted_view"])
async def test_literal_predecessor_receipt_and_new_view_dependencies_remain_separate(
    case, paid_input
):
    r = case
    original_context = continuity._snapshot_context(r.row)
    await apply(r, await prepare(r))
    predecessor = await continuity._current_snapshot_input(
        r.session, project_id=r.project.id, chapter_number=1, include_source=True
    )
    original_dependency = continuity._snapshot_dependencies(
        2,
        continuity._SnapshotInput(
            r.chapter.id,
            r.source,
            original_context if paid_input == "legacy_raw" else predecessor.context,
        ),
    )
    chapter = ChapterModel(
        project_id=r.project.id,
        chapter_number=2,
        chapter_goal="Retain the independently closed second gate",
        metadata_json={},
    )
    r.session.add(chapter)
    await r.session.flush()
    body = "The second gate was closed."
    draft = ChapterDraftVersionModel(
        project_id=r.project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md=body,
        is_current=True,
        promotion_state="promoted",
    )
    r.session.add(draft)
    await r.session.flush()
    source, rejection = await validate_adopted_knowledge_source(
        r.session,
        project_id=r.project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id,
        chapter_md=body,
        lock=False,
    )
    assert rejection is None
    item = {"name": "second gate", "value": "closed", "kind": "location", "source_quote": body}
    row = ChapterStateSnapshotModel(
        project_id=r.project.id,
        chapter_id=chapter.id,
        chapter_number=2,
        facts={
            "facts": [item],
            "snapshot_contract": {
                **source,
                "is_usable": True,
                "snapshot_dependencies": original_dependency,
            },
        },
        raw_extraction=json.dumps({"facts": [item]}),
        extraction_status="ok_promoted",
    )
    r.session.add(row)
    await r.session.commit()
    args2 = {
        "project_id": r.project.id,
        "chapter_id": chapter.id,
        "source_chapter_draft_version_id": draft.id,
        "chapter_md": body,
    }
    proof = await continuity.validate_snapshot_epistemic_view(r.session, **args2)
    await continuity.apply_snapshot_epistemic_view(
        r.session,
        **args2,
        expected_snapshot_sha256=proof["snapshot_sha256"],
        admission_receipt=proof["admission_receipt"],
        actor="operator",
        reason="Explicit same-source reprojection",
    )
    assert row.facts["snapshot_contract"]["snapshot_dependencies"] == original_dependency
    assert (
        row.facts[continuity._EPISTEMIC_VIEW]["view_dependencies"]["previous_view_sha256"]
        == predecessor.context.epistemic_view_sha256
    )
    assert await continuity.snapshot_evidence_current(
        r.session, project_id=r.project.id, chapter_number=2
    )
    parent_view = continuity.build_snapshot_epistemic_view(r.row, source_body=r.draft.content_md)
    continuity._publish_epistemic_view(
        r.row,
        parent_view,
        actor="operator",
        reason="Explicit new parent application",
        before_sha256=continuity.snapshot_epistemic_witness(r.row),
    )
    await r.session.commit()
    assert not await continuity.snapshot_evidence_current(
        r.session, project_id=r.project.id, chapter_number=2
    )
    proof = await continuity.validate_snapshot_epistemic_view(r.session, **args2)
    await continuity.apply_snapshot_epistemic_view(
        r.session,
        **args2,
        expected_snapshot_sha256=proof["snapshot_sha256"],
        admission_receipt=proof["admission_receipt"],
        actor="operator",
        reason="Explicit child reprojection; original paid input retained",
    )
    assert row.facts["snapshot_contract"]["snapshot_dependencies"] == original_dependency
    assert row.facts[continuity._EPISTEMIC_VIEW]["view_dependencies"][
        "previous_application_sha256"
    ] != (predecessor.context.epistemic_application_sha256)
    assert await continuity.snapshot_evidence_current(
        r.session, project_id=r.project.id, chapter_number=2
    )
    data = deepcopy(r.row.facts)
    data[continuity._EPISTEMIC_VIEW]["policy"] = "old-policy"
    r.row.facts = data
    await r.session.commit()
    assert not await continuity.snapshot_evidence_current(
        r.session, project_id=r.project.id, chapter_number=2
    )


async def test_snapshot_without_current_epistemic_context_never_renders_raw_authority(case):
    assert continuity.render_hard_fact_snapshot_block(continuity._snapshot_context(case.row)) == ""


@pytest.mark.parametrize("entrypoint", ["chapter_writer", "chapter_review", "scene_review"])
@pytest.mark.parametrize("state", ["missing", "stale"])
async def test_native_entry_blocks_before_provider_for_inadmissible_adopted_snapshot(
    case, monkeypatch, entrypoint, state
):
    from bestseller.services import context, drafts, reviews

    r = case
    if state == "stale":
        await apply(r, await prepare(r))
        data = deepcopy(r.row.facts)
        data[continuity._EPISTEMIC_VIEW]["policy"] = "old-policy"
        r.row.facts = data
        await r.session.commit()
    assert not await continuity.snapshot_evidence_current(
        r.session, project_id=r.project.id, chapter_number=1
    )
    chapter = SimpleNamespace(
        id=uuid4(), project_id=r.project.id, chapter_number=2, status="planned", metadata_json={}
    )
    project = SimpleNamespace(id=r.project.id, slug="unrelated-estuary")
    scene = SimpleNamespace(id=uuid4())
    provider = AsyncMock(side_effect=AssertionError("provider cannot be reached"))
    monkeypatch.setattr(drafts, "complete_text", provider)
    monkeypatch.setattr(reviews, "complete_text", provider)

    async def build_until_snapshot(*args, **kwargs):
        return await context._safe_load_previous_snapshot(
            r.session, project_id=r.project.id, current_chapter_number=2
        )

    if entrypoint == "chapter_writer":
        caller = SimpleNamespace(
            scalar=AsyncMock(return_value=chapter),
            scalars=AsyncMock(return_value=[scene]),
            get=AsyncMock(return_value=None),
        )
        monkeypatch.setattr(drafts, "get_project_by_slug", AsyncMock(return_value=project))
        monkeypatch.setattr(drafts, "build_chapter_writer_context", build_until_snapshot)
        action = drafts.generate_chapter_draft_once(caller, project.slug, 2, settings=r.settings)
    elif entrypoint == "chapter_review":
        monkeypatch.setattr(
            reviews,
            "_load_chapter_context",
            AsyncMock(return_value=(project, chapter, None, [scene], SimpleNamespace())),
        )
        monkeypatch.setattr(reviews, "build_chapter_writer_context", build_until_snapshot)
        action = reviews.review_chapter_draft(r.session, r.settings, project.slug, 2)
    else:
        monkeypatch.setattr(
            reviews,
            "_load_scene_context",
            AsyncMock(return_value=(project, chapter, scene, None, SimpleNamespace())),
        )
        monkeypatch.setattr(reviews, "build_scene_writer_context", build_until_snapshot)
        action = reviews.review_scene_draft(r.session, r.settings, project.slug, 2, 1)
    with pytest.raises(RuntimeError, match="source_context_unavailable: previous_chapter_snapshot"):
        await action
    provider.assert_not_awaited()


async def test_first_chapter_and_missing_or_unadopted_prior_remain_none(case):
    r = case
    assert (
        await continuity.load_previous_chapter_snapshot(
            r.session, project_id=r.project.id, current_chapter_number=1
        )
        is None
    )
    assert (
        await continuity.load_previous_chapter_snapshot(
            r.session, project_id=r.project.id, current_chapter_number=9
        )
        is None
    )
    r.draft.promotion_state = "candidate"
    await r.session.commit()
    assert await current(r) is None


async def test_snapshot_producer_rejects_unadmitted_predecessor_before_model(case, monkeypatch):
    r = case
    chapter = ChapterModel(
        project_id=r.project.id,
        chapter_number=2,
        chapter_goal="Read the adopted predecessor before extraction",
        metadata_json={},
    )
    r.session.add(chapter)
    await r.session.flush()
    draft = ChapterDraftVersionModel(
        project_id=r.project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="The gate stayed shut.",
        is_current=True,
        promotion_state="promoted",
    )
    r.session.add(draft)
    await r.session.commit()
    provider = AsyncMock(side_effect=AssertionError("provider cannot be reached"))
    monkeypatch.setattr(continuity, "complete_text", provider)
    with pytest.raises(ValueError, match="snapshot_epistemic_predecessor_not_current"):
        await continuity.extract_chapter_state_snapshot(
            r.session,
            r.settings,
            project_id=r.project.id,
            chapter=chapter,
            chapter_md=draft.content_md,
            source_chapter_draft_version_id=draft.id,
        )
    provider.assert_not_awaited()
