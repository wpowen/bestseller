"""Pure prefix safety checks: fake sessions/driver only, no source proofs or DB IO."""

from contextlib import contextmanager
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from bestseller import settings as app_settings
from bestseller.infra.db import models as db
from support import planning_prefix_capture as prefix

DATABASE = "par_P3_a1b2c3d4_test_db"
HOST = "172.28.0.2"
URL = f"postgresql+asyncpg://postgres@{HOST}:5432/{DATABASE}"


class FakeSession:
    def __init__(self, engine, *, identity=None, active=False):
        self.engine = engine
        self.new, self.dirty, self.deleted = set(), set(), set()
        self.identity = identity or (DATABASE, HOST, 5432, "on")
        self.active = active
        self.queries = []
        self.rollbacks = 0

    def get_bind(self):
        return self.engine

    def in_transaction(self):
        return self.active

    async def execute(self, statement):
        self.queries.append(str(statement))
        self.active = True
        return SimpleNamespace(one=lambda: self.identity)

    async def rollback(self):
        self.rollbacks += 1
        self.active = False


@contextmanager
def target(url=URL, **kwargs):
    # Real URL/dialect metadata; neither the engine nor its pool ever connects.
    engine = create_async_engine(url, **kwargs).sync_engine
    with patch.dict(os.environ, {"BESTSELLER__DATABASE__URL": URL}, clear=True):
        yield FakeSession(engine)


class StagedArtifactSettingsTests(unittest.TestCase):
    def check_scope(self, *, raises):
        key = "BESTSELLER__ARTIFACT_STORE__LOCAL_DIR"
        stage = Path("/private-prefix/par_P3_incomplete_scope_test")
        for previous in (None, "/private-prefix/artifacts"):
            env = {
                "BESTSELLER__DATABASE__URL": "sqlite+aiosqlite:///par_P2_scope_test.db",
                "BESTSELLER__LLM__PLANNER__MODEL": "scope-test-planner",
            }
            if previous is not None:
                env[key] = previous
            with (
                self.subTest(previous_present=previous is not None),
                patch.dict(os.environ, env, clear=True),
                patch.object(
                    app_settings, "DEFAULT_LOCAL_CONFIG_PATH", Path("/unused-scope-local")
                ),
                patch.object(
                    app_settings,
                    "_load_process_env_with_dotenv",
                    side_effect=lambda: dict(os.environ),
                ),
            ):
                app_settings.reset_settings_cache()
                try:
                    before = app_settings.get_settings()
                    before_env = dict(os.environ)
                    failure = ValueError("scope-test-only")
                    try:
                        with prefix._staged_artifact_settings(stage):
                            self.assertEqual(os.environ[key], str(stage))
                            self.assertEqual(app_settings.get_settings.cache_info().currsize, 0)
                            direct = app_settings.load_settings()
                            cached = app_settings.get_settings()
                            self.assertIsNot(cached, before)
                            self.assertEqual(direct.artifact_store.local_dir, str(stage))
                            self.assertEqual(cached.artifact_store.local_dir, str(stage))
                            self.assertEqual(cached.llm.planner.model, "scope-test-planner")
                            self.assertEqual(cached.database.url, before.database.url)
                            if raises:
                                raise failure
                    except ValueError as error:
                        self.assertTrue(raises)
                        self.assertIs(error, failure)
                    else:
                        self.assertFalse(raises)
                    self.assertEqual(dict(os.environ), before_env)
                    self.assertEqual(app_settings.get_settings.cache_info().currsize, 0)
                    restored = app_settings.get_settings()
                    self.assertIsNot(restored, before)
                    self.assertEqual(
                        restored.artifact_store.local_dir, before.artifact_store.local_dir
                    )
                    self.assertEqual(restored.llm.planner.model, "scope-test-planner")
                finally:
                    app_settings.reset_settings_cache()

    def test_stage_scope_restores_environment_and_settings_cache_on_success(self):
        self.check_scope(raises=False)

    def test_stage_scope_restores_environment_and_settings_cache_on_error(self):
        self.check_scope(raises=True)


class TargetGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_target_is_probed_readonly_and_transaction_ends(self):
        with target() as session:
            await prefix.guard_prefix_target(session)
        self.assertEqual(session.rollbacks, 1)
        self.assertFalse(session.active)
        self.assertEqual(session.queries[0], "SET TRANSACTION READ ONLY")
        self.assertIn("statement_timeout", session.queries[1])
        self.assertIn("current_database()", session.queries[2])
        self.assertIn("host(inet_server_addr())", session.queries[2])
        self.assertIn("inet_server_port()", session.queries[2])
        self.assertFalse(any("INSERT" in sql for sql in session.queries))

    async def test_unsafe_urls_rejected_before_any_connection(self):
        cases = (
            URL.replace(DATABASE, "bestseller"),
            URL.replace("par_P3_", "par_P2_"),
            URL.replace(DATABASE, "par_P3_nothex_test_db"),
            URL.replace(HOST, "172.28.0.3"),
            URL.replace(":5432/", ":5433/"),
            URL + "?host=172.28.0.3",
            URL + "?port=5433",
            URL + "?database=bestseller",
            URL + "?service=production",
            URL + "?options=-csearch_path%3Dother",
        )
        for url in cases:
            with self.subTest(url_index=cases.index(url)), target(url) as session:
                with self.assertRaises(prefix.PrefixError):
                    await prefix.guard_prefix_target(session)
                self.assertEqual(session.queries, [])
                self.assertEqual(session.rollbacks, 0)

    async def test_missing_or_unsafe_selection_rejected_before_connect(self):
        for selection in (None, URL.replace(DATABASE, "production"), URL + "?host=other"):
            with self.subTest(selection_index=str(selection is None)), target() as session:
                if selection is None:
                    os.environ.pop("BESTSELLER__DATABASE__URL")
                else:
                    os.environ["BESTSELLER__DATABASE__URL"] = selection
                with self.assertRaises(prefix.PrefixError):
                    await prefix.guard_prefix_target(session)
                self.assertEqual(session.queries, [])

    async def test_actual_server_mismatch_or_non_readonly_rejected_and_rolled_back(self):
        for identity in (
            ("production", HOST, 5432, "on"),
            (DATABASE, "172.28.0.3", 5432, "on"),
            (DATABASE, HOST, 5433, "on"),
            (DATABASE, HOST, 5432, "off"),
        ):
            with self.subTest(identity_index=identity[-1]), target() as session:
                session.identity = identity
                with self.assertRaises(prefix.PrefixError):
                    await prefix.guard_prefix_target(session)
                self.assertEqual(session.rollbacks, 1)

    async def test_existing_pg_transaction_or_pending_state_rejected_before_probe(self):
        for state in ("active", "new", "dirty", "deleted"):
            with self.subTest(state=state), target() as session:
                setattr(session, state, True if state == "active" else {object()})
                with self.assertRaises(prefix.PrefixError):
                    await prefix.guard_prefix_target(session)
                self.assertEqual(session.queries, [])
                self.assertEqual(session.rollbacks, 0)

    async def test_native_engine_nonrouting_server_settings_are_accepted(self):
        from bestseller.infra.db.session import create_engine

        settings = SimpleNamespace(
            database=SimpleNamespace(
                url=URL,
                echo_sql=False,
                pool_size=1,
                max_overflow=0,
                pool_timeout_seconds=5,
                pool_recycle_seconds=1800,
                application_name="par_P3_native_driver_test",
                statement_timeout_ms=30000,
                lock_timeout_ms=1000,
            )
        )
        engine = create_engine(settings).sync_engine
        with patch.dict(os.environ, {"BESTSELLER__DATABASE__URL": URL}, clear=True):
            session = FakeSession(engine)
            await prefix.guard_prefix_target(session)
        self.assertEqual(session.rollbacks, 1)
        self.assertIn("current_database()", session.queries[-1])

    async def test_server_settings_cannot_hide_routing_or_sql_options(self):
        for value in (
            {"application_name": "par_P3", "search_path": "other"},
            {"application_name": "par_P3\0search_path\0other"},
            {"options": "-csearch_path=other"},
            {"statement_timeout": "1; SET search_path=other"},
            {"statement_timeout": 1000},
            {"role": "other"},
            "not-a-dict",
        ):
            with (
                self.subTest(index=str(type(value))),
                target(connect_args={"server_settings": value}) as session,
            ):
                with self.assertRaises(prefix.PrefixError):
                    await prefix.guard_prefix_target(session)
                self.assertEqual(session.queries, [])

    async def test_connect_args_redirect_rejected_before_probe(self):
        for args in (
            {"host": "172.28.0.3"},
            {"port": 5433},
            {"database": "production"},
            {"dsn": "redacted"},
            {"server_settings": {"search_path": "other"}},
        ):
            with self.subTest(keys=tuple(args)), target(connect_args=args) as session:
                with self.assertRaises(prefix.PrefixError):
                    await prefix.guard_prefix_target(session)
                self.assertEqual(session.queries, [])

    async def test_opaque_creator_rejected_before_probe(self):
        for kwargs in ({"creator": lambda: None}, {"async_creator": AsyncMock()}):
            with target(**kwargs) as session:
                with self.assertRaises(prefix.PrefixError):
                    await prefix.guard_prefix_target(session)
                self.assertEqual(session.queries, [])

    async def test_sqlite_keeps_original_p2_policy_and_never_probes(self):
        for url in ("sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite:///par_P2_prefix_test.db"):
            with target(url) as session:
                session.active = True  # P2 callers may already own a transaction.
                await prefix.guard_prefix_target(session)
                self.assertEqual(session.queries, [])
        with target("sqlite+aiosqlite:///par_P3_wrong_test.db") as session:
            with self.assertRaisesRegex(prefix.PrefixError, "isolated_sqlite_required"):
                await prefix.guard_prefix_target(session)

    async def test_private_pg_root_rejected_before_probe_or_bundle_access(self):
        with target() as session:
            settings = SimpleNamespace(
                artifact_store=SimpleNamespace(mode="local", local_dir="reports/prefix")
            )
            with self.assertRaisesRegex(prefix.PrefixError, "private_payload_root"):
                await prefix.import_prefix(session, settings, {}, expected_sha256="untrusted")
            self.assertEqual(session.queries, [])

    async def test_prefix_error_keeps_fixed_legacy_args_and_machine_code(self):
        error = prefix.PrefixError("source_incomplete")
        self.assertEqual(error.args, ("source_incomplete",))
        self.assertEqual(error.code, "prefix_source_incomplete")

    async def test_native_failure_retains_real_business_cause(self):
        # The native reader runs unchanged and fails on an invalid session.
        # This is no plan fixture and supplies no approval/materialization proof.
        with self.assertRaises(prefix.PrefixError) as caught:
            await prefix._native(None, None, None)
        self.assertIsNotNone(caught.exception.__cause__)
        self.assertIsInstance(caught.exception.__cause__, AttributeError)
        self.assertEqual(caught.exception.code, "prefix_source_incomplete")

    async def test_driver_event_refuses_late_redirect_before_connect(self):
        with target() as session, prefix._pg_routing(session, make_url(URL)):
            params = {"host": HOST, "port": 5432, "database": DATABASE, "user": "postgres"}
            callbacks = [
                fn
                for fn in session.engine.dialect.dispatch.do_connect
                if fn.__module__ == prefix.__name__
            ]
            self.assertEqual(len(callbacks), 1)
            callback = callbacks[0]
            callback(session.engine.dialect, None, [], params)
            params["database"] = "production"
            with self.assertRaisesRegex(prefix.PrefixError, "pg_driver_redirect_forbidden"):
                callback(session.engine.dialect, None, [], params)

    async def test_pool_checkout_readonly_probe_rejects_rerouted_handles(self):
        for identity in ((DATABASE, HOST, 5432, "on"), ("production", HOST, 5432, "on")):
            with target() as session, prefix._pg_routing(session, make_url(URL)):
                cursor = Mock()
                cursor.fetchone.return_value = identity
                connection = Mock()
                connection.cursor.return_value = cursor
                callbacks = [
                    fn
                    for fn in session.engine.pool.dispatch.checkout
                    if fn.__module__ == prefix.__name__
                ]
                self.assertEqual(len(callbacks), 1)
                callback = callbacks[0]
                if identity[0] == DATABASE:
                    callback(connection, None, None)
                else:
                    with self.assertRaisesRegex(prefix.PrefixError, "pg_target_identity"):
                        callback(connection, None, None)
                self.assertEqual(
                    cursor.execute.call_args_list[0].args, ("SET TRANSACTION READ ONLY",)
                )
                cursor.close.assert_called_once()
                connection.rollback.assert_called_once()

    async def test_unsafe_machine_code_cannot_expose_text(self):
        error = prefix.PrefixError("sensitive free text")
        self.assertEqual(error.args, ("invalid_error_code",))
        self.assertEqual(error.code, "prefix_invalid_error_code")

    async def test_story_secret_column_is_narrow_and_still_scanned_for_credentials(self):
        prefix._no_credentials({"tables": {"characters": [{"secret": "测试用人物故事设定"}]}})
        for value in (
            {"secret": "story-only"},
            {"tables": {"characters": [{"metadata_json": {"secret": "story-only"}}]}},
            {"tables": {"characters": [{"secret": "Bearer private-value"}]}},
        ):
            with self.assertRaises(prefix.PrefixError):
                prefix._no_credentials(value)


class ParentClosureTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_referenced_planning_parents_are_read_with_original_ids(self):
        project, volume, character, unrelated = uuid4(), uuid4(), uuid4(), uuid4()
        records = {
            (db.VolumeModel, volume): db.VolumeModel(
                id=volume, project_id=project, status="completed", metadata_json={}
            ),
            (db.CharacterModel, character): db.CharacterModel(
                id=character, project_id=project, metadata_json={}
            ),
            (db.CharacterModel, unrelated): db.CharacterModel(id=unrelated, project_id=project),
        }
        reader = SimpleNamespace(
            get=AsyncMock(side_effect=lambda model, key: records[(model, key)])
        )
        rows = {name: [] for name in prefix.TABLE_MODELS}
        rows["chapters"] = [
            {"volume_id": str(volume), "pov_character_id": str(character)},
            {"volume_id": str(volume), "pov_character_id": None},
        ]
        await prefix.capture_planning_parents(reader, rows, project_id=project)
        self.assertEqual(reader.get.await_count, 2)
        self.assertEqual([r["id"] for r in rows["volumes"]], [str(volume)])
        self.assertEqual([r["id"] for r in rows["characters"]], [str(character)])
        self.assertEqual(rows["volumes"][0]["status"], "planned")
        self.assertNotIn(str(unrelated), str(rows["characters"]))
        self.assertFalse({"chapter_draft_versions", "review_reports", "canon_facts"} & rows.keys())
        # Current Character schema has no Faction FK; do not import unrelated factions.
        faction_edges = [
            fk
            for fk in db.CharacterModel.__table__.foreign_keys
            if fk.column.table is db.FactionModel.__table__
        ]
        self.assertEqual("factions" in prefix.TABLE_MODELS, bool(faction_edges))

    async def test_missing_or_foreign_parent_fails_closed(self):
        project, key = uuid4(), uuid4()
        for parent in (None, db.VolumeModel(id=key, project_id=uuid4())):
            reader = SimpleNamespace(get=AsyncMock(return_value=parent))
            rows = {name: [] for name in prefix.TABLE_MODELS}
            rows["chapters"] = [{"volume_id": str(key), "pov_character_id": None}]
            with self.assertRaisesRegex(prefix.PrefixError, "planning_parent_closure"):
                await prefix.capture_planning_parents(reader, rows, project_id=project)

    async def test_no_parent_reference_requires_no_new_source_table_query(self):
        reader = SimpleNamespace(get=AsyncMock())
        rows = {name: [] for name in prefix.TABLE_MODELS}
        rows["chapters"] = [{"volume_id": None, "pov_character_id": None}]
        await prefix.capture_planning_parents(reader, rows, project_id=uuid4())
        reader.get.assert_not_awaited()

    async def test_parent_foreign_keys_are_included_in_import_order(self):
        order = prefix.IMPORT_ORDER
        for name in order:
            for fk in inspect(prefix.TABLE_MODELS[name]).local_table.foreign_keys:
                parent = fk.column.table.name
                self.assertIn(parent, order)
                self.assertLess(order.index(parent), order.index(name))


class EmptyTargetTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, names, counts, *, dialect="postgresql", columns=None):
        inspector = SimpleNamespace(
            get_table_names=lambda **kw: names,
            get_schema_names=lambda: ["public"],
            get_columns=lambda name, **kw: [{"name": c} for c in (columns or ["version_num"])],
        )
        connection = SimpleNamespace(run_sync=AsyncMock(side_effect=lambda fn: fn(None)))
        session = SimpleNamespace(
            get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name=dialect)),
            connection=AsyncMock(return_value=connection),
            scalar=AsyncMock(side_effect=lambda query: counts[query.get_final_froms()[0].name]),
        )
        with patch.object(prefix, "inspect", return_value=inspector):
            await prefix.require_empty_prefix_target(session)

    async def test_only_pg_alembic_metadata_may_be_nonempty(self):
        await self.check(["projects", "alembic_version"], {"projects": 0, "alembic_version": 1})
        for table in ("projects", "chapter_draft_versions", "execution_budgets", "book_controls"):
            with (
                self.subTest(table=table),
                self.assertRaisesRegex(prefix.PrefixError, "target_not_empty"),
            ):
                await self.check(["alembic_version", table], {"alembic_version": 1, table: 1})

    async def test_nondefault_schema_cannot_hide_business_rows_or_an_alembic_impostor(self):
        for name in ("drafts", "alembic_version"):
            inspector = SimpleNamespace(
                get_schema_names=lambda: ["public", "private_business", "information_schema"],
                get_table_names=lambda schema, name=name: (
                    [name] if schema == "private_business" else []
                ),
            )
            connection = SimpleNamespace(run_sync=AsyncMock(side_effect=lambda fn: fn(None)))
            session = SimpleNamespace(
                get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
                connection=AsyncMock(return_value=connection),
                scalar=AsyncMock(return_value=1),
            )
            with patch.object(prefix, "inspect", return_value=inspector):
                with self.assertRaisesRegex(prefix.PrefixError, "target_not_empty"):
                    await prefix.require_empty_prefix_target(session)
            query = session.scalar.call_args.args[0]
            self.assertEqual(query.get_final_froms()[0].schema, "private_business")

    async def test_alembic_exception_is_metadata_shape_only_and_pg_only(self):
        with self.assertRaises(prefix.PrefixError):
            await self.check(["alembic_version"], {"alembic_version": 1}, columns=["business"])
        with self.assertRaisesRegex(prefix.PrefixError, "target_not_empty"):
            await self.check(["alembic_version"], {"alembic_version": 1}, dialect="sqlite")


