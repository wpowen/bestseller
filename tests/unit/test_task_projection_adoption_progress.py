from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    QualityScoreModel,
    ReviewReportModel,
)
from bestseller.services import continuity, web_progress_queries
from bestseller.services.draft_promotion import numeric_promotion_debt_receipt
from bestseller.services.reviews import chapter_review_body_fingerprint
from bestseller.web import server

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def synthetic_projection_database_boundary(monkeypatch):
    # These synthetic sessions exercise receipt/count projection. Reprojection
    # against entity tables is covered separately with real isolated SQL.
    from bestseller.services import story_bible_projection

    async def current_projection(_session, *, project_id, source_lineages):
        assert all(source['project_id'] == str(project_id) for source in source_lineages.values())
        return dict.fromkeys(source_lineages, 'legacy_current')

    monkeypatch.setattr(story_bible_projection, 'adopted_bible_projection_statuses', current_projection)


@pytest.mark.parametrize("defect", [None, "corrupt", "mirror_only"])
def test_export_bytes_verify_across_container_mounts(tmp_path, defect):
    content = b"adopted chapter"
    digest = hashlib.sha256(content).hexdigest()
    folder = tmp_path / "other-book" / ".export-artifacts" / digest
    folder.mkdir(parents=True)
    if defect != "mirror_only":
        (folder / "chapter-001.md").write_bytes(b"changed" if defect == "corrupt" else content)
    (tmp_path / "other-book" / "chapter-001.md").write_bytes(content)
    artifact = SimpleNamespace(checksum=digest, storage_uri="/different-host/output/other-book/.export-artifacts/" + digest + "/chapter-001.md")
    settings = SimpleNamespace(output=SimpleNamespace(base_dir=tmp_path))
    assert server._export_artifact_bytes_verified(artifact, settings, "other-book") is (defect is None)


def chapter(project_id, number, state="candidate", *, current=True):
    ch = ChapterModel(id=uuid4(), project_id=project_id, chapter_number=number,
                      title="Chapter", current_word_count=9000, target_word_count=2600,
                      status="complete", production_state="ok", metadata_json={})
    draft = ChapterDraftVersionModel(id=uuid4(), project_id=project_id, chapter_id=ch.id,
                                    version_no=1, content_md="真实正文。" * 80,
                                    word_count=9000, is_current=current, promotion_state=state)
    return ch, draft


def bound_snapshot(ch, draft, *, mode="promoted"):
    """A synthetic row with the real source/raw-bound view and receipt chain."""
    source = {
        "schema": "adopted-knowledge.v1", "project_id": str(ch.project_id),
        "chapter_id": str(ch.id), "source_chapter_draft_version_id": str(draft.id),
        "source_version_no": draft.version_no,
        "source_body_sha256": hashlib.sha256(draft.content_md.encode()).hexdigest(),
    }
    snapshot = SimpleNamespace(
        id=uuid4(), project_id=ch.project_id, chapter_id=ch.id,
        chapter_number=ch.chapter_number, extraction_status=f"ok_{mode}",
        time_anchor=None, chapter_time_span=None,
        raw_extraction=json.dumps({"facts": []}), extraction_model="offline-fixture",
        facts={"facts": [], "snapshot_contract": {**source, "is_usable": True}},
    )
    view = continuity.build_snapshot_epistemic_view(snapshot, source_body=draft.content_md)
    continuity._publish_epistemic_view(
        snapshot, view, actor="offline-projection-fixture",
        reason="Bind synthetic current-page evidence to the exact source and raw extraction",
        before_sha256=continuity.snapshot_epistemic_witness(snapshot),
    )
    assert continuity._verified_epistemic_view(
        snapshot, draft.content_md, continuity._SnapshotInput(),
    ) is not None
    return snapshot


