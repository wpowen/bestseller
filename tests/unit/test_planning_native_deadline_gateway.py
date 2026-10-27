"""Actual SDK boundary and SQLite ledger; physical HTTP sends are local mocks."""
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest
from litellm.types.utils import ModelResponse
from sqlalchemy import select, update
from test_planning_draft_admission import harness as harness, grade_payload  # noqa: F401
from test_planning_admission_literal_runtime import literal as literal  # noqa: F401
from test_planning_admission_id_runtime import id_review as id_review, assess  # noqa: F401
from gateway_ledger_support import replay_reserved_http_send

from bestseller.infra.db.models import ExecutionOperationModel, LlmRunModel
from bestseller.services import llm, planning_draft_admission as admission
from bestseller.services import retained_planning_generation as physical
from bestseller.services.budget import ExecutionBudgetError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
REAL_SDK_BOUNDARY = llm._call_litellm


def install_sdk(h, monkeypatch, *, fail=False):
    h.settings.llm.planner.timeout_seconds = 900
    h.settings.llm.critic.timeout_seconds = 180
    h.settings.llm.critic.api_key_env = None
    h.sdk_calls = []
    async def sdk(**kwargs):
        h.sdk_calls.append(kwargs)
        await replay_reserved_http_send(kwargs)
        if fail:
            raise httpx.ReadTimeout('offline transport timeout')
        calls = [{'id':'deadline-native', 'type':'function', 'function':{
            'name':'submit_planning_admission',
            'arguments':json.dumps(grade_payload(h.kwargs()['candidate_payload']))}}]
        message = {'role':'assistant','content':'','reasoning_content':'Retained offline reasoning',
            'tool_calls':calls}
        return ModelResponse(choices=[{'message':message,'finish_reason':'tool_calls'}],
            usage={'prompt_tokens':10,'completion_tokens':5})
    monkeypatch.setattr(llm,'_call_litellm',REAL_SDK_BOUNDARY)
    monkeypatch.setattr(llm,'_get_litellm',lambda:SimpleNamespace(acompletion=sdk))
    monkeypatch.setattr(llm,'_ensure_shared_litellm_http_client',lambda:None)


async def test_real_sdk_timeout_matches_all_retained_receipts_without_role_or_wire_drift(id_review,monkeypatch):
    h=id_review;install_sdk(h,monkeypatch)
    roles=deepcopy((h.settings.llm.planner.model_dump(mode='json'),h.settings.llm.critic.model_dump(mode='json')))
    state={};receipt=await assess(h,state)
    assert receipt['draft_eligible'] is True and len(h.sdk_calls)==1
    assert h.sdk_calls[0]['timeout']==900 and h.sdk_calls[0]['stream'] is False
    assert (h.settings.llm.planner.model_dump(mode='json'),h.settings.llm.critic.model_dump(mode='json'))==roles
    async with h.session_factory() as session:
        from uuid import UUID
        run=await session.get(LlmRunModel,UUID(receipt['llm_run_id']))
        policy=run.metadata_json['native_deadline_policy']
        key=run.metadata_json['execution_receipt']['operation_key']
        root=await session.get(ExecutionOperationModel,key)
        assert root.result_json['metadata']['native_deadline_policy']==policy
        assert policy==state['attempts'][0]['completion']['metadata']['native_deadline_policy']
        request,_=physical._file(h.settings,run,'request')
        assert request['effective']['native_deadline_policy']==policy
        assert request['effective']['timeout_seconds']==900
        assert request['effective']['configured_timeout_seconds']==180
        assert request['effective']['stream'] is False
        await admission.verify_planning_draft_admission(h.wrap(session),**h.kwargs(),
            settings=h.settings,receipt=receipt)
    assert await assess(h,deepcopy(state))==receipt and len(h.sdk_calls)==1


async def test_real_sdk_timeout_failure_retains_actual_deadline_and_full_unknown_charge(id_review,monkeypatch):
    h=id_review;install_sdk(h,monkeypatch,fail=True)
    with pytest.raises(ExecutionBudgetError,match='execution_result_unknown'):
        await assess(h,{})
    assert len(h.sdk_calls)==1 and h.sdk_calls[0]['timeout']==900
    async with h.session_factory() as session:
        rows=list(await session.scalars(select(ExecutionOperationModel).where(
            ExecutionOperationModel.status=='transport_failure_receipt')))
        assert len(rows)==1
        evidence=rows[0].result_json
        assert evidence['timeout_seconds']==900 and evidence['physical_sends']==1
        assert evidence['native_deadline_policy']=={'schema':'planning-native-deadline.v1','applies':True,
            'sdk_timeout_seconds':900,'hard_timeout_seconds':905.0,'configured_role_timeout_seconds':180}
        assert evidence['partial_stream_observed'] is False


@pytest.mark.parametrize('tamper',['missing','bool_is_int','numeric_type'])
async def test_durable_root_cannot_drop_actual_deadline_after_native_grade(id_review,monkeypatch,tamper):
    h=id_review;install_sdk(h,monkeypatch)
    state={};receipt=await assess(h,state)
    async with h.session_factory() as session:
        from uuid import UUID
        run=await session.get(LlmRunModel,UUID(receipt['llm_run_id']))
        key=run.metadata_json['execution_receipt']['operation_key']
        root=await session.get(ExecutionOperationModel,key)
        changed=deepcopy(root.result_json)
        if tamper=='missing':del changed['metadata']['native_deadline_policy']
        elif tamper=='bool_is_int':changed['metadata']['native_deadline_policy']['applies']=1
        else:changed['metadata']['native_deadline_policy']['sdk_timeout_seconds']=900.0
        # Force the stored corruption: Python equality treats True/1 and
        # 900/900.0 as equal, so ORM dirty checking alone would skip this write.
        await session.execute(update(ExecutionOperationModel).where(
            ExecutionOperationModel.key==key).values(result_json=changed))
        await session.commit()
    async with h.session_factory() as session:
        with pytest.raises(ValueError,match='admission_literal_native_deadline_changed'):
            await admission.verify_planning_draft_admission(h.wrap(session),**h.kwargs(),
                settings=h.settings,receipt=receipt)
    assert len(h.sdk_calls)==1


async def test_caller_cannot_inject_reserved_transport_evidence_before_send(id_review,monkeypatch):
    h=id_review;install_sdk(h,monkeypatch)
    build=admission.build_literal_admission_request
    def inject(*args,**kwargs):
        request,contract=build(*args,**kwargs)
        request.metadata['native_deadline_policy']={'untrusted':'cannot become SDK evidence'}
        return request,contract
    monkeypatch.setattr(admission,'build_literal_admission_request',inject)
    with pytest.raises(ValueError,match='caller_policy_forbidden'):
        await assess(h,{})
    assert h.sdk_calls==[]
