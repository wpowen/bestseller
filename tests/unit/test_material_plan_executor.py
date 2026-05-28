from __future__ import annotations

from pathlib import Path

from bestseller.services.material_plan_executor import execute_material_plan
from bestseller.services.material_self_repair import (
    MaterialRepairAction,
    MaterialSelfRepairPlan,
)


def _plan(*actions: MaterialRepairAction, root: Path) -> MaterialSelfRepairPlan:
    return MaterialSelfRepairPlan(
        project_dir=root.as_posix(),
        actions=actions,
        blocking=bool(actions),
        metrics={"action_count": len(actions)},
    )


def test_replace_deprecated_reference_with_replacement_applies(tmp_path: Path) -> None:
    material = tmp_path / "story-bible" / "cast.md"
    material.parent.mkdir()
    material.write_text("旧名在这里出现。", encoding="utf-8")
    action = MaterialRepairAction(
        action_type="replace_deprecated_reference",
        target="旧名",
        source_path="story-bible/cast.md:1",
        reason="deprecated",
        confidence="high",
        requires_llm=False,
        payload={"replacement": "新名"},
    )

    report = execute_material_plan(tmp_path, _plan(action, root=tmp_path))

    assert report.applied == 1
    assert report.rerun_required is True
    assert material.read_text(encoding="utf-8") == "新名在这里出现。"
    assert report.results[0].backup_path is not None
    assert report.results[0].backup_path.exists()


def test_requires_llm_action_is_skipped_offline(tmp_path: Path) -> None:
    action = MaterialRepairAction(
        action_type="replace_deprecated_reference",
        target="旧名",
        source_path="story-bible/cast.md:1",
        reason="deprecated",
        confidence="high",
        requires_llm=True,
        payload={"replacement": "新名"},
    )

    report = execute_material_plan(tmp_path, _plan(action, root=tmp_path))

    assert report.applied == 0
    assert report.skipped_offline == 1
    assert report.results[0].skipped_reason == "requires_llm"


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    material = tmp_path / "story-bible" / "cast.md"
    material.parent.mkdir()
    material.write_text("旧名在这里出现。", encoding="utf-8")
    action = MaterialRepairAction(
        action_type="replace_deprecated_reference",
        target="旧名",
        source_path="story-bible/cast.md:1",
        reason="deprecated",
        confidence="high",
        requires_llm=False,
        payload={"replacement": "新名"},
    )

    report = execute_material_plan(
        tmp_path,
        _plan(action, root=tmp_path),
        dry_run=True,
    )

    assert report.applied == 0
    assert report.rerun_required is False
    assert material.read_text(encoding="utf-8") == "旧名在这里出现。"
    assert report.results[0].backup_path is not None
    assert not report.results[0].backup_path.exists()


def test_backup_path_is_created_per_action(tmp_path: Path) -> None:
    first = tmp_path / "a.md"
    second = tmp_path / "b.md"
    first.write_text("旧名", encoding="utf-8")
    second.write_text("旧名", encoding="utf-8")
    actions = [
        MaterialRepairAction(
            action_type="replace_deprecated_reference",
            target="旧名",
            source_path=f"{name}:1",
            reason="deprecated",
            confidence="high",
            requires_llm=False,
            payload={"replacement": "新名"},
        )
        for name in ("a.md", "b.md")
    ]

    report = execute_material_plan(tmp_path, _plan(*actions, root=tmp_path))

    assert report.applied == 2
    assert all(result.backup_path and result.backup_path.exists() for result in report.results)


def test_exception_in_one_action_does_not_abort_others(tmp_path: Path) -> None:
    material = tmp_path / "b.md"
    material.write_text("旧名", encoding="utf-8")
    bad = MaterialRepairAction(
        action_type="merge_duplicate_entity",
        target="bad",
        source_path=".",
        reason="duplicate",
        confidence="high",
        requires_llm=False,
    )
    good = MaterialRepairAction(
        action_type="replace_deprecated_reference",
        target="旧名",
        source_path="b.md:1",
        reason="deprecated",
        confidence="high",
        requires_llm=False,
        payload={"replacement": "新名"},
    )

    report = execute_material_plan(tmp_path, _plan(bad, good, root=tmp_path))

    assert report.applied == 1
    assert report.results[0].skipped_reason == "exception:IsADirectoryError"
    assert material.read_text(encoding="utf-8") == "新名"

