"""Unknown threshold lookups must not prescribe another genre's content."""

from __future__ import annotations

import json

import pytest
import yaml

from bestseller.services import genre_profile_thresholds as thresholds
from bestseller.services.genre_review_profiles import load_thresholds as review_thresholds
from bestseller.services.genre_skill_profiles import (
    attach_genre_skill_profile,
    genre_skill_profile_from_metadata,
    resolve_genre_skill_profile,
)
from bestseller.services.narrative_line_tracker import report_gaps


@pytest.fixture(autouse=True)
def clear_threshold_cache():
    thresholds._reset_cache()
    yield
    thresholds._reset_cache()


def assert_neutral(profile: thresholds.GenreProfileThresholds) -> None:
    assert profile.id == "unspecified"
    assert profile.hook_config.preferred_types == ()
    assert profile.hook_config.strength_baseline is None
    assert profile.hook_config.chapter_end_required is False
    assert profile.coolpoint_config.preferred_patterns == ()
    assert profile.coolpoint_config.density_per_chapter is None
    assert profile.coolpoint_config.combo_interval == 0
    assert profile.coolpoint_config.milestone_interval == 0
    assert profile.micropayoff_config.preferred_types == ()
    assert profile.micropayoff_config.min_per_chapter == 0
    assert profile.micropayoff_config.transition_min == 0
    # Operational bounds remain independently grounded in the generic models.
    assert profile.pacing_config == thresholds.PacingThresholds()
    assert profile.override_config == thresholds.OverrideConfig()


@pytest.mark.parametrize(
    "genre", [None, "", " \t\n", "现实", "纪实", "default", "unspecified",
              "unknown-experimental-genre", "literary slice of life", "科幻社会寓言"],
)
def test_unknown_or_unspecified_has_no_content_prescription(genre):
    assert_neutral(thresholds.resolve_thresholds(genre))
    assert_neutral(review_thresholds(genre))


def test_missing_neutral_yaml_still_fails_to_neutral_not_legacy_defaults(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(thresholds, "_THRESHOLDS_DIR", tmp_path)
    assert_neutral(thresholds.resolve_thresholds(None))
    assert_neutral(thresholds.resolve_thresholds("unlisted-genre"))
    assert_neutral(thresholds.resolve_thresholds("action-progression"))


def test_unknown_fallback_warning_reports_neutral_destination(caplog):
    thresholds.resolve_thresholds("现实")
    assert "Genre thresholds missing for 现实; falling back to unspecified" in caplog.text
    assert "falling back to action-progression" not in caplog.text


def test_neutral_json_and_yaml_roundtrips_preserve_unset_targets():
    original = thresholds.resolve_thresholds(None)
    assert thresholds.parse_thresholds(json.loads(json.dumps(original.to_dict()))) == original
    restored_yaml = yaml.safe_load(yaml.safe_dump(original.to_dict()))
    assert thresholds.parse_thresholds(restored_yaml) == original


@pytest.mark.parametrize("genre", thresholds.KNOWN_GENRE_IDS)
def test_explicit_profiles_match_their_yaml_and_roundtrip(genre):
    raw = yaml.safe_load((thresholds._THRESHOLDS_DIR / f"{genre}.yaml").read_text())
    profile = thresholds.resolve_thresholds(genre)
    assert profile == thresholds.parse_thresholds(raw)
    assert thresholds.parse_thresholds(profile.to_dict()) == profile


@pytest.mark.parametrize("raw,canonical", thresholds._RAW_GENRE_KEYWORDS)
def test_existing_aliases_keep_exact_explicit_profile(raw, canonical):
    assert thresholds.resolve_thresholds(raw) == thresholds.load_thresholds(canonical)


def test_user_composite_alias_still_selects_action_progression():
    assert thresholds.resolve_thresholds("未来玄幻爽文") == thresholds.load_thresholds(
        "action-progression"
    )


def test_author_supplied_profile_is_not_neutralized(monkeypatch, tmp_path):
    raw = {
        "id": "author-selected", "name": "Author selected",
        "hook_config": {"preferred_types": ["author hook"], "strength_baseline": "strong",
                        "chapter_end_required": True, "transition_allowance": 8},
        "coolpoint_config": {"preferred_patterns": ["author beat"], "density_per_chapter": "high",
                             "combo_interval": 9, "milestone_interval": 27},
        "micropayoff_config": {"preferred_types": ["author reward"], "min_per_chapter": 4,
                               "transition_min": 2},
        "pacing_config": {"strand_max_gap": {"overt": 77}},
        "override_config": {"allowed_rationale_types": ["EDITORIAL_INTENT"],
                            "debt_multiplier": 1.7},
    }
    (tmp_path / "author-selected.yaml").write_text(yaml.safe_dump(raw))
    monkeypatch.setattr(thresholds, "_THRESHOLDS_DIR", tmp_path)
    selected = thresholds.resolve_thresholds("author-selected")
    assert selected == thresholds.parse_thresholds(raw)
    assert selected.hook_config.chapter_end_required is True
    assert selected.micropayoff_config.min_per_chapter == 4
    assert thresholds.parse_thresholds(selected.to_dict()) == selected


def test_explicit_partial_profile_retains_legacy_defaults():
    parsed = thresholds.parse_thresholds({"id": "custom"})
    assert parsed.hook_config == thresholds.HookConfig()
    assert parsed.hook_config.chapter_end_required is True
    assert parsed.micropayoff_config.min_per_chapter == 2


def test_unknown_skill_snapshot_records_neutral_and_roundtrips():
    profile = resolve_genre_skill_profile("unknown-experimental-genre")
    assert profile.review_profile_key == "default"
    assert profile.threshold_profile_key == "unspecified"
    assert profile.profile_key == "unspecified"
    assert profile.activation.gate_mode == "audit_only"
    metadata = {"author_contract": {"length": 2600, "identity": "author-controlled"}}
    attached = attach_genre_skill_profile(metadata, profile)
    assert attached["author_contract"] == metadata["author_contract"]
    assert "genre_skill_profile" not in metadata
    assert genre_skill_profile_from_metadata(attached) == profile


def test_existing_explicit_snapshot_is_read_without_semantic_rebinding():
    metadata = attach_genre_skill_profile({}, resolve_genre_skill_profile("玄幻", "升级"))
    metadata["genre_skill_profile"]["threshold_profile_key"] = "action-progression"
    snapshot = genre_skill_profile_from_metadata(metadata)
    assert snapshot is not None
    assert snapshot.threshold_profile_key == "action-progression"
    assert snapshot.to_metadata() == metadata["genre_skill_profile"]


def test_narrative_gap_explicit_pacing_still_wins_over_unknown_genre():
    pacing = thresholds.PacingThresholds(strand_max_gap={
        "overt": 77, "undercurrent": 78, "hidden": 79, "core_axis": 80,
    })
    report = report_gaps(project_id="independent", current_chapter=30, genre_id="现实",
                         history=[{"chapter_no": 1, "dominant_line": "overt",
                                   "line_intensity": 0.9}], pacing_config=pacing)
    assert report.pacing_config == pacing
    assert all(gap.severity == "ok" for gap in report.gaps)


def test_missing_file_and_blank_share_generic_operational_bounds():
    report = report_gaps(project_id="independent", current_chapter=30, genre_id="现实",
                         history=[])
    assert report.pacing_config == thresholds.PacingThresholds()
    assert all(gap.severity == "ok" for gap in report.gaps)
