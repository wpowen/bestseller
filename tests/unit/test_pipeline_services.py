from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import MissingGreenlet

from bestseller.domain.chapter_generation_input import ChapterGenerationInputBundle
from bestseller.domain.context import SceneWriterContextPacket
from bestseller.domain.contradiction import ContradictionCheckResult, ContradictionViolation
from bestseller.domain.knowledge import SceneKnowledgeRefreshResult
from bestseller.domain.pipeline import ProjectPipelineResult, ProjectRepairResult
from bestseller.domain.review import ChapterReviewFinding, ChapterReviewResult, ChapterReviewScores
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExportArtifactModel,
    LlmRunModel,
    ProjectModel,
    QualityScoreModel,
    RewriteTaskModel,
    SceneCardModel,
    SceneDraftVersionModel,
    StyleGuideModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
)
from bestseller.services import contradiction as contradiction_services
from bestseller.services import drafts as draft_services
from bestseller.services import exports as export_services
from bestseller.services import identity_guard as identity_guard_services
from bestseller.services import pipelines as pipeline_services
from bestseller.services import reviews as review_services
from bestseller.services.concept_lab import build_concept_lab_catalog
from bestseller.services.truth_version import TruthVersionStaleError
from bestseller.services.write_safety_gate import WriteSafetyBlockError, WriteSafetyFinding
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def test_non_progressive_outline_replan_does_not_reuse_rejected_plan_artifact() -> None:
    """A dedicated replan must bypass the normal resume shortcut.

    Short/non-progressive books used to see an existing chapter-outline
    artifact and skip ``generate_novel_plan`` even when
    ``allow_outline_replan=True``.  The dedicated repair task then returned
    without a new artifact version and exhausted its bounded retries without
    ever changing the plan.
    """

    import inspect

    source = inspect.getsource(pipeline_services.run_autowrite_pipeline)
    resume_guard = source[source.index("if (\n        existing_plan_artifact is not None") :]
    resume_guard = resume_guard[:500]

    assert "and not allow_outline_replan" in resume_guard


def test_clean_assembly_clears_scene_auto_repair_residue() -> None:
    scene = SimpleNamespace(
        metadata_json={
            "auto_repair_hint": "补齐场景跳转桥",
            "auto_repair_block_codes": ["SCENE_JUMP_UNRESOLVED"],
            "auto_repair_adjusted_target_word_count": 440,
            "methodology_contract": {"stakes": "keep"},
        }
    )

    cleared = draft_services._clear_scene_auto_repair_residue_after_clean_assembly(
        [scene]
    )

    assert cleared == 1
    assert scene.metadata_json == {"methodology_contract": {"stakes": "keep"}}


def test_scene_auto_repair_forces_transactional_replacement_with_current_draft() -> None:
    scene = SimpleNamespace(
        status="needs_rewrite",
        metadata_json={
            "auto_repair_hint": "补强转折",
            "auto_repair_block_codes": ["BLOCK_LOW"],
        },
    )

    assert pipeline_services._scene_requires_auto_repair_generation(
        scene,
        SimpleNamespace(id=uuid4(), is_current=True),
    ) is True


def test_scene_review_without_auto_repair_does_not_replace_current_draft() -> None:
    scene = SimpleNamespace(status="reviewed", metadata_json={})

    assert pipeline_services._scene_requires_auto_repair_generation(
        scene,
        SimpleNamespace(id=uuid4(), is_current=True),
    ) is False


def test_outline_readiness_retry_clears_only_stale_auto_repair_residue() -> None:
    scenes = [
        SimpleNamespace(
            target_word_count=867,
            metadata_json={
                "auto_repair_hint": "上一轮修复残留",
                "auto_repair_block_codes": ["OLD_BLOCK"],
                "methodology_contract": {
                    "stakes": "主角必须当场验明铜钱来源。",
                    "pressure_stack": ["证据时限收窄"],
                    "focus_character": "林渊",
                    "reveal_mode": "用实物细节揭示下一步方向。",
                    "signature_image": "发烫铜钱",
                    "breakpoint": "铜钱背面露出新刻痕。",
                },
            },
        )
    ]
    report = pipeline_services.evaluate_chapter_outline_readiness(
        chapter_number=86,
        chapter_title="第86章 反扑",
        chapter_target_word_count=867,
        chapter_metadata={},
        scene_cards=scenes,
        pending_rewrite_task_count=0,
    )

    assert pipeline_services._readiness_blocked_only_by_stale_auto_repair_residue(
        report
    )
    cleared = pipeline_services._clear_stale_scene_auto_repair_residue_for_outline_retry(
        scenes
    )
    retry_report = pipeline_services.evaluate_chapter_outline_readiness(
        chapter_number=86,
        chapter_title="第86章 反扑",
        chapter_target_word_count=867,
        chapter_metadata={},
        scene_cards=scenes,
        pending_rewrite_task_count=0,
    )

    assert cleared == 1
    assert retry_report.passed is True
    assert "auto_repair_hint" not in scenes[0].metadata_json
    assert "auto_repair_block_codes" not in scenes[0].metadata_json


def test_outline_readiness_pass_clears_stale_chapter_block_metadata() -> None:
    chapter = SimpleNamespace(
        metadata_json={
            "blocked_by_chapter_outline_readiness_gate": True,
            "chapter_outline_readiness_block_codes": [
                "OUTLINE_STALE_AUTO_REPAIR_RESIDUE"
            ],
            "chapter_outline_readiness_hint": "旧阻塞提示",
            "chapter_outline_readiness_report": {"blocked": True},
            "keep": "value",
        }
    )

    cleared = pipeline_services._clear_chapter_outline_readiness_block_metadata(
        chapter,
        recovered_by="readiness_passed",
    )

    assert cleared is True
    assert chapter.metadata_json["keep"] == "value"
    assert (
        chapter.metadata_json["chapter_outline_readiness_block_cleared_by"]
        == "readiness_passed"
    )
    assert "blocked_by_chapter_outline_readiness_gate" not in chapter.metadata_json
    assert "chapter_outline_readiness_block_codes" not in chapter.metadata_json
    assert "chapter_outline_readiness_hint" not in chapter.metadata_json
    assert "chapter_outline_readiness_report" not in chapter.metadata_json


def test_chapter_first_uses_minimax_safe_cap_not_target_length_cap() -> None:
    settings = load_settings(
        env={
            "BESTSELLER__LLM__WRITER__MODEL": "openai/MiniMax-M2.7-highspeed",
            "BESTSELLER__LLM__WRITER__MODEL_OVERRIDE": "openai/MiniMax-M2.7-highspeed",
            "BESTSELLER__LLM__WRITER__MAX_TOKENS": "32768",
        }
    )

    assert draft_services.chapter_first_runaway_max_tokens(settings) == 5_488


def test_chapter_first_falls_back_to_model_family_ceiling() -> None:
    settings = load_settings(
        env={
            "BESTSELLER__LLM__WRITER__MODEL": "openai/MiniMax-M2.7-highspeed",
            "BESTSELLER__LLM__WRITER__MODEL_OVERRIDE": "openai/MiniMax-M2.7-highspeed",
            "BESTSELLER__LLM__WRITER__MAX_TOKENS": "0",
        }
    )

    assert draft_services.chapter_first_runaway_max_tokens(settings) == 5_488


def test_chapter_first_keeps_provider_safe_minimax_cap_for_full_chapters() -> None:
    settings = load_settings(
        env={
            "BESTSELLER__LLM__WRITER__MODEL": "openai/MiniMax-M2.7-highspeed",
            "BESTSELLER__LLM__WRITER__MODEL_OVERRIDE": "openai/MiniMax-M2.7-highspeed",
            "BESTSELLER__LLM__WRITER__MAX_TOKENS": "32768",
        }
    )

    cap = draft_services.chapter_first_runaway_max_tokens(
        settings,
        target_word_count=2600,
        hard_max_word_count=3500,
        language="zh-CN",
    )

    assert cap is not None
    assert cap == 5_488


def test_chapter_first_full_regeneration_for_severe_under_length() -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.target_word_count = 2200
    draft = SimpleNamespace(word_count=1424, content_md="")

    reason = pipeline_services._chapter_first_full_regeneration_reason(
        project,
        chapter,
        draft,
        ("LENGTH_UNDER", "CHAPTER_LENGTH_BLOCK_LOW"),
        attempt_number=1,
    )

    assert reason is not None
    assert reason.startswith("severe_under_length:")


def test_chapter_first_infers_low_side_from_generic_length_band_code() -> None:
    project = build_project()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "words_per_chapter": {"min": 2500, "target": 2800, "max": 3500},
    }
    chapter = build_chapter(project.id)
    chapter.target_word_count = 2800
    draft = SimpleNamespace(word_count=2087, content_md="")

    reason = pipeline_services._chapter_first_full_regeneration_reason(
        project,
        chapter,
        draft,
        ("OPENING_PRESSURE_THIN", "LENGTH_OUT_OF_BAND"),
        attempt_number=1,
    )

    assert reason is not None
    assert reason.startswith("severe_under_length:")


def test_chapter_first_fallback_count_ignores_latin_padding_for_chinese() -> None:
    project = build_project()
    project.language = "zh-CN"
    chapter = build_chapter(project.id)
    chapter.target_word_count = 2600
    draft = SimpleNamespace(word_count=0, content_md=("汉" * 2600) + (" latin" * 5000))

    reason = pipeline_services._chapter_first_full_regeneration_reason(
        project,
        chapter,
        draft,
        ("LENGTH_OUT_OF_BAND",),
        attempt_number=1,
    )

    assert reason is None


def test_resume_length_recheck_ignores_latin_padding_and_stale_counts() -> None:
    draft = SimpleNamespace(
        content_md=("汉" * 2600) + (" latin" * 5000),
        word_count=7600,
    )

    needs_recheck, actual = (
        pipeline_services._existing_chapter_draft_needs_length_recheck(
            draft,
            language="zh-CN",
            hard_min=1800,
            hard_max=3500,
        )
    )

    assert needs_recheck is False
    assert actual == 2600


def test_chapter_first_full_regeneration_stops_at_project_limit() -> None:
    project = build_project()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "chapter_first_full_regeneration_max_attempts": 1,
    }
    chapter = build_chapter(project.id)
    chapter.target_word_count = 2800
    chapter.metadata_json = {"chapter_first_full_regeneration_count": 1}
    draft = SimpleNamespace(word_count=1400, content_md="")

    reason = pipeline_services._chapter_first_full_regeneration_reason(
        project,
        chapter,
        draft,
        ("LENGTH_UNDER", "CHAPTER_LENGTH_BLOCK_LOW"),
        attempt_number=2,
    )

    assert reason is None


def test_chapter_first_allows_local_repair_for_minor_under_length() -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.target_word_count = 2200
    draft = SimpleNamespace(word_count=1900, content_md="")

    reason = pipeline_services._chapter_first_full_regeneration_reason(
        project,
        chapter,
        draft,
        ("LENGTH_UNDER",),
        attempt_number=1,
    )

    assert reason is None


def test_chapter_first_full_regeneration_for_repeated_front10_structural_block() -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.chapter_number = 2
    draft = SimpleNamespace(word_count=2200, content_md="")

    reason = pipeline_services._chapter_first_full_regeneration_reason(
        project,
        chapter,
        draft,
        ("FRONT10_RULE_LECTURE_DENSITY",),
        attempt_number=2,
    )

    assert reason == "repeated_front10_structural_block:FRONT10_RULE_LECTURE_DENSITY"


def test_chapter_first_full_regeneration_for_front10_hard_contract_pollution() -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.chapter_number = 2
    draft = SimpleNamespace(word_count=2200, content_md="")

    reason = pipeline_services._chapter_first_full_regeneration_reason(
        project,
        chapter,
        draft,
        ("FRONT10_FORBIDDEN_SIGNAL",),
        attempt_number=1,
    )

    assert reason == "front10_hard_contract_polluted:FRONT10_FORBIDDEN_SIGNAL"


def test_chapter_review_full_regeneration_for_very_low_score() -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    draft = SimpleNamespace(word_count=2200, content_md="")
    report = SimpleNamespace(report_json={"blocking_codes": []})
    # Mid-band low scores (e.g. 0.57) stay on targeted rewrite; only
    # catastrophic scores force whole-chapter regeneration.
    quality = SimpleNamespace(score_overall=0.57)
    reason_mid = pipeline_services._chapter_review_full_regeneration_reason(
        project,
        chapter,
        draft,
        report,
        quality,
        rewrite_iterations=0,
    )
    assert reason_mid is None

    quality_catastrophic = SimpleNamespace(score_overall=0.45)
    reason = pipeline_services._chapter_review_full_regeneration_reason(
        project,
        chapter,
        draft,
        report,
        quality_catastrophic,
        rewrite_iterations=0,
    )
    assert reason == "very_low_review_score:0.45"


def test_length_repair_codes_follow_current_draft_direction() -> None:
    codes = draft_services._drop_conflicting_length_repair_codes(
        (
            "LENGTH_UNDER",
            "CHAPTER_LENGTH_BLOCK_LOW",
            "LENGTH_OVER",
            "CHAPTER_LENGTH_BLOCK_HIGH",
            "FRONT10_RULE_LECTURE_DENSITY",
        ),
        length_payload={
            "band": "BLOCK_LOW",
            "issue_code": "CHAPTER_LENGTH_BLOCK_LOW",
            "word_count": 1250,
            "target_words": 2200,
        },
    )

    assert "LENGTH_UNDER" in codes
    assert "CHAPTER_LENGTH_BLOCK_LOW" in codes
    assert "FRONT10_RULE_LECTURE_DENSITY" in codes
    assert "LENGTH_OVER" not in codes
    assert "CHAPTER_LENGTH_BLOCK_HIGH" not in codes


def test_volume_outline_auto_repair_constraints_are_exact_count_directives() -> None:
    constraints = pipeline_services._volume_outline_auto_repair_constraints(
        language="zh-CN",
        volume_number=2,
        expected_count=50,
        error_message="returned 40/50 chapters",
    )

    assert any("第2卷" in item for item in constraints)
    assert any("50 个章节对象" in item for item in constraints)
    assert any("不得" in item for item in constraints)


def test_volume_outline_auto_repairable_accepts_bounded_structural_failures() -> None:
    assert pipeline_services._is_volume_outline_auto_repairable(
        RuntimeError("failed chapter-outline repair loop: returned 40/50 chapters")
    )
    assert pipeline_services._is_volume_outline_auto_repairable(
        RuntimeError(
            "Planner artifact 'volume_1_chapter_outline' has degenerate outline "
            "fields: ch2 chapter_goal≈main_conflict(sim=1.0)"
        )
    )
    assert pipeline_services._is_volume_outline_auto_repairable(
        RuntimeError(
            "Planner artifact failed semantic promotion: "
            "OUTLINE_INFORMATION_CONTRACT_GAP@ch4"
        )
    )
    assert not pipeline_services._is_volume_outline_auto_repairable(
        RuntimeError("Prewrite readiness gate failed")
    )
    assert pipeline_services._volume_outline_auto_repair_reason(
        RuntimeError("failed chapter-outline repair loop: returned 40/50 chapters")
    ) == "chapter_outline_count_contract"
    assert pipeline_services._volume_outline_auto_repair_reason(
        RuntimeError("Planner artifact failed semantic promotion")
    ) == "aggregate_semantic_contract"


def test_volume_outline_aggregate_repair_constraints_preserve_budget() -> None:
    constraints = pipeline_services._volume_outline_auto_repair_constraints(
        language="zh-CN",
        volume_number=1,
        expected_count=9,
        error_message=(
            "has degenerate outline fields: "
            "ch2 chapter_goal≈main_conflict(sim=1.0)"
        ),
    )

    assert any("汇总硬合同自动修复" in item for item in constraints)
    assert any("恰好 9 章" in item for item in constraints)
    assert any("ch2" in item for item in constraints)


def test_chapter_first_auto_repair_instruction_is_patch_first() -> None:
    project_id = uuid4()
    chapter = build_chapter(project_id)
    chapter.metadata_json = {"retention_retry_strict_prompt": "必须保留章末主钩子。"}

    instructions = pipeline_services._render_chapter_first_local_repair_instructions(
        chapter=chapter,
        block_codes=("TIMELINE_INCONSISTENT",),
        scene_hints=["补齐第2场到第3场的时间桥。"],
    )

    assert "局部替换优先" in instructions
    assert "不是重新生成章节" in instructions
    assert "不得大幅扩写" in instructions
    assert "patch-first" in instructions
    assert "补齐第2场到第3场的时间桥" in instructions
    assert "必须保留章末主钩子" in instructions


def test_chapter_first_auto_repair_instruction_rebuilds_structural_opening() -> None:
    project_id = uuid4()
    chapter = build_chapter(project_id)
    chapter.opening_situation = "林渊赶到十七栋楼下，先看到楼道现场异常。"
    chapter.metadata_json = {
        "object_signal_contract": {"forbidden_signals": ["铜钱发烫"]}
    }

    instructions = pipeline_services._render_chapter_first_local_repair_instructions(
        chapter=chapter,
        block_codes=("OPENING_SCENE_DRIFT", "FRONT10_FORBIDDEN_SIGNAL"),
        scene_hints=["删除电话桥段，重写首场现场入场。"],
    )

    assert "结构性开篇修复合同" in instructions
    assert "不按普通 patch-first" in instructions
    assert "必须重写开篇前500字" in instructions
    assert "首场使用了章节合同未规划的【禁用通联转送桥段】" in instructions
    assert "媒介不是绝对禁用" in instructions
    assert "第一场开局合同：林渊赶到十七栋楼下" in instructions
    assert "铜钱发烫" not in instructions
    assert "【物件触感捷径】" in instructions
    # structural-repair contract caps length with a 3500-char hard ceiling.
    assert "硬上限 3500 字" in instructions


def test_chapter_first_auto_repair_instruction_treats_scene_forbidden_as_structural() -> None:
    project_id = uuid4()
    chapter = build_chapter(project_id)
    chapter.opening_situation = "林渊赶到十七栋楼下，直接进入现场。"
    scene = build_scene(project_id, chapter.id)
    scene.forbidden_actions = ["不得写电话、来电、手机通知、寄件、快递、外卖、配送、物流、跑腿。"]

    instructions = pipeline_services._render_chapter_first_local_repair_instructions(
        chapter=chapter,
        block_codes=("FRONT10_SCENE_FORBIDDEN_ACTION",),
        scene_hints=["删除电话桥段，重写首场现场入场。"],
        scenes=[scene],
    )

    assert "结构性开篇修复合同" in instructions
    assert "局部替换优先" not in instructions
    assert "电话" not in instructions
    assert "手机" not in instructions
    assert "快递" not in instructions
    assert "【禁用通联转送桥段】" in instructions


def test_chapter_first_auto_repair_instruction_includes_scene_hard_constraints() -> None:
    project_id = uuid4()
    chapter = build_chapter(project_id)
    # 去同质化 P0-1: redaction now sources terms from PER-BOOK metadata
    # (object_signal_contract / foreshadowing) + a genre-agnostic modern-tech
    # candidate list — NOT one book's hardcoded horror nouns.
    chapter.metadata_json = {
        "object_signal_contract": {"forbidden_signals": ["铜钱吸力"]}
    }
    chapter.foreshadowing_actions = {"forbidden_early_leaks": ["归人"]}
    scene = build_scene(project_id, chapter.id)
    scene.forbidden_actions = [
        "不得用快递、外卖、配送作为章末钩子。",
    ]

    instructions = pipeline_services._render_chapter_first_local_repair_instructions(
        chapter=chapter,
        block_codes=("LENGTH_OVER",),
        scene_hints=["压缩重复解释，保持场景出口。"],
        scenes=[scene],
    )

    assert "章节硬约束优先级" in instructions
    # metadata-driven per-book forbidden signals are redacted to placeholders
    assert "铜钱吸力" not in instructions
    assert "归人" not in instructions
    # generic modern-tech immersion-breakers are redacted too
    assert "快递" not in instructions
    assert "【禁用通联转送桥段】" in instructions
    assert "【暂缓长线信息】" in instructions


def test_volume_checkpoint_judge_skips_immature_front_chapters() -> None:
    assert (
        review_services._should_run_volume_checkpoint_judge(
            chapter_number=1,
            interval=10,
            min_chapters=10,
        )
        is False
    )
    assert (
        review_services._should_run_volume_checkpoint_judge(
            chapter_number=10,
            interval=10,
            min_chapters=10,
        )
        is True
    )


def test_window_judge_metadata_filters_stale_repair_telemetry() -> None:
    metadata = {
        "auto_repair_last_block_codes": ["FRONT10_FORBIDDEN_SIGNAL"],
        "front10_framework_repair_last_block_codes": ["TIMELINE_INCONSISTENT"],
        "methodology_contract": {
            "chapter_function": "golden_three",
            "retention_retry_last_block_codes": ["DIALOGUE_PING_PONG"],
            "scene_contract": {"required_payoff": "张建军被门吞掉"},
        },
        "quality_targets": {
            "opening_pull": 0.86,
            "gate_last_findings": ["old failure"],
        },
        "unrelated": "should not be sent",
    }

    safe = review_services._window_judge_safe_metadata(metadata)

    assert "auto_repair_last_block_codes" not in safe
    assert "front10_framework_repair_last_block_codes" not in safe
    assert "unrelated" not in safe
    assert safe["methodology_contract"] == {
        "chapter_function": "golden_three",
        "scene_contract": {"required_payoff": "张建军被门吞掉"},
    }
    assert safe["quality_targets"] == {"opening_pull": 0.86}


def test_llm_commercial_pass_downgrades_heuristic_rule_rewrite() -> None:
    review_result = ChapterReviewResult(
        verdict="rewrite",
        severity_max="high",
        scores=ChapterReviewScores(
            overall=0.72,
            goal=0.98,
            coverage=0.9,
            coherence=0.8,
            continuity=0.8,
            main_plot_progression=0.8,
            subplot_progression=0.78,
            style=0.8,
            hook=0.36,
            ending_hook_effectiveness=0.34,
            volume_mission_alignment=0.78,
            pacing_rhythm=0.8,
            character_voice_distinction=0.8,
            thematic_resonance=0.88,
            contract_alignment=0.57,
        ),
        findings=[
            ChapterReviewFinding(
                category="ending_hook_effectiveness",
                severity="high",
                message="收尾钩子不够硬。",
            ),
            ChapterReviewFinding(
                category="contract_alignment",
                severity="medium",
                message="缺失 closing_hook。",
            ),
        ],
        evidence_summary={"llm_rule_gate_conflict": {"rule_verdict": "rewrite"}},
        rewrite_instructions="请重写尾钩。",
    )
    llm_payload = {
        "pass": True,
        "overall_score": 0.91,
        "dimension_scores": {
            "hook_strength": 0.92,
            "commercial_pull": 0.91,
            "methodology_compliance": 0.94,
        },
        "blocking_issues": [],
    }

    assert review_services._can_accept_llm_pass_over_rule_rewrite(
        review_result,
        llm_payload,
    )
    downgraded = review_services._downgrade_rule_rewrite_after_llm_pass(
        review_result,
        llm_payload,
    )

    assert downgraded.verdict == "pass"
    assert downgraded.severity_max == "low"
    assert downgraded.rewrite_instructions is None
    assert downgraded.scores.overall == 0.91
    assert downgraded.scores.hook == 0.92
    assert downgraded.scores.ending_hook_effectiveness == 0.92
    assert downgraded.scores.contract_alignment == 0.94
    assert "rule_rewrite_downgraded_by_llm_pass" in downgraded.evidence_summary


def test_llm_commercial_pass_can_downgrade_opening_and_continuity_heuristics() -> None:
    review_result = ChapterReviewResult(
        verdict="rewrite",
        severity_max="high",
        scores=ChapterReviewScores(
            overall=0.61,
            goal=1.0,
            coverage=0.9,
            coherence=0.8,
            continuity=0.42,
            main_plot_progression=0.57,
            subplot_progression=0.27,
            style=0.8,
            hook=0.37,
            ending_hook_effectiveness=0.65,
            volume_mission_alignment=0.41,
            pacing_rhythm=0.61,
            character_voice_distinction=0.8,
            thematic_resonance=0.7,
            contract_alignment=0.6,
        ),
        findings=[
            ChapterReviewFinding(
                category="opening_contract",
                severity="medium",
                message="开篇锚点同义改写未命中精确短语。",
            ),
            ChapterReviewFinding(
                category="continuity",
                severity="high",
                message="连续性启发式低分。",
            ),
        ],
    )

    assert review_services._can_accept_llm_pass_over_rule_rewrite(
        review_result,
        {
            "pass": True,
            "overall_score": 0.88,
            "dimension_scores": {"commercial_pull": 0.87},
            "blocking_issues": [],
        },
    )


def test_llm_commercial_pass_does_not_downgrade_non_overridable_rule_rewrite() -> None:
    review_result = ChapterReviewResult(
        verdict="rewrite",
        severity_max="high",
        scores=ChapterReviewScores(
            overall=0.72,
            goal=0.98,
            coverage=0.9,
            coherence=0.8,
            continuity=0.8,
            main_plot_progression=0.8,
            subplot_progression=0.78,
            style=0.8,
            hook=0.36,
            ending_hook_effectiveness=0.34,
            volume_mission_alignment=0.78,
            pacing_rhythm=0.8,
            character_voice_distinction=0.8,
            thematic_resonance=0.88,
            contract_alignment=0.57,
        ),
        findings=[
            ChapterReviewFinding(
                category="name_canon",
                severity="high",
                message="出现项目角色池外姓名。",
            )
        ],
    )

    assert not review_services._can_accept_llm_pass_over_rule_rewrite(
        review_result,
        {"pass": True, "blocking_issues": [], "dimension_scores": {}, "overall_score": 0.9},
    )


def test_clear_explicit_chapter_regeneration_residue_resets_retry_state() -> None:
    chapter = SimpleNamespace(
        production_state="blocked",
        metadata_json={
            "retention_retry_count": 12,
            "retention_auto_repair_exhausted": True,
            "auto_repair_last_block_codes": ["LENGTH_OVER"],
            "methodology_contract": {"keep": True},
        },
    )

    changed = pipeline_services._clear_explicit_chapter_regeneration_residue(chapter)

    assert changed is True
    assert chapter.production_state == "ok"
    assert "retention_retry_count" not in chapter.metadata_json
    assert "auto_repair_last_block_codes" not in chapter.metadata_json
    assert chapter.metadata_json["methodology_contract"] == {"keep": True}


def test_clear_explicit_scene_regeneration_residue_preserves_dynamic_word_band() -> None:
    scenes = [
        SimpleNamespace(
            target_word_count=824,
            metadata_json={
                "auto_repair_adjusted_target_word_count": 824,
                "auto_repair_hint": "扩写",
                "methodology_contract": {"keep": True},
            },
        ),
        SimpleNamespace(
            target_word_count=824,
            metadata_json={
                "auto_repair_length_scale": 1.4,
                "methodology_contract": {"keep": True},
            },
        ),
        SimpleNamespace(
            target_word_count=824,
            metadata_json={
                "auto_repair_original_target_word_count": 550,
                "methodology_contract": {"keep": True},
            },
        ),
        SimpleNamespace(
            target_word_count=824,
            metadata_json={
                "auto_repair_source_block_code": "BLOCK_LOW",
                "auto_repair_min_scene_target_floor": 578,
                "auto_repair_scene_target_cap": 3000,
                "methodology_contract": {"keep": True},
            },
        ),
    ]

    report = pipeline_services._clear_explicit_scene_regeneration_residue(
        scenes,
        chapter_target_word_count=2200,
    )

    assert report["metadata_residue_cleared"] == 4
    assert report["target_rebalanced"] is False
    assert report["target_word_count_sum"] == 3296
    assert [scene.target_word_count for scene in scenes] == [824, 824, 824, 824]
    for scene in scenes:
        assert scene.metadata_json == {"methodology_contract": {"keep": True}}


def test_clear_explicit_scene_regeneration_residue_rebalances_true_overflow() -> None:
    scenes = [
        SimpleNamespace(target_word_count=1100, metadata_json={}),
        SimpleNamespace(target_word_count=1100, metadata_json={}),
        SimpleNamespace(target_word_count=1100, metadata_json={}),
        SimpleNamespace(target_word_count=1100, metadata_json={}),
    ]

    report = pipeline_services._clear_explicit_scene_regeneration_residue(
        scenes,
        chapter_target_word_count=2200,
    )

    assert report["target_rebalanced"] is True
    assert report["target_word_count_sum"] == 2200
    assert [scene.target_word_count for scene in scenes] == [550, 550, 550, 550]


@pytest.mark.asyncio
async def test_release_stale_auto_repair_block_when_latest_quality_report_is_clean() -> None:
    chapter = SimpleNamespace(
        id=uuid4(),
        production_state="blocked",
        metadata_json={
            "auto_repair_in_progress": True,
            "auto_repair_last_block_codes": ["SCENE_JUMP_UNRESOLVED"],
        },
    )
    report = SimpleNamespace(
        blocks_write=False,
        report_json={"violations": [], "blocking_codes": []},
    )
    session = FakeSession(scalar_results=[report])

    released = await pipeline_services._release_stale_auto_repair_block_if_latest_quality_clean(
        session,
        chapter,
    )

    assert released is True
    assert chapter.production_state == "ok"
    assert "auto_repair_in_progress" not in chapter.metadata_json
    assert chapter.metadata_json["auto_repair_resolved_by_clean_quality_report"] is True


@pytest.mark.asyncio
async def test_release_stale_block_even_after_auto_repair_metadata_was_cleared() -> None:
    chapter = SimpleNamespace(
        id=uuid4(),
        production_state="blocked",
        metadata_json={},
    )
    report = SimpleNamespace(
        blocks_write=False,
        report_json={"violations": [], "blocking_codes": []},
    )
    session = FakeSession(scalar_results=[report])

    released = await pipeline_services._release_stale_auto_repair_block_if_latest_quality_clean(
        session,
        chapter,
    )

    assert released is True
    assert chapter.production_state == "ok"
    assert chapter.metadata_json["auto_repair_resolved_by_clean_quality_report"] is True


@pytest.mark.asyncio
async def test_release_stale_auto_repair_block_preserves_other_hard_gate_blocks() -> None:
    # WS-C2: ``phase_d_time_gate`` moved to advanced tier; use a true core
    # tier key so the test still exercises "other hard gate block present".
    chapter = SimpleNamespace(
        id=uuid4(),
        production_state="blocked",
        metadata_json={
            "auto_repair_in_progress": True,
            "blocked_by_write_safety_gate": True,
        },
    )
    report = SimpleNamespace(
        blocks_write=False,
        report_json={"violations": [], "blocking_codes": []},
    )
    session = FakeSession(scalar_results=[report])

    released = await pipeline_services._release_stale_auto_repair_block_if_latest_quality_clean(
        session,
        chapter,
    )

    assert released is False
    assert chapter.production_state == "blocked"
    assert session.scalar_results == [report]


@pytest.mark.asyncio
async def test_stop_auto_repair_when_latest_quality_report_is_clean() -> None:
    chapter = SimpleNamespace(
        id=uuid4(),
        production_state="pending",
        metadata_json={
            "auto_repair_in_progress": True,
            "auto_repair_last_block_codes": ["FRONT10_FORBIDDEN_SIGNAL"],
            "quality_gate_block_codes": ["FRONT10_FORBIDDEN_SIGNAL"],
            "production_block_code": "FRONT10_FORBIDDEN_SIGNAL",
        },
    )
    report = SimpleNamespace(
        blocks_write=False,
        report_json={"violations": [], "blocking_codes": []},
    )
    scene = SimpleNamespace(
        metadata_json={
            "auto_repair_hint": "上一轮修复残留",
            "auto_repair_block_codes": ["FRONT10_FORBIDDEN_SIGNAL"],
            "methodology_contract": {"stakes": "保留"},
        }
    )
    session = FakeSession(scalar_results=[report], scalars_results=[[scene]])

    stopped = await pipeline_services._stop_auto_repair_if_latest_quality_clean(
        session,
        chapter,
    )

    assert stopped is True
    assert chapter.production_state == "pending"
    assert "auto_repair_in_progress" not in chapter.metadata_json
    assert "quality_gate_block_codes" not in chapter.metadata_json
    assert "production_block_code" not in chapter.metadata_json
    assert chapter.metadata_json["auto_repair_last_resolved_block_codes"] == [
        "FRONT10_FORBIDDEN_SIGNAL"
    ]
    assert chapter.metadata_json["auto_repair_stopped_by_clean_quality_report"] is True
    assert scene.metadata_json == {"methodology_contract": {"stakes": "保留"}}


def test_latest_quality_report_is_not_clean_when_violation_severity_blocks() -> None:
    report = SimpleNamespace(
        blocks_write=False,
        report_json={
            "blocking_codes": [],
            "violations": [
                {
                    "code": "GOLDEN_THREE_WEAK",
                    "severity": "block",
                    "detail": "golden-three hook is weak",
                }
            ],
        },
    )

    assert pipeline_services._latest_quality_report_is_clean(report) is False


def test_current_auto_repair_codes_include_fresh_metadata_audit_findings() -> None:
    chapter = SimpleNamespace(
        metadata_json={
            "auto_repair_last_block_codes": ["PAYOFF_LEDGER_LOW"],
            "deterministic_audit_latest": {
                "passed": False,
                "findings": [
                    {
                        "code": "ENDING_HOOK_MISSING",
                        "severity": "high",
                    }
                ],
            },
        }
    )

    codes = pipeline_services._current_auto_repair_block_codes(chapter)

    assert codes == (
        "PAYOFF_LEDGER_LOW",
        "ENDING_HOOK_MISSING",
    )


@pytest.mark.asyncio
async def test_stop_auto_repair_preserves_deterministic_audit_blocks() -> None:
    chapter = SimpleNamespace(
        id=uuid4(),
        production_state="pending",
        metadata_json={
            "auto_repair_in_progress": True,
            "auto_repair_last_block_codes": ["ENDING_HOOK_MISSING"],
            "deterministic_audit_latest": {
                "passed": False,
                "findings": [
                    {
                        "code": "ENDING_HOOK_MISSING",
                        "severity": "high",
                    }
                ],
            },
        },
    )
    report = SimpleNamespace(
        blocks_write=False,
        report_json={"violations": [], "blocking_codes": []},
    )
    session = FakeSession(scalar_results=[report])

    stopped = await pipeline_services._stop_auto_repair_if_latest_quality_clean(
        session,
        chapter,
    )

    assert stopped is False
    assert chapter.metadata_json["auto_repair_in_progress"] is True


@pytest.mark.asyncio
async def test_stop_auto_repair_preserves_other_hard_gate_blocks() -> None:
    # WS-C2: ``phase_d_time_gate`` moved to advanced tier; use a true core
    # tier key so the test still exercises "other hard gate block present".
    chapter = SimpleNamespace(
        id=uuid4(),
        production_state="blocked",
        metadata_json={
            "auto_repair_in_progress": True,
            "blocked_by_write_safety_gate": True,
        },
    )
    report = SimpleNamespace(
        blocks_write=False,
        report_json={"violations": [], "blocking_codes": []},
    )
    session = FakeSession(scalar_results=[report])

    stopped = await pipeline_services._stop_auto_repair_if_latest_quality_clean(
        session,
        chapter,
    )

    assert stopped is False
    assert chapter.production_state == "blocked"


def test_fanqie_short_project_forces_fanqie_prompt_pack() -> None:
    project = build_project()
    project.project_type = "fanqie_short"
    project.genre = "科幻"
    writing_profile = SimpleNamespace(
        market=SimpleNamespace(prompt_pack_key="scifi-starwar")
    )

    pack = draft_services._resolve_project_prompt_pack(project, writing_profile)

    assert pack is not None
    assert pack.key == "fanqie_short"


