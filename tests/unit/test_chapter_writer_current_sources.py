from copy import deepcopy
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_adopted_knowledge_lineage import db as db
from test_adopted_knowledge_lineage import stub_llm

from bestseller.infra.db.models import (
    CharacterModel,
    CharacterStateSnapshotModel,
    PlanningArtifactVersionModel,
)
from bestseller.services import feedback
from bestseller.services.chapter_writer_sources import (
    ChapterParticipantScope,
    project_current_writer_boundaries,
)
from bestseller.services.content_origin_policy import origin_record
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.writing_profile import (
    get_project_runtime_writing_profile,
    render_serial_fiction_guardrails,
)
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def local_only(monkeypatch):
    monkeypatch.delenv("BESTSELLER_LINEAGE_PG", raising=False)


@pytest.fixture
async def character_source(db):
    session, project, chapter, draft = db
    assert (await session.connection()).dialect.name == "sqlite"
    character = CharacterModel(
        project_id=project.id,
        name="Cal",
        role="ally",
        knowledge_state_json={},
        metadata_json={},
        power_tier="unranked",
    )
    session.add(character)
    await session.commit()
    return session, project, chapter, draft, character


async def _select_cast(case):
    session, project, _, _, character = case
    cast = {"protagonist": {"name": character.name}}
    artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type="cast_spec",
        version_no=1,
        status="approved",
        schema_version="test",
        content=cast,
    )
    session.add(artifact)
    await session.flush()
    project.metadata_json = {
        "cast_spec": cast,
        "active_foundation_selection": {
            "schema": "foundation-selection.v1",
            "project_id": str(project.id),
            "artifact_ids": {"cast_spec": str(artifact.id)},
            "artifact_content_hashes": {"cast_spec": content_hash(cast)},
        },
    }
    await session.commit()


async def _extract_character_state(case, monkeypatch, **state):
    session, project, chapter, draft, character = case
    calls = stub_llm(
        monkeypatch, feedback, {"character_states": [{"character_name": character.name, **state}]}
    )
    await feedback.extract_chapter_feedback(
        session,
        load_settings(),
        project_id=project.id,
        chapter=chapter,
        chapter_md=draft.content_md,
        source_chapter_draft_version_id=draft.id,
    )
    await session.commit()
    assert len(calls) == 1
    return await session.scalar(
        select(CharacterStateSnapshotModel).where(
            CharacterStateSnapshotModel.project_id == project.id,
            CharacterStateSnapshotModel.character_id == character.id,
            CharacterStateSnapshotModel.chapter_number == chapter.chapter_number,
        )
    )


def test_all_actors_use_first_scene_cutoff_without_mutating_scenes():
    scenes = [NS(id=uuid4(), scene_number=1, participants=['Ione']),
              NS(id=uuid4(), scene_number=3, participants=['Cal', 'Ione'])]
    before = deepcopy([s.__dict__ for s in scenes])
    scope = ChapterParticipantScope(scenes)
    assert scope.participants == ['Ione', 'Cal']
    assert scope.scene_number == 1 and scope.id == scenes[0].id
    assert [s.__dict__ for s in scenes] == before


def test_world_and_cast_values_hashes_survive_projection_exactly():
    world = {'forbidden_zones': ['Never turn the river into a sentient judge.'],
             'power_system': {'hard_limits': ['A seal cannot restore a dead witness.']}}
    cast = {'protagonist': {'name':'Ione','knowledge_state':{'unaware_of':['The keeper is alive.']},
                            'voice_profile':{'register':'terse'}},
            'supporting_cast':[{'name':'Cal','knowledge_state':{'knows':['No seal was opened.']}}]}
    artifacts = {'world_spec':NS(id=uuid4(),content=world), 'cast_spec':NS(id=uuid4(),content=cast)}
    before=deepcopy((world,cast)); out=project_current_writer_boundaries(artifacts, ['Ione','Cal'])
    assert out['fields']['/forbidden_zones']==world['forbidden_zones']
    assert out['fields']['/power_system/hard_limits']==world['power_system']['hard_limits']
    assert [r['name'] for r in out['cast_projection']['participants']]==['Ione','Cal']
    assert all('knowledge_state' not in row for row in out['cast_projection']['participants'])
    assert out['cast_projection']['withheld_fields'][1]['reason']=='foundation_cast_has_no_verified_temporal_scope'
    assert (world,cast)==before


