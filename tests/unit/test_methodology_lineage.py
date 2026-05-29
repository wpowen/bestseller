from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services import workflows
from bestseller.services.methodology_lineage import (
    METHODOLOGY_LINEAGE_METADATA_KEY,
    AppliedMethodology,
    MethodologyLineage,
    attach_methodology_lineage,
    methodology_lineage_from_dict,
    methodology_lineage_from_metadata,
    methodology_lineage_review_expectations,
    render_methodology_lineage_prompt_block,
)


def _applied(slot: str = "hook_ledger") -> AppliedMethodology:
    return AppliedMethodology(
        rule_id=f"wm.test.{slot}",
        slot=slot,
        craft_function=slot,
        target_artifact_path="chapter_contract.methodology_contract",
        application_hint="Use the rule in the planned artifact.",
        evidence_fields=("methodology_contract.hooks_to_plant",),
        verifiability="strict",
        gate_mode="block",
        indicator_targets=("hook_ledger_closure_rate",),
        source_lineage="writing_methodology",
        why_selected="test",
    )


@pytest.mark.unit
def test_methodology_lineage_round_trips_json() -> None:
    lineage = MethodologyLineage(
        chapter_no=3,
        genre_profile="xianxia",
        chapter_role="opening",
        selected=(_applied(),),
        selection_seed="abc123",
        budget_tokens=900,
        budget_cards=6,
    )

    restored = methodology_lineage_from_dict(lineage.to_dict())

    assert restored == lineage
    assert restored.for_slot("hook_ledger") == (_applied(),)
    assert restored.strict_only() == (_applied(),)
    assert restored.for_stage("review") == (_applied(),)


@pytest.mark.unit
def test_attach_and_read_lineage_from_metadata_preserves_other_keys() -> None:
    lineage = MethodologyLineage(
        chapter_no=1,
        genre_profile="fantasy",
        chapter_role="opening",
        selected=(_applied("opening_three_function"),),
        selection_seed="seed",
        budget_tokens=900,
        budget_cards=6,
    )

    metadata = attach_methodology_lineage({"methodology_contract": {"keep": True}}, lineage)

    assert metadata["methodology_contract"] == {"keep": True}
    assert methodology_lineage_from_metadata(metadata) == lineage


@pytest.mark.unit
def test_applied_methodology_requires_evidence_fields() -> None:
    with pytest.raises(ValueError, match="evidence_fields"):
        AppliedMethodology(
            rule_id="wm.invalid",
            slot="hook_ledger",
            craft_function="hook_ledger",
            target_artifact_path="hook_ledger",
            application_hint="",
            evidence_fields=(),
            verifiability="strict",
            gate_mode="block",
            indicator_targets=(),
            source_lineage="writing_methodology",
            why_selected="",
        )


@pytest.mark.unit
def test_workflow_lineage_sync_is_noop_when_selector_returns_none() -> None:
    chapter = SimpleNamespace(metadata_json={"methodology_contract": {"keep": True}})
    changed = workflows._sync_chapter_methodology_lineage(
        project=SimpleNamespace(metadata_json={}),
        chapter=chapter,
        chapter_outline=SimpleNamespace(chapter_number=1),
    )

    assert changed is False
    assert chapter.metadata_json == {"methodology_contract": {"keep": True}}


@pytest.mark.unit
def test_workflow_lineage_sync_attaches_selected_lineage(monkeypatch: pytest.MonkeyPatch) -> None:
    lineage = MethodologyLineage(
        chapter_no=2,
        genre_profile="fantasy",
        chapter_role="opening",
        selected=(_applied(),),
        selection_seed="abc123",
        budget_tokens=900,
        budget_cards=6,
    )

    def fake_select_lineage_for_chapter_outline(**_: object) -> MethodologyLineage:
        return lineage

    monkeypatch.setattr(
        workflows,
        "select_lineage_for_chapter_outline",
        fake_select_lineage_for_chapter_outline,
    )
    chapter = SimpleNamespace(metadata_json={"methodology_contract": {"keep": True}})

    changed = workflows._sync_chapter_methodology_lineage(
        project=SimpleNamespace(metadata_json={}),
        chapter=chapter,
        chapter_outline=SimpleNamespace(chapter_number=2),
    )

    assert changed is True
    assert chapter.metadata_json["methodology_contract"] == {"keep": True}
    assert chapter.metadata_json[METHODOLOGY_LINEAGE_METADATA_KEY] == lineage.to_dict()


@pytest.mark.unit
def test_render_lineage_prompt_block_uses_stage_specific_cards() -> None:
    lineage = MethodologyLineage(
        chapter_no=5,
        genre_profile="fantasy",
        chapter_role="setup",
        selected=(
            _applied("scene_causality_engine"),
            _applied("hook_ledger"),
            _applied("revision_repair_engine"),
        ),
        selection_seed="seed",
        budget_tokens=900,
        budget_cards=6,
    )
    payload = {METHODOLOGY_LINEAGE_METADATA_KEY: lineage.to_dict()}

    draft_block = render_methodology_lineage_prompt_block(
        payload,
        stage="prose_scene",
        language="zh-CN",
    )
    review_block = render_methodology_lineage_prompt_block(
        payload,
        stage="review",
        language="zh-CN",
    )

    assert "read only" in draft_block
    assert "scene_causality_engine" in draft_block
    assert "revision_repair_engine" not in draft_block
    assert "verify" in review_block
    assert "methodology_contract.hooks_to_plant" in review_block


@pytest.mark.unit
def test_lineage_review_expectations_are_rule_attributed() -> None:
    lineage = MethodologyLineage(
        chapter_no=5,
        genre_profile="fantasy",
        chapter_role="setup",
        selected=(_applied("hook_ledger"),),
        selection_seed="seed",
        budget_tokens=900,
        budget_cards=6,
    )

    expectations = methodology_lineage_review_expectations(
        {
            METHODOLOGY_LINEAGE_METADATA_KEY: lineage.to_dict(),
            "methodology_contract": {"hooks_to_plant": ["暗门后的第二枚印记"]},
        }
    )

    assert expectations == [
        (
            "methodology:wm.test.hook_ledger:methodology_contract.hooks_to_plant",
            "暗门后的第二枚印记",
        )
    ]
