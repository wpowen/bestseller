"""Current durable evidence supersedes cached display state, without adoption."""
import copy
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest
from sqlalchemy import JSON, DefaultClause, ForeignKeyConstraint, MetaData, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.infra.db.models import ProjectModel, WorkflowRunModel, WorkflowStepRunModel
from bestseller.web import server
from test_task_projection_adoption_progress import _js_functions

pytestmark = pytest.mark.unit


def project_row(*, active=True, slug="harbor-stories", candidates=1):
    return {"slug": slug, "title": "Harbor", "status": "planning", "book_state": "in_progress",
            "activity_status": "active" if active else "inactive", "has_active_workflow": active,
            "execution_active": active, "adoption_evidence": "verified", "adopted_chapters": 0,
            "completed_chapters": 0, "candidate_chapters": candidates, "candidate_word_count_total": 2222,
            "word_count_total": 0, "target_chapters": 50, "target_word_count": 130000,
            "created_at": "2026-10-15T01:00:00+00:00", "updated_at": "2026-10-15T01:00:00+00:00",
            "repair_status": {"phase": "needs_attention", "detail": "An earlier repair failed."},
            "current_workflow": {"workflow_run_id": "current-run", "workflow_type": "chapter_pipeline",
                "status": "running" if active else "failed", "current_step": "review_chapter_v1",
                "created_at": "2026-10-16T04:24:00+00:00", "updated_at": "2026-10-16T04:30:00+00:00"}}


def old_task(slug="harbor-stories"):
    return {"task_id": "old-repair", "project_slug": slug, "task_type": "repair", "status": "incomplete",
            "current_stage": "legacy_quality_closure_needs_attention", "updated_at": "2026-10-16T04:20:00+00:00",
            "progress_events": [{"stage": "old_gate_failed", "timestamp": "2026-10-16T04:20:00+00:00"}],
            "error": "Historic rejection", "payload": {"slug": slug, "do_not_replay": True}}


@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize("slug", ["harbor-stories", "medical-notes"])
def test_current_evidence_supersedes_cache_without_events_or_progress(active, slug):
    cached = old_task(slug)
    source = copy.deepcopy(cached)
    row = project_row(active=active, slug=slug)
    result, = server._reconcile_dashboard_tasks_with_projects([cached], [row])
    assert result["task_id"] == f"project:{slug}"
    assert result["status"] == ("running" if active else "incomplete")
    assert result["current_stage"] == ("review_chapter_v1" if active else "project_candidate_review")
    assert result["workflow_run_id"] == "current-run"
    assert result["updated_at"] == row["current_workflow"]["updated_at"]
    assert result["progress_events"] == []
    assert result["chapter_word_stats"]["completed_chapters"] == 0
    assert result["chapter_word_stats"]["word_count_total"] == 0
    assert result["chapter_word_stats"]["candidate_word_count_total"] == 2222
    assert result["task_history"][0]["progress_events"] == cached["progress_events"]
    assert result["projection_migration"]["cache_preserved"] is True
    assert result.get("error") is None  # history is not a current error
    assert result["task_history"][0]["error"] == source["error"]
    assert cached == source
    repeated, = server._reconcile_dashboard_tasks_with_projects([result], [row])
    assert repeated == result  # migration is idempotent; history is not replayed


def test_new_task_without_pg_workflow_is_retained_and_other_projects_are_isolated():
    pending = {"task_id": "new-conception", "status": "queued", "project_slug": "other"}
    row = project_row(slug="other", candidates=0, active=False)
    row.pop("current_workflow")
    original = [pending, old_task()]
    projected = server._reconcile_dashboard_tasks_with_projects(original, [row, project_row()])
    assert projected[0] == pending
    assert len(projected) == 2


def test_adopted_words_still_use_pg_source_not_candidate_or_cache_counts():
    row = project_row()
    row.update(adopted_chapters=1, completed_chapters=1, word_count_total=2600)
    result = server._project_row_to_dashboard_task(row)
    assert result["chapter_word_stats"]["word_count_total"] == 2600
    assert result["chapter_word_stats"]["candidate_word_count_total"] == 2222


@pytest.mark.asyncio
async def test_list_detail_and_progress_share_current_projection_without_mutations(monkeypatch, tmp_path):
    cached = old_task()
    cache = tmp_path / ".web_tasks.json"
    original = json.dumps([cached]).encode()
    cache.write_bytes(original)

    async def load(settings, *, light=False, project_slug=None):
        assert light is True
        rows = [project_row(), project_row(slug="other-project")]
        return [row for row in rows if project_slug is None or row["slug"] == project_slug]

    monkeypatch.setattr(server, "_load_projects_payload", load)
    listing = await server._load_current_dashboard_tasks(object(), [cached])
    detail, = await server._load_current_dashboard_tasks(object(), [cached], slug="harbor-stories")
    assert detail == next(t for t in listing if t["project_slug"] == "harbor-stories")
    progress = server._task_progress_payload(detail)
    assert progress["current_stage"] == "review_chapter_v1" and progress["latest_event"] is None
    assert progress["workflow_run_id"] == "current-run"
    assert progress["task_history"][0]["current_stage"] == cached["current_stage"]
    assert cache.read_bytes() == original


