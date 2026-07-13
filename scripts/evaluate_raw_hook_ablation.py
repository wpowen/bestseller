"""A/B/C ablation for raw novel hooks without creating or planning a book.

Arms are intentionally incremental:

* minimal: only person + abnormal situation;
* methodology: adds one sustained-choice principle;
* guarded: adds visible action and rejects arbitrary ability costs;
* enhanced: current production-oriented raw-idea context.

All candidates are anonymized, shuffled, and scored by the same independent
judge model. The artifact is evidence for prompt selection, not a production
gate by itself.
"""

# ruff: noqa: E402, I001 -- bootstrap src before project imports.

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import UTC, datetime
import json
from pathlib import Path
import random
import statistics
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bestseller.infra.db.session import session_scope
from bestseller.services.concept_tournament import (
    _build_raw_idea_pool_messages,
    _default_generator,
    _parse_json_object,
    _parse_raw_idea_records,
    load_concept_tournament_config,
)
from bestseller.settings import load_settings

ARMS = (
    "minimal",
    "methodology",
    "consequence",
    "author_pitch",
    "guarded",
    "enhanced",
)
DEFAULT_CASES = (
    ("xianxia", "仙侠", "古典仙侠"),
    ("urban", "都市", "都市异能"),
    ("history", "历史", "架空历史"),
)
SCORE_AXES = (
    "freshness",
    "click",
    "character_logic",
    "story_motion",
    "promise_survival",
    "genre_fidelity",
    "plain_language",
)


def _model_family(model_key: object) -> str:
    normalized = str(model_key or "unknown").strip().lower()
    for family in ("minimax", "deepseek", "qwen", "openai", "anthropic", "nvidia", "mimo"):
        if family in normalized:
            return family
    return normalized.split("-", 1)[0] or "unknown"
CALIBRATION_HOOKS = (
    {
        "id": "cal-strong-a",
        "seed": "一名遗体收殓师，每替一个枉死者完成收尸，就会继承死者原本应该拥有的未来；有人本该暴富，有人本该结婚，还有人本该在七天后杀死他。",
    },
    {
        "id": "cal-strong-b",
        "seed": "一名遗体收殓师，每替一个枉死者完成收尸，就会继承死者原本应该拥有的未来；有人本该暴富，有人本该结婚，还有人本该在七天后杀死他。",
    },
    {
        "id": "cal-weak",
        "seed": "一个替两家宗门核账的记账先生，长期在双方之间核账、藏私、调停，每结清一季旧账，就会被新的账目卷得更深。",
    },
    {
        "id": "cal-dumb-cost",
        "seed": "一个送葬人每钉下一枚往生钉就离死亡更近一步，必须在寿命耗尽前找出黑棺的秘密。",
    },
)


def _parse_case(value: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in value.split("|")]
    if len(parts) != 3 or not all(parts):
        raise argparse.ArgumentTypeError("case must be id|genre|sub_genre")
    return parts[0], parts[1], parts[2]


def _build_judge_messages(
    *, genre: str, sub_genre: str, candidates: list[dict[str, str]]
) -> tuple[str, str]:
    system = (
        "你是极其苛刻的商业长篇选题编辑。候选来源已匿名，不猜提示词版本，不替候选补"
        "设定，不因表达更长或名词更多加分。逐项独立评分，只输出JSON。"
    )
    user = (
        f"题材={genre}（{sub_genre}）\n候选={json.dumps(candidates, ensure_ascii=False)}\n\n"
        "逐项评分0-10：freshness 核心组合是否新；click 是否让目标读者想点；"
        "character_logic 正常聪明人是否会这样行动；story_motion 是否已经看见主角会做什么；"
        "promise_survival 开局异常解释后是否仍有同一故事可写；genre_fidelity 是否由题材"
        "原生身份、资源、关系和行动成立；plain_language 是否一遍读懂。另给ai_assembly"
        "0-10，越高越像为了新奇拼装名词、器官、残魂、天道、收费代价；dumb_cost=true"
        "表示依赖按次折寿、失忆、伤身、扣身份/人格/命数或无因果惩罚。若只是一次性谜底、"
        "重复接单或换人换地，promise_survival不得超过4；若主角行为只是作者强迫，"
        "character_logic不得超过4；若换掉题材名词仍成立，genre_fidelity不得超过5。"
        "只输出JSON：{\"scores\":[{\"id\":\"候选id\",\"freshness\":0-10,"
        "\"click\":0-10,\"character_logic\":0-10,\"story_motion\":0-10,"
        "\"promise_survival\":0-10,\"genre_fidelity\":0-10,"
        "\"plain_language\":0-10,\"ai_assembly\":0-10,\"dumb_cost\":false,"
        "\"reason\":\"40字内具体理由\"}]}"
    )
    return system, user


