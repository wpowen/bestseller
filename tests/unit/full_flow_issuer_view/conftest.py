"""Isolated native-column/physical-input fixtures, never normal issuance proof."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import importlib
from pathlib import Path
import socket
from types import SimpleNamespace
from uuid import uuid4

from issuer_view_test_support import canonical, sha
import pytest
from sqlalchemy import JSON, MetaData, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
from bestseller.infra.db import models as db
from bestseller.services import (
    chapter_review_reconciliation,
    context,
    llm,
    model_catalog,
    pipelines,
    review_source_context,
    review_source_ordering,
)
from bestseller.services import primary_review_transport as transport
from bestseller.services import retained_review_input_proof as retained
from bestseller.services import review_issuer_archive as archive
from bestseller.settings import load_settings

MODELS = (
    db.ProjectModel,
    db.ChapterModel,
    db.ChapterDraftVersionModel,
    db.WorkflowRunModel,
    db.ReviewReportModel,
    db.QualityScoreModel,
    db.SceneCardModel,
    db.RewriteTaskModel,
    db.LlmRunModel,
    db.ExecutionBudgetModel,
    db.ExecutionOperationModel,
    db.ExecutionAttemptModel,
)


@pytest.fixture
def issuer_view():
    # Main runs RED before this module is authorized. Never skip missing code.
    return importlib.import_module("bestseller.services.review_issuer_view")


@pytest.fixture(autouse=True)
def no_gateway_or_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("Issuer view unit contracts cannot dispatch a model or network request.")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(llm, "complete_text", denied)


@pytest.fixture
def source_copy(tmp_path, issuer_view, monkeypatch):
    root = Path(pipelines.__file__).resolve().parents[3]
    paths = chapter_review_reconciliation._review_policy_inventory(
        root, (root / "src/bestseller/services/pipelines.py").read_text()
    )
    before = pipelines.chapter_review_recovery_source_hash()
    copied = tmp_path / "current-policy-copy"
    for path in paths:
        assert not path.is_symlink()
        destination = copied / path.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(path.read_bytes())
    assert pipelines.chapter_review_recovery_source_hash() == before
    monkeypatch.setattr(pipelines, "__file__", str(copied / "src/bestseller/services/pipelines.py"))
    monkeypatch.setattr(
        archive, "__file__", str(copied / "src/bestseller/services/review_issuer_archive.py")
    )
    assert pipelines.chapter_review_recovery_source_hash() == before
    return copied


@pytest.fixture
async def issuer_case(tmp_path, issuer_view, source_copy, monkeypatch, request):
    options = getattr(request, "param", {})
    path = tmp_path / "issuer_view_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    metadata = MetaData()
    pending = [model.__table__ for model in MODELS]
    native_tables = {}
    while pending:
        native = pending.pop()
        if native.key in native_tables:
            continue
        native_tables[native.key] = native
        pending.extend(foreign_key.column.table for foreign_key in native.foreign_keys)
    # Keep native foreign keys and their complete referenced-table closure.
    # Changing only copied SQLite column types must not weaken source identity.
    for key in sorted(native_tables):
        table = native_tables[key].to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
            elif isinstance(column.type, PGUUID):
                column.type = Uuid(as_uuid=True, native_uuid=False)
    for table in metadata.tables.values():
        for foreign_key in table.foreign_keys:
            assert foreign_key.column.table.metadata is metadata
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            settings = load_settings(env={})
            settings.llm.mock = False
            settings.artifact_store.local_dir = str(tmp_path / "artifacts")
            settings.artifact_store.retain_prompt_payloads = True
            settings.llm.critic = settings.llm.critic.model_copy(
                update={
                    "model": "openai/issuer-view-unit",
                    "api_base": "https://issuer.fixture.invalid/v1",
                    "api_key_env": None,
                    "max_tokens": 16384,
                    "temperature": 0.2,
                    "thinking_type": None,
                    "reasoning_effort": None,
                    "stream": False,
                    "n_candidates": 1,
                    "model_override": None,
                }
            )
            entry = model_catalog.ModelCatalogEntry(
                id="issuer-view-unit",
                display_name="unit",
                vendor="fixture",
                model=settings.llm.critic.model,
                api_base=settings.llm.critic.api_base,
            )
            catalog = tmp_path / "fixture-model-catalog.yaml"
            catalog.write_text(canonical({"models": [entry.model_dump(mode="json")]}))
            monkeypatch.setattr(model_catalog, "_CATALOG_PATH", catalog)
            project = db.ProjectModel(
                slug="issuer-view-unit",
                title="Technical source custody only",
                language="zh-CN",
                genre="general-fiction",
                target_chapters=1,
                target_word_count=2000,
                metadata_json={"llm_model_id": entry.id},
            )
            session.add(project)
            await session.flush()
            chapter = db.ChapterModel(
                project_id=project.id, chapter_number=1, chapter_goal="fixture", metadata_json={}
            )
            session.add(chapter)
            await session.flush()
            draft = db.ChapterDraftVersionModel(
                project_id=project.id,
                chapter_id=chapter.id,
                version_no=1,
                content_md="第一段原文。\n\n第二段原文。",
                is_current=True,
                promotion_state="under_review",
            )
            owner = db.WorkflowRunModel(
                project_id=project.id,
                workflow_type="chapter_pipeline",
                scope_type="chapter",
                scope_id=chapter.id,
                status="completed",
                metadata_json={},
            )
            session.add_all([draft, owner])
            await session.flush()
            session.add(
                db.SceneCardModel(
                    project_id=project.id,
                    chapter_id=chapter.id,
                    scene_number=1,
                    scene_type="development",
                    purpose={"story": "fixture"},
                    metadata_json={},
                )
            )
            capsule = archive.capture_current_issuer_archive(settings)["capsule"]
            selection = {"policy": "typed-world-and-relevant-current-state.v1"}
            marker = options.get("marker", review_source_ordering.SOURCE_ORDERING_POLICY)
            if marker is not None:
                selection["source_ordering_policy"] = marker
            source = {
                "schema": review_source_context.SOURCE_POLICY,
                "adopted_continuity": {"schema": "planning-continuity.v1", "adopted_sources": []},
                "current_source_channels": {
                    "source_selection_receipt": selection,
                    "story_bible": {"relationships": [{"order": 2}, {"order": 1}]},
                    "literal_false": False,
                    "literal_zero": 0,
                },
                "authority": retained._SOURCE_AUTHORITY,
            }
            if options.get("capsule", True):
                source["review_issuer"] = deepcopy(capsule)
            current_source = deepcopy(source)
            current_source.pop("review_issuer", None)
            system, user, source_receipt = transport.prepare_source_complete_prompt(
                system_prompt="Technical custody fixture; no literary verdict.",
                user_prompt="Read.",
                source_context=source,
                output_tokens=1024,
                tools=None,
            )
            completion_request = llm.LLMCompletionRequest(
                logical_role="critic",
                system_prompt=system,
                user_prompt=user,
                fallback_response="never dispatched",
                project_id=project.id,
                workflow_run_id=owner.id,
                prompt_template="chapter_review",
                prompt_version="issuer-view-technical-input.v1",
                max_tokens_override=1024,
            )
            role = retained._fresh_role(settings, project, completion_request, {entry.id: entry})
            effective = llm._effective_input_hash(completion_request, role)
            origin = llm._paid_call_origin(completion_request, effective)
            compact = {"schema": "effective-input.v1", "hash": effective, "model": role.model}
            expanded = {
                **compact,
                "messages_source": "system_user",
                "tools_included": False,
                "legacy_prompt_hash": llm._hash_prompt(system, user),
            }
            scope, operation_key, attempt_id, run_id = (
                "test:issuer-view:" + uuid4().hex,
                uuid4().hex,
                uuid4().hex,
                uuid4(),
            )
            execution = {
                "scope_id": scope,
                "operation_key": operation_key,
                "attempts": [{"attempt_id": attempt_id, "status": "completed"}],
                "policy": "execution-budget.v1",
            }
            raw_message = {"role": "assistant", "content": "No quality proof.", "tool_calls": []}
            paid = llm.LLMCompletionResult(
                content="No quality proof.",
                provider="openai",
                model_name=role.model,
                finish_reason="stop",
                input_tokens=3,
                output_tokens=4,
                tool_calls=[],
                raw_message=raw_message,
                metadata={
                    "paid_call_origin": deepcopy(origin),
                    "effective_input_receipt": deepcopy(compact),
                    "execution_receipt": execution,
                },
            ).model_dump(mode="json")
            completed = deepcopy(paid)
            completed["metadata"]["effective_input_receipt"] = deepcopy(expanded)
            with llm.bind_project_prompt_audit(
                project.id, reason="issuer view technical unit fixture"
            ):
                request_ref, response_ref, reason, replayable = llm._persist_prompt_payloads(
                    settings=settings,
                    request=completion_request,
                    llm_run_id=run_id,
                    configured_model=role.model,
                    effective_model=role.model,
                    provider="openai",
                    effective_temperature=role.temperature,
                    effective_max_tokens=1024,
                    effective_timeout_seconds=role.timeout_seconds,
                    effective_stream=False,
                    effective_thinking_type=None,
                    effective_reasoning_effort=None,
                    effective_role_settings=role,
                    content=paid["content"],
                    tool_call_count=0,
                    finish_reason="stop",
                    fallback_used=False,
                    fallback_source=None,
                    input_tokens=3,
                    output_tokens=4,
                    tool_calls=[],
                    raw_message=raw_message,
                )
            assert reason is None and replayable and request_ref and response_ref
            run = db.LlmRunModel(
                id=run_id,
                project_id=project.id,
                workflow_run_id=owner.id,
                logical_role="critic",
                provider="openai",
                model_name=role.model,
                prompt_template=completion_request.prompt_template,
                prompt_version=completion_request.prompt_version,
                prompt_hash=effective,
                finish_reason="stop",
                request_payload_ref=request_ref,
                response_payload_ref=response_ref,
                metadata_json={
                    "execution_receipt": execution,
                    "effective_input_receipt": expanded,
                    "paid_call_origin": origin,
                    "review_source_receipt": source_receipt,
                },
            )
            budget = db.ExecutionBudgetModel(
                scope_id=scope,
                max_calls=100,
                max_tokens=100000,
                calls_used=1,
                tokens_charged=7,
                deadline_at=datetime.now(UTC) + timedelta(days=1),
            )
            operation = db.ExecutionOperationModel(
                key=operation_key,
                scope_id=scope,
                input_hash=effective,
                status="completed",
                owner="unit-fixture",
                lease_until=datetime.now(UTC) + timedelta(hours=1),
                result_json=completed,
            )
            attempt = db.ExecutionAttemptModel(
                id=attempt_id,
                operation_key=operation_key,
                scope_id=scope,
                input_hash=effective,
                model_name=role.model,
                status="completed",
                reserved_tokens=7,
                actual_tokens=7,
                created_at=datetime.now(UTC),
            )
            transport_key = sha(f"transport-result.v1\n{scope}\n{attempt_id}".encode())
            transport_row = db.ExecutionOperationModel(
                key=transport_key,
                scope_id=scope,
                input_hash=effective,
                status="transport_result",
                owner="unit-fixture",
                lease_until=datetime.now(UTC) + timedelta(hours=1),
                result_json={
                    "schema": "transport-result.v1",
                    "operation_key": operation_key,
                    "attempt_id": attempt_id,
                    "input_hash": effective,
                    "result": paid,
                },
            )
            fingerprint = pipelines.chapter_review_body_fingerprint(draft.content_md)
            report = db.ReviewReportModel(
                project_id=project.id,
                target_type="chapter",
                target_id=chapter.id,
                llm_run_id=run.id,
                reviewer_type="technical-custody-fixture",
                verdict="unknown",
                severity_max="high",
                structured_output={
                    "draft_id": str(draft.id),
                    "reviewed_body_fingerprint": fingerprint,
                    "evidence_summary": {},
                },
            )
            session.add_all([run, budget, operation, attempt, transport_row, report])
            await session.flush()
            quality = db.QualityScoreModel(
                project_id=project.id,
                target_type="chapter",
                target_id=chapter.id,
                review_report_id=report.id,
                chapter_draft_version_id=draft.id,
                score_overall=0.0,
                is_current=False,
            )
            session.add(quality)
            await session.flush()
            cycle_id = pipelines._chapter_critic_cycle_id(
                project.id, chapter.id, fingerprint["digest"]
            )
            cycle = {
                "schema_version": "chapter-critic-cycle.v1",
                "cycle_id": cycle_id,
                "source_draft_id": str(draft.id),
                "source_body_hash": fingerprint["digest"],
                "policy_source_hash": capsule["runtime_policy_sha256"],
                "review_workflow_run_id": str(owner.id),
                "state": "completed",
                "attempts": [
                    {
                        "attempt": 1,
                        "state": "completed",
                        "outcome": "available",
                        "operation_namespace": f"chapter-critic:{cycle_id}:1",
                        "draft_id": str(draft.id),
                        "body_fingerprint": fingerprint,
                        "review_report_id": str(report.id),
                        "quality_score_id": str(quality.id),
                        "rewrite_task_id": None,
                        "review_result": ChapterReviewResult(
                            verdict="unknown",
                            severity_max="high",
                            scores=ChapterReviewScores(
                                **dict.fromkeys(ChapterReviewScores.model_fields, 0.0)
                            ),
                        ).model_dump(mode="json"),
                    }
                ],
                "infrastructure_resumptions": [],
            }
            chapter.metadata_json = {"chapter_critic_cycles": {cycle_id: cycle}}
            await session.commit()
            case = SimpleNamespace(
                module=issuer_view,
                session=session,
                settings=settings,
                project=project,
                chapter=chapter,
                draft=draft,
                report=report,
                quality=quality,
                cycle=cycle,
                run=run,
                operation=operation,
                attempt=attempt,
                transport=transport_row,
                source=source,
                current_source=current_source,
                capsule=capsule,
                root=source_copy,
                artifact_root=Path(settings.artifact_store.local_dir),
                models=MODELS,
                ids={
                    "project_id": project.id,
                    "chapter_id": chapter.id,
                    "draft_id": draft.id,
                    "cycle_id": cycle_id,
                },
                source_reads=[],
            )

            async def build_context(*args, **kwargs):
                return SimpleNamespace(fixture="data-only context")

            async def load_source(*args, **kwargs):
                assert kwargs["project"].id == project.id and kwargs["chapter"].id == chapter.id
                assert kwargs["content_md"] == draft.content_md
                case.source_reads.append(True)
                return deepcopy(case.current_source)

            monkeypatch.setattr(context, "build_chapter_writer_context", build_context)
            monkeypatch.setattr(review_source_context, "load_review_source_context", load_source)
            yield case
    finally:
        await engine.dispose()
