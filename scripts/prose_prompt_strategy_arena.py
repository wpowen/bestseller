"""Run a same-scene prose prompt strategy arena.

Dry-run example:
    .venv/bin/python scripts/prose_prompt_strategy_arena.py \
      --trace output/<slug>/traces/<scene-prompt>.json --dry-run

Prompt export example:
    .venv/bin/python scripts/prose_prompt_strategy_arena.py \
      --trace output/<slug>/traces/<scene-prompt>.json --prompts-only

Live example:
    .venv/bin/python scripts/prose_prompt_strategy_arena.py \
      --trace output/<slug>/traces/<scene-prompt>.json \
      --writer-model minimax-m3 \
      --writer-model qwen3.7-plus-coding-plan \
      --judge-model minimax-m3 \
      --judge-model deepseek-v4-flash

Manual selection analysis:
    .venv/bin/python scripts/prose_prompt_strategy_arena.py \
      --manifest output/prose-prompt-arena/<run>/manifest.json \
      --manual-selection ~/Downloads/manual-selection.json

Completion audit:
    .venv/bin/python scripts/prose_prompt_strategy_arena.py \
      --manifest output/prose-prompt-arena/<run>/manifest.json \
      --audit

Judge prompt export from an existing report:
    .venv/bin/python scripts/prose_prompt_strategy_arena.py \
      --manifest output/prose-prompt-arena/<run>/manifest.json \
      --export-judge-prompts \
      --judge-model deepseek-v4-flash

External draft import:
    .venv/bin/python scripts/prose_prompt_strategy_arena.py \
      --prompt-manifest output/prose-prompt-arena/<run>/prompt-manifest.json \
      --import-drafts output/external-drafts \
      --skip-judging \
      --export-judge-prompts

External judgement import:
    .venv/bin/python scripts/prose_prompt_strategy_arena.py \
      --manifest output/prose-prompt-arena/<run>/manifest.json \
      --import-judgements output/external-judgements \
      --skip-judging

Partial import is only for smoke tests; pass --allow-partial-import explicitly.
"""

# ruff: noqa: E402, I001, RUF001

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bestseller.infra.db.session import session_scope
from bestseller.services.llm import (
    LLMCompletionRequest,
    LLMCompletionResult,
    LLMRole,
    complete_text,
)
from bestseller.services.model_catalog import (
    ModelCatalogEntry,
    get_model_catalog_entry,
    load_model_catalog,
)
from bestseller.services.prose_prompt_experiment import (
    DraftResult,
    ExperimentReport,
    JudgeResult,
    PromptTraceCase,
    PromptStrategy,
    PromptVariant,
    build_blind_label_by_draft_ids,
    build_default_strategies,
    build_judge_result_schema,
    build_judge_system_prompt,
    build_judge_user_prompt,
    build_methodology_application_audit,
    build_prompt_variants,
    draft_from_dict,
    draft_to_dict,
    judgement_from_dict,
    judgement_to_dict,
    load_prompt_trace,
    make_dry_run_draft,
    make_dry_run_judgement,
    parse_judge_result,
    utc_now_iso,
    write_experiment_package,
)
from bestseller.settings import AppSettings, LLMRoleSettings, load_settings


@dataclass(frozen=True)
class ModelSpec:
    label: str
    model: str | None = None
    api_base: str | None = None
    api_key_env: str | None = None
    api_key_header: str | None = None
    available: bool | None = None
    configured_role: bool = False


DEFAULT_AUDIT_WRITER_MODELS = ("minimax-m3", "qwen3.7-plus-coding-plan")
DEFAULT_AUDIT_JUDGE_MODELS = ("minimax-m3", "deepseek-v4-flash")


