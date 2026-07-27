from __future__ import annotations

import json

import pytest

from bestseller.services.prompt_assembly import adapt_compiler_report
from bestseller.services.prompt_compiler import (
    PromptBlock,
    PromptBudgetError,
    PromptConflictError,
    PromptProvenance,
    compile_prompt,
)
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def block(
    key: str,
    *,
    text: str | None = None,
    channel: str = "user",
    layer: str = "optional",
    authority: int = 10,
    family: str | None = None,
    required: bool = False,
    min_tokens: int = 0,
    max_tokens: int | None = None,
    trim_policy: str = "drop",
    source: str = "test",
    provenance: PromptProvenance | None = None,
) -> PromptBlock:
    return PromptBlock(
        key=key,
        channel=channel,
        layer=layer,
        authority=authority,
        instruction_family=family or key,
        required=required,
        min_tokens=min_tokens,
        max_tokens=max_tokens,
        trim_policy=trim_policy,
        source=source,
        text=text or key,
        provenance=provenance,
    )


def test_semantic_family_dedupes_by_authority_not_literal_text() -> None:
    result = compile_prompt(
        [
            block(
                "weak-hook",
                text="Open with something interesting.",
                layer="craft",
                authority=10,
                family="opening-hook",
            ),
            block(
                "strong-hook",
                text="Open on the concrete threat already in motion.",
                layer="scene_spec",
                authority=90,
                family="opening_hook",
            ),
        ],
        total_budget_tokens=200,
        safety_margin=0,
    )

    assert "concrete threat" in result.user
    assert "something interesting" not in result.user
    assert result.report.duplicates == ("weak-hook",)


def test_layer_then_authority_controls_render_order() -> None:
    result = compile_prompt(
        [
            block("optional", layer="optional", authority=999),
            block("craft-low", layer="craft", authority=1),
            block("output", layer="output", authority=1),
            block("scene-low", layer="scene_spec", authority=1),
            block("canon-low", layer="hard_canon", authority=1),
            block("craft-high", layer="craft", authority=100),
        ],
        total_budget_tokens=500,
        safety_margin=0,
    )

    assert result.user.split("\n\n") == [
        "canon-low",
        "scene-low",
        "output",
        "craft-high",
        "craft-low",
        "optional",
    ]


def test_different_required_hard_blocks_in_same_family_fail_closed() -> None:
    with pytest.raises(PromptConflictError, match="word_count") as exc_info:
        compile_prompt(
            [
                block(
                    "word-band-a",
                    text="Write 1800-2600 words.",
                    layer="hard_canon",
                    family="word-count",
                    required=True,
                ),
                block(
                    "word-band-b",
                    text="Write 3000-4500 words.",
                    layer="output",
                    family="word_count",
                    required=True,
                ),
            ],
            total_budget_tokens=500,
            safety_margin=0,
        )

    assert exc_info.value.conflicts == ("word-band-a", "word-band-b")


def test_required_hard_core_over_budget_fails_closed() -> None:
    with pytest.raises(PromptBudgetError) as exc_info:
        compile_prompt(
            [
                block(
                    "canon",
                    text="必须遵守。" * 120,
                    layer="hard_canon",
                    required=True,
                    trim_policy="preserve",
                )
            ],
            total_budget_tokens=20,
            safety_margin=0,
        )

    assert exc_info.value.required_keys == ("canon",)


def test_budget_error_message_names_the_blocks_and_the_gap() -> None:
    """The operator only ever sees ``str(exc)``.

    ``required_keys`` lives on the exception object, but the volume workflow
    persists ``str(exc)`` into ``workflow_runs.error_message`` — three real
    books failed on 2026-07-26 showing nothing but "required hard core exceeds
    combined writer prompt budget", with no way to tell which blocks were
    oversized or by how much. Whoever reads the failure must be able to act on
    the text alone.
    """

    with pytest.raises(PromptBudgetError) as exc_info:
        compile_prompt(
            [
                block(
                    "canon",
                    text="必须遵守。" * 120,
                    layer="hard_canon",
                    required=True,
                    trim_policy="preserve",
                ),
                block(
                    "spec",
                    text="章节计划。" * 80,
                    layer="scene_spec",
                    required=True,
                    trim_policy="preserve",
                ),
            ],
            total_budget_tokens=20,
            safety_margin=0,
        )

    message = str(exc_info.value)
    assert "canon" in message, "the offending block keys must be in the message"
    assert "20" in message, "the available budget must be in the message"
    # The measured requirement, so the reader can size the gap without a repro.
    assert any(str(n) in message for n in (exc_info.value.required_tokens,))


def test_no_usable_budget_error_also_names_the_numbers() -> None:
    with pytest.raises(PromptBudgetError) as exc_info:
        compile_prompt(
            [block("canon", text="x", layer="hard_canon", required=True)],
            total_budget_tokens=10,
            safety_margin=0.99,
        )

    message = str(exc_info.value)
    assert "10" in message, "the configured budget must be in the message"
    assert "0.99" in message, "the safety margin that consumed it must be too"


def test_compilation_and_hash_are_deterministic_for_same_blocks() -> None:
    blocks = [
        block("system", text="You write prose.", channel="system", layer="output"),
        block("task", text="Write the confrontation.", layer="scene_spec"),
    ]

    first = compile_prompt(blocks, total_budget_tokens=200, safety_margin=0.1)
    second = compile_prompt(reversed(blocks), total_budget_tokens=200, safety_margin=0.1)

    assert first == second
    assert first.report.final_hash == second.report.final_hash
    assert len(first.report.final_hash) == 64


