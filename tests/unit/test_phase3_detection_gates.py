from __future__ import annotations

from bestseller.domain.chapter_seam_contract import ChapterSeamContract
from bestseller.services.chapter_seam_inheritance_gate import (
    evaluate_chapter_seam_inheritance,
)
from bestseller.services.duplicate_passage_gate import evaluate_duplicate_passages
from bestseller.services.forbidden_terms_drift_gate import evaluate_forbidden_terms_drift
from bestseller.services.gate_verdict_migration import (
    CORE_GATE_BATCH_A,
    normalize_core_gate_batch,
    normalize_gate_payload,
)
from bestseller.services.identity_freezer_gate import evaluate_identity_freezer
from bestseller.services.paragraph_coherence_gate import evaluate_paragraph_coherence


def test_paragraph_coherence_gate_flags_reset_and_floating_subject() -> None:
    verdict = evaluate_paragraph_coherence(
        {
            51: "# 第51章\n\n多年以前, 故事重新开始。\n\n他走过去。\n\n这时黑影出现。",
        }
    )

    codes = {finding.code for finding in verdict.findings}
    assert verdict.verdict == "blocked"
    assert "chapter_opening_reset" in codes
    assert "floating_pronoun_or_subject" in codes


def test_chapter_seam_inheritance_gate_requires_callbacks() -> None:
    verdict = evaluate_chapter_seam_inheritance(
        previous_chapter_text="林渊掌心的回执镜片烫得发红。",
        current_chapter_text="林渊推门进了张家旧宅。",
        seam_contract=ChapterSeamContract(
            chapter_no=52,
            inherits_from_prev=["回执镜片发烫"],
            required_callbacks=["回执镜片"],
        ),
    )

    assert verdict.verdict == "blocked"
    assert "required_callback_missing" in {finding.code for finding in verdict.findings}


def test_duplicate_passage_gate_finds_cross_chapter_duplicates() -> None:
    repeated = "林渊把铜钱压在镜面上, 听见门外三短一长的敲击声。"
    verdict = evaluate_duplicate_passages({8: repeated, 62: repeated, 64: "新段落"})

    assert verdict.verdict == "blocked"
    assert verdict.findings[0].code == "duplicate_passage"


def test_identity_freezer_gate_reports_unfrozen_named_roles() -> None:
    verdict = evaluate_identity_freezer(
        [
            {"name": "林渊", "frozen": True},
            {"name": "苏婉宁", "frozen": False},
        ]
    )

    assert verdict.verdict == "blocked"
    assert verdict.metrics["identity_registry_coverage"] == 0.5
    assert verdict.findings[0].code == "identity_not_frozen"


def test_forbidden_terms_drift_gate_finds_old_world_terms() -> None:
    verdict = evaluate_forbidden_terms_drift(
        {62: "玩家进入试炼副本。", 71: "林渊追查镜债。"},
        guardrails={"forbidden_terms": [{"term": "玩家"}, "试炼"]},
    )

    assert verdict.verdict == "blocked"
    assert {finding.path for finding in verdict.findings} == {"chapter:62"}


def test_gate_verdict_migration_normalizes_batch_a() -> None:
    verdict = normalize_gate_payload(
        "legacy_gate",
        {
            "passed": False,
            "overall_score": 80,
            "issues": [{"code": "X", "severity": "critical", "detail": "bad"}],
        },
    )
    batch = normalize_core_gate_batch({"commercial_novel_gate": {"passed": True}})

    assert verdict.verdict == "blocked"
    assert len(batch) == len(CORE_GATE_BATCH_A)
    assert all(item.schema_version == "gate-verdict.v2" for item in batch)
