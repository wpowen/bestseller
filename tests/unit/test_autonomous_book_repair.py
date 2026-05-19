from __future__ import annotations

from pathlib import Path
import re
from uuid import uuid4

import pytest

from bestseller.infra.db.models import (
    ChapterModel,
    ChapterQualityReportModel,
    ProjectModel,
    RewriteTaskModel,
)
from bestseller.services.autonomous_book_repair import (
    AUTONOMOUS_REPAIR_STRATEGY,
    AUTONOMOUS_REPAIR_TRIGGER,
    QualityRepairTaskSpec,
    append_previous_rewrite_failure_feedback,
    append_quality_gate_feedback,
    build_quality_repair_instructions,
    build_quality_repair_plan,
    create_quality_retrofit_rewrite_tasks,
    discover_output_book_slugs,
    load_latest_quality_gate_violations,
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


def test_build_quality_repair_plan_skips_invalid_audit_rows() -> None:
    plan = build_quality_repair_plan(
        slug="english-book",
        audit_rows=[
            {
                "chapter_number": "1",
                "priority": "high",
                "cause_ids": "flat_narration;weak_attraction",
                "audit_validity": "invalid_audit_language_mismatch",
            }
        ],
    )

    assert plan.to_dict()["task_count"] == 0


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
    assert "人物姓名、称呼、关系称谓必须沿用" in instructions
    assert "不能随段落漂移" in instructions


def test_build_quality_repair_instructions_include_detector_specific_feedback() -> None:
    spec = QualityRepairTaskSpec(
        slug="test-book",
        chapter_number=17,
        priority="high",
        task_priority=2,
        cause_ids=("flat_narration", "weak_attraction", "ai_voice"),
        audit_row={
            "char_count": "2914",
            "pulse_count": "2",
            "pulse_density": "0.21",
            "pulse_threshold": "1.0",
            "banned_pattern_breakdown": "cliched_metaphor:1;smooth_transition:1",
            "rhythm_hard_stops": "0",
            "rhythm_acceleration": "0",
            "rhythm_delay": "1",
            "rhythm_external_interrupts": "8",
            "rhythm_total_anchors": "9",
            "rhythm_types_covered": "2",
            "rhythm_expected_min_count": "8",
            "rhythm_expected_min_types": "3",
            "rhythm_passed": "False",
        },
    )

    instructions = build_quality_repair_instructions(spec)

    assert "pulse_density=0.21" in instructions
    assert "目标 >= 1.00" in instructions
    assert "可用压力触发示例" in instructions
    assert "像……一样" in instructions
    assert "更要命的是" in instructions
    assert "当前节奏锚点" in instructions
    assert "types=2/3" in instructions
    assert "优先补缺失锚点" in instructions


def test_overflow_repair_instructions_do_not_ask_model_to_expand() -> None:
    spec = QualityRepairTaskSpec(
        slug="test-book",
        chapter_number=1,
        priority="high",
        task_priority=2,
        cause_ids=("flat_narration",),
        audit_row={
            "chapter_number": "1",
            "priority": "high",
            "word_count_reason": "overflow: 3102 > 3000",
        },
    )

    instructions = build_quality_repair_instructions(spec)

    assert "压缩型修复" in instructions
    assert "不得新增场景、人物或设定名" in instructions
    assert "字数不足时扩写" not in instructions


def test_acceptance_gap_length_instruction_uses_hard_band() -> None:
    spec = QualityRepairTaskSpec(
        slug="human-nature-game-1779104692",
        chapter_number=2,
        priority="critical",
        task_priority=1,
        cause_ids=("LENGTH_STABILITY_BELOW_BAR", "SCORECARD_BELOW_ACCEPTANCE_BAR"),
        patch_points=(),
        audit_row={
            "target_word_count": "2200",
            "char_count": "2950",
            "word_count_reason": "overflow",
            "length_ratio": "1.3409",
        },
    )

    instructions = build_quality_repair_instructions(spec)

    assert "长度稳定性压缩任务" in instructions
    assert "中文汉字安全带" in instructions
    assert "禁止新增人物" in instructions
    assert "直接提升整书 scorecard" in instructions


def test_build_quality_repair_instructions_respects_english_language() -> None:
    spec = QualityRepairTaskSpec(
        slug="english-book",
        chapter_number=4,
        priority="high",
        task_priority=2,
        cause_ids=("flat_narration", "weak_attraction"),
        language="en-US",
        audit_row={
            "language": "en-US",
            "count_unit": "english_words",
            "char_count": "1450",
            "word_count_reason": "underflow: 1450 < 2000",
            "pulse_density": "0.25",
            "pulse_threshold": "1.0",
            "pulse_count": "2",
        },
    )

    instructions = build_quality_repair_instructions(spec)

    assert "Language: English" in instructions
    assert "English serial-fiction quality gates" in instructions
    assert "English words" in instructions
    assert "中文汉字安全带" not in instructions
    assert "内部质量门按中文汉字数计数" not in instructions


def test_append_quality_gate_feedback_adds_hard_gate_actions_once() -> None:
    instructions = append_quality_gate_feedback(
        "基础要求",
        (
            {"code": "LENGTH_UNDER", "detail": "1752 chars < min 1800"},
            {"code": "CLIFFHANGER_REPEAT", "detail": "body_reaction repeated"},
            {"code": "CANON_FORBIDDEN_TERM", "detail": "forbidden term"},
        ),
    )
    rebuilt = append_quality_gate_feedback(
        instructions,
        ({"code": "LENGTH_UNDER", "detail": "1752 chars < min 1800"},),
    )

    assert "最近质量门硬阻断" in instructions
    assert re.search(r"\d+-\d+ 个中文汉字的安全带内", instructions)
    assert "结尾钩子必须换型" in instructions
    assert "禁用词和越池命名是硬门" in instructions
    assert rebuilt.count("最近质量门硬阻断") == 1


def test_append_quality_gate_feedback_includes_opening_and_golden_weaks() -> None:
    instructions = append_quality_gate_feedback(
        "基础要求",
        (
            {"code": "OPENING_ENTITY_OVERLOAD", "detail": "15 entities in first 150 lines"},
            {"code": "GOLDEN_THREE_WEAK", "detail": "no hook keyword"},
        ),
    )

    assert "开场命名约束" in instructions
    assert "第一章在前 1000 字内必须出现明确卖点/钩子关键词之一" in instructions
    assert "黄金三章硬门修复" in instructions


def test_append_quality_gate_feedback_respects_english_language() -> None:
    instructions = append_quality_gate_feedback(
        "Language: English\nBase requirements",
        (
            {"code": "LENGTH_UNDER", "detail": "1450 words < min 2000"},
            {"code": "OPENING_ENTITY_OVERLOAD", "detail": "too many names"},
        ),
    )

    assert "Recent hard quality-gate blocks" in instructions
    assert "English words safe band" in instructions
    assert "Opening naming constraint" in instructions
    assert "中文汉字" not in instructions


def test_append_previous_rewrite_failure_feedback_adds_candidate_failures_once() -> None:
    instructions = append_previous_rewrite_failure_feedback(
        "基础要求",
        (
            {
                "candidate_word_count": 2270,
                "findings": [
                    {
                        "code": "QUALITY_RETROFIT_WEAK_ATTRACTION",
                        "detail": "pulse_density=0.66 < 1.00; pulse_count=5",
                    },
                    {
                        "code": "QUALITY_RETROFIT_FLAT_NARRATION",
                        "detail": "rhythm_types=2/3; missing_types=延宕停拍",
                    }
                ],
            },
        ),
    )
    rebuilt = append_previous_rewrite_failure_feedback(
        instructions,
        (
            {
                "candidate_word_count": 2270,
                "findings": [
                    {
                        "code": "QUALITY_RETROFIT_WEAK_ATTRACTION",
                        "detail": "pulse_density=0.66 < 1.00; pulse_count=5",
                    }
                ],
            },
        ),
    )

    assert "最近失败候选稿反馈" in instructions
    assert "pulse_density=0.66" in instructions
    assert "9-10 个分散的压力" in instructions
    assert "至少 3 类节奏锚点" in instructions
    assert "停了一拍" in instructions
    assert rebuilt.count("最近失败候选稿反馈") == 1


def test_append_previous_rewrite_failure_feedback_respects_english_language() -> None:
    instructions = append_previous_rewrite_failure_feedback(
        "Language: English\nBase requirements",
        (
            {
                "candidate_word_count": 1450,
                "findings": [
                    {
                        "code": "QUALITY_RETROFIT_WEAK_ATTRACTION",
                        "detail": "pulse_density=0.20 < 1.00",
                    },
                    {
                        "code": "LENGTH_UNDER",
                        "detail": "1450 words < min 2000",
                    },
                ],
            },
        ),
    )

    assert "Recent failed candidate feedback" in instructions
    assert "English words as the safe band" in instructions
    assert "real pressure nodes" in instructions
    assert "中文汉字" not in instructions


def test_append_previous_rewrite_failure_feedback_escalates_repeated_failures() -> None:
    instructions = append_previous_rewrite_failure_feedback(
        "基础要求",
        (
            {
                "candidate_word_count": 2270,
                "findings": [
                    {"code": "QUALITY_RETROFIT_WEAK_ATTRACTION", "detail": "pulse_density=0.66 < 1.00"},
                    {"code": "QUALITY_RETROFIT_FLAT_NARRATION", "detail": "rhythm_types=2/3"},
                ],
            },
            {
                "candidate_word_count": 2260,
                "findings": [
                    {"code": "QUALITY_RETROFIT_WEAK_ATTRACTION", "detail": "pulse_density=0.57 < 1.00"},
                    {"code": "QUALITY_RETROFIT_FLAT_NARRATION", "detail": "rhythm_types=2/3"},
                ],
            },
            {
                "candidate_word_count": 2300,
                "findings": [
                    {"code": "QUALITY_RETROFIT_WEAK_ATTRACTION", "detail": "pressure points missing"},
                ],
            },
        ),
    )

    assert "重复失败闭环修复约束" in instructions
    assert "牵引类失败重复触发" in instructions
    assert "节奏类失败重复触发" in instructions
    assert "至少 45% 段落应与当前草稿不同" in instructions


def test_append_previous_rewrite_failure_feedback_escalates_opening_golden_repeats() -> None:
    instructions = append_previous_rewrite_failure_feedback(
        "基础要求",
        (
            {
                "candidate_word_count": 1800,
                "findings": [
                    {
                        "code": "OPENING_ENTITY_OVERLOAD",
                        "detail": "16 entities in head",
                    },
                    {
                        "code": "GOLDEN_THREE_WEAK",
                        "detail": "no promise in first 1000 chars",
                    },
                ],
            },
            {
                "candidate_word_count": 1820,
                "findings": [
                    {"code": "OPENING_ENTITY_OVERLOAD", "detail": "too many entities"},
                    {"code": "GOLDEN_THREE_WEAK", "detail": "no hype trigger"},
                ],
            },
        ),
    )

    assert "重复失败闭环修复约束" in instructions
    assert "开场命名失败重复触发" in instructions
    assert "黄金三章弱化重复触发" in instructions
    assert "上一版仍命名过载" in instructions
    assert "黄金三章重写指令" in instructions


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
        audit_row={
            "chapter_number": "7",
            "priority": "critical",
            "platform": "qimao",
            "language": "zh-CN",
            "word_count_passed": "False",
            "word_count_reason": "underflow: 1400 < 2500",
            "char_count": "1400",
            "count_unit": "cjk_chars",
        },
    )
    session = FakeSession(scalar_results=[chapter], execute_results=[[], []])

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
    assert task.metadata_json["quality_failure_events"][0]["code"] == "WORD_COUNT_UNDERFLOW"
    assert (
        task.metadata_json["quality_failure_events"][0]["preventable_stage"]
        == "draft_generation"
    )
    assert "premium_category_hard_engine" in task.context_required


