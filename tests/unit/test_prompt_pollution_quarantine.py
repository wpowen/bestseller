"""Cross-book quarantine: another book's text may never enter this book's prompts.

Scope note (2026-08-02): this file used to also assert the *motif police* — the
detectors that vetoed a book for containing debt or death vocabulary, and the
minimal-cost filter that rejected 反噬 / 欠人情 / 闭关养伤. Those were retired:
they censored ordinary story material, contradicted the framework's own
cost mandates, and killed two real books in the foundation and outline stages.
What remains here is the part that was always right — no other book's concrete
content, and no rejected draft, may become material for this book.
"""

from __future__ import annotations

import inspect

from bestseller.services import concept_tournament, conception
from bestseller.services.anti_default_motif import (
    contains_debt_motif,
    contains_default_death_motif,
    contains_minimal_cost_violation,
    planner_anti_default_block,
)

POLLUTED_TITLE = "尸账旧簿"
POLLUTED_PREMISE = "主角替旧书亡者清算前世欠账"
POLLUTED_MECHANISM = "每翻一页账簿就唤醒一具旧书尸体"


# ── The retirement itself ────────────────────────────────────────────────────


def test_motif_detectors_are_retired() -> None:
    """Death and debt are story material. No detector may veto them again."""
    for text in (
        "债主拿着欠条来讨债",
        "矿洞深处埋着一具枯骨",
        "力量使用后会反噬并短期失声",
        "晋升后欠长老一个人情",
        "突破后三个月无法调动灵气，需要休息养伤",
        "以延寿作为境界阶梯",
    ):
        assert not contains_debt_motif(text), text
        assert not contains_default_death_motif(text), text
        assert not contains_minimal_cost_violation(text), text


def test_guardrail_blocks_render_nothing() -> None:
    """A guard rendered into a prompt is itself an injection."""
    assert planner_anti_default_block({}, is_en=False) == ""
    assert planner_anti_default_block({}, is_en=True) == ""


def test_candidate_gate_keeps_only_the_users_own_switches() -> None:
    """A concept may contain a corpse or a debt; it may not contradict the form.

    The tone switch is the user's own choice, so a 轻松 book whose premise is
    saturated with bleak imagery is still rejected — that is executing the
    selection, not imposing framework taste.
    """
    corpse_candidate = concept_tournament.ConceptCandidate(
        dimension="系统自动构思",
        concept="守墓人从两具尸体上发现相同线索",
        opening_crisis="第三具尸体在封锁后出现",
    )
    debt_candidate = concept_tournament.ConceptCandidate(
        dimension="系统自动构思",
        concept="讨债人用账本追查一笔欠账",
        mechanism="每次追索都会让债务关系发生可见变化",
    )

    # No user seed authorising either theme — both are now accepted.
    assert (
        concept_tournament._candidate_hard_rejection_reason(
            debt_candidate,
            seed_concept="药童修复一座失控药圃",
            tone_preference="",
            effect_skills=(),
        )
        is None
    )
    assert (
        concept_tournament._candidate_hard_rejection_reason(
            corpse_candidate,
            seed_concept="药童修复一座失控药圃",
            tone_preference="",
            effect_skills=(),
        )
        is None
    )

    # An explicit 轻松 pick still binds.
    assert concept_tournament._candidate_hard_rejection_reason(
        corpse_candidate,
        seed_concept="药童修复一座失控药圃",
        tone_preference="light",
        effect_skills=(),
    )


def test_minimal_cost_no_longer_rejects_genre_native_cost_language() -> None:
    """纯爽 is pacing. It never made 反噬/灼脉 an illegal word."""
    for text in ("雷意灼脉，施术后经脉灼伤", "力量反噬并让主角短期失声"):
        assert (
            concept_tournament._creation_intent_content_violations(
                text, cost_style="minimal"
            )
            == ()
        )


# ── Cross-book quarantine (unchanged, still enforced) ────────────────────────


