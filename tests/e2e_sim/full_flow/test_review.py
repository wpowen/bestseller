import unittest
from uuid import uuid4

from scripts.full_flow.simulation.contracts import CallIdentity, build_document, text_response
from scripts.full_flow.simulation.review import (
    inspect_script_source,
    inspect_transport_source,
    review_script_document,
)

from .test_contracts import ENDPOINTS, USAGE, catalog


class ReviewTests(unittest.TestCase):
    def test_unknown_template_and_required_missing_are_distinct(self):
        source = catalog()
        response = text_response(source, "chapter_review", "", usage=USAGE, finish_reason="length")
        document = build_document(
            source,
            [(CallIdentity("chapter_review", 1, project_id=str(uuid4())), [response])],
            allowed_endpoints=ENDPOINTS,
        )
        review = review_script_document(
            source.inventory, document, required_templates=["chapter_first_writer"]
        )
        self.assertTrue(review["structurally_reviewed"])
        self.assertEqual(review["missing_required_templates"], ["chapter_first_writer"])
        self.assertEqual(review["native_schema_or_business_acceptance"], "Unknown")
        document["entries"][0]["match"]["prompt_template"] = "uncovered_new_judge"
        review = review_script_document(source.inventory, document)
        self.assertFalse(review["structurally_reviewed"])
        self.assertEqual(review["uncovered_templates"], ["uncovered_new_judge"])
        self.assertEqual(review["invalid_entries"][0]["code"], "SCRIPT_TEMPLATE_UNCOVERED")

    def test_script_source_is_never_executed_and_dynamic_entries_are_not_coverage(self):
        source = """
raise RuntimeError("THIS_MODULE_MUST_NEVER_BE_EXECUTED")
entry(name, "chapter_review", spec)
entry(name, f"planner_volume_{number}_chapter_outline", spec)
entry(name, "unknown_new_template", spec)
"""
        review = inspect_script_source(catalog().inventory, source)
        self.assertEqual(review["known_declarations"], ["chapter_review"])
        self.assertEqual(review["uncovered_declarations"], ["unknown_new_template"])
        self.assertEqual(len(review["dynamic_declarations"]), 1)
        self.assertFalse(review["coverage_proven"])

    def test_interface_mismatch_detected_without_importing_transport(self):
        result = inspect_transport_source("""
raise RuntimeError("NO_IMPORT")
TAPE_SCHEMA = "changed"
def install_scripted_http(tape, changed): pass
""")
        self.assertFalse(result["compatible"])
        self.assertIn("TAPE_SCHEMA", result["mismatches"])
        self.assertIn("install_scripted_http", result["mismatches"])
