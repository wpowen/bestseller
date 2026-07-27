"""Phase B/C remediation guards: reader_judge axes, audit_only, voice few-shot, arena."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services import pipelines
from bestseller.services.lean_full_arena import (
    ProfileDraftPair,
    anonymize_for_judge,
    summarize_lean_wins,
    to_arena_pair,
)
from bestseller.services.reader_judge import (
    aggregate_prose_quality,
    extract_reader_judge_dimensions,
    voice_axis_failures,
)
from bestseller.services.voice_few_shot import render_voice_few_shot
from bestseller.settings import PipelineSettings


pytestmark = pytest.mark.unit


def test_reader_judge_weights_include_voice_axes() -> None:
    dims = {
        "opening_pull": 0.8,
        "payoff_density": 0.6,
        "emotional_impact": 0.7,
        "anti_abandon": 0.5,
        "ai_taste": 0.4,
        "human_voice": 0.9,
    }
    expected = (
        0.8 * 0.18
        + 0.6 * 0.22
        + 0.7 * 0.18
        + 0.5 * 0.14
        + 0.4 * 0.14
        + 0.9 * 0.14
    )
    assert aggregate_prose_quality(dims) == pytest.approx(expected, abs=1e-6)


def test_voice_axis_failures_respect_enforce_flag() -> None:
    dims = {"ai_taste": 0.2, "human_voice": 0.2}
    assert voice_axis_failures(dims, enforce=False) == []
    fails = voice_axis_failures(dims, enforce=True, min_ai_taste=0.55, min_human_voice=0.55)
    assert any(x.startswith("reader_judge:ai_taste:") for x in fails)
    assert any(x.startswith("reader_judge:human_voice:") for x in fails)


def test_voice_axis_failures_missing_dims() -> None:
    fails = voice_axis_failures({}, enforce=True)
    assert "reader_judge:ai_taste:missing" in fails
    assert "reader_judge:human_voice:missing" in fails


def test_final_gate_blocks_low_voice_when_enforced(monkeypatch) -> None:
    monkeypatch.setattr(
        pipelines,
        "get_quality_gates_config",
        lambda: SimpleNamespace(
            ai_flavor=SimpleNamespace(enabled=False),
            prose_quality=SimpleNamespace(
                anti_meta_enabled=False,
                anti_meta_severity="block",
                in_scene_ending_severity="block",
                show_dont_tell_enabled=False,
                show_dont_tell_severity="warn",
            ),
            reader_quality=SimpleNamespace(
                enforce_reader_judge_voice_axes=True,
                min_ai_taste=0.55,
                min_human_voice=0.55,
            ),
        ),
    )
    result = pipelines.run_final_quality_gates(
        chapter_number=1,
        content_md="雨停了。她把门关上。",
        project=SimpleNamespace(language="zh-CN", metadata_json={}),
        chapter_metadata={
            "reader_judge": {
                "dimensions": {"ai_taste": 0.2, "human_voice": 0.9},
            }
        },
    )
    assert result.passed is False
    assert any(i.startswith("reader_judge:ai_taste:") for i in result.issues)


def test_final_gate_voice_debt_is_soft(monkeypatch) -> None:
    monkeypatch.setattr(
        pipelines,
        "get_quality_gates_config",
        lambda: SimpleNamespace(
            ai_flavor=SimpleNamespace(enabled=False),
            prose_quality=SimpleNamespace(
                anti_meta_enabled=False,
                anti_meta_severity="block",
                in_scene_ending_severity="block",
                show_dont_tell_enabled=False,
                show_dont_tell_severity="warn",
            ),
            reader_quality=SimpleNamespace(
                enforce_reader_judge_voice_axes=True,
                min_ai_taste=0.55,
                min_human_voice=0.55,
            ),
        ),
    )
    result = pipelines.run_final_quality_gates(
        chapter_number=1,
        content_md="雨停了。她把门关上。",
        project=SimpleNamespace(language="zh-CN", metadata_json={}),
        chapter_metadata={
            "reader_judge_voice_debt": True,
            "reader_judge": {
                "dimensions": {"ai_taste": 0.2, "human_voice": 0.2},
            },
        },
    )
    assert result.passed is True
    assert any(i.startswith("voice_debt:") for i in result.issues)


def test_voice_few_shot_default_off() -> None:
    assert render_voice_few_shot(genre_key="xianxia", enabled=False) == ""


def test_voice_few_shot_enabled_is_bounded() -> None:
    text = render_voice_few_shot(genre_key="xianxia", enabled=True)
    assert "声口短范例" in text
    assert len(text) <= 520


def test_enable_voice_few_shot_default_false() -> None:
    assert PipelineSettings().enable_voice_few_shot is False


def test_lean_full_arena_pair_mapping() -> None:
    item = ProfileDraftPair(
        pair_id="ch1",
        chapter_number=1,
        lean_text="lean draft body",
        full_text="full draft body",
    )
    pair = to_arena_pair(item)
    assert pair.framework_text == "lean draft body"
    assert pair.benchmark_text == "full draft body"
    assert anonymize_for_judge("【lean】hello prose_prompt_profile") == "hello "


def test_summarize_lean_wins() -> None:
    from bestseller.services.benchmark_arena import ArenaMatchResult, ArenaPair

    def _r(outcome: str) -> ArenaMatchResult:
        return ArenaMatchResult(
            pair=ArenaPair(
                pair_id="x",
                framework_text="a",
                benchmark_text="b",
                benchmark_tier="t",
                category="c",
                chapter_number=1,
            ),
            outcome=outcome,
            forward=None,
            backward=None,
        )

    summary = summarize_lean_wins([_r("win"), _r("loss"), _r("tie")])
    assert summary["pairs"] == 3
    assert summary["lean_win_rate"] == pytest.approx(0.5, abs=1e-6)


def test_extract_reader_judge_dimensions() -> None:
    dims = extract_reader_judge_dimensions(
        {"reader_judge": {"dimensions": {"ai_taste": 0.8, "human_voice": 1.2}}}
    )
    assert dims["ai_taste"] == pytest.approx(0.8)
    assert dims["human_voice"] == pytest.approx(1.0)


def test_pipelines_honors_reader_judge_audit_only_in_source() -> None:
    from bestseller.services import pipelines
    import inspect

    src = inspect.getsource(pipelines)
    assert "reader_judge_audit_only" in src
    assert "if not _audit_only:" in src
    assert "_prose_quality_score = _judge.prose_quality_score" in src


def test_drafts_lean_strips_action_sequence_in_source() -> None:
    from bestseller.services import drafts
    import inspect

    src = inspect.getsource(drafts)
    assert 'if _scene_prose_profile == "lean":' in src
    assert "_current_scene_contract.pop(\"action_sequence\", None)" in src
