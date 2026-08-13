"""One token owning every design axis — the 2026-08-09 《灵根废我用烂账翻盘》 defect.

The live book is the fixture. Its approved champion mentioned 账本 twice, in one
clause about a senior sister docking wages; the finalize call then made it the
golden finger, the romance mode, the power system, the chapter-end hook AND a
per-chapter law, and 17 occurrences of 账 rode into every chapter prompt inside
the writing-profile block.

The two things these tests hold down:
  1. it fires on that profile, and
  2. it goes silent the moment the approved source actually leans on the term —
     because that is the exact failure that retired the previous debt police
     (a book about a debt died for writing its own premise).
"""

from __future__ import annotations

import pytest

from bestseller.services.motif_concentration import (
    detect_amplified_motifs,
    detect_profile_motif_amplification,
    load_common_chars,
    render_motif_amplification_feedback,
    writing_profile_axis_texts,
)

pytestmark = pytest.mark.unit


# Verbatim from the live book (project custom-xuanhuan-1786199429).
LEDGER_PROFILE: dict[str, dict[str, object]] = {
    "market": {
        "reader_promise": "看一个嘴碎废柴靠一桶浑汤在剑宗最底层往上拱。",
        "selling_points": ["每三天一次窗口", "现编瞎话过关"],
        "trope_keywords": ["废柴逆袭", "把柄流"],
        "hook_keywords": ["每三天一桶水", "嘴碎废柴", "剑宗最底层", "井口封印", "账本把柄"],
        "opening_strategy": "第一夜偷井水，被记名师姐当场撞见。",
        "chapter_hook_strategy": (
            "每章末尾必卡一个下一轮窗口的钩子——井口封印被撬、账本上多出一笔、"
            "下一个盯上这桶水的人露脸、半张丹方被人摸走。"
        ),
    },
    "character": {
        "protagonist_archetype": "话痨废柴杂役",
        "protagonist_core_drive": (
            "在剑宗最底层活下来，并且活成第一个让白芍账本上每笔烂账都有人抢着替他销的废柴"
        ),
        "golden_finger": (
            "账本嗅觉（软金手指）：他没有血脉、没有面板，只有一双记得住每笔烂账的贼眼和"
            "一张死人都能气活的嘴——他能从任何一笔交易里嗅出谁欠谁、欠多少、利息几何，"
            "每三天靠现编瞎话把别人丢掉的边角料换成自己活下去的筹码。"
            "所有收益仍需经宗门账本或坊市账本走一遍。"
        ),
        "growth_curve": "升级速度快但每升一级就套上新的把柄。",
        "romance_mode": "slow-burn（白芍线，前期是互相记账、互相捏把柄的别扭关系）",
        "relationship_tension": (
            "一笔笔账本条目下压着的暧昧与利用。白芍替他销账，他替白芍搞到她算不到的"
            "边角料——谁先认账谁就输。"
        ),
        "antagonist_mode": "执事赵叔每三天巡一次井口，手里攥着把柄。",
    },
    "world": {
        "worldbuilding_density": "中等，靠现场带出",
        "info_reveal_strategy": (
            "上层宗门规矩、外门人事、坊市交易规则由白芍账本与主角嘴碎串场逐步带出。"
        ),
        "rule_hardness": "硬规则，违者逐出外门",
        "power_system_style": (
            "炼气—筑基—金丹五阶硬天花板，修炼绑定灵米品级、丹药残渣、青果灵液等边角料路径；"
            "每次小突破都伴随着新把柄入账。"
        ),
        "setting_tags": ["剑宗外门", "杂役院", "哑井", "账本把柄"],
    },
    "serialization": {
        "opening_mandate": (
            "第一章必须把歪丹换灵米、青果封嘴、井口封印被撬压进同一夜；"
            "主角必须展现出'话痨+账本嗅觉'的软金手指雏形。"
        ),
        "first_three_chapter_goal": "让读者看见他怎么在白芍和师弟之间现编瞎话。",
        "scene_drive_rule": (
            "每章至少包含一场现编瞎话的对话戏+一次把柄生成/回收事件，"
            "每3章必须完成一次完整的'窗口开启→威胁出现→瞎话过关→新把柄入账'循环。"
        ),
        "chapter_ending_rule": (
            "每章末尾必卡一个钩子：每章小钩、每3章窗口大钩（井口封印/账本条目/新人物露脸）、"
            "每10-15章卷级悬念钩。"
        ),
    },
}

