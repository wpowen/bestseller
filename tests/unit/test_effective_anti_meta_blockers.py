"""Real detector/checker boundary for independently configured prose actions."""

from copy import deepcopy
from dataclasses import asdict, replace
from itertools import product
from types import SimpleNamespace

import pytest

from bestseller.services.anti_meta_gate import (
    AntiMetaActionConfigError,
    check_anti_meta_gate,
    effective_anti_meta_blockers,
    filter_inactive_anti_meta_block_codes,
)
from bestseller.services.checker_schema import CheckerIssue
from bestseller.services.quality_gates_config import ProseQualityGateConfig

pytestmark = pytest.mark.unit


def config(**changes):
    return replace(ProseQualityGateConfig(), **changes)


@pytest.mark.parametrize(
    ("meta_enabled", "meta_severity", "ending_enabled", "ending_severity"),
    list(product((False, True), ("warn", "block"), (False, True), ("warn", "block"))),
)
def test_real_gate_action_matrix_preserves_raw_report_and_exact_checker_issues(
    meta_enabled, meta_severity, ending_enabled, ending_severity
):
    # Real hard leak, real soft leak and failed lexical ending in the same body.
    report = check_anti_meta_gate("本章交代的是所有人都在等待。蓝。", chapter_position=8)
    assert not report.passed and not report.ending_passed
    checker = report.to_checker_report()
    assert {i.id for i in checker.issues} == {
        "ANTI_META_HARD_TERM",
        "ANTI_META_SOFT_TERM",
        "ANTI_META_ENDING_OUT_OF_SCENE",
    }
    before_report, before_checker = deepcopy(asdict(report)), deepcopy(asdict(checker))
    cfg = config(
        anti_meta_enabled=meta_enabled,
        anti_meta_severity=meta_severity,
        in_scene_ending_enabled=ending_enabled,
        in_scene_ending_severity=ending_severity,
    )
    selected = effective_anti_meta_blockers(checker, cfg)
    expected = []
    if meta_enabled and meta_severity == "block":
        expected.append("ANTI_META_HARD_TERM")
    if ending_enabled and ending_severity == "block":
        expected.append("ANTI_META_ENDING_OUT_OF_SCENE")
    assert [issue.id for issue in selected] == expected
    assert all(any(issue is original for original in checker.issues) for issue in selected)
    assert effective_anti_meta_blockers(report, cfg) == selected
    assert asdict(report) == before_report and asdict(checker) == before_checker


def test_ending_only_observation_is_not_promoted_by_meta_block():
    report = check_anti_meta_gate("她朝门外答道:“明天。再说。”", chapter_position=9)
    # Keep the legacy detector's result as audit, including its quote-split limitation.
    assert not report.ending_passed and not report.passed and report.findings == ()
    assert (
        effective_anti_meta_blockers(
            report, config(anti_meta_severity="block", in_scene_ending_severity="warn")
        )
        == ()
    )
    explicit = effective_anti_meta_blockers(
        report, config(anti_meta_enabled=False, in_scene_ending_severity="block")
    )
    assert [issue.id for issue in explicit] == ["ANTI_META_ENDING_OUT_OF_SCENE"]
    assert not report.passed  # No report rewriting to manufacture a raw pass.


def test_blocked_meta_leak_is_still_blocked_when_ending_is_disabled():
    report = check_anti_meta_gate("下一章揭开答案。雨停了。她起身。她推开门。", chapter_position=7)
    assert report.ending_passed
    selected = effective_anti_meta_blockers(report, config(in_scene_ending_enabled=False))
    assert [issue.id for issue in selected] == ["ANTI_META_HARD_TERM"]
    assert selected[0].can_override is False


def test_soft_leak_remains_observation_under_explicit_meta_block():
    report = check_anti_meta_gate("所有人都在院中。雨停了。她起身。她推开门。", chapter_position=5)
    assert report.findings and report.passed
    assert effective_anti_meta_blockers(report, config()) == ()


def test_clean_real_checker_produces_no_invented_blockers():
    report = check_anti_meta_gate("她把门推开。", chapter_position=5)
    assert report.passed and report.to_checker_report().issues == ()
    assert effective_anti_meta_blockers(report, config()) == ()


