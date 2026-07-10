# ruff: noqa: I001, RUF001

from __future__ import annotations

import json
from pathlib import Path
import importlib.util
import sys
from types import SimpleNamespace
import asyncio

import pytest

from bestseller.services.prose_prompt_experiment import (
    DraftResult,
    ExperimentReport,
    JudgeResult,
    PromptTraceCase,
    PromptVariant,
    aggregate_strategy_rankings,
    build_public_blind_packet,
    build_blind_label_by_draft_ids,
    build_experiment_diagnosis,
    build_default_strategies,
    build_judge_result_schema,
    build_judge_system_prompt,
    build_methodology_application_audit,
    build_prompt_variants,
    build_scene_resource_brief,
    aggregate_dimension_gaps,
    draft_from_dict,
    load_prompt_trace,
    make_dry_run_draft,
    make_dry_run_judgement,
    parse_judge_result,
    render_html_report,
    utc_now_iso,
    write_experiment_package,
)


pytestmark = pytest.mark.unit


def _build_selected_variants(
    case: PromptTraceCase, *strategy_ids: str
) -> list[PromptVariant]:
    strategy_by_id = {
        strategy.strategy_id: strategy for strategy in build_default_strategies()
    }
    return build_prompt_variants(
        case,
        strategies=[strategy_by_id[strategy_id] for strategy_id in strategy_ids],
    )


def _build_control_and_golden_variants(case: PromptTraceCase) -> list[PromptVariant]:
    return _build_selected_variants(
        case, "production_control", "golden_three_opening"
    )


def test_public_blind_packet_excludes_draft_and_strategy_provenance() -> None:
    packet, private_mapping = build_public_blind_packet(
        packet_seed="case-1",
        candidates={
            "production_control__writer-a__s1": "正文甲",
            "new_strategy__writer-b__s1": "正文乙",
        },
    )

    public_text = json.dumps(packet, ensure_ascii=False)
    assert "正文甲" in public_text and "正文乙" in public_text
    assert "production_control" not in public_text
    assert "new_strategy" not in public_text
    assert "writer-a" not in public_text
    assert "writer-b" not in public_text
    assert "draft_id" not in public_text
    assert private_mapping["warning"].startswith("private")


def test_default_strategies_are_twenty_six_unique_methodology_probes() -> None:
    strategies = build_default_strategies()

    assert len(strategies) == 26
    assert len({item.strategy_id for item in strategies}) == 26
    assert [item.strategy_id for item in strategies[:2]] == [
        "production_control",
        "human_process_first",
    ]
    assert {"golden_three_opening", "shuangwen_payoff_first", "ending_hook_lock"} <= {
        item.strategy_id for item in strategies
    }
    golden = next(item for item in strategies if item.strategy_id == "golden_three_opening")
    assert "当前场景" in golden.instruction
    assert "如果当前章节属于前三章" not in golden.instruction


def test_blind_labels_are_stable_and_not_generation_order() -> None:
    labels = build_blind_label_by_draft_ids(
        [
            "production_control__manual__s1",
            "golden_three_opening__manual__s1",
        ]
    )

    assert set(labels.values()) == {"A", "B"}
    assert labels["golden_three_opening__manual__s1"] == "A"
    assert labels["production_control__manual__s1"] == "B"


def test_load_trace_and_build_variants_prepend_strategy_block(tmp_path: Path) -> None:
    trace = _write_trace(tmp_path)
    case = load_prompt_trace(trace)

    variants = build_prompt_variants(case, limit=3)

    assert case.case_id == "demo-book-c1-s2"
    assert variants[0].strategy.strategy_id == "production_control"
    assert variants[0].user_prompt == "原始 user prompt"
    assert "本次正文横评策略" in variants[1].user_prompt
    assert "同一场景资源摘要" in variants[1].user_prompt
    assert "假新郎拜堂成功" in variants[1].user_prompt
    assert "原始 user prompt" in variants[1].user_prompt
    assert variants[1].system_prompt == "原始 system prompt"


def test_methodology_application_audit_separates_mentions_from_hard_constraints() -> None:
    case = PromptTraceCase(
        case_id="case",
        source_path="trace.json",
        system_prompt="正文要体现黄金三章、爽点、结尾钩子，也要去 AI 味。",
        user_prompt=(
            "第一段必须立刻给出危险。核心爽点按压迫、选择、执行、反馈四拍写。"
            "最后 120 字出现新问题压过旧问题。禁止空泛总结，用具体动作承载信息。"
        ),
    )

    audit = build_methodology_application_audit(case)

    findings = {item["dimension"]: item for item in audit["findings"]}
    assert findings["golden_three_opening"]["status"] == "operationalized"
    assert findings["shuangwen_payoff"]["status"] == "operationalized"
    assert findings["ending_hook"]["status"] == "operationalized"
    assert findings["anti_ai_flavor"]["status"] == "operationalized"

    concept_only = PromptTraceCase(
        case_id="case",
        source_path="trace.json",
        system_prompt="正文要有黄金三章意识和爽点。",
        user_prompt="写得吸引人。",
    )
    concept_audit = build_methodology_application_audit(concept_only)
    concept_findings = {item["dimension"]: item for item in concept_audit["findings"]}
    assert concept_findings["golden_three_opening"]["status"] == "mentioned_only"
    assert concept_findings["shuangwen_payoff"]["status"] == "mentioned_only"


