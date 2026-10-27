"""SQLite custody and inert-provider tests for one lineage-bound patch batch."""

from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, MetaData, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterQualityReportModel,
    LlmRunModel,
    ProjectModel,
    RewriteTaskModel,
    SceneCardModel,
    WorkflowRunModel,
)
from bestseller.services import retained_candidate_recovery as recovery
from bestseller.services.deterministic_post_write_audit import (
    DeterministicAuditFinding,
    DeterministicAuditReport,
)
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.quality_report_claim import graded_text_fingerprint
from bestseller.services.retained_candidate_patch import TOOL_NAME
from bestseller.services.rewrite_patch import text_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
PARENT = "The keeper returns the copper key.\n\nThe gate remains closed."
CANDIDATE = "The keeper opens the copper gate.\n\nShe hands the key to the waiting pilot."
REPLACEMENT = "She turns the key; cold air brushes past the opening door."
MODELS = (
    ProjectModel,
    ChapterModel,
    SceneCardModel,
    ChapterDraftVersionModel,
    RewriteTaskModel,
    WorkflowRunModel,
    LlmRunModel,
    ChapterQualityReportModel,
)


@pytest.fixture
async def case(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'retained_recovery_test.sqlite3'}"
    )

    def ddl(connection):
        for model in MODELS:
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX test_one_current "
            "ON chapter_draft_versions(chapter_id) WHERE is_current=1"
        )

    async with engine.begin() as connection:
        await connection.run_sync(ddl)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    writes = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def observe(_connection, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    async with factory() as session:
        project = ProjectModel(
            slug="keepers-offline",
            title="The Copper Gate",
            genre="fantasy",
            language="en",
            target_chapters=4,
            target_word_count=8000,
            metadata_json={},
        )
        session.add(project)
        await session.flush()
        project.metadata_json = {"execution_scope_id": f"project:{project.id}"}
        chapter = ChapterModel(
            project_id=project.id,
            chapter_number=2,
            title="A visitor",
            chapter_goal="Open the gate",
            target_word_count=2000,
            metadata_json={
                "chapter_review_attempts_active": 9,
                "chapter_critic_cycles": {"old": {"state": "completed"}},
            },
        )
        session.add(chapter)
        await session.flush()
        scene = SceneCardModel(
            project_id=project.id,
            chapter_id=chapter.id,
            scene_number=1,
            scene_type="action",
            target_word_count=2000,
            metadata_json={},
        )
        wf = WorkflowRunModel(
            project_id=project.id,
            workflow_type="chapter_pipeline",
            scope_type="chapter",
            scope_id=chapter.id,
            requested_by="fixture",
            status="failed",
        )
        session.add_all([scene, wf])
        await session.flush()
        root_llm = LlmRunModel(
            project_id=project.id,
            workflow_run_id=wf.id,
            logical_role="editor",
            provider="retained_fixture",
            model_name="inert",
            metadata_json={"execution_receipt": {"scope_id": f"project:{project.id}"}},
            input_tokens=147,
            output_tokens=11,
        )
        session.add(root_llm)
        await session.flush()
        parent = ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=chapter.id,
            version_no=27,
            content_md=PARENT,
            word_count=13,
            is_current=True,
            promotion_state="candidate",
        )
        candidate = ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=chapter.id,
            version_no=28,
            content_md=CANDIDATE,
            word_count=20,
            is_current=False,
            promotion_state="candidate",
            llm_run_id=root_llm.id,
        )
        session.add_all([parent, candidate])
        await session.flush()
        original_report = ChapterQualityReportModel(
            chapter_id=chapter.id,
            blocks_write=True,
            regen_attempts=3,
            report_json={
                "rewrite_evaluation_id": "original-eval",
                "graded": graded_text_fingerprint(CANDIDATE),
                "blocking_codes": ["SCENE_CARD_PROSE_COPIED"],
                "violations": [{"code": "SCENE_CARD_PROSE_COPIED", "severity": "high"}],
            },
        )
        task = RewriteTaskModel(
            project_id=project.id,
            trigger_type="chapter_review",
            trigger_source_id=chapter.id,
            rewrite_strategy="existing",
            priority=4,
            status="failed",
            attempts=1,
            instructions="Continue the plot; retain original source.",
            error_log="old failure",
            metadata_json={
                "draft_id": str(parent.id),
                "parent_draft_id": str(parent.id),
                "parent_draft_hash": text_hash(PARENT),
                "candidate_chapter_draft_id": str(candidate.id),
                "candidate_body_sha256": text_hash(CANDIDATE),
                "candidate_llm_run_id": str(root_llm.id),
                "candidate_gate_graded": graded_text_fingerprint(CANDIDATE),
                "candidate_gate_evaluation_id": "original-eval",
                "raw_history": ["unchanged"],
                "candidate_quality_gate_violations": [{"code": "SCENE_CARD_PROSE_COPIED"}],
                "candidate_duplicate_gate_findings": [],
                "candidate_quality_retrofit_findings": [],
            },
        )
        session.add_all([task, original_report])
        await session.commit()
        ids = {
            "project_id": project.id,
            "chapter_id": chapter.id,
            "failed_task_id": task.id,
            "candidate_id": candidate.id,
        }
        state = SimpleNamespace(
            factory=factory,
            session=session,
            project=project,
            chapter=chapter,
            parent=parent,
            candidate=candidate,
            task=task,
            root_llm=root_llm,
            original_report=original_report,
            ids=ids,
            writes=writes,
            calls=[],
            source_revision=1,
            failure=None,
            gate="ok",
            effects=True,
            duplicate=False,
            after_call=None,
            audit_extra=None,
            preflight_extra=None,
            original_task=deepcopy(task.metadata_json),
        )
        packet = SimpleNamespace(
            story_bible={"selected_fact": "The key was entrusted to the keeper."}
        )

        async def context(*_args):
            return packet

        monkeypatch.setattr(recovery, "build_chapter_writer_context", context)

        async def sources(_session, *, project, chapter, **_kw):
            assert _kw["context_packet"] is packet
            return {
                "current_source_channels": {
                    "exact": state.source_revision,
                    "bible": packet.story_bible,
                },
                "editable_plan": {"goal": "open gate"},
                "adopted_continuity": {
                    "project_id": str(project.id),
                    "before_chapter": chapter.chapter_number,
                    "recent_adopted_prose": [{"chapter": 1, "body": "The keeper received a key."}],
                },
            }

        monkeypatch.setattr(recovery, "load_review_source_context", sources)
        monkeypatch.setattr(
            recovery,
            "prepare_source_complete_prompt",
            lambda **kw: (
                kw["system_prompt"],
                kw["user_prompt"] + json.dumps(kw["source_context"]),
                {"source": "fixture"},
            ),
        )
        from bestseller.services import pipelines

        monkeypatch.setattr(
            pipelines, "chapter_review_recovery_source_hash", lambda: "fixture-policy"
        )
        band = SimpleNamespace(hard_min=1, hard_max=10000)

        def audit(**kw):
            body = kw["content_md"]
            code = "SCENE_CARD_PROSE_COPIED" if body == CANDIDATE else state.audit_extra
            findings = (
                (
                    DeterministicAuditFinding(
                        code,
                        "high",
                        "The keeper opens the copper gate.",
                        1,
                        1,
                        "Re-express the same action.",
                    ),
                )
                if code
                else ()
            )
            if body == CANDIDATE and state.preflight_extra:
                findings += (
                    DeterministicAuditFinding(
                        state.preflight_extra, "high", "ending", 3, 1, "Keep raw evidence."
                    ),
                )
            return DeterministicAuditReport(2, findings, not bool(findings), {}), band

        monkeypatch.setattr(recovery.reviews, "_audit_chapter_rewrite_candidate", audit)
        monkeypatch.setattr(
            recovery.reviews,
            "audit_persisted_story_effect_realization",
            lambda _p, _c, body, **kw: {
                "selected_effects": ["action"],
                "passed": state.effects if body != CANDIDATE else True,
                "body_sha256": text_hash(body),
            },
        )

        async def duplicates(*_args, **kw):
            if state.duplicate:
                return (
                    SimpleNamespace(
                        source="fixture",
                        code="DUPLICATE",
                        severity="high",
                        message="A duplicate",
                        evidence="gate",
                        payload={},
                    ),
                )
            return ()

        monkeypatch.setattr(
            recovery.reviews, "_collect_post_assembly_duplicate_findings", duplicates
        )

        async def gate(**kw):
            if state.gate == "error":
                raise RuntimeError("secret database detail")
            body = kw["content"]
            evaluation = str(uuid4())
            row = ChapterQualityReportModel(
                chapter_id=chapter.id,
                blocks_write=state.gate == "blocked",
                regen_attempts=0,
                report_json={
                    "rewrite_evaluation_id": evaluation,
                    "graded": graded_text_fingerprint(body),
                    "violations": [],
                    "blocking_codes": [],
                },
            )
            session.add(row)
            await session.flush()
            # These shipping side effects must be discarded by the savepoint.
            chapter.metadata_json = {"unwanted_shipping_mutation": True}
            return recovery.reviews.RewriteQualityGateReceipt(
                state.gate,
                evaluation,
                graded_text_fingerprint(body),
                row if state.gate != "missing" else None,
            )

        monkeypatch.setattr(recovery.reviews, "_evaluate_rewrite_quality_gate", gate)

        async def complete(_session, _settings, request):
            state.calls.append(request)
            async with factory() as observer:
                child = await observer.scalar(
                    select(RewriteTaskModel).where(RewriteTaskModel.parent_task_id == task.id)
                )
                assert child.status == "running" and child.attempts == 1
                assert child.metadata_json["attempt_claimed"] == 1
            if state.failure == "provider":
                raise RuntimeError("secret provider detail")
            from bestseller.services.retained_candidate_patch import prepare_candidate_patch

            findings = [
                f
                for f in audit(content_md=CANDIDATE)[0].findings
                if f.code == "SCENE_CARD_PROSE_COPIED"
            ]
            prepared = prepare_candidate_patch(CANDIDATE, findings)
            payload = {
                "schema": prepared["schema"],
                "body_sha256": prepared["body_sha256"],
                "catalog_sha256": prepared["catalog_sha256"],
                "patches": [
                    {"target_id": item["target_id"], "replacement": REPLACEMENT}
                    for item in prepared["targets"]
                ],
            }
            if state.failure == "invalid":
                payload["body_sha256"] = "forged"
            paid = LlmRunModel(
                project_id=project.id,
                workflow_run_id=request.workflow_run_id,
                logical_role="editor",
                provider="inert",
                model_name="fixture",
                metadata_json={"execution_receipt": {"scope_id": f"project:{project.id}"}},
            )
            session.add(paid)
            await session.flush()
            if state.after_call:
                await state.after_call()
            return LLMCompletionResult(
                content="",
                provider="inert",
                model_name="fixture",
                llm_run_id=paid.id,
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "type": "function",
                        "function": {
                            "name": TOOL_NAME,
                            "arguments": json.dumps({"patch_json": json.dumps(payload)}),
                        },
                    }
                ],
            )

        monkeypatch.setattr(recovery, "complete_text", complete)
        writes.clear()
        yield state
    await engine.dispose()


