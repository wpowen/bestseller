"""Isolated stdlib tests: no application imports, test DB, provider, or repo writes."""

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import unittest

from bestseller.services.unsent_reclaim_control import validate_unsent_reclaim_control


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def canonical_hash(value):
    return sha(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")))


def control(parent, old, new):
    return {
        "key": sha("unsent-reclaim.v1\n" + parent["key"] + "\n" + old),
        "scope_id": parent["scope_id"], "input_hash": parent["input_hash"],
        "status": "unsent_reclaim_receipt", "owner": old,
        "lease_until": "2026-10-01T00:00:00+00:00",
        "result_json": {"operation_key": parent["key"], "old_owner": old,
                        "new_owner": new, "credit_released": False},
    }


def case(*, owners=("owner-A", "owner-B"), status="completed", with_actual=True):
    root = {
        "key": sha("root"), "scope_id": "original-scope", "input_hash": sha("input"),
        "owner": owners[-1], "status": status,
        "lease_until": "2026-10-01T00:00:00+00:00",
        # Deliberately no purpose metadata: this is NOT a purpose validator.
        "result_json": {"content": "原始结果"} if status == "completed" else None,
    }
    controls = [control(root, old, new) for old, new in zip(owners, owners[1:])]
    attempts = [{
        "id": "actual-1", "operation_key": root["key"], "scope_id": root["scope_id"],
        "input_hash": root["input_hash"], "model_name": "retained-model",
        "reserved_tokens": 200, "actual_tokens": 17, "status": "completed",
        "created_at": "2026-10-02T00:00:00+00:00",
    }] if with_actual else []
    return {"operation": controls[0], "operations": [root, *controls],
            "actual_attempts": attempts}


class UnsentReclaimControlTests(unittest.TestCase):
    def rejects(self, data):
        with self.assertRaises(ValueError):
            validate_unsent_reclaim_control(**data)

    def test_current_completed_parent_one_actual_preserves_historical_control(self):
        data = case()
        before = deepcopy(data)
        witness = validate_unsent_reclaim_control(**data)
        self.assertEqual(data, before)
        self.assertEqual(witness["parent_status"], "completed")
        self.assertEqual(witness["owner_chain"]["owners"], ["owner-A", "owner-B"])
        self.assertTrue(witness["root_requires_independent_validation"])
        self.assertEqual(witness["actual_row_sha256"],
                         {"actual-1": canonical_hash(data["actual_attempts"][0])})
        self.assertEqual(set(data["operation"]["result_json"]),
                         {"operation_key", "old_owner", "new_owner", "credit_released"})
        self.assertTrue(all(k not in witness for k in (
            "known_purpose", "neverSent", "never_sent", "credit_released", "replay_allowed")))

    def test_stopped_zeroactual_parent_purpose_remains_unknown(self):
        data = case(status="stopped", with_actual=False)
        witness = validate_unsent_reclaim_control(**data)
        self.assertEqual(witness["parent_status"], "stopped")
        self.assertEqual(witness["actual_row_sha256"], {})
        self.assertTrue(witness["root_requires_independent_validation"])
        self.assertIsNone(data["operations"][0]["result_json"])
        # No original request evidence exists; a control witness supplies none.
        self.assertNotIn("request_purpose", witness)

    def test_all_siblings_form_full_multiowner_chain_to_current_owner(self):
        data = case(owners=("A", "B", "C", "D"))
        expected_keys = [r["key"] for r in data["operations"][1:]]
        for target in data["operations"][1:]:
            data["operation"] = target
            witness = validate_unsent_reclaim_control(**data)
            self.assertEqual(witness["owner_chain"], {
                "owners": ["A", "B", "C", "D"], "control_keys": expected_keys})
        previous = validate_unsent_reclaim_control(**data)
        data["operations"].reverse()
        self.assertEqual(validate_unsent_reclaim_control(**data), previous)

    def test_all_full_rows_and_all_actual_inventory_hashed(self):
        data = case()
        root = deepcopy(data["operations"][0])
        root.update(key=sha("another-root"), input_hash=sha("another-input"))
        data["operations"].append(root)
        actual = deepcopy(data["actual_attempts"][0])
        actual.update(id="actual-2", operation_key=root["key"], input_hash=root["input_hash"])
        data["actual_attempts"].append(actual)
        data["operations"][0]["opaque_extra"] = {"keep": [None, True, 1.5, "原文"]}
        witness = validate_unsent_reclaim_control(**data)
        rows = sorted(data["operations"], key=lambda row: row["key"])
        actuals = sorted(data["actual_attempts"], key=lambda row: row["id"])
        self.assertEqual(witness["operation_row_sha256"],
                         {r["key"]: canonical_hash(r) for r in rows})
        self.assertEqual(witness["operations_sha256"], canonical_hash(rows))
        self.assertEqual(witness["actual_attempts_sha256"], canonical_hash(actuals))
        self.assertEqual(witness["inventory_sha256"],
                         canonical_hash({"operations": rows, "actual_attempts": actuals}))
        data["actual_attempts"].reverse()
        self.assertEqual(validate_unsent_reclaim_control(**data), witness)
        data["operations"][-1]["opaque_extra"] = "retained change"
        self.assertNotEqual(validate_unsent_reclaim_control(**data)["inventory_sha256"],
                            witness["inventory_sha256"])

    def test_lease_not_used_to_infer_reclaim_or_attempt_time(self):
        data = case()
        data["operation"]["lease_until"] = "2099-01-01T00:00:00Z"
        data["operations"][0]["lease_until"] = "1900-01-01T00:00:00Z"
        witness = validate_unsent_reclaim_control(**data)
        self.assertTrue(all("time" not in key for key in witness))
        prior_hash = witness["inventory_sha256"]
        data["operation"]["lease_until"] = "2098-01-01T00:00:00Z"
        self.assertNotEqual(validate_unsent_reclaim_control(**data)["inventory_sha256"],
                            prior_hash)

    def test_wrong_control_row_fields(self):
        for key, value in (
            ("key", sha("wrong")), ("key", "INVALID"), ("scope_id", "foreign"),
            ("input_hash", sha("wrong")), ("owner", "forged-owner"),
            ("status", "completed"), ("owner", None), ("project_id", "invented-project"),
        ):
            with self.subTest(key=key, value=value):
                data = case()
                data["operation"][key] = value
                self.rejects(data)

    def test_wrong_exact_producer_payload(self):
        for key, value in (
            ("operation_key", sha("missing")), ("old_owner", "forged"),
            ("new_owner", "forged"), ("old_owner", ""), ("new_owner", None),
            ("new_owner", "owner-A"), ("credit_released", True),
            ("credit_released", 0), ("credit_released", None),
            ("schema", "unsent-reclaim.v1"), ("unexpected", "extra"),
        ):
            with self.subTest(key=key, value=value):
                data = case()
                data["operation"]["result_json"][key] = value
                self.rejects(data)
        for key in ("operation_key", "old_owner", "new_owner", "credit_released"):
            with self.subTest(missing=key):
                data = case()
                del data["operation"]["result_json"][key]
                self.rejects(data)

    def test_payload_schema_forgery_cannot_replace_status(self):
        data = case()
        data["operation"]["status"] = "transport_failure_receipt"
        data["operation"]["result_json"]["schema"] = "unsent-reclaim.v1"
        self.rejects(data)

    def test_missing_parent_self_parent_control_parent(self):
        data = case()
        data["operations"].pop(0)
        self.rejects(data)
        data = case()
        data["operation"]["result_json"]["operation_key"] = data["operation"]["key"]
        self.rejects(data)
        data = case()
        data["operations"][0]["status"] = "unsent_reclaim_receipt"
        self.rejects(data)

    def test_target_must_be_exact_full_inventory_row(self):
        data = case()
        data["operation"] = deepcopy(data["operation"])
        data["operation"]["extra"] = "absent-from-inventory"
        self.rejects(data)
        data = case()
        data["operations"].pop()
        self.rejects(data)

    def test_duplicate_operation_keys_and_duplicate_oldowners_rejected(self):
        for change in (False, True):
            with self.subTest(changed=change):
                data = case()
                duplicate = deepcopy(data["operation"])
                if change:
                    duplicate["result_json"]["new_owner"] = "owner-C"
                data["operations"].append(duplicate)
                self.rejects(data)

    def test_fork_merge_cycle_disconnected_and_missing_middle_rejected(self):
        edges = (
            [("A", "B"), ("A", "C")],
            [("A", "C"), ("B", "C")],
            [("A", "B"), ("B", "A")],
            [("A", "B"), ("C", "D")],
            [("A", "B"), ("B", "D"), ("X", "Y"), ("Y", "X")],
            [("A", "B"), ("C", "D")],  # missing B -> C
            [("A", "B"), ("B", "C"), ("C", "B")],
        )
        for links in edges:
            with self.subTest(links=links):
                data = case(owners=("A", "D"))
                root = data["operations"][0]
                controls = [control(root, old, new) for old, new in links]
                data.update(operation=controls[0], operations=[root, *controls])
                self.rejects(data)

    def test_wrong_current_parent_owner_rejected(self):
        data = case(owners=("A", "B", "C"))
        for owner in ("A", "B", "elsewhere"):
            with self.subTest(owner=owner):
                data["operations"][0]["owner"] = owner
                self.rejects(data)

    def test_all_sibling_controls_and_other_chains_are_checked(self):
        data = case(owners=("A", "B", "C"))
        data["operations"][-1]["result_json"]["schema"] = "forged"
        self.rejects(data)
        data = case()
        root = deepcopy(data["operations"][0])
        root.update(key=sha("other-root"), status="stopped", owner="D", result_json=None)
        siblings = [control(root, "C", "D"), control(root, "X", "Y")]
        data["operations"].extend([root, *siblings])
        self.rejects(data)

    def test_pending_unknown_and_unrecognized_parent_fail_closed_even_zeroactual(self):
        for status in ("pending", "unknown", "Unknown", "rejected", "not_sent", "cancelled"):
            with self.subTest(status=status):
                self.rejects(case(status=status, with_actual=False))

    def test_completed_parent_without_completed_actual_rejected(self):
        self.rejects(case(with_actual=False))
        data = case()
        data["actual_attempts"][0].update(status="rejected", actual_tokens=0)
        self.rejects(data)

    def test_conservative_terminal_actual_status_and_usage(self):
        for status, tokens in (("completed", 0), ("completed", 20),
                               ("rejected", 0), ("not_sent", 0)):
            with self.subTest(status=status, tokens=tokens):
                data = case(status="stopped")
                data["actual_attempts"][0].update(status=status, actual_tokens=tokens)
                validate_unsent_reclaim_control(**data)
        for status, tokens in (
            ("unknown", 0), ("pending", 0), ("reserved", 0), ("failed", 0),
            ("reconciled_charged", 20), ("reconciled_charged", 0),
            ("rejected", 1), ("not_sent", 1), ("completed", -1),
            ("completed", True), ("completed", 0.0), ("completed", None),
        ):
            with self.subTest(status=status, tokens=tokens):
                data = case()
                data["actual_attempts"][0].update(status=status, actual_tokens=tokens)
                self.rejects(data)

    def test_all_actuals_bound_and_ids_unique(self):
        for key, value in (
            ("id", ""), ("id", 1), ("scope_id", "foreign"),
            ("operation_key", sha("missing")), ("input_hash", sha("foreign")),
            ("project_id", "invented-project"),
        ):
            with self.subTest(key=key, value=value):
                data = case()
                data["actual_attempts"][0][key] = value
                self.rejects(data)
        data = case()
        data["actual_attempts"].append(deepcopy(data["actual_attempts"][0]))
        self.rejects(data)
        data = case()
        data["actual_attempts"][0]["operation_key"] = data["operation"]["key"]
        self.rejects(data)

    def test_unknown_extra_actual_cannot_be_filtered_away(self):
        data = case()
        extra = deepcopy(data["actual_attempts"][0])
        extra.update(id="unknown-extra", status="unknown", actual_tokens=None)
        data["actual_attempts"].append(extra)
        self.rejects(data)
        data = case()
        root = deepcopy(data["operations"][0])
        root["key"] = sha("unrelated-root")
        data["operations"].append(root)
        extra.update(operation_key=root["key"])
        data["actual_attempts"].append(extra)
        witness = validate_unsent_reclaim_control(**data)
        self.assertIn(extra["id"], witness["actual_row_sha256"])
        self.assertTrue(witness["root_requires_independent_validation"])

    def test_every_supplied_operation_must_have_same_scope(self):
        data = case()
        root = deepcopy(data["operations"][0])
        root.update(key=sha("unrelated-root"), scope_id="foreign")
        data["operations"].append(root)
        self.rejects(data)

    def test_all_passed_graph_is_finite_serialized_json(self):
        values = (float("nan"), float("inf"), -float("inf"), datetime(2026, 1, 1),
                  {1: "nonstring key"}, (1, 2), {1, 2}, b"bytes", Path("/tmp/x"),
                  "\ud800")
        for location in ("root", "control", "actual"):
            for value in values:
                with self.subTest(location=location, kind=type(value).__name__):
                    data = case()
                    row = {"root": data["operations"][0], "control": data["operation"],
                           "actual": data["actual_attempts"][0]}[location]
                    row["opaque_extra"] = {"deep": [value]}
                    self.rejects(data)
        data = case()
        data["operations"][0]["recursive"] = data["operations"][0]
        self.rejects(data)

    def test_malformed_rows_always_raise_valueerror(self):
        for field, value in (("operation", None), ("operations", {}),
                             ("operations", [None]), ("actual_attempts", None),
                             ("actual_attempts", [None])):
            with self.subTest(field=field, value=value):
                data = case()
                data[field] = value
                self.rejects(data)


if __name__ == "__main__":
    unittest.main()
