"""The writer receives the exact full selected plan, never an unrelated latest row."""
# ruff: noqa: F811
from copy import deepcopy
import json

import pytest
from test_editable_full_plan_source import (
    current, full_case as full_case, plan_case as plan_case,
    integrated_modules as integrated_modules, materialized_case as materialized_case,
    store as store,
)
from test_fanqie_materialized_plan_resume import evidence
from bestseller.services.chapter_writer_delivery import load_delivery_blocks, delivery_block
from bestseller.services.prompt_compiler import compile_prompt
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_real_full_delivery_preserves_chapter_and_all_scene_contracts(full_case):
    case=full_case
    before=await evidence(case)
    async with case.factory() as session:
        project,chapter,scenes=await current(case,session)
        blocks=await load_delivery_blocks(session,case.settings,
            project=project,chapter=chapter,scenes=scenes)
        assert len(blocks)==1 and blocks[0].required and blocks[0].trim_policy=='preserve'
        payload=json.loads(blocks[0].text.split('\n',2)[2])
        approved=case.original.outline.content['chapters'][0]
        assert payload['artifact_id']==str(case.original.outline.id)!=str(case.decoy_id)
        assert payload['approved_chapter_sha256']==content_hash(approved)
        assert payload['chapter_contract']=={k:v for k,v in approved.items() if k not in {'scenes','_meta'}}
        for actual,original in zip(payload['scenes'],approved['scenes'],strict=True):
            for key in ('entry_state','exit_state','action_sequence','target_word_count','forbidden_actions'):
                if key in original and original[key] is not None:assert actual[key]==original[key]
        compiled=compile_prompt(blocks,total_budget_tokens=20000,require_provenance=True)
        assert not compiled.report.dropped and not compiled.report.truncated
        assert 'not already occurred history' in compiled.user
        assert not session.dirty and not session.new
    assert await evidence(case)==before and len(case.sent)==1


async def test_full_invalid_projection_never_returns_empty_delivery(full_case):
    case=full_case
    async with case.factory() as session:
        project,chapter,scenes=await current(case,session)
        scenes[0].exit_state={'summary':'An unapproved new ending'}
        await session.commit()
    before=await evidence(case)
    async with case.factory() as session:
        project,chapter,scenes=await current(case,session)
        with pytest.raises(ValueError,match='outline_source_admission_required'):
            await load_delivery_blocks(session,case.settings,project=project,chapter=chapter,scenes=scenes)
    assert await evidence(case)==before and len(case.sent)==1


@pytest.mark.parametrize('fault',['artifact','chapter','projection'])
async def test_full_receipt_cannot_be_transplanted_into_other_delivery(full_case,fault):
    case=full_case
    from bestseller.services.editable_plan_source import load_selected_plan_source
    async with case.factory() as session:
        project,chapter,scenes=await current(case,session)
        artifact,plan,receipt=await load_selected_plan_source(session,case.settings,
            project=project,chapter=chapter,scenes=scenes)
        receipt=deepcopy(receipt)
        if fault=='artifact':receipt['source_artifact_id']=str(case.decoy_id)
        elif fault=='chapter':receipt['chapter_projection_hashes']={}
        else:receipt['source_plan_projection']='0'*64
        with pytest.raises(ValueError,match='scope_changed'):
            delivery_block(project_id=project.id,chapter_id=chapter.id,
                chapter_number=chapter.chapter_number,artifact_id=artifact.id,approved=plan,receipt=receipt)
