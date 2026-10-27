"""Blocked adopted bodies cannot contribute character state; SQLite only."""

import asyncio
from copy import deepcopy
import socket
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import db as db

from bestseller.domain.feedback import CharacterStateExtraction
from bestseller.infra.db import models
from bestseller.services import character_snapshot_sources as sources
from bestseller.services import feedback, llm, story_bible
from bestseller.services.adopted_chapter_source import select_adopted_chapter_source

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("BESTSELLER_LINEAGE_PG", "0")

    def forbidden(*args, **kwargs):
        raise AssertionError("blocked snapshot tests forbid network/providers")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(feedback, "complete_text", forbidden)


@pytest.fixture
async def case(db):
    session, project, first, first_draft = db
    assert session.bind.dialect.name == "sqlite"
    assert "test" in session.bind.engine.url.database
    character = models.CharacterModel(
        project_id=project.id,
        name="Witness",
        role="protagonist",
        stance="ally",
        knowledge_state_json={},
        metadata_json={},
    )
    chapter = models.ChapterModel(
        project_id=project.id,
        chapter_number=2,
        chapter_goal="snapshot source",
        metadata_json={},
    )
    session.add_all([character, chapter])
    await session.flush()
    adopted = models.ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=9,
        content_md="Witness found the old key.",
        promotion_state="promoted",
        is_current=True,
    )
    session.add(adopted)
    await session.flush()
    rows = []
    for ch, draft, arc, known in (
        (first, first_draft, "first arc", "first knowledge"),
        (chapter, adopted, "withdrawn arc", "withdrawn knowledge"),
    ):
        lineage, rejection = await feedback.validate_adopted_knowledge_source(
            session,
            project_id=project.id,
            chapter_id=ch.id,
            source_chapter_draft_version_id=draft.id,
            chapter_md=draft.content_md,
            lock=False,
        )
        assert rejection is None
        await feedback._apply_character_state_updates(
            session,
            project.id,
            ch,
            [
                CharacterStateExtraction(
                    character_name=character.name,
                    arc_state=arc,
                    knowledge_gained=[known],
                    beliefs_gained=[f"{arc} belief"],
                )
            ],
            source_lineage=lineage,
        )
        rows.append(
            await session.scalar(
                select(models.CharacterStateSnapshotModel).where(
                    models.CharacterStateSnapshotModel.chapter_id == ch.id,
                )
            )
        )
    adopted.is_current = False
    await session.flush()
    candidate = models.ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=11,
        content_md="Witness is revising the old account.",
        promotion_state="candidate",
        is_current=True,
    )
    session.add(candidate)
    await session.commit()
    await session.refresh(character)
    return session, project, character, chapter, adopted, candidate, rows


def block(chapter, draft, *, mode="exact"):
    receipt = {
        "draft_id": str(draft.id),
        "body_sha256": sources.body_hash(draft.content_md),
        "passed": False,
    }
    if mode == "other_body":
        receipt["body_sha256"] = sources.body_hash("different body")
    elif mode == "other_draft":
        receipt["draft_id"] = str(uuid4())
    key = f"{receipt['draft_id']}:{receipt['body_sha256']}"
    chapter.metadata_json = {
        **chapter.metadata_json,
        "continuity_admission_by_source": {key: receipt},
        # An unrelated latest pass must not erase the exact ledger block.
        "continuity_admission": {
            "draft_id": str(uuid4()),
            "body_sha256": "f" * 64,
            "passed": True,
        },
    }


async def inventory(session):
    return deepcopy(
        [
            (
                model.__tablename__,
                [
                    dict(row)
                    for row in (
                        await session.execute(select(model.__table__).order_by(model.id))
                    ).mappings()
                ],
            )
            for model in (
                models.CharacterModel,
                models.ChapterModel,
                models.ChapterDraftVersionModel,
                models.CharacterStateSnapshotModel,
            )
        ]
    )


