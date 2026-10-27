"""F07: real isolated SQLite persistence, stubbed model/index, no network or PG."""

from copy import deepcopy
import hashlib
import socket
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import db as db

from bestseller.infra.db.models import (
    CanonFactModel,
    CharacterModel,
    CharacterStateSnapshotModel,
    ProjectModel,
    SceneCardModel,
    SceneDraftVersionModel,
    StyleGuideModel,
    TimelineEventModel,
)
from bestseller.services import knowledge
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.retrieval import _scene_context_text
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("BESTSELLER_LINEAGE_PG", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("F07 forbids network, provider and production DB access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


@pytest.fixture
async def scene_db(db):
    session, project, chapter, _ = db
    conn = await session.connection()
    assert conn.dialect.name == "sqlite"
    assert "test" in str(conn.engine.url.database)
    raw = await conn.get_raw_connection()
    # Exercise real SQL upserts, but this is NOT evidence of PG locking behavior.
    await raw.driver_connection.create_function("pg_advisory_xact_lock", 1, lambda _: None)
    for model in (SceneCardModel, SceneDraftVersionModel, StyleGuideModel):
        ddl = str(CreateTable(model.__table__).compile(dialect=conn.dialect))
        await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    scene = SceneCardModel(
        project_id=project.id, chapter_id=chapter.id, scene_number=1,
        scene_type="turning_point", title="计划胜利", time_label="计划次日",
        participants=["岑禾", "未出场者"], purpose={"story": "计划升级", "emotion": "计划狂喜"},
        exit_state={"岑禾": {"power_tier": "高级", "arc_state": "计划成功"}},
    )
    character = CharacterModel(
        project_id=project.id, name="岑禾", power_tier="初级", arc_state="仍在尝试",
        metadata_json={"keep": "original"},
    )
    session.add_all([scene, character])
    await session.flush()
    draft = SceneDraftVersionModel(
        project_id=project.id, scene_card_id=scene.id, version_no=1,
        content_md="岑禾没有晋级，仍留在原来的班级。", word_count=20,
        promotion_state="promoted", is_current=True,
    )
    session.add(draft)
    await session.commit()
    return session, project, chapter, scene, draft, character


def model_and_index(monkeypatch, *, summary=None):
    calls, chunks = [], []

    async def complete(session, settings, request):
        calls.append(request)
        return LLMCompletionResult(
            content=summary if summary is not None else request.fallback_response,
            provider="test", model_name="test", llm_run_id=uuid4(),
        )

    async def index(session, settings, **kw):
        chunks.append(_scene_context_text(
            chapter_number=kw["chapter_number"], scene=kw["scene"],
            summary_text=kw["summary_text"], draft_content=kw["draft_content"],
        ))
        return 1

    monkeypatch.setattr(knowledge, "complete_text", complete)
    monkeypatch.setattr(knowledge, "index_scene_retrieval_context", index)
    return calls, chunks


async def refresh(scene_db, **kw):
    session, project, chapter, scene, _, _ = scene_db
    return await knowledge.refresh_scene_knowledge(
        session, load_settings(), project.slug, chapter.chapter_number, scene.scene_number, **kw,
    )


@pytest.mark.parametrize("genre,name,plan,body,summary", [
    ("仙侠", "卓遥", {"power_tier": "元婴", "arc_state": "突破"},
     "卓遥说：若能突破元婴就好了。他依旧停在筑基。", "卓遥仍在筑基，突破只是愿望。"),
    ("职场", "闻笙", {"power_tier": "总监", "arc_state": "升职"},
     "闻笙没有升任总监，申请还未获批。", "闻笙的升职申请尚未获批。"),
    ("科幻", "Mira", {"power_tier": "commander", "arc_state": "reconciled"},
     "Mira imagined being a commander. The crew still distrusted her.",
     "Mira remained distrusted; command was only a hope."),
])
async def test_plans_never_materialize_as_character_facts(
    scene_db, monkeypatch, genre, name, plan, body, summary,
):
    session, project, _, scene, draft, character = scene_db
    project.genre = genre
    character.name = name
    scene.participants = [name, "absent-planned-person"]
    scene.exit_state = {name: plan}
    draft.content_md = body
    await session.commit()
    original_plan = deepcopy(scene.exit_state)
    calls, chunks = model_and_index(monkeypatch, summary=summary)
    workflow = uuid4()
    result = await refresh(scene_db, workflow_run_id=workflow)
    await session.commit()
    await session.refresh(character)
    facts = list(await session.scalars(select(CanonFactModel)))
    assert len(calls) == 1
    assert calls[0].project_id == project.id and calls[0].workflow_run_id == workflow
    assert calls[0].prompt_version == "2.0"
    assert body in calls[0].user_prompt
    assert "计划升级" not in calls[0].user_prompt
    assert "absent-planned-person" not in calls[0].user_prompt
    assert result.summary_text == summary
    assert result.canon_facts_created == 2
    assert {f.predicate for f in facts} == {"latest_story_turn", "scene_summary"}
    for fact in facts:
        assert fact.value_json["summary"] == summary
        evidence = fact.value_json["source_evidence"]
        assert evidence["source_draft_version_id"] == str(draft.id)
        assert evidence["source_body_sha256"] == hashlib.sha256(body.encode()).hexdigest()
        assert evidence["summary_kind"] == "model_summary"
        assert "body_span" not in evidence  # No fabricated quotation evidence.
        assert "story_purpose" not in fact.value_json
    assert (character.arc_state, character.power_tier) == ("仍在尝试", "初级")
    assert character.metadata_json == {"keep": "original"}
    assert list(await session.scalars(select(CharacterStateSnapshotModel))) == []
    assert len(list(await session.scalars(select(CharacterModel)))) == 1
    event = await session.scalar(select(TimelineEventModel))
    assert event.consequences == [summary]
    assert event.participant_ids == []
    assert "计划次日" != event.story_time_label
    assert scene.exit_state == original_plan
    assert summary in chunks[0] and body in chunks[0]
    assert "计划升级" not in chunks[0] and "absent-planned-person" not in chunks[0]
    assert "计划胜利" not in chunks[0]
    assert (await knowledge.propagate_scene_discoveries(
        session, project.id, 1, 1, result,
    )) == {"characters_updated": 0, "relationships_updated": 0}


@pytest.mark.parametrize("response", [None, "", "   "])
async def test_empty_summary_fallback_is_an_exact_body_excerpt(scene_db, monkeypatch, response):
    session, _, _, _, draft, _ = scene_db
    draft.content_md = "  " + draft.content_md * 100
    await session.commit()
    calls, _ = model_and_index(monkeypatch, summary=response)
    result = await refresh(scene_db)
    assert result.summary_text == draft.content_md[:1200]
    fact = await session.scalar(select(CanonFactModel).where(
        CanonFactModel.predicate == "scene_summary",
    ))
    evidence = fact.value_json["source_evidence"]
    assert evidence["summary_kind"] == "body_excerpt"
    span = evidence["body_span"]
    assert draft.content_md[span["start"]:span["end"]] == result.summary_text
    assert len(calls) == 1


@pytest.mark.parametrize("allow_candidate", [False, True])
async def test_unpromoted_body_is_rejected_before_model_or_writes(
    scene_db, monkeypatch, allow_candidate,
):
    session, _, _, _, draft, _ = scene_db
    draft.promotion_state = "candidate"
    await session.commit()
    calls, chunks = model_and_index(monkeypatch)
    with pytest.raises(ValueError, match="promoted"):
        await refresh(scene_db, allow_current_candidate=allow_candidate)
    assert not calls and not chunks
    assert not list(await session.scalars(select(CanonFactModel)))


async def test_empty_body_cannot_use_plan_fallback(scene_db, monkeypatch):
    session, _, _, _, draft, _ = scene_db
    draft.content_md = " \n "
    await session.commit()
    calls, _ = model_and_index(monkeypatch)
    with pytest.raises(ValueError, match="scene_knowledge_body_required"):
        await refresh(scene_db)
    assert not calls
    assert not list(await session.scalars(select(CanonFactModel)))


def legacy_fact(scene_db, *, current=True):
    _, project, chapter, scene, draft, character = scene_db
    return CanonFactModel(
        project_id=project.id, subject_type="character", subject_id=character.id,
        subject_label=character.name, predicate="last_known_state", fact_type="state",
        value_json={
            "chapter_number": 1, "scene_number": 1,
            "state": {"power_tier": "高级"}, "summary": "真实正文摘要应保留",
            "source_draft_version_id": str(draft.id),
        },
        source_type="extracted", source_scene_id=scene.id, source_chapter_id=chapter.id,
        valid_from_chapter_no=1, is_current=current, tags=["chapter:1", "scene:1", "state"],
    )


@pytest.mark.parametrize("current", [True, False])
async def test_legacy_claims_are_reported_without_deletion_or_guessed_restoration(
    scene_db, monkeypatch, current,
):
    session, project, chapter, scene, _, character = scene_db
    fact = legacy_fact(scene_db, current=current)
    snapshot = CharacterStateSnapshotModel(
        project_id=project.id, character_id=character.id, chapter_id=chapter.id,
        scene_card_id=scene.id, chapter_number=1, scene_number=1,
        power_tier="高级", source_binding={"kind": "scene", "keep": "binding"},
    )
    # Plans may have been revised after the old producer ran; equality is not provenance.
    scene.exit_state = {"changed-plan": {"power_tier": "different"}}
    character.power_tier = "高级"
    session.add_all([fact, snapshot])
    await session.commit()
    original = deepcopy((fact.value_json, snapshot.source_binding, character.metadata_json))
    report = await knowledge.audit_scene_plan_knowledge(session, project_id=project.id)
    assert report["status"] == "reconciliation_required"
    assert report["canon_claims"][0]["fact_id"] == str(fact.id)
    assert report["canon_claims"][0]["is_current"] is current
    assert report["snapshot_claims"][0]["snapshot_id"] == str(snapshot.id)
    assert report["character_projection_review_ids"] == [str(character.id)]
    assert "historical_canon_consumers_do_not_honor_supersession" in report["blockers"]
    calls, _ = model_and_index(monkeypatch)
    with pytest.raises(knowledge.ScenePlanKnowledgeReconciliationRequiredError) as error:
        await refresh(scene_db)
    assert error.value.report == report
    assert not calls
    await session.commit()
    await session.refresh(fact)
    await session.refresh(snapshot)
    await session.refresh(character)
    assert original == (fact.value_json, snapshot.source_binding, character.metadata_json)
    assert character.power_tier == "高级"  # Unknown prior value is not guessed.
    assert fact.is_current is current
    assert len(list(await session.scalars(select(CanonFactModel)))) == 1


async def test_body_summary_history_is_preserved_and_not_classified_as_plan_state(
    scene_db, monkeypatch,
):
    session, project, chapter, scene, _, _ = scene_db
    summary = "她没能晋级。"
    old = CanonFactModel(
        project_id=project.id, subject_type="scene_card", subject_id=scene.id,
        subject_label="scene", predicate="scene_summary", fact_type="scene_summary",
        value_json={"summary": summary}, source_type="extracted",
        source_scene_id=scene.id, source_chapter_id=chapter.id, tags=[], is_current=True,
    )
    session.add(old)
    await session.commit()
    assert (await knowledge.audit_scene_plan_knowledge(
        session, project_id=project.id,
    ))["status"] == "clear"
    model_and_index(monkeypatch, summary=summary)
    await refresh(scene_db)
    await session.commit()
    await session.refresh(old)
    assert old.value_json == {"summary": summary}
    assert old.is_current is False
    assert await session.get(CanonFactModel, old.id) is old


@pytest.mark.parametrize("exit_state", [
    {}, {"岑禾": "已获准晋级"}, {"global": {"winner": "岑禾"}},
])
async def test_missing_or_unstructured_state_does_not_fall_back_to_purpose(
    scene_db, monkeypatch, exit_state,
):
    session, _, _, scene, _, character = scene_db
    scene.exit_state = exit_state
    await session.commit()
    model_and_index(monkeypatch)
    await refresh(scene_db)
    assert {f.subject_type for f in await session.scalars(select(CanonFactModel))} == {
        "project", "scene_card",
    }
    assert character.power_tier == "初级"
    assert not list(await session.scalars(select(CharacterStateSnapshotModel)))


async def test_legacy_audit_is_project_scoped_and_keeps_independent_evidence(
    scene_db, monkeypatch,
):
    session, project, chapter, scene, _, character = scene_db
    other = ProjectModel(
        slug="other-test", title="other", genre="other", target_word_count=10000, target_chapters=4,
    )
    session.add(other)
    await session.flush()
    other_fact = legacy_fact(scene_db)
    other_fact.project_id = other.id
    observed = legacy_fact(scene_db)
    observed.value_json = {
        **observed.value_json, "evidence": {"quote": "正文证据", "start": 0, "end": 4},
    }
    observed.source_type = "manual"
    observed_snapshot = CharacterStateSnapshotModel(
        project_id=project.id, character_id=character.id, chapter_id=chapter.id,
        chapter_number=1, scene_number=None, power_tier="初级",
        source_binding={"kind": "adopted_feedback"},
    )
    session.add_all([other_fact, observed, observed_snapshot])
    await session.commit()
    assert (await knowledge.audit_scene_plan_knowledge(
        session, project_id=project.id,
    ))["status"] == "clear"
    assert (await knowledge.audit_scene_plan_knowledge(
        session, project_id=other.id,
    ))["canon_claims"][0]["fact_id"] == str(other_fact.id)
    model_and_index(monkeypatch)
    await refresh(scene_db)
    await session.commit()
    assert observed.is_current
    assert observed_snapshot.power_tier == "初级"


async def test_plan_presence_alone_requires_reconciliation(scene_db, monkeypatch):
    session, project, _, scene, draft, _ = scene_db
    fact = legacy_fact(scene_db)
    fact.predicate, fact.fact_type, fact.tags = "last_seen_scene", "presence", ["presence"]
    fact.value_json = {
        "chapter_number": 1, "scene_number": 1, "scene_title": scene.title,
        "time_label": scene.time_label, "scene_type": scene.scene_type,
        "source_draft_version_id": str(draft.id),
    }
    session.add(fact)
    await session.commit()
    report = await knowledge.audit_scene_plan_knowledge(session, project_id=project.id)
    assert report["canon_claims"][0]["predicate"] == "last_seen_scene"
    calls, _ = model_and_index(monkeypatch)
    with pytest.raises(knowledge.ScenePlanKnowledgeReconciliationRequiredError):
        await refresh(scene_db)
    assert not calls


@pytest.mark.parametrize("kind", [None, "scene"])
async def test_orphan_scene_snapshot_without_canon_is_still_reported(scene_db, kind):
    session, project, chapter, scene, _, character = scene_db
    snapshot = CharacterStateSnapshotModel(
        project_id=project.id, character_id=character.id, chapter_id=chapter.id,
        scene_card_id=scene.id, chapter_number=1, scene_number=1,
        source_binding={"kind": kind} if kind else None,
    )
    session.add(snapshot)
    await session.commit()
    report = await knowledge.audit_scene_plan_knowledge(session, project_id=project.id)
    assert report["status"] == "reconciliation_required"
    assert report["canon_claims"] == []
    assert report["snapshot_claims"][0]["snapshot_id"] == str(snapshot.id)


async def test_legacy_timeline_is_not_deleted_when_no_character_facts_exist(scene_db, monkeypatch):
    session, project, chapter, scene, draft, _ = scene_db
    event = TimelineEventModel(
        project_id=project.id, chapter_id=chapter.id, scene_card_id=scene.id,
        event_name="legacy", event_type=scene.scene_type, story_time_label="计划次日",
        story_order=1.01, participant_ids=scene.participants,
        consequences=[scene.purpose["story"]],
        metadata_json={"summary": "真实正文摘要", "source_draft_version_id": str(draft.id)},
    )
    session.add(event)
    await session.commit()
    calls, _ = model_and_index(monkeypatch)
    with pytest.raises(knowledge.ScenePlanKnowledgeReconciliationRequiredError) as error:
        await refresh(scene_db)
    assert error.value.report["timeline_claims"][0]["event_id"] == str(event.id)
    assert not calls
    await session.commit()
    await session.refresh(event)
    assert event.metadata_json["summary"] == "真实正文摘要"
    assert event.consequences == [scene.purpose["story"]]