def _parse_scores(raw: str) -> list[dict[str, Any]]:
    payload = _parse_json_object(raw) or {}
    rows = payload.get("scores")
    if not isinstance(rows, list):
        return []
    parsed: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("id") or "").strip():
            continue
        try:
            scores = {
                axis: max(0.0, min(10.0, float(row.get(axis, 0))))
                for axis in (*SCORE_AXES, "ai_assembly")
            }
        except (TypeError, ValueError):
            continue
        hard_pass = (
            all(scores[axis] >= 7.0 for axis in SCORE_AXES)
            and scores["click"] >= 7.5
            and scores["ai_assembly"] <= 3.0
            and not bool(row.get("dumb_cost"))
        )
        parsed.append(
            {
                "id": str(row["id"]).strip(),
                **scores,
                "dumb_cost": bool(row.get("dumb_cost")),
                "hard_pass": hard_pass,
                "reason": str(row.get("reason") or "").strip(),
            }
        )
    return parsed


def _judge_calibration(scores: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {row["id"]: row for row in scores}
    required = {item["id"] for item in CALIBRATION_HOOKS}
    if not required.issubset(by_id):
        return {"passed": False, "reason": "calibration_scores_incomplete"}
    strong_a = by_id["cal-strong-a"]
    strong_b = by_id["cal-strong-b"]
    weak = by_id["cal-weak"]
    dumb = by_id["cal-dumb-cost"]
    consistency_delta = max(
        abs(float(strong_a[axis]) - float(strong_b[axis]))
        for axis in SCORE_AXES
    )
    checks = {
        "strong_beats_weak_click": strong_a["click"] >= weak["click"] + 1.0,
        "strong_beats_weak_motion": strong_a["story_motion"] >= weak["story_motion"],
        "dumb_cost_detected": bool(dumb["dumb_cost"]),
        "duplicate_consistent": consistency_delta <= 1.0,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "duplicate_max_delta": consistency_delta,
        "scores": scores,
    }


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings()
    config = dict(load_concept_tournament_config())
    generation_model = args.generation_model or config.get("generation_model_key")
    judge_model = args.judge_model or config.get("finalist_judge_model_key")
    cases = tuple(args.case or DEFAULT_CASES)
    rng = random.Random(args.seed)
    results: list[dict[str, Any]] = []
    judge_run_ids: list[str] = []
    calibration: dict[str, Any] = {"passed": False, "reason": "not_run"}

    async with session_scope(settings) as session:
        generator = await _default_generator(
            session,
            settings,
            template="raw_hook_ablation_generation",
            max_tokens=2400,
            logical_role="planner",
            model_catalog_key=generation_model,
        )
        judge = await _default_generator(
            session,
            settings,
            template="raw_hook_ablation_judge",
            max_tokens=args.judge_max_tokens,
            logical_role="critic",
            model_catalog_key=judge_model,
        )
        calibration_system, calibration_user = _build_judge_messages(
            genre="商业类型小说",
            sub_genre="跨题材点击力校准",
            candidates=[dict(item) for item in CALIBRATION_HOOKS],
        )
        calibration_raw, calibration_run_id = await judge(
            calibration_system, calibration_user
        )
        if calibration_run_id is not None:
            judge_run_ids.append(str(calibration_run_id))
        calibration = _judge_calibration(_parse_scores(calibration_raw))
        for case_id, genre, sub_genre in cases:
            anonymized: list[dict[str, str]] = []
            lookup: dict[str, dict[str, str]] = {}
            generation_runs: list[str] = []
            focuses = [
                str(value).strip()
                for value in (config.get("raw_idea_batch_focuses") or [])
                if str(value).strip()
            ]
            selected_arms = tuple(args.arm or ARMS)
            for arm in selected_arms:
                ideas: list[dict[str, Any]] = []
                seen_seeds: set[str] = set()
                batch_size = max(1, min(args.generation_batch_size, args.count))
                for batch_index, start in enumerate(range(0, args.count, batch_size)):
                    count = min(batch_size, args.count - start)
                    focus_hint = (
                        focuses[batch_index % len(focuses)] if focuses else ""
                    )
                    system, user = _build_raw_idea_pool_messages(
                        genre=genre,
                        sub_genre=sub_genre,
                        count=count,
                        prompt_arm=arm,
                        focus_hint=focus_hint,
                    )
                    raw, run_id = await generator(system, user)
                    if run_id is not None:
                        generation_runs.append(str(run_id))
                    for record in _parse_raw_idea_records(raw, limit=count):
                        seed = str(record["seed"])
                        normalized_seed = "".join(seed.split())
                        if normalized_seed in seen_seeds:
                            continue
                        seen_seeds.add(normalized_seed)
                        ideas.append(record)
                for index, record in enumerate(ideas):
                    lane = str(record["lane"])
                    seed = str(record["seed"])
                    candidate_id = f"{case_id}-{arm}-{index}"
                    lookup[candidate_id] = {
                        "case_id": case_id,
                        "genre": genre,
                        "sub_genre": sub_genre,
                        "arm": arm,
                        "lane": lane,
                        "seed": seed,
                        "opening": str(record.get("opening") or ""),
                        "why_it_keeps_moving": str(
                            record.get("why_it_keeps_moving") or ""
                        ),
                        "future_situations": list(
                            record.get("future_situations") or []
                        ),
                    }
                    anonymized.append({"id": candidate_id, "seed": seed})

            rng.shuffle(anonymized)
            score_by_id: dict[str, dict[str, Any]] = {}
            for start in range(0, len(anonymized), args.judge_batch_size):
                batch = anonymized[start : start + args.judge_batch_size]
                system, user = _build_judge_messages(
                    genre=genre, sub_genre=sub_genre, candidates=batch
                )
                raw, run_id = await judge(system, user)
                if run_id is not None:
                    judge_run_ids.append(str(run_id))
                for score in _parse_scores(raw):
                    score_by_id[score["id"]] = score

            for candidate_id, candidate in lookup.items():
                results.append(
                    {
                        **candidate,
                        "score": score_by_id.get(candidate_id),
                    }
                )
            for result in results:
                if result["case_id"] == case_id:
                    result["generation_run_ids"] = generation_runs

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        grouped[row["arm"]].append(row)
    metrics: dict[str, Any] = {}
    for arm, rows in grouped.items():
        scored = [row for row in rows if isinstance(row.get("score"), dict)]
        metrics[arm] = {
            "generated": len(rows),
            "scored": len(scored),
            "hard_pass_count": sum(bool(row["score"]["hard_pass"]) for row in scored),
            "hard_pass_rate": (
                sum(bool(row["score"]["hard_pass"]) for row in scored) / len(scored)
                if scored
                else 0.0
            ),
            "axis_means": {
                axis: round(
                    statistics.mean(float(row["score"][axis]) for row in scored), 3
                )
                if scored
                else 0.0
                for axis in (*SCORE_AXES, "ai_assembly")
            },
        }

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "generation_model": generation_model,
        "judge_model": judge_model,
        "judge_independence": {
            "generation_family": _model_family(generation_model),
            "judge_family": _model_family(judge_model),
            "status": (
                "same_model_self_judge"
                if generation_model == judge_model
                else "cross_family"
                if _model_family(generation_model) != _model_family(judge_model)
                else "same_family_provisional"
            ),
        },
        "count_per_arm_case": args.count,
        "generation_batch_size": args.generation_batch_size,
        "cases": [
            {"id": case_id, "genre": genre, "sub_genre": sub_genre}
            for case_id, genre, sub_genre in cases
        ],
        "metrics": metrics,
        "judge_calibration": calibration,
        "results": results,
        "judge_run_ids": judge_run_ids,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(output.resolve())
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", type=_parse_case)
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--arm", action="append", choices=ARMS)
    parser.add_argument("--generation-batch-size", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument("--generation-model")
    parser.add_argument("--judge-model")
    parser.add_argument("--judge-batch-size", type=int, default=6)
    parser.add_argument("--judge-max-tokens", type=int, default=4500)
    parser.add_argument(
        "--output",
        default="output/model-comparison/hook-pipeline/raw-hook-ablation.json",
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
