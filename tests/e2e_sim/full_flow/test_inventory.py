import json
from pathlib import Path
import tempfile
import unittest

from scripts.full_flow.simulation.inventory import changed_sources, scan_repository


class InventoryTests(unittest.TestCase):
    def source(self, root, name, source):
        path = root / "src/bestseller/services" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        return path

    def test_ast_collects_direct_conditional_constant_and_positional_templates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.source(
                root,
                "demo.py",
                """
TEMPLATE = "planning_draft_admission"
def produce(part_id, template, schema):
    LLMCompletionRequest(prompt_template=template, tools=tools(schema))
produce("one", "planner_cast_person_part", runtime_schema)
LLMCompletionRequest(prompt_template=TEMPLATE)
LLMCompletionRequest(prompt_template=(
    "story_architect_v1_repair" if repair else "story_architect_v1"))
_llm_call_json(template="conception_market", system_prompt="PRIVATE_MARKER_DO_NOT_EXPORT")
""",
            )
            result = scan_repository(root)
            names = {row["template"] for row in result["templates"]}
            self.assertEqual(
                names,
                {
                    "planning_draft_admission",
                    "planner_cast_person_part",
                    "story_architect_v1",
                    "story_architect_v1_repair",
                    "conception_market",
                    "conception_market_repair",
                },
            )
            self.assertNotIn("PRIVATE_MARKER_DO_NOT_EXPORT", json.dumps(result))
            self.assertTrue(result["dynamic_sites"])
            self.assertFalse(result["parse_failures"])

    def test_dynamic_logical_name_stays_unresolved_and_source_drift_is_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.source(
                root,
                "planner.py",
                """
generate(logical_name=f"volume_{volume_number}_chapter_outline")
""",
            )
            result = scan_repository(root)
            self.assertEqual(result["templates"], [])
            self.assertEqual(
                result["dynamic_sites"][0]["expression"],
                "planner_volume_{volume_number}_chapter_outline",
            )
            self.assertEqual(changed_sources(root, result), [])
            path.write_text(path.read_text() + "\n# coordinator change\n")
            self.assertEqual(changed_sources(root, result), ["src/bestseller/services/planner.py"])

    def test_archival_occurrences_are_explicit_and_parse_failure_is_not_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.source(
                root, "example_history_v1/frozen.py", 'request(prompt_template="old_writer")'
            )
            self.source(root, "broken.py", "def invalid(:")
            result = scan_repository(root)
            self.assertTrue(result["templates"][0]["historical_only"])
            self.assertEqual(result["parse_failures"][0]["code"], "SCRIPT_SOURCE_PARSE_FAILED")
