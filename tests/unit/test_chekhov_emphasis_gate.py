from __future__ import annotations

import pytest

from bestseller.services.chekhov_emphasis_gate import evaluate_chekhov_emphasis

pytestmark = pytest.mark.unit


def test_chekhov_emphasis_passes_paid_functional_item() -> None:
    report = evaluate_chekhov_emphasis(
        current_chapter=5,
        emphasized_items=(
            {
                "item": "铜镜裂纹",
                "prominence": "high",
                "expected_function": "映出父亲旧名",
                "expected_use_by_chapter": 5,
                "status": "used",
            },
        ),
    )

    assert report.passed is True
    assert report.issues == ()


def test_chekhov_emphasis_reports_missing_function_window_and_overdue() -> None:
    report = evaluate_chekhov_emphasis(
        current_chapter=8,
        emphasized_items=(
            {
                "item": "黑色铜钱",
                "prominence": "high",
                "status": "planted",
            },
            {
                "item": "药铺暗门",
                "prominence": "high",
                "expected_function": "逃出围捕",
                "expected_use_by_chapter": 6,
                "status": "active",
            },
        ),
    )

    codes = {issue.id for issue in report.issues}

    assert report.passed is False
    assert "CHEKHOV_EXPECTED_FUNCTION_MISSING" in codes
    assert "CHEKHOV_USE_WINDOW_MISSING" in codes
    assert "CHEKHOV_USE_OVERDUE" in codes


def test_chekhov_emphasis_extracts_items_from_contract_metadata() -> None:
    report = evaluate_chekhov_emphasis(
        current_chapter=3,
        chapter_contract={
            "metadata": {
                "emphasized_items": [
                    {
                        "item": "墙上旧符",
                        "prominence": "high",
                        "clue_type": "minor",
                        "expected_function": "提示祠堂方向",
                        "expected_use_by_chapter": 4,
                        "dual_type": True,
                    }
                ]
            }
        },
    )

    codes = {issue.id for issue in report.issues}

    assert "CHEKHOV_MINOR_CLUE_OVEREMPHASIZED" in codes
    assert "CHEKHOV_DUAL_TYPE_UNLINKED" in codes


def test_chekhov_emphasis_empty_input_passes() -> None:
    report = evaluate_chekhov_emphasis(current_chapter=1)

    assert report.passed is True
    assert report.metrics["emphasized_item_count"] == 0