def test_write_package_outputs_manifest_html_prompts_and_drafts(tmp_path: Path) -> None:
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts = [
        DraftResult(
            draft_id=f"{variant.strategy.strategy_id}__dry-run__s1",
            variant_id=variant.variant_id,
            writer_model="dry-run-writer",
            sample_index=1,
            text=make_dry_run_draft(variant),
        )
        for variant in variants
    ]
    variant_by_id = {item.variant_id: item for item in variants}
    judgements = [
        make_dry_run_judgement(draft, variant_by_id[draft.variant_id])
        for draft in drafts
    ]
    report = ExperimentReport(
        case=case,
        variants=variants,
        drafts=drafts,
        judgements=judgements,
        created_at=utc_now_iso(),
        dry_run=True,
    )

    paths = write_experiment_package(report, tmp_path / "arena")

    manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
    html = Path(paths["html"]).read_text(encoding="utf-8")
    assert manifest["dry_run"] is True
    assert len(manifest["variants"]) == 2
    assert "resource_brief" in manifest["case"]
    assert "假新郎拜堂成功" in manifest["case"]["resource_brief"]
    assert "methodology_application_audit" in manifest["case"]
    assert manifest["case"]["methodology_application_audit"]["findings"]
    assert len(manifest["drafts"]) == 2
    assert {item["blind_label"] for item in manifest["drafts"]} == {"A", "B"}
    assert manifest["rankings"][0]["mean_overall"] >= 0
    assert manifest["rankings"][0]["blind_label"] in {"A", "B"}
    assert manifest["strategy_rankings"][0]["draft_count"] == 1
    assert manifest["strategy_rankings"][0]["blind_labels"]
    assert manifest["dimension_gaps"][0]["dimension"] in {
        "opening_hook",
        "golden_three_fit",
        "shuangwen_payoff",
        "suspense_hook",
        "scene_causality",
        "character_embodiment",
        "prose_texture",
        "anti_ai_flavor",
        "reader_onboarding",
        "ending_hook",
        "overall",
    }
    assert manifest["dimension_gaps"][0]["prompt_probe"]
    assert manifest["dimension_gaps"][0]["outline_probe"]
    assert manifest["diagnosis"]["status"] in {"outline_or_prompt_gap", "strategy_signal_found"}
    assert "人工横读区" in html
    assert "人工判定" in html
    assert "导出人工判定 JSON" in html
    assert "downloadManualSelection" in html
    assert "manualReveal" in html
    assert "buildManualSummary" in html
    assert "STRATEGY_BY_LABEL" in html
    assert "buildManualRevealText" in html
    assert "本轮暂未选出最优方案" in html
    assert "round2_outline_repair_*" in html
    assert "设计拆解" in html
    assert "设计变量=" in html
    assert "scene-specific hard requirement" in html
    assert "方案 A" in html
    assert 'data-blind-label="A"' in html
    assert "默认只展示盲读编号和正文" in html
    assert "setTextOpen(true)" in html
    assert "showUnselectedOnly" in html
    assert "toggleCompactMode" in html
    assert 'class="prose-block" open' in html
    assert "揭示策略和模型" in html
    assert "揭示盲评排序与策略映射" in html
    assert "实验诊断" in html
    assert "维度缺口矩阵" in html
    assert "大纲/细纲反推" in html
    assert "场景资源摘要" in html
    assert "原始 Prompt 方法论应用诊断" in html
    assert "dry-run 占位稿" in html
    assert (tmp_path / "arena" / "prompts" / "production_control.json").exists()
    assert (tmp_path / "arena" / "drafts" / drafts[0].draft_id).with_suffix(".json").exists()
    stored_draft = draft_from_dict(
        json.loads(
            (tmp_path / "arena" / "drafts" / f"{drafts[0].draft_id}.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assert stored_draft.text == drafts[0].text


def test_parse_judge_result_clamps_scores_and_tolerates_wrapped_json() -> None:
    raw = (
        '说明：{"scores":{"overall":12,"opening_hook":-1},'
        '"winner_reason":"强","risk_notes":["平"]}'
    )

    result = parse_judge_result("draft-1", "judge-x", raw)

    assert result.scores["overall"] == 10.0
    assert result.scores["opening_hook"] == 0.0
    assert result.winner_reason == "强"
    assert result.risk_notes == ["平"]


def test_judge_schema_names_all_dimensions_and_optional_blind_echo() -> None:
    schema = build_judge_result_schema()
    system_prompt = build_judge_system_prompt()

    assert schema["blind_label"].startswith("optional")
    assert schema["judge_label"].startswith("optional")
    assert set(schema["scores"]) == {
        "opening_hook",
        "golden_three_fit",
        "shuangwen_payoff",
        "suspense_hook",
        "scene_causality",
        "character_embodiment",
        "prose_texture",
        "anti_ai_flavor",
        "reader_onboarding",
        "ending_hook",
        "overall",
    }
    assert "blind_label" in system_prompt
    assert "judge_label" in system_prompt
    assert "opening_hook" in system_prompt


def test_scene_resource_brief_extracts_current_scene_contract(tmp_path: Path) -> None:
    case = load_prompt_trace(_write_trace(tmp_path))

    brief = build_scene_resource_brief(case)

    assert "主角欲望：破解配阴婚死局" in brief
    assert "招牌画面：雷霆之气劈碎纸人" in brief
    assert "爽点合同：假新郎拜堂成功" in brief
    assert "物料资源：假新郎纸扎" in brief


def test_render_html_contains_all_drafts(tmp_path: Path) -> None:
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_selected_variants(
        case,
        "production_control",
        "human_process_first",
        "golden_three_opening",
    )
    drafts = [
        DraftResult(
            draft_id=f"draft-{idx}",
            variant_id=variant.variant_id,
            writer_model="dry-run-writer",
            sample_index=1,
            text=f"正文 {idx}",
        )
        for idx, variant in enumerate(variants, start=1)
    ]
    variant_by_id = {item.variant_id: item for item in variants}
    judgements = [
        make_dry_run_judgement(draft, variant_by_id[draft.variant_id])
        for draft in drafts
    ]
    report = ExperimentReport(
        case=case,
        variants=variants,
        drafts=drafts,
        judgements=judgements,
        created_at=utc_now_iso(),
        dry_run=True,
    )

    html = render_html_report(report)

    assert html.count("<article") == 3
    assert "方案 A" in html
    assert "方案 C" in html
    assert "生产原样控制组" in html
    assert "黄金三章开篇钩子" in html


def test_strategy_aggregation_and_no_judgement_diagnosis(tmp_path: Path) -> None:
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts = [
        DraftResult(
            draft_id=f"draft-{idx}",
            variant_id=variant.variant_id,
            writer_model="dry-run-writer",
            sample_index=1,
            text=f"正文 {idx}",
        )
        for idx, variant in enumerate(variants, start=1)
    ]
    report = ExperimentReport(
        case=case,
        variants=variants,
        drafts=drafts,
        judgements=[],
        created_at=utc_now_iso(),
        dry_run=False,
    )

    strategy_rows = aggregate_strategy_rankings(report)
    diagnosis = build_experiment_diagnosis(report)

    assert len(strategy_rows) == 2
    assert strategy_rows[0]["mean_overall"] is None
    assert diagnosis["status"] == "no_judgements"


def test_dimension_gap_matrix_surfaces_outline_back_projection(tmp_path: Path) -> None:
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = build_prompt_variants(case, limit=1)
    draft = DraftResult(
        draft_id="draft-1",
        variant_id=variants[0].variant_id,
        writer_model="writer",
        sample_index=1,
        text="正文",
    )
    report = ExperimentReport(
        case=case,
        variants=variants,
        drafts=[draft],
        judgements=[
            JudgeResult(
                draft_id=draft.draft_id,
                judge_model="judge",
                scores={
                    "opening_hook": 3,
                    "ending_hook": 4,
                    "overall": 5,
                    "shuangwen_payoff": 8,
                },
                winner_reason="开篇弱，结尾平",
                risk_notes=[],
            )
        ],
        created_at=utc_now_iso(),
    )

    gaps = aggregate_dimension_gaps(report)
    diagnosis = build_experiment_diagnosis(report)

    assert gaps[0]["dimension"] == "opening_hook"
    assert gaps[0]["status"] == "gap"
    assert gaps[0]["lowest_blind_labels"] == ["A"]
    assert "细纲" in gaps[0]["outline_probe"]
    missing_dimension = next(item for item in gaps if item["dimension"] == "golden_three_fit")
    assert missing_dimension["mean_score"] is None
    assert missing_dimension["status"] == "no_scores"
    assert diagnosis["weakest_dimensions"][0]["dimension"] == "opening_hook"


def test_script_model_specs_resolve_catalog_entries() -> None:
    script_path = Path(__file__).parents[2] / "scripts" / "prose_prompt_strategy_arena.py"
    spec = importlib.util.spec_from_file_location("prose_prompt_strategy_arena", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["prose_prompt_strategy_arena"] = module
    spec.loader.exec_module(module)

    specs = module._resolve_model_specs(
        ["minimax-m3", "openai/qwen3.7-plus"],
        default_label="configured-writer",
        allow_unavailable=True,
    )

    assert [item.label for item in specs] == ["minimax-m3", "qwen3.7-plus-coding-plan"]
    assert specs[0].api_base == "https://api.minimaxi.com/v1"
    assert specs[1].api_key_env == "QWEN_CODING_PLAN_API_KEY"
    assert specs[0].available in {True, False}


def test_script_preflight_reports_planned_calls(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_path = Path(__file__).parents[2] / "scripts" / "prose_prompt_strategy_arena.py"
    spec = importlib.util.spec_from_file_location(
        "prose_prompt_strategy_arena_preflight",
        script_path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["prose_prompt_strategy_arena_preflight"] = module
    spec.loader.exec_module(module)
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    args = module._parse_args(
        [
            "--trace",
            str(_write_trace(tmp_path)),
            "--preflight",
            "--writer-model",
            "minimax-m3",
            "--judge-model",
            "deepseek-v4-flash",
            "--samples-per-strategy",
            "2",
        ]
    )

    module._print_preflight(case, variants, args)

    out = capsys.readouterr().out
    assert "planned_writer_calls: 4" in out
    assert "planned_judge_calls: 4" in out
    assert "minimax-m3" in out
    assert "next_steps:" in out
    assert "--prompts-only" in out


def test_script_audit_defaults_to_target_live_models() -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_audit_defaults")

    default_args = module._parse_args(["--manifest", "manifest.json", "--audit"])
    assert module._audit_expected_writer_models(default_args) == (
        "minimax-m3",
        "qwen3.7-plus-coding-plan",
    )
    assert module._audit_expected_judge_models(default_args) == (
        "minimax-m3",
        "deepseek-v4-flash",
    )

    override_args = module._parse_args(
        [
            "--manifest",
            "manifest.json",
            "--audit",
            "--expected-writer-model",
            "writer-x",
            "--expected-judge-model",
            "judge-x",
        ]
    )
    assert module._audit_expected_writer_models(override_args) == ("writer-x",)
    assert module._audit_expected_judge_models(override_args) == ("judge-x",)


def test_script_incremental_outputs_can_be_loaded_for_resume(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_resume")
    draft = DraftResult(
        draft_id="strategy__model__s1",
        variant_id="case__strategy",
        writer_model="model",
        sample_index=1,
        text="正文",
        provider="fake",
        finish_reason="stop",
    )
    judgement = make_dry_run_judgement(
        draft,
        build_prompt_variants(load_prompt_trace(_write_trace(tmp_path)), limit=1)[0],
    )

    stored = module._write_incremental_draft(tmp_path, draft)
    module._write_incremental_judgement(tmp_path, judgement)

    drafts = module._load_existing_drafts(tmp_path)
    judgements = module._load_existing_judgements(tmp_path)
    assert stored.output_path
    assert drafts[draft.draft_id].text == "正文"
    assert (draft.draft_id, judgement.judge_model) in judgements


def test_script_imports_external_drafts_by_strategy_id(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_import")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts_dir = tmp_path / "external-drafts"
    drafts_dir.mkdir()
    (drafts_dir / "production_control.md").write_text("控制组正文", encoding="utf-8")
    (drafts_dir / "production_control__minimax-m3.md").write_text(
        "控制组正文 MiniMax",
        encoding="utf-8",
    )
    (drafts_dir / "golden_three_opening__qwen.md").write_text(
        "黄金三章正文",
        encoding="utf-8",
    )

    drafts = module._import_external_drafts(
        variants,
        drafts_dir,
        writer_model="manual-qwen",
    )

    assert [item.text for item in drafts] == [
        "控制组正文",
        "控制组正文 MiniMax",
        "黄金三章正文",
    ]
    assert {item.provider for item in drafts} == {"external-import"}
    assert drafts[0].draft_id == "production_control__manual-qwen__s1"
    assert drafts[1].draft_id == "production_control__minimax-m3__s1"
    assert drafts[1].writer_model == "minimax-m3"
    assert drafts[2].variant_id.endswith("golden_three_opening")
    assert drafts[2].writer_model == "qwen"


def test_script_import_requires_all_strategy_drafts_by_default(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_import_complete")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts_dir = tmp_path / "external-drafts"
    drafts_dir.mkdir()
    (drafts_dir / "production_control.md").write_text("控制组正文", encoding="utf-8")

    with pytest.raises(RuntimeError, match="golden_three_opening"):
        module._import_external_drafts(
            variants,
            drafts_dir,
            writer_model="manual-qwen",
        )

    partial = module._import_external_drafts(
        variants,
        drafts_dir,
        writer_model="manual-qwen",
        allow_partial=True,
    )
    assert [item.variant_id for item in partial] == [variants[0].variant_id]


def test_script_import_requires_expected_writer_coverage_by_default(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_import_writer_coverage")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts_dir = tmp_path / "external-drafts"
    drafts_dir.mkdir()
    for variant in variants:
        (drafts_dir / f"{variant.strategy.strategy_id}__minimax-m3.md").write_text(
            f"{variant.strategy.strategy_id} MiniMax 正文",
            encoding="utf-8",
        )

    with pytest.raises(RuntimeError, match=r"qwen3\.7-plus-coding-plan"):
        module._import_external_drafts(
            variants,
            drafts_dir,
            writer_model="unused-default",
            expected_writer_models=["minimax-m3", "qwen3.7-plus-coding-plan"],
        )

    partial = module._import_external_drafts(
        variants,
        drafts_dir,
        writer_model="unused-default",
        expected_writer_models=["minimax-m3", "qwen3.7-plus-coding-plan"],
        allow_partial=True,
    )
    assert {item.writer_model for item in partial} == {"minimax-m3"}


def test_script_audit_accepts_multi_writer_external_import(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_multi_writer_audit")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts_dir = tmp_path / "external-drafts"
    drafts_dir.mkdir()
    for variant in variants:
        strategy_id = variant.strategy.strategy_id
        (drafts_dir / f"{strategy_id}__minimax-m3.md").write_text(
            f"{strategy_id} MiniMax 正文",
            encoding="utf-8",
        )
        (drafts_dir / f"{strategy_id}__qwen3.7-plus.md").write_text(
            f"{strategy_id} Qwen 正文",
            encoding="utf-8",
        )
    drafts = module._import_external_drafts(
        variants,
        drafts_dir,
        writer_model="unused-default",
    )
    report = ExperimentReport(
        case=case,
        variants=variants,
        drafts=drafts,
        judgements=[],
        created_at=utc_now_iso(),
        dry_run=False,
    )
    paths = write_experiment_package(report, tmp_path / "arena")

    audit_paths = module._audit_experiment_manifest(
        Path(paths["manifest"]),
        expected_strategy_count=2,
        expected_writer_models=["minimax-m3", "qwen3.7-plus"],
    )

    payload = json.loads(Path(audit_paths["json"]).read_text(encoding="utf-8"))
    writer_check = next(
        item for item in payload["evidence"] if item["check"] == "expected_writer_models"
    )
    assert len(drafts) == 4
    assert {item.writer_model for item in drafts} == {"minimax-m3", "qwen3.7-plus"}
    assert writer_check["passed"] is True
    assert "expected_writer_models" not in {item["check"] for item in payload["pending"]}


def test_script_writes_external_prompt_handoff(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_handoff")
    variants = _build_control_and_golden_variants(
        load_prompt_trace(_write_trace(tmp_path))
    )

    module._write_prompt_variants(tmp_path, variants)
    writer_prompt_paths = module._write_external_writer_prompt_files(tmp_path, variants)
    handoff_path = module._write_external_prompt_handoff(tmp_path, variants)

    handoff = handoff_path.read_text(encoding="utf-8")
    writer_prompt = (
        tmp_path / "writer-prompts" / "production_control__minimax-m3.md"
    ).read_text(encoding="utf-8")
    assert len(writer_prompt_paths) == 4
    assert "外部正文生成交接清单" in handoff
    assert "--import-drafts" in handoff
    assert "--prompt-manifest" in handoff
    assert "writer-prompts/production_control__minimax-m3.md" in handoff
    assert "--trace" not in handoff
    assert "production_control__minimax-m3.md" in handoff
    assert "production_control__qwen3.7-plus-coding-plan.md" in handoff
    assert "单 writer smoke" in handoff
    assert "<strategy_id>__<model>.md" in handoff
    assert "golden_three_opening__minimax-m3.md" in handoff
    assert "prompts/production_control.json" in handoff
    assert "save_output_as: `external-drafts/production_control__minimax-m3.md`" in (
        writer_prompt
    )
    assert "<system_prompt>" in writer_prompt
    assert "<user_prompt>" in writer_prompt


def test_script_writes_prompt_only_manifest(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_prompt_manifest")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    module._write_prompt_variants(tmp_path, variants)

    manifest_path = module._write_prompt_only_manifest(tmp_path, case, variants)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["case"]["case_id"] == "demo-book-c1-s2"
    assert manifest["case"]["methodology_application_audit"]["findings"]
    assert manifest["variant_count"] == 2
    assert manifest["draft_import_pattern"] == "<strategy_id>.md or <strategy_id>__<model>.md"
    assert manifest["requires_complete_import"] is True
    assert manifest["expected_writer_models"] == [
        "minimax-m3",
        "qwen3.7-plus-coding-plan",
    ]
    assert manifest["writer_prompt_dir"].endswith("writer-prompts")
    assert "<strategy_id>__<model>.md uses <model>" in manifest["writer_label_rule"]
    assert "--import-drafts" in manifest["import_command_template"]
    assert "--prompt-manifest" in manifest["import_command_template"]
    assert "--import-writer-model" not in manifest["import_command_template"]
    assert "--import-writer-model" in manifest["single_writer_import_command_template"]
    assert "--trace" not in manifest["import_command_template"]
    assert manifest["variants"][0]["strategy_id"] == "production_control"
    assert manifest["variants"][0]["draft_filename"] == "production_control.md"
    assert manifest["variants"][0]["prompt_path"].endswith("prompts/production_control.json")
    assert manifest["variants"][0]["writer_prompt_files"]["minimax-m3"].endswith(
        "writer-prompts/production_control__minimax-m3.md"
    )
    assert manifest["variants"][0]["draft_filenames"]["qwen3.7-plus-coding-plan"] == (
        "production_control__qwen3.7-plus-coding-plan.md"
    )


def test_script_writes_blind_external_judge_handoff(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_judge_handoff")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts = [
        DraftResult(
            draft_id=f"{variant.strategy.strategy_id}__manual__s1",
            variant_id=variant.variant_id,
            writer_model="manual",
            sample_index=1,
            text=f"正文 {idx}",
        )
        for idx, variant in enumerate(variants, start=1)
    ]

    handoff_path = module._write_external_judge_handoff(
        tmp_path,
        case,
        drafts,
        judge_labels=["deepseek-v4-flash"],
    )

    handoff = handoff_path.read_text(encoding="utf-8")
    private_map = json.loads((tmp_path / "judge-blind-map.private.json").read_text())
    judge_manifest = json.loads((tmp_path / "judge-prompt-manifest.json").read_text())
    public_packet = json.loads((tmp_path / "public-review-packet.json").read_text())
    public_packet_text = json.dumps(public_packet, ensure_ascii=False)
    production_label = next(
        item["blind_label"]
        for item in private_map["labels"]
        if item["draft_id"] == "production_control__manual__s1"
    )
    prompt_payload = json.loads(
        (
            tmp_path / "judge-prompts" / f"{production_label}__deepseek-v4-flash.json"
        ).read_text(encoding="utf-8")
    )
    prompt_text = json.dumps(prompt_payload, ensure_ascii=False)
    assert "外部盲评交接清单" in handoff
    assert "--import-judgements" in handoff
    assert "--manifest <arena-output-dir>/manifest.json" in handoff
    assert "--trace" not in handoff
    assert "--import-drafts" not in handoff
    assert "blind_label" in handoff
    assert "production_control" not in public_packet_text
    assert "manual" not in public_packet_text
    assert "draft_id" not in public_packet_text
    assert {item["text"] for item in public_packet["candidates"]} == {"正文 1", "正文 2"}
    assert prompt_payload["blind_label"] == production_label
    assert prompt_payload["result_schema"]["blind_label"] == production_label
    assert prompt_payload["result_schema"]["judge_label"] == "deepseek-v4-flash"
    assert set(prompt_payload["result_schema"]["scores"]) == set(
        build_judge_result_schema()["scores"]
    )
    assert (
        prompt_payload["expected_result_filename"]
        == f"{production_label}__deepseek-v4-flash.json"
    )
    assert "正文 1" in prompt_payload["user_prompt"]
    assert f"盲读编号：{production_label}" in prompt_payload["user_prompt"]
    assert "Judge标签：deepseek-v4-flash" in prompt_payload["user_prompt"]
    assert "scores" in prompt_payload["result_schema"]
    assert "strategy_id" not in prompt_text
    assert "production_control" not in prompt_text
    assert "draft_id" not in prompt_text
    assert judge_manifest["expected_judge_models"] == ["deepseek-v4-flash"]
    assert judge_manifest["draft_count"] == 2
    assert judge_manifest["prompt_count"] == 2
    assert private_map["labels"][0]["draft_id"] == "production_control__manual__s1"


def test_script_exports_judge_prompts_from_existing_manifest(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_judge_from_manifest")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts = [
        DraftResult(
            draft_id=f"{variant.strategy.strategy_id}__writer-x__s1",
            variant_id=variant.variant_id,
            writer_model="writer-x",
            sample_index=1,
            text=f"正文 {idx}",
        )
        for idx, variant in enumerate(variants, start=1)
    ]
    report = ExperimentReport(
        case=case,
        variants=variants,
        drafts=drafts,
        judgements=[],
        created_at=utc_now_iso(),
        dry_run=False,
    )
    paths = write_experiment_package(report, tmp_path / "arena")

    handoff_path = module._export_judge_prompts_from_manifest(
        Path(paths["manifest"]),
        output_dir=tmp_path / "judge-export",
        judge_labels=["judge-x"],
    )

    private_map = json.loads(
        (tmp_path / "judge-export" / "judge-blind-map.private.json").read_text()
    )
    judge_manifest = json.loads(
        (tmp_path / "judge-export" / "judge-prompt-manifest.json").read_text()
    )
    production_label = next(
        item["blind_label"]
        for item in private_map["labels"]
        if item["draft_id"] == "production_control__writer-x__s1"
    )
    payload = json.loads(
        (
            tmp_path / "judge-export" / "judge-prompts" / f"{production_label}__judge-x.json"
        ).read_text(encoding="utf-8")
    )
    handoff = handoff_path.read_text(encoding="utf-8")
    assert "外部盲评交接清单" in handoff
    assert payload["blind_label"] == production_label
    assert "正文 1" in payload["user_prompt"]
    assert "示例书" in payload["user_prompt"]
    assert "production_control" not in json.dumps(payload, ensure_ascii=False)
    assert judge_manifest["expected_judge_models"] == ["judge-x"]
    assert judge_manifest["prompt_count"] == 2


def test_script_imports_external_judgements_by_blind_label(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_import_judgements")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts = [
        DraftResult(
            draft_id=f"{variant.strategy.strategy_id}__manual__s1",
            variant_id=variant.variant_id,
            writer_model="manual",
            sample_index=1,
            text=f"正文 {idx}",
        )
        for idx, variant in enumerate(variants, start=1)
    ]
    labels = module._blind_labels_for_drafts(drafts)
    golden_label = labels["golden_three_opening__manual__s1"]
    judgements_dir = tmp_path / "external-judgements"
    judgements_dir.mkdir()
    (judgements_dir / f"{golden_label}__judge-x.json").write_text(
        json.dumps(
            {
                "scores": {"overall": 8, "opening_hook": 7},
                "winner_reason": "结尾更有悬念",
                "risk_notes": ["说明略多"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    imported = module._import_external_judgements(drafts, judgements_dir)

    assert len(imported) == 1
    assert imported[0].draft_id == "golden_three_opening__manual__s1"
    assert imported[0].judge_model == "judge-x"
    assert imported[0].scores["overall"] == 8
    assert imported[0].winner_reason == "结尾更有悬念"


def test_script_imports_external_judgements_by_payload_blind_label(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_import_judgements_payload")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts = [
        DraftResult(
            draft_id=f"{variant.strategy.strategy_id}__manual__s1",
            variant_id=variant.variant_id,
            writer_model="manual",
            sample_index=1,
            text=f"正文 {idx}",
        )
        for idx, variant in enumerate(variants, start=1)
    ]
    labels = module._blind_labels_for_drafts(drafts)
    golden_label = labels["golden_three_opening__manual__s1"]
    judgements_dir = tmp_path / "external-judgements"
    judgements_dir.mkdir()
    (judgements_dir / "renamed-download.json").write_text(
        json.dumps(
            {
                "blind_label": golden_label,
                "judge_label": "judge-y",
                "scores": {"overall": 9, "ending_hook": 8},
                "winner_reason": "正文更抓人",
                "risk_notes": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    imported = module._import_external_judgements(drafts, judgements_dir)

    assert len(imported) == 1
    assert imported[0].draft_id == "golden_three_opening__manual__s1"
    assert imported[0].judge_model == "judge-y"
    assert imported[0].scores["overall"] == 9


def test_script_import_requires_expected_judge_coverage_by_default(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_import_judge_coverage")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts = [
        DraftResult(
            draft_id=f"{variant.strategy.strategy_id}__writer-x__s1",
            variant_id=variant.variant_id,
            writer_model="writer-x",
            sample_index=1,
            text=f"正文 {idx}",
        )
        for idx, variant in enumerate(variants, start=1)
    ]
    labels = module._blind_labels_for_drafts(drafts)
    judgements_dir = tmp_path / "external-judgements"
    judgements_dir.mkdir()
    for draft in drafts:
        label = labels[draft.draft_id]
        (judgements_dir / f"{label}__minimax-m3.json").write_text(
            json.dumps(
                {
                    "blind_label": label,
                    "judge_label": "minimax-m3",
                    "scores": {"overall": 8},
                    "winner_reason": "已读",
                    "risk_notes": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    with pytest.raises(RuntimeError, match=r"deepseek-v4-flash"):
        module._import_external_judgements(
            drafts,
            judgements_dir,
            expected_judge_models=["minimax-m3", "deepseek-v4-flash"],
        )


def test_script_merges_external_judgements_into_existing_manifest(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_merge_judgements")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts = [
        DraftResult(
            draft_id=f"{variant.strategy.strategy_id}__writer-x__s1",
            variant_id=variant.variant_id,
            writer_model="writer-x",
            sample_index=1,
            text=f"正文 {idx}",
        )
        for idx, variant in enumerate(variants, start=1)
    ]
    report = ExperimentReport(
        case=case,
        variants=variants,
        drafts=drafts,
        judgements=[],
        created_at=utc_now_iso(),
        dry_run=False,
    )
    paths = write_experiment_package(report, tmp_path / "arena")
    labels = module._blind_labels_for_drafts(drafts)
    golden_label = labels["golden_three_opening__writer-x__s1"]
    judgements_dir = tmp_path / "external-judgements"
    judgements_dir.mkdir()
    (judgements_dir / f"{golden_label}__judge-x.json").write_text(
        json.dumps(
            {
                "scores": {"overall": 8, "ending_hook": 9},
                "winner_reason": "结尾更有悬念",
                "risk_notes": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    merged_paths = module._merge_judgements_into_manifest(
        Path(paths["manifest"]),
        judgements_dir,
        output_dir=tmp_path / "merged",
    )

    manifest = json.loads(Path(merged_paths["manifest"]).read_text(encoding="utf-8"))
    html = Path(merged_paths["html"]).read_text(encoding="utf-8")
    assert merged_paths["imported_judgements"] == "1"
    assert merged_paths["total_judgements"] == "1"
    assert manifest["judgements"][0]["draft_id"] == "golden_three_opening__writer-x__s1"
    assert manifest["judgements"][0]["judge_model"] == "judge-x"
    assert manifest["rankings"][0]["blind_label"] == golden_label
    assert "结尾更有悬念" in html


def test_script_audits_complete_experiment_manifest(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_audit_complete")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts = [
        DraftResult(
            draft_id=f"{variant.strategy.strategy_id}__writer-x__s1",
            variant_id=variant.variant_id,
            writer_model="writer-x",
            sample_index=1,
            text=f"正文 {idx}",
        )
        for idx, variant in enumerate(variants, start=1)
    ]
    variant_by_id = {item.variant_id: item for item in variants}
    report = ExperimentReport(
        case=case,
        variants=variants,
        drafts=drafts,
        judgements=[
            make_dry_run_judgement(draft, variant_by_id[draft.variant_id])
            for draft in drafts
        ],
        created_at=utc_now_iso(),
        dry_run=False,
    )
    paths = write_experiment_package(report, tmp_path / "arena")
    manual_selection = tmp_path / "manual-selection.json"
    manual_selection.write_text(
        json.dumps(
            {
                "case_id": case.case_id,
                "selections": {"A": {"choice": "best", "notes": "最佳"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    module._analyze_manual_selection(Path(paths["manifest"]), manual_selection)

    audit_paths = module._audit_experiment_manifest(
        Path(paths["manifest"]),
        manual_selection_path=manual_selection,
        expected_strategy_count=2,
        expected_writer_models=["writer-x"],
        expected_judge_models=["dry-run-judge"],
    )

    payload = json.loads(Path(audit_paths["json"]).read_text(encoding="utf-8"))
    assert payload["status"] == "complete"
    assert payload["counts"]["strategies"] == 2
    assert payload["manual_selection"]["best_labels"] == ["A"]
    assert payload["manual_selection_analysis"]["complete"] is True
    assert not payload["failures"]
    assert not payload["pending"]
    md = Path(audit_paths["md"]).read_text(encoding="utf-8")
    assert "正文提示词横评完成度审计" in md
    assert "PASS strategy_count" in md


def test_script_audit_rejects_stale_manual_analysis(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_audit_stale_manual")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts = [
        DraftResult(
            draft_id=f"{variant.strategy.strategy_id}__writer-x__s1",
            variant_id=variant.variant_id,
            writer_model="writer-x",
            sample_index=1,
            text=f"正文 {idx}",
        )
        for idx, variant in enumerate(variants, start=1)
    ]
    variant_by_id = {item.variant_id: item for item in variants}
    report = ExperimentReport(
        case=case,
        variants=variants,
        drafts=drafts,
        judgements=[
            make_dry_run_judgement(draft, variant_by_id[draft.variant_id])
            for draft in drafts
        ],
        created_at=utc_now_iso(),
        dry_run=False,
    )
    paths = write_experiment_package(report, tmp_path / "arena")
    manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
    labels_by_draft = {
        item["draft_id"]: item["blind_label"]
        for item in manifest["drafts"]
    }
    old_manual_selection = tmp_path / "manual-selection-old.json"
    old_manual_selection.write_text(
        json.dumps(
            {
                "case_id": case.case_id,
                "selections": {
                    labels_by_draft["production_control__writer-x__s1"]: {
                        "choice": "best",
                        "notes": "旧选择",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    module._analyze_manual_selection(Path(paths["manifest"]), old_manual_selection)

    current_manual_selection = tmp_path / "manual-selection-current.json"
    current_manual_selection.write_text(
        json.dumps(
            {
                "case_id": case.case_id,
                "selections": {
                    labels_by_draft["golden_three_opening__writer-x__s1"]: {
                        "choice": "best",
                        "notes": "当前选择",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    audit_paths = module._audit_experiment_manifest(
        Path(paths["manifest"]),
        manual_selection_path=current_manual_selection,
        expected_strategy_count=2,
        expected_writer_models=["writer-x"],
        expected_judge_models=["dry-run-judge"],
    )

    payload = json.loads(Path(audit_paths["json"]).read_text(encoding="utf-8"))
    assert payload["status"] == "pending_human_or_external"
    assert payload["manual_selection_analysis"]["complete"] is False
    assert {item["check"] for item in payload["pending"]} == {
        "manual_selection_analysis"
    }
    detail = payload["manual_selection_analysis"]["detail"]
    assert "manual_selection_path" in detail
    assert "best_labels" in detail


def test_script_audit_marks_external_or_human_gaps_pending(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_audit_pending")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts = [
        DraftResult(
            draft_id=f"{variant.strategy.strategy_id}__writer-x__s1",
            variant_id=variant.variant_id,
            writer_model="writer-x",
            sample_index=1,
            text=f"正文 {idx}",
        )
        for idx, variant in enumerate(variants, start=1)
    ]
    report = ExperimentReport(
        case=case,
        variants=variants,
        drafts=drafts,
        judgements=[],
        created_at=utc_now_iso(),
        dry_run=False,
    )
    paths = write_experiment_package(report, tmp_path / "arena")

    audit_paths = module._audit_experiment_manifest(
        Path(paths["manifest"]),
        expected_strategy_count=2,
        expected_writer_models=["writer-x"],
        expected_judge_models=["judge-x"],
    )

    payload = json.loads(Path(audit_paths["json"]).read_text(encoding="utf-8"))
    assert payload["status"] == "pending_human_or_external"
    assert not payload["failures"]
    assert {item["check"] for item in payload["pending"]} == {
        "judge_coverage",
        "expected_judge_models",
        "manual_final_selection",
    }


def test_script_audit_accepts_all_rejected_no_winner_analysis(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_audit_no_winner")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts = [
        DraftResult(
            draft_id=f"{variant.strategy.strategy_id}__writer-x__s1",
            variant_id=variant.variant_id,
            writer_model="writer-x",
            sample_index=1,
            text=f"正文 {idx}",
        )
        for idx, variant in enumerate(variants, start=1)
    ]
    variant_by_id = {item.variant_id: item for item in variants}
    report = ExperimentReport(
        case=case,
        variants=variants,
        drafts=drafts,
        judgements=[
            make_dry_run_judgement(draft, variant_by_id[draft.variant_id])
            for draft in drafts
        ],
        created_at=utc_now_iso(),
        dry_run=False,
    )
    paths = write_experiment_package(report, tmp_path / "arena")
    manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
    manual_selection = tmp_path / "manual-selection.json"
    manual_selection.write_text(
        json.dumps(
            {
                "case_id": case.case_id,
                "selections": {
                    item["blind_label"]: {"choice": "reject", "notes": "都不够好"}
                    for item in manifest["drafts"]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    module._analyze_manual_selection(Path(paths["manifest"]), manual_selection)

    audit_paths = module._audit_experiment_manifest(
        Path(paths["manifest"]),
        manual_selection_path=manual_selection,
        expected_strategy_count=2,
        expected_writer_models=["writer-x"],
        expected_judge_models=["dry-run-judge"],
    )

    payload = json.loads(Path(audit_paths["json"]).read_text(encoding="utf-8"))
    assert payload["status"] == "complete"
    assert payload["manual_selection"]["best_labels"] == []
    assert payload["manual_selection"]["final_decision"] is True
    assert payload["manual_selection_analysis"]["complete"] is True
    assert "no-winner" in payload["manual_selection"]["detail"]


def test_script_audit_keeps_partial_no_best_selection_pending(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_audit_partial_no_best")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts = [
        DraftResult(
            draft_id=f"{variant.strategy.strategy_id}__writer-x__s1",
            variant_id=variant.variant_id,
            writer_model="writer-x",
            sample_index=1,
            text=f"正文 {idx}",
        )
        for idx, variant in enumerate(variants, start=1)
    ]
    variant_by_id = {item.variant_id: item for item in variants}
    report = ExperimentReport(
        case=case,
        variants=variants,
        drafts=drafts,
        judgements=[
            make_dry_run_judgement(draft, variant_by_id[draft.variant_id])
            for draft in drafts
        ],
        created_at=utc_now_iso(),
        dry_run=False,
    )
    paths = write_experiment_package(report, tmp_path / "arena")
    manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
    manual_selection = tmp_path / "manual-selection.json"
    manual_selection.write_text(
        json.dumps(
            {
                "case_id": case.case_id,
                "selections": {
                    manifest["drafts"][0]["blind_label"]: {
                        "choice": "reject",
                        "notes": "这一篇不行",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    audit_paths = module._audit_experiment_manifest(
        Path(paths["manifest"]),
        manual_selection_path=manual_selection,
        expected_strategy_count=2,
        expected_writer_models=["writer-x"],
        expected_judge_models=["dry-run-judge"],
    )

    payload = json.loads(Path(audit_paths["json"]).read_text(encoding="utf-8"))
    assert payload["status"] == "pending_human_or_external"
    assert payload["manual_selection"]["final_decision"] is False
    assert {item["check"] for item in payload["pending"]} == {
        "manual_final_selection"
    }


def test_script_audit_marks_incomplete_judge_dimensions_pending(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_audit_score_dimensions")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = build_prompt_variants(case, limit=1)
    draft = DraftResult(
        draft_id="production_control__writer-x__s1",
        variant_id=variants[0].variant_id,
        writer_model="writer-x",
        sample_index=1,
        text="正文 1",
    )
    report = ExperimentReport(
        case=case,
        variants=variants,
        drafts=[draft],
        judgements=[
            JudgeResult(
                draft_id=draft.draft_id,
                judge_model="judge-x",
                scores={"overall": 8.0},
                winner_reason="只返回了整体分。",
                risk_notes=[],
                raw_text=json.dumps({"scores": {"overall": 8.0}}, ensure_ascii=False),
            )
        ],
        created_at=utc_now_iso(),
        dry_run=False,
    )
    paths = write_experiment_package(report, tmp_path / "arena")
    manual_selection = tmp_path / "manual-selection.json"
    manual_selection.write_text(
        json.dumps(
            {
                "case_id": case.case_id,
                "selections": {"A": {"choice": "best", "notes": "最佳"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    module._analyze_manual_selection(Path(paths["manifest"]), manual_selection)

    audit_paths = module._audit_experiment_manifest(
        Path(paths["manifest"]),
        manual_selection_path=manual_selection,
        expected_strategy_count=1,
        expected_writer_models=["writer-x"],
        expected_judge_models=["judge-x"],
    )

    payload = json.loads(Path(audit_paths["json"]).read_text(encoding="utf-8"))
    assert payload["status"] == "pending_human_or_external"
    assert not payload["failures"]
    assert {item["check"] for item in payload["pending"]} == {
        "judge_score_dimensions"
    }
    assert "opening_hook" in payload["pending"][0]["remediation"]


def test_script_analyzes_manual_selection_back_to_strategy(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_manual")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts = [
        DraftResult(
            draft_id=f"{variant.strategy.strategy_id}__dry-run__s1",
            variant_id=variant.variant_id,
            writer_model="dry-run-writer",
            sample_index=1,
            text=make_dry_run_draft(variant),
        )
        for variant in variants
    ]
    variant_by_id = {item.variant_id: item for item in variants}
    report = ExperimentReport(
        case=case,
        variants=variants,
        drafts=drafts,
        judgements=[
            make_dry_run_judgement(draft, variant_by_id[draft.variant_id])
            for draft in drafts
        ],
        created_at=utc_now_iso(),
        dry_run=True,
    )
    paths = write_experiment_package(report, tmp_path / "arena")
    manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
    labels_by_draft = {
        item["draft_id"]: item["blind_label"]
        for item in manifest["drafts"]
    }
    production_label = labels_by_draft["production_control__dry-run__s1"]
    golden_label = labels_by_draft["golden_three_opening__dry-run__s1"]
    manual_selection = tmp_path / "manual-selection.json"
    manual_selection.write_text(
        json.dumps(
            {
                "case_id": case.case_id,
                "selections": {
                    production_label: {"choice": "best", "notes": "开篇最抓人"},
                    golden_label: {"choice": "useful", "notes": "爽点可借"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    analysis_paths = module._analyze_manual_selection(
        Path(paths["manifest"]),
        manual_selection,
    )

    payload = json.loads(Path(analysis_paths["json"]).read_text(encoding="utf-8"))
    assert payload["diagnosis"]["status"] == "manual_best_found"
    assert payload["best"][0]["blind_label"] == production_label
    assert payload["best"][0]["strategy"]["strategy_id"] == "production_control"
    assert payload["best"][0]["prompt_path"].endswith("prompts/production_control.json")
    assert payload["best"][0]["notes"] == "开篇最抓人"
    assert "设计变量=" in payload["best"][0]["design_summary"]
    assert payload["production_prompt_patch_candidates"][0]["source_strategy_id"] == (
        "production_control"
    )
    assert payload["next_round_strategy_proposals"][0]["proposal_id"] == (
        "round2_selected_fusion"
    )
    assert production_label in payload["next_round_strategy_proposals"][0][
        "source_blind_labels"
    ]
    assert payload["next_round_strategy_proposals"][0]["prompt_rules"]
    assert payload["outline_probe_checklist"][0]["field"] == "first_visible_hook"
    assert "生产 writer prompt" in payload["next_experiment"][0]
    assert Path(analysis_paths["md"]).exists()
    md = Path(analysis_paths["md"]).read_text(encoding="utf-8")
    assert "生产 Prompt 回灌候选" in md
    assert "大纲/细纲反推检查" in md
    assert "下一轮实验" in md
    assert "二代策略草案" in md
    assert "- design:" in md
    assert "round2_selected_fusion" in md

    round2_paths = module._materialize_strategy_proposals(
        Path(paths["manifest"]),
        Path(analysis_paths["json"]),
        output_dir=tmp_path / "round2",
    )
    round2_manifest = json.loads(
        Path(round2_paths["prompt_manifest"]).read_text(encoding="utf-8")
    )
    round2_prompt = json.loads(
        (tmp_path / "round2" / "prompts" / "round2-selected-fusion.json").read_text(
            encoding="utf-8"
        )
    )
    round2_source = json.loads(
        (tmp_path / "round2" / "round2-source.json").read_text(encoding="utf-8")
    )
    round2_handoff = Path(round2_paths["prompt_handoff"]).read_text(encoding="utf-8")
    assert round2_manifest["variant_count"] == 3
    assert round2_manifest["case"]["case_id"].endswith("-round2")
    assert "round2-selected-fusion__minimax-m3.md" in round2_handoff
    assert "round2-selected-fusion__qwen3.7-plus-coding-plan.md" in round2_handoff
    assert "二代正文 prompt 实验" in round2_prompt["user_prompt"]
    assert "原始 user prompt" in round2_prompt["user_prompt"]
    assert round2_source["proposal_count"] == 3

    external_round2 = tmp_path / "round2-drafts"
    external_round2.mkdir()
    for item in round2_manifest["variants"]:
        (external_round2 / item["draft_filename"]).write_text(
            f"二代正文：{item['strategy_id']}",
            encoding="utf-8",
        )
    imported_paths = asyncio.run(
        module._run_prompt_manifest_import(
            Path(round2_paths["prompt_manifest"]),
            SimpleNamespace(
                import_drafts=str(external_round2),
                out=str(tmp_path / "round2-report"),
                import_writer_model="round2-writer",
                allow_partial_import=True,
                import_judgements=None,
                skip_judging=True,
                export_judge_prompts=True,
                judge_model=["judge-x"],
                allow_unavailable_models=False,
                judge_max_tokens=2048,
                resume=False,
            ),
        )
    )
    imported_manifest = json.loads(
        Path(imported_paths["manifest"]).read_text(encoding="utf-8")
    )
    assert imported_paths["drafts"] == "3"
    assert imported_paths["judgements"] == "0"
    assert Path(imported_paths["prompt_manifest"]).exists()
    assert imported_paths["judge_handoff"]
    assert len(imported_manifest["drafts"]) == 3
    assert imported_manifest["variants"][0]["strategy"]["strategy_id"].startswith("round2-")


def test_script_no_winner_generates_outline_repair_round2_package(tmp_path: Path) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_no_winner")
    case = load_prompt_trace(_write_trace(tmp_path))
    variants = _build_control_and_golden_variants(case)
    drafts = [
        DraftResult(
            draft_id=f"{variant.strategy.strategy_id}__dry-run__s1",
            variant_id=variant.variant_id,
            writer_model="dry-run-writer",
            sample_index=1,
            text=make_dry_run_draft(variant),
        )
        for variant in variants
    ]
    variant_by_id = {item.variant_id: item for item in variants}
    report = ExperimentReport(
        case=case,
        variants=variants,
        drafts=drafts,
        judgements=[
            make_dry_run_judgement(draft, variant_by_id[draft.variant_id])
            for draft in drafts
        ],
        created_at=utc_now_iso(),
        dry_run=True,
    )
    paths = write_experiment_package(report, tmp_path / "arena")
    manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
    manual_selection = tmp_path / "manual-selection.json"
    manual_selection.write_text(
        json.dumps(
            {
                "case_id": case.case_id,
                "selections": {
                    item["blind_label"]: {"choice": "reject", "notes": "都不够好"}
                    for item in manifest["drafts"]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    analysis_paths = module._analyze_manual_selection(
        Path(paths["manifest"]),
        manual_selection,
    )

    payload = json.loads(Path(analysis_paths["json"]).read_text(encoding="utf-8"))
    assert payload["diagnosis"]["status"] == "manual_no_winner"
    assert payload["best"] == []
    assert payload["useful"] == []
    assert payload["production_prompt_patch_candidates"] == []
    proposal_ids = [
        item["proposal_id"] for item in payload["next_round_strategy_proposals"]
    ]
    assert proposal_ids
    assert all(item.startswith("round2_outline_repair_") for item in proposal_ids)
    assert payload["next_round_strategy_proposals"][0]["outline_probe"]
    assert "先补大纲/细纲" in payload["next_round_strategy_proposals"][0]["run_note"]

    round2_paths = module._materialize_strategy_proposals(
        Path(paths["manifest"]),
        Path(analysis_paths["json"]),
        output_dir=tmp_path / "round2-no-winner",
    )
    round2_manifest = json.loads(
        Path(round2_paths["prompt_manifest"]).read_text(encoding="utf-8")
    )
    first_strategy_id = round2_manifest["variants"][0]["strategy_id"]
    first_prompt = json.loads(
        (
            tmp_path
            / "round2-no-winner"
            / "prompts"
            / f"{first_strategy_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert round2_manifest["variant_count"] == len(proposal_ids)
    assert first_strategy_id.startswith("round2-outline-repair-")
    assert "大纲/细纲反查点" in first_prompt["user_prompt"]
    assert "原始 user prompt" in first_prompt["user_prompt"]

    external_round2 = tmp_path / "round2-no-winner-drafts"
    external_round2.mkdir()
    for item in round2_manifest["variants"]:
        (external_round2 / item["draft_filename"]).write_text(
            f"无赢家二代正文：{item['strategy_id']}",
            encoding="utf-8",
        )
    imported_paths = asyncio.run(
        module._run_prompt_manifest_import(
            Path(round2_paths["prompt_manifest"]),
            SimpleNamespace(
                import_drafts=str(external_round2),
                out=str(tmp_path / "round2-no-winner-report"),
                import_writer_model="round2-writer",
                allow_partial_import=True,
                import_judgements=None,
                skip_judging=True,
                export_judge_prompts=False,
                judge_model=[],
                allow_unavailable_models=False,
                judge_max_tokens=2048,
                resume=False,
            ),
        )
    )
    imported_manifest = json.loads(
        Path(imported_paths["manifest"]).read_text(encoding="utf-8")
    )
    assert imported_paths["drafts"] == str(len(proposal_ids))
    assert len(imported_manifest["drafts"]) == len(proposal_ids)


def test_script_manifest_path_resolution_does_not_duplicate_existing_relative_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_arena_script("prose_prompt_strategy_arena_paths")
    monkeypatch.chdir(tmp_path)
    draft_path = Path("output/arena/drafts/a.md")
    draft_path.parent.mkdir(parents=True)
    draft_path.write_text("正文", encoding="utf-8")

    resolved = module._resolve_manifest_path(Path("output/arena"), "output/arena/drafts/a.md")

    assert resolved == draft_path


def _load_arena_script(module_name: str):
    script_path = Path(__file__).parents[2] / "scripts" / "prose_prompt_strategy_arena.py"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _write_trace(tmp_path: Path) -> Path:
    path = tmp_path / "trace.json"
    path.write_text(
        json.dumps(
            {
                "project": {"slug": "demo-book", "title": "示例书", "genre": "玄幻"},
                "chapter": {
                    "number": 1,
                    "title": "第一章",
                    "metadata": {
                        "causal_contract": {
                            "chapter_function": "desire_lock",
                            "protagonist_desire": "破解配阴婚死局",
                            "pressure": "活人少女即将被献祭",
                            "resistance": "古村邪祟干扰",
                            "protagonist_choice": "扎出假新郎欺骗规则",
                            "visible_action_or_reaction": "规则判定通过",
                            "gain_or_reveal": "拓印阴阳交泰规则",
                            "cost_or_tradeoff": "规则不完整",
                            "next_reader_desire": "厉绝锋为何劈碎纸人",
                        },
                        "methodology_contract": {
                            "pacing_mode": "puzzle_solving",
                            "emotion_phase": "horror_to_clever_relief",
                            "hooks_to_resolve": ["配阴婚死局破解"],
                            "hooks_to_plant": ["镇诡司追杀"],
                        },
                        "selected_effect_skills": {
                            "primary": "suspense_reveal_engine",
                            "secondary": "hype_satisfaction_engine",
                            "expected_contracts": {
                                "suspense_reveal_contract": "喜烛颜色揭示时辰被篡改",
                                "hype_satisfaction_contract": "假新郎拜堂成功",
                            },
                        },
                        "world_rule_landing": "利用节气与时辰差异欺骗规则",
                        "world_rule_refs": ["配阴婚民俗规则"],
                        "world_asset_refs": ["假新郎纸扎"],
                    },
                },
                "scene": {
                    "number": 2,
                    "type": "development",
                    "purpose": {"story": "破解死局", "emotion": "交付解谜爽感"},
                    "entry_state": {"summary": "路无咎开始现场扎纸"},
                    "exit_state": {"summary": "厉绝锋宣判死罪"},
                    "metadata": {
                        "methodology_contract": {
                            "action_sequence": [
                                "假新郎完成",
                                "替换成功",
                                "厉绝锋破墙",
                            ],
                            "conflict_stakes": "若替换失败，规则反噬",
                            "cut_point": "厉绝锋宣判死罪",
                            "signature_image": "雷霆之气劈碎纸人",
                        }
                    },
                },
                "prompt_stats": {"context_budget_tokens": 8000},
                "prompts": {"system": "原始 system prompt", "user": "原始 user prompt"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path
