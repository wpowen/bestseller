"""Main-run RED: a mutated compiler result must not become runnable authority.

No app/network/model call. Only temporary private files may be written when
main runs these checks; existing authoring and codec tests remain unchanged.
"""

from pathlib import Path
import tempfile
import unittest

from scripts.full_flow.simulation.author_l1 import compile_private_bundle, write_private_bundle
from scripts.full_flow.simulation.contracts import ScriptContractError

from .test_l1_authoring import ROOT, authored


class L1AuthoringReadinessRedTests(unittest.TestCase):
    def assert_rejected_before_write(self, bundle):
        with tempfile.TemporaryDirectory(prefix="l1-authoring-red-", dir="/private/tmp") as private:
            destination = Path(private) / "bundle"
            with self.assertRaises(ScriptContractError) as raised:
                write_private_bundle(bundle, destination, repository_root=ROOT)
            self.assertEqual(raised.exception.code, "SCRIPT_AUTHORING_BUNDLE_CHANGED")
            self.assertFalse(destination.exists())

    def test_clearing_missing_codec_blockers_cannot_publish_script_json(self):
        bundle = compile_private_bundle(authored())
        self.assertTrue(bundle["report"]["blockers"])
        bundle["report"]["blockers"] = []
        bundle["report"]["state"] = "STRUCTURE_READY_FOR_MAIN_REVIEW"
        self.assert_rejected_before_write(bundle)

    def test_changed_rendered_body_cannot_keep_original_body_receipt(self):
        bundle = compile_private_bundle(authored())
        bundle["bodies"]["chapter-01.txt"] += "changed-runtime-token"
        self.assert_rejected_before_write(bundle)

    def test_changed_quickstart_seed_cannot_keep_original_input_receipt(self):
        bundle = compile_private_bundle(authored())
        bundle["quickstart"]["concept_seed"] += "changed-runtime-input"
        self.assert_rejected_before_write(bundle)

    def test_changed_finite_plan_cannot_keep_original_slot_receipt(self):
        bundle = compile_private_bundle(authored())
        bundle["execution_plan"][0]["template_ordinal"] = 2
        self.assert_rejected_before_write(bundle)
