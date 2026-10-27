"""Adopted source and transaction contracts for the chapter Bible updater."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from story_bible_read_views_test_support import create_story_bible_read_view_tables

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    CharacterModel,
    RelationshipModel,
    WorldRuleModel,
)
from bestseller.services import llm, story_bible
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.llm import LLMCompletionResult
from bestseller.settings import load_settings
from test_adopted_knowledge_lineage import db as knowledge_db, make_accepted_debt

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def db(knowledge_db):
    session = knowledge_db[0]
    if session.bind.dialect.name == "sqlite":
        await create_story_bible_read_view_tables(session)
    return knowledge_db


def payload():
    return {
        "character_updates": [
            {"name": "阿青", "knowledge_gained": ["钥匙在桌上"], "emotional_state": "放心"}
        ],
        "relationship_updates": [
            {
                "character_a": "阿青",
                "character_b": "阿白",
                "change": "互相信任",
                "affinity_direction": "up",
            }
        ],
        "world_updates": [{"rule_or_location": "key rule", "description": "钥匙可开门"}],
        "premium_engine_updates": {
            "progression_events": [
                {
                    "event_type": "resource_gained",
                    "subject": "阿青",
                    "resource_key": "key",
                    "delta": 1,
                    "cause": "取得钥匙",
                }
            ]
        },
    }


def stub_llm(monkeypatch, response=None, on_call=None):
    calls = []

    async def complete(session, settings, request):
        assert not session.in_nested_transaction()
        calls.append(request)
        if on_call:
            await on_call(session)
        raw = (
            response
            if isinstance(response, str)
            else json.dumps(payload() if response is None else response, ensure_ascii=False)
        )
        return LLMCompletionResult(
            content=raw, provider="test", model_name="test", llm_run_id=uuid4()
        )

    monkeypatch.setattr(llm, "complete_text", complete)
    return calls


async def update(db, **kwargs):
    session, project, chapter, draft = db
    return await story_bible.update_story_bible_from_chapter(
        session,
        load_settings(),
        project=project,
        chapter=chapter,
        chapter_text=draft.content_md,
        source_chapter_draft_version_id=draft.id,
        **kwargs,
    )


@pytest.mark.parametrize("invalid", ["missing", "candidate", "body"])
async def test_bible_rejects_source_before_payment(db, monkeypatch, invalid):
    session, project, chapter, draft = db
    source, body = draft.id, draft.content_md
    if invalid == "missing":
        source = None
    elif invalid == "candidate":
        draft.promotion_state = "candidate"
    else:
        body += "不是同一版。"
    await session.flush()
    calls = stub_llm(monkeypatch)
    result = await story_bible.update_story_bible_from_chapter(
        session,
        load_settings(),
        project=project,
        chapter=chapter,
        chapter_text=body,
        source_chapter_draft_version_id=source,
    )
    assert result["extraction_status"] == "rejected_source"
    assert result["degraded"] is True
    assert not calls
    assert not list(await session.scalars(select(CharacterModel)))
    assert chapter.metadata_json["story_bible_update_receipts"][-1]["status"] == "rejected_source"


async def test_bible_preserves_features_and_is_idempotent(db, monkeypatch):
    session, project, chapter, draft = db
    calls = stub_llm(monkeypatch)
    result = await update(db, workflow_run_id=uuid4(), step_run_id=uuid4())
    await session.commit()
    await session.refresh(chapter)
    repeated = await update(db)
    assert result["characters_updated"] == result["relationships_updated"] == 1
    assert result["world_rules_added"] == result["premium_state_events_recorded"] == 1
    assert len(calls) == 1
    assert repeated["extraction_status"] == "already_applied"
    assert repeated["relationships_updated"] == repeated["premium_state_events_recorded"] == 0
    relations = list(await session.scalars(select(RelationshipModel)))
    assert len(relations) == 1
    assert float(relations[0].strength) == pytest.approx(0.2)
    assert len(relations[0].metadata_json["events"]) == 1
    assert relations[0].relationship_type == "互相信任"
    lineage = result["source_lineage"]
    assert lineage["source_chapter_draft_version_id"] == str(draft.id)
    assert lineage["source_version_no"] == 1
    assert lineage["source_body_sha256"] == hashlib.sha256(draft.content_md.encode()).hexdigest()
    assert lineage["operation_id"] == calls[0].operation_id
    assert len(project.metadata_json["premium_state_ledger"]["progression_events"]) == 1


async def test_bible_rechecks_after_gateway_releases_transaction(db, monkeypatch):
    async def change(session):
        await session.commit()
        db[3].content_md += "网关返回前变稿。"
        await session.flush()

    calls = stub_llm(monkeypatch, on_call=change)
    result = await update(db)
    assert len(calls) == 1
    assert result["extraction_status"] == "rejected_source"
    assert not list(await db[0].scalars(select(CharacterModel)))
    assert not db[1].metadata_json.get("premium_state_ledger")


async def test_bible_new_body_is_not_hidden_by_previous_receipt(db, monkeypatch):
    calls = stub_llm(monkeypatch, {})
    await update(db)
    db[3].content_md += "新的完整结尾。"
    await db[0].flush()
    result = await update(db)
    assert len(calls) == 2
    assert result["extraction_status"] == "empty"
    assert len(db[2].metadata_json["story_bible_update_receipts"]) == 2


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        "[]",
        '{"relationship_updates":[null]}',
        '{"character_updates":[{"name":"阿青","knowledge_gained":null}]}',
    ],
)
async def test_bible_bad_payload_is_audited_without_partial_apply(db, monkeypatch, response):
    calls = stub_llm(monkeypatch, response)
    result = await update(db)
    assert len(calls) == 2
    assert result["extraction_status"] == "parse_error"
    assert not list(await db[0].scalars(select(CharacterModel)))
    receipt = db[2].metadata_json["story_bible_update_receipts"][-1]
    assert len(receipt["llm_operations"]) == 2


async def test_bible_budget_stop_propagates(db, monkeypatch):
    async def stop(*args):
        raise ExecutionBudgetError("execution_transaction_not_releasable", "test")

    monkeypatch.setattr(llm, "complete_text", stop)
    with pytest.raises(ExecutionBudgetError):
        await update(db)


async def test_bible_apply_is_atomic_and_pure_db_nested(db, monkeypatch):
    stub_llm(monkeypatch)
    before = story_bible.get_or_create_character_by_name

    async def fail(session, **kwargs):
        assert session.in_nested_transaction()
        if kwargs["character_name"] == "阿白":
            raise RuntimeError("relationship apply failed")
        return await before(session, **kwargs)

    monkeypatch.setattr(story_bible, "get_or_create_character_by_name", fail)
    with pytest.raises(RuntimeError, match="relationship apply failed"):
        await update(db)
    assert not list(await db[0].scalars(select(CharacterModel)))
    await db[0].refresh(db[1])
    await db[0].refresh(db[2])
    assert not db[1].metadata_json.get("premium_state_ledger")
    assert not db[2].metadata_json.get("story_bible_update_receipts")


async def test_bible_accepted_debt_is_not_blocked(db, monkeypatch):
    await make_accepted_debt(db)
    stub_llm(monkeypatch, {})
    result = await update(db)
    assert result["extraction_status"] == "empty"
    assert result["source_lineage"]["adoption_state"] == "accepted_with_debt"


async def test_bible_bad_parse_rechecks_source_before_paid_repair(db, monkeypatch):
    async def change(session):
        db[3].promotion_state = "superseded"
        await session.flush()

    calls = stub_llm(monkeypatch, "not json", change)
    result = await update(db)
    assert result["extraction_status"] == "rejected_source"
    assert len(calls) == 1


async def test_bible_all_effect_flushes_are_within_savepoint(db, monkeypatch):
    flushed = []

    def check(session, _context, _instances):
        for row in set(session.new) | set(session.dirty):
            if isinstance(row, (CharacterModel, RelationshipModel, WorldRuleModel)):
                assert session.in_nested_transaction()
                flushed.append(type(row).__name__)

    event.listen(db[0].sync_session, "before_flush", check)
    stub_llm(monkeypatch)
    await update(db)
    assert {"CharacterModel", "RelationshipModel", "WorldRuleModel"} <= set(flushed)
    assert not db[0].in_nested_transaction()


async def test_bible_same_historical_source_never_replays_affinity(db, monkeypatch):
    calls = stub_llm(monkeypatch)
    original = db[3].content_md
    await update(db)
    b_payload = payload()
    b_payload["character_updates"][0]["emotional_state"] = "警惕"
    b_payload["relationship_updates"][0].update(change="戒备", affinity_direction="down")
    b_payload["world_updates"][0]["description"] = "钥匙已经遗失，门只能由守卫开启。"
    b_calls = stub_llm(monkeypatch, b_payload)
    db[3].content_md += "第二个结尾。"
    await db[0].flush()
    await update(db)
    relation = await db[0].scalar(select(RelationshipModel))
    strength = relation.strength
    events = len(relation.metadata_json["events"])
    db[3].content_md = original
    await db[0].flush()
    result = await update(db)
    assert result["extraction_status"] == "requires_projection_reconciliation"
    assert result["degraded"] is True
    assert result["projection_reconciled"] is False
    assert result["candidate_payload"] == payload()
    assert (
        result["last_applied_source_lineage"]["source_body_sha256"]
        != result["source_lineage"]["source_body_sha256"]
    )
    assert result["relationships_updated"] == result["characters_updated"] == 0
    assert len(calls) == len(b_calls) == 1
    await db[0].refresh(relation)
    assert relation.strength == strength
    assert len(relation.metadata_json["events"]) == events
    assert relation.relationship_type == "戒备"
    character = await db[0].scalar(select(CharacterModel).where(CharacterModel.name == "阿青"))
    assert character.metadata_json["emotional_state"] == "警惕"
    receipt = db[2].metadata_json["story_bible_update_receipts"][-1]
    assert receipt["status"] == "requires_projection_reconciliation"
    assert receipt["candidate_payload"] == payload()
    assert (await update(db))["extraction_status"] == "requires_projection_reconciliation"
    assert len(calls) == len(b_calls) == 1


async def test_bible_each_entity_update_retains_source_lineage(db, monkeypatch):
    legacy = WorldRuleModel(
        project_id=db[1].id,
        rule_code="key_rule",
        name="key rule",
        description="旧有且更详细的规则说明，应按既有策略保留完整描述。",
        metadata_json={},
    )
    db[0].add(legacy)
    await db[0].flush()
    calls = stub_llm(monkeypatch)
    result = await update(db)
    lineage = result["source_lineage"]
    character = await db[0].scalar(select(CharacterModel).where(CharacterModel.name == "阿青"))
    assert character.metadata_json["source_lineage"] == lineage
    assert character.metadata_json["story_bible_update_history"][-1]["source_lineage"] == lineage
    relationship = await db[0].scalar(select(RelationshipModel))
    assert relationship.metadata_json["source_lineage"] == lineage
    assert relationship.metadata_json["events"][-1]["source_lineage"] == lineage
    await db[0].refresh(legacy)
    observation = legacy.metadata_json["story_bible_update_history"][-1]
    assert observation["source_lineage"] == lineage
    assert observation["description_applied"] is False
    assert legacy.metadata_json["description_source_lineage"] is None
    assert legacy.metadata_json["source_lineage"] == lineage
    assert legacy.description.startswith("旧有且更详细")
    assert len(calls) == 1


async def test_bible_distinct_draft_a_b_a_does_not_claim_projection_restored(db, monkeypatch):
    session, project, chapter, a = db
    calls = stub_llm(monkeypatch)
    await update(db)
    a.promotion_state, a.is_current = "superseded", False
    await session.flush()
    b = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=2,
        content_md="她收起钥匙。",
        promotion_state="promoted",
        is_current=True,
    )
    session.add(b)
    await session.flush()
    await update((session, project, chapter, b))
    b.promotion_state, b.is_current = "superseded", False
    await session.flush()
    a.promotion_state, a.is_current = "promoted", True
    await session.flush()
    result = await update(db)
    assert result["extraction_status"] == "requires_projection_reconciliation"
    assert result["projection_reconciled"] is False
    assert result["last_applied_source_lineage"]["source_chapter_draft_version_id"] == str(b.id)
    assert result["source_lineage"]["source_chapter_draft_version_id"] == str(a.id)
    assert len(calls) == 2


async def test_bible_failed_attempt_does_not_replace_latest_applied_source(db, monkeypatch):
    calls = stub_llm(monkeypatch)
    original = db[3].content_md
    await update(db)
    failed_calls = stub_llm(monkeypatch, "not json")
    db[3].content_md += "失败的另一版。"
    await db[0].flush()
    assert (await update(db))["extraction_status"] == "parse_error"
    db[3].content_md = original
    await db[0].flush()
    assert (await update(db))["extraction_status"] == "already_applied"
    assert len(calls) == 1 and len(failed_calls) == 2


async def test_bible_parse_repair_does_not_pay_after_concurrent_a_b_a(db, monkeypatch):
    async def concurrent_applications(session):
        # Simulate successful A and B applications while the first gateway
        # request is in flight, followed by a return to adopted source A.
        stub_llm(monkeypatch, {})
        original = db[3].content_md
        await update(db)
        db[3].content_md += "另一版。"
        await session.flush()
        await update(db)
        db[3].content_md = original
        await session.flush()

    calls = stub_llm(monkeypatch, "not json", concurrent_applications)
    result = await update(db)
    assert result["extraction_status"] == "requires_projection_reconciliation"
    assert result["projection_reconciled"] is False
    assert len(calls) == 1
