"""Evidence expectations must never turn a blocked or truncated run green."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as Row
import unittest
from uuid import UUID

from e2e_sim import scenarios
from e2e_sim.scenarios import assess, write_junit


class ScenarioEvidenceTests(unittest.TestCase):
    def test_blocked_prefix_is_red_not_successful_length_injection(self):
        data = {
            "adopted": False,
            "knowledge_closed": False,
            "review_unknown": False,
            "length_review_runs": 0,
            "native_sends": 0,
            "harness_errors": 0,
        }
        self.assertEqual(assess("review_length", data)[0], "fail")
        self.assertEqual(assess("chapter_adoption", data)[0], "fail")

    def test_adoption_requires_knowledge_and_native_transport(self):
        data = {
            "adopted": True,
            "knowledge_closed": True,
            "knowledge": {"bible_complete": True},
            "correlation_complete": True,
            "task_bound": True,
            "task_status": "completed",
            "native_sends": 1,
            "harness_errors": 0,
            "ledger_attempts": 1,
        }
        self.assertEqual(
            assess("chapter_adoption", data), ("pass", "native_adoption_and_knowledge_closed")
        )
        data["knowledge_closed"] = False
        self.assertEqual(assess("chapter_adoption", data)[0], "fail")

    def test_length_requires_observed_native_review_and_unknown(self):
        data = {
            "adopted": False,
            "knowledge_closed": False,
            "review_unknown": True,
            "correlation_complete": True,
            "task_bound": True,
            "task_status": "completed",
            "length_review_runs": 1,
            "native_sends": 1,
            "harness_errors": 0,
            "rewrite_count": 0,
            "knowledge_rows": 0,
            "ledger_attempts": 1,
        }
        self.assertEqual(assess("review_length", data), ("pass", "length_review_quarantined"))
        data["adopted"] = True
        self.assertEqual(assess("review_length", data)[0], "fail")

    def test_closed_projection_without_bible_or_exact_correlation_is_not_acceptance(self):
        data = {
            "adopted": True,
            "knowledge_closed": True,
            "native_sends": 2,
            "ledger_attempts": 2,
            "correlation_complete": True,
            "task_bound": True,
            "task_status": "completed",
            "knowledge": {"bible_complete": False},
        }
        self.assertEqual(assess("chapter_adoption", data)[0], "fail")
        data["knowledge"]["bible_complete"] = True
        data["correlation_complete"] = False
        self.assertEqual(assess("chapter_adoption", data)[0], "error")

    def test_unasserted_wire_correlation_cannot_justify_an_adoption(self):
        data = {
            "adopted": True,
            "knowledge_closed": True,
            "knowledge": {"bible_complete": True},
            "native_sends": 1,
            "ledger_attempts": 1,
        }
        self.assertEqual(assess("chapter_adoption", data)[0], "error")

    def test_junit_red_is_failure_and_harness_mapping_is_error(self):
        from pathlib import Path
        import tempfile
        from xml.etree import ElementTree as ET

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "junit.xml"
            write_junit(
                path,
                [
                    {"name": "a", "status": "fail", "reason": "native_rejection"},
                    {"name": "b", "status": "error", "reason": "harness_UNMAPPED"},
                ],
            )
            root = ET.parse(path).getroot()
            self.assertEqual(root.get("failures"), "1")
            self.assertEqual(root.get("errors"), "1")


class FreshInvocationTests(unittest.TestCase):
    def cli(self, output, *extra, env_overrides=None):
        # Audit hooks make accidental DB imports or any socket use fail visibly.
        code = """
import runpy, sys
def guard(event, args):
    if event == 'import' and args[0].split('.')[0] in {'sqlalchemy', 'bestseller'}:
        raise SystemExit('DB_IMPORT_BEFORE_OUTPUT_GUARD')
    if event.startswith('socket.'):
        raise SystemExit('SOCKET_BEFORE_OUTPUT_GUARD')
