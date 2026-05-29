#!/usr/bin/env python
"""Run an A/B pilot for methodology-v2 hook-ledger integration."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from bestseller.domain.narrative import ChapterContractRead
from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
from bestseller.services.hook_ledger import HookLedgerAudit, run_hook_ledger_audit
from bestseller.services.hook_ledger_runtime import (
    hook_ledger_audit_to_dict,
    merge_hook_ledger_audit_into_chapter_review,
    render_hook_ledger_planner_contract,
)
from bestseller.services.llm import (
    LLMCompletionRequest,
    LLMCompletionResult,
    complete_text,
)
from bestseller.settings import (
    apply_runtime_llm_profile,
    load_settings,
    runtime_llm_profile_payload,
    set_runtime_llm_profile,
)


@dataclass(frozen=True)
class SyntheticClue:
    clue_code: str
    clue_type: str = "foreshadow"
    label: str = ""
    planted_in_chapter_number: int | None = None
    expected_payoff_by_chapter_number: int | None = None
    actual_paid_off_chapter_number: int | None = None
    status: str = "planted"
    metadata_json: dict[str, Any] | None = None


@dataclass(frozen=True)
class ChapterLedgerResult:
    chapter_number: int
    plant_count: int
    resolve_count: int
    active_count: int
    closure_rate: float
    finding_codes: tuple[str, ...]
    rewrite_required: bool


@dataclass(frozen=True)
class VariantResult:
    variant: str
    methodology_v2_enabled: bool
    planner_contract_injected: bool
    parse_ok: bool
    fallback_used: bool
    chapters_count: int
    plant_coverage: float
    resolve_coverage_after_ch1: float
    balance_pass_rate: float
    active_count_pass_rate: float
    next_seed_pass_rate: float
    rewrite_required_rate: float
    avg_closure_rate: float
    finding_count: int
    ledger_quality_score: float
    output_dir: str


class _TelemetryOnlySession:
    def add(self, _obj: object) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    def in_nested_transaction(self) -> bool:
        return False


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _now_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


@contextmanager
def _methodology_v2_flag(enabled: bool) -> Iterator[None]:
    original = os.environ.get("BESTSELLER_METHODOLOGY_V2")
    if enabled:
        os.environ["BESTSELLER_METHODOLOGY_V2"] = "1"
    else:
        os.environ.pop("BESTSELLER_METHODOLOGY_V2", None)
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("BESTSELLER_METHODOLOGY_V2", None)
        else:
            os.environ["BESTSELLER_METHODOLOGY_V2"] = original


def _build_planner_prompt(*, methodology_v2: bool) -> str:
    with _methodology_v2_flag(methodology_v2):
        ledger_contract = render_hook_ledger_planner_contract(language="zh-CN")
    contract_section = (
        f"\n\n{ledger_contract}\n"
        if ledger_contract
        else "\n\n本轮不提供方法论 v2 钩子账本合同，只按常规章节规划输出。\n"
    )
    return f"""你是长篇商业小说的章节规划器。请为一个 4 章小样本输出章节合同 JSON，用于 A/B 测试。

