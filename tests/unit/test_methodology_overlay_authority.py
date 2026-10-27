from copy import deepcopy
from types import SimpleNamespace

import pytest

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.services.chapter_causality_gate import evaluate_chapter_causality_contract
from bestseller.services.methodology_overlay import (
    methodology_contract_blocks,
    methodology_contract_requires_checks,
    resolve_methodology_contract_mode,
)
from bestseller.services.narrative_contracts import validate_scene_contract_pre_draft

pytestmark = pytest.mark.unit


def settings(mode="strict"):
    return SimpleNamespace(pipeline=SimpleNamespace(methodology_contract_mode=mode))


def project(metadata, genre="quiet travel"):
    return SimpleNamespace(metadata_json=metadata, genre=genre)


def explicit_hook(hook, source="explicit"):
    return {
        "creation_intent_contract": {
            "field_sources": {"hook_spec": source},
            "hook_spec": hook,
        }
    }


@pytest.mark.parametrize("genre", ["quiet travel", "science fiction"])
@pytest.mark.parametrize(
    "cache",
    [
        {},
        {"source": "user", "methodology_contract_strict": True},
        {"methodology_contract_mode": "strict"},
        {"methodology_overlay_mode": "strict"},
        {"methodology_gate_mode": "strict"},
        {"methodology_contract_warn_only": True},
        {"methodology_contract_mode": "warn"},
        {"methodology_contract": {"source": "user", "pressure_stack": ["AI_PLAN"]}},
    ],
)
def test_unselected_unknown_and_cached_modes_are_effectively_off(cache, genre):
    before = deepcopy(cache)
    resolved = resolve_methodology_contract_mode(project(cache, genre), settings=settings())
    assert resolved == "off"
    assert not methodology_contract_requires_checks(resolved)
    assert not methodology_contract_blocks(resolved)
    assert cache == before


@pytest.mark.parametrize("mode", ["strict", "warn", "off"])
def test_settings_only_cannot_grant_author_authority(mode):
    assert resolve_methodology_contract_mode(None, settings=settings(mode)) == "off"
    assert resolve_methodology_contract_mode(project({}), settings=settings(mode)) == "off"


@pytest.mark.parametrize("source", [None, "user", "default", "derived", "legacy"])
def test_hook_needs_canonical_explicit_provenance(source):
    metadata = explicit_hook(
        {"require_methodology_overlay": True, "methodology_contract_mode": "strict"}, source
    )
    assert resolve_methodology_contract_mode(project(metadata), settings=settings()) == "off"


@pytest.mark.parametrize("selection", [None, False, {}, [], "", "true", 1])
def test_explicit_empty_or_partial_selection_does_not_require_the_full_suite(selection):
    metadata = explicit_hook(
        {
            "require_methodology_overlay": selection,
            "methodology_contract_mode": "strict",
            "methodology_overlay": {"pacing_mode": "author-selected-partial-goal"},
        }
    )
    assert resolve_methodology_contract_mode(project(metadata), settings=settings()) == "off"


@pytest.mark.parametrize("effects", [[], ["comedy_engine"], ["hype_satisfaction_engine"]])
def test_explicit_book_palette_does_not_select_methodology(effects):
    metadata = {
        "methodology_contract_mode": "strict",
        "creation_intent_contract": {
            "field_sources": {"story_enhancers": "explicit", "hook_spec": "explicit"},
            "story_enhancers": {"effect_skills": effects},
            "hook_spec": {"methodology_contract_mode": "strict"},
        },
    }
    before = deepcopy(metadata)
    assert resolve_methodology_contract_mode(project(metadata), settings=settings()) == "off"
    assert metadata == before


@pytest.mark.parametrize("mode", ["strict", "warn", "off"])
def test_explicit_overlay_and_execution_policy_keep_the_selected_mode(mode):
    metadata = explicit_hook(
        {"require_methodology_overlay": True, "methodology_contract_mode": mode}
    )
    metadata["methodology_contract_strict"] = True
    before = deepcopy(metadata)
    assert resolve_methodology_contract_mode(project(metadata), settings=settings()) == mode
    assert metadata == before


@pytest.mark.parametrize(
    "flag,expected",
    [("methodology_contract_strict", "strict"), ("methodology_contract_warn_only", "warn")],
)
def test_explicit_canonical_policy_flags_are_supported(flag, expected):
    metadata = explicit_hook({"require_methodology_overlay": True, flag: True})
    assert resolve_methodology_contract_mode(project(metadata)) == expected


def test_content_selection_without_execution_policy_does_not_inherit_strict_settings():
    metadata = explicit_hook({"require_methodology_overlay": True})
    assert resolve_methodology_contract_mode(project(metadata), settings=settings()) == "off"


@pytest.mark.parametrize("mode", [None, "", "unknown-policy", {}])
def test_empty_or_unknown_execution_policy_is_off(mode):
    metadata = explicit_hook(
        {"require_methodology_overlay": True, "methodology_contract_mode": mode}
    )
    assert resolve_methodology_contract_mode(project(metadata), settings=settings()) == "off"


def test_causal_axes_remain_checked_without_forced_methodology():
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "unrelated-evidence",
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "Departure",
                    "goal": "Neri retrieves a missing letter.",
                    "main_conflict": "A steward refuses entry before the bridge closes.",
                    "hook_description": "Why does the letter name a different recipient?",
                    "causal_contract": {
                        "pressure": "The bridge closes at sunset.",
                        "protagonist_choice": "Neri exchanges her ticket.",
                        "visible_action_or_reaction": "She hands the ticket to the steward.",
                        "resistance": "The steward demands proof of ownership.",
                        "cost_or_tradeoff": "She loses her only passage ticket.",
                        "gain_or_reveal": "The letter names another recipient.",
                        "state_change": "Neri now holds proof of the missing delivery.",
                        "next_reader_desire": "Find who changed the recipient.",
                    },
                }
            ],
        }
    )
    before = batch.model_dump(mode="json")
    mode = resolve_methodology_contract_mode(
        project({"methodology_contract_mode": "strict"}), settings=settings()
    )
    report = evaluate_chapter_causality_contract(
        batch, require_methodology_overlay=methodology_contract_requires_checks(mode)
    )
    assert report.passed
    assert all(report.chapter_results[0].present_axes.values())
    assert batch.model_dump(mode="json") == before
    # Genuine causal gaps remain failures with exactly the same resolver result.
    broken = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "missing-cause",
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "Quiet room",
                    "goal": "She thinks.",
                    "scenes": [],
                }
            ],
        }
    )
    failed = evaluate_chapter_causality_contract(
        broken, require_methodology_overlay=methodology_contract_requires_checks(mode)
    )
    assert not failed.passed
    assert any("CAUSAL" in finding.code for finding in failed.findings)


def test_identity_hard_gate_stays_active_when_methodology_is_off():
    scene = SimpleNamespace(
        scene_number=1,
        time_label="Day 1 at noon",
        participants=["Neri"],
        purpose={"story": "Retrieve the signed letter."},
        metadata_json={},
    )
    mode = resolve_methodology_contract_mode(project({"methodology_contract_mode": "strict"}))
    report = validate_scene_contract_pre_draft(
        scene, require_identity_registry=True, methodology_contract_mode=mode
    )
    assert any("IDENTITY" in violation.code for violation in report.violations)
