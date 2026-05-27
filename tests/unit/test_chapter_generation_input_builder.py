# ruff: noqa: RUF001

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from bestseller.services.chapter_generation_input_builder import (
    build_chapter_generation_input_stamp,
    build_chapter_generation_input_bundle,
)


def test_chapter_generation_input_bundle_preserves_scene_methodology_fields() -> None:
    project = SimpleNamespace(
        id=uuid4(),
        slug="qingnang",
        title="青囊不语问阴阳",
        genre="民俗悬疑",
        sub_genre="镜债",
        language="zh-CN",
    )
    chapter = SimpleNamespace(
        id=uuid4(),
        chapter_number=1,
        title="十五分钟入账",
        chapter_goal="林渊在倒计时内确认王建业认账。",
        opening_situation="电梯血线倒流。",
        main_conflict="林渊必须决定是否开门。",
        hook_type="物件钩",
        hook_description="铜钱通字冒出血点。",
        metadata_json={"causal_contract": {"state_change": "林渊成为第七人。"}},
    )
    scene = SimpleNamespace(
        scene_number=1,
        title="门缝铜钱",
        scene_type="opening_pressure",
        time_label="23:45",
        participants=["林渊", "王建业"],
        purpose={"story": "林渊用铜钱压住门缝。", "emotion": "压迫"},
        entry_state={"reader": "十五分钟倒计时启动。"},
        exit_state={"reader": "门外响起三短一长。"},
        target_word_count=900,
        key_dialogue_beats=["王建业说：我没认。"],
        sensory_anchors={"smell": "腐桂花味"},
        forbidden_actions=["解释源门"],
        metadata_json={
            "methodology_contract": {
                "conflict_stakes": "开门可能替王建业入账。",
                "signature_image": "铜钱血点从通字里冒出。",
                "cut_point": "三短一长敲门声。",
            },
            "signature_image": "铜钱血点从通字里冒出。",
            "cut_point": "三短一长敲门声。",
            "information_control_mode": "先动作后解释。",
        },
    )
    context = SimpleNamespace(
        story_bible={"rules": ["认账"]},
        previous_scene_summaries=[{"summary": "父亲录音提示不要替认。"}],
        recent_timeline_events=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        reader_contract_block="黄金三章：开篇强压力。",
        hype_constraints_block="本章必须交付识破爽点。",
        assigned_hype_type="识破",
        assigned_hype_recipe_key="folk_opening",
        assigned_hype_intensity=0.9,
    )

    bundle = build_chapter_generation_input_bundle(
        project=project,
        chapter=chapter,
        scenes=[scene],
        context_packet=context,
        target_word_count=2200,
    )

    assert bundle.ready is True
    assert bundle.scenes[0]["signature_image"] == "铜钱血点从通字里冒出。"
    assert bundle.scenes[0]["gate_function"].startswith("opening_pull")
    assert bundle.scenes[0]["visible_progress"] == "林渊用铜钱压住门缝。"
    assert bundle.scenes[0]["reader_payoff"] == "铜钱血点从通字里冒出。"
    assert bundle.scenes[0]["ending_hook_payload"] == "三短一长敲门声。"
    assert bundle.scenes[0]["key_dialogue_beats"] == ["王建业说：我没认。"]
    assert bundle.scenes[0]["time_label"] == "23:45"
    assert bundle.scenes[0]["transition_contract"]["forbid_blank_cut"] is True
    assert bundle.scenes[0]["transition_contract"]["forbid_horizontal_rule_separator"] is True
    assert bundle.methodology_blocks["reader_contract_block"] == "黄金三章：开篇强压力。"
    assert bundle.acceptance_contract["schema_version"] == "chapter-acceptance-contract.v1"
    assert bundle.acceptance_contract["front_position_rules"][
        "opening_must_start_with_pressure"
    ] is True
    assert bundle.acceptance_contract["front_position_rules"][
        "ending_must_land_on_completed_scene_frame"
    ] is True
    assert bundle.acceptance_contract["ending_frame_contract"]["required_if_dialogue_hook"]
    assert bundle.acceptance_contract["knowledge_boundary_contract"]["allowed_explainers"] == [
        "林渊"
    ]
    assert "认账" in bundle.acceptance_contract["knowledge_boundary_contract"][
        "specialist_rule_terms"
    ]
    assert bundle.acceptance_contract["object_signal_contract"]["forbidden_shortcut"]
    methodology_app = bundle.acceptance_contract["methodology_application_contract"]
    assert methodology_app["schema_version"] == "methodology-application-contract.v1"
    assert "platform_character_debt_v1" in methodology_app["profile_ids"]
    assert any(
        item["card_id"] == "platform.character_debt_ledger"
        for item in methodology_app["applications"]
    )
    assert any(
        item["label"] == "closing_hook"
        for item in bundle.acceptance_contract["must_deliver"]
    )