题材：都市悬疑 + 轻玄幻。
主角：林渊，民间诊所医生，父亲死于旧账组织。
主线：雨夜铜钱牵出旧账组织，林渊必须保护证人并找到父亲死亡真相。
结构要求：
- 第 1 章打开冲突；
- 第 2 章推进调查并给一个阶段性发现；
- 第 3 章让旧线索兑现或反转；
- 第 4 章完成一次小闭环，同时留下下一轮压力。
- 每章都要有 closing_hook。
{contract_section}
只输出 JSON，不要 markdown，不要解释。格式必须是：
{{
  "chapters": [
    {{
      "chapter_number": 1,
      "contract_summary": "...",
      "core_conflict": "...",
      "information_release": "...",
      "closing_hook": "...",
      "hooks_to_resolve": [],
      "hooks_to_plant": ["..."]
    }}
  ]
}}
"""


def _fallback_plan(*, methodology_v2: bool) -> str:
    if methodology_v2:
        return json.dumps(
            {
                "chapters": [
                    {
                        "chapter_number": 1,
                        "contract_summary": "林渊收下缺角铜钱，发现父亲旧案被重新激活。",
                        "core_conflict": "证人被旧账组织追杀，林渊必须决定是否介入。",
                        "information_release": "缺角铜钱与父亲遗物属于同一套钱印。",
                        "closing_hook": "诊所门缝里伸进第二枚完整铜钱。",
                        "hooks_to_resolve": [],
                        "hooks_to_plant": [
                            "缺角铜钱为什么能写出林渊的名字",
                            "父亲公交票终点站被刮掉",
                            "黑车里的人知道证人的爷爷已经死了",
                        ],
                    },
                    {
                        "chapter_number": 2,
                        "contract_summary": "林渊追到地下车库，救下证人并确认旧账组织仍在收命。",
                        "core_conflict": "林渊要拿到账册残页，但车库阵印会夺走他的掌纹。",
                        "information_release": "公交票终点指向 B2-17，父亲曾查到这里。",
                        "closing_hook": "账册残页出现林家旧宅的地址。",
                        "hooks_to_resolve": [
                            "父亲公交票终点站被刮掉",
                            "黑车里的人知道证人的爷爷已经死了",
                        ],
                        "hooks_to_plant": [
                            "林家旧宅地址为何藏在账册残页里",
                            "林渊掌心牙印开始扩散",
                        ],
                    },
                    {
                        "chapter_number": 3,
                        "contract_summary": "林渊回旧宅，发现父亲不是欠债人而是曾经的记账人。",
                        "core_conflict": "母亲留下的封柜会暴露真相，也会引来收账人。",
                        "information_release": "完整钱印需要活人牙齿补齐。",
                        "closing_hook": "父亲录音里叫出林渊身边证人的本名。",
                        "hooks_to_resolve": [
                            "缺角铜钱为什么能写出林渊的名字",
                            "林家旧宅地址为何藏在账册残页里",
                        ],
                        "hooks_to_plant": [
                            "证人的本名为什么在父亲录音里",
                            "父亲作为记账人是否背叛过受害者",
                        ],
                    },
                    {
                        "chapter_number": 4,
                        "contract_summary": "林渊利用缺角钱印破坏小账本，救下证人但引出主账。",
                        "core_conflict": "要毁掉小账本就必须用林渊自己的牙印补位。",
                        "information_release": "证人被追杀是因为她继承了主账钥匙。",
                        "closing_hook": "主账页自动翻到林渊母亲的名字。",
                        "hooks_to_resolve": [
                            "林渊掌心牙印开始扩散",
                            "证人的本名为什么在父亲录音里",
                        ],
                        "hooks_to_plant": [
                            "主账为什么登记了林渊母亲",
                            "父亲作为记账人是否背叛过受害者",
                        ],
                    },
                ]
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "chapters": [
                {
                    "chapter_number": 1,
                    "contract_summary": "林渊遇到证人，旧案重启。",
                    "core_conflict": "林渊是否帮助证人。",
                    "information_release": "铜钱与父亲有关。",
                    "closing_hook": "门外有人敲门。",
                    "hooks_to_resolve": [],
                    "hooks_to_plant": ["缺角铜钱的来源"],
                },
                {
                    "chapter_number": 2,
                    "contract_summary": "林渊调查地下车库。",
                    "core_conflict": "林渊要找到线索。",
                    "information_release": "父亲来过这里。",
                    "closing_hook": "账册出现地址。",
                    "hooks_to_resolve": [],
                    "hooks_to_plant": [],
                },
                {
                    "chapter_number": 3,
                    "contract_summary": "林渊回旧宅找到父亲录音。",
                    "core_conflict": "真相越来越危险。",
                    "information_release": "父亲可能参与旧账。",
                    "closing_hook": "证人身份可疑。",
                    "hooks_to_resolve": ["缺角铜钱的来源"],
                    "hooks_to_plant": [],
                },
                {
                    "chapter_number": 4,
                    "contract_summary": "林渊毁掉小账本。",
                    "core_conflict": "林渊付出代价救证人。",
                    "information_release": "主账仍在。",
                    "closing_hook": "母亲名字出现。",
                    "hooks_to_resolve": [],
                    "hooks_to_plant": ["母亲为什么在主账上"],
                },
            ]
        },
        ensure_ascii=False,
    )


async def _call_planner(
    *,
    prompt: str,
    fallback_response: str,
    variant: str,
    max_tokens: int,
) -> LLMCompletionResult:
    settings = apply_runtime_llm_profile(load_settings())
    request = LLMCompletionRequest(
        logical_role="planner",
        model_tier="strong",
        system_prompt="你是严谨的长篇小说章节规划器，只输出可解析 JSON。",
        user_prompt=prompt,
        fallback_response=fallback_response,
        prompt_template="methodology_hook_ledger_ab_planner_v1",
        prompt_version="v1",
        metadata={"pilot": "methodology_hook_ledger_ab", "variant": variant},
        max_tokens_override=max_tokens,
    )
    return await complete_text(_TelemetryOnlySession(), settings, request)  # type: ignore[arg-type]


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.S)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _contracts_from_payload(payload: dict[str, Any]) -> tuple[ChapterContractRead, ...]:
    chapters = payload.get("chapters")
    if not isinstance(chapters, list):
        return ()
    contracts: list[ChapterContractRead] = []
    for raw in chapters:
        if not isinstance(raw, dict):
            continue
        try:
            chapter_number = int(raw.get("chapter_number"))
        except (TypeError, ValueError):
            continue
        contracts.append(
            ChapterContractRead(
                id=uuid4(),
                chapter_id=uuid4(),
                chapter_number=chapter_number,
                contract_summary=str(raw.get("contract_summary") or "未提供章节摘要"),
                core_conflict=str(raw.get("core_conflict") or "") or None,
                information_release=str(raw.get("information_release") or "") or None,
                closing_hook=str(raw.get("closing_hook") or "") or None,
                hooks_to_resolve=_clean_strings(raw.get("hooks_to_resolve")),
                hooks_to_plant=_clean_strings(raw.get("hooks_to_plant")),
            )
        )
    return tuple(sorted(contracts, key=lambda item: item.chapter_number))


def _clean_strings(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _clues_for_contract(contract: ChapterContractRead) -> list[SyntheticClue]:
    clues: list[SyntheticClue] = []
    for index, text in enumerate(contract.hooks_to_plant, start=1):
        clues.append(
            SyntheticClue(
                clue_code=f"contract:ch{contract.chapter_number}:plant:{index}",
                label=text,
                planted_in_chapter_number=contract.chapter_number,
                expected_payoff_by_chapter_number=contract.chapter_number + 3,
                metadata_json={"hook_type": _infer_hook_type(text)},
            )
        )
    for index, text in enumerate(contract.hooks_to_resolve, start=1):
        clues.append(
            SyntheticClue(
                clue_code=f"contract:ch{contract.chapter_number}:resolve:{index}",
                label=text,
                clue_type=_infer_hook_type(text),
                planted_in_chapter_number=max(1, contract.chapter_number - 1),
                expected_payoff_by_chapter_number=contract.chapter_number,
                actual_paid_off_chapter_number=contract.chapter_number,
                status="resolved",
                metadata_json={"hook_type": _infer_hook_type(text)},
            )
        )
    return clues


def _infer_hook_type(text: str) -> str:
    lowered = text.lower()
    if any(token in text for token in ("倒计时", "期限", "时限", "截止", "限时")):
        return "deadline"
    if any(token in text for token in ("危险", "追杀", "毁掉", "暴露", "威胁")):
        return "threat"
    if any(token in text for token in ("想要", "欲望", "渴望", "必须得到")):
        return "desire"
    if any(token in text for token in ("谜", "真相", "谁", "为什么")) or "why" in lowered:
        return "mystery"
    return "information_gap"


def _passing_review() -> ChapterReviewResult:
    scores = ChapterReviewScores(
        overall=0.9,
        goal=0.9,
        coverage=0.9,
        coherence=0.9,
        continuity=0.9,
        main_plot_progression=0.9,
        subplot_progression=0.9,
        style=0.9,
        hook=0.9,
        ending_hook_effectiveness=0.9,
        volume_mission_alignment=0.9,
        pacing_rhythm=0.9,
        character_voice_distinction=0.9,
        thematic_resonance=0.9,
        contract_alignment=0.9,
    )
    return ChapterReviewResult(
        verdict="pass",
        severity_max="info",
        scores=scores,
        findings=[],
        evidence_summary={},
    )


def _audit_contracts(contracts: tuple[ChapterContractRead, ...]) -> tuple[
    tuple[ChapterLedgerResult, ...],
    tuple[HookLedgerAudit, ...],
]:
    clues: list[SyntheticClue] = []
    chapter_results: list[ChapterLedgerResult] = []
    audits: list[HookLedgerAudit] = []
    for contract in contracts:
        clues.extend(_clues_for_contract(contract))
        audit = run_hook_ledger_audit(clues, current_chapter=contract.chapter_number)
        merged = merge_hook_ledger_audit_into_chapter_review(
            _passing_review(),
            audit,
            chapter_number=contract.chapter_number,
            language="zh-CN",
        )
        audits.append(audit)
        chapter_results.append(
            ChapterLedgerResult(
                chapter_number=contract.chapter_number,
                plant_count=audit.per_chapter_balance.plant_count,
                resolve_count=audit.per_chapter_balance.resolve_count,
                active_count=audit.active_count.active_count,
                closure_rate=round(audit.closure_rate, 3),
                finding_codes=tuple(finding.code for finding in audit.all_findings),
                rewrite_required=merged.verdict == "rewrite",
            )
        )
    return tuple(chapter_results), tuple(audits)


def _summarize_variant(
    *,
    variant: str,
    methodology_v2: bool,
    planner_contract_injected: bool,
    parse_ok: bool,
    fallback_used: bool,
    contracts: tuple[ChapterContractRead, ...],
    chapter_results: tuple[ChapterLedgerResult, ...],
    output_dir: Path,
) -> VariantResult:
    chapters = len(chapter_results)
    after_ch1 = max(1, sum(1 for result in chapter_results if result.chapter_number > 1))
    plant_coverage = _ratio(
        sum(1 for result in chapter_results if result.plant_count > 0),
        chapters,
    )
    resolve_coverage = _ratio(
        sum(
            1
            for result in chapter_results
            if result.chapter_number > 1 and result.resolve_count > 0
        ),
        after_ch1,
    )
    balance_pass = _ratio(
        sum(
            1
            for result in chapter_results
            if result.plant_count > 0
            and (result.chapter_number == 1 or result.resolve_count > 0)
        ),
        chapters,
    )
    active_pass = _ratio(
        sum(1 for result in chapter_results if 3 <= result.active_count <= 7),
        chapters,
    )
    next_seed_pass = _ratio(
        sum(
            1
            for result in chapter_results
            if "HOOK_NEXT_COMPRESSION_SEED_MISSING" not in result.finding_codes
        ),
        chapters,
    )
    rewrite_rate = _ratio(sum(1 for result in chapter_results if result.rewrite_required), chapters)
    avg_closure = _ratio(sum(result.closure_rate for result in chapter_results), chapters)
    finding_count = sum(len(result.finding_codes) for result in chapter_results)
    score = round(
        plant_coverage * 0.18
        + resolve_coverage * 0.22
        + balance_pass * 0.18
        + active_pass * 0.16
        + next_seed_pass * 0.14
        + (1.0 - rewrite_rate) * 0.12,
        3,
    )
    return VariantResult(
        variant=variant,
        methodology_v2_enabled=methodology_v2,
        planner_contract_injected=planner_contract_injected,
        parse_ok=parse_ok,
        fallback_used=fallback_used,
        chapters_count=len(contracts),
        plant_coverage=round(plant_coverage, 3),
        resolve_coverage_after_ch1=round(resolve_coverage, 3),
        balance_pass_rate=round(balance_pass, 3),
        active_count_pass_rate=round(active_pass, 3),
        next_seed_pass_rate=round(next_seed_pass, 3),
        rewrite_required_rate=round(rewrite_rate, 3),
        avg_closure_rate=round(avg_closure, 3),
        finding_count=finding_count,
        ledger_quality_score=score,
        output_dir=str(output_dir),
    )


def _ratio(numerator: float, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / denominator


async def _run_variant(
    *,
    root: Path,
    variant: str,
    methodology_v2: bool,
    max_tokens: int,
) -> tuple[VariantResult, dict[str, Any]]:
    prompt = _build_planner_prompt(methodology_v2=methodology_v2)
    fallback = _fallback_plan(methodology_v2=methodology_v2)
    output_dir = root / "output" / f"methodology-hook-ledger-ab-{variant}-{_now_slug()}"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "planner_prompt.md").write_text(prompt + "\n", encoding="utf-8")

    result = await _call_planner(
        prompt=prompt,
        fallback_response=fallback,
        variant=variant,
        max_tokens=max_tokens,
    )
    raw_plan = result.content.strip()
    (output_dir / "planner_output.json").write_text(raw_plan + "\n", encoding="utf-8")

    payload = _parse_json_object(raw_plan)
    parse_ok = bool(payload.get("chapters"))
    if not parse_ok:
        payload = _parse_json_object(fallback)
    contracts = _contracts_from_payload(payload)
    chapter_results, audits = _audit_contracts(contracts)
    planner_contract_injected = "【方法论 v2 钩子账本合同】" in prompt
    fallback_used = raw_plan.strip() == fallback.strip() or not parse_ok
    summary = _summarize_variant(
        variant=variant,
        methodology_v2=methodology_v2,
        planner_contract_injected=planner_contract_injected,
        parse_ok=parse_ok,
        fallback_used=fallback_used,
        contracts=contracts,
        chapter_results=chapter_results,
        output_dir=output_dir,
    )
    artifact = {
        "variant": asdict(summary),
        "llm": {
            "provider": result.provider,
            "model": result.model_name,
            "finish_reason": result.finish_reason,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "latency_ms": result.latency_ms,
        },
        "contracts": [contract.model_dump(mode="json") for contract in contracts],
        "chapter_results": [asdict(item) for item in chapter_results],
        "audits": [hook_ledger_audit_to_dict(audit) for audit in audits],
    }
    (output_dir / "audit_report.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary, artifact


def _render_markdown(
    *,
    active_profile: dict[str, Any],
    summaries: tuple[VariantResult, VariantResult],
    artifacts: tuple[dict[str, Any], dict[str, Any]],
) -> str:
    baseline, treatment = summaries
    delta = round(treatment.ledger_quality_score - baseline.ledger_quality_score, 3)
    verdict = (
        "B 组胜出：methodology v2 hook ledger 接入后，规划合同更可审计，review 更能发现并触发修复。"
        if delta > 0
        else "本轮未证明 B 组优于 A 组，需要扩大样本或调整接入。"
    )
    rows = [
        ("ledger_quality_score", baseline.ledger_quality_score, treatment.ledger_quality_score),
        ("plant_coverage", baseline.plant_coverage, treatment.plant_coverage),
        (
            "resolve_coverage_after_ch1",
            baseline.resolve_coverage_after_ch1,
            treatment.resolve_coverage_after_ch1,
        ),
        ("balance_pass_rate", baseline.balance_pass_rate, treatment.balance_pass_rate),
        (
            "active_count_pass_rate",
            baseline.active_count_pass_rate,
            treatment.active_count_pass_rate,
        ),
        ("next_seed_pass_rate", baseline.next_seed_pass_rate, treatment.next_seed_pass_rate),
        ("rewrite_required_rate", baseline.rewrite_required_rate, treatment.rewrite_required_rate),
        ("finding_count", baseline.finding_count, treatment.finding_count),
    ]
    metric_lines = "\n".join(
        f"| {name} | {a} | {b} | {round(float(b) - float(a), 3)} |"
        for name, a, b in rows
    )
    return f"""# Methodology V2 Hook Ledger A/B Test