# The approved champion, verbatim. 账本 appears once here — incidental, in-world,
# and nothing like the takeover the profile above performed.
APPROVED_CHAMPION = (
    "沈潮生，苍雾剑宗外门杂役院杂役，浇药园的活计他干了三年，丹田常年空转。"
    "园子尽头那口被封死的哑井，三天出一桶浑汤，能把废丹渣炼成下品回元丹。"
    "今夜子时就是这一轮井水出水的时间窗口，错过这一桶，他月底杂役工钱彻底清零。"
    "记名师姐白芍每天清点药园挂果，差一颗就扣一文，她不是恶人，"
    "但她的账本就是沈潮生的命门；执事赵叔每三天巡一次井口，只查泥封有没有被人动过。"
)


def test_fires_on_the_live_ledger_profile() -> None:
    motifs = detect_profile_motif_amplification(
        LEDGER_PROFILE, source_text=APPROVED_CHAMPION
    )
    labels = {motif.label for motif in motifs}
    assert "账本" in labels, motifs
    for motif in motifs:
        # The whole claim is "every design axis", not "several".
        assert set(motif.axes) == {"hook", "edge", "relation", "world", "serial"}
        assert motif.lift >= 2.5
        assert motif.count >= 6


def test_silent_when_the_approved_concept_is_genuinely_about_the_ledger() -> None:
    """The failure mode that retired the previous police must not come back.

    Same profile, but now the approved concept is a ledger story. A book must
    never be punished for writing the premise it was approved to write.
    """

    ledger_source = APPROVED_CHAMPION + (
        "这是一个账本的故事：主角靠一本烂账在宗门里翻身，账本记着每一笔欠账，"
        "他替人销账、逼人认账，全书围绕这本账本展开，账本就是他的命。"
    )
    labels = {
        motif.label
        for motif in detect_profile_motif_amplification(
            LEDGER_PROFILE, source_text=ledger_source
        )
    }
    assert "账本" not in labels


def test_the_books_own_core_object_is_never_reported() -> None:
    """井 is all over the profile AND all over the approved concept: lift ~1."""

    labels = {
        motif.token
        for motif in detect_profile_motif_amplification(
            LEDGER_PROFILE, source_text=APPROVED_CHAMPION
        )
    }
    assert "井" not in labels


def test_no_vocabulary_anywhere_in_the_module() -> None:
    """This detector must never grow a motif word bank — that was the old defect.

    The stop-list is empirical corpus frequency, and even it is a detector input
    that never reaches a prompt.
    """

    import inspect

    from bestseller.services import motif_concentration

    source = inspect.getsource(motif_concentration)
    body = source.split('"""', 2)[-1]  # skip the module docstring's case history
    for token in ("债", "欠条", "讨账", "借尸还魂", "灭门"):
        assert token not in body, f"motif vocabulary leaked into the detector: {token}"


def test_common_char_stoplist_excludes_motifs_and_includes_function_chars() -> None:
    common = load_common_chars()
    assert common, "empirical baseline must load"
    for motif_char in "账柄债井寿":
        assert motif_char not in common
    for function_char in "的了是在":
        assert function_char in common


def test_short_or_empty_profiles_never_fire() -> None:
    assert detect_profile_motif_amplification({}, source_text=APPROVED_CHAMPION) == ()
    assert detect_profile_motif_amplification(None, source_text="") == ()
    # Fewer than the minimum number of non-empty axes: nothing to "span".
    assert (
        detect_amplified_motifs(
            {"edge": "账本账本账本账本账本账本", "hook": "账本账本账本账本"},
            source_text="",
        )
        == ()
    )


