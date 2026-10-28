"""Source discovery exposes no credentials and only constructs a read-only URL."""

import json
import unittest

from e2e_sim.capture_source import connection_parameters, proof_environment


class CaptureSourceTests(unittest.TestCase):
    def test_compose_values_expand_without_logging_or_persisting_credentials(self):
        compose = {
            "services": {
                "db": {
                    "ports": ["${PG_PORT:-55432}:5432"],
                    "environment": {
                        "POSTGRES_USER": "${PG_USER:-reader}",
                        "POSTGRES_PASSWORD": "${PG_PASSWORD}",
                        "POSTGRES_DB": "${PG_DB:-book}",
                    },
                }
            }
        }
        values = connection_parameters(compose, {"PG_PASSWORD": "PRIVATE", "PG_PORT": "55433"})
        self.assertEqual(values["host"], "127.0.0.1")
        self.assertEqual(values["port"], 55433)
        self.assertEqual(values["database"], "book")
        self.assertEqual(values["password"], "PRIVATE")
        self.assertNotIn("PRIVATE", json.dumps({"host": values["host"], "port": values["port"]}))

    def test_nested_reader_uses_the_same_source_store_and_role_configuration(self):
        from types import SimpleNamespace

        role = SimpleNamespace(
            model_dump=lambda **kw: {"model": "source/model", "api_key_env": "MODEL_KEY"}
        )
        settings = SimpleNamespace(
            artifact_store=SimpleNamespace(local_dir="/authorized/store"),
            llm=SimpleNamespace(
                **dict.fromkeys(("planner", "writer", "critic", "summarizer", "editor"), role)
            ),
        )
        result = proof_environment(settings)
        self.assertEqual(result["BESTSELLER__ARTIFACT_STORE__LOCAL_DIR"], "/authorized/store")
        self.assertEqual(json.loads(result["BESTSELLER__LLM__PLANNER"])["model"], "source/model")
        self.assertEqual(result["BESTSELLER__LLM__MOCK"], "false")

    def test_role_export_preserves_parameters_but_rejects_credential_urls(self):
        from types import SimpleNamespace

        from e2e_sim.capture_source import sanitized_role_configuration

        class Role:
            def model_dump(self, **kw):
                return {
                    "model": "source/model",
                    "api_base": "https://provider.invalid/v1",
                    "api_key_env": "MODEL_KEY",
                    "stream": False,
                    "max_tokens": 1234,
                }

        role = Role()
        settings = SimpleNamespace(
            llm=SimpleNamespace(
                **dict.fromkeys(("planner", "writer", "critic", "summarizer", "editor"), role)
            )
        )
        value = sanitized_role_configuration(settings)
        self.assertEqual(value["api_key_env_names"], ["MODEL_KEY"])
        self.assertEqual(value["roles"]["writer"]["max_tokens"], 1234)
        role.model_dump = lambda **kw: {"api_base": "https://u:PRIVATE@provider.invalid/v1"}
        with self.assertRaisesRegex(RuntimeError, "credential_url"):
            sanitized_role_configuration(settings)


