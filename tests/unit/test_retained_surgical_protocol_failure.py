"""A retained first failure consumes its slot; only the remaining draw is sent."""
from copy import deepcopy
from dataclasses import replace
import json
from uuid import uuid4

import pytest
from test_planning_surgical_repair import case as case, decoded, request

from bestseller.services import llm, planner, planning_surgical_repair as surgical
from bestseller.services.budget import bind_execution_scope
from bestseller.services.prompt_source_policy import content_hash

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]


def failure(c):
    first=request(c)
    role=surgical.budget.resolve_request_role(c.settings,c.project,first)
    return surgical.RetainedProtocolFailure(project_id=c.project.id,
        workflow_run_id=c.workflow_id,scope_id='project:'+str(c.project.id),
        source_context_sha256=content_hash(c.context),
        candidate_sha256=content_hash(planner._without_planning_meta(c.payload)),
        effective_input_sha256=llm._effective_input_hash(first,role),llm_run_id=uuid4(),
        content='{"patches":[{"scope":"scene","scene_number":null}]}',
        error='Original scene target is missing its required integer.')


async def run(c,token):
    with bind_execution_scope('project:'+str(c.project.id)):
        return await surgical.repair(None,c.settings,project=c.project,
            workflow_run_id=c.workflow_id,logical_name='volume_1_chapter_outline',
            outline_payload=c.payload,directives=c.directives,source_context=c.context,
            validation_context=c.validation,retained_failure=token)


async def test_original_first_return_is_not_dispatched_or_charged_again(case):
    token=failure(case);before=deepcopy(case.payload)
    result,final_id=await run(case,token)
    assert case.gateway.await_count==1
    assert final_id!=token.llm_run_id
    assert decoded(case.requests[0])['directives']['protocol_feedback']==[token.feedback(case.payload)]
    assert result['_meta']['surgical_repair_receipt']['protocol_attempts']==[token.attempt()]
    assert case.payload==before


async def test_invalid_remaining_return_never_gets_a_third_slot(case):
    token=failure(case);case.responses=['not JSON']
    with pytest.raises(ValueError):await run(case,token)
    assert case.gateway.await_count==1


@pytest.mark.parametrize('field,value',[
    ('project_id',uuid4()),('workflow_run_id',uuid4()),('scope_id','project:foreign'),
    ('source_context_sha256','0'*64),('candidate_sha256','0'*64),
    ('effective_input_sha256','0'*64),('error',''),('llm_run_id',None),
])
async def test_retained_failure_cannot_move_to_another_current_input(case,field,value):
    token=replace(failure(case),**{field:value})
    with pytest.raises(ValueError,match='current_binding_changed'):await run(case,token)
    assert case.gateway.await_count==0


async def test_story_mapping_cannot_impersonate_verified_failure(case):
    with pytest.raises(ValueError,match='verified_type_required'):
        await run(case,{'llm_run_id':str(uuid4()),'content':'invented'})
    assert case.gateway.await_count==0


async def test_all_structural_target_errors_share_one_feedback_without_partial_application(case):
    before=deepcopy(case.payload)
    rows=[{'chapter_number':1,'scope':'scene','field':field,'value':value}
        for field,value in [('participants',['Mira']),('time_label','evening')]]
    rows.append({'chapter_number':2,'scope':'chapter','field':'not_registered','value':'unknown'})
    native=json.dumps({'patches':rows})
    feedback=surgical.protocol_failure_feedback({'attempt':1,'llm_run_id':str(uuid4()),
        'error':'Original first structure error.'},native,case.payload)
    assert feedback['rejected_response']==native
    assert [row['patch_indices'] for row in feedback['structural_errors']]==[[1,2],[3]]
    assert case.payload==before and case.gateway.await_count==0
