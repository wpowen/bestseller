"""Flow API and its actual page script must project adoption, never demo data."""

from contextlib import asynccontextmanager
import json
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace

from pydantic import ValidationError
import pytest
from sqlalchemy import event, select
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import db as db
from test_adopted_knowledge_lineage import make_accepted_debt

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    PlanningArtifactVersionModel,
)
from bestseller.services import pipeline_flow_overview as flow
from bestseller.services.chapter_word_count_truth import authoritative_zh_word_count
from bestseller.services.pipeline_flow_schema import (
    PipelineFlowAdoptionStats,
    PipelineFlowChapterRuntime,
    PipelineFlowOverview,
)
from bestseller.settings import load_settings
from bestseller.web import server

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
HTML = Path(__file__).resolve().parents[2] / "src/bestseller/web/novel_pipeline_flow.html"


@pytest.fixture(autouse=True)
def sqlite_only(monkeypatch):
    monkeypatch.delenv("BESTSELLER_LINEAGE_PG", raising=False)


@pytest.fixture
async def configured(db, monkeypatch):
    session, project, chapter, draft = db
    conn = await session.connection()
    assert conn.dialect.name == "sqlite"
    ddl = str(CreateTable(PlanningArtifactVersionModel.__table__, if_not_exists=True).compile(dialect=conn.dialect))
    await conn.exec_driver_sql(ddl.replace("::jsonb", ""))

    @asynccontextmanager
    async def scope(_settings):
        yield session

    async def structure(*_args):
        rows = list(
            await session.scalars(select(ChapterModel).where(ChapterModel.project_id == project.id))
        )
        # Legacy labels and cached counts are deliberately misleading.
        chapters = [
            SimpleNamespace(
                chapter_number=row.chapter_number,
                title=row.title,
                status="approved",
                production_state=row.production_state,
                current_word_count=999999,
                scenes=[],
            )
            for row in rows
        ]
        return SimpleNamespace(
            total_chapters=len(rows),
            total_scenes=0,
            target_chapters=project.target_chapters,
            volumes=[SimpleNamespace(chapters=chapters)],
        )

    async def workflow(*_args, **_kwargs):
        return SimpleNamespace(
            runs=[
                SimpleNamespace(
                    workflow_run_id=project.id,
                    workflow_type="chapter_pipeline",
                    status="completed",
                    steps=[],
                    current_step="done",
                    error_message=None,
                )
            ]
        )

    async def bible(*_args):
        return SimpleNamespace(world_rules=[], characters=[], locations=[], relationships=[])

    async def narrative(*_args):
        return SimpleNamespace(plot_arcs=[], clues=[], chapter_contracts=[], scene_contracts=[])

    monkeypatch.setattr(server, "session_scope", scope)
    monkeypatch.setattr(flow, "build_project_structure", structure)
    monkeypatch.setattr(flow, "build_project_workflow_overview", workflow)
    monkeypatch.setattr(flow, "build_story_bible_overview", bible)
    monkeypatch.setattr(flow, "build_narrative_overview", narrative)
    return session, project, chapter, draft


async def payload(configured, settings=None):
    return await server._load_pipeline_flow_payload(
        settings or load_settings(), configured[1].slug, SimpleNamespace(list_tasks=lambda: [])
    )