class NativeCastSchemaGuardTests(unittest.TestCase):
    @staticmethod
    def request():
        from bestseller.services.source_bound_planning_submission import planning_submission_tool

        return {
            "schema_version": "llm-payload.v1",
            "kind": "request",
            "logical_role": "planner",
            "prompt_template": "planner_cast_spec",
            "requested": {"tools": [planning_submission_tool("cast_spec")]},
        }

    @staticmethod
    def properties(document):
        return document["requested"]["tools"][0]["function"]["parameters"]["$defs"][
            "CharacterInput"
        ]["properties"]

    def test_exact_native_schema_passes_without_changing_payload_bytes_or_hash(self):
        import hashlib
        import json

        document = self.request()
        before = json.dumps(document, ensure_ascii=False, indent=2).encode()
        prefix._no_credentials(document)
        after = json.dumps(document, ensure_ascii=False, indent=2).encode()
        self.assertEqual(before, after)
        self.assertEqual(hashlib.sha256(before).digest(), hashlib.sha256(after).digest())

    def test_same_named_field_value_is_refused_even_at_schema_path(self):
        document = self.request()
        self.properties(document)["secret"] = "sk-" + "SIMULATIONONLY" * 4
        with self.assertRaises(prefix.PrefixError):
            prefix._no_credentials(document)
        with self.assertRaises(prefix.PrefixError):
            prefix._no_credentials({"secret": "story value"})

    def test_forged_schema_and_exact_definition_at_other_paths_are_refused(self):
        from copy import deepcopy

        for variant in range(8):
            document = self.request()
            if variant == 0:
                self.properties(document)["secret"] = {"anyOf": [{"type": "string"}]}
            elif variant == 1:
                document["requested"]["tools"][0]["function"]["description"] += " changed"
            elif variant == 2:
                document["prompt_template"] = "other_template"
            elif variant == 3:
                document["logical_role"] = "writer"
            elif variant == 4:
                document = {"properties": {"secret": deepcopy(self.properties(document)["secret"])}}
            elif variant == 5:
                document["schema_version"] = "other-schema"
            elif variant == 6:
                document["kind"] = "response"
            else:
                self.properties(document)["api_key"] = "SIMULATIONONLY"
            with self.subTest(variant=variant), self.assertRaises(prefix.PrefixError):
                prefix._no_credentials(document)

    def test_one_native_tool_cannot_authorize_a_second_forged_tool(self):
        from copy import copy

        document = self.request()
        # Shared nested objects must not carry authority between tools.
        other = copy(document["requested"]["tools"][0])
        other["function"] = copy(other["function"])
        other["function"]["name"] = "forged_tool"
        document["requested"]["tools"].append(other)
        with self.assertRaises(prefix.PrefixError):
            prefix._no_credentials(document)

    def test_retained_p2_character_story_exception_still_scans_values(self):
        prefix._no_credentials({"tables": {"characters": [{"secret": "测试用人物故事设定"}]}})
        value = "sk-" + "SIMULATIONONLY" * 4
        with self.assertRaises(prefix.PrefixError):
            prefix._no_credentials({"tables": {"characters": [{"secret": value}]}})

    def test_story_value_in_workflow_metadata_is_not_a_schema_exception(self):
        document = {
            "tables": {
                "workflow_runs": [
                    {"metadata_json": {"planning": {"candidate": {"secret": "测试用人物故事设定"}}}}
                ]
            }
        }
        with self.assertRaises(prefix.PrefixError):
            prefix._no_credentials(document)

    def test_suspected_credential_formats_elsewhere_still_fail(self):
        samples = [
            "sk-" + "SIMULATIONONLY" * 4,
            "AKIA" + "X" * 16,
            "ghp_" + "X" * 36,
            "Bearer " + "X" * 32,
            "-----BEGIN PRIVATE KEY-----",
            "sk-ant-" + "SIMULATIONONLY" * 4,
            "xoxb-" + "X" * 32,
            "AIza" + "X" * 35,
        ]
        for index, value in enumerate(samples):
            document = self.request()
            document["note"] = value
            with self.subTest(index=index), self.assertRaises(prefix.PrefixError):
                prefix._no_credentials(document)


