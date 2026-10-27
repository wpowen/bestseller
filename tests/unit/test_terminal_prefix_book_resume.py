"""Book resume surfaces exact terminal prefix without creating paid work."""
from copy import deepcopy
import pytest
from test_chapter_outline_readiness import rig as prefix_case  # noqa:F401
import test_chapter_outline_readiness as fixtures
from bestseller.services import chapter_outline_readiness as ready,foundation_selection
from bestseller.settings import load_settings

pytestmark=[pytest.mark.unit,pytest.mark.asyncio]

@pytest.fixture
async def terminal(prefix_case,monkeypatch):  # noqa:F811
    h=prefix_case
    h.project.metadata_json['active_foundation_selection']={'test_only':True}
    h.judges[:]=[False]
    await fixtures._approve(h,through_chapter=1,max_repair_rounds=0,allow_editorial_debt=False)
    async def selected(session,*,project,settings):return h.foundation
    monkeypatch.setattr(foundation_selection,'load_selected_foundation_artifacts',selected)
    return h

async def test_refusal_and_same_resume_preserve_history_and_no_commit(terminal):
    h=terminal;wf=fixtures._workflow(h);before=deepcopy(wf.metadata_json);commits=len(h.store.commits)
    first=await ready.current_terminal_prefix_blocker(h.store,load_settings(),project=h.project)
    second=await ready.current_terminal_prefix_blocker(h.store,load_settings(),project=h.project)
    assert first==second and first['workflow_run_id']==str(wf.id)
    assert first['new_generation_or_repair_credit'] is False
    assert first['approval_policy']['allow_editorial_debt'] is False
    assert wf.metadata_json==before and len(h.store.commits)==commits

@pytest.mark.parametrize('fault',['candidate','foundation','grade','proof_missing'])
async def test_bound_source_corruption_cannot_become_new_window_permission(terminal,monkeypatch,fault):
    h=terminal;wf=fixtures._workflow(h)
    if fault=='candidate':wf.metadata_json['candidates'][-1]['candidate']['chapters'][0]['goal']='Changed'
    elif fault=='foundation':h.foundation['world_spec'].content['changed_after_review']=True
    elif fault=='grade':wf.metadata_json['candidates'][-1]['report']['pass']=True
    else:
        async def unavailable(*args,**kwargs):raise ValueError('physical_proof_missing')
        monkeypatch.setattr(foundation_selection,'load_selected_foundation_artifacts',unavailable)
    with pytest.raises(ValueError):await ready.current_terminal_prefix_blocker(h.store,load_settings(),project=h.project)

async def test_applied_operator_candidate_cannot_escape_to_fresh_rolling_draw(terminal):
    h=terminal;wf=fixtures._workflow(h)
    wf.metadata_json['phase']='awaiting_operator_review_authorization'
    current=wf.metadata_json['candidates'][-1]
    current['operator_edit']={'synthetic_fixture_only':True}
    current.pop('report',None)
    result=await ready.current_terminal_prefix_blocker(h.store,load_settings(),project=h.project)
    assert result['reason']=='operator_candidate_requires_bound_review'
    assert result['report_hash'] is None and result['new_generation_or_repair_credit'] is False

async def test_old_record_removed_from_active_checkpoint_is_not_current_blocker(terminal):
    from bestseller.services.prompt_source_policy import content_hash
    h=terminal;wf=fixtures._workflow(h);old=deepcopy(wf.metadata_json)
    bucket=h.project.metadata_json['outline_commercial_judge_checkpoints']['window']
    replacement=deepcopy(bucket['best'])
    replacement['candidate']['chapters'][0]['goal']='A separately prepared current branch'
    replacement['candidate_hash']=content_hash(replacement['candidate'])
    bucket['best']=replacement
    assert await ready.current_terminal_prefix_blocker(h.store,load_settings(),project=h.project) is None
    assert wf.metadata_json==old
