"""Owned Bible projections: real isolated SQLite and local deterministic extraction stubs."""

import asyncio
from copy import deepcopy
import socket
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from test_adopted_bible_lineage import db as db
from test_adopted_bible_lineage import knowledge_db as knowledge_db
from test_adopted_bible_lineage import payload, stub_llm, update
from test_knowledge_downstream_source_dependencies import (
    _next_chapter,
    _replace_source,
)
from test_knowledge_downstream_source_dependencies import (
    test_bible_replacement_retires_superseded_source_knowledge as replacement_regression,
)
from test_knowledge_downstream_source_dependencies import (
    test_older_chapter_repair_preserves_later_relationship_projection as ordering_regression,
)

from bestseller.infra.db.models import CharacterModel, ProjectModel, RelationshipModel
from bestseller.services import llm, story_bible

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("BESTSELLER_LINEAGE_PG", "0")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Bible reprojection tests forbid network/provider access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)


async def test_replacement_with_empty_contribution(db, monkeypatch):
    assert db[0].bind.dialect.name == "sqlite"
    await replacement_regression(db, monkeypatch)


async def test_repair_and_later_cached_retry_keep_chapter_order(db, monkeypatch):
    await ordering_regression(db, monkeypatch)


async def entities(db):
    character = await db[0].scalar(select(CharacterModel).where(
        CharacterModel.project_id == db[1].id, CharacterModel.name == "阿青",
    ))
    relationship = await db[0].scalar(select(RelationshipModel).where(
        RelationshipModel.project_id == db[1].id,
    ))
    return character, relationship


async def later_application(db, monkeypatch):
    later = await _next_chapter(db, "两位观测员在港口分开。")
    changed = payload()
    changed["character_updates"][0].update(
        knowledge_gained=["后章保留的观察"], emotional_state="迟疑",
    )
    changed["relationship_updates"][0].update(change="决裂", affinity_direction="down")
    stub_llm(monkeypatch, changed)
    await update(later)
    return later


async def test_authored_baseline_overlap_later_contributions_and_audits_survive(db, monkeypatch):
    session, project, *_ = db
    a = await story_bible.get_or_create_character_by_name(
        session, project_id=project.id, character_name="阿青",
    )
    b = await story_bible.get_or_create_character_by_name(
        session, project_id=project.id, character_name="阿白",
    )
    a.knowledge_state_json = {"knows": ["作者基线", "钥匙在桌上"], "private": {"keep": None}}
    a.metadata_json = {"emotional_state": "平静", "authored": {"keep": True}}
    relationship = RelationshipModel(
        id=story_bible._stable_relationship_id(project.id, a.id, b.id), project_id=project.id,
        character_a_id=a.id, character_b_id=b.id, relationship_type="亲属", strength=0.4,
        last_changed_chapter_no=0, metadata_json={"events": [{"authored": True}]},
    )
    session.add(relationship)
    await session.flush()
    stub_llm(monkeypatch)
    await update(db)
    later = await later_application(db, monkeypatch)
    old_receipts = deepcopy(db[2].metadata_json["story_bible_update_receipts"])
    later_receipts = deepcopy(later[2].metadata_json["story_bible_update_receipts"])
    history = deepcopy(a.metadata_json["story_bible_update_history"])
    events = deepcopy(relationship.metadata_json["events"])
    old_ledger = deepcopy(project.metadata_json["premium_state_ledger"])
    replacement = await _replace_source(db, "观测员离开。未获得新观察。")
    stub_llm(monkeypatch, {})
    assert (await update(replacement))["extraction_status"] == "empty"
    await session.refresh(a)
    await session.refresh(relationship)
    assert a.knowledge_state_json["knows"] == ["作者基线", "钥匙在桌上", "后章保留的观察"]
    assert a.knowledge_state_json["private"] == {"keep": None}
    assert a.metadata_json["authored"] == {"keep": True}
    assert a.metadata_json["emotional_state"] == "迟疑"
    assert relationship.relationship_type == "决裂" and relationship.last_changed_chapter_no == 2
    assert float(relationship.strength) == pytest.approx(0.2)
    assert a.metadata_json["story_bible_update_history"] == history
    assert relationship.metadata_json["events"] == events
    assert replacement[2].metadata_json["story_bible_update_receipts"][:-1] == old_receipts
    assert later[2].metadata_json["story_bible_update_receipts"] == later_receipts
    assert project.metadata_json["premium_state_ledger"] == old_ledger


