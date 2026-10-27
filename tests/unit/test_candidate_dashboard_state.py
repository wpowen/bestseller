"""Candidate prose needs review; it must not advertise a new planning start."""
import subprocess
from bestseller.web import server
from test_task_projection_adoption_progress import _js_functions


def test_candidate_db_projection_is_attention_without_claiming_adoption():
    row={'slug':'unrelated-book','status':'planning','adoption_evidence':'verified',
         'candidate_chapters':1,'candidate_word_count_total':2200,'completed_chapters':0,
         'target_chapters':50,'has_active_workflow':False}
    card=server._project_row_to_dashboard_task(row)
    assert card['status']=='incomplete'
    assert card['current_stage']=='project_candidate_review'
    assert card['chapter_word_stats']['candidate_chapters']==1
    assert card['chapter_word_stats']['completed_chapters']==0
    assert not card['execution_active'] and card['progress_events']==[]


def test_rendered_candidate_state_and_stage_are_truthful():
    functions=_js_functions(['isActiveTask','dashboardProjectStatus','dashboardProjectStage',
        'repairPhaseOf','isConceptionOnlyTask','getTaskChapterWordStats',
        'isCompletedBookTask','isUnverifiedCompletedTask','taskExecutionTerminalCode',
        'taskExecutionStopPresentation','classifyBookState'])
    script=functions+'''
const assert=require('node:assert/strict');
const project={status:'planning',has_active_workflow:false,activity_status:'inactive',
 adoption_evidence:'verified',candidate_chapters:1};
assert.equal(dashboardProjectStatus(project),'incomplete');
assert.equal(dashboardProjectStage(project),'project_candidate_review');
const card={synthetic_project:true,status:'incomplete',project_status:'planning',
 chapter_word_stats:{adoption_evidence:'verified',candidate_chapters:1,adopted_chapters:0}};
const state=classifyBookState(card,{unitLabel:'章'});
assert.equal(state.label,'草稿待审');
assert.ok(state.detail.includes('尚未正式采用'));
assert.ok(!state.next.includes('重新生成规划'));
assert.equal(dashboardProjectStatus({...project,has_active_workflow:true,activity_status:'active'}),'running');
assert.equal(dashboardProjectStatus({...project,candidate_chapters:0}),'project');
'''
    result=subprocess.run(['/opt/homebrew/bin/node','-e',script],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
