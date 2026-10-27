"""Unavailable reader evaluation must not become a clean quality verdict."""

from pathlib import Path
from uuid import uuid4

import pytest

from bestseller.services import reader_panel_judge as judge
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.quality_attribution_loop import run_quality_attribution_loop
from bestseller.settings import load_settings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "fallback"),
    [
        ("[]", True),
        ("not a review", False),
        ("{}", False),
        ('[{"issue":"missing evidence"}]', False),
    ],
)
async def test_failed_panel_never_converges(tmp_path: Path, monkeypatch, content, fallback):
    (tmp_path / "chapter-001.md").write_text(
        "A pilot discovers that the navigation log was altered."
    )

    async def complete(*args, **kwargs):
        return LLMCompletionResult(
            content=content,
            provider="openai",
            model_name="test-critic",
            llm_run_id=uuid4(),
            finish_reason="stop",
            fallback_used=fallback,
        )

    monkeypatch.setattr(judge, "complete_text", complete)
    result = await run_quality_attribution_loop(
        None,
        load_settings(env={}),
        tmp_path,
        chapter_range=(1, 1),
        panel=(judge.DEFAULT_PANEL[0],),
        max_iterations=1,
    )
    assert result["converged"] is False
    assert result["unavailable_reason"].startswith("READER_PANEL_")
    assert result["repair_log"][0]["status"] == "evaluation_unavailable"


@pytest.mark.asyncio
async def test_no_text_never_converges(tmp_path: Path):
    result = await run_quality_attribution_loop(
        None, load_settings(env={}), tmp_path, chapter_range=(1, 1), max_iterations=1
    )
    assert result["converged"] is False
    assert result["unavailable_reason"] == "READER_PANEL_NO_TEXT"


@pytest.mark.asyncio
async def test_real_empty_review_can_converge(tmp_path: Path, monkeypatch):
    (tmp_path / "chapter-001.md").write_text(
        "A pilot discovers that the navigation log was altered."
    )

    async def complete(*args, **kwargs):
        return LLMCompletionResult(
            content="[]",
            provider="openai",
            model_name="test-critic",
            llm_run_id=uuid4(),
            finish_reason="stop",
        )

    monkeypatch.setattr(judge, "complete_text", complete)
    result = await run_quality_attribution_loop(
        None,
        load_settings(env={}),
        tmp_path,
        chapter_range=(1, 1),
        panel=(judge.DEFAULT_PANEL[0],),
        max_iterations=1,
    )
    assert result["converged"] is True