async def test_cached_retry_restores_demonstrably_stale_projection_without_model(db, monkeypatch):
    stub_llm(monkeypatch)
    await update(db)
    later = await later_application(db, monkeypatch)
    character, relationship = await entities(db)
    history = deepcopy(relationship.metadata_json["events"])
    relationship.relationship_type = "互相信任"
    relationship.last_changed_chapter_no = 1
    relationship.strength = 0.2
    await db[0].flush()
    calls = stub_llm(monkeypatch)
    result = await update(later)
    assert result["extraction_status"] == "already_applied"
    assert result["projection_reconciled"] is True
    assert not calls
    assert relationship.relationship_type == "决裂" and relationship.last_changed_chapter_no == 2
    assert float(relationship.strength) == pytest.approx(0.0)
    assert relationship.metadata_json["events"] == history
    assert character.knowledge_state_json["knows"] == ["钥匙在桌上", "后章保留的观察"]
    receipts = deepcopy(later[2].metadata_json["story_bible_update_receipts"])
    assert (await update(later))["extraction_status"] == "already_applied"
    assert later[2].metadata_json["story_bible_update_receipts"] == receipts


@pytest.mark.parametrize("bad", ["legacy_baseline", "owner", "version", "body"])
async def test_ambiguous_attribution_fails_closed_without_touching_materialized_rows(
    db, monkeypatch, bad,
):
    stub_llm(monkeypatch)
    await update(db)
    character, relationship = await entities(db)
    metadata = deepcopy(character.metadata_json)
    if bad == "legacy_baseline":
        metadata.pop("story_bible_projection_base")
    else:
        key, value = {"owner": ("project_id", str(uuid4())),
                      "version": ("source_version_no", "1"),
                      "body": ("source_body_sha256", "0" * 64)}[bad]
        metadata["story_bible_update_history"][0]["source_lineage"][key] = value
    character.metadata_json = metadata
    await db[0].flush()
    before = (deepcopy(character.knowledge_state_json), deepcopy(character.metadata_json),
              relationship.relationship_type, relationship.strength,
              deepcopy(relationship.metadata_json))
    replacement = await _replace_source(db, "观测员没有获得任何新知识。")
    calls = stub_llm(monkeypatch, {})
    result = await update(replacement)
    assert result["extraction_status"] == "requires_projection_reconciliation"
    assert result["projection_reconciled"] is False
    await db[0].refresh(character)
    await db[0].refresh(relationship)
    assert (character.knowledge_state_json, character.metadata_json,
            relationship.relationship_type, relationship.strength,
            relationship.metadata_json) == before
    assert not calls  # Retained attribution is already unprovable before extraction.
    receipts = deepcopy(replacement[2].metadata_json["story_bible_update_receipts"])
    assert await update(replacement) == result
    assert not calls
    assert replacement[2].metadata_json["story_bible_update_receipts"] == receipts


async def test_unrelated_project_and_failed_new_source_remain_unchanged(db, monkeypatch):
    other = ProjectModel(slug=f"bible-isolation-{uuid4().hex}", title="Other", genre="test",
                         target_word_count=1000, target_chapters=1)
    db[0].add(other)
    await db[0].flush()
    foreign = CharacterModel(
        project_id=other.id, name="阿青", knowledge_state_json={"knows": ["外部"]},
        metadata_json={"source_lineage": {"unrelated": True}},
    )
    db[0].add(foreign)
    await db[0].flush()
    stub_llm(monkeypatch)
    await update(db)
    character, relationship = await entities(db)
    before = (deepcopy(character.knowledge_state_json), deepcopy(character.metadata_json),
              relationship.relationship_type, relationship.strength,
              deepcopy(relationship.metadata_json))
    replacement = await _replace_source(db, "观测员对结果保持沉默。")
    stub_llm(monkeypatch, "invalid json")
    assert (await update(replacement))["extraction_status"] == "parse_error"
    await db[0].refresh(character)
    await db[0].refresh(relationship)
    await db[0].refresh(foreign)
    assert (character.knowledge_state_json, character.metadata_json,
            relationship.relationship_type, relationship.strength,
            relationship.metadata_json) == before
    assert foreign.knowledge_state_json == {"knows": ["外部"]}
    assert foreign.metadata_json == {"source_lineage": {"unrelated": True}}


