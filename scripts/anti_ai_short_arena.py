#!/usr/bin/env python3
"""Run the bounded 50-call Chinese fiction AI-flavor prompt arena.

The run is isolated from book drafts.  It writes short samples, deterministic
measurements, blind pairwise judgements, and a reviewable report under
``output/anti-ai-short-arena/latest`` by default.
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bestseller.infra.db.session import session_scope
from bestseller.services.anti_ai_short_arena import (
    ShortArenaBrief,
    ShortArenaStrategy,
    aggregate_strategy_metrics,
    build_short_arena_briefs,
    build_short_arena_strategies,
    build_short_writer_system_prompt,
    build_short_writer_user_prompt,
    clean_short_sample,
    compute_acceptance,
    pair_ids,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import AppSettings, load_settings


MAX_LLM_CALLS = 50
WRITER_MODEL_KEY = "minimax-m3"
PRIMARY_JUDGE_MODEL_KEY = "minimax-m3"
SECONDARY_JUDGE_MODEL_KEY = "deepseek-v4-flash"


class CallBudget:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.count = 0
        self._lock = asyncio.Lock()

    async def claim(self) -> int:
        async with self._lock:
            if self.count >= self.maximum:
                raise RuntimeError(f"LLM call budget exhausted at {self.maximum}")
            self.count += 1
            return self.count


async def _complete(
    settings: AppSettings,
    budget: CallBudget,
    semaphore: asyncio.Semaphore,
    *,
    logical_role: str,
    model_catalog_key: str,
    system_prompt: str,
    user_prompt: str,
    fallback_response: str,
    prompt_template: str,
    max_tokens: int,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    call_no = await budget.claim()
    async with semaphore:
        async with session_scope(settings) as session:
            result = await complete_text(
                session,
                settings,
                LLMCompletionRequest(
                    logical_role=logical_role,
                    model_catalog_key=model_catalog_key,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    fallback_response=fallback_response,
                    prompt_template=prompt_template,
                    prompt_version="2026-07-18",
                    max_tokens_override=max_tokens,
                    metadata={**metadata, "arena_call_no": call_no},
                ),
            )
    return {
        "call_no": call_no,
        "content": result.content,
        "provider": result.provider,
        "model_name": result.model_name,
        "latency_ms": result.latency_ms,
        "finish_reason": result.finish_reason,
        "fallback_used": result.fallback_used,
    }


def _require_real_result(result: Mapping[str, Any], *, label: str) -> None:
    """Keep fallback/mock output out of experiment metrics and acceptance."""

    if bool(result.get("fallback_used")):
        raise RuntimeError(f"{label} used an LLM fallback; arena result is inconclusive")


async def _generate_one(
    settings: AppSettings,
    budget: CallBudget,
    semaphore: asyncio.Semaphore,
    strategy: ShortArenaStrategy,
    brief: ShortArenaBrief,
) -> dict[str, Any]:
    result = await _complete(
        settings,
        budget,
        semaphore,
        logical_role="writer",
        model_catalog_key=WRITER_MODEL_KEY,
        system_prompt=build_short_writer_system_prompt(strategy),
        user_prompt=build_short_writer_user_prompt(brief),
        fallback_response="生成失败，未得到有效正文。",
        prompt_template="anti_ai_short_arena.writer",
        max_tokens=256,
        metadata={"strategy_id": strategy.strategy_id, "brief_id": brief.brief_id},
    )
    _require_real_result(
        result, label=f"writer strategy={strategy.strategy_id} brief={brief.brief_id}"
    )
    result["text"] = clean_short_sample(str(result.pop("content")))
    result["strategy_id"] = strategy.strategy_id
    result["brief_id"] = brief.brief_id
    return result


_JUDGE_SYSTEM = """你是中文类型小说匿名审稿人，只判断哪段正文的 AI 味更低。
AI 味不是语法错误，而是以下可感问题：作者先下结论再补证据；用通用身体微动作代替人物选择；
用“他没做什么”制造克制；解释因果和规则；不同场景都能套用的工整句法；总结收尾。
优先选择：事件先发生、人物选择具体、动作依赖本场物件、后果可见、读者能自行推断的文本。
不得因为更华丽、更长、使用更多感官词就判优。若一段删掉了场景任务或事实，判它任务失真。
只输出 JSON，字段为 winner(A/B/tie)、confidence、reason、a_task_ok、b_task_ok。"""


def _blind_order(
    brief_id: str, left: str, right: str, judge_key: str, judge_round: str
) -> tuple[str, str]:
    digest = hashlib.sha256(
        f"{brief_id}|{left}|{right}|{judge_key}|{judge_round}".encode()
    ).digest()
    return (left, right) if digest[0] % 2 == 0 else (right, left)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip().strip("`")
    try:
        data = json.loads(stripped)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None


async def _judge_pair(
    settings: AppSettings,
    budget: CallBudget,
    semaphore: asyncio.Semaphore,
    *,
    judge_key: str,
    judge_round: str,
    brief: ShortArenaBrief,
    left_id: str,
    right_id: str,
    drafts: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    a_id, b_id = _blind_order(
        brief.brief_id, left_id, right_id, judge_key, judge_round
    )
    user_prompt = (
        "【场景任务】\n"
        + brief.instruction
        + "\n\n【匿名正文 A】\n"
        + drafts[a_id][brief.brief_id]
        + "\n\n【匿名正文 B】\n"
        + drafts[b_id][brief.brief_id]
    )
    result = await _complete(
        settings,
        budget,
        semaphore,
        logical_role="critic",
        model_catalog_key=judge_key,
        system_prompt=_JUDGE_SYSTEM,
        user_prompt=user_prompt,
        fallback_response=(
            '{"winner":"tie","confidence":0,"reason":"judge fallback",'
            '"a_task_ok":false,"b_task_ok":false}'
        ),
        prompt_template="anti_ai_short_arena.pairwise_judge",
        max_tokens=1800,
        metadata={
            "brief_id": brief.brief_id,
            "left_id": left_id,
            "right_id": right_id,
            "blind_a": a_id,
            "blind_b": b_id,
            "judge_key": judge_key,
            "judge_round": judge_round,
        },
    )
    _require_real_result(
        result, label=f"judge model={judge_key} brief={brief.brief_id}"
    )
    parsed = _extract_json_object(str(result.pop("content")))
    if not parsed:
        raise RuntimeError(
            f"judge model={judge_key} brief={brief.brief_id} returned invalid JSON"
        )
    blind_winner = str(parsed.get("winner") or "").strip().upper()
    if blind_winner not in {"A", "B", "TIE"}:
        raise RuntimeError(
            f"judge model={judge_key} brief={brief.brief_id} returned invalid winner"
        )
    winner_id = a_id if blind_winner == "A" else b_id if blind_winner == "B" else "tie"
    return {
        **result,
        "judge_key": judge_key,
        "judge_round": judge_round,
        "brief_id": brief.brief_id,
        "pair": [left_id, right_id],
        "blind_mapping": {"A": a_id, "B": b_id},
        "blind_winner": blind_winner,
        "winner_id": winner_id,
        "confidence": parsed.get("confidence"),
        "reason": str(parsed.get("reason") or "")[:300],
        "a_task_ok": parsed.get("a_task_ok"),
        "b_task_ok": parsed.get("b_task_ok"),
        "parse_valid": True,
    }


def _pairwise_rates(
    verdicts: Iterable[Mapping[str, Any]], strategy_ids: Sequence[str]
) -> dict[str, float]:
    points: Counter[str] = Counter()
    appearances: Counter[str] = Counter()
    for verdict in verdicts:
        left, right = (str(item) for item in verdict["pair"])
        appearances[left] += 1
        appearances[right] += 1
        winner = str(verdict.get("winner_id") or "tie")
        if winner == "tie":
            points[left] += 0.5
            points[right] += 0.5
        elif winner in {left, right}:
            points[winner] += 1.0
    return {
        strategy_id: round(points[strategy_id] / max(1, appearances[strategy_id]), 4)
        for strategy_id in strategy_ids
    }


def _head_to_head_rate(
    verdicts: Iterable[Mapping[str, Any]], winner_id: str, opponent_id: str
) -> float:
    points = 0.0
    total = 0
    for verdict in verdicts:
        pair = {str(item) for item in verdict["pair"]}
        if pair != {winner_id, opponent_id}:
            continue
        total += 1
        judged = str(verdict.get("winner_id") or "tie")
        if judged == winner_id:
            points += 1.0
        elif judged == "tie":
            points += 0.5
    return 0.0 if total == 0 else points / total


def _render_report(manifest: Mapping[str, Any]) -> str:
    strategy_map = {item["strategy_id"]: item for item in manifest["strategies"]}
    lines = [
        "# 中文正文低 AI 味短样本实验报告",
        "",
        f"- 生产写手：`{manifest['models']['writer']}`",
        (
            f"- 匿名判官：`{manifest['models']['primary_judge']}` + "
            f"`{manifest['models']['secondary_judge']}`"
        ),
        f"- LLM 调用：`{manifest['llm_calls']}/{manifest['max_llm_calls']}`",
        f"- 最终方案：`{manifest['winner_id']}`（{strategy_map[manifest['winner_id']]['label']}）",
        f"- 验收：`{'PASS' if manifest['acceptance']['passed'] else 'FAIL'}`",
        "",
        "## 排名",
        "",
        "| 排名 | 策略 | 确定性风险↓ | 主判盲评胜率↑ | 综合分↑ |",
        "|---:|---|---:|---:|---:|",
    ]
    for index, row in enumerate(manifest["rankings"], start=1):
        lines.append(
            f"| {index} | {row['strategy_id']} | {row['deterministic_risk']:.2f} | "
            f"{row['primary_win_rate']:.0%} | {row['composite_score']:.2f} |"
        )
    acceptance = manifest["acceptance"]
    lines.extend(
        [
            "",
            "## 相对生产控制组",
            "",
            f"- 确定性风险改善：`{acceptance['risk_improvement_vs_control']:.1%}`",
            f"- 匿名正面对决胜率：`{acceptance['head_to_head_win_rate_vs_control']:.1%}`",
            f"- 长度通过率：`{acceptance['winner_length_pass_rate']:.0%}`",
            f"- 场景任务覆盖率：`{acceptance['winner_coverage_pass_rate']:.0%}`",
            "",
            "## 同题样例：当前生产规则 vs 最终方案",
            "",
        ]
    )
    winner_id = str(manifest["winner_id"])
    drafts = manifest["drafts_by_strategy"]
    for brief in manifest["briefs"]:
        brief_id = brief["brief_id"]
        lines.extend(
            [
                f"### {brief_id}",
                "",
                "当前生产规则：",
                "",
                f"> {drafts['production_control'][brief_id]}",
                "",
                "最终方案：",
                "",
                f"> {drafts[winner_id][brief_id]}",
                "",
            ]
        )
    lines.extend(
        [
            "## 解释边界",
            "",
            "- 这是当前生产写手上的受控短样本结论，不等于所有模型、所有题材永久成立。",
            "- 确定性指标只抓已知反模式；最终判断同时使用匿名盲评，避免用黑名单冒充文学质量。",
            "- 生产接入后仍需跑章节级回归，因为 70–110 字无法测出长章累积重复和节奏漂移。",
            "",
        ]
    )
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    budget = CallBudget(MAX_LLM_CALLS)
    briefs = build_short_arena_briefs()
    strategies = build_short_arena_strategies()

    generation_tasks = [
        _generate_one(settings, budget, semaphore, strategy, brief)
        for strategy in strategies
        for brief in briefs
    ]
    generated = await asyncio.gather(*generation_tasks)
    drafts_by_strategy: dict[str, dict[str, str]] = defaultdict(dict)
    generation_records: list[dict[str, Any]] = []
    for record in generated:
        drafts_by_strategy[record["strategy_id"]][record["brief_id"]] = record["text"]
        generation_records.append(record)
        draft_path = output_dir / "drafts" / record["strategy_id"] / f"{record['brief_id']}.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(record["text"] + "\n", encoding="utf-8")

    metrics_by_strategy = {
        strategy.strategy_id: aggregate_strategy_metrics(
            drafts_by_strategy[strategy.strategy_id], briefs
        )
        for strategy in strategies
    }
    non_control = sorted(
        (
            strategy.strategy_id
            for strategy in strategies
            if strategy.strategy_id != "production_control"
        ),
        key=lambda strategy_id: float(metrics_by_strategy[strategy_id]["deterministic_risk"]),
    )
    judge_pool = ("production_control", *non_control[:2])

    primary_tasks = [
        _judge_pair(
            settings,
            budget,
            semaphore,
            judge_key=PRIMARY_JUDGE_MODEL_KEY,
            judge_round="primary",
            brief=brief,
            left_id=left_id,
            right_id=right_id,
            drafts=drafts_by_strategy,
        )
        for brief in briefs
        for left_id, right_id in pair_ids(judge_pool)
    ]
    primary_verdicts = await asyncio.gather(*primary_tasks)
    primary_rates = _pairwise_rates(primary_verdicts, judge_pool)
    final_pair = tuple(
        sorted(
            judge_pool,
            key=lambda strategy_id: (
                -primary_rates[strategy_id],
                float(metrics_by_strategy[strategy_id]["deterministic_risk"]),
            ),
        )[:2]
    )
    secondary_tasks = [
        _judge_pair(
            settings,
            budget,
            semaphore,
            judge_key=SECONDARY_JUDGE_MODEL_KEY,
            judge_round="secondary",
            brief=brief,
            left_id=final_pair[0],
            right_id=final_pair[1],
            drafts=drafts_by_strategy,
        )
        for brief in briefs
    ]
    secondary_verdicts = await asyncio.gather(*secondary_tasks)
    secondary_rates = _pairwise_rates(secondary_verdicts, final_pair)

    rankings: list[dict[str, Any]] = []
    for strategy in strategies:
        strategy_id = strategy.strategy_id
        risk = float(metrics_by_strategy[strategy_id]["deterministic_risk"])
        primary_rate = primary_rates.get(strategy_id, 0.0)
        secondary_rate = secondary_rates.get(strategy_id, primary_rate)
        blind_score = 0.7 * primary_rate + 0.3 * secondary_rate
        composite = 0.65 * blind_score * 100.0 + 0.35 * (100.0 - risk)
        rankings.append(
            {
                "strategy_id": strategy_id,
                "deterministic_risk": risk,
                "primary_win_rate": primary_rate,
                "secondary_win_rate": secondary_rate,
                "composite_score": round(composite, 2),
                "was_blind_judged": strategy_id in judge_pool,
            }
        )
    rankings.sort(key=lambda row: (-row["composite_score"], row["deterministic_risk"]))
    winner_id = str(rankings[0]["strategy_id"])
    all_verdicts = [*primary_verdicts, *secondary_verdicts]
    head_to_head = _head_to_head_rate(all_verdicts, winner_id, "production_control")
    acceptance = compute_acceptance(
        winner_id=winner_id,
        metrics_by_strategy=metrics_by_strategy,
        head_to_head_vs_control=head_to_head,
        llm_calls=budget.count,
    )

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "models": {
            "writer": WRITER_MODEL_KEY,
            "primary_judge": PRIMARY_JUDGE_MODEL_KEY,
            "secondary_judge": SECONDARY_JUDGE_MODEL_KEY,
        },
        "max_llm_calls": MAX_LLM_CALLS,
        "llm_calls": budget.count,
        "briefs": [asdict(brief) for brief in briefs],
        "strategies": [asdict(strategy) for strategy in strategies],
        "generation_records": generation_records,
        "drafts_by_strategy": drafts_by_strategy,
        "metrics_by_strategy": metrics_by_strategy,
        "judge_pool": list(judge_pool),
        "final_pair": list(final_pair),
        "primary_verdicts": primary_verdicts,
        "secondary_verdicts": secondary_verdicts,
        "rankings": rankings,
        "winner_id": winner_id,
        "acceptance": acceptance,
        "evidence_integrity": {
            "fallback_count": 0,
            "valid_judgement_count": len(all_verdicts),
            "distinct_judge_models": sorted(
                {PRIMARY_JUDGE_MODEL_KEY, SECONDARY_JUDGE_MODEL_KEY}
            ),
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "report.md").write_text(_render_report(manifest), encoding="utf-8")
    print(json.dumps({"winner_id": winner_id, "acceptance": acceptance}, ensure_ascii=False))
    print(f"manifest: {output_dir / 'manifest.json'}")
    print(f"report: {output_dir / 'report.md'}")
    return 0 if acceptance["passed"] else 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default="output/anti-ai-short-arena/latest",
        help="Experiment output directory.",
    )
    parser.add_argument("--concurrency", type=int, default=3)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
