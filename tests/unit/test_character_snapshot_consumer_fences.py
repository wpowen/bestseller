"""Real SQLite reader APIs must not revive unknown, retired or rejected state."""

from copy import deepcopy
import socket
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import db as db

from bestseller.domain.feedback import CharacterStateExtraction
from bestseller.infra.db import models
from bestseller.services import (
    bible_gate,
    character_evolution,
    consistency,
    contradiction,
    feedback,
    planning_context,
    volume_audit,
)
from bestseller.services.character_snapshot_sources import (
    seal_snapshot_binding,
    snapshot_binding,
)
from bestseller.services.feedback import validate_adopted_knowledge_source
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("snapshot consumer regression forbids network/providers")

    monkeypatch.setenv("BESTSELLER_LINEAGE_PG", "0")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
async def case(db):
    session, project, *_ = db
    assert session.bind.dialect.name == "sqlite"
    for model in (
        models.CharacterStateSnapshotModel,
        models.VolumeModel,
        models.ArcBeatModel,
        models.SceneCardModel,
        models.SceneDraftVersionModel,
        models.ChapterContractModel,
        models.PayoffModel,
        models.WorldRuleModel,
        models.RewriteTaskModel,
        models.ExportArtifactModel,
        models.SubplotScheduleModel,
        models.PlanningArtifactVersionModel,
    ):
        ddl = str(
            CreateTable(model.__table__, if_not_exists=True).compile(dialect=session.bind.dialect)
        )
        await session.execute(text(ddl.replace("::jsonb", "")))
    project.invariants_json = {"power_system": {"tiers": ["low", "middle", "high", "peak"]}}
    character = models.CharacterModel(
        project_id=project.id,
        name="Observer",
        role="protagonist",
        stance="ally",
        knowledge_state_json={},
        metadata_json={},
    )
    session.add(character)
    await session.commit()
    return session, project, character


async def chapter_source(case, number):
    session, project, _ = case
    chapter = await session.scalar(
        select(models.ChapterModel).where(
            models.ChapterModel.project_id == project.id,
            models.ChapterModel.chapter_number == number,
        )
    )
    if chapter is None:
        chapter = models.ChapterModel(
            project_id=project.id,
            chapter_number=number,
            chapter_goal="reader source fence",
            metadata_json={},
        )
        session.add(chapter)
        await session.flush()
    draft = await session.scalar(
        select(models.ChapterDraftVersionModel).where(
            models.ChapterDraftVersionModel.chapter_id == chapter.id,
            models.ChapterDraftVersionModel.promotion_state == "promoted",
        )
    )
    if draft is None:
        draft = models.ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=chapter.id,
            version_no=1,
            content_md=f"Observer records the evidence in chapter {number}.",
            promotion_state="promoted",
            is_current=True,
            promotion_metadata={},
        )
        session.add(draft)
        await session.flush()
    source, rejection = await validate_adopted_knowledge_source(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id,
        chapter_md=draft.content_md,
        lock=False,
    )
    assert rejection is None
    return chapter, source


async def accepted(case, number, **values):
    session, project, character = case
    chapter, source = await chapter_source(case, number)
    previous = set(await session.scalars(select(models.CharacterStateSnapshotModel.id)))
    await feedback._apply_character_state_updates(
        session,
        project.id,
        chapter,
        [CharacterStateExtraction(character_name=character.name, **values)],
        source_lineage=source,
    )
    row = next(
        row
        for row in await session.scalars(select(models.CharacterStateSnapshotModel))
        if row.id not in previous
    )
    await session.refresh(character)
    return row


async def poison(case, number, *, kind="unknown", **values):
    session, project, character = case
    chapter, source = await chapter_source(case, number)
    binding = None
    if kind != "unknown":
        if kind == "retired":
            source = {**source, "source_body_sha256": "0" * 64}
        elif kind == "foreign":
            source = {**source, "project_id": str(uuid4())}
        binding = snapshot_binding(
            "adopted_feedback",
            project_id=project.id,
            character_id=character.id,
            chapter_number=number,
            adopted_source=source,
        )
    row = models.CharacterStateSnapshotModel(
        project_id=project.id,
        character_id=character.id,
        chapter_id=chapter.id,
        chapter_number=number,
        scene_number=None,
        trust_map={},
        beliefs=values.pop("beliefs", []),
        source_binding=binding,
        **values,
    )
    row.source_binding = seal_snapshot_binding(row, binding)
    session.add(row)
    await session.flush()
    return row