@pytest.mark.parametrize("mode", ["exact", "other_body", "other_draft", "none"])
async def test_actual_continuity_ledger_gates_all_character_readers_without_mutation(case, mode):
    session, project, character, chapter, adopted, candidate, rows = case
    if mode != "none":
        block(chapter, adopted, mode=mode)
    await session.commit()
    before = await inventory(session)
    selected, _ = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
    blocked = mode == "exact"
    assert (selected is None) == blocked
    assert candidate.is_current and not adopted.is_current
    assert adopted.promotion_state == "promoted"
    old_source = rows[1].source_binding["adopted_source"]
    assert (await sources._adopted_source(session, project.id, old_source) is None) == blocked
    latest = await story_bible.get_latest_character_state(
        session,
        project_id=project.id,
        character_id=character.id,
    )
    assert isinstance(latest, sources.CharacterSnapshotView)
    assert latest.raw.id == rows[0 if blocked else 1].id
    known = await sources.read_character_snapshot_knowledge(
        session,
        project_id=project.id,
        character=character,
    )
    effective = await story_bible.get_effective_character_state(
        session,
        project_id=project.id,
        character=character,
    )
    assert known["knows"] == (
        ["first knowledge"]
        if blocked
        else [
            "first knowledge",
            "withdrawn knowledge",
        ]
    )
    assert effective.knowledge_state == known
    assert effective.arc_state == ("first arc" if blocked else "withdrawn arc")
    if blocked:
        assert "withdrawn" not in repr(known)
        assert effective.previous_arc_state is None
        # Mutable producer projections remain available as raw audit evidence.
        assert character.arc_state == "withdrawn arc"
        assert "withdrawn knowledge" in repr(character.knowledge_state_json)
    assert await inventory(session) == before


@pytest.mark.parametrize("mode", ["block", "replacement", "ambiguous", "body_changed"])
async def test_reader_independently_checks_canonical_id_and_body_even_with_legacy_lineage(
    case,
    monkeypatch,
    mode,
):
    session, project, _, chapter, adopted, candidate, rows = case
    source = deepcopy(rows[1].source_binding["adopted_source"])
    if mode == "block":
        block(chapter, adopted)
    elif mode == "replacement":
        candidate.promotion_state = "promoted"
    elif mode == "ambiguous":
        candidate.promotion_state = "promoted"
        candidate.is_current = False
    else:
        adopted.content_md = "Changed in place without a matching snapshot."
    await session.commit()

    async def legacy_lineage(*args, **kwargs):
        return source, None

    monkeypatch.setattr(sources, "validate_adopted_knowledge_source", legacy_lineage)
    before = await inventory(session)
    assert await sources._adopted_source(session, project.id, source) is None
    assert await inventory(session) == before


async def test_new_adopted_source_replaces_old_without_rebinding_historical_snapshot(case):
    session, project, character, chapter, adopted, candidate, rows = case
    block(chapter, adopted)
    candidate.promotion_state = "promoted"
    await session.commit()
    lineage, rejection = await feedback.validate_adopted_knowledge_source(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=candidate.id,
        chapter_md=candidate.content_md,
        lock=False,
    )
    assert rejection is None
    await feedback._apply_character_state_updates(
        session,
        project.id,
        chapter,
        [
            CharacterStateExtraction(
                character_name=character.name,
                arc_state="new arc",
                knowledge_gained=["new knowledge"],
            )
        ],
        source_lineage=lineage,
    )
    await session.commit()
    before = await inventory(session)
    assert (
        await sources._adopted_source(
            session,
            project.id,
            rows[1].source_binding["adopted_source"],
        )
        is None
    )
    latest = await story_bible.get_latest_character_state(
        session,
        project_id=project.id,
        character_id=character.id,
    )
    assert latest.arc_state == "new arc"
    effective = await story_bible.get_effective_character_state(
        session,
        project_id=project.id,
        character=character,
    )
    assert effective.knowledge_state["knows"] == ["first knowledge", "new knowledge"]
    assert effective.previous_arc_state == "first arc"
    assert "withdrawn" not in repr(effective)
    assert await inventory(session) == before


@pytest.mark.parametrize("binding", [None, {}, {"schema": "unknown"}, "malformed"])
async def test_latest_unknown_or_malformed_binding_has_no_mutable_fallback(case, binding):
    session, project, character, _, _, _, rows = case
    for row in rows:
        row.source_binding = binding
    await session.commit()
    before = await inventory(session)
    assert (
        await story_bible.get_latest_character_state(
            session,
            project_id=project.id,
            character_id=character.id,
        )
        is None
    )
    assert (
        await sources.read_character_snapshot_knowledge(
            session,
            project_id=project.id,
            character=character,
        )
        == {}
    )
    effective = await story_bible.get_effective_character_state(
        session,
        project_id=project.id,
        character=character,
    )
    assert effective.arc_state is None and effective.knowledge_state == {}
    assert await inventory(session) == before


@pytest.mark.parametrize(
    "cutoff,scene,expected",
    [
        (None, None, 1),
        (2, None, 1),
        (2, 1, 1),
        (1, None, 1),
        (1, 1, None),
        (0, None, None),
    ],
)
async def test_latest_blocked_raw_row_falls_back_to_eligible_prefix(case, cutoff, scene, expected):
    session, project, character, chapter, adopted, _, rows = case
    block(chapter, adopted)
    await session.commit()
    before = await inventory(session)
    latest = await story_bible.get_latest_character_state(
        session,
        project_id=project.id,
        character_id=character.id,
        before_chapter_number=cutoff,
        before_scene_number=scene,
    )
    assert (latest.chapter_number if latest else None) == expected
    if latest:
        assert latest.raw.id == rows[0].id
    assert await inventory(session) == before