class PrivateOutputPathTests(unittest.TestCase):
    def test_resolved_new_child_is_allowed_without_creating_it(self):
        from pathlib import Path
        import tempfile

        from e2e_sim.capture_source import validate_private_output

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "private"
            root.mkdir(mode=0o700)
            destination = root / "new-capture"
            self.assertEqual(validate_private_output(destination, root), destination.resolve())
            self.assertFalse(destination.exists())

    def test_dotdot_outside_existing_and_symlink_paths_are_refused_before_write(self):
        from pathlib import Path
        import tempfile

        from e2e_sim.capture_source import validate_private_output

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root, outside = base / "private", base / "outside"
            root.mkdir(mode=0o700)
            outside.mkdir()
            (root / "escape").symlink_to(outside, target_is_directory=True)
            (root / "inside").mkdir()
            (root / "inside-link").symlink_to(root / "inside", target_is_directory=True)
            (root / "broken").symlink_to(outside / "not-created", target_is_directory=True)
            cases = [
                root / ".." / "outside" / "capture",
                root / "escape" / "capture",
                root / "inside-link" / "capture",
                root / "broken" / "capture",
                root / "inside",
                outside / "capture",
                root,
            ]
            for number, path in enumerate(cases):
                with self.subTest(case=number), self.assertRaises(ValueError):
                    validate_private_output(path, root)
            self.assertEqual(list(outside.iterdir()), [])

    def test_cli_refuses_traversal_with_safe_stack_before_capture(self):
        from contextlib import redirect_stdout
        import io
        from pathlib import Path
        import tempfile
        from unittest.mock import AsyncMock, patch

        from e2e_sim import capture_source

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = base / "private"
            root.mkdir(mode=0o700)
            report = base / "safe.json"
            invoked = AsyncMock()
            argv = [
                "capture_source",
                "--profile",
                str(root / "missing.json"),
                "--output",
                str(root / ".." / "escaped"),
                "--report",
                str(report),
            ]
            with (
                patch.object(capture_source, "PRIVATE_ROOT", root),
                patch.object(capture_source, "capture", invoked),
                patch("sys.argv", argv),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(capture_source.main(), 1)
            invoked.assert_not_called()
            proof = json.loads(report.read_text())
            self.assertTrue(proof["safe_stack"])
            self.assertEqual(proof["error_type"], "ValueError")
            self.assertFalse((base / "escaped").exists())

    def test_symlink_private_root_is_not_authority(self):
        from pathlib import Path
        import tempfile

        from e2e_sim.capture_source import validate_private_output

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            real = base / "real"
            real.mkdir(mode=0o700)
            alias = base / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaises(ValueError):
                validate_private_output(alias / "capture", alias)


class IndependentSourceProfileTests(unittest.TestCase):
    def test_profile_is_external_private_and_never_needs_a_main_workspace(self):
        from pathlib import Path
        import tempfile
        from unittest.mock import patch

        from e2e_sim import capture_source

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            path = root / "capture-profile.json"
            profile = {
                "connection": {
                    "host": "127.0.0.1",
                    "port": 55432,
                    "database": "source",
                    "username": "reader",
                    "password": "PRIVATE",
                },
                "artifact_root": "/authorized/source/artifacts",
                "runtime_environment": {"MODEL_API_KEY": "PRIVATE"},
                "roles": {},
            }
            path.write_text(json.dumps(profile))
            path.chmod(0o600)
            with patch.object(capture_source, "PRIVATE_ROOT", root):
                actual = capture_source.load_capture_profile(path)
            self.assertEqual(actual["connection"]["port"], 55432)
            self.assertEqual(actual["runtime_environment"]["MODEL_API_KEY"], "PRIVATE")

    def test_profile_refuses_main_environment_paths_symlinks_and_unsafe_env_keys(self):
        from pathlib import Path
        import tempfile
        from unittest.mock import patch

        from e2e_sim import capture_source

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            path = root / "capture-profile.json"
            normal = {
                "connection": {
                    "host": "127.0.0.1",
                    "port": 55432,
                    "database": "source",
                    "username": "reader",
                    "password": "PRIVATE",
                },
                "artifact_root": "/authorized/source/artifacts",
                "roles": {},
            }
            with patch.object(capture_source, "PRIVATE_ROOT", root):
                for change in (
                    {"source_root": "/forbidden/main"},
                    {"runtime_environment": {"PYTHONPATH": "/forbidden/main"}},
                    {"artifact_root": str(capture_source.REPO.parents[2] / "artifacts")},
                ):
                    path.write_text(json.dumps({**normal, **change}))
                    path.chmod(0o600)
                    with self.subTest(fields=list(change)), self.assertRaises(ValueError):
                        capture_source.load_capture_profile(path)
                restricted = root / "bestseller-v3-private-denied"
                restricted.mkdir()
                artifact_alias = root / "artifact-alias"
                artifact_alias.symlink_to(restricted, target_is_directory=True)
                path.write_text(json.dumps({**normal, "artifact_root": str(artifact_alias)}))
                path.chmod(0o600)
                with self.assertRaises(ValueError):
                    capture_source.load_capture_profile(path)
                path.write_text(json.dumps(normal))
                path.chmod(0o600)
                alias = root / "alias.json"
                alias.symlink_to(path)
                with self.assertRaises(ValueError):
                    capture_source.load_capture_profile(alias)


class NestedReaderArtifactRootTests(unittest.TestCase):
    def test_native_nested_reader_loads_verified_payload_from_explicit_root(self):
        from pathlib import Path
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch
        from uuid import uuid4

        from bestseller.services.retained_planning_generation import _file
        from bestseller.settings import load_settings, reset_settings_cache
        from e2e_sim.capture_source import install_proof_environment

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            payload = root / "request.json"
            run = SimpleNamespace(
                id=uuid4(),
                workflow_run_id=uuid4(),
                project_id=uuid4(),
                logical_role="planner",
                request_payload_ref="request.json",
            )
            document = {
                "schema_version": "llm-payload.v1",
                "kind": "request",
                "replayable": True,
                "replayability_reasons": [],
                "llm_run_id": str(run.id),
                "workflow_run_id": str(run.workflow_run_id),
                "project_id": str(run.project_id),
                "logical_role": run.logical_role,
            }
            payload.write_text(json.dumps(document))
            settings = load_settings(env={})
            settings.artifact_store.local_dir = str(root)
            with patch.dict("os.environ", {}, clear=True):
                reset_settings_cache()
                install_proof_environment(settings, {})
                # This is the real retained-file reader and a fresh settings
                # load as used by native nested callbacks, not a proof mock.
                actual, digest = _file(load_settings(), run, "request")
                self.assertEqual(actual, document)
                self.assertEqual(len(digest), 64)
                reset_settings_cache()


class AuthorizedSourceWorkspaceTests(unittest.TestCase):
    def test_explicit_source_configuration_stays_in_memory(self):
        from pathlib import Path
        import tempfile

        from e2e_sim.capture_source import load_authorized_source

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / "source"
            source.mkdir()
            (source / "config").mkdir()
            (source / ".env").write_text("PG_PASSWORD=PRIVATE\nMODEL_API_KEY=PRIVATE\n")
            (source / "docker-compose.yml").write_text(
                'services:\n  db:\n    ports: ["55432:5432"]\n    environment:\n'
                "      POSTGRES_USER: reader\n      POSTGRES_PASSWORD: ${PG_PASSWORD}\n"
                "      POSTGRES_DB: book\n"
            )
            (source / "config/default.yaml").write_text("llm: {}\n")
            artifact = root / "artifacts"
            artifact.mkdir()
            from unittest.mock import patch

            with patch("e2e_sim.capture_source.REPO", source):
                result = load_authorized_source(source, artifact)
            self.assertEqual(result["connection"]["password"], "PRIVATE")
            self.assertEqual(result["runtime_environment"]["MODEL_API_KEY"], "PRIVATE")
            self.assertEqual(result["config_path"], source / "config/default.yaml")
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["artifacts", "source"])

    def test_declared_custom_and_fallback_credentials_are_cleared(self):
        import os
        from unittest.mock import patch

        from bestseller.settings import load_settings, reset_settings_cache
        from e2e_sim.capture_source import install_proof_environment

        settings = load_settings(env={})
        settings.llm.planner.api_key_env = "CUSTOM_SECRET"
        settings.llm.planner.rate_limit_fallback_api_key_env = "FALLBACK_SECRET"
        with patch.dict(
            os.environ, {"CUSTOM_SECRET": "PRIVATE", "FALLBACK_SECRET": "PRIVATE"}, clear=True
        ):
            install_proof_environment(settings, {})
            self.assertNotIn("CUSTOM_SECRET", os.environ)
            self.assertNotIn("FALLBACK_SECRET", os.environ)
            reset_settings_cache()


