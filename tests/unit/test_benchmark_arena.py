from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from bestseller.services.benchmark_arena import (
    ARENA_DIMENSIONS,
    ArenaPair,
    BenchmarkTargets,
    adapt_independent_judge_result,
    build_arena_system_prompt,
    build_arena_user_prompt,
    evaluate_targets,
    load_benchmark_targets,
    parse_arena_verdict,
    run_arena_match,
    summarize_arena,
)


def _pair(pair_id: str = "p1", tier: str = "t1") -> ArenaPair:
    return ArenaPair(
        pair_id=pair_id,
        framework_text="框架正文。",
        benchmark_text="真书正文。",
        benchmark_tier=tier,
        category="action-progression",
        chapter_number=1,
        framework_label="fw-book",
        benchmark_label="《某真书》",
    )


def test_arena_prompts_are_blind() -> None:
    system = build_arena_system_prompt()
    assert "不要因为出处偏向任何一方" in system
    for key in ARENA_DIMENSIONS:
        assert key in system
    user = build_arena_user_prompt("文本A", "文本B", category="悬疑", chapter_number=50)
    # Prompt must never carry book identities — only the raw texts.
    assert "《" not in user
    assert "文本A" in user and "文本B" in user
    assert "第50章" in user


def test_parse_arena_verdict_maps_slots() -> None:
    raw = json.dumps(
        {"winner": "甲", "dimensions": {"hook": "乙", "prose": "持平"}, "reason": "r"},
        ensure_ascii=False,
    )
    forward = parse_arena_verdict(raw, framework_is_a=True)
    assert forward is not None
    assert forward.winner == "framework"
    assert forward.dimension_winners["hook"] == "benchmark"
    assert forward.dimension_winners["prose"] == "tie"
    backward = parse_arena_verdict(raw, framework_is_a=False)
    assert backward is not None
    assert backward.winner == "benchmark"
    assert parse_arena_verdict("not json at all", framework_is_a=True) is None


def test_run_arena_match_requires_swap_consistency() -> None:
    async def judge_framework_both_directions(system: str, user: str) -> str:
        # 甲 wins in the forward call (framework is 甲), 乙 wins in the backward
        # call (framework is 乙) — i.e. the judge consistently picks framework.
        is_forward = user.index("框架正文") < user.index("真书正文")
        winner = "甲" if is_forward else "乙"
        return json.dumps({"winner": winner, "dimensions": {"hook": winner}}, ensure_ascii=False)

    result = asyncio.run(run_arena_match(_pair(), judge_framework_both_directions))
    assert result.outcome == "win"
    assert result.dimension_outcomes["hook"] == "win"

    async def judge_always_slot_a(system: str, user: str) -> str:
        return json.dumps({"winner": "甲", "dimensions": {}}, ensure_ascii=False)

    # A position-biased judge (always picks slot 甲) must produce a tie.
    result = asyncio.run(run_arena_match(_pair(), judge_always_slot_a))
    assert result.outcome == "tie"

    async def judge_garbage(system: str, user: str) -> str:
        return "服务器繁忙"

    result = asyncio.run(run_arena_match(_pair(), judge_garbage))
    assert result.outcome == "tie"


def test_summarize_and_evaluate_targets() -> None:
    async def judge_framework(system: str, user: str) -> str:
        is_forward = user.index("框架正文") < user.index("真书正文")
        winner = "甲" if is_forward else "乙"
        return json.dumps({"winner": winner, "dimensions": {}}, ensure_ascii=False)

    async def judge_benchmark(system: str, user: str) -> str:
        is_forward = user.index("框架正文") < user.index("真书正文")
        winner = "乙" if is_forward else "甲"
        return json.dumps({"winner": winner, "dimensions": {}}, ensure_ascii=False)

    async def _collect() -> list:
        results = []
        for i in range(8):
            judge = judge_framework if i < 4 else judge_benchmark
            results.append(await run_arena_match(_pair(f"p{i}", tier="t2"), judge))
        return results

    results = asyncio.run(_collect())
    summary = summarize_arena(results, tier="t2")
    assert summary.pairs == 8
    assert summary.wins == 4 and summary.losses == 4
    assert summary.win_rate == 0.5

    targets = BenchmarkTargets(
        vs_t2_win_rate_min=0.5, vs_t1_win_rate_min=0.35, min_pairs_per_tier=8
    )
    evaluation = evaluate_targets({"t2": summary}, targets)
    # t2 passes its floor; t1 has no pairs → inconclusive but does not gate.
    assert evaluation.passed is True
    assert evaluation.details["t2"]["status"] == "pass"
    assert evaluation.details["t1"]["status"] == "inconclusive"

    # No tier with enough evidence → overall FAIL (never a silent pass).
    evaluation = evaluate_targets({}, targets)
    assert evaluation.passed is False


def test_load_benchmark_targets_from_repo_config_and_fallback(tmp_path: Path) -> None:
    targets = load_benchmark_targets()  # repo config/benchmark_targets.yaml
    assert targets.vs_t2_win_rate_min == 0.50
    assert targets.vs_t1_win_rate_min == 0.35
    fallback = load_benchmark_targets(tmp_path / "absent.yaml")
    assert fallback.vs_t2_win_rate_min == 0.50


def test_independent_judge_adapter_stays_advisory_and_maps_blind_winner() -> None:
    result = SimpleNamespace(
        status="decisive",
        winner="draft_a",
        advisory_only=True,
        dimension_outcomes={"reader_pull": "draft_a"},
    )

    adapted = adapt_independent_judge_result(_pair(), result)

    assert adapted.outcome == "win"
    assert adapted.forward is None and adapted.backward is None
