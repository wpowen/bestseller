from __future__ import annotations

from bestseller.services.seriality_outline_gate import evaluate_seriality_outline_batch

CONTRACT = {"schema_version": "concept-contract.v2"}
VOLUME = {
    "seriality_phase_id": "phase-02",
    "seriality_phase_ref": "城市黑市",
    "unit_family_ref": "余生交易案",
    "renewable_unit_variant": "余生交易案",
    "accumulation_track_deltas": [
        {"track_ref": "主角权限", "delta": "主角从只能看见片段变为追踪一次交易"}
    ],
}


def _chapter(contribution: str, *, instance: str = "case-001", delta: bool = True) -> dict[str, object]:
    return {
        "seriality_contract": {
            "phase_id": "phase-02",
            "unit_family_ref": "余生交易案",
            "unit_instance_id": instance,
            "unit_variant_contribution": contribution,
            "phase_progress": f"黑市边界因{contribution}发生具体变化",
            "prior_state_refs": ["上一案获得的掮客姓名"],
            "irreversible_state_after": f"完成{contribution}后交易方永久暴露一层",
            "no_reset_evidence": f"上一案联系人参与{contribution}并承担后果",
            "accumulation_track_deltas": (
                [{"track_ref": "主角权限", "delta": f"完成{contribution}后可追踪一层交易"}]
                if delta
                else []
            ),
        }
    }


def test_outline_gate_requires_each_chapter_execution_contract() -> None:
    report = evaluate_seriality_outline_batch(
        [_chapter("发现交易入口"), {}], CONTRACT, VOLUME
    )

    assert report.passed is False
    assert "chapter_seriality_contract_missing" in report.blocking_codes


def test_outline_gate_rejects_copy_pasted_contributions() -> None:
    report = evaluate_seriality_outline_batch(
        [_chapter("调查余生交易"), _chapter("调查余生交易")], CONTRACT, VOLUME
    )

    assert report.passed is False
    assert "chapter_seriality_contribution_repeated" in report.blocking_codes


def test_outline_gate_accepts_progressive_batch_and_ignores_legacy() -> None:
    chapters = [_chapter("发现交易入口"), _chapter("迫使掮客改写交易条件")]

    assert evaluate_seriality_outline_batch(chapters, CONTRACT, VOLUME).passed is True
    assert evaluate_seriality_outline_batch([{}], None, None).passed is True


def test_outline_gate_rejects_wrong_phase_family_and_unapproved_track() -> None:
    chapter = _chapter("潜入交易入口")
    contract = chapter["seriality_contract"]
    assert isinstance(contract, dict)
    contract["phase_id"] = "phase-99"
    contract["unit_family_ref"] = "随便换一个玩法"
    contract["accumulation_track_deltas"] = [
        {"track_ref": "未批准能力", "delta": "突然获得新能力"}
    ]

    report = evaluate_seriality_outline_batch([chapter], CONTRACT, VOLUME)

    assert not report.passed
    assert "chapter_phase_reference_mismatch" in report.blocking_codes
    assert "chapter_unit_family_mismatch" in report.blocking_codes
    assert "chapter_accumulation_track_mismatch" in report.blocking_codes
    assert "chapter_accumulation_coverage_incomplete" in report.blocking_codes


def test_long_batch_requires_multiple_story_unit_instances() -> None:
    chapters = [_chapter(f"推进步骤{index}") for index in range(1, 13)]

    report = evaluate_seriality_outline_batch(chapters, CONTRACT, VOLUME)

    assert not report.passed
    assert "chapter_story_unit_density_too_low" in report.blocking_codes


def test_partial_batch_may_prepare_track_but_full_volume_must_realize_it() -> None:
    volume = {**VOLUME, "chapter_count_target": 4}
    partial = [_chapter("发现入口", delta=False), _chapter("跟踪掮客", delta=False)]

    assert evaluate_seriality_outline_batch(partial, CONTRACT, volume).passed

    full = [
        _chapter("发现入口", delta=False),
        _chapter("跟踪掮客", delta=False),
        _chapter("确认交易", delta=False),
        _chapter("封锁出口", delta=False),
    ]
    report = evaluate_seriality_outline_batch(full, CONTRACT, volume)
    assert not report.passed
    assert "chapter_accumulation_coverage_incomplete" in report.blocking_codes