class FakeSession:
    def __init__(
        self,
        *,
        scalar_results: list[object | None] | None = None,
        scalars_results: list[list[object]] | None = None,
        execute_results: list[object | None] | None = None,
        get_map: dict[object, object] | None = None,
    ) -> None:
        self.scalar_results = list(scalar_results or [])
        self.scalars_results = list(scalars_results or [])
        self.execute_results = list(execute_results or [])
        self.get_map = dict(get_map or {})
        self.added: list[object] = []
        self.executed: list[object] = []
        self.is_active = True
        self.rollback_calls = 0

    def begin_nested(self):
        class _NoopNestedTransaction:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _NoopNestedTransaction()

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            table = getattr(obj, "__table__", None)
            if table is None or "id" not in table.c:
                continue
            if getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid4())

    async def scalar(self, stmt: object) -> object | None:
        if not self.scalar_results:
            return None
        return self.scalar_results.pop(0)

    async def scalars(self, stmt: object) -> list[object]:
        if not self.scalars_results:
            return []
        return self.scalars_results.pop(0)

    async def get(self, model: object, key: object) -> object | None:
        return self.get_map.get((model, key))

    async def execute(self, *args: object, **kwargs: object) -> None:
        if args:
            self.executed.append(args[0])
        if self.execute_results:
            return self.execute_results.pop(0)
        return None

    async def rollback(self) -> None:
        self.rollback_calls += 1
        self.is_active = True


@pytest.mark.asyncio
async def test_recover_session_after_nonfatal_error_rolls_back_missing_greenlet() -> None:
    session = FakeSession()

    await pipeline_services._recover_session_after_nonfatal_error(
        session,
        MissingGreenlet("expired ORM attribute attempted async IO"),
    )

    assert session.rollback_calls == 1


