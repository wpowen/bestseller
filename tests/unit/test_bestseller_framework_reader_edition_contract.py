from pathlib import Path
import re


SKILL_ROOT = Path(".agents/skills/bestseller-framework")


def test_mode_b_has_literal_and_diverse_title_contract() -> None:
    planning = (SKILL_ROOT / "planning.md").read_text(encoding="utf-8")
    planner_prompt = (SKILL_ROOT / "prompts/planner.md").read_text(encoding="utf-8")
    quality = (SKILL_ROOT / "quality.md").read_text(encoding="utf-8")

    for document in (planning, planner_prompt, quality):
        assert "title_register_diversity" in document
        assert "literal_chapter_title" in document


def test_reader_edition_hides_production_scene_scaffolding() -> None:
    output = (SKILL_ROOT / "output.md").read_text(encoding="utf-8")
    orchestration = (SKILL_ROOT / "orchestration.md").read_text(encoding="utf-8")
    chapter_template = (SKILL_ROOT / "templates/chapter-frontmatter.md").read_text(
        encoding="utf-8"
    )

    assert "reader_edition" in output
    assert "visible_scene_heading_count = 0" in output
    assert "visible_scene_heading_count = 0" in orchestration
    assert "<!-- production-scene:" in chapter_template
    assert not re.search(r"^## 场景[一二三四]", chapter_template, re.MULTILINE)
