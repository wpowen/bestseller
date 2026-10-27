"""Defaults, new-book binding and old-book preservation. Zero provider dispatch."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from bestseller.settings import load_settings, apply_runtime_llm_profile, LLM_ROLE_NAMES
from bestseller.services import llm, model_catalog as catalog
from bestseller.services.prompt_source_policy import content_hash

pytestmark = pytest.mark.unit


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, '_api_key_present', lambda _: True)
    catalog._load_catalog_raw.cache_clear()
    raw = load_settings(env={})
    raw.artifact_store.local_dir = str(tmp_path)
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'llm-runtime-profile.json').write_text(json.dumps({'active_profile':'minimax'}))
    return raw


def test_formal_default_and_five_roles_match_official_model(settings):
    entry = catalog.get_model_catalog_entry('minimax-m3.1-flash')
    assert entry.model == 'openai/MiniMax-M3.1-Flash' and 'preview' not in entry.model.lower()
    assert entry.api_base == 'https://api.minimaxi.com/v1' and entry.api_key_env == 'MINIMAX_API_KEY'
    assert catalog.default_new_project_model_id(settings) == entry.id
    effective = apply_runtime_llm_profile(settings)
    for name in LLM_ROLE_NAMES:
        assert getattr(settings.llm,name).model == getattr(effective.llm,name).model == entry.model


@pytest.mark.parametrize('selected', [None, 'minimax-m3', 'deepseek-v4-flash'])
def test_actual_quickstart_freezes_default_but_preserves_explicit_choice(settings, monkeypatch, selected):
    from bestseller.web import server
    monkeypatch.setattr(server, 'load_settings', lambda: settings)
    captured=[]
    monkeypatch.setattr(server.WebTaskManager,'create_autowrite_task',lambda self,payload: captured.append(deepcopy(payload)) or {'task_id':'offline-only'})
    payload={'genre_key':'xianxia-upgrade','chapter_count':3}
    if selected:payload['llm_model_id']=selected
    server.WebTaskManager().create_quickstart_task(payload)
    expected=selected or 'minimax-m3.1-flash'
    assert captured[0]['llm_model_id']==expected
    intent=captured[0]['creation_intent_contract']
    assert intent['llm_model_id']==expected
    assert intent['field_sources']['llm_model_id']==('explicit' if selected else 'default')
    assert payload.get('llm_model_id')==selected


class Prepared(BaseException):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize('name', LLM_ROLE_NAMES)
@pytest.mark.parametrize('kind', ['new_default','legacy_unbound','explicit_m3','explicit_deepseek'])
async def test_actual_gateway_preclaim_uses_bound_model_even_with_twice_applied_profile(settings, monkeypatch, name, kind):
    project_id=None if kind=='new_default' else uuid4()
    metadata={}
    expected='openai/MiniMax-M3.1-Flash'
    if kind=='legacy_unbound':
        old={n:getattr(settings.llm,n).model_copy(update={'model':'openai/MiniMax-M3'}).model_dump(mode='json') for n in LLM_ROLE_NAMES}
        path=Path(settings.artifact_store.local_dir)/'runtime'/catalog.LEGACY_MODEL_DEFAULTS_FILE
        path.write_text(json.dumps({'schema':'legacy-project-model-defaults.v1','projects':{str(project_id):{'roles':old,'roles_sha256':content_hash(old)}}}))
        expected='openai/MiniMax-M3'
    if kind=='explicit_m3':metadata['llm_model_id']='minimax-m3';expected='openai/MiniMax-M3'
    if kind=='explicit_deepseek':metadata['llm_model_id']='deepseek-v4-flash';expected='deepseek/deepseek-v4-flash'
    before=deepcopy(metadata)
    class Session:
        async def scalar(self, query):return deepcopy(metadata)
    observed={}
    original_hash=llm._effective_input_hash
    def capture(request,role):
        observed.update(model=role.model,payload=llm._effective_input_payload(request,role),hash=original_hash(request,role))
        raise Prepared()
    async def denied(*a,**k):pytest.fail('No model, claim or reserve allowed')
    from bestseller.services.budget import ExecutionLedger
    monkeypatch.setattr(llm,'_effective_input_hash',capture)
    monkeypatch.setattr(llm,'_call_litellm',denied)
    monkeypatch.setattr(ExecutionLedger,'claim',denied)
    monkeypatch.setattr(ExecutionLedger,'reserve',denied)
    request=llm.LLMCompletionRequest(project_id=project_id,workflow_run_id=uuid4(),logical_role=name,
        system_prompt='Offline source-only request construction',user_prompt='Synthetic text, never sent.',fallback_response='not a grade')
    with pytest.raises(Prepared):
        await llm.complete_text(Session(),apply_runtime_llm_profile(apply_runtime_llm_profile(settings)),request)
    assert observed['model']==expected and metadata==before
    if kind=='new_default':
        assert observed['payload']['reasoning_effort']==('low' if observed['payload']['max_tokens']<=4096 else 'max')
        assert observed['payload']['thinking_type'] is None
        assert observed['payload']['output_parameter']=='max_completion_tokens'


@pytest.mark.asyncio
async def test_service_new_project_freezes_default_in_metadata_without_overriding_explicit(settings, monkeypatch, tmp_path):
    from bestseller.services import projects
    from bestseller.domain.project import ProjectCreate
    from bestseller.infra.db.models import ProjectModel
    from bestseller.services import planning_kernel,book_packaging_store
    async def no_capabilities(*a,**k):return None
    monkeypatch.setattr(projects,'initialize_project_genre_capabilities',no_capabilities)
    monkeypatch.setattr(book_packaging_store,'promote_creation_packaging_contract_if_ready',no_capabilities)
    monkeypatch.setattr(planning_kernel,'persist_project_planning_kernel',lambda *a,**k:None)
    class Session:
        async def scalar(self,q):return None
        def add(self,obj):
            if isinstance(obj,ProjectModel):obj.id=uuid4()
        async def flush(self):pass
        async def refresh(self,obj):pass
    for selected in (None,'minimax-m3'):
        metadata={} if selected is None else {'llm_model_id':selected}
        payload=ProjectCreate(slug='offline-'+str(uuid4())[:8],title='Offline fixture',genre='悬疑',target_chapters=3,target_word_count=6000,metadata=metadata)
        project=await projects.create_project(Session(),payload,settings)
        assert project.metadata_json['llm_model_id']==(selected or 'minimax-m3.1-flash')
        assert payload.metadata==metadata