class FakeExecuteRows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = list(rows)

    def all(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class FakeScalarOneOrNone:
    def __init__(self, value: object | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object | None:
        return self.value


def build_settings():
    settings = load_settings(env={})
    # Most tests in this module exercise downstream chapter-pipeline behavior
    # with deliberately skeletal outline fixtures. Keep the new whole-book
    # promotion gate out of those unrelated fixtures; dedicated semantic-gate
    # tests below cover the hard-block contract explicitly.
    settings.pipeline.enable_outline_semantic_gate = False
    settings.pipeline.enable_rolling_outline = False
    return settings


def test_progressive_volume_block_continue_defaults_to_sequential() -> None:
    settings = build_settings()

    assert settings.pipeline.progressive_continue_after_volume_block is False


def test_sequential_chapter_generation_guard_defaults_enabled() -> None:
    settings = build_settings()

    assert settings.pipeline.enforce_sequential_chapter_generation is True


@pytest.mark.asyncio
async def test_load_prior_incomplete_chapter_numbers_flags_status_and_draft_gaps() -> None:
    project_id = uuid4()
    session = FakeSession(
        execute_results=[
            FakeExecuteRows(
                [
                    (1, "ok", {}, 1),
                    (
                        2,
                        "blocked",
                        {"blocked_by_material_referential_integrity_gate": True},
                        1,
                    ),
                    (3, "ok", {}, 0),
                    (4, "pending", {}, 0),
                ]
            )
        ]
    )

    result = await pipeline_services._load_prior_incomplete_chapter_numbers(
        session,
        project_id=project_id,
        before_chapter_number=5,
    )

    assert result == [2, 3, 4]


@pytest.mark.asyncio
async def test_load_prior_incomplete_chapter_numbers_ignores_local_blocks_with_drafts() -> None:
    project_id = uuid4()
    session = FakeSession(
        execute_results=[
            FakeExecuteRows(
                [
                    (
                        86,
                        "blocked",
                        {
                            "blocked_by_write_safety_gate": True,
                            "write_safety_block_code": "CHAPTER_LENGTH_BLOCK_HIGH",
                        },
                        1,
                    ),
                    (
                        87,
                        "blocked",
                        {"blocked_by_material_referential_integrity_gate": True},
                        1,
                    ),
                    (88, "ok", {}, 1),
                ]
            )
        ]
    )

    result = await pipeline_services._load_prior_incomplete_chapter_numbers(
        session,
        project_id=project_id,
        before_chapter_number=89,
    )

    assert result == [87]


def _disable_chapter_length_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the new chapter_length gate in tests that use stub-short
    mock content (≤200 zh chars). Production behavior keeps the gate on.

    Added 2026-05-23 with the chapter_length gate. Without this, every
    integration test that uses synthetic 50-char draft content gets
    flagged CHAPTER_TOO_SHORT and the chapter requires human review.
    """

    from bestseller.services import quality_gates_config

    quality_gates_config.reset_quality_gates_cache()

    original = quality_gates_config.get_quality_gates_config

    def _patched() -> Any:
        cfg = original.__wrapped__()  # bypass lru_cache
        # ``OriginalityEngineConfig`` is a frozen dataclass; rebuild it.
        import dataclasses

        new_orig = dataclasses.replace(
            cfg.originality_engine,
            chapter_length_gate_enabled=False,
            # Synthetic 50-char integration drafts also fail the reader-persona
            # simulation (high abandon / low weighted score), which would block the
            # chapter and route it to machine repair — drowning out what these
            # review/export pipeline tests actually exercise. Don't persist persona
            # feedback so the gate finds no file to evaluate.
            persist_persona_feedback=False,
        )
        # block_on_persona_failure lives on ReaderQualityGateConfig (reader_quality),
        # not OriginalityEngineConfig — also disable it so persona never blocks even
        # if a feedback file is present. (Dedicated persona-gate tests set it back.)
        new_reader_quality = dataclasses.replace(
            cfg.reader_quality,
            block_on_persona_failure=False,
        )
        return dataclasses.replace(
            cfg,
            originality_engine=new_orig,
            reader_quality=new_reader_quality,
        )

    monkeypatch.setattr(
        quality_gates_config, "get_quality_gates_config", _patched
    )
    # The pipelines.py call goes through the module-level binding.
    import bestseller.services.pipelines as pipeline_module

    monkeypatch.setattr(
        pipeline_module, "get_quality_gates_config", _patched, raising=False
    )


def test_project_story_bible_root_prefers_mode_b_layout(tmp_path: Path) -> None:
    project = build_project()
    project.metadata_json = {**(project.metadata_json or {}), "mode_b": True}
    mode_b_root = tmp_path / "ai-generated" / project.slug / "story-bible"
    mode_b_root.mkdir(parents=True)

    resolved = pipeline_services._project_story_bible_root(project, tmp_path)

    assert resolved == mode_b_root


def test_project_story_bible_root_keeps_classic_layout(tmp_path: Path) -> None:
    project = build_project()
    classic_root = tmp_path / project.slug / "story-bible"
    classic_root.mkdir(parents=True)

    resolved = pipeline_services._project_story_bible_root(project, tmp_path)

    assert resolved == classic_root


@pytest.mark.asyncio
async def test_recover_session_after_nonfatal_error_rolls_back_dirty_session() -> None:
    session = FakeSession()
    session.is_active = False

    await pipeline_services._recover_session_after_nonfatal_error(
        session,
        RuntimeError("context helper failed"),
    )

    assert session.rollback_calls == 1
    assert session.is_active is True


def test_outline_chapters_for_volume_filters_existing_cumulative_batch() -> None:
    content = {
        "batch_name": "progressive-merged-outline",
        "chapters": [
            {"chapter_number": 1, "volume_number": 1},
            {"chapter_number": 51, "volume_number": 2},
            {"chapter_number": 52, "volume_number": 2},
            {"chapter_number": 101, "volume_number": 3},
        ],
    }

    chapters = pipeline_services._outline_chapters_for_volume(content, 2)

    assert [chapter["chapter_number"] for chapter in chapters] == [51, 52]


@pytest.mark.asyncio
async def test_resume_outline_chapters_for_volume_requires_expected_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = type(
        "ArtifactStub",
        (),
        {
            "content": {
                "chapters": [
                    {"chapter_number": 51, "volume_number": 2},
                    {"chapter_number": 52, "volume_number": 2},
                ]
            }
        },
    )()

    async def fake_get_latest_planning_artifact(session, *, project_id, artifact_type):
        assert artifact_type == pipeline_services.ArtifactType.CHAPTER_OUTLINE_BATCH
        return artifact

    monkeypatch.setattr(
        pipeline_services,
        "get_latest_planning_artifact",
        fake_get_latest_planning_artifact,
    )

    enough = await pipeline_services._resume_outline_chapters_for_volume(
        FakeSession(),
        project_id=uuid4(),
        volume_number=2,
        expected_count=2,
    )
    too_few = await pipeline_services._resume_outline_chapters_for_volume(
        FakeSession(),
        project_id=uuid4(),
        volume_number=2,
        expected_count=3,
    )

    assert len(enough) == 2
    assert too_few == []


def build_project() -> ProjectModel:
    # target_chapters kept <= PROGRESSIVE_CHAPTER_THRESHOLD so autowrite tests
    # exercising the non-progressive path aren't silently rerouted by the
    # target-based trigger in run_autowrite_pipeline.
    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="fantasy",
        target_word_count=60000,
        target_chapters=30,
        metadata_json={
            "identity_manifest_status": "locked",
            "identity_manifest": [
                {
                    "name": "沈砚",
                    "role": "protagonist",
                    "gender": "male",
                    "pronoun_set_zh": "他",
                    "pronoun_set_en": "he/him",
                    "aliases": [],
                },
                {
                    "name": "港务官",
                    "role": "supporting",
                    "gender": "female",
                    "pronoun_set_zh": "她",
                    "pronoun_set_en": "she/her",
                    "aliases": [],
                },
            ],
        },
    )
    project.id = uuid4()
    return project


def mark_project_blocked_for_structural_repair(project: ProjectModel) -> None:
    project.status = "paused"
    project.metadata_json = {
        **(project.metadata_json or {}),
        "production_paused": True,
        "production_pause_reason": "structural_repair_before_continuation",
        "generation_resume_blocked_until_repair_audit": True,
    }


def build_chapter(project_id) -> ChapterModel:
    chapter = ChapterModel(
        project_id=project_id,
        chapter_number=1,
        title="失准星图",
        chapter_goal="展示主线冲突",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=3000,
    )
    chapter.id = uuid4()
    return chapter


@pytest.mark.asyncio
async def test_deterministic_length_trim_before_export_clears_sole_overlength_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bestseller.services import chapter_quality_bundle
    from bestseller.services.chapter_length_gate import count_zh_chars

    class _Report:
        def __init__(self, *, over_max: bool) -> None:
            self.blocking_findings = (
                [
                    SimpleNamespace(
                        code="CHAPTER_LENGTH_BLOCK_HIGH",
                        evidence={"hard_max": 3000},
                    )
                ]
                if over_max
                else []
            )

        def to_dict(self) -> dict[str, object]:
            return {
                "passed": not self.blocking_findings,
                "blocking_codes": [
                    finding.code for finding in self.blocking_findings
                ],
            }

    def _fake_bundle(text: str, context: object) -> _Report:
        return _Report(over_max=count_zh_chars(text) > 3000)

    monkeypatch.setattr(
        chapter_quality_bundle,
        "run_chapter_quality_bundle",
        _fake_bundle,
    )

    project = build_project()
    project.target_chapters = 500
    project.language = "zh-CN"
    chapter = build_chapter(project.id)
    chapter.chapter_number = 2
    chapter.target_word_count = 2000
    chapter.production_state = "blocked"
    chapter.metadata_json = {
        "quality_bundle_blocking_codes": ["CHAPTER_LENGTH_BLOCK_HIGH"],
        "quality_gate_block_codes": ["CHAPTER_LENGTH_BLOCK_HIGH"],
        "chapter_review_attempts_active": 3,
        "quality_bundle": {"passed": False},
    }
    blocks = [
        "".join(chr(0x4E00 + ((start + i) % 2000)) for i in range(1200))
        for start in (0, 1200, 2400)
    ]
    chapter_text = "# 第2章 临聘编号\n\n" + "\n\n".join(blocks)
    draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md=chapter_text,
        word_count=draft_services.count_words(chapter_text),
        is_current=True,
    )
    draft.id = uuid4()
    session = FakeSession(scalar_results=[None])

    applied = await pipeline_services._maybe_apply_deterministic_length_trim_before_export(
        session,
        settings=build_settings(),
        project=project,
        chapter=chapter,
        chapter_draft=draft,
        chapter_number=2,
    )

    assert applied is True
    assert count_zh_chars(draft.content_md) <= 3000
    from bestseller.services.chapter_word_count_truth import (
        authoritative_zh_word_count,
    )

    assert draft.word_count == authoritative_zh_word_count(
        draft.content_md,
        language=project.language,
    )
    assert chapter.current_word_count == draft.word_count
    assert chapter.production_state == "ok"
    assert chapter.status == "revision"
    assert chapter.metadata_json["quality_bundle"] == {
        "passed": True,
        "blocking_codes": [],
    }
    assert chapter.metadata_json["deterministic_length_trim"]["hard_max"] == 3000
    assert "quality_bundle_blocking_codes" not in chapter.metadata_json
    assert "quality_gate_block_codes" not in chapter.metadata_json
    assert "chapter_review_attempts_active" not in chapter.metadata_json


@pytest.mark.asyncio
async def test_deterministic_hook_echo_bridge_before_review_clears_hook_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bestseller.services import chapter_quality_bundle

    class _Report:
        def __init__(self, *, hook_block: bool) -> None:
            self.blocking_findings = (
                [
                    SimpleNamespace(
                        code="HOOK_ECHO_MISSING",
                        evidence={
                            "missed_tokens": [
                                "名单",
                                "回执",
                                "陆临聘",
                                "加密频",
                            ]
                        },
                    )
                ]
                if hook_block
                else []
            )

        def to_dict(self) -> dict[str, object]:
            return {
                "passed": not self.blocking_findings,
                "blocking_codes": [
                    finding.code for finding in self.blocking_findings
                ],
            }

    calls = {"count": 0}

    def _fake_bundle(text: str, context: object) -> _Report:
        calls["count"] += 1
        return _Report(hook_block=calls["count"] == 1)

    monkeypatch.setattr(
        chapter_quality_bundle,
        "run_chapter_quality_bundle",
        _fake_bundle,
    )

    project = build_project()
    project.language = "zh-CN"
    chapter = build_chapter(project.id)
    chapter.chapter_number = 5
    chapter.production_state = "blocked"
    chapter.metadata_json = {
        "quality_bundle_blocking_codes": ["HOOK_ECHO_MISSING"],
        "chapter_review_attempts_active": 2,
    }
    draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第5章 加密频\n\n陆沉推开门。" + (" latin" * 5000),
        word_count=12,
        is_current=True,
    )
    draft.id = uuid4()

    applied = await pipeline_services._maybe_apply_deterministic_hook_echo_bridge_before_review(
        FakeSession(scalar_results=[None]),
        settings=build_settings(),
        project=project,
        chapter=chapter,
        chapter_draft=draft,
        chapter_number=5,
    )

    assert applied is True
    assert "名单、回执、陆临聘和加密频没有消失" in draft.content_md
    assert chapter.production_state == "ok"
    assert chapter.metadata_json["quality_bundle"] == {
        "passed": True,
        "blocking_codes": [],
    }
    assert "quality_bundle_blocking_codes" not in chapter.metadata_json
    assert "chapter_review_attempts_active" not in chapter.metadata_json
    assert draft.word_count == pipeline_services.authoritative_word_count_for_language(
        draft.content_md,
        language=project.language,
    )
    assert draft.word_count < draft_services.count_words(draft.content_md)


def build_scene(project_id, chapter_id) -> SceneCardModel:
    scene = SceneCardModel(
        project_id=project_id,
        chapter_id=chapter_id,
        scene_number=1,
        scene_type="setup",
        title="封港命令",
        time_label="第一日夜，封港前一小时",
        participants=["沈砚", "港务官"],
        purpose={"story": "抛出禁令任务", "emotion": "压迫感和抗拒"},
        entry_state={},
        exit_state={},
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        metadata_json={},
        target_word_count=1000,
    )
    scene.id = uuid4()
    return scene


def test_chapter_first_prompt_uses_publish_band_not_tight_target_delta() -> None:
    project = build_project()
    project.language = "zh-CN"
    chapter = build_chapter(project.id)
    chapter.target_word_count = 2200
    scene = build_scene(project.id, chapter.id)
    scene.metadata_json = {"auto_repair_hint": "补入林正淳和青囊线索，但不要扩写新场景。"}
    context_packet = SimpleNamespace(
        chapter_contract=None,
        hard_fact_snapshot=None,
        chapter_length_block=None,
        timeline_canon_block=None,
        character_role_block=None,
        dialogue_voice_block=None,
        scene_coherence_block=None,
        canon_guardrails_block=None,
        reader_contract_block=None,
        hype_constraints_block=None,
        hook_echo_block=None,
        exposition_density_block=None,
        voice_dna_block=None,
        chapter_market_constraints_block=None,
        signature_scene_block=None,
        prior_persona_feedback_block=None,
        story_bible={},
        previous_scene_summaries=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        recent_timeline_events=[],
        retrieval_chunks=[],
    )

    _, user_prompt = draft_services.build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        context_packet,
        target_word_count=chapter.target_word_count,
        # Explicit full profile: these blocks are lean-dropped by design
        # (plan §4.3); this test covers what the block itself states.
        prose_prompt_profile="full",
    )

    # zh publish band is the wide 1800-3500 (target 2200), not a tight target±delta.
    assert "发布硬范围 1800-3500 字" in user_prompt
    assert "篇幅硬范围是 1800-3500 个汉字" in user_prompt
    assert "补入林正淳和青囊线索" not in user_prompt
    assert "2024-2376" not in user_prompt


def test_chapter_first_prompt_includes_selected_concept_lab_contract() -> None:
    project = build_project()
    project.language = "zh-CN"
    bundle = build_concept_lab_catalog("apocalypse-supply", count=1).bundles[0]
    project.metadata_json = {
        **(project.metadata_json or {}),
        "concept_lab": bundle.model_dump(mode="json"),
    }
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    context_packet = SimpleNamespace(
        chapter_contract=None,
        hard_fact_snapshot=None,
        chapter_length_block=None,
        timeline_canon_block=None,
        character_role_block=None,
        dialogue_voice_block=None,
        scene_coherence_block=None,
        canon_guardrails_block=None,
        reader_contract_block=None,
        hype_constraints_block=None,
        hook_echo_block=None,
        exposition_density_block=None,
        voice_dna_block=None,
        chapter_market_constraints_block=None,
        signature_scene_block=None,
        prior_persona_feedback_block=None,
        story_bible={},
        previous_scene_summaries=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        recent_timeline_events=[],
        retrieval_chunks=[],
    )

    _, user_prompt = draft_services.build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        context_packet,
        target_word_count=chapter.target_word_count,
    )

    assert "【已选脑洞组合合同】" in user_prompt
    assert bundle.reader_promise in user_prompt
    assert "per_chapter_contract" in user_prompt


def test_chapter_first_prompt_uses_current_scene_contract_not_stale_metadata() -> None:
    project = build_project()
    project.language = "zh-CN"
    chapter = build_chapter(project.id)
    chapter.target_word_count = 2600
    scene = build_scene(project.id, chapter.id)
    scene.hook_requirement = "电梯门开着，里面没有轿厢。"
    scene.metadata_json = {
        "methodology_contract": {
            "visible_action_or_reaction": "林渊停在电梯口，不让王建业靠近井口。",
            "signature_image": "空电梯井壁映出一张无脸影子。",
            "cut_point": "电梯井里传出第二个王建业的笑声。",
            "information_control_mode": "现场证据先行，不解释完整规则。",
        },
        "scene_contract": {
            "visible_object": "空电梯井壁映出一张无脸影子。",
            "exit_hook": "电梯门开着，里面没有轿厢。",
        },
        "cut_point": "电话里响起第二个王建业的笑声。",
        "action_sequence": ["铜钱烫醒旧疤"],
        "auto_repair_hint": "上一轮要求电话开场。",
        "signature_image": "康熙铜钱发烫。",
    }
    context_packet = SimpleNamespace(
        chapter_contract=None,
        hard_fact_snapshot=None,
        chapter_length_block=None,
        timeline_canon_block=None,
        character_role_block=None,
        dialogue_voice_block=None,
        scene_coherence_block=None,
        canon_guardrails_block=None,
        reader_contract_block=None,
        hype_constraints_block=None,
        hook_echo_block=None,
        exposition_density_block=None,
        voice_dna_block=None,
        chapter_market_constraints_block=None,
        signature_scene_block=None,
        prior_persona_feedback_block=None,
        story_bible={},
        previous_scene_summaries=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        recent_timeline_events=[],
        retrieval_chunks=[],
    )

    _, user_prompt = draft_services.build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        context_packet,
        target_word_count=chapter.target_word_count,
    )

    assert "空电梯井壁映出一张无脸影子" in user_prompt
    assert "电梯井里传出第二个王建业的笑声" in user_prompt
    assert "电话里响起第二个王建业" not in user_prompt
    assert "铜钱烫醒旧疤" not in user_prompt
    assert "上一轮要求电话开场" not in user_prompt


def test_chapter_first_prompt_prioritizes_previous_chapter_ending() -> None:
    project = build_project()
    project.language = "zh-CN"
    chapter = build_chapter(project.id)
    chapter.chapter_number = 2
    chapter.chapter_goal = "承接张建军上门；本章旧细纲含王建业失踪前的模糊表述。"
    chapter.target_word_count = 2200
    scene = build_scene(project.id, chapter.id)
    scene.scene_number = 1
    context_packet = SimpleNamespace(
        chapter_number=2,
        chapter_contract=None,
        hard_fact_snapshot={
            "chapter_number": 1,
            "facts": [
                {
                    "name": "王建业状态",
                    "subject": "王建业",
                    "value": "被镜面带走后生死未明",
                    "notes": "林渊只抓住一只鞋",
                    "source_quote": "人影一闪，消失在镜子碎裂的那片白光里",
                }
            ],
        },
        chapter_length_block=None,
        timeline_canon_block=None,
        character_role_block=None,
        dialogue_voice_block=None,
        scene_coherence_block=None,
        canon_guardrails_block=None,
        reader_contract_block=None,
        hype_constraints_block=None,
        hook_echo_block=None,
        exposition_density_block=None,
        voice_dna_block=None,
        chapter_market_constraints_block=None,
        signature_scene_block=None,
        prior_persona_feedback_block=None,
        story_bible={},
        previous_scene_summaries=[
            SimpleNamespace(
                summary="上一章开头摘要不重要。",
                closing_lines="王建业惨叫后消失在镜面白光里，林渊只抓住一只鞋；门外张建军敲了三短一长。",
                extended_tail="林渊只来得及抓住王建业的一只鞋。门外响起三短一长，张建军攥着旧铁片站在门口。",
            )
        ],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        recent_timeline_events=[],
        retrieval_chunks=[],
    )

    _, user_prompt = draft_services.build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        context_packet,
        target_word_count=chapter.target_word_count,
    )

    assert "【上一章硬承接（最高优先级）】" in user_prompt
    assert "优先于本章旧细纲" in user_prompt
    assert "已被镜面带走后生死未明" in user_prompt
    assert "人影一闪，消失在镜子碎裂的那片白光里" in user_prompt
    assert user_prompt.index("【上一章硬承接（最高优先级）】") < user_prompt.index("【章节目标】")


def test_chapter_first_prompt_keeps_scene_forbidden_actions_outside_truncated_contract() -> None:
    project = build_project()
    project.language = "zh-CN"
    chapter = build_chapter(project.id)
    chapter.target_word_count = 2200
    scene = build_scene(project.id, chapter.id)
    scene.forbidden_actions = [
        "不得写“点头的声音”；点头只能是可见动作，声音只能来自钥匙、门缝、纸页、喉咙或镜面。"
    ]
    scene.metadata_json = {
        "methodology_contract": {
            f"large_contract_{index}": "复杂方法论合同" * 80 for index in range(12)
        }
    }
    context_packet = SimpleNamespace(
        chapter_contract=None,
        hard_fact_snapshot=None,
        chapter_length_block=None,
        timeline_canon_block=None,
        character_role_block=None,
        dialogue_voice_block=None,
        scene_coherence_block=None,
        canon_guardrails_block=None,
        reader_contract_block=None,
        hype_constraints_block=None,
        hook_echo_block=None,
        exposition_density_block=None,
        voice_dna_block=None,
        chapter_market_constraints_block=None,
        signature_scene_block=None,
        prior_persona_feedback_block=None,
        story_bible={},
        previous_scene_summaries=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        recent_timeline_events=[],
        retrieval_chunks=[],
    )

    _, user_prompt = draft_services.build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        context_packet,
        target_word_count=chapter.target_word_count,
    )

    assert "硬禁令" in user_prompt
    assert "不得写“点头的声音”" in user_prompt
    assert "场景执行合同" not in user_prompt
    assert "【弱场景逻辑地图】" in user_prompt


def test_chapter_first_prompt_includes_character_safety_block() -> None:
    project = build_project()
    project.language = "zh-CN"
    chapter = build_chapter(project.id)
    chapter.target_word_count = 2200
    scene = build_scene(project.id, chapter.id)
    context_packet = SimpleNamespace(
        chapter_contract=None,
        hard_fact_snapshot=None,
        chapter_length_block=None,
        timeline_canon_block=None,
        character_role_block=None,
        dialogue_voice_block=None,
        scene_coherence_block=None,
        canon_guardrails_block=None,
        reader_contract_block=None,
        hype_constraints_block=None,
        hook_echo_block=None,
        exposition_density_block=None,
        voice_dna_block=None,
        chapter_market_constraints_block=None,
        signature_scene_block=None,
        prior_persona_feedback_block=None,
        story_bible={},
        previous_scene_summaries=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        recent_timeline_events=[],
        retrieval_chunks=[],
    )

    _, user_prompt = draft_services.build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        context_packet,
        target_word_count=chapter.target_word_count,
        character_safety_block="王建业：计划死亡/退场章为第6章之后；本章禁止写成已死。",
    )

    assert "【角色生死与登场安全】" in user_prompt
    assert "王建业" in user_prompt
    assert "本章禁止写成已死" in user_prompt
    assert "已经死了，对吧？" in user_prompt


def test_chapter_first_prompt_includes_character_knowledge_boundary() -> None:
    project = build_project()
    project.language = "zh-CN"
    chapter = build_chapter(project.id)
    chapter.target_word_count = 2200
    scene = build_scene(project.id, chapter.id)
    context_packet = SimpleNamespace(
        chapter_contract=None,
        hard_fact_snapshot=None,
        chapter_length_block=None,
        timeline_canon_block=None,
        character_role_block=None,
        dialogue_voice_block=None,
        scene_coherence_block=None,
        canon_guardrails_block=None,
        reader_contract_block=None,
        hype_constraints_block=None,
        hook_echo_block=None,
        exposition_density_block=None,
        voice_dna_block=None,
        chapter_market_constraints_block=None,
        signature_scene_block=None,
        prior_persona_feedback_block=None,
        story_bible={},
        previous_scene_summaries=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        recent_timeline_events=[],
        retrieval_chunks=[],
        participant_knowledge_states=[
            {
                "character_name": "张建军",
                "knows": ["王建业在303门口失踪"],
                "unaware_of": ["认账", "入账"],
            }
        ],
    )

    _, user_prompt = draft_services.build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        context_packet,
        target_word_count=chapter.target_word_count,
    )

    assert "【角色认知边界】" in user_prompt
    assert "张建军" in user_prompt
    assert "角色的对话和行为不得超越其认知边界" in user_prompt
    assert "非专业角色只能描述自己亲眼看见的异常" in user_prompt


def test_chapter_first_prompt_enforces_scene_opening_and_front10_forbidden_terms() -> None:
    project = build_project()
    project.language = "zh-CN"
    chapter = build_chapter(project.id)
    chapter.target_word_count = 2200
    chapter.opening_situation = "23:43，林渊赶到十七栋楼下，王建业站在雨棚下等他。"
    chapter.foreshadowing_actions = {"forbidden_early_leaks": ["林远山", "源门"]}
    chapter.metadata_json = {
        "object_signal_contract": {"forbidden_signals": ["铜钱发烫"]},
    }
    chapter.foreshadowing_actions = {"forbidden_early_leaks": ["困魂镜"]}
    chapter.foreshadowing_actions = {"forbidden_early_leaks": ["困魂镜"]}
    scene = build_scene(project.id, chapter.id)
    scene.title = "十七栋楼下的空电梯"
    scene.hook_requirement = "电梯门开着，里面没有轿厢。"
    scene.entry_state = {
        "state": "林渊骑电动车赶到十七栋楼下，王建业在雨棚下等他。"
    }
    context_packet = SimpleNamespace(
        chapter_contract=None,
        hard_fact_snapshot=None,
        chapter_length_block=None,
        timeline_canon_block=None,
        character_role_block=None,
        dialogue_voice_block=None,
        scene_coherence_block=None,
        canon_guardrails_block=None,
        reader_contract_block=None,
        hype_constraints_block=None,
        hook_echo_block=None,
        exposition_density_block=None,
        voice_dna_block=None,
        chapter_market_constraints_block=None,
        signature_scene_block=None,
        prior_persona_feedback_block=None,
        story_bible={},
        previous_scene_summaries=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        recent_timeline_events=[],
        retrieval_chunks=[],
    )

    _, user_prompt = draft_services.build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        context_packet,
        target_word_count=chapter.target_word_count,
        # Explicit full profile: these blocks are lean-dropped by design
        # (plan §4.3); this test covers what the block itself states.
        prose_prompt_profile="full",
    )

    assert "【开场场景指导】" in user_prompt
    assert "第一段建议从这里开写" in user_prompt
    assert "前200字应当出现第一场的地点/人物/异常" in user_prompt
    assert "【前十章禁写与物件信号硬约束】" in user_prompt
    assert "系统门禁已登记" in user_prompt
    assert "不要复述禁写清单" in user_prompt
    # 去同质化 P0-1: genre-neutral phrasing (was "家族本名" — one book's lineage motif)
    assert "人物本名" in user_prompt
    assert "允许电话/短信作为同一 POV 内的现实沟通工具" in user_prompt
    assert "不得引入额外活人 NPC（快递员/配送员等）" in user_prompt
    assert "铜钱发烫" not in user_prompt


def test_chapter_first_prompt_treats_scene_cards_as_hidden_nodes() -> None:
    project = build_project()
    project.language = "zh-CN"
    chapter = build_chapter(project.id)
    chapter.target_word_count = 2600
    scenes = []
    for scene_number in range(1, 5):
        scene = build_scene(project.id, chapter.id)
        scene.scene_number = scene_number
        scene.target_word_count = 650
        scenes.append(scene)
    context_packet = SimpleNamespace(
        chapter_contract=None,
        hard_fact_snapshot=None,
        chapter_length_block=None,
        timeline_canon_block=None,
        character_role_block=None,
        dialogue_voice_block=None,
        scene_coherence_block=None,
        canon_guardrails_block=None,
        reader_contract_block=None,
        hype_constraints_block=None,
        hook_echo_block=None,
        exposition_density_block=None,
        voice_dna_block=None,
        chapter_market_constraints_block=None,
        signature_scene_block=None,
        prior_persona_feedback_block=None,
        story_bible={},
        previous_scene_summaries=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        recent_timeline_events=[],
        retrieval_chunks=[],
    )

    _, user_prompt = draft_services.build_chapter_first_draft_prompts(
        project,
        chapter,
        scenes,
        None,
        context_packet,
        target_word_count=chapter.target_word_count,
        # Explicit full profile: these blocks are lean-dropped by design
        # (plan §4.3); this test covers what the block itself states.
        prose_prompt_profile="full",
    )

    assert "本章包含 4 个隐藏情节节点" in user_prompt
    assert "没有各自的字数配额" in user_prompt
    assert "节点目标合计" not in user_prompt
    assert "节点不是可见场景，也不要求平均篇幅" in user_prompt
    assert "全文建议22-32段" not in user_prompt
    assert "每场5-8段" not in user_prompt
    # hard_max for the zh band is 3500 (target 2600 → band 1800-2600-3500).
    assert "超过3500字" in user_prompt
    assert "地图给出的既成入场、必须变化和禁用项是边界" in user_prompt
    # 去同质化 P0-1: the escalation guard is genre-neutral now (no one book's
    # horror beats baked into the universal writer prompt).
    assert "升级成未写在场景卡里的高潮/死亡/关键转折动作" in user_prompt


def test_generated_chapter_cleanup_removes_forbidden_signal_negation_echoes() -> None:
    cleaned, stats = draft_services._clean_generated_chapter_text(
        "铜面传来震颤——不是发烫，是有什么东西在另一头敲门。",
        chapter_number=2,
        source="test",
    )

    assert "发烫" not in cleaned
    assert "没有温度变化" in cleaned
    assert stats["forbidden_signal_negations"] == 1


def test_sanitize_novel_markdown_removes_standalone_word_count_marker() -> None:
    cleaned = draft_services.sanitize_novel_markdown_content(
        "林渊拇指压着铜钱，另一只手把纸页翻过来。\n\n"
        "（字数：598）\n\n"
        "林渊把账页收进怀里，没抬头。",
        language="zh-CN",
    )
    bullet_cleaned = draft_services.sanitize_novel_markdown_content(
        "她听见楼道里的门响。\n\n- 字数: 598\n\n镜面裂开一道细纹。",
        language="zh-CN",
    )

    assert "字数：598" not in cleaned
    assert "字数: 598" not in bullet_cleaned
    assert "林渊拇指压着铜钱" in cleaned
    assert "林渊把账页收进怀里" in cleaned
    assert "镜面裂开一道细纹" in bullet_cleaned


def test_generated_chapter_cleanup_preserves_duplicates_when_dedup_would_underflow() -> None:
    duplicate = (
        "林夜把铜钱按在账册上，门外脚步停住，证物袋里的灰线同时绷紧。"
    )
    content = (
        "# 第1章 旧案\n\n"
        "林夜抬头，雨水顺着窗缝往下淌。\n\n"
        f"{duplicate}\n\n"
        f"{duplicate}\n\n"
        "他没有后退，只把账册推向灯下。"
    )
    before = draft_services.count_words(content)

    cleaned, stats = draft_services._clean_generated_chapter_text(
        content,
        chapter_number=1,
        source="chapter_rewrite",
        min_word_count=before - 1,
    )

    assert stats["duplicate_paragraphs"] == 0
    assert stats["duplicate_paragraphs_preserved_under_min"] == 1
    assert cleaned.count(duplicate) == 2
    assert draft_services.count_words(cleaned) == before


def test_chapter_review_flags_phone_prelude_when_first_scene_is_in_person() -> None:
    chapter = build_chapter(uuid4())
    chapter.opening_situation = "23:43，林渊赶到十七栋楼下，王建业站在雨棚下等他。"
    scene = build_scene(uuid4(), uuid4())
    scene.title = "十七栋楼下的空电梯"
    scene.hook_requirement = "电梯门开着，里面没有轿厢。"
    scene.entry_state = {
        "state": "林渊骑电动车赶到十七栋楼下，王建业在雨棚下等他。"
    }

    findings = review_services._chapter_opening_contract_findings(
        chapter,
        [scene],
        "电话响第三遍的时候，林渊正在翻青囊。来电显示王建业。",
    )

    assert any(finding.category == "opening_contract" for finding in findings)
    assert any("OPENING_SCENE_DRIFT" in finding.message for finding in findings)


def test_front10_contract_gate_blocks_phone_drift_and_forbidden_signal() -> None:
    chapter = build_chapter(uuid4())
    chapter.opening_situation = "23:43，林渊赶到十七栋楼下，王建业站在雨棚下等他。"
    chapter.metadata_json = {
        "object_signal_contract": {"forbidden_signals": ["铜钱发烫"]},
    }
    chapter.foreshadowing_actions = {"forbidden_early_leaks": ["困魂镜"]}
    scene = build_scene(uuid4(), uuid4())
    scene.title = "十七栋楼下的空电梯"
    scene.hook_requirement = "电梯门开着，里面没有轿厢。"
    scene.entry_state = {
        "state": "林渊骑电动车赶到十七栋楼下，王建业在雨棚下等他。"
    }

    violations = draft_services._front10_contract_violations_for_content(
        chapter,
        [scene],
        "电话响第三遍的时候，林渊掌心里的铜钱忽然变热，热得像刚烧开的水。困魂镜正在醒来。",
    )

    # Phone/mediated-opening drift is no longer a deterministic hard block (2026-05-26
    # architecture cleanup: it wrongly forbade legitimate phone openings and is now an
    # audit dimension in chapter_llm_quality_judge). The deterministic gate still
    # hard-blocks forbidden-signal leaks (e.g. 困魂镜).
    assert {violation.code for violation in violations} == {
        "FRONT10_FORBIDDEN_SIGNAL",
    }
    assert all(violation.severity == "block" for violation in violations)
    forbidden = [
        violation for violation in violations if violation.code == "FRONT10_FORBIDDEN_SIGNAL"
    ][0]
    assert "困魂镜" in forbidden.detail


def test_front10_contract_gate_does_not_treat_human_recoil_as_object_signal() -> None:
    chapter = build_chapter(uuid4())
    chapter.chapter_number = 2
    chapter.metadata_json = {
        "object_signal_contract": {"forbidden_signals": ["铜钱发烫"]},
    }
    scene = build_scene(uuid4(), uuid4())

    violations = draft_services._front10_contract_violations_for_content(
        chapter,
        [scene],
        "张建军看了一眼303门缝，整个人像被烫了一样缩回来。",
    )

    assert "FRONT10_FORBIDDEN_SIGNAL" not in {
        violation.code for violation in violations
    }


def test_front10_contract_gate_does_not_count_mundane_account_book_as_rule_lecture() -> None:
    chapter = build_chapter(uuid4())
    chapter.chapter_number = 2
    scene = build_scene(uuid4(), uuid4())

    content = (
        "小雨说账本上记着张建军三天的欠款，也记着王建业的餐盒押金。"
        "林渊看着账本，逼张建军承认自己敲过303的门。"
        "张建军一再否认，最后说自己只是没有应声。"
        "这一次否认停在门口，下一次否认会落到小雨手腕上。"
    )
    violations = draft_services._front10_contract_violations_for_content(
        chapter,
        [scene],
        content,
    )

    assert "FRONT10_RULE_LECTURE_DENSITY" not in {
        violation.code for violation in violations
    }


def test_front10_contract_gate_blocks_scene_forbidden_terms_and_rule_lecture() -> None:
    chapter = build_chapter(uuid4())
    chapter.chapter_number = 2
    # 去同质化 P0-1: rule-lecture density now fires on THIS book's own declared
    # rule jargon (per-book metadata), not a hardcoded one-book vocabulary.
    chapter.metadata_json = {
        "rule_lecture_terms": ["认账", "入账", "镜债", "账线", "否认"],
    }
    scene = build_scene(uuid4(), uuid4())
    scene.forbidden_actions = [
        "不得写电话、来电、手机通知、寄件、快递、外卖、配送、物流、跑腿。",
        "不得让小雨下楼；不得把湿纸条按在、贴在或压在小雨手腕上。",
        "不得提前说林正淳。",
        "不得让林渊讲完整规则课；只能用问话顺序和动作结果让读者看懂风险。",
    ]
    content = (
        "林渊看着张建军说，你脑子里想的每一个我就是个送外卖的，"
        "全是给镜债递刀子。账本找的是最近的人，所以先认动作，再认因果。"
        "否认、入账、认账、镜债、账线这些词在楼道里一遍遍落下。"
        + "他看着门缝停住。" * 130
        + "小雨往楼梯口下楼，林渊把湿纸条按在小雨手腕上，心里知道规则只认动作。"
        + "镜子里传来林正淳的声音。"
    )

    violations = draft_services._front10_contract_violations_for_content(
        chapter,
        [scene],
        content,
    )

    codes = {violation.code for violation in violations}
    assert "FRONT10_SCENE_FORBIDDEN_ACTION" in codes
    assert "FRONT10_RULE_LECTURE_DENSITY" in codes
    scene_forbidden = [
        violation for violation in violations if violation.code == "FRONT10_SCENE_FORBIDDEN_ACTION"
    ][0]
    # 去同质化 P0-1: scene-forbidden detection catches genre-agnostic immersion
    # breakers (外卖/下楼) from the scene's own forbidden_actions; book-specific
    # names/jargon are enforced via per-book metadata (forbidden_signals /
    # rule_lecture_terms), not by hardcoding one book's character names.
    assert "外卖" in scene_forbidden.detail


def test_generated_chapter_cleanup_collapses_llm_text_loops() -> None:
    loop = "\n\n".join(["门开了。", "水声停了。", "灰线断了。"] * 3)
    content = f"# 第1章 旧账\n\n{loop}\n\n林渊抬头，镜面裂开一道缝。"

    cleaned, stats = draft_services._clean_generated_chapter_text(
        content,
        chapter_number=1,
        source="test",
    )

    assert stats["loop_paragraphs"] > 0
    assert cleaned.count("门开了。") == 1
    assert cleaned.count("水声停了。") == 1
    assert cleaned.count("灰线断了。") == 1
    assert "镜面裂开一道缝" in cleaned


def test_chapter_rewrite_prompt_preserves_front10_opening_contract() -> None:
    project = build_project()
    project.language = "zh-CN"
    chapter = build_chapter(project.id)
    chapter.target_word_count = 2600
    chapter.opening_situation = "23:43，林渊赶到十七栋楼下，王建业站在雨棚下等他。"
    chapter.metadata_json = {
        "object_signal_contract": {"forbidden_signals": ["铜钱发烫"]},
    }
    current_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 旧账\n\n电话响第三遍的时候，林渊接起了王建业的电话。",
        word_count=40,
        is_current=True,
    )
    rewrite_task = RewriteTaskModel(
        project_id=project.id,
        trigger_source_id=chapter.id,
        trigger_type="chapter_review",
        instructions="修复开场和物件信号。",
        rewrite_strategy="chapter_rewrite",
        status="pending",
    )
    rewrite_task.id = uuid4()
    chapter_context = SimpleNamespace(
        chapter_scenes=[
            SimpleNamespace(
                scene_number=1,
                title="十七栋楼下的空电梯",
                scene_type="setup",
                story_purpose="林渊到现场阻止王建业靠近空电梯井。",
                emotion_purpose="压迫和不安",
                summary=None,
            )
        ],
        canon_guardrails_block=None,
        hook_echo_block=None,
        signature_scene_block=None,
        voice_dna_block=None,
        chapter_market_constraints_block=None,
        exposition_density_block=None,
        story_bible={},
        previous_scene_summaries=[],
        recent_timeline_events=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        retrieval_chunks=[],
        hard_fact_snapshot=None,
    )

    _, user_prompt = review_services.build_chapter_rewrite_prompts(
        project,
        chapter,
        current_draft,
        rewrite_task,
        chapter_context,
    )

    assert "【前十章重写硬合同】" in user_prompt
    assert "第一段必须重新落到这个开场场面" in user_prompt
    assert "前500字不得突然新增电话" in user_prompt
    assert "来源、转交人、可信原因和到场动机" in user_prompt
    assert "铜钱发烫" in user_prompt


def test_chapter_auto_repair_length_contract_allows_dynamic_publish_range() -> None:
    project = build_project()
    project.language = "zh-CN"
    chapter = build_chapter(project.id)
    chapter.target_word_count = 2200

    contract = draft_services._chapter_auto_repair_length_contract(project, chapter)

    # zh publish band is 1800-3500 (target ~2200); see _chapter_length_contract_band.
    assert "1800-3500" in contract
    assert "不得新增无关场景" in contract
    assert "目标约 2200 字" in contract


@pytest.mark.asyncio
async def test_retention_safety_after_assembly_blocks_from_prev_draft() -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.chapter_number = 2
    chapter.production_state = "ok"
    draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="三日后，清晨。李四走进客栈。店小二殷勤地擦着桌子。",
        word_count=30,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    prev_text = (
        "下一刻，门外脚步声响起。突然，墙后传来一声低咳——"
        "竟是他以为已死之人。未完——"
    )
    session = FakeSession(execute_results=[FakeScalarOneOrNone(prev_text)])

    blocked = await pipeline_services._evaluate_retention_safety_after_assembly(
        session,
        project=project,
        chapter=chapter,
        chapter_draft=draft,
        chapter_number=2,
    )

    assert blocked is True
    assert chapter.production_state == "blocked"
    assert "HOOK_ECHO_MISSING" in chapter.metadata_json["auto_repair_last_block_codes"]
    assert chapter.metadata_json["retention_gate_passed"] is False
    assert chapter.metadata_json["retention_gate_last_findings"]


@pytest.mark.asyncio
async def test_retention_safety_after_assembly_pass_keeps_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_chapter_length_gate(monkeypatch)
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.chapter_number = 2
    chapter.production_state = "ok"
    draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md=(
            "门外脚步声越来越近。下一刻，门被推开，竟是他以为已死之人。"
            "墙后的低咳声还在，名单从怀里掉了出来。"
            "他踏上登临破界的石阶，低声说：从此天地间，我自有道。"
        ),
        word_count=50,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    prev_text = (
        "下一刻，门外脚步声响起。突然，墙后传来一声低咳——"
        "竟是他以为已死之人。未完——"
    )
    session = FakeSession(execute_results=[FakeScalarOneOrNone(prev_text)])

    blocked = await pipeline_services._evaluate_retention_safety_after_assembly(
        session,
        project=project,
        chapter=chapter,
        chapter_draft=draft,
        chapter_number=2,
    )

    assert blocked is False
    assert chapter.production_state == "ok"
    assert chapter.metadata_json["retention_gate_passed"] is True
    assert "auto_repair_last_block_codes" not in chapter.metadata_json


def build_style(project_id) -> StyleGuideModel:
    return StyleGuideModel(
        project_id=project_id,
        pov_type="third-limited",
        tense="present",
        tone_keywords=["冷峻", "紧张"],
        prose_style="baseline",
        sentence_style="mixed",
        info_density="medium",
        dialogue_ratio=0.35,
        taboo_words=[],
        taboo_topics=[],
        reference_works=[],
        custom_rules=[],
    )


@pytest.mark.asyncio
async def test_run_scene_pipeline_blocks_when_truth_materializations_are_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json = {
        "truth_version": 2,
        "truth_updated_at": "2026-04-23T00:00:00+00:00",
        "truth_last_changed_artifact_type": "book_spec",
        "_truth_artifact_fingerprints": {},
        "_truth_change_log": [],
    }
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    session = FakeSession(scalar_results=[None, None, None])

    async def fake_load_scene_identifiers(_session, _project_slug, _chapter_number, _scene_number):
        return project, chapter, scene

    monkeypatch.setattr(
        pipeline_services,
        "_load_scene_identifiers",
        fake_load_scene_identifiers,
    )

    with pytest.raises(TruthVersionStaleError):
        await pipeline_services.run_scene_pipeline(
            session,
            build_settings(),
            "my-story",
            1,
            1,
        )


def test_structural_repair_pause_guard_allows_explicit_repair() -> None:
    project = build_project()
    mark_project_blocked_for_structural_repair(project)

    with pytest.raises(pipeline_services.ProjectRepairPauseError):
        pipeline_services._assert_project_not_blocked_for_structural_repair(
            project,
            project_slug="my-story",
            operation="chapter pipeline 1",
        )

    pipeline_services._assert_project_not_blocked_for_structural_repair(
        project,
        project_slug="my-story",
        operation="chapter pipeline 1",
        allow_structural_repair=True,
    )


def test_local_quality_pause_does_not_block_forward_writing() -> None:
    """A local opening-gate pause must not stall new-chapter writing.

    Regression: 青囊不语问阴阳 paused the whole project after exhausting the
    qimao opening gate (a *local* prose check) and never advanced. The write
    gate must treat a local-reason pause as non-blocking.
    """
    project = build_project()
    project.status = "paused"
    project.metadata_json = {
        **(project.metadata_json or {}),
        "production_paused": True,
        "production_pause_reason": "qimao_opening_gate_exhausted",
        "last_generation_gate_reason": "qimao_opening_gate_exhausted",
    }

    assert pipeline_services._project_blocked_for_structural_repair(project) is False
    # Must NOT raise — forward writing proceeds in parallel with the local repair.
    pipeline_services._assert_project_not_blocked_for_structural_repair(
        project,
        project_slug="my-story",
        operation="chapter pipeline 42",
    )


def test_structural_repair_required_still_blocks() -> None:
    """An explicit structural marker must keep blocking forward writing."""
    project = build_project()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "production_paused": True,
        "production_pause_reason": "material_referential_integrity_gate",
        "structural_repair_required": True,
    }

    assert pipeline_services._project_blocked_for_structural_repair(project) is True
    with pytest.raises(pipeline_services.ProjectRepairPauseError):
        pipeline_services._assert_project_not_blocked_for_structural_repair(
            project,
            project_slug="my-story",
            operation="chapter pipeline 5",
        )


@pytest.mark.asyncio
async def test_autowrite_clears_temporary_planning_throttle_pause() -> None:
    project = build_project()
    project.status = "paused"
    project.metadata_json = {
        **(project.metadata_json or {}),
        "production_paused": True,
        "production_pause_reason": pipeline_services.TEMPORARY_PLANNING_THROTTLE_REASON,
        "generation_resume_blocked_until_repair_audit": True,
        "paused_at": "2026-06-02T08:00:00+00:00",
    }
    session = FakeSession()

    cleared = await pipeline_services._clear_auto_resumable_project_pause(
        session,
        project,
    )

    assert cleared is True
    assert project.status == "revising"
    assert "production_paused" not in project.metadata_json
    assert "production_pause_reason" not in project.metadata_json
    assert "generation_resume_blocked_until_repair_audit" not in project.metadata_json
    assert "paused_at" not in project.metadata_json
    assert (
        project.metadata_json["last_project_pause_auto_resumed_reason"]
        == pipeline_services.TEMPORARY_PLANNING_THROTTLE_REASON
    )
    pipeline_services._assert_project_not_blocked_for_structural_repair(
        project,
        project_slug="my-story",
        operation="autowrite pipeline",
    )


def test_focus_pause_blocks_direct_autowrite_entry() -> None:
    project = build_project()
    project.status = "paused"
    project.metadata_json = {
        "production_paused": True,
        "production_pause_reason": "focus_user_requested_code_repair_20260718",
        "focus_pause": {"reason": "focus_user_requested_code_repair_20260718"},
    }

    with pytest.raises(pipeline_services.ProjectRepairPauseError):
        pipeline_services._assert_project_not_blocked_for_structural_repair(
            project,
            project_slug="my-story",
            operation="autowrite pipeline",
        )


def test_autowrite_start_clears_conception_lifecycle_but_preserves_other_metadata() -> None:
    project = build_project()
    project.metadata_json = {
        "conception_only": True,
        "planning_status": "awaiting_concept_approval",
        "concept_lab_bundle": {"version": 1},
    }

    changed = pipeline_services._mark_project_autowrite_started(project)

    assert changed is True
    assert "conception_only" not in project.metadata_json
    assert project.metadata_json["planning_status"] == "writing"
    assert project.metadata_json["conception_approved"] is True
    assert project.metadata_json["concept_lab_bundle"] == {"version": 1}


@pytest.mark.asyncio
async def test_run_scene_pipeline_blocks_structural_repair_pause_before_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    mark_project_blocked_for_structural_repair(project)
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    session = FakeSession()

    async def fake_load_scene_identifiers(_session, _project_slug, _chapter_number, _scene_number):
        return project, chapter, scene

    async def fail_truth_guard(*args, **kwargs):
        raise AssertionError("truth guard should not run after structural pause block")

    monkeypatch.setattr(
        pipeline_services,
        "_load_scene_identifiers",
        fake_load_scene_identifiers,
    )
    monkeypatch.setattr(pipeline_services, "_enforce_truth_version_guard", fail_truth_guard)

    with pytest.raises(pipeline_services.ProjectRepairPauseError):
        await pipeline_services.run_scene_pipeline(
            session,
            build_settings(),
            "my-story",
            1,
            1,
        )


@pytest.mark.asyncio
async def test_run_scene_pipeline_blocks_on_contradiction_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    session = FakeSession(scalar_results=[None])
    settings = build_settings()
    settings.pipeline.enable_truth_version_guard = False
    settings.pipeline.enable_contradiction_checks = True
    settings.pipeline.contradiction_block_on_violation = True

    async def fake_load_scene_identifiers(_session, _project_slug, _chapter_number, _scene_number):
        return project, chapter, scene

    async def fake_build_context(*args, **kwargs):
        return SceneWriterContextPacket(
            project_id=project.id,
            project_slug=project.slug,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=1,
            scene_number=1,
            query_text="封港命令",
        )

    async def fake_run_pre_scene_contradiction_checks(*args, **kwargs):
        return ContradictionCheckResult(
            passed=False,
            violations=[
                ContradictionViolation(
                    check_type="knowledge_leak",
                    severity="error",
                    message="沈砚不能提前知道血莲印真相",
                    evidence="reader_knowledge chapter=7",
                )
            ],
            warnings=[],
            checks_run=1,
        )

    async def fake_load_identity_registry(*args, **kwargs):
        return [
            identity_guard_services.CharacterIdentity(
                name="沈砚",
                gender="male",
                pronoun_set_zh="他",
                pronoun_set_en="he/him",
            ),
            identity_guard_services.CharacterIdentity(
                name="港务官",
                gender="female",
                pronoun_set_zh="她",
                pronoun_set_en="she/her",
            ),
        ]

    monkeypatch.setattr(
        pipeline_services,
        "_load_scene_identifiers",
        fake_load_scene_identifiers,
    )
    monkeypatch.setattr(
        pipeline_services,
        "build_scene_writer_context_from_models",
        fake_build_context,
    )
    monkeypatch.setattr(
        contradiction_services,
        "run_pre_scene_contradiction_checks",
        fake_run_pre_scene_contradiction_checks,
    )
    monkeypatch.setattr(identity_guard_services, "load_identity_registry", fake_load_identity_registry)

    with pytest.raises(WriteSafetyBlockError):
        await pipeline_services.run_scene_pipeline(
            session,
            settings,
            "my-story",
            1,
            1,
        )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    assert workflow_runs[0].status == "failed"
    assert workflow_runs[0].metadata_json["blocked_by_write_safety_gate"] is True
    assert workflow_runs[0].metadata_json["write_safety_gate_source"] == "contradiction"


@pytest.mark.asyncio
async def test_run_scene_pipeline_blocks_pre_draft_scene_contract_before_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json = {"identity_manifest_status": "locked"}
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    scene.participants = ["陌生人"]
    scene.time_label = None
    session = FakeSession()
    settings = build_settings()
    settings.pipeline.enable_truth_version_guard = False

    async def fake_load_scene_identifiers(_session, _project_slug, _chapter_number, _scene_number):
        return project, chapter, scene

    async def fake_load_current_scene_draft(_session, _scene_id):
        return None

    async def fake_build_context(*args, **kwargs):
        return SceneWriterContextPacket(
            project_id=project.id,
            project_slug=project.slug,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=1,
            scene_number=1,
            query_text="封港命令",
        )

    async def fake_load_identity_registry(*args, **kwargs):
        return [
            identity_guard_services.CharacterIdentity(
                name="沈砚",
                gender="male",
                pronoun_set_zh="他",
                pronoun_set_en="he/him",
            )
        ]

    async def fake_generate_scene_draft(*args, **kwargs):
        raise AssertionError("writer should not be called when the pre-draft contract blocks")

    monkeypatch.setattr(pipeline_services, "_load_scene_identifiers", fake_load_scene_identifiers)
    monkeypatch.setattr(pipeline_services, "_load_current_scene_draft", fake_load_current_scene_draft)
    monkeypatch.setattr(pipeline_services, "build_scene_writer_context_from_models", fake_build_context)
    monkeypatch.setattr(identity_guard_services, "load_identity_registry", fake_load_identity_registry)
    monkeypatch.setattr(pipeline_services, "generate_scene_draft", fake_generate_scene_draft)

    with pytest.raises(ValueError, match="pre_draft_scene_contract"):
        await pipeline_services.run_scene_pipeline(
            session,
            settings,
            "my-story",
            1,
            1,
        )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    assert workflow_runs[0].status == "failed"
    assert workflow_runs[0].metadata_json["pre_draft_scene_contract"]["passed"] is False


@pytest.mark.asyncio
async def test_run_scene_pipeline_injects_premium_engine_blocks_into_writer_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    project.genre = "xianxia"
    project.metadata_json = {
        **(project.metadata_json or {}),
        "sub_genre": "凡人流修仙",
        "world_spec": {
            "world_name": "青岚界",
            "power_system": {
                "name": "灵根修行",
                "tiers": ["炼气", "筑基", "金丹"],
                "protagonist_starting_tier": "炼气十层",
            },
        },
        "cast_spec": {
            "protagonist": {
                "name": "沈砚",
                "power_tier": "炼气十层",
                "resources": [{"resource_key": "筑基丹", "amount": 1}],
                "relationships": [
                    {
                        "character": "港务官",
                        "type": "temporary ally",
                        "tension": (
                            "她要查清筑基丹流向, "
                            "沈砚必须决定是否借她的船离场。"
                        ),
                    }
                ],
            },
            "supporting_cast": [
                {
                    "name": "港务官",
                    "role": "broker",
                    "relationship_to_protagonist": "互相利用的临时盟友",
                    "evolution_arc": "从利益交换到一次有限信任",
                }
            ],
        },
        "volume_plan": [
            {
                "volume_number": 1,
                "volume_title": "入宗夺丹",
                "opening_state": {"protagonist_power_tier": "炼气十层"},
            }
        ],
        "prewrite_repair_directives": [
            "后续卷规划必须更换相邻卷主压力源；当前卷章节也要引入新的外部压力或内部代价，避免同一反派/势力连续驱动。"
        ],
        "factions": [
            {
                "name": "执法堂",
                "goal": "追回秘境中流失的筑基资源。",
                "method": "盘查、封港、追踪丹药气息。",
                "relationship_to_protagonist": "制度性压力",
                "internal_conflict": "长老要立威, 外务执事想私下分润。",
                "next_reaction": "若筑基丹消失, 会先封锁码头再查散修。",
            }
        ],
    }
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    captured: dict[str, SceneWriterContextPacket] = {}

    async def fake_load_scene_identifiers(_session, _project_slug, _chapter_number, _scene_number):
        return project, chapter, scene

    async def fake_load_current_scene_draft(_session, _scene_id):
        return None

    async def fake_build_context(*args, **kwargs):
        return SceneWriterContextPacket(
            project_id=project.id,
            project_slug=project.slug,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=1,
            scene_number=1,
            query_text="封港命令",
            story_bible={
                "volume": {"volume_number": 1},
                "world_rules": [
                    {
                        "rule_code": "R-001",
                        "name": "试炼禁令",
                        "description": "秘境试炼中偷取筑基丹会引发执法堂追索。",
                        "exploitation_potential": "先藏丹后换身份离场。",
                        "future_backlash": "宗门会追查资源流向。",
                    }
                ],
            },
        )

    async def fake_generate_scene_draft(*args, **kwargs):
        captured["context"] = kwargs["context_packet"]
        draft = SceneDraftVersionModel(
            project_id=project.id,
            scene_card_id=scene.id,
            version_no=1,
            content_md="沈砚握紧筑基丹, 先退入阴影观察局势。",
            word_count=200,
            is_current=True,
            generation_params={},
        )
        draft.id = uuid4()
        draft.llm_run_id = uuid4()
        return draft

    async def fake_review_scene_draft(*args, **kwargs):
        return (
            type("ReviewResultStub", (), {"verdict": "pass", "severity_max": "low"})(),
            type("ReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})(),
            type("QualityStub", (), {"id": uuid4()})(),
            None,
        )

    async def fake_refresh_scene_knowledge(*args, **kwargs):
        return SceneKnowledgeRefreshResult(
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=1,
            scene_number=1,
            canon_fact_ids=[],
            timeline_event_ids=[],
            canon_facts_created=0,
            canon_facts_reused=0,
            timeline_events_created=0,
            timeline_events_reused=0,
            summary_text="无新增知识",
            llm_run_id=None,
        )

    monkeypatch.setattr(pipeline_services, "_load_scene_identifiers", fake_load_scene_identifiers)
    monkeypatch.setattr(pipeline_services, "_load_current_scene_draft", fake_load_current_scene_draft)
    monkeypatch.setattr(pipeline_services, "build_scene_writer_context_from_models", fake_build_context)
    monkeypatch.setattr(pipeline_services, "generate_scene_draft", fake_generate_scene_draft)
    monkeypatch.setattr(pipeline_services, "review_scene_draft", fake_review_scene_draft)
    monkeypatch.setattr(pipeline_services, "refresh_scene_knowledge", fake_refresh_scene_knowledge)

    settings = build_settings()
    settings.output.base_dir = str(tmp_path)
    profile_path = tmp_path / project.slug / "story-bible" / "ranking-capability-profile.md"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(
        "# 《测试书》榜单级能力 Profile\n\n"
        "- 固定入口：港口秘境。\n"
        "- 可解规则：禁令必须有破局路径和代价。\n"
        "- 单元案推动主线：每个试炼案都回收筑基资源线。\n",
        encoding="utf-8",
    )
    settings.pipeline.enable_truth_version_guard = False
    settings.pipeline.enable_contradiction_checks = False
    settings.pipeline.require_pre_draft_scene_contract = False
    settings.pipeline.enable_scene_plan_richness_gate = False

    result = await pipeline_services.run_scene_pipeline(
        FakeSession(),
        settings,
        "my-story",
        1,
        1,
        requested_by="tester",
    )

    context = captured["context"]
    assert result.final_verdict == "pass"
    assert context.ranking_capability_profile_block is not None
    assert any("[写前规划门禁]" in item for item in context.contradiction_warnings)
    assert any("更换相邻卷主压力源" in item for item in context.contradiction_warnings)
    assert "榜单级能力 Profile" in context.ranking_capability_profile_block
    assert "港口秘境" in context.ranking_capability_profile_block
    assert context.progression_context_block is not None
    assert "【进阶体系约束】" in context.progression_context_block
    assert "炼气 → 筑基 → 金丹" in context.progression_context_block
    assert "筑基丹=1" in context.progression_context_block
    assert context.decision_policy_block is not None
    assert "【主角决策策略】" in context.decision_policy_block
    assert "public_vanity_duel" in context.decision_policy_block
    assert context.rule_system_context_block is not None
    assert "【规则系统约束】" in context.rule_system_context_block
    assert "试炼禁令" in context.rule_system_context_block
    assert context.faction_ecology_context_block is not None
    assert "【阵营生态与反应压力约束】" in context.faction_ecology_context_block
    assert "执法堂" in context.faction_ecology_context_block
    assert context.relationship_agency_context_block is not None
    assert "【关系张力与主角能动性约束】" in context.relationship_agency_context_block
    assert "沈砚 -> 港务官" in context.relationship_agency_context_block
    assert "主角必须有主动选择和代价" in context.relationship_agency_context_block


@pytest.mark.asyncio
async def test_generate_scene_draft_creates_new_current_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    style = build_style(project.id)

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_identity_registry(*args, **kwargs):
        return [
            identity_guard_services.CharacterIdentity(
                name="沈砚",
                gender="male",
                pronoun_set_zh="他",
                pronoun_set_en="he/him",
            ),
            identity_guard_services.CharacterIdentity(
                name="港务官",
                gender="female",
                pronoun_set_zh="她",
                pronoun_set_en="she/her",
            ),
        ]

    monkeypatch.setattr(draft_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(identity_guard_services, "load_identity_registry", fake_load_identity_registry)
    session = FakeSession(
        scalar_results=[chapter, scene, 0],
        get_map={(StyleGuideModel, project.id): style},
    )

    draft = await draft_services.generate_scene_draft(session, "my-story", 1, 1)

    assert draft.version_no == 1
    assert draft.is_current is True
    assert scene.status == "drafted"
    assert chapter.status == "drafting"
    assert any(isinstance(obj, SceneDraftVersionModel) for obj in session.added)


@pytest.mark.asyncio
async def test_assemble_chapter_draft_creates_assembled_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.language = "zh-CN"
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    observed_languages: list[str] = []
    scene_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=1,
        content_md="程彻抓起挂在门后的黑色双肩包，猛地拉开拉链检查里面的物资。",
        word_count=128,
        is_current=True,
        generation_params={},
    )
    scene_draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    def fake_word_count(content_md: str, *, language: str = "zh-CN") -> int:
        observed_languages.append(language)
        return 42

    monkeypatch.setattr(draft_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        draft_services,
        "authoritative_word_count_for_language",
        fake_word_count,
    )
    session = FakeSession(
        scalar_results=[chapter, scene_draft, 0],
        scalars_results=[[scene]],
    )

    chapter_draft = await draft_services.assemble_chapter_draft(session, "my-story", 1)

    assert chapter_draft.version_no == 1
    assert chapter_draft.is_current is True
    assert chapter.current_word_count == 42
    assert observed_languages == ["zh-CN"]
    assert any(isinstance(obj, ChapterDraftVersionModel) for obj in session.added)


@pytest.mark.asyncio
async def test_assemble_chapter_draft_blocks_cross_chapter_repetition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.chapter_number = 2
    scene = build_scene(project.id, chapter.id)
    repeated = "三年前试炼场崩塌，不是意外。叶长青提前改了阵法参数，宁尘的父亲冲进了崩塌区。"
    scene_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=1,
        content_md=repeated,
        word_count=128,
        is_current=True,
        generation_params={},
    )
    scene_draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(draft_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(
        scalar_results=[chapter, scene_draft, 0],
        scalars_results=[[scene]],
        execute_results=[
            FakeExecuteRows([(1, f"# 第1章 暗潮试探\n\n{repeated}")]),
        ],
    )

    chapter_draft = await draft_services.assemble_chapter_draft(session, "my-story", 2)

    assert chapter_draft.is_current is True
    assert chapter.production_state == "blocked"
    assert chapter.metadata_json["write_safety_block_code"] == "CROSS_CHAPTER_REPETITION"
    assert chapter.metadata_json["post_assembly_duplicate_gate"]["finding_count"] >= 1


@pytest.mark.asyncio
async def test_assemble_chapter_draft_blocks_repeated_short_opening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.chapter_number = 75
    scene = build_scene(project.id, chapter.id)
    opening = "这一刻，所有线索都被压回同一条账路上。"
    scene_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=1,
        content_md=f"{opening}\n\n林渊把账页翻到最后一行，发现签名被水泡开。",
        word_count=128,
        is_current=True,
        generation_params={},
    )
    scene_draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(draft_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(
        scalar_results=[chapter, scene_draft, 0],
        scalars_results=[[scene]],
        execute_results=[
            FakeExecuteRows([(70, f"# 第70章 账路初现\n\n{opening}\n\n沈念停下脚步。")]),
        ],
    )

    chapter_draft = await draft_services.assemble_chapter_draft(session, "my-story", 75)

    assert chapter_draft.is_current is True
    assert chapter.production_state == "blocked"
    assert chapter.metadata_json["write_safety_block_code"] == "CHAPTER_OPENING_REPETITION"
    gate = chapter.metadata_json["post_assembly_duplicate_gate"]
    assert gate["finding_count"] >= 1
    assert gate["findings"][0]["source"] == "post_assembly_opening_diversity_gate"


@pytest.mark.asyncio
async def test_review_scene_draft_creates_rewrite_task_for_low_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    scene_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=1,
        content_md="短场景草稿。",
        word_count=10,
        is_current=True,
        generation_params={},
    )
    scene_draft.id = uuid4()
    style = build_style(project.id)

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(review_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(
        scalar_results=[chapter, scene, scene_draft],
        get_map={(StyleGuideModel, project.id): style},
    )

    result, report, quality, rewrite_task = await review_services.review_scene_draft(
        session,
        build_settings(),
        "my-story",
        1,
        1,
    )

    assert result.verdict == "rewrite"
    assert report.id is not None
    assert quality.id is not None
    assert rewrite_task is not None
    assert scene.status == "needs_rewrite"
    assert chapter.status == "revision"


@pytest.mark.asyncio
async def test_rewrite_scene_from_task_creates_new_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    scene_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=1,
        content_md="旧版本草稿",
        word_count=120,
        is_current=True,
        generation_params={},
    )
    scene_draft.id = uuid4()
    style = build_style(project.id)
    rewrite_task = RewriteTaskModel(
        project_id=project.id,
        trigger_type="scene_review",
        trigger_source_id=scene.id,
        rewrite_strategy="scene_dialogue_conflict_expansion",
        priority=3,
        status="pending",
        instructions="补强冲突和对话",
        context_required=[],
        metadata_json={},
    )
    rewrite_task.id = uuid4()
    rewrite_task.attempts = 0

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(review_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(
        scalar_results=[chapter, scene, scene_draft, rewrite_task, 1],
        get_map={(StyleGuideModel, project.id): style},
    )

    new_draft, completed_task = await review_services.rewrite_scene_from_task(
        session,
        "my-story",
        1,
        1,
    )

    assert new_draft.version_no == 2
    # Rewrite fallback (when the LLM is unavailable) now preserves the
    # existing draft verbatim instead of inventing template prose. The
    # HTML comment marker attached by render_rewritten_scene_markdown is
    # stripped by sanitize_novel_markdown_content before the draft is
    # persisted — so the final stored content equals the original draft.
    # Previously this test asserted ``new_draft.word_count > scene_draft
    # .word_count``, which was implicitly relying on the template prose
    # inflation that we just removed.
    assert new_draft.content_md.strip() == "旧版本草稿"
    assert "重新被推回《" not in new_draft.content_md
    assert "third-limited" not in new_draft.content_md
    assert "rewrite-scene-fallback" not in new_draft.content_md
    assert completed_task.status == "completed"
    assert scene.status == "drafted"


@pytest.mark.asyncio
async def test_export_project_markdown_writes_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.target_word_count = 120
    chapter.status = "complete"
    chapter.production_state = "ok"
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图\n\n" + ("沈砚按住星图，港口的雾又往前压了一尺。" * 200),
        word_count=2860,
        assembled_from_scene_draft_ids=[str(uuid4())],
        is_current=True,
    )
    chapter_draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(export_services, "get_project_by_slug", fake_get_project_by_slug)
    settings = build_settings()
    settings.output.base_dir = str(tmp_path / "output")
    session = FakeSession(
        scalar_results=[chapter_draft],
        scalars_results=[[chapter]],
    )

    artifact, output_path = await export_services.export_project_markdown(
        session,
        settings,
        "my-story",
    )

    assert artifact.id is not None
    assert output_path.exists() is True
    assert output_path.read_text(encoding="utf-8").startswith("# My Story")
    expected_stats = export_services.build_markdown_reading_stats(
        output_path.read_text(encoding="utf-8")
    )
    assert artifact.metadata_json["word_count"] == expected_stats["word_count"]
    assert artifact.metadata_json["skipped_chapters"] == []
    assert artifact.metadata_json["warnings"] == []
    package_root = tmp_path / "output" / project.slug
    assert (package_root / "chapter-001.md").exists() is True
    assert (package_root / "story-bible" / "series-brief.md").exists() is True
    assert (package_root / "story-bible" / "reader-desire-map.md").exists() is True
    assert (package_root / "story-bible" / "series-bible.md").exists() is True
    assert (package_root / "story-bible" / "continuity-ledger.md").exists() is True
    assert (package_root / "story-bible" / "batch-queue.csv").exists() is True
    assert (package_root / "story-bible" / "volume-plan.csv").exists() is True


@pytest.mark.asyncio
async def test_publication_export_rejects_missing_promoted_chapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter_one = build_chapter(project.id)
    chapter_two = build_chapter(project.id)
    chapter_two.chapter_number = 2
    promoted = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter_one.id,
        version_no=1,
        content_md="# 第1章\n\n正文",
        word_count=2,
        assembled_from_scene_draft_ids=[],
        is_current=True,
        promotion_state="promoted",
    )

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(export_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(
        scalar_results=[promoted, None],
        scalars_results=[[chapter_one, chapter_two]],
    )

    with pytest.raises(export_services.ProjectExportIncompleteError) as exc_info:
        await export_services._load_project_export_payload(session, project.slug)

    assert exc_info.value.missing_chapters == (2,)


@pytest.mark.asyncio
async def test_export_project_closure_draft_uses_current_quality_debt_without_weakening_strict_export(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    project.language = "zh-CN"
    chapter = build_chapter(project.id)
    chapter.status = "revision"
    chapter.production_state = "quality_debt"
    chapter.metadata_json = {"chapter_quality_debt": True}
    draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=3,
        content_md="# 第1章 债务稿\n\n" + ("沈砚扣住星盘，逼问守门人交出账册。" * 180),
        word_count=2800,
        assembled_from_scene_draft_ids=[str(uuid4())],
        is_current=True,
        promotion_state="candidate",
    )
    draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(export_services, "get_project_by_slug", fake_get_project_by_slug)
    settings = build_settings()
    settings.output.base_dir = str(tmp_path / "output")
    session = FakeSession(scalar_results=[draft], scalars_results=[[chapter]])

    artifact, output_path = await export_services.export_project_closure_draft_markdown(
        session, settings, "my-story"
    )

    assert output_path.name == "project-draft-with-quality-debt.md"
    assert "未通过严格出版门禁" in output_path.read_text(encoding="utf-8")
    assert artifact.export_type == "markdown_draft"
    assert artifact.metadata_json["publication_ready"] is False
    assert artifact.metadata_json["quality_debt_chapters"] == [1]


@pytest.mark.asyncio
async def test_export_project_markdown_removes_stale_chapter_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.target_word_count = 120
    chapter.status = "complete"
    chapter.production_state = "ok"
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=2,
        content_md="# 第1章 新稿\n\n" + ("这一次是数据库当前稿，沈砚没有退后。" * 210),
        word_count=2860,
        assembled_from_scene_draft_ids=[str(uuid4())],
        is_current=True,
    )
    chapter_draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(export_services, "get_project_by_slug", fake_get_project_by_slug)
    settings = build_settings()
    settings.output.base_dir = str(tmp_path / "output")
    package_root = tmp_path / "output" / project.slug
    package_root.mkdir(parents=True)
    stale = package_root / "chapter-002.md"
    stale.write_text("# 第2章 旧稿\n\n这不再是当前稿。", encoding="utf-8")
    session = FakeSession(
        scalar_results=[chapter_draft],
        scalars_results=[[chapter]],
    )

    await export_services.export_project_markdown(session, settings, "my-story")

    assert stale.exists() is False
    assert "数据库当前稿" in (package_root / "chapter-001.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_export_project_docx_writes_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.target_word_count = 120
    chapter.status = "complete"
    chapter.production_state = "ok"
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图\n\n" + ("沈砚按住星图，港口的雾又往前压了一尺。" * 200),
        word_count=2860,
        assembled_from_scene_draft_ids=[str(uuid4())],
        is_current=True,
    )
    chapter_draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(export_services, "get_project_by_slug", fake_get_project_by_slug)
    settings = build_settings()
    settings.output.base_dir = str(tmp_path / "output")
    session = FakeSession(
        scalar_results=[chapter_draft],
        scalars_results=[[chapter]],
    )

    artifact, output_path = await export_services.export_project_docx(
        session,
        settings,
        "my-story",
    )

    assert artifact.id is not None
    assert output_path.exists() is True
    assert output_path.suffix == ".docx"


@pytest.mark.asyncio
async def test_export_project_epub_writes_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.target_word_count = 120
    chapter.status = "complete"
    chapter.production_state = "ok"
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图\n\n" + ("沈砚按住星图，港口的雾又往前压了一尺。" * 200),
        word_count=2860,
        assembled_from_scene_draft_ids=[str(uuid4())],
        is_current=True,
    )
    chapter_draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(export_services, "get_project_by_slug", fake_get_project_by_slug)
    settings = build_settings()
    settings.output.base_dir = str(tmp_path / "output")
    session = FakeSession(
        scalar_results=[chapter_draft],
        scalars_results=[[chapter]],
    )

    artifact, output_path = await export_services.export_project_epub(
        session,
        settings,
        "my-story",
    )

    assert artifact.id is not None
    assert output_path.exists() is True
    assert output_path.suffix == ".epub"


@pytest.mark.asyncio
async def test_export_project_markdown_blocks_unfinished_placeholder_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.target_word_count = 120
    chapter.status = "complete"
    chapter.production_state = "ok"
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图\n\n盟友甲在仓库门口等他。",
        word_count=120,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    chapter_draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(export_services, "get_project_by_slug", fake_get_project_by_slug)
    settings = build_settings()
    settings.output.base_dir = str(tmp_path / "output")
    session = FakeSession(
        scalar_results=[chapter_draft],
        scalars_results=[[chapter]],
    )

    with pytest.raises(ValueError, match="盟友甲"):
        await export_services.export_project_markdown(
            session,
            settings,
            "my-story",
        )


def test_publication_gate_blocks_unapproved_chapter_state() -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.chapter_number = 30
    chapter.status = "drafting"
    chapter.production_state = "pending"
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第30章 沉渊绞杀\n\n宁尘向前走了一步。",
        word_count=20,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )

    blockers = export_services.collect_publication_blockers(project, [(chapter, chapter_draft)])

    assert any("不是可发布状态" in blocker for blocker in blockers)
    # 2026-07-26：门禁项的判据由「等于 ok」改为「是不是终态」——quality_debt 是修复
    # 循环自己的裁决，拦它等于用一道门推翻另一道门。pending 仍然是「没写完」，
    # 照样拦，只是文案随之改变。见 test_export_ships_terminal_debt.py。
    assert any("尚未写完" in blocker for blocker in blockers)


def test_publication_gate_allows_repaired_revision_ok_chapter() -> None:
    project = build_project()
    project.language = "en"
    chapter = build_chapter(project.id)
    chapter.chapter_number = 30
    chapter.status = "revision"
    chapter.production_state = "ok"
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# Chapter 30\n\n" + ("clean prose " * 1699) + "clean prose.",
        word_count=3400,
        assembled_from_scene_draft_ids=[str(uuid4())],
        is_current=True,
    )

    blockers = export_services.collect_publication_blockers(project, [(chapter, chapter_draft)])

    assert blockers == []


def test_publication_gate_blocks_common_sense_findings() -> None:
    project = build_project()
    project.genre = "灵异"
    chapter = build_chapter(project.id)
    chapter.chapter_number = 1
    chapter.status = "complete"
    chapter.production_state = "ok"
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 十五分钟凶宅\n\n林渊把铜钱压到王建业额头，鼻血滴在对方脸上，没擦。",
        word_count=30,
        assembled_from_scene_draft_ids=[str(uuid4())],
        is_current=True,
    )

    blockers = export_services.collect_publication_blockers(project, [(chapter, chapter_draft)])

    assert any("常识因果门禁" in blocker for blocker in blockers)


def test_publication_gate_blocks_short_chinese_commercial_chapter() -> None:
    project = build_project()
    project.language = "zh-CN"
    chapter = build_chapter(project.id)
    chapter.chapter_number = 1
    chapter.status = "complete"
    chapter.production_state = "ok"
    chapter.target_word_count = 2200
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 镜债开门\n\n" + ("林渊按住铜钱，镜面裂开一线。" * 120),
        word_count=2160,
        assembled_from_scene_draft_ids=[str(uuid4())],
        is_current=True,
    )

    blockers = export_services.collect_publication_blockers(project, [(chapter, chapter_draft)])

    # zh commercial hard floor is 1800 (CHINESE_CHAPTER_HARD_MIN_WORDS); a ~1440-char
    # chapter is below it and must be export-blocked.
    assert any("章节体量" in blocker and "1800" in blocker for blocker in blockers)


def test_publication_gate_blocks_failed_unified_quality_snapshot() -> None:
    project = build_project()
    project.language = "zh-CN"
    project.target_chapters = 100
    chapter = build_chapter(project.id)
    chapter.chapter_number = 1
    chapter.status = "complete"
    chapter.production_state = "ok"
    chapter.target_word_count = 2200
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 镜债开门\n\n本章会告诉读者这里是主线钩子。\n\n短。",
        word_count=30,
        assembled_from_scene_draft_ids=[str(uuid4())],
        is_current=True,
    )

    blockers = export_services.collect_publication_blockers(project, [(chapter, chapter_draft)])

    assert any("统一质量快照未通过" in blocker for blocker in blockers)


def test_publication_gate_blocks_cross_chapter_repeated_paragraph() -> None:
    project = build_project()
    chapter_29 = build_chapter(project.id)
    chapter_29.chapter_number = 29
    chapter_29.title = "冷锋死线"
    chapter_29.status = "complete"
    chapter_29.production_state = "ok"
    chapter_30 = build_chapter(project.id)
    chapter_30.chapter_number = 30
    chapter_30.title = "沉渊绞杀"
    chapter_30.status = "complete"
    chapter_30.production_state = "ok"
    repeated = "三年前试炼场崩塌，不是意外。叶长青提前改了阵法参数，你爹为了救人，冲进了崩塌区。"
    draft_29 = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter_29.id,
        version_no=1,
        content_md=f"# 第29章 冷锋死线\n\n{repeated}\n\n宁尘没有立刻回答。",
        word_count=60,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    draft_30 = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter_30.id,
        version_no=1,
        content_md=f"# 第30章 沉渊绞杀\n\n{repeated}\n\n陆沉的脸色变得难看。",
        word_count=60,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )

    blockers = export_services.collect_publication_blockers(
        project,
        [(chapter_30, draft_30)],
        comparison_payloads=[(chapter_29, draft_29), (chapter_30, draft_30)],
    )

    assert any("跨章段落重复" in blocker for blocker in blockers)


@pytest.mark.asyncio
async def test_export_project_markdown_blocks_cross_chapter_repetition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter_29 = build_chapter(project.id)
    chapter_29.chapter_number = 29
    chapter_29.title = "冷锋死线"
    chapter_29.status = "complete"
    chapter_29.production_state = "ok"
    chapter_30 = build_chapter(project.id)
    chapter_30.chapter_number = 30
    chapter_30.title = "沉渊绞杀"
    chapter_30.status = "complete"
    chapter_30.production_state = "ok"
    repeated = "周长老的手心滚烫，灵力顺着经脉一路向下，直直撞向丹田深处那枚沉睡的道种。"
    draft_29 = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter_29.id,
        version_no=1,
        content_md=f"# 第29章 冷锋死线\n\n{repeated}\n\n宁尘听见风声贴着耳侧刮过。",
        word_count=80,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    draft_30 = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter_30.id,
        version_no=1,
        content_md=f"# 第30章 沉渊绞杀\n\n{repeated}\n\n陆沉把纸条攥进掌心。",
        word_count=80,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(export_services, "get_project_by_slug", fake_get_project_by_slug)
    settings = build_settings()
    settings.output.base_dir = str(tmp_path / "output")
    session = FakeSession(
        scalar_results=[draft_29, draft_30],
        scalars_results=[[chapter_29, chapter_30]],
    )

    with pytest.raises(ValueError, match="跨章段落重复"):
        await export_services.export_project_markdown(
            session,
            settings,
            "my-story",
        )


@pytest.mark.asyncio
async def test_generate_scene_draft_with_settings_records_llm_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    style = build_style(project.id)

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(draft_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(
        scalar_results=[chapter, scene, 0],
        get_map={(StyleGuideModel, project.id): style},
    )

    settings = build_settings()
    settings.llm.mock = True

    async def fake_load_identity_registry(*args, **kwargs):
        return [
            identity_guard_services.CharacterIdentity(
                name="沈砚",
                gender="male",
                pronoun_set_zh="他",
                pronoun_set_en="he/him",
            ),
            identity_guard_services.CharacterIdentity(
                name="港务官",
                gender="female",
                pronoun_set_zh="她",
                pronoun_set_en="she/her",
            ),
        ]

    monkeypatch.setattr(identity_guard_services, "load_identity_registry", fake_load_identity_registry)
    draft = await draft_services.generate_scene_draft(
        session,
        "my-story",
        1,
        1,
        settings=settings,
    )

    assert draft.llm_run_id is not None
    assert any(isinstance(obj, LlmRunModel) for obj in session.added)


@pytest.mark.asyncio
async def test_generate_scene_draft_direct_settings_injects_premium_blocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    project.genre = "xianxia"
    project.metadata_json = {
        **(project.metadata_json or {}),
        "sub_genre": "凡人流修仙",
        "world_spec": {
            "world_name": "青岚界",
            "power_system": {
                "name": "灵根修行",
                "tiers": ["炼气", "筑基"],
                "protagonist_starting_tier": "炼气十层",
            },
        },
        "cast_spec": {
            "protagonist": {
                "name": "沈砚",
                "power_tier": "炼气十层",
                "resources": [{"resource_key": "筑基丹", "amount": 1}],
                "relationships": [
                    {
                        "character": "港务官",
                        "type": "temporary ally",
                        "tension": (
                            "她要查清筑基丹流向, "
                            "沈砚必须决定是否借她的船离场。"
                        ),
                    }
                ],
            },
            "supporting_cast": [
                {
                    "name": "港务官",
                    "role": "broker",
                    "relationship_to_protagonist": "互相利用的临时盟友",
                    "evolution_arc": "从利益交换到一次有限信任",
                }
            ],
        },
        "factions": [
            {
                "name": "执法堂",
                "goal": "追回秘境中流失的筑基资源。",
                "method": "盘查、封港、追踪丹药气息。",
                "relationship_to_protagonist": "制度性压力",
                "internal_conflict": "长老要立威, 外务执事想私下分润。",
                "next_reaction": "若筑基丹消失, 会先封锁码头再查散修。",
            }
        ],
    }
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    style = build_style(project.id)
    captured: dict[str, SceneWriterContextPacket] = {}

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_identity_registry(*args, **kwargs):
        return [
            identity_guard_services.CharacterIdentity(
                name="沈砚",
                gender="male",
                pronoun_set_zh="他",
                pronoun_set_en="he/him",
            ),
            identity_guard_services.CharacterIdentity(
                name="港务官",
                gender="female",
                pronoun_set_zh="她",
                pronoun_set_en="she/her",
            ),
        ]

    async def fake_build_context(*args, **kwargs):
        packet = SceneWriterContextPacket(
            project_id=project.id,
            project_slug=project.slug,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=1,
            scene_number=1,
            query_text="封港命令",
            story_bible={
                "volume": {"volume_number": 1},
                "world_rules": [
                    {
                        "rule_code": "R-001",
                        "name": "试炼禁令",
                        "description": "秘境偷取筑基丹会触发执法堂追索。",
                        "story_consequence": "主角不能正面带丹离开秘境。",
                        "exploitation_potential": "先藏丹后换身份离场。",
                        "future_backlash": "宗门会追查资源流向。",
                    }
                ],
            },
        )
        captured["context"] = packet
        return packet

    monkeypatch.setattr(draft_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(identity_guard_services, "load_identity_registry", fake_load_identity_registry)
    monkeypatch.setattr(draft_services, "build_scene_writer_context_from_models", fake_build_context)

    session = FakeSession(
        scalar_results=[chapter, scene, 0],
        get_map={(StyleGuideModel, project.id): style},
    )
    settings = build_settings()
    settings.llm.mock = True
    settings.output.base_dir = str(tmp_path)
    profile_path = tmp_path / project.slug / "story-bible" / "ranking-capability-profile.md"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(
        "# 《测试书》榜单级能力 Profile\n\n"
        "- 固定入口：港口秘境。\n"
        "- 可解规则：禁令必须有破局路径和代价。\n",
        encoding="utf-8",
    )

    await draft_services.generate_scene_draft(
        session,
        "my-story",
        1,
        1,
        settings=settings,
    )

    context = captured["context"]
    assert context.ranking_capability_profile_block is not None
    assert "港口秘境" in context.ranking_capability_profile_block
    assert context.progression_context_block is not None
    assert "炼气 → 筑基" in context.progression_context_block
    assert context.decision_policy_block is not None
    assert "public_vanity_duel" in context.decision_policy_block
    assert context.rule_system_context_block is not None
    assert "试炼禁令" in context.rule_system_context_block
    assert context.faction_ecology_context_block is not None
    assert "执法堂" in context.faction_ecology_context_block
    assert context.relationship_agency_context_block is not None
    assert "沈砚 -> 港务官" in context.relationship_agency_context_block


@pytest.mark.asyncio
async def test_generate_scene_draft_direct_call_blocks_pre_draft_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json = {"identity_manifest_status": "locked"}
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    scene.participants = ["陌生人"]
    scene.time_label = None

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    async def fake_load_identity_registry(*args, **kwargs):
        return [
            identity_guard_services.CharacterIdentity(
                name="沈砚",
                gender="male",
                pronoun_set_zh="他",
                pronoun_set_en="he/him",
            )
        ]

    monkeypatch.setattr(draft_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(identity_guard_services, "load_identity_registry", fake_load_identity_registry)

    session = FakeSession(scalar_results=[chapter, scene])
    settings = build_settings()
    settings.llm.mock = True

    with pytest.raises(ValueError, match="pre_draft_scene_contract"):
        await draft_services.generate_scene_draft(
            session,
            "my-story",
            1,
            1,
            settings=settings,
        )

    assert scene.metadata_json["pre_draft_scene_contract"]["passed"] is False


@pytest.mark.asyncio
async def test_run_scene_pipeline_rewrites_until_review_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    initial_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=1,
        content_md="初始草稿",
        word_count=120,
        is_current=True,
        generation_params={},
    )
    initial_draft.id = uuid4()
    initial_draft.llm_run_id = uuid4()

    rewritten_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=2,
        content_md="重写草稿",
        word_count=820,
        is_current=True,
        generation_params={},
    )
    rewritten_draft.id = uuid4()
    rewritten_draft.llm_run_id = uuid4()

    first_report = type("ReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})()
    second_report = type("ReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})()
    quality_a = type("QualityStub", (), {"id": uuid4()})()
    quality_b = type("QualityStub", (), {"id": uuid4()})()
    rewrite_task = type("RewriteTaskStub", (), {"id": uuid4(), "status": "pending"})()

    async def fake_load_scene_identifiers(session, project_slug, chapter_number, scene_number):
        return project, chapter, scene

    async def fake_load_current_scene_draft(session, scene_id):
        return initial_draft

    async def fake_review_scene_draft(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        calls = getattr(fake_review_scene_draft, "calls", 0) + 1
        fake_review_scene_draft.calls = calls
        if calls == 1:
            return (
                type(
                    "ReviewResultStub",
                    (),
                    {"verdict": "rewrite", "severity_max": "medium"},
                )(),
                first_report,
                quality_a,
                rewrite_task,
            )
        return (
            type(
                "ReviewResultStub",
                (),
                {"verdict": "pass", "severity_max": "low"},
            )(),
            second_report,
            quality_b,
            None,
        )

    async def fake_rewrite_scene_from_task(
        session,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        return rewritten_draft, rewrite_task

    async def fake_refresh_scene_knowledge(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        return SceneKnowledgeRefreshResult(
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            canon_fact_ids=[uuid4(), uuid4()],
            timeline_event_ids=[uuid4()],
            canon_facts_created=2,
            canon_facts_reused=0,
            timeline_events_created=1,
            timeline_events_reused=0,
            summary_text="知识层摘要",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(pipeline_services, "_load_scene_identifiers", fake_load_scene_identifiers)
    monkeypatch.setattr(pipeline_services, "_load_current_scene_draft", fake_load_current_scene_draft)
    monkeypatch.setattr(pipeline_services, "review_scene_draft", fake_review_scene_draft)
    monkeypatch.setattr(pipeline_services, "rewrite_scene_from_task", fake_rewrite_scene_from_task)
    monkeypatch.setattr(pipeline_services, "refresh_scene_knowledge", fake_refresh_scene_knowledge)

    session = FakeSession()
    result = await pipeline_services.run_scene_pipeline(
        session,
        build_settings(),
        "my-story",
        1,
        1,
        requested_by="tester",
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    workflow_steps = [obj for obj in session.added if isinstance(obj, WorkflowStepRunModel)]

    assert result.final_verdict == "pass"
    assert result.rewrite_iterations == 1
    assert result.review_iterations == 2
    assert result.canon_fact_count == 2
    assert result.timeline_event_count == 1
    assert result.current_draft_id == rewritten_draft.id
    assert result.requires_human_review is False
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"
    assert len(workflow_steps) == 5


@pytest.mark.asyncio
async def test_run_scene_pipeline_regenerates_when_rewrite_loses_current_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    initial_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=1,
        content_md="初始草稿",
        word_count=120,
        is_current=True,
        generation_params={},
    )
    initial_draft.id = uuid4()
    initial_draft.llm_run_id = uuid4()

    recovered_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=2,
        content_md="重新生成后的草稿",
        word_count=820,
        is_current=True,
        generation_params={},
    )
    recovered_draft.id = uuid4()
    recovered_draft.llm_run_id = uuid4()

    first_report = type("ReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})()
    second_report = type("ReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})()
    quality_a = type("QualityStub", (), {"id": uuid4()})()
    quality_b = type("QualityStub", (), {"id": uuid4()})()
    rewrite_task = type("RewriteTaskStub", (), {"id": uuid4(), "status": "pending"})()

    async def fake_load_scene_identifiers(session, project_slug, chapter_number, scene_number):
        return project, chapter, scene

    async def fake_load_current_scene_draft(session, scene_id):
        return initial_draft

    async def fake_review_scene_draft(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        calls = getattr(fake_review_scene_draft, "calls", 0) + 1
        fake_review_scene_draft.calls = calls
        if calls == 1:
            return (
                type(
                    "ReviewResultStub",
                    (),
                    {"verdict": "rewrite", "severity_max": "medium"},
                )(),
                first_report,
                quality_a,
                rewrite_task,
            )
        return (
            type(
                "ReviewResultStub",
                (),
                {"verdict": "pass", "severity_max": "low"},
            )(),
            second_report,
            quality_b,
            None,
        )

    async def fake_rewrite_scene_from_task(
        session,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        raise ValueError("Scene 2 in chapter 2 does not have a current draft.")

    async def fake_generate_scene_draft(*args, **kwargs):
        fake_generate_scene_draft.calls = getattr(fake_generate_scene_draft, "calls", 0) + 1
        return recovered_draft

    async def fake_refresh_scene_knowledge(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        return SceneKnowledgeRefreshResult(
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            canon_fact_ids=[],
            timeline_event_ids=[],
            canon_facts_created=0,
            canon_facts_reused=0,
            timeline_events_created=0,
            timeline_events_reused=0,
            summary_text="知识层摘要",
            llm_run_id=None,
        )

    monkeypatch.setattr(pipeline_services, "_load_scene_identifiers", fake_load_scene_identifiers)
    monkeypatch.setattr(pipeline_services, "_load_current_scene_draft", fake_load_current_scene_draft)
    monkeypatch.setattr(pipeline_services, "review_scene_draft", fake_review_scene_draft)
    monkeypatch.setattr(pipeline_services, "rewrite_scene_from_task", fake_rewrite_scene_from_task)
    monkeypatch.setattr(pipeline_services, "generate_scene_draft", fake_generate_scene_draft)
    monkeypatch.setattr(pipeline_services, "refresh_scene_knowledge", fake_refresh_scene_knowledge)

    session = FakeSession()
    result = await pipeline_services.run_scene_pipeline(
        session,
        build_settings(),
        "my-story",
        2,
        2,
        requested_by="tester",
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    workflow_steps = [obj for obj in session.added if isinstance(obj, WorkflowStepRunModel)]

    assert result.final_verdict == "pass"
    assert result.current_draft_id == recovered_draft.id
    assert result.review_iterations == 2
    assert result.rewrite_iterations == 1
    assert getattr(fake_generate_scene_draft, "calls", 0) == 1
    assert workflow_runs[0].status == "completed"
    assert workflow_runs[0].metadata_json["scene_rewrite_missing_current_draft_recovered"] is True
    assert any(step.step_name == "recover_missing_scene_draft" for step in workflow_steps)


@pytest.mark.asyncio
async def test_run_scene_pipeline_stops_after_stalled_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    initial_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=1,
        content_md="初始草稿",
        word_count=120,
        is_current=True,
        generation_params={},
    )
    initial_draft.id = uuid4()
    initial_draft.llm_run_id = uuid4()

    rewritten_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=2,
        content_md="重写一次后的草稿",
        word_count=160,
        is_current=True,
        generation_params={},
    )
    rewritten_draft.id = uuid4()
    rewritten_draft.llm_run_id = uuid4()

    rewrite_task = type("RewriteTaskStub", (), {"id": uuid4(), "status": "pending"})()
    report_a = type("ReportStub", (), {"id": uuid4(), "llm_run_id": None})()
    report_b = type("ReportStub", (), {"id": uuid4(), "llm_run_id": None})()
    quality_a = type("QualityStub", (), {"id": uuid4()})()
    quality_b = type("QualityStub", (), {"id": uuid4()})()

    async def fake_load_scene_identifiers(session, project_slug, chapter_number, scene_number):
        return project, chapter, scene

    async def fake_load_current_scene_draft(session, scene_id):
        return initial_draft

    async def fake_review_scene_draft(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        calls = getattr(fake_review_scene_draft, "calls", 0) + 1
        fake_review_scene_draft.calls = calls
        if calls == 1:
            return (
                type(
                    "ReviewResultStub",
                    (),
                    {
                        "verdict": "rewrite",
                        "severity_max": "medium",
                        "scores": type("ScoreStub", (), {"overall": 0.50})(),
                        "rewrite_instructions": "补强冲突和尾钩",
                    },
                )(),
                report_a,
                quality_a,
                rewrite_task,
            )
        score = 0.51 if calls == 2 else 0.515
        return (
            type(
                "ReviewResultStub",
                (),
                {
                    "verdict": "rewrite",
                    "severity_max": "medium",
                    "scores": type("ScoreStub", (), {"overall": score})(),
                    "rewrite_instructions": "补强冲突和尾钩",
                },
            )(),
            report_b,
            quality_b,
            rewrite_task,
        )

    async def fake_rewrite_scene_from_task(
        session,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        calls = getattr(fake_rewrite_scene_from_task, "calls", 0) + 1
        fake_rewrite_scene_from_task.calls = calls
        return rewritten_draft, rewrite_task

    monkeypatch.setattr(pipeline_services, "_load_scene_identifiers", fake_load_scene_identifiers)
    monkeypatch.setattr(pipeline_services, "_load_current_scene_draft", fake_load_current_scene_draft)
    monkeypatch.setattr(pipeline_services, "review_scene_draft", fake_review_scene_draft)
    monkeypatch.setattr(pipeline_services, "rewrite_scene_from_task", fake_rewrite_scene_from_task)

    session = FakeSession()
    settings = build_settings()
    settings.quality.min_scene_rewrite_improvement = 0.03
    settings.pipeline.accept_on_stall = False

    result = await pipeline_services.run_scene_pipeline(
        session,
        settings,
        "my-story",
        1,
        1,
        requested_by="tester",
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]

    assert result.final_verdict == "rewrite"
    assert result.review_iterations == 3
    assert result.rewrite_iterations == 2
    assert result.requires_human_review is True
    assert getattr(fake_rewrite_scene_from_task, "calls", 0) == 2
    assert workflow_runs[0].status == "machine_blocked"
    assert workflow_runs[0].metadata_json["stalled_rewrite"] is True
    assert workflow_runs[0].metadata_json["stalled_rewrite_count"] == 2
    assert workflow_runs[0].current_step == "scene_rewrite_stalled_blocked"


@pytest.mark.asyncio
async def test_run_chapter_pipeline_assembles_and_exports(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _disable_chapter_length_gate(monkeypatch)
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图",
        word_count=1200,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    chapter_draft.id = uuid4()
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="chapter",
        source_id=chapter.id,
        storage_uri=str(tmp_path / "output" / "chapter-001.md"),
        checksum="a" * 64,
        version_label="chapter-001-v1",
    )
    export_artifact.id = uuid4()
    report = type("ChapterReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})()
    quality = type("ChapterQualityStub", (), {"id": uuid4()})()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_run_scene_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        return pipeline_services.ScenePipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            current_draft_id=uuid4(),
            current_draft_version_no=2,
            final_verdict="pass",
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            review_iterations=2,
            rewrite_iterations=1,
            requires_human_review=False,
            llm_run_ids=[],
        )

    async def fake_assemble_chapter_draft(session, project_slug: str, chapter_number: int, *, settings=None):
        return chapter_draft

    async def fake_export_chapter_markdown(
        session,
        settings,
        project_slug: str,
        chapter_number: int,
        **kwargs,
    ):
        output_path = tmp_path / "output" / "chapter-001.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(chapter_draft.content_md, encoding="utf-8")
        return export_artifact, output_path

    async def fake_review_chapter_draft(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return (
            type(
                "ChapterReviewResultStub",
                (),
                {"verdict": "pass", "severity_max": "low"},
            )(),
            report,
            quality,
            None,
        )

    async def fake_retention_noop(*args, **kwargs) -> None:
        pass

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "run_scene_pipeline", fake_run_scene_pipeline)
    monkeypatch.setattr(pipeline_services, "assemble_chapter_draft", fake_assemble_chapter_draft)
    monkeypatch.setattr(pipeline_services, "export_chapter_markdown", fake_export_chapter_markdown)
    monkeypatch.setattr(pipeline_services, "review_chapter_draft", fake_review_chapter_draft)
    monkeypatch.setattr(
        pipeline_services,
        "_evaluate_retention_safety_after_assembly",
        fake_retention_noop,
    )
    monkeypatch.setattr(
        pipeline_services,
        "run_final_quality_gates",
        lambda **_: pipeline_services.FinalQualityGateResult(passed=True),
    )

    session = FakeSession(
        scalar_results=[chapter],
        scalars_results=[[scene]],
    )
    result = await pipeline_services.run_chapter_pipeline(
        session,
        build_settings(),
        "my-story",
        1,
        requested_by="tester",
        export_markdown=True,
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]

    assert result.chapter_draft_id == chapter_draft.id
    assert result.export_artifact_id == export_artifact.id
    assert result.output_path is not None
    assert result.requires_human_review is False
    assert len(result.scene_results) == 1
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"


@pytest.mark.asyncio
async def test_run_chapter_pipeline_blocks_chapter_first_on_predraft_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.target_chapters = 500
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_build_chapter_writer_context(*args, **kwargs):
        return SimpleNamespace()

    def fake_build_chapter_generation_input_bundle(*args, **kwargs):
        return ChapterGenerationInputBundle(
            chapter={"chapter_number": 1},
            scenes=(
                {
                    "scene_number": 1,
                    "gate_function": "continuity: bridge",
                    "visible_progress": "",
                    "reader_payoff": "",
                    "ending_hook_payload": "",
                    "methodology_contract": {},
                },
            ),
            acceptance_contract={
                "chapter_number": 1,
                "must_deliver": [{"label": "chapter_goal", "value": "开门"}],
                "scene_gate_targets": [],
                "front_position_rules": {},
            },
            required_context_keys=("chapter.goal", "chapter_acceptance_contract"),
            missing_context_keys=("chapter_acceptance_contract",),
        )

    async def fail_generate_chapter_draft_once(*args, **kwargs):
        raise AssertionError("predraft gate should block before chapter generation")

    settings = build_settings()
    settings.pipeline.enable_chapter_outline_readiness_gate = False
    # Predraft gate is soft by default now (autonomous-completion self-harm fix);
    # pin the legacy hard-block to keep validating the machine-blocked path.
    settings.pipeline.chapter_predraft_quality_gate_block_on_failure = True

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        pipeline_services,
        "build_chapter_writer_context",
        fake_build_chapter_writer_context,
    )
    monkeypatch.setattr(
        pipeline_services,
        "build_chapter_generation_input_bundle",
        fake_build_chapter_generation_input_bundle,
    )
    monkeypatch.setattr(
        pipeline_services,
        "generate_chapter_draft_once",
        fail_generate_chapter_draft_once,
    )

    session = FakeSession(
        scalar_results=[chapter],
        scalars_results=[[scene]],
    )
    result = await pipeline_services.run_chapter_pipeline(
        session,
        settings,
        "my-story",
        1,
        requested_by="tester",
        chapter_first=True,
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]

    assert result.requires_human_review is True
    assert result.chapter_draft_id is None
    assert chapter.production_state == "blocked"
    assert chapter.metadata_json["blocked_by_chapter_predraft_quality_gate"] is True
    assert workflow_runs[0].status == "machine_blocked"


@pytest.mark.asyncio
async def test_run_chapter_pipeline_short_circuits_after_retention_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.target_chapters = 500
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=7,
        content_md="第一章正文。",
        word_count=2200,
        is_current=True,
    )
    draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_build_chapter_writer_context(*args, **kwargs):
        return SimpleNamespace()

    def fake_build_chapter_generation_input_bundle(*args, **kwargs):
        return ChapterGenerationInputBundle(
            chapter={"chapter_number": 1, "goal": "开门见压"},
            scenes=(
                {
                    "scene_number": 1,
                    "gate_function": "hook: 开门冲突",
                    "visible_progress": "主角拿到账页",
                    "reader_payoff": "看到规则生效",
                    "ending_hook_payload": "镜子回执",
                    "methodology_contract": {
                        "stakes": "十五分钟后入账",
                        "breakpoint": "门外敲门",
                    },
                },
            ),
            acceptance_contract={
                "chapter_number": 1,
                "must_deliver": [{"label": "chapter_goal", "value": "开门见压"}],
                "scene_gate_targets": [],
                "front_position_rules": {},
            },
            required_context_keys=("chapter.goal",),
            missing_context_keys=(),
        )

    async def fake_generate_chapter_draft_once(*args, **kwargs):
        return draft

    async def fake_retention_eval(*args, **kwargs):
        chapter.status = "revision"
        chapter.production_state = "blocked"
        chapter.metadata_json = {
            **(chapter.metadata_json or {}),
            "auto_repair_last_block_codes": ["HOOK_ECHO_MISSING"],
            "retention_retry_count": 10,
        }

    def fake_apply_retention_budget(*args, **kwargs):
        return True

    async def fail_review_chapter_draft(*args, **kwargs):
        raise AssertionError("retention exhaustion should return before review")

    settings = build_settings()
    settings.pipeline.enable_chapter_outline_readiness_gate = False
    settings.pipeline.enable_chapter_predraft_quality_gate = False
    # Opt into STRICT retention enforcement: this test covers the hard-block
    # short-circuit (machine-repair before review). The default is now soft
    # (accept-on-stall + advance) — see test_retention_safety_gate_soft.py and
    # _retention_gate_blocks_for_project.
    settings.pipeline.retention_safety_gate_block_on_failure = True

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        pipeline_services,
        "build_chapter_writer_context",
        fake_build_chapter_writer_context,
    )
    monkeypatch.setattr(
        pipeline_services,
        "build_chapter_generation_input_bundle",
        fake_build_chapter_generation_input_bundle,
    )
    monkeypatch.setattr(
        pipeline_services,
        "generate_chapter_draft_once",
        fake_generate_chapter_draft_once,
    )
    monkeypatch.setattr(
        pipeline_services,
        "_evaluate_retention_safety_after_assembly",
        fake_retention_eval,
    )
    monkeypatch.setattr(
        pipeline_services,
        "_apply_retention_retry_budget",
        fake_apply_retention_budget,
    )
    monkeypatch.setattr(
        pipeline_services,
        "review_chapter_draft",
        fail_review_chapter_draft,
    )

    session = FakeSession(
        scalar_results=[chapter],
        scalars_results=[[scene]],
    )
    result = await pipeline_services.run_chapter_pipeline(
        session,
        settings,
        "my-story",
        1,
        requested_by="tester",
        chapter_first=True,
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]

    # Retention-budget exhaustion routes to the MACHINE deep-repair tier (consumed by
    # the web machine-repair gate / resume flow), not straight to human review: the
    # chapter is MACHINE_BLOCKED with requires_machine_repair, and the pipeline still
    # short-circuits before review (fail_review_chapter_draft would raise if reached).
    assert result.requires_human_review is False
    assert result.final_verdict == "rewrite"
    assert result.chapter_draft_id == draft.id
    assert chapter.production_state == "blocked"
    assert chapter.metadata_json["retention_auto_repair_exhausted"] is True
    assert chapter.metadata_json["requires_machine_repair"] is True
    assert workflow_runs[0].status == "machine_blocked"
    assert workflow_runs[0].current_step == "retention_auto_repair_exhausted"


@pytest.mark.asyncio
async def test_run_chapter_pipeline_runs_fanqie_long_gate_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _disable_chapter_length_gate(monkeypatch)
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 限时反击",
        word_count=1200,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    chapter_draft.id = uuid4()
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="chapter",
        source_id=chapter.id,
        storage_uri=str(tmp_path / "output" / "chapter-001.md"),
        checksum="b" * 64,
        version_label="chapter-001-v1",
    )
    export_artifact.id = uuid4()
    gate_calls: list[int] = []

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_run_scene_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        return pipeline_services.ScenePipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            current_draft_id=uuid4(),
            current_draft_version_no=2,
            final_verdict="pass",
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            review_iterations=1,
            rewrite_iterations=0,
            requires_human_review=False,
            llm_run_ids=[],
        )

    async def fake_assemble_chapter_draft(session, project_slug: str, chapter_number: int, *, settings=None):
        return chapter_draft

    async def fake_export_chapter_markdown(
        session,
        settings,
        project_slug: str,
        chapter_number: int,
        **kwargs,
    ):
        output_path = tmp_path / "output" / "chapter-001.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(chapter_draft.content_md, encoding="utf-8")
        return export_artifact, output_path

    async def fake_review_chapter_draft(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return (
            type("ChapterReviewResultStub", (), {"verdict": "pass", "severity_max": "low"})(),
            type("ChapterReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})(),
            type("ChapterQualityStub", (), {"id": uuid4()})(),
            None,
        )

    async def fake_fanqie_gate(session, **kwargs):
        gate_calls.append(kwargs["chapter_number"])
        return {
            "artifact_id": "fanqie-gate-1",
            "passed": True,
            "critical_count": 0,
            "finding_count": 0,
            "metrics": {"chapter_count": 1},
            "blocks_write": False,
        }

    async def fake_retention_noop(*args, **kwargs) -> None:
        pass

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "run_scene_pipeline", fake_run_scene_pipeline)
    monkeypatch.setattr(pipeline_services, "assemble_chapter_draft", fake_assemble_chapter_draft)
    monkeypatch.setattr(pipeline_services, "export_chapter_markdown", fake_export_chapter_markdown)
    monkeypatch.setattr(pipeline_services, "review_chapter_draft", fake_review_chapter_draft)
    monkeypatch.setattr(pipeline_services, "_run_fanqie_long_gate_for_chapter", fake_fanqie_gate)
    monkeypatch.setattr(
        pipeline_services,
        "_evaluate_retention_safety_after_assembly",
        fake_retention_noop,
    )

    settings = build_settings()
    settings.pipeline.enable_fanqie_long_ranking_gate = True
    session = FakeSession(
        scalar_results=[chapter],
        scalars_results=[[scene]],
    )
    result = await pipeline_services.run_chapter_pipeline(
        session,
        settings,
        "my-story",
        1,
        requested_by="tester",
        export_markdown=True,
    )

    steps = [obj for obj in session.added if isinstance(obj, WorkflowStepRunModel)]
    fanqie_steps = [step for step in steps if step.step_name == "fanqie_long_ranking_gate"]

    assert result.requires_human_review is False
    assert gate_calls == [1]
    assert fanqie_steps
    assert fanqie_steps[0].output_ref["passed"] is True


@pytest.mark.asyncio
async def test_fanqie_long_gate_demotes_to_audit_only_after_attempt_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: keyword-matching ``fanqie_long_ranking_gate`` blocked
    青囊不语问阴阳 ch1 for ~145 versions on 2026-05-25. The fix added a
    per-chapter persistent counter — once a chapter has tripped the
    gate ``block_attempt_cap`` times across pipeline runs we demote it
    to audit-only so downstream LLM judges can arbitrate."""

    project = build_project()
    chapter = build_chapter(project.id)
    # Simulate prior runs: counter at cap - 1 → next failure should trigger
    # demotion (cap == 3 in default settings).
    chapter.metadata_json = {"fanqie_long_ranking_block_attempts": 2}
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=99,
        content_md="纯背景介绍。没有任何冲突词。",
        word_count=200,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    chapter_draft.id = uuid4()

    failing_report = {
        "passed": False,
        "findings": [
            {
                "code": "first_100_pressure_missing",
                "severity": "critical",
                "evidence": "no threat words in first 100 chars",
                "target": "opening.first_100",
                "repair_hint": "add pressure",
            }
        ],
        "metrics": {},
    }
    fake_artifact = SimpleNamespace(id=uuid4(), content=failing_report)

    async def fake_load_texts(session, *, project_slug, through_chapter):
        return {chapter.chapter_number: chapter_draft.content_md}

    async def fake_evaluate_and_persist(
        session, *, project_slug, chapter_texts, protagonist_name
    ):
        return fake_artifact

    monkeypatch.setattr(
        pipeline_services,
        "load_current_chapter_texts_for_fanqie_gate",
        fake_load_texts,
    )
    monkeypatch.setattr(
        pipeline_services,
        "evaluate_and_persist_fanqie_long_readiness",
        fake_evaluate_and_persist,
    )

    session = FakeSession()
    payload = await pipeline_services._run_fanqie_long_gate_for_chapter(
        session,
        project=project,
        project_slug=project.slug,
        chapter_number=chapter.chapter_number,
        chapter_draft=chapter_draft,
        block_on_failure=True,
        chapter=chapter,
        block_attempt_cap=3,
    )

    # Gate finding is unchanged — first_100_pressure_missing still
    # surfaces in audit output — but the hard write-block is dropped.
    assert payload["passed"] is False
    assert payload["blocks_write"] is False, "demoted to audit-only"
    assert payload["block_attempts"] == 3
    assert payload["block_attempts_demoted"] is True
    assert chapter.metadata_json["fanqie_long_ranking_block_attempts"] == 3
    assert chapter.metadata_json["fanqie_long_ranking_block_attempts_demoted"] is True


@pytest.mark.asyncio
async def test_fanqie_long_gate_resets_counter_when_chapter_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Companion to the previous test: once a chapter clears the gate
    we wipe the per-chapter counter so a *future* regression starts
    fresh on its budget rather than already being mid-way demoted."""

    project = build_project()
    chapter = build_chapter(project.id)
    chapter.metadata_json = {
        "fanqie_long_ranking_block_attempts": 2,
        "fanqie_long_ranking_block_attempts_demoted": True,
    }
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=99,
        content_md="林渊被逼到墙角，子时已到，必须当场认账。",
        word_count=200,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    chapter_draft.id = uuid4()

    passing_report = {
        "passed": True,
        "findings": [],
        "metrics": {},
    }
    fake_artifact = SimpleNamespace(id=uuid4(), content=passing_report)

    async def fake_load_texts(session, *, project_slug, through_chapter):
        return {chapter.chapter_number: chapter_draft.content_md}

    async def fake_evaluate_and_persist(
        session, *, project_slug, chapter_texts, protagonist_name
    ):
        return fake_artifact

    monkeypatch.setattr(
        pipeline_services,
        "load_current_chapter_texts_for_fanqie_gate",
        fake_load_texts,
    )
    monkeypatch.setattr(
        pipeline_services,
        "evaluate_and_persist_fanqie_long_readiness",
        fake_evaluate_and_persist,
    )

    session = FakeSession()
    payload = await pipeline_services._run_fanqie_long_gate_for_chapter(
        session,
        project=project,
        project_slug=project.slug,
        chapter_number=chapter.chapter_number,
        chapter_draft=chapter_draft,
        block_on_failure=True,
        chapter=chapter,
        block_attempt_cap=3,
    )

    assert payload["passed"] is True
    assert payload["blocks_write"] is False
    assert payload["block_attempts"] == 0
    assert payload["block_attempts_demoted"] is False
    assert "fanqie_long_ranking_block_attempts" not in chapter.metadata_json
    assert "fanqie_long_ranking_block_attempts_demoted" not in chapter.metadata_json


@pytest.mark.asyncio
async def test_run_chapter_pipeline_exports_checkpoint_when_scene_needs_machine_repair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图\n\n场景草稿待机器修复。",
        word_count=900,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    chapter_draft.id = uuid4()
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="chapter",
        source_id=chapter.id,
        storage_uri=str(tmp_path / "output" / "chapter-001.md"),
        checksum="c" * 64,
        version_label="chapter-001-v1",
    )
    export_artifact.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_run_scene_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        return pipeline_services.ScenePipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            current_draft_id=uuid4(),
            current_draft_version_no=1,
            final_verdict="rewrite",
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            rewrite_task_id=uuid4(),
            review_iterations=2,
            rewrite_iterations=1,
            requires_human_review=True,
            llm_run_ids=[],
        )

    async def fake_assemble_chapter_draft(session, project_slug: str, chapter_number: int, *, settings=None):
        return chapter_draft

    async def fake_export_chapter_markdown(
        session,
        settings,
        project_slug: str,
        chapter_number: int,
        **kwargs,
    ):
        output_path = tmp_path / "output" / "chapter-001.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(chapter_draft.content_md, encoding="utf-8")
        return export_artifact, output_path

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "run_scene_pipeline", fake_run_scene_pipeline)
    monkeypatch.setattr(pipeline_services, "assemble_chapter_draft", fake_assemble_chapter_draft)
    monkeypatch.setattr(pipeline_services, "export_chapter_markdown", fake_export_chapter_markdown)
    monkeypatch.setattr(
        pipeline_services,
        "_evaluate_retention_safety_after_assembly",
        AsyncMock(
            side_effect=AssertionError(
                "scene machine-blocked drafts must not enter retention auto-repair"
            )
        ),
    )
    monkeypatch.setattr(
        "bestseller.services.drafts.maybe_prepare_chapter_auto_repair",
        AsyncMock(
            side_effect=AssertionError(
                "scene machine-blocked drafts must not trigger chapter auto-repair"
            )
        ),
    )

    session = FakeSession(
        scalar_results=[chapter],
        scalars_results=[[scene]],
    )
    result = await pipeline_services.run_chapter_pipeline(
        session,
        build_settings(),
        "my-story",
        1,
        requested_by="tester",
        export_markdown=True,
    )

    assert result.requires_human_review is True
    assert result.chapter_draft_id == chapter_draft.id
    assert result.export_artifact_id == export_artifact.id
    assert result.output_path is not None
    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    assert workflow_runs[0].status == "machine_blocked"
    assert workflow_runs[0].current_step == "scene_machine_repair_required"
    assert workflow_runs[0].metadata_json["auto_repair_skipped_reason"] == "scene_machine_blocked"


@pytest.mark.asyncio
async def test_run_chapter_pipeline_repairs_scene_block_before_assembly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图\n\n修复后章节。",
        word_count=1200,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    chapter_draft.id = uuid4()
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="chapter",
        source_id=chapter.id,
        storage_uri=str(tmp_path / "output" / "chapter-001.md"),
        checksum="c" * 64,
        version_label="chapter-001-v1",
    )
    export_artifact.id = uuid4()
    report = type("ChapterReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})()
    quality = type("ChapterQualityStub", (), {"id": uuid4()})()
    scene_calls = {"count": 0}

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_run_scene_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        scene_calls["count"] += 1
        if scene_calls["count"] == 1:
            raise WriteSafetyBlockError(
                "blocked",
                findings=[
                    WriteSafetyFinding(
                        source="contradiction",
                        code="character_resurrection",
                        severity="critical",
                        message="dead character appeared",
                    )
                ],
            )
        return pipeline_services.ScenePipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            current_draft_id=uuid4(),
            current_draft_version_no=1,
            final_verdict="pass",
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            review_iterations=1,
            rewrite_iterations=0,
            requires_human_review=False,
            llm_run_ids=[],
        )

    async def fake_prepare_auto_repair(session, *, project, chapter, repairable_codes, attempt_number=1):
        chapter.production_state = "pending"
        scene.status = "needs_rewrite"
        return True, ("character_resurrection",)

    async def fake_assemble_chapter_draft(session, project_slug: str, chapter_number: int, *, settings=None):
        assert scene_calls["count"] == 2
        return chapter_draft

    async def fake_export_chapter_markdown(
        session,
        settings,
        project_slug: str,
        chapter_number: int,
        **kwargs,
    ):
        output_path = tmp_path / "output" / "chapter-001.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(chapter_draft.content_md, encoding="utf-8")
        return export_artifact, output_path

    async def fake_review_chapter_draft(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return (
            type("ChapterReviewResultStub", (), {"verdict": "pass", "severity_max": "low"})(),
            report,
            quality,
            None,
        )

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "run_scene_pipeline", fake_run_scene_pipeline)
    monkeypatch.setattr(pipeline_services, "assemble_chapter_draft", fake_assemble_chapter_draft)
    monkeypatch.setattr(pipeline_services, "export_chapter_markdown", fake_export_chapter_markdown)
    monkeypatch.setattr(pipeline_services, "review_chapter_draft", fake_review_chapter_draft)
    monkeypatch.setattr(
        "bestseller.services.drafts.maybe_prepare_chapter_auto_repair",
        fake_prepare_auto_repair,
    )

    session = FakeSession(
        scalar_results=[chapter],
        scalars_results=[[scene], [scene]],
    )
    result = await pipeline_services.run_chapter_pipeline(
        session,
        build_settings(),
        "my-story",
        1,
        requested_by="tester",
        export_markdown=True,
    )

    assert result.chapter_draft_id == chapter_draft.id
    assert scene_calls["count"] == 2


@pytest.mark.asyncio
async def test_run_chapter_pipeline_rewrites_until_review_passes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _disable_chapter_length_gate(monkeypatch)
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    initial_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图\n\n## 场景 1：封港命令",
        word_count=900,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    initial_draft.id = uuid4()
    rewritten_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=2,
        content_md="# 第1章 失准星图\n\n## 场景 1：封港命令\n\n章节重写完成。",
        word_count=1800,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    rewritten_draft.id = uuid4()
    first_report = type("ChapterReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})()
    second_report = type("ChapterReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})()
    quality_a = type("ChapterQualityStub", (), {"id": uuid4()})()
    quality_b = type("ChapterQualityStub", (), {"id": uuid4()})()
    rewrite_task = type("ChapterRewriteTaskStub", (), {"id": uuid4(), "status": "pending"})()
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="chapter",
        source_id=chapter.id,
        storage_uri=str(tmp_path / "output" / "chapter-001.md"),
        checksum="b" * 64,
        version_label="chapter-001-v2",
    )
    export_artifact.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_run_scene_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        return pipeline_services.ScenePipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            current_draft_id=uuid4(),
            current_draft_version_no=2,
            final_verdict="pass",
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            review_iterations=2,
            rewrite_iterations=1,
            requires_human_review=False,
            llm_run_ids=[],
        )

    async def fake_assemble_chapter_draft(session, project_slug: str, chapter_number: int, *, settings=None):
        calls = getattr(fake_assemble_chapter_draft, "calls", 0) + 1
        fake_assemble_chapter_draft.calls = calls
        return initial_draft if calls == 1 else rewritten_draft

    async def fake_review_chapter_draft(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        calls = getattr(fake_review_chapter_draft, "calls", 0) + 1
        fake_review_chapter_draft.calls = calls
        if calls == 1:
            return (
                type(
                    "ChapterReviewResultStub",
                    (),
                    {"verdict": "rewrite", "severity_max": "medium"},
                )(),
                first_report,
                quality_a,
                rewrite_task,
            )
        return (
            type(
                "ChapterReviewResultStub",
                (),
                {"verdict": "pass", "severity_max": "low"},
            )(),
            second_report,
            quality_b,
            None,
        )

    async def fake_rewrite_chapter_from_task(
        session,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        rewrite_task.status = "completed"
        return rewritten_draft, rewrite_task

    async def fake_export_chapter_markdown(
        session,
        settings,
        project_slug: str,
        chapter_number: int,
        **kwargs,
    ):
        output_path = tmp_path / "output" / "chapter-001.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rewritten_draft.content_md, encoding="utf-8")
        return export_artifact, output_path

    async def fake_retention_noop(*args, **kwargs) -> None:
        pass

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "run_scene_pipeline", fake_run_scene_pipeline)
    monkeypatch.setattr(pipeline_services, "assemble_chapter_draft", fake_assemble_chapter_draft)
    monkeypatch.setattr(pipeline_services, "review_chapter_draft", fake_review_chapter_draft)
    monkeypatch.setattr(
        pipeline_services,
        "rewrite_chapter_from_task",
        fake_rewrite_chapter_from_task,
    )
    monkeypatch.setattr(pipeline_services, "export_chapter_markdown", fake_export_chapter_markdown)
    monkeypatch.setattr(
        pipeline_services,
        "_evaluate_retention_safety_after_assembly",
        fake_retention_noop,
    )

    session = FakeSession(
        scalar_results=[chapter],
        scalars_results=[[scene]],
    )
    result = await pipeline_services.run_chapter_pipeline(
        session,
        build_settings(),
        "my-story",
        1,
        requested_by="tester",
        export_markdown=True,
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    workflow_steps = [obj for obj in session.added if isinstance(obj, WorkflowStepRunModel)]

    assert result.final_verdict == "pass"
    assert result.chapter_draft_id == rewritten_draft.id
    assert result.chapter_rewrite_iterations == 1
    assert result.chapter_review_iterations == 2
    assert result.review_report_id == second_report.id
    assert result.quality_score_id == quality_b.id
    assert result.export_artifact_id == export_artifact.id
    assert result.requires_human_review is False
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"
    # 8 = the historical 7 chapter-loop steps + the warn-only
    # opening_golden_chapter_gate step (the synthetic ch1 draft has no
    # tension signal, so the advisory gate records its findings).
    assert len(workflow_steps) == 8
    step_names = {step.step_name for step in workflow_steps}
    assert "opening_golden_chapter_gate" in step_names


@pytest.mark.asyncio
async def test_run_chapter_pipeline_blocks_failed_review_even_when_accept_on_stall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_chapter_length_gate(monkeypatch)
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图\n\n章节仍不合格。",
        word_count=900,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    chapter_draft.id = uuid4()
    report = type("ChapterReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})()
    quality = type("ChapterQualityStub", (), {"id": uuid4()})()
    rewrite_task = type("ChapterRewriteTaskStub", (), {"id": uuid4(), "status": "pending"})()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_run_scene_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        return pipeline_services.ScenePipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            current_draft_id=uuid4(),
            current_draft_version_no=1,
            final_verdict="pass",
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            review_iterations=1,
            rewrite_iterations=0,
            requires_human_review=False,
            llm_run_ids=[],
        )

    async def fake_assemble_chapter_draft(session, project_slug: str, chapter_number: int, *, settings=None):
        return chapter_draft

    async def fake_review_chapter_draft(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return (
            type("ChapterReviewResultStub", (), {"verdict": "rewrite", "severity_max": "high"})(),
            report,
            quality,
            rewrite_task,
        )

    async def fake_retention_noop(*args, **kwargs) -> None:
        pass

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "run_scene_pipeline", fake_run_scene_pipeline)
    monkeypatch.setattr(pipeline_services, "assemble_chapter_draft", fake_assemble_chapter_draft)
    monkeypatch.setattr(pipeline_services, "review_chapter_draft", fake_review_chapter_draft)
    monkeypatch.setattr(
        pipeline_services,
        "_evaluate_retention_safety_after_assembly",
        fake_retention_noop,
    )

    settings = build_settings()
    settings.pipeline.accept_on_stall = True
    settings.pipeline.chapter_review_block_on_failure = True
    settings.quality.max_chapter_revisions = 0
    session = FakeSession(
        scalar_results=[chapter],
        scalars_results=[[scene]],
    )
    result = await pipeline_services.run_chapter_pipeline(
        session,
        settings,
        "my-story",
        1,
        requested_by="tester",
        export_markdown=False,
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]

    assert result.final_verdict == "rewrite"
    assert result.requires_human_review is True
    assert result.chapter_draft_id == chapter_draft.id
    assert chapter.status == "revision"
    assert chapter.production_state == "blocked"
    assert workflow_runs[0].status == "machine_blocked"


@pytest.mark.asyncio
async def test_run_chapter_pipeline_records_quality_debt_after_review_stall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_chapter_length_gate(monkeypatch)
    project = build_project()
    chapter = build_chapter(project.id)
    scene = build_scene(project.id, chapter.id)
    chapter_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第1章 失准星图\n\n草稿通过确定性质量门禁，但评论仍建议继续打磨。",
        word_count=2200,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    chapter_draft.id = uuid4()
    report = type("ChapterReportStub", (), {"id": uuid4(), "llm_run_id": uuid4()})()
    quality = QualityScoreModel(
        project_id=project.id,
        target_type="chapter",
        target_id=chapter.id,
        score_overall=0.55,
        evidence_summary={},
    )
    quality.id = uuid4()
    rewrite_task = type("ChapterRewriteTaskStub", (), {"id": uuid4(), "status": "pending"})()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_run_scene_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        scene_number,
        **kwargs,
    ):
        return pipeline_services.ScenePipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter.id,
            scene_id=scene.id,
            chapter_number=chapter.chapter_number,
            scene_number=scene.scene_number,
            current_draft_id=uuid4(),
            current_draft_version_no=1,
            final_verdict="pass",
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            review_iterations=1,
            rewrite_iterations=0,
            requires_human_review=False,
            llm_run_ids=[],
        )

    async def fake_assemble_chapter_draft(session, project_slug: str, chapter_number: int, *, settings=None):
        chapter.production_state = "ok"
        chapter.current_word_count = chapter_draft.word_count
        return chapter_draft

    async def fake_review_chapter_draft(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return (
            type("ChapterReviewResultStub", (), {"verdict": "rewrite", "severity_max": "high"})(),
            report,
            quality,
            rewrite_task,
        )

    async def fake_retention_noop(*args, **kwargs) -> None:
        pass

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "run_scene_pipeline", fake_run_scene_pipeline)
    monkeypatch.setattr(pipeline_services, "assemble_chapter_draft", fake_assemble_chapter_draft)
    monkeypatch.setattr(pipeline_services, "review_chapter_draft", fake_review_chapter_draft)
    monkeypatch.setattr(
        pipeline_services,
        "_evaluate_retention_safety_after_assembly",
        fake_retention_noop,
    )

    settings = build_settings()
    settings.pipeline.accept_on_stall = True
    settings.pipeline.chapter_review_block_on_failure = False
    settings.quality.max_chapter_revisions = 0
    session = FakeSession(
        scalar_results=[chapter],
        scalars_results=[[scene]],
    )

    result = await pipeline_services.run_chapter_pipeline(
        session,
        settings,
        "my-story",
        1,
        requested_by="tester",
        export_markdown=False,
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]

    assert result.final_verdict == "rewrite"
    assert result.requires_human_review is True
    assert result.chapter_draft_id == chapter_draft.id
    assert chapter.status == "revision"
    assert chapter.production_state == "quality_debt"
    assert workflow_runs[0].status == "completed"
    assert workflow_runs[0].metadata_json["requires_human_review"] is True
    assert workflow_runs[0].metadata_json["chapter_quality_debt"] is True
    assert workflow_runs[0].metadata_json["chapter_quality_debt_reason"] == (
        "chapter_rewrite_revision_limit"
    )


@pytest.mark.asyncio
async def test_best_chapter_draft_tie_prefers_target_and_syncs_chapter_word_count() -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.target_word_count = 2_600
    chapter.current_word_count = 2_477
    near_target = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="接近目标的版本",
        word_count=2_598,
        assembled_from_scene_draft_ids=[],
        is_current=False,
    )
    near_target.id = uuid4()
    latest = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=2,
        content_md="更短但同分的版本",
        word_count=2_477,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    latest.id = uuid4()
    equal_score_a = SimpleNamespace(score_overall=0.55)
    equal_score_b = SimpleNamespace(score_overall=0.55)

    class Rows:
        def all(self):
            return [(near_target, equal_score_a), (latest, equal_score_b)]

    class Session:
        async def execute(self, _statement):
            return Rows()

        async def scalar(self, _statement):
            return latest

        async def flush(self):
            return None

    selected = await pipeline_services._promote_best_scoring_chapter_draft_on_stall(
        Session(),  # type: ignore[arg-type]
        chapter=chapter,
        current_draft=latest,
        project=None,
    )

    assert selected.id == near_target.id
    assert near_target.is_current is True
    assert latest.is_current is False
    assert chapter.current_word_count == 2_598


@pytest.mark.asyncio
async def test_run_project_pipeline_exports_project_checkpoint_when_machine_repair_required(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=1,
        scene_results=[],
        chapter_draft_id=uuid4(),
        chapter_draft_version_no=1,
        export_artifact_id=uuid4(),
        output_path=str(tmp_path / "output" / "chapter-001.md"),
        requires_human_review=True,
    )
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="project",
        source_id=project.id,
        storage_uri=str(tmp_path / "output" / "project.md"),
        checksum="d" * 64,
        version_label="project-current",
    )
    export_artifact.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter]

    child_progress_callbacks: list[object] = []

    async def fake_run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        child_progress_callbacks.append(kwargs.get("progress"))
        return chapter_result

    async def fake_export_project_markdown(session, settings, project_slug: str, **kwargs):
        output_path = tmp_path / "output" / "project.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# My Story", encoding="utf-8")
        return export_artifact, output_path

    async def fake_review_project_consistency(
        session,
        settings,
        project_slug: str,
        **kwargs,
    ):
        return (
            type("ProjectReviewResultStub", (), {"verdict": "attention"})(),
            type("ProjectReviewReportStub", (), {"id": uuid4()})(),
            type("ProjectReviewQualityStub", (), {"id": uuid4()})(),
        )

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(pipeline_services, "export_project_markdown", fake_export_project_markdown)
    monkeypatch.setattr(
        pipeline_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        AsyncMock(
            return_value=type(
                "NarrativeGraphResultStub",
                (),
                {"workflow_run_id": uuid4(), "plot_arc_count": 3, "clue_count": 1},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        AsyncMock(
            return_value=type(
                "NarrativeTreeResultStub",
                (),
                {"workflow_run_id": uuid4(), "node_count": 16},
            )()
        ),
    )

    session = FakeSession()
    progress_events: list[tuple[str, dict[str, object]]] = []
    result = await pipeline_services.run_project_pipeline(
        session,
        build_settings(),
        "my-story",
        requested_by="tester",
        export_markdown=True,
        progress=lambda stage, payload: progress_events.append((stage, payload)),
    )

    assert result.requires_human_review is True
    assert result.export_artifact_id == export_artifact.id
    assert result.output_path is not None
    assert child_progress_callbacks and callable(child_progress_callbacks[0])
    assert any(stage == "chapter_pipeline_started" for stage, _ in progress_events)


@pytest.mark.asyncio
async def test_run_project_pipeline_passes_concept_lab_context_to_material_forge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    bundle = build_concept_lab_catalog("apocalypse-supply", count=1).bundles[0]
    project.metadata_json = {
        **(project.metadata_json or {}),
        "concept_lab": bundle.model_dump(mode="json"),
    }
    chapter = build_chapter(project.id)
    captured: dict[str, object] = {}
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=1,
        scene_results=[],
        chapter_draft_id=uuid4(),
        chapter_draft_version_no=1,
        export_artifact_id=uuid4(),
        output_path=str(tmp_path / "output" / "chapter-001.md"),
        requires_human_review=False,
    )
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="project",
        source_id=project.id,
        storage_uri=str(tmp_path / "output" / "project.md"),
        checksum="f" * 64,
        version_label="project-current",
    )
    export_artifact.id = uuid4()

    class _CountResult:
        def scalar_one(self) -> int:
            return 0

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter]

    async def fake_forge_all_materials(*args: object, **kwargs: object) -> list[object]:
        captured["concept_lab_context"] = kwargs.get("concept_lab_context")
        return [SimpleNamespace(emitted_count=3)]

    async def fake_run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return chapter_result

    async def fake_export_project_markdown(session, settings, project_slug: str, **kwargs):
        output_path = tmp_path / "output" / "project.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# My Story", encoding="utf-8")
        return export_artifact, output_path

    async def fake_review_project_consistency(
        session,
        settings,
        project_slug: str,
        **kwargs,
    ):
        return (
            type("ProjectReviewResultStub", (), {"verdict": "pass"})(),
            type("ProjectReviewReportStub", (), {"id": uuid4()})(),
            type("ProjectReviewQualityStub", (), {"id": uuid4()})(),
        )

    from bestseller.services import material_forge

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(material_forge, "forge_all_materials", fake_forge_all_materials)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(pipeline_services, "export_project_markdown", fake_export_project_markdown)
    monkeypatch.setattr(
        pipeline_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        AsyncMock(
            return_value=type(
                "NarrativeGraphResultStub",
                (),
                {"workflow_run_id": uuid4(), "plot_arc_count": 3, "clue_count": 1},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        AsyncMock(
            return_value=type(
                "NarrativeTreeResultStub",
                (),
                {"workflow_run_id": uuid4(), "node_count": 16},
            )()
        ),
    )

    settings = build_settings()
    settings.pipeline.enable_forge_pipeline = True
    session = FakeSession(execute_results=[_CountResult()])
    progress_events: list[tuple[str, dict[str, object]]] = []

    result = await pipeline_services.run_project_pipeline(
        session,
        settings,
        "my-story",
        requested_by="tester",
        export_markdown=True,
        progress=lambda stage, payload: progress_events.append((stage, payload)),
    )

    context = str(captured["concept_lab_context"])
    assert result.final_verdict == "pass"
    assert "已选脑洞物料合同" in context
    assert bundle.reader_promise in context
    assert bundle.material_brief.query_terms[0] in context
    assert ("material_forge_completed", {
        "project_slug": project.slug,
        "total_forged": 3,
        "concept_lab_material_brief": True,
    }) in progress_events


@pytest.mark.asyncio
@pytest.mark.parametrize("pause_mode, expected_calls", [(False, [1, 2]), (True, [1])])
async def test_run_project_pipeline_review_flagged_chapter_pause_vs_continue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pause_mode: bool,
    expected_calls: list[int],
) -> None:
    # Default (pause_mode=False): a chapter whose scenes only "require human
    # review" no longer halts the whole book — the loop continues writing the
    # remaining chapters and flags them. Legacy hard-pause is opt-in via
    # ``whole_book_pause_on_scene_review=True`` (pause_mode=True → stops at [1]).
    project = build_project()
    chapter1 = build_chapter(project.id)
    chapter2 = build_chapter(project.id)
    chapter2.chapter_number = 2
    calls: list[int] = []

    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="project",
        source_id=project.id,
        storage_uri=str(tmp_path / "output" / "project.md"),
        checksum="e" * 64,
        version_label="project-current",
    )
    export_artifact.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter1, chapter2]

    async def fake_run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        calls.append(chapter_number)
        return pipeline_services.ChapterPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter1.id,
            chapter_number=chapter_number,
            scene_results=[],
            chapter_draft_id=uuid4(),
            chapter_draft_version_no=1,
            export_artifact_id=uuid4(),
            output_path=str(tmp_path / "output" / f"chapter-{chapter_number:03d}.md"),
            requires_human_review=True,
        )

    async def fake_export_project_markdown(session, settings, project_slug: str, **kwargs):
        output_path = tmp_path / "output" / "project.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# My Story", encoding="utf-8")
        return export_artifact, output_path

    async def fake_review_project_consistency(
        session,
        settings,
        project_slug: str,
        **kwargs,
    ):
        return (
            type("ProjectReviewResultStub", (), {"verdict": "attention"})(),
            type("ProjectReviewReportStub", (), {"id": uuid4()})(),
            type("ProjectReviewQualityStub", (), {"id": uuid4()})(),
        )

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(pipeline_services, "export_project_markdown", fake_export_project_markdown)
    monkeypatch.setattr(
        pipeline_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        AsyncMock(
            return_value=type(
                "NarrativeGraphResultStub",
                (),
                {"workflow_run_id": uuid4(), "plot_arc_count": 3, "clue_count": 1},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        AsyncMock(
            return_value=type(
                "NarrativeTreeResultStub",
                (),
                {"workflow_run_id": uuid4(), "node_count": 16},
            )()
        ),
    )

    settings = load_settings(
        env={
            "BESTSELLER__PIPELINE__WHOLE_BOOK_PAUSE_ON_SCENE_REVIEW": (
                "true" if pause_mode else "false"
            ),
            "BESTSELLER__PIPELINE__ENABLE_OUTLINE_SEMANTIC_GATE": "false",
            "BESTSELLER__PIPELINE__ENABLE_ROLLING_OUTLINE": "false",
        }
    )
    result = await pipeline_services.run_project_pipeline(
        FakeSession(),
        settings,
        "my-story",
        requested_by="tester",
        export_markdown=True,
    )

    assert calls == expected_calls
    assert [item.chapter_number for item in result.chapter_results] == expected_calls
    assert result.requires_human_review is True


@pytest.mark.asyncio
async def test_run_project_pipeline_materializes_and_exports(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    materialization_result = type(
        "MaterializationResultStub",
        (),
        {"workflow_run_id": uuid4()},
    )()
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=1,
        scene_results=[],
        chapter_draft_id=uuid4(),
        chapter_draft_version_no=1,
        export_artifact_id=uuid4(),
        output_path=str(tmp_path / "output" / "chapter-001.md"),
        requires_human_review=False,
    )
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="project",
        source_id=project.id,
        storage_uri=str(tmp_path / "output" / "project.md"),
        checksum="a" * 64,
        version_label="project-current",
    )
    export_artifact.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter]

    async def fake_materialize_latest(session, project_slug: str, **kwargs):
        return materialization_result

    async def fake_run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return chapter_result

    async def fake_export_project_markdown(session, settings, project_slug: str, **kwargs):
        output_path = tmp_path / "output" / "project.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# My Story", encoding="utf-8")
        return export_artifact, output_path

    async def fake_review_project_consistency(
        session,
        settings,
        project_slug: str,
        **kwargs,
    ):
        return (
            type("ProjectReviewResultStub", (), {"verdict": "pass"})(),
            type("ProjectReviewReportStub", (), {"id": uuid4()})(),
            type("ProjectReviewQualityStub", (), {"id": uuid4()})(),
        )

    async def fake_get_latest_planning_artifact(session, project_id, artifact_type):
        return object()

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_chapter_outline_batch",
        fake_materialize_latest,
    )
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(pipeline_services, "export_project_markdown", fake_export_project_markdown)
    monkeypatch.setattr(
        pipeline_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        AsyncMock(
            return_value=type(
                "NarrativeGraphResultStub",
                (),
                {"workflow_run_id": uuid4(), "plot_arc_count": 3, "clue_count": 1},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        AsyncMock(
            return_value=type(
                "NarrativeTreeResultStub",
                (),
                {"workflow_run_id": uuid4(), "node_count": 16},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "get_latest_planning_artifact",
        fake_get_latest_planning_artifact,
    )

    session = FakeSession()
    result = await pipeline_services.run_project_pipeline(
        session,
        build_settings(),
        "my-story",
        requested_by="tester",
        materialize_outline=True,
        export_markdown=True,
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]

    assert isinstance(result, ProjectPipelineResult)
    assert result.materialization_workflow_run_id == materialization_result.workflow_run_id
    assert result.export_artifact_id == export_artifact.id
    assert result.requires_human_review is False
    assert len(result.chapter_results) == 1
    assert len(workflow_runs) == 1
    assert workflow_runs[0].status == "completed"


@pytest.mark.asyncio
async def test_run_project_pipeline_completes_ten_chapters_in_chapter_first_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The book orchestrator must drive ten whole chapters, never scene prose."""

    project = build_project()
    project.target_chapters = 10
    chapters = [build_chapter(project.id) for _ in range(10)]
    for number, chapter in enumerate(chapters, start=1):
        chapter.chapter_number = number
        chapter.metadata_json = {
            **(chapter.metadata_json or {}),
            "whole_chapter_logic_contract": {"chapter": number},
        }
    calls: list[tuple[int, bool | None]] = []
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="project",
        source_id=project.id,
        storage_uri=str(tmp_path / "output" / "project.md"),
        checksum="b" * 64,
        version_label="project-current",
    )
    export_artifact.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return chapters

    async def fake_run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        calls.append((chapter_number, kwargs.get("chapter_first")))
        chapter = chapters[chapter_number - 1]
        chapter.status = "complete"
        chapter.production_state = "ok"
        chapter.current_word_count = 2800
        return pipeline_services.ChapterPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter.id,
            chapter_number=chapter_number,
            scene_results=[],
            chapter_draft_id=uuid4(),
            chapter_draft_version_no=1,
            export_artifact_id=uuid4(),
            output_path=str(
                tmp_path / "output" / f"chapter-{chapter_number:03d}.md"
            ),
            requires_human_review=False,
        )

    async def fake_export_project_markdown(session, settings, project_slug, **kwargs):
        output_path = tmp_path / "output" / "project.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# 十章整书\n", encoding="utf-8")
        return export_artifact, output_path

    async def fake_review_project_consistency(*args, **kwargs):
        return (
            SimpleNamespace(verdict="pass"),
            SimpleNamespace(id=uuid4()),
            SimpleNamespace(id=uuid4()),
        )

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(
        pipeline_services,
        "export_project_markdown",
        fake_export_project_markdown,
    )
    monkeypatch.setattr(
        pipeline_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )

    result = await pipeline_services.run_project_pipeline(
        FakeSession(),
        build_settings(),
        "my-story",
        requested_by="mode-b-book-framework-test",
        materialize_narrative_graph=False,
        materialize_narrative_tree=False,
        export_markdown=True,
        chapter_first=True,
        stop_on_chapter_failure=True,
    )

    assert calls == [(number, True) for number in range(1, 11)]
    assert [item.chapter_number for item in result.chapter_results] == list(
        range(1, 11)
    )
    assert all(item.approved_scene_count == 0 for item in result.chapter_results)
    assert result.final_verdict == "pass"
    assert result.requires_human_review is False
    assert result.output_path is not None