class SourceRuntimeFreezeTests(unittest.TestCase):
    def test_native_active_profile_is_frozen_before_nested_readers(self):
        import os
        from pathlib import Path
        import tempfile
        from unittest.mock import patch

        from bestseller.settings import load_settings, reset_settings_cache
        from e2e_sim.capture_source import freeze_source_runtime, install_proof_environment

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "runtime").mkdir()
            (root / "runtime/llm-runtime-profile.json").write_text('{"active_profile":"minimax"}')
            settings = load_settings(env={})
            settings.artifact_store.local_dir = str(root)
            settings.llm.critic.max_tokens = 16384
            with patch.dict(
                os.environ,
                {"BESTSELLER_LLM_RUNTIME_PROFILE_PATH": str(root / "wrong.json")},
                clear=True,
            ):
                effective = freeze_source_runtime(settings)
                self.assertEqual(effective.llm.critic.max_tokens, 32768)
                install_proof_environment(effective, {})
                nested = load_settings()
                self.assertEqual(nested.llm.critic.max_tokens, 32768)
                self.assertEqual(nested.llm.critic.model, effective.llm.critic.model)
                reset_settings_cache()


class CredentialRejectionDiagnosticTests(unittest.TestCase):
    def test_rejection_keeps_hash_only_field_and_path_diagnostics(self):
        from e2e_sim.diagnostics import safe_exception
        from support.planning_prefix_capture import PrefixError, _no_credentials

        payload = {"private_prompt": [{"api_key_env": "PRIVATE_VALUE"}]}
        with self.assertRaises(PrefixError) as caught:
            _no_credentials(payload)
        proof = safe_exception(caught.exception)
        diagnostic = proof["credential_rejection"]
        self.assertEqual(diagnostic["field_category"], "api_key_env")
        self.assertEqual(diagnostic["path_depth"], 3)
        self.assertEqual(len(diagnostic["path_sha256"]), 64)
        self.assertNotIn("PRIVATE_VALUE", json.dumps(proof))
        self.assertNotIn("private_prompt", json.dumps(proof))
