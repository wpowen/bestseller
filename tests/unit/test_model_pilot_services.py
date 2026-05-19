from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.evaluation import (
    BenchmarkCaseSpec,
    ModelPilotQualityExpectation,
    ModelPilotRoleOverride,
    ModelPilotSpec,
    ModelPilotVariantSpec,
    ModelUsageSummary,
)
from bestseller.services import model_pilot as model_pilot_services
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def test_load_builtin_model_pilot_includes_minimax_and_deepseek() -> None:
    pilots = model_pilot_services.list_model_pilots()

    assert any(item.pilot_id == "short-complete-30" for item in pilots)
    spec = model_pilot_services.load_model_pilot("short-complete-30")
    enabled = {variant.variant_id for variant in spec.variants if variant.enabled}
    assert {"minimax-m27", "deepseek-official"} <= enabled
    assert spec.book.target_chapters == 30
    assert spec.quality.require_no_fallback is True
    assert spec.quality.require_sample_quality_parity is True


def test_apply_model_pilot_variant_overrides_roles_and_disables_fallback() -> None:
    settings = load_settings(env={"BESTSELLER__LLM__MOCK": "true"})
    variant = ModelPilotVariantSpec(
        variant_id="deepseek-official",
        label="DeepSeek",
        roles={
            "planner": ModelPilotRoleOverride(
                model="deepseek/deepseek-reasoner",
                api_base="https://api.deepseek.com/v1",
                api_key_env="DEEPSEEK_API_KEY",
            ),
            "writer": ModelPilotRoleOverride(
                model="deepseek/deepseek-reasoner",
                model_override="deepseek/deepseek-reasoner",
                api_base="https://api.deepseek.com/v1",
                api_key_env="DEEPSEEK_API_KEY",
                stream=False,
            ),
        },
    )

    patched = model_pilot_services.apply_model_pilot_variant(settings, variant)

    assert patched.llm.planner.model == "deepseek/deepseek-reasoner"
    assert patched.llm.planner.api_key_env == "DEEPSEEK_API_KEY"
    assert patched.llm.writer.model_override == "deepseek/deepseek-reasoner"
    assert patched.llm.writer.stream is False
    assert patched.llm.retry.rate_limit_fallback_enabled is False


@pytest.mark.asyncio
async def test_run_model_pilot_aggregates_variant_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_settings = load_settings(env={"BESTSELLER__LLM__MOCK": "true"})
    settings = base_settings.model_copy(
        update={
            "output": base_settings.output.model_copy(update={"base_dir": str(tmp_path)})
        }
    )
    spec = ModelPilotSpec(
        pilot_id="short-complete-30",
        title="30章完整故事模型试点",
        quality=ModelPilotQualityExpectation(require_sample_quality_parity=True),
        book=BenchmarkCaseSpec(
            case_id="short-complete-30",
            title="第七次日落档案",
            genre="suspense-mystery",
            target_word_count=60000,
            target_chapters=30,
            premise="30章内完结的规则悬疑。",
        ),
        variants=[
            ModelPilotVariantSpec(
                variant_id="minimax-m27",
                label="MiniMax",
                roles={
                    "writer": ModelPilotRoleOverride(
                        model="openai/MiniMax-M2.7-highspeed",
                        api_base="https://api.minimaxi.com/v1",
                    )
                },
            )
        ],
    )

    async def fake_run_autowrite_pipeline(session, settings, **kwargs):
        project_payload = kwargs["project_payload"]
        assert project_payload.target_chapters == 30
        assert project_payload.metadata["complete_story_required"] is True
        assert settings.llm.writer.model == "openai/MiniMax-M2.7-highspeed"
        return SimpleNamespace(
            project_id=uuid4(),
            project_slug=project_payload.slug,
            chapter_count=30,
            final_verdict="pass",
            requires_human_review=False,
            export_status="exported",
            output_dir=str(tmp_path / project_payload.slug),
            output_files=[str(tmp_path / project_payload.slug / "project.md")],
        )

    async def fake_review_project_consistency(session, settings, project_slug, **kwargs):
        return (
            SimpleNamespace(
                scores=SimpleNamespace(overall=0.86, resolution_completeness=0.91),
                findings=[],
                sample_quality_parity={
                    "scorecard_quality_score": 84.0,
                    "whole_book_quality_report": {"passed": True},
                    "premium_book_gate_report": {
                        "passed": True,
                        "capability_snapshot": {
                            "category_hard_engine": {"passed": True}
                        },
                    },
                    "reference_distance_score": 0.82,
                },
            ),
            None,
            None,
        )

    async def fake_collect_model_usage_summary(session, project_id):
        return ModelUsageSummary(
            request_count=12,
            fallback_count=0,
            total_input_tokens=1000,
            total_output_tokens=2000,
            total_latency_ms=3000,
            model_counts={"openai/MiniMax-M2.7-highspeed": 12},
            provider_counts={"openai": 12},
            role_counts={"writer": 12},
        )

    monkeypatch.setattr(
        model_pilot_services,
        "run_autowrite_pipeline",
        fake_run_autowrite_pipeline,
    )
    monkeypatch.setattr(
        model_pilot_services,
        "review_project_consistency",
        fake_review_project_consistency,
    )
    monkeypatch.setattr(
        model_pilot_services,
        "collect_model_usage_summary",
        fake_collect_model_usage_summary,
    )

    result = await model_pilot_services.run_model_pilot(
        object(),
        settings,
        spec=spec,
        slug_prefix="pilot30",
    )

    assert result.passed_variant_count == 1
    assert result.failed_variant_count == 0
    assert result.report_path is not None
    assert result.variant_results[0].chapter_count == 30
    assert result.variant_results[0].usage.fallback_count == 0
    assert result.variant_results[0].sample_quality_parity is not None
    assert result.variant_results[0].sample_quality_parity["passed"] is True
    assert any(
        check.check_name == "sample_quality_parity" and check.passed
        for check in result.variant_results[0].checks
    )
    assert (tmp_path / "model-pilots").exists()
