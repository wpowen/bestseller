"""Execute the page reads with fake time/network; never contact a server or DB."""

from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const page = process.argv[1], scenario = process.argv[2];
const html = fs.readFileSync(`src/bestseller/web/novel_${page}.html`, 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script); // Also syntax-check the entire shipped script.
function fn(name) {
  const match = new RegExp(`^([\\t ]*)(?:async )?function ${name}\\(`, 'm').exec(script);
  assert.ok(match, name);
  const end = script.indexOf('\n' + match[1] + '}', match.index);
  assert.ok(end > match.index, name);
  return script.slice(match.index, end + match[1].length + 2);
}
let now = 0, serial = 0, calls = [], timers = new Map(), listeners = {}, elements = {};
let fail = new Set(), hold = new Set(), pending = [], activeView = 'Dashboard';
let status = 'running', adopted = 0, authAvailable = true, renderedTasks = [], errors = [];
const element = id => elements[id] ??= {
  textContent: '', innerHTML: '', style: {}, dataset: {}, hidden: false, value: '',
  classList: {contains: name => name === 'active' && id === '#view' + activeView,
    add() {}, remove() {}, toggle() {}},
  querySelector: () => null, addEventListener() {}, scrollIntoView() {},
};
const response = url => {
  if (url.includes('story-bible')) return {title:'planning title', characters:[]};
  if (url.includes('pipeline-flow')) return {
    project:{source:'db',title:'DB title',status:'running',target_chapters:20},
    adoption:{adoption_evidence:'verified',adopted_chapters:adopted,
      adopted_chapter_numbers:Array.from({length:adopted},(_,i)=>i+1),word_count_total:adopted*100},
    chapters:Array.from({length:adopted},(_,i)=>({chapter_number:i+1,adoption_state:'promoted'})),
  };
  if (url === '/api/library') return [{slug:'other-book',title:'DB title',
    status:'running',completed_chapters:adopted}];
  if (url.startsWith('/api/projects?')) return [{slug:'other-book'}];
  if (url.includes('/listing')) return {listing_profile:{title:'listing'},
    project:{slug:'other-book'}};
  if (url.includes('/toc')) return {chapters:[{number:1,title:'chapter',word_count:100}]};
  if (url.includes('-authorization')) return {available:authAvailable};
  if (url.endsWith('/cancel')) {status='cancelled'; return {};}
  if (url.endsWith('/continue-reviewed-conception')) {status='running'; return {};}
  if (url === '/api/tasks/t1') return {task_id:'t1',status,project_slug:'other-book',
    title:'DB title',created_at:'2026-10-01',cancel_requested:status==='running' && adopted===2,
    payload:{_run_conception:status==='failed'},chapter_word_stats:{completedChapters:adopted},progress_events:[]};
  if (url.startsWith('/api/tasks?')) return [{task_id:'t1',status,adopted}];
  return {};
};
const context = vm.createContext({
  AbortController,
  console:{warn(){},error(){}}, Date:class extends Date {static now(){return now;}},
  location:{pathname:'/flow/other-book'},
  document:{visibilityState:'visible',getElementById:element,querySelector:element,
    querySelectorAll:()=>[],createElement:()=>element('escaped'),
    addEventListener:(name,callback)=>(listeners[name]??=[]).push(callback)},
  setInterval:(callback,ms)=>{const id=++serial;
    timers.set(id,{callback,ms,next:now+ms});return id;},
  clearInterval:id=>timers.delete(id),
  setTimeout:(callback,ms)=>{const id=++serial;
    timers.set(id,{callback,ms,next:now+ms,once:true});return id;},
  clearTimeout:id=>timers.delete(id),
  fetch:(url, options={})=>{
    calls.push({url,at:now,method:options.method||'GET'});
    const send = () => {
      const broken = [...fail].some(key=>url.includes(key));
      if (broken && ['network_error','detail_auth_hidden_error'].includes(scenario))
        return Promise.reject(Error('offline'));
      return Promise.resolve({ok:!broken,status:broken?503:200,statusText:'Unavailable',
        json:async()=>response(url)});
    };
    if ([...hold].some(key=>url.includes(key)))
      return new Promise(resolve=>pending.push(()=>resolve(send())));
    return send();
  },
  window:{__activePreviewTaskId:'t1',location:{},open(){}},
  confirm:()=>true, alert:message=>errors.push(message),
  $:element, esc:String, fmtWords:String, fmtShort:String,
  rawTaskCount:0,rawProjectCount:0,rawClosedBookCount:0,rawDashboardProjectCount:0,
  allTasks:[],projectBySlug:new Map(),llmModel:'',taskStartTime:null,
  taskProgressSyncState:()=> 'verified', taskKnowledgeProgressText:()=>'',
  latestProgressFraction:()=>null, elapsedSinceCreated:()=>'',
  taskUnitLabels:()=>({unit:'章'}), taskExecutionStopPresentation:()=>null,
  isFanqieShortTask:()=>false,isConceptionOnlyTask:()=>false,isUnverifiedCompletedTask:()=>false,
  // Poll cadence only; real closure predicates are exercised by R19 tests.
  isBodyCompletePendingDelivery:()=>false,
  isCompletedBookTask:d=>d.status==='completed',isActiveTask:d=>d.status==='running',
  getTaskChapterWordStats:d=>({hasStats:true,targetChapters:20,completedChapters:adopted,
    wordTotal:adopted*100,adoptedChapterNumbers:Array.from({length:adopted},(_,i)=>i+1)}),
  buildTaskProgressInsight:()=>({}),isBlockedStage:()=>false,STAGE:{},
  mergeTaskProgressSnapshot:d=>renderedTasks.push(d.status),
  refreshPromptManifestSelector(){},hideChapterGridIfChanged(){},renderChapterGridIfChanged(){},
  hasPersistedListingProject:()=>true,taskModelSelectHtml:()=>'',tlItemHtml:()=>'',
  isProjectArchivedBook:()=>false,isProjectClosedBook:()=>false,shouldProjectAppearOnDashboard:()=>true,
  updateProjectMaps(){},enrichDashboardTaskTitles:x=>x,mergeDashboardProjects:x=>x,normalizeDashboardTasks:x=>x,
  renderDashboard(){},renderListingProfile(){},listingPayloadSignature:JSON.stringify,
  renderListingLoadError:message=>errors.push(message),
});
function run(code) {return vm.runInContext(code,context);}
async function flush() {for(let i=0;i<100;i++) await Promise.resolve();}
async function advance(ms) {
  const until=now+ms;
  while(true) {
    const next=Math.min(...[...timers.values()].map(t=>t.next));
    if(next>until) break;
    now=next;
    for(const [id,timer] of [...timers]) if(timer.next===next && timers.has(id)) {
      if(timer.once) timers.delete(id); else timer.next+=timer.ms;
      timer.callback();
    }
    await flush();
  }
  now=until; await flush();
}
async function visible(value) {
  context.document.visibilityState=value?'visible':'hidden';
  for(const callback of listeners.visibilitychange||[]) callback();
  await flush();
}
const count = part => calls.filter(c=>c.url.includes(part)).length;
const main = page==='pipeline_flow'?'pipeline-flow':page==='library'?'/api/library':'/api/tasks?';
const refresh = page==='pipeline_flow'?'loadData()':page==='library'?null:'refreshDashboard()';
async function boot() {
  if(page!=='quickstart') run(script);
  else {
    run('const pollingResources=new Map();\n'+fn('pollResource')+'\n'+[
      'refreshDashboard','boundedDashboardJson','applyDashboardReadState','loadDashboard',
      'startProgressPoll','stopProgressPoll','updateProgressView',
      'loadProgressView','loadAndRenderListing','fetchAndRenderListing',
    ].map(fn).join('\n')+`
      let dashboardRefreshInFlight=null,pollTimer=null,elapsedTimer=null,focusTaskId='t1';
      const dashboardReadState={tasks:{rows:[],receivedAt:null,error:null},
        projects:{rows:[],receivedAt:null,error:null}};
      let listingLoadedForSlug=null,listingLoadedForStatus=null,listingLoadInFlight=null;
      let listingRenderedSignature=null,listingRequestGeneration=0,listingFocusRequested=false;
      const reviewedContinuationBusy=new Set();
      function updateElapsed(){}
    `);
    const begin=script.indexOf('  // Refresh dashboard periodically when visible');
    const end=script.indexOf('  // ── Keyboard shortcuts',begin);
    const cancelBegin=script.indexOf('  window.cancelTask =');
    const cancelEnd=script.indexOf('\n  };',cancelBegin)+5;
    run(script.slice(cancelBegin,cancelEnd));
    run(script.slice(begin,end));
    run('refreshDashboard()');
  }
  await flush();
}
(async()=>{
  if(scenario==='hidden_initial') context.document.visibilityState='hidden';
  if(scenario==='single_flight') hold.add(main);
  if(scenario==='dashboard_partial') {hold.add('/api/projects?');fail.add(main);}
  if(scenario==='design_unavailable') {fail.add('story-bible');fail.add('narrative');}
  await boot();
  if(scenario==='cadence') {
    assert.equal(count(main),1);
    adopted=1;
    await advance(page==='pipeline_flow'?5000:page==='library'?30000:15000);
    assert.equal(count(main),2,'short progress/list poll');
    if(page==='pipeline_flow') {
      assert.equal(element('statChapters').textContent,1);
      assert.equal(element('infoCurrent').textContent,1);
      assert.equal(count('story-bible'),1);
      await advance(55000);
      assert.equal(count('story-bible'),2);assert.equal(count('narrative'),2);
      assert.equal(count('execution-progress'),13);
      await run('loadData()');
      assert.equal(count('story-bible'),3,'manual refresh bypasses design TTL');
      fail.add('story-bible');await run('loadData()');
      assert.ok(element('designScope').textContent.includes('规划'));
      assert.equal(element('statChapters').textContent,1,'design failure cannot erase adoption');
    }
  } else if(scenario==='hidden'||scenario==='hidden_initial') {
    await visible(false);const before=calls.length;
    await advance(120000);assert.equal(calls.length,before,'hidden page must issue no timed reads');
    await visible(true);assert.equal(count(main),2,'visible resumes immediately');
    const after=calls.length;await advance(1);assert.equal(calls.length,after);
  } else if(scenario==='single_flight') {
    // Quickstart now has a 20s deadline: assert the same single-flight
    // contract inside that live window; deadline/recovery have native tests.
    await advance(page==='quickstart'?9000:120000);
    assert.equal(count(main),1,'slow request must not overlap');
    hold.clear();pending.splice(0).forEach(release=>release());await flush();
    await advance(page==='pipeline_flow'?5000:page==='library'?30000:15000);
    assert.equal(count(main),2,'poll recovers after pending read');
  } else if(scenario==='manual') {
    await visible(false);const before=count(main);
    await advance(120000);assert.equal(count(main),before);
    if(page==='library') await context.window.archiveBook('other-book',true);
    else if(page==='quickstart') await context.window.cancelTask('t1');
    else await run(refresh);
    await flush();
    assert.equal(count(main),before+1,'manual actions and their readback still work while hidden');
    if(page!=='pipeline_flow') assert.equal(calls.filter(c=>c.method==='POST').length,1);
    assert.deepEqual(errors,[]);
  } else if(scenario==='dashboard_partial') {
    // Keep the outstanding-read assertion within the bounded 20s window.
    await advance(9000);
    assert.equal(count(main),1,'failed sibling must wait for the outstanding dashboard read');
    assert.equal(count('/api/projects?'),1);
    hold.clear();fail.clear();pending.splice(0).forEach(release=>release());await flush();
    await advance(45000);assert.equal(count(main),2);
  } else if(scenario==='design_unavailable') {
    assert.ok(element('worldBackbone').innerHTML.includes('不可用'));
    assert.ok(element('arcList').innerHTML.includes('不可用'));
    adopted=1;await advance(5000);
    assert.equal(element('statChapters').textContent,1,'missing design cannot block DB adoption');
    assert.equal(count('story-bible'),1);
    fail.clear();await visible(false);await visible(true);
    assert.equal(count('story-bible'),2);assert.equal(element('statCharacters').textContent,0);
  } else if(scenario==='http_error'||scenario==='network_error') {
    const interval=page==='pipeline_flow'?5000:page==='library'?30000:15000;
    fail.add(main);await advance(interval);assert.equal(count(main),2);
    const lastGood=page==='library'?element('#libraryGrid').innerHTML:null;
    await advance(interval);assert.equal(count(main),2,'first failure backs off');
    await advance(interval);assert.equal(count(main),3,'retry after 2x interval');
    assert.equal(page==='library'?element('#libraryGrid').innerHTML:null,lastGood);
    await advance(interval*3);assert.equal(count(main),3,'consecutive failures back off further');
    fail.clear();await visible(false);await visible(true);
    assert.equal(count(main),4,'visibility overrides retry deadline');
    await advance(interval);assert.equal(count(main),5,'success resets backoff');
    if(page==='pipeline_flow')
      assert.ok(count('execution-progress')>count(main),'resource failures are independent');
  } else if(scenario==='detail') {
    activeView='Progress';run("startProgressPoll('t1')");await flush();
    assert.equal(count('/api/tasks/t1'),1);assert.equal(count('/listing'),1);
    adopted=2;await advance(3000);assert.equal(count('/api/tasks/t1'),2);
    assert.equal(element('#pvStage').textContent,'🛑 正在停止…');
    assert.equal(count('/listing'),1,'listing must not follow 3s progress cadence');
    await advance(57000);assert.equal(count('/api/tasks/t1'),21);assert.equal(count('/listing'),2);
    const before=calls.length;await visible(false);await advance(60000);
    assert.equal(calls.length,before);
    await visible(true);assert.equal(count('/api/tasks/t1'),22);assert.equal(count('/listing'),3);
    // Completion and failure still stop the existing short-cycle task poll.
    status='failed';await advance(3000);
    assert.equal(count('continuation-authorization'),1);
    assert.equal(element('#pvReviewedContinuation').hidden,false);
    const failedCount=count('/api/tasks/t1');await advance(15000);
    assert.equal(count('/api/tasks/t1'),failedCount);
    assert.equal(calls.filter(c=>c.method==='POST').length,0,
      'authorization checks never auto-continue');
    await element('#pvContinueReviewed').onclick();await flush();
    assert.equal(count('continue-reviewed-conception'),1);assert.equal(renderedTasks.at(-1),'running');
    status='completed';await advance(3000);const completedCount=count('/api/tasks/t1');
    await advance(15000);assert.equal(count('/api/tasks/t1'),completedCount);
  } else if(scenario==='detail_slow') {
    activeView='Progress';hold.add('/api/tasks/t1');run("startProgressPoll('t1')");await flush();
    await advance(30000);assert.equal(count('/api/tasks/t1'),1);
    hold.clear();pending.splice(0).forEach(release=>release());await flush();
    assert.equal(renderedTasks.at(-1),'running');
    fail.add('/api/tasks/t1');await advance(3000);const n=count('/api/tasks/t1');
    await advance(3000);assert.equal(count('/api/tasks/t1'),n);
    await advance(3000);assert.equal(count('/api/tasks/t1'),n+1);
    fail.clear();await visible(false);await visible(true);
    assert.equal(renderedTasks.at(-1),'running');
  } else if(scenario==='listing_error') {
    activeView='Progress';fail.add('/listing');run("startProgressPoll('t1')");await flush();
    await advance(60000);assert.equal(count('/listing'),1);assert.equal(count('/api/tasks/t1'),21);
    await advance(60000);assert.equal(count('/listing'),2);
    fail.clear();status='completed';await advance(3000);
    assert.equal(count('/listing'),3,'terminal transition refreshes despite old retry deadline');
  } else if(scenario==='bounded_resources') {
    hold.add('/api/tasks/t1');hold.add('/held-book/listing');
    run("updateProgressView('t1');loadAndRenderListing('held-book','running')");await flush();
    const progress=run("pollingResources.get('progress:t1')");
    const listing=run("pollingResources.get('listing:0:held-book:running')");
    for(let i=1;i<=200;i++) {
      run(`listingRequestGeneration=${i};loadAndRenderListing('switch-book-${i}','running')`);
      await flush();
      assert.ok(run('pollingResources.size')<=64,'idle generations are pruned after each read');
    }
    assert.equal(run("pollingResources.get('progress:t1')"),progress);
    assert.equal(run("pollingResources.get('listing:0:held-book:running')"),listing);
    run("listingRequestGeneration=0;updateProgressView('t1');"+
      "loadAndRenderListing('held-book','running',true)");await flush();
    assert.equal(count('/api/tasks/t1'),1,'cleanup preserves detail single-flight');
    assert.equal(count('/held-book/listing'),1,'cleanup preserves listing single-flight');
    hold.add('/busy-book-');
    for(let i=0;i<70;i++) run(`loadAndRenderListing('busy-book-${i}','running')`);
    await flush();
    assert.equal(run('[...pollingResources.values()].filter(s=>s.promise).length'),72);
    assert.ok(run('pollingResources.size')>64,'active reads may temporarily exceed the cap');
    hold.clear();pending.splice(0).forEach(release=>release());await flush();
    assert.ok(run('pollingResources.size')<=64,'settled reads bring the Map back to its cap');
    assert.equal(run('[...pollingResources.values()].filter(s=>s.promise).length'),0);
  } else if(['detail_auth_slow','detail_auth_hidden_error','detail_toc_slow'].includes(scenario)) {
    activeView='Progress';adopted=1;run("startProgressPoll('t1')");await flush();
    const authCase=scenario!=='detail_toc_slow';
    const endpoint=authCase?'continuation-authorization':'/toc';
    const detailReads=()=>calls.filter(c=>c.url==='/api/tasks/t1').length;
    if(authCase) status='failed';
    hold.add(endpoint);await advance(3000);
    const tasks=detailReads(), secondary=count(endpoint), toc=count('/toc');
    await advance(30000);
    assert.equal(detailReads(),tasks,'single-flight covers the entire detail chain');
    assert.equal(count(endpoint),secondary,'slow auth/TOC cannot overlap');
    await visible(false);const hiddenCalls=calls.length;
    if(scenario==='detail_auth_hidden_error') fail.add(endpoint);
    hold.clear();pending.splice(0).forEach(release=>release());await flush();
    await advance(30000);
    assert.equal(calls.length,hiddenCalls,'settling auth/TOC while hidden starts no new reads');
    if(authCase) assert.equal(count('/toc'),toc,'hidden auth result/error cannot start TOC');
    fail.clear();await visible(true);
    assert.equal(detailReads(),tasks+1,'visible refresh works after secondary completion');
  }
  console.log(`${page}: ${scenario} passed`);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


@pytest.mark.parametrize("page", ["pipeline_flow", "quickstart", "library"])
@pytest.mark.parametrize(
    "scenario",
    [
        "cadence",
        "hidden",
        "hidden_initial",
        "single_flight",
        "http_error",
        "network_error",
        "manual",
    ],
)
def test_page_polling_with_fake_time_and_fetch(page: str, scenario: str) -> None:
    _run(page, scenario)


@pytest.mark.parametrize("scenario", ["detail", "detail_slow", "listing_error"])
def test_task_detail_polling_preserves_progress_and_authorization(scenario: str) -> None:
    _run("quickstart", scenario)


def test_dashboard_waits_for_all_inflight_reads_after_partial_failure() -> None:
    _run("quickstart", "dashboard_partial")


def test_design_unavailable_does_not_block_adoption_or_leave_loading_placeholders() -> None:
    _run("pipeline_flow", "design_unavailable")


def test_quickstart_resource_cleanup_is_bounded_and_preserves_inflight_reads() -> None:
    _run("quickstart", "bounded_resources")


@pytest.mark.parametrize(
    "scenario", ["detail_auth_slow", "detail_auth_hidden_error", "detail_toc_slow"]
)
def test_secondary_detail_reads_preserve_singleflight_and_hidden_pause(scenario: str) -> None:
    _run("quickstart", scenario)


def _run(page: str, scenario: str) -> None:
    node = shutil.which("node")
    assert node, "Node is required for page polling behavior tests"
    result = subprocess.run(
        [node, "-e", HARNESS, page, scenario],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{page}: {scenario} passed" in result.stdout
