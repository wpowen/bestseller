from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.prewrite_quality_profile import (
    STRICT_PREWRITE_PROFILE,
    apply_default_prewrite_quality_profile,
    evaluate_book_spec_quality,
    evaluate_concept_quality,
    evaluate_story_design_kernel_quality,
    evaluate_volume_plan_quality,
    evaluate_world_spec_source_quality,
    repair_target_for_block_code,
    strict_blocks,
    strict_outline_batch_size,
)

pytestmark = pytest.mark.unit


def test_new_project_metadata_defaults_to_commercial_strict_prewrite() -> None:
    metadata = apply_default_prewrite_quality_profile({})

    assert metadata["quality_profile"] == STRICT_PREWRITE_PROFILE
    assert metadata["methodology_contract_mode"] == "strict"
    assert metadata["commercial_strict_prewrite"] is True


def test_strict_profile_runs_thoroughly_but_does_not_force_hard_block() -> None:
    # Self-harm fix (2026-06): strict mode no longer cascade-aborts. Whether a
    # gate hard-blocks is governed by its own config flag — a warn-configured
    # gate does NOT hard-block even in strict mode (it still runs + reports +
    # repairs). But strict mode still drives thorough run-mode behaviour.
    project = SimpleNamespace(metadata_json={"quality_profile": STRICT_PREWRITE_PROFILE})
    warn_settings = SimpleNamespace(
        pipeline=SimpleNamespace(prewrite_readiness_block_on_failure=False)
    )
    assert strict_blocks(project, warn_settings, "prewrite_readiness_block_on_failure") is False
    # Run-mode stays strict-aware (thorough evaluation + repair batch sizing).
    # Default batch is 3 chapters — 5 overflowed the planner token budget and
    # churned on truncation on the real 50-chapter run.
    assert strict_outline_batch_size(project, warn_settings) == 3
    # A gate explicitly configured to block still hard-blocks.
    hard_settings = SimpleNamespace(
        pipeline=SimpleNamespace(commercial_planning_readiness_block_on_failure=True)
    )
    assert (
        strict_blocks(project, hard_settings, "commercial_planning_readiness_block_on_failure")
        is True
    )


def test_concept_gate_blocks_award_title_and_english_mechanism_leak() -> None:
    report = evaluate_concept_quality(
        title="修仙 2.0 / 现代修真升级流·构思中",
        premise="主角获得 state_loop_engine 后开始升级。",
        language="zh-CN",
    )

    codes = {finding.code for finding in report.blocking_findings}
    assert report.passed is False
    assert "title_uncommercial" in codes
    assert "english_mechanism_leak" in codes


def test_book_spec_gate_blocks_truncated_dramatic_question() -> None:
    report = evaluate_book_spec_quality(
        {
            "dramatic_question": "？",
            "reader_promise": "主角每次升级都会付出可见代价。",
            "protagonist": {"external_goal": "夺回被宗门垄断的灵网入口"},
        },
        language="zh-CN",
    )

    assert report.passed is False
    assert any(finding.code == "field_truncated" for finding in report.blocking_findings)


def test_book_spec_gate_does_not_treat_schema_keys_as_english_mechanisms() -> None:
    report = evaluate_book_spec_quality(
        {
            "_meta": {"quality_profile": "commercial_strict_prewrite"},
            "dramatic_question": "程恪能不能用验房报告逼出灵网复检？",
            "reader_promise": "读者追看主角用职业规则反制修真地产黑箱。",
            "anti_commonsense_hook": {
                "mechanism_key": "silver_lining_loser",
                "arc_engine": ["职业场景", "倒计时"],
                "hook_type": "deadline",
                "opening_frame": "countdown_threat",
                "expression_style": "cost_first",
                "one_liner": "验房报告不是文书，是能触发执法阵的证据。",
            },
            "core_loop": "验房取证、规则反制、复检升级。",
            "narrative_lines": {
                "overt_line": [{"line_role": "overt", "summary": "程恪逼出灵网复检。"}],
                "undercurrent_line": [
                    {"line_role": "undercurrent", "summary": "物业和执法队的旧账互相牵连。"}
                ],
                "hidden_line": [{"line_role": "hidden", "summary": "备案被改写另有源头。"}],
            },
            "protagonist": {"external_goal": "保住执照并救下被楼盘吸走修为的住户。"},
        },
        language="zh-CN",
    )

    assert report.passed is True
    assert not any(finding.code == "english_mechanism_leak" for finding in report.blocking_findings)


def test_book_spec_gate_does_not_flag_english_genre_taxonomy_slugs() -> None:
    # Regression (2026-06-03): genre/sub_genre/audience carry internal English
    # taxonomy slugs (suspense-mystery / rule-mystery-complete). These must not be
    # treated as reader-visible English mechanism leaks, or every autowrite run
    # with an English genre key aborts at the book_spec gate.
    report = evaluate_book_spec_quality(
        {
            "genre": "suspense-mystery",
            "sub_genre": "rule-mystery-complete",
            "audience": "web-novel",
            # The genre slug also propagates into content fields like `tone`.
            "tone": ["suspense-mystery"],
            "title": "第七次日落档案",
            "reader_promise": "读者追看修复师在三十天内查清自己的死亡记录。",
            "dramatic_question": "顾砚能否在第七次红色日落前关闭档案规则？",
            "protagonist": {"external_goal": "关闭红色日落规则并查清母亲失忆真相。"},
        },
        language="zh-CN",
    )

    assert report.passed is True
    assert not any(
        finding.code == "english_mechanism_leak" for finding in report.blocking_findings
    )


