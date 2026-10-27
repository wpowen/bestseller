"""Execute the shipped dashboard loader/poller in Node; no localhost/DB/model."""

from pathlib import Path
import json
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[1],'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
function fn(name) {
 const m=new RegExp(`^([\\t ]*)(?:async )?function ${name}\\(`,'m').exec(script);
 assert.ok(m,name);const end=script.indexOf('\n'+m[1]+'}',m.index);
 return script.slice(m.index,end+m[1].length+2);
}
let now=0,id=0,timers=new Map(),calls=[],pending=[],rendered=[],elements={},mode='ok';
const task={task_id:'500-book',status:'failed',title:'Keep blocked creation'};
const el=id=>elements[id]??={textContent:'',classList:{contains:()=>false}};
const deferred=()=>{let resolve,reject;
 const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const network=(url,options={})=>{
 calls.push({url,options});assert.ok(!options.method || options.method==='GET');
 assert.equal(options.cache,'no-store');assert.ok(options.signal);
 if(mode==='hold'||(mode==='projects-hold'&&url.includes('/api/projects?'))||
    (mode==='tasks-hold'&&url.includes('/api/tasks?'))||url.startsWith('/api/status')) {
  const p=deferred();pending.push({url,options,...p});return p.promise;
 }
 if(mode==='error'&&url.includes('/api/tasks?')) return Promise.resolve({ok:false,status:503});
 if(mode==='invalid'&&url.includes('/api/tasks?')) return Promise.resolve({ok:true,
 json:async()=>({error:'not rows'})});
 return Promise.resolve({ok:true,json:async()=>url.includes('/api/tasks?')?[task]:[]});
};
const c=vm.createContext({console,AbortController,URLSearchParams,fetch:network,
 Date:class extends Date {static now(){return now;}},
 performance:{now:()=>now},
 setTimeout:(f,ms)=>{const n=++id;timers.set(n,{f,at:now+ms});return n;},
 clearTimeout:n=>timers.delete(n),
 document:{visibilityState:'visible',getElementById:el},$:el,
 window:{location:{search:''},history:{replaceState(){}}},location:{hash:''},
 rawTaskCount:0,rawProjectCount:0,rawClosedBookCount:0,rawDashboardProjectCount:0,allTasks:[],
 isProjectArchivedBook:()=>false,isProjectClosedBook:()=>false,shouldProjectAppearOnDashboard:()=>true,
 updateProjectMaps(){},enrichDashboardTaskTitles:x=>x,mergeDashboardProjects:x=>x,normalizeDashboardTasks:x=>x,
 renderDashboard:()=>rendered.push(vm.runInContext('allTasks.map(x=>x.task_id)',c)),
 formatTodayMasthead(){},updateLlmProfileUi(){},updateDashboardMetrics(){},
 loadGenres(){},loadModelCatalog(){},resetWizardState(){},
 initializeCreationConfiguration:()=>new Promise(()=>{}),switchView(){},openProgress(){},alert(){},
 isConceptionOnlyTask:()=>false,isExecutionWindowTask:()=>false,
 getTaskChapterWordStats:t=>t.chapter_word_stats||{hasStats:true,completedChapters:4,targetChapters:4},
 repairPhaseOf:()=>'',isActiveTask:t=>['running','queued'].includes(t.status),
 taskExecutionTerminalCode:()=>null,taskExecutionStopPresentation:()=>null,
 llmModel:'',maxConcurrentTasks:3,highlightTaskId:null,
});
vm.runInContext(`const pollingResources=new Map();let dashboardRefreshInFlight=null;
 const dashboardReadState={tasks:{rows:[],receivedAt:null,error:null},
 projects:{rows:[],receivedAt:null,error:null}};
`+['pollResource','refreshDashboard','boundedDashboardJson','applyDashboardReadState','loadDashboard','init',
 'isCompletedBookTask','isBodyCompletePendingDelivery','isUnverifiedCompletedTask','taskErrorSummary','classifyBookState'].map(fn).join('\n'),c);
const run=s=>vm.runInContext(s,c);
async function flush(){for(let i=0;i<50;i++)await Promise.resolve();}
async function advance(ms){now+=ms;
 for(const [n,t]of [...timers])if(t.at<=now){timers.delete(n);t.f();}await flush();}
const scenario=process.argv[2];
(async()=>{
 if(scenario==='partial'){
  mode='projects-hold';const p=run('refreshDashboard()');await flush();
  assert.ok(rendered.some(rows=>rows.includes('500-book')),'tasks visible before projects settle');
  assert.match(el('dashLoadStatus').textContent,/作品加载中/);
  await advance(20000);await p;
  assert.ok(pending[0].options.signal.aborted);
  assert.match(el('dashLoadStatus').textContent,/作品刷新失败/);
  assert.equal(run('allTasks[0].status'),'failed');
 }else if(scenario==='stale-recover'){
  await run('refreshDashboard()');mode='error';await run('refreshDashboard()');
  assert.equal(run('allTasks[0].task_id'),'500-book');
  assert.match(el('dashLoadStatus').textContent,/任务刷新失败.*已过期/);
  mode='ok';await run('refreshDashboard()');
  assert.doesNotMatch(el('dashLoadStatus').textContent,/失败|过期/);
 }else if(scenario==='timeout-force'){
  mode='hold';const p=run('refreshDashboard()');await flush();
  const forced=run('refreshDashboard()');await flush();assert.equal(calls.length,2);
  mode='ok';await advance(20000);await p;await forced;
  assert.equal(calls.length,4,'one bounded read then manual recovery, no overlap');
  assert.equal(run('dashboardRefreshInFlight'),null);
  assert.equal(run("pollingResources.get('dashboard').promise"),null);
  assert.equal(timers.size,0);assert.equal(run('allTasks[0].task_id'),'500-book');
  // Even a transport that ignores abort cannot replace newer data later.
  pending.forEach(p=>p.resolve({ok:true,json:async()=>[]}));await flush();
  assert.equal(run('allTasks[0].task_id'),'500-book');
 }else if(scenario==='invalid'){
  await run('refreshDashboard()');mode='invalid';await run('refreshDashboard()');
  assert.equal(run('allTasks[0].task_id'),'500-book');
  assert.match(el('dashLoadStatus').textContent,/列表格式不可用.*已过期/);
 }else if(scenario==='project-first'){
  mode='tasks-hold';const p=run('refreshDashboard()');await flush();
  assert.equal(run('dashboardReadState.projects.receivedAt'),0);
  assert.match(el('dashLoadStatus').textContent,/作品已更新/);
  await advance(20000);await p;assert.match(el('dashLoadStatus').textContent,/任务刷新失败/);
 }else if(scenario==='init'){
  const p=run('init()');await flush();
  assert.ok(calls.some(x=>x.url==='/api/status?light=1'));
  assert.ok(calls.some(x=>x.url.includes('include_inactive=1')));
  assert.ok(rendered.some(rows=>rows.includes('500-book')),'status/config cannot block tasks');
  await p;await advance(20000);assert.equal(timers.size,0);
 }else if(scenario==='cadence'){
  await run('refreshDashboard()');await run('refreshDashboard(false)');
  assert.equal(calls.length,2);await advance(15000);await run('refreshDashboard(false)');
  assert.equal(calls.length,4);c.document.visibilityState='hidden';
  await advance(30000);await run('refreshDashboard(false)');assert.equal(calls.length,4);
  await run('refreshDashboard()');assert.equal(calls.length,6,'manual refresh works while hidden');
 }else if(scenario==='backoff'){
  await run('refreshDashboard()');mode='error';await advance(15000);
  await run('refreshDashboard(false)');assert.equal(calls.length,4);
  await advance(15000);await run('refreshDashboard(false)');assert.equal(calls.length,4);
  mode='ok';await run('refreshDashboard()');assert.equal(calls.length,6);
  await advance(15000);await run('refreshDashboard(false)');assert.equal(calls.length,8);
 }else if(scenario==='slow-success'){
  mode='hold';const p=run('refreshDashboard()');await flush();await advance(19000);
  assert.ok(pending.every(x=>!x.options.signal.aborted));
  pending.forEach(x=>x.resolve({ok:true,json:async()=>x.url.includes('/api/tasks?')?[task]:[]}));
  await p;assert.equal(run('allTasks[0].task_id'),'500-book');assert.equal(timers.size,0);
 }else if(scenario==='not-checked'){
  task.delivery_closure={status:'not_checked',is_complete:false,body_complete:false,
    reason:'delivery_verification_not_requested',reader:'read_book_delivery_closure'};
  c.marker=task.delivery_closure;
  await run('refreshDashboard()');
  assert.match(el('dashLoadStatus').textContent,/进度已更新；正式交付状态请进入作品详情核验/);
  for(const status of ['project','completed']){
   c.subject={status,book_state:'delivery_pending',delivery_closure:task.delivery_closure};
   assert.equal(run('isBodyCompletePendingDelivery(subject)'),false);
   assert.equal(run('isCompletedBookTask(subject)'),false);
   const view=run('classifyBookState(subject)');
   assert.equal(view.mode,'交付待核验');
   assert.equal(view.label,status==='completed'?'历史记录完成':'交付待核验');
   assert.notEqual(view.phase,'blocked');assert.notEqual(view.phase,'complete');
  }
  c.subject={status:'completed',delivery_closure:{status:'verified',is_complete:true}};
  assert.equal(run('isCompletedBookTask(subject)'),true);
  assert.equal(run('classifyBookState(subject).label'),'已完成');
  c.subject={status:'failed',delivery_closure:task.delivery_closure};
  assert.equal(run('classifyBookState(subject).label'),'已失败');
  delete task.delivery_closure;await run('refreshDashboard()');
  assert.doesNotMatch(el('dashLoadStatus').textContent,/正式交付状态请进入/);
 }else throw Error(scenario);
 assert.ok(calls.filter(x=>x.url.includes('/api/tasks?')).every(x=>x.url.includes('include_inactive=1')));
 assert.ok(calls.filter(x=>x.url.includes('/api/tasks?')||x.url.includes('/api/projects?'))
  .every(x=>x.url.includes('progress_readback=1')));
 console.log('PASS '+scenario);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""


@pytest.mark.unit
@pytest.mark.parametrize(
    "scenario",
    [
        "partial",
        "stale-recover",
        "timeout-force",
        "invalid",
        "project-first",
        "init",
        "cadence",
        "backoff",
        "not-checked",
        "slow-success",
    ],
)
def test_shipped_dashboard_loader(scenario):
    node = shutil.which("node")
    assert node, "Node is required to execute the real shipped JS"
    result = subprocess.run(
        [node, "-e", HARNESS, "src/bestseller/web/novel_quickstart.html", scenario],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS " + scenario in result.stdout


LEGACY_HARNESS = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const script=fs.readFileSync(process.argv[1],'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
function fn(name){
 const m=new RegExp(`^([\\t ]*)(?:async )?function ${name}\\(`,'m').exec(script);
 assert.ok(m,name);const end=script.indexOf('\n'+m[1]+'}',m.index);
 return script.slice(m.index,end+m[1].length+2);
}
let now=0,renders=0;
const c=vm.createContext({
 Date:class extends Date{static now(){return now;}},document:{visibilityState:'visible'},
 fetch:url=>url.includes('/api/projects?')?new Promise(()=>{}):
  Promise.resolve({ok:true,json:async()=>[{task_id:'current-task',status:'running'}]}),
 rawTaskCount:0,rawProjectCount:0,rawClosedBookCount:0,rawDashboardProjectCount:0,allTasks:[],
 isProjectArchivedBook:()=>false,isProjectClosedBook:()=>false,shouldProjectAppearOnDashboard:()=>true,
 updateProjectMaps(){},enrichDashboardTaskTitles:x=>x,mergeDashboardProjects:x=>x,
 normalizeDashboardTasks:x=>x,renderDashboard:()=>{renders++;},
});
vm.runInContext(`const pollingResources=new Map();let dashboardRefreshInFlight=null;`+
 ['pollResource','refreshDashboard','loadDashboard'].map(fn).join('\n'),c);
(async()=>{
 vm.runInContext('refreshDashboard()',c);
 for(let i=0;i<50;i++)await Promise.resolve();
 if(process.argv[2]==='partial'){
  assert.ok(renders>0,'LEGACY_RED_PARTIAL: successful tasks blocked by projects');
 }else{
  now=20000;for(let i=0;i<50;i++)await Promise.resolve();
  assert.equal(vm.runInContext('dashboardRefreshInFlight',c),null,
   'LEGACY_RED_TIMEOUT: original loader never releases inflight at the deadline');
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
"""


@pytest.mark.unit
@pytest.mark.parametrize("scenario", ["partial", "timeout"])
def test_original_loader_has_explicit_behavioral_counterexample(scenario):
    import hashlib

    preimage = ROOT / "reports/ui-e2e-followup-2026-10-26/dashboard-stage/novel_quickstart.preimage.html"
    assert hashlib.sha256(preimage.read_bytes()).hexdigest() == (
        "1138fe7cd19ab008d12c4f6489ca253faa7b9b06a4e9c030dedcda1063e2ac86"
    )
    result = subprocess.run(
        [shutil.which("node"), "-e", LEGACY_HARNESS, str(preimage), scenario],
        cwd=ROOT, capture_output=True, text=True, timeout=10,
    )
    # Expected failure must be the actual old behavior, never a missing fixture.
    assert result.returncode != 0
    assert "LEGACY_RED_" + scenario.upper() in result.stderr, result.stdout + result.stderr


CARD_HARNESS = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const script=fs.readFileSync(process.argv[1],'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
function fn(name){
 const m=new RegExp(`^([\\t ]*)(?:async )?function ${name}\\(`,'m').exec(script);
 // The shipped card's outer closing brace uses spaces after a legacy tabbed
 // declaration. Extract that actual function, not following window handlers.
 assert.ok(m,name);const indent=name==='renderTaskCard'?'  ':m[1];
 const end=script.indexOf('\n'+indent+'}',m.index);
 return script.slice(m.index,end+indent.length+2);
}
const rows=JSON.parse(process.argv[2]),scenario=process.argv[3];
const c=vm.createContext({console,window:{},document:{},$(){return null;},
 esc:String,fmtWords:String,fmtShort:String,fmtDur:String,elapsedSinceCreated:()=>'',
 latestProgressFraction:()=>null,taskModelSelectHtml:()=>'',renderRepairPanel:()=>'',
 describeStageEvent:()=>'',formatConceptTournamentProgress:()=>'',
 hasPersistedListingProject:()=>true,STAGE:{},selectedSlugs:new Set(),highlightTaskId:null,
 llmModel:'',projectTitleBySlug:new Map(),projectBySlug:new Map(),allTasks:[],project:structuredClone(
  ['closed','closed-legacy-card','closed-no-full','historical-task'].includes(scenario)?rows.closed:rows.partial),
});
const names=['isActiveTask','dashboardProjectStatus','dashboardProjectStage','projectToDashboardTask',
 'getTaskChapterWordStats','isConceptionOnlyTask','isExecutionWindowTask','isCompletedBookTask',
 'isBodyCompletePendingDelivery','isUnverifiedCompletedTask','isShortCompletedTask','isFanqieShortTask',
 'taskUnitLabels','taskExecutionTerminalCode','taskExecutionStopPresentation','repairPhaseOf',
 'taskErrorSummary','attentionReasonForTask','classifyBookState','hasStagePrefix','taskProgressSyncState','taskKnowledgeProgressText',
 'formatEventClock','latestStageEvent','buildTaskProgressInsight','renderProgressInsightHtml',
 'renderBookStateStrip','renderTaskCard'];
vm.runInContext(names.filter(name => name !== 'taskErrorSummary'
 || script.includes('function taskErrorSummary(')).map(fn).join('\n'),c);
const run=s=>vm.runInContext(s,c);
if(['full-open','unverified'].includes(scenario)){
 run(`project.adopted_chapters=project.completed_chapters=4;
  project.adopted_chapter_numbers=[1,2,3,4];`);
 if(scenario==='unverified')run(`project.adoption_evidence='unverified';`);
}
if(scenario==='closed-no-full')run(`project.status='closed';project.adopted_chapters=2;
 project.completed_chapters=2;project.adopted_chapter_numbers=[1,2];`);
if(scenario==='running')run(`project.execution_active=project.has_active_workflow=true;
 project.activity_status='active';project.current_workflow={status:'running',current_step:'judge'};`);
run('var card=projectToDashboardTask(project);');
if(scenario==='closed-legacy-card')run(`card.status='incomplete';`);
if(scenario==='historical-task')run(`card.status='completed';card.synthetic_project=false;`);
if(scenario==='failed')run(`card.status='failed';card.synthetic_project=false;
 card.projection_authority='task_execution';card.error='retained-native-error';`);
if(scenario==='recorded-traceback'){
 c.errorTask=rows.errorTask;
 run(`Object.assign(card,errorTask);card.diagnostics={raw_error:card.error};`);
 const raw=run('JSON.stringify(card)'),error=run('card.error'),status=run('card.status');
 assert.match(error,/Traceback/);assert.match(error,/fanqie_segment_not_adopted:4/);
 const html=run('renderTaskCard(card)'),reason=run('attentionReasonForTask(card)');
 const expected='第4章尚未采用，请查看审校与证据状态；已有稿件保留';
 assert.match(html,new RegExp(expected));assert.equal(reason,expected);
 assert.doesNotMatch(html,/Traceback|File "/);assert.doesNotMatch(reason,/Traceback|File "/);
 const next=run('classifyBookState(card).next');
 assert.equal(next,'查看诊断与审校证据，修复后从当前进度继续；已有稿件保留。');
 assert.match(html,new RegExp(next));assert.doesNotMatch(next,/删除|重试/);
 assert.doesNotMatch(html,/查看诊断后重试，或完全删除这本书/);
 assert.equal(run('JSON.stringify(card)'),raw);assert.equal(run('card.diagnostics.raw_error'),error);
 assert.equal(run('card.status'),status);assert.equal(status,'failed');
 assert.ok(Array.from(run('taskErrorSummary(card.error)')).length<=180);
 console.log('PASS card '+scenario);process.exit(0);
}
const output=run('renderTaskCard(card)'),view=run('classifyBookState(card)');
assert.equal(run('isCompletedBookTask(card)'),false,'fast read never grants delivery');
if(['closed','closed-legacy-card','closed-no-full','historical-task','full-open'].includes(scenario)){
 assert.match(output,/交付待核验/);assert.match(output,/查看详情/);
 assert.doesNotMatch(output,/onclick="(?:resumeProjectBySlug|resumeTaskById|markProjectCompleteFromDashboard)\(/);
 assert.notEqual(view.phase,'blocked');assert.notEqual(view.phase,'complete');
 assert.doesNotMatch(output,/正文齐备，待交付|未完成.*可恢复/);
 if(scenario==='historical-task')assert.match(output,/历史记录完成/);
}else{
 assert.notEqual(view.mode,'交付待核验');assert.doesNotMatch(output,/查看详情 \/ 交付核验/);
 if(scenario==='failed')assert.match(output,/retained-native-error/);
 if(scenario==='running')assert.equal(run('card.status'),'running');
}
console.log('PASS card '+scenario);
"""


@pytest.mark.unit
@pytest.mark.parametrize("scenario", [
    "closed", "closed-legacy-card", "closed-no-full", "historical-task",
    "partial", "full-open", "unverified", "failed", "running", "recorded-traceback",
])
def test_recorded_progress_rows_through_real_projection_and_card(scenario):
    result = subprocess.run(
        [shutil.which("node"), "-e", CARD_HARNESS,
         "src/bestseller/web/novel_quickstart.html", json.dumps(RECORDED_PROGRESS_ROWS), scenario],
        cwd=ROOT, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS card " + scenario in result.stdout


@pytest.mark.unit
@pytest.mark.parametrize("scenario", ["closed", "partial"])
def test_deployed_recorded_card_has_real_semantic_counterexample(scenario):
    preimage = ROOT / (
        "reports/ui-e2e-followup-2026-10-26/dashboard-stage/"
        "semantic-correction/preimage/src/bestseller/web/novel_quickstart.html"
    )
    result = subprocess.run(
        [shutil.which("node"), "-e", CARD_HARNESS, str(preimage),
         json.dumps(RECORDED_PROGRESS_ROWS), scenario],
        cwd=ROOT, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "AssertionError" in result.stderr
    assert "ReferenceError" not in result.stderr and "SyntaxError" not in result.stderr
    assert "交付待核验" in result.stderr


@pytest.mark.unit
@pytest.mark.parametrize("error,expected", [
    (None, ""),
    ("\n\r\n", ""),
    ("fanqie_segment_not_adopted:4", "第4章尚未采用，请查看审校与证据状态；已有稿件保留"),
    ("Error: fanqie_segment_not_adopted:3\n\n", "第3章尚未采用，请查看审校与证据状态；已有稿件保留"),
    ("ValueError: knowledge_not_closed:2", "第2章知识入库尚未闭合；已有正文保留"),
    ("ValueError: fanqie_segment_knowledge_not_closed:4", "第4章知识入库尚未闭合；已有正文保留"),
    ("Traceback (most recent call last):\n  File \"/app/worker.py\", line 12\n\nUnknown: evidence unavailable\n\n", "Unknown: evidence unavailable"),
    ("ValueError: prefix_fanqie_segment_not_adopted:4", "ValueError: prefix_fanqie_segment_not_adopted:4"),
    ("fanqie_segment_not_adopted:0", "fanqie_segment_not_adopted:0"),
    ("Unknown: " + "证" * 200, ("Unknown: " + "证" * 200)[:179] + "…"),
    ("Unknown: <b>untrusted text</b>", "Unknown: <b>untrusted text</b>"),
])
def test_error_summary_is_bounded_exact_and_does_not_invent_authority(error, expected):
    script = r"""
const fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('src/bestseller/web/novel_quickstart.html','utf8');
const m=/^  function taskErrorSummary\(error\) \{[\s\S]*?^  \}/m.exec(html);
if(!m)throw Error('summary API missing');
const input=JSON.parse(process.argv[1]);
const output=vm.runInNewContext(m[0]+'\ntaskErrorSummary(input)',{input});
console.log(JSON.stringify(output));
"""
    result = subprocess.run(
        [shutil.which("node"), "-e", script, json.dumps(error)],
        cwd=ROOT, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected
    assert len(expected) <= 180


# Exact deployed PG row decision fields; titles anonymized, no paid receipts.
RECORDED_PROGRESS_ROWS = {'closed': {'slug': 'realistic-life-1792514984', 'status': 'completed', 'book_state': 'delivery_pending', 'activity_status': 'inactive', 'execution_active': False, 'has_active_workflow': False, 'adoption_evidence': 'verified', 'adopted_chapters': 4, 'adopted_chapter_numbers': [1, 2, 3, 4], 'completed_chapters': 4, 'target_chapters': 4, 'candidate_chapters': 0, 'delivery_closure': {'status': 'not_checked', 'is_complete': False, 'body_complete': False, 'reason': 'delivery_verification_not_requested', 'reader': 'read_book_delivery_closure'}, 'target_word_count': 8000, 'word_count_total': 10940, 'project_type': 'fanqie_short', 'content_mode': 'fanqie_short_story', 'repair_status': {'phase': 'completed', 'is_repairing': False}, 'title': 'Recorded closed project'}, 'partial': {'slug': 'realistic-life-1792907083', 'status': 'writing', 'book_state': 'in_progress', 'activity_status': 'inactive', 'execution_active': False, 'has_active_workflow': False, 'adoption_evidence': 'verified', 'adopted_chapters': 2, 'adopted_chapter_numbers': [1, 2], 'completed_chapters': 2, 'target_chapters': 4, 'candidate_chapters': 0, 'delivery_closure': {'status': 'not_checked', 'is_complete': False, 'body_complete': False, 'reason': 'delivery_verification_not_requested', 'reader': 'read_book_delivery_closure'}, 'target_word_count': 8000, 'word_count_total': 4932, 'project_type': 'fanqie_short', 'content_mode': 'fanqie_short_story', 'repair_status': {'phase': 'normal', 'is_repairing': False}, 'title': 'Recorded partial project'}}

# Recorded native task error; preserved exactly, no secrets or provider response.
RECORDED_PROGRESS_ROWS["errorTask"] = {'task_id': 'c6935ebf-e2a9-4c06-80d4-af871d8f07e3', 'task_type': 'autowrite', 'status': 'failed', 'current_stage': 'failed', 'project_status': 'writing', 'activity_status': 'inactive', 'synthetic_project': False, 'projection_authority': 'task_execution', 'execution_active': False, 'has_active_workflow': False, 'error': 'Traceback (most recent call last):\n  File "/app/src/bestseller/web/server.py", line 5526, in _run_autowrite_worker\n    with bind_progress(progress):\n                 ^^^^^^^^^^^^^^^^^\n  File "/usr/local/lib/python3.11/asyncio/runners.py", line 190, in run\n    return runner.run(main)\n           ^^^^^^^^^^^^^^^^\n  File "/usr/local/lib/python3.11/asyncio/runners.py", line 118, in run\n    return self._loop.run_until_complete(task)\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File "/usr/local/lib/python3.11/asyncio/base_events.py", line 654, in run_until_complete\n    return future.result()\n           ^^^^^^^^^^^^^^^\n  File "/app/src/bestseller/web/server.py", line 5519, in guarded_runner\n    async with web_pipeline_owner(load_settings(), str(payload["slug"])):\n               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File "/usr/local/lib/python3.11/asyncio/tasks.py", line 489, in wait_for\n    return fut.result()\n           ^^^^^^^^^^^^\n  File "/app/src/bestseller/web/server.py", line 5493, in runner\n    await session.commit()\n  File "/app/src/bestseller/services/execution_stage.py", line 86, in wrapped\n    result = await function(*args, **kwargs)\n             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File "/app/src/bestseller/services/production_prompt_audit.py", line 52, in wrapped\n    return await function(*args, **kwargs)\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File "/app/src/bestseller/services/pipelines.py", line 17646, in run_autowrite_pipeline\n    return await run_fanqie_short_pipeline(\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File "/app/src/bestseller/services/production_prompt_audit.py", line 52, in wrapped\n    return await function(*args, **kwargs)\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File "/app/src/bestseller/services/fanqie_short_pipeline.py", line 1034, in run_fanqie_short_pipeline\n    await _run_fanqie_segments(\n  File "/app/src/bestseller/services/fanqie_short_pipeline.py", line 580, in _run_fanqie_segments\n    raise ValueError(f"fanqie_segment_not_adopted:{chapter_no}")\nValueError: fanqie_segment_not_adopted:4\n'}
