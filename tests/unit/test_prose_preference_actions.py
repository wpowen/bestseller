"""Default craft preferences cannot become hard chapter obligations via caches."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from bestseller.services import pipelines, quality_gates_config
from bestseller.services.anti_meta_gate import AntiMetaActionConfigError, check_anti_meta_gate
from bestseller.services.chapter_quality_bundle import ChapterQualityBundleContext, run_chapter_quality_bundle

pytestmark = pytest.mark.unit
ENDING = "ANTI_META_ENDING_OUT_OF_SCENE"


def test_current_default_and_missing_setting_are_advisory():
    assert quality_gates_config.ProseQualityGateConfig().in_scene_ending_severity == "warn"
    assert quality_gates_config._build_prose_quality({}).in_scene_ending_severity == "warn"
    assert quality_gates_config.get_quality_gates_config().prose_quality.in_scene_ending_severity == "warn"


@pytest.mark.parametrize("ending_action", ["warn", "block"])
def test_real_bundle_respects_independent_action_without_rewriting_raw_report(monkeypatch, ending_action):
    original = quality_gates_config.get_quality_gates_config()
    prose = replace(original.prose_quality, anti_meta_severity="block", in_scene_ending_severity=ending_action)
    monkeypatch.setattr(quality_gates_config, "get_quality_gates_config", lambda: replace(original, prose_quality=prose))
    body = "她朝门外答道:“明天。再说。”"
    raw = check_anti_meta_gate(body, chapter_position=9)
    assert raw.ending_passed is False and raw.findings == ()
    report = run_chapter_quality_bundle(body, ChapterQualityBundleContext(chapter_number=9))
    ending, = [f for f in report.findings if f.code == ENDING]
    assert ending.blocking is (ending_action == "block")
    assert ending.evidence["author_rule_inferred"] is False
    assert raw.ending_passed is False and raw.passed is False
    assert (ENDING in report.to_dict()["blocking_codes"]) is (ending_action == "block")


@pytest.mark.parametrize("explicit_block", [False, True])
def test_old_flat_views_do_not_resurrect_retired_preference(monkeypatch, explicit_block):
    original = quality_gates_config.get_quality_gates_config()
    cfg = replace(original, prose_quality=replace(original.prose_quality,
        in_scene_ending_severity="block" if explicit_block else "warn"))
    monkeypatch.setattr(pipelines, "get_quality_gates_config", lambda: cfg)
    metadata = {"quality_contract_version": "quality-contract-v2-heuristic-authority",
        "quality_bundle_blocking_codes": [ENDING, "CANON_FORBIDDEN_TERM"],
        "quality_gate_block_codes": [ENDING, "LENGTH_OVER"],
        "auto_repair_last_block_codes": [ENDING, "CANON_FORBIDDEN_TERM"],
        "production_block_code": ENDING, "auto_repair_total_attempts": 7,
        "unrelated_authored_clause": "The narrator may end with a question."}
    before = deepcopy(metadata)
    chapter = SimpleNamespace(metadata_json=metadata)
    first = pipelines._active_quality_block_codes_from_metadata(metadata)
    second = pipelines._active_quality_block_codes_from_metadata(metadata)
    assert first == second and (ENDING in first) is explicit_block
    assert "CANON_FORBIDDEN_TERM" in first and "LENGTH_OVER" in first
    assert (ENDING in pipelines._current_auto_repair_block_codes(chapter)) is explicit_block
    assert metadata == before


@pytest.mark.parametrize("strict", [False, True])
def test_invalid_action_is_configuration_failure_not_a_prose_finding(monkeypatch, strict):
    original = quality_gates_config.get_quality_gates_config()
    cfg = replace(original, prose_quality=replace(original.prose_quality,
        in_scene_ending_severity="unsupported_action"))
    monkeypatch.setattr(quality_gates_config, "get_quality_gates_config", lambda: cfg)
    with pytest.raises(AntiMetaActionConfigError):
        run_chapter_quality_bundle("她朝门外答道:“明天。再说。”",
            ChapterQualityBundleContext(chapter_number=9, commercial_strict=strict))
