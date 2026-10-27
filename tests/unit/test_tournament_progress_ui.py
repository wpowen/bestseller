"""Regression tests for concept-tournament progress rendering.

The dashboard receives progress events from a live task, so this contract is
kept at the HTML boundary: it must expose stage-specific progress when the new
payload is present and remain compatible with older index/total-only events.
"""

import json
from pathlib import Path
import subprocess

HTML = Path(__file__).parents[2] / "src" / "bestseller" / "web" / "novel_quickstart.html"


def _source() -> str:
    return HTML.read_text(encoding="utf-8")


def _run_formatter(payload: dict[str, object]) -> str:
    """Execute the browser formatter with Node, keeping the test dependency-free."""

    source = _source()
    start = source.index("function formatConceptTournamentProgress")
    end = source.index("function describeStageEvent", start)
    formatter = source[start:end]
    script = f"""
function fmtShort(seconds) {{ return `${{seconds}}秒`; }}
{formatter}
process.stdout.write(formatConceptTournamentProgress({json.dumps(payload, ensure_ascii=False)}));
"""
    result = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


def test_tournament_stage_label_does_not_claim_a_fixed_duration() -> None:
    source = _source()

    assert "concept_tournament_progress: '🏆 概念淘汰赛进行中'" in source
    assert "约 10-20 分钟" not in source


def test_tournament_progress_formats_batch_completion_and_elapsed_time() -> None:
    source = _source()

    helper = source.split("function formatConceptTournamentProgress", 1)[1].split(
        "function describeStageEvent", 1
    )[0]
    for stage in ("raw_idea_pool", "raw_idea_rank"):
        assert stage in helper
    for field in ("status", "completed", "elapsed_seconds"):
        assert f"payload.{field}" in helper
    assert "已完成 ${done}/${Math.floor(total)}" in helper
    assert "已耗时 ${fmtShort(Math.floor(elapsed))}" in helper


def test_legacy_index_events_are_displayed_as_current_work_not_completion() -> None:
    source = _source()

    helper = source.split("function formatConceptTournamentProgress", 1)[1].split(
        "function describeStageEvent", 1
    )[0]
    assert "正在处理第 ${Math.floor(index) + 1}/${Math.floor(total)} 个" in helper
    assert "Do not turn index into a false completed count" in helper
    assert "conceptEvent.payload" in source
    assert "formatConceptTournamentProgress(conceptEvent.payload || {})" in source


def test_runtime_formatter_uses_explicit_completed_count_for_out_of_order_events() -> None:
    text = _run_formatter(
        {
            "stage": "raw_idea_rank",
            "status": "started",
            "index": 3,
            "completed": 1,
            "total": 4,
            "elapsed_seconds": 61,
        }
    )

    assert "筛选创意池" in text
    assert "已完成 1/4" in text
    assert "第 4/4 个" not in text
    assert "已耗时 61秒" in text


def test_runtime_formatter_handles_topup_and_null_or_missing_optional_fields() -> None:
    topup = _run_formatter(
        {"stage": "raw_idea_pool_topup", "status": "completed", "total": 1, "completed": 1}
    )
    assert "补充创意池" in topup
    assert "已完成 1/1" in topup

    nulls = _run_formatter(
        {
            "stage": "raw_idea_pool",
            "status": "completed",
            "total": None,
            "completed": None,
            "elapsed_seconds": None,
        }
    )
    assert "本阶段已完成" in nulls
    assert nulls.count("本阶段已完成") == 1
    assert "已完成 0/" not in nulls
    assert "已耗时" not in nulls

    legacy = _run_formatter({"stage": "engine_kernel", "status": "started", "index": 0, "total": 2})
    assert "正在处理第 1/2 个" in legacy
    assert "已完成 0/2" not in legacy


def test_causal_review_progress_names_review_and_batch_unit() -> None:
    text = _run_formatter({
        "stage": "raw_idea_rank_causal_review", "status": "failed",
        "index": 0, "total": 1, "completed": 0,
    })
    assert "补验评审依据" in text
    assert "0/1 批" in text
    assert "本批失败" in text
    assert "raw_idea_rank_causal_review" not in text