def test_craft_effects_are_limited_to_one_primary_and_one_secondary() -> None:
    result = compile_prompt(
        [
            block(
                "primary-best",
                layer="craft",
                authority=50,
                family="craft.effect.primary.visceral",
            ),
            block(
                "primary-extra",
                layer="craft",
                authority=10,
                family="craft.effect.primary.irony",
            ),
            block(
                "secondary-best",
                layer="craft",
                authority=40,
                family="craft.effect.secondary.rhythm",
            ),
            block(
                "secondary-extra",
                layer="craft",
                authority=5,
                family="craft.effect.secondary.imagery",
            ),
        ],
        total_budget_tokens=200,
        safety_margin=0,
    )

    assert "primary-best" in result.user
    assert "secondary-best" in result.user
    assert "primary-extra" not in result.user
    assert "secondary-extra" not in result.user
    assert set(result.report.dropped) == {"primary-extra", "secondary-extra"}


def test_primary_task_and_hard_obligation_limits_fail_closed_when_required() -> None:
    with pytest.raises(PromptConflictError, match="primary task"):
        compile_prompt(
            [
                block(
                    "task-a",
                    family="scene.primary_task",
                    layer="scene_spec",
                    required=True,
                ),
                block(
                    "task-b",
                    family="chapter.primary_task",
                    layer="scene_spec",
                    required=True,
                ),
            ],
            total_budget_tokens=200,
            safety_margin=0,
        )

    with pytest.raises(PromptConflictError, match="hard obligations"):
        compile_prompt(
            [
                block(
                    f"obligation-{index}",
                    family=f"scene.hard_obligation.{index}",
                    layer="scene_spec",
                    required=True,
                )
                for index in range(6)
            ],
            total_budget_tokens=500,
            safety_margin=0,
        )


def test_combined_channels_obey_budget_margin_and_report_is_serializable() -> None:
    result = compile_prompt(
        [
            block(
                "system",
                text="S" * 120,
                channel="system",
                layer="output",
                required=True,
            ),
            block(
                "task",
                text="U" * 120,
                layer="scene_spec",
                required=True,
            ),
            block(
                "optional",
                text="O" * 800,
                layer="optional",
                trim_policy="truncate_tail",
                min_tokens=10,
            ),
        ],
        total_budget_tokens=100,
        safety_margin=0.10,
    )

    report = result.report
    assert report.system_tokens + report.user_tokens == report.total_tokens
    assert report.total_tokens <= report.usable_budget_tokens == 90
    assert report.budget_remaining_tokens == 90 - report.total_tokens
    assert report.required_complete is True
    assert json.loads(report.model_dump_json())["final_hash"] == report.final_hash


def test_default_settings_expose_total_writer_budget_and_margin() -> None:
    settings = load_settings(env={})

    assert settings.llm.writer_total_input_budget_tokens == 8000
    assert settings.llm.writer_prompt_safety_margin == pytest.approx(0.10)


def test_legacy_assembly_report_can_adapt_compiler_report_without_global_state() -> None:
    compiled = compile_prompt(
        [block("task", layer="scene_spec", required=True)],
        total_budget_tokens=100,
        safety_margin=0.1,
    )

    adapted = adapt_compiler_report(compiled.report)

    assert adapted.budget_tokens == 100
    assert adapted.total_kept_tokens == compiled.report.total_tokens
    assert adapted.mode == "compiled"
    assert adapted.dropped_keys == compiled.report.dropped


def test_phase_allowlist_keeps_only_phase_blocks_and_reports_reasons() -> None:
    result = compile_prompt(
        [
            block("event", layer="scene_spec", required=True),
            block("enhancer", layer="craft"),
        ],
        total_budget_tokens=100,
        safety_margin=0,
        phase="scene",
        phase_allowlist={"scene": ("event",)},
    )

    assert result.report.kept == ("event",)
    assert "enhancer" in result.report.dropped
    assert result.report.drop_reasons["enhancer"] == "phase_not_allowlisted"


def test_disallowed_required_phase_block_fails_closed() -> None:
    with pytest.raises(PromptConflictError, match="allowlist"):
        compile_prompt(
            [block("event", required=True)],
            total_budget_tokens=100,
            safety_margin=0,
            phase="macro_outline",
            phase_allowlist={"macro_outline": ()},
        )


def test_required_unselected_enhancer_fails_closed() -> None:
    with pytest.raises(PromptConflictError, match="unselected enhancers"):
        compile_prompt(
            [
                PromptBlock(
                    key="required-effect",
                    channel="user",
                    layer="scene_spec",
                    authority=100,
                    instruction_family="required-effect",
                    required=True,
                    source="test",
                    text="必须执行反转",
                    enhancer_key="twist_reversal_engine",
                )
            ],
            total_budget_tokens=100,
            safety_margin=0,
            selected_enhancer_keys=(),
        )


def test_explicit_character_budget_fails_without_silent_truncation() -> None:
    with pytest.raises(PromptBudgetError):
        compile_prompt(
            [block("required", text="x" * 20, required=True)],
            total_budget_tokens=100,
            total_budget_chars=10,
            safety_margin=0,
        )


def test_source_snapshot_hash_alias_is_checked_and_reported() -> None:
    result = compile_prompt(
        [
            block(
                "canon",
                required=True,
                provenance=PromptProvenance(
                    kind="canonical_snapshot", source_id="snap", source_hash="hash"
                ),
            )
        ],
        total_budget_tokens=100,
        source_snapshot_hash="hash",
        safety_margin=0,
    )
    assert result.report.source_snapshot_hash == "hash"