@pytest.mark.asyncio
async def test_run_project_pipeline_blocks_project_consistency_failure_despite_accept_on_stall(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=1,
        scene_results=[],
        chapter_draft_id=uuid4(),
        chapter_draft_version_no=1,
        export_artifact_id=uuid4(),
        output_path=str(tmp_path / "output" / "chapter-001.md"),
        requires_human_review=False,
    )
    export_artifact = ExportArtifactModel(
        project_id=project.id,
        export_type="markdown",
        source_scope="project",
        source_id=project.id,
        storage_uri=str(tmp_path / "output" / "project.md"),
        checksum="c" * 64,
        version_label="project-current",
    )
    export_artifact.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter]

    async def fake_run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return chapter_result

    async def fake_export_project_markdown(session, settings, project_slug: str, **kwargs):
        output_path = tmp_path / "output" / "project.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# My Story", encoding="utf-8")
        return export_artifact, output_path

    async def fake_review_project_consistency(
        session,
        settings,
        project_slug: str,
        **kwargs,
    ):
        return (
            type("ProjectReviewResultStub", (), {"verdict": "attention"})(),
            type("ProjectReviewReportStub", (), {"id": uuid4()})(),
            type("ProjectReviewQualityStub", (), {"id": uuid4()})(),
        )

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(pipeline_services, "export_project_markdown", fake_export_project_markdown)
    monkeypatch.setattr(
        pipeline_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        AsyncMock(
            return_value=type(
                "NarrativeGraphResultStub",
                (),
                {"workflow_run_id": uuid4(), "plot_arc_count": 3, "clue_count": 1},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        AsyncMock(
            return_value=type(
                "NarrativeTreeResultStub",
                (),
                {"workflow_run_id": uuid4(), "node_count": 16},
            )()
        ),
    )

    settings = build_settings()
    settings.pipeline.accept_on_stall = True
    result = await pipeline_services.run_project_pipeline(
        FakeSession(),
        settings,
        "my-story",
        requested_by="tester",
        export_markdown=True,
    )

    assert result.requires_human_review is True
    assert result.final_verdict == "attention"


@pytest.mark.asyncio
async def test_run_project_pipeline_warns_on_partial_project_consistency_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=1,
        scene_results=[],
        chapter_draft_id=uuid4(),
        chapter_draft_version_no=1,
        requires_human_review=False,
    )

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter]

    async def fake_run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return chapter_result

    async def fake_review_project_consistency(
        session,
        settings,
        project_slug: str,
        **kwargs,
    ):
        return (
            type("ProjectReviewResultStub", (), {"verdict": "attention"})(),
            type("ProjectReviewReportStub", (), {"id": uuid4()})(),
            type("ProjectReviewQualityStub", (), {"id": uuid4()})(),
        )

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(
        pipeline_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        AsyncMock(
            return_value=type(
                "NarrativeGraphResultStub",
                (),
                {"workflow_run_id": uuid4(), "plot_arc_count": 3, "clue_count": 1},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        AsyncMock(
            return_value=type(
                "NarrativeTreeResultStub",
                (),
                {"workflow_run_id": uuid4(), "node_count": 16},
            )()
        ),
    )

    settings = build_settings()
    settings.pipeline.project_consistency_block_on_failure = True
    session = FakeSession()
    result = await pipeline_services.run_project_pipeline(
        session,
        settings,
        "my-story",
        requested_by="tester",
        export_markdown=False,
        current_volume_number=2,
        total_volumes=3,
        chapter_numbers={1},
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]

    assert result.requires_human_review is False
    assert result.final_verdict == "attention"
    assert workflow_runs[0].status == "completed"
    assert workflow_runs[0].metadata_json["project_consistency_warn_only"] is True
    assert workflow_runs[0].metadata_json["project_consistency_scope"] == "partial_volume"


@pytest.mark.asyncio
async def test_run_project_pipeline_warns_on_draft_mode_project_consistency_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=1,
        scene_results=[],
        chapter_draft_id=uuid4(),
        chapter_draft_version_no=1,
        requires_human_review=False,
    )

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter]

    async def fake_run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return chapter_result

    async def fake_review_project_consistency(
        session,
        settings,
        project_slug: str,
        **kwargs,
    ):
        return (
            type("ProjectReviewResultStub", (), {"verdict": "attention"})(),
            type("ProjectReviewReportStub", (), {"id": uuid4()})(),
            type("ProjectReviewQualityStub", (), {"id": uuid4()})(),
        )

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(
        pipeline_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        AsyncMock(
            return_value=type(
                "NarrativeGraphResultStub",
                (),
                {"workflow_run_id": uuid4(), "plot_arc_count": 3, "clue_count": 1},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        AsyncMock(
            return_value=type(
                "NarrativeTreeResultStub",
                (),
                {"workflow_run_id": uuid4(), "node_count": 16},
            )()
        ),
    )

    settings = build_settings()
    settings.pipeline.project_consistency_block_on_failure = True
    settings.quality.draft_mode = True
    session = FakeSession()
    result = await pipeline_services.run_project_pipeline(
        session,
        settings,
        "my-story",
        requested_by="tester",
        export_markdown=False,
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]

    assert result.requires_human_review is False
    assert result.final_verdict == "draft"
    assert workflow_runs[0].status == "completed"
    assert workflow_runs[0].metadata_json["project_consistency_warn_only"] is True
    assert workflow_runs[0].metadata_json["project_consistency_scope"] == "draft_mode"


@pytest.mark.asyncio
async def test_run_project_pipeline_backfills_qimao_planning_contract_from_outline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "platform_target": "七猫小说",
    }
    chapter = build_chapter(project.id)
    child_called = False
    progress_events: list[str] = []

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter]

    async def fake_run_chapter_pipeline(*args, **kwargs):
        nonlocal child_called
        child_called = True
        return pipeline_services.ChapterPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter.id,
            chapter_number=chapter.chapter_number,
            scene_results=[],
            chapter_draft_id=uuid4(),
            chapter_draft_version_no=1,
            export_artifact_id=uuid4(),
            output_path=None,
            requires_human_review=True,
        )

    async def fake_review_project_consistency(*args, **kwargs):
        return (
            type("ProjectReviewResultStub", (), {"verdict": "attention"})(),
            type("ProjectReviewReportStub", (), {"id": uuid4()})(),
            type("ProjectReviewQualityStub", (), {"id": uuid4()})(),
        )

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(
        pipeline_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )

    session = FakeSession()
    result = await pipeline_services.run_project_pipeline(
        session,
        build_settings(),
        "my-story",
        requested_by="tester",
        export_markdown=False,
        materialize_narrative_graph=False,
        materialize_narrative_tree=False,
        progress=lambda event, payload: progress_events.append(event),
    )

    workflow_runs = [obj for obj in session.added if isinstance(obj, WorkflowRunModel)]
    assert child_called is True
    assert result.requires_human_review is True
    assert project.metadata_json["qimao_planning_gate_report"]["passed"] is True
    assert project.metadata_json["qimao_opening_contract"]["source"] == (
        "outline_backfill_qimao_planning_gate"
    )
    assert workflow_runs[0].metadata_json["qimao_planning_gate_report"]["passed"] is True
    assert "qimao_planning_gate_passed" in progress_events