async def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.audit:
        if not args.manifest:
            raise RuntimeError("--audit requires --manifest.")
        paths = _audit_experiment_manifest(
            Path(args.manifest),
            output_dir=Path(args.audit_out) if args.audit_out else None,
            manual_selection_path=Path(args.manual_selection) if args.manual_selection else None,
            expected_strategy_count=args.expected_strategies,
            expected_writer_models=_audit_expected_writer_models(args),
            expected_judge_models=_audit_expected_judge_models(args),
        )
        print(f"audit_json: {paths['json']}")
        print(f"audit_md: {paths['md']}")
        print(paths["summary"])
        return 0

    if args.manifest and args.export_judge_prompts:
        handoff_path = _export_judge_prompts_from_manifest(
            Path(args.manifest),
            output_dir=Path(args.out) if args.out else None,
            judge_labels=args.judge_model or ["external-judge"],
        )
        print(f"judge_handoff: {handoff_path}")
        return 0

    if args.manifest and args.import_judgements:
        paths = _merge_judgements_into_manifest(
            Path(args.manifest),
            Path(args.import_judgements),
            output_dir=Path(args.out) if args.out else None,
        )
        print(f"manifest: {paths['manifest']}")
        print(f"html: {paths['html']}")
        print(f"imported_judgements: {paths['imported_judgements']}")
        print(f"total_judgements: {paths['total_judgements']}")
        return 0

    if args.manifest and args.strategy_proposals:
        paths = _materialize_strategy_proposals(
            Path(args.manifest),
            Path(args.strategy_proposals),
            output_dir=Path(args.out) if args.out else None,
        )
        print(f"prompt_manifest: {paths['prompt_manifest']}")
        print(f"prompt_handoff: {paths['prompt_handoff']}")
        print(f"variants: {paths['variant_count']} drafts: 0 judgements: 0")
        return 0

    if args.prompt_manifest:
        paths = await _run_prompt_manifest_import(
            Path(args.prompt_manifest),
            args,
        )
        print(f"manifest: {paths['manifest']}")
        print(f"html: {paths['html']}")
        if paths.get("judge_handoff"):
            print(f"judge_handoff: {paths['judge_handoff']}")
        print(
            f"variants: {paths['variants']} "
            f"drafts: {paths['drafts']} judgements: {paths['judgements']}"
        )
        return 0

    if args.manual_selection or args.manifest:
        if not args.manual_selection or not args.manifest:
            raise RuntimeError("--manifest and --manual-selection must be passed together.")
        paths = _analyze_manual_selection(
            Path(args.manifest),
            Path(args.manual_selection),
            Path(args.analysis_out) if args.analysis_out else None,
        )
        print(f"manual_analysis_json: {paths['json']}")
        print(f"manual_analysis_md: {paths['md']}")
        print(paths["summary"])
        return 0

    if not args.trace:
        raise RuntimeError("--trace is required unless --manifest and --manual-selection are used.")

    case = load_prompt_trace(args.trace)
    strategies = build_default_strategies()
    variants = build_prompt_variants(case, strategies, limit=args.limit)

    if args.preflight:
        _print_preflight(case, variants, args)
        return 0

    if args.resume and not args.out:
        raise RuntimeError("--resume requires --out so the previous run directory is explicit.")

    output_dir = Path(args.out) if args.out else _default_output_dir(case.case_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_prompt_variants(output_dir, variants)
    _write_external_writer_prompt_files(output_dir, variants)
    handoff_path = _write_external_prompt_handoff(output_dir, variants)
    if args.prompts_only:
        prompt_manifest_path = _write_prompt_only_manifest(output_dir, case, variants)
        print(f"prompt_manifest: {prompt_manifest_path}")
        print(f"prompt_handoff: {handoff_path}")
        print(f"variants: {len(variants)} drafts: 0 judgements: 0")
        return 0

    if args.dry_run:
        drafts = _dry_run_drafts(variants)
        judgements = [
            make_dry_run_judgement(draft, _variant_by_id(variants)[draft.variant_id])
            for draft in drafts
        ]
    elif args.import_drafts:
        drafts = _import_external_drafts(
            variants,
            Path(args.import_drafts),
            writer_model=args.import_writer_model,
            allow_partial=args.allow_partial_import,
        )
        judgements = []
        if not args.skip_judging:
            settings = load_settings()
            judge_models = _resolve_model_specs(
                args.judge_model,
                default_label="configured-critic",
                allow_unavailable=args.allow_unavailable_models,
            )
            judgements = await _run_judges(
                case,
                drafts,
                settings=settings,
                judge_models=judge_models,
                max_tokens=args.judge_max_tokens,
                output_dir=output_dir,
                resume=args.resume,
            )
    else:
        settings = load_settings()
        writer_models = _resolve_model_specs(
            args.writer_model,
            default_label="configured-writer",
            allow_unavailable=args.allow_unavailable_models,
        )
        judge_models = _resolve_model_specs(
            args.judge_model,
            default_label="configured-critic",
            allow_unavailable=args.allow_unavailable_models,
        )
        drafts = await _run_writers(
            variants,
            settings=settings,
            writer_models=writer_models,
            samples_per_strategy=args.samples_per_strategy,
            max_tokens=args.writer_max_tokens,
            output_dir=output_dir,
            resume=args.resume,
        )
        judgements = []
        if not args.skip_judging:
            judgements = await _run_judges(
                case,
                drafts,
                settings=settings,
                judge_models=judge_models,
                max_tokens=args.judge_max_tokens,
                output_dir=output_dir,
                resume=args.resume,
            )

    if args.import_judgements:
        imported = _import_external_judgements(
            drafts,
            Path(args.import_judgements),
            expected_judge_models=[_model_slug(label) for label in args.judge_model],
        )
        existing_keys = {_judgement_key(item.draft_id, item.judge_model) for item in judgements}
        for judgement in imported:
            key = _judgement_key(judgement.draft_id, judgement.judge_model)
            if key not in existing_keys:
                judgements.append(judgement)
                existing_keys.add(key)

    judge_handoff_path = None
    if args.export_judge_prompts:
        judge_handoff_path = _write_external_judge_handoff(
            output_dir,
            case,
            drafts,
            judge_labels=args.judge_model or ["external-judge"],
        )

    report = ExperimentReport(
        case=case,
        variants=variants,
        drafts=drafts,
        judgements=judgements,
        created_at=utc_now_iso(),
        dry_run=args.dry_run,
    )
    paths = write_experiment_package(report, output_dir)
    print(f"manifest: {paths['manifest']}")
    print(f"html: {paths['html']}")
    print(f"prompt_handoff: {handoff_path}")
    if judge_handoff_path is not None:
        print(f"judge_handoff: {judge_handoff_path}")
    print(f"variants: {len(variants)} drafts: {len(drafts)} judgements: {len(judgements)}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", help="Path to a scene prompt trace JSON.")
    parser.add_argument("--manifest", help="Existing arena manifest.json to analyze.")
    parser.add_argument(
        "--manual-selection",
        help="manual-selection.json exported from the arena HTML page.",
    )
    parser.add_argument(
        "--analysis-out",
        help="Directory for manual selection analysis. Defaults beside the manifest.",
    )
    parser.add_argument(
        "--strategy-proposals",
        help=(
            "manual-selection-analysis.json whose next_round_strategy_proposals "
            "should be materialized into runnable round2 prompts."
        ),
    )
    parser.add_argument(
        "--prompt-manifest",
        help=(
            "prompt-manifest.json from a prompt-only or round2 prompt package. "
            "Use with --import-drafts to build a report from that exact strategy set."
        ),
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Audit whether an existing manifest satisfies the horizontal-test objective.",
    )
    parser.add_argument(
        "--audit-out",
        help="Directory for audit artifacts. Defaults beside the manifest.",
    )
    parser.add_argument(
        "--expected-strategies",
        type=int,
        default=20,
        help="Expected number of strategy variants for --audit.",
    )
    parser.add_argument(
        "--expected-writer-model",
        action="append",
        default=None,
        help=(
            "Writer model label expected for every strategy in --audit. May be repeated. "
            "Defaults to the target MiniMax/Qwen writer pair."
        ),
    )
    parser.add_argument(
        "--expected-judge-model",
        action="append",
        default=None,
        help=(
            "Judge model label expected for every draft in --audit. May be repeated. "
            "Defaults to the target MiniMax/DeepSeek judge pair."
        ),
    )
    parser.add_argument("--out", help="Output directory. Defaults under output/prose-prompt-arena.")
    parser.add_argument("--limit", type=int, default=20, help="Number of strategies to run.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate prompts/report without LLM calls.",
    )
    parser.add_argument(
        "--prompts-only",
        action="store_true",
        help="Only write the 20 prompt variants and external handoff; do not create drafts.",
    )
    parser.add_argument(
        "--import-drafts",
        help=(
            "Directory of externally generated .md drafts named <strategy_id>.md "
            "or <strategy_id>__*.md."
        ),
    )
    parser.add_argument(
        "--import-writer-model",
        default="external-writer",
        help="Writer label recorded for drafts loaded through --import-drafts.",
    )
    parser.add_argument(
        "--allow-partial-import",
        action="store_true",
        help="Allow --import-drafts to omit some strategies. Default requires all variants.",
    )
    parser.add_argument(
        "--export-judge-prompts",
        action="store_true",
        help="Write blind judge prompt packets for externally run judge models.",
    )
    parser.add_argument(
        "--import-judgements",
        help=(
            "Directory of external judge JSON outputs named <blind_label>__<judge>.json "
            "or <blind_label>.json."
        ),
    )
    parser.add_argument(
        "--writer-model",
        action="append",
        default=[],
        help="Writer model string. May be repeated. Omit to use configured writer.",
    )
    parser.add_argument(
        "--judge-model",
        action="append",
        default=[],
        help="Judge model string. May be repeated. Omit to use configured critic.",
    )
    parser.add_argument(
        "--skip-judging",
        action="store_true",
        help="Only generate drafts and the manual reading page; do not call LLM judges.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse drafts/judgements already present under --out.",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Print model availability and planned call counts; do not generate drafts.",
    )
    parser.add_argument(
        "--allow-unavailable-models",
        action="store_true",
        help="Do not fail when a catalog model's API key env is missing.",
    )
    parser.add_argument("--samples-per-strategy", type=int, default=1)
    parser.add_argument("--writer-max-tokens", type=int, default=4096)
    parser.add_argument("--judge-max-tokens", type=int, default=2048)
    return parser.parse_args(argv)


def _audit_expected_writer_models(args: argparse.Namespace) -> tuple[str, ...]:
    values = getattr(args, "expected_writer_model", None)
    return tuple(values or DEFAULT_AUDIT_WRITER_MODELS)


def _audit_expected_judge_models(args: argparse.Namespace) -> tuple[str, ...]:
    values = getattr(args, "expected_judge_model", None)
    return tuple(values or DEFAULT_AUDIT_JUDGE_MODELS)


def _analyze_manual_selection(
    manifest_path: Path,
    manual_selection_path: Path,
    output_dir: Path | None = None,
) -> dict[str, str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manual_selection = json.loads(manual_selection_path.read_text(encoding="utf-8"))
    root = manifest_path.parent
    output_dir = output_dir or root / "manual-analysis"
    output_dir.mkdir(parents=True, exist_ok=True)

    selections = _selection_items(manual_selection)
    drafts_by_label = {
        str(item.get("blind_label") or ""): item
        for item in _dict_list(manifest.get("drafts"))
        if item.get("blind_label")
    }
    variants_by_id = {
        str(item.get("variant_id") or ""): item
        for item in _dict_list(manifest.get("variants"))
        if item.get("variant_id")
    }
    rankings_by_draft = {
        str(item.get("draft_id") or ""): item
        for item in _dict_list(manifest.get("rankings"))
        if item.get("draft_id")
    }
    strategy_rankings_by_id = {
        str(item.get("strategy_id") or ""): item
        for item in _dict_list(manifest.get("strategy_rankings"))
        if item.get("strategy_id")
    }
    judgements_by_draft: dict[str, list[dict[str, object]]] = {}
    for judgement in _dict_list(manifest.get("judgements")):
        draft_id = str(judgement.get("draft_id") or "")
        if draft_id:
            judgements_by_draft.setdefault(draft_id, []).append(judgement)

    analyzed = [
        _manual_selection_row(
            label=label,
            selection=selection,
            draft=drafts_by_label.get(label),
            variants_by_id=variants_by_id,
            rankings_by_draft=rankings_by_draft,
            strategy_rankings_by_id=strategy_rankings_by_id,
            judgements_by_draft=judgements_by_draft,
            root=root,
        )
        for label, selection in selections.items()
    ]
    best = [item for item in analyzed if item["choice"] == "best"]
    useful = [item for item in analyzed if item["choice"] == "useful"]
    rejected = [item for item in analyzed if item["choice"] == "reject"]
    payload = {
        "case_id": manifest.get("case", {}).get("case_id") or manual_selection.get("case_id"),
        "manifest_path": str(manifest_path),
        "manual_selection_path": str(manual_selection_path),
        "best": best,
        "useful": useful,
        "rejected": rejected,
        "all_selected": analyzed,
        "diagnosis": _manual_selection_diagnosis(manifest, best, useful),
        "production_prompt_patch_candidates": _production_prompt_patch_candidates(
            best,
            useful,
        ),
        "outline_probe_checklist": _outline_probe_checklist(best, useful),
        "next_round_strategy_proposals": _next_round_strategy_proposals(
            manifest,
            best,
            useful,
        ),
        "next_experiment": _next_experiment_steps(best, useful),
    }

    json_path = output_dir / "manual-selection-analysis.json"
    md_path = output_dir / "manual-selection-analysis.md"
    _write_json_file(json_path, payload)
    md_path.write_text(_render_manual_selection_markdown(payload), encoding="utf-8")
    diagnosis = payload["diagnosis"]
    assert isinstance(diagnosis, dict)
    return {"json": str(json_path), "md": str(md_path), "summary": str(diagnosis["summary"])}


def _audit_experiment_manifest(
    manifest_path: Path,
    *,
    output_dir: Path | None = None,
    manual_selection_path: Path | None = None,
    expected_strategy_count: int = 20,
    expected_writer_models: Sequence[str] = (),
    expected_judge_models: Sequence[str] = (),
) -> dict[str, str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.parent
    output_dir = output_dir or root
    output_dir.mkdir(parents=True, exist_ok=True)

    variants = _dict_list(manifest.get("variants"))
    drafts = _dict_list(manifest.get("drafts"))
    judgements = _dict_list(manifest.get("judgements"))
    failures: list[dict[str, str]] = []
    pending: list[dict[str, str]] = []
    evidence: list[dict[str, object]] = []

    strategy_ids = [
        str(_object_dict(item.get("strategy")).get("strategy_id") or "")
        for item in variants
    ]
    strategy_ids = [item for item in strategy_ids if item]
    _record_check(
        evidence,
        failures,
        "strategy_count",
        len(strategy_ids) == expected_strategy_count,
        f"{len(strategy_ids)} strategies present; expected {expected_strategy_count}.",
        "Run prompt generation with the full default strategy set.",
    )
    _record_check(
        evidence,
        failures,
        "strategy_uniqueness",
        len(strategy_ids) == len(set(strategy_ids)),
        "Strategy ids are unique.",
        "Remove duplicate strategy ids before comparing outputs.",
    )

    prompt_missing = [
        strategy_id
        for strategy_id in strategy_ids
        if not (root / "prompts" / f"{strategy_id}.json").exists()
    ]
    _record_check(
        evidence,
        failures,
        "prompt_files",
        not prompt_missing,
        f"Prompt files checked for {len(strategy_ids)} strategies.",
        "Regenerate prompts; missing: " + ", ".join(prompt_missing[:10]),
    )

    drafts_by_strategy: dict[str, list[dict[str, object]]] = {}
    for draft in drafts:
        variant_id = str(draft.get("variant_id") or "")
        strategy_id = _strategy_id_for_variant(variants, variant_id)
        if strategy_id:
            drafts_by_strategy.setdefault(strategy_id, []).append(draft)
    missing_draft_strategies = [
        strategy_id for strategy_id in strategy_ids if not drafts_by_strategy.get(strategy_id)
    ]
    _record_check(
        evidence,
        failures,
        "draft_coverage",
        not missing_draft_strategies,
        f"{len(drafts)} drafts cover {len(drafts_by_strategy)} strategies.",
        "Generate or import drafts for: " + ", ".join(missing_draft_strategies[:10]),
    )

    missing_draft_files = [
        str(draft.get("draft_id") or "")
        for draft in drafts
        if not _draft_file_exists(root, draft)
    ]
    _record_check(
        evidence,
        failures,
        "draft_files",
        not missing_draft_files,
        f"Draft files checked for {len(drafts)} drafts.",
        "Regenerate package; missing draft files: " + ", ".join(missing_draft_files[:10]),
    )

    if expected_writer_models:
        writer_gaps = _writer_coverage_gaps(
            drafts_by_strategy,
            expected_writer_models=expected_writer_models,
        )
        _record_check(
            evidence,
            pending,
            "expected_writer_models",
            not writer_gaps,
            "Expected writer model coverage checked.",
            "Missing strategy/model drafts: " + ", ".join(writer_gaps[:10]),
        )
    else:
        writer_models = sorted({str(draft.get("writer_model") or "") for draft in drafts})
        evidence.append(
            {
                "check": "writer_models_observed",
                "passed": True,
                "detail": ", ".join(writer_models) if writer_models else "none",
            }
        )

    judgements_by_draft: dict[str, list[dict[str, object]]] = {}
    for judgement in judgements:
        draft_id = str(judgement.get("draft_id") or "")
        if draft_id:
            judgements_by_draft.setdefault(draft_id, []).append(judgement)
    missing_judgement_drafts = [
        str(draft.get("draft_id") or "")
        for draft in drafts
        if not judgements_by_draft.get(str(draft.get("draft_id") or ""))
    ]
    _record_check(
        evidence,
        pending,
        "judge_coverage",
        bool(drafts) and not missing_judgement_drafts,
        f"{len(judgements)} judgements cover {len(judgements_by_draft)} drafts.",
        "Run judges or import external judgements for: "
        + ", ".join(missing_judgement_drafts[:10]),
    )

    if expected_judge_models:
        judge_gaps = _judge_coverage_gaps(
            drafts,
            judgements_by_draft,
            expected_judge_models=expected_judge_models,
        )
        _record_check(
            evidence,
            pending,
            "expected_judge_models",
            not judge_gaps,
            "Expected judge model coverage checked.",
            "Missing draft/judge scores: " + ", ".join(judge_gaps[:10]),
        )

    if judgements:
        score_dimension_gaps = _judge_score_dimension_gaps(root, judgements)
        _record_check(
            evidence,
            pending,
            "judge_score_dimensions",
            not score_dimension_gaps,
            f"{len(judgements)} judgements checked for {len(_score_keys())} score dimensions.",
            "Re-run or re-import full judge JSON; missing dimensions: "
            + ", ".join(score_dimension_gaps[:10]),
        )

    html_path = root / "report.html"
    html = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
    _record_check(
        evidence,
        failures,
        "html_report",
        html_path.exists() and "人工横读区" in html and "STRATEGY_BY_LABEL" in html,
        f"HTML report path: {html_path}",
        "Regenerate package so the manual reading page and reveal map exist.",
    )

    manual_state = _manual_selection_state(
        manual_selection_path,
        expected_labels=[
            str(draft.get("blind_label") or "")
            for draft in drafts
            if draft.get("blind_label")
        ],
    )
    has_manual_final_decision = bool(manual_state.get("final_decision"))
    _record_check(
        evidence,
        pending,
        "manual_final_selection",
        has_manual_final_decision,
        str(manual_state.get("detail") or ""),
        "Export manual-selection.json from report.html, choose a best draft or mark all "
        "blind drafts when there is no winner, then analyze it.",
    )
    analysis_state = _manual_analysis_state(
        root,
        manual_state,
        manifest_path=manifest_path,
        manifest=manifest,
        manual_selection_path=manual_selection_path,
    )
    _record_check(
        evidence,
        pending,
        "manual_selection_analysis",
        (not has_manual_final_decision) or bool(analysis_state.get("complete")),
        str(analysis_state.get("detail") or ""),
        "Run --manifest <manifest.json> --manual-selection <manual-selection.json> "
        "so the best strategy, prompt patch candidates, and outline probes are materialized.",
    )

    status = "complete"
    if failures:
        status = "incomplete"
    elif pending:
        status = "pending_human_or_external"
    payload = {
        "manifest_path": str(manifest_path),
        "status": status,
        "summary": _audit_summary(status, failures, pending),
        "counts": {
            "variants": len(variants),
            "strategies": len(strategy_ids),
            "drafts": len(drafts),
            "judgements": len(judgements),
        },
        "expected": {
            "strategy_count": expected_strategy_count,
            "writer_models": list(expected_writer_models),
            "judge_models": list(expected_judge_models),
        },
        "evidence": evidence,
        "failures": failures,
        "pending": pending,
        "manual_selection": manual_state,
        "manual_selection_analysis": analysis_state,
    }
    json_path = output_dir / "experiment-audit.json"
    md_path = output_dir / "experiment-audit.md"
    _write_json_file(json_path, payload)
    md_path.write_text(_render_audit_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path), "summary": str(payload["summary"])}


def _export_judge_prompts_from_manifest(
    manifest_path: Path,
    *,
    output_dir: Path | None,
    judge_labels: Sequence[str],
) -> Path:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.parent
    output_dir = output_dir or root
    case = _case_from_manifest(manifest, manifest_path)
    drafts = _drafts_from_manifest(root, manifest)
    if not drafts:
        raise RuntimeError(f"Manifest has no loadable drafts: {manifest_path}")
    return _write_external_judge_handoff(
        output_dir,
        case,
        drafts,
        judge_labels=judge_labels,
    )


def _merge_judgements_into_manifest(
    manifest_path: Path,
    judgements_dir: Path,
    *,
    output_dir: Path | None,
) -> dict[str, str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.parent
    output_dir = output_dir or root
    case = _case_from_manifest(manifest, manifest_path)
    variants = _variants_from_manifest(root, manifest, case.case_id)
    drafts = _drafts_from_manifest(root, manifest)
    if not drafts:
        raise RuntimeError(f"Manifest has no loadable drafts: {manifest_path}")
    if not variants:
        raise RuntimeError(f"Manifest has no variants: {manifest_path}")
    existing = _judgements_from_manifest(root, manifest)
    expected_judge_models = _expected_judges_from_handoff_manifest(root)
    imported = _import_external_judgements(
        drafts,
        judgements_dir,
        expected_judge_models=expected_judge_models,
    )
    by_key = {_judgement_key(item.draft_id, item.judge_model): item for item in existing}
    imported_count = 0
    for judgement in imported:
        key = _judgement_key(judgement.draft_id, judgement.judge_model)
        if key in by_key:
            continue
        by_key[key] = judgement
        imported_count += 1
    report = ExperimentReport(
        case=case,
        variants=variants,
        drafts=drafts,
        judgements=sorted(
            by_key.values(),
            key=lambda item: (item.draft_id, item.judge_model),
        ),
        created_at=utc_now_iso(),
        dry_run=bool(manifest.get("dry_run")),
    )
    paths = write_experiment_package(report, output_dir)
    return {
        "manifest": paths["manifest"],
        "html": paths["html"],
        "imported_judgements": str(imported_count),
        "total_judgements": str(len(report.judgements)),
    }


def _materialize_strategy_proposals(
    manifest_path: Path,
    analysis_path: Path,
    *,
    output_dir: Path | None,
) -> dict[str, str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    root = manifest_path.parent
    output_dir = output_dir or root / "round2-prompts"
    output_dir.mkdir(parents=True, exist_ok=True)

    case = _case_from_manifest_with_prompts(manifest, manifest_path)
    strategies = _strategies_from_round2_proposals(analysis)
    if not strategies:
        raise RuntimeError(
            f"No next_round_strategy_proposals found in analysis file: {analysis_path}"
        )
    variants = build_prompt_variants(case, strategies)
    _write_prompt_variants(output_dir, variants)
    _write_external_writer_prompt_files(output_dir, variants)
    handoff_path = _write_external_prompt_handoff(output_dir, variants)
    manifest_path_out = _write_prompt_only_manifest(output_dir, case, variants)
    _write_json_file(
        output_dir / "round2-source.json",
        {
            "source_manifest": str(manifest_path),
            "source_analysis": str(analysis_path),
            "source_run_root": str(root),
            "proposal_count": len(strategies),
            "strategy_ids": [item.strategy_id for item in strategies],
        },
    )
    return {
        "prompt_manifest": str(manifest_path_out),
        "prompt_handoff": str(handoff_path),
        "variant_count": str(len(variants)),
    }


async def _run_prompt_manifest_import(
    prompt_manifest_path: Path,
    args: argparse.Namespace,
) -> dict[str, str]:
    if not args.import_drafts:
        raise RuntimeError("--prompt-manifest requires --import-drafts.")
    prompt_manifest = json.loads(prompt_manifest_path.read_text(encoding="utf-8"))
    case, variants = _case_and_variants_from_prompt_manifest(prompt_manifest_path)
    output_dir = Path(args.out) if args.out else prompt_manifest_path.parent / "report"
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_prompt_variants(output_dir, variants)
    _write_external_writer_prompt_files(output_dir, variants)
    prompt_manifest_out = _write_prompt_only_manifest(output_dir, case, variants)
    _write_external_prompt_handoff(output_dir, variants)
    drafts = _import_external_drafts(
        variants,
        Path(args.import_drafts),
        writer_model=args.import_writer_model,
        allow_partial=args.allow_partial_import,
        expected_writer_models=_string_list(prompt_manifest.get("expected_writer_models")),
    )
    judgements: list[JudgeResult] = []
    if args.import_judgements:
        judgements.extend(_import_external_judgements(drafts, Path(args.import_judgements)))
    elif not args.skip_judging:
        settings = load_settings()
        judge_models = _resolve_model_specs(
            args.judge_model,
            default_label="configured-critic",
            allow_unavailable=args.allow_unavailable_models,
        )
        judgements = await _run_judges(
            case,
            drafts,
            settings=settings,
            judge_models=judge_models,
            max_tokens=args.judge_max_tokens,
            output_dir=output_dir,
            resume=args.resume,
        )

    judge_handoff_path = None
    if args.export_judge_prompts:
        judge_handoff_path = _write_external_judge_handoff(
            output_dir,
            case,
            drafts,
            judge_labels=args.judge_model or ["external-judge"],
        )

    report = ExperimentReport(
        case=case,
        variants=list(variants),
        drafts=drafts,
        judgements=judgements,
        created_at=utc_now_iso(),
        dry_run=False,
    )
    paths = write_experiment_package(report, output_dir)
    return {
        "manifest": paths["manifest"],
        "html": paths["html"],
        "prompt_manifest": str(prompt_manifest_out),
        "judge_handoff": str(judge_handoff_path) if judge_handoff_path else "",
        "variants": str(len(variants)),
        "drafts": str(len(drafts)),
        "judgements": str(len(judgements)),
    }


def _case_and_variants_from_prompt_manifest(
    prompt_manifest_path: Path,
) -> tuple[PromptTraceCase, list[PromptVariant]]:
    manifest = json.loads(prompt_manifest_path.read_text(encoding="utf-8"))
    root = prompt_manifest_path.parent
    case_payload = _object_dict(manifest.get("case"))
    variants_payload = _dict_list(manifest.get("variants"))
    if not variants_payload:
        raise RuntimeError(f"Prompt manifest has no variants: {prompt_manifest_path}")
    prompts = _prompt_payload_for_manifest_variant(root, variants_payload[0])
    case = PromptTraceCase(
        case_id=str(case_payload.get("case_id") or root.name),
        source_path=str(case_payload.get("source_path") or prompt_manifest_path),
        system_prompt=str(prompts.get("system_prompt") or ""),
        user_prompt=str(prompts.get("user_prompt") or ""),
        project=_object_dict(case_payload.get("project")),
        chapter=_object_dict(case_payload.get("chapter")),
        scene=_object_dict(case_payload.get("scene")),
    )
    variants: list[PromptVariant] = []
    for item in variants_payload:
        strategy_id = str(item.get("strategy_id") or "")
        if not strategy_id:
            continue
        prompt_payload = _prompt_payload_for_manifest_variant(root, item)
        strategy = PromptStrategy(
            strategy_id=strategy_id,
            title=str(item.get("strategy_title") or strategy_id),
            hypothesis=str(item.get("hypothesis") or ""),
            instruction=str(item.get("instruction") or ""),
            diagnostic_focus=str(item.get("diagnostic_focus") or ""),
        )
        variants.append(
            PromptVariant(
                variant_id=str(item.get("variant_id") or f"{case.case_id}__{strategy_id}"),
                case_id=case.case_id,
                strategy=strategy,
                system_prompt=str(prompt_payload.get("system_prompt") or case.system_prompt),
                user_prompt=str(prompt_payload.get("user_prompt") or case.user_prompt),
            )
        )
    if not variants:
        raise RuntimeError(f"Prompt manifest has no usable variants: {prompt_manifest_path}")
    return case, variants


def _prompt_payload_for_manifest_variant(
    root: Path,
    item: dict[str, object],
) -> dict[str, object]:
    prompt_path = _resolve_manifest_path(root, item.get("prompt_path"))
    if prompt_path and prompt_path.exists():
        try:
            return _object_dict(json.loads(prompt_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return {}
    return _prompt_payload_for_strategy(root, str(item.get("strategy_id") or ""))


def _case_from_manifest_with_prompts(
    manifest: dict[str, object],
    manifest_path: Path,
) -> PromptTraceCase:
    case = _case_from_manifest(manifest, manifest_path)
    if case.system_prompt and case.user_prompt:
        return case
    prompts = _prompt_payload_for_control(manifest_path.parent)
    return PromptTraceCase(
        case_id=f"{case.case_id}-round2",
        source_path=case.source_path,
        system_prompt=str(prompts.get("system_prompt") or case.system_prompt),
        user_prompt=str(prompts.get("user_prompt") or case.user_prompt),
        project=case.project,
        chapter=case.chapter,
        scene=case.scene,
        prompt_stats=case.prompt_stats,
    )


def _prompt_payload_for_control(root: Path) -> dict[str, object]:
    payload = _prompt_payload_for_strategy(root, "production_control")
    if payload:
        return payload
    prompt_paths = sorted((root / "prompts").glob("*.json"))
    for path in prompt_paths:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload = _object_dict(loaded)
        if payload.get("system_prompt") and payload.get("user_prompt"):
            return payload
    return {}


def _strategies_from_round2_proposals(
    analysis: dict[str, object],
) -> list[PromptStrategy]:
    strategies: list[PromptStrategy] = []
    for item in _dict_list(analysis.get("next_round_strategy_proposals")):
        proposal_id = _model_slug(str(item.get("proposal_id") or "round2_strategy"))
        rules = _string_list(item.get("prompt_rules"))
        instruction_parts = [
            "本轮是二代正文 prompt 实验，请只执行下列策略规则：",
            *[f"{idx}. {rule}" for idx, rule in enumerate(rules, start=1)],
        ]
        outline_probe = str(item.get("outline_probe") or "").strip()
        if outline_probe:
            instruction_parts.append(f"大纲/细纲反查点：{outline_probe}")
        run_note = str(item.get("run_note") or "").strip()
        if run_note:
            instruction_parts.append(f"实验备注：{run_note}")
        strategies.append(
            PromptStrategy(
                strategy_id=proposal_id,
                title=str(item.get("title") or proposal_id),
                hypothesis=str(item.get("hypothesis") or ""),
                instruction="\n".join(part for part in instruction_parts if part),
                diagnostic_focus="round2_strategy",
            )
        )
    return strategies


def _case_from_manifest(
    manifest: dict[str, object],
    manifest_path: Path,
) -> PromptTraceCase:
    case = _object_dict(manifest.get("case"))
    return PromptTraceCase(
        case_id=str(case.get("case_id") or manifest_path.parent.name),
        source_path=str(case.get("source_path") or manifest_path),
        system_prompt="",
        user_prompt="",
        project=_object_dict(case.get("project")),
        chapter=_object_dict(case.get("chapter")),
        scene=_object_dict(case.get("scene")),
        prompt_stats=_object_dict(case.get("prompt_stats")),
    )


def _variants_from_manifest(
    root: Path,
    manifest: dict[str, object],
    case_id: str,
) -> list[PromptVariant]:
    variants: list[PromptVariant] = []
    for item in _dict_list(manifest.get("variants")):
        strategy_payload = _object_dict(item.get("strategy"))
        strategy_id = str(strategy_payload.get("strategy_id") or "")
        if not strategy_id:
            continue
        prompt_payload = _prompt_payload_for_strategy(root, strategy_id)
        strategy = PromptStrategy(
            strategy_id=strategy_id,
            title=str(strategy_payload.get("title") or strategy_id),
            hypothesis=str(strategy_payload.get("hypothesis") or ""),
            instruction=str(strategy_payload.get("instruction") or ""),
            diagnostic_focus=str(strategy_payload.get("diagnostic_focus") or ""),
        )
        variants.append(
            PromptVariant(
                variant_id=str(item.get("variant_id") or f"{case_id}__{strategy_id}"),
                case_id=case_id,
                strategy=strategy,
                system_prompt=str(prompt_payload.get("system_prompt") or ""),
                user_prompt=str(prompt_payload.get("user_prompt") or ""),
            )
        )
    return variants


def _prompt_payload_for_strategy(root: Path, strategy_id: str) -> dict[str, object]:
    path = root / "prompts" / f"{strategy_id}.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return _object_dict(payload)


def _drafts_from_manifest(root: Path, manifest: dict[str, object]) -> list[DraftResult]:
    drafts: list[DraftResult] = []
    for item in _dict_list(manifest.get("drafts")):
        draft = _draft_from_manifest_item(root, item)
        if draft is not None:
            drafts.append(draft)
    return drafts


def _draft_from_manifest_item(
    root: Path,
    item: dict[str, object],
) -> DraftResult | None:
    draft_id = str(item.get("draft_id") or "")
    if not draft_id:
        return None
    json_path = root / "drafts" / f"{draft_id}.json"
    if json_path.exists():
        try:
            stored = draft_from_dict(json.loads(json_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError):
            stored = None
        if stored is not None and stored.text:
            return stored
    output_path = _resolve_manifest_path(root, item.get("output_path"))
    text = ""
    if output_path and output_path.exists():
        text = output_path.read_text(encoding="utf-8").strip()
    if not text:
        md_path = root / "drafts" / f"{draft_id}.md"
        text = md_path.read_text(encoding="utf-8").strip() if md_path.exists() else ""
    if not text:
        return None
    return DraftResult(
        draft_id=draft_id,
        variant_id=str(item.get("variant_id") or ""),
        writer_model=str(item.get("writer_model") or ""),
        sample_index=int(item.get("sample_index") or 1),
        text=text,
        provider=_optional_str(item.get("provider")),
        finish_reason=_optional_str(item.get("finish_reason")),
        output_path=str(output_path) if output_path else None,
    )


def _judgements_from_manifest(
    root: Path,
    manifest: dict[str, object],
) -> list[JudgeResult]:
    by_key = dict(_load_existing_judgements(root))
    for item in _dict_list(manifest.get("judgements")):
        try:
            judgement = judgement_from_dict(item)
        except (TypeError, ValueError):
            continue
        if judgement.draft_id and judgement.judge_model:
            by_key.setdefault(_judgement_key(judgement.draft_id, judgement.judge_model), judgement)
    return list(by_key.values())


def _record_check(
    evidence: list[dict[str, object]],
    problems: list[dict[str, str]],
    check: str,
    passed: bool,
    detail: str,
    remediation: str,
) -> None:
    evidence.append({"check": check, "passed": passed, "detail": detail})
    if not passed:
        problems.append({"check": check, "detail": detail, "remediation": remediation})


def _strategy_id_for_variant(
    variants: Sequence[dict[str, object]],
    variant_id: str,
) -> str:
    for variant in variants:
        if str(variant.get("variant_id") or "") != variant_id:
            continue
        strategy = variant.get("strategy") if isinstance(variant.get("strategy"), dict) else {}
        assert isinstance(strategy, dict)
        return str(strategy.get("strategy_id") or "")
    return ""


def _draft_file_exists(root: Path, draft: dict[str, object]) -> bool:
    output_path = _resolve_manifest_path(root, draft.get("output_path"))
    if output_path is not None and output_path.exists():
        return True
    draft_id = str(draft.get("draft_id") or "")
    return bool(draft_id and (root / "drafts" / f"{draft_id}.md").exists())


def _writer_coverage_gaps(
    drafts_by_strategy: dict[str, list[dict[str, object]]],
    *,
    expected_writer_models: Sequence[str],
) -> list[str]:
    gaps: list[str] = []
    for strategy_id, drafts in drafts_by_strategy.items():
        observed = {str(draft.get("writer_model") or "") for draft in drafts}
        for model in expected_writer_models:
            if model not in observed:
                gaps.append(f"{strategy_id}/{model}")
    return gaps


def _judge_coverage_gaps(
    drafts: Sequence[dict[str, object]],
    judgements_by_draft: dict[str, list[dict[str, object]]],
    *,
    expected_judge_models: Sequence[str],
) -> list[str]:
    gaps: list[str] = []
    for draft in drafts:
        draft_id = str(draft.get("draft_id") or "")
        observed = {
            str(judgement.get("judge_model") or "")
            for judgement in judgements_by_draft.get(draft_id, [])
        }
        for model in expected_judge_models:
            if model not in observed:
                gaps.append(f"{draft_id}/{model}")
    return gaps


def _judge_score_dimension_gaps(
    root: Path,
    judgements: Sequence[dict[str, object]],
) -> list[str]:
    stored = _load_existing_judgements(root)
    expected = set(_score_keys())
    gaps: list[str] = []
    for judgement in judgements:
        draft_id = str(judgement.get("draft_id") or "")
        judge_model = str(judgement.get("judge_model") or "")
        if not draft_id or not judge_model:
            continue
        present = _present_score_keys_for_audit(
            judgement,
            stored.get(_judgement_key(draft_id, judge_model)),
        )
        missing = sorted(expected - present)
        if missing:
            gaps.append(f"{draft_id}/{judge_model}: {','.join(missing)}")
    return gaps


def _present_score_keys_for_audit(
    manifest_judgement: dict[str, object],
    stored_judgement: JudgeResult | None,
) -> set[str]:
    score_keys = manifest_judgement.get("score_keys")
    if isinstance(score_keys, list):
        return {str(item) for item in score_keys}
    if stored_judgement is not None and stored_judgement.raw_text:
        payload = _try_json(stored_judgement.raw_text)
        if isinstance(payload, dict):
            raw_scores = payload.get("scores")
            if isinstance(raw_scores, dict):
                return {str(key) for key in raw_scores}
    scores = manifest_judgement.get("scores")
    if isinstance(scores, dict):
        return {str(key) for key in scores}
    if stored_judgement is not None:
        return set(stored_judgement.scores)
    return set()


def _manual_selection_state(
    manual_selection_path: Path | None,
    *,
    expected_labels: Sequence[str] = (),
) -> dict[str, object]:
    if manual_selection_path is None:
        return {
            "path": "",
            "best_labels": [],
            "useful_labels": [],
            "reject_labels": [],
            "selection_count": 0,
            "final_decision": False,
            "detail": "Manual selection is not available yet.",
        }
    try:
        payload = json.loads(manual_selection_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "path": str(manual_selection_path),
            "best_labels": [],
            "useful_labels": [],
            "reject_labels": [],
            "selection_count": 0,
            "final_decision": False,
            "detail": "Manual selection file is missing or invalid.",
        }
    selections = _selection_items(payload)
    choices_by_label = {
        label: str(item.get("choice") or "")
        for label, item in selections.items()
        if str(item.get("choice") or "")
    }
    best_labels = [
        label for label, choice in choices_by_label.items() if choice == "best"
    ]
    useful_labels = [
        label for label, choice in choices_by_label.items() if choice == "useful"
    ]
    reject_labels = [
        label for label, choice in choices_by_label.items() if choice == "reject"
    ]
    expected = {label for label in expected_labels if label}
    selected = set(choices_by_label)
    all_expected_selected = bool(expected) and expected.issubset(selected)
    final_decision = bool(best_labels) or all_expected_selected
    if best_labels:
        detail = "Manual best labels: " + ", ".join(best_labels)
    elif all_expected_selected:
        detail = (
            "Manual no-winner/useful-only decision covers all blind drafts: "
            f"{len(selected & expected)}/{len(expected)} selected."
        )
    else:
        detail = (
            "Manual selection is incomplete: "
            f"{len(selected & expected)}/{len(expected)} expected blind drafts selected."
            if expected
            else "Manual selection has no best label and no expected blind-label set."
        )
    return {
        "path": str(manual_selection_path),
        "best_labels": best_labels,
        "useful_labels": useful_labels,
        "reject_labels": reject_labels,
        "selection_count": len(selections),
        "choices_by_label": choices_by_label,
        "expected_label_count": len(expected),
        "final_decision": final_decision,
        "detail": detail,
    }


def _manual_analysis_state(
    root: Path,
    manual_state: dict[str, object],
    *,
    manifest_path: Path,
    manifest: dict[str, object],
    manual_selection_path: Path | None,
) -> dict[str, object]:
    if not manual_state.get("final_decision"):
        return {
            "path": str(root / "manual-analysis" / "manual-selection-analysis.json"),
            "complete": False,
            "detail": "Manual final selection is not available yet.",
        }
    analysis_path = root / "manual-analysis" / "manual-selection-analysis.json"
    markdown_path = root / "manual-analysis" / "manual-selection-analysis.md"
    try:
        payload = json.loads(analysis_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "path": str(analysis_path),
            "markdown_path": str(markdown_path),
            "complete": False,
            "detail": f"Manual selection analysis not found or invalid: {analysis_path}",
        }
    required_keys = (
        "diagnosis",
        "production_prompt_patch_candidates",
        "outline_probe_checklist",
        "next_round_strategy_proposals",
    )
    missing = [key for key in required_keys if key not in payload]
    mismatches = _manual_analysis_mismatches(
        payload,
        manifest_path=manifest_path,
        manifest=manifest,
        manual_selection_path=manual_selection_path,
        manual_state=manual_state,
    )
    complete = not missing and not mismatches and markdown_path.exists()
    detail = (
        "Manual selection analysis is present with strategy back-projection."
        if complete
        else "Manual selection analysis is stale or incomplete; missing/mismatch: "
        + ", ".join(
            missing
            + mismatches
            + ([] if markdown_path.exists() else ["markdown"])
        )
    )
    return {
        "path": str(analysis_path),
        "markdown_path": str(markdown_path),
        "complete": complete,
        "detail": detail,
    }


def _manual_analysis_mismatches(
    payload: dict[str, object],
    *,
    manifest_path: Path,
    manifest: dict[str, object],
    manual_selection_path: Path | None,
    manual_state: dict[str, object],
) -> list[str]:
    mismatches: list[str] = []
    if _same_path(payload.get("manifest_path"), manifest_path) is False:
        mismatches.append("manifest_path")
    if manual_selection_path is not None and _same_path(
        payload.get("manual_selection_path"),
        manual_selection_path,
    ) is False:
        mismatches.append("manual_selection_path")

    expected_case_id = str(_object_dict(manifest.get("case")).get("case_id") or "")
    if expected_case_id and str(payload.get("case_id") or "") != expected_case_id:
        mismatches.append("case_id")

    best_labels = {str(label) for label in manual_state.get("best_labels", [])}
    analyzed_best_labels = {
        str(_object_dict(item).get("blind_label") or "")
        for item in _dict_list(payload.get("best"))
    }
    if best_labels and not best_labels.issubset(analyzed_best_labels):
        mismatches.append("best_labels")
    manual_choices = {
        str(label): str(choice)
        for label, choice in _object_dict(manual_state.get("choices_by_label")).items()
    }
    analyzed_choices = {
        str(_object_dict(item).get("blind_label") or ""): str(
            _object_dict(item).get("choice") or ""
        )
        for item in _dict_list(payload.get("all_selected"))
        if _object_dict(item).get("blind_label")
    }
    if manual_choices and manual_choices != analyzed_choices:
        mismatches.append("selection_choices")
    return mismatches


def _same_path(value: object, expected: Path) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return Path(value).expanduser().resolve() == expected.expanduser().resolve()
    except OSError:
        return Path(value).expanduser() == expected.expanduser()


def _audit_summary(
    status: str,
    failures: Sequence[dict[str, str]],
    pending: Sequence[dict[str, str]],
) -> str:
    if status == "complete":
        return "实验证据已齐：可用于回灌正文 prompt 策略。"
    if failures:
        return f"实验结构未完成：{len(failures)} 个硬缺口需要修复。"
    return f"实验结构已就绪，但还有 {len(pending)} 个外部/人工环节未完成。"


def _render_audit_markdown(payload: dict[str, object]) -> str:
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    expected = payload.get("expected") if isinstance(payload.get("expected"), dict) else {}
    count_line = (
        f"{counts.get('variants')}/{counts.get('drafts')}/"
        f"{counts.get('judgements')}"
    )
    lines = [
        "# 正文提示词横评完成度审计",
        "",
        f"- status: {payload.get('status') or ''}",
        f"- summary: {payload.get('summary') or ''}",
        f"- manifest: {payload.get('manifest_path') or ''}",
        f"- variants/drafts/judgements: {count_line}",
        f"- expected strategies: {expected.get('strategy_count') or ''}",
        "",
    ]
    for title, key in (("硬缺口", "failures"), ("待外部/人工完成", "pending")):
        rows = payload.get(key)
        if not isinstance(rows, list) or not rows:
            continue
        lines.extend([f"## {title}", ""])
        for row in rows:
            if not isinstance(row, dict):
                continue
            lines.extend(
                [
                    f"- {row.get('check')}: {row.get('detail')}",
                    f"  - 修复: {row.get('remediation')}",
                    "",
                ]
            )
    evidence = payload.get("evidence")
    if isinstance(evidence, list):
        lines.extend(["## 证据清单", ""])
        for row in evidence:
            if not isinstance(row, dict):
                continue
            mark = "PASS" if row.get("passed") else "MISS"
            lines.append(f"- {mark} {row.get('check')}: {row.get('detail')}")
        lines.append("")
    return "\n".join(lines)


def _manual_selection_row(
    *,
    label: str,
    selection: dict[str, object],
    draft: dict[str, object] | None,
    variants_by_id: dict[str, dict[str, object]],
    rankings_by_draft: dict[str, dict[str, object]],
    strategy_rankings_by_id: dict[str, dict[str, object]],
    judgements_by_draft: dict[str, list[dict[str, object]]],
    root: Path,
) -> dict[str, object]:
    if draft is None:
        return {
            "blind_label": label,
            "choice": str(selection.get("choice") or ""),
            "notes": str(selection.get("notes") or ""),
            "error": "blind label was not found in manifest drafts",
        }
    variant = variants_by_id.get(str(draft.get("variant_id") or ""), {})
    strategy = variant.get("strategy") if isinstance(variant.get("strategy"), dict) else {}
    assert isinstance(strategy, dict)
    strategy_id = str(strategy.get("strategy_id") or "")
    draft_path = _resolve_manifest_path(root, draft.get("output_path"))
    prompt_path = root / "prompts" / f"{strategy_id}.json" if strategy_id else None
    draft_id = str(draft.get("draft_id") or "")
    return {
        "blind_label": label,
        "choice": str(selection.get("choice") or ""),
        "notes": str(selection.get("notes") or ""),
        "draft_id": draft_id,
        "variant_id": str(draft.get("variant_id") or ""),
        "writer_model": str(draft.get("writer_model") or ""),
        "sample_index": draft.get("sample_index"),
        "draft_path": str(draft_path) if draft_path else "",
        "prompt_path": str(prompt_path) if prompt_path else "",
        "strategy": {
            "strategy_id": strategy_id,
            "title": str(strategy.get("title") or ""),
            "hypothesis": str(strategy.get("hypothesis") or ""),
            "instruction": str(strategy.get("instruction") or ""),
            "diagnostic_focus": str(strategy.get("diagnostic_focus") or ""),
        },
        "design_summary": _strategy_takeaway(strategy),
        "judge_scores": judgements_by_draft.get(draft_id, []),
        "ranking": rankings_by_draft.get(draft_id, {}),
        "strategy_ranking": strategy_rankings_by_id.get(strategy_id, {}),
        "takeaway": _strategy_takeaway(strategy),
    }


def _manual_selection_diagnosis(
    manifest: dict[str, object],
    best: list[dict[str, object]],
    useful: list[dict[str, object]],
) -> dict[str, object]:
    manifest_diagnosis = (
        manifest.get("diagnosis") if isinstance(manifest.get("diagnosis"), dict) else {}
    )
    if best:
        titles = ", ".join(
            str(item.get("strategy", {}).get("title") or item.get("blind_label"))
            for item in best
            if isinstance(item.get("strategy"), dict)
        )
        return {
            "status": "manual_best_found",
            "summary": (
                f"人工最优已映射到策略：{titles}。"
                "优先拆解这些 prompt 约束并回灌生产 writer。"
            ),
            "outline_back_projection": (
                "如果最优策略集中在开篇、爽点或结尾悬念，说明方法论不是没有，"
                "而是生产 prompt 需要把抽象术语翻译成当前场景的可执行动作。"
            ),
            "manifest_diagnosis": manifest_diagnosis,
        }
    if useful:
        return {
            "status": "manual_useful_only",
            "summary": "没有人工最优，但存在可取片段；应组合可取策略，并回查大纲/细纲缺口。",
            "outline_back_projection": (
                "优先检查细纲是否已经给出第一眼钩子、压迫、爽点兑现、反馈、"
                "章末新问题；如果这些字段为空，单靠正文 prompt 很难救。"
            ),
            "manifest_diagnosis": manifest_diagnosis,
        }
    return {
        "status": "manual_no_winner",
        "summary": (
            "人工选择没有最优或可取项；"
            "更可能是场景合同/细纲素材不足，或 writer 模型不适配。"
        ),
        "outline_back_projection": (
            "先补细纲：开场异常、主角欲望、阻力、代价、可见爽点反馈、结尾悬念；"
            "再重新跑同一横评，不要继续堆更多抽象方法论词。"
        ),
        "manifest_diagnosis": manifest_diagnosis,
    }


def _production_prompt_patch_candidates(
    best: list[dict[str, object]],
    useful: list[dict[str, object]],
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for priority, row in enumerate([*best, *useful], start=1):
        strategy = row.get("strategy") if isinstance(row.get("strategy"), dict) else {}
        if not isinstance(strategy, dict):
            continue
        strategy_id = str(strategy.get("strategy_id") or "")
        instruction = str(strategy.get("instruction") or "")
        if not strategy_id or not instruction:
            continue
        candidates.append(
            {
                "priority": str(priority),
                "source_blind_label": str(row.get("blind_label") or ""),
                "source_strategy_id": strategy_id,
                "source_strategy_title": str(strategy.get("title") or ""),
                "patch_intent": _patch_intent_for_focus(
                    str(strategy.get("diagnostic_focus") or "")
                ),
                "candidate_prompt_rule": instruction,
                "integration_note": (
                    "把这条作为 scene-specific hard requirement 注入正文 prompt，"
                    "不要只放在方法论摘要里。"
                ),
            }
        )
    return candidates


def _outline_probe_checklist(
    best: list[dict[str, object]],
    useful: list[dict[str, object]],
) -> list[dict[str, str]]:
    focuses = {
        str(row.get("strategy", {}).get("diagnostic_focus") or "")
        for row in [*best, *useful]
        if isinstance(row.get("strategy"), dict)
    }
    checklist = [
        {
            "field": "first_visible_hook",
            "question": "细纲是否给出第一眼异常/危险/压力，而不是只给背景说明？",
            "why": "没有可见钩子时，黄金三章类 prompt 只能临时补，稳定性差。",
        },
        {
            "field": "payoff_contract",
            "question": "本场爽点是否具备压迫、选择、执行、反馈四拍？",
            "why": "缺反馈时，正文容易写成剧情摘要，读者感不到爽。",
        },
        {
            "field": "ending_new_question",
            "question": "场景/章节末尾是否有压过旧问题的新问题、代价或强敌动作？",
            "why": "只写状态完成会平收，章末悬念策略也无从落地。",
        },
        {
            "field": "materialized_action_chain",
            "question": "行动链是否绑定具体道具、规则、人物反应和不可逆变化？",
            "why": "素材不具体时，模型会回到解释、总结和抽象情绪。",
        },
    ]
    if "outline_probe" in focuses:
        checklist.insert(
            0,
            {
                "field": "outline_contract_completeness",
                "question": "如果大纲缺目标/阻力/代价/状态变化，是否先补细纲而不是继续改 prompt？",
                "why": "大纲缺结构时，正文 prompt 的提升会很快见顶。",
            },
        )
    return checklist


def _next_experiment_steps(
    best: list[dict[str, object]],
    useful: list[dict[str, object]],
) -> list[str]:
    if best:
        return [
            "把人工最优策略的硬约束合并进生产 writer prompt，保留原控制组作回归基线。",
            "用同一 trace 重跑：生产原样、新生产 prompt、人工最优策略、人工可取策略。",
            "如果新生产 prompt 能接近或超过人工最优策略，再扩大到 3-5 个不同章节位置验证。",
        ]
    if useful:
        return [
            "把可取策略拆成最小 prompt 规则，组合成 3-5 个二代策略，而不是新增 20 个散点策略。",
            "先补细纲里的开篇钩子、爽点合同、结尾问题，再用同一 writer model 复测。",
            "保留失败样本，标注每篇失败是素材缺失、prompt 执行失败，还是模型表达问题。",
        ]
    return [
        "先暂停扩大 prompt 试验，回到大纲/细纲检查目标、阻力、代价、爽点、末尾悬念是否齐全。",
        "补齐场景合同后只跑 5 个高信号策略，确认素材修复是否比 prompt 堆叠更有效。",
        "如果 5 个高信号策略仍无提升，再更换 writer model 或调整正文长度/上下文预算。",
    ]


def _next_round_strategy_proposals(
    manifest: dict[str, object],
    best: list[dict[str, object]],
    useful: list[dict[str, object]],
) -> list[dict[str, object]]:
    selected = [*best, *useful]
    weak_dimensions = _weak_dimension_rows(manifest)
    if selected:
        selected_rules = _selected_prompt_rules(selected)
        source_labels = [
            str(row.get("blind_label") or "")
            for row in selected
            if row.get("blind_label")
        ]
        primary = selected[0]
        primary_strategy = (
            primary.get("strategy") if isinstance(primary.get("strategy"), dict) else {}
        )
        assert isinstance(primary_strategy, dict)
        proposals: list[dict[str, object]] = [
            {
                "proposal_id": "round2_selected_fusion",
                "title": "人工有效策略融合版",
                "hypothesis": "把人工最优/可取项压缩成少数硬约束，比单条策略更稳定。",
                "source_blind_labels": source_labels,
                "prompt_rules": selected_rules[:4],
                "outline_probe": "只在细纲已具备开篇钩子、爽点反馈和结尾问题时测试，否则先补细纲。",
                "success_metric": "同一 trace 下 overall 接近或超过人工最优；弱维度不低于 7.5。",
                "run_note": "与生产原样控制组、人工最优单策略、人工可取单策略同场复测。",
            },
            {
                "proposal_id": "round2_primary_distillation",
                "title": "最优策略蒸馏版",
                "hypothesis": "保留最优策略的核心动作，删除附带说明，测试是否是硬约束本身有效。",
                "source_blind_labels": [str(primary.get("blind_label") or "")],
                "prompt_rules": [
                    str(primary_strategy.get("instruction") or ""),
                    "只保留 1-2 条必须执行的场景动作，不追加新的抽象方法论名词。",
                ],
                "outline_probe": "如果蒸馏版失效，说明原策略可能依赖更完整的场景素材或模型随机性。",
                "success_metric": "保持最优策略 90% 以上 overall，同时正文更短、更少解释。",
                "run_note": "用于决定能否回灌生产 writer prompt，而不是只保留实验策略。",
            },
        ]
        if weak_dimensions:
            dim = weak_dimensions[0]
            proposals.append(
                {
                    "proposal_id": f"round2_patch_{dim.get('dimension') or 'weak_dim'}",
                    "title": f"弱维度补强：{dim.get('label') or dim.get('dimension')}",
                    "hypothesis": "在人工有效策略上只补最低分维度，验证瓶颈是否来自 prompt 缺口。",
                    "source_blind_labels": source_labels,
                    "prompt_rules": [
                        *selected_rules[:2],
                        str(dim.get("prompt_probe") or ""),
                    ],
                    "outline_probe": str(dim.get("outline_probe") or ""),
                    "success_metric": (
                        f"{dim.get('label') or dim.get('dimension')} 均分提升至少 1 分，"
                        "overall 不下降。"
                    ),
                    "run_note": "如果该维度仍低，优先修大纲/细纲，不继续堆 prompt 规则。",
                }
            )
        return proposals

    if weak_dimensions:
        proposals = []
        for dim in weak_dimensions[:3]:
            dimension = str(dim.get("dimension") or "weak_dim")
            label = str(dim.get("label") or dimension)
            proposals.append(
                {
                    "proposal_id": f"round2_outline_repair_{dimension}",
                    "title": f"无赢家回退：先补{label}",
                    "hypothesis": (
                        "如果所有策略都不好，最低维度更可能是场景合同缺素材，"
                        "而不是 prompt 词不够。"
                    ),
                    "source_blind_labels": [],
                    "prompt_rules": [
                        str(dim.get("prompt_probe") or ""),
                        "正文 prompt 只要求执行这个维度的一条可见动作，避免同时塞入多套方法论。",
                    ],
                    "outline_probe": str(dim.get("outline_probe") or ""),
                    "success_metric": f"{label} 从 gap/no_scores 提升到 watch 或 passing。",
                    "run_note": "先补大纲/细纲字段，再只跑 5 个高信号策略验证素材修复效果。",
                }
            )
        return proposals

    return [
        {
            "proposal_id": "round2_minimal_high_signal_rerun",
            "title": "最小高信号复测",
            "hypothesis": "缺少人工赢家和维度分数时，先减少变量，确认评审链路和素材是否可靠。",
            "source_blind_labels": [],
            "prompt_rules": [
                "只测试开篇钩子、爽点交付、结尾悬念、去 AI 味、生产原样五类策略。",
            ],
            "outline_probe": "补齐第一眼异常、压迫选择反馈、章末新问题后再跑。",
            "success_metric": "每个 draft 都有完整 10 维 judge 分数和人工选择。",
            "run_note": "这是修复实验设计，不是扩大 prompt 数量。",
        }
    ]


def _weak_dimension_rows(manifest: dict[str, object]) -> list[dict[str, object]]:
    rows = _dict_list(manifest.get("dimension_gaps"))
    actionable = [
        row
        for row in rows
        if str(row.get("status") or "") in {"gap", "watch", "no_scores"}
    ]
    return sorted(
        actionable,
        key=lambda row: (
            99.0 if row.get("mean_score") is None else float(row.get("mean_score") or 0.0),
            str(row.get("dimension") or ""),
        ),
    )


def _selected_prompt_rules(rows: Sequence[dict[str, object]]) -> list[str]:
    rules: list[str] = []
    seen: set[str] = set()
    for row in rows:
        strategy = row.get("strategy") if isinstance(row.get("strategy"), dict) else {}
        if not isinstance(strategy, dict):
            continue
        instruction = str(strategy.get("instruction") or "").strip()
        if instruction and instruction not in seen:
            rules.append(instruction)
            seen.add(instruction)
    return rules


def _patch_intent_for_focus(focus: str) -> str:
    return {
        "opening": "强化开篇第一眼钩子",
        "retention": "制造读者问题链和追读欲望",
        "shuangwen": "把爽点从结论改成可见交付过程",
        "suspense": "把悬疑拆成分层揭示",
        "ending": "锁定章末/场景末新悬念",
        "causality": "让目标、阻力、代价、状态变化可见",
        "embodiment": "用身体动作替代作者解释",
        "grounding": "把抽象要求落到具体物料和规则",
        "anti_exposition": "降低说明文比例",
        "anti_ai": "降低 AI 味和空泛总结",
        "outline_probe": "验证素材是否足够支撑正文",
    }.get(focus, "保留人工可感有效的策略约束")


def _render_manual_selection_markdown(payload: dict[str, object]) -> str:
    diagnosis = payload["diagnosis"]
    assert isinstance(diagnosis, dict)
    lines = [
        "# 人工盲选策略反查",
        "",
        f"- case: {payload.get('case_id') or ''}",
        f"- 结论: {diagnosis.get('summary') or ''}",
        f"- 回推: {diagnosis.get('outline_back_projection') or ''}",
        "",
    ]
    patch_candidates = payload.get("production_prompt_patch_candidates")
    if isinstance(patch_candidates, list) and patch_candidates:
        lines.extend(["## 生产 Prompt 回灌候选", ""])
        for item in patch_candidates:
            if not isinstance(item, dict):
                continue
            lines.extend(
                [
                    f"- [{item.get('source_blind_label')}] {item.get('patch_intent')}",
                    f"  - 来源策略: {item.get('source_strategy_id')}",
                    f"  - 候选规则: {item.get('candidate_prompt_rule')}",
                    f"  - 接入注意: {item.get('integration_note')}",
                    "",
                ]
            )
    checklist = payload.get("outline_probe_checklist")
    if isinstance(checklist, list) and checklist:
        lines.extend(["## 大纲/细纲反推检查", ""])
        for item in checklist:
            if not isinstance(item, dict):
                continue
            lines.extend(
                [
                    f"- {item.get('field')}: {item.get('question')}",
                    f"  - 原因: {item.get('why')}",
                    "",
                ]
            )
    next_steps = payload.get("next_experiment")
    if isinstance(next_steps, list) and next_steps:
        lines.extend(["## 下一轮实验", ""])
        for idx, step in enumerate(next_steps, start=1):
            lines.append(f"{idx}. {step}")
        lines.append("")
    proposals = payload.get("next_round_strategy_proposals")
    if isinstance(proposals, list) and proposals:
        lines.extend(["## 二代策略草案", ""])
        for item in proposals:
            if not isinstance(item, dict):
                continue
            lines.extend(
                [
                    f"### {item.get('title') or item.get('proposal_id') or ''}",
                    f"- proposal_id: {item.get('proposal_id') or ''}",
                    f"- hypothesis: {item.get('hypothesis') or ''}",
                    "- source_blind_labels: "
                    + ", ".join(_string_list(item.get("source_blind_labels"))),
                    "- prompt_rules:",
                ]
            )
            for rule in _string_list(item.get("prompt_rules")):
                lines.append(f"  - {rule}")
            lines.extend(
                [
                    f"- outline_probe: {item.get('outline_probe') or ''}",
                    f"- success_metric: {item.get('success_metric') or ''}",
                    f"- run_note: {item.get('run_note') or ''}",
                    "",
                ]
            )
    for title, key in (("最优", "best"), ("可取", "useful"), ("淘汰", "rejected")):
        rows = payload.get(key)
        if not isinstance(rows, list) or not rows:
            continue
        lines.extend([f"## {title}", ""])
        for row in rows:
            if not isinstance(row, dict):
                continue
            strategy = row.get("strategy") if isinstance(row.get("strategy"), dict) else {}
            assert isinstance(strategy, dict)
            lines.extend(
                [
                    f"### 方案 {row.get('blind_label') or ''} - {strategy.get('title') or ''}",
                    f"- strategy_id: {strategy.get('strategy_id') or ''}",
                    f"- writer_model: {row.get('writer_model') or ''}",
                    f"- prompt: {row.get('prompt_path') or ''}",
                    f"- draft: {row.get('draft_path') or ''}",
                    f"- hypothesis: {strategy.get('hypothesis') or ''}",
                    f"- instruction: {strategy.get('instruction') or ''}",
                    f"- design: {row.get('design_summary') or row.get('takeaway') or ''}",
                    f"- notes: {row.get('notes') or ''}",
                    f"- takeaway: {row.get('takeaway') or ''}",
                    "",
                ]
            )
    return "\n".join(lines)


def _selection_items(manual_selection: dict[str, object]) -> dict[str, dict[str, object]]:
    raw = manual_selection.get("selections")
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict[str, object]] = {}
    for label, value in raw.items():
        if isinstance(value, dict) and value.get("choice"):
            normalized[str(label)] = value
    return normalized


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _object_dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _resolve_manifest_path(root: Path, value: object) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    if path.is_absolute() or path.exists():
        return path
    return root / path


def _strategy_takeaway(strategy: dict[str, object]) -> str:
    title = str(strategy.get("title") or strategy.get("strategy_id") or "该策略")
    instruction = str(strategy.get("instruction") or "")
    focus = str(strategy.get("diagnostic_focus") or "")
    return (
        f"{title} 的有效约束应被保留为生产 prompt 的场景级硬要求；"
        f"设计变量={focus}；核心写法={instruction}"
    )


def _import_external_drafts(
    variants: Sequence[PromptVariant],
    drafts_dir: Path,
    *,
    writer_model: str,
    allow_partial: bool = False,
    expected_writer_models: Sequence[str] = (),
) -> list[DraftResult]:
    if not drafts_dir.exists() or not drafts_dir.is_dir():
        raise RuntimeError(f"--import-drafts directory does not exist: {drafts_dir}")
    drafts: list[DraftResult] = []
    missing_strategy_ids: list[str] = []
    for variant in variants:
        paths = _external_draft_paths_for_strategy(
            drafts_dir,
            variant.strategy.strategy_id,
        )
        if not paths:
            missing_strategy_ids.append(variant.strategy.strategy_id)
            continue
        sample_counts_by_writer: dict[str, int] = {}
        for path in paths:
            writer_label, explicit_sample_index = _external_draft_writer_from_path(
                path,
                variant.strategy.strategy_id,
                default_writer_model=writer_model,
            )
            if explicit_sample_index is None:
                sample_counts_by_writer[writer_label] = (
                    sample_counts_by_writer.get(writer_label, 0) + 1
                )
                sample_index = sample_counts_by_writer[writer_label]
            else:
                sample_index = explicit_sample_index
            text = path.read_text(encoding="utf-8").strip()
            if not text:
                continue
            drafts.append(
                DraftResult(
                    draft_id=(
                        f"{variant.strategy.strategy_id}__{_model_slug(writer_label)}"
                        f"__s{sample_index}"
                    ),
                    variant_id=variant.variant_id,
                    writer_model=writer_label,
                    sample_index=sample_index,
                    text=text,
                    provider="external-import",
                    finish_reason="imported",
                    output_path=str(path),
                )
            )
    if missing_strategy_ids and not allow_partial:
        missing = ", ".join(missing_strategy_ids)
        raise RuntimeError(
            "External draft import is incomplete. Missing strategy draft files: "
            f"{missing}. Add the missing <strategy_id>.md files or pass "
            "--allow-partial-import for a deliberate smoke test."
        )
    writer_gaps = _external_draft_writer_gaps(
        variants,
        drafts,
        expected_writer_models=expected_writer_models,
    )
    if writer_gaps and not allow_partial:
        raise RuntimeError(
            "External draft import is missing expected writer coverage: "
            + ", ".join(writer_gaps[:20])
            + ". Add <strategy_id>__<model>.md files for every expected writer "
            "or pass --allow-partial-import for a deliberate smoke test."
        )
    if not drafts:
        raise RuntimeError(f"No importable .md drafts found in {drafts_dir}")
    return drafts


def _external_draft_writer_gaps(
    variants: Sequence[PromptVariant],
    drafts: Sequence[DraftResult],
    *,
    expected_writer_models: Sequence[str],
) -> list[str]:
    expected = [model for model in expected_writer_models if model]
    if not expected:
        return []
    variant_by_id = {variant.variant_id: variant for variant in variants}
    observed_by_strategy: dict[str, set[str]] = {
        variant.strategy.strategy_id: set() for variant in variants
    }
    for draft in drafts:
        variant = variant_by_id.get(draft.variant_id)
        if variant is None:
            continue
        observed_by_strategy.setdefault(variant.strategy.strategy_id, set()).add(
            draft.writer_model
        )
    gaps: list[str] = []
    for variant in variants:
        strategy_id = variant.strategy.strategy_id
        observed = observed_by_strategy.get(strategy_id, set())
        for model in expected:
            if model not in observed:
                gaps.append(f"{strategy_id}/{model}")
    return gaps


def _external_draft_writer_from_path(
    path: Path,
    strategy_id: str,
    *,
    default_writer_model: str,
) -> tuple[str, int | None]:
    exact_stem = strategy_id
    if path.stem == exact_stem:
        return default_writer_model, None
    prefix = f"{strategy_id}__"
    if not path.stem.startswith(prefix):
        return default_writer_model, None
    suffix = path.stem[len(prefix) :].strip()
    if not suffix:
        return default_writer_model, None
    writer_label, separator, sample_suffix = suffix.rpartition("__s")
    if separator and sample_suffix.isdigit() and writer_label:
        return writer_label, int(sample_suffix)
    return suffix, None


def _external_draft_paths_for_strategy(drafts_dir: Path, strategy_id: str) -> list[Path]:
    exact = drafts_dir / f"{strategy_id}.md"
    paths = [exact] if exact.exists() else []
    paths.extend(
        path
        for path in sorted(drafts_dir.glob(f"{strategy_id}__*.md"))
        if path.is_file() and path not in paths
    )
    return paths


def _dry_run_drafts(variants: Sequence[PromptVariant]) -> list[DraftResult]:
    return [
        DraftResult(
            draft_id=f"{variant.strategy.strategy_id}__dry-run__s1",
            variant_id=variant.variant_id,
            writer_model="dry-run-writer",
            sample_index=1,
            text=make_dry_run_draft(variant),
            provider="dry-run",
            finish_reason="dry-run",
        )
        for variant in variants
    ]


async def _run_writers(
    variants: Sequence[PromptVariant],
    *,
    settings: AppSettings,
    writer_models: Sequence[ModelSpec],
    samples_per_strategy: int,
    max_tokens: int,
    output_dir: Path,
    resume: bool,
) -> list[DraftResult]:
    drafts: list[DraftResult] = []
    existing = _load_existing_drafts(output_dir) if resume else {}
    for variant in variants:
        for writer_model in writer_models:
            for sample_index in range(1, samples_per_strategy + 1):
                draft_id = _draft_id_for(variant, writer_model, sample_index)
                if draft_id in existing:
                    drafts.append(existing[draft_id])
                    print(f"resume: skip existing draft {draft_id}")
                    continue
                role_settings = _settings_for_model_spec(settings, "writer", writer_model)
                result = await _complete(
                    role_settings,
                    logical_role="writer",
                    system_prompt=variant.system_prompt,
                    user_prompt=variant.user_prompt,
                    fallback_response="",
                    prompt_template="prose_prompt_strategy_arena.writer",
                    max_tokens=max_tokens,
                    metadata={
                        "variant_id": variant.variant_id,
                        "strategy_id": variant.strategy.strategy_id,
                        "sample_index": sample_index,
                    },
                )
                draft = DraftResult(
                    draft_id=draft_id,
                    variant_id=variant.variant_id,
                    writer_model=writer_model.label,
                    sample_index=sample_index,
                    text=result.content.strip(),
                    provider=result.provider,
                    finish_reason=result.finish_reason,
                )
                draft = _write_incremental_draft(output_dir, draft)
                drafts.append(draft)
    return drafts


async def _run_judges(
    case: PromptTraceCase,
    drafts: Sequence[DraftResult],
    *,
    settings: AppSettings,
    judge_models: Sequence[ModelSpec],
    max_tokens: int,
    output_dir: Path,
    resume: bool,
) -> list[JudgeResult]:
    results: list[JudgeResult] = []
    existing = _load_existing_judgements(output_dir) if resume else {}
    system_prompt = build_judge_system_prompt()
    for draft in drafts:
        for judge_model in judge_models:
            key = _judgement_key(draft.draft_id, judge_model.label)
            if key in existing:
                results.append(existing[key])
                print(f"resume: skip existing judgement {draft.draft_id} / {judge_model.label}")
                continue
            role_settings = _settings_for_model_spec(settings, "critic", judge_model)
            result = await _complete(
                role_settings,
                logical_role="critic",
                system_prompt=system_prompt,
                user_prompt=build_judge_user_prompt(case, draft.text),
                fallback_response='{"scores":{},"winner_reason":"","risk_notes":["fallback"]}',
                prompt_template="prose_prompt_strategy_arena.judge",
                max_tokens=max_tokens,
                metadata={"draft_id": draft.draft_id, "judge_model": judge_model.label},
            )
            judgement = parse_judge_result(draft.draft_id, judge_model.label, result.content)
            _write_incremental_judgement(output_dir, judgement)
            results.append(judgement)
    return results


async def _complete(
    settings: AppSettings,
    *,
    logical_role: LLMRole,
    system_prompt: str,
    user_prompt: str,
    fallback_response: str,
    prompt_template: str,
    max_tokens: int,
    metadata: dict[str, object],
) -> LLMCompletionResult:
    async with session_scope(settings) as session:
        return await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role=logical_role,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response=fallback_response or "（LLM 调用失败，未得到正文。）",
                prompt_template=prompt_template,
                prompt_version="2026-06-25",
                max_tokens_override=max_tokens,
                metadata=metadata,
            ),
        )


def _settings_for_model_spec(settings: AppSettings, role: str, spec: ModelSpec) -> AppSettings:
    if spec.configured_role:
        return settings
    llm = settings.llm
    current = getattr(llm, role)
    assert isinstance(current, LLMRoleSettings)
    updated = current.model_copy(
        update={
            "model": spec.model,
            "model_override": None,
            "api_base": spec.api_base,
            "api_key_env": spec.api_key_env,
            "api_key_header": spec.api_key_header,
        }
    )
    return settings.model_copy(update={"llm": llm.model_copy(update={role: updated})})


def _resolve_model_specs(
    requested: Sequence[str],
    *,
    default_label: str,
    allow_unavailable: bool,
) -> list[ModelSpec]:
    if not requested:
        return [ModelSpec(label=default_label, configured_role=True)]
    catalog = load_model_catalog()
    specs: list[ModelSpec] = []
    for value in requested:
        entry = _catalog_entry_for_arg(value, catalog)
        if entry is not None:
            if not entry.available and not allow_unavailable:
                raise RuntimeError(
                    f"Model catalog entry {entry.id!r} is unavailable; "
                    f"missing env {entry.api_key_env!r}. "
                    "Set the key or pass --allow-unavailable-models for fallback testing."
                )
            specs.append(
                ModelSpec(
                    label=entry.id,
                    model=entry.model,
                    api_base=entry.api_base,
                    api_key_env=entry.api_key_env,
                    api_key_header=entry.api_key_header,
                    available=entry.available,
                )
            )
            continue
        if value.startswith("configured-"):
            specs.append(ModelSpec(label=value, configured_role=True))
            continue
        print(
            f"warning: model {value!r} is not in config/model_catalog.yaml; "
            "using the current role endpoint/key with only model replaced.",
            file=sys.stderr,
        )
        specs.append(ModelSpec(label=value, model=value))
    return specs


def _print_preflight(
    case: PromptTraceCase,
    variants: Sequence[PromptVariant],
    args: argparse.Namespace,
) -> None:
    load_settings()
    writer_specs = _resolve_model_specs(
        args.writer_model,
        default_label="configured-writer",
        allow_unavailable=True,
    )
    judge_specs = _resolve_model_specs(
        args.judge_model,
        default_label="configured-critic",
        allow_unavailable=True,
    )
    samples = max(1, int(args.samples_per_strategy))
    draft_calls = len(variants) * len(writer_specs) * samples
    judge_calls = 0 if args.skip_judging else draft_calls * len(judge_specs)

    print(f"case: {case.case_id}")
    print(f"trace: {case.source_path}")
    print(f"project: {case.project.get('title') or ''}")
    print(f"strategies: {len(variants)}")
    print(f"samples_per_strategy: {samples}")
    print(f"planned_writer_calls: {draft_calls}")
    print(f"planned_judge_calls: {judge_calls}")
    print("writers:")
    for spec in writer_specs:
        print(f"  - {_model_preflight_line(spec)}")
    print("judges:")
    for spec in judge_specs:
        print(f"  - {_model_preflight_line(spec)}")
    print("next_steps:")
    for line in _preflight_next_steps(args, writer_specs, judge_specs):
        print(line)


def _preflight_next_steps(
    args: argparse.Namespace,
    writer_specs: Sequence[ModelSpec],
    judge_specs: Sequence[ModelSpec],
) -> list[str]:
    trace = str(args.trace or "<trace>")
    output_dir = str(args.out or "output/prose-prompt-arena/<run-id>")
    writer_flags = " ".join(f"--writer-model {spec.label}" for spec in writer_specs)
    judge_flags = " ".join(f"--judge-model {spec.label}" for spec in judge_specs)
    unavailable = [
        spec for spec in (*writer_specs, *judge_specs) if spec.available is False
    ]
    lines = [
        "  live_run: .venv/bin/python scripts/prose_prompt_strategy_arena.py "
        f"--trace {trace} --out {output_dir} {writer_flags} {judge_flags}",
        "  external_prompt_package: .venv/bin/python "
        "scripts/prose_prompt_strategy_arena.py "
        f"--trace {trace} --out {output_dir} --prompts-only",
    ]
    if unavailable:
        missing_envs = sorted(
            {spec.api_key_env for spec in unavailable if spec.api_key_env}
        )
        if missing_envs:
            lines.append("  missing_env: " + ", ".join(missing_envs))
        lines.append(
            "  fallback: run external_prompt_package now, then import generated drafts "
            "through the prompt-manifest workflow."
        )
    else:
        lines.append(
            "  ready: required catalog model keys are present; live_run can execute "
            "the full writer/judge experiment."
        )
    return lines


def _model_preflight_line(spec: ModelSpec) -> str:
    if spec.configured_role:
        return f"{spec.label}: configured role model"
    availability = (
        "available"
        if spec.available is True
        else "unavailable"
        if spec.available is False
        else "unknown"
    )
    return (
        f"{spec.label}: {availability} model={spec.model} "
        f"api_base={spec.api_base or ''} api_key_env={spec.api_key_env or ''}"
    )


def _catalog_entry_for_arg(
    value: str,
    catalog: Sequence[ModelCatalogEntry],
) -> ModelCatalogEntry | None:
    direct = get_model_catalog_entry(value)
    if direct is not None:
        return direct
    for entry in catalog:
        if value == entry.model or value == entry.display_name:
            return entry
    return None


def _write_prompt_variants(output_dir: Path, variants: Sequence[PromptVariant]) -> None:
    prompts_dir = output_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    for variant in variants:
        _write_json_file(
            prompts_dir / f"{variant.strategy.strategy_id}.json",
            {
                "variant_id": variant.variant_id,
                "strategy": {
                    "strategy_id": variant.strategy.strategy_id,
                    "title": variant.strategy.title,
                    "hypothesis": variant.strategy.hypothesis,
                    "instruction": variant.strategy.instruction,
                    "diagnostic_focus": variant.strategy.diagnostic_focus,
                },
                "system_prompt": variant.system_prompt,
                "user_prompt": variant.user_prompt,
            },
        )


def _write_external_writer_prompt_files(
    output_dir: Path,
    variants: Sequence[PromptVariant],
    writer_labels: Sequence[str] = DEFAULT_AUDIT_WRITER_MODELS,
) -> list[Path]:
    writer_prompt_dir = output_dir / "writer-prompts"
    writer_prompt_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for variant in variants:
        strategy_id = variant.strategy.strategy_id
        for writer_label in writer_labels:
            draft_filename = f"{strategy_id}__{writer_label}.md"
            path = writer_prompt_dir / f"{strategy_id}__{writer_label}.md"
            lines = [
                "# 外部 Writer Prompt",
                "",
                f"- strategy_id: `{strategy_id}`",
                f"- strategy_title: {variant.strategy.title}",
                f"- writer_model_label: `{writer_label}`",
                f"- save_output_as: `external-drafts/{draft_filename}`",
                f"- prompt_json: `prompts/{strategy_id}.json`",
                "",
                "## 生成要求",
                "",
                "1. 把下方 system prompt 和 user prompt 原样交给 writer 模型。",
                "2. 模型输出只保存正文，不要保存解释、分析、评分或 Markdown 标题。",
                "3. 保存文件名必须与 `save_output_as` 一致，否则导入审计无法归因。",
                "",
                "## System Prompt",
                "",
                "<system_prompt>",
                variant.system_prompt,
                "</system_prompt>",
                "",
                "## User Prompt",
                "",
                "<user_prompt>",
                variant.user_prompt,
                "</user_prompt>",
                "",
            ]
            path.write_text("\n".join(lines), encoding="utf-8")
            paths.append(path)
    return paths


def _write_external_prompt_handoff(
    output_dir: Path,
    variants: Sequence[PromptVariant],
) -> Path:
    path = output_dir / "external-prompt-handoff.md"
    lines = [
        "# 外部正文生成交接清单",
        "",
        "## 使用方式",
        "",
        "1. 优先打开 `writer-prompts/<strategy_id>__<model>.md`，它已经把 system/user "
        "prompt 和保存文件名整理好。",
        "2. 双 writer 横评时，每个策略每个模型各输出一篇正文，保存为 `<strategy_id>__<model>.md`。",
        "3. 推荐模型标签固定用 `minimax-m3` 和 `qwen3.7-plus-coding-plan`，"
        "避免审计时模型名对不上。",
        "4. 默认必须导回每个策略的正文；缺任何策略会失败，避免误把抽样当横评。",
        "5. 单 writer smoke 才使用 `<strategy_id>.md`，"
        "并在导入时额外传 `--import-writer-model <model-label>`。",
        "6. 保存目录可用 `--import-drafts <dir>` 导回横评报告。",
        "7. 外部生成时不要修改 prompt 内容；否则策略归因会失真。",
        "",
        "## 导回命令模板",
        "",
        "```bash",
        ".venv/bin/python scripts/prose_prompt_strategy_arena.py \\",
        "  --prompt-manifest <prompt-package-dir>/prompt-manifest.json \\",
        "  --import-drafts <external-drafts-dir> \\",
        "  --skip-judging \\",
        "  --out <arena-output-dir>",
        "```",
        "",
        "如果使用 `<strategy_id>.md` 这种单模型文件名，才追加：",
        "",
        "```bash",
        "  --import-writer-model <model-label>",
        "```",
        "",
        "## Prompt 清单",
        "",
        "| # | strategy_id | 策略 | writer prompt | prompt JSON | 外部正文文件名 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for idx, variant in enumerate(variants, start=1):
        strategy_id = variant.strategy.strategy_id
        prompt_path = output_dir / "prompts" / f"{strategy_id}.json"
        writer_prompt_paths = [
            output_dir / "writer-prompts" / f"{strategy_id}__{writer_label}.md"
            for writer_label in DEFAULT_AUDIT_WRITER_MODELS
        ]
        lines.append(
            "| "
            f"{idx} | `{strategy_id}` | {variant.strategy.title} | "
            f"{', '.join(f'`{path}`' for path in writer_prompt_paths)} | "
            f"`{prompt_path}` | `{strategy_id}__minimax-m3.md`, "
            f"`{strategy_id}__qwen3.7-plus-coding-plan.md` |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_prompt_only_manifest(
    output_dir: Path,
    case: PromptTraceCase,
    variants: Sequence[PromptVariant],
) -> Path:
    path = output_dir / "prompt-manifest.json"
    _write_json_file(
        path,
        {
            "case": {
                "case_id": case.case_id,
                "source_path": case.source_path,
                "project": case.project,
                "chapter": case.chapter,
                "scene": case.scene,
                "methodology_application_audit": build_methodology_application_audit(
                    case
                ),
            },
            "variant_count": len(variants),
            "draft_import_pattern": "<strategy_id>.md or <strategy_id>__<model>.md",
            "requires_complete_import": True,
            "expected_writer_models": list(DEFAULT_AUDIT_WRITER_MODELS),
            "writer_prompt_dir": str(output_dir / "writer-prompts"),
            "writer_label_rule": (
                "<strategy_id>__<model>.md uses <model> as writer_model; "
                "<strategy_id>.md is only for single-writer smoke imports and uses "
                "--import-writer-model."
            ),
            "import_command_template": (
                ".venv/bin/python scripts/prose_prompt_strategy_arena.py "
                "--prompt-manifest <prompt-package-dir>/prompt-manifest.json "
                "--import-drafts <external-drafts-dir> "
                "--skip-judging --out <arena-output-dir>"
            ),
            "single_writer_import_command_template": (
                ".venv/bin/python scripts/prose_prompt_strategy_arena.py "
                "--prompt-manifest <prompt-package-dir>/prompt-manifest.json "
                "--import-drafts <external-drafts-dir> "
                "--import-writer-model <model-label> --skip-judging "
                "--out <arena-output-dir>"
            ),
            "variants": [
                {
                    "variant_id": variant.variant_id,
                    "strategy_id": variant.strategy.strategy_id,
                    "strategy_title": variant.strategy.title,
                    "hypothesis": variant.strategy.hypothesis,
                    "instruction": variant.strategy.instruction,
                    "diagnostic_focus": variant.strategy.diagnostic_focus,
                    "prompt_path": str(
                        output_dir / "prompts" / f"{variant.strategy.strategy_id}.json"
                    ),
                    "writer_prompt_files": {
                        writer_label: str(
                            output_dir
                            / "writer-prompts"
                            / f"{variant.strategy.strategy_id}__{writer_label}.md"
                        )
                        for writer_label in DEFAULT_AUDIT_WRITER_MODELS
                    },
                    "draft_filenames": {
                        writer_label: (
                            f"{variant.strategy.strategy_id}__{writer_label}.md"
                        )
                        for writer_label in DEFAULT_AUDIT_WRITER_MODELS
                    },
                    "draft_filename": f"{variant.strategy.strategy_id}.md",
                }
                for variant in variants
            ],
        },
    )
    return path


def _write_external_judge_handoff(
    output_dir: Path,
    case: PromptTraceCase,
    drafts: Sequence[DraftResult],
    *,
    judge_labels: Sequence[str],
) -> Path:
    prompts_dir = output_dir / "judge-prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    handoff_path = output_dir / "external-judge-handoff.md"
    blind_labels = _blind_labels_for_drafts(drafts)
    system_prompt = build_judge_system_prompt()
    normalized_judges = [_model_slug(label) for label in judge_labels] or ["external-judge"]
    packets: list[dict[str, str]] = []
    for draft in drafts:
        blind_label = blind_labels[draft.draft_id]
        for judge_label in normalized_judges:
            result_filename = f"{blind_label}__{judge_label}.json"
            prompt_path = prompts_dir / f"{blind_label}__{judge_label}.json"
            _write_json_file(
                prompt_path,
                {
                    "blind_label": blind_label,
                    "judge_label": judge_label,
                    "system_prompt": system_prompt,
                    "user_prompt": _build_external_judge_user_prompt(
                        case,
                        draft.text,
                        blind_label=blind_label,
                        judge_label=judge_label,
                    ),
                    "expected_result_filename": result_filename,
                    "result_schema": build_judge_result_schema(
                        blind_label=blind_label,
                        judge_label=judge_label,
                    ),
                },
            )
            packets.append(
                {
                    "blind_label": blind_label,
                    "judge_label": judge_label,
                    "prompt_path": str(prompt_path),
                    "result_filename": result_filename,
                }
            )

    _write_json_file(
        output_dir / "judge-prompt-manifest.json",
        {
            "expected_judge_models": normalized_judges,
            "draft_count": len(drafts),
            "prompt_count": len(packets),
            "result_files": [
                {
                    "blind_label": packet["blind_label"],
                    "judge_label": packet["judge_label"],
                    "result_filename": packet["result_filename"],
                }
                for packet in packets
            ],
        },
    )
    _write_json_file(
        output_dir / "judge-blind-map.private.json",
        {
            "warning": "Do not send this private map to external judges.",
            "labels": [
                {"blind_label": blind_labels[draft.draft_id], "draft_id": draft.draft_id}
                for draft in drafts
            ],
        },
    )
    lines = [
        "# 外部盲评交接清单",
        "",
        "## 使用方式",
        "",
        "1. 只把 `judge-prompts/*.json` 中的 `system_prompt` 和 `user_prompt` 发给 judge 模型。",
        "2. 不要把 `judge-blind-map.private.json`、正文文件名、策略名或 prompt 策略发给 judge。",
        "3. 要求 judge 只返回 JSON，字段为 `blind_label`、`judge_label`、"
        "`scores`、`winner_reason`、`risk_notes`。",
        "4. 把 judge 原始 JSON 保存到 `external-judgements/`，文件名用下表的结果文件名。",
        "5. 导回时运行 `--import-judgements <external-judgements-dir>`。",
        "",
        "## 导回命令模板",
        "",
        "```bash",
        ".venv/bin/python scripts/prose_prompt_strategy_arena.py \\",
        "  --manifest <arena-output-dir>/manifest.json \\",
        "  --import-judgements <external-judgements-dir> \\",
        "  --out <arena-output-dir>",
        "```",
        "",
        "## Judge Prompt 清单",
        "",
        "| 方案 | Judge | prompt JSON | 结果文件名 |",
        "| --- | --- | --- | --- |",
    ]
    for packet in packets:
        lines.append(
            "| "
            f"{packet['blind_label']} | `{packet['judge_label']}` | "
            f"`{packet['prompt_path']}` | `{packet['result_filename']}` |"
        )
    lines.append("")
    handoff_path.write_text("\n".join(lines), encoding="utf-8")
    return handoff_path


def _build_external_judge_user_prompt(
    case: PromptTraceCase,
    draft_text: str,
    *,
    blind_label: str,
    judge_label: str,
) -> str:
    return (
        f"盲读编号：{blind_label}\n"
        f"Judge标签：{judge_label}\n"
        "请在输出 JSON 顶层原样带回 blind_label 和 judge_label，便于导回系统；"
        "不要输出正文来源、策略猜测或额外解释。\n\n"
        f"{build_judge_user_prompt(case, draft_text)}"
    )


def _import_external_judgements(
    drafts: Sequence[DraftResult],
    judgements_dir: Path,
    *,
    expected_judge_models: Sequence[str] = (),
) -> list[JudgeResult]:
    if not judgements_dir.exists() or not judgements_dir.is_dir():
        raise RuntimeError(f"--import-judgements directory does not exist: {judgements_dir}")
    blind_labels = _blind_labels_for_drafts(drafts)
    draft_by_label = {blind_labels[draft.draft_id]: draft for draft in drafts}
    imported: list[JudgeResult] = []
    for path in sorted(judgements_dir.glob("*.json")):
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            continue
        payload = _try_json(raw)
        label, judge_label = _external_judgement_labels(path, payload)
        draft = draft_by_label.get(label)
        if draft is None:
            continue
        if isinstance(payload, dict) and payload.get("draft_id") and payload.get("judge_model"):
            judgement = judgement_from_dict(payload)
        else:
            judgement = parse_judge_result(draft.draft_id, judge_label, raw)
        imported.append(judgement)
    if not imported:
        raise RuntimeError(f"No importable external judge JSON files found in {judgements_dir}")
    judge_gaps = _external_judgement_gaps(
        drafts,
        imported,
        expected_judge_models=expected_judge_models,
    )
    if judge_gaps:
        raise RuntimeError(
            "External judgement import is missing expected judge coverage: "
            + ", ".join(judge_gaps[:20])
            + ". Add <blind_label>__<judge>.json files for every expected judge."
        )
    return imported


def _expected_judges_from_handoff_manifest(root: Path) -> list[str]:
    path = root / "judge-prompt-manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return _string_list(_object_dict(payload).get("expected_judge_models"))


def _external_judgement_gaps(
    drafts: Sequence[DraftResult],
    judgements: Sequence[JudgeResult],
    *,
    expected_judge_models: Sequence[str],
) -> list[str]:
    expected = [model for model in expected_judge_models if model]
    if not expected:
        return []
    observed_by_draft: dict[str, set[str]] = {draft.draft_id: set() for draft in drafts}
    for judgement in judgements:
        observed_by_draft.setdefault(judgement.draft_id, set()).add(judgement.judge_model)
    gaps: list[str] = []
    for draft in drafts:
        observed = observed_by_draft.get(draft.draft_id, set())
        for model in expected:
            if model not in observed:
                gaps.append(f"{draft.draft_id}/{model}")
    return gaps


def _external_judgement_labels(path: Path, payload: object) -> tuple[str, str]:
    file_label, file_judge_label = _external_judgement_name_parts(path)
    if not isinstance(payload, dict):
        return file_label, file_judge_label
    label = str(payload.get("blind_label") or payload.get("label") or file_label)
    judge_label = str(
        payload.get("judge_label")
        or payload.get("judge_model")
        or payload.get("model")
        or file_judge_label
    )
    return label, judge_label


def _external_judgement_name_parts(path: Path) -> tuple[str, str]:
    stem = path.stem
    if "__" not in stem:
        return stem, "external-judge"
    label, judge_label = stem.split("__", 1)
    return label, judge_label or "external-judge"


def _blind_labels_for_drafts(drafts: Sequence[DraftResult]) -> dict[str, str]:
    return build_blind_label_by_draft_ids(draft.draft_id for draft in drafts)


def _try_json(raw: str) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _score_keys() -> tuple[str, ...]:
    return (
        "opening_hook",
        "golden_three_fit",
        "shuangwen_payoff",
        "suspense_hook",
        "scene_causality",
        "character_embodiment",
        "prose_texture",
        "anti_ai_flavor",
        "ending_hook",
        "overall",
    )


def _write_incremental_draft(output_dir: Path, draft: DraftResult) -> DraftResult:
    drafts_dir = output_dir / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    md_path = drafts_dir / f"{draft.draft_id}.md"
    md_path.write_text(draft.text, encoding="utf-8")
    stored = DraftResult(
        draft_id=draft.draft_id,
        variant_id=draft.variant_id,
        writer_model=draft.writer_model,
        sample_index=draft.sample_index,
        text=draft.text,
        provider=draft.provider,
        finish_reason=draft.finish_reason,
        output_path=str(md_path),
    )
    _write_json_file(drafts_dir / f"{draft.draft_id}.json", draft_to_dict(stored))
    return stored


def _write_incremental_judgement(output_dir: Path, judgement: JudgeResult) -> None:
    judges_dir = output_dir / "judgements"
    judges_dir.mkdir(parents=True, exist_ok=True)
    path = judges_dir / f"{judgement.draft_id}__{_model_slug(judgement.judge_model)}.json"
    _write_json_file(path, judgement_to_dict(judgement))


def _load_existing_drafts(output_dir: Path) -> dict[str, DraftResult]:
    drafts: dict[str, DraftResult] = {}
    for path in sorted((output_dir / "drafts").glob("*.json")):
        try:
            draft = draft_from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if draft.draft_id and draft.text:
            drafts[draft.draft_id] = draft
    return drafts


def _load_existing_judgements(output_dir: Path) -> dict[tuple[str, str], JudgeResult]:
    judgements: dict[tuple[str, str], JudgeResult] = {}
    for path in sorted((output_dir / "judgements").glob("*.json")):
        try:
            judgement = judgement_from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if judgement.draft_id and judgement.judge_model:
            judgements[_judgement_key(judgement.draft_id, judgement.judge_model)] = judgement
    return judgements


def _draft_id_for(
    variant: PromptVariant,
    writer_model: ModelSpec,
    sample_index: int,
) -> str:
    return f"{variant.strategy.strategy_id}__{_model_slug(writer_model.label)}__s{sample_index}"


def _judgement_key(draft_id: str, judge_model: str) -> tuple[str, str]:
    return (draft_id, judge_model)


def _write_json_file(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _variant_by_id(variants: Sequence[PromptVariant]) -> dict[str, PromptVariant]:
    return {variant.variant_id: variant for variant in variants}


def _default_output_dir(case_id: str) -> Path:
    stamp = utc_now_iso().replace(":", "").replace("+", "Z")
    return ROOT / "output" / "prose-prompt-arena" / f"{case_id}-{stamp}"


def _model_slug(model: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in model).strip("-") or "model"


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