sys.addaudithook(guard)
runpy.run_module('e2e_sim.scenarios', run_name='__main__')
"""
        env = {k: v for k, v in os.environ.items() if not k.startswith(("E2E_SIM_", "BESTSELLER"))}
        env["PYTHONPATH"] = os.pathsep.join(
            [
                str(Path(__file__).resolve().parents[1]),
                *[p for p in sys.path if Path(p).name == "site-packages"],
            ]
        )
        env.update(env_overrides or {})
        output_args = ["--output", str(output)] if output is not None else []
        return subprocess.run(
            [sys.executable, "-B", "-c", code, *output_args, *extra],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_existing_output_with_old_wire_and_preflight_is_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "old-output"
            output.mkdir()
            (output / "wire-worker.jsonl").write_text('{"old": true}\n')
            (output / "prefix-check.json").write_text('{"status": "pass"}\n')
            before = {p.name: p.read_bytes() for p in output.iterdir()}
            reply = self.cli(output)
            self.assertEqual(reply.returncode, 2, reply.stderr)
            self.assertEqual(json.loads(reply.stdout).get("reason"), "output_already_exists")
            self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})
            self.assertNotIn("BEFORE_OUTPUT_GUARD", reply.stdout + reply.stderr)

    def test_second_process_cannot_reuse_first_process_output(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "new-output"
            first = self.cli(output)
            self.assertEqual(first.returncode, 2)  # missing invocation; no DB or POST
            self.assertTrue(output.is_dir())
            before = {p.name: p.read_bytes() for p in output.iterdir()}
            second = self.cli(output, "--prefix-only")
            self.assertEqual(second.returncode, 2, second.stderr)
            self.assertEqual(json.loads(second.stdout).get("reason"), "output_already_exists")
            self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})
            self.assertNotIn(
                "BEFORE_OUTPUT_GUARD", first.stdout + first.stderr + second.stdout + second.stderr
            )

    def test_existing_and_dangling_output_symlinks_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "target").mkdir()
            for target in (root / "target", root / "missing"):
                link = root / ("link-" + target.name)
                link.symlink_to(target, target_is_directory=True)
                reply = self.cli(link)
                self.assertEqual(reply.returncode, 2, reply.stderr)
                self.assertEqual(json.loads(reply.stdout).get("reason"), "output_already_exists")
                self.assertTrue(link.is_symlink())
            self.assertEqual(list((root / "target").iterdir()), [])

    def test_new_children_record_shared_run_and_distinct_uuid_before_any_db_import(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            invocation_ids = []
            for child in ("prefix-output", "scenario-output"):
                output = root / child
                reply = self.cli(
                    output,
                    "--evidence-root",
                    str(root),
                    env_overrides={
                        "E2E_SIM_RUN_ID": "par_P3_a1b2c3d4",
                        "E2E_SIM_PREFIX_SHA256": "a" * 64,
                    },
                )
                self.assertEqual(reply.returncode, 2, reply.stderr)
                self.assertEqual(
                    json.loads(reply.stdout)["reason"], "prefix_preflight_path_required"
                )
                self.assertNotIn("BEFORE_OUTPUT_GUARD", reply.stdout + reply.stderr)
                invocation = json.loads((output / "invocation.json").read_text())
                self.assertEqual(invocation["schema"], "e2e-sim-invocation.v1")
                self.assertEqual(invocation["run_id"], "par_P3_a1b2c3d4")
                self.assertEqual(invocation["trusted_bundle_sha256"], "a" * 64)
                identity = UUID(invocation["invocation_id"])
                self.assertEqual(identity.version, 4)
                self.assertEqual(str(identity), invocation["invocation_id"])
                self.assertEqual(output.stat().st_mode & 0o777, 0o700)
                invocation_ids.append(identity)
            self.assertNotEqual(*invocation_ids)

    def test_write_tape_still_needs_no_output_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            tape = Path(folder) / "tape.json"
            reply = self.cli(None, "--write-tape", str(tape))
            self.assertEqual(reply.returncode, 0, reply.stderr)
            self.assertTrue(tape.is_file())

    def test_failed_current_task_cannot_borrow_old_adoption(self):
        data = {
            "adopted": True,
            "knowledge_closed": True,
            "knowledge": {"bible_complete": True},
            "correlation_complete": True,
            "native_sends": 1,
            "ledger_attempts": 1,
            "task_bound": True,
            "task_status": "failed",
        }
        self.assertEqual(assess("chapter_adoption", data)[0], "fail")
        data["task_status"] = "completed"
        self.assertEqual(assess("chapter_adoption", data)[0], "pass")


class ExactRunReducerTests(unittest.TestCase):
    def fixture(self):
        workflow = UUID(int=10)
        good = Row(
            id=UUID(int=11),
            workflow_run_id=workflow,
            prompt_template="chapter_draft",
            finish_reason="stop",
            input_tokens=1,
            output_tokens=2,
            prompt_hash="a" * 64,
        )
        foreign = Row(
            id=UUID(int=12),
            workflow_run_id=workflow,
            prompt_template="chapter_review",
            finish_reason="length",
            input_tokens=3,
            output_tokens=4,
            prompt_hash="b" * 64,
        )
        reviews = [Row(llm_run_id=foreign.id, verdict="unknown")]
        correlation = {
            "ok": True,
            "correlated_send_count": 1,
            "correlations": [{"llm_run_id": str(good.id), "workflow_run_id": str(workflow)}],
        }
        return good, foreign, reviews, correlation

    def test_same_workflow_uncorrelated_length_and_unknown_never_pass(self):
        good, foreign, reviews, correlation = self.fixture()
        result = scenarios.reduce_run_evidence([good, foreign], reviews, correlation)
        self.assertEqual(result["length_review_runs"], 0)
        self.assertFalse(result["review_unknown"])
        self.assertEqual([r["id"] for r in result["llm_runs"]], [str(good.id)])
        self.assertEqual(result["review_count"], 0)
        self.assertEqual(result["review_verdicts"], {})
        result.update(
            adopted=False,
            knowledge_closed=False,
            correlation_complete=True,
            native_sends=1,
            ledger_attempts=1,
            task_bound=True,
        )
        self.assertEqual(assess("review_length", result)[0], "fail")

    def test_only_exact_correlated_length_unknown_is_positive(self):
        good, foreign, reviews, correlation = self.fixture()
        correlation["correlations"][0]["llm_run_id"] = str(foreign.id)
        result = scenarios.reduce_run_evidence([good, foreign], reviews, correlation)
        self.assertEqual(result["length_review_runs"], 1)
        self.assertTrue(result["review_unknown"])
        self.assertEqual([r["id"] for r in result["llm_runs"]], [str(foreign.id)])
        self.assertEqual(result["review_count"], 1)
        self.assertEqual(result["review_verdicts"], {"unknown": 1})

    def test_unknown_on_other_correlated_run_cannot_justify_length(self):
        good, length, _, correlation = self.fixture()
        correlation["correlations"].append(
            {"llm_run_id": str(length.id), "workflow_run_id": str(length.workflow_run_id)}
        )
        reviews = [Row(llm_run_id=good.id, verdict="unknown")]
        result = scenarios.reduce_run_evidence([good, length], reviews, correlation)
        self.assertEqual(result["length_review_runs"], 1)
        self.assertFalse(result["review_unknown"])

    def test_no_exact_ids_is_no_positive_evidence(self):
        good, foreign, reviews, correlation = self.fixture()
        correlation["correlations"] = []
        result = scenarios.reduce_run_evidence([good, foreign], reviews, correlation)
        self.assertEqual(result["llm_runs"], [])
        self.assertEqual(result["length_review_runs"], 0)
        self.assertFalse(result["review_unknown"])


class InvocationBoundaryTests(unittest.TestCase):
    def invocation(self):
        return {
            "schema": "e2e-sim-invocation.v1",
            "invocation_id": str(UUID(int=1, version=4)),
            "run_id": "par_P3_a1b2c3d4",
            "trusted_bundle_sha256": "a" * 64,
        }

    def receipt(self):
        return {
            "status": "pass",
            "downstream_empty": True,
            "trusted_bundle_sha256": "a" * 64,
            "invocation": self.invocation(),
        }

    def test_preflight_run_id_sha_and_invocation_must_match(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "prefix-check.json"
            for field, bad in [
                ("run_id", "different"),
                ("trusted_bundle_sha256", "b" * 64),
                ("invocation_id", ""),
            ]:
                receipt = self.receipt()
                receipt["invocation"][field] = bad
                path.write_text(json.dumps(receipt))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    scenarios.load_preflight(
                        path, dict(self.invocation(), invocation_id=str(UUID(int=2, version=4)))
                    )
            path.write_text(json.dumps({"status": "pass", "downstream_empty": True}))
            with self.assertRaises(ValueError):
                scenarios.load_preflight(
                    path, dict(self.invocation(), invocation_id=str(UUID(int=2, version=4)))
                )

    def test_valid_preflight_is_separate_and_one_shot(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "prefix-check.json"
            path.write_text(json.dumps(self.receipt()))
            invocation = self.invocation()
            invocation["invocation_id"] = str(UUID(int=2, version=4))
            result = scenarios.load_preflight(path, invocation)
            self.assertEqual(result["status"], "pass")
            self.assertEqual(invocation["preflight_invocation_id"], str(UUID(int=1, version=4)))
            with self.assertRaisesRegex(ValueError, "prefix_preflight_already_consumed"):
                scenarios.load_preflight(path, invocation)

    def test_only_records_appended_after_boundary_are_read(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "wire-worker.jsonl"
            path.write_text('{"event": "old"}\n')
            (root / "failures-app.jsonl").write_text('{"error": "old"}\n')
            (root / "network-app.jsonl").write_text('{"error": "old"}\n')
            boundary = scenarios.snapshot_logs([root])
            with path.open("a") as stream:
                stream.write('{"event": "current"}\n')
            (root / "wire-app.jsonl").write_text('{"event": "new_file"}\n')
            records = scenarios.appended_records([root], boundary)
            self.assertCountEqual(records["wire"], [{"event": "current"}, {"event": "new_file"}])
            self.assertEqual(records["failures"], [])
            self.assertEqual(records["network"], [])

    def test_replaced_truncated_and_partial_logs_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "wire-worker.jsonl"
            for mutation in ("replace", "truncate", "partial", "remove"):
                path.write_text('{"event": "old"}\n')
                boundary = scenarios.snapshot_logs([root])
                if mutation == "replace":
                    replacement = root / "replacement"
                    replacement.write_text('{"event": "foreign"}\n')
                    replacement.replace(path)
                elif mutation == "truncate":
                    path.write_text("")
                elif mutation == "remove":
                    path.unlink()
                else:
                    with path.open("a") as stream:
                        stream.write('{"partial":')
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    scenarios.appended_records([root], boundary)

    def test_task_binding_requires_exact_task_and_workflow(self):
        pid, workflow, foreign = str(UUID(int=1)), str(UUID(int=2)), str(UUID(int=3))
        current = {"event": "synthetic_http_send", "project_id": pid, "workflow_run_id": workflow}
        events = [
            current,
            dict(current, workflow_run_id=foreign),
            dict(current, task_id="other-task"),
            dict(current, project_id=foreign),
        ]
        task = {"task_id": "current-task", "workflow_run_id": workflow, "status": "completed"}
        selected = scenarios.bind_task_events(events, "current-task", task, pid)
        self.assertEqual(selected, [current])
        for bad in (
            dict(task, task_id="old-task"),
            dict(task, workflow_run_id=None),
            dict(task, workflow_run_id="not-a-workflow"),
        ):
            with self.subTest(task=bad), self.assertRaises(ValueError):
                scenarios.bind_task_events(events, "current-task", bad, pid)