async def raw_inventory(session):
    rows = list(
        await session.scalars(
            select(models.CharacterStateSnapshotModel).order_by(
                models.CharacterStateSnapshotModel.id,
            )
        )
    )
    return [
        deepcopy({column.key: getattr(row, column.key) for column in row.__table__.columns})
        for row in rows
    ]


async def test_knowledge_uses_bound_prefix_without_current_future_or_beliefs_as_knows(case):
    session, project, character = case
    await accepted(case, 1, knowledge_gained=["known at one"], beliefs_gained=["belief at one"])
    await accepted(case, 4, knowledge_gained=["future knowledge"])
    await poison(case, 1, beliefs=["unknown claim"])
    await session.commit()
    before = await raw_inventory(session)
    state = await character_evolution.get_character_knowledge_state(
        session,
        project.id,
        character.name,
        1,
    )
    assert state.knows == ["known at one"]
    assert state.falsely_believes == [] and state.unaware_of == []
    assert await raw_inventory(session) == before


async def test_actual_source_replacement_retires_old_snapshots_without_rebinding(case):
    session, project, character = case
    old = await accepted(case, 1, knowledge_gained=["old draft knowledge"], arc_state="old")
    draft = await session.get(
        models.ChapterDraftVersionModel,
        UUID(old.source_binding["adopted_source"]["source_chapter_draft_version_id"]),
    )
    draft.promotion_state, draft.is_current = "superseded", False
    await session.flush()
    session.add(
        models.ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=old.chapter_id,
            version_no=2,
            content_md="Observer adopted a different recorded observation.",
            promotion_state="promoted",
            is_current=True,
            promotion_metadata={},
        )
    )
    await session.flush()
    current = await accepted(case, 1, knowledge_gained=["current draft knowledge"], arc_state="new")
    await session.commit()
    before = await raw_inventory(session)
    state = await character_evolution.get_character_knowledge_state(
        session,
        project.id,
        character.name,
        1,
    )
    timeline = await character_evolution.get_character_evolution_timeline(
        session,
        project.id,
        character.name,
    )
    assert state.knows == ["current draft knowledge"]
    assert [row["arc_state"] for row in timeline] == [current.arc_state]
    assert await raw_inventory(session) == before


async def test_same_chapter_scene_knowledge_cutoff_and_timeline_order_survive(case):
    from bestseller.services.character_snapshot_sources import read_character_snapshot_knowledge
    from bestseller.services.drafts import render_chapter_draft_markdown
    from bestseller.services.knowledge import _upsert_character_state_snapshot

    session, project, character = case
    chapter, source = await chapter_source(case, 1)
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
        draft = models.SceneDraftVersionModel(
            project_id=project.id,
            scene_card_id=scene.id,
            version_no=1,
            content_md=f"Observer records scene {number}.",
            promotion_state="promoted",
            is_current=True,
        )
        session.add(draft)
        await session.flush()
        scenes.append(scene)
        drafts.append(draft)
    adopted = await session.get(
        models.ChapterDraftVersionModel, UUID(source["source_chapter_draft_version_id"])
    )
    adopted.assembled_from_scene_draft_ids = [str(draft.id) for draft in drafts]
    adopted.content_md = render_chapter_draft_markdown(chapter, drafts, language=project.language)
    await session.flush()
    for scene, draft in zip(scenes, drafts, strict=True):
        await _upsert_character_state_snapshot(
            session,
            project_id=project.id,
            character=character,
            chapter=chapter,
            scene=scene,
            state_payload={
                "arc_state": f"scene {scene.scene_number}",
                "knowledge_state": {"knows": [f"scene {scene.scene_number} knowledge"]},
            },
            summary_text="source-bound scene observation",
            source_scene_draft=draft,
        )
    await session.commit()
    before = await raw_inventory(session)
    prefix = await read_character_snapshot_knowledge(
        session,
        project_id=project.id,
        character=character,
        before_chapter_number=1,
        before_scene_number=2,
    )
    current = await character_evolution.get_character_knowledge_state(
        session,
        project.id,
        character.name,
        1,
    )
    timeline = await character_evolution.get_character_evolution_timeline(
        session,
        project.id,
        character.name,
    )
    assert prefix["knows"] == ["scene 1 knowledge"]
    assert current.knows == ["scene 2 knowledge"]
    assert [row["scene_number"] for row in timeline] == [1, 2]
    assert await raw_inventory(session) == before


