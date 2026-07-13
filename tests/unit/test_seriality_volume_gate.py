from __future__ import annotations

from bestseller.services.seriality_volume_gate import (
    evaluate_seriality_volume_mapping,
)
from bestseller.services.seriality_outline_gate import evaluate_seriality_outline_batch

CONTRACT = {
    "schema_version": "concept-contract.v2",
    "seriality_proof": {
        "phase_transitions": ["处理单案", "城市黑市", "跨城争夺"],
        "accumulation_tracks": ["主角权限", "组织线索"],
        "unit_families": ["异常遗体案", "黑市交易案", "跨城设施争夺"],
    },
}


def test_short_book_does_not_require_long_form_mapping() -> None:
    short_contract = {
        "schema_version": "concept-contract.v2",
        "seriality_proof": {
            "target_chapters": 20,
            "capacity_report": {
                "target_chapters": 20,
                "capacity_tier": "short",
            },
            "phase_transitions": [],
            "accumulation_tracks": [],
            "unit_families": [],
        },
    }

    report = evaluate_seriality_volume_mapping(
        [{"volume_number": 1, "chapter_count_target": 20}],
        short_contract,
    )

    assert report.passed
    assert report.blocking_codes == ()


def test_short_book_outline_does_not_require_chapter_seriality_contract() -> None:
    short_contract = {
        "schema_version": "concept-contract.v2",
        "seriality_proof": {
            "target_chapters": 20,
            "capacity_report": {
                "target_chapters": 20,
                "capacity_tier": "short",
            },
            "phase_transitions": [],
            "accumulation_tracks": [],
            "unit_families": [],
        },
    }

    report = evaluate_seriality_outline_batch(
        [{"chapter_number": 1, "chapter_goal": "完成开局行动"}],
        short_contract,
        {"chapter_count_target": 20},
    )

    assert report.passed
    assert report.blocking_codes == ()


def test_volume_mapping_requires_all_phases_and_accumulation() -> None:
    report = evaluate_seriality_volume_mapping(
        [
            {
                "seriality_phase_id": "phase-01",
                "seriality_phase_ref": "处理单案",
                "unit_family_ref": "异常遗体案",
                "renewable_unit_variant": "婚礼余生案",
                "accumulation_track_deltas": [
                    {"track_ref": "主角权限", "delta": "主角权限：首次获得查看未来片段的权限"}
                ],
            },
            {
                "seriality_phase_id": "phase-02",
                "seriality_phase_ref": "城市黑市",
                "unit_family_ref": "黑市交易案",
                "renewable_unit_variant": "彩票余生案",
                "accumulation_track_deltas": [
                    {"track_ref": "组织线索", "delta": "组织线索：锁定明日会的一名掮客"}
                ],
            },
        ],
        CONTRACT,
    )

    assert not report.passed
    assert "phase_mapping_incomplete" in report.blocking_codes


def test_volume_mapping_passes_when_contract_is_implemented() -> None:
    report = evaluate_seriality_volume_mapping(
        [
            {
                "seriality_phase_id": "phase-01",
                "seriality_phase_ref": "处理单案",
                "unit_family_ref": "异常遗体案",
                "renewable_unit_variant": "婚礼余生案",
                "accumulation_track_deltas": [{"track_ref": "主角权限", "delta": "主角权限：首次看见死者未来"}],
            },
            {
                "seriality_phase_id": "phase-02",
                "seriality_phase_ref": "城市黑市",
                "unit_family_ref": "黑市交易案",
                "renewable_unit_variant": "彩票余生案",
                "accumulation_track_deltas": [{"track_ref": "组织线索", "delta": "组织线索：确认明日会交易入口"}],
            },
            {
                "seriality_phase_id": "phase-03",
                "seriality_phase_ref": "跨城争夺",
                "unit_family_ref": "跨城设施争夺",
                "renewable_unit_variant": "跨城命运设施争夺",
                "accumulation_track_deltas": [
                    {"track_ref": "主角权限", "delta": "主角权限：可追踪跨城余生转移"},
                    {"track_ref": "组织线索", "delta": "组织线索：明日会总部坐标公开"},
                ],
            },
        ],
        CONTRACT,
    )

    assert report.passed


def test_consecutive_copy_of_same_unit_variant_is_rejected() -> None:
    report = evaluate_seriality_volume_mapping(
        [
            {
                "seriality_phase_id": "phase-01",
                "seriality_phase_ref": "处理单案",
                "unit_family_ref": "异常遗体案",
                "renewable_unit_variant": "重复处理同一种案件",
                "accumulation_track_deltas": [
                    {"track_ref": "主角权限", "delta": "主角权限：首次看见未来"},
                    {"track_ref": "组织线索", "delta": "组织线索：查到掮客姓名"},
                ],
            },
            {
                "seriality_phase_id": "phase-02",
                "seriality_phase_ref": "城市黑市",
                "unit_family_ref": "黑市交易案",
                "renewable_unit_variant": "重复处理同一种案件",
                "accumulation_track_deltas": [
                    {"track_ref": "主角权限", "delta": "主角权限：可追踪一次转让"},
                    {"track_ref": "组织线索", "delta": "组织线索：查到黑市地址"},
                ],
            },
            {
                "seriality_phase_id": "phase-03",
                "seriality_phase_ref": "跨城争夺",
                "unit_family_ref": "跨城设施争夺",
                "renewable_unit_variant": "跨城重复案件",
                "accumulation_track_deltas": [
                    {"track_ref": "主角权限", "delta": "主角权限：解锁跨城追踪"},
                    {"track_ref": "组织线索", "delta": "组织线索：定位总部入口"},
                ],
            },
        ],
        CONTRACT,
    )

    assert not report.passed
    assert "renewable_unit_repeated" in report.blocking_codes


def test_phase_stuffing_and_generic_track_deltas_are_rejected() -> None:
    report = evaluate_seriality_volume_mapping(
        [
            {
                "seriality_phase_id": "phase-01",
                "seriality_phase_ref": "处理单案、城市黑市、跨城争夺",
                "unit_family_ref": "异常遗体案",
                "renewable_unit_variant": "把所有阶段塞进一卷",
                "accumulation_track_deltas": [
                    {"track_ref": "主角权限", "delta": "主角权限变化"},
                    {"track_ref": "组织线索", "delta": "组织线索推进"},
                ],
            }
        ],
        CONTRACT,
    )

    assert not report.passed
    assert "phase_reference_invalid" in report.blocking_codes
    assert "accumulation_delta_generic" in report.blocking_codes
