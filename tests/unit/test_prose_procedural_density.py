"""Synthetic, book-independent behavior tests for the audit-only probe."""

# ruff: noqa: RUF001 -- Chinese punctuation/full-width input is intentional.

from dataclasses import replace
import json

import pytest

from bestseller.services.prose_procedural_density import (
    AuditConfig,
    DialogueTurn,
    audit_prose,
)


def metric(text, name, **kwargs):
    return audit_prose(text, **kwargs)["metrics"][name]


def test_empty_is_serializable_audit_only_without_gate_decision():
    report = audit_prose("")
    assert report["mode"] == "audit_only"
    assert report["metrics"]["characters"] == 0
    assert report["metrics"]["dialogue"]["status"] == "insufficient_sample"
    assert not {"passed", "must_rewrite", "blocked"} & report.keys()
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize(
    "clock", ["19:05", "１９：０５", "十九时零五分", "晚上七点零五分", "七点半", "午后两点一刻"]
)
def test_concrete_clock_variants(clock):
    assert metric(f"她看表，{clock}，船来了。", "clock")["count"] == 1


@pytest.mark.parametrize(
    "text", ["过了七分钟。", "还有两个小时。", "三点意见。", "25:89", "二十九点五十分", "127:050"]
)
def test_duration_figurative_and_invalid_clock_not_counted(text):
    assert metric(text, "clock")["count"] == 0


def test_density_denominator_and_spans_use_original_text():
    text = "\n  19:05  她来了。"
    result = metric(text, "clock")
    assert result["per_1000_characters"] == pytest.approx(1000 / len("19:05她来了。"))
    span = result["spans"][0]
    assert text[span["start"] : span["end"]] == "19:05"


@pytest.mark.parametrize("subject", ["陶罐", "星图", "滑板"])
def test_state_repetition_survives_object_renaming_and_paraphrases(subject):
    text = f"{subject}仍在桌上。{subject}依旧在桌上。{subject}还在桌上。"
    result = metric(text, "state_repetition")
    assert result["repeated_mentions"] == 2
    assert result["groups"][0]["mentions"] == 3


def test_state_changes_and_distinct_objects_not_collapsed():
    result = metric("陶罐仍在桌上。星图还在柜里。陶罐仍在柜里。", "state_repetition")
    assert result["repeated_mentions"] == 0


def test_same_state_synonyms_and_explicit_aliases():
    result = metric(
        "纸盒完好无损。那个盒子原封不动。纸盒没有破损。",
        "state_repetition",
        entity_aliases={"那个盒子": "纸盒"},
    )
    assert result["repeated_mentions"] == 2


def test_repeat_window_does_not_claim_distant_state_is_redundant():
    cfg = replace(AuditConfig(), repetition_window=20)
    assert (
        metric(
            "陶罐还在桌上。" + "风吹过山坡。" * 20 + "陶罐仍在桌上。",
            "state_repetition",
            config=cfg,
        )["repeated_mentions"]
        == 0
    )


def test_amounts_normalize_cjk_arabic_and_currency_not_clocks():
    result = metric("账上三百元，付三百块钱，又记300元；19:05。", "number_repetition")
    assert result["repeated_mentions"] == 2
    assert result["groups"][0]["mentions"] == 3


def test_quantities_keep_units_separate_and_support_nonmoney_numbers():
    result = metric("三十斤，30斤，三十米，30米，三十元。", "number_repetition")
    assert result["repeated_mentions"] == 2
    assert len(result["groups"]) == 2


def test_chinese_large_amounts_decimal_and_negative():
    result = metric("一点五万元，15000元；负三元，-3元。", "number_repetition")
    assert result["repeated_mentions"] == 2


def test_process_lexicon_is_replaceable_and_longest_nonoverlapping():
    cfg = replace(AuditConfig(), process_terms=("核验", "重新核验", "登记"))
    result = metric("重新核验，然后登记，封存。", "process_terms", config=cfg)
    assert result["count"] == 2
    assert result["term_counts"] == {"重新核验": 1, "登记": 1}
    assert metric("封存", "process_terms", config=replace(cfg, process_terms=()))["count"] == 0


def test_negation_chain_accepts_different_negative_forms():
    result = metric("没有回头，不曾伸手，未作答。花开了。", "negation_chains")
    assert result["chain_count"] == 1
    assert result["longest_chain"] == 3


