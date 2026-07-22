#!/usr/bin/env python3
"""Run a paired live A/B test of scene assembly vs one-shot chapter writing.

Example:
    .venv/bin/python -u scripts/generation_mode_ab.py \
      --out output/generation-mode-ab/20260720-initial
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.drafts import (  # noqa: E402
    sanitize_novel_markdown_content,
    strip_scaffolding_echoes,
)
from bestseller.services.generation_mode_ab import (  # noqa: E402
    MODE_CHAPTER_FIRST,
    MODE_SCENE_BY_SCENE,
    MODES,
    ChapterABCase,
    DeterministicScore,
    GeneratedSample,
    PairwiseJudgement,
    assemble_scene_texts,
    build_chapter_first_prompts,
    build_default_cases,
    build_pairwise_judge_prompts,
    build_scene_prompts,
    judgement_to_dict,
    parse_pairwise_judgement,
    sample_to_dict,
    score_generated_sample,
    summarize_experiment,
)
from bestseller.services.llm import (  # noqa: E402
    LLMCompletionRequest,
    LLMCompletionResult,
    complete_text,
)
from bestseller.services.model_catalog import get_model_catalog_entry  # noqa: E402
from bestseller.settings import AppSettings, load_settings  # noqa: E402

DEFAULT_WRITER_MODEL = "minimax-m3"
DEFAULT_JUDGES = ("minimax-m2.7-highspeed", "deepseek-v4-flash")


async def main() -> int:
    args = _parse_args()
    settings = load_settings()
    _validate_models(args.writer_model, args.judge_model)
    cases = build_default_cases()
    if args.case:
        wanted = set(args.case)
        cases = tuple(case for case in cases if case.case_id in wanted)
        missing = wanted - {case.case_id for case in cases}
        if missing:
            raise RuntimeError(f"unknown case ids: {sorted(missing)}")

    out = Path(args.out) if args.out else _default_out()
    samples_dir = out / "samples"
    judgements_dir = out / "judgements"
    for path in (out, samples_dir, judgements_dir):
        path.mkdir(parents=True, exist_ok=True)

    _write_json(
        out / "experiment.json",
        {
            "schema_version": "generation-mode-ab/v1",
            "created_at": datetime.now(UTC).isoformat(),
            "writer_model": args.writer_model,
            "judge_models": args.judge_model,
            "cases": [asdict(case) for case in cases],
            "design": "docs/experiments/generation-mode-ab-test.md",
        },
    )

    samples: list[GeneratedSample] = []
    for case in cases:
        print(f"[writer] case={case.case_id} mode={MODE_SCENE_BY_SCENE}", flush=True)
        samples.append(
            await _load_or_generate_scene_sample(
                case,
                settings=settings,
                writer_model=args.writer_model,
                path=samples_dir / f"{case.case_id}__{MODE_SCENE_BY_SCENE}.json",
            )
        )
        print(f"[writer] case={case.case_id} mode={MODE_CHAPTER_FIRST}", flush=True)
        samples.append(
            await _load_or_generate_chapter_sample(
                case,
                settings=settings,
                writer_model=args.writer_model,
                path=samples_dir / f"{case.case_id}__{MODE_CHAPTER_FIRST}.json",
            )
        )

    sample_by_key = {(sample.case_id, sample.mode): sample for sample in samples}
    _write_blind_packets(out, cases, sample_by_key)
    judgements: list[PairwiseJudgement] = []
    for case in cases:
        scene_text = sample_by_key[(case.case_id, MODE_SCENE_BY_SCENE)].text
        chapter_text = sample_by_key[(case.case_id, MODE_CHAPTER_FIRST)].text
        for judge_model in args.judge_model:
            for swapped in (False, True):
                suffix = "swapped" if swapped else "forward"
                path = judgements_dir / f"{case.case_id}__{judge_model}__{suffix}.json"
                print(
                    f"[judge] case={case.case_id} model={judge_model} direction={suffix}",
                    flush=True,
                )
                judgements.append(
                    await _load_or_judge(
                        case,
                        scene_text=scene_text,
                        chapter_text=chapter_text,
                        settings=settings,
                        judge_model=judge_model,
                        swapped=swapped,
                        path=path,
                    )
                )

    for import_path in args.import_judgements or ():
        imported = _load_external_judgements(Path(import_path))
        judgements.extend(imported)
        print(f"[judge] imported={len(imported)} from={import_path}", flush=True)

    summary = summarize_experiment(cases, samples, judgements)
    effective_judges = sorted({item.judge_model for item in judgements})
    manifest = {
        "schema_version": "generation-mode-ab/v1",
        "writer_model": args.writer_model,
        "judge_models": effective_judges,
        "cases": [asdict(case) for case in cases],
        "samples": [sample_to_dict(sample) for sample in samples],
        "judgements": [judgement_to_dict(item) for item in judgements],
        "summary": summary,
    }
    _write_json(out / "manifest.json", manifest)
    (out / "report.md").write_text(
        _render_report(args.writer_model, effective_judges, cases, samples, judgements, summary),
        encoding="utf-8",
    )
    print(f"[done] decision={summary['decision']}", flush=True)
    print(f"[done] report={out / 'report.md'}", flush=True)
    print(f"[done] manifest={out / 'manifest.json'}", flush=True)
    return 0


async def _load_or_generate_scene_sample(
    case: ChapterABCase,
    *,
    settings: AppSettings,
    writer_model: str,
    path: Path,
) -> GeneratedSample:
    if path.exists():
        print(f"  resume {path.name}", flush=True)
        sample = _rescore_sample(_sample_from_dict(_read_json(path)), case)
        _write_json(path, sample_to_dict(sample))
        return sample
    parts: list[str] = []
    calls: list[LLMCompletionResult] = []
    for beat in case.scene_beats:
        system, user = build_scene_prompts(
            case,
            beat,
            previous_tail=parts[-1] if parts else "",
        )
        result = await _call_model(
            settings,
            logical_role="writer",
            model_key=writer_model,
            system=system,
            user=user,
            prompt_template="generation_mode_ab_scene_writer",
            max_tokens=1900,
            metadata={"case_id": case.case_id, "mode": MODE_SCENE_BY_SCENE, "beat": beat.number},
        )
        _assert_real_completion(result, f"{case.case_id}/scene-{beat.number}")
        content = _clean_prose(result.content)
        if not content:
            raise RuntimeError(f"empty scene output: {case.case_id}/scene-{beat.number}")
        parts.append(content)
        calls.append(result)
        print(
            f"  scene={beat.number} chars={len(content)} latency_ms={result.latency_ms}",
            flush=True,
        )
    text = assemble_scene_texts(parts)
    sample = _make_sample(
        case,
        mode=MODE_SCENE_BY_SCENE,
        text=text,
        model=writer_model,
        calls=calls,
        component_count=len(parts),
    )
    _write_json(path, sample_to_dict(sample))
    return sample


async def _load_or_generate_chapter_sample(
    case: ChapterABCase,
    *,
    settings: AppSettings,
    writer_model: str,
    path: Path,
) -> GeneratedSample:
    if path.exists():
        print(f"  resume {path.name}", flush=True)
        sample = _rescore_sample(_sample_from_dict(_read_json(path)), case)
        _write_json(path, sample_to_dict(sample))
        return sample
    system, user = build_chapter_first_prompts(case)
    result = await _call_model(
        settings,
        logical_role="writer",
        model_key=writer_model,
        system=system,
        user=user,
        prompt_template="generation_mode_ab_chapter_writer",
        max_tokens=5200,
        metadata={"case_id": case.case_id, "mode": MODE_CHAPTER_FIRST},
    )
    _assert_real_completion(result, f"{case.case_id}/chapter")
    text = _clean_prose(result.content)
    if not text:
        raise RuntimeError(f"empty chapter output: {case.case_id}")
    sample = _make_sample(
        case,
        mode=MODE_CHAPTER_FIRST,
        text=text,
        model=writer_model,
        calls=[result],
        component_count=1,
    )
    print(f"  chapter chars={len(text)} latency_ms={result.latency_ms}", flush=True)
    _write_json(path, sample_to_dict(sample))
    return sample


async def _load_or_judge(
    case: ChapterABCase,
    *,
    scene_text: str,
    chapter_text: str,
    settings: AppSettings,
    judge_model: str,
    swapped: bool,
    path: Path,
) -> PairwiseJudgement:
    if path.exists():
        print(f"  resume {path.name}", flush=True)
        return _judgement_from_dict(_read_json(path))
    system, user = build_pairwise_judge_prompts(
        case,
        left_text=scene_text,
        right_text=chapter_text,
        judge_label=judge_model,
        swapped=swapped,
    )
    result = await _call_model(
        settings,
        logical_role="critic",
        model_key=judge_model,
        system=system,
        user=user,
        prompt_template="generation_mode_ab_pairwise_judge",
        max_tokens=2200,
        metadata={"case_id": case.case_id, "judge": judge_model, "swapped": swapped},
    )
    _assert_real_completion(result, f"{case.case_id}/{judge_model}/{swapped}")
    path.with_suffix(".raw.txt").write_text(result.content + "\n", encoding="utf-8")
    judgement = parse_pairwise_judgement(
        case.case_id,
        judge_model,
        result.content,
        swapped=swapped,
    )
    _write_json(path, judgement_to_dict(judgement))
    print(f"  winner={judgement.winner}", flush=True)
    return judgement


async def _call_model(
    settings: AppSettings,
    *,
    logical_role: str,
    model_key: str,
    system: str,
    user: str,
    prompt_template: str,
    max_tokens: int,
    metadata: dict[str, Any],
) -> LLMCompletionResult:
    async with session_scope(settings) as session:
        return await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role=logical_role,  # type: ignore[arg-type]
                system_prompt=system,
                user_prompt=user,
                fallback_response='{"error":"fallback"}'
                if logical_role == "critic"
                else "生成失败",
                prompt_template=prompt_template,
                prompt_version="v1",
                model_catalog_key=model_key,
                max_tokens_override=max_tokens,
                metadata=metadata,
            ),
        )


def _make_sample(
    case: ChapterABCase,
    *,
    mode: str,
    text: str,
    model: str,
    calls: list[LLMCompletionResult],
    component_count: int,
) -> GeneratedSample:
    return GeneratedSample(
        case_id=case.case_id,
        mode=mode,
        text=text,
        deterministic=score_generated_sample(text, case),
        writer_model=model,
        provider=",".join(sorted({call.provider for call in calls})),
        latency_ms=sum(call.latency_ms or 0 for call in calls),
        input_tokens=sum(call.input_tokens or 0 for call in calls),
        output_tokens=sum(call.output_tokens or 0 for call in calls),
        component_count=component_count,
        fallback_used=any(call.fallback_used for call in calls),
    )


def _clean_prose(text: str) -> str:
    body = sanitize_novel_markdown_content(text, language="zh")
    return strip_scaffolding_echoes(body).strip()


def _assert_real_completion(result: LLMCompletionResult, label: str) -> None:
    if result.fallback_used or result.provider == "mock":
        raise RuntimeError(
            f"invalid fallback/mock completion for {label}: provider={result.provider} "
            f"fallback={result.fallback_used}"
        )


def _sample_from_dict(payload: dict[str, Any]) -> GeneratedSample:
    deterministic = DeterministicScore(**payload.pop("deterministic"))
    return GeneratedSample(deterministic=deterministic, **payload)


def _rescore_sample(sample: GeneratedSample, case: ChapterABCase) -> GeneratedSample:
    return GeneratedSample(
        case_id=sample.case_id,
        mode=sample.mode,
        text=sample.text,
        deterministic=score_generated_sample(sample.text, case),
        writer_model=sample.writer_model,
        provider=sample.provider,
        latency_ms=sample.latency_ms,
        input_tokens=sample.input_tokens,
        output_tokens=sample.output_tokens,
        component_count=sample.component_count,
        fallback_used=sample.fallback_used,
    )


def _judgement_from_dict(payload: dict[str, Any]) -> PairwiseJudgement:
    payload["risk_notes"] = tuple(payload.get("risk_notes") or ())
    return PairwiseJudgement(**payload)


def _write_blind_packets(
    out: Path,
    cases: tuple[ChapterABCase, ...],
    sample_by_key: dict[tuple[str, str], GeneratedSample],
) -> None:
    blind_root = out / "blind"
    private_mapping: dict[str, dict[str, str]] = {}
    for case in cases:
        scene = sample_by_key[(case.case_id, MODE_SCENE_BY_SCENE)].text
        chapter = sample_by_key[(case.case_id, MODE_CHAPTER_FIRST)].text
        private_mapping[case.case_id] = {
            "forward_A": MODE_SCENE_BY_SCENE,
            "forward_B": MODE_CHAPTER_FIRST,
            "swapped_A": MODE_CHAPTER_FIRST,
            "swapped_B": MODE_SCENE_BY_SCENE,
        }
        for direction, first, second in (
            ("forward", scene, chapter),
            ("swapped", chapter, scene),
        ):
            case_dir = blind_root / direction / case.case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            (case_dir / "brief.md").write_text(
                f"# 匿名评审任务\n\n题材：{case.genre}\n\n"
                f"前情：{case.previous_context}\n\n章节目标：{case.chapter_goal}\n",
                encoding="utf-8",
            )
            (case_dir / "A.md").write_text(first + "\n", encoding="utf-8")
            (case_dir / "B.md").write_text(second + "\n", encoding="utf-8")
    _write_json(blind_root / "private-mapping.json", private_mapping)


def _load_external_judgements(path: Path) -> list[PairwiseJudgement]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("judgements") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise RuntimeError(f"external judgement file must contain a list: {path}")
    results: list[PairwiseJudgement] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError(f"invalid external judgement row: {path}")
        if "mode_scores" in row:
            results.append(_judgement_from_dict(dict(row)))
            continue
        raw_payload = {
            "scores": row.get("scores"),
            "winner": row.get("winner"),
            "evidence": row.get("evidence"),
            "risk_notes": row.get("risk_notes"),
        }
        results.append(
            parse_pairwise_judgement(
                str(row.get("case_id") or ""),
                str(row.get("judge_model") or "external-judge"),
                json.dumps(raw_payload, ensure_ascii=False),
                swapped=bool(row.get("swapped")),
            )
        )
    return results


def _render_report(
    writer_model: str,
    judge_models: list[str],
    cases: tuple[ChapterABCase, ...],
    samples: list[GeneratedSample],
    judgements: list[PairwiseJudgement],
    summary: dict[str, Any],
) -> str:
    decision_label = {
        MODE_SCENE_BY_SCENE: "多场景分别生成后组章",
        MODE_CHAPTER_FIRST: "一次性整章生成",
        "inconclusive": "首轮无明确赢家",
    }[summary["decision"]]
    delta = float(summary["score_delta_chapter_first_minus_scene"])
    observed_leader = MODE_CHAPTER_FIRST if delta >= 0 else MODE_SCENE_BY_SCENE
    observed_leader_label = (
        "整章一次" if observed_leader == MODE_CHAPTER_FIRST else "多场景"
    )
    observed_trailer = MODE_SCENE_BY_SCENE if observed_leader == MODE_CHAPTER_FIRST else MODE_CHAPTER_FIRST
    deterministic = summary["deterministic"]
    inconclusive_reasons: list[str] = []
    if not summary.get("enough_judgements", False):
        inconclusive_reasons.append(
            f"盲评仅 {summary['judgement_count']} 次，预注册门槛为 "
            f"{summary.get('required_judgement_count', len(cases) * 4)} 次"
        )
    if summary["position_agreement_rate"] < 0.75:
        inconclusive_reasons.append(
            f"换位一致率 {summary['position_agreement_rate']:.1%}，低于 75%"
        )
    if abs(delta) < 0.30:
        inconclusive_reasons.append(f"综合分差 {delta:+.2f}，未达到 0.30")
    if summary["case_wins"][observed_leader] < 2:
        inconclusive_reasons.append(
            f"{observed_leader_label}仅赢 {summary['case_wins'][observed_leader]} 组，少于 2 组"
        )
    if not summary["coverage_guard_passed"]:
        inconclusive_reasons.append("领先模式未通过必须事件覆盖保护")
    lines = [
        "# 章节生成模式 A/B Test 报告",
        "",
        f"- 结论：**{decision_label}**",
        f"- 写手模型：`{writer_model}`",
        f"- 判官：{', '.join(f'`{item}`' for item in judge_models)}",
        f"- 样本：{len(cases)} 组配对章节；盲评：{len(judgements)} 次（含位置互换）",
        f"- 位置互换一致率：{summary['position_agreement_rate']:.1%}",
        "",
        "## 总分与硬指标",
        "",
        "| 模式 | 盲评加权分 | AI 味检测分（低好） | 必须事件覆盖 | 长度通过率 |",
        "|---|---:|---:|---:|---:|",
    ]
    sample_by_mode = {mode: [item for item in samples if item.mode == mode] for mode in MODES}
    for mode in MODES:
        det = summary["deterministic"][mode]
        label = "多场景" if mode == MODE_SCENE_BY_SCENE else "整章一次"
        lines.append(
            f"| {label} | {summary['mode_weighted_scores'][mode]:.2f} | "
            f"{det['mean_ai_flavor_score']:.2f} | {det['mean_required_event_coverage']:.1%} | "
            f"{det['length_pass_rate']:.1%} |"
        )
    lines.extend(
        [
            "",
            "## 四维盲评分",
            "",
            "| 模式 | AI 味低 | 逻辑 | 故事性 | 可读性 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for mode in MODES:
        dims = summary["dimension_scores"][mode]
        label = "多场景" if mode == MODE_SCENE_BY_SCENE else "整章一次"
        lines.append(
            f"| {label} | {dims['anti_ai']:.2f} | {dims['logic']:.2f} | "
            f"{dims['story']:.2f} | {dims['readability']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## 分样本结果",
            "",
            "| 样本 | 多场景分 | 整章一次分 | 胜者 |",
            "|---|---:|---:|---|",
        ]
    )
    for case in cases:
        result = summary["case_results"][case.case_id]
        winner = {
            MODE_SCENE_BY_SCENE: "多场景",
            MODE_CHAPTER_FIRST: "整章一次",
            "tie": "接近",
        }[result["winner"]]
        lines.append(
            f"| {case.title} | {result['mode_scores'][MODE_SCENE_BY_SCENE]:.2f} | "
            f"{result['mode_scores'][MODE_CHAPTER_FIRST]:.2f} | {winner} |"
        )
    lines.extend(
        [
            "",
            "## 生成成本诊断",
            "",
            "| 模式 | 调用单元 | 总延迟 ms | 输入 tokens | 输出 tokens |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for mode in MODES:
        rows = sample_by_mode[mode]
        label = "多场景" if mode == MODE_SCENE_BY_SCENE else "整章一次"
        lines.append(
            f"| {label} | {sum(item.component_count for item in rows)} | "
            f"{sum(item.latency_ms or 0 for item in rows)} | "
            f"{sum(item.input_tokens or 0 for item in rows)} | "
            f"{sum(item.output_tokens or 0 for item in rows)} |"
        )
    lines.extend(
        [
            "",
            "## 决策门槛",
            "",
            f"- 三组样本胜场：多场景 {summary['case_wins'][MODE_SCENE_BY_SCENE]}；"
            f"整章一次 {summary['case_wins'][MODE_CHAPTER_FIRST]}。",
            "- 加权分差（整章一次 - 多场景）："
            f"{summary['score_delta_chapter_first_minus_scene']:+.2f}。",
            f"- 情节覆盖保护：{'通过' if summary['coverage_guard_passed'] else '未通过'}。",
            f"- 盲评数量：{summary['judgement_count']} / "
            f"{summary.get('required_judgement_count', len(cases) * 4)}；"
            f"{'达到' if summary.get('enough_judgements', False) else '未达到'}预注册门槛。",
            "- 本轮未在看到结果后修改提示词。报告中的正文与判官原始 JSON 均保存在同目录。",
            "",
            "## 判官稳定性",
            "",
            "| 判官 | 多场景均分 | 整章一次均分 | 换位一致率 |",
            "|---|---:|---:|---:|",
        ]
    )
    for judge in judge_models:
        judge_scores = summary["mode_weighted_scores_by_judge"][judge]
        stability = summary["position_agreement_by_judge"][judge]
        lines.append(
            f"| {judge} | {judge_scores[MODE_SCENE_BY_SCENE]:.2f} | "
            f"{judge_scores[MODE_CHAPTER_FIRST]:.2f} | "
            f"{stability['agreement_rate']:.1%} |"
        )
    lines.extend(
        [
            "",
            "## 当前可执行结论",
            "",
            f"- 观察性领先者是**{observed_leader_label}**：综合分差（整章一次 - 多场景）"
            f"为 {delta:+.2f}；这只是本轮样本表现，不等于通过生产切换门槛。",
            f"- AI 味硬检测：{observed_leader_label} "
            f"{deterministic[observed_leader]['mean_ai_flavor_score']:.2f}，"
            f"另一模式 {deterministic[observed_trailer]['mean_ai_flavor_score']:.2f}（低好）。",
            f"- 长度通过率：{observed_leader_label} "
            f"{deterministic[observed_leader]['length_pass_rate']:.1%}，"
            f"另一模式 {deterministic[observed_trailer]['length_pass_rate']:.1%}；"
            "长度稳定性必须单独判断，不能由综合分代替。",
            (
                "- 未形成生产级赢家：" + "；".join(inconclusive_reasons) + "。"
                if summary["decision"] == "inconclusive"
                else f"- 已达到预注册门槛，生产候选为**{decision_label}**。"
            ),
            "- 下一轮应保留场景卡作为规划约束，但先整章生成；"
            "场景级调用只用于定点修复已定位的问题段，"
            "不再把三份独立正文直接拼成成书章节。",
            "- 多场景模式的主要风险是重复开场、时间/数字状态冲突和总字数膨胀；"
            "整章模式的主要风险是局部专业常识错误和个别必演事件顺序漂移。",
            "",
            "## 边界",
            "",
            f"三组现实经营题材样本用于判断明显的生成单元效应，不代表所有题材。"
            f"本轮实际使用 {len(judge_models)} 个判官（{', '.join(judge_models)}）；"
            "若未达到预注册盲评数量，只能形成观察性结论。明确赢家仍需增加独立判官，"
            "并在玄幻、都市情感和非悬疑职场样本上回归。",
            "",
        ]
    )
    return "\n".join(lines)


def _validate_models(writer: str, judges: list[str]) -> None:
    for key in (writer, *judges):
        entry = get_model_catalog_entry(key)
        if entry is None:
            raise RuntimeError(f"model catalog entry not found: {key}")
        if not entry.available:
            raise RuntimeError(f"model is not available: {key}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None)
    parser.add_argument("--writer-model", default=DEFAULT_WRITER_MODEL)
    parser.add_argument(
        "--judge-model",
        action="append",
        default=None,
        help=(
            "Repeat for multiple live judges. Defaults to MiniMax M2.7; "
            "use --import-judgements for an independent second judge."
        ),
    )
    parser.add_argument("--case", action="append", default=None)
    parser.add_argument(
        "--import-judgements",
        action="append",
        default=None,
        help="Import external blind judgements after live judges complete.",
    )
    args = parser.parse_args()
    args.judge_model = args.judge_model or list(DEFAULT_JUDGES)
    return args


def _default_out() -> Path:
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    return ROOT / "output" / "generation-mode-ab" / stamp


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