class StorySecretGuardTests(unittest.TestCase):
    @staticmethod
    def document(value, *, story=True):
        field = {"secret": value}
        source = {"book_spec": {"content": {"protagonist": field}}} if story else {"audit": field}
        return {
            "tables": {
                "workflow_runs": [
                    {
                        "metadata_json": {
                            "full_outline_revision": {
                                "proposal_receipt": {
                                    "field_contract": {
                                        "source_authority": {"approved_sources": source}
                                    }
                                }
                            }
                        }
                    }
                ]
            }
        }

    def test_story_text_in_embedded_workflow_is_allowed_without_byte_changes(self):
        import hashlib
        import json

        document = self.document("测试人物隐藏了一段往事。")
        before = json.dumps(document, ensure_ascii=False, indent=2).encode()
        prefix._no_credentials(document)
        after = json.dumps(document, ensure_ascii=False, indent=2).encode()
        self.assertEqual(before, after)
        self.assertEqual(hashlib.sha256(before).digest(), hashlib.sha256(after).digest())

    def test_approved_story_structures_and_seven_ascii_boundary(self):
        cases = [
            {"cast": {"secret": "人物的代号为Abc1234。"}},
            {"character": {"hidden_secret": "人物隐藏了一段往事。"}},
            {"tables": {"characters": [{"secret": "人物隐藏了一段往事。"}]}},
            {
                "tables": {
                    "planning_artifact_versions": [
                        {
                            "artifact_type": "cast_spec",
                            "content": {"secret": "人物隐藏了一段往事。"},
                        }
                    ]
                }
            },
        ]
        for index, document in enumerate(cases):
            with self.subTest(index=index):
                prefix._no_credentials(document)

    def test_non_string_non_han_eight_ascii_and_key_formats_are_refused(self):
        values = [
            None,
            42,
            {"text": "人物往事"},
            "story only",
            "",
            "人物Abc12345往事",
            "人物" + "sk-" + "SIMULATIONONLY" * 4,
            "人物Bearer abc",
            "人物-----BEGIN EC PRIVATE KEY-----",
            "人物ya29.abc",
        ]
        for index, value in enumerate(values):
            with self.subTest(index=index), self.assertRaises(prefix.PrefixError):
                prefix._no_credentials(self.document(value))

    def test_authorization_reference_is_not_a_story_attribute_exception(self):
        document = {"book_spec": {"authorization_ref": "测试用人物故事说明。"}}
        with self.assertRaises(prefix.PrefixError):
            prefix._no_credentials(document)

    def test_non_native_schema_defaults_and_han_adjacent_token_are_refused(self):
        cases = [
            {"components": {"schemas": {"cast": {"default": {"secret": "人物隐藏往事。"}}}}},
            {"cast": {"secret": "人物" + "sk-" + "_".join(["abc"] * 8)}},
        ]
        for index, document in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(prefix.PrefixError):
                prefix._no_credentials(document)

    def test_story_value_outside_story_paths_and_under_headers_is_refused(self):
        cases = [
            self.document("人物隐藏了一段往事。", story=False),
            {"cast": {"headers": {"secret": "人物隐藏了一段往事。"}}},
            {"metadata_json": {"secret": "人物隐藏了一段往事。"}},
            {"properties": {"cast": {"secret": "人物隐藏了一段往事。"}}},
        ]
        for index, document in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(prefix.PrefixError):
                prefix._no_credentials(document)


if __name__ == "__main__":
    unittest.main()