@pytest.mark.parametrize("scenario", ["archived_prefix", "short", "empty", "zero_target"])
@pytest.mark.parametrize("export_state", ["stale", "missing"])
async def test_flow_api_uses_db_identity_and_adoption_with_stale_or_missing_exports(
    configured, tmp_path, scenario, export_state
):
    session, project, chapter, draft = configured
    target = {"archived_prefix": 50, "short": 4, "empty": 12, "zero_target": 0}[scenario]
    adopted = {"archived_prefix": 3, "short": 1, "empty": 0, "zero_target": 0}[scenario]
    project.slug = "unrelated-" + scenario
    project.title = "数据库书名"
    project.genre = "东方玄幻" if scenario == "archived_prefix" else "日常温馨"
    project.status = "archived" if scenario == "archived_prefix" else "planning"
    project.project_type = "fanqie_short" if scenario == "short" else "linear"
    project.target_chapters = target
    project.current_chapter_number = 85 if adopted else 0  # cursor is not adoption
    chapter.status = "approved"
    draft.content_md = "这是采用正文。"
    draft.word_count = 999999
    expected_words = authoritative_zh_word_count(draft.content_md) * adopted
    if adopted:
        for number in range(2, adopted + 1):
            row = ChapterModel(
                project_id=project.id,
                chapter_number=number,
                chapter_goal="test",
                status="approved",
                current_word_count=999999,
            )
            session.add(row)
            await session.flush()
            session.add(
                ChapterDraftVersionModel(
                    project_id=project.id,
                    chapter_id=row.id,
                    version_no=1,
                    content_md=draft.content_md,
                    word_count=999999,
                    promotion_state="promoted",
                    is_current=True,
                )
            )
        # A large current editing candidate cannot displace the adopted incumbent.
        draft.is_current = False
        await session.flush()
        session.add(
            ChapterDraftVersionModel(
                project_id=project.id,
                chapter_id=chapter.id,
                version_no=2,
                content_md="未采用" * 3000,
                word_count=9000,
                promotion_state="candidate",
                is_current=True,
            )
        )
        row = ChapterModel(
            project_id=project.id,
            chapter_number=adopted + 1,
            chapter_goal="candidate",
            status="approved",
            current_word_count=999999,
        )
        session.add(row)
        await session.flush()
        session.add(
            ChapterDraftVersionModel(
                project_id=project.id,
                chapter_id=row.id,
                version_no=1,
                content_md="未采用" * 3000,
                word_count=9000,
                promotion_state="candidate",
                is_current=True,
            )
        )
    else:
        await session.delete(draft)
        await session.delete(chapter)
    await session.commit()
    settings = load_settings().model_copy(deep=True)
    settings.output.base_dir = tmp_path
    export = tmp_path / project.slug / "exports" / "project.json"
    if export_state == "stale":
        export.parent.mkdir(parents=True)
        export.write_text(
            json.dumps(
                {
                    "title": "旧导出",
                    "genre": "旧题材",
                    "status": "revising",
                    "target_chapters": 500,
                    "completed_chapters": 85,
                }
            )
        )
    before = export.read_bytes() if export.exists() else None
    reads = []

    def read_only(_conn, _cursor, statement, _params, _context, _many):
        assert not re.match(r"\s*(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER)\b", statement, re.I)
        reads.append(statement)

    event.listen(session.bind.sync_connection, "before_cursor_execute", read_only)
    try:
        value = await payload(configured, settings)
    finally:
        event.remove(session.bind.sync_connection, "before_cursor_execute", read_only)
    assert reads and not session.new and not session.dirty and not session.deleted
    assert value["project"]["source"] == "db"
    assert (
        value["project"]["genre"] == project.genre and value["project"]["status"] == project.status
    )
    assert value["project"]["title"] == project.title
    assert value["project"]["target_chapters"] == target
    assert value["adoption"]["adopted_chapters"] == adopted
    assert value["adoption"]["word_count_total"] == expected_words
    assert value["adoption"]["adopted_chapter_numbers"] == list(range(1, adopted + 1))
    assert all(
        ch["adoption_state"] == "promoted" and ch["word_count"] < 5000
        for ch in value["chapters"]
        if ch["chapter_number"] <= adopted
    )
    assert all(
        ch["adoption_state"] == "unadopted" and ch["word_count"] == 0
        for ch in value["chapters"]
        if ch["chapter_number"] > adopted
    )
    if scenario != "short":
        loop = next(node for node in value["nodes"] if node["id"] == "chapter_loop")
        assert loop["status"] == "pending"  # completed workflow / approved rows cannot close target
        assert loop["metrics"]["adopted_chapters"] == adopted
    assert PipelineFlowOverview.model_validate(value).model_dump(mode="json") == value
    assert (export.read_bytes() if export.exists() else None) == before


async def test_flow_keeps_verified_debt_distinct_from_promotion(configured):
    session, project, chapter, draft = configured
    project.target_chapters = 1
    await make_accepted_debt((session, project, chapter, draft))
    await session.commit()
    value = await payload(configured)
    assert value["adoption"]["adopted_chapters"] == 1
    assert value["adoption"]["promoted_chapters"] == 0
    assert value["adoption"]["accepted_with_debt_chapters"] == 1
    assert value["chapters"][0]["adoption_state"] == "accepted_with_debt"
    assert draft.promotion_state == "under_review"


async def test_direct_builder_without_adoption_evidence_never_trusts_approved(configured):
    value = await flow.build_pipeline_flow_overview(configured[0], configured[1].slug)
    assert value.adoption is None
    assert value.chapters[0].adoption_state == "unknown"
    assert next(node for node in value.nodes if node.id == "chapter_loop").status == "pending"


async def test_unassigned_chapter_is_visible_and_complete_target_requires_adoption(
    configured, monkeypatch
):
    session, project, _, _ = configured
    project.target_chapters = 1
    await session.commit()

    async def no_volumes(*args):
        return SimpleNamespace(total_chapters=1, total_scenes=0, target_chapters=1, volumes=[])

    monkeypatch.setattr(flow, "build_project_structure", no_volumes)
    value = await payload(configured)
    assert len(value["chapters"]) == 1
    assert value["chapters"][0]["adoption_state"] == "promoted"
    assert (
        next(node for node in value["nodes"] if node["id"] == "chapter_loop")["status"]
        == "completed"
    )


async def test_foreign_owner_and_stale_debt_cannot_become_flow_adoption(configured):
    from uuid import uuid4

    session, project, chapter, draft = configured
    await make_accepted_debt((session, project, chapter, draft))
    draft.content_md += "采用评审之后修改了正文。"
    await session.commit()
    assert (await payload(configured))["adoption"]["adopted_chapters"] == 0
    chapter.metadata_json = {}
    draft.promotion_state = "promoted"
    draft.project_id = uuid4()
    await session.commit()
    value = await payload(configured)
    assert value["adoption"]["adopted_chapters"] == 0
    assert value["chapters"][0]["adoption_state"] == "unadopted"
    assert value["chapters"][0]["word_count"] == 0