def test_refuses_to_judge_without_enough_approved_material() -> None:
    """No baseline, no verdict.

    Paths that skip the tournament (concept lab, seedless resume) would otherwise
    hand every token an infinite lift and flag the book's strongest element.
    """

    assert detect_profile_motif_amplification(LEDGER_PROFILE, source_text="") == ()
    assert (
        detect_profile_motif_amplification(LEDGER_PROFILE, source_text="剑宗外门杂役")
        == ()
    )


def test_one_motif_is_reported_once_not_once_per_character() -> None:
    """Live 2026-08-09 confirmation run reported 「代价」 twice, as 代 and as 价.

    Both characters of a term qualify independently, so a single takeover was
    counted twice — and ``_should_adopt_mechanism_retry`` compares those counts
    to decide whether a rewrite improved anything.
    """

    axes = {
        name: (
            f"{name}轴上他必须掂量代价，代价压着他走。"
            "牧道石阶显形，外人多看一眼便多长一截编号。"
        )
        for name in ("hook", "edge", "relation", "world", "serial")
    }
    # Must clear the minimum-source floor, or the detector bails early and the
    # assertion below passes vacuously (it did, first time round).
    source = (
        "牧羊少年阿苔连年被测为无灵根的枯骨，同龄人入宗门，他只配替镇上赵富户放羊。"
        "北陆青脊山下的旧牧道年久无人走，石阶塌了半边，镇上没人肯往那头去。"
        "他每天赶着羊群沿溪水绕行，天黑前必须把羊数清楚，少一只就要挨骂。"
        "山上的雾起得快，落得慢，羊群一散就得摸黑找到后半夜才敢回镇。"
        "赵富户的管事嘴上不说，心里早把他当成一个随时能换掉的短工。"
    )
    assert sum(1 for ch in source if "一" <= ch <= "鿿") >= 120
    motifs = detect_amplified_motifs(axes, source_text=source)
    assert motifs, "fixture must actually trip the detector, or this proves nothing"
    terms = [motif.term for motif in motifs]
    assert len(terms) == len(set(terms)), terms
    assert terms.count("代价") == 1


def test_amplification_baseline_excludes_the_judges_commentary() -> None:
    """The champion's judging prose must never become the baseline.

    Live A/B run 1 (2026-08-09) exposed this: the winner dict carries
    ``judge_reason`` / ``rejected_reason`` / ``seriality_judge``, which quote the
    story's own words back at it. Feeding those into the baseline both enlarges
    the denominator and hands it the very term being measured, so the gate
    reported a rewrite clean while the shipped profile still carried a term at
    lift 3.0 against the story facts alone.
    """

    from bestseller.services.conception import _champion_story_text

    champion = {
        "concept": "少年靠一口哑井换命",
        "core_abnormality": "井三天出一桶浑汤",
        "judge_reason": "账本账本账本账本账本这个设定新颖",
        "rejected_reason": "账本用得太多",
        "seriality_judge": {"note": "账本线可以撑五十章"},
    }
    text = _champion_story_text(champion)
    assert "哑井" in text and "浑汤" in text
    assert "账本" not in text
    assert "新颖" not in text
    assert _champion_story_text(None) == ""


def test_axis_split_covers_every_profile_section_that_ships_to_the_writer() -> None:
    axes = writing_profile_axis_texts(LEDGER_PROFILE)
    assert set(axes) == {"hook", "edge", "relation", "world", "serial"}
    assert all(text.strip() for text in axes.values())


def test_feedback_names_the_defect_without_dictating_replacement_content() -> None:
    motifs = detect_profile_motif_amplification(
        LEDGER_PROFILE, source_text=APPROVED_CHAMPION
    )
    feedback = render_motif_amplification_feedback(motifs, is_en=False)
    assert "账本" in feedback  # this book's own word, in this book's own repair
    assert "设计轴" in feedback
    # It must not hand the model a replacement motif to write instead.
    for planted in ("改写成", "请改用", "建议使用"):
        assert planted not in feedback
    assert render_motif_amplification_feedback((), is_en=False) == ""
