import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import story_factory_genres


class StoryFactoryGenresTests(unittest.TestCase):
    def test_valid_genres_include_apocalypse_power_fantasy(self) -> None:
        self.assertIn("末日爽文", story_factory_genres.VALID_GENRES)

    def test_allowed_genres_string_mentions_apocalypse_power_fantasy(self) -> None:
        allowed = story_factory_genres.format_allowed_genres()
        self.assertIn("末日爽文", allowed)
        self.assertIn("悬疑生存", allowed)

    def test_genre_rule_summary_contains_apocalypse_constraints(self) -> None:
        summary = story_factory_genres.genre_rule_summary("末日爽文")
        self.assertIn("资源争夺", summary)
        self.assertIn("尸潮", summary)


if __name__ == "__main__":
    unittest.main()
