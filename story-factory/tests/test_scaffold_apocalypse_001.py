import sys
import unittest
from pathlib import Path
import re


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import compile_story_package
import scaffold_apocalypse_001


class ApocalypseScaffoldTests(unittest.TestCase):
    def test_build_story_package_matches_project_contract(self) -> None:
        payload = scaffold_apocalypse_001.build_story_package()

        self.assertEqual(
            set(payload.keys()),
            {"book", "reader_desire_map", "story_bible", "route_graph", "walkthrough", "chapters"},
        )
        self.assertEqual(payload["book"]["id"], "apocalypse_001")
        self.assertEqual(payload["book"]["genre"], "末日爽文")
        self.assertEqual(payload["book"]["total_chapters"], 60)
        self.assertEqual(len(payload["chapters"]), 60)

    def test_generated_story_package_validates_without_errors(self) -> None:
        payload = scaffold_apocalypse_001.build_story_package()
        errors, warnings, report = compile_story_package.validate_package(payload)

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(report["chapter_count"], 60)
        self.assertGreaterEqual(report["average_choice_nodes_per_chapter"], 2)
        self.assertEqual(report["warnings"], [])

    def test_every_choice_node_uses_three_options(self) -> None:
        payload = scaffold_apocalypse_001.build_story_package()

        for chapter in payload["chapters"]:
            for node in chapter["nodes"]:
                if "choice" not in node:
                    continue
                self.assertEqual(len(node["choice"]["choices"]), 3, chapter["id"])

    def test_story_text_does_not_leak_internal_character_slugs(self) -> None:
        payload = scaffold_apocalypse_001.build_story_package()
        leaked_slug_pattern = re.compile(r"(linshuang|hance|tanghai|shenchong|luoyue|hemu|qinnian)")
        collected_texts: list[str] = []

        for chapter in payload["chapters"]:
            for node in chapter["nodes"]:
                if "text" in node:
                    collected_texts.append(node["text"]["content"])
                if "dialogue" in node:
                    collected_texts.append(node["dialogue"]["content"])
                if "choice" in node:
                    for choice in node["choice"]["choices"]:
                        collected_texts.append(choice["description"])
                        for result_node in choice["result_nodes"]:
                            if "text" in result_node:
                                collected_texts.append(result_node["text"]["content"])
                            if "dialogue" in result_node:
                                collected_texts.append(result_node["dialogue"]["content"])

        self.assertIsNone(leaked_slug_pattern.search("\n".join(collected_texts)))


if __name__ == "__main__":
    unittest.main()