@pytest.mark.parametrize(
    "field",
    [
        "anti_meta_enabled",
        "in_scene_ending_enabled",
        "anti_meta_severity",
        "in_scene_ending_severity",
    ],
)
def test_missing_action_flag_does_not_guess_authority(field):
    values = asdict(config())
    del values[field]
    with pytest.raises(AntiMetaActionConfigError, match=r"^anti_meta_action_config_invalid$"):
        effective_anti_meta_blockers(
            check_anti_meta_gate("蓝。", chapter_position=2), SimpleNamespace(**values)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("anti_meta_enabled", 1),
        ("in_scene_ending_enabled", "false"),
        ("anti_meta_severity", "unknown"),
        ("anti_meta_severity", []),
        ("in_scene_ending_severity", 1),
        ("in_scene_ending_severity", None),
    ],
)
def test_invalid_actions_cannot_silently_release_a_blocker(field, value):
    with pytest.raises(AntiMetaActionConfigError, match=r"^anti_meta_action_config_invalid$"):
        effective_anti_meta_blockers(
            check_anti_meta_gate("蓝。", chapter_position=2), config(**{field: value})
        )


def test_other_checker_or_unknown_issue_cannot_silently_enter_action_resolver():
    checker = check_anti_meta_gate("蓝。", chapter_position=2).to_checker_report()
    with pytest.raises(TypeError, match=r"^anti_meta_action_report_invalid$"):
        effective_anti_meta_blockers(replace(checker, agent="other-gate"), config())
    unknown = CheckerIssue("UNKNOWN_INTEGRITY_GATE", "other", "critical", "", "", "", False)
    with pytest.raises(ValueError, match=r"^anti_meta_action_issue_unknown$"):
        effective_anti_meta_blockers(replace(checker, issues=(*checker.issues, unknown)), config())


@pytest.mark.parametrize(
    ("text", "changes", "expected_codes"),
    [
        ("她朝门外答道:“明天。再说。”", {"in_scene_ending_severity": "warn"}, ()),
        (
            "她朝门外答道:“明天。再说。”",
            {"anti_meta_enabled": False, "in_scene_ending_severity": "block"},
            ("ANTI_META_ENDING_OUT_OF_SCENE",),
        ),
        (
            "本章交代等待。雨停了。她起身。她推开门。",
            {"in_scene_ending_enabled": False},
            ("ANTI_META_HARD_TERM",),
        ),
        ("本章交代等待。蓝。", {"anti_meta_enabled": False, "in_scene_ending_enabled": False}, ()),
    ],
)
def test_real_final_gate_consumes_actions_without_provider_or_detector_stub(
    monkeypatch, text, changes, expected_codes
):
    from bestseller.services import pipelines

    cfg = SimpleNamespace(
        ai_flavor=SimpleNamespace(enabled=False),
        prose_quality=config(show_dont_tell_enabled=False, **changes),
    )
    monkeypatch.setattr(pipelines, "get_quality_gates_config", lambda: cfg)
    result = pipelines.run_final_quality_gates(
        chapter_number=8,
        content_md=text,
        project=SimpleNamespace(language="zh-CN", metadata_json={}),
    )
    assert result.errors == []
    assert result.issues == [f"anti_meta:{code}" for code in expected_codes]
    assert result.passed is (not expected_codes)


@pytest.mark.parametrize(
    ("meta_enabled", "meta_severity", "ending_enabled", "ending_severity"),
    list(product((False, True), ("warn", "block"), (False, True), ("warn", "block"))),
)
def test_cache_read_view_has_independent_actions_and_is_idempotent(
    meta_enabled, meta_severity, ending_enabled, ending_severity
):
    end = ["ANTI_META_ENDING_OUT_OF_SCENE", "anti_meta:ANTI_META_ENDING_OUT_OF_SCENE"]
    leaks = [
        "ANTI_META_LEAK",
        "ANTI_META_HARD_TERM",
        "ANTI_META_SOFT_TERM",
        "anti_meta:ANTI_META_HARD_TERM",
    ]
    protected = ["CANON_FORBIDDEN_TERM", "LENGTH_UNDER", "CROSS_CHAPTER_REPETITION", "UNKNOWN"]
    codes = [*end, *protected, *leaks, "UNKNOWN", "LENGTH_UNDER"]
    before = deepcopy(codes)
    cfg = config(
        anti_meta_enabled=meta_enabled,
        anti_meta_severity=meta_severity,
        in_scene_ending_enabled=ending_enabled,
        in_scene_ending_severity=ending_severity,
    )
    view = filter_inactive_anti_meta_block_codes(codes, cfg)
    expected = [*end] if ending_enabled and ending_severity == "block" else []
    expected += protected
    expected += leaks if meta_enabled and meta_severity == "block" else []
    expected += ["UNKNOWN", "LENGTH_UNDER"]
    assert view == tuple(expected)
    assert codes == before
    assert filter_inactive_anti_meta_block_codes(view, cfg) == view
    assert filter_inactive_anti_meta_block_codes(iter(codes), cfg) == view


