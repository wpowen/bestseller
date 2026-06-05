from bestseller.infra.db.models import ChapterModel
from bestseller.services.rewrite_escalation import (
    EscalationLevel,
    apply_post_process,
    decide_escalation,
)

# ruff: noqa: RUF001


def _chapter(attempts=None):
    return ChapterModel(
        chapter_number=7,
        chapter_goal="x",
        metadata_json={"rewrite_attempts_by_kind": attempts or {}},
    )


def test_length_over_hits_force_reduce_on_4th_attempt():
    decision = decide_escalation(
        chapter=_chapter({"length": 3}),
        block_codes=["BLOCK_HIGH"],
        current_word_count=3200,
        target_word_count=2200,
        hard_max_word_count=3000,
    )

    assert decision.level == EscalationLevel.FORCE_REDUCE
    assert "必须删除" in decision.strict_directive


def test_length_extreme_over_triggers_truncate_on_5th():
    decision = decide_escalation(
        chapter=_chapter({"length": 4}),
        block_codes=["BLOCK_HIGH"],
        current_word_count=4200,
        target_word_count=2200,
        hard_max_word_count=3000,
    )

    assert decision.post_process_action == "truncate"


def test_forbidden_term_persistent_triggers_regex_strip():
    decision = decide_escalation(
        chapter=_chapter({"forbidden_term": 4}),
        block_codes=["CANON_FORBIDDEN_TERM"],
        current_word_count=2200,
        target_word_count=2200,
        hard_max_word_count=3000,
        forbidden_terms_hit=["旧设定"],
    )

    assert decision.post_process_action == "regex_strip"


def test_persistent_general_failure_triggers_machine_repair_not_human_review():
    decision = decide_escalation(
        chapter=_chapter({"canon_state": 5}),
        block_codes=["CANON_STATE_REGRESSION"],
        current_word_count=2200,
        target_word_count=2200,
        hard_max_word_count=3000,
    )

    assert decision.level == EscalationLevel.MACHINE_REPAIR
    assert "机器深度修复" in decision.strict_directive
    assert "等待人工" not in decision.strict_directive


def test_post_process_truncate_preserves_ending_hook():
    decision = decide_escalation(
        chapter=_chapter({"length": 4}),
        block_codes=["BLOCK_HIGH"],
        current_word_count=5000,
        target_word_count=300,
        hard_max_word_count=1000,
    )
    text = "开头" + ("中" * 1000) + "章末钩子？"
    modified, log = apply_post_process(text, decision)

    assert log["applied"] is True
    assert "章末钩子？" in modified


def test_post_process_regex_strip_uses_replacement_table():
    decision = decide_escalation(
        chapter=_chapter({"forbidden_term": 4}),
        block_codes=["CANON_FORBIDDEN_TERM"],
        current_word_count=100,
        target_word_count=100,
        hard_max_word_count=200,
    )
    modified, log = apply_post_process("这里有旧设定。", decision, forbidden_terms=["旧设定"])

    assert "旧设定" not in modified
    assert log["replacements"]["旧设定"] == 1
