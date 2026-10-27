"""Reference-only proposals from rejected native observations, never old grades."""
from copy import deepcopy
import json

import pytest
from test_outline_projection_consistency_recovery import (  # noqa: F401
    AUTH, case as case, domain_case, migration, source_case, source_fixture, store,
)
from bestseller.infra.db import models as db
from bestseller.services import outline_projection_consistency_recovery as service
from bestseller.services.budget import bind_execution_scope

pytestmark = [pytest.mark.unit,pytest.mark.asyncio]


@pytest.mark.parametrize('case',['planning-draft-admission.v5'],indirect=True)
async def test_v5_invalid_quotes_select_only_exact_candidate_references_and_keep_raw_unknown(case):
    c=case
    async with c.factory() as session:
        owner=await session.get(db.WorkflowRunModel,c.failed_id)
        before=deepcopy(owner.metadata_json)
        with bind_execution_scope(c.scope):
            prepared=await service.prepare_source_projection_consistency_repair(session,c.settings,
                failed_admission_workflow_id=c.failed_id,**AUTH)
        assert not (session.dirty or session.new or session.deleted)
    p=prepared['prepared'];contract=p['field_contract']
    assert contract['paths']==['/chapters/0/chapter_goal']
    assert len(p['reference_observations'])==2
    assert all(not row['grade_asserted'] for row in p['reference_observations'])
    for raw,bound in zip(p['observations'],p['reference_observations'],strict=True):
        assert raw==bound['native_payload'] and raw['draft_eligible'] is False
        assert 'untrusted commentary' in raw['hard_violations'][0]['evidence']
    assert p['source_evidence']['observation_trust']=='untrusted_native_not_grade_or_author_facts'
    async with c.factory() as session:
        assert (await session.get(db.WorkflowRunModel,c.failed_id)).metadata_json==before
    assert c.new_calls==[]


@pytest.mark.parametrize('case',['planning-draft-admission.v5'],indirect=True)
async def test_v5_reference_proposal_changes_real_field_once_without_regrading_old_candidate(case):
    c=case
    async with c.factory() as session:
        old=deepcopy((await session.get(db.WorkflowRunModel,c.failed_id)).metadata_json)
        with bind_execution_scope(c.scope):
            result=await service.propose_source_projection_consistency_repair(session,c.settings,
                failed_admission_workflow_id=c.failed_id,**AUTH)
    assert not result['grade_asserted'] and not result['adoption_granted']
    receipt=result['proposal_receipt']
    assert receipt['correction']['changed_paths']==['/chapters/0/chapter_goal']
    assert receipt['correction']['non_targets_unchanged'] and receipt['correction']['inverse_verified']
    assert len(c.new_calls)==1 and len(c.old_calls)==2
    async with c.factory() as session:
        assert (await session.get(db.WorkflowRunModel,c.failed_id)).metadata_json==old
        with bind_execution_scope(c.scope):
            again=await service.propose_source_projection_consistency_repair(session,c.settings,
                failed_admission_workflow_id=c.failed_id,**AUTH)
    assert again['reused'] and len(c.new_calls)==1 and len(c.old_calls)==2
    assert json.dumps(result['candidate_payload'],sort_keys=True)!=json.dumps(c.candidate,sort_keys=True)