@pytest.mark.parametrize("kind", ["unknown", "retired", "foreign"])
async def test_timeline_and_volume_feedback_filter_before_latest_dedup(case, kind):
    session, project, character = case
    good = await accepted(case, 1, arc_state="accepted arc", power_tier="low")
    await poison(case, 2, kind=kind, arc_state="rejected arc", power_tier="peak")
    other = models.ProjectModel(
        slug=f"other-{uuid4().hex}",
        title="other",
        genre="test",
        target_word_count=1000,
        target_chapters=1,
    )
    session.add(other)
    await session.flush()
    session.add(
        models.CharacterStateSnapshotModel(
            project_id=other.id,
            character_id=character.id,
            chapter_number=3,
            arc_state="other project",
            trust_map={},
            beliefs=[],
        )
    )
    session.add(
        models.VolumeModel(
            project_id=project.id,
            volume_number=1,
            title="One",
            metadata_json={"chapter_start": 1, "chapter_end": 3},
        )
    )
    await session.commit()
    before = await raw_inventory(session)
    timeline = await character_evolution.get_character_evolution_timeline(
        session,
        project.id,
        character.name,
    )
    report = await planning_context.collect_volume_writing_feedback(session, project.id, 1)
    assert [row["arc_state"] for row in timeline] == ["accepted arc"]
    assert len(report["cast_evolution"]) == 1
    assert report["cast_evolution"][0]["chapter_number"] == good.chapter_number
    assert report["cast_evolution"][0]["arc_state"] == "accepted arc"
    assert await raw_inventory(session) == before


async def test_stagnation_filters_before_two_row_limit_and_current_chapter_cutoff(case):
    session, project, character = case
    for number in (1, 2):
        await accepted(case, number, arc_state="same", power_tier="low")
    for number in range(3, 72):
        await poison(case, number, arc_state="unknown")
    await accepted(case, 100, arc_state="future")
    await session.commit()
    warnings = await character_evolution.detect_character_stagnation(session, project.id, 80)
    assert len(warnings) == 1
    assert warnings[0].character_name == character.name
    assert warnings[0].last_update_chapter == 2
    assert warnings[0].chapters_since_update == 78


@pytest.mark.parametrize("kind", ["unknown", "retired", "foreign"])
async def test_chapter_bible_gate_rejects_ineligible_current_stance(case, kind):
    session, project, _ = case
    await accepted(case, 1, stance="ally")
    await poison(case, 2, kind=kind, stance="enemy")
    result = await bible_gate.validate_chapter_against_bible(
        session,
        project_id=project.id,
        chapter_number=2,
    )
    assert result.passed and not result.violations


@pytest.mark.parametrize("kind", ["unknown", "retired", "foreign", "accepted_null"])
async def test_stance_prior_fallback_ignores_invalid_and_null_before_selection(case, kind):
    session, project, character = case
    await accepted(case, 1, stance="ally")
    if kind == "accepted_null":
        await accepted(case, 2, arc_state="no stance observation")
    else:
        await poison(case, 2, kind=kind, stance="enemy")
    await accepted(case, 3, stance="enemy", stance_change_reason="an explicit betrayal")
    character.stance_locked_until_chapter = 3
    await session.commit()
    before = await raw_inventory(session)
    gate = await bible_gate.validate_chapter_against_bible(
        session,
        project_id=project.id,
        chapter_number=3,
    )
    violations, _ = await contradiction._check_stance_flip(
        session,
        project.id,
        3,
        [character.name],
    )
    assert not gate.passed and len(gate.violations) == 1
    assert len(violations) == 1 and violations[0].check_type == "character_stance_flip_locked"
    assert await raw_inventory(session) == before