async def invoke(case, execute=False):
    return await recovery.repair_retained_candidate(
        case.session,
        load_settings(env={}),
        **case.ids,
        requested_by="offline-test",
        execute=execute,
    )


async def assert_original(case):
    await case.session.refresh(case.task)
    await case.session.refresh(case.original_report)
    await case.session.refresh(case.root_llm)
    await case.session.refresh(case.chapter)
    assert case.task.status == "failed" and case.task.attempts == 1
    assert case.task.metadata_json == case.original_task
    assert case.original_report.regen_attempts == 3
    assert case.root_llm.input_tokens == 147
    assert case.chapter.metadata_json == {
        "chapter_review_attempts_active": 9,
        "chapter_critic_cycles": {"old": {"state": "completed"}},
    }


async def test_dry_run_is_read_only_and_deterministic(case):
    first = await invoke(case)
    second = await invoke(case)
    assert first == second and first["provider_calls"] == first["database_writes"] == 0
    assert not case.writes and not case.calls
    await assert_original(case)


async def test_success_claim_committed_before_single_call_and_working_pointer_only(case):
    result = await invoke(case, True)
    assert result["status"] == "completed", result
    assert len(case.calls) == 1 and result["receipt"]["adoption_authorized"] is False
    assert case.calls[0].project_id == case.project.id
    drafts = list(
        await case.session.scalars(
            select(ChapterDraftVersionModel).order_by(ChapterDraftVersionModel.version_no)
        )
    )
    assert [d.is_current for d in drafts] == [False, False, True]
    assert drafts[-1].content_md == REPLACEMENT + CANDIDATE[CANDIDATE.index("\n\n") :]
    assert drafts[-1].promotion_state == "candidate" and drafts[-1].promoted_at is None
    await assert_original(case)
    case.writes.clear()
    replay = await invoke(case, True)
    assert replay["replay"] and replay["status"] == "completed"
    assert not case.writes and len(case.calls) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "body",
        "llm",
        "parent",
        "grade",
        "scope",
        "task",
        "candidate_current",
        "other_project",
        "gate_report",
        "hard",
    ],
)
async def test_preflight_binding_rejects_without_provider_or_writes(case, fault):
    if fault == "body":
        case.candidate.content_md += " changed"
    elif fault == "llm":
        case.candidate.llm_run_id = uuid4()
    elif fault == "parent":
        case.parent.content_md += " changed"
    elif fault == "grade":
        case.task.metadata_json = {**case.task.metadata_json, "candidate_gate_graded": {}}
    elif fault == "scope":
        case.root_llm.metadata_json = {"execution_receipt": {"scope_id": "project:wrong"}}
    elif fault == "task":
        case.task.status = "completed"
    elif fault == "candidate_current":
        case.parent.is_current = False
        await case.session.flush()
        case.candidate.is_current = True
    elif fault == "other_project":
        case.candidate.project_id = uuid4()
    elif fault == "gate_report":
        case.original_report.report_json = {"graded": graded_text_fingerprint("another body")}
    elif fault == "hard":
        case.task.metadata_json = {
            **case.task.metadata_json,
            "candidate_quality_gate_violations": [{"code": "CANON_CONFLICT"}],
        }
    await case.session.commit()
    case.writes.clear()
    with pytest.raises(ValueError, match="retained_candidate_recovery_"):
        await invoke(case, True)
    assert not case.calls and not case.writes


