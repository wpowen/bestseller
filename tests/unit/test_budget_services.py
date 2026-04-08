from __future__ import annotations

import pytest
from uuid import uuid4

from bestseller.services.budget import (
    TokenUsageSummary,
    BudgetCheckResult,
    estimate_project_cost,
)


def _make_settings():
    """Create minimal settings for budget tests."""
    from bestseller.settings import load_settings
    from pathlib import Path

    return load_settings(
        config_path=Path("config/default.yaml"),
        env={},
    )


def test_estimate_project_cost_returns_expected_fields() -> None:
    settings = _make_settings()
    result = estimate_project_cost(10, settings)

    assert result["chapter_count"] == 10
    assert result["estimated_input_tokens"] > 0
    assert result["estimated_output_tokens"] > 0
    assert result["estimated_total_tokens"] == result["estimated_input_tokens"] + result["estimated_output_tokens"]
    assert result["estimated_cost_usd"] >= 0
    assert "note" in result


def test_estimate_project_cost_scales_with_chapters() -> None:
    settings = _make_settings()
    cost_10 = estimate_project_cost(10, settings)
    cost_100 = estimate_project_cost(100, settings)

    assert cost_100["estimated_cost_usd"] > cost_10["estimated_cost_usd"]
    # Should scale roughly linearly
    ratio = cost_100["estimated_cost_usd"] / cost_10["estimated_cost_usd"]
    assert 9 < ratio < 11


def test_token_usage_summary_model() -> None:
    project_id = uuid4()
    summary = TokenUsageSummary(
        project_id=project_id,
        total_input_tokens=100000,
        total_output_tokens=25000,
        total_tokens=125000,
        estimated_cost_usd=0.675,
        llm_call_count=50,
    )
    assert summary.total_tokens == 125000
    assert summary.llm_call_count == 50


def test_budget_check_result_model() -> None:
    project_id = uuid4()
    usage = TokenUsageSummary(project_id=project_id)
    result = BudgetCheckResult(
        within_budget=True,
        usage=usage,
        budget_limit=0,
        utilization_pct=0.0,
        warning_level=None,
    )
    assert result.within_budget is True
    assert result.warning_level is None