@pytest.mark.asyncio
async def test_database_unavailable_does_not_recertify_cached_running(monkeypatch):
    async def unavailable(*args, **kwargs):
        raise RuntimeError("readback unavailable")
    monkeypatch.setattr(server, "_load_projects_payload", unavailable)
    cached = {**old_task(), "status": "running"}
    original = copy.deepcopy(cached)
    result, = await server._load_current_dashboard_tasks(object(), [cached])
    assert result["activity_status"] == "unknown" and not result["execution_active"]
    assert result["current_stage"] == original["current_stage"]
    assert cached == original


@pytest.mark.asyncio
@pytest.mark.parametrize("finished", [False, True])
async def test_real_workflow_select_uses_current_step_and_does_not_revive_stale_history(tmp_path, finished):
    # Actual SQL against an isolated test database; no production session config.
    path = tmp_path / "current_dashboard_test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    metadata = MetaData()
    for model in (ProjectModel, WorkflowRunModel, WorkflowStepRunModel):
        table = model.__table__.to_metadata(metadata)
        for col in table.columns:
            if isinstance(col.type, JSONB):
                col.type = JSON()
            if col.server_default is not None and "::jsonb" in str(col.server_default.arg):
                col.server_default = DefaultClause(text("'{}'"))
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    now = datetime.now(UTC)
    old_time = now - timedelta(days=30)
    project = ProjectModel(id=uuid4(), slug="different-genre", title="Different", genre="medical",
                           target_word_count=100000, target_chapters=50, metadata_json={})
    old = WorkflowRunModel(id=uuid4(), project_id=project.id, workflow_type="project_repair",
                           status="running", current_step="old_gate", created_at=old_time,
                           updated_at=old_time, metadata_json={})
    live = WorkflowRunModel(id=uuid4(), project_id=project.id, workflow_type="chapter_pipeline",
                            status="completed" if finished else "running", current_step="review_chapter_v1",
                            created_at=now - timedelta(minutes=3), updated_at=now - timedelta(minutes=2), metadata_json={})
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add_all([project, old, live, WorkflowStepRunModel(
                id=uuid4(), workflow_run_id=live.id, step_order=1, step_name="actual_review",
                status="completed", created_at=now, input_ref={}, output_ref={})])
            await session.commit()
            from sqlalchemy import event
            selected_columns=[]
            def capture(_connection,statement,_multiparams,_params,_options):
                if hasattr(statement,"selected_columns") and "JOIN workflow_runs" in str(statement):
                    selected_columns.append(set(statement.selected_columns.keys()))
            event.listen(engine.sync_engine,"before_execute",capture)
            try:
                result = (await server._load_workflow_activity_by_project(session, [project.id]))[project.slug]
            finally:
                event.remove(engine.sync_engine,"before_execute",capture)
            assert len(selected_columns)==1
            assert {"planner_heartbeat_at","latest_step_at"} <= selected_columns[0]
            assert not {"metadata","metadata_json","input_ref","output_ref"} & selected_columns[0]
            assert result["execution_active"] is (not finished)
            assert result["status"] == ("inactive" if finished else "active")
            assert result["current_workflow"]["workflow_run_id"] == str(live.id)
            assert result["current_workflow"]["current_step"] == "review_chapter_v1"
            if not finished:
                assert datetime.fromisoformat(result["current_workflow"]["last_evidence_at"]) == now
            assert not session.dirty and not session.new
            assert old.status == "running"  # preserve historic truth
    finally:
        await engine.dispose()