def test_negation_chain_broken_by_positive_clause_and_lexical_exceptions():
    result = metric(
        "他没有回头。花开了。他不肯回答。她没有走。\n不仅如此，不错，不由自主地笑了。",
        "negation_chains",
    )
    assert result["chain_count"] == 0
    assert result["longest_chain"] == 2


def test_shared_speaker_language_reports_overlap_not_literary_verdict():
    turns = [
        DialogueTurn(who, "请先核对记录，然后办理交接。") for who in ("甲", "乙") for _ in range(4)
    ]
    result = metric("", "dialogue", dialogue_turns=turns)
    assert result["status"] == "measured"
    assert result["pairs"][0]["bigram_jaccard"] == 1


def test_distinct_voice_has_lower_overlap():
    turns = [DialogueTurn("甲", "俺饿啦，锅里还有吃的没？") for _ in range(4)] + [
        DialogueTurn("乙", "诸位，星舰即将进入跃迁轨道。") for _ in range(4)
    ]
    assert metric("", "dialogue", dialogue_turns=turns)["pairs"][0]["bigram_jaccard"] < 0.1


def test_explicit_transcript_and_attribution_but_no_guessed_speakers():
    text = (
        "甲：请先核对记录，然后办理交接。\n乙：请先核对记录，然后办理交接。\n"
        "甲说：“再查一次。”\n“我不答应。”乙说。\n“没人知道是谁说的。”"
    )
    cfg = replace(AuditConfig(), dialogue_min_chars=1, dialogue_min_turns=1)
    result = metric(text, "dialogue", config=cfg)
    assert result["attributed_turns"] == 4
    assert result["unattributed_quotes"] == 1
    assert len(result["speakers"]) == 2


def test_insufficient_dialogue_must_not_report_zero_homogeneity():
    result = metric("“我走了。”\n“回来。”", "dialogue")
    assert result["status"] == "insufficient_sample"
    assert result["pairs"] == []


def test_deterministic_no_mutation_and_config_identity():
    turns = [DialogueTurn("甲", "先走吧。")]
    a = audit_prose("凌晨三点，登记。", dialogue_turns=turns)
    assert a == audit_prose("凌晨三点，登记。", dialogue_turns=turns)
    assert len(turns) == 1
    b = audit_prose("凌晨三点，登记。", config=replace(AuditConfig(), process_terms=()))
    assert a["config_sha256"] != b["config_sha256"]


@pytest.mark.parametrize(
    "kwargs", [{"repetition_window": 0}, {"dialogue_min_chars": 0}, {"negation_chain_min": 1}]
)
def test_invalid_config_rejected(kwargs):
    with pytest.raises(ValueError):
        AuditConfig(**kwargs)


def test_large_number_does_not_multiply_embedded_section_twice():
    assert metric("一亿三千万人，130000000人。", "number_repetition")["repeated_mentions"] == 1


def test_unresolved_pronouns_are_not_distinct_named_speakers():
    cfg = replace(AuditConfig(), dialogue_min_chars=1, dialogue_min_turns=1)
    result = metric("他说：“快走。”\n她说：“等等。”", "dialogue", config=cfg)
    assert result["status"] == "insufficient_sample"
    assert result["attributed_turns"] == 0
    assert result["unattributed_quotes"] == 2


def test_negation_inside_time_and_idiom_is_not_negative_clause():
    result = metric("未时风起，未央宫很远，不小心摔了一跤。", "negation_chains")
    assert result["chain_count"] == 0
    assert result["longest_chain"] == 0


def test_do_not_confuse_two_contradictory_normalized_states():
    result = metric("纸盒没有破损。纸盒没有移动。", "state_repetition")
    assert result["repeated_mentions"] == 0


@pytest.mark.parametrize("subject", ["舱门", "闸口", "木窗"])
def test_generic_confirmations_and_closed_state_paraphrases(subject):
    text = f"{subject}保持关闭。{subject}仍然紧闭。{subject}没有打开。"
    assert metric(text, "state_repetition")["repeated_mentions"] == 2


def test_open_and_closed_states_do_not_merge():
    assert metric("舱门保持关闭。舱门保持开启。", "state_repetition")["repeated_mentions"] == 0