def test_chapter_generation_input_bundle_merges_chapter_metadata_methodology_contract() -> None:
    project = SimpleNamespace(
        id=uuid4(),
        slug="qingnang",
        title="青囊不语问阴阳",
        genre="民俗悬疑",
        sub_genre="镜债",
        language="zh-CN",
    )
    decision_protocol = {
        "chosen_action": "林渊先验门缝和空电梯，不让王建业顺口认账。",
        "alternatives_rejected": ["立刻报警等封楼", "直接进空电梯"],
        "why_this_not_that": "子时前只有现场物证能判断边界，报警无法阻止镜子记名。",
        "constraint": "林渊只能看见症状，不能替王建业解释完整规则。",
        "wrong_choice_loss": "王建业被收账，林渊自己的影子也会被记名。",
    }
    chapter = SimpleNamespace(
        id=uuid4(),
        chapter_number=1,
        title="十五分钟入账",
        chapter_goal="林渊在倒计时内确认王建业认账。",
        opening_situation="十七栋空电梯停在三楼。",
        main_conflict="林渊必须决定是否开门。",
        hook_type="物件钩",
        hook_description="缺角铜钱落在门缝血线里。",
        metadata_json={
            "methodology_contract": {
                "visible_action_or_reaction": "林渊压住镜脚，抢下一枚缺角血钱。",
                "conflict_stakes": "误认会让林渊替王建业入账。",
                "decision_protocol": decision_protocol,
                "relationship_debts": [
                    {
                        "debtor": "林渊",
                        "creditor": "王建业",
                        "evidence_or_handle": "林渊抢下缺角血钱却没救下人。",
                        "due_condition": "王建业名字再次出现在账页时",
                        "breach_consequence": "林渊影子被记名",
                        "repayment_modes": ["查清镜源", "阻止二次认账"],
                    }
                ],
            }
        },
    )
    scene = SimpleNamespace(
        scene_number=1,
        title="空电梯",
        scene_type="opening_pressure",
        time_label="23:45",
        participants=["林渊", "王建业"],
        purpose={"story": "林渊赶到十七栋。", "emotion": "压迫"},
        entry_state={"location": "十七栋楼下"},
        exit_state={"location": "三楼门口"},
        target_word_count=700,
        key_dialogue_beats=[],
        sensory_anchors={"smell": "消毒水和桂花味混在一起"},
        forbidden_actions=[],
        metadata_json={
            "methodology_contract": {
                "visible_action_or_reaction": "林渊用铜钱压住镜脚。",
                "relationship_debts": [
                    {
                        "debtor": "林渊",
                        "creditor": "王建业",
                        "evidence_or_handle": "铜钱压住镜脚只争取数秒。",
                        "due_condition": "王建业开口解释镜子来历时",
                        "breach_consequence": "林渊被迫替认",
                        "repayment_modes": ["打断解释", "留下物证"],
                    }
                ],
                "signature_image": "空电梯镜面里没有林渊的影子。",
            }
        },
    )
    context = SimpleNamespace(
        story_bible={"characters": [{"name": "林渊", "role": "protagonist"}]},
        previous_scene_summaries=[],
        recent_timeline_events=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        chapter_contract={"information_release": "王建业只知道看见了怪事，不懂认账规则。"},
    )

    bundle = build_chapter_generation_input_bundle(
        project=project,
        chapter=chapter,
        scenes=[scene],
        context_packet=context,
        target_word_count=2200,
    )

    assert bundle.chapter_contract["decision_protocol"] == decision_protocol
    digest = bundle.acceptance_contract["methodology_application_contract"][
        "chapter_contract_digest"
    ]
    assert digest["decision_protocol"] == decision_protocol


