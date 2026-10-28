"""Pure runner safety tests; run with unittest, without pytest/DB fixtures."""

from contextlib import redirect_stdout
import hashlib
import io
import json
import logging
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

try:
    from . import boot, run
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from e2e_sim import boot, run


ORIGINAL_ROOT = Path(__file__).resolve().parents[2]
ROOT = ORIGINAL_ROOT


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The production P3 runner must continue rejecting int/full-flow. Its
        # unit tests use a separate real Git layout instead of borrowing P3's
        # checkout, private directory or Docker resources.
        global ROOT
        cls.fixture_dir=tempfile.TemporaryDirectory(dir="/private/tmp")
        base=Path(cls.fixture_dir.name);ROOT=base/"P3";ROOT.mkdir()
        subprocess.run(["git","init","--quiet","--separate-git-dir",str(base/"git-metadata"),str(ROOT)],check=True)
        for name in ("config", "src", "migrations"):
            shutil.copytree(ORIGINAL_ROOT/name,ROOT/name)
        shutil.copytree(ORIGINAL_ROOT/"tests/e2e_sim",ROOT/"tests/e2e_sim")
        shutil.copytree(ORIGINAL_ROOT/"tests/support",ROOT/"tests/support")
        for name in ("pyproject.toml", "Dockerfile", "alembic.ini"):
            shutil.copy2(ORIGINAL_ROOT/name,ROOT/name)
        (ROOT/"reports/parallel-2026-10-28/P3").mkdir(parents=True)
        subprocess.run(["git","add","--","config"],cwd=ROOT,check=True)
        subprocess.run(["git","-c","user.name=Unit fixture","-c","user.email=fixture@invalid.test","commit","--quiet","-m","Fixture config"],cwd=ROOT,check=True)
    @classmethod
    def tearDownClass(cls):
        global ROOT
        ROOT=ORIGINAL_ROOT
        cls.fixture_dir.cleanup()
    def setUp(self):
        self.private_dir=tempfile.TemporaryDirectory(dir="/private/tmp")
        self.private_patch=patch.object(run,"PRIVATE_ROOT",Path(self.private_dir.name))
        self.private_patch.start()
        self.addCleanup(self.private_patch.stop)
        self.addCleanup(self.private_dir.cleanup)
    def test_real_integration_tree_stays_forbidden_to_the_P3_runner(self):
        with self.assertRaisesRegex(ValueError,"isolated P3 Git worktree"):
            run.Plan(ORIGINAL_ROOT)
    def plan(self, **kwargs):
        kwargs.setdefault("prefix_bundle", run.PRIVATE_ROOT / "capture/prefix.json")
        kwargs.setdefault("prefix_sha256", "a" * 64)
        return run.Plan(ROOT, nonce="a1b2c3d4", **kwargs)

    def prefix_receipt(self, runner):
        from uuid import uuid4

        invocation = {
            "schema": "e2e-sim-invocation.v1",
            "invocation_id": str(uuid4()),
            "run_id": runner.plan.prefix,
            "trusted_bundle_sha256": runner.plan.prefix_sha256,
        }
        tables = (
            "ChapterDraftVersionModel",
            "SceneDraftVersionModel",
            "ReviewReportModel",
            "QualityScoreModel",
            "DraftPromotionDecisionModel",
            "RewriteTaskModel",
            "CanonFactModel",
            "TimelineEventModel",
            "ChapterStateSnapshotModel",
            "ExportArtifactModel",
        )
        return {
            "status": "pass",
            "imported": True,
            "native_reader_passed": True,
            "downstream_empty": True,
            "downstream_rows": dict.fromkeys(tables, 0),
            "trusted_bundle_sha256": runner.plan.prefix_sha256,
            "invocation": invocation,
        }

    def test_readonly_database_probe_normalizes_pg_inet_hostmask(self):
        database = self.plan().database
        cursor = MagicMock()

        def fetchone():
            sql = cursor.execute.call_args.args[0]
            if "current_database()" in sql:
                address = "172.28.0.2" if "host(inet_server_addr())" in sql else "172.28.0.2/32"
                return database, "150004", address
            return ("0.5.1",)

        cursor.fetchone.side_effect = fetchone
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        psycopg = Mock()
        psycopg.connect.return_value = connection
        output = io.StringIO()
        env = {
            "E2E_SIM_DATABASE": database,
            "E2E_SIM_PG_IP": "172.28.0.2",
            "BESTSELLER__DATABASE__URL": f"postgresql+asyncpg://postgres@172.28.0.2:5432/{database}",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch.dict(sys.modules, {"psycopg": psycopg}),
            redirect_stdout(output),
        ):
            boot.verify_database()
        self.assertEqual(
            psycopg.connect.call_args.kwargs["options"], "-c default_transaction_read_only=on"
        )
        self.assertTrue(
            any(
                "host(inet_server_addr())" in call.args[0] for call in cursor.execute.call_args_list
            )
        )
        self.assertNotIn("postgresql", output.getvalue())

    def test_unique_test_database_and_owned_resources(self):
        a, b = run.Plan(ROOT), run.Plan(ROOT)
        self.assertNotEqual(a.prefix, b.prefix)
        self.assertTrue(a.database.startswith("par_P3_"))
        self.assertIn("_test_", a.database)
        self.assertTrue(all(name.startswith(a.prefix + "_") for name in a.names.values()))
        for nonce in ("", "prod", "../../x", "abc;rm", "a" * 64):
            with self.subTest(nonce=nonce), self.assertRaises(ValueError):
                run.Plan(ROOT, nonce=nonce)

    def test_no_pull_publish_credentials_or_writable_source(self):
        plan = self.plan()
        network = plan.network_command()
        self.assertIn("--internal", network)
        for role in ("pg", "redis", "api", "worker", "driver", "migrate", "tape"):
            command = plan.container_command(
                role, "sha256:" + "a" * 64, pg_ip="172.28.0.2", redis_ip="172.28.0.3"
            )
            self.assertEqual(command[:1], ["create"])
            self.assertIn("--pull=never", command)
            self.assertNotIn("--publish", command)
            self.assertNotIn("-p", command)
            self.assertNotIn("--env-file", command)
            self.assertNotIn("--privileged", command)
            self.assertNotIn("--network=host", command)
            for item in command:
                if (
                    item.startswith("type=bind")
                    and "dst=/evidence" not in item
                    and "dst=/private-prefix" not in item
                ):
                    self.assertIn("readonly", item)
                    self.assertNotIn("local.yaml", item)
                    self.assertNotIn(".env", item)
            self.assertIn(plan.labels[0], command)
            self.assertIn(plan.labels[1], command)
            if role in ("pg", "redis"):
                self.assertIn("--tmpfs", command)
            else:
                self.assertIn("--read-only", command)
                self.assertIn("/usr/bin/env", command)
                self.assertIn("-i", command)
                self.assertIn("PYTHONPATH=/app/src:/app/tests", command)
                self.assertIn("/app:rw,nosuid,nodev,size=64m", command)

    def test_invalid_image_and_non_worktree_refused(self):
        for image in ("--privileged", "a b", "a\n--publish=80", ""):
            with self.subTest(image=image), self.assertRaises(ValueError):
                self.plan(app_image=image)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            run.Plan(Path(tmp))

    def test_minimal_docker_env_and_redaction(self):
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "SENSITIVE",
                "DOCKER_CONTEXT": "production",
                "HTTP_PROXY": "http://secret",
                "PGPASSWORD": "secret",
            },
        ):
            env = run.docker_environment(Path("/isolated"))
        self.assertEqual(set(env), {"PATH", "HOME", "DOCKER_CONFIG", "LANG"})
        self.assertEqual(env["HOME"], "/isolated")
        self.assertNotIn("SENSITIVE", json.dumps(env))
        cleaned = run.sanitize("Bearer SECRET postgresql://u:SECRET@127.0.0.1/db api_key=SECRET")
        self.assertNotIn("SECRET", cleaned)
        plan_json = run.sanitize(json.dumps({"env": ["E2E_SIM_KEY=SECRET", "api_key=SECRET"]}))
        self.assertEqual(len(json.loads(plan_json)["env"]), 2)
        self.assertNotIn("SECRET", plan_json)

    def test_low_disk_refuses_before_docker_mutation(self):
        runner = run.Runner(self.plan())
        fake = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        with (
            patch.object(run.shutil, "disk_usage", return_value=Mock(free=run.MIN_FREE_BYTES - 1)),
            patch.object(run.subprocess, "run", fake),
        ):
            with self.assertRaisesRegex(run.InfrastructureError, "disk"):
                runner.docker(["start", runner.plan.names["pg"]], mutation=True)
        fake.assert_not_called()

    def test_cleanup_requires_exact_nonce_label_and_known_name(self):
        runner = run.Runner(self.plan())
        own = self.plan().label_map
        for labels in ({}, {**own, run.NONCE_LABEL: "different"}):
            with self.subTest(labels=labels), self.assertRaises(run.InfrastructureError):
                runner.verify_owned(
                    "container", runner.plan.names["api"], {"Config": {"Labels": labels}}
                )
        runner.verify_owned("container", runner.plan.names["api"], {"Config": {"Labels": own}})
        with self.assertRaises(run.InfrastructureError):
            runner.verify_owned("container", "existing-production", {"Config": {"Labels": own}})

    def test_absence_requires_exact_docker_not_found_error(self):
        runner = run.Runner(self.plan())
        name = runner.plan.names["network"]
        for error in (
            f"Error response from daemon: network {name} not found",
            f"network {name} not found",
            f"No such network: {name}",
        ):
            result = subprocess.CompletedProcess([], 1, "", error)
            with patch.object(runner, "docker", return_value=result):
                self.assertIsNone(runner.inspect("network", name, check=False))
        for error in (
            "cannot connect to Docker daemon",
            "network production not found",
            "No such network: production",
            f"network {name}-other not found",
            f"network {name} not found\ncannot connect to Docker daemon",
        ):
            result = subprocess.CompletedProcess([], 1, "", error)
            with (
                patch.object(runner, "docker", return_value=result),
                self.assertRaises(run.InfrastructureError),
            ):
                runner.inspect("network", name, check=False)

    def test_cleanup_never_lists_or_prunes_and_preserves_foreign_resources(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "reports/parallel-2026-10-28/P3") as tmp:
            runner = run.Runner(self.plan(report_dir=Path(tmp) / "run"))
            runner.created = [
                ("container", runner.plan.names["api"]),
                ("network", runner.plan.names["network"]),
            ]
            calls = []

            def docker(args, **kwargs):
                calls.append(args)
                if args[1:2] == ["inspect"]:
                    labels = runner.plan.label_map if args[0] == "network" else {}
                    body = (
                        {"Labels": labels}
                        if args[0] == "network"
                        else {"Config": {"Labels": labels}}
                    )
                    return subprocess.CompletedProcess(args, 0, json.dumps([body]), "")
                return subprocess.CompletedProcess(args, 0, "", "")

            with patch.object(runner, "docker", side_effect=docker):
                runner.cleanup()
            self.assertFalse(any("prune" in c or "ls" in c for c in calls))
            self.assertFalse(any(c[:2] == ["container", "rm"] for c in calls))
            self.assertTrue(any(c[:2] == ["network", "rm"] for c in calls))
            self.assertTrue(runner.cleanup_errors)

    def test_preflight_failure_writes_sanitized_log_and_junit(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "reports/parallel-2026-10-28/P3") as tmp:
            plan = self.plan(report_dir=Path(tmp) / "run")
            runner = run.Runner(plan)
            with patch.object(
                runner, "execute", side_effect=run.InfrastructureError("preflight Bearer SECRET")
            ):
                self.assertEqual(runner.run(), 2)
            log = (plan.evidence / "run.log").read_text()
            junit = (plan.evidence / "runner-junit.xml").read_text()
            self.assertNotIn("SECRET", log + junit)
            self.assertIn("preflight", log)
            failures = [
                json.loads(line)
                for line in (plan.evidence / "failures-runner.jsonl").read_text().splitlines()
            ]
            self.assertTrue(failures[0]["safe_stack"])
            self.assertEqual(failures[0]["error_type"], "InfrastructureError")
            self.assertIn('errors="1"', junit)

    def test_existing_evidence_is_never_overwritten(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "reports/parallel-2026-10-28/P3") as tmp:
            previous = Path(tmp) / "previous"
            previous.mkdir()
            (previous / "run.log").write_text("original receipt")
            runner = run.Runner(self.plan(report_dir=previous))
            with patch.object(runner, "execute") as execute:
                self.assertEqual(runner.run(), 2)
            execute.assert_not_called()
            self.assertEqual((previous / "run.log").read_text(), "original receipt")
            self.assertNotEqual(runner.plan.evidence, previous)

    def test_low_disk_at_startup_still_reports_failure_without_docker(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "reports/parallel-2026-10-28/P3") as tmp:
            runner = run.Runner(self.plan(report_dir=Path(tmp) / "run"))
            with (
                patch.object(run.shutil, "disk_usage", return_value=Mock(free=0)),
                patch.object(run.subprocess, "run") as command,
            ):
                self.assertEqual(runner.run(), 2)
            command.assert_not_called()
            self.assertIn("InfrastructureError", (runner.plan.evidence / "run.log").read_text())
            self.assertIn('errors="1"', (runner.plan.evidence / "runner-junit.xml").read_text())

    def test_local_socket_only_and_ambient_context_ignored(self):
        with self.assertRaises(ValueError):
            run.Runner(self.plan(), "tcp://production:2375")
        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://production:2375"}):
            runner = run.Runner(self.plan())
        self.assertTrue(runner.docker_host.startswith("unix://"))

    def test_driver_process_stays_unpatched_and_settings_preserve_gates(self):
        env = self.plan().process_environment("172.28.0.2", "172.28.0.3")
        self.assertEqual(env["BESTSELLER__LLM__MOCK"], "false")
        self.assertEqual(env["WORKER_SELF_HEAL"], "0")
        self.assertFalse(any(key.startswith("BESTSELLER__QUALITY__") for key in env))
        self.assertFalse(
            any(
                key.startswith("BESTSELLER__PIPELINE__") and "MATERIAL_LIBRARY" not in key
                for key in env
            )
        )
        self.assertNotIn("transport", run.DRIVER_STARTUP)
        self.assertIn("'e2e_sim.scenarios'", run.DRIVER_STARTUP)

    def test_config_snapshot_preserves_tracked_rules_excludes_local_credentials(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "reports/parallel-2026-10-28/P3") as tmp:
            with (
                tempfile.TemporaryDirectory() as private,
                patch.object(run, "PRIVATE_ROOT", Path(private)),
            ):
                runner = run.Runner(self.plan(report_dir=Path(tmp) / "run"))
                runner.plan.evidence.mkdir()
                paths = (
                    "config/default.yaml\0config/quality_gates.yaml\0"
                    "config/local.yaml\0config/.env\0"
                )
                result = subprocess.CompletedProcess([], 0, paths, "")
                with patch.object(run.subprocess, "run", return_value=result) as command:
                    runner.prepare_config()
                for filename in ("default.yaml", "quality_gates.yaml"):
                    self.assertEqual(
                        (runner.plan.private_namespace / "fixture-config" / filename).read_bytes(),
                        (ROOT / "config" / filename).read_bytes(),
                    )
                self.assertFalse(
                    (runner.plan.private_namespace / "fixture-config/local.yaml").exists()
                )
                self.assertFalse((runner.plan.private_namespace / "fixture-config/.env").exists())
                manifest = json.loads((runner.plan.evidence / "config-manifest.json").read_text())
                self.assertEqual(
                    set(manifest), {"config/default.yaml", "config/quality_gates.yaml"}
                )
                self.assertNotIn("OPENAI_API_KEY", command.call_args.kwargs["env"])

    def test_config_inventory_cannot_traverse_outside_tracked_config(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "reports/parallel-2026-10-28/P3") as tmp:
            runner = run.Runner(self.plan(report_dir=Path(tmp) / "run"))
            result = subprocess.CompletedProcess([], 0, "config/../../private/credentials\0", "")
            with (
                patch.object(run.subprocess, "run", return_value=result),
                self.assertRaises(run.InfrastructureError),
            ):
                runner.prepare_config()

    def test_harness_errors_are_infrastructure_but_native_red_is_failure(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "reports/parallel-2026-10-28/P3") as tmp:
            runner = run.Runner(self.plan(report_dir=Path(tmp) / "run"))
            runner.plan.evidence.mkdir()
            path = runner.plan.evidence / "scenario-output/results.json"
            path.parent.mkdir(exist_ok=True)
            results = [
                {"name": "chapter_adoption", "status": "fail"},
            ]
            path.write_text(json.dumps(results))
            self.assertEqual(runner.scenario_outcome(1), 1)
            results[0]["status"] = "error"
            path.write_text(json.dumps(results))
            with self.assertRaises(run.InfrastructureError):
                runner.scenario_outcome(1)
            results[0]["status"] = "fail"
            path.write_text(json.dumps(results))
            (runner.plan.evidence / "wire-worker.jsonl").write_text(
                '{"event":"harness_UNMAPPED"}\n'
            )
            with self.assertRaises(run.InfrastructureError):
                runner.scenario_outcome(1)
            path.write_text(json.dumps([*results, {"name": "review_length", "status": "pass"}]))
            with self.assertRaises(run.InfrastructureError):
                runner.scenario_outcome(1)

    def test_runner_docker_output_and_cleanup_exceptions_never_copy_text(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "reports/parallel-2026-10-28/P3") as tmp:
            runner = run.Runner(self.plan(report_dir=Path(tmp) / "run"))
            runner.home.mkdir(parents=True)
            raw = "PRIVATE_PROMPT source line secret_body"
            response = subprocess.CompletedProcess([], 1, raw, raw)
            with patch.object(run.subprocess, "run", return_value=response):
                with self.assertRaises(run.InfrastructureError):
                    runner.docker(["wait", runner.plan.names["driver"]])
            runner.created = [("container", runner.plan.names["worker"])]
            with patch.object(runner, "inspect", side_effect=ValueError(raw)):
                runner.cleanup()
            saved = (runner.plan.evidence / "failures-runner.jsonl").read_text()
            self.assertNotIn(
                raw, saved + json.dumps(runner.records) + json.dumps(runner.cleanup_errors)
            )
            failure = json.loads(saved.splitlines()[-1])
            self.assertTrue(failure["safe_stack"])
            self.assertEqual(failure["process_role"], "worker")
            self.assertEqual(failure["phase"], "cleanup")

    def test_boot_logger_captures_actual_traceback_without_formatting_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = boot.SafeFailureHandler(Path(tmp), "worker")
            try:
                raise ValueError("PRIVATE_PROMPT")
            except ValueError as exc:
                record = logging.LogRecord(
                    "bestseller.worker.tasks",
                    logging.ERROR,
                    __file__,
                    1,
                    "PRIVATE_LOG_TEXT",
                    (),
                    (type(exc), exc, exc.__traceback__),
                )
            record.getMessage = Mock(side_effect=AssertionError("must never format record"))
            handler.handle(record)
            record.getMessage.assert_not_called()
            saved = (Path(tmp) / "failures-worker.jsonl").read_text()
            failure = json.loads(saved)
            self.assertNotIn("PRIVATE", saved)
            self.assertTrue(failure["safe_stack"])
            self.assertEqual(failure["logger"], "bestseller.worker.tasks")
            self.assertEqual(
                failure["safe_stack"][-1]["function"],
                "test_boot_logger_captures_actual_traceback_without_formatting_message",
            )

    def test_boot_top_level_crash_is_safe_and_does_not_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(boot, "launch", side_effect=ValueError("PRIVATE_PROMPT")):
                self.assertEqual(boot.main(["worker"], evidence=Path(tmp)), 2)
            saved = (Path(tmp) / "failures-worker.jsonl").read_text()
            self.assertNotIn("PRIVATE_PROMPT", saved)
            self.assertTrue(json.loads(saved)["safe_stack"])

    def test_prefix_role_is_explicit_isolated_and_boot_routes_cli(self):
        plan = self.plan()
        command = plan.container_command(
            "prefix", plan.app_image, pg_ip="172.28.0.2", redis_ip="172.28.0.3"
        )
        self.assertTrue(plan.names["prefix"].startswith(plan.prefix + "_"))
        self.assertEqual(command[-5:], ["python", "-B", "-m", "e2e_sim.boot", "prefix-check"])
        self.assertIn("E2E_SIM_ROLE=prefix", command)
        self.assertIn("tests/support", " ".join(command))
        self.assertIn("--pull=never", command)
        from types import ModuleType

        import e2e_sim.scenarios as scenarios

        seen = []
        fence = ModuleType("e2e_sim.network_fence")
        fence.install_fence = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"E2E_SIM_PG_IP": "172.28.0.2", "E2E_SIM_REDIS_IP": "172.28.0.3"}
            with (
                patch.dict(sys.modules, {"e2e_sim.network_fence": fence}),
                patch.object(scenarios, "main", side_effect=lambda: seen.extend(sys.argv[1:]) or 0),
                patch.dict(os.environ, env),
                patch.object(boot, "verify_database"),
            ):
                self.assertEqual(boot.main(["prefix-check"], evidence=Path(tmp)), 0)
        self.assertEqual(
            seen,
            [
                "--prefix-only",
                "--output",
                "/evidence/prefix-output",
                "--evidence-root",
                "/evidence",
            ],
        )

    def test_prefix_receipt_rejects_other_invocation_even_if_status_says_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = run.Runner(
                self.plan(
                    report_dir=ROOT / "reports/parallel-2026-10-28/P3" / ("unit-" + Path(tmp).name)
                )
            )
            with patch.object(runner.plan, "evidence", Path(tmp)):
                receipt = self.prefix_receipt(runner)
                receipt["invocation"]["run_id"] = "other-run"
                (Path(tmp) / "prefix-check.json").write_text(json.dumps(receipt))
                child = Path(tmp) / "prefix-output"
                child.mkdir()
                (child / "prefix-check.json").write_text(json.dumps(receipt))
                (child / "invocation.json").write_text(json.dumps(receipt["invocation"]))
                with self.assertRaises(run.InfrastructureError):
                    runner.verify_prefix_receipt()

    def test_prefix_receipt_must_pass_before_api_or_worker(self):
        for receipt, permitted in (
            (None, False),
            ({"status": "fail"}, False),
            ({"status": "pass"}, True),
            ({"status": "error"}, False),
        ):
            with (
                self.subTest(receipt=receipt),
                tempfile.TemporaryDirectory(dir=ROOT / "reports/parallel-2026-10-28/P3") as tmp,
            ):
                runner = run.Runner(self.plan(report_dir=Path(tmp) / "run"))
                runner.plan.evidence.mkdir()
                created = []

                def create(
                    role, *_args, created=created, receipt=receipt, runner=runner, **_kwargs
                ):
                    created.append(role)
                    if role == "prefix" and receipt is not None:
                        body = (
                            self.prefix_receipt(runner)
                            if receipt.get("status") == "pass"
                            else receipt
                        )
                        child = runner.plan.evidence / "prefix-output"
                        child.mkdir()
                        (child / "prefix-check.json").write_text(json.dumps(body))
                        (child / "invocation.json").write_text(
                            json.dumps(body.get("invocation", {}))
                        )

                identity = "sha256:" + "a" * 64

                def docker(args, identity=identity, **_kwargs):
                    output = "Redis server v=7.2.1" if "--version" in args else identity
                    return subprocess.CompletedProcess(args, 0, output, "")

                with (
                    patch.object(runner, "prepare_config"),
                    patch.object(runner, "prepare_private_prefix"),
                    patch.object(runner, "verify_prior_round2"),
                    patch.object(runner, "docker", side_effect=docker),
                    patch.object(runner, "inspect", return_value=None),
                    patch.object(runner, "verify_owned"),
                    patch.object(runner, "ready"),
                    patch.object(runner, "ip", return_value="172.28.0.2"),
                    patch.object(runner, "create", side_effect=create),
                    patch.object(runner, "wait", return_value=0),
                ):
                    with self.assertRaises(run.InfrastructureError):
                        runner.execute()  # deliberately no final driver receipt
                self.assertIn("prefix", created)
                self.assertEqual("api" in created, permitted)
                self.assertEqual("worker" in created, permitted)
                self.assertLess(created.index("migrate"), created.index("prefix"))
                self.assertLess(created.index("tape"), created.index("prefix"))

    def test_round_four_marker_and_safe_retry_guard(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "reports/parallel-2026-10-28/P3") as tmp:
            runner = run.Runner(self.plan(report_dir=Path(tmp) / "new-run"))
            with patch.object(runner, "execute", return_value=0), patch.object(runner, "cleanup"):
                self.assertEqual(runner.run(), 0)
            marker = runner.plan.evidence / "round4-run.json"
            self.assertEqual(json.loads(marker.read_text())["round"], 4)
            self.assertFalse((runner.plan.evidence / "round3-run.json").exists())
            marker.write_text(json.dumps({"round": 4, "exit_code": 2}))
            later = run.Runner(self.plan(report_dir=Path(tmp) / "later"))
            with self.assertRaises(run.InfrastructureError):
                later.verify_prior_round2(runner.plan.evidence)
            try:
                raise RuntimeError("prefix_preflight_failed")
            except RuntimeError as exc:
                failure = run.safe_exception(exc)
            (runner.plan.evidence / "failures-runner.jsonl").write_text(json.dumps(failure) + "\n")
            later.verify_prior_round2(runner.plan.evidence)

    def test_retry_guard_requires_safe_frames_for_prior_round2_failure(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "reports/parallel-2026-10-28/P3") as tmp:
            previous = Path(tmp) / "previous"
            previous.mkdir()
            (previous / "round2-run.json").write_text(json.dumps({"round": 2, "exit_code": 2}))
            runner = run.Runner(self.plan(report_dir=Path(tmp) / "next"))
            with self.assertRaises(run.InfrastructureError):
                runner.verify_prior_round2(previous)
            try:
                raise RuntimeError("prefix_validation_failed: PRIVATE_PROMPT")
            except RuntimeError as exc:
                failure = run.safe_exception(exc)
            (previous / "failures-runner.jsonl").write_text(json.dumps(failure) + "\n")
            runner.verify_prior_round2(previous)

    def test_direct_script_import_does_not_need_pythonpath(self):
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tests/e2e_sim/run.py"), "--help"],
            cwd="/",
            env=run.docker_environment(Path("/tmp")),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_private_prefix_mounts_and_retained_outputs_are_not_report_payloads(self):
        plan = self.plan()
        command = plan.container_command("prefix", plan.app_image)
        self.assertIn(
            f"type=bind,src={plan.prefix_bundle},dst=/prefix-input/prefix.json,readonly", command
        )
        self.assertIn(f"type=bind,src={plan.private_namespace},dst=/private-prefix", command)
        env = plan.process_environment("172.28.0.2", "172.28.0.3")
        self.assertEqual(env["E2E_SIM_PREFIX_BUNDLE"], "/prefix-input/prefix.json")
        self.assertEqual(env["E2E_SIM_PREFIX_SHA256"], "a" * 64)
        self.assertEqual(env["BESTSELLER__ARTIFACT_STORE__LOCAL_DIR"], "/private-prefix/artifacts")
        self.assertEqual(env["BESTSELLER__OUTPUT__BASE_DIR"], "/private-prefix/output")
        self.assertEqual(env["E2E_SIM_TAPE"], "/private-prefix/tape.json")
        self.assertNotIn("/evidence/runtime", " ".join(env.values()))
        self.assertIn(
            f"type=bind,src={plan.private_namespace / 'fixture-config'},dst=/app/config,readonly",
            command,
        )

    def test_private_namespace_is_0700_empty_and_manifest_has_hashes_only(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory(dir=ROOT / "reports/parallel-2026-10-28/P3") as reports,
        ):
            private = Path(tmp)
            body = {"tables": {"native_prefix": ["PRIVATE_CAPTURED_BODY"]}}
            raw = json.dumps(
                body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            sha = hashlib.sha256(raw).hexdigest()
            bundle = private / "prefix.json"
            bundle.write_text(json.dumps({**body, "sha256": sha}))
            with patch.object(run, "PRIVATE_ROOT", private):
                runner = run.Runner(
                    self.plan(
                        prefix_bundle=bundle,
                        prefix_sha256=sha,
                        source_roles=self.source_roles(private),
                        report_dir=Path(reports) / "run",
                    )
                )
                runner.plan.evidence.mkdir()
                runner.prepare_private_prefix()
                self.assertEqual(runner.plan.private_namespace.stat().st_mode & 0o777, 0o700)
                self.assertFalse((runner.plan.private_namespace / "artifacts").exists())
                manifest = (runner.plan.evidence / "prefix-input-manifest.json").read_text()
                self.assertNotIn("PRIVATE_CAPTURED_BODY", manifest)
                self.assertIn(sha, manifest)
                self.assertEqual(list(runner.plan.private_namespace.iterdir()), [])
                with self.assertRaises(run.InfrastructureError):
                    runner.prepare_private_prefix()

    def test_prefix_bundle_requires_independent_digest_no_synthetic_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp)
            bundle = private / "prefix.json"
            bundle.write_text(json.dumps({"sha256": "a" * 64, "payload": "PRIVATE"}))
            with patch.object(run, "PRIVATE_ROOT", private):
                runner = run.Runner(
                    self.plan(prefix_bundle=bundle, source_roles=self.source_roles(private))
                )
                with self.assertRaises(run.InfrastructureError) as failure:
                    runner.prepare_private_prefix()
                self.assertEqual(failure.exception.code, "prefix_input_hash_failed")
                self.assertFalse((private / "runs").exists())
            with self.assertRaises(ValueError):
                self.plan(prefix_bundle=ROOT / "reports/prefix.json")
            with self.assertRaises(ValueError):
                self.plan(prefix_bundle=Path("relative.json"))

    def test_default_private_pointer_and_explicit_input_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp)
            with patch.object(run, "PRIVATE_ROOT", private):
                with self.assertRaises(run.InfrastructureError):
                    run.resolve_prefix_input(None, None)
                bundle = private / "capture/prefix.json"
                (private / "LATEST.json").write_text(
                    json.dumps({"bundle_path": str(bundle), "bundle_sha256": "a" * 64})
                )
                self.assertEqual(run.resolve_prefix_input(None, None), (bundle, "a" * 64))
                self.assertIsNone(run.resolve_source_roles(None))
                metadata = json.loads((private / "LATEST.json").read_text())
                metadata["source_roles_path"] = str(private / "runtime-roles.json")
                (private / "LATEST.json").write_text(json.dumps(metadata))
                self.assertEqual(run.resolve_source_roles(None), private / "runtime-roles.json")
                with self.assertRaises(run.InfrastructureError):
                    run.resolve_prefix_input(bundle, None)
                with self.assertRaises(run.InfrastructureError):
                    run.resolve_prefix_input(None, "a" * 64)

    def test_only_one_requested_scenario_can_run_in_each_fresh_database(self):
        for scenario in ("chapter_adoption", "review_length"):
            plan = self.plan(scenario=scenario)
            self.assertEqual(
                plan.process_environment("172.28.0.2", "172.28.0.3")["E2E_SIM_SCENARIO"], scenario
            )
            with tempfile.TemporaryDirectory(dir=ROOT / "reports/parallel-2026-10-28/P3") as tmp:
                runner = run.Runner(self.plan(scenario=scenario, report_dir=Path(tmp) / "run"))
                runner.plan.evidence.mkdir()
                (runner.plan.evidence / "scenario-output").mkdir(exist_ok=True)
                (runner.plan.evidence / "scenario-output/results.json").write_text(
                    json.dumps([{"name": scenario, "status": "pass"}])
                )
                self.assertEqual(runner.scenario_outcome(0), 0)
                (runner.plan.evidence / "scenario-output").mkdir(exist_ok=True)
                (runner.plan.evidence / "scenario-output/results.json").write_text(
                    json.dumps(
                        [
                            {"name": "chapter_adoption", "status": "pass"},
                            {"name": "review_length", "status": "pass"},
                        ]
                    )
                )
                with self.assertRaises(run.InfrastructureError):
                    runner.scenario_outcome(0)
        with self.assertRaises(ValueError):
            self.plan(scenario="all")
        self.assertIn("'--scenario'", run.DRIVER_STARTUP)
        self.assertIn("E2E_SIM_SCENARIO", run.DRIVER_STARTUP)

    def test_native_logged_failure_after_catch_has_safe_frames_and_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = boot.SafeFailureHandler(Path(tmp), "worker")
            logger = logging.getLogger("bestseller.worker.tasks")
            output = io.StringIO()
            with redirect_stdout(output), boot.safe_logging(handler):
                try:
                    raise RuntimeError("execution_budget_stopped: PRIVATE_SOURCE")
                except RuntimeError:
                    logger.exception("PRIVATE_LOG_SOURCE %s", "PRIVATE_PROMPT")
                logger.error("PRIVATE_ERROR_WITHOUT_TRACEBACK")
            saved = (Path(tmp) / "failures-worker.jsonl").read_text()
            failure = json.loads(saved)
            self.assertIn("execution_budget_stopped", failure["error_codes"])
            self.assertTrue(failure["safe_stack"])
            self.assertNotIn("PRIVATE", saved + output.getvalue())

    def test_timeout_evidence_contains_cause_frames_without_subprocess_payload(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "reports/parallel-2026-10-28/P3") as tmp:
            runner = run.Runner(self.plan(report_dir=Path(tmp) / "run"))

            def execute():
                return runner.docker(["wait", runner.plan.names["driver"]])

            timeout = subprocess.TimeoutExpired(
                ["PRIVATE_COMMAND"], 1, output=b"PRIVATE_STDOUT", stderr=b"PRIVATE_STDERR"
            )
            with (
                patch.object(runner, "execute", side_effect=execute),
                patch.object(run.subprocess, "run", side_effect=timeout),
            ):
                self.assertEqual(runner.run(), 2)
            saved = (runner.plan.evidence / "failures-runner.jsonl").read_text()
            failure = json.loads(saved)
            self.assertIn("harness_docker_timeout", failure["error_codes"])
            self.assertTrue(failure["causes"][0]["safe_stack"])
            self.assertNotIn("PRIVATE", saved + (runner.plan.evidence / "run.log").read_text())

    def test_invalid_cli_prefix_input_never_formats_raw_error(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(run.main(["--plan", "--prefix-bundle", "/PRIVATE_BODY"]), 2)
        self.assertNotIn("PRIVATE_BODY", output.getvalue())
        self.assertTrue(json.loads(output.getvalue())["safe_stack"])

    def test_retry_refusal_cannot_hide_earlier_undiagnosed_failure(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "reports/parallel-2026-10-28/P3") as tmp:
            first = Path(tmp) / "first"
            first.mkdir()
            (first / "round2-run.json").write_text(json.dumps({"round": 2, "exit_code": 1}))
            runner = run.Runner(self.plan(report_dir=Path(tmp) / "second"))
            with self.assertRaises(run.InfrastructureError):
                runner.verify_prior_round2(first)
            # A refusal's own safe diagnostic does not repair the original evidence.
            later = Path(tmp) / "later"
            later.mkdir()
            (later / "round2-run.json").write_text(json.dumps({"round": 2, "exit_code": 2}))
            try:
                raise run.InfrastructureError(
                    "previous failure undiagnosed", code="harness_retry_missing_safe_stack"
                )
            except run.InfrastructureError as exc:
                (later / "failures-runner.jsonl").write_text(
                    json.dumps(run.safe_exception(exc)) + "\n"
                )
            with self.assertRaises(run.InfrastructureError):
                runner.verify_prior_round2(later)

    def test_migration_keeps_safe_logging_while_using_native_alembic(self):
        from types import ModuleType

        fence = ModuleType("e2e_sim.network_fence")
        fence.install_fence = Mock()
        alembic = ModuleType("alembic")
        config_module = ModuleType("alembic.config")
        config = Mock(config_file_name="/app/alembic.ini", config_ini_section="alembic")
        config_module.Config = Mock(return_value=config)
        observed = []

        def upgrade(passed, revision):
            observed.append((passed.config_file_name, revision))
            logging.getLogger("alembic.runtime.migration").error("PRIVATE_MIGRATION_TEXT")

        alembic.command = Mock(upgrade=upgrade)
        with tempfile.TemporaryDirectory() as tmp:
            env = {"E2E_SIM_PG_IP": "172.28.0.2", "E2E_SIM_REDIS_IP": "172.28.0.3"}
            with (
                patch.dict(
                    sys.modules,
                    {
                        "e2e_sim.network_fence": fence,
                        "alembic": alembic,
                        "alembic.config": config_module,
                    },
                ),
                patch.dict(os.environ, env),
                patch.object(boot, "verify_database"),
            ):
                self.assertEqual(boot.main(["migrate"], evidence=Path(tmp)), 0)
        self.assertEqual(observed, [(None, "head")])
        config.get_section.assert_called_once_with("alembic")

    def test_cleanup_reports_native_process_exit_and_hashes_only(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "reports/parallel-2026-10-28/P3") as tmp:
            runner = run.Runner(self.plan(report_dir=Path(tmp) / "run"))
            runner.plan.evidence.mkdir()
            runner.created = [("container", runner.plan.names["worker"])]

            def docker(args, **_kwargs):
                if args[:2] == ["container", "inspect"]:
                    stdout = json.dumps(
                        {"exit_code": 23, "process_status": "exited", "oom_killed": False}
                    )
                else:
                    stdout = "PRIVATE_NATIVE_LOG"
                return subprocess.CompletedProcess(args, 0, stdout, "PRIVATE_STDERR")

            with (
                patch.object(
                    runner, "inspect", return_value={"Config": {"Labels": runner.plan.label_map}}
                ),
                patch.object(runner, "docker", side_effect=docker),
            ):
                runner.cleanup()
            self.assertEqual(runner.process_exits[0]["exit_code"], 23)
            self.assertEqual(runner.process_exits[0]["process_role"], "worker")
            saved = (runner.plan.evidence / "worker.log").read_text()
            self.assertNotIn("PRIVATE", saved)
            self.assertIn("sha256", saved)
            self.assertFalse(runner.cleanup_errors)

    def source_roles(self, private):
        path = Path(private) / "runtime-roles.json"
        path.write_text(
            json.dumps(
                {
                    "schema": "p3-source-runtime-roles.v1",
                    "roles": {
                        role: {
                            "model": "openai/source-route",
                            "api_base": "https://source-provider.invalid/v1",
                            "temperature": 0.7,
                            "max_tokens": 2048,
                            "api_key_env": "SOURCE_API_KEY",
                            "model_override": None,
                        }
                        for role in ("planner", "writer", "critic", "summarizer", "editor")
                    },
                    "api_key_env_names": ["SOURCE_API_KEY", "CATALOG_API_KEY"],
                }
            )
        )
        return path

    def test_source_roles_keep_original_parameters_and_supply_only_dummy_keys(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(run, "PRIVATE_ROOT", Path(tmp)):
            path = self.source_roles(tmp)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaises(run.InfrastructureError):
                run.source_roles_environment(path, "b" * 64)
            (Path(tmp) / "LATEST.json").write_text(
                json.dumps({"source_roles_path": str(path), "source_roles_sha256": digest})
            )
            self.assertEqual(run.resolve_source_roles_digest(path), digest)
            plan = self.plan(source_roles=path, source_roles_sha256=digest)
            env = plan.process_environment("172.28.0.2", "172.28.0.3")
            self.assertEqual(
                json.loads(env["BESTSELLER__LLM__PLANNER__MODEL"]), "openai/source-route"
            )
            self.assertEqual(
                json.loads(env["BESTSELLER__LLM__PLANNER__API_BASE"]),
                "https://source-provider.invalid/v1",
            )
            self.assertEqual(json.loads(env["BESTSELLER__LLM__PLANNER__TEMPERATURE"]), 0.7)
            self.assertIsNone(json.loads(env["BESTSELLER__LLM__PLANNER__MODEL_OVERRIDE"]))
            self.assertEqual(env["SOURCE_API_KEY"], "offline-only-e2e-simulation")
            self.assertEqual(env["CATALOG_API_KEY"], "offline-only-e2e-simulation")
            self.assertEqual(env["E2E_SIM_SOURCE_ROLES"], "/prefix-input/runtime-roles.json")
            self.assertIn(
                f"type=bind,src={path.resolve()},dst=/prefix-input/runtime-roles.json,readonly",
                plan.container_command("prefix", plan.app_image),
            )

    def test_source_roles_refuse_credentials_and_reserved_environment_overrides(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(run, "PRIVATE_ROOT", Path(tmp)):
            path = self.source_roles(tmp)
            original = json.loads(path.read_text())
            for field, value in (
                ("api_key", "PRIVATE_PRODUCTION_KEY"),
                ("api_base", "https://user:PRIVATE@provider.invalid/v1"),
            ):
                data = json.loads(json.dumps(original))
                data["roles"]["planner"][field] = value
                path.write_text(json.dumps(data))
                with self.assertRaises(run.InfrastructureError):
                    self.plan(source_roles=path).process_environment("172.28.0.2", "172.28.0.3")
            original["api_key_env_names"] = ["BESTSELLER__DATABASE__URL"]
            path.write_text(json.dumps(original))
            with self.assertRaises(run.InfrastructureError):
                self.plan(source_roles=path).process_environment("172.28.0.2", "172.28.0.3")

    def test_full_prefix_preflight_requires_source_roles_no_sim_route_fallback(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(run, "PRIVATE_ROOT", Path(tmp)):
            plan = self.plan(prefix_bundle=Path(tmp) / "prefix.json")
            self.assertNotIn(
                "BESTSELLER__LLM__PLANNER__MODEL",
                plan.process_environment("172.28.0.2", "172.28.0.3"),
            )
            with self.assertRaises(run.InfrastructureError) as failure:
                run.Runner(plan).prepare_private_prefix()
            self.assertIn(
                "prefix_source_roles_required", run.safe_exception(failure.exception)["error_codes"]
            )

    def test_prefix_only_stops_before_api_worker_and_http_driver(self):
        plan = self.plan(prefix_only=True)
        runner = run.Runner(plan)
        created = []

        def docker(args, **kwargs):
            value = "Redis server v=7.2.1" if "--version" in args else "sha256:" + "a" * 64
            return subprocess.CompletedProcess(args, 0, value, "")

        with (
            patch.object(runner, "prepare_private_prefix"),
            patch.object(runner, "prepare_config"),
            patch.object(runner, "verify_prior_round2"),
            patch.object(runner, "docker", side_effect=docker),
            patch.object(runner, "inspect", return_value=None),
            patch.object(runner, "verify_owned"),
            patch.object(runner, "ready"),
            patch.object(runner, "ip", return_value="172.28.0.2"),
            patch.object(runner, "create", side_effect=lambda role, *a, **kw: created.append(role)),
            patch.object(runner, "wait", return_value=0),
            patch.object(runner, "verify_prefix_receipt"),
        ):
            self.assertEqual(runner.execute(), 0)
        self.assertEqual(created, ["pg", "redis", "migrate", "tape", "prefix"])


if __name__ == "__main__":
    unittest.main()