def test_qimao_planning_gate_repairs_abstract_contract_from_outline() -> None:
    project = build_project()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "qimao_opening_contract": {
            "protagonist_name": "沈青崖",
            "opening_incident": "开篇以灵异案件建立视觉锚点，展示主角差异化身份。",
            "first_page_conflict": "围绕青帮的新一层压力开始成形。",
            "protagonist_immediate_goal": (
                "第一层驱动力是查明十五年前沈家灭门惨案真相，手刃仇敌。"
                "第二层驱动力是了解自己的血脉真相。"
            ),
            "visible_loss_if_fail": "追查十五年前沈家灭门惨案真相，手刃仇敌。",
            "protagonist_edge": "沈青崖能看见常人不可见的鬼魂和邪祟痕迹。",
            "edge_limit": "重瞳只能看见第一层异常，不能直接锁定幕后主使。",
            "chapter_1_small_turn": "沈青崖主动行动，完成一次局部反制或信息差建立。",
            "chapter_2_reveal": "第二章放出会改变局势判断的新信息。",
            "chapter_3_payoff": "第三章完成一个小回报并打开下一轮危险。",
            "first_10000_loop": "主角行动 -> 得到短回报 -> 引来反压 -> 新钩子",
            "forbidden_opening_modes": ["background_exposition", "normal_day"],
        },
    }
    first = build_chapter(project.id)
    first.title = "验尸房来客"
    first.chapter_goal = "沈青崖在验尸房完成尸检，遭遇第一具尸体的鬼魂现身说法。"
    first.main_conflict = "死者鬼魂声称被冤枉，真凶就在现场，但验尸房里只有活人。"
    first.hook_description = "沈青崖发现死者脖颈处有肉眼不可见的掐痕。"
    second = build_chapter(project.id)
    second.chapter_number = 2
    second.title = "李宅疑云"
    second.main_conflict = "李德盛死前三天曾请道士驱邪，道士却被官府驱逐。"
    third = build_chapter(project.id)
    third.chapter_number = 3
    third.title = "道士之死"
    third.hook_description = "道士临死前用血在河堤上留下一个字：「归」。"

    report = pipeline_services._record_qimao_planning_gate(
        project,
        chapters=[first, second, third],
    )

    assert report is not None
    assert report["passed"] is True
    repaired = project.metadata_json["qimao_opening_contract"]
    assert "验尸房来客" in repaired["first_page_conflict"]
    assert "当场" in repaired["protagonist_immediate_goal"]
    assert project.metadata_json["qimao_opening_contract_status"] == "planned_gate_passed"


