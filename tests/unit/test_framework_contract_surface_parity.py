from __future__ import annotations

from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def _runtime_contract_marker() -> str:
    config = yaml.safe_load((ROOT / "config/default.yaml").read_text(encoding="utf-8"))
    generation = config["generation"]
    chapter = generation["words_per_chapter"]
    scene = generation["words_per_scene"]
    return (
        "BESTSELLER_RUNTIME_CONTRACT "
        f"chapter={chapter['min']}:{chapter['target']}:{chapter['max']} "
        f"scene={scene['min']}:{scene['target']}:{scene['max']} "
        "runtime_truth=postgresql"
    )


@pytest.mark.parametrize(
    "relative_path",
    (
        ".agents/skills/bestseller-framework/SKILL.md",
        ".claude/skills/bestseller-framework/SKILL.md",
        ".cursor/rules/bestseller-core.mdc",
        "docs/ai-context.md",
        "docs/ai-context-system-prompt.md",
    ),
)
def test_critical_framework_surfaces_publish_runtime_contract(
    relative_path: str,
) -> None:
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    assert _runtime_contract_marker() in text


def test_mode_b_surfaces_use_real_pipeline_and_database_truth() -> None:
    for relative_path in (
        ".agents/skills/bestseller-framework/modes.md",
        ".claude/skills/bestseller-framework/modes.md",
    ):
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "run_chapter_pipeline" in text
        assert "1800, 3500" in text
        assert "绝不调用仓库后端" not in text


def test_progress_yaml_is_a_projection_not_competing_runtime_truth() -> None:
    for relative_path in (
        ".agents/skills/bestseller-framework/templates/progress-state.md",
        ".claude/skills/bestseller-framework/templates/progress-state.md",
        ".agents/skills/bestseller-framework/orchestration.md",
        ".claude/skills/bestseller-framework/orchestration.md",
    ):
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "PostgreSQL" in text
        assert "检查点/投影" in text