@pytest.mark.parametrize(
    "failure",
    [
        "provider",
        "gate",
        "effects",
        "duplicate",
        "audit",
        "missing_gate",
        "gate_error",
        "source",
        "parent",
    ],
)
async def test_failed_successor_retained_no_pointer_change_no_second_credit(case, failure):
    if failure == "provider":
        case.failure = "provider"
    elif failure == "gate":
        case.gate = "blocked"
    elif failure == "effects":
        case.effects = False
    elif failure == "duplicate":
        case.duplicate = True
    elif failure == "audit":
        case.audit_extra = "SCENE_CARD_PROSE_COPIED"
    elif failure == "missing_gate":
        case.gate = "missing"
    elif failure == "gate_error":
        case.gate = "error"
    elif failure == "source":

        async def change():
            case.source_revision += 1

        case.after_call = change
    elif failure == "parent":

        async def change():
            case.parent.content_md += " concurrent change"
            await case.session.flush()

        case.after_call = change
    result = await invoke(case, True)
    assert result["status"] == "failed", result
    assert result["receipt"]["working_pointer_selected"] is False and len(case.calls) == 1
    await case.session.refresh(case.parent)
    assert case.parent.is_current
    assert "secret" not in json.dumps(result)
    drafts = list(await case.session.scalars(select(ChapterDraftVersionModel)))
    assert len(drafts) == (2 if failure == "provider" else 3)
    if failure == "parent":
        with pytest.raises(ValueError, match="parent_binding"):
            await invoke(case, True)
    else:
        replay = await invoke(case, True)
        assert replay["status"] == "failed"
    assert len(case.calls) == 1
    await assert_original(case)