def test_chapter_generation_input_bundle_filters_transient_generation_metadata() -> None:
    project = SimpleNamespace(
        id=uuid4(),
        slug="qingnang",
        title="青囊不语问阴阳",
        genre="民俗悬疑",
        sub_genre="镜债",
        language="zh-CN",
    )
    chapter = SimpleNamespace(
        id=uuid4(),
        chapter_number=1,
        title="十五分钟入账",
        chapter_goal="林渊在倒计时内确认王建业认账。",
        opening_situation="十七栋空电梯停在三楼。",
        main_conflict="林渊必须决定是否开门。",
        hook_type="物件钩",
        hook_description="缺角铜钱落在门缝血线里。",
        metadata_json={
            "methodology_contract": {
                "decision_protocol": {
                    "chosen_action": "林渊先验门缝。",
                    "alternatives_rejected": ["直接进电梯"],
                    "why_this_not_that": "直接进电梯会暴露影子。",
                    "constraint": "只能看现场物证。",
                    "wrong_choice_loss": "林渊被记名。",
                }
            },
            "chapter_first_generation": {
                "generation_input_bundle": {"huge": "x" * 10000}
            },
            "front10_regen_chapter_snapshot": {"old": "state"},
        },
    )
    scene = SimpleNamespace(
        scene_number=1,
        title="空电梯",
        scene_type="opening_pressure",
        time_label="23:45",
        participants=["林渊", "王建业"],
        purpose={"story": "林渊赶到十七栋。"},
        entry_state={"location": "楼下"},
        exit_state={"location": "三楼"},
        target_word_count=700,
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        metadata_json={"methodology_contract": {"signature_image": "空电梯没有影子。"}},
    )

    bundle = build_chapter_generation_input_bundle(
        project=project,
        chapter=chapter,
        scenes=[scene],
        context_packet=SimpleNamespace(
            story_bible={"characters": [{"name": "林渊", "role": "protagonist"}]},
            previous_scene_summaries=[],
            recent_timeline_events=[],
            active_plot_arcs=[],
            active_arc_beats=[],
            unresolved_clues=[],
            planned_payoffs=[],
        ),
        target_word_count=2200,
    )

    assert "methodology_contract" in bundle.chapter["metadata"]
    assert "chapter_first_generation" not in bundle.chapter["metadata"]
    assert "front10_regen_chapter_snapshot" not in bundle.chapter["metadata"]


def test_chapter_generation_input_bundle_ignores_stale_scene_creative_metadata() -> None:
    project = SimpleNamespace(
        id=uuid4(),
        slug="qingnang",
        title="青囊不语问阴阳",
        genre="民俗悬疑",
        sub_genre="镜债",
        language="zh-CN",
    )
    chapter = SimpleNamespace(
        id=uuid4(),
        chapter_number=1,
        title="子时前",
        chapter_goal="林渊在十七栋现场验证镜债。",
        opening_situation="林渊赶到十七栋楼下。",
        main_conflict="王建业即将被镜债收走。",
        hook_type="现场异常",
        hook_description="门外响起三短一长。",
        metadata_json={},
    )
    scene = SimpleNamespace(
        scene_number=1,
        title="空电梯",
        scene_type="opening_pressure",
        time_label="23:43",
        participants=["林渊", "王建业"],
        purpose={"story": "林渊先查空电梯。", "reader_hook": "电梯门开着，里面没有轿厢。"},
        entry_state={"location": "十七栋楼下"},
        exit_state={"location": "电梯口"},
        target_word_count=650,
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        hook_requirement="电梯门开着，里面没有轿厢。",
        metadata_json={
            "methodology_contract": {
                "visible_action_or_reaction": "林渊停在电梯口，不让王建业靠近井口。",
                "signature_image": "空电梯井壁映出一张无脸影子。",
                "cut_point": "电梯井里传出第二个王建业的笑声。",
                "information_control_mode": "现场证据先行，不解释完整规则。",
            },
            "scene_contract": {
                "visible_object": "空电梯井壁映出一张无脸影子。",
                "exit_hook": "电梯门开着，里面没有轿厢。",
            },
            "cut_point": "电话里响起第二个王建业的笑声。",
            "action_sequence": ["铜钱烫醒旧疤"],
            "auto_repair_hint": "上一轮要求改成电话开场。",
            "signature_image": "康熙铜钱发烫。",
            "visible_progress": "旧电话流程。",
        },
    )

    bundle = build_chapter_generation_input_bundle(
        project=project,
        chapter=chapter,
        scenes=[scene],
        context_packet=SimpleNamespace(
            story_bible={"characters": [{"name": "林渊", "role": "protagonist"}]},
            previous_scene_summaries=[],
            recent_timeline_events=[],
            active_plot_arcs=[],
            active_arc_beats=[],
            unresolved_clues=[],
            planned_payoffs=[],
        ),
        target_word_count=2600,
    )

    scene_payload = bundle.scenes[0]
    assert scene_payload["signature_image"] == "空电梯井壁映出一张无脸影子。"
    assert scene_payload["cut_point"] == "电梯井里传出第二个王建业的笑声。"
    assert scene_payload["visible_progress"] == "林渊停在电梯口，不让王建业靠近井口。"
    assert scene_payload["action_sequence"] is None
    assert "电话" not in str(scene_payload)
    assert "发烫" not in str(scene_payload)
    assert "上一轮要求" not in str(scene_payload)


