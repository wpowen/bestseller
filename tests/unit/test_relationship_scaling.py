"""Tests for the relationship-scaling gate.

Locks in the contract that prevents the observed production failure mode
of long novels shipping with only 3-5 supporting_cast entries — a thin
roster that forces scenes to recycle the same handful of faces across
every volume regardless of plot scale.
"""

from __future__ import annotations

import pytest

from bestseller.services.relationship_scaling import (
    MAX_ROLE_SHARE,
    MIN_DISTINCT_ROLE_CATEGORIES,
    MIN_NON_ANTAGONIST_PER_VOLUME,
    MIN_TOTAL_SUPPORTING_CAST,
    ROLE_BUCKETS,
    SUPPORTING_CAST_PER_VOLUME_CEILING_RATIO,
    SUPPORTING_CAST_PER_VOLUME_FLOOR_RATIO,
    _bucket_for_role,
    _parse_active_volumes,
    compute_supporting_bounds,
    render_relationship_constraints_block,
    scan_relationship_scaling,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cast(
    name: str,
    *,
    role: str = "ally",
    active_volumes: list | None = None,
    relationship: str = "挚友",
    evolution: str = "从陌路到生死之交",
) -> dict:
    return {
        "name": name,
        "role": role,
        "active_volumes": active_volumes if active_volumes is not None else [1, 2],
        "relationship_to_protagonist": relationship,
        "evolution_arc": evolution,
    }


def _healthy_roster(volume_count: int = 10) -> list[dict]:
    """Roster sized + diversified for a 10-volume book.

    Target floor for 10 volumes = ceil(10 * 1.5) = 15.
    Spread across 5 distinct buckets (mentor, ally, rival, family,
    romantic, subordinate, confidant) — none exceeding 40% share.
    Every volume has at least one active non-antagonist.
    """

    return [
        _cast("师父玄阳", role="mentor", active_volumes=[1, 2, 3, 4]),
        _cast("掌门师叔", role="teacher", active_volumes=[5, 6, 7]),
        _cast("道侣苏慕雪", role="romantic", active_volumes=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]),
        _cast("师兄林青", role="ally", active_volumes=[1, 2, 3, 4, 5]),
        _cast("剑痴陆沉", role="rival", active_volumes=[3, 4, 5, 6, 7]),
        _cast("妹妹苏婉", role="family", active_volumes=[1, 2, 8, 9, 10]),
        _cast("忠仆石头", role="subordinate", active_volumes=[2, 3, 4, 5, 6, 7, 8]),
        _cast("军师诸葛", role="confidant", active_volumes=[6, 7, 8, 9, 10]),
        _cast("盟友秦风", role="ally", active_volumes=[4, 5, 6]),
        _cast("对手叶孤", role="rival", active_volumes=[7, 8, 9]),
        _cast("父亲苏山", role="family", active_volumes=[1, 9, 10]),
        _cast("挚友周行", role="friend", active_volumes=[2, 3, 4]),
        _cast("青梅林姝", role="love_interest", active_volumes=[1, 2]),
        _cast("商人胡九", role="broker", active_volumes=[5, 6]),
        _cast("同伴阿狸", role="companion", active_volumes=[8, 9, 10]),
    ]


# ---------------------------------------------------------------------------
# Canonical role buckets
# ---------------------------------------------------------------------------

def test_role_buckets_contain_core_categories():
    """Verify the canonical buckets required by the diversity rule exist."""

    for bucket in (
        "mentor",
        "ally",
        "rival",
        "family",
        "romantic",
        "subordinate",
        "confidant",
        "broker",
        "antagonist",
    ):
        assert bucket in ROLE_BUCKETS
        assert ROLE_BUCKETS[bucket]


def test_bucket_for_role_english_labels():
    assert _bucket_for_role("mentor") == "mentor"
    assert _bucket_for_role("ally") == "ally"
    assert _bucket_for_role("rival") == "rival"
    assert _bucket_for_role("romantic_interest") == "romantic"
    assert _bucket_for_role("love_interest") == "romantic"
    assert _bucket_for_role("family") == "family"
    assert _bucket_for_role("subordinate") == "subordinate"
    assert _bucket_for_role("confidant") == "confidant"
    assert _bucket_for_role("broker") == "broker"
    assert _bucket_for_role("antagonist") == "antagonist"


def test_bucket_for_role_chinese_synonyms():
    assert _bucket_for_role("师父") == "mentor"
    assert _bucket_for_role("挚友") == "ally"
    assert _bucket_for_role("劲敌") == "rival"
    assert _bucket_for_role("父亲") == "family"
    assert _bucket_for_role("恋人") == "romantic"
    assert _bucket_for_role("部下") == "subordinate"
    assert _bucket_for_role("反派") == "antagonist"