async def test_read_status_distinguishes_current_legacy_stale_and_unknown(db, monkeypatch):
    from bestseller.services.story_bible_projection import adopted_bible_projection_status

    stub_llm(monkeypatch)
    result = await update(db)
    source = result["source_lineage"]
    kwargs = {"project_id": db[1].id, "source_lineage": source}
    assert await adopted_bible_projection_status(db[0], **kwargs) == "current"
    character, _ = await entities(db)
    metadata = deepcopy(character.metadata_json)
    metadata.pop("story_bible_projection_base")
    character.metadata_json = metadata
    await db[0].flush()
    before = deepcopy(character.metadata_json)
    assert await adopted_bible_projection_status(db[0], **kwargs) == "legacy_current"
    assert character.metadata_json == before
    metadata = deepcopy(before)
    metadata["story_bible_update_history"][0]["source_lineage"].pop("project_id")
    character.metadata_json = metadata
    await db[0].flush()
    assert await adopted_bible_projection_status(db[0], **kwargs) == "unknown"


async def test_empty_first_source_is_proven_and_reruns_without_model(db, monkeypatch):
    from bestseller.services.story_bible_projection import adopted_bible_projection_status

    calls = stub_llm(monkeypatch, {})
    result = await update(db)
    assert result["extraction_status"] == "empty"
    assert await adopted_bible_projection_status(
        db[0], project_id=db[1].id, source_lineage=result["source_lineage"],
    ) == "current"
    receipts = deepcopy(db[2].metadata_json["story_bible_update_receipts"])
    assert (await update(db))["extraction_status"] == "already_applied"
    assert len(calls) == 1
    assert db[2].metadata_json["story_bible_update_receipts"] == receipts


async def test_long_authored_knowledge_is_preserved_in_full(db, monkeypatch):
    character = await story_bible.get_or_create_character_by_name(
        db[0], project_id=db[1].id, character_name="阿青",
    )
    baseline = [f"作者基线观察{i}" for i in range(40)]
    character.knowledge_state_json = {"knows": baseline, "unrelated": None}
    await db[0].flush()
    stub_llm(monkeypatch)
    await update(db)
    assert character.knowledge_state_json["knows"] == [*baseline, "钥匙在桌上"]
    assert character.knowledge_state_json["unrelated"] is None


async def test_batch_read_runs_one_project_plan_and_keeps_legitimate_legacy(db, monkeypatch):
    from bestseller.services.story_bible_projection import adopted_bible_projection_statuses

    stub_llm(monkeypatch)
    first = await update(db)
    later = await later_application(db, monkeypatch)
    third = await later_application(later, monkeypatch)
    sources = {
        book[2].id: book[2].metadata_json["story_bible_update_receipts"][-1]["source_lineage"]
        for book in (db, later, third)
    }
    assert first["source_lineage"] == sources[db[2].id]
    character, _ = await entities(db)
    metadata = deepcopy(character.metadata_json)
    metadata.pop("story_bible_projection_base")
    character.metadata_json = metadata
    await db[0].flush()
    statements = []

    def record(state):
        statements.append(state.statement)

    async def forbidden_flush(*_args, **_kwargs):
        raise AssertionError("Read status must not flush")

    event.listen(db[0].sync_session, "do_orm_execute", record)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(db[0], "flush", forbidden_flush)
            values = await adopted_bible_projection_statuses(
                db[0], project_id=db[1].id, source_lineages=sources,
            )
        assert values == dict.fromkeys(sources, "legacy_current")
        assert all(stmt._for_update_arg is None for stmt in statements)
        assert sum(stmt.column_descriptions[0].get("entity") is CharacterModel
                   for stmt in statements) == 1
        assert character.metadata_json == metadata
    finally:
        event.remove(db[0].sync_session, "do_orm_execute", record)