def test_chapter_generation_input_stamp_is_compact() -> None:
    project = SimpleNamespace(
        id=uuid4(),
        slug="qingnang",
        title="青囊不语问阴阳",
        genre="民俗悬疑",
        sub_genre="镜债",
        language="zh-CN",
    )
    chapter = SimpleNamespace(
        id=uuid4(),
        chapter_number=1,
        title="十五分钟入账",
        chapter_goal="林渊在倒计时内确认王建业认账。",
        opening_situation="十七栋空电梯停在三楼。",
        main_conflict="林渊必须决定是否开门。",
        hook_type="物件钩",
        hook_description="缺角铜钱落在门缝血线里。",
        metadata_json={"methodology_contract": {"decision_protocol": {"chosen_action": "验门缝"}}},
    )
    scene = SimpleNamespace(
        scene_number=1,
        title="空电梯",
        scene_type="opening_pressure",
        time_label="23:45",
        participants=["林渊", "王建业"],
        purpose={"story": "林渊赶到十七栋。"},
        entry_state={"location": "楼下"},
        exit_state={"location": "三楼"},
        target_word_count=700,
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        metadata_json={"methodology_contract": {"signature_image": "空电梯没有影子。"}},
    )
    bundle = build_chapter_generation_input_bundle(
        project=project,
        chapter=chapter,
        scenes=[scene],
        context_packet=SimpleNamespace(
            story_bible={"characters": [{"name": "林渊", "role": "protagonist"}]},
            previous_scene_summaries=[],
            recent_timeline_events=[],
            active_plot_arcs=[],
            active_arc_beats=[],
            unresolved_clues=[],
            planned_payoffs=[],
        ),
        target_word_count=2200,
    )

    stamp = build_chapter_generation_input_stamp(bundle)

    assert stamp["schema_version"] == "chapter-generation-input-stamp.v1"
    assert stamp["scene_count"] == 1
    assert "story_bible" not in stamp
    assert "scenes" not in stamp
    assert "chapter" not in stamp


def test_chapter_generation_input_bundle_reports_missing_context() -> None:
    bundle = build_chapter_generation_input_bundle(
        project=SimpleNamespace(slug="x", title="x", genre="x", sub_genre=None, language="zh-CN"),
        chapter=SimpleNamespace(chapter_number=2, chapter_goal="", metadata_json={}),
        scenes=[],
        context_packet=SimpleNamespace(story_bible={}, previous_scene_summaries=[]),
        target_word_count=0,
    )

    assert bundle.ready is False
    assert "chapter.goal" in bundle.missing_context_keys
    assert "scenes" in bundle.missing_context_keys
    assert "chapter_acceptance_contract" in bundle.missing_context_keys
    assert "continuity.previous_scene_summaries" in bundle.missing_context_keys


def test_chapter_generation_input_bundle_does_not_require_previous_summary_for_chapter_one() -> None:
    bundle = build_chapter_generation_input_bundle(
        project=SimpleNamespace(slug="x", title="x", genre="x", sub_genre=None, language="zh-CN"),
        chapter=SimpleNamespace(chapter_number=1, chapter_goal="", metadata_json={}),
        scenes=[],
        context_packet=SimpleNamespace(story_bible={}, previous_scene_summaries=[]),
        target_word_count=0,
    )

    assert "continuity.previous_scene_summaries" not in bundle.missing_context_keys