def test_bucket_for_role_unknown_falls_into_other():
    assert _bucket_for_role("mysterious_stranger") == "other"
    assert _bucket_for_role("神秘人") == "other"


# ---------------------------------------------------------------------------
# Bounds calculation
# ---------------------------------------------------------------------------

def test_compute_supporting_bounds_ten_volumes():
    bounds = compute_supporting_bounds(10)
    # 10 * 1.5 = 15 floor
    assert bounds.floor == 15
    # 10 * 3.0 = 30 ceiling
    assert bounds.ceiling == 30


def test_compute_supporting_bounds_respects_minimum_floor():
    """A 2-volume novella still needs at least MIN_TOTAL_SUPPORTING_CAST."""

    bounds = compute_supporting_bounds(2)
    assert bounds.floor == MIN_TOTAL_SUPPORTING_CAST
    assert bounds.ceiling > bounds.floor


def test_compute_supporting_bounds_large_book():
    bounds = compute_supporting_bounds(24)
    # 24 * 1.5 = 36 floor
    assert bounds.floor == 36
    # 24 * 3.0 = 72 ceiling
    assert bounds.ceiling == 72


def test_compute_supporting_bounds_zero_treated_as_one():
    bounds = compute_supporting_bounds(0)
    assert bounds.floor >= MIN_TOTAL_SUPPORTING_CAST


# ---------------------------------------------------------------------------
# Active volumes parsing
# ---------------------------------------------------------------------------

def test_parse_active_volumes_flat_list():
    assert _parse_active_volumes([1, 2, 3]) == {1, 2, 3}


def test_parse_active_volumes_string_digits():
    assert _parse_active_volumes(["1", "2", "3"]) == {1, 2, 3}


def test_parse_active_volumes_range_tuple():
    assert _parse_active_volumes([[1, 3], [5, 7]]) == {1, 2, 3, 5, 6, 7}


def test_parse_active_volumes_dict_start_end():
    assert _parse_active_volumes([
        {"start_volume": 1, "end_volume": 3},
    ]) == {1, 2, 3}


def test_parse_active_volumes_dict_volumes_inner():
    assert _parse_active_volumes([{"volumes": [2, 4, 6]}]) == {2, 4, 6}


def test_parse_active_volumes_empty():
    assert _parse_active_volumes(None) == set()
    assert _parse_active_volumes([]) == set()


# ---------------------------------------------------------------------------
# Healthy roster
# ---------------------------------------------------------------------------

def test_healthy_roster_passes():
    roster = _healthy_roster(10)
    report = scan_relationship_scaling(
        roster, total_chapters=150, volume_count=10
    )
    assert report.supporting_cast_count == 15
    assert report.critical_count == 0
    assert report.is_critical is False


def test_healthy_roster_has_diverse_buckets():
    roster = _healthy_roster(10)
    report = scan_relationship_scaling(
        roster, total_chapters=150, volume_count=10
    )
    # All non-antagonist categories should show up
    assert report.distinct_role_buckets >= MIN_DISTINCT_ROLE_CATEGORIES


# ---------------------------------------------------------------------------
# Starved roster
# ---------------------------------------------------------------------------

def test_starved_roster_critical():
    """3 entries for a 24-volume book is starved — floor is 36."""

    roster = [
        _cast("角色1", role="mentor", active_volumes=[1, 2, 3]),
        _cast("角色2", role="ally", active_volumes=[4, 5, 6]),
        _cast("角色3", role="rival", active_volumes=[7, 8, 9]),
    ]
    report = scan_relationship_scaling(
        roster, total_chapters=316, volume_count=24
    )
    codes = {f.code for f in report.findings}
    assert "starved_supporting_cast" in codes
    assert report.is_critical


def test_empty_roster_yields_starvation():
    report = scan_relationship_scaling(
        [], total_chapters=150, volume_count=10
    )
    assert report.supporting_cast_count == 0
    codes = {f.code for f in report.findings}
    assert "starved_supporting_cast" in codes
    assert report.is_critical


# ---------------------------------------------------------------------------
# Bloated roster
# ---------------------------------------------------------------------------

def test_bloated_roster_warning():
    """Count above ceiling produces a warning, not a critical."""

    # For a 4-volume book, ceiling = max(floor+1, ceil(4*3.0)) = 12.
    # 20 entries blows through the ceiling.
    roster = [
        _cast(
            f"冗余角色{i}",
            role=("ally", "mentor", "family", "rival", "confidant")[i % 5],
            active_volumes=[((i % 4) + 1)],
        )
        for i in range(20)
    ]
    report = scan_relationship_scaling(
        roster, total_chapters=60, volume_count=4
    )
    codes = {f.code for f in report.findings}
    assert "bloated_supporting_cast" in codes
    bloated = next(
        f for f in report.findings if f.code == "bloated_supporting_cast"
    )
    assert bloated.severity == "warning"


