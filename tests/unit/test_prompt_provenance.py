from __future__ import annotations

import pytest

from bestseller.services.prompt_assembly import resolve_selected_enhancer_keys
from bestseller.services.prompt_compiler import (
    PromptBlock,
    PromptConflictError,
    PromptProvenance,
    compile_prompt,
)

pytestmark = pytest.mark.unit


def _block(
    key: str,
    *,
    provenance: PromptProvenance | None = None,
    enhancer_key: str | None = None,
) -> PromptBlock:
    return PromptBlock(
        key=key,
        channel="user",
        layer="hard_canon",
        authority=100,
        instruction_family=key,
        required=True,
        source="test",
        text=key,
        provenance=provenance,
        enhancer_key=enhancer_key,
    )


def test_canonical_compilation_requires_provenance() -> None:
    with pytest.raises(PromptConflictError) as exc_info:
        compile_prompt(
            [_block("identity")],
            total_budget_tokens=100,
            safety_margin=0,
            require_provenance=True,
        )
    assert exc_info.value.conflicts == ("identity",)


def test_stale_canonical_snapshot_is_rejected() -> None:
    block = _block(
        "identity",
        provenance=PromptProvenance(
            kind="canonical_snapshot",
            source_id="snapshot-v1",
            source_hash="old-hash",
        ),
    )
    with pytest.raises(PromptConflictError) as exc_info:
        compile_prompt(
            [block],
            total_budget_tokens=100,
            safety_margin=0,
            canonical_snapshot_hash="new-hash",
        )
    assert exc_info.value.conflicts == ("identity",)


def test_only_selected_enhancer_blocks_enter_the_prompt() -> None:
    selected = _block("twist", enhancer_key="twist_reversal_engine")
    unselected = _block("comedy", enhancer_key="comedy_engine")
    result = compile_prompt(
        [selected, unselected],
        total_budget_tokens=100,
        safety_margin=0,
        selected_enhancer_keys=("twist_reversal_engine",),
    )

    assert "twist" in result.user
    assert "comedy" not in result.user
    assert "comedy" in result.report.dropped


def test_provenance_is_exposed_in_compiler_report() -> None:
    provenance = PromptProvenance(
        kind="canonical_snapshot",
        source_id="snapshot-v1",
        source_hash="same-hash",
    )
    result = compile_prompt(
        [_block("identity", provenance=provenance)],
        total_budget_tokens=100,
        safety_margin=0,
        canonical_snapshot_hash="same-hash",
        require_provenance=True,
    )

    assert result.report.provenance_sources == ("snapshot-v1",)
    assert result.report.canonical_snapshot_hash == "same-hash"


def test_selection_adapter_does_not_invent_unselected_effects() -> None:
    assert resolve_selected_enhancer_keys({"effect_skills": ["twist_reversal_engine"]}) == (
        "twist_reversal_engine",
    )
    assert resolve_selected_enhancer_keys({"effect_skills": None}) == ()
    assert resolve_selected_enhancer_keys({}) == ()
