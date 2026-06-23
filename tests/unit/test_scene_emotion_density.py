"""L1 tests for the embodied-emotion / anomaly-tension DENSITY scorer.

Root-cause regression guard: the scene emotion/hook scores were ratio-matches
against genre-blind checklists (capped at 10 terms), so prose carrying emotion +
tension through BODY LANGUAGE and IMAGERY — i.e. good show-don't-tell prose —
scored ~0.1 and churned forever (every chapter to V4+, 0/50 complete). The
density scorer rewards richness of universal embodied markers instead, so strong
prose in ANY genre passes while flat telling-prose still fails.
"""

# ruff: noqa: RUF001 — Chinese prose fixtures are the test subject.
from __future__ import annotations

from bestseller.services.reviews import (
    _EMBODIED_EMOTION_TERMS,
    _TENSION_HOOK_TERMS,
    _density_score,
)

# A real-style ritual-xuanhuan scene tail: emotion via body language, tension via
# imagery/anomaly — NO emotion-label words, NO modern-suspense props.
_STRONG_PROSE = (
    "三根新香齐齐往左拧，烟气不往上走。祀渊后颈的汗毛全部立起来，"
    "右手在袖口里攥紧，指节碰到袖布。他数到第十三下心跳，喉咙发紧，"
    "瞳孔却一点点收缩。镜中那只活瞳忽然转向他，纸页边缘渗出一种更深的灰。"
)

# Flat telling-prose: states feelings, no body language, no anomaly/imagery.
_FLAT_PROSE = (
    "他走进房间，心里有些不高兴。她告诉他事情已经处理完了，他点了点头，"
    "觉得还算满意。两人聊了关于工作的事，最后他决定明天再说，于是回家了。"
)


def test_density_empty_is_zero():
    assert _density_score("", _EMBODIED_EMOTION_TERMS) == 0.0


def test_density_saturates_at_target():
    # 4 distinct embodied markers → full signal (target default 4).
    text = "他攥紧拳头，心跳加快，喉咙发紧，后颈冒汗。"
    assert _density_score(text, _EMBODIED_EMOTION_TERMS, target=4) == 1.0


def test_density_partial_below_target():
    text = "他攥紧了手。"  # one marker
    s = _density_score(text, _EMBODIED_EMOTION_TERMS, target=4)
    assert 0.0 < s < 1.0


def test_strong_show_dont_tell_prose_scores_high_emotion():
    assert _density_score(_STRONG_PROSE, _EMBODIED_EMOTION_TERMS, target=4) == 1.0


def test_strong_prose_scores_high_tension():
    assert _density_score(_STRONG_PROSE[-200:], _TENSION_HOOK_TERMS, target=3) >= 0.66


def test_flat_telling_prose_stays_low_both():
    """The gate must NOT be watered down — flat prose stays below the bar."""
    assert _density_score(_FLAT_PROSE, _EMBODIED_EMOTION_TERMS, target=4) < 0.5
    assert _density_score(_FLAT_PROSE, _TENSION_HOOK_TERMS, target=3) < 0.5


def test_genre_neutral_modern_prose_also_recognised():
    """Embodied markers are universal — a modern thriller scene scores too."""
    modern = "她盯着手机屏幕，指尖发凉，心跳骤然加快，喉咙一阵发紧，后背全是冷汗。"
    assert _density_score(modern, _EMBODIED_EMOTION_TERMS, target=4) == 1.0
