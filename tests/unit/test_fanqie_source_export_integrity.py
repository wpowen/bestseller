# ruff: noqa: RUF001
"""Real SQLite adopted sources/knowledge; no provider or main-tree writes."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateTable
from test_fanqie_segment_resume import configured as configured
from test_fanqie_segment_resume import db as db
from test_fanqie_segment_resume import seed as seed_feedback_snapshot
from support.adopted_bible_fixture import seed_empty_bible

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterDraftVersionModel,
    ChapterStateSnapshotModel,
    DraftPromotionDecisionModel,
    RewriteTaskModel,
)
from bestseller.services.budget import bind_execution_scope
from bestseller.services.fanqie_short_quality import review_whole_fanqie_short_story

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
FIXTURE_TEXTS = json.loads(
    '{"fixture_only":true,"literary_or_model_grade_asserted":false,"sourc'
    'e_test":"tests/unit/test_fanqie_short.py::test_fanqie_short_finalize'
    'r_repairs_rejected_draft_to_upload_ready","producer_source_sha256":"'
    'd147b1f76d3da5628ef65d78f04e8a7254ea975495bff7ceaed54bacda59ff20","p'
    'arameters":{"title":"器语者","genre":"东方幻想","protagonist_name":"我","tar'
    'get_word_count":700},"weak_text":"# 器语者\\n\\n## 正文\\n\\n清晨的风落在寄渊阁檐下。多年以前'
    "，器灵制度由此建立，世界观由此展开。霜钟、原初之器、封印、共感、器魂、铭纹、十二枚古器都各有来历。顾器站在那里，听别人说了很久关于历史和"
    "制度的事。\\n\\n这是一段会被重复的诊断性水文，用来验证终稿闭环会删除机械重复段落。\\n\\n这是一段会被重复的诊断性水文，用来验证终稿闭"
    '环会删除机械重复段落。\\n\\n下章，他将去地下三层追问真正的真相。未完待续。","strong_text":"我被按到审讯桌前时，认罪书'
    "已经摊开。对方逼我签下“偷用禁术、毁掉证物”的罪名；不签，立刻开除，公告贴满师门和城门。\\n\\n我膝盖撞得发痛，却先听见桌上朱笔在发抖。"
    "那支笔说：墨里有别人的血。\\n\\n这是我藏了二十年的能力：能听见器物残留的记忆。能力一旦暴露，会被当成怪物关进水牢；可此刻不用它，我连明"
    "天都没有。\\n\\n“签。”对方把我的手往纸上压，“证据齐全，你逃不掉。”\\n\\n朱笔突然滚落，笔尖在地上划出暗红的线。我反手扣住它，任笔"
    "尖扎破掌心。血珠渗出的瞬间，残留画面撞进脑海：昨夜有人用这支笔改过记录，把“证物被封”改成“我施术”。\\n\\n头痛像铁钉钉进太阳穴，代价"
    "立刻反噬，我喉咙发紧，几乎说不出话。可我还是笑了。\\n\\n“我笑你们逼我认罪，”我举起沾血的朱笔，“却忘了证据也会说话。”\\n\\n复盘的"
    "时候，我才看见自己过去错过了多少细节。每一次对方声称迫不得已，事后都有一处对不上的说辞；每一次对方指责别人不懂事，又会在旁人面前把受害者"
    "的忍让说成自己的功劳。那些矛盾不是巧合，是他早就把别人的退让当成理所当然。\\n\\n最难的一晚不是对峙那天，而是真相落定以后。四周终于安静"
    "，消息也不再涌来，我却反复确认那些证据还在。人被逼久了，会下意识怀疑自己是不是太狠；直到看见身边的人终于睡得安稳，我才知道那不是狠，是自"
    "保。\\n\\n后来有人递来道歉信，话写得很满，落款也很诚恳。我没有当场原谅，只把信收进文件袋。清白本来就不该靠迟来的歉意兑换，伤口也不是一"
    "句误会就能合上。能被公开承认，已经是第一步；能不能被原谅，是另一回事。\\n\\n真相大白后，伪造证据的人当众认罪，所有被篡改的记录一一恢复"
    "。我没有再追问下一场阴谋，只看着那份认罪书被投入火盆。旧案结束，受害者拿回名字，逼我签字的人付出代价。我转身离开审讯室，这一次不是被押走"
    '，而是自己走出去，重新开始。","strong_title":"被逼签认罪书后，我让真凶当场认罪","strong_text_sha25'
    '6":"57f6c53c9530f26794a182d946dd05148f753cae2be51cd9cda9ae2206609438'
    '"}'
)


async def seed(configured, monkeypatch, **kwargs):
    await seed_feedback_snapshot(configured, monkeypatch, **kwargs)
    await seed_empty_bible(configured, monkeypatch)


@pytest.fixture
def integrated_modules():
    from bestseller.services import (
        fanqie_short_finalizer,
        fanqie_short_pipeline,
        fanqie_short_source_export,
    )

    return fanqie_short_source_export, fanqie_short_pipeline, fanqie_short_finalizer


def weak_text():
    return FIXTURE_TEXTS["weak_text"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("integrated_modules source tests forbid PG/network/provider")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


@pytest.fixture(autouse=True)
def isolate_derived_bible_projection(monkeypatch):
    from bestseller.services import story_bible

    async def retained(session, settings, project_id, outcome):
        # Adoption/feedback/snapshot/KG closure stay real. SQLite does not
        # implement the unrelated PG advisory lock/embedding read-view refresh.
        assert session.bind.dialect.name == "sqlite"
        return outcome

    monkeypatch.setattr(story_bible, "_ensure_adopted_bible_read_views", retained)


@pytest.fixture
async def source_case(configured, monkeypatch, tmp_path, request):
    session, settings, project, chapter, draft = configured
    assert session.bind.dialect.name == "sqlite"
    connection = await session.connection()
    for model in (RewriteTaskModel, DraftPromotionDecisionModel):
        ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    strong = SimpleNamespace(
        full_text=FIXTURE_TEXTS["strong_text"], title=FIXTURE_TEXTS["strong_title"]
    )
    assert (
        hashlib.sha256(strong.full_text.encode()).hexdigest() == FIXTURE_TEXTS["strong_text_sha256"]
    )
    assert review_whole_fanqie_short_story(
        strong.full_text, title=strong.title, protagonist_name="我"
    ).passed
    project.title, project.genre = strong.title, "东方幻想"
    project.metadata_json = {
        **project.metadata_json,
        "execution_scope_id": "fixture:" + uuid4().hex,
        "pov": "first_person",
    }
    project.project_type = "fanqie_short"
    project.target_word_count = 700
    chapter.status, chapter.production_state = "complete", "ok"
    draft.content_md = (
        weak_text() if getattr(request, "param", None) == "weak" else strong.full_text
    )
    await session.commit()
    await seed(configured, monkeypatch)

    # No paid boundary remains available after the real knowledge fixture seeding.
    async def denied(*args, **kwargs):
        raise AssertionError("no model/writer/reviewer in source publication tests")

    from bestseller.services import continuity, feedback, llm

    monkeypatch.setattr(feedback, "complete_text", denied)
    monkeypatch.setattr(continuity, "complete_text", denied)
    monkeypatch.setattr(llm, "complete_text", denied)
    output = tmp_path / "book"
    assert output.is_relative_to(tmp_path)
    return SimpleNamespace(
        session=session,
        settings=settings,
        project=project,
        chapter=chapter,
        draft=draft,
        output=output,
        configured=configured,
        weak=getattr(request, "param", None) == "weak",
        semantic_control_flow=getattr(request, "param", None) == "semantic_control_flow",
    )


async def old_evidence(case):
    snapshots = list(await case.session.scalars(select(ChapterStateSnapshotModel)))
    return deepcopy(
        (
            case.draft.content_md,
            case.draft.promotion_state,
            case.draft.promotion_metadata,
            case.chapter.metadata_json,
            [
                {col.key: getattr(row, col.key) for col in row.__table__.columns}
                for row in snapshots
            ],
        )
    )


async def publish(helper, case, expected=None):
    expected = expected or await helper.read_adopted_assembly(case.session, case.project.id)
    return await helper.publish_adopted_assembly(
        case.session,
        project_id=case.project.id,
        output_dir=case.output,
        expected=expected,
        unlock_line_ratio=0.3,
        protagonist_name="我",
    )


async def test_current_adopted_assembly_is_only_official_body_and_can_complete(
    integrated_modules, source_case
):
    helper, _, _ = integrated_modules
    case = source_case
    before = await old_evidence(case)
    expected = await helper.read_adopted_assembly(case.session, case.project.id)
    result = await publish(helper, case, expected)
    assert result["status"] == "pass" and result["whole_book_complete"]
    assert Path(result["export_paths"]["markdown_path"]).read_text() == (
        "# " + case.project.title + "\n\n" + expected.full_text
    )
    await case.session.refresh(case.project)
    assert case.project.status == "completed"
    assert await old_evidence(case) == before


async def test_new_unadopted_candidate_does_not_displace_promoted_export(
    integrated_modules, source_case
):
    helper, _, _ = integrated_modules
    case = source_case
    case.draft.is_current = False
    candidate = ChapterDraftVersionModel(
        project_id=case.project.id,
        chapter_id=case.chapter.id,
        version_no=2,
        content_md="不可作为正式导出的另一份候选。",
        is_current=True,
        promotion_state="candidate",
    )
    case.session.add(candidate)
    await case.session.commit()
    before = await old_evidence(case)
    result = await publish(helper, case)
    assert result["full_text"] == case.draft.content_md
    assert candidate.content_md not in Path(result["export_paths"]["markdown_path"]).read_text()
    assert candidate.promotion_state == "candidate" and not case.draft.is_current
    assert await old_evidence(case) == before


@pytest.mark.parametrize("source_case", ["weak"], indirect=True)
async def test_rule_passing_finalizer_candidate_cannot_publish_or_green_old_source(
    integrated_modules, source_case
):
    helper, _, finalizer = integrated_modules
    case = source_case
    before = await old_evidence(case)
    candidate = finalizer.finalize_fanqie_short_for_upload(
        case.output,
        title="器语者",
        genre="东方幻想",
        full_text=weak_text(),
        protagonist_name="顾器",
        target_word_count=700,
    )
    assert candidate.readiness["candidate_ready_by_rules"]
    assert not candidate.ready_for_upload and candidate.full_text != weak_text()
    assert not list(case.output.rglob("exports/fanqie-short.md"))
    assert (
        Path(candidate.export_paths["candidate_markdown_path"]).read_text() == candidate.full_text
    )
    result = await publish(helper, case)
    assert result["status"] == "needs_revision" and not result["whole_book_complete"]
    assert not result["review"].passed and not result["export_paths"]
    assert result["candidate_paths"] and result["rejected_paths"]
    assert result["full_text"] != candidate.full_text
    assert not list(case.output.rglob("exports/fanqie-short.md"))
    await case.session.refresh(case.project)
    assert case.project.status == "revising"
    assert await old_evidence(case) == before


@pytest.mark.parametrize(
    "fault", ["body", "adoption", "snapshot", "feedback", "project", "scope", "pause", "reader"]
)
async def test_source_or_control_drift_cannot_publish(integrated_modules, source_case, fault):
    helper, _, _ = integrated_modules
    case = source_case
    expected = await helper.read_adopted_assembly(case.session, case.project.id)
    if fault == "body":
        case.draft.content_md += "来源已改变。"
    elif fault == "adoption":
        case.draft.promotion_state = "candidate"
    elif fault == "snapshot":
        snapshot = await case.session.scalar(select(ChapterStateSnapshotModel))
        snapshot.extraction_status = "rejected_source"
    elif fault == "feedback":
        metadata = deepcopy(case.chapter.metadata_json)
        metadata["knowledge_feedback_receipts"] = []
        case.chapter.metadata_json = metadata
    elif fault == "project":
        case.project.title += "新标题"
    elif fault == "scope":
        case.project.metadata_json = {
            **case.project.metadata_json,
            "execution_scope_id": "different",
        }
    elif fault == "reader":
        case.project.metadata_json = {**case.project.metadata_json, "unlock_line_ratio": 0.4}
    else:
        case.session.add(
            BookProductionControlModel(
                project_id=case.project.id,
                desired_state="pause",
                command_serial=1,
            )
        )
    await case.session.commit()
    with pytest.raises(ValueError):
        await publish(helper, case, expected)
    assert not list(case.output.rglob("exports/fanqie-short.md"))


async def test_candidate_archive_is_exact_immutable_and_not_authority(
    integrated_modules, source_case
):
    helper, _, _ = integrated_modules
    case = source_case
    expected = await helper.read_adopted_assembly(case.session, case.project.id)
    raw = "  模型候选原返回\n\n\n尾部空格  \n"
    kwargs = {
        "producer": "deslop_round",
        "body": raw,
        "source_manifest": expected.manifest,
        "evidence": {"llm_run_id": None, "physical_proof_asserted": False},
    }
    first = helper.preserve_whole_candidate(case.output, **kwargs)
    assert first == helper.preserve_whole_candidate(case.output, **kwargs)
    assert Path(first["candidate_markdown_path"]).read_text() == raw
    result = await publish(helper, case, expected)
    assert raw not in result["full_text"]
    Path(first["candidate_markdown_path"]).write_text("corrupt")
    with pytest.raises(ValueError, match="archive_conflict"):
        helper.preserve_whole_candidate(case.output, **kwargs)


async def test_pending_rewrite_is_preserved_and_does_not_fake_project_completion(
    integrated_modules, source_case
):
    helper, _, _ = integrated_modules
    case = source_case
    task = RewriteTaskModel(
        project_id=case.project.id,
        trigger_type="fanqie_ranking_gate",
        trigger_source_id=case.chapter.id,
        rewrite_strategy="fix_opening",
        status="pending",
        instructions="保留来源的现有改稿任务。",
        metadata_json={"old_evidence": None},
    )
    case.session.add(task)
    await case.session.commit()
    result = await publish(helper, case)
    assert result["review"].passed and not result["whole_book_complete"]
    assert task.status == "pending" and task.metadata_json == {"old_evidence": None}
    await case.session.refresh(case.project)
    assert case.project.status != "completed"


async def test_writing_checkpoint_does_not_override_operator_pause(integrated_modules, source_case):
    helper, _, _ = integrated_modules
    case = source_case
    await helper.checkpoint_writing(case.session, case.project.id)
    case.session.add(
        BookProductionControlModel(
            project_id=case.project.id,
            desired_state="pause",
            command_serial=1,
        )
    )
    await case.session.commit()
    with pytest.raises(ValueError, match="halted"):
        await helper.checkpoint_writing(case.session, case.project.id)


async def test_context_scope_cannot_be_rebound_for_export(integrated_modules, source_case):
    helper, _, _ = integrated_modules
    with bind_execution_scope("foreign-scope"):
        with pytest.raises(ValueError, match="scope_changed"):
            await helper.read_adopted_assembly(source_case.session, source_case.project.id)


@pytest.mark.parametrize("source_case", ["strong", "weak", "semantic_control_flow"], indirect=True)
async def test_public_pipeline_uses_real_adopted_source_and_never_exports_style_candidate(
    integrated_modules,
    source_case,
    monkeypatch,
):
    helper, pipeline, _ = integrated_modules
    case = source_case
    from bestseller.domain.fanqie_short import FanqieShortBeat, FanqieShortBeatSheet
    from bestseller.domain.project import ProjectCreate
    from bestseller.services import deslop_revise
    if case.semantic_control_flow:
        from bestseller.services import fanqie_short_semantic_readiness as bridge
        # Explicit synthetic CONSUMER SEAM, not fake paid evidence or a claim
        # that v4 can disprove every blocker. Actual proof is adapter-tested.
        case.project.title = "器语者"
        await case.session.commit()

        async def synthetic_context(*_args, **_kwargs):
            return SimpleNamespace(synthetic_control_flow_only=True)

        def synthetic_view(context, *, full_text, title, unlock_line_ratio, protagonist_name):
            assert context.synthetic_control_flow_only
            raw = review_whole_fanqie_short_story(
                full_text, title=title, unlock_line_ratio=unlock_line_ratio,
                protagonist_name=protagonist_name,
            ).to_dict()
            assert raw["passed"] is False
            return {"opening_gate_passed": True, "ranking_gate_passed": True,
                    "short_v2_gate_passed": True,
                    "semantic_derived_review": {"original_report": raw, "remaining_blockers": [],
                        "adjudicated_nonblocking": [], "model_grade_asserted": False,
                        "adjudication_trace": {"status": "synthetic_control_flow_only", "decisions": []}}}

        monkeypatch.setattr(bridge, "load_upload_semantic_context", synthetic_context)
        monkeypatch.setattr(bridge, "semantic_readiness_view", synthetic_view)

    async def no_op(*args, **kwargs):
        return None

    # This export test has always used an inert planning scaffold. Keep the
    # new outer-plan boundary inert here too; test_fanqie_materialized_plan_resume exercises
    # it against actual SQLite selection and actual paid-receipt verification.
    from bestseller.services import fanqie_short_plan_resume

    monkeypatch.setattr(
        fanqie_short_plan_resume,
        "require_or_read_existing_complete_fanqie_plan",
        no_op,
    )

    async def planned(*args, **kwargs):
        return SimpleNamespace(
            workflow_run_id=uuid4(),
            chapters_created=0,
            scenes_created=0,
            plot_arc_count=0,
            clue_count=0,
        )

    async def latest(*args, **kwargs):
        return None

    async def beats(*args, **kwargs):
        return FanqieShortBeatSheet(
            beats=[FanqieShortBeat(segment_number=1, beat_role="hook", purpose="fixture")],
            unlock_milestone_segment=1,
        )

    baseline = await helper.read_adopted_assembly(case.session, case.project.id)
    candidate_raw = "  这是未采用的整篇候选；即使规则通过也不能换掉正式正文。  \n"
    calls = []

    async def inert_revise(session, settings, *, observe_candidate, workflow_run_id, **kwargs):
        calls.append(kwargs["content"])
        assert settings.artifact_store.retain_prompt_payloads and workflow_run_id is not None
        await observe_candidate(
            {
                "content": candidate_raw,
                "llm_run_id": None,
                "round": 1,
                "parent_content": kwargs["content"],
            }
        )
        return candidate_raw

    # Trigger the already-existing advisory path; source/knowledge/gates/export stay real.
    monkeypatch.setattr("bestseller.services.ai_flavor_gate.needs_deslop_revise", lambda _: True)
    monkeypatch.setattr(deslop_revise, "revise_prose_deslop", inert_revise)
    monkeypatch.setattr(pipeline, "_ensure_project_invariants", no_op)
    monkeypatch.setattr(pipeline, "generate_foundation_plan", planned)
    monkeypatch.setattr(pipeline, "get_latest_planning_artifact", latest)
    monkeypatch.setattr(pipeline, "derive_ideology_kernel", no_op)
    monkeypatch.setattr(pipeline, "generate_fanqie_beat_sheet", beats)
    monkeypatch.setattr(pipeline, "persist_fanqie_chapter_outline", no_op)
    for name in (
        "materialize_latest_story_bible",
        "materialize_latest_chapter_outline_batch",
        "materialize_latest_narrative_graph",
    ):
        monkeypatch.setattr(pipeline, name, planned)

    async def litstyle(*args, content_md, **kwargs):
        assert content_md == baseline.full_text
        return SimpleNamespace(final_score=0, level="not_applicable")

    monkeypatch.setattr(pipeline, "judge_chapter_litstyle_stable", litstyle)
    case.settings.output.base_dir = str(case.output.parent)
    case.project.slug = case.output.name
    await case.session.commit()
    before = await old_evidence(case)
    payload = ProjectCreate(
        slug=case.project.slug,
        title=case.project.title,
        genre=case.project.genre,
        target_chapters=4,
        target_word_count=8000,
        project_type="fanqie_short",
        metadata={"content_mode": "fanqie_short_story", "segment_count": 4},
    )
    events = []
    result = await pipeline.run_fanqie_short_pipeline(
        case.session,
        case.settings,
        project_payload=payload,
        premise="fixture",
        export_markdown=True,
        progress=lambda event, payload: events.append((event, payload)),
    )
    assert calls == [baseline.full_text]
    candidates = [
        path.read_text() for path in case.output.glob("candidates/whole-story/*/candidate.md")
    ]
    assert candidate_raw in candidates
    if not case.weak:
        assert result.final_verdict == "pass", case.project.metadata_json[
            "fanqie_whole_export_receipt"
        ]
        assert (
            Path(result.output_path).read_text()
            == "# " + case.project.title + "\n\n" + baseline.full_text
        )
    else:
        assert result.final_verdict == "needs_revision" and result.output_path is None
        assert not list(case.output.rglob("exports/fanqie-short.md"))
    after = await old_evidence(case)
    assert after[:3] == before[:3] and after[4] == before[4]
    assert all(after[3].get(key) == value for key, value in before[3].items())
    assert case.draft.content_md == (weak_text() if case.weak else baseline.full_text)
    if case.semantic_control_flow:
        receipt = case.project.metadata_json["fanqie_whole_export_receipt"]
        assert receipt["whole_review"]["passed"] is False
        assert receipt["effective_passed"] is True
        assert result.final_verdict == "pass" and not result.requires_human_review
        completion = next(payload for event, payload in events if event == "fanqie_short_pipeline_completed")
        assert completion["whole_review_passed"] is True
        assert completion["heuristic_whole_review_passed"] is False
        assert completion["ranking_gate_passed"] and completion["short_v2_gate_passed"]
        # The existing formal reader has no settings argument and must retain
        # raw=false/effective=true rather than rejecting the original raw flag.
        verified = await helper.read_fanqie_delivery_evidence(case.session, case.project.id)
        assert verified["schema"] == helper.DELIVERY_SCHEMA
        from bestseller.services.book_delivery_readiness import read_book_delivery_closure
        closure = await read_book_delivery_closure(case.session, case.project)
        assert closure.verdict.is_complete, closure.blockers


@pytest.mark.parametrize("source_case", ["weak"], indirect=True)
async def test_revision_tasks_pass_existing_parent_guard_and_are_idempotent(
    integrated_modules, source_case
):
    from bestseller.services.reviews import _validate_rewrite_task_parent

    helper, _, _ = integrated_modules
    case = source_case
    before = await old_evidence(case)
    expected = await helper.read_adopted_assembly(case.session, case.project.id)
    first = await publish(helper, case, expected)
    assert first["normal_rewrite_task_ids"]
    tasks = list(await case.session.scalars(select(RewriteTaskModel)))
    assert {str(row.id) for row in tasks} == set(first["normal_rewrite_task_ids"])
    for task in tasks:
        await _validate_rewrite_task_parent(case.session, task, case.draft)
        assert task.status == "pending" and task.metadata_json["grade_asserted"] is False
    second = await publish(helper, case, expected)
    assert second["normal_rewrite_task_ids"] == first["normal_rewrite_task_ids"]
    assert second["revision_task_counts"] == {"ranking": 0, "short_v2": 0}
    assert len(list(await case.session.scalars(select(RewriteTaskModel)))) == len(tasks)
    assert await old_evidence(case) == before


async def test_export_io_error_cannot_checkpoint_project_completed(
    integrated_modules, source_case, monkeypatch
):
    helper, _, _ = integrated_modules
    case = source_case
    before = await old_evidence(case)

    def failed(*args, **kwargs):
        raise OSError("injected export failure")

    monkeypatch.setattr(
        "bestseller.services.fanqie_short_export.export_fanqie_short_markdown", failed
    )
    with pytest.raises(OSError, match="export failure"):
        await publish(helper, case)
    await case.session.refresh(case.project)
    assert case.project.status == "writing"
    assert await old_evidence(case) == before


async def test_evaluation_only_does_not_reopen_completed_project(integrated_modules, source_case):
    helper, _, _ = integrated_modules
    case = source_case
    first = await publish(helper, case)
    output_path = Path(first["export_paths"]["markdown_path"])
    old = output_path.read_bytes()
    expected = await helper.read_adopted_assembly(case.session, case.project.id)
    result = await helper.publish_adopted_assembly(
        case.session,
        project_id=case.project.id,
        output_dir=case.output,
        expected=expected,
        unlock_line_ratio=0.3,
        protagonist_name="我",
        export_markdown=False,
    )
    await case.session.refresh(case.project)
    assert case.project.status == "completed" and result["whole_book_complete"]
    assert not result["export_paths"] and output_path.read_bytes() == old


@pytest.mark.parametrize("pending", ["draft", "control"])
async def test_pending_caller_source_or_control_is_not_flushed_or_overwritten(
    integrated_modules, source_case, pending
):
    helper, _, _ = integrated_modules
    case = source_case
    expected = await helper.read_adopted_assembly(case.session, case.project.id)
    original_body = case.draft.content_md
    if pending == "draft":
        case.draft.content_md += "未提交的调用者改稿。"
    else:
        control = BookProductionControlModel(project_id=case.project.id, desired_state="pause")
        case.session.add(control)
    with pytest.raises(ValueError, match="pending_source_state"):
        await publish(helper, case, expected)
    with case.session.no_autoflush:
        assert (
            await case.session.scalar(
                select(ChapterDraftVersionModel.content_md).where(
                    ChapterDraftVersionModel.id == case.draft.id,
                )
            )
            == original_body
        )
        if pending == "draft":
            assert case.draft in case.session.dirty and case.draft.content_md != original_body
        else:
            assert control in case.session.new and control.desired_state == "pause"
    assert not list(case.output.rglob("exports/fanqie-short.md"))


def seed_prior_outputs(output):
    """Opaque historical bytes are evidence, never a source/grade fixture."""
    prior = {
        "exports/fanqie-short.md": b"# A different old book\r\n\r\nold prose\x00\xff",
        "exports/signing-readiness.json": b'{"ready_for_upload":false,"unknown":null}\r\n',
        "exports/adopted-source.json": b'{"old_source":null,"raw":"kept","status":"old"}',
        "exports/legacy-raw.bin": b"\x00\xff\xfe\r\n",
        "rejected-drafts/fanqie-short.rejected.md": b"old rejected prose\r\n",
        "rejected-drafts/rejection-report.json": b'{"unknown_fact":null,"raw":[false,0]}',
        "finalized/exports/fanqie-short.md": b"old finalized prose\r\n",
        "finalized/exports/adopted-source.json": b'{"legacy":"unknown"}\r\n',
        "finalized/rejected-drafts/rejection-report.json": b'{"raw":"old rejection"}',
        "finalized/finalization-report.json": b'{"status":"old","unknown":null}\r\n',
        "story-bible/canon-guardrails.json": (
            b'{"forbidden_terms_candidates":[],"unknown":null,"raw":"old"}\r\n'
        ),
    }
    for relative, data in prior.items():
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return prior


@pytest.mark.parametrize(
    ("source_case", "route"),
    [("strong", "passed"), ("weak", "rejected"), ("strong", "finalizer")],
    indirect=["source_case"],
)
async def test_every_output_route_archives_prior_bytes_and_leaves_other_book_untouched(
    integrated_modules, source_case, route
):
    helper, _, finalizer = integrated_modules
    case = source_case
    before = await old_evidence(case)
    prior = seed_prior_outputs(case.output)
    neighbor = case.output.parent / "another-book"
    neighbor_bytes = seed_prior_outputs(neighbor)
    if route == "finalizer":
        candidate = finalizer.finalize_fanqie_short_for_upload(
            case.output,
            title="A different candidate",
            genre="东方幻想",
            full_text=weak_text(),
            protagonist_name="顾器",
            target_word_count=700,
        )
        archive = json.loads(Path(candidate.report_path).read_text())["evidence"][
            "previous_outputs_archive"
        ]
    else:
        result = await publish(helper, case)
        archive = result["previous_outputs_archive"]
        assert result["status"] == ("pass" if route == "passed" else "needs_revision")
    directory = Path(archive["directory"])
    manifest = json.loads(Path(archive["manifest_path"]).read_bytes())
    assert set(manifest["files"]) == set(prior)
    for relative, data in prior.items():
        assert (directory / "files" / relative).read_bytes() == data
        assert manifest["files"][relative] == {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
        if relative.startswith("finalized/") or route == "finalizer":
            assert (case.output / relative).read_bytes() == data
    assert not manifest["source_authority_granted"] and not manifest["grade_granted"]
    assert len(list((case.output / "export-history").iterdir())) == 1
    assert {relative: (neighbor / relative).read_bytes() for relative in neighbor_bytes} == (
        neighbor_bytes
    )
    assert not (neighbor / "export-history").exists()
    assert await old_evidence(case) == before


async def test_prior_output_archive_reuses_identical_bytes_without_rewriting(
    integrated_modules, source_case
):
    helper, _, _ = integrated_modules
    case = source_case
    prior = seed_prior_outputs(case.output)
    first = helper.archive_existing_whole_outputs(case.output)
    directory = Path(first["directory"])
    original = {
        path.relative_to(directory): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in directory.rglob("*")
        if path.is_file()
    }
    for _ in range(2):
        assert helper.archive_existing_whole_outputs(case.output) == first
        assert {
            path.relative_to(directory): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in directory.rglob("*")
            if path.is_file()
        } == original
    assert len(list((case.output / "export-history").iterdir())) == 1
    assert {relative: (case.output / relative).read_bytes() for relative in prior} == prior


@pytest.mark.parametrize("conflict", ["bytes", "manifest"])
async def test_archival_conflict_fails_before_any_official_output_or_status_write(
    integrated_modules, source_case, conflict
):
    helper, _, _ = integrated_modules
    case = source_case
    before = await old_evidence(case)
    prior = seed_prior_outputs(case.output)
    archive = helper.archive_existing_whole_outputs(case.output)
    corrupted = (
        Path(archive["manifest_path"])
        if conflict == "manifest"
        else Path(archive["directory"]) / "files" / "exports/adopted-source.json"
    )
    corrupted.write_bytes(b"conflicting retained evidence")
    with pytest.raises(ValueError, match="export_archive_conflict"):
        await publish(helper, case)
    assert corrupted.read_bytes() == b"conflicting retained evidence"
    assert {relative: (case.output / relative).read_bytes() for relative in prior} == prior
    await case.session.refresh(case.project)
    assert case.project.status == "writing"
    assert not (case.output / "candidates").exists()
    assert await old_evidence(case) == before


async def test_archive_sync_failure_cannot_overwrite_prior_outputs(
    integrated_modules, source_case, monkeypatch
):
    helper, _, _ = integrated_modules
    case = source_case
    before = await old_evidence(case)
    prior = seed_prior_outputs(case.output)

    def failed_sync(*args, **kwargs):
        raise OSError("injected archive sync failure")

    monkeypatch.setattr("os.fsync", failed_sync)
    with pytest.raises(OSError, match="archive sync failure"):
        await publish(helper, case)
    assert {relative: (case.output / relative).read_bytes() for relative in prior} == prior
    await case.session.refresh(case.project)
    assert case.project.status == "writing"
    assert await old_evidence(case) == before


async def test_platform_delivery_has_exact_file_checksums_and_generic_reader_accepts_it(
    integrated_modules,
    source_case,
):
    from bestseller.services.book_delivery_readiness import read_book_delivery_closure

    helper, _, _ = integrated_modules
    case = source_case
    result = await publish(helper, case)
    await case.session.refresh(case.project)
    verification = case.project.metadata_json["fanqie_whole_export_receipt"][
        "delivery_verification"
    ]
    assert result["whole_book_complete"]
    for key, record in verification["files"].items():
        data = Path(result["export_paths"][key]).read_bytes()
        assert record["sha256"] == hashlib.sha256(data).hexdigest()
        assert record["bytes"] == len(data)
    readback = await read_book_delivery_closure(case.session, case.project)
    assert readback.verdict.is_complete, readback.blockers


@pytest.mark.parametrize("fault", ["missing", "body", "source", "sidecar", "checksum_receipt"])
async def test_evaluation_only_rechecks_platform_files_not_completed_label(
    integrated_modules,
    source_case,
    fault,
):
    helper, _, _ = integrated_modules
    case = source_case
    first = await publish(helper, case)
    await case.session.refresh(case.project)
    paths = first["export_paths"]
    if fault == "missing":
        Path(paths["markdown_path"]).unlink()
    elif fault == "body":
        Path(paths["markdown_path"]).write_text("伪造的另一份正文。", encoding="utf-8")
    elif fault == "source":
        Path(paths["source_receipt_path"]).write_text("{}", encoding="utf-8")
    elif fault == "sidecar":
        Path(paths["readiness_path"]).write_text("{}", encoding="utf-8")
    else:
        meta = deepcopy(case.project.metadata_json)
        meta["fanqie_whole_export_receipt"].pop("delivery_verification")
        case.project.metadata_json = meta
        await case.session.commit()
    before = deepcopy(case.project.metadata_json)
    result = await helper.publish_adopted_assembly(
        case.session,
        project_id=case.project.id,
        output_dir=case.output,
        expected=await helper.read_adopted_assembly(case.session, case.project.id),
        unlock_line_ratio=0.3,
        protagonist_name="我",
        export_markdown=False,
    )
    assert not result["whole_book_complete"] and not result["export_paths"]
    await case.session.refresh(case.project)
    assert case.project.status == "completed"  # evaluation does not mutate the old claim
    assert case.project.metadata_json == before


@pytest.mark.parametrize("fault", ["changed_body", "missing_file", "corrupt_sidecar"])
async def test_normal_export_return_is_insufficient_without_platform_readback(
    integrated_modules,
    source_case,
    monkeypatch,
    fault,
):
    from bestseller.services import fanqie_short_export

    helper, _, _ = integrated_modules
    case = source_case
    original = fanqie_short_export.export_fanqie_short_markdown

    def corrupt(*args, **kwargs):
        paths = original(*args, **kwargs)
        path = Path(paths["markdown_path"])
        if fault == "changed_body":
            path.write_text("返回成功之后被替换的正文。", encoding="utf-8")
        elif fault == "missing_file":
            path.unlink()
        else:
            Path(paths["readiness_path"]).write_text("{}", encoding="utf-8")
        return paths

    monkeypatch.setattr(fanqie_short_export, "export_fanqie_short_markdown", corrupt)
    with pytest.raises((OSError, ValueError)):
        await publish(helper, case)
    await case.session.refresh(case.project)
    assert case.project.status == "writing"
    assert not case.project.metadata_json.get("fanqie_short_ready_for_upload")
    monkeypatch.setattr(fanqie_short_export, "export_fanqie_short_markdown", original)
    assert (await publish(helper, case))["whole_book_complete"]


async def test_platform_retains_verified_numeric_debt_and_never_promotes(
    integrated_modules,
    source_case,
    monkeypatch,
):
    from test_adopted_knowledge_lineage import make_accepted_debt

    helper, _, _ = integrated_modules
    case = source_case
    await make_accepted_debt((case.session, case.project, case.chapter, case.draft))
    case.chapter.status = "revision"
    await case.session.commit()
    await seed(case.configured, monkeypatch)
    result = await publish(helper, case)
    assert result["whole_book_complete"]
    assert case.draft.promotion_state == "under_review"
    manifest = json.loads(Path(result["export_paths"]["source_receipt_path"]).read_bytes())
    assert manifest["sources"][0]["lineage"]["adoption_state"] == "accepted_with_debt"
    assert not list(await case.session.scalars(select(DraftPromotionDecisionModel)))


async def test_platform_rejects_known_closure_promotion_before_official_write(
    integrated_modules,
    source_case,
):
    helper, _, _ = integrated_modules
    case = source_case
    expected = await helper.read_adopted_assembly(case.session, case.project.id)
    case.session.add(
        DraftPromotionDecisionModel(
            project_id=case.project.id,
            chapter_draft_version_id=case.draft.id,
            from_state="eligible",
            to_state="promoted",
            decision_source="book_closure",
            reason_codes=["settled_at_closure"],
            evidence_json={},
        )
    )
    await case.session.commit()
    with pytest.raises(ValueError, match="legacy_closure_promotion"):
        await publish(helper, case, expected)
    assert not (case.output / "exports" / "fanqie-short.md").exists()
    assert case.draft.promotion_state == "promoted"


async def test_generic_closure_uses_platform_receipt_and_cannot_substitute_project_markdown(
    integrated_modules,
    source_case,
    monkeypatch,
):
    from bestseller.services import exports
    from bestseller.services.book_closure import settle_project_status_on_closure

    helper, _, _ = integrated_modules
    case = source_case
    result = await publish(helper, case)

    async def forbidden(*args, **kwargs):
        pytest.fail("generic project.md must not substitute for platform export")

    monkeypatch.setattr(exports, "export_project_markdown", forbidden)
    kwargs = {"settings": case.settings, "fallback_status": "completed", "now_iso": "fixture-time"}
    verdict = await settle_project_status_on_closure(case.session, case.project, **kwargs)
    assert verdict.is_complete
    Path(result["export_paths"]["markdown_path"]).unlink()
    verdict = await settle_project_status_on_closure(case.session, case.project, **kwargs)
    assert not verdict.is_complete and verdict.body_complete
    assert case.project.status != "completed"
    assert (
        "fanqie_delivery_requires_platform_export"
        in case.project.metadata_json["completion_export_error"]
    )


async def test_successful_platform_retry_preserves_previous_completion_receipt(
    integrated_modules,
    source_case,
):
    helper, _, _ = integrated_modules
    case = source_case
    first = await publish(helper, case)
    await case.session.refresh(case.project)
    previous = deepcopy(case.project.metadata_json["fanqie_whole_export_receipt"])
    Path(first["export_paths"]["readiness_path"]).write_text("{}", encoding="utf-8")
    second = await publish(helper, case)
    assert second["whole_book_complete"]
    await case.session.refresh(case.project)
    assert case.project.metadata_json["fanqie_whole_export_history"][-1] == previous
    assert second["previous_outputs_archive"]
