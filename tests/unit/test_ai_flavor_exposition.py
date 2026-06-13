"""AI-flavor detection: narrator rule-exposition + parallel gloss (POV blind spot).

The user's core AI-flavor complaint was "平白叙述, 解释设定/规则, 而不是站在角色
角度写体验". The existing detector families (solo_short/micro_action/not-x-but-y)
caught surface cadence but had NO probe for the narrator stepping out to explain
rules/terms/clauses ("第三十七条"/"凡研者入堂须研墨"/"X司点人用") or parallel
gloss ("验的是墨, 验的是推演"). deslop_revise stops when the detector is clean,
so these slipped through. These two discourse rules close that blind spot.
"""

from __future__ import annotations

from bestseller.services.ai_flavor.detector import detect


def _cats(text: str) -> set[str]:
    return {s.category for s in detect(text, language="zh-CN").spans}


def test_rule_clause_exposition_flagged() -> None:
    samples = [
        "门口便传来一记铜磬。开卷磬，研堂议事司点人才用。",
        "砚台未离身者不得入堂议事——堂规白纸黑字写在第三十七条上。",
        "凡研者入堂，须当场研墨一推，以验墨性真伪。",
        "磬响三息，堂中诸人必须放下手中活计，等候差遣。",
    ]
    for s in samples:
        assert "info_narration" in _cats(s), f"missed rule exposition in: {s}"


def test_parallel_gloss_flagged() -> None:
    assert "info_narration" in _cats("谢迟听见了后半截，验的是墨，验的是推演。")


def test_needs_deslop_revise_triggers_on_low_score_exposition(tmp_path) -> None:
    """The closing-the-loop guarantee: rule-exposition scores below the block
    threshold (advisory) yet must still route to the whole-passage deslop
    rewrite, because the span patcher cannot fix discourse-level tells."""
    from bestseller.services.ai_flavor_gate import (
        AiFlavorGateConfig,
        needs_deslop_revise,
        run_ai_flavor_gate,
    )

    expo = (
        "谢迟站在堂中。开卷磬，研堂议事司点人才用。"
        "砚台未离身者不得入堂议事——堂规白纸黑字写在第三十七条上。他没动。"
    )
    outcome = run_ai_flavor_gate(
        chapter_number=1,
        content_md=expo,
        language="zh-CN",
        config=AiFlavorGateConfig(),
        project_output_dir=tmp_path,
    )
    assert outcome.decision != "block"  # score is low / advisory
    assert needs_deslop_revise(outcome) is True

    clean = "袖口里那方砚台压着陶盆底，砖缝里渗上来的冷气顺着腕骨往上爬。他伸手进袖，指节抵着砚沿。"
    clean_outcome = run_ai_flavor_gate(
        chapter_number=1,
        content_md=clean,
        language="zh-CN",
        config=AiFlavorGateConfig(),
        project_output_dir=tmp_path,
    )
    assert needs_deslop_revise(clean_outcome) is False


def test_clean_prose_not_flagged() -> None:
    clean = [
        "袖口里那方砚台压着三天苗的陶盆底，砖缝里渗上来的冷气顺着腕骨往上爬。",
        "墨锭擦过砚面，那一下没出声。台下听不见。",
        "三天苗叶尖抖了一下。他没停。",
        "他活了三年，砚商赔过他一方砚。",
        "她想要的东西，他必须给。",
        "凡事都有第一次。",
        "殷泱的量尺往前递的那一寸，停住了。",
    ]
    for s in clean:
        cats = _cats(s)
        assert "info_narration" not in cats, f"false positive rule_exposition: {s}"
        assert "info_narration" not in cats, f"false positive parallel_gloss: {s}"