def test_qimao_planning_gate_backfills_missing_contract_from_outline() -> None:
    project = build_project()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "platform_target": "七猫小说",
        "protagonist_name": "林照",
    }
    first = build_chapter(project.id)
    first.title = "账房火光"
    first.chapter_goal = "林照在账房门口保住旧账本，阻止族叔烧掉母亲旧案证据。"
    first.main_conflict = "族叔按着账童抢账本，逼林照交出钥匙，否则当场点火。"
    first.hook_description = "林照发现账页夹层里有一枚带血的私印。"
    second = build_chapter(project.id)
    second.chapter_number = 2
    second.title = "私印主人"
    second.main_conflict = "私印主人否认到过账房，却被账页墨迹反证。"
    third = build_chapter(project.id)
    third.chapter_number = 3
    third.title = "夹层证词"
    third.hook_description = "夹层证词指向一个还活着的灭口人。"

    report = pipeline_services._record_qimao_planning_gate(
        project,
        chapters=[first, second, third],
    )

    assert report is not None
    assert report["passed"] is True
    backfilled = project.metadata_json["qimao_opening_contract"]
    assert backfilled["source"] == "outline_backfill_qimao_planning_gate"
    assert "账房火光" in backfilled["opening_incident"]
    assert "林照" in backfilled["protagonist_edge"]
    assert project.metadata_json["qimao_opening_contract_status"] == "planned_gate_passed"


def test_record_commercial_planning_readiness_gate_blocks_thin_long_serial(
    tmp_path: Path,
) -> None:
    project = build_project()
    project.target_chapters = 500
    project.metadata_json = {
        **(project.metadata_json or {}),
        "qimao_opening_contract": {"opening_incident": "尸体喊冤，当场逼主角保住证据。"},
    }
    chapters: list[ChapterModel] = []
    for number in (1, 2, 3):
        chapter = build_chapter(project.id)
        chapter.chapter_number = number
        chapter.opening_situation = ""
        chapter.main_conflict = ""
        chapter.hook_description = ""
        chapter.hype_type = "reversal" if number == 1 else None
        chapter.hype_intensity = 0.1
        scene = build_scene(project.id, chapter.id)
        scene.participants = ["沈青崖"]
        scene.purpose = {"story": "独自调查推进剧情"}
        scene.hook_requirement = ""
        chapter.scenes = [scene]
        chapters.append(chapter)

    report = pipeline_services._record_commercial_planning_readiness_gate(
        project,
        chapters=chapters,
        package_root=tmp_path,
    )

    assert report is not None
    assert report["passed"] is False
    codes = {finding["code"] for finding in report["findings"]}
    assert "long_serial_artifacts_missing" not in codes
    assert "golden_three_solo_scene_chain" in codes
    assert "golden_three_hype_underpowered" in codes
    assert project.metadata_json["commercial_planning_hype_repair_count"] == 0
    assert all(chapter.hype_intensity == 0.1 for chapter in chapters)
    assert (tmp_path / "story-bible" / "series-brief.md").exists()
    assert (tmp_path / "story-bible" / "volume-plan.csv").exists()
    assert project.metadata_json["commercial_planning_readiness_status"] == "planned_gate_failed"


def test_commercial_planning_error_message_includes_llm_blocking_codes() -> None:
    message = pipeline_services._commercial_planning_readiness_error_message(
        {"passed": True, "findings": []},
        llm_judge_payload={
            "pass": False,
            "blocking_issues": [
                {
                    "code": "CONFLICT_TOO_ABSTRACT",
                    "severity": "high",
                    "evidence": "conflict has no opponent or stakes",
                }
            ],
        },
    )

    assert message.startswith(
        "Commercial planning readiness gate failed: llm:CONFLICT_TOO_ABSTRACT"
    )
    # Evidence must ride the exception text: the metadata write holding the
    # judge payload is rolled back by the raise, so this message is the only
    # surviving forensic trail (2026-07-16).
    assert "conflict has no opponent or stakes" in message


def test_commercial_planning_llm_judge_does_not_block_without_actionable_issues() -> None:
    judge_result = SimpleNamespace(passed=False, blocking_issues=())

    assert (
        pipeline_services._commercial_planning_llm_judge_should_block(judge_result)
        is False
    )


def test_commercial_planning_llm_judge_blocks_with_actionable_issues() -> None:
    judge_result = SimpleNamespace(
        passed=False,
        blocking_issues=(SimpleNamespace(code="CONFLICT_TOO_ABSTRACT"),),
    )

    assert (
        pipeline_services._commercial_planning_llm_judge_should_block(judge_result)
        is True
    )


def test_commercial_planning_actionable_retention_codes_are_hard_blockers() -> None:
    report = {
        "passed": False,
        "findings": [
            {
                "code": "GOLDEN_FINGER_NOT_VISIBLE_IN_GT3",
                "severity": "medium",
            }
        ],
    }

    assert pipeline_services._commercial_planning_has_actionable_blockers(report) is True


def test_commercial_planning_non_actionable_codes_remain_soft() -> None:
    report = {
        "passed": False,
        "findings": [
            {
                "code": "long_serial_artifacts_missing",
                "severity": "warning",
            }
        ],
    }

    assert pipeline_services._commercial_planning_has_actionable_blockers(report) is False


def test_whole_book_semantic_gate_forwards_identity_and_tone_drift_to_llm() -> None:
    project = build_project()
    project.target_chapters = 2
    project.target_word_count = 5_200
    project.metadata_json = {
        **(project.metadata_json or {}),
        "genre_intent_contract": {
            "genre_key": "xuanhuan",
            "genre_label": "玄幻",
            "channel_key": "male",
            "tone_preference": "light",
        },
        "story_spine": {"who": "陆沉，边村少年"},
        "identity_manifest": [{"name": "裴野", "role": "protagonist"}],
        "writing_profile": {"style": {"tone_keywords": ["高压", "冷硬"]}},
    }
    chapters: list[ChapterModel] = []
    for number in (1, 2):
        chapter = build_chapter(project.id)
        chapter.chapter_number = number
        chapter.title = f"门外第{number}声"
        chapter.chapter_goal = f"陆沉确认第{number}个闯入者的身份"
        chapter.opening_situation = f"门闩在第{number}声敲击后松动"
        chapter.main_conflict = f"巡查者要求陆沉在第{number}次钟响前开门"
        chapter.hook_description = f"门外留下第{number}枚带血铜钱"
        chapter.target_word_count = 2_600
        chapters.append(chapter)

    report = pipeline_services._record_outline_semantic_gate(project, chapters)
    codes = {item["code"] for item in report["findings"]}
    candidate_codes = {
        item["code"] for item in report["llm_adjudication_candidates"]
    }

    # Identity/tone matching is contextual. Deterministic detectors preserve
    # evidence but cannot veto the outline before the commercial LLM judge sees
    # the real story context.
    assert report["raw_promotion_allowed"] is False
    assert report["promotion_allowed"] is True
    assert "OUTLINE_IDENTITY_MISMATCH" in codes
    assert "OUTLINE_TONE_MISMATCH" in codes
    assert "OUTLINE_IDENTITY_MISMATCH" in candidate_codes
    assert "OUTLINE_TONE_MISMATCH" in candidate_codes
    assert project.status != "needs_replan"
    assert "generation_resume_blocked_until_repair_audit" not in project.metadata_json


def test_rolling_semantic_gate_persists_replan_for_noncontiguous_prefix() -> None:
    project = build_project()
    project.target_chapters = 8
    project.target_word_count = 20_800
    project.metadata_json = {
        **(project.metadata_json or {}),
        "rolling_outline_plan": {"window_start": 1, "window_end": 8},
    }
    chapters: list[ChapterModel] = []
    for number in (1, 3):
        chapter = build_chapter(project.id)
        chapter.chapter_number = number
        chapter.target_word_count = 2_600
        chapters.append(chapter)

    report = pipeline_services._record_outline_semantic_gate(
        project, chapters, build_settings()
    )

    assert report["promotion_allowed"] is False
    assert report["findings"][0]["code"] == "OUTLINE_SEMANTIC_INPUT_INVALID"
    assert project.status == "needs_replan"
    assert project.metadata_json["outline_semantic_gate_status"] == "needs_replan"


def test_explicit_replan_invalidates_stale_rolling_snapshot_authority() -> None:
    metadata = {
        "macro_outline_plan": {"macro_plan_hash": "old-macro"},
        "rolling_outline_plan": {
            "plan_hash": "old-plan",
            "source_snapshot_hash": "old-snapshot",
            "window_start": 1,
            "window_end": 9,
        },
        "rolling_outline_windows": [{"window_start": 1, "window_end": 9}],
        "rolling_outline_windows_hash": "old-schedule",
        "planning_status": "replanning",
    }

    repaired = pipeline_services._reset_stale_rolling_outline_for_explicit_replan(
        metadata,
        reason="rolling outline source snapshot hash mismatch",
    )

    assert "macro_outline_plan" not in repaired
    assert "rolling_outline_plan" not in repaired
    assert repaired["planning_status"] == "replanning"
    assert repaired["rolling_outline_replan_reset_reason"] == (
        "rolling outline source snapshot hash mismatch"
    )
    assert repaired["rolling_outline_replan_superseded"] == {
        "plan_hash": "old-plan",
        "source_snapshot_hash": "old-snapshot",
        "window_start": 1,
        "window_end": 9,
    }


@pytest.mark.asyncio
async def test_rolling_outline_missing_fails_closed_and_persists_replan() -> None:
    project = build_project()
    settings = build_settings()
    settings.pipeline.enable_rolling_outline = True
    settings.pipeline.rolling_outline_block_when_missing = True

    with pytest.raises(pipeline_services.ProjectRepairPauseError, match="plans are missing"):
        await pipeline_services._select_rolling_outline_window(
            FakeSession(), settings, project, [build_chapter(project.id)]
        )

    assert project.status == "needs_replan"
    assert project.metadata_json["rolling_outline_status"] == "needs_replan"
    assert project.metadata_json["production_pause_reason"] == "rolling_outline_missing"


@pytest.mark.asyncio
async def test_rolling_outline_tamper_fails_closed_and_persists_replan() -> None:
    from bestseller.services.book_design import ensure_project_book_design_snapshot
    from bestseller.services.rolling_outline import (
        build_macro_plan,
        build_rolling_outline_plan,
        promote_rolling_outline,
    )

    project = build_project()
    settings = build_settings()
    settings.pipeline.enable_rolling_outline = True
    snapshot = ensure_project_book_design_snapshot(project)
    macro = build_macro_plan(
        {"chapter_number": number, "anchor": f"anchor-{number}"}
        for number in range(1, 9)
    )
    plan = promote_rolling_outline(
        build_rolling_outline_plan(
            macro,
            current_state_snapshot={"current_chapter": 0, "facts": []},
            next_macro_anchor="book_complete",
            source_snapshot_hash=snapshot.source_hash,
            window_size=8,
        ),
        "approved",
    )
    raw_plan = plan.to_dict()
    raw_plan["detail_slots"][0]["anchor"] = "tampered"
    project.metadata_json = {
        **project.metadata_json,
        "macro_outline_plan": macro.to_dict(),
        "rolling_outline_plan": raw_plan,
    }

    with pytest.raises(pipeline_services.ProjectRepairPauseError, match="integrity check"):
        await pipeline_services._select_rolling_outline_window(
            FakeSession(), settings, project, [build_chapter(project.id)]
        )

    assert project.status == "needs_replan"
    assert project.metadata_json["production_pause_reason"] == "rolling_outline_invalid"


@pytest.mark.asyncio
async def test_rolling_outline_advances_to_only_the_next_bounded_window() -> None:
    from bestseller.services.book_design import ensure_project_book_design_snapshot
    from bestseller.services.rolling_outline import (
        build_macro_plan,
        build_rolling_outline_plan,
        load_rolling_outline_plan,
        promote_rolling_outline,
        rolling_window_schedule_hash,
    )

    project = build_project()
    project.target_chapters = 16
    project.target_word_count = 41_600
    project.current_chapter_number = 8
    settings = build_settings()
    settings.pipeline.enable_rolling_outline = True
    snapshot = ensure_project_book_design_snapshot(project)
    macro = build_macro_plan(
        {"chapter_number": number, "anchor": f"anchor-{number}"}
        for number in range(1, 17)
    )
    initial = promote_rolling_outline(
        build_rolling_outline_plan(
            macro,
            current_state_snapshot={"current_chapter": 0, "facts": []},
            next_macro_anchor=macro.slots[8].to_dict(),
            source_snapshot_hash=snapshot.source_hash,
            window_size=8,
        ),
        "approved",
    )
    schedule = [
        {"window_start": 1, "window_end": 8},
        {"window_start": 9, "window_end": 16},
    ]
    project.metadata_json = {
        **project.metadata_json,
        "macro_outline_plan": macro.to_dict(),
        "rolling_outline_plan": initial.to_dict(),
        "rolling_outline_windows": schedule,
        "rolling_outline_windows_hash": rolling_window_schedule_hash(schedule),
    }
    chapters: list[ChapterModel] = []
    for number in range(1, 17):
        chapter = build_chapter(project.id)
        chapter.chapter_number = number
        chapters.append(chapter)

    selected = await pipeline_services._select_rolling_outline_window(
        FakeSession(), settings, project, chapters
    )

    assert [chapter.chapter_number for chapter in selected] == list(range(9, 17))
    _, persisted = load_rolling_outline_plan(
        project.metadata_json["macro_outline_plan"],
        project.metadata_json["rolling_outline_plan"],
        source_snapshot_hash=snapshot.source_hash,
    )
    assert (persisted.window_start, persisted.window_end) == (9, 16)


@pytest.mark.asyncio
async def test_rolling_outline_refuses_partial_active_window() -> None:
    from bestseller.services.book_design import ensure_project_book_design_snapshot
    from bestseller.services.rolling_outline import (
        build_macro_plan,
        build_rolling_outline_plan,
        promote_rolling_outline,
    )

    project = build_project()
    project.target_chapters = 8
    project.target_word_count = 20_800
    settings = build_settings()
    settings.pipeline.enable_rolling_outline = True
    snapshot = ensure_project_book_design_snapshot(project)
    macro = build_macro_plan(
        {"chapter_number": number, "anchor": f"anchor-{number}"}
        for number in range(1, 9)
    )
    active = promote_rolling_outline(
        build_rolling_outline_plan(
            macro,
            current_state_snapshot={"current_chapter": 0, "facts": []},
            next_macro_anchor="book_complete",
            source_snapshot_hash=snapshot.source_hash,
            window_size=8,
        ),
        "approved",
    )
    project.metadata_json = {
        **project.metadata_json,
        "macro_outline_plan": macro.to_dict(),
        "rolling_outline_plan": active.to_dict(),
    }
    chapters: list[ChapterModel] = []
    for number in range(1, 8):
        chapter = build_chapter(project.id)
        chapter.chapter_number = number
        chapters.append(chapter)

    with pytest.raises(pipeline_services.ProjectRepairPauseError, match="not fully materialized"):
        await pipeline_services._select_rolling_outline_window(
            FakeSession(), settings, project, chapters
        )

    assert project.status == "needs_replan"
    assert project.metadata_json["rolling_window_missing_chapters"] == [8]


@pytest.mark.asyncio
async def test_invalid_book_design_snapshot_persists_replan_before_pause() -> None:
    project = build_project()
    project.metadata_json = {
        **project.metadata_json,
        "book_design_snapshot": {"snapshot_id": "broken"},
    }

    with pytest.raises(pipeline_services.ProjectRepairPauseError, match="snapshot"):
        await pipeline_services._enforce_book_design_consistency(FakeSession(), project)

    assert project.status == "needs_replan"
    assert project.metadata_json["book_design_consistency_status"] == "needs_replan"
    assert project.metadata_json["production_pause_reason"] == "book_design_snapshot_invalid"


def test_record_commercial_planning_readiness_reports_weak_hype_without_mutation(
    tmp_path: Path,
) -> None:
    project = build_project()
    project.target_chapters = 500
    project.metadata_json = {
        **(project.metadata_json or {}),
        "qimao_opening_contract": {"opening_incident": "尸体喊冤，当场逼主角保住证据。"},
    }
    chapters: list[ChapterModel] = []
    for number in (1, 2, 3):
        chapter = build_chapter(project.id)
        chapter.chapter_number = number
        chapter.opening_situation = "尸体刚喊冤，官府就要当场结案并封锁验尸房。"
        chapter.main_conflict = "沈青崖必须在官府夺走证据前证明尸体被灭口，否则唯一线索会被烧掉。"
        chapter.hook_description = "章尾留下谁在尸体掌心写下归字的悬念。"
        chapter.hype_type = "reveal" if number == 1 else None
        chapter.hype_intensity = 8.0 if number == 1 else 0.1
        scene = build_scene(project.id, chapter.id)
        scene.scene_type = "confrontation"
        scene.participants = ["沈青崖", "周捕头"]
        scene.purpose = {"story": "周捕头逼他交出证据，沈青崖当场反制。"}
        scene.entry_state = {"evidence": "尸体喊冤"}
        scene.exit_state = {"evidence": "保住第一条线索"}
        scene.hook_requirement = "尸体掌心露出归字，指向下一章追查。"
        chapter.scenes = [scene]
        chapters.append(chapter)

    report = pipeline_services._record_commercial_planning_readiness_gate(
        project,
        chapters=chapters,
        package_root=tmp_path,
    )

    assert report is not None
    assert report["passed"] is False
    assert project.metadata_json["commercial_planning_hype_repair_count"] == 0
    assert [chapter.hype_intensity for chapter in chapters] == [8.0, 0.1, 0.1]
    assert "commercial_planning_hype_repair" not in chapters[1].metadata_json
    assert project.metadata_json["commercial_planning_readiness_status"] == (
        "planned_gate_failed"
    )


def test_record_commercial_planning_readiness_reports_missing_visible_loss_without_mutation(
    tmp_path: Path,
) -> None:
    project = build_project()
    project.target_chapters = 500
    project.metadata_json = {
        **(project.metadata_json or {}),
        "qimao_opening_contract": {"opening_incident": "主角当场被迫保住证据。"},
    }
    chapters: list[ChapterModel] = []
    for number in (1, 2, 3):
        chapter = build_chapter(project.id)
        chapter.chapter_number = number
        chapter.chapter_goal = "主角主动选择承受异变代价，证明自身判断。"
        chapter.opening_situation = "官府封锁验尸房，周捕头要求沈青崖交出尸检记录。"
        chapter.main_conflict = "周捕头逼沈青崖交出证据。"
        chapter.hook_description = "章尾留下谁在尸体掌心写下归字的悬念。"
        chapter.hype_type = "reversal"
        chapter.hype_intensity = 8.0
        scene = build_scene(project.id, chapter.id)
        scene.scene_type = "confrontation"
        scene.participants = ["沈青崖", "周捕头"]
        scene.purpose = {"story": "周捕头逼他交出证据，沈青崖当场反制。"}
        scene.hook_requirement = "尸体掌心露出归字，指向下一章追查。"
        chapter.scenes = [scene]
        chapters.append(chapter)

    report = pipeline_services._record_commercial_planning_readiness_gate(
        project,
        chapters=chapters,
        package_root=tmp_path,
    )

    assert report is not None
    assert report["passed"] is False
    assert project.metadata_json["commercial_planning_visible_loss_repair_count"] == 0
    assert all(chapter.main_conflict == "周捕头逼沈青崖交出证据。" for chapter in chapters)
    assert "commercial_planning_visible_loss_repair" not in chapters[0].metadata_json


@pytest.mark.asyncio
async def test_run_project_pipeline_creates_opening_quality_rewrite_task_for_general_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "editor_rejection_reasons": "开篇切入点比较普通，缺乏足够吸引力。",
        "opening_quality_contract": {
            "platform_target": "商业网文签约口径",
            "protagonist_name": "沈姝",
            "opening_incident": "沈姝推门进账房时，族叔正按着账童抢账本，威胁不交就烧掉母亲旧案证据。",
            "first_page_conflict": "前600字内被逼交出账本，否则旧案证据被毁。",
            "protagonist_immediate_goal": "先保住账本并确认谁在灭口。",
            "visible_loss_if_fail": "失败会失去唯一翻案证据。",
            "protagonist_edge": "主角能从账目细节看出隐藏漏洞。",
            "edge_limit": "账本只能救第一轮，不能直接推翻主谋。",
            "chapter_1_small_turn": "主角当众反制逼迫者。",
            "chapter_2_reveal": "逼迫者背后另有主谋。",
            "chapter_3_payoff": "沈姝拿到账房暗格里的第一份签押证据，确认灭口者与族叔相连。",
            "first_10000_loop": "触发冲突 -> 主角行动 -> 收益/代价 -> 新钩子",
            "forbidden_opening_modes": ["background_exposition", "normal_day", "scenery_first"],
        },
    }
    chapter = build_chapter(project.id)
    draft_id = uuid4()
    chapter_draft = ChapterDraftVersionModel(
        chapter_id=chapter.id,
        version_no=1,
        content_md=(
            "天玄大陆有三千年历史，家族制度复杂，世界观设定分为内城与外城。"
            "多年以前，沈姝所在的沈家曾经掌握账房权力，家族由来可以追溯到前朝。"
            "她站在窗前看天气，街道很安静。"
        ),
        word_count=120,
        is_current=True,
    )
    chapter_draft.id = draft_id
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=1,
        scene_results=[],
        chapter_draft_id=draft_id,
        chapter_draft_version_no=1,
        export_artifact_id=None,
        output_path=None,
        requires_human_review=False,
    )

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter]

    async def fake_run_chapter_pipeline(*args, **kwargs):
        return chapter_result

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)

    session = FakeSession(get_map={(ChapterDraftVersionModel, draft_id): chapter_draft})
    # Legacy hard-block mode keeps the gate raising (soft-continue is now default).
    gate_settings = build_settings()
    gate_settings.pipeline.qimao_opening_gate_block_on_failure = True
    with pytest.raises(ValueError, match="Qimao opening gate failed"):
        await pipeline_services.run_project_pipeline(
            session,
            gate_settings,
            "my-story",
            requested_by="tester",
            export_markdown=False,
            materialize_narrative_graph=False,
            materialize_narrative_tree=False,
        )

    rewrite_tasks = [obj for obj in session.added if isinstance(obj, RewriteTaskModel)]
    assert len(rewrite_tasks) == 1
    assert rewrite_tasks[0].trigger_type == "qimao_opening_gate"
    assert project.metadata_json.get("qimao_opening_gate_blocked") is True
    assert rewrite_tasks[0].rewrite_strategy == "qimao_opening_incident_rewrite"
    assert "这不是润色任务" in rewrite_tasks[0].instructions
    assert project.metadata_json["opening_quality_gate_blocked"] is True
    assert project.metadata_json["opening_quality_gate_report"]["passed"] is False
    assert project.metadata_json["qimao_opening_gate_blocked"] is True
    assert project.metadata_json["qimao_opening_gate_report"]["passed"] is False