async def test_running_claim_does_not_resume_paid_call(case):
    async def interrupted(*_args):
        raise KeyboardInterrupt("simulated worker exit")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(recovery, "complete_text", interrupted)
    try:
        with pytest.raises(KeyboardInterrupt):
            await invoke(case, True)
        result = await invoke(case, True)
        assert result["replay"] and result["status"] == "running"
        assert not case.calls
    finally:
        monkey.undo()


async def test_dirty_session_cannot_commit_unrelated_work(case):
    case.project.title = "Unrelated pending edit"
    with pytest.raises(ValueError, match="clean_session_required"):
        await invoke(case, True)
    assert not case.calls and not case.writes


async def test_scene_copy_plus_audit_only_ending_keeps_raw_but_only_patches_copy(case):
    case.preflight_extra = "ENDING_HOOK_MISSING"
    plan = await invoke(case)
    assert [f["code"] for f in plan["fence"]["preflight_audit"]["findings"]] == [
        "SCENE_CARD_PROSE_COPIED",
        "ENDING_HOOK_MISSING",
    ]
    assert plan["target_count"] == 1 and not case.writes
    result = await invoke(case, True)
    assert result["status"] == "completed", result
    child = await case.session.scalar(
        select(RewriteTaskModel).where(RewriteTaskModel.parent_task_id == case.task.id)
    )
    assert [f["code"] for f in child.metadata_json["prepared_patch"]["findings"]] == [
        "SCENE_CARD_PROSE_COPIED"
    ]
    assert (
        child.metadata_json["request"]["metadata"]["source_transport"]["preflight_audit"][
            "findings"
        ][1]["code"]
        == "ENDING_HOOK_MISSING"
    )


