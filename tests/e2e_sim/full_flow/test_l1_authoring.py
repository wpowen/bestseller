"""Main-run pure authoring checks; no app, transport, DB, HTTP or model calls.

Existing codec tests are untouched. Private files use an explicit external
directory, independent of pytest's checkout-local basetemp. Structural tokens
are composed at test execution, never a committed manuscript or prompt dump.
"""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from scripts.full_flow.simulation.author_l1 import (
    PROFILE,
    SCHEMA,
    compile_private_bundle,
    read_private_authoring,
    write_private_bundle,
)
from scripts.full_flow.simulation.contracts import ScriptContractError

from .test_quickstart_builders import blueprint

ROOT = Path(__file__).resolve().parents[3]


def authored():
    seed = "private-" + "input"
    return {
        "schema": SCHEMA,
        "profile": PROFILE,
        "original_input": seed,
        "quickstart": {
            "creation_mode": "fanqie_short",
            "length_key": "fanqie-short-8k",
            "pov": "first_person",
            "production_profile": "complete",
            "genre_key": "private-genre",
            "llm_model_id": "private-model",
            "draft_mode": False,
            "stop_after_conception": False,
            "concept_seed": seed,
        },
        "story": blueprint(),
        "execution_plan": [
            {
                "template": "conception_review",
                "template_ordinal": 1,
                "prompt_version": "private-version",
                "source_site": "conception.py::private-site",
            }
        ],
        "replies": [
            {
                "template": "conception_review",
                "template_ordinal": 1,
                "chapter_number": 1,
                "wire_assertions": [
                    {"pointer": "/messages/1/content", "contains": "private-marker"}
                ],
                "material": {
                    "overall_coherence_score": 0.1,
                    "contradictions": ["authored-conflict"],
                    "gaps": ["authored-gap"],
                    "premise_seeds": ["authored-seed"],
                },
            }
        ],
    }


