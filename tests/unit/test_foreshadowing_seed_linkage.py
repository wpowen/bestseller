"""G3 (xianxia benchmark): foreshadowing seed-id linkage in narrative graph.

Production failure (shilouyan-bench-v1): all 3 payoffs failed to link to any
clue (source_clue 0/3) and all 18 clues stayed status=planted (0/18), because
``_build_clues_and_payoffs`` related payoff→clue via ``_match_clue_code`` —
fragile text-substring matching. The LLM phrases a plant ("蚀漏砚缺口处那一道
细纹——研墨时与指尖血共振后隐有温热") and its payoff ("蚀漏砚缺口处那一道细纹
（确认与卫荆摊位青灰砚石同源）") differently, so the substring match misses and
the foreshadow loop never closes.

Fix: plants carry an inline seed tag ``[S<n>]``; the matching payoff references
the same tag. Materialization resolves the source clue by seed tag (exact),
falling back to text matching only when no tag is present (backward compatible).
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from bestseller.services.narrative import (
    _build_clues_and_payoffs,
    _extract_seed_tag,
)


def test_extract_seed_tag_parses_and_strips() -> None:
    tag, text = _extract_seed_tag("[S1] 蚀漏砚缺口处那一道细纹")
    assert tag == "S1"
    assert text == "蚀漏砚缺口处那一道细纹"


def test_extract_seed_tag_none_when_absent() -> None:
    tag, text = _extract_seed_tag("没有标签的伏笔文本")
    assert tag is None
    assert text == "没有标签的伏笔文本"


def _volume(number: int):
    return SimpleNamespace(id=uuid4(), volume_number=number, project_id=uuid4())


def _chapter(volume_id, number: int, hook: str | None = None):
    # hook defaults to None so an empty foreshadowing_planted list does not
    # trigger the hook_description fallback clue (which would add noise to the
    # exact-linkage assertions).
    return SimpleNamespace(
        id=uuid4(),
        volume_id=volume_id,
        chapter_number=number,
        hook_description=hook,
    )


def _entry(number: int, planted: list[str], paid: list[str]):
    return SimpleNamespace(
        volume_number=number,
        foreshadowing_planted=planted,
        foreshadowing_paid_off=paid,
    )


def test_seed_linked_payoff_resolves_clue_across_volumes() -> None:
    """A payoff in volume 2 links to its plant in volume 1 by seed tag even
    when the two texts share no common substring."""
    v1, v2 = _volume(1), _volume(2)
    c1, c2 = _chapter(v1.id, 1), _chapter(v2.id, 2)
    chapters = [c1, c2]
    scenes_by_chapter = {
        c1.id: [SimpleNamespace(scene_number=1)],
        c2.id: [SimpleNamespace(scene_number=1)],
    }
    volume_entries = {
        1: _entry(1, planted=["[S1] 砚台缺口的细纹"], paid=[]),
        2: _entry(2, planted=[], paid=["[S1] 青灰砚石同源得到确认"]),
    }
    arc_ids = {"mystery_arc": uuid4()}

    clue_specs, payoff_specs = _build_clues_and_payoffs(
        arc_ids=arc_ids,
        volumes=[v1, v2],
        chapters=chapters,
        scenes_by_chapter=scenes_by_chapter,
        volume_entries=volume_entries,
    )

    assert len(clue_specs) == 1
    assert len(payoff_specs) == 1
    clue_code = clue_specs[0]["clue_code"]
    assert payoff_specs[0]["source_clue_code"] == clue_code
    # The inline tag must not leak into the stored label.
    assert "[S1]" not in clue_specs[0]["label"]
    assert "[S1]" not in payoff_specs[0]["label"]


def test_unlabeled_payoff_falls_back_to_text_match() -> None:
    """Backward compatibility: without seed tags, text matching still links."""
    v1 = _volume(1)
    c1 = _chapter(v1.id, 1)
    scenes_by_chapter = {c1.id: [SimpleNamespace(scene_number=1)]}
    volume_entries = {
        1: _entry(
            1,
            planted=["青灰砚石的来历之谜"],
            paid=["青灰砚石的来历之谜终于揭开"],
        ),
    }
    clue_specs, payoff_specs = _build_clues_and_payoffs(
        arc_ids={"mystery_arc": uuid4()},
        volumes=[v1],
        chapters=[c1],
        scenes_by_chapter=scenes_by_chapter,
        volume_entries=volume_entries,
    )
    assert payoff_specs[0]["source_clue_code"] == clue_specs[0]["clue_code"]