def test_old_book_and_cliche_samples_never_enter_generation_prompts() -> None:
    banned = (POLLUTED_TITLE, POLLUTED_PREMISE)
    old_books = [
        {
            "title": POLLUTED_TITLE,
            "premise": POLLUTED_PREMISE,
            "golden_finger": POLLUTED_MECHANISM,
        }
    ]

    _system, kernel_prompt = concept_tournament._build_engine_kernel_messages(
        genre="仙侠",
        sub_genre="古典仙侠",
        lane="纯题材直觉",
        chapter_count=100,
        banned=banned,
        seed_concept="活着的药童修复一座失控药圃",
    )
    _system, hook_prompt = concept_tournament._build_hook_from_engine_messages(
        genre="仙侠",
        sub_genre="古典仙侠",
        kernel={"protagonist_identity": "药童", "opening_crisis": "药圃失控"},
        banned=banned,
    )
    tournament_dedup = concept_tournament._render_avoid_mechanisms_block(old_books)
    conception_dedup = conception._mechanism_dedup_prompt_block(
        {"avoid_mechanisms": old_books},
        is_en=False,
    )

    combined = "\n".join(
        (kernel_prompt, hook_prompt, tournament_dedup, conception_dedup)
    )
    for polluted_text in (POLLUTED_TITLE, POLLUTED_PREMISE, POLLUTED_MECHANISM):
        assert polluted_text not in combined
    for motif_token in ("尸体", "账本", "欠条"):
        assert motif_token not in combined

    assert "具体禁用文本不进入本轮提示词" in combined
    assert "旧书标题、前提、金手指和意象原文均不进入提示词" in combined


def test_pollution_retry_is_clean_room_not_a_replay_of_intermediate_drafts() -> None:
    prompt = conception._pollution_retry_finalize_prompt(
        {
            "genre": "仙侠",
            "sub_genre": "古典仙侠",
            "chapter_count": 100,
            "language": "zh-CN",
            "user_hints": {"concept_seed": "活着的药童修复一座失控药圃"},
            "high_concept": {
                "concept": "药童必须在开山大典前修好会迁移的灵脉断点",
                "mechanism": "他能听出阵纹中下一处断点",
            },
            "commercial_brief": {"reader_promise": POLLUTED_PREMISE},
            "avoid_mechanisms": [
                {"title": POLLUTED_TITLE, "golden_finger": POLLUTED_MECHANISM}
            ],
            "creative_premise_seed": POLLUTED_PREMISE,
            "creative_hook": POLLUTED_MECHANISM,
        },
        genre_profile=None,
        is_en=False,
    )

    assert "活着的药童修复一座失控药圃" in prompt
    assert "药童必须在开山大典前修好会迁移的灵脉断点" in prompt
    assert POLLUTED_TITLE not in prompt
    assert POLLUTED_PREMISE not in prompt
    assert POLLUTED_MECHANISM not in prompt
    assert "不继承任何被拒中间稿" in prompt


def test_pollution_retry_keeps_automatic_seed_when_no_seed_or_champion() -> None:
    prompt = conception._pollution_retry_finalize_prompt(
        {
            "genre": "仙侠",
            "sub_genre": "古典仙侠",
            "chapter_count": 100,
            "language": "zh-CN",
            "automatic_story_seed": "守山杂役误触残碑后，能听见护山阵法正在求救。",
            "user_hints": {},
            "high_concept": {},
        },
        genre_profile=None,
        is_en=False,
    )

    assert "守山杂役误触残碑后，能听见护山阵法正在求救" in prompt
    assert "已过筛冠军：{}" in prompt
    assert prompt.index("守山杂役误触残碑后") < prompt.index("【隔离后的权威故事事实】")


def test_retry_adoption_is_fail_closed_for_any_retry_pollution_class() -> None:
    assert not conception._should_adopt_mechanism_retry(
        retry_result={"premise": "retry"},
        original_echo=[],
        retry_echo=[],
        original_hard=(True, False, False, False),
        retry_hard=(False, True, False, False),
    )


def test_pollution_retry_exception_cannot_promote_original_finalize() -> None:
    source = inspect.getsource(conception.run_conception_pipeline)
    gate = source[source.index("_unresolved_concept_guard") : source.index(
        "# A frontend field is not", source.index("_unresolved_concept_guard")
    )]

    assert "_detected_concept_guard = tuple(dict.fromkeys(detected))" in gate
    assert "_unresolved_concept_guard = _detected_concept_guard" in gate
    assert "keeping original finalize" not in gate


def test_ontology_retry_withholds_the_detected_terms() -> None:
    block = conception._render_ontology_drift_rewrite_feedback(
        ("停尸房", "法医"),
        is_en=False,
    )
    assert "停尸房" not in block
    assert "法医" not in block
    assert "具体词不展示" in block


def test_story_architect_withholds_peer_settings_from_the_prompt() -> None:
    from bestseller.domain.facets import StoryFacets
    from bestseller.services import story_architect

    polluted_peer = StoryFacets(
        primary_genre="xianxia",
        setting=POLLUTED_PREMISE,
        trope_tags=("尸账", "旧簿"),
    )
    prompt = story_architect._build_user_prompt(
        primary_genre="xianxia",
        language="zh-CN",
        user_hints=None,
        existing_facets=[polluted_peer],
    )

    assert POLLUTED_PREMISE not in prompt
    assert "尸账" not in prompt
    assert "Same-Genre Peers" in prompt
    assert "similarity gate" in prompt