async def test_latest_preserves_rejected_accepted_fields_on_view(case):
    session, project, character, _, _, _, rows = case
    row = rows[1]
    row.stance = "enemy"
    row.alive_status = "deceased"
    row.source_binding = sources.seal_snapshot_binding(
        row,
        {
            **row.source_binding,
            "accepted_fields": {"alive_status": False, "stance": False},
        },
    )
    await session.commit()
    before = await inventory(session)
    latest = await story_bible.get_latest_character_state(
        session,
        project_id=project.id,
        character_id=character.id,
    )
    assert latest.raw.id == row.id
    assert latest.stance is None and latest.alive_status is None
    assert latest.raw.stance == "enemy" and latest.raw.alive_status == "deceased"
    assert await inventory(session) == before


async def test_latest_scene_cutoff_uses_actual_eligible_scene_snapshots(case):
    from bestseller.services.drafts import render_chapter_draft_markdown
    from bestseller.services.knowledge import _upsert_character_state_snapshot

    session, project, character, blocked_chapter, adopted, _, _ = case
    block(blocked_chapter, adopted)
    conn = await session.connection()
    for model in (models.SceneCardModel, models.SceneDraftVersionModel):
        ddl = str(CreateTable(model.__table__).compile(dialect=conn.dialect))
        await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    chapter = await session.scalar(
        select(models.ChapterModel).where(
            models.ChapterModel.project_id == project.id,
            models.ChapterModel.chapter_number == 1,
        )
    )
    draft = await session.scalar(
        select(models.ChapterDraftVersionModel).where(
            models.ChapterDraftVersionModel.chapter_id == chapter.id,
        )
    )
    scenes, drafts = [], []
    for number in (1, 2):
        scene = models.SceneCardModel(
            project_id=project.id,
            chapter_id=chapter.id,
            scene_number=number,
            scene_type="dialogue",
            participants=[character.name],
            metadata_json={},
        )
        session.add(scene)
        await session.flush()
        scene_draft = models.SceneDraftVersionModel(
            project_id=project.id,
            scene_card_id=scene.id,
            version_no=1,
            content_md=f"Witness records scene {number}.",
            promotion_state="promoted",
            is_current=True,
        )
        session.add(scene_draft)
        await session.flush()
        scenes.append(scene)
        drafts.append(scene_draft)
    draft.content_md = render_chapter_draft_markdown(chapter, drafts, language=project.language)
    draft.assembled_from_scene_draft_ids = [str(row.id) for row in drafts]
    await session.flush()
    for scene, scene_draft in zip(scenes, drafts, strict=True):
        await _upsert_character_state_snapshot(
            session,
            project_id=project.id,
            character=character,
            chapter=chapter,
            scene=scene,
            state_payload={"arc_state": f"scene {scene.scene_number}"},
            summary_text="eligible scene evidence",
            source_scene_draft=scene_draft,
        )
    await session.commit()
    before = await inventory(session)
    for chapter_no, scene_no, expected in ((1, 1, None), (1, 2, 1), (1, 3, 2), (2, 1, 2)):
        latest = await story_bible.get_latest_character_state(
            session,
            project_id=project.id,
            character_id=character.id,
            before_chapter_number=chapter_no,
            before_scene_number=scene_no,
        )
        assert (latest.scene_number if latest else None) == expected
        if latest:
            assert latest.arc_state == f"scene {expected}"
    assert await inventory(session) == before


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_selector_failure_propagates_from_all_character_readers(case, monkeypatch, failure):
    from bestseller.services import adopted_chapter_source

    session, project, character, _, _, _, rows = case

    async def fail(*args, **kwargs):
        raise failure("selector failed")

    monkeypatch.setattr(adopted_chapter_source, "select_adopted_chapter_source", fail)
    readers = [
        sources._adopted_source(session, project.id, rows[1].source_binding["adopted_source"]),
        story_bible.get_latest_character_state(
            session,
            project_id=project.id,
            character_id=character.id,
        ),
        sources.read_character_snapshot_knowledge(
            session,
            project_id=project.id,
            character=character,
        ),
        story_bible.get_effective_character_state(
            session,
            project_id=project.id,
            character=character,
        ),
    ]
    for reader in readers:
        with pytest.raises(failure, match="selector failed"):
            await reader