async def test_rejected_current_stance_does_not_become_a_bible_violation(case):
    session, project, _ = case
    await accepted(case, 1, stance="ally")
    raw = await accepted(case, 2, stance="enemy")  # no reason: retained raw, rejected effect
    assert raw.stance == "enemy"
    assert raw.source_binding["accepted_fields"]["stance"] is False
    before = await raw_inventory(session)
    gate = await bible_gate.validate_chapter_against_bible(
        session,
        project_id=project.id,
        chapter_number=2,
    )
    assert gate.passed and not gate.violations
    assert await raw_inventory(session) == before


async def test_power_current_filter_and_prior_32_limit_use_only_eligible_values(case):
    session, project, character = case
    await accepted(case, 1, power_tier="peak")
    for number in range(2, 37):
        await poison(case, number, power_tier="low")
    await poison(case, 40, power_tier="peak")
    await accepted(case, 40, power_tier="low", power_tier_downgrade_reason="a sealing event")
    await session.commit()
    before = await raw_inventory(session)
    _, warnings = await contradiction._check_power_tier_regression(
        session,
        project.id,
        40,
        [character.name],
    )
    assert len(warnings) == 1 and warnings[0].check_type == "character_power_tier_regression"
    assert await raw_inventory(session) == before


async def test_volume_audit_ignores_unknown_retired_and_rejected_life_claims(case):
    session, project, character = case
    character.death_chapter_number = 1
    character.alive_status = "deceased"
    for number, kind in ((2, "unknown"), (3, "retired"), (4, "foreign")):
        await poison(case, number, kind=kind, alive_status="alive", power_tier=str(number))
    raw = await accepted(case, 5, alive_status="alive")
    assert raw.alive_status == "alive" and not raw.source_binding["accepted_fields"]["alive_status"]
    await session.commit()
    before = await raw_inventory(session)
    assert await volume_audit._resurrection_scan(session, project.id, 1, 5) == 0
    assert await volume_audit._power_tier_drift(session, project.id, 1, 5) == 0
    assert await raw_inventory(session) == before


async def test_volume_audit_keeps_eligible_tier_drift_and_life_evidence(case):
    session, project, character = case
    for number, tier in enumerate(("low", "middle", "high"), 1):
        await accepted(case, number, power_tier=tier, alive_status="alive")
    character.death_chapter_number = 1
    await session.commit()
    assert await volume_audit._resurrection_scan(session, project.id, 1, 3) == 1
    assert await volume_audit._power_tier_drift(session, project.id, 1, 3) == 1


async def test_consistency_public_loader_passes_only_eligible_protagonist_evidence(
    case, monkeypatch
):
    session, project, _ = case
    good = await accepted(case, 1, arc_state="accepted")
    await poison(case, 2, arc_state="unknown")
    await poison(case, 3, kind="retired", arc_state="retired")
    await session.commit()
    before = await raw_inventory(session)
    observed = []
    actual = consistency._collect_narrative_review_signals

    def capture(**kwargs):
        observed.extend(kwargs["protagonist_snapshots"])
        return actual(**kwargs)

    class ReadBoundaryReachedError(Exception):
        pass

    async def stop_before_model(*args, **kwargs):
        raise ReadBoundaryReachedError

    monkeypatch.setattr(consistency, "_collect_narrative_review_signals", capture)
    monkeypatch.setattr(consistency, "complete_text", stop_before_model)
    with pytest.raises(ReadBoundaryReachedError):
        await consistency.review_project_consistency(session, load_settings(), project.slug)
    assert [row.id for row in observed] == [good.id]
    assert await raw_inventory(session) == before