@pytest.mark.asyncio
async def test_create_quality_retrofit_rewrite_tasks_refreshes_existing_pending_task() -> None:
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
        task_priority=0,
        cause_ids=("LENGTH_STABILITY_BELOW_BAR", "SCORECARD_BELOW_ACCEPTANCE_BAR"),
        audit_row={
            "target_word_count": "2200",
            "char_count": "2950",
            "word_count_reason": "overflow",
            "length_ratio": "1.3409",
        },
    )
    session = FakeSession(scalar_results=[chapter], execute_results=[[], [existing]])

    result = await create_quality_retrofit_rewrite_tasks(session, project, [spec])

    assert result.created == 0
    assert result.skipped_existing == 1
    assert result.task_ids == (str(existing.id),)
    assert session.added == []
    assert existing.priority == 0
    assert "长度稳定性压缩任务" in existing.instructions
    assert existing.metadata_json["cause_ids"] == [
        "LENGTH_STABILITY_BELOW_BAR",
        "SCORECARD_BELOW_ACCEPTANCE_BAR",
    ]
    assert existing.metadata_json["instructions_refreshed_from_latest_spec"] is True
    assert "premium_category_hard_engine" in existing.context_required


@pytest.mark.asyncio
async def test_load_latest_quality_gate_violations_ignores_blocks_write_gate_only() -> None:
    project = _project()
    chapter = _chapter(project, 1)
    report = ChapterQualityReportModel(
        chapter_id=chapter.id,
        report_json={
            "violations": [
                {"code": "QUALITY_RETROFIT_WEAK_ATTRACTION", "detail": "pressure points不足"},
            ]
        },
        regen_attempts=0,
        blocks_write=False,
    )
    session = FakeSession(scalar_results=[report])

    violations = await load_latest_quality_gate_violations(session, chapter)

    assert violations == (
        {"code": "QUALITY_RETROFIT_WEAK_ATTRACTION", "detail": "pressure points不足"},
    )
