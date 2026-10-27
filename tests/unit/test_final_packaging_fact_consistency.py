from __future__ import annotations

from bestseller.services import conception


def test_final_packaging_fact_gate_catches_late_synopsis_mutation() -> None:
    conflicts = conception._detect_final_packaging_numeric_fact_conflicts(
        premise="陆家第七代守着祖宅。",
        synopsis="陆家第十八代终于回到祖宅。",
        story_spine={"core_abnormality": "陆家第七代守着同一口井。"},
    )

    assert conflicts
    assert all(conflict.unit == "代" for conflict in conflicts)
    report = conception._final_packaging_fact_block_report(
        conflicts,
        title="旧标题",
        synopsis="陆家第十八代终于回到祖宅。",
    )
    assert report["passed"] is False
    assert report["fact_consistency_blocked"] is True
    assert report["hard_conflicts"][0]["code"] == "CANON_FACT_CONSISTENCY_BLOCKED"
    assert report["audit_identity"]["synopsis"]["char_count"] == len(
        "陆家第十八代终于回到祖宅。"
    )


def test_final_packaging_fact_gate_allows_different_character_numbers() -> None:
    conflicts = conception._detect_final_packaging_numeric_fact_conflicts(
        premise="陆家第七代守着祖宅。",
        synopsis="陈家第十八代终于回到祖宅。",
        story_spine={"core_abnormality": "陆家第七代守着同一口井。"},
    )

    assert conflicts == []


def test_final_packaging_fact_gate_allows_consistent_rewritten_synopsis() -> None:
    conflicts = conception._detect_final_packaging_numeric_fact_conflicts(
        premise="陆家第七代守着祖宅。",
        synopsis="陆家第七代终于回到祖宅。",
        story_spine={"core_abnormality": "陆家第七代守着同一口井。"},
    )

    assert conflicts == []


def test_fact_blocked_candidate_is_rejected_by_existing_promotion_decision() -> None:
    conflicts = conception._detect_final_packaging_numeric_fact_conflicts(
        premise="陆家第七代守着祖宅。",
        synopsis="陆家第十八代终于回到祖宅。",
        story_spine={"core_abnormality": "陆家第七代守着同一口井。"},
    )
    candidate_report = conception._final_packaging_fact_block_report(
        conflicts,
        title="新标题",
        synopsis="陆家第十八代终于回到祖宅。",
    )

    decision = conception._packaging_candidate_decision(
        incumbent_title="旧标题",
        candidate_title="新标题",
        incumbent_synopsis="陆家第七代终于回到祖宅。",
        candidate_synopsis="陆家第十八代终于回到祖宅。",
        candidate_rank=(),
        incumbent_rank=(0.0,),
        candidate_passed=bool(candidate_report["passed"]),
    )

    assert decision["accepted"] is False
    assert decision["candidate_explored"] is True
