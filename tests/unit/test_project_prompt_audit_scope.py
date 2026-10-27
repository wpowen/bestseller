"""An explicit project audit cannot leak unrelated prompts or SDK credentials."""
import json
import stat
from uuid import uuid4

from bestseller.services.llm import LLMCompletionRequest, _persist_prompt_payloads, bind_project_prompt_audit
from bestseller.settings import load_settings


def _persist(settings, project, workflow):
    return _persist_prompt_payloads(settings=settings, request=LLMCompletionRequest(
        logical_role='critic', system_prompt='approved-source rules', user_prompt='current draft',
        fallback_response='unavailable', project_id=project, workflow_run_id=workflow,
        metadata={'api_key':'DO-NOT-RETAIN-SDK-SECRET'}), llm_run_id=uuid4(),
        configured_model='configured', effective_model='effective', provider='openai',
        effective_temperature=0.2, effective_max_tokens=2000, effective_timeout_seconds=60,
        effective_stream=False, effective_thinking_type=None, effective_reasoning_effort=None,
        content='verdict', tool_call_count=0, finish_reason='stop', fallback_used=False, fallback_source=None,
        input_tokens=20, output_tokens=5)


def test_exact_audit_scope_retains_only_selected_project_and_resets(tmp_path):
    settings=load_settings(env={});settings.artifact_store.local_dir=str(tmp_path)
    settings.artifact_store.retain_prompt_payloads=True
    project,other,workflow=uuid4(),uuid4(),uuid4()
    assert _persist(settings,project,workflow)[0] is None
    with bind_project_prompt_audit(project,reason='authorized framework diagnosis'):
        request_ref,response_ref,_,replayable=_persist(settings,project,workflow)
        assert replayable and response_ref
        path=tmp_path/request_ref;payload=json.loads(path.read_text())
        assert payload['project_id']==str(project)
        assert payload['messages']['user']['text']=='current draft'
        assert stat.S_IMODE(path.stat().st_mode)==0o600
        assert 'DO-NOT-RETAIN-SDK-SECRET' not in path.read_text()
        assert _persist(settings,other,workflow)[0] is None
        assert _persist(settings,project,None)[0] is None
    assert _persist(settings,project,workflow)[0] is None


def test_scope_does_not_override_disabled_retention(tmp_path):
    settings=load_settings(env={});settings.artifact_store.local_dir=str(tmp_path)
    project=uuid4()
    with bind_project_prompt_audit(project,reason='authorized diagnosis'):
        assert _persist(settings,project,uuid4())[0] is None
    assert not list(tmp_path.rglob('*.json'))
