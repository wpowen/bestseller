import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOOK_SWIFT = ROOT / "LifeScript-iOS" / "Sources" / "LifeScript" / "Models" / "Book.swift"
STORY_THEME_SWIFT = ROOT / "LifeScript-iOS" / "Sources" / "LifeScript" / "DesignSystem" / "StoryTheme.swift"


class IOSGenreContractTests(unittest.TestCase):
    def test_book_model_supports_apocalypse_power_fantasy(self) -> None:
        source = BOOK_SWIFT.read_text(encoding="utf-8")
        self.assertIn('"末日爽文"', source)

    def test_story_theme_supports_apocalypse_power_fantasy(self) -> None:
        source = STORY_THEME_SWIFT.read_text(encoding="utf-8")
        self.assertIn(".apocalypsePower", source)
        self.assertIn("废土夺权", source)


if __name__ == "__main__":
    unittest.main()