async def test_premium_absence_is_distinct_from_a_malformed_present_receipt():
    from bestseller.services.story_bible_projection import validated_premium_projection

    assert validated_premium_projection({}) is None
    for value in (None, {}, False, {"receipt": None}):
        with pytest.raises(ValueError):
            validated_premium_projection({"premium_state_source_projection": value})


@pytest.mark.parametrize("defect", [
    "schema", "raw_hash", "active_hash", "snapshot_hash", "active_ledger", "snapshot",
    "active_owner", "event_owner", "unknown_success", "raw_content", "unscoped", "duplicate",
    "current_snapshot", "current_report", "baseline", "projection_history",
])
async def test_premium_view_rederives_and_rejects_corrupt_or_unattributed_evidence(
    db, monkeypatch, defect,
):
    from bestseller.services.story_bible_projection import validated_premium_projection

    stub_llm(monkeypatch)
    await update(db)
    metadata = deepcopy(db[1].metadata_json)
    view = metadata["premium_state_source_projection"]
    receipt = view["receipt"]
    assert validated_premium_projection(metadata) is not None
    if defect == "schema":
        receipt["schema"] = "unknown"
    elif defect in {"raw_hash", "active_hash", "snapshot_hash"}:
        receipt[{"raw_hash": "raw_ledger_sha256", "active_hash": "active_ledger_sha256",
                 "snapshot_hash": "snapshot_sha256"}[defect]] = "0" * 64
    elif defect == "active_ledger":
        view["active_ledger"]["progression_events"] = []
    elif defect == "snapshot":
        view["snapshot"] = {"forged": True}
    elif defect == "active_owner":
        next(iter(receipt["active_sources"].values()))["project_id"] = str(uuid4())
    elif defect == "event_owner":
        metadata["premium_state_ledger"]["progression_events"][0]["source_lineage"][
            "project_id"
        ] = str(uuid4())
    elif defect == "unknown_success":
        receipt["known_successes"] = []
    elif defect == "raw_content":
        metadata["premium_state_ledger"]["progression_events"][0]["delta"] = 999
    elif defect == "unscoped":
        metadata["premium_state_ledger"]["progression_events"][0].pop("source_lineage")
    elif defect == "duplicate":
        events = metadata["premium_state_ledger"]["progression_events"]
        events.append(deepcopy(events[0]))
    elif defect == "current_snapshot":
        metadata["premium_state_snapshot"] = {"forged": True}
    elif defect == "current_report":
        metadata["premium_state_ledger_report"] = {"passed": True}
    elif defect == "baseline":
        receipt["opaque_baseline"]["ledger"] = {"relationship_events": [{"opaque": True}]}
    elif defect == "projection_history":
        view["projection_history"][0]["previous_values"] = {"forged": True}
    with pytest.raises(ValueError):
        validated_premium_projection(metadata)


def relationship_premium(after):
    changes = payload()
    changes["premium_engine_updates"] = {"relationship_events": [{
        "character_a": "阿青", "character_b": "阿白", "axis": "trust",
        "active_choice": "观测员主动向同伴说明决定并承担后果。", "after": after,
    }]}
    return changes


