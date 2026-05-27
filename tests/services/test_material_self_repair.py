from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.material_self_repair import plan_material_self_repair

pytestmark = pytest.mark.unit


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_material_self_repair_plans_reference_and_chapter_repairs(tmp_path: Path) -> None:
    _write(
        tmp_path / "story-bible" / "cast-and-promises.md",
        "## 林渊\n- identity: protagonist\n- role: active investigator\n",
    )
    _write(
        tmp_path / "story-bible" / "forbidden-terms.yaml",
        "deprecated_terms:\n  - 旧主角\n",
    )
    _write(
        tmp_path / "story-bible" / "notes.md",
        "- 旧主角不能继续出现在正典里。\n- 新线索来自 [[人物/周雪]]。\n- 待补规则 R-999。\n",
    )

    plan = plan_material_self_repair(tmp_path, chapter_number=5)

    action_types = {action.action_type for action in plan.actions}
    assert plan.blocking is True
    assert "replace_deprecated_reference" in action_types
    assert "create_missing_entity_placeholder" in action_types
    assert "expand_missing_chapter_material" in action_types
    assert plan.metrics["blocking_action_count"] >= 3


def test_material_self_repair_clean_project_has_no_actions(tmp_path: Path) -> None:
    _write(
        tmp_path / "story-bible" / "cast-and-promises.md",
        "## 林渊\n- identity: protagonist\n- role: active investigator\n",
    )
    _write(
        tmp_path / "story-bible" / "rule-ledger.md",
        "| ID | Rule | First seen | Visible effect | Solution | Cost | Future use |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| R-001 | 镜面欠账 | ch1 | 倒影缺席 | 铜钱压镜 | 失温 | ch1-5 |\n",
    )
    _write(
        tmp_path / "story-bible" / "reveal-schedule.yaml",
        "reveals:\n  - id: rev-1\n    earliest_chapter: 5\n    tokens: [镜面欠账]\n",
    )
    _write(
        tmp_path / "story-bible" / "volume-plan-v2.yaml",
        "milestones:\n  - chapter: 5\n    required_evidence: [铜钱]\n",
    )

    plan = plan_material_self_repair(tmp_path, chapter_number=5)

    assert plan.blocking is False
    assert plan.actions == ()