@pytest.mark.asyncio
async def test_run_project_pipeline_pauses_after_qimao_opening_attempts_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "opening_quality_contract": {
            "platform_target": "商业网文签约口径",
            "protagonist_name": "沈姝",
            "opening_incident": "沈姝推门进账房时，族叔正按着账童抢账本。",
            "first_page_conflict": "前600字内被逼交出账本。",
            "protagonist_immediate_goal": "保住账本。",
            "visible_loss_if_fail": "失败会失去唯一翻案证据。",
            "protagonist_edge": "她能从账目细节看出隐藏漏洞。",
            "edge_limit": "账本不能直接推翻主谋。",
            "chapter_1_small_turn": "主角当众反制逼迫者。",
            "chapter_2_reveal": "逼迫者背后另有主谋。",
            "chapter_3_payoff": "拿到第一份签押证据。",
            "first_10000_loop": "触发冲突 -> 行动 -> 代价 -> 新钩子",
            "forbidden_opening_modes": ["background_exposition", "normal_day"],
        },
    }
    chapter = build_chapter(project.id)
    draft_id = uuid4()
    chapter_draft = ChapterDraftVersionModel(
        chapter_id=chapter.id,
        version_no=1,
        content_md=(
            "天玄大陆有三千年历史，家族制度复杂。沈姝站在窗前看天气。"
            "街道很安静，没有冲突，也没有人逼她立刻行动。"
        ),
        word_count=80,
        is_current=True,
    )
    chapter_draft.id = draft_id
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=1,
        scene_results=[],
        chapter_draft_id=draft_id,
        chapter_draft_version_no=1,
        export_artifact_id=None,
        output_path=None,
        requires_human_review=False,
    )

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter]

    async def fake_run_chapter_pipeline(*args, **kwargs):
        return chapter_result

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)

    settings = build_settings()
    settings.pipeline.qimao_opening_max_attempts = 1
    # Legacy hard-pause mode keeps the exhausted gate raising (soft is now default).
    settings.pipeline.qimao_opening_gate_block_on_failure = True
    session = FakeSession(get_map={(ChapterDraftVersionModel, draft_id): chapter_draft})

    with pytest.raises(ValueError, match="Qimao opening gate failed"):
        await pipeline_services.run_project_pipeline(
            session,
            settings,
            "my-story",
            requested_by="tester",
            export_markdown=False,
            materialize_narrative_graph=False,
            materialize_narrative_tree=False,
        )

    rewrite_tasks = [obj for obj in session.added if isinstance(obj, RewriteTaskModel)]
    assert rewrite_tasks == []
    assert project.status == "paused"
    assert chapter.status == "revision"
    assert chapter.production_state == "needs_human_review"
    assert project.metadata_json["qimao_opening_gate_exhausted"] is True
    assert project.metadata_json["qimao_opening_gate_attempts_by_chapter"]["1"] == 1


@pytest.mark.asyncio
async def test_run_project_pipeline_creates_whole_book_quality_rewrite_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "volume_plan": [
            {"volume_number": 1, "arc_ranges": [[1, 4]], "chapter_count_target": 4}
        ],
        "emotion_driven_kernel": {
            "version": 1,
            "reader_emotion_promise": "读者追看沈姝如何赢下一步并承担代价。",
            "primary_reader_waiting": ["旧案何时爆开"],
            "empathy_contracts": [
                {
                    "contract_id": "pipeline-empathy",
                    "character_key": "沈姝",
                    "chapter_range": "1-4",
                    "situation": "旧案证据被夺走。",
                    "current_desire": "夺回证据。",
                    "fear_or_loss": "失败会失去救母亲的机会。",
                    "flaw_pressure": "习惯独自承担。",
                    "sensory_entry": "门外脚步声逼近。",
                    "judgment_logic": "只能在证据残缺时判断。",
                    "emotional_reaction": "恐惧后进入行动。",
                    "reasonable_action": "先保住印章。",
                    "consequence": "赢下一步但暴露身份的代价留下。",
                }
            ],
            "bomb_contracts": [],
            "antagonist_moral_contracts": [],
            "ending_texture_contract": {
                "ending_type": "HE",
                "core_wish_fulfilled": "母亲冤屈被洗清。",
                "relationship_settlement": "母女重新并肩。",
                "irreversible_cost_retained": "旧身份不能复原。",
                "theme_answer": "胜利是带着伤痕选择自由。",
                "future_open": "未来仍然打开。",
            },
            "emotion_chain": [
                {
                    "chapter_range": "1-4",
                    "target_reader_emotion": "焦虑到满足",
                    "reader_waiting_for": "旧案证据兑现。",
                    "reader_worry": "母亲藏身处暴露。",
                    "pressure_source": "港务官压迫。",
                    "payoff_or_aftereffect": "证据兑现但身份暴露。",
                    "callback": "印章",
                }
            ],
            "callback_motifs": ["印章"],
        },
    }
    chapters: list[ChapterModel] = []
    draft_by_id: dict[object, ChapterDraftVersionModel] = {}
    result_by_number: dict[int, pipeline_services.ChapterPipelineResult] = {}

    def good_chapter(number: int) -> str:
        return (
            f"沈姝在第{number}章刚进门就被新的证据逼到墙边, 对手夺走账页, 威胁她必须让步。"
            "她抓住对方话里的漏洞反制, 抢回一枚关键印章。"
            "这次小胜让她拿到筹码, 却也付出暴露身份的代价。"
            "章末, 门外突然响起新的脚步声, 真正拿走账本的人是谁?"
        )

    for number in range(1, 5):
        chapter = build_chapter(project.id)
        chapter.chapter_number = number
        chapter.title = f"第{number}章"
        chapter.id = uuid4()
        chapters.append(chapter)

        draft = ChapterDraftVersionModel(
            chapter_id=chapter.id,
            version_no=1,
            content_md=(
                good_chapter(number)
                if number < 4
                else "沈姝回到房间, 整理了一天的想法。天色渐暗, 她觉得事情还没有结束。"
            ),
            word_count=200,
            is_current=True,
        )
        draft.id = uuid4()
        draft_by_id[(ChapterDraftVersionModel, draft.id)] = draft
        result_by_number[number] = pipeline_services.ChapterPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter.id,
            chapter_number=number,
            scene_results=[],
            chapter_draft_id=draft.id,
            chapter_draft_version_no=1,
            export_artifact_id=None,
            output_path=None,
            requires_human_review=False,
        )

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return chapters

    async def fake_run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        return result_by_number[chapter_number]

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)

    progress_events: list[str] = []
    session = FakeSession(get_map=draft_by_id)
    # Legacy hard-block mode keeps the gate raising (soft-continue is now default).
    gate_settings = build_settings()
    gate_settings.pipeline.whole_book_quality_gate_block_on_failure = True
    with pytest.raises(ValueError, match="Whole-book quality gate failed"):
        await pipeline_services.run_project_pipeline(
            session,
            gate_settings,
            "my-story",
            requested_by="tester",
            export_markdown=False,
            materialize_narrative_graph=False,
            materialize_narrative_tree=False,
            progress=lambda event, payload: progress_events.append(event),
        )

    rewrite_tasks = [obj for obj in session.added if isinstance(obj, RewriteTaskModel)]
    assert len(rewrite_tasks) == 1
    assert rewrite_tasks[0].trigger_type == "whole_book_quality_gate"
    assert rewrite_tasks[0].rewrite_strategy == "chapter_function_rewrite"
    assert "全书质量门禁重写任务" in rewrite_tasks[0].instructions
    assert project.metadata_json["whole_book_quality_gate_blocked"] is True
    assert project.metadata_json["whole_book_quality_report"]["passed"] is False
    assert (
        project.metadata_json["whole_book_quality_report"]["metrics"]["emotion_driven"][
            "available"
        ]
        is True
    )
    assert len(project.metadata_json["whole_book_engagement_ledger"]) == 4
    assert "whole_book_quality_gate_failed" in progress_events


@pytest.mark.asyncio
async def test_whole_book_quality_gate_soft_continue_does_not_raise() -> None:
    """Default soft-continue: a failed whole-book quality gate queues a rewrite
    task and flags the chapter, but does NOT raise (the book keeps writing)."""
    project = build_project()
    project.metadata_json = {**(project.metadata_json or {}), "volume_plan": [], "emotion_driven_kernel": {}}
    chapter = build_chapter(project.id)
    chapter.chapter_number = 1
    draft_id = uuid4()
    draft = ChapterDraftVersionModel(
        chapter_id=chapter.id,
        version_no=1,
        content_md="沈姝回到房间, 整理了一天的想法。天色渐暗, 一切平静, 没有冲突也没有新钩子。",
        word_count=60,
        is_current=True,
    )
    draft.id = draft_id
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=1,
        scene_results=[],
        chapter_draft_id=draft_id,
        chapter_draft_version_no=1,
        export_artifact_id=None,
        output_path=None,
        requires_human_review=False,
    )
    workflow_run = WorkflowRunModel(project_id=project.id, workflow_type="project", status="running")
    workflow_run.id = uuid4()
    workflow_run.metadata_json = {}
    session = FakeSession(get_map={(ChapterDraftVersionModel, draft_id): draft})
    settings = build_settings()  # default: whole_book_quality_gate_block_on_failure=False

    # Must NOT raise even though the gate fails — soft-continue is the default.
    await pipeline_services._enforce_whole_book_quality_gate_after_chapter(
        session,
        project=project,
        chapter=chapter,
        chapter_result=chapter_result,
        chapter_texts={},
        workflow_run=workflow_run,
        progress=None,
        settings=settings,
    )

    rewrite_tasks = [obj for obj in session.added if isinstance(obj, RewriteTaskModel)]
    assert len(rewrite_tasks) == 1
    assert rewrite_tasks[0].trigger_type == "whole_book_quality_gate"


def test_clear_gate_state_removes_stale_terminal_flags_only() -> None:
    cleaned = pipeline_services._clear_gate_state(
        {
            "whole_book_quality_gate_blocked": True,
            "whole_book_quality_gate_block_codes": ["chapter_function_missing"],
            "whole_book_quality_report": {"passed": True},
        },
        "whole_book_quality_gate_blocked",
        "whole_book_quality_gate_block_codes",
    )

    assert "whole_book_quality_gate_blocked" not in cleaned
    assert "whole_book_quality_gate_block_codes" not in cleaned
    assert cleaned["whole_book_quality_report"] == {"passed": True}


@pytest.mark.asyncio
async def test_whole_book_quality_pass_clears_stale_failure_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "whole_book_quality_gate_blocked": True,
        "whole_book_quality_gate_block_codes": ["chapter_function_missing"],
        "whole_book_quality_gate_codes": ["chapter_function_missing"],
    }
    chapter = build_chapter(project.id)
    draft_id = uuid4()
    draft = ChapterDraftVersionModel(
        chapter_id=chapter.id,
        version_no=1,
        content_md="沈姝夺回账页，却也暴露了藏身处。门外随即响起第二拨脚步声。",
        word_count=30,
        is_current=True,
    )
    draft.id = draft_id
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=chapter.chapter_number,
        scene_results=[],
        chapter_draft_id=draft_id,
        chapter_draft_version_no=1,
        export_artifact_id=None,
        output_path=None,
        requires_human_review=False,
    )
    workflow_run = WorkflowRunModel(
        project_id=project.id,
        workflow_type="project",
        status="running",
        metadata_json={"whole_book_quality_gate_blocked": True},
    )
    workflow_run.id = uuid4()
    session = FakeSession(get_map={(ChapterDraftVersionModel, draft_id): draft})
    monkeypatch.setattr(
        pipeline_services,
        "evaluate_whole_book_quality",
        lambda *args, **kwargs: SimpleNamespace(passed=True),
    )
    monkeypatch.setattr(
        pipeline_services,
        "whole_book_quality_report_to_dict",
        lambda report: {"passed": True, "findings": [], "ledger": []},
    )

    await pipeline_services._enforce_whole_book_quality_gate_after_chapter(
        session,
        project=project,
        chapter=chapter,
        chapter_result=chapter_result,
        chapter_texts={},
        workflow_run=workflow_run,
        progress=None,
        settings=build_settings(),
    )

    assert "whole_book_quality_gate_blocked" not in project.metadata_json
    assert "whole_book_quality_gate_block_codes" not in project.metadata_json
    assert "whole_book_quality_gate_blocked" not in workflow_run.metadata_json
    assert len(session.executed) == 1


@pytest.mark.asyncio
async def test_qimao_opening_gate_soft_continue_does_not_raise() -> None:
    """Default soft-continue: a failed Qimao opening gate queues a rewrite task
    and flags the chapter, but does NOT raise the whole book down."""
    project = build_project()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "opening_quality_contract": {
            "platform_target": "商业网文签约口径",
            "protagonist_name": "沈姝",
            "opening_incident": "沈姝推门进账房时，族叔正按着账童抢账本。",
            "first_page_conflict": "前600字内被逼交出账本。",
            "protagonist_immediate_goal": "保住账本。",
            "visible_loss_if_fail": "失败会失去唯一翻案证据。",
            "protagonist_edge": "她能从账目细节看出隐藏漏洞。",
            "edge_limit": "账本不能直接推翻主谋。",
            "chapter_1_small_turn": "主角当众反制逼迫者。",
            "chapter_2_reveal": "逼迫者背后另有主谋。",
            "chapter_3_payoff": "拿到第一份签押证据。",
            "first_10000_loop": "触发冲突 -> 行动 -> 代价 -> 新钩子",
            "forbidden_opening_modes": ["background_exposition", "normal_day"],
        },
    }
    chapter = build_chapter(project.id)
    chapter.chapter_number = 1
    draft_id = uuid4()
    draft = ChapterDraftVersionModel(
        chapter_id=chapter.id,
        version_no=1,
        content_md="天玄大陆有三千年历史。沈姝站在窗前看天气, 街道安静, 没有冲突, 没有人逼她行动。",
        word_count=70,
        is_current=True,
    )
    draft.id = draft_id
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=1,
        scene_results=[],
        chapter_draft_id=draft_id,
        chapter_draft_version_no=1,
        export_artifact_id=None,
        output_path=None,
        requires_human_review=False,
    )
    workflow_run = WorkflowRunModel(project_id=project.id, workflow_type="project", status="running")
    workflow_run.id = uuid4()
    workflow_run.metadata_json = {}
    session = FakeSession(get_map={(ChapterDraftVersionModel, draft_id): draft})
    settings = build_settings()  # default soft; max_attempts default so this is a queue-rewrite (non-exhausted) pass

    # Must NOT raise — soft-continue queues the rewrite task and returns.
    await pipeline_services._enforce_qimao_opening_gate_after_chapter(
        session,
        project=project,
        chapter=chapter,
        chapter_result=chapter_result,
        opening_texts={},
        workflow_run=workflow_run,
        settings=settings,
        progress=None,
    )

    rewrite_tasks = [obj for obj in session.added if isinstance(obj, RewriteTaskModel)]
    assert len(rewrite_tasks) == 1
    assert rewrite_tasks[0].trigger_type == "qimao_opening_gate"


@pytest.mark.asyncio
async def test_disabled_opening_gate_clears_stale_failure_state() -> None:
    project = build_project()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "opening_quality_gate_disabled": True,
        "opening_quality_gate_blocked": True,
        "qimao_opening_gate_blocked": True,
        "qimao_opening_gate_exhausted": True,
    }
    chapter = build_chapter(project.id)
    workflow_run = WorkflowRunModel(
        project_id=project.id,
        workflow_type="project",
        status="running",
        metadata_json={"qimao_opening_gate_blocked": True},
    )
    workflow_run.id = uuid4()
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=workflow_run.id,
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=chapter.chapter_number,
        scene_results=[],
        chapter_draft_id=None,
        chapter_draft_version_no=None,
        export_artifact_id=None,
        output_path=None,
        requires_human_review=False,
    )

    await pipeline_services._enforce_qimao_opening_gate_after_chapter(
        FakeSession(),
        project=project,
        chapter=chapter,
        chapter_result=chapter_result,
        opening_texts={},
        workflow_run=workflow_run,
        settings=build_settings(),
        progress=None,
    )

    assert "opening_quality_gate_blocked" not in project.metadata_json
    assert "qimao_opening_gate_blocked" not in project.metadata_json
    assert "qimao_opening_gate_exhausted" not in project.metadata_json
    assert "qimao_opening_gate_blocked" not in workflow_run.metadata_json


@pytest.mark.asyncio
async def test_run_project_pipeline_emits_chapter_progress_with_title_and_word_counts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    chapter = build_chapter(project.id)
    chapter.target_word_count = 5000
    chapter.title = "暗潮入局"
    materialization_result = type(
        "MaterializationResultStub",
        (),
        {"workflow_run_id": uuid4()},
    )()
    chapter_result = pipeline_services.ChapterPipelineResult(
        workflow_run_id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        chapter_number=1,
        scene_results=[],
        chapter_draft_id=uuid4(),
        chapter_draft_version_no=1,
        export_artifact_id=uuid4(),
        output_path=str(tmp_path / "output" / "chapter-001.md"),
        requires_human_review=False,
    )

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter]

    async def fake_materialize_latest(session, project_slug: str, **kwargs):
        return materialization_result

    async def fake_run_chapter_pipeline(
        session,
        settings,
        project_slug,
        chapter_number,
        **kwargs,
    ):
        chapter.current_word_count = 4986
        chapter.title = "暗潮入局"
        return chapter_result

    async def fake_export_project_markdown(session, settings, project_slug: str, **kwargs):
        output_path = tmp_path / "output" / "project.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# My Story", encoding="utf-8")
        export_artifact = ExportArtifactModel(
            project_id=project.id,
            export_type="markdown",
            source_scope="project",
            source_id=project.id,
            storage_uri=str(output_path),
            checksum="b" * 64,
            version_label="project-current",
        )
        export_artifact.id = uuid4()
        return export_artifact, output_path

    async def fake_review_project_consistency(
        session,
        settings,
        project_slug: str,
        **kwargs,
    ):
        return (
            type("ProjectReviewResultStub", (), {"verdict": "pass"})(),
            type("ProjectReviewReportStub", (), {"id": uuid4()})(),
            type("ProjectReviewQualityStub", (), {"id": uuid4()})(),
        )

    async def fake_get_latest_planning_artifact(session, project_id, artifact_type):
        return object()

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_chapter_outline_batch",
        fake_materialize_latest,
    )
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(pipeline_services, "export_project_markdown", fake_export_project_markdown)
    monkeypatch.setattr(
        pipeline_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        AsyncMock(
            return_value=type(
                "NarrativeGraphResultStub",
                (),
                {"workflow_run_id": uuid4(), "plot_arc_count": 3, "clue_count": 1},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        AsyncMock(
            return_value=type(
                "NarrativeTreeResultStub",
                (),
                {"workflow_run_id": uuid4(), "node_count": 16},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "get_latest_planning_artifact",
        fake_get_latest_planning_artifact,
    )

    progress_events: list[tuple[str, dict[str, object] | None]] = []

    def progress(stage: str, payload: dict[str, object] | None = None) -> None:
        progress_events.append((stage, payload))

    session = FakeSession()
    await pipeline_services.run_project_pipeline(
        session,
        build_settings(),
        "my-story",
        requested_by="tester",
        materialize_outline=True,
        export_markdown=True,
        progress=progress,
    )

    started = [payload for stage, payload in progress_events if stage == "chapter_pipeline_started"]
    completed = [payload for stage, payload in progress_events if stage == "chapter_pipeline_completed"]

    assert started == [
        {
            "project_slug": "my-story",
            "chapter_number": 1,
            "progress": "1/1",
            "global_progress": "1/1",
            "target_word_count": 5000,
        }
    ]
    assert completed == [
        {
            "project_slug": "my-story",
            "chapter_number": 1,
            "progress": "1/1",
            "global_progress": "1/1",
            "workflow_run_id": str(chapter_result.workflow_run_id),
            "requires_human_review": False,
            "chapter_draft_version_no": 1,
            "chapter_title": "暗潮入局",
            "word_count": 4986,
            "target_word_count": 5000,
        }
    ]


@pytest.mark.asyncio
async def test_run_project_pipeline_filters_requested_chapter_numbers_and_checkpoints_before_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    # Pre-seed invariants so L1 _ensure_project_invariants is a no-op — this
    # test focuses on chapter-dispatch ordering, not invariant seeding (which
    # has its own dedicated tests and its own commit of the seeded payload).
    project.invariants_json = {
        "project_id": str(project.id),
        "language": "zh-CN",
        "pov": "close_third",
        "tense": "past",
        "length_envelope": {
            "min_chars": 5000,
            "target_chars": 6400,
            "max_chars": 7500,
        },
    }
    chapter_1 = build_chapter(project.id)
    chapter_1.status = "complete"
    chapter_2 = build_chapter(project.id)
    chapter_2.id = uuid4()
    chapter_2.chapter_number = 2
    chapter_2.title = "第二章"
    chapter_3 = build_chapter(project.id)
    chapter_3.id = uuid4()
    chapter_3.chapter_number = 3
    chapter_3.title = "第三章"

    sequence: list[str] = []
    processed: list[int] = []

    async def fake_get_project_by_slug(session, slug: str) -> ProjectModel:
        return project

    async def fake_load_project_chapters(session, project_id):
        return [chapter_1, chapter_2, chapter_3]

    async def fake_checkpoint_commit(session) -> None:
        sequence.append("commit")

    async def fake_run_chapter_pipeline(session, settings, project_slug, chapter_number, **kwargs):
        sequence.append(f"chapter:{chapter_number}")
        processed.append(chapter_number)
        return pipeline_services.ChapterPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            chapter_id=chapter_2.id,
            chapter_number=chapter_number,
            scene_results=[],
            chapter_draft_id=uuid4(),
            chapter_draft_version_no=1,
            requires_human_review=False,
        )

    async def fake_review_project_consistency(session, settings, project_slug: str, **kwargs):
        return (
            type("ProjectReviewResultStub", (), {"verdict": "pass"})(),
            type("ProjectReviewReportStub", (), {"id": uuid4()})(),
            type("ProjectReviewQualityStub", (), {"id": uuid4()})(),
        )

    async def fake_sync_world_expansion_progress(session, *, project):
        return None

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "_load_project_chapters", fake_load_project_chapters)
    monkeypatch.setattr(pipeline_services, "_checkpoint_commit", fake_checkpoint_commit)
    monkeypatch.setattr(pipeline_services, "run_chapter_pipeline", fake_run_chapter_pipeline)
    monkeypatch.setattr(
        pipeline_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )
    monkeypatch.setattr(
        pipeline_services,
        "sync_world_expansion_progress",
        fake_sync_world_expansion_progress,
    )

    session = FakeSession(
        # The pipeline issues several ``session.execute`` calls before reaching
        # ``_load_prior_incomplete_chapter_numbers`` (invariants checks, identity
        # manifest, etc). Pre-load enough empty ``FakeExecuteRows`` so the
        # prior-incomplete-chapters SELECT finds a real ``.all()`` and the
        # pipeline can proceed to the chapter run.
        execute_results=[FakeExecuteRows([]) for _ in range(16)],
    )
    result = await pipeline_services.run_project_pipeline(
        session,
        build_settings(),
        "my-story",
        requested_by="tester",
        materialize_narrative_graph=False,
        materialize_narrative_tree=False,
        export_markdown=False,
        chapter_numbers={2},
    )

    assert processed == [2]
    assert sequence[0] == "commit"
    assert sequence[1] == "chapter:2"
    assert [item.chapter_number for item in result.chapter_results] == [2]


@pytest.mark.asyncio
async def test_run_autowrite_pipeline_runs_auto_repair_and_reports_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = build_project()
    settings = build_settings()
    settings.output.base_dir = str(tmp_path / "output")
    exported_project = tmp_path / "output" / project.slug / "project.md"
    exported_project.parent.mkdir(parents=True, exist_ok=True)
    exported_project.write_text("# My Story", encoding="utf-8")

    async def fake_get_project_by_slug(session, slug: str):
        return None

    async def fake_create_project(session, payload, settings):
        return project

    planning_kwargs: dict[str, object] = {}

    async def fake_generate_novel_plan(session, settings, project_slug: str, premise: str, **kwargs):
        planning_kwargs.update(kwargs)
        return type(
            "PlanningResultStub",
            (),
            {
                "workflow_run_id": uuid4(),
                "volume_count": 1,
                "chapter_count": 1,
            },
        )()

    async def fake_materialize_story_bible(session, project_slug: str, **kwargs):
        return type("StoryBibleResultStub", (), {"workflow_run_id": uuid4()})()

    async def fake_materialize_outline(session, project_slug: str, **kwargs):
        return type("OutlineResultStub", (), {"workflow_run_id": uuid4()})()

    async def fake_run_project_pipeline(session, settings, project_slug: str, **kwargs):
        return ProjectPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            project_slug=project_slug,
            chapter_results=[],
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            final_verdict="attention",
            export_artifact_id=None,
            output_path=None,
            requires_human_review=True,
        )

    async def fake_run_project_repair(session, settings, project_slug: str, **kwargs):
        return ProjectRepairResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            project_slug=project_slug,
            pending_rewrite_task_count=2,
            superseded_task_count=2,
            processed_chapters=[],
            review_report_id=uuid4(),
            quality_score_id=uuid4(),
            final_verdict="pass",
            export_artifact_id=uuid4(),
            output_path=str(exported_project),
            remaining_pending_rewrite_count=0,
            requires_human_review=False,
        )

    progress_events: list[str] = []

    def fake_progress(stage: str, payload: dict[str, object] | None = None) -> None:
        progress_events.append(stage)

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(pipeline_services, "create_project", fake_create_project)
    monkeypatch.setattr(pipeline_services, "generate_novel_plan", fake_generate_novel_plan)
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_story_bible",
        fake_materialize_story_bible,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_chapter_outline_batch",
        fake_materialize_outline,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        AsyncMock(
            return_value=type(
                "NarrativeGraphResultStub",
                (),
                {"workflow_run_id": uuid4(), "plot_arc_count": 3, "clue_count": 1},
            )()
        ),
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        AsyncMock(
            return_value=type(
                "NarrativeTreeResultStub",
                (),
                {"workflow_run_id": uuid4(), "node_count": 16},
            )()
        ),
    )
    monkeypatch.setattr(pipeline_services, "run_project_pipeline", fake_run_project_pipeline)
    monkeypatch.setattr(
        "bestseller.services.repair.run_project_repair",
        fake_run_project_repair,
    )

    session = FakeSession()
    result = await pipeline_services.run_autowrite_pipeline(
        session,
        settings,
        project_payload=pipeline_services.ProjectCreate(
            slug=project.slug,
            title=project.title,
            genre=project.genre,
            target_word_count=project.target_word_count,
            target_chapters=project.target_chapters,
        ),
        premise="导航员揭穿帝国谎言。",
        progress=fake_progress,
    )

    assert result.repair_attempted is True
    assert result.repair_workflow_run_id is not None
    assert result.export_status == "exported"
    assert result.output_path == str(exported_project)
    assert result.output_files == [str(exported_project.resolve())]
    assert "auto_repair_started" in progress_events
    assert "auto_repair_completed" in progress_events
    assert progress_events[-1] == "autowrite_completed"
    assert planning_kwargs.get("progress") is fake_progress


def test_should_use_progressive_pipeline_routes_large_target_chapters() -> None:
    """target_chapters > threshold picks the progressive path even when the
    setting is off — this is the bug that stalled large books during self-heal
    (web used the threshold, worker used the setting, default False)."""
    settings = build_settings()
    settings.pipeline.progressive_planning = False

    small = pipeline_services.ProjectCreate(
        slug="small", title="small", genre="fantasy",
        target_word_count=30000, target_chapters=30,
    )
    assert pipeline_services._should_use_progressive_pipeline(settings, small) is False

    at_threshold = pipeline_services.ProjectCreate(
        slug="edge", title="edge", genre="fantasy",
        target_word_count=30000,
        target_chapters=pipeline_services.PROGRESSIVE_CHAPTER_THRESHOLD,
    )
    assert pipeline_services._should_use_progressive_pipeline(settings, at_threshold) is True

    large = pipeline_services.ProjectCreate(
        slug="large", title="large", genre="fantasy",
        target_word_count=2000000,
        target_chapters=pipeline_services.PROGRESSIVE_CHAPTER_THRESHOLD + 1,
    )
    assert pipeline_services._should_use_progressive_pipeline(settings, large) is True


def test_should_use_progressive_pipeline_respects_explicit_setting() -> None:
    """Explicit progressive_planning=True wins over a small target."""
    settings = build_settings()
    settings.pipeline.progressive_planning = True

    small = pipeline_services.ProjectCreate(
        slug="small", title="small", genre="fantasy",
        target_word_count=10000, target_chapters=10,
    )
    assert pipeline_services._should_use_progressive_pipeline(settings, small) is True


def test_rolling_outline_routes_books_larger_than_one_window_to_progressive() -> None:
    settings = build_settings()
    settings.pipeline.progressive_planning = False
    settings.pipeline.enable_rolling_outline = True
    settings.pipeline.rolling_outline_window_size = 8
    payload = pipeline_services.ProjectCreate(
        slug="rolling",
        title="rolling",
        genre="fantasy",
        target_word_count=30_000,
        target_chapters=12,
    )

    assert pipeline_services._should_use_progressive_pipeline(settings, payload) is True


def test_volume_plan_expands_into_bounded_jit_windows_without_changing_volume_identity() -> None:
    windows = pipeline_services._expand_volume_plan_into_rolling_windows(
        [
            {
                "volume_number": 1,
                "volume_title": "禁区初响",
                "chapter_count_target": 18,
                "volume_goal": "裴野确认禁区呼唤的来源",
            },
            {
                "volume_number": 2,
                "volume_title": "旧名回潮",
                "chapter_count_target": 7,
                "volume_goal": "裴野追查祖父旧名",
            },
        ],
        window_size=8,
    )

    assert [item["chapter_count_target"] for item in windows] == [9, 9, 7]
    assert [item["chapter_range"] for item in windows] == [
        [1, 9],
        [10, 18],
        [19, 25],
    ]
    assert [item["volume_number"] for item in windows] == [1, 1, 2]
    assert windows[1]["narrative_volume_chapter_count"] == 18

    macro_slots = pipeline_services._build_progressive_macro_slots(
        [
            {"volume_number": 1, "chapter_count_target": 18, "volume_goal": "查明禁区"},
            {"volume_number": 2, "chapter_count_target": 7, "volume_goal": "追查旧名"},
        ]
    )
    assert len(macro_slots) == 25
    assert macro_slots[18]["chapter_number"] == 19
    assert "追查旧名" in macro_slots[18]["anchor"]


@pytest.mark.asyncio
async def test_cross_volume_schedule_advances_without_skipping_boundary_chapters() -> None:
    from bestseller.services.book_design import ensure_project_book_design_snapshot
    from bestseller.services.rolling_outline import (
        build_macro_plan,
        build_rolling_outline_plan,
        promote_rolling_outline,
        rolling_window_schedule_hash,
    )

    project = build_project()
    project.target_chapters = 20
    project.target_word_count = 52_000
    project.current_chapter_number = 10
    settings = build_settings()
    settings.pipeline.enable_rolling_outline = True
    snapshot = ensure_project_book_design_snapshot(project)
    macro = build_macro_plan(
        {"chapter_number": number, "anchor": f"anchor-{number}"}
        for number in range(1, 21)
    )
    initial = promote_rolling_outline(
        build_rolling_outline_plan(
            macro,
            current_state_snapshot={"current_chapter": 0, "facts": []},
            next_macro_anchor=macro.slots[10].to_dict(),
            source_snapshot_hash=snapshot.source_hash,
            window_size=10,
        ),
        "approved",
    )
    schedule = [
        {"window_start": 1, "window_end": 10, "volume_number": 1},
        {"window_start": 11, "window_end": 20, "volume_number": 2},
    ]
    project.metadata_json = {
        **project.metadata_json,
        "macro_outline_plan": macro.to_dict(),
        "rolling_outline_plan": initial.to_dict(),
        "rolling_outline_windows": schedule,
        "rolling_outline_windows_hash": rolling_window_schedule_hash(schedule),
    }
    chapters: list[ChapterModel] = []
    for number in range(1, 21):
        chapter = build_chapter(project.id)
        chapter.chapter_number = number
        chapters.append(chapter)

    selected = await pipeline_services._select_rolling_outline_window(
        FakeSession(), settings, project, chapters
    )

    assert [chapter.chapter_number for chapter in selected] == list(range(11, 21))
    assert project.metadata_json["rolling_outline_plan"]["window_start"] == 11
    assert project.metadata_json["rolling_outline_plan"]["window_end"] == 20


def test_should_use_progressive_pipeline_handles_missing_target() -> None:
    """A missing target_chapters attribute must not trip the progressive path
    (defensive — ProjectCreate's validator already forbids zero, but other
    payload shapes might omit the field)."""
    settings = build_settings()
    settings.pipeline.progressive_planning = False

    class _PayloadWithoutTarget:
        slug = "x"

    assert pipeline_services._should_use_progressive_pipeline(
        settings, _PayloadWithoutTarget()
    ) is False


@pytest.mark.asyncio
async def test_run_autowrite_pipeline_reroutes_large_target_to_progressive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When target_chapters exceeds the threshold, run_autowrite_pipeline
    must delegate to run_progressive_autowrite_pipeline even if the setting
    is off. Regression guard for the web/worker routing divergence."""
    settings = build_settings()
    settings.pipeline.progressive_planning = False

    sentinel = object()
    captured: dict[str, object] = {}

    async def fake_progressive(session, settings_arg, **kwargs):
        captured["called"] = True
        captured["target_chapters"] = kwargs["project_payload"].target_chapters
        return sentinel

    async def fake_non_progressive_guard(*args, **kwargs):
        raise AssertionError("non-progressive path should not be used for large targets")

    monkeypatch.setattr(
        pipeline_services,
        "run_progressive_autowrite_pipeline",
        fake_progressive,
    )
    monkeypatch.setattr(pipeline_services, "generate_novel_plan", fake_non_progressive_guard)

    payload = pipeline_services.ProjectCreate(
        slug="huge", title="huge", genre="fantasy",
        target_word_count=2000000,
        target_chapters=pipeline_services.PROGRESSIVE_CHAPTER_THRESHOLD + 1,
    )
    result = await pipeline_services.run_autowrite_pipeline(
        FakeSession(),
        settings,
        project_payload=payload,
        premise="premise",
    )

    assert result is sentinel
    assert captured["called"] is True
    assert captured["target_chapters"] == pipeline_services.PROGRESSIVE_CHAPTER_THRESHOLD + 1


@pytest.mark.asyncio
async def test_run_autowrite_pipeline_reroutes_partial_foundation_resume_to_progressive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short-book resume with VOLUME_PLAN but no merged outline must continue
    at volume outline generation instead of rerunning the foundation planner."""
    project = build_project()
    settings = build_settings()
    settings.pipeline.progressive_planning = False
    settings.pipeline.resume_enabled = True

    existing_volume_plan = type(
        "PlanningArtifactStub",
        (),
        {"source_run_id": uuid4(), "content": [{"volume_number": 1}]},
    )()

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_get_latest_planning_artifact(session, *, project_id, artifact_type):
        assert project_id == project.id
        if artifact_type == pipeline_services.ArtifactType.CHAPTER_OUTLINE_BATCH:
            return None
        if artifact_type == pipeline_services.ArtifactType.VOLUME_PLAN:
            return existing_volume_plan
        return None

    sentinel = object()
    captured: dict[str, object] = {}

    async def fake_progressive(session, settings_arg, **kwargs):
        captured["called"] = True
        captured["project_slug"] = kwargs["project_payload"].slug
        return sentinel

    async def fake_generate_novel_plan(*args, **kwargs):
        raise AssertionError("partial foundation resume must not rerun generate_novel_plan")

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        pipeline_services, "get_latest_planning_artifact", fake_get_latest_planning_artifact
    )
    monkeypatch.setattr(
        pipeline_services,
        "run_progressive_autowrite_pipeline",
        fake_progressive,
    )
    monkeypatch.setattr(pipeline_services, "generate_novel_plan", fake_generate_novel_plan)

    progress_events: list[str] = []

    def fake_progress(stage: str, payload: dict[str, object] | None = None) -> None:
        progress_events.append(stage)

    payload = pipeline_services.ProjectCreate(
        slug=project.slug, title=project.title, genre=project.genre,
        target_word_count=project.target_word_count, target_chapters=6,
    )
    result = await pipeline_services.run_autowrite_pipeline(
        FakeSession(),
        settings,
        project_payload=payload,
        premise="premise",
        progress=fake_progress,
    )

    assert result is sentinel
    assert captured["called"] is True
    assert captured["project_slug"] == project.slug
    assert "planning_resume_rerouted_progressive" in progress_events


