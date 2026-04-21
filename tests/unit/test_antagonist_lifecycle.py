"""Tests for the antagonist-lifecycle gate.

Locks in the evolution contract that prevents the observed production
failure modes:

  * 道种破虚: every volume's antagonist_plan labelled '元婴老者' (rotating
    label) — regressed into identical_overt_antagonist_labels finding.
  * Post-fix regression: every antagonist still a one-volume kill-and-
    move-on boss → flat story despite different names. The lifecycle
    gate rejects this via monotonous_resolution_types and
    all_antagonists_killed_template findings.
"""

from __future__ import annotations

import pytest

from bestseller.services.antagonist_lifecycle import (
    CANONICAL_LINE_ROLES,
    CANONICAL_RESOLUTIONS,
    LINE_ROLE_HIDDEN,
    LINE_ROLE_OVERT,
    LINE_ROLE_UNDERCURRENT,
    MAX_SAME_RESOLUTION_RATIO,
    MIN_NON_KILLED_ANTAGONIST_RATIO,
    MIN_TOTAL_ANTAGONISTS,
    RESOLUTION_DEFEATED_AND_KILLED,
    RESOLUTION_DEFEATED_AND_REDEEMED,
    RESOLUTION_DISAPPEARED_UNRESOLVED,
    RESOLUTION_ONGOING,
    RESOLUTION_OUTLIVED,
    RESOLUTION_TRANSFORMED_TO_ALLY,
    RESOLUTION_TRANSFORMED_TO_NEUTRAL,
    render_antagonist_lifecycle_constraints_block,
    scan_antagonist_lifecycle,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _antag(
    name: str,
    *,
    line_role: str = LINE_ROLE_OVERT,
    volumes: list[tuple[int, int]] | None = None,
    resolution: str = RESOLUTION_DEFEATED_AND_KILLED,
    transition_volume: int | None = None,
    transition_mechanism: str = "duel",
) -> dict:
    return {
        "name": name,
        "line_role": line_role,
        "stages_of_relevance": [list(v) for v in (volumes or [(1, 1)])],
        "resolution_type": resolution,
        "transition_volume": transition_volume,
        "transition_mechanism": transition_mechanism,
    }


def _healthy_roster(volume_count: int = 10) -> list[dict]:
    """A roster that passes all gates for a 10-volume book.

    Key design:
      * 4 overt stage bosses (V1-2, V3-5, V6-8, V9-10)
      * 1 undercurrent shadow hand (V2-V9)
      * 1 hidden-line final antagonist (V1-V10, reveal V8)
      * Resolution palette mixes kill + transform + outlived
    """

    return [
        # Overt rotation — distinct names, varied resolutions
        _antag("村长刘老", line_role=LINE_ROLE_OVERT,
               volumes=[(1, 2)], resolution=RESOLUTION_OUTLIVED,
               transition_volume=2, transition_mechanism="主角离开村子"),
        _antag("外门大师兄", line_role=LINE_ROLE_OVERT,
               volumes=[(3, 5)], resolution=RESOLUTION_TRANSFORMED_TO_ALLY,
               transition_volume=5, transition_mechanism="共同对抗外敌"),
        _antag("叛徒长老", line_role=LINE_ROLE_OVERT,
               volumes=[(6, 8)], resolution=RESOLUTION_DEFEATED_AND_KILLED,
               transition_volume=8, transition_mechanism="身份揭穿后决战"),
        _antag("终局魔君", line_role=LINE_ROLE_OVERT,
               volumes=[(9, 10)], resolution=RESOLUTION_DEFEATED_AND_KILLED,
               transition_volume=10, transition_mechanism="最终决战"),
        # Undercurrent
        _antag("影手", line_role=LINE_ROLE_UNDERCURRENT,
               volumes=[(2, 9)], resolution=RESOLUTION_DISAPPEARED_UNRESOLVED,
               transition_volume=9, transition_mechanism="揭穿后隐退"),
        # Hidden reveal
        _antag("原初封印者", line_role=LINE_ROLE_HIDDEN,
               volumes=[(1, 10)], resolution=RESOLUTION_DEFEATED_AND_REDEEMED,
               transition_volume=10, transition_mechanism="血脉共鸣"),
    ]


# ---------------------------------------------------------------------------
# Canonical lists
# ---------------------------------------------------------------------------

def test_canonical_resolutions_non_empty():
    assert RESOLUTION_DEFEATED_AND_KILLED in CANONICAL_RESOLUTIONS
    assert RESOLUTION_TRANSFORMED_TO_ALLY in CANONICAL_RESOLUTIONS
    assert RESOLUTION_DISAPPEARED_UNRESOLVED in CANONICAL_RESOLUTIONS
    assert RESOLUTION_OUTLIVED in CANONICAL_RESOLUTIONS
    assert RESOLUTION_ONGOING in CANONICAL_RESOLUTIONS


def test_canonical_line_roles():
    assert set(CANONICAL_LINE_ROLES) == {
        LINE_ROLE_OVERT, LINE_ROLE_UNDERCURRENT, LINE_ROLE_HIDDEN,
    }


# ---------------------------------------------------------------------------
# Healthy roster
# ---------------------------------------------------------------------------

def test_healthy_roster_passes():
    roster = _healthy_roster(10)
    report = scan_antagonist_lifecycle(
        roster, total_chapters=150, volume_count=10
    )
    assert report.antagonist_count == 6
    assert report.critical_count == 0
    assert report.is_critical is False


# ---------------------------------------------------------------------------
# Starved roster
# ---------------------------------------------------------------------------

def test_starved_roster_critical():
    """2 antagonists across 24 volumes is a starved roster."""

    roster = [
        _antag("大反派A", volumes=[(1, 12)],
               resolution=RESOLUTION_DEFEATED_AND_KILLED),
        _antag("大反派B", volumes=[(13, 24)],
               resolution=RESOLUTION_DEFEATED_AND_KILLED),
    ]
    report = scan_antagonist_lifecycle(
        roster, total_chapters=316, volume_count=24
    )
    codes = {f.code for f in report.findings}
    assert "starved_antagonist_roster" in codes


# ---------------------------------------------------------------------------
# Identical-label rotation — canonical 道种破虚 failure
# ---------------------------------------------------------------------------

def test_identical_overt_antagonist_labels_critical():
    """25 volumes all labelled '元婴老者' — exactly the failure mode."""

    roster = [
        _antag(
            "元婴老者",
            line_role=LINE_ROLE_OVERT,
            volumes=[(i, i)],
            resolution=RESOLUTION_DEFEATED_AND_KILLED,
            transition_volume=i,
        )
        for i in range(1, 11)
    ]
    report = scan_antagonist_lifecycle(
        roster, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "identical_overt_antagonist_labels" in codes
    finding = next(
        f for f in report.findings
        if f.code == "identical_overt_antagonist_labels"
    )
    assert finding.severity == "critical"
    assert finding.payload["shared_name"] == "元婴老者"


# ---------------------------------------------------------------------------
# Monotonous resolution types
# ---------------------------------------------------------------------------

def test_monotonous_resolution_types_warning():
    """All antagonists get killed → warning."""

    roster = [
        _antag(f"敌人{i}", line_role=LINE_ROLE_OVERT,
               volumes=[(i, i)],
               resolution=RESOLUTION_DEFEATED_AND_KILLED)
        for i in range(1, 11)
    ]
    report = scan_antagonist_lifecycle(
        roster, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "monotonous_resolution_types" in codes
    assert "all_antagonists_killed_template" in codes
    finding = next(
        f for f in report.findings if f.code == "monotonous_resolution_types"
    )
    assert finding.severity == "warning"


def test_varied_resolution_palette_no_warning():
    """Mix of kills, transforms, disappearances passes the gate."""

    roster = [
        _antag("敌人1", volumes=[(1, 2)],
               resolution=RESOLUTION_DEFEATED_AND_KILLED),
        _antag("敌人2", volumes=[(3, 4)],
               resolution=RESOLUTION_TRANSFORMED_TO_ALLY),
        _antag("敌人3", volumes=[(5, 6)],
               resolution=RESOLUTION_TRANSFORMED_TO_NEUTRAL),
        _antag("敌人4", volumes=[(7, 8)],
               resolution=RESOLUTION_DISAPPEARED_UNRESOLVED),
        _antag("敌人5", volumes=[(9, 10)],
               resolution=RESOLUTION_OUTLIVED),
    ]
    report = scan_antagonist_lifecycle(
        roster, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "monotonous_resolution_types" not in codes
    assert "all_antagonists_killed_template" not in codes


# ---------------------------------------------------------------------------
# Missing lifecycle fields
# ---------------------------------------------------------------------------

def test_missing_fields_critical():
    roster = [
        {"name": "无生命周期的敌人", "volumes": [[1, 2]]},  # missing line_role + resolution
    ]
    report = scan_antagonist_lifecycle(
        roster, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "antagonist_missing_lifecycle_fields" in codes


def test_invalid_line_role_treated_as_missing():
    roster = [_antag("敌人1", line_role="wrong_role",
                     volumes=[(1, 2)])]
    report = scan_antagonist_lifecycle(
        roster, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "antagonist_missing_lifecycle_fields" in codes


# ---------------------------------------------------------------------------
# Per-volume overt coverage
# ---------------------------------------------------------------------------

def test_volume_without_overt_antagonist_critical():
    """If V5-V7 have no active overt antagonist, flag them."""

    roster = [
        _antag("前期敌人", volumes=[(1, 3)]),
        _antag("后期敌人", volumes=[(8, 9)]),
        # V4-V7 have no overt antagonist — but V10 is exempt (final)
        # so we expect V4-V7 flagged.
    ]
    # Also add some non-overt so the other distribution checks pass.
    roster.append(
        _antag(
            "暗线操盘者",
            line_role=LINE_ROLE_UNDERCURRENT,
            volumes=[(2, 9)],
            resolution=RESOLUTION_DISAPPEARED_UNRESOLVED,
        )
    )
    report = scan_antagonist_lifecycle(
        roster, total_chapters=150, volume_count=10
    )
    codes = {f.code for f in report.findings}
    assert "volume_without_overt_antagonist" in codes
    finding = next(
        f for f in report.findings
        if f.code == "volume_without_overt_antagonist"
    )
    for v in (4, 5, 6, 7):
        assert v in finding.payload["volumes"]


def test_final_volume_exempt_from_overt_coverage_rule():
    """The very last volume may have no active overt antagonist
    (after the final boss is resolved)."""

    roster = [
        _antag("V1敌人", volumes=[(1, 1)]),
        _antag("V2敌人", volumes=[(2, 2)]),
        _antag("V3敌人", volumes=[(3, 3)]),
        _antag(
            "暗线",
            line_role=LINE_ROLE_UNDERCURRENT,
            volumes=[(1, 4)],
            resolution=RESOLUTION_DISAPPEARED_UNRESOLVED,
        ),
    ]
    # V4 is the final volume → exempt
    report = scan_antagonist_lifecycle(
        roster, total_chapters=60, volume_count=4
    )
    codes = {f.code for f in report.findings}
    # No "volume_without_overt_antagonist" should fire for V4.
    finding = next(
        (f for f in report.findings
         if f.code == "volume_without_overt_antagonist"),
        None,
    )
    assert finding is None or 4 not in finding.payload["volumes"]


# ---------------------------------------------------------------------------
# Stages parsing — accepts various shapes
# ---------------------------------------------------------------------------

def test_stages_as_list_of_volumes_parsed():
    """stages_of_relevance may be a flat list of volume numbers."""

    roster = [{
        "name": "扁平敌人",
        "line_role": LINE_ROLE_OVERT,
        "stages_of_relevance": [1, 2, 3, 5, 6, 7],
        "resolution_type": RESOLUTION_DEFEATED_AND_KILLED,
    }]
    report = scan_antagonist_lifecycle(
        roster, total_chapters=150, volume_count=10
    )
    summary = report.antagonist_summaries[0]
    # Spans (1,3) and (5,7) should collapse into a volume_span of (1,7)
    assert summary.volume_span == (1, 7)


def test_stages_as_dict_objects_parsed():
    """stages_of_relevance may be a list of dicts."""

    roster = [{
        "name": "字典结构敌人",
        "line_role": LINE_ROLE_OVERT,
        "stages_of_relevance": [
            {"start_volume": 1, "end_volume": 3},
            {"start": 7, "end": 9},
        ],
        "resolution_type": RESOLUTION_DEFEATED_AND_KILLED,
    }]
    report = scan_antagonist_lifecycle(
        roster, total_chapters=150, volume_count=10
    )
    summary = report.antagonist_summaries[0]
    assert summary.volume_span == (1, 9)


# ---------------------------------------------------------------------------
# Envelope form
# ---------------------------------------------------------------------------

def test_accepts_envelope_with_antagonists_key():
    envelope = {"antagonists": _healthy_roster(10)}
    report = scan_antagonist_lifecycle(
        envelope, total_chapters=150, volume_count=10
    )
    assert report.antagonist_count == 6
    assert report.critical_count == 0


def test_accepts_pydantic_like_entries():
    class _Fake:
        def model_dump(self):
            return {"antagonists": _healthy_roster(10)}

    report = scan_antagonist_lifecycle(
        _Fake(), total_chapters=150, volume_count=10
    )
    assert report.antagonist_count == 6


# ---------------------------------------------------------------------------
# Empty roster
# ---------------------------------------------------------------------------

def test_empty_roster_yields_starvation():
    report = scan_antagonist_lifecycle(
        [], total_chapters=150, volume_count=10
    )
    assert report.antagonist_count == 0
    codes = {f.code for f in report.findings}
    assert "starved_antagonist_roster" in codes
    assert report.is_critical


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def test_report_to_prompt_block_empty_when_healthy():
    roster = _healthy_roster(10)
    report = scan_antagonist_lifecycle(
        roster, total_chapters=150, volume_count=10
    )
    assert report.to_prompt_block() == ""


def test_report_to_prompt_block_lists_findings():
    report = scan_antagonist_lifecycle(
        [], total_chapters=150, volume_count=10
    )
    block = report.to_prompt_block(language="zh-CN")
    assert block
    assert "生命周期" in block or "lifecycle" in block.lower()


def test_render_constraints_block_zh():
    block = render_antagonist_lifecycle_constraints_block(
        total_chapters=316, volume_count=24, language="zh-CN"
    )
    assert "316" in block
    assert "24" in block
    assert "line_role" in block
    assert "resolution_type" in block
    # Canonical resolutions must be listed
    assert RESOLUTION_TRANSFORMED_TO_ALLY in block
    assert RESOLUTION_DEFEATED_AND_KILLED in block


def test_render_constraints_block_en():
    block = render_antagonist_lifecycle_constraints_block(
        total_chapters=544, volume_count=16, language="en-US"
    )
    assert "544" in block
    assert "16" in block
    assert "line_role" in block.lower()
    assert "resolution_type" in block.lower()
    assert "hard constraints" in block.lower()