@pytest.mark.parametrize('adopted',[False,True])
def test_runtime_projection_preserves_storage_routing_and_adopted_author_choice(adopted):
    text='Chapter one must release the sky engine before the hearing.'
    metadata={'active_foundation_selection':{'schema':'foundation-selection.v1','id':'unrelated-source'},
              'genre_intent_contract':{'prompt_pack_key':'xianxia-upgrade-core'},
              'writing_profile':{'market':{'opening_strategy':text},'style':{'custom_rules':['Keep archive letters numbered.']}},
              'writing_profile_origins':{'market.opening_strategy':origin_record(text,kind='explicit',adopted=adopted,producer='resolve_writing_profile')}}
    project=NS(metadata_json=metadata,genre='玄幻',sub_genre=None,audience=None,language='zh-CN')
    before=deepcopy(metadata)
    profile=get_project_runtime_writing_profile(project)
    rendered=render_serial_fiction_guardrails(profile,language='zh-CN',scope='chapter')
    assert (text in rendered) is adopted
    assert project.metadata_json==before
    assert profile.style.custom_rules==['Keep archive letters numbered.']
    assert profile.market.prompt_pack_key=='xianxia-upgrade-core'
    assert profile._runtime_source_receipt['storage_modified'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("bound,has_snapshot", [(True, False), (False, False), (True, True)])
async def test_undated_materialized_cast_never_proves_chapter_knowledge(
    character_source, monkeypatch, bound, has_snapshot
):
    from bestseller.services.character_snapshot_sources import snapshot_source_status
    from bestseller.services.context import _load_participant_knowledge_states

    session, project, _, draft, char = character_source
    future = "A letter will be delivered in the final volume."
    char.knowledge_state_json = {"knows": [future]}
    char.metadata_json = {
        "lie_truth_arc": {"core_lie": "An assumption", "core_truth": "An invented inverse"}
    }
    project.metadata_json = (
        {"active_foundation_selection": {"schema": "foundation-selection.v1"}} if bound else {}
    )
    draft.content_md = "The seal is still closed."
    await session.commit()
    if has_snapshot:
        snapshot = await _extract_character_state(
            character_source, monkeypatch, knowledge_gained=["The seal is still closed."]
        )
        assert (
            await snapshot_source_status(
                session, snapshot, project_id=project.id, character_id=char.id
            )
        )["status"] == "eligible"
    rows = await _load_participant_knowledge_states(
        session, project=project, chapter=NS(chapter_number=2), participant_names=["Cal"]
    )
    if not has_snapshot:
        # Unbound legacy projects cannot turn mutable materialized state into
        # dated knowledge either.
        assert rows == []
    else:
        assert rows[0]["knows"] == ["The seal is still closed."]
        assert "lie_truth_arc" not in rows[0]
        assert future not in repr(rows)


def test_selected_planning_commitments_keep_mixed_future_constraints_exactly():
    from bestseller.services.prompt_source_policy import content_hash
    hook='First inspect the seal without writing; later witnesses must learn on page.'
    volume={'volume_number':4,'information_flow_scene_cards':[
        {'chapter_number':14,'actual_action':'The prisoner remains silent; the witness must not hear speech yet.'},
        {'chapter_number':15,'actual_action':'First speech only after the dated seal is opened.'}]}
    book={'series_engine':{'first_three_chapter_hook':hook,'golden_three_hooks':['Inspect','Pay','Witness']}}
    artifacts={'world_spec':NS(id=uuid4(),content={}), 'book_spec':NS(id=uuid4(),content=book),
               'volume_plan':NS(id=uuid4(),content=[volume])}
    before=deepcopy((volume,book)); result=project_current_writer_boundaries(artifacts,volume_number=4)
    selected=result['selected_planning_commitments']
    assert selected[0]['fields']['/series_engine/first_three_chapter_hook']==hook
    assert selected[1]['value']==volume and selected[1]['value_sha256']==content_hash(volume)
    assert selected[1]['source_pointer']=='/0'
    assert 'not_current_facts' in selected[1]['authority']
    assert (volume,book)==before


@pytest.mark.asyncio
async def test_initial_planning_snapshot_preserved_but_not_accepted_as_temporal_knowledge(
    character_source, monkeypatch
):
    from bestseller.services.story_bible import (
        _ensure_initial_character_state_snapshot,
        get_effective_character_state,
    )

    session, project, _, draft, character = character_source
    character.knowledge_state_json = {"knows": ["The final letter will be delivered."]}
    character.arc_state = "A whole-book trajectory"
    await _select_cast(character_source)
    before = deepcopy(character.knowledge_state_json)
    assert await _ensure_initial_character_state_snapshot(
        session,
        project_id=character.project_id,
        character=character,
        planning_knowledge_unscoped=True,
    )
    await session.commit()
    initial = await session.scalar(select(CharacterStateSnapshotModel))
    assert initial.source_binding["kind"] == "initial"
    assert initial.beliefs == [] and character.knowledge_state_json == before
    # A historical pre-fix row remains physically untouched, yet its chapter-0
    # label cannot prove the future knowledge was available at the opening.
    initial.beliefs = ["Historical future knowledge"]
    raw = deepcopy(initial.beliefs)
    await session.commit()
    state = await get_effective_character_state(
        session,
        project_id=character.project_id,
        character=character,
        before_chapter_number=1,
        before_scene_number=1,
        planning_initial_knowledge_unknown=True,
    )
    assert state.knowledge_state == {} and initial.beliefs == raw
    assert state.arc_state is None and character.arc_state == "A whole-book trajectory"
    # A date alone cannot upgrade the historical row; the positive case needs
    # the current adopted-body producer and its sealed knowledge delta.
    legacy_dated = CharacterStateSnapshotModel(
        project_id=project.id,
        character_id=character.id,
        chapter_number=1,
        scene_number=1,
        beliefs=["A dated event already occurred."],
    )
    session.add(legacy_dated)
    await session.commit()
    state = await get_effective_character_state(
        session,
        project_id=project.id,
        character=character,
        before_chapter_number=2,
        before_scene_number=1,
        planning_initial_knowledge_unknown=True,
    )
    assert state.knowledge_state == {} and legacy_dated.source_binding is None
    draft.content_md = "A dated event already occurred."
    await session.commit()
    await _extract_character_state(
        character_source, monkeypatch, knowledge_gained=["A dated event already occurred."]
    )
    state = await get_effective_character_state(
        session,
        project_id=character.project_id,
        character=character,
        before_chapter_number=2,
        before_scene_number=1,
        planning_initial_knowledge_unknown=True,
    )
    assert state.knowledge_state == {"knows": ["A dated event already occurred."]}
    assert (
        initial.beliefs == raw and character.knowledge_state_json["knows"][0] == before["knows"][0]
    )
    assert legacy_dated.beliefs == ["A dated event already occurred."]


@pytest.mark.asyncio
async def test_dated_arc_delta_cannot_resurrect_initial_whole_book_trajectory(
    character_source, monkeypatch
):
    from bestseller.services.character_snapshot_sources import snapshot_source_status
    from bestseller.services.story_bible import get_effective_character_state

    session, project, _, draft, char = character_source
    char.arc_state = "Final-volume trajectory"
    char.knowledge_state_json = {"knows": ["Future revelation"]}
    await _select_cast(character_source)
    from bestseller.services.story_bible import _ensure_initial_character_state_snapshot

    assert await _ensure_initial_character_state_snapshot(
        session, project_id=project.id, character=char
    )
    await session.commit()
    initial = await session.scalar(select(CharacterStateSnapshotModel))
    assert (
        await snapshot_source_status(session, initial, project_id=project.id, character_id=char.id)
    )["status"] == "eligible"
    draft.content_md = "Cal inspected the unopened seal. The seal is unopened."
    await session.commit()
    dated = await _extract_character_state(
        character_source,
        monkeypatch,
        arc_state="Inspected the unopened seal",
        knowledge_gained=["The seal is unopened."],
    )
    result = await get_effective_character_state(
        session,
        project_id=project.id,
        character=char,
        before_chapter_number=2,
        before_scene_number=1,
        planning_initial_knowledge_unknown=True,
    )
    assert result.arc_state == dated.arc_state and result.previous_arc_state is None
    assert result.knowledge_state == {"knows": ["The seal is unopened."]}
    assert initial.arc_state == "Final-volume trajectory" and initial.beliefs == [
        "Future revelation"
    ]


def test_unadopted_legacy_character_story_fields_cannot_override_selected_cast():
    project=NS(metadata_json={'active_foundation_selection':{'schema':'foundation-selection.v1'},
        'writing_profile':{'character':{'protagonist_core_drive':'Restore a deleted empire',
                                       'golden_finger':'An obsolete time-reversal engine'}}},
        genre='玄幻',sub_genre=None,audience=None,language='zh-CN')
    before=deepcopy(project.metadata_json);profile=get_project_runtime_writing_profile(project)
    assert not profile.character.golden_finger and not profile.character.protagonist_core_drive
    assert project.metadata_json==before
    retired={row['path'] for row in profile._runtime_source_receipt['retired_references']}
    assert {'character.protagonist_core_drive','character.golden_finger'}<=retired


def test_final_chapter_builder_preserves_exact_boundaries_even_when_registered_as_leak(monkeypatch):
    from _prose_prompt_fixtures import build_chapter_first_system_prompt

    from bestseller.services import drafts
    from bestseller.services.prompt_source_policy import content_hash
    token='窗外信号'
    projection=project_current_writer_boundaries({'world_spec':NS(id=uuid4(),content={
        'forbidden_zones':[token+'不得绕过具名守门者。'],
        'power_system':{'hard_limits':['只有'+token+'不能令死者复生。']}})})
    original=drafts.build_chapter_first_draft_prompts; captured={}
    def call(*args,**kwargs):
        project,chapter,scenes,style,packet=args
        chapter.metadata_json={**(chapter.metadata_json or {}),
            'object_signal_contract':{'forbidden_signals':[token]}}
        packet.story_bible={'current_world_boundaries':projection}
        compiled=original(*args,**{**kwargs,'prompt_mode':'compiled'})
        captured['compiled']=compiled
        return compiled.system,compiled.user
    monkeypatch.setattr(drafts,'build_chapter_first_draft_prompts',call)
    build_chapter_first_system_prompt()
    import json
    exact=json.dumps(projection,ensure_ascii=False,separators=(',',':'))
    result=captured['compiled']
    assert exact in result.user
    assert content_hash(projection['fields']['/forbidden_zones'])==projection['field_sha256']['/forbidden_zones']
    assert 'chapter_first.section.current_world_boundaries' in result.report.source_integrity_blocks