def test_cache_view_never_broad_matches_prefix_case_or_whitespace():
    codes = [
        "ANTI_META_UNKNOWN",
        "ANTI_META_ENDING_OUT_OF_SCENE_EXTRA",
        " ANTI_META_LEAK",
        "ANTI_META_LEAK ",
        "anti_meta_leak",
        "other:ANTI_META_LEAK",
        "anti_meta:UNKNOWN",
        "anti_meta:ANTI_META_HARD_TERM_EXTRA",
        "anti_meta:ANTI_META_LEAK",
        "CANON_STATE_REGRESSION",
        "PARAGRAPH_DUPLICATE_PARAPHRASE",
        "LENGTH_OVER",
    ]
    assert filter_inactive_anti_meta_block_codes(
        codes, config(anti_meta_enabled=False, in_scene_ending_enabled=False)
    ) == tuple(codes)


@pytest.mark.parametrize("text", ["蓝。", "A quiet blue.", "她朝门外答道:“明天。再说。”"])
def test_cross_story_real_detector_cache_view_preserves_raw_failure_and_metadata(text):
    report = check_anti_meta_gate(text, chapter_position=11)
    assert not report.ending_passed
    metadata = {
        "quality_bundle_blocking_codes": ["ANTI_META_ENDING_OUT_OF_SCENE", "LENGTH_UNDER"],
        "anti_meta": asdict(report),
        "rewrite_attempts": 2,
        "status": "blocked",
    }
    before = deepcopy(metadata)
    cfg = config(anti_meta_severity="block", in_scene_ending_severity="warn")
    assert filter_inactive_anti_meta_block_codes(
        metadata["quality_bundle_blocking_codes"], cfg
    ) == ("LENGTH_UNDER",)
    assert metadata == before and not report.passed
    assert effective_anti_meta_blockers(report, cfg) == ()


@pytest.mark.parametrize("codes", ["ANTI_META_LEAK", b"ANTI_META_LEAK", [None], [1]])
def test_malformed_flat_cache_is_not_silently_reinterpreted(codes):
    with pytest.raises(TypeError, match="anti_meta_block_code"):
        filter_inactive_anti_meta_block_codes(codes, config())


def test_cache_view_requires_complete_actual_config_even_for_empty_codes():
    with pytest.raises(AntiMetaActionConfigError):
        filter_inactive_anti_meta_block_codes((), SimpleNamespace())
    with pytest.raises(AntiMetaActionConfigError, match=r"^anti_meta_action_config_invalid$"):
        filter_inactive_anti_meta_block_codes((), config(in_scene_ending_enabled="false"))


def test_typed_config_error_is_exported_and_remains_value_error_compatible():
    from bestseller.services import anti_meta_gate

    assert "AntiMetaActionConfigError" in anti_meta_gate.__all__
    assert issubclass(AntiMetaActionConfigError, ValueError)
    with pytest.raises(ValueError) as captured:
        filter_inactive_anti_meta_block_codes((), None)
    assert type(captured.value) is AntiMetaActionConfigError
    assert isinstance(captured.value.__cause__, AttributeError)


@pytest.mark.parametrize("audit", ["absent", None, {"findings": []}])
@pytest.mark.parametrize("ending_severity", ["warn", "block"])
def test_actual_pipeline_cache_read_without_audit_report_does_not_restore_stale_ending(
    monkeypatch, audit, ending_severity
):
    from bestseller.services import pipelines
    from bestseller.services.write_gate import GateConfig

    codes = ["ANTI_META_ENDING_OUT_OF_SCENE", "ANTI_META_LEAK", "LENGTH_UNDER", "UNKNOWN"]
    metadata = {"quality_bundle_blocking_codes": codes, "attempt_count": 2}
    if audit != "absent":
        metadata["deterministic_audit_latest"] = audit
    before = deepcopy(metadata)
    cfg = SimpleNamespace(
        l6_gate=GateConfig(mode_by_violation={}),
        prose_quality=config(in_scene_ending_severity=ending_severity),
    )
    monkeypatch.setattr(pipelines, "get_quality_gates_config", lambda: cfg)
    view = pipelines._filter_inactive_audit_cache_codes(codes, metadata)
    expected = tuple(codes if ending_severity == "block" else codes[1:])
    assert view == expected
    assert pipelines._filter_inactive_audit_cache_codes(view, metadata) == expected
    assert metadata == before