def test_book_spec_gate_still_flags_underscored_mechanism_in_content() -> None:
    # The slug exemption is hyphen-only; underscored mechanism tokens leaking into
    # a Chinese content field must still be caught.
    report = evaluate_book_spec_quality(
        {
            "genre": "suspense-mystery",
            "tone": ["悬疑紧张，主角触发 state_loop_engine 后的代价感"],
            "reader_promise": "读者追看修复师查清死亡记录。",
            "protagonist": {"external_goal": "关闭红色日落规则。"},
        },
        language="zh-CN",
    )

    assert report.passed is False
    assert any(
        finding.code == "english_mechanism_leak" for finding in report.blocking_findings
    )


def test_repair_zh_book_spec_language_strips_echoed_genre_slug() -> None:
    from bestseller.services.prewrite_quality_profile import repair_zh_book_spec_language

    repaired = repair_zh_book_spec_language(
        {
            "genre": "suspense-mystery",  # identifier — left untouched
            "tone": "suspense-mystery 为主调，掺入旧城邻里间的温度与克制。",
            "themes": ["真相与代价", "rule-mystery-complete 式的规则恐惧"],
            "protagonist": {"external_goal": "关闭红色日落规则。"},
        },
        language="zh-CN",
    )

    assert repaired["genre"] == "suspense-mystery"  # identifier preserved
    assert "suspense" not in repaired["tone"]
    assert repaired["tone"].startswith("为主调")
    assert all("rule-mystery" not in t for t in repaired["themes"])
    # The repaired spec must now pass the gate.
    report = evaluate_book_spec_quality(repaired, language="zh-CN")
    assert not any(
        finding.code == "english_mechanism_leak" for finding in report.blocking_findings
    )


def test_repair_zh_book_spec_language_is_noop_for_english_books() -> None:
    from bestseller.services.prewrite_quality_profile import repair_zh_book_spec_language

    spec = {"tone": "suspense-mystery driven, slow-burn dread", "genre": "suspense-mystery"}
    assert repair_zh_book_spec_language(spec, language="en") == spec


def test_book_spec_gate_blocks_english_mechanism_values() -> None:
    report = evaluate_book_spec_quality(
        {
            "dramatic_question": "主角能否破解 state_loop_engine？",
            "reader_promise": "读者追看主角用职业规则反制修真地产黑箱。",
            "protagonist": {"external_goal": "保住执照并救下被楼盘吸走修为的住户。"},
        },
        language="zh-CN",
    )

    assert report.passed is False
    finding = next(
        finding for finding in report.blocking_findings if finding.code == "english_mechanism_leak"
    )
    assert finding.path == "dramatic_question"


def test_world_spec_source_gate_blocks_failed_distillation_sources() -> None:
    report = evaluate_world_spec_source_quality(
        {
            "rules": [
                {
                    "name": "灵网协议",
                    "story_consequence": "每次越权都会被执法阵标记。",
                    "source_urn": "complete-extraction-failure-source-5003",
                }
            ],
            "distillation_confidence": 0,
        }
    )

    codes = {finding.code for finding in report.blocking_findings}
    assert "fallback_source_leak" in codes
    assert "zero_confidence_source" in codes


def test_story_design_kernel_gate_blocks_opening_only_beat_schedule_for_long_plan() -> None:
    report = evaluate_story_design_kernel_quality(
        {
            "reader_promise": "读者追看主角如何把修真协议商业化。",
            "beat_schedule": [
                {"chapter_range": "1-3", "purpose": "开局展示灵网漏洞"}
            ],
        },
        target_chapters=20,
    )

    assert report.passed is False
    assert any(
        finding.code == "beat_schedule_incomplete"
        for finding in report.blocking_findings
    )


def test_story_design_kernel_gate_ignores_meta_fallback_terms() -> None:
    report = evaluate_story_design_kernel_quality(
        {
            "_meta": {
                "source_step": "story_design_kernel",
                "semantic_repair_history": [
                    {"message": "fallback retry after length-limited attempt"}
                ],
            },
            "reader_promise": "读者追看程恪如何用验房报告逼出灵网复检。",
            "beat_schedule": [{"chapter_range": "1-20", "purpose": "完整复检升级链"}],
        },
        target_chapters=20,
    )

    assert "fallback_source_leak" not in {
        finding.code for finding in report.blocking_findings
    }


def test_story_design_kernel_gate_blocks_business_fallback_source_terms() -> None:
    report = evaluate_story_design_kernel_quality(
        {
            "reader_promise": "读者追看程恪如何用验房报告逼出灵网复检。",
            "distilled_mechanism_bindings": [
                {"source_urn": "complete-extraction-failure-source-5003"}
            ],
            "beat_schedule": [{"chapter_range": "1-20", "purpose": "完整复检升级链"}],
        },
        target_chapters=20,
    )

    assert "fallback_source_leak" in {
        finding.code for finding in report.blocking_findings
    }


def test_volume_plan_gate_blocks_thin_20_chapter_plan() -> None:
    report = evaluate_volume_plan_quality(
        [
            {
                "volume_number": 1,
                "chapter_count_target": 20,
                "conflict_phase": "setup",
                "primary_force_name": "灵网执法队",
                "volume_goal": "拿到灵网入口",
            }
        ],
        target_chapters=20,
    )

    codes = {finding.code for finding in report.blocking_findings}
    assert report.passed is False
    assert "volume_plan_thin" in codes


def test_planning_kernel_repair_action_codes_have_repair_targets() -> None:
    from bestseller.services.planning_kernel import _REPAIR_ACTIONS

    missing_targets = {
        code: repair_target_for_block_code(code)
        for code in sorted(_REPAIR_ACTIONS)
        if repair_target_for_block_code(code) == "machine_blocked"
    }

    assert missing_targets == {}


def test_unknown_repair_code_defaults_to_machine_blocked() -> None:
    assert repair_target_for_block_code("new_unmapped_gate_code") == "machine_blocked"