async def test_late_older_premium_repair_keeps_later_state_and_actual_gate_pair(db, monkeypatch):
    from bestseller.services.category_hard_engines import (
        build_category_engine_fixture,
        evaluate_category_hard_engine,
    )
    from bestseller.services.premium_state_ledger import (
        materialize_premium_state_snapshot,
        validate_premium_state_ledger,
    )
    from bestseller.services.story_bible_projection import (
        adopted_bible_projection_statuses,
        validated_premium_projection,
    )

    settings = build_category_engine_fixture("realistic-life", good=True)
    settings["premium_state_snapshot"] = materialize_premium_state_snapshot(
        settings["premium_state_ledger"],
    )
    initial = deepcopy(settings)
    db[1].metadata_json = settings
    await db[0].flush()
    stub_llm(monkeypatch, relationship_premium("同伴停止交付共同事务。"))
    assert (await update(db))["extraction_status"] == "ok"
    later = await _next_chapter(db, "同伴作出了独立的决定。")
    stub_llm(monkeypatch, relationship_premium("同伴恢复共同事务并约定复核。"))
    await update(later)
    old_ledger = deepcopy(db[1].metadata_json["premium_state_ledger"])
    old_view = deepcopy(db[1].metadata_json["premium_state_source_projection"])
    repaired = await _replace_source(db, "观测员重新说明第一轮决定。")
    stub_llm(monkeypatch, relationship_premium("第一轮决定被重新说明。"))
    await update(repaired)
    metadata = db[1].metadata_json
    view = validated_premium_projection(metadata)
    raw = metadata["premium_state_ledger"]["relationship_events"]
    assert raw[:len(old_ledger["relationship_events"])] == old_ledger["relationship_events"]
    original = initial["premium_state_ledger"]["relationship_events"]
    assert raw[:len(original)] == original
    assert view["receipt"]["opaque_baseline"]["ledger"] == initial["premium_state_ledger"]
    audit = metadata["premium_state_source_projection"]["projection_history"]
    assert audit[0]["previous_values"] == {key: initial[key] for key in (
        "premium_state_snapshot", "premium_state_ledger_report",
    ) if key in initial}
    assert audit[:-1] == old_view["projection_history"]
    assert audit[-1]["previous_values"]["premium_state_snapshot"] == old_view["snapshot"]
    assert metadata["premium_state_snapshot"] == view["snapshot"]
    assert metadata["premium_state_source_projection"]["receipt_history"][:-1] == (
        old_view["receipt_history"]
    )
    assert metadata["premium_state_source_projection"]["receipt_history"][-1] == old_view["receipt"]
    assert view["active_ledger"]["relationship_events"][:len(original)] == original
    events = view["active_ledger"]["relationship_events"][len(original):]
    assert [item["chapter_number"] for item in events] == [1, 2]
    assert events[-1]["after"] == "同伴恢复共同事务并约定复核。"
    assert evaluate_category_hard_engine(metadata, category_key="realistic-life").passed
    # Exercise the actual gate with its paired view, without editing its consumer.
    adapted = {k: deepcopy(v) for k, v in metadata.items()
               if k != "premium_state_source_projection"}
    adapted.update(
        premium_state_ledger=view["active_ledger"], premium_state_snapshot=view["snapshot"],
        premium_state_ledger_report=validate_premium_state_ledger(view["active_ledger"]).to_dict(),
    )
    assert evaluate_category_hard_engine(adapted, category_key="realistic-life").passed
    mismatched = {**adapted, "premium_state_ledger": metadata["premium_state_ledger"]}
    assert any(f.path == "premium_state_snapshot.relationship_state" for f in
               evaluate_category_hard_engine(mismatched, category_key="realistic-life").findings)
    sources = {
        book[2].id: book[2].metadata_json["story_bible_update_receipts"][-1]["source_lineage"]
        for book in (repaired, later)
    }
    assert await adopted_bible_projection_statuses(
        db[0], project_id=db[1].id, source_lineages=sources,
    ) == dict.fromkeys(sources, "current")


async def test_empty_replacement_publishes_current_snapshot_and_keeps_raw_history(db, monkeypatch):
    from bestseller.services.story_bible_projection import validated_premium_projection

    stub_llm(monkeypatch, relationship_premium("仅旧稿可见的状态。"))
    assert (await update(db))["extraction_status"] == "ok"
    before = deepcopy(db[1].metadata_json)
    repaired = await _replace_source(db, "观测员没有作出关系决定。")
    stub_llm(monkeypatch, {})
    assert (await update(repaired))["extraction_status"] == "empty"
    metadata = db[1].metadata_json
    view = validated_premium_projection(metadata)
    assert metadata["premium_state_ledger"] == before["premium_state_ledger"]
    assert view["active_ledger"]["relationship_events"] == []
    assert metadata["premium_state_snapshot"]["relationship_state"] == {}
    assert view["snapshot"] == metadata["premium_state_snapshot"]
    audit = metadata["premium_state_source_projection"]["projection_history"]
    assert audit[:-1] == before["premium_state_source_projection"]["projection_history"]
    assert audit[-1]["previous_values"]["premium_state_snapshot"] == (
        before["premium_state_snapshot"]
    )
    frozen = deepcopy(metadata)
    assert (await update(repaired))["extraction_status"] == "already_applied"
    assert db[1].metadata_json == frozen