def test_actual_page_prefers_current_pg_over_legacy_cache_and_preserves_history():
    functions = _js_functions([
        "mergeDashboardProjects", "projectToDashboardTask", "dashboardProjectStatus", "dashboardProjectStage",
        "shouldProjectAppearOnDashboard", "isProjectArchivedBook", "isProjectClosedBook", "isActiveTask",
        "normalizeDashboardTasks", "dashboardTaskPriority", "taskTimestamp", "classifyBookState",
        "repairPhaseOf", "isConceptionOnlyTask", "isCompletedBookTask", "isUnverifiedCompletedTask",
        "getTaskChapterWordStats", "hasStagePrefix", "taskExecutionTerminalCode", "taskExecutionStopPresentation",
    ])
    data = json.dumps({"project": project_row(), "cached": old_task()})
    script = functions + '\nconst STAGE = {};\nconst assert=require("node:assert/strict");\nconst input=' + data + ''';
const original = JSON.stringify(input.cached);
let result = normalizeDashboardTasks(mergeDashboardProjects([input.cached], [input.project]));
assert.equal(result.length, 1);
let card = result[0];
assert.equal(card.current_stage, 'review_chapter_v1');
assert.equal(card.updated_at, input.project.current_workflow.updated_at);
assert.equal(card.status, 'running');
assert.equal(result.filter(isActiveTask).length, 1);
assert.equal(classifyBookState(card).label, '审校中');
assert.equal(card.chapter_word_stats.completed_chapters, 0);
assert.equal(card.chapter_word_stats.candidate_chapters, 1);
assert.deepEqual(card.progress_events, []);
assert.equal(card.task_history[0].current_stage, input.cached.current_stage);
assert.equal(JSON.stringify(input.cached), original);
// A later cached timestamp is not authority and must not beat PostgreSQL.
assert.equal(mergeDashboardProjects([{...input.cached,status:'running',updated_at:'2099-01-01'}],
 [input.project])[0].current_stage, 'review_chapter_v1');
// Parallel read race: retain the newer PG projection, including history.
const older = {...input.project,current_workflow:{...input.project.current_workflow,updated_at:'2026-10-16T04:25:00+00:00'}};
assert.deepEqual(mergeDashboardProjects([card],[older]), [card]);
// Once stopped, retain actual candidate prose without claiming execution/adoption.
const stopped = {...input.project,has_active_workflow:false,execution_active:false,activity_status:'inactive',
 current_workflow:{...input.project.current_workflow,status:'failed',updated_at:'2026-10-16T04:35:00+00:00'}};
card = mergeDashboardProjects([input.cached], [stopped])[0];
assert.equal(card.status, 'incomplete');
assert.equal(card.current_stage, 'project_candidate_review');
assert.equal(isActiveTask(card), false);
assert.equal(classifyBookState(card).label, '草稿待审');
assert.deepEqual(card.progress_events, []);
'''
    completed = subprocess.run(["/opt/homebrew/bin/node", "-e", script], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_entire_dashboard_script_has_valid_javascript():
    html = (Path(server.__file__).with_name("novel_quickstart.html")).read_text()
    script = html.split("<script>", 1)[1].split("</script>", 1)[0]
    result = subprocess.run(["/opt/homebrew/bin/node", "--check"], input=script,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('active,closed,expected',[(False,False,'actual-failed'),(True,False,'project:harbor-stories'),(False,True,'project:harbor-stories')])
def test_later_planning_failure_survives_registration_but_not_new_live_or_closed_work(active,closed,expected):
    row=project_row(active=active,candidates=0)
    row['current_workflow'].update(status='running' if active else 'completed',updated_at='2026-10-21 03:15:08.590000+00:00')
    row['updated_at']='2026-10-21 03:15:08.590000+00:00'
    if closed:
        row['book_state']='closed_complete'
        row['delivery_closure']={'status':'verified','is_complete':True}
    failure={'task_id':'actual-failed','task_type':'autowrite','project_slug':'harbor-stories','status':'failed',
        'updated_at':'2026-10-21T11:15:08.710000+08:00','current_stage':'failed',
        'error':'source_bound_planning_canonical_selection_required','payload':{'_run_conception':True},
        'progress_events':[{'stage':'project_created'}]}
    original=copy.deepcopy(failure)
    result,=server._reconcile_dashboard_tasks_with_projects([failure],[row])
    assert result['task_id']==expected
    if expected=='actual-failed':
        assert result['status']=='failed' and result['error']==failure['error'] and not result['execution_active']
        assert result['synthetic_project'] is False and result['projection_authority']=='task_execution'
    assert failure==original



def test_only_current_process_execution_owner_survives_inactive_registration():
    row=project_row(active=False,candidates=0);row['current_workflow']['status']='completed'
    cached={**old_task(),'task_id':'real-live','task_type':'autowrite','status':'running',
        'current_stage':'conception_review_revalidation_required','updated_at':'2026-10-16T04:40:00+00:00'}
    owned={**cached,'execution_owner_verified':True}
    live,=server._reconcile_dashboard_tasks_with_projects([owned],[row])
    assert live['task_id']=='real-live' and live['execution_active'] is True and live['status']=='running'
    old,=server._reconcile_dashboard_tasks_with_projects([cached],[row])
    assert old['task_id']=='project:harbor-stories' and not old['execution_active']


def test_execution_ownership_lives_only_inside_actual_worker_and_is_not_persisted(tmp_path,monkeypatch):
    manager=server.WebTaskManager(persist_path=tmp_path/'test-owner.json')
    task=server.WebTaskState(task_id='fixture-owner',task_type='autowrite',status='running',
        created_at='2026-10-21T00:00:00+00:00',updated_at='2026-10-21T00:00:00+00:00',payload={})
    manager._tasks[task.task_id]=task
    async def scope(_):return 'isolated-owner-scope'
    async def check(_):return None
    monkeypatch.setattr(manager,'_task_execution_scope',scope)
    monkeypatch.setattr(manager,'_assert_task_execution_open',check)
    observed=[]
    def worker(tid,payload):
        observed.append(manager.is_task_executing(tid))
        assert 'execution_owner_verified' not in manager.get_task(tid)
    assert not manager.is_task_executing(task.task_id)
    manager._run_with_slot(task.task_id,worker,{})
    assert observed==[True] and not manager.is_task_executing(task.task_id)