class L1AuthoringTests(unittest.TestCase):
    def error(self, code, function):
        with self.assertRaises(ScriptContractError) as raised:
            function()
        self.assertEqual(raised.exception.code, code)

    def test_current_registry_has_128_but_absent_short_slots_still_block(self):
        result = compile_private_bundle(authored())
        self.assertEqual(result["report"]["literal_registry_count"], 128)
        missing = {
            row.get("template")
            for row in result["report"]["blockers"]
            if row["code"] == "SCRIPT_AUTHORING_REQUIRED_TEMPLATE_SLOT_MISSING"
        }
        self.assertIn("planner_fanqie_beat_sheet", missing)
        self.assertIn("reader_judge", missing)
        self.assertEqual(result["report"]["execution_coverage"], "Unknown")
        self.assertFalse(result["report"]["execution_started_by_authoring"])

    def test_private_low_score_and_false_judgment_are_preserved_without_mutating_input(self):
        value = authored()
        value["replies"][0]["material"]["passed"] = False
        before = deepcopy(value)
        result = compile_private_bundle(value)
        self.assertEqual(value, before)
        self.assertEqual(result["config"]["replies"], value["replies"])
        self.assertEqual(result["config"]["replies"][0]["material"]["overall_coherence_score"], 0.1)
        self.assertFalse(result["config"]["replies"][0]["material"]["passed"])

    def test_private_atoms_are_composed_once_without_filling_target_length(self):
        value = authored()
        value["story"]["chapters"][0]["prose_atoms"][0]["action"] = [
            {"story_pointer": "/protagonist/name"},
            "-",
            "action",
        ]
        result = compile_private_bundle(value)
        body = result["bodies"]["chapter-01.txt"]
        self.assertEqual(body.count("protagonist-action"), 1)
        self.assertLess(len(body), value["story"]["chapters"][0]["target_word_count"])
        self.assertEqual(
            result["report"]["body_sha256"]["chapter-01.txt"],
            hashlib.sha256(body.encode()).hexdigest(),
        )

    def test_client_manifest_hashes_only_explicit_original_input(self):
        value = authored()
        request = compile_private_bundle(value)["quickstart"]
        self.assertEqual(request["concept_seed"], value["original_input"])
        manifest = request["client_intent_manifest"]
        self.assertEqual(manifest["concept_seed_length"], len(value["original_input"].encode()))
        self.assertEqual(
            manifest["concept_seed_hash"],
            hashlib.sha256(value["original_input"].encode()).hexdigest(),
        )

    def test_seed_truncation_or_stale_client_manifest_is_rejected(self):
        for mutate, code in (
            (
                lambda v: v["quickstart"].update(concept_seed="different"),
                "SCRIPT_AUTHORING_ORIGINAL_INPUT_MISMATCH",
            ),
            (
                lambda v: v["quickstart"].update(client_intent_manifest={}),
                "SCRIPT_AUTHORING_CLIENT_MANIFEST_MISMATCH",
            ),
        ):
            with self.subTest(code=code):
                value = authored()
                mutate(value)
                self.error(code, lambda value=value: compile_private_bundle(value))

    def test_unknown_template_is_retained_as_blocker_not_substituted(self):
        value = authored()
        value["execution_plan"][0]["template"] = "unknown_private_template"
        value["replies"][0]["template"] = "unknown_private_template"
        result = compile_private_bundle(value)
        self.assertTrue(
            any(
                row["code"] == "SCRIPT_TEMPLATE_UNCOVERED"
                and row.get("template") == "unknown_private_template"
                for row in result["report"]["blockers"]
            )
        )
        self.assertEqual(result["config"]["replies"][0]["template"], "unknown_private_template")

    def test_missing_material_or_wire_binding_does_not_become_pass(self):
        for field, code in (
            ("material", "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED"),
            ("wire_assertions", "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED"),
        ):
            with self.subTest(field=field):
                value = authored()
                del value["replies"][0][field]
                result = compile_private_bundle(value)
                self.assertTrue(any(row["code"] == code for row in result["report"]["blockers"]))
                self.assertNotIn(field, result["config"]["replies"][0])

    def test_opinion_cannot_be_selected_from_wire_schema(self):
        value = authored()
        value["replies"][0]["material"]["overall_coherence_score"] = {"$wire": "/schema/enum/0"}
        self.error("SCRIPT_VERDICT_MUST_BE_AUTHORED", lambda: compile_private_bundle(value))

    def test_duplicate_and_noncontiguous_slots_are_not_auto_numbered(self):
        value = authored()
        value["execution_plan"].append(deepcopy(value["execution_plan"][0]))
        self.error("SCRIPT_AUTHORING_DUPLICATE_SLOT", lambda: compile_private_bundle(value))
        value = authored()
        value["execution_plan"][0]["template_ordinal"] = 2
        self.error(
            "SCRIPT_AUTHORING_NONCONTIGUOUS_TEMPLATE_SLOTS", lambda: compile_private_bundle(value)
        )

    def test_unmatched_plan_slot_remains_missing(self):
        value = authored()
        value["replies"] = []
        result = compile_private_bundle(value)
        self.assertTrue(
            any(
                row["code"] == "SCRIPT_AUTHORED_REPLY_NOT_UNIQUE"
                for row in result["report"]["blockers"]
            )
        )
        self.assertEqual(result["config"]["replies"], [])

    def test_new_book_cannot_supply_resume_or_grade_authority(self):
        for field in ("project_slug", "new_book_budget_grant", "grade", "approved", "promoted"):
            with self.subTest(field=field):
                value = authored()
                value["quickstart"][field] = "forbidden"
                self.error(
                    "SCRIPT_AUTHORING_NATIVE_STATE_FORBIDDEN",
                    lambda value=value: compile_private_bundle(value),
                )

    def test_private_root_is_fresh_external_and_pending_bundle_not_executable(self):
        result = compile_private_bundle(authored())
        with tempfile.TemporaryDirectory(prefix="l1-authoring-", dir="/private/tmp") as directory:
            root = Path(directory) / "new-bundle"
            report = write_private_bundle(result, root, repository_root=ROOT)
            self.assertEqual(report["state"], "BLOCKED")
            self.assertTrue((root / "script.pending.json").is_file())
            self.assertFalse((root / "script.json").exists())
            self.assertFalse((root / "physical-ordinals.json").exists())
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            for file in root.iterdir():
                self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            self.error(
                "SCRIPT_PRIVATE_DESTINATION_EXISTS",
                lambda: write_private_bundle(result, root, repository_root=ROOT),
            )
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})

    def test_private_input_modes_duplicates_and_checkout_guard(self):
        with tempfile.TemporaryDirectory(prefix="l1-authoring-", dir="/private/tmp") as directory:
            path = Path(directory) / "authoring.json"
            path.write_text(json.dumps(authored()))
            os.chmod(path, 0o600)
            self.assertEqual(read_private_authoring(path, repository_root=ROOT), authored())
            path.write_text('{"schema":1,"schema":2}')
            self.error(
                "SCRIPT_AUTHORING_DUPLICATE_KEY",
                lambda: read_private_authoring(path, repository_root=ROOT),
            )
            os.chmod(path, 0o644)
            self.error(
                "SCRIPT_PRIVATE_CONFIG_MODE",
                lambda: read_private_authoring(path, repository_root=ROOT),
            )
        self.error(
            "SCRIPT_PRIVATE_ROOT_OVERLAPS_REPOSITORY",
            lambda: read_private_authoring(
                ROOT / "no-private-input-here.json", repository_root=ROOT
            ),
        )

    def test_symlink_destination_is_rejected_without_writes(self):
        result = compile_private_bundle(authored())
        with tempfile.TemporaryDirectory(prefix="l1-authoring-", dir="/private/tmp") as directory:
            path = Path(directory)
            (path / "actual").mkdir()
            (path / "link").symlink_to(path / "actual", target_is_directory=True)
            self.error(
                "SCRIPT_PRIVATE_PATH_SYMLINK",
                lambda: write_private_bundle(
                    result, path / "link" / "bundle", repository_root=ROOT
                ),
            )
            self.assertEqual(list((path / "actual").iterdir()), [])