class Session:
    def __init__(self, pairs=()):
        self.pairs = pairs
        self.objects = {}
        self.snapshots = []

    async def execute(self, statement):
        assert statement.is_select  # projection never repairs or adopts data
        if "FROM chapter_state_snapshots" in str(statement):
            return [(snapshot,) for snapshot in self.snapshots]
        return [] if any(name in str(statement) for name in ("planning_artifact_versions", "export_artifacts")) else self.pairs

    async def get(self, model, identity):
        return self.objects.get((model, identity))

    async def scalar(self, statement):
        model = statement.column_descriptions[0].get("entity")
        if model in {ChapterDraftVersionModel, QualityScoreModel, ReviewReportModel}:
            params = statement.compile().params
            row = self.objects.get((model, params.get("id_1")))
            return row if row is not None and all(
                getattr(row, name) == params[name + "_1"]
                for name in ("project_id", "chapter_id", "target_type", "target_id")
                if name + "_1" in params
            ) else None
        return len(self.pairs)

    async def scalars(self, statement):
        assert statement.column_descriptions[0].get("entity") is ChapterDraftVersionModel
        params = statement.compile().params
        return [
            draft for _, draft in self.pairs
            if draft is not None
            and draft.project_id == params["project_id_1"]
            and draft.chapter_id == params["chapter_id_1"]
            and draft.promotion_state == params["promotion_state_1"]
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("marketing_only", [False, True])
async def test_execution_progress_is_unknown_without_workflow_or_prose(tmp_path, monkeypatch, marketing_only):
    project = SimpleNamespace(id=uuid4(), slug="harbor-fiction", title="Project",
                              status="planning", target_chapters=50, language="zh-CN")

    @asynccontextmanager
    async def scope(_):
        session = Session()
        original_execute = session.execute
        async def execute(statement):
            if marketing_only and "planning_artifact_versions" in str(statement):
                return [("promotional_brief", 1, {"copy": "营销文案不等于正文导出"})]
            return await original_execute(statement)
        session.execute = execute
        yield session

    async def get_project(*_):
        return project

    async def overview(*_):
        return {"runs": [], "run_count": 0, "recent_steps": []}

    monkeypatch.setattr(server, "session_scope", scope)
    monkeypatch.setattr(server, "get_project_by_slug", get_project)
    monkeypatch.setattr(web_progress_queries, "load_execution_workflow_summary", overview)
    value = await server._load_execution_progress_payload(SimpleNamespace(), project.slug)
    assert value["execution_active"] is False
    assert value["health"] == "unknown"
    assert value["evidence_status"] == "no_workflow"
    assert value["project"]["chapters_written"] == 0
    assert value["project"]["adopted_chapters"] == 0
    assert value["knowledge"]["status"] == "pending"
    assert value["knowledge"]["label"] == "未开始"
    assert value["knowledge"]["closed_chapters"] == 0
    assert value["knowledge"]["adopted_chapters"] == 0
    assert next(p for p in value["phases"] if p["key"] == "knowledge")["status"] == "pending"
    assert next(p for p in value["phases"] if p["key"] == "export")["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", [None, "hash", "owner", "status", "approval", "design_only", "scope"])
async def test_canonical_premise_and_design_lock_do_not_complete_foundation(monkeypatch, defect):
    from test_conception_approval_bridge import _snapshot

    from bestseller.infra.db.models import PlanningArtifactVersionModel

    saved = _snapshot()
    content = saved["content"]
    snapshot_id, project_id = uuid4(), uuid4()
    snapshot = SimpleNamespace(id=snapshot_id, project_id=project_id,
                               artifact_type="conception_snapshot", status="canonical", content=content)
    project = SimpleNamespace(id=project_id, slug="canonical-project", title="Project",
                              status="planning", target_chapters=12, language="zh-CN", metadata_json={
        "conception_approved": True, "creation_canonical_snapshot_id": str(snapshot_id),
        "creation_canonical_snapshot_hash": server.canonical_snapshot_content_hash(
            {"status": "canonical", "content": content}),
        "book_design_snapshot": {"schema": "design-lock-only"},
    })
    if defect == "hash":
        project.metadata_json["creation_canonical_snapshot_hash"] = "stale"
    elif defect == "owner":
        snapshot.project_id = uuid4()
    elif defect == "status":
        snapshot.status = "superseded"
    elif defect == "approval":
        project.metadata_json["conception_approved"] = False
    elif defect == "design_only":
        project.metadata_json = {"book_design_snapshot": {"schema": "design-lock-only"}}
    elif defect == "scope":
        project.target_chapters = 50
    session = Session()
    session.objects[(PlanningArtifactVersionModel, snapshot_id)] = snapshot

    @asynccontextmanager
    async def scope(_):
        yield session

    async def get_project(*_):
        return project

    async def overview(*_):
        return {"runs": [], "run_count": 0, "recent_steps": []}

    monkeypatch.setattr(server, "session_scope", scope)
    monkeypatch.setattr(server, "get_project_by_slug", get_project)
    monkeypatch.setattr(web_progress_queries, "load_execution_workflow_summary", overview)
    value = await server._load_execution_progress_payload(SimpleNamespace(), project.slug)
    phases = {p["key"]: p for p in value["phases"]}
    assert phases["premise"]["status"] == ("done" if defect is None else "pending")
    for key in ["book_spec", "world_spec", "cast_spec", "story_design_kernel"]:
        assert phases[key]["status"] == "pending"
    assert value["artifacts"] == []
    assert project.metadata_json["book_design_snapshot"] == {"schema": "design-lock-only"}


@pytest.mark.asyncio
async def test_promoted_incumbent_and_candidate_revision_are_separate():
    project_id = uuid4()
    ch, adopted = chapter(project_id, 1, "promoted", current=False)
    _, revision = chapter(project_id, 1)
    revision.chapter_id = ch.id
    draft_only = chapter(project_id, 2)
    stats = (await server._load_adopted_chapter_progress(
        Session([(ch, adopted), (ch, revision), draft_only]), [project_id],
    ))[project_id]
    assert stats["adopted_chapters"] == stats["completed_chapters"] == 1
    assert stats["promoted_chapters"] == 1
    assert stats["accepted_with_debt_chapters"] == 0
    assert stats["candidate_chapters"] == 2
    assert stats["adopted_chapter_numbers"] == [1]
    assert stats["word_count_total"] < 9000  # measured body, not metadata
    assert len(stats["chapters"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", [False, True])
async def test_debt_requires_exact_current_body_score_and_review(stale):
    project_id = uuid4()
    ch, draft = chapter(project_id, 1)
    score_id, report_id = uuid4(), uuid4()
    report = ReviewReportModel(id=report_id, project_id=project_id,
                              target_type="chapter", target_id=ch.id, structured_output={
        "draft_id": str(draft.id),
        "reviewed_body_fingerprint": chapter_review_body_fingerprint(draft.content_md),
        "findings": [],
    })
    score = QualityScoreModel(id=score_id, project_id=project_id,
                              target_type="chapter", target_id=ch.id,
                              chapter_draft_version_id=draft.id,
                              review_report_id=report_id, is_current=True, judge_key="real-review",
                              score_goal=.8, score_conflict=.8, score_emotion=.8, score_style=.8,
                              score_overall=.8,
                              evidence_summary={"hard_gates_passed": True, "blocking_codes": []})
    receipt = numeric_promotion_debt_receipt(draft, score, report)
    assert receipt is not None
    ch.metadata_json = {"numeric_promotion_debt": receipt, "chapter_quality_debt": True,
                        "chapter_quality_debt_reason": "chapter_not_promoted"}
    ch.production_state = "quality_debt"
    session = Session([(ch, draft)])
    session.objects = {(ChapterDraftVersionModel, draft.id): draft,
                       (QualityScoreModel, score_id): score, (ReviewReportModel, report_id): report}
    source = {"schema": "adopted-knowledge.v1", "project_id": str(project_id),
              "chapter_id": str(ch.id), "source_chapter_draft_version_id": str(draft.id),
              "source_version_no": draft.version_no,
              "source_body_sha256": hashlib.sha256(draft.content_md.encode()).hexdigest()}
    ch.metadata_json["knowledge_feedback_receipts"] = [{"status": "ok", "source_lineage": source}]
    ch.metadata_json["story_bible_update_receipts"] = [{"status": "empty", "source_lineage": source}]
    session.snapshots = [bound_snapshot(ch, draft, mode="accepted_with_debt")]
    if stale:
        draft.content_md += "正文后来改变。"
    stats = (await server._load_adopted_chapter_progress(session, [project_id]))[project_id]
    assert stats["accepted_with_debt_chapters"] == int(not stale)
    assert stats["completed_chapters"] == int(not stale)
    assert stats["candidate_chapters"] == int(stale)
    assert stats["knowledge"]["closed_chapters"] == int(not stale)


@pytest.mark.asyncio
async def test_disk_exports_and_candidate_wordcounts_do_not_complete_chapters(tmp_path):
    project = SimpleNamespace(id=uuid4(), slug="desert-journey", language="zh-CN")
    directory = tmp_path / project.slug
    directory.mkdir()
    export = directory / "chapter-001.md"
    export.write_text("历史导出正文。", encoding="utf-8")
    pairs = [chapter(project.id, 1)]
    stats = (await server._load_project_content_stats(
        Session(pairs), [project], settings=SimpleNamespace(output=SimpleNamespace(base_dir=tmp_path)),
        include_filesystem=True,
    ))[project.id]
    assert stats["completed_chapters"] == 0
    assert stats["candidate_chapters"] == 1
    assert stats["chapters_on_disk"] == 1
    assert stats["word_count_total"] == 0
    assert export.read_text() == "历史导出正文。"


def _js_functions(names):
    if "classifyBookState" in names:
        names = list(dict.fromkeys([*names, "taskErrorSummary", "isBodyCompletePendingDelivery", "isCompletedBookTask",
            "isConceptionOnlyTask", "isExecutionWindowTask", "getTaskChapterWordStats"]))
    source = (ROOT / "src/bestseller/web/novel_quickstart.html").read_text()
    result = []
    for name in names:
        start = source.index(f"function {name}(")
        brace = source.index("{", source.index(")", start))
        depth, end = 1, brace + 1
        while depth:
            depth += (source[end] == "{") - (source[end] == "}")
            end += 1
        result.append(source[start:end])
    return "\n".join(result)


def test_homepage_does_not_invent_execution_or_adoption():
    functions = _js_functions([
        "isActiveTask", "dashboardProjectStatus", "dashboardProjectStage",
        "getTaskChapterWordStats", "taskProgressSyncState", "taskKnowledgeProgressText",
    ])
    script = functions + """
const assert = require('node:assert/strict');
for (const status of ['planning', 'running', 'revising']) {
  const project = {status, book_state:'in_progress', has_active_workflow:false};
  assert.equal(dashboardProjectStatus(project), 'project');
  assert.equal(isActiveTask({synthetic_project:true,status:'running',has_active_workflow:false}), false);
}
assert.equal(isActiveTask({synthetic_project:true,status:'running',has_active_workflow:true}), true);
assert.equal(isActiveTask({status:'queued'}), true);
assert.equal(dashboardProjectStatus({status:'planning',has_active_workflow:true}), 'running');
const candidate = {chapter_word_stats:{source:'db',adoption_evidence:'verified',
 completed_chapters:0,adopted_chapters:0,candidate_chapters:1,word_count_total:0,
 chapters:[{number:1,word_count:2000,acceptance:'candidate'}],adopted_chapter_numbers:[]},progress_events:[]};
assert.equal(getTaskChapterWordStats(candidate).completedChapters,0);
assert.equal(getTaskChapterWordStats(candidate).hasStats,true);
assert.equal(taskProgressSyncState(candidate),'unknown');
assert.equal(taskProgressSyncState({progress_events:[]}), 'unknown');
const adopted = {...candidate,chapter_word_stats:{...candidate.chapter_word_stats,
 completed_chapters:1,adopted_chapters:1,adopted_chapter_numbers:[1]},
 progress_events:[{stage:'chapter_pipeline_completed',payload:{chapter_number:1}}]};
assert.equal(taskProgressSyncState(adopted),'verified');
assert.equal(taskProgressSyncState({...adopted,progress_events:[{stage:'chapter_pipeline_completed',payload:{chapter_number:2}}]}),'mismatch');
assert.equal(taskKnowledgeProgressText(candidate),'知识入库待验证');
assert.equal(taskKnowledgeProgressText({...candidate,chapter_word_stats:{...candidate.chapter_word_stats,
 knowledge:{status:'pending',adopted_chapters:0,closed_chapters:0}}}),'知识入库0/0（未开始）');
assert.equal(taskKnowledgeProgressText({...adopted,chapter_word_stats:{...adopted.chapter_word_stats,
 knowledge:{status:'pending',adopted_chapters:1,closed_chapters:0}}}),'知识入库0/1（待闭合）');
assert.equal(taskKnowledgeProgressText({...adopted,chapter_word_stats:{...adopted.chapter_word_stats,
 knowledge:{status:'closed',adopted_chapters:1,closed_chapters:1}}}),'知识入库1/1（已闭合）');
console.log('projection-counterexamples-ok');
"""
    result = subprocess.run(["/opt/homebrew/bin/node", "-e", script], text=True,
                            capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("feedback_status", ["ok", "empty", "already_applied"])
@pytest.mark.parametrize("defect", [None, "body", "draft", "version", "owner", "schema",
                                  "unusable", "candidate", "feedback", "latest_receipt"])
async def test_knowledge_closes_only_exact_adopted_body_and_current_receipts(defect, feedback_status):
    project_id = uuid4()
    ch, draft = chapter(project_id, 1, "candidate" if defect == "candidate" else "promoted")
    source = {"schema": "adopted-knowledge.v1", "project_id": str(project_id),
              "chapter_id": str(ch.id), "source_chapter_draft_version_id": str(draft.id),
              "source_version_no": draft.version_no,
              "source_body_sha256": hashlib.sha256(draft.content_md.encode()).hexdigest()}
    ch.metadata_json = {
        "knowledge_feedback_receipts": [{"status": feedback_status, "source_lineage": dict(source)}],
        "story_bible_update_receipts": [{"status": "empty", "source_lineage": dict(source)}],
    }
    snapshot = bound_snapshot(ch, draft)
    contract = snapshot.facts["snapshot_contract"]
    if defect in {"body", "draft", "version", "owner", "schema"}:
        key, value = {"body": ("source_body_sha256", "old-body"),
                      "draft": ("source_chapter_draft_version_id", str(uuid4())),
                      "version": ("source_version_no", 99),
                      "owner": ("project_id", str(uuid4())),
                      "schema": ("schema", "legacy")}[defect]
        contract[key] = value
    elif defect == "unusable":
        contract["is_usable"] = False
    elif defect == "feedback":
        ch.metadata_json["knowledge_feedback_receipts"][0]["source_lineage"]["source_body_sha256"] = "old-body"
    elif defect == "latest_receipt":
        ch.metadata_json["knowledge_feedback_receipts"].append({
            "status": "ok", "source_lineage": {**source, "source_body_sha256": "newer-other-body"}})
    session = Session([(ch, draft)])
    session.snapshots = [snapshot]
    stats = (await server._load_adopted_chapter_progress(session, [project_id]))[project_id]
    knowledge = stats["knowledge"]
    assert knowledge["closed_chapters"] == int(defect is None)
    assert knowledge["status"] == ("closed" if defect is None else "pending")
    assert knowledge["adopted_chapters"] == int(defect != "candidate")
    assert knowledge["closed_chapter_numbers"] == ([1] if defect is None else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("bible", ["absent", "ok", "empty", "already_applied", "llm_error",
                                  "rejected_source", "requires_projection_reconciliation", "wrong_body",
                                  "success_then_error", "error_then_success", "old_success_after_current"])
async def test_explicit_bible_debt_prevents_false_knowledge_closure(bible):
    ch, draft = chapter(uuid4(), 1, "promoted")
    source = {"schema": "adopted-knowledge.v1", "project_id": str(ch.project_id),
              "chapter_id": str(ch.id), "source_chapter_draft_version_id": str(draft.id),
              "source_version_no": draft.version_no,
              "source_body_sha256": hashlib.sha256(draft.content_md.encode()).hexdigest()}
    success = {"status": "ok", "source_lineage": source}
    error = {"status": "llm_error", "source_lineage": source}
    old = {"status": "ok", "source_lineage": {**source, "source_body_sha256": "old"}}
    receipts = {"absent": [], "wrong_body": [old], "success_then_error": [success, error],
                "error_then_success": [error, success], "old_success_after_current": [success, old]}.get(
        bible, [{"status": bible, "source_lineage": source}])
    ch.metadata_json = {"knowledge_feedback_receipts": [success], "story_bible_update_receipts": receipts}
    session = Session([(ch, draft)])
    session.snapshots = [bound_snapshot(ch, draft)]
    stats = (await server._load_adopted_chapter_progress(session, [ch.project_id]))[ch.project_id]
    closed = bible in {"ok", "empty", "already_applied", "error_then_success"}
    assert stats["knowledge"]["closed_chapters"] == int(closed)
    assert stats["knowledge"]["status"] == ("closed" if closed else "pending")
    if bible == "absent":
        assert stats["knowledge"]["bible"]["status"] == "unknown"
        assert stats["knowledge"]["bible"]["unknown_chapters"] == 1


def test_summary_and_detail_share_adoption_evidence(monkeypatch):
    project = SimpleNamespace(id=uuid4(), slug="night-train", title="Project", language="zh-CN",
                              project_type="linear", target_chapters=50, target_word_count=130000,
                              metadata_json={})
    session = Session([chapter(project.id, 1, "promoted"), chapter(project.id, 2)])

    async def scalars(statement):
        assert statement.is_select
        return [project]

    session.scalars = scalars

    @asynccontextmanager
    async def scope(_):
        yield session

    monkeypatch.setattr(server, "session_scope", scope)
    detail = server._load_task_chapter_word_stats(SimpleNamespace(), [project.slug])[project.slug]
    summary = server._load_task_chapter_word_stats(SimpleNamespace(), [project.slug], summary_only=True)[project.slug]
    for key in ["completed_chapters", "adopted_chapters", "candidate_chapters", "knowledge",
                "adoption_evidence", "adopted_chapter_numbers", "word_count_total"]:
        assert summary[key] == detail[key]
    assert detail["completed_chapters"] == 1
    assert detail["candidate_chapters"] == 1
    assert len(detail["chapters"]) == 1 and summary["chapters"] == []


def test_synthetic_card_has_activity_evidence_but_no_fabricated_events():
    row = {"slug": "quiet-observatory", "status": "planning", "book_state": "in_progress",
           "has_active_workflow": False, "adoption_evidence": "verified", "adopted_chapters": 0,
           "completed_chapters": 0, "target_chapters": 50}
    card = server._project_row_to_dashboard_task(row)
    assert card["status"] == "project"
    assert card["execution_active"] is False
    assert card["evidence_status"] == "no_workflow"
    assert card["progress_events"] == []
    active = server._project_row_to_dashboard_task({**row, "has_active_workflow": True})
    assert active["status"] == "running" and active["execution_active"] is True


def test_legacy_completed_record_is_not_book_delivery():
    functions = _js_functions([
        "taskExecutionTerminalCode", "isExecutionWindowTask",
        "isActiveTask", "isConceptionOnlyTask", "getTaskChapterWordStats",
        "isCompletedBookTask", "isUnverifiedCompletedTask", "repairPhaseOf",
        "classifyBookState", "matchesDashboardFilter",
    ])
    script = functions + """
const assert = require('node:assert/strict');
const dashboardFilter = 'completed';
function isAttentionTask() { return false; }
const historical = {status:'completed', project_id:null, progress_events:[],
 metadata:{attempt_id:'historical-attempt',input_hash:'saved',result_hash:'saved'}};
const state = classifyBookState(historical);
assert.equal(state.label,'历史记录完成');
assert.equal(state.mode,'交付待核验');
assert.equal(state.phase,'unknown');
assert.ok(!state.detail.includes('未生成'));
assert.equal(isConceptionOnlyTask(historical),false);
assert.equal(isCompletedBookTask(historical),false);
assert.equal(isUnverifiedCompletedTask(historical),true);
assert.equal(Array.from({length:59}, () => historical).filter(matchesDashboardFilter).length,0);
const concept = {...historical,result:{mode:'conception_only'}};
assert.equal(isCompletedBookTask(concept),false);
assert.equal(isUnverifiedCompletedTask(concept),false);
const adopted = {...historical, chapter_word_stats:{adoption_evidence:'verified',
 adopted_chapters:50,target_chapters:50,adopted_chapter_numbers:[]},
 delivery_closure:{status:'verified',is_complete:true}};
assert.equal(isCompletedBookTask({...adopted,delivery_closure:null}),false);
assert.equal(isCompletedBookTask(adopted),true);
assert.equal(isUnverifiedCompletedTask(adopted),false);
assert.equal(matchesDashboardFilter(adopted),true);
assert.equal(isCompletedBookTask({...adopted,chapter_word_stats:{
 ...adopted.chapter_word_stats,adopted_chapters:49}}),false);
assert.equal(isCompletedBookTask({...adopted,chapter_word_stats:{
 ...adopted.chapter_word_stats,adoption_evidence:'unknown'}}),false);
assert.equal(isCompletedBookTask({...adopted,chapter_word_stats:{
 ...adopted.chapter_word_stats,target_chapters:0}}),false);
"""
    result = subprocess.run(["/opt/homebrew/bin/node", "-e", script], text=True,
                            capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
