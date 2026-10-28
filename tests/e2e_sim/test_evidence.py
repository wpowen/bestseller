"""Offline row fixtures only; no database, ledger authority or paid-call proof."""

from copy import deepcopy
from types import SimpleNamespace as Row
import unittest
from uuid import UUID

try:
    from .evidence import correlate_native_rows, read_native_correlations
except ImportError:
    from evidence import correlate_native_rows, read_native_correlations

PROJECT = "00000000-0000-0000-0000-000000000001"
WORKFLOW = "00000000-0000-0000-0000-000000000002"
OTHER = "00000000-0000-0000-0000-000000000003"
OPERATION = "a" * 64
INPUT = "b" * 64
ATTEMPT = "c" * 32
RUN = "00000000-0000-0000-0000-000000000004"


def fixture():
    event = {
        "schema": "v3-synthetic-http.v1",
        "simulation_only": True,
        "event": "synthetic_http_send",
        "observed_native_send_count": 1,
        "project_id": PROJECT,
        "workflow_run_id": WORKFLOW,
        "native_operation_key": OPERATION,
        "native_input_hash": INPUT,
        "native_attempt_id": ATTEMPT,
    }
    operation = Row(key=OPERATION, input_hash=INPUT, scope_id="fixture-scope")
    attempt = Row(
        id=ATTEMPT,
        operation_key=OPERATION,
        input_hash=INPUT,
        scope_id="fixture-scope",
        status="completed",
        model_name="fixture-model",
    )
    run = Row(
        id=UUID(RUN),
        project_id=UUID(PROJECT),
        workflow_run_id=UUID(WORKFLOW),
        prompt_hash=INPUT,
        model_name="fixture-model",
        metadata_json={
            "paid_call_origin": {
                "schema": "paid-call-origin.v1",
                "status": "recorded",
                "project_id": PROJECT,
                "workflow_run_id": WORKFLOW,
                "prompt_hash": INPUT,
            },
            "effective_input_receipt": {"schema": "effective-input.v1", "hash": INPUT},
            "execution_receipt": {
                "scope_id": "fixture-scope",
                "operation_key": OPERATION,
                "policy": "execution-budget.v1",
                "attempts": [
                    {
                        "attempt_id": ATTEMPT,
                        "effective_input_hash": INPUT,
                        "model": "fixture-model",
                        "status": "completed",
                    }
                ],
            },
        },
    )
    return event, operation, attempt, run