@pytest.mark.asyncio
async def test_run_autowrite_pipeline_reuses_materializations_on_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    settings = build_settings()
    settings.pipeline.resume_enabled = True

    outline_artifact = type(
        "PlanningArtifactStub",
        (),
        {"source_run_id": uuid4(), "content": {"chapters": []}},
    )()
    completed_runs = {
        pipeline_services.WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE,
        pipeline_services.WORKFLOW_TYPE_MATERIALIZE_CHAPTER_OUTLINE,
        pipeline_services.WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_GRAPH,
        pipeline_services.WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_TREE,
    }
    completed_run_ids = {workflow_type: uuid4() for workflow_type in completed_runs}

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_get_latest_planning_artifact(session, *, project_id, artifact_type):
        assert project_id == project.id
        if artifact_type == pipeline_services.ArtifactType.CHAPTER_OUTLINE_BATCH:
            return outline_artifact
        return None

    async def fake_get_latest_completed_workflow_run(session, *, project_id, workflow_type):
        assert project_id == project.id
        if workflow_type not in completed_runs:
            return None
        run = WorkflowRunModel(
            project_id=project.id,
            workflow_type=workflow_type,
            status="completed",
        )
        run.id = completed_run_ids[workflow_type]
        return run

    async def fail_materializer(*args, **kwargs):
        raise AssertionError("completed materialization should be reused on resume")

    async def fake_run_project_pipeline(*args, **kwargs):
        return ProjectPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            project_slug=project.slug,
            chapter_results=[],
        )

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        pipeline_services, "get_latest_planning_artifact", fake_get_latest_planning_artifact
    )
    monkeypatch.setattr(
        pipeline_services,
        "get_latest_completed_workflow_run",
        fake_get_latest_completed_workflow_run,
    )
    monkeypatch.setattr(
        pipeline_services, "materialize_latest_story_bible", fail_materializer
    )
    monkeypatch.setattr(
        pipeline_services, "materialize_latest_chapter_outline_batch", fail_materializer
    )
    monkeypatch.setattr(
        pipeline_services, "materialize_latest_narrative_graph", fail_materializer
    )
    monkeypatch.setattr(
        pipeline_services, "materialize_latest_narrative_tree", fail_materializer
    )
    monkeypatch.setattr(
        pipeline_services, "run_project_pipeline", fake_run_project_pipeline
    )

    progress_events: list[str] = []

    def fake_progress(stage: str, payload: dict[str, object] | None = None) -> None:
        progress_events.append(stage)

    payload = pipeline_services.ProjectCreate(
        slug=project.slug,
        title=project.title,
        genre=project.genre,
        target_word_count=project.target_word_count,
        target_chapters=project.target_chapters,
    )

    result = await pipeline_services.run_autowrite_pipeline(
        FakeSession(),
        settings,
        project_payload=payload,
        premise="premise",
        progress=fake_progress,
    )

    assert result.project_workflow_run_id
    assert "planning_skipped_resume" in progress_events
    assert "story_bible_materialization_skipped_resume" in progress_events
    assert "outline_materialization_skipped_resume" in progress_events
    assert "narrative_graph_materialization_skipped_resume" in progress_events
    assert "narrative_tree_materialization_skipped_resume" in progress_events
    assert "outline_materialization_started" not in progress_events


@pytest.mark.asyncio
async def test_run_autowrite_pipeline_skips_project_repair_for_scene_machine_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    settings = build_settings()
    settings.pipeline.resume_enabled = True
    # Legacy hard-pause mode is what suppresses auto-repair on scene-machine-block.
    settings.pipeline.whole_book_pause_on_scene_review = True

    outline_artifact = type(
        "PlanningArtifactStub",
        (),
        {"source_run_id": uuid4(), "content": {"chapters": []}},
    )()
    completed_runs = {
        pipeline_services.WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE,
        pipeline_services.WORKFLOW_TYPE_MATERIALIZE_CHAPTER_OUTLINE,
        pipeline_services.WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_GRAPH,
        pipeline_services.WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_TREE,
    }

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_get_latest_planning_artifact(session, *, project_id, artifact_type):
        if artifact_type == pipeline_services.ArtifactType.CHAPTER_OUTLINE_BATCH:
            return outline_artifact
        return None

    async def fake_get_latest_completed_workflow_run(session, *, project_id, workflow_type):
        if workflow_type not in completed_runs:
            return None
        run = WorkflowRunModel(
            project_id=project.id,
            workflow_type=workflow_type,
            status="completed",
        )
        run.id = uuid4()
        return run

    async def fake_run_project_pipeline(*args, **kwargs):
        return ProjectPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            project_slug=project.slug,
            chapter_results=[],
            final_verdict="attention",
            requires_human_review=True,
        )

    async def fake_has_scene_machine_blocked(session, project_id):
        assert project_id == project.id
        return True

    async def fail_project_repair(*args, **kwargs):
        raise AssertionError("scene-machine-blocked chapters must not enter project repair")

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        pipeline_services, "get_latest_planning_artifact", fake_get_latest_planning_artifact
    )
    monkeypatch.setattr(
        pipeline_services,
        "get_latest_completed_workflow_run",
        fake_get_latest_completed_workflow_run,
    )
    monkeypatch.setattr(
        pipeline_services, "run_project_pipeline", fake_run_project_pipeline
    )
    monkeypatch.setattr(
        pipeline_services,
        "_project_has_scene_machine_blocked_chapter",
        fake_has_scene_machine_blocked,
    )
    monkeypatch.setattr(
        "bestseller.services.repair.run_project_repair",
        fail_project_repair,
    )

    progress_events: list[str] = []

    def fake_progress(stage: str, payload: dict[str, object] | None = None) -> None:
        progress_events.append(stage)

    payload = pipeline_services.ProjectCreate(
        slug=project.slug,
        title=project.title,
        genre=project.genre,
        target_word_count=project.target_word_count,
        target_chapters=project.target_chapters,
    )

    result = await pipeline_services.run_autowrite_pipeline(
        FakeSession(),
        settings,
        project_payload=payload,
        premise="premise",
        progress=fake_progress,
    )

    assert result.repair_attempted is False
    assert result.requires_human_review is True
    assert "auto_repair_skipped_scene_machine_blocked" in progress_events
    assert "auto_repair_started" not in progress_events


@pytest.mark.asyncio
async def test_run_autowrite_pipeline_runs_project_repair_in_soft_continue_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default soft-continue: a scene-machine-blocked chapter no longer suppresses
    the project-repair pass — the framework's own remediation engages on the
    "attention" verdict instead of leaving it a dead end."""
    project = build_project()
    settings = build_settings()
    settings.pipeline.resume_enabled = True
    # Default mode (whole_book_pause_on_scene_review=False) — leave it off.

    outline_artifact = type(
        "PlanningArtifactStub",
        (),
        {"source_run_id": uuid4(), "content": {"chapters": []}},
    )()
    completed_runs = {
        pipeline_services.WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE,
        pipeline_services.WORKFLOW_TYPE_MATERIALIZE_CHAPTER_OUTLINE,
        pipeline_services.WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_GRAPH,
        pipeline_services.WORKFLOW_TYPE_MATERIALIZE_NARRATIVE_TREE,
    }

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_get_latest_planning_artifact(session, *, project_id, artifact_type):
        if artifact_type == pipeline_services.ArtifactType.CHAPTER_OUTLINE_BATCH:
            return outline_artifact
        return None

    async def fake_get_latest_completed_workflow_run(session, *, project_id, workflow_type):
        if workflow_type not in completed_runs:
            return None
        run = WorkflowRunModel(
            project_id=project.id,
            workflow_type=workflow_type,
            status="completed",
        )
        run.id = uuid4()
        return run

    async def fake_run_project_pipeline(*args, **kwargs):
        return ProjectPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            project_slug=project.slug,
            chapter_results=[],
            final_verdict="attention",
            requires_human_review=True,
        )

    async def fake_has_scene_machine_blocked(session, project_id):
        return True

    repair_calls: list[str] = []

    async def fake_project_repair(*args, **kwargs):
        repair_calls.append("called")
        return type(
            "RepairResultStub",
            (),
            {
                "workflow_run_id": uuid4(),
                "review_report_id": uuid4(),
                "quality_score_id": uuid4(),
                "export_artifact_id": None,
                "output_path": None,
                "final_verdict": "attention",
                "requires_human_review": True,
            },
        )()

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        pipeline_services, "get_latest_planning_artifact", fake_get_latest_planning_artifact
    )
    monkeypatch.setattr(
        pipeline_services,
        "get_latest_completed_workflow_run",
        fake_get_latest_completed_workflow_run,
    )
    monkeypatch.setattr(pipeline_services, "run_project_pipeline", fake_run_project_pipeline)
    monkeypatch.setattr(
        pipeline_services,
        "_project_has_scene_machine_blocked_chapter",
        fake_has_scene_machine_blocked,
    )
    monkeypatch.setattr(
        "bestseller.services.repair.run_project_repair",
        fake_project_repair,
    )

    progress_events: list[str] = []

    def fake_progress(stage: str, payload: dict[str, object] | None = None) -> None:
        progress_events.append(stage)

    payload = pipeline_services.ProjectCreate(
        slug=project.slug,
        title=project.title,
        genre=project.genre,
        target_word_count=project.target_word_count,
        target_chapters=project.target_chapters,
    )

    result = await pipeline_services.run_autowrite_pipeline(
        FakeSession(),
        settings,
        project_payload=payload,
        premise="premise",
        progress=fake_progress,
    )

    assert repair_calls == ["called"]
    assert result.repair_attempted is True
    assert "auto_repair_started" in progress_events
    assert "auto_repair_skipped_scene_machine_blocked" not in progress_events


@pytest.mark.asyncio
async def test_progressive_autowrite_skips_bible_materialization_on_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a project already has a completed `materialize_story_bible` workflow
    run AND resume is enabled, the resume path must NOT re-run materialization.

    Regression guard for a stall observed on `exorcist-detective-1778051012`
    (chapter 9 of "青囊不语问阴阳"): the worker self-heal kept entering the
    progressive pipeline, hit `materialize_latest_story_bible`, the L2 bible
    completeness gate raised on stricter rules added after foundation, and the
    job retried forever — chapter 9 never resumed.
    """
    project = build_project()
    settings = build_settings()
    settings.pipeline.resume_enabled = True

    completed_bible_run = WorkflowRunModel(
        project_id=project.id,
        workflow_type=pipeline_services.WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE,
        status="completed",
    )
    completed_bible_run.id = uuid4()

    existing_volume_plan = type(
        "PlanningArtifactStub",
        (),
        {"source_run_id": uuid4(), "content": []},
    )()

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_get_latest_planning_artifact(session, *, project_id, artifact_type):
        if artifact_type == pipeline_services.ArtifactType.VOLUME_PLAN:
            return existing_volume_plan
        return None

    async def fake_get_latest_completed_workflow_run(session, *, project_id, workflow_type):
        assert project_id == project.id
        assert workflow_type == pipeline_services.WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE
        return completed_bible_run

    async def fake_materialize_latest_story_bible(*args, **kwargs):
        raise AssertionError(
            "materialize_latest_story_bible must be skipped on resume when a "
            "completed run already exists"
        )

    async def fake_checkpoint_commit(session) -> None:
        return None

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        pipeline_services, "get_latest_planning_artifact", fake_get_latest_planning_artifact
    )
    monkeypatch.setattr(
        pipeline_services,
        "get_latest_completed_workflow_run",
        fake_get_latest_completed_workflow_run,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_story_bible",
        fake_materialize_latest_story_bible,
    )
    monkeypatch.setattr(pipeline_services, "_checkpoint_commit", fake_checkpoint_commit)

    progress_events: list[str] = []

    def fake_progress(stage: str, payload: dict[str, object] | None = None) -> None:
        progress_events.append(stage)

    payload = pipeline_services.ProjectCreate(
        slug=project.slug, title=project.title, genre=project.genre,
        target_word_count=project.target_word_count, target_chapters=project.target_chapters,
    )

    # Empty volume plan exits the per-volume loop after the bible-resume decision,
    # so the pipeline returns cleanly. Any call to the bible materializer would
    # have raised AssertionError above.
    await pipeline_services.run_progressive_autowrite_pipeline(
        FakeSession(), settings,
        project_payload=payload, premise="...", progress=fake_progress,
    )

    assert "foundation_planning_skipped_resume" in progress_events
    assert "story_bible_materialization_skipped_resume" in progress_events
    assert "story_bible_materialization_started" not in progress_events


@pytest.mark.asyncio
async def test_progressive_autowrite_refreshes_truth_when_resuming_cached_outline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    settings = build_settings()
    settings.pipeline.resume_enabled = True
    settings.pipeline.require_foundation_identity_lock = False

    completed_bible_run = WorkflowRunModel(
        project_id=project.id,
        workflow_type=pipeline_services.WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE,
        status="completed",
    )
    completed_bible_run.id = uuid4()

    volume_plan_artifact = type(
        "PlanningArtifactStub",
        (),
        {
            "source_run_id": uuid4(),
            "content": [
                {
                    "volume_number": 2,
                    "title": "Volume 2",
                    "chapter_count_target": 2,
                }
            ],
        },
    )()

    refresh_calls: list[str] = []
    call_order: list[str] = []

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_get_latest_planning_artifact(session, *, project_id, artifact_type):
        if artifact_type == pipeline_services.ArtifactType.VOLUME_PLAN:
            return volume_plan_artifact
        return type("ArtifactStub", (), {"content": {}})()

    async def fake_get_latest_completed_workflow_run(session, *, project_id, workflow_type):
        if workflow_type == pipeline_services.WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE:
            return completed_bible_run
        return None

    async def fake_volume_fully_written(session, project_id, volume_number):
        return False, 0, 0

    async def fake_resume_outline_chapters_for_volume(
        session,
        *,
        project_id,
        volume_number,
        expected_count,
    ):
        assert volume_number == 2
        assert expected_count == 2
        return [
            {"chapter_number": 51, "volume_number": 2},
            {"chapter_number": 52, "volume_number": 2},
        ]

    async def fake_generate_volume_plan(*args, **kwargs):
        raise AssertionError("cached outline resume must not regenerate the volume plan")

    async def fake_materialize_latest_chapter_outline_batch(*args, **kwargs):
        return type("Result", (), {"workflow_run_id": uuid4(), "project_id": project.id})()

    async def fake_materialize_latest_narrative_graph(*args, **kwargs):
        return type("Result", (), {"workflow_run_id": uuid4(), "project_id": project.id})()

    async def fake_materialize_latest_narrative_tree(*args, **kwargs):
        return type("Result", (), {"workflow_run_id": uuid4(), "project_id": project.id})()

    async def fake_refresh_truth(session, settings, project_arg, *, requested_by, progress=None):
        refresh_calls.append(project_arg.slug)
        call_order.append("refresh_truth")
        return True

    async def fake_run_project_pipeline(*args, **kwargs):
        call_order.append("run_project_pipeline")
        assert call_order == ["refresh_truth", "run_project_pipeline"]
        return ProjectPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            project_slug=project.slug,
            chapter_results=[],
        )

    async def fake_checkpoint_commit(session) -> None:
        return None

    async def fake_collect_volume_writing_feedback(session, project_id, volume_number):
        return {"character_states": [], "arc_summary": {"unresolved_threads": []}}

    def fake_summarize_volume_feedback(feedback, *, language: str):
        return ""

    import bestseller.services.planning_context as planning_context

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        pipeline_services, "get_latest_planning_artifact", fake_get_latest_planning_artifact
    )
    monkeypatch.setattr(
        pipeline_services,
        "get_latest_completed_workflow_run",
        fake_get_latest_completed_workflow_run,
    )
    monkeypatch.setattr(pipeline_services, "_volume_fully_written", fake_volume_fully_written)
    monkeypatch.setattr(
        pipeline_services,
        "_resume_outline_chapters_for_volume",
        fake_resume_outline_chapters_for_volume,
    )
    monkeypatch.setattr(pipeline_services, "generate_volume_plan", fake_generate_volume_plan)
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_chapter_outline_batch",
        fake_materialize_latest_chapter_outline_batch,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        fake_materialize_latest_narrative_graph,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        fake_materialize_latest_narrative_tree,
    )
    monkeypatch.setattr(
        pipeline_services,
        "_refresh_stale_truth_materializations_for_resume",
        fake_refresh_truth,
    )
    monkeypatch.setattr(pipeline_services, "run_project_pipeline", fake_run_project_pipeline)
    monkeypatch.setattr(pipeline_services, "_checkpoint_commit", fake_checkpoint_commit)
    monkeypatch.setattr(
        planning_context,
        "collect_volume_writing_feedback",
        fake_collect_volume_writing_feedback,
    )
    monkeypatch.setattr(
        planning_context,
        "summarize_volume_feedback",
        fake_summarize_volume_feedback,
    )

    payload = pipeline_services.ProjectCreate(
        slug=project.slug,
        title=project.title,
        genre=project.genre,
        target_word_count=project.target_word_count,
        target_chapters=project.target_chapters,
    )

    await pipeline_services.run_progressive_autowrite_pipeline(
        FakeSession(),
        settings,
        project_payload=payload,
        premise="...",
        export_markdown=False,
        auto_repair_on_attention=False,
    )

    assert refresh_calls == [project.slug]


@pytest.mark.asyncio
async def test_progressive_autowrite_stops_later_volume_planning_when_volume_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    settings = build_settings()
    settings.pipeline.resume_enabled = True
    settings.pipeline.require_foundation_identity_lock = False
    settings.pipeline.progressive_continue_after_volume_block = False

    completed_bible_run = WorkflowRunModel(
        project_id=project.id,
        workflow_type=pipeline_services.WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE,
        status="completed",
    )
    completed_bible_run.id = uuid4()

    volume_plan_artifact = type(
        "PlanningArtifactStub",
        (),
        {
            "source_run_id": uuid4(),
            "content": [
                {"volume_number": 1, "title": "Volume 1", "chapter_count_target": 2},
                {"volume_number": 2, "title": "Volume 2", "chapter_count_target": 2},
            ],
        },
    )()
    checked_volumes: list[int] = []
    run_volumes: list[int] = []
    progress_events: list[str] = []

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_get_latest_planning_artifact(session, *, project_id, artifact_type):
        if artifact_type == pipeline_services.ArtifactType.VOLUME_PLAN:
            return volume_plan_artifact
        return type("ArtifactStub", (), {"content": {}})()

    async def fake_get_latest_completed_workflow_run(session, *, project_id, workflow_type):
        if workflow_type == pipeline_services.WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE:
            return completed_bible_run
        return None

    async def fake_volume_fully_written(session, project_id, volume_number):
        checked_volumes.append(volume_number)
        return False, 0, 2

    async def fake_chapter_numbers_in_volume(session, project_id, volume_number):
        assert volume_number == 1
        return {1, 2}

    async def fake_refresh_truth(*args, **kwargs):
        return False

    async def fake_run_project_pipeline(*args, **kwargs):
        run_volumes.append(kwargs["current_volume_number"])
        return ProjectPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            project_slug=project.slug,
            chapter_results=[
                pipeline_services.ProjectPipelineChapterSummary(
                    chapter_number=1,
                    workflow_run_id=uuid4(),
                    chapter_draft_version_no=1,
                    requires_human_review=True,
                )
            ],
            final_verdict="attention",
            requires_human_review=True,
        )

    async def fake_collect_volume_writing_feedback(*args, **kwargs):
        raise AssertionError("blocked volume must not collect feedback or advance")

    async def fake_checkpoint_commit(session) -> None:
        return None

    def fake_progress(stage: str, payload: dict[str, object] | None = None) -> None:
        progress_events.append(stage)

    import bestseller.services.planning_context as planning_context

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        pipeline_services, "get_latest_planning_artifact", fake_get_latest_planning_artifact
    )
    monkeypatch.setattr(
        pipeline_services,
        "get_latest_completed_workflow_run",
        fake_get_latest_completed_workflow_run,
    )
    monkeypatch.setattr(pipeline_services, "_volume_fully_written", fake_volume_fully_written)
    monkeypatch.setattr(
        pipeline_services,
        "_chapter_numbers_in_volume",
        fake_chapter_numbers_in_volume,
    )
    monkeypatch.setattr(
        pipeline_services,
        "_refresh_stale_truth_materializations_for_resume",
        fake_refresh_truth,
    )
    monkeypatch.setattr(pipeline_services, "run_project_pipeline", fake_run_project_pipeline)
    monkeypatch.setattr(pipeline_services, "_checkpoint_commit", fake_checkpoint_commit)
    monkeypatch.setattr(
        planning_context,
        "collect_volume_writing_feedback",
        fake_collect_volume_writing_feedback,
    )

    payload = pipeline_services.ProjectCreate(
        slug=project.slug,
        title=project.title,
        genre=project.genre,
        target_word_count=project.target_word_count,
        target_chapters=project.target_chapters,
    )

    result = await pipeline_services.run_progressive_autowrite_pipeline(
        FakeSession(),
        settings,
        project_payload=payload,
        premise="...",
        export_markdown=False,
        auto_repair_on_attention=False,
        progress=fake_progress,
    )

    assert result.requires_human_review is True
    assert checked_volumes == [1, 1]
    assert run_volumes == [1]
    assert "volume_writing_machine_repair_required" in progress_events


@pytest.mark.asyncio
async def test_progressive_autowrite_stops_when_current_volume_remains_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    settings = build_settings()
    settings.pipeline.resume_enabled = True
    settings.pipeline.require_foundation_identity_lock = False

    completed_bible_run = WorkflowRunModel(
        project_id=project.id,
        workflow_type=pipeline_services.WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE,
        status="completed",
    )
    completed_bible_run.id = uuid4()

    volume_plan_artifact = type(
        "PlanningArtifactStub",
        (),
        {
            "source_run_id": uuid4(),
            "content": [
                {"volume_number": 1, "title": "Volume 1", "chapter_count_target": 2},
                {"volume_number": 2, "title": "Volume 2", "chapter_count_target": 2},
            ],
        },
    )()
    run_volumes: list[int] = []
    progress_events: list[str] = []
    repair_kwargs: dict[str, object] = {}

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_get_latest_planning_artifact(session, *, project_id, artifact_type):
        if artifact_type == pipeline_services.ArtifactType.VOLUME_PLAN:
            return volume_plan_artifact
        return type("ArtifactStub", (), {"content": {}})()

    async def fake_get_latest_completed_workflow_run(session, *, project_id, workflow_type):
        if workflow_type == pipeline_services.WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE:
            return completed_bible_run
        return None

    async def fake_volume_fully_written(session, project_id, volume_number):
        assert volume_number == 1
        return False, 1, 2

    async def fake_chapter_numbers_in_volume(session, project_id, volume_number):
        assert volume_number == 1
        return {1, 2}

    async def fake_refresh_truth(*args, **kwargs):
        return False

    async def fake_run_project_pipeline(*args, **kwargs):
        run_volumes.append(kwargs["current_volume_number"])
        return ProjectPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            project_slug=project.slug,
            chapter_results=[
                pipeline_services.ProjectPipelineChapterSummary(
                    chapter_number=1,
                    workflow_run_id=uuid4(),
                    chapter_draft_version_no=1,
                    requires_human_review=False,
                )
            ],
            final_verdict="pass",
            requires_human_review=False,
        )

    async def fake_run_project_repair(*args, **kwargs):
        repair_kwargs.update(kwargs)
        return ProjectRepairResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            project_slug=project.slug,
            pending_rewrite_task_count=0,
            superseded_task_count=0,
            processed_chapters=[],
            review_report_id=None,
            quality_score_id=None,
            final_verdict="attention",
            export_artifact_id=None,
            output_path=None,
            remaining_pending_rewrite_count=0,
            requires_human_review=True,
        )

    async def fake_collect_volume_writing_feedback(*args, **kwargs):
        raise AssertionError("incomplete volume must not collect feedback or advance")

    async def fake_checkpoint_commit(session) -> None:
        return None

    def fake_progress(stage: str, payload: dict[str, object] | None = None) -> None:
        progress_events.append(stage)

    import bestseller.services.planning_context as planning_context

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        pipeline_services, "get_latest_planning_artifact", fake_get_latest_planning_artifact
    )
    monkeypatch.setattr(
        pipeline_services,
        "get_latest_completed_workflow_run",
        fake_get_latest_completed_workflow_run,
    )
    monkeypatch.setattr(pipeline_services, "_volume_fully_written", fake_volume_fully_written)
    monkeypatch.setattr(
        pipeline_services,
        "_chapter_numbers_in_volume",
        fake_chapter_numbers_in_volume,
    )
    monkeypatch.setattr(
        pipeline_services,
        "_refresh_stale_truth_materializations_for_resume",
        fake_refresh_truth,
    )
    monkeypatch.setattr(pipeline_services, "run_project_pipeline", fake_run_project_pipeline)
    monkeypatch.setattr(pipeline_services, "_checkpoint_commit", fake_checkpoint_commit)
    monkeypatch.setattr(
        "bestseller.services.repair.run_project_repair",
        fake_run_project_repair,
    )
    monkeypatch.setattr(
        planning_context,
        "collect_volume_writing_feedback",
        fake_collect_volume_writing_feedback,
    )

    payload = pipeline_services.ProjectCreate(
        slug=project.slug,
        title=project.title,
        genre=project.genre,
        target_word_count=project.target_word_count,
        target_chapters=project.target_chapters,
    )

    result = await pipeline_services.run_progressive_autowrite_pipeline(
        FakeSession(),
        settings,
        project_payload=payload,
        premise="...",
        export_markdown=False,
        auto_repair_on_attention=True,
        progress=fake_progress,
    )

    assert result.requires_human_review is True
    assert result.final_verdict == "attention"
    assert run_volumes == [1]
    assert repair_kwargs["target_chapter_numbers"] == {1, 2}
    assert "volume_writing_incomplete_current_volume" in progress_events


@pytest.mark.asyncio
async def test_progressive_autowrite_can_continue_after_volume_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    settings = build_settings()
    settings.pipeline.resume_enabled = True
    settings.pipeline.require_foundation_identity_lock = False
    settings.pipeline.progressive_continue_after_volume_block = True

    completed_bible_run = WorkflowRunModel(
        project_id=project.id,
        workflow_type=pipeline_services.WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE,
        status="completed",
    )
    completed_bible_run.id = uuid4()

    volume_plan_artifact = type(
        "PlanningArtifactStub",
        (),
        {
            "source_run_id": uuid4(),
            "content": [
                {"volume_number": 1, "title": "Volume 1", "chapter_count_target": 2},
                {"volume_number": 2, "title": "Volume 2", "chapter_count_target": 2},
            ],
        },
    )()
    run_volumes: list[int] = []
    feedback_volumes: list[int] = []
    progress_events: list[str] = []
    volume_written_checks: dict[int, int] = {}

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_get_latest_planning_artifact(session, *, project_id, artifact_type):
        if artifact_type == pipeline_services.ArtifactType.VOLUME_PLAN:
            return volume_plan_artifact
        return type("ArtifactStub", (), {"content": {}})()

    async def fake_get_latest_completed_workflow_run(session, *, project_id, workflow_type):
        if workflow_type == pipeline_services.WORKFLOW_TYPE_MATERIALIZE_STORY_BIBLE:
            return completed_bible_run
        return None

    async def fake_volume_fully_written(session, project_id, volume_number):
        volume_written_checks[volume_number] = volume_written_checks.get(volume_number, 0) + 1
        if volume_number == 1:
            if volume_written_checks[volume_number] == 1:
                return False, 1, 2
            return True, 2, 2
        if volume_number == 2:
            if volume_written_checks[volume_number] == 1:
                return False, 0, 2
            return True, 2, 2
        return False, 0, 0

    async def fake_chapter_numbers_in_volume(session, project_id, volume_number):
        return {1, 2} if volume_number == 1 else set()

    async def fake_resume_outline_chapters_for_volume(
        session,
        *,
        project_id,
        volume_number,
        expected_count,
    ):
        if volume_number == 2:
            return [{"chapter_number": 3}, {"chapter_number": 4}]
        return []

    async def fake_refresh_truth(*args, **kwargs):
        return False

    async def fake_run_project_pipeline(*args, **kwargs):
        volume_number = kwargs["current_volume_number"]
        run_volumes.append(volume_number)
        blocked = volume_number == 1
        return ProjectPipelineResult(
            workflow_run_id=uuid4(),
            project_id=project.id,
            project_slug=project.slug,
            chapter_results=[
                pipeline_services.ProjectPipelineChapterSummary(
                    chapter_number=volume_number,
                    workflow_run_id=uuid4(),
                    chapter_draft_version_no=1,
                    requires_human_review=blocked,
                )
            ],
            final_verdict="attention" if blocked else "pass",
            requires_human_review=blocked,
        )

    async def fake_collect_volume_writing_feedback(session, project_id, volume_number):
        feedback_volumes.append(volume_number)
        return {"character_states": [], "arc_summary": {"unresolved_threads": []}}

    def fake_summarize_volume_feedback(feedback, *, language: str):
        return ""

    async def fake_materialize(*args, **kwargs):
        return type("Materialized", (), {"workflow_run_id": uuid4()})()

    async def fake_checkpoint_commit(session) -> None:
        return None

    def fake_progress(stage: str, payload: dict[str, object] | None = None) -> None:
        progress_events.append(stage)

    import bestseller.services.planning_context as planning_context

    monkeypatch.setattr(pipeline_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        pipeline_services, "get_latest_planning_artifact", fake_get_latest_planning_artifact
    )
    monkeypatch.setattr(
        pipeline_services,
        "get_latest_completed_workflow_run",
        fake_get_latest_completed_workflow_run,
    )
    monkeypatch.setattr(pipeline_services, "_volume_fully_written", fake_volume_fully_written)
    monkeypatch.setattr(
        pipeline_services,
        "_chapter_numbers_in_volume",
        fake_chapter_numbers_in_volume,
    )
    monkeypatch.setattr(
        pipeline_services,
        "_resume_outline_chapters_for_volume",
        fake_resume_outline_chapters_for_volume,
    )
    monkeypatch.setattr(
        pipeline_services,
        "_refresh_stale_truth_materializations_for_resume",
        fake_refresh_truth,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_chapter_outline_batch",
        fake_materialize,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_graph",
        fake_materialize,
    )
    monkeypatch.setattr(
        pipeline_services,
        "materialize_latest_narrative_tree",
        fake_materialize,
    )
    monkeypatch.setattr(pipeline_services, "run_project_pipeline", fake_run_project_pipeline)
    monkeypatch.setattr(pipeline_services, "_checkpoint_commit", fake_checkpoint_commit)
    monkeypatch.setattr(
        planning_context,
        "collect_volume_writing_feedback",
        fake_collect_volume_writing_feedback,
    )
    monkeypatch.setattr(
        planning_context,
        "summarize_volume_feedback",
        fake_summarize_volume_feedback,
    )

    payload = pipeline_services.ProjectCreate(
        slug=project.slug,
        title=project.title,
        genre=project.genre,
        target_word_count=project.target_word_count,
        target_chapters=project.target_chapters,
    )

    result = await pipeline_services.run_progressive_autowrite_pipeline(
        FakeSession(),
        settings,
        project_payload=payload,
        premise="...",
        export_markdown=False,
        auto_repair_on_attention=False,
        progress=fake_progress,
    )

    assert result.requires_human_review is True
    assert result.final_verdict == "attention"
    assert run_volumes == [1, 2]
    assert feedback_volumes == [2]
    assert "volume_writing_machine_repair_required" in progress_events
    assert "volume_writing_repair_parallelized" in progress_events


# ---------------------------------------------------------------------------
# Genre-aware golden-three "visible loss" backfill.
# Regression for 《福星甩不掉》: the backfill hard-coded a detective stake
# ("失去关键证据，对手扩大优势") onto every book's first three chapters, which
# is retention-killing nonsense in a 治愈喜剧 with no "对手" or "证据".
# ---------------------------------------------------------------------------
def test_visible_loss_backfill_is_a_content_safe_noop() -> None:
    from bestseller.services.pipelines import _backfill_golden_three_visible_losses

    def _ch(n: int) -> ChapterModel:
        return ChapterModel(
            project_id=uuid4(),
            chapter_number=n,
            chapter_goal="",
            main_conflict="主角只想躺平摆烂",
            information_revealed={},
            information_withheld={},
            foreshadowing_actions={},
            metadata={},
        )

    low = [_ch(1)]
    repaired = _backfill_golden_three_visible_losses(low, low_pressure=True)
    assert repaired == 0
    assert "关键证据" not in low[0].main_conflict
    assert "对手" not in low[0].main_conflict
    assert low[0].main_conflict == "主角只想躺平摆烂"

    default = [_ch(1)]
    repaired = _backfill_golden_three_visible_losses(default, low_pressure=False)
    assert repaired == 0
    assert default[0].main_conflict == "主角只想躺平摆烂"


def test_approved_outline_replan_releases_prose_gate_at_version_boundary() -> None:
    project = SimpleNamespace(
        status="planning",
        metadata_json={
            "outline_replan_in_progress": True,
            "outline_replan_prior_outline_version": 19,
            "outline_semantic_gate_report": {"promotion_allowed": True},
            "planning_status": "replanning",
            "production_paused": True,
            "production_pause_reason": "outline_replan_in_progress",
            "generation_resume_blocked_until_repair_audit": True,
        },
    )

    released = pipeline_services._release_approved_outline_replan_gate(
        project,
        SimpleNamespace(version_no=20),
    )

    assert released is True
    assert project.status == "writing"
    assert project.metadata_json["planning_status"] == "writing"
    assert project.metadata_json["outline_semantic_gate_status"] == "approved"
    assert "outline_replan_in_progress" not in project.metadata_json
    assert "production_paused" not in project.metadata_json


@pytest.mark.parametrize(
    ("promotion_allowed", "version_no"),
    [(False, 20), (True, 19)],
)
def test_outline_replan_gate_stays_closed_without_both_proofs(
    promotion_allowed: bool,
    version_no: int,
) -> None:
    project = SimpleNamespace(
        status="planning",
        metadata_json={
            "outline_replan_in_progress": True,
            "outline_replan_prior_outline_version": 19,
            "outline_semantic_gate_report": {
                "promotion_allowed": promotion_allowed
            },
            "production_paused": True,
        },
    )

    released = pipeline_services._release_approved_outline_replan_gate(
        project,
        SimpleNamespace(version_no=version_no),
    )

    assert released is False
    assert project.status == "planning"
    assert project.metadata_json["outline_replan_in_progress"] is True
    assert project.metadata_json["production_paused"] is True