async def test_schema_exposes_source_and_rejects_invalid_adoption():
    schema = PipelineFlowOverview.model_json_schema()
    assert "adoption" in schema["properties"]
    assert "adoption_state" in schema["$defs"]["PipelineFlowChapterRuntime"]["properties"]
    for update in ({"source": "export"}, {"adopted_chapters": -1}):
        with pytest.raises(ValidationError):
            PipelineFlowAdoptionStats.model_validate(
                {
                    "source": "db",
                    "adopted_chapters": 0,
                    "promoted_chapters": 0,
                    "accepted_with_debt_chapters": 0,
                    "word_count_total": 0,
                    "adopted_chapter_numbers": [],
                    **update,
                }
            )
    with pytest.raises(ValidationError):
        PipelineFlowChapterRuntime(chapter_number=1, status="approved", adoption_state="approved")


def render_page(value, *, design="stale", fail_refresh=False):
    node = shutil.which("node")
    assert node is not None, "Node is required to execute the actual flow page script"
    script = re.search(r"<script>(.*?)</script>", HTML.read_text(), re.S).group(1)
    harness = r"""
const vm = require('node:vm');
let input = '';
process.stdin.on('data', x => input += x);
process.stdin.on('end', async () => {
  const spec = JSON.parse(input), elements = {}, intervals = [];
  let unavailable = false;
  const context = vm.createContext({
    location: {pathname: '/flow/' + spec.value.project.slug},
    document: {getElementById: id => elements[id] ??= {
      _text: '', get textContent() {return this._text;},
      set textContent(x) {this._text = String(x);}, innerHTML: '', style: {}}},
    console: {error: () => {}},
    setInterval: fn => intervals.push(fn),
    fetch: async url => {
      const endpoint = url.split('/').pop();
      if (endpoint === 'pipeline-flow') return {
        ok: !unavailable, statusText: 'Unavailable', json: async () => spec.value};
      if (endpoint === 'execution-progress') return {ok: true, json: async () => ({})};
      if (spec.design === 'missing') return {ok: false, statusText: 'Missing'};
      return {ok: true, json: async () => ({title: '旧规划标题',
        world_backbone: {world_frame: '错误题材 / 旧分类', core_promise: '旧规划承诺'},
        project: {target_chapters: 500, current_chapter_number: 85, status: 'revising'}})};
    },
  });
  vm.runInContext(spec.script, context);
  await new Promise(resolve => setImmediate(resolve));
  if (spec.fail_refresh) {unavailable = true; await vm.runInContext('loadData()', context);}
  process.stdout.write(JSON.stringify(elements));
});
"""
    result = subprocess.run(
        [node, "-e", harness],
        input=json.dumps(
            {"script": script, "value": value, "design": design, "fail_refresh": fail_refresh}
        ),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("design", ["stale", "missing"])
@pytest.mark.parametrize("count", [0, 3])
async def test_actual_flow_script_renders_db_truth_zeros_and_adoption_not_word_threshold(
    configured, design, count
):
    value = await payload(configured)
    value["project"].update(
        title="当前数据库书名",
        genre="日常温馨",
        status="archived",
        target_chapters=50 if count else 0,
        current_chapter_number=85,
    )
    value["adoption"].update(
        adopted_chapters=count,
        promoted_chapters=count,
        adopted_chapter_numbers=list(range(1, count + 1)),
        word_count_total=300 if count else 0,
    )
    value["chapters"] = (
        [
            {
                "chapter_number": 1,
                "title": "短正文",
                "status": "drafted",
                "adoption_state": "promoted",
                "word_count": 100,
            },
            {
                "chapter_number": 2,
                "title": "长候选",
                "status": "approved",
                "adoption_state": "unadopted",
                "word_count": 9000,
            },
        ]
        if count
        else []
    )
    rendered = render_page(value, design=design)
    assert rendered["title"]["_text"] == "当前数据库书名"
    assert rendered["infoGenre"]["_text"] == "日常温馨 / -"
    assert rendered["infoStatus"]["_text"] == "archived"
    assert rendered["infoTarget"]["_text"] == str(50 if count else 0)
    assert rendered["infoCurrent"]["_text"] == str(count)
    assert rendered["statChapters"]["_text"] == str(count)
    assert rendered["statWordCount"]["_text"] == str(300 if count else 0)
    if count:
        assert "chapter-cell completed" in rendered["chapterGrid"]["innerHTML"]
        assert "chapter-cell planned" in rendered["chapterGrid"]["innerHTML"]
    else:
        assert "无章节数据" in rendered["chapterGrid"]["innerHTML"]
    assert "规划" in rendered["designScope"]["_text"]


async def test_flow_api_failure_clears_previous_counts_instead_of_using_design_defaults(configured):
    rendered = render_page(await payload(configured), fail_refresh=True)
    for field in ("infoTarget", "infoCurrent", "statChapters", "statWordCount"):
        assert rendered[field]["_text"] == "-"
    assert "不可用" in rendered["title"]["_text"]
