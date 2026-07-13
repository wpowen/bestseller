"""Compare concept-generation prompt arms without creating a book project.

All model calls flow through ``services.llm.complete_text`` (directly or via
``run_concept_tournament``), so provider routing, traces, retries, and cost
accounting match production.
"""

# ruff: noqa: E402, RUF001

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from itertools import combinations
import json
from pathlib import Path
import random
import statistics
import sys
from time import perf_counter
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bestseller.infra.db.session import session_scope
from bestseller.services.concept_tournament import (
    ConceptCandidate,
    _deterministic_anti_pattern,
    _parse_json_object,
    load_concept_tournament_config,
    run_concept_tournament,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import AppSettings, load_settings

ARMS = ("current", "lean_story_package", "native_baseline", "engine_first")


def _model_family(model_key: object) -> str:
    normalized = str(model_key or "unknown").strip().lower()
    for family in ("minimax", "deepseek", "qwen", "openai", "anthropic", "nvidia", "mimo"):
        if family in normalized:
            return family
    return normalized.split("-", 1)[0] or "unknown"


def _resolve_pairwise_votes(
    *,
    left_arm: str,
    right_arm: str,
    left_full_pass: bool,
    right_full_pass: bool,
    forward_winner: str | None = None,
    reverse_winner: str | None = None,
) -> tuple[str, str]:
    """Resolve a comparison without hiding no-champion failures.

    A missing champion is a production failure, not a sample to discard.  The
    LLM judge is only needed when both arms produced a champion.
    """

    if left_full_pass and not right_full_pass:
        return left_arm, "right_no_champion"
    if right_full_pass and not left_full_pass:
        return right_arm, "left_no_champion"
    if not left_full_pass and not right_full_pass:
        return "both_failed", "both_no_champion"
    if forward_winner == "B" and reverse_winner == "A":
        return right_arm, "consistent_position_swap"
    if forward_winner == "A" and reverse_winner == "B":
        return left_arm, "consistent_position_swap"
    if forward_winner == reverse_winner == "TIE":
        return "tie", "consistent_tie"
    return "invalid", "judge_position_bias_or_invalid"


def _load_suite(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError(f"invalid eval suite: {path}")
    return payload


def _hook_pass(result: dict[str, Any], winner_min: float) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("composite") is not None
        and float(item["composite"]) >= winner_min
        and not str(item.get("rejected_reason") or "").startswith("钩子硬门失败")
        for item in result.get("candidates", [])
    )


def _anti_results(suite: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fixture in suite.get("anti_fixtures") or []:
        if not isinstance(fixture, dict):
            continue
        hook = str(fixture.get("hook") or "")
        actual = _deterministic_anti_pattern(
            ConceptCandidate(
                dimension="anti",
                concept=hook,
                mechanism=hook,
                decision_proof=hook,
            )
        )
        expected = str(fixture.get("expected_rejection") or "")
        rows.append(
            {
                "id": fixture.get("id"),
                "expected": expected,
                "actual": actual,
                "passed": actual == expected,
            }
        )
    return rows


def _winner_text(row: dict[str, Any]) -> str:
    winner = row.get("result", {}).get("winner") or {}
    fields = (
        "concept",
        "mechanism",
        "protagonist_identity",
        "decision_proof",
        "emotional_promise",
        "progress_bar",
        "endgame_direction",
    )
    return "\n".join(f"{field}: {winner.get(field, '')}" for field in fields)


async def _pairwise_vote(
    session: AsyncSession,
    settings: AppSettings,
    *,
    left: dict[str, Any],
    right: dict[str, Any],
    judge_model_key: str | None,
) -> dict[str, Any]:
    system = (
        "你是严苛的网文总编。比较两个同题材故事方案，只看一句话吸引力、人物理性、"
        "因果运动、非重复长篇潜力和题材兑现。字段更多、解释更长不能加分。只输出JSON。"
    )
    user = (
        f"题材：{left['genre']}（{left['sub_genre']}），目标{left['chapters']}章。\n\n"
        f"方案A：\n{_winner_text(left)}\n\n方案B：\n{_winner_text(right)}\n\n"
        "只输出：{\"winner\":\"A|B|tie\",\"reason\":\"不超过80字\"}"
    )
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            system_prompt=system,
            user_prompt=user,
            fallback_response="{}",
            prompt_template="hook_pipeline_pairwise_eval",
            prompt_version="1.0",
            max_tokens_override=240,
            model_catalog_key=judge_model_key,
            metadata={"eval": "long_serial_hook_pipeline", "position_swap": True},
        ),
    )
    parsed = _parse_json_object(completion.content) or {}
    winner = str(parsed.get("winner") or "").upper()
    return {
        "winner": winner if winner in {"A", "B", "TIE"} else "INVALID",
        "reason": str(parsed.get("reason") or ""),
        "llm_run_id": str(completion.llm_run_id) if completion.llm_run_id else None,
    }


