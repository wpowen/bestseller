"""Calibration: choppy_rhythm must distinguish mechanical staccato from
skilled emotional restraint.

Empirical finding (real MiniMax-M3 stress generation, 2026-06-13): the
``mean_hit`` branch (paragraph mean sentence length ≤ 11) false-positives on
high-craft restraint — a 3-sentence paragraph of short, *varied-subject,
concrete* sentences ("他把妹妹往上托了托。她脑袋从臂弯里滑出去一点。他用下巴
抵住。") reads well but tripped the rule. Mechanical staccato instead hammers
the *same* leading subject with little new content ("他没接话。他只塞。他没带
笔杆。"). The fix gates ``mean_hit`` behind a mechanical-subject-repetition
co-signal; the ultra-short run signal still fires unconditionally.
"""

from __future__ import annotations

from bestseller.services.ai_flavor.detector import detect


def _cats(text: str) -> list[str]:
    return [s.category for s in detect(text, language="zh").spans]


def test_varied_subject_restraint_not_flagged() -> None:
    # Good craft: short but varied subjects + distinct concrete actions.
    text = "他把妹妹往上托了托。她脑袋从臂弯里滑出去一点。他用下巴抵住。"
    assert "choppy_rhythm" not in _cats(text)


def test_mechanical_same_subject_staccato_flagged() -> None:
    # Mechanical: same leading subject hammered, minimal new content.
    text = "他没接话。他只塞。他没带笔杆。他只带了砚。他没退。"
    assert "choppy_rhythm" in _cats(text)


def test_ultrashort_run_still_flagged_regardless_of_subject() -> None:
    # A long run of ultra-short fragments is staccato even with varied subjects.
    text = "门开了。风进来。灯灭了。她笑了。他退了。刀亮了。"
    assert "choppy_rhythm" in _cats(text)


def test_normal_prose_clean() -> None:
    text = (
        "焦糊味裹着湿灰从地窖口灌进来，谢迟把妹妹往墙角又挪了挪，"
        "才敢回头看那束手电光，卫荆就站在光里，灰绳束腰。"
    )
    assert "choppy_rhythm" not in _cats(text)
