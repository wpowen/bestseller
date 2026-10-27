"""Unverified terminology heuristics cannot establish character ignorance."""

from copy import deepcopy

import pytest

from bestseller.services.common_sense_gate import (
    CommonSenseFinding,
    _find_lay_character_rule_knowledge_leak,
    evaluate_common_sense_gate,
)

pytestmark = pytest.mark.unit
CODE = "lay_character_rule_knowledge_leak"


def passage(term, speech):
    return f"墙上写着「{term}」。{term}被记录在这里。{term}没有变化。\n\n{speech}"


@pytest.mark.parametrize("term,speech,genre", [
    ("潮核", "导师沈岚站在桌边。她抬眼看他：“潮核已经稳定。”", "海洋幻想"),
    ("磁阱", "研究员顾远：“磁阱已经稳定。”", "太空科幻"),
    ("契印", "匠师林乔：“契印仍在。”", "历史奇幻"),
    ("共振", "教师夏舟：“共振开始了。”", "校园实验小说"),
])
def test_mentor_and_expert_quotes_do_not_become_ignorance_contracts(term, speech, genre):
    text = passage(term, speech)
    report = evaluate_common_sense_gate(text, chapter_number=2, genre=genre)
    findings = [f for f in report.findings if f.code == CODE]
    assert findings and report.passed is True
    for finding in findings:
        assert finding.severity == "medium" and finding.blocking is False
        assert finding.evidence["authority"] == "heuristic_only"
        assert finding.evidence["actor_status"] == "unverified"
        assert finding.evidence["knowledge_status"] == "unknown"
        assert finding.evidence["terms"] == [term]
        assert finding.evidence["window"] in text
        assert "非专业角色" not in finding.message
        assert "不能据此" in finding.message


def test_action_tail_stays_an_unverified_candidate_not_a_resolved_actor():
    text = passage("潮核", "她抬眼看他：“潮核已经稳定。”")
    finding = _find_lay_character_rule_knowledge_leak(text, chapter_number=2)[0]
    assert finding.evidence["speaker_candidate"] == "她抬眼看"
    assert finding.evidence["authority"] == "heuristic_only"
    assert finding.blocking is False
    assert "character_id" not in finding.evidence


@pytest.mark.parametrize("knowledge", [None, {}, {"另一个人": ("潮核",)}, {"叶川": ()}])
def test_missing_positive_knowledge_is_unknown_not_a_forbidden_contract(knowledge):
    text = passage("潮核", "叶川：“潮核已经稳定。”")
    before = deepcopy(knowledge)
    report = evaluate_common_sense_gate(
        text, chapter_number=1, known_rule_terms_by_character=knowledge,
    )
    finding = next(f for f in report.findings if f.code == CODE)
    assert finding.blocking is False and report.passed is True
    assert finding.evidence["knowledge_status"] == "unknown"
    assert knowledge == before


def test_even_explicit_unfamiliarity_is_not_a_heuristic_proof_of_violation():
    text = passage("潮核", "叶川：“我不知道潮核是什么意思。”")
    report = evaluate_common_sense_gate(text, chapter_number=2)
    assert report.passed is True
    assert all(not f.blocking for f in report.findings if f.code == CODE)


def test_exact_positive_knowledge_remains_an_optional_noise_filter():
    text = passage("潮核", "叶川：“潮核已经稳定。”")
    report = evaluate_common_sense_gate(
        text, chapter_number=2, known_rule_terms_by_character={"叶川": ("潮核",)},
    )
    assert not any(f.code == CODE for f in report.findings)
    assert report.passed is True


def test_positive_knowledge_does_not_transfer_between_name_prefixes():
    text = passage("潮核", "叶川明：“潮核已经稳定。”")
    report = evaluate_common_sense_gate(
        text, chapter_number=2, known_rule_terms_by_character={"叶川": ("潮核",)},
    )
    assert any(f.code == CODE and not f.blocking for f in report.findings)
    assert report.passed is True


def test_audit_authority_survives_serialization_roundtrip():
    finding = _find_lay_character_rule_knowledge_leak(
        passage("潮核", "叶川：“潮核已经稳定。”"), chapter_number=2,
    )[0]
    restored = CommonSenseFinding.model_validate_json(finding.model_dump_json())
    assert restored == finding and restored.blocking is False


def test_existing_findings_keep_default_blocking_true():
    finding = CommonSenseFinding(code="other_existing_code", severity="medium", message="evidence")
    assert finding.blocking is True
    assert CommonSenseFinding.model_validate(finding.model_dump(exclude={"blocking"})).blocking is True


def test_independent_real_arithmetic_blocker_is_not_downgraded_by_advisory():
    text = passage("潮核", "叶川：“潮核已经稳定。”") + (
        "\n\n只剩半个小时。他赶路用了二十分钟，进门后竟然还剩二十分钟。"
    )
    report = evaluate_common_sense_gate(text, chapter_number=2)
    assert any(f.code == CODE and f.blocking is False for f in report.findings)
    assert report.passed is False
    blockers = [f for f in report.findings if f.blocking and f.severity in {"medium", "high"}]
    assert blockers and all(f.code != CODE for f in blockers)


@pytest.mark.parametrize("chapter", [None, 4, 30])
def test_existing_detection_scope_is_unchanged(chapter):
    assert not _find_lay_character_rule_knowledge_leak(
        passage("潮核", "叶川：“潮核已经稳定。”"), chapter_number=chapter,
    )