class EvidenceTests(unittest.TestCase):
    def correlate(self, event, operation, attempt, run):
        return correlate_native_rows(PROJECT, [event], [operation], [attempt], [run])

    def assert_unmatched(self, rows):
        result = self.correlate(*rows)
        self.assertEqual(result["correlated_send_count"], 0)
        self.assertEqual(result["correlations"], [])
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["event_index"], 0)
        self.assertFalse(result["ok"])
        return result

    def test_exact_chain_returns_explicit_identity_without_mutating_rows(self):
        rows = fixture()
        before = deepcopy(rows)
        result = self.correlate(*rows)
        self.assertTrue(result["ok"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["correlated_send_count"], 1)
        record = result["correlations"][0]
        self.assertEqual(
            {
                key: record[key]
                for key in (
                    "project_id",
                    "workflow_run_id",
                    "operation_key",
                    "input_hash",
                    "attempt_id",
                    "llm_run_id",
                )
            },
            {
                "project_id": PROJECT,
                "workflow_run_id": WORKFLOW,
                "operation_key": OPERATION,
                "input_hash": INPUT,
                "attempt_id": ATTEMPT,
                "llm_run_id": RUN,
            },
        )
        self.assertEqual(rows, before)

    def test_wrong_wire_identity_and_boolean_native_counter_are_rejected(self):
        changes = {
            "project_id": OTHER,
            "workflow_run_id": OTHER,
            "native_operation_key": "d" * 64,
            "native_input_hash": "e" * 64,
            "native_attempt_id": "f" * 32,
            "observed_native_send_count": True,
            "schema": "fake",
            "simulation_only": False,
        }
        for key, value in changes.items():
            with self.subTest(field=key):
                rows = fixture()
                rows[0][key] = value
                self.assert_unmatched(rows)

    def test_missing_wire_identity_never_uses_aggregate_counts(self):
        for key in fixture()[0]:
            if key == "event":
                continue
            with self.subTest(field=key):
                rows = fixture()
                rows[0].pop(key)
                self.assert_unmatched(rows)

    def test_wrong_native_attempt_operation_hash_scope_or_status(self):
        for key, value in (
            ("operation_key", "d" * 64),
            ("input_hash", "e" * 64),
            ("scope_id", "other"),
            ("status", "not_sent"),
            ("id", "f" * 32),
        ):
            with self.subTest(field=key):
                rows = fixture()
                setattr(rows[2], key, value)
                self.assert_unmatched(rows)

    def test_wrong_llm_row_and_origin_workflow_hash_or_operation(self):
        for location, key, value in (
            ("row", "workflow_run_id", UUID(OTHER)),
            ("row", "project_id", UUID(OTHER)),
            ("row", "prompt_hash", "e" * 64),
            ("paid_call_origin", "workflow_run_id", OTHER),
            ("paid_call_origin", "project_id", OTHER),
            ("paid_call_origin", "prompt_hash", "e" * 64),
            ("paid_call_origin", "status", "unrecorded"),
            ("execution_receipt", "operation_key", "d" * 64),
            ("execution_receipt", "scope_id", "other"),
            ("effective_input_receipt", "hash", "e" * 64),
        ):
            with self.subTest(location=location, field=key):
                rows = fixture()
                if location == "row":
                    setattr(rows[3], key, value)
                else:
                    rows[3].metadata_json[location][key] = value
                self.assert_unmatched(rows)

    def test_receipt_attempt_must_match_actual_attempt_and_effective_hash(self):
        for key, value in (
            ("attempt_id", "f" * 32),
            ("effective_input_hash", "e" * 64),
            ("model", "other"),
            ("status", "unknown"),
        ):
            with self.subTest(field=key):
                rows = fixture()
                rows[3].metadata_json["execution_receipt"]["attempts"][0][key] = value
                self.assert_unmatched(rows)

    def test_missing_rows_or_metadata_are_explicit_errors(self):
        for index in (1, 2, 3):
            event, operation, attempt, run = fixture()
            lists = [[operation], [attempt], [run]]
            lists[index - 1] = []
            result = correlate_native_rows(PROJECT, [event], *lists)
            self.assertFalse(result["ok"])
            self.assertEqual(result["correlations"], [])
        for value in (None, {}, {"paid_call_origin": "SECRET_PROMPT"}):
            rows = fixture()
            rows[3].metadata_json = value
            result = self.assert_unmatched(rows)
            self.assertNotIn("SECRET_PROMPT", str(result))

    def test_unrelated_rows_cannot_substitute_for_missing_matching_row(self):
        event, operation, attempt, run = fixture()
        wrong = deepcopy(run)
        wrong.workflow_run_id = UUID(OTHER)
        result = correlate_native_rows(PROJECT, [event], [operation], [attempt], [wrong] * 50)
        self.assertEqual(result["correlations"], [])
        result = correlate_native_rows(PROJECT, [event], [operation], [attempt], [wrong, run])
        self.assertEqual(result["correlations"][0]["llm_run_id"], RUN)

    def test_duplicate_sends_and_ambiguous_rows_never_inflate_evidence(self):
        event, operation, attempt, run = fixture()
        result = correlate_native_rows(
            PROJECT, [event, deepcopy(event)], [operation], [attempt], [run]
        )
        self.assertEqual(result["correlated_send_count"], 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["reason"], "duplicate_native_attempt")
        another = deepcopy(run)
        another.id = UUID(OTHER)
        result = correlate_native_rows(PROJECT, [event], [operation], [attempt], [run, another])
        self.assertEqual(result["correlated_send_count"], 0)
        self.assertEqual(result["errors"][0]["reason"], "llm_correlation_not_unique")

    def test_operation_claim_hash_is_distinct_from_actual_retry_input_hash(self):
        rows = fixture()
        rows[1].input_hash = "d" * 64
        result = self.correlate(*rows)
        self.assertTrue(result["ok"])
        self.assertEqual(result["correlations"][0]["operation_input_hash"], "d" * 64)
        self.assertEqual(result["correlations"][0]["input_hash"], INPUT)

    def test_unknown_timeout_without_llm_receipt_is_unmatched_not_success(self):
        event, operation, attempt, _ = fixture()
        attempt.status = "unknown"
        result = correlate_native_rows(PROJECT, [event], [operation], [attempt], [])
        self.assertEqual(result["correlated_send_count"], 0)
        self.assertFalse(result["ok"])

    def test_non_send_events_are_ignored_and_invalid_project_fails_closed(self):
        result = correlate_native_rows(PROJECT, [{"event": "harness_UNMAPPED"}], [], [], [])
        self.assertEqual(result["correlations"], [])
        self.assertTrue(result["ok"])
        with self.assertRaisesRegex(ValueError, "invalid_project_id"):
            correlate_native_rows("SECRET", [], [], [], [])

    def test_async_reader_is_available_without_importing_database_services(self):
        import asyncio
        import inspect

        self.assertTrue(inspect.iscoroutinefunction(read_native_correlations))
        # No events means no session access, import or query is needed.
        result = asyncio.run(read_native_correlations(object(), PROJECT, []))
        self.assertEqual(result["correlated_send_count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
