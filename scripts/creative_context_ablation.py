"""Incremental novel-concept prompt ablation with strict blind pairwise judging.

The experiment deliberately starts from a one-line prompt and adds one capability
at a time. It does not call the production conception pipeline. That makes every
increment attributable and prevents old framework context from leaking into the
new baseline.

Example:
    .venv/bin/python scripts/creative_context_ablation.py --samples 2
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
from itertools import pairwise
import json
import os
from pathlib import Path
import random
import re
import time
from typing import Any

from dotenv import load_dotenv
import litellm

# Chinese prompt text intentionally uses full-width punctuation. The experiment
# also handles LiteLLM's provider-neutral response objects and uses seeded
# pseudo-randomness only for anonymous display order.
# ruff: noqa: RUF001, ANN401, S311


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "output" / "creative-context-ablation"
MODEL = "openai/MiniMax-M3"
API_BASE = "https://api.minimaxi.com/v1"

SYSTEM_BASE = (
    "你是一位真正懂长篇中文网络小说的作者。完整思考人物、世界、因果、情绪和连载潜力，"
    "写出一个具体、可信、让人想读的原创小说创意。不要讨论写作方法，不要评价自己的答案。"
    "总长度控制在1200至1800个汉字；宁可删去装饰性设定，也要保住完整因果和关键故事事实。"
)

STORY_PACKAGE_CONTEXT = """
在不套公式的前提下，把创意写成一个完整故事包。读者应能看清：
- 一句话钩子；
- 主角的具体身份、私人欲望与缺陷；
- 核心异常/能力的可执行规则、收益、限制与代价；
- 第一场迫使主角行动的危机；
- 一个具体对手或对抗系统；
- 能持续升级但不机械重复的长篇故事引擎；
- 最终必须回答的终局问题。
这些部分必须来自同一条因果链，不要把互不相关的热词拼在一起。
""".strip()

DECISION_CONTEXT = """
只在内部审计重大选择，绝对不要输出“内部审计”段落、替代方案清单或写作说明，把审计结论直接融入处境：
如果你就是主角，在他当时确实知道的信息、资源、性格和风险下，
你会怎么做？至少比较一个更安全、更便宜或更直接的替代方案。若仍选择高风险行动，必须由时间压力、
信息边界、价值观、不可替代收益或对手封锁证明这是当时的局部最优解。对手也必须像聪明人一样应对。
""".strip()

# Compact expansion of the selected bestseller-brainhole-engine Skill. It keeps
# the quality principle while deliberately omitting its ten-field chapter schema.
SELECTED_BRAINHOLE_SKILL = """
【本次只选择一个脑洞 Skill】
脑洞要做到“第一眼意外，解释后必然”。新奇点必须压到人物核心与现实行动上，并真实改变关系、资源、
暴露风险、制度压力或未来选择；不能只是职业名词与修仙名词的表面碰撞。主角只能使用当前身份和成长阶段
已经获得的能力或权力，不能靠作者临时授权。若去掉这个脑洞，主线应当明显无法成立。
""".strip()

REVISION_SYSTEM = (
    "你是极其苛刻的小说总编。先在内部找出候选最致命的一处问题，再整体重写。"
    "只保留真正有价值的故事核，不得追加第二套设定，不得输出诊断过程。"
    "成品控制在1200至1800个汉字，优先保住完整因果和关键故事事实。"
)

REVISION_CONTEXT = """
重写时只检查五件事：钩子是否具体且不可替换；因果是否闭合；主角与对手是否聪明；
长篇引擎是否会变化升级；情绪承诺是否明确。发现明显更安全的选择就重构处境，而不是补一句“别无选择”。
输出一份完整的新版本，不要逐条点评。
""".strip()

JUDGE_SYSTEM = (
    "你是非常苛刻的网络小说总编，只负责二选一盲评。你不奖励格式齐全、术语多、篇幅长或规则复杂；"
    "你只奖励真正更想读、更自洽、人物更聪明、能写成长篇的故事。若两者都弱或差异不足，必须判 TIE。"
    "只输出一个合法 JSON 对象。"
)

JUDGE_TEMPLATE = """
在不知道来源的情况下比较方案 A 与方案 B。先检查硬伤：
1. 钩子是否是可套给很多书的空公式；
2. 主角是否无视显而易见的安全/低成本方案；
3. 能力、代价、冲突是否没有因果关系；
4. 新奇是否只是名词拼贴；
5. 长篇是否只能机械重复同一动作。