@pytest.mark.parametrize(
    "code", ["CANON_FORBIDDEN_TERM", "LENGTH_OUT_OF_BAND", "UNKNOWN_HARD_GUARD"]
)
async def test_no_hard_or_unknown_blocker_can_be_demoted_to_copy_only(case, code):
    case.preflight_extra = code
    with pytest.raises(ValueError, match="audit_not_copy_only"):
        await invoke(case, True)
    assert not case.calls and not case.writes


async def test_invalid_patch_retains_paid_return_but_not_new_body(case):
    case.failure = "invalid"
    result = await invoke(case, True)
    assert result["status"] == "failed" and "new_draft_id" not in result["receipt"]
    assert result["receipt"]["completion"]["tool_calls"]
    async with case.factory() as observer:
        paid = await observer.get(LlmRunModel, UUID(result["receipt"]["completion"]["llm_run_id"]))
        assert paid is not None


async def test_completed_replay_rejects_changed_source_without_writes(case):
    assert (await invoke(case, True))["status"] == "completed"
    case.source_revision += 1
    case.writes.clear()
    with pytest.raises(ValueError, match="completed_source_changed"):
        await invoke(case, True)
    assert not case.writes and len(case.calls) == 1


async def test_new_policy_never_grants_new_running_lineage_credit(case, monkeypatch):
    case.failure = "provider"
    first = await invoke(case, True)
    from bestseller.services import pipelines

    monkeypatch.setattr(pipelines, "chapter_review_recovery_source_hash", lambda: "changed-policy")
    again = await invoke(case, True)
    assert first["status"] == again["status"] == "failed"
    assert len(case.calls) == 1


async def test_another_failed_task_in_same_lineage_cannot_get_another_batch(case):
    case.failure = "provider"
    assert (await invoke(case, True))["status"] == "failed"
    another = RewriteTaskModel(
        project_id=case.ids["project_id"],
        parent_task_id=case.ids["failed_task_id"],
        trigger_type="chapter_review",
        trigger_source_id=case.ids["chapter_id"],
        rewrite_strategy="existing",
        status="failed",
        attempts=1,
        instructions="A separately retained failure.",
        metadata_json=deepcopy(case.original_task),
    )
    case.session.add(another)
    await case.session.commit()
    args = {**case.ids, "failed_task_id": another.id}
    case.writes.clear()
    with pytest.raises(ValueError, match="lineage_already_claimed"):
        await recovery.repair_retained_candidate(
            case.session, load_settings(env={}), **args, requested_by="other-worker", execute=True
        )
    assert len(case.calls) == 1 and not case.writes
