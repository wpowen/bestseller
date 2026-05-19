from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ChapterModel, ProjectModel, RewriteTaskModel
from bestseller.services.autonomous_book_repair import (
    AUTONOMOUS_REPAIR_STRATEGY,
    AUTONOMOUS_REPAIR_TRIGGER,
    QualityRepairTaskSpec,
    build_quality_repair_instructions,
    build_quality_repair_plan,
    create_quality_retrofit_rewrite_tasks,
    discover_output_book_slugs,
)


pytestmark = pytest.mark.unit


class FakeExecuteResult:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def scalars(self) -> "FakeExecuteResult":
        return self

    def __iter__(self):
        return iter(self._values)


class FakeSession:
    def __init__(
        self,
        *,
        scalar_results: list[object | None] | None = None,
        execute_results: list[list[object]] | None = None,
    ) -> None:
        self.scalar_results = list(scalar_results or [])
        self.execute_results = list(execute_results or [])
        self.added: list[object] = []
        self.executed: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            table = getattr(obj, "__table__", None)
            if table is not None and "id" in table.c and getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid4())

    async def scalar(self, stmt: object) -> object | None:
        if not self.scalar_results:
            return None
        return self.scalar_results.pop(0)

    async def execute(self, stmt: object) -> FakeExecuteResult:
        self.executed.append(stmt)
        rows = self.execute_results.pop(0) if self.execute_results else []
        return FakeExecuteResult(rows)


def _project() -> ProjectModel:
    project = ProjectModel(
        slug="test-book",
        title="Test Book",
        genre="惊悚灵异",
        target_word_count=900000,
        target_chapters=300,
        metadata_json={},
    )
    project.id = uuid4()
    return project


def _chapter(project: ProjectModel, number: int) -> ChapterModel:
    chapter = ChapterModel(
        project_id=project.id,
        chapter_number=number,
        title=f"第{number}章",
        chapter_goal="推进调查",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=3000,
    )
    chapter.id = uuid4()
    return chapter


def test_discover_output_book_slugs_requires_chapter_files(tmp_path: Path) -> None:
    (tmp_path / "book-a").mkdir()
    (tmp_path / "book-a" / "chapter-001.md").write_text("# ch1", encoding="utf-8")
    (tmp_path / "book-b").mkdir()
    (tmp_path / "book-b" / "notes.md").write_text("draft", encoding="utf-8")
    (tmp_path / "book-c").mkdir()
    (tmp_path / "book-c" / "chapter-010.md").write_text("# ch10", encoding="utf-8")

    assert discover_output_book_slugs(tmp_path) == ["book-a", "book-c"]


def test_build_quality_repair_plan_filters_and_enriches_patch_points() -> None:
    audit_rows = [
        {
            "chapter_number": "1",
            "priority": "critical",
            "cause_ids": "flat_narration;weak_attraction;ai_voice",
            "char_count": "1100",
            "pulse_density": "0.03",
        },
        {
            "chapter_number": "2",
            "priority": "medium",
            "cause_ids": "weak_prose",
        },
        {
            "chapter_number": "3",
            "priority": "high",
            "cause_ids": "weak_immersion",
        },
    ]
    patch_plan = [
        {
            "chapter_number": 1,
            "patch_points": [
                {
                    "cause_id": "ai_voice",
                    "location": "paragraph-4",
                    "issue_summary": "解释型总结句过多",
                    "snippet": "他意识到这一切并不简单。",
                    "repair_action_summary": "改成证物变化触发行动",
                }
            ],
        }
    ]

    plan = build_quality_repair_plan(
        slug="test-book",
        audit_rows=audit_rows,
        patch_plan=patch_plan,
    )
    rebuilt = build_quality_repair_plan(
        slug="test-book",
        audit_rows=audit_rows,
        patch_plan=patch_plan,
    )

    assert plan.to_dict()["task_count"] == 2
    assert plan.priority_counts == {"critical": 1, "high": 1}
    assert plan.cause_counts["flat_narration"] == 1
    assert plan.cause_counts["weak_immersion"] == 1
    assert plan.specs[0].chapter_number == 1
    assert plan.specs[0].patch_points[0]["location"] == "paragraph-4"
    assert plan.specs[0].repair_id == rebuilt.specs[0].repair_id


def test_build_quality_repair_instructions_make_quality_targets_actionable() -> None:
    spec = QualityRepairTaskSpec(
        slug="test-book",
        chapter_number=8,
        priority="critical",
        task_priority=1,
        cause_ids=("flat_narration", "weak_attraction", "weak_prose", "ai_voice"),
        patch_points=(
            {
                "cause_id": "weak_prose",
                "location": "paragraph-9",
                "issue_summary": "抽象感官词过多",
                "snippet": "一种难以言说的感觉弥漫开来。",
                "repair_action_summary": "换成可拍物件和动作压力",
            },
        ),
        audit_row={"char_count": "1400", "pulse_density": "0.04"},
    )

    instructions = build_quality_repair_instructions(spec)

    assert "不要只做润色" in instructions
    assert "每 300-500 字至少有压力" in instructions
    assert "删除 AI 句式" in instructions
    assert "paragraph-9" in instructions
    assert "换成可拍物件和动作压力" in instructions


@pytest.mark.asyncio
async def test_create_quality_retrofit_rewrite_tasks_creates_db_tasks() -> None:
    project = _project()
    chapter = _chapter(project, 7)
    spec = QualityRepairTaskSpec(
        slug=project.slug,
        chapter_number=7,
        priority="critical",
        task_priority=1,
        cause_ids=("flat_narration", "ai_voice"),
        patch_points=({"location": "paragraph-2", "issue_summary": "AI 解释句"},),
        audit_row={"chapter_number": "7", "priority": "critical"},
    )
    session = FakeSession(scalar_results=[chapter], execute_results=[[]])

    result = await create_quality_retrofit_rewrite_tasks(session, project, [spec])

    assert result.created == 1
    assert result.skipped_existing == 0
    assert result.missing_chapters == ()
    assert len(session.added) == 1
    task = session.added[0]
    assert isinstance(task, RewriteTaskModel)
    assert task.trigger_type == AUTONOMOUS_REPAIR_TRIGGER
    assert task.rewrite_strategy == AUTONOMOUS_REPAIR_STRATEGY
    assert task.priority == 1
    assert task.metadata_json["autonomous_repair_id"] == spec.repair_id
    assert task.metadata_json["patch_points"][0]["location"] == "paragraph-2"
    assert "premium_category_hard_engine" in task.context_required


@pytest.mark.asyncio
async def test_create_quality_retrofit_rewrite_tasks_skips_existing_pending_task() -> None:
    project = _project()
    chapter = _chapter(project, 9)
    existing = RewriteTaskModel(
        project_id=project.id,
        trigger_type=AUTONOMOUS_REPAIR_TRIGGER,
        trigger_source_id=chapter.id,
        rewrite_strategy=AUTONOMOUS_REPAIR_STRATEGY,
        priority=1,
        status="pending",
        instructions="old",
        context_required=[],
        metadata_json={},
    )
    existing.id = uuid4()
    spec = QualityRepairTaskSpec(
        slug=project.slug,
        chapter_number=9,
        priority="high",
        task_priority=2,
        cause_ids=("weak_attraction",),
    )
    session = FakeSession(scalar_results=[chapter], execute_results=[[existing]])

    result = await create_quality_retrofit_rewrite_tasks(session, project, [spec])

    assert result.created == 0
    assert result.skipped_existing == 1
    assert result.task_ids == (str(existing.id),)
    assert session.added == []