然后各按 1-10 分评价五维：click_and_freshness、causal_coherence、character_intelligence、
serial_engine、emotional_promise。winner 只能是 A、B 或 TIE。理由必须指出决定胜负的具体故事事实，
不得说“更完整”“更有吸引力”这种空话。

输出格式：
{{"winner":"A|B|TIE","confidence":0.0,"a_scores":{{"click_and_freshness":0,"causal_coherence":0,
"character_intelligence":0,"serial_engine":0,"emotional_promise":0}},"b_scores":{{"click_and_freshness":0,
"causal_coherence":0,"character_intelligence":0,"serial_engine":0,"emotional_promise":0}},
"a_hard_rejections":[],"b_hard_rejections":[],"decisive_reason":""}}

【方案 A】
{a}

【方案 B】
{b}
""".strip()

TASKS = {
    "generic_xianxia": "帮我生成一个适合长篇连载的仙侠网络小说创意。",
    "occupational_supernatural": (
        "帮我生成一个现代职业与死亡/亡者有关的长篇悬疑奇幻小说创意，"
        "能力必须和死者未完成的人生有关。"
    ),
    "urban_relationship": (
        "帮我生成一个以成年人关系选择为核心、兼具强情节和职业质感的都市长篇小说创意。"
    ),
}

ARMS = ("L0_bare", "L1_story_package", "L2_decision", "L3_selected_skill", "L4_one_revision")
SCORE_KEYS = (
    "click_and_freshness",
    "causal_coherence",
    "character_intelligence",
    "serial_engine",
    "emotional_promise",
)


@dataclass(slots=True)
class Candidate:
    candidate_id: str
    task: str
    arm: str
    sample: int
    prompt: str
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    status: str = "VALID"
    error: str = ""
    finish_reason: str = ""


@dataclass(slots=True)
class Verdict:
    task: str
    sample: int
    lower_arm: str
    higher_arm: str
    order: str
    status: str
    winner_arm: str | None
    payload: dict[str, Any] | None
    raw: str


def _message_text(response: Any) -> str:
    message = response.choices[0].message
    value = getattr(message, "content", None) or getattr(message, "reasoning_content", None) or ""
    return str(value).strip()


def _usage_value(response: Any, name: str) -> int:
    usage = getattr(response, "usage", None)
    return int(getattr(usage, name, 0) or 0)


def _prompt_for(task_prompt: str, arm: str) -> str:
    blocks = [task_prompt]
    if arm in {"L1_story_package", "L2_decision", "L3_selected_skill"}:
        blocks.append(STORY_PACKAGE_CONTEXT)
    if arm in {"L2_decision", "L3_selected_skill"}:
        blocks.append(DECISION_CONTEXT)
    if arm == "L3_selected_skill":
        blocks.append(SELECTED_BRAINHOLE_SKILL)
    return "\n\n".join(blocks)


async def _complete(
    *,
    api_key: str,
    system: str,
    user: str,
    temperature: float,
    max_tokens: int,
    semaphore: asyncio.Semaphore,
) -> tuple[str, int, int, int, str]:
    started = time.perf_counter()
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            async with semaphore:
                response = await litellm.acompletion(
                    model=MODEL,
                    api_base=API_BASE,
                    api_key=api_key,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=180,
                )
            text = _message_text(response)
            if text:
                return (
                    text,
                    _usage_value(response, "prompt_tokens"),
                    _usage_value(response, "completion_tokens"),
                    int((time.perf_counter() - started) * 1000),
                    str(getattr(response.choices[0], "finish_reason", "") or ""),
                )
            last_error = RuntimeError("provider returned an empty response")
        except Exception as exc:
            last_error = exc
        if attempt < 2:
            await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM call failed after 3 attempts: {last_error}")


async def _generate_candidate(
    *,
    api_key: str,
    task_name: str,
    task_prompt: str,
    arm: str,
    sample: int,
    semaphore: asyncio.Semaphore,
    prior: Candidate | None = None,
) -> Candidate:
    if arm == "L4_one_revision":
        if prior is None:
            raise ValueError("L4 requires the matching L3 candidate")
        prompt = f"{REVISION_CONTEXT}\n\n【待重写候选】\n{prior.text}"
        system = REVISION_SYSTEM
        if prior.status != "VALID":
            return Candidate(
                candidate_id=f"blocked_{task_name}_{sample}",
                task=task_name,
                arm=arm,
                sample=sample,
                prompt=prompt,
                text="",
                input_tokens=0,
                output_tokens=0,
                latency_ms=0,
                status="BLOCKED",
                error="matching L3 candidate was invalid",
                finish_reason="blocked",
            )
    else:
        prompt = _prompt_for(task_prompt, arm)
        system = SYSTEM_BASE
    try:
        text, input_tokens, output_tokens, latency_ms, finish_reason = await _complete(
            api_key=api_key,
            system=system,
            user=prompt,
            temperature=0.82,
            max_tokens=12000,
            semaphore=semaphore,
        )
        status = "VALID"
        error = ""
    except Exception as exc:
        # Infrastructure failures are retained as invalid pass@1 samples. One
        # failed provider call must not erase every completed comparison.
        text = ""
        input_tokens = 0
        output_tokens = 0
        latency_ms = 0
        status = "INVALID"
        error = str(exc)
        finish_reason = "error"
    digest = hashlib.sha256(f"{task_name}|{arm}|{sample}|{text}".encode()).hexdigest()[:12]
    return Candidate(
        candidate_id=f"c_{digest}",
        task=task_name,
        arm=arm,
        sample=sample,
        prompt=prompt,
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        status=status,
        error=error,
        finish_reason=finish_reason,
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    # Search from the first opening brace so nested score objects cannot be
    # mistaken for the complete verdict. raw_decode safely ignores code fences
    # and other text after the outer JSON object.
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _valid_verdict(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict) or payload.get("winner") not in {"A", "B", "TIE"}:
        return False
    for side in ("a_scores", "b_scores"):
        scores = payload.get(side)
        if not isinstance(scores, dict):
            return False
        for key in SCORE_KEYS:
            try:
                value = float(scores.get(key))
            except (TypeError, ValueError):
                return False
            if value < 1 or value > 10:
                return False
    return bool(str(payload.get("decisive_reason") or "").strip())


def _reparse_verdict(verdict: Verdict) -> Verdict:
    payload = _extract_json(verdict.raw)
    if not _valid_verdict(payload):
        verdict.status = "INVALID"
        verdict.winner_arm = None
        verdict.payload = payload
        return verdict
    winner = str(payload["winner"])
    a_arm, b_arm = (
        (verdict.higher_arm, verdict.lower_arm)
        if verdict.order == "swapped"
        else (verdict.lower_arm, verdict.higher_arm)
    )
    verdict.status = "VALID"
    verdict.winner_arm = None if winner == "TIE" else (a_arm if winner == "A" else b_arm)
    verdict.payload = payload
    return verdict


async def _judge_pair(
    *,
    api_key: str,
    lower: Candidate,
    higher: Candidate,
    swapped: bool,
    semaphore: asyncio.Semaphore,
) -> Verdict:
    if lower.status != "VALID" or higher.status != "VALID":
        return Verdict(
            task=lower.task,
            sample=lower.sample,
            lower_arm=lower.arm,
            higher_arm=higher.arm,
            order="swapped" if swapped else "normal",
            status="INVALID",
            winner_arm=None,
            payload=None,
            raw="candidate generation failed",
        )
    a, b = (higher, lower) if swapped else (lower, higher)
    prompt = JUDGE_TEMPLATE.format(a=a.text, b=b.text)
    try:
        raw, _, _, _, _ = await _complete(
            api_key=api_key,
            system=JUDGE_SYSTEM,
            user=prompt,
            temperature=0.1,
            # MiniMax-M3 counts hidden reasoning against this budget. A lower
            # cap truncates otherwise valid JSON and recreates the production
            # gate's false-default failure mode.
            max_tokens=5000,
            semaphore=semaphore,
        )
    except Exception as exc:
        return Verdict(
            task=lower.task,
            sample=lower.sample,
            lower_arm=lower.arm,
            higher_arm=higher.arm,
            order="swapped" if swapped else "normal",
            status="INVALID",
            winner_arm=None,
            payload=None,
            raw=str(exc),
        )
    payload = _extract_json(raw)
    if not _valid_verdict(payload):
        return Verdict(
            task=lower.task,
            sample=lower.sample,
            lower_arm=lower.arm,
            higher_arm=higher.arm,
            order="swapped" if swapped else "normal",
            status="INVALID",
            winner_arm=None,
            payload=payload,
            raw=raw,
        )
    winner = str(payload["winner"])
    winner_arm = None if winner == "TIE" else (a.arm if winner == "A" else b.arm)
    return Verdict(
        task=lower.task,
        sample=lower.sample,
        lower_arm=lower.arm,
        higher_arm=higher.arm,
        order="swapped" if swapped else "normal",
        status="VALID",
        winner_arm=winner_arm,
        payload=payload,
        raw=raw,
    )


def _pair_summary(verdicts: list[Verdict]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int, str, str], list[Verdict]] = {}
    for verdict in verdicts:
        key = (verdict.task, verdict.sample, verdict.lower_arm, verdict.higher_arm)
        groups.setdefault(key, []).append(verdict)
    rows: list[dict[str, Any]] = []
    for key, items in sorted(groups.items()):
        valid = [item for item in items if item.status == "VALID"]
        winners = [item.winner_arm for item in valid]
        if len(valid) != 2:
            outcome = "INVALID"
        elif winners[0] != winners[1]:
            outcome = "UNSTABLE"
        elif winners[0] is None:
            outcome = "TIE"
        else:
            outcome = str(winners[0])
        rows.append(
            {
                "task": key[0],
                "sample": key[1],
                "lower_arm": key[2],
                "higher_arm": key[3],
                "outcome": outcome,
            }
        )
    return rows


def _report(candidates: list[Candidate], summaries: list[dict[str, Any]]) -> str:
    lines = ["# Creative Context Ablation Report", "", "## Candidate cost", ""]
    lines.append(
        "| task | arm | sample | status | finish | input tokens | output tokens | "
        "latency ms | chars |"
    )
    lines.append("|---|---:|---:|---|---|---:|---:|---:|---:|")
    for item in sorted(
        candidates,
        key=lambda value: (value.task, value.sample, ARMS.index(value.arm)),
    ):
        lines.append(
            f"| {item.task} | {item.arm} | {item.sample} | {item.status} | "
            f"{item.finish_reason} | {item.input_tokens} | "
            f"{item.output_tokens} | {item.latency_ms} | {len(item.text)} |"
        )
    lines.extend(["", "## Strict position-swapped adjacent comparisons", ""])
    lines.append("| task | sample | comparison | outcome |")
    lines.append("|---|---:|---|---|")
    for row in summaries:
        lines.append(
            f"| {row['task']} | {row['sample']} | {row['lower_arm']} → {row['higher_arm']} | "
            f"{row['outcome']} |"
        )
    lines.extend(["", "## Marginal promotion counts", ""])
    comparisons = list(pairwise(ARMS))
    for lower, higher in comparisons:
        rows = [
            row for row in summaries if row["lower_arm"] == lower and row["higher_arm"] == higher
        ]
        wins = sum(1 for row in rows if row["outcome"] == higher)
        losses = sum(1 for row in rows if row["outcome"] == lower)
        ties = sum(1 for row in rows if row["outcome"] == "TIE")
        unstable = sum(1 for row in rows if row["outcome"] in {"UNSTABLE", "INVALID"})
        lines.append(
            f"- `{lower}` → `{higher}`: wins={wins}, losses={losses}, "
            f"ties={ties}, unstable={unstable}"
        )
    return "\n".join(lines) + "\n"


async def _run(args: argparse.Namespace) -> Path:
    load_dotenv(ROOT / ".env")
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY is unavailable")
    litellm.suppress_debug_info = True
    semaphore = asyncio.Semaphore(args.workers)
    selected_tasks = {name: TASKS[name] for name in args.tasks}
    candidates: list[Candidate] = []
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = Path(args.out) / stamp
    out_dir.mkdir(parents=True, exist_ok=False)

    def checkpoint() -> None:
        (out_dir / "checkpoint-candidates.json").write_text(
            json.dumps([asdict(item) for item in candidates], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if args.resume_candidates:
        candidates.extend(
            Candidate(**item)
            for item in json.loads(args.resume_candidates.read_text(encoding="utf-8"))
        )
        checkpoint()
        print(f"resumed candidates: {len(candidates)}", flush=True)
    else:
        base_jobs = [
            _generate_candidate(
                api_key=api_key,
                task_name=task_name,
                task_prompt=task_prompt,
                arm=arm,
                sample=sample,
                semaphore=semaphore,
            )
            for task_name, task_prompt in selected_tasks.items()
            for sample in range(1, args.samples + 1)
            for arm in ARMS[:-1]
        ]
        generated = []
        for completed, job in enumerate(asyncio.as_completed(base_jobs), start=1):
            candidate = await job
            generated.append(candidate)
            candidates.append(candidate)
            checkpoint()
            print(
                f"base candidate {completed}/{len(base_jobs)}: "
                f"{candidate.task}/{candidate.arm}/{candidate.sample} {candidate.status}",
                flush=True,
            )
        print(f"generated base candidates: {len(generated)}", flush=True)
        l3_by_key = {
            (item.task, item.sample): item
            for item in generated
            if item.arm == "L3_selected_skill"
        }
        revision_jobs = [
            _generate_candidate(
                api_key=api_key,
                task_name=task_name,
                task_prompt=task_prompt,
                arm="L4_one_revision",
                sample=sample,
                semaphore=semaphore,
                prior=l3_by_key[(task_name, sample)],
            )
            for task_name, task_prompt in selected_tasks.items()
            for sample in range(1, args.samples + 1)
        ]
        revisions = []
        for completed, job in enumerate(asyncio.as_completed(revision_jobs), start=1):
            candidate = await job
            revisions.append(candidate)
            candidates.append(candidate)
            checkpoint()
            print(
                f"revision candidate {completed}/{len(revision_jobs)}: "
                f"{candidate.task}/{candidate.sample} {candidate.status}",
                flush=True,
            )
        print(f"generated revision candidates: {len(revisions)}", flush=True)

    candidate_map = {(item.task, item.sample, item.arm): item for item in candidates}
    if args.resume_verdicts:
        verdicts = [
            _reparse_verdict(Verdict(**item))
            for item in json.loads(args.resume_verdicts.read_text(encoding="utf-8"))
        ]
        print(f"reparsed verdicts: {len(verdicts)}", flush=True)
    else:
        judge_jobs = []
        for task_name in selected_tasks:
            for sample in range(1, args.samples + 1):
                for lower_arm, higher_arm in pairwise(ARMS):
                    lower = candidate_map[(task_name, sample, lower_arm)]
                    higher = candidate_map[(task_name, sample, higher_arm)]
                    judge_jobs.extend(
                        [
                            _judge_pair(
                                api_key=api_key,
                                lower=lower,
                                higher=higher,
                                swapped=False,
                                semaphore=semaphore,
                            ),
                            _judge_pair(
                                api_key=api_key,
                                lower=lower,
                                higher=higher,
                                swapped=True,
                                semaphore=semaphore,
                            ),
                        ]
                    )
        verdicts = []
        for completed, job in enumerate(asyncio.as_completed(judge_jobs), start=1):
            verdict = await job
            verdicts.append(verdict)
            (out_dir / "checkpoint-verdicts.json").write_text(
                json.dumps([asdict(item) for item in verdicts], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(
                f"judge {completed}/{len(judge_jobs)}: {verdict.lower_arm}→"
                f"{verdict.higher_arm}/{verdict.order} {verdict.status}",
                flush=True,
            )
    print(f"completed blind judge calls: {len(verdicts)}", flush=True)
    summaries = _pair_summary(verdicts)

    random.Random(args.blind_seed).shuffle(candidates)
    anonymous = {
        f"draft_{index:03d}": {"task": item.task, "text": item.text}
        for index, item in enumerate(candidates, start=1)
    }
    private_map = {
        f"draft_{index:03d}": {
            "candidate_id": item.candidate_id,
            "task": item.task,
            "arm": item.arm,
            "sample": item.sample,
        }
        for index, item in enumerate(candidates, start=1)
    }
    (out_dir / "prompts.json").write_text(
        json.dumps(
            {
                "model": MODEL,
                "tasks": selected_tasks,
                "system": SYSTEM_BASE,
                "contexts": {
                    "story_package": STORY_PACKAGE_CONTEXT,
                    "decision": DECISION_CONTEXT,
                    "selected_brainhole_skill": SELECTED_BRAINHOLE_SKILL,
                    "revision": REVISION_CONTEXT,
                },
                "judge_system": JUDGE_SYSTEM,
                "judge_template": JUDGE_TEMPLATE,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "candidates.json").write_text(
        json.dumps([asdict(item) for item in candidates], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "blind-candidates.json").write_text(
        json.dumps(anonymous, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "blind-map.private.json").write_text(
        json.dumps(private_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "verdicts.json").write_text(
        json.dumps([asdict(item) for item in verdicts], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(_report(candidates, summaries), encoding="utf-8")
    return out_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=2)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--tasks", nargs="+", choices=tuple(TASKS), default=["generic_xianxia"])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--blind-seed", type=int, default=20260710)
    parser.add_argument("--resume-candidates", type=Path)
    parser.add_argument("--resume-verdicts", type=Path)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be >= 1")
    out_dir = asyncio.run(_run(args))
    print(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