async def _run(args: argparse.Namespace) -> int:
    suite = _load_suite(Path(args.cases))
    selected = set(args.case or [])
    cases = [
        case
        for case in suite["cases"]
        if isinstance(case, dict) and (not selected or case.get("id") in selected)
    ]
    if not cases:
        raise ValueError("no eval cases selected")

    settings = load_settings()
    base_config = dict(load_concept_tournament_config())
    if args.generation_model:
        base_config["generation_model_key"] = args.generation_model
    if args.judge_model:
        base_config["judge_model_key"] = args.judge_model
    if args.n_candidates:
        base_config["n_candidates"] = args.n_candidates
    winner_min = float(base_config.get("winner_min", 5.5))
    arms = tuple(args.arm or ARMS)
    runs: list[dict[str, Any]] = []

    async with session_scope(settings) as session:
        for case in cases:
            for repeat in range(args.repeats):
                seed = args.random_seed + repeat
                for arm in arms:
                    config = {**base_config, "candidate_prompt_mode": arm}
                    started = perf_counter()
                    result = await run_concept_tournament(
                        session,
                        settings,
                        genre=str(case.get("genre") or ""),
                        sub_genre=str(case.get("sub_genre") or ""),
                        chapter_count=int(case.get("chapters") or 500),
                        seed_concept=str(case.get("seed") or ""),
                        config=config,
                        rng=random.Random(seed),  # noqa: S311 - reproducible eval sampling
                    )
                    result_payload = result.to_dict()
                    runs.append(
                        {
                            "case_id": case.get("id"),
                            "group": case.get("group"),
                            "genre": case.get("genre"),
                            "sub_genre": case.get("sub_genre"),
                            "chapters": case.get("chapters"),
                            "repeat": repeat + 1,
                            "seed": seed,
                            "arm": arm,
                            "latency_seconds": round(perf_counter() - started, 3),
                            "hook_pass": _hook_pass(result_payload, winner_min),
                            "full_pass": result.winner is not None,
                            "result": result_payload,
                        }
                    )

        pairwise: list[dict[str, Any]] = []
        if not args.no_pairwise and len(arms) >= 2:
            indexed = {(row["case_id"], row["repeat"], row["arm"]): row for row in runs}
            for case in cases:
                for repeat in range(1, args.repeats + 1):
                    for left_arm, right_arm in combinations(arms, 2):
                        left = indexed.get((case.get("id"), repeat, left_arm))
                        right = indexed.get((case.get("id"), repeat, right_arm))
                        if not left or not right:
                            continue
                        forward: dict[str, Any] | None = None
                        reverse: dict[str, Any] | None = None
                        if left["full_pass"] and right["full_pass"]:
                            forward = await _pairwise_vote(
                                session,
                                settings,
                                left=left,
                                right=right,
                                judge_model_key=base_config.get("judge_model_key"),
                            )
                            reverse = await _pairwise_vote(
                                session,
                                settings,
                                left=right,
                                right=left,
                                judge_model_key=base_config.get("judge_model_key"),
                            )
                        resolved, resolution_reason = _resolve_pairwise_votes(
                            left_arm=left_arm,
                            right_arm=right_arm,
                            left_full_pass=bool(left["full_pass"]),
                            right_full_pass=bool(right["full_pass"]),
                            forward_winner=forward["winner"] if forward else None,
                            reverse_winner=reverse["winner"] if reverse else None,
                        )
                        pairwise.append(
                            {
                                "case_id": case.get("id"),
                                "repeat": repeat,
                                "left_arm": left_arm,
                                "right_arm": right_arm,
                                "left_full_pass": left["full_pass"],
                                "right_full_pass": right["full_pass"],
                                "forward": forward,
                                "reverse": reverse,
                                "resolved_winner": resolved,
                                "resolution_reason": resolution_reason,
                            }
                        )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in runs:
        grouped[row["arm"]].append(row)
    metrics: dict[str, Any] = {}
    for arm, rows in grouped.items():
        viable = [row for row in rows if row["group"] == "viable"]
        chars = [row["result"]["candidate_prompt_chars"] for row in rows]
        metrics[arm] = {
            "runs": len(rows),
            "viable_hook_pass_rate": sum(row["hook_pass"] for row in viable) / len(viable)
            if viable
            else 0.0,
            "viable_full_pass_rate": sum(row["full_pass"] for row in viable) / len(viable)
            if viable
            else 0.0,
            "viable_no_champion_rate": sum(not row["full_pass"] for row in viable)
            / len(viable)
            if viable
            else 0.0,
            "median_candidate_prompt_chars": statistics.median(chars) if chars else 0,
            "median_latency_seconds": statistics.median(
                row["latency_seconds"] for row in rows
            ),
        }
    anti = _anti_results(suite)
    payload = {
        "schema_version": "long-serial-hook-eval.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "suite": str(Path(args.cases).resolve()),
        "config": base_config,
        "judge_independence": {
            "generation_family": _model_family(base_config.get("generation_model_key")),
            "judge_family": _model_family(base_config.get("judge_model_key")),
            "finalist_judge_family": _model_family(
                base_config.get("finalist_judge_model_key")
            ),
            "status": (
                "same_model_self_judge"
                if base_config.get("generation_model_key")
                == base_config.get("finalist_judge_model_key")
                else "cross_family"
                if _model_family(base_config.get("generation_model_key"))
                != _model_family(base_config.get("finalist_judge_model_key"))
                else "same_family_provisional"
            ),
        },
        "metrics": metrics,
        "anti": anti,
        "anti_pass_rate": sum(row["passed"] for row in anti) / len(anti) if anti else 0.0,
        "pairwise": pairwise,
        "runs": runs,
    }
    output = Path(args.output) if args.output else ROOT / "output" / "hook-pipeline-eval" / (
        datetime.now().strftime("%Y%m%dT%H%M%S") + ".json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output.resolve())
    print(
        json.dumps(
            {"metrics": metrics, "anti_pass_rate": payload["anti_pass_rate"]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(ROOT / "config" / "hook_eval_cases.yaml"))
    parser.add_argument("--case", action="append", help="case id; repeat to select several")
    parser.add_argument("--arm", action="append", choices=ARMS)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--random-seed", type=int, default=20260711)
    parser.add_argument("--generation-model")
    parser.add_argument("--judge-model")
    parser.add_argument("--n-candidates", type=int)
    parser.add_argument("--no-pairwise", action="store_true")
    parser.add_argument("--output")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
