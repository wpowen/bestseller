"""F0-14 renderer component only; F0-13's guarded extraction entry is NOT covered.

No admission, promoted-state, grade or provider collaborator is replaced here.
The full extraction/query integration still needs Main's native fixture.
"""

from types import SimpleNamespace

import pytest

from bestseller.infra.db import models as m
from bestseller.services.feedback import _build_feedback_extraction_prompts

from .f0_ordering_support import uid

pytestmark = pytest.mark.unit


def _arc():
    return m.PlotArcModel(id=uid(500), project_id=uid(900), arc_code="main-arc",
                          name="Main arc", arc_type="main", status="active")


def _prompt(arc, beats):
    return _build_feedback_extraction_prompts(
        chapter=SimpleNamespace(chapter_number=2, title="A present decision"),
        chapter_md="The observer makes a decision.", characters=[], active_arcs=[arc],
        arc_beats_by_arc={arc.id: beats}, unresolved_clues=[], active_antagonist_plans=[],
        language="en")[1]


def test_f0_14_equal_beat_order_across_distinct_scopes_renders_stably():
    arc = _arc()
    scopes = [("project", None, None, None), ("volume", 1, None, None),
              ("chapter", 1, 2, None), ("scene", 1, 2, 1)]
    beats = [m.ArcBeatModel(
        id=uid(i), project_id=arc.project_id, plot_arc_id=arc.id, beat_order=1,
        scope_level=level, scope_volume_number=volume, scope_chapter_number=chapter,
        scope_scene_number=scene, beat_kind="turn", summary=f"scope-marker-{i}",
        status="planned", metadata_json={})
        for i, (level, volume, chapter, scene) in enumerate(scopes, 1)]
    forward = _prompt(arc, beats)
    reverse = _prompt(arc, list(reversed(beats)))
    assert forward == reverse
    positions = [forward.index(f"scope-marker-{i}") for i in range(1, 5)]
    assert positions == sorted(positions)
    assert len([line for line in forward.splitlines() if "scope-marker-" in line]) == 4


def test_f0_14_beat_order_remains_primary_to_persistent_uuid():
    arc = _arc()
    beats = [m.ArcBeatModel(
        id=uid(i), project_id=arc.project_id, plot_arc_id=arc.id, beat_order=order,
        scope_level="chapter", scope_volume_number=1, scope_chapter_number=2,
        scope_scene_number=None, beat_kind="turn", summary=marker,
        status="planned", metadata_json={})
        for i, order, marker in [(1, 3, "last-marker"), (2, 2, "middle-marker"),
                                 (90, 1, "first-marker")]]
    for arrival in (beats, list(reversed(beats))):
        prompt = _prompt(arc, arrival)
        assert prompt.index("first-marker") < prompt.index("middle-marker") < prompt.index("last-marker")
