from __future__ import annotations

from pathlib import Path

from bestseller.services.cross_kernel_consistency_gate import (
    evaluate_cross_kernel_consistency,
)
from bestseller.services.kernel_integration_validator import (
    EXPECTED_KERNEL_FILES,
    validate_kernel_file_integration,
)
from bestseller.services.voice_profile_coverage_gate import evaluate_voice_profile_coverage
from bestseller.services.wave4_gate_suite import WAVE4_GATE_NAMES, normalize_wave4_gate_suite


def test_kernel_integration_validator_requires_all_kernel_files(tmp_path: Path) -> None:
    kernels_dir = tmp_path / "story-bible/kernels"
    kernels_dir.mkdir(parents=True)
    for filename in EXPECTED_KERNEL_FILES[:-1]:
        (kernels_dir / filename).write_text("x" * 32, encoding="utf-8")

    verdict = validate_kernel_file_integration(tmp_path / "story-bible", min_bytes=16)

    assert verdict.verdict == "blocked"
    assert verdict.findings[0].code == "kernel_file_missing"


def test_kernel_integration_validator_passes_complete_non_placeholder_set(
    tmp_path: Path,
) -> None:
    kernels_dir = tmp_path / "story-bible/kernels"
    kernels_dir.mkdir(parents=True)
    for filename in EXPECTED_KERNEL_FILES:
        (kernels_dir / filename).write_text("x" * 32, encoding="utf-8")

    verdict = validate_kernel_file_integration(tmp_path / "story-bible", min_bytes=16)

    assert verdict.passed is True


def test_voice_profile_coverage_gate_requires_95_percent_coverage() -> None:
    verdict = evaluate_voice_profile_coverage(
        [
            {"name": "林渊", "voice_profile": {"register": "冷静"}},
            {"name": "苏婉宁"},
        ]
    )

    assert verdict.verdict == "blocked"
    assert verdict.metrics["identity_registry_coverage"] == 0.5
    assert verdict.findings[0].code == "voice_profile_missing"


def test_voice_profile_coverage_gate_can_filter_required_roles() -> None:
    verdict = evaluate_voice_profile_coverage(
        [
            {"name": "林渊", "role": "protagonist", "voice_profile": {"register": "冷静"}},
            {"name": "路人甲", "role": "supporting"},
        ],
        required_roles=("protagonist",),
    )

    assert verdict.passed is True
    assert verdict.metrics["named_character_count"] == 1


def test_wave4_gate_suite_normalizes_six_gate_payloads() -> None:
    verdicts = normalize_wave4_gate_suite(
        {"geography_continuity_gate": {"passed": True, "coverage": 1.0}}
    )

    assert len(verdicts) == len(WAVE4_GATE_NAMES)
    assert verdicts[0].gate_name == "geography_continuity_gate"
    assert all(verdict.schema_version == "gate-verdict.v2" for verdict in verdicts)


def test_cross_kernel_consistency_flags_mystery_payoff_outside_scope() -> None:
    verdict = evaluate_cross_kernel_consistency(
        {
            "mystery_anchor_kernel": {
                "anchors": [
                    {
                        "question": "镜债真正源头是谁",
                        "stake_if_solved": "主线闭环",
                        "reveal_milestones": [
                            {
                                "volume": 1,
                                "fraction_revealed": 0.2,
                                "reveal_kind": "hint",
                                "description": "线索",
                            },
                            {
                                "volume": 1,
                                "fraction_revealed": 0.8,
                                "reveal_kind": "partial_truth",
                                "description": "半真相",
                            },
                        ],
                        "final_payoff_chapter_range": [120, 130],
                    }
                ]
            }
        },
        total_chapters=100,
    )

    assert verdict.verdict == "blocked"
    assert verdict.findings[0].code == "mystery_payoff_beyond_book_scope"
