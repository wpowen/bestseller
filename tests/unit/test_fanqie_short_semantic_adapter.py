"""Real isolated SQLite SELECT/readback tests; every paid row is synthetic."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from sqlalchemy import delete, event, select, update
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import CreateTable

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src/bestseller").is_dir())
HERE = ROOT / "reports/project-reliability-review-2026-10-24/implementation/source-export-semantic-integration"
sys.path[:0] = [str(ROOT / "tests/unit"), str(ROOT / "src"), str(ROOT / "tests")]
import test_fanqie_short_semantic_consumer as fixture
from bestseller.services import fanqie_short_semantic_consumer as consumer
from support.database_safety import install_database_safety, selected_database_url

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services.fanqie_short_semantic_adapter import load_current_semantic_context

semantic = fixture.f.stage


@compiles(JSONB, "sqlite")
def jsonb_sqlite(_type, _compiler, **kw):
    return "JSON"


@compiles(PGUUID, "sqlite")
def uuid_sqlite(_type, _compiler, **kw):
    return "TEXT"


class NativeAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        (HERE / "test-tmp").mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=HERE / "test-tmp")
        self.base = Path(self.directory.name)
        self.env = patch.dict(
            os.environ,
            {"BESTSELLER__DATABASE__URL": f"sqlite+aiosqlite:///{self.base}/adapter_test.sqlite3"},
        )
        self.env.start()
        url = selected_database_url()
        assert (
            url.get_backend_name() == "sqlite" and Path(url.database).name == "adapter_test.sqlite3"
        )
        # Pytest's shared conftest already owns the Engine guard/listener.
        # Standalone unittest keeps the stage's explicit guard ownership.
        self.remove_db_guard = (lambda: None) if os.environ.get("PYTEST_CURRENT_TEST") else install_database_safety()
        self.guards = [
            patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")),
            patch.object(
                socket.socket, "connect_ex", side_effect=AssertionError("network forbidden")
            ),
            patch.object(
                socket, "create_connection", side_effect=AssertionError("network forbidden")
            ),
            patch.object(
                semantic,
                "_native_complete_text",
                new=AsyncMock(side_effect=AssertionError("provider forbidden")),
            ),
        ]
        for guard in self.guards:
            guard.start()
        self.engine = create_async_engine(url)
        models = (
            ProjectModel,
            WorkflowRunModel,
            PlanningArtifactVersionModel,
            LlmRunModel,
            ExecutionBudgetModel,
            ExecutionOperationModel,
            ExecutionAttemptModel,
        )
        async with self.engine.begin() as connection:
            for model in models:
                ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
                await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.inputs, self.source = fixture.build()
        self.native_report = deepcopy(self.inputs["original_report"])
        self.assembly = SimpleNamespace(
            full_text=self.inputs["full_text"], manifest=deepcopy(self.inputs["manifest"])
        )
        self.project_id = UUID(self.inputs["project_id"])
        self.workflow_id = UUID(self.inputs["workflow_run_id"])
        native = self.source["native"]
        self.run_id = UUID(native["llm_run"]["id"])
        self.root_key = native["operation"]["key"]
        self.attempt_id = native["attempts"][0]["id"]
        self.transport_key = native["transport_results"][0]["key"]
        now = datetime.now(UTC)
        self.project = ProjectModel(
            id=self.project_id,
            slug="runtime-adapter-test",
            title=self.inputs["title"],
            genre="fixture",
            target_chapters=1,
            target_word_count=8000,
            metadata_json={
                "current_approved_contract": deepcopy(self.inputs["approved_contract"]),
                "execution_scope_id": self.inputs["execution_scope_id"],
                "current_scope": self.inputs["current_scope"],
                "operation_namespace": self.inputs["operation_namespace"],
            },
        )
        self.session.add(self.project)
        self.session.add(
            WorkflowRunModel(
                id=self.workflow_id,
                project_id=self.project_id,
                workflow_type="whole_semantic_review",
                status="completed",
                metadata_json={"fixture_only": True},
            )
        )
        self.session.add(
            ExecutionBudgetModel(
                scope_id=native["operation"]["scope_id"],
                max_calls=10,
                max_tokens=100000,
                calls_used=1,
                tokens_charged=55,
                deadline_at=now,
                status="active",
                metadata_json={},
            )
        )
        run = deepcopy(native["llm_run"])
        for key in ("id", "project_id", "workflow_run_id"):
            run[key] = UUID(run[key])
        self.session.add(LlmRunModel(**run))
        self.early_root = deepcopy(native["operation"])
        self.early_root["result_json"].pop("llm_run_id")
        self.early_root["result_json"]["metadata"].pop("prompt_payload_receipt")
        self.early_root["result_json"]["metadata"]["llm_run_flushed"] = False
        transport = deepcopy(native["transport_results"][0])
        transport["result_json"]["result"] = deepcopy(self.early_root["result_json"])
        for row in (self.early_root, transport):
            self.session.add(ExecutionOperationModel(**row, owner="fixture", lease_until=now))
        self.session.add(ExecutionAttemptModel(**native["attempts"][0], created_at=now))
        self.saved = semantic.validate_response(
            self.source["request_payload"],
            self.source["completion"],
            current_binding=self.source["request_payload"]["binding"],
            retained_payloads=self.source["retained_payloads"],
        )
        assert self.saved["error"] is None, self.saved["error"]
        self.artifact_id = uuid4()
        self.session.add(
            PlanningArtifactVersionModel(
                id=self.artifact_id,
                project_id=self.project_id,
                artifact_type="fanqie_whole_semantic_review",
                status="validated",
                schema_version="4",
                version_no=1,
                content=deepcopy(self.saved),
                source_run_id=self.workflow_id,
            )
        )
        self.settings = SimpleNamespace(artifact_store=SimpleNamespace(local_dir=str(self.base)))
        for ref, data in self.source["physical_files"].items():
            path = self.base / ref
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        await self.session.commit()
        self.factory_calls = []

    async def asyncTearDown(self):
        await self.session.close()
        await self.engine.dispose()
        for guard in reversed(self.guards):
            guard.stop()
        self.remove_db_guard()
        self.env.stop()
        self.directory.cleanup()

    async def factory(self, session, project, assembly, source_run_id):
        # Actual independent SELECT, not artifact.request_payload -> kwargs.
        meta = await session.scalar(
            select(ProjectModel.metadata_json)
            .where(ProjectModel.id == project.id)
            .execution_options(autoflush=False)
        )
        self.factory_calls.append(source_run_id)
        return dict(
            project_id=str(project.id),
            workflow_run_id=str(source_run_id),
            full_text=assembly.full_text,
            title=project.title,
            protagonist="我",
            manifest=deepcopy(assembly.manifest),
            approved_contract=deepcopy(meta["current_approved_contract"]),
            original_report=deepcopy(self.native_report),
            current_scope=meta["current_scope"],
            execution_scope_id=meta["execution_scope_id"],
            operation_namespace=meta["operation_namespace"],
        )

    @contextmanager
    def only_select(self):
        seen = []

        def check(conn, cursor, statement, parameters, context, executemany):
            assert statement.lstrip().upper().startswith("SELECT"), statement[:50]
            seen.append(statement)

        event.listen(self.engine.sync_engine, "before_cursor_execute", check)
        try:
            with (
                patch.object(
                    self.session,
                    "flush",
                    new=AsyncMock(side_effect=AssertionError("flush forbidden")),
                ),
                patch.object(
                    self.session,
                    "commit",
                    new=AsyncMock(side_effect=AssertionError("commit forbidden")),
                ),
            ):
                yield seen
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", check)

    async def load(self, **kwargs):
        with self.only_select() as seen:
            result = await load_current_semantic_context(
                self.session,
                self.settings,
                project=self.project,
                assembly=self.assembly,
                title=self.inputs["title"],
                original_report=self.native_report,
                current_inputs_factory=kwargs.pop("factory", self.factory),
                semantic=semantic,
                consumer=consumer,
                **kwargs,
            )
        self.assertGreaterEqual(len(seen), 8)
        return result

    async def mutate_saved(self, mutate):
        saved = deepcopy(self.saved)
        mutate(saved)
        await self.session.execute(
            update(PlanningArtifactVersionModel)
            .where(PlanningArtifactVersionModel.id == self.artifact_id)
            .values(content=saved)
        )

    async def test_original_native_rows_files_and_early_root_reverify(self):
        result = await self.load()
        self.assertEqual(result["derived_trace"]["status"], "verified")
        self.assertEqual(result["derived_review"]["remaining_blockers"], [])
        self.assertEqual(self.factory_calls, [self.workflow_id])
        self.assertNotIn("ready_for_upload", result)
        self.assertNotIn("whole_book_complete", result)
        json.dumps(result["artifact_reference"], allow_nan=False)
        json.dumps(result["derived_trace"], allow_nan=False)
        context = result["semantic_context"]
        view = consumer.semantic_readiness_gates(
            current_inputs=context.current_inputs, evidence=context.evidence, semantic=semantic
        )
        self.assertTrue(view["ranking_gate_passed"])
        self.assertFalse(view["semantic_derived_review"]["original_report"]["passed"])
        # Root did not gain fabricated flush/retention fields.
        root = await self.session.scalar(
            select(ExecutionOperationModel.result_json).where(
                ExecutionOperationModel.key == self.root_key
            )
        )
        self.assertEqual(root, self.early_root["result_json"])
        context.evidence["native"]["operation"]["status"] = "unknown"
        with self.assertRaises(ValueError):
            consumer.semantic_readiness_gates(
                current_inputs=context.current_inputs, evidence=context.evidence, semantic=semantic
            )

    async def test_no_flush_or_overwrite_of_dirty_caller_object(self):
        self.project.genre = "caller unflushed edit"
        await self.load()
        self.assertIn(self.project, self.session.dirty)
        self.assertEqual(self.project.genre, "caller unflushed edit")
        with self.session.no_autoflush:
            stored = await self.session.scalar(
                select(ProjectModel.genre).where(ProjectModel.id == self.project_id)
            )
        self.assertEqual(stored, "fixture")

    async def test_legacy_no_settings_reader_uses_fresh_paths_with_actual_artifact(self):
        from bestseller.services import fanqie_short_semantic_readiness as bridge
        from bestseller.settings import load_settings
        settings = load_settings().model_copy(deep=True)
        settings.artifact_store.local_dir = str(self.base)
        settings.artifact_store.retain_prompt_payloads = True

        async def fresh(session, project, assembly, source_run_id, *, settings):
            self.assertEqual(Path(settings.artifact_store.local_dir), self.base)
            # Independently SELECT the fixture's current source, never return
            # artifact.request_payload as a current input or use a true flag.
            return await self.factory(session, project, assembly, source_run_id)

        with self.only_select(), patch.object(bridge, "_fresh_inputs", side_effect=fresh), \
             patch("bestseller.settings.load_settings", return_value=settings) as loader, \
             patch("bestseller.settings.get_settings", side_effect=AssertionError("cached settings forbidden")):
            context = await bridge.load_upload_semantic_context(
                self.session, project=self.project, assembly=self.assembly,
                original_report=self.native_report,
            )  # Intentionally no settings: actual current artifact and paid SQLite rows/files.
        loader.assert_called_once()
        view = consumer.semantic_readiness_gates(
            current_inputs=context.current_inputs, evidence=context.evidence, semantic=semantic)
        self.assertEqual(view["semantic_derived_review"]["adjudication_trace"]["status"], "verified")
        self.assertEqual(self.factory_calls, [self.workflow_id])

    async def test_publication_factory_receives_title_evidence_and_manifest_is_not_aliased(self):
        from bestseller.services import fanqie_short_semantic_readiness as bridge
        native = {"assembly_sha256": self.assembly.manifest["assembly_sha256"],
                  "project": {"title": self.inputs["title"]}, "reader_contract": {"pov": "first_person"},
                  "sources": [{"chapter_number": 1, "draft_id": str(uuid4()),
                               "raw_body_sha256": "a" * 64,
                               "lineage": {"adoption_state": "promoted"}}]}
        assembly = SimpleNamespace(full_text=self.assembly.full_text, manifest=deepcopy(native))
        title_evidence = {"synthetic_interface_only": True}  # Not passed to validator or publication.
        result = {**self.inputs, "manifest": deepcopy(native)}
        with patch("bestseller.services.fanqie_short_title_evidence.load_native_title_evidence",
                   new=AsyncMock(return_value=title_evidence)) as reader, \
             patch("bestseller.services.fanqie_short_semantic_inputs.build_current_inputs",
                   new=AsyncMock(return_value=result)) as factory:
            loaded = await bridge._fresh_inputs(
                self.session, self.project, assembly, self.workflow_id, settings=self.settings)
        self.assertIs(loaded, result)
        self.assertEqual(loaded["manifest"], native)
        self.assertNotIn("promotion_state", loaded["manifest"]["sources"][0])
        reader.assert_awaited_once_with(self.session, self.settings, project=self.project)
        factory.assert_awaited_once_with(self.session, project=self.project, assembly=assembly,
                                        workflow_run_id=self.workflow_id, native_title_evidence=title_evidence)

    async def test_missing_native_rows_block(self):
        cases = [
            (WorkflowRunModel, WorkflowRunModel.id == self.workflow_id),
            (LlmRunModel, LlmRunModel.id == self.run_id),
            (ExecutionOperationModel, ExecutionOperationModel.key == self.root_key),
            (ExecutionOperationModel, ExecutionOperationModel.key == self.transport_key),
            (ExecutionAttemptModel, ExecutionAttemptModel.id == self.attempt_id),
            (
                ExecutionBudgetModel,
                ExecutionBudgetModel.scope_id == self.inputs["execution_scope_id"],
            ),
        ]
        for model, clause in cases:
            with self.subTest(table=model.__tablename__, clause=str(clause)):
                nested = await self.session.begin_nested()
                try:
                    await self.session.execute(delete(model).where(clause))
                    with self.assertRaises(ValueError):
                        await self.load()
                finally:
                    await nested.rollback()

    async def test_attempt_inventory_never_filters_failures(self):
        for state in ("unknown", "reserved", "not_sent", "reconciled_charged", "completed"):
            with self.subTest(state=state):
                nested = await self.session.begin_nested()
                try:
                    row = deepcopy(self.source["native"]["attempts"][0])
                    row.update(id="extra-attempt", status=state)
                    self.session.add(ExecutionAttemptModel(**row, created_at=datetime.now(UTC)))
                    await self.session.flush()
                    with self.assertRaisesRegex(ValueError, "single_original"):
                        await self.load()
                finally:
                    await nested.rollback()

    async def test_native_failure_reconciliation_and_unknown_inventory_blocks(self):
        for state in (
            "transport_failure_receipt",
            "reconciliation_receipt",
            "unusable_provider_return",
            "transport_result",
            "unknown",
            "wrong_scope_attempt_only",
        ):
            with self.subTest(state=state):
                nested = await self.session.begin_nested()
                try:
                    self.session.add(
                        ExecutionOperationModel(
                            key=hashlib.sha256(state.encode()).hexdigest(),
                            scope_id=(
                                "other-scope"
                                if state == "wrong_scope_attempt_only"
                                else self.inputs["execution_scope_id"]
                            ),
                            input_hash="b" * 64,
                            status=(
                                "transport_failure_receipt"
                                if state == "wrong_scope_attempt_only"
                                else state
                            ),
                            owner="fixture",
                            lease_until=datetime.now(UTC),
                            result_json=(
                                {"attempt_id": self.attempt_id}
                                if state == "wrong_scope_attempt_only"
                                else {
                                    "operation_key": self.root_key,
                                    "attempt_id": self.attempt_id,
                                }
                            ),
                        )
                    )
                    await self.session.flush()
                    with self.assertRaisesRegex(ValueError, "transport_inventory"):
                        await self.load()
                finally:
                    await nested.rollback()

    async def test_native_states_tokens_and_recovery_are_not_success(self):
        for model, field, value, clause in [
            (WorkflowRunModel, "status", "running", WorkflowRunModel.id == self.workflow_id),
            (WorkflowRunModel, "project_id", uuid4(), WorkflowRunModel.id == self.workflow_id),
            (
                ExecutionOperationModel,
                "status",
                "unknown",
                ExecutionOperationModel.key == self.root_key,
            ),
            (
                ExecutionAttemptModel,
                "status",
                "reserved",
                ExecutionAttemptModel.id == self.attempt_id,
            ),
            (
                ExecutionAttemptModel,
                "actual_tokens",
                999,
                ExecutionAttemptModel.id == self.attempt_id,
            ),
            (LlmRunModel, "input_tokens", 999, LlmRunModel.id == self.run_id),
            (
                ExecutionBudgetModel,
                "status",
                "unknown",
                ExecutionBudgetModel.scope_id == self.inputs["execution_scope_id"],
            ),
        ]:
            with self.subTest(table=model.__tablename__, field=field):
                nested = await self.session.begin_nested()
                try:
                    await self.session.execute(update(model).where(clause).values(**{field: value}))
                    with self.assertRaises(ValueError):
                        await self.load()
                finally:
                    await nested.rollback()
        meta = deepcopy(self.source["native"]["llm_run"]["metadata_json"])
        meta["execution_recovery"] = {"schema": "execution-recovery.v1"}
        await self.session.execute(
            update(LlmRunModel).where(LlmRunModel.id == self.run_id).values(metadata_json=meta)
        )
        with self.assertRaises(ValueError):
            await self.load()

    async def test_current_corruption_and_multiple_artifacts_never_fall_back(self):
        for fault in ("payload", "raw", "report", "receipt", "binding"):
            with self.subTest(fault=fault):
                nested = await self.session.begin_nested()
                try:

                    def mutate(saved):
                        if fault == "payload":
                            saved["request_payload"] = None
                        elif fault == "raw":
                            saved["raw_response"] += " "
                        elif fault == "report":
                            saved["original_report"]["notes"] = ["forged"]
                        elif fault == "receipt":
                            saved["provider_receipt"]["llm_run_id"] = str(uuid4())
                        else:
                            saved["request_payload"]["binding_sha256"] = "0" * 64

                    await self.mutate_saved(mutate)
                    with self.assertRaises((ValueError, KeyError)):
                        await self.load()
                finally:
                    await nested.rollback()
        self.session.add(
            PlanningArtifactVersionModel(
                id=uuid4(),
                project_id=self.project_id,
                artifact_type="fanqie_whole_semantic_review",
                status="validated",
                schema_version="4",
                version_no=2,
                content=deepcopy(self.saved),
                source_run_id=self.workflow_id,
            )
        )
        await self.session.flush()
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            await self.load()

    async def test_historical_body_or_title_ignored_but_current_stale_binding_blocks(self):
        for version, field in enumerate(("full_text", "title"), 2):
            old = deepcopy(self.saved)
            old["request_payload"][field] += "historical"
            self.session.add(
                PlanningArtifactVersionModel(
                    id=uuid4(),
                    project_id=self.project_id,
                    artifact_type="fanqie_whole_semantic_review",
                    status="validated",
                    schema_version="4",
                    version_no=version,
                    content=old,
                    source_run_id=None,
                )
            )
        await self.session.flush()
        await self.load()
        for field in ("canonical", "design", "scope", "execution_scope", "manifest", "report"):
            with self.subTest(field=field):

                async def changed(session, project, assembly, source_run_id):
                    inputs = await self.factory(session, project, assembly, source_run_id)
                    if field == "canonical":
                        inputs["approved_contract"]["approval_evidence"][
                            "creation_canonical_snapshot_hash"
                        ] = "0" * 64
                    elif field == "design":
                        inputs["approved_contract"]["book_design_snapshot"]["reader_promise"] = (
                            "changed"
                        )
                    elif field == "scope":
                        inputs["current_scope"] = "new-scope"
                    elif field == "execution_scope":
                        inputs["execution_scope_id"] = "new-execution"
                    elif field == "manifest":
                        inputs["manifest"]["sources"][0]["draft_id"] = str(uuid4())
                    else:
                        inputs["original_report"]["thresholds"]["protected_threshold"] = 99
                    return inputs

                with self.assertRaises(ValueError):
                    await self.load(factory=changed)

    async def test_factory_is_mandatory_and_fresh_contract_change_is_seen(self):
        with self.assertRaisesRegex(ValueError, "factory_required"):
            await self.load(factory=None)
        meta = deepcopy(self.project.metadata_json)
        meta["current_approved_contract"]["book_design_snapshot"]["reader_promise"] = "new current"
        await self.session.execute(
            update(ProjectModel)
            .where(ProjectModel.id == self.project_id)
            .values(metadata_json=meta)
        )
        with self.assertRaises(ValueError):
            await self.load()

    async def test_factory_cannot_drop_native_manifest_fields(self):
        self.assembly.manifest["control_serial"] = 8

        async def dropping(session, project, assembly, source_run_id):
            inputs = await self.factory(session, project, assembly, source_run_id)
            inputs["manifest"].pop("control_serial")
            return inputs

        with self.assertRaisesRegex(ValueError, "native_manifest_fields_missing"):
            await self.load(factory=dropping)

    async def test_status_manifest_and_raw_native_binding_are_strict(self):
        for fault in ("artifact_status", "source_workflow", "manifest", "native_raw", "scope"):
            with self.subTest(fault=fault):
                nested = await self.session.begin_nested()
                try:
                    if fault == "artifact_status":
                        await self.session.execute(
                            update(PlanningArtifactVersionModel)
                            .where(PlanningArtifactVersionModel.id == self.artifact_id)
                            .values(status="draft")
                        )
                    elif fault == "source_workflow":
                        other = uuid4()
                        self.session.add(
                            WorkflowRunModel(
                                id=other,
                                project_id=self.project_id,
                                workflow_type="whole_semantic_review",
                                status="completed",
                                metadata_json={},
                            )
                        )
                        await self.session.flush()
                        await self.session.execute(
                            update(PlanningArtifactVersionModel)
                            .where(PlanningArtifactVersionModel.id == self.artifact_id)
                            .values(source_run_id=other)
                        )
                    elif fault == "manifest":
                        await self.mutate_saved(
                            lambda saved: saved["request_payload"]["manifest"]["sources"][0].update(
                                draft_id=str(uuid4())
                            )
                        )
                    elif fault == "native_raw":
                        root = deepcopy(self.early_root["result_json"])
                        root["content"] += " "
                        await self.session.execute(
                            update(ExecutionOperationModel)
                            .where(ExecutionOperationModel.key == self.root_key)
                            .values(result_json=root)
                        )
                    else:
                        await self.session.execute(
                            update(ExecutionOperationModel)
                            .where(ExecutionOperationModel.key == self.root_key)
                            .values(scope_id="wrong-scope")
                        )
                    with self.assertRaises(ValueError):
                        await self.load()
                finally:
                    await nested.rollback()

    async def test_physical_file_missing_duplicate_nonfinite_and_bad_utf8_block(self):
        ref = self.source["native"]["llm_run"]["response_payload_ref"]
        path = self.base / ref
        original = path.read_bytes()
        for data in (None, b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b"\xff", b"{}"):
            with self.subTest(data=data):
                try:
                    if data is None:
                        path.unlink()
                    else:
                        path.write_bytes(data)
                    with self.assertRaises((ValueError, OSError, KeyError)):
                        await self.load()
                finally:
                    path.write_bytes(original)

    async def test_physical_ref_escape_file_directory_and_root_symlink_block(self):
        for field, ref in (
            ("request_payload_ref", "../escape.json"),
            ("response_payload_ref", f"runtime/llm_payloads/{uuid4()}.response.json"),
        ):
            nested = await self.session.begin_nested()
            try:
                await self.session.execute(
                    update(LlmRunModel).where(LlmRunModel.id == self.run_id).values(**{field: ref})
                )
                with self.assertRaises(ValueError):
                    await self.load()
            finally:
                await nested.rollback()
        response = self.base / self.source["native"]["llm_run"]["response_payload_ref"]
        target = self.base / "other.json"
        target.write_bytes(response.read_bytes())
        response.unlink()
        response.symlink_to(target)
        with self.assertRaises(OSError):
            await self.load()
        response.unlink()
        response.write_bytes(target.read_bytes())
        directory = self.base / "runtime/llm_payloads"
        actual = self.base / "saved-files"
        directory.rename(actual)
        directory.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(OSError):
            await self.load()
        directory.unlink()
        actual.rename(directory)
        link = self.base / "root-link"
        link.symlink_to(self.base, target_is_directory=True)
        self.settings.artifact_store.local_dir = str(link)
        with self.assertRaises(OSError):
            await self.load()


if __name__ == "__main__":
    unittest.main()