# ---------------------------------------------------------------------------
# Monotonous role distribution
# ---------------------------------------------------------------------------

def test_monotonous_role_distribution_critical():
    """A roster stuffed with only one category is flat."""

    roster = [
        _cast(f"伙伴{i}", role="ally", active_volumes=[(i % 10) + 1])
        for i in range(15)
    ]
    report = scan_relationship_scaling(
        roster, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "monotonous_role_distribution" in codes
    finding = next(
        f for f in report.findings
        if f.code == "monotonous_role_distribution"
    )
    assert finding.severity == "critical"


def test_two_bucket_roster_still_monotonous():
    """Two buckets falls short of the MIN_DISTINCT_ROLE_CATEGORIES floor."""

    roster = [
        _cast(f"伙伴{i}", role="ally", active_volumes=[(i % 10) + 1])
        for i in range(8)
    ]
    roster.extend(
        _cast(f"导师{i}", role="mentor", active_volumes=[(i % 10) + 1])
        for i in range(7)
    )
    report = scan_relationship_scaling(
        roster, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "monotonous_role_distribution" in codes


# ---------------------------------------------------------------------------
# Dominant role share
# ---------------------------------------------------------------------------

def test_dominant_role_share_warning():
    """> 40% of roster sharing one bucket fires a warning."""

    roster = [
        _cast(f"盟友{i}", role="ally", active_volumes=[(i % 10) + 1])
        for i in range(10)  # 10/15 ≈ 66% ally
    ]
    # 5 other varied entries to keep diversity above the floor
    roster.extend([
        _cast("师父", role="mentor", active_volumes=[1, 2]),
        _cast("对手", role="rival", active_volumes=[3, 4]),
        _cast("恋人", role="romantic", active_volumes=[5, 6]),
        _cast("家人", role="family", active_volumes=[7, 8]),
        _cast("军师", role="confidant", active_volumes=[9, 10]),
    ])
    report = scan_relationship_scaling(
        roster, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "dominant_role_share" in codes
    finding = next(
        f for f in report.findings if f.code == "dominant_role_share"
    )
    assert finding.severity == "warning"
    assert finding.payload["dominant"] == "ally"
    assert finding.payload["share"] > MAX_ROLE_SHARE


# ---------------------------------------------------------------------------
# Per-volume non-antagonist coverage
# ---------------------------------------------------------------------------

def test_volume_without_non_antagonist_critical():
    """If V5-V7 have no active non-antagonist, flag them."""

    roster = [
        _cast("V1-V4盟友", role="ally", active_volumes=[1, 2, 3, 4]),
        _cast("V1-V4师父", role="mentor", active_volumes=[1, 2, 3, 4]),
        _cast("V1-V4家人", role="family", active_volumes=[1, 2, 3, 4]),
        _cast("V8-V10盟友", role="ally", active_volumes=[8, 9, 10]),
        _cast("V8-V10师父", role="mentor", active_volumes=[8, 9, 10]),
        _cast("V8-V10家人", role="family", active_volumes=[8, 9, 10]),
        # V5-V7 intentionally left without any non-antagonist coverage
    ]
    report = scan_relationship_scaling(
        roster, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "volume_without_non_antagonist" in codes
    finding = next(
        f for f in report.findings
        if f.code == "volume_without_non_antagonist"
    )
    for v in (5, 6, 7):
        assert v in finding.payload["volumes"]


def test_antagonist_only_entries_do_not_count_as_coverage():
    """Only non-antagonist buckets anchor warm scenes."""

    roster = [
        _cast("敌人1", role="antagonist", active_volumes=[1, 2, 3, 4, 5]),
        _cast("盟友", role="ally", active_volumes=[1, 2, 3, 4, 5]),
        _cast("师父", role="mentor", active_volumes=[1, 2, 3, 4, 5]),
        _cast("家人", role="family", active_volumes=[1, 2, 3, 4, 5]),
    ]
    # 5 volumes with only antagonist coverage in V6-V10
    roster.append(
        _cast("敌人2", role="antagonist", active_volumes=[6, 7, 8, 9, 10])
    )
    report = scan_relationship_scaling(
        roster, total_chapters=80, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "volume_without_non_antagonist" in codes
    finding = next(
        f for f in report.findings
        if f.code == "volume_without_non_antagonist"
    )
    for v in (6, 7, 8, 9, 10):
        assert v in finding.payload["volumes"]


# ---------------------------------------------------------------------------
# Missing lifecycle fields
# ---------------------------------------------------------------------------

def test_missing_fields_critical():
    roster = [
        {"name": "残缺配角"},  # missing everything else
    ]
    report = scan_relationship_scaling(
        roster, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "supporting_cast_missing_fields" in codes


def test_missing_evolution_arc_critical():
    """Evolution arc is a required lifecycle field."""

    roster = [{
        "name": "平面角色",
        "role": "ally",
        "active_volumes": [1, 2, 3],
        "relationship_to_protagonist": "好友",
        # evolution_arc missing
    }]
    report = scan_relationship_scaling(
        roster, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "supporting_cast_missing_fields" in codes


# ---------------------------------------------------------------------------
# Envelope / pydantic support
# ---------------------------------------------------------------------------

def test_accepts_envelope_with_supporting_cast_key():
    envelope = {"supporting_cast": _healthy_roster(10)}
    report = scan_relationship_scaling(
        envelope, total_chapters=150, volume_count=10
    )
    assert report.supporting_cast_count == 15
    assert report.critical_count == 0


def test_accepts_pydantic_like_entries():
    class _Fake:
        def model_dump(self):
            return {"supporting_cast": _healthy_roster(10)}

    report = scan_relationship_scaling(
        _Fake(), total_chapters=150, volume_count=10
    )
    assert report.supporting_cast_count == 15


def test_pydantic_like_individual_entries_supported():
    """Each roster item may itself be a pydantic-like object."""

    class _Entry:
        def __init__(self, **kw):
            self._kw = kw

        def model_dump(self):
            return dict(self._kw)

    roster = [_Entry(**_cast(f"角色{i}", role=bucket, active_volumes=[1, 2, 3]))
              for i, bucket in enumerate(
                  ("mentor", "ally", "rival", "family", "romantic", "confidant")
              )]
    report = scan_relationship_scaling(
        roster, total_chapters=30, volume_count=3
    )
    assert report.supporting_cast_count == 6
    # All non-antagonist; diversity satisfied
    assert report.distinct_role_buckets >= MIN_DISTINCT_ROLE_CATEGORIES


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def test_report_to_prompt_block_empty_when_healthy():
    roster = _healthy_roster(10)
    report = scan_relationship_scaling(
        roster, total_chapters=150, volume_count=10
    )
    assert report.to_prompt_block() == ""


def test_report_to_prompt_block_lists_findings_zh():
    report = scan_relationship_scaling(
        [], total_chapters=150, volume_count=10
    )
    block = report.to_prompt_block(language="zh-CN")
    assert block
    assert "关系网" in block or "supporting_cast" in block


def test_report_to_prompt_block_lists_findings_en():
    report = scan_relationship_scaling(
        [], total_chapters=150, volume_count=10
    )
    block = report.to_prompt_block(language="en-US")
    assert block
    assert "relationship" in block.lower() or "supporting_cast" in block.lower()


def test_render_constraints_block_zh():
    block = render_relationship_constraints_block(
        total_chapters=316, volume_count=24, language="zh-CN"
    )
    assert "316" in block
    assert "24" in block
    assert "supporting_cast" in block
    assert "evolution_arc" in block
    assert "active_volumes" in block


def test_render_constraints_block_en():
    block = render_relationship_constraints_block(
        total_chapters=544, volume_count=16, language="en-US"
    )
    assert "544" in block
    assert "16" in block
    assert "supporting_cast" in block.lower()
    assert "evolution_arc" in block.lower()
    assert "hard constraints" in block.lower()


def test_render_constraints_block_mentions_floor():
    """The prompt must name the concrete floor so the LLM hits it."""

    block = render_relationship_constraints_block(
        total_chapters=316, volume_count=24, language="zh-CN"
    )
    bounds = compute_supporting_bounds(24)
    assert str(bounds.floor) in block
    assert str(bounds.ceiling) in block


# ---------------------------------------------------------------------------
# Role-distribution payload sanity
# ---------------------------------------------------------------------------

def test_role_distribution_counts_buckets():
    roster = [
        _cast("师父", role="mentor", active_volumes=[1, 2]),
        _cast("盟友A", role="ally", active_volumes=[3, 4]),
        _cast("盟友B", role="ally", active_volumes=[5, 6]),
        _cast("对手", role="rival", active_volumes=[7, 8]),
        _cast("家人", role="family", active_volumes=[9, 10]),
        _cast("恋人", role="romantic", active_volumes=[1, 10]),
    ]
    report = scan_relationship_scaling(
        roster, total_chapters=60, volume_count=10
    )
    assert report.role_distribution["ally"] == 2
    assert report.role_distribution["mentor"] == 1
    assert report.role_distribution["rival"] == 1
    assert report.role_distribution["family"] == 1
    assert report.role_distribution["romantic"] == 1