@pytest.mark.parametrize("defect", ["late_unscoped", "actual_chapter_number"])
async def test_premium_batch_refuses_ambiguous_append_or_changed_actual_order(
    db, monkeypatch, defect,
):
    from bestseller.services.story_bible_projection import adopted_bible_projection_statuses

    stub_llm(monkeypatch, relationship_premium("同伴保留共同决定。"))
    source = (await update(db))["source_lineage"]
    metadata = deepcopy(db[1].metadata_json)
    if defect == "late_unscoped":
        metadata["premium_state_ledger"]["relationship_events"].append({
            "character_a": "阿青", "character_b": "阿白", "after": "来源不明的决定",
        })
        db[1].metadata_json = metadata
    else:
        db[2].chapter_number = 4
    await db[0].flush()
    assert await adopted_bible_projection_statuses(
        db[0], project_id=db[1].id, source_lineages={db[2].id: source},
    ) == {db[2].id: "unknown"}
    assert (await update(db))["extraction_status"] == "requires_projection_reconciliation"
    await db[0].refresh(db[1])
    assert db[1].metadata_json == metadata


async def test_late_structural_block_retains_paid_sample_and_never_pays_same_source_again(
    db, monkeypatch,
):
    stub_llm(monkeypatch)
    await update(db)
    character, _ = await entities(db)
    baseline = deepcopy(character.metadata_json["story_bible_projection_base"])
    replacement = await _replace_source(db, "观测员修改了关系决定。")

    async def lose_legacy_baseline(session):
        metadata = deepcopy(character.metadata_json)
        metadata.pop("story_bible_projection_base")
        character.metadata_json = metadata
        await session.flush()

    calls = stub_llm(monkeypatch, on_call=lose_legacy_baseline)
    result = await update(replacement)
    assert result["extraction_status"] == "requires_projection_reconciliation"
    assert len(calls) == 1
    retained = deepcopy(replacement[2].metadata_json["story_bible_update_receipts"])
    assert retained[-1]["candidate_payload"] == payload()
    assert len(retained[-1]["raw_extractions"]) == len(retained[-1]["llm_operations"]) == 1
    calls = stub_llm(monkeypatch)
    assert await update(replacement) == result
    assert not calls
    assert replacement[2].metadata_json["story_bible_update_receipts"] == retained
    # Even repairing metadata is not authority to approve a failed paid sample.
    await db[0].refresh(character)
    character.metadata_json = {**character.metadata_json,
                              "story_bible_projection_base": baseline, "author_note": "changed"}
    await db[0].flush()
    assert await update(replacement) == result
    assert not calls
    assert replacement[2].metadata_json["story_bible_update_receipts"] == retained


async def test_preflight_is_read_only_and_propagates_runtime_errors_and_cancellation(
    db, monkeypatch,
):
    from bestseller.services import story_bible_projection as projection
    from bestseller.services.feedback import validate_adopted_knowledge_source

    source, rejection = await validate_adopted_knowledge_source(
        db[0], project_id=db[1].id, chapter_id=db[2].id,
        source_chapter_draft_version_id=db[3].id, chapter_md=db[3].content_md, lock=False,
    )
    assert rejection is None
    statements = []

    def record(state):
        statements.append(state.statement)

    async def forbidden_flush(*_args, **_kwargs):
        raise AssertionError("Preflight must not flush")

    event.listen(db[0].sync_session, "do_orm_execute", record)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(db[0], "flush", forbidden_flush)
            await projection.preflight_adopted_bible_projection(
                db[0], project_id=db[1].id, source_lineage=source,
            )
        assert all(stmt._for_update_arg is None for stmt in statements)
    finally:
        event.remove(db[0].sync_session, "do_orm_execute", record)
    calls = stub_llm(monkeypatch)
    for error in (RuntimeError("database unavailable"), asyncio.CancelledError()):
        async def fail(*_args, error=error, **_kwargs):
            raise error

        monkeypatch.setattr(projection, "preflight_adopted_bible_projection", fail)
        with pytest.raises(type(error)):
            await update(db)
        assert not calls
        assert not db[2].metadata_json.get("story_bible_update_receipts")