## 结论

{verdict}

- A 组：`BESTSELLER_METHODOLOGY_V2=0`，不注入 hook ledger planner contract。
- B 组：`BESTSELLER_METHODOLOGY_V2=1`，注入 hook ledger planner contract，
  并用 review-time audit 合并结果。
- LLM Profile：`{active_profile.get("active_key")}` / `{active_profile.get("active_label")}`。
- B-A 总分差：`{delta}`。

## 指标对比

| 指标 | A baseline | B methodology_v2 | Δ |
|---|---:|---:|---:|
{metric_lines}

## 物料位置

- A 输出：`{baseline.output_dir}`
- B 输出：`{treatment.output_dir}`

## 每章审计摘要

### A baseline

{_render_chapter_table(artifacts[0]["chapter_results"])}

### B methodology_v2

{_render_chapter_table(artifacts[1]["chapter_results"])}
"""


def _render_chapter_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| 章 | plant | resolve | active | closure | rewrite | findings |",
        "|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        findings = ", ".join(row.get("finding_codes") or ()) or "-"
        lines.append(
            "| {chapter_number} | {plant_count} | {resolve_count} | "
            "{active_count} | {closure_rate} | {rewrite_required} | {findings} |".format(
                **row,
                findings=findings,
            )
        )
    return "\n".join(lines)


async def run_ab(args: argparse.Namespace) -> Path:
    root = _repo_root()
    settings = load_settings()
    set_runtime_llm_profile(settings, args.profile)
    active_profile = runtime_llm_profile_payload(settings)

    baseline_summary, baseline_artifact = await _run_variant(
        root=root,
        variant="baseline",
        methodology_v2=False,
        max_tokens=args.max_tokens,
    )
    treatment_summary, treatment_artifact = await _run_variant(
        root=root,
        variant="methodology-v2",
        methodology_v2=True,
        max_tokens=args.max_tokens,
    )
    report_dir = root / "output" / f"methodology-hook-ledger-ab-report-{_now_slug()}"
    report_dir.mkdir(parents=True, exist_ok=True)
    markdown = _render_markdown(
        active_profile=active_profile,
        summaries=(baseline_summary, treatment_summary),
        artifacts=(baseline_artifact, treatment_artifact),
    )
    (report_dir / "ab_report.md").write_text(markdown + "\n", encoding="utf-8")
    (report_dir / "ab_report.json").write_text(
        json.dumps(
            {
                "active_llm_profile": active_profile,
                "baseline": asdict(baseline_summary),
                "methodology_v2": asdict(treatment_summary),
                "delta": round(
                    treatment_summary.ledger_quality_score
                    - baseline_summary.ledger_quality_score,
                    3,
                ),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(report_dir)
    return report_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="xiaomi-mimo")
    parser.add_argument("--max-tokens", type=int, default=4000)
    return parser.parse_args()


def main() -> None:
    asyncio.run(run_ab(parse_args()))


if __name__ == "__main__":
    main()
