# ruff: noqa: RUF001
from __future__ import annotations

from bestseller.services.emotion_driven_kernel import (
    emotion_driven_kernel_from_dict,
    emotion_driven_kernel_to_dict,
    evaluate_emotion_contracts,
    extract_chapter_emotion_contract,
    render_emotion_driven_kernel_prompt_block,
)


def _valid_kernel_payload() -> dict[str, object]:
    return {
        "reader_emotion_promise": "让读者先心疼主角，再等待他带着代价完成反击。",
        "primary_reader_waiting": ["身份揭开", "旧案翻盘"],
        "empathy_contracts": [
            {
                "contract_id": "empathy-protagonist-opening",
                "character_key": "protagonist",
                "chapter_range": "1-3",
                "situation": "主角被迫回到旧案现场。",
                "current_desire": "找到能证明自己清白的第一份证据。",
                "fear_or_loss": "如果失败，妹妹会被当成同谋带走。",
                "sensory_entry": "潮湿档案纸的霉味和手腕旧伤的刺痛。",
                "judgment_logic": "他习惯先怀疑每个帮助自己的人。",
                "emotional_reaction": "他压住求助冲动，先藏起档案袋。",
                "reasonable_action": "假装配合问询，实际调换证据封条。",
                "consequence": "他拿到线索，也暴露了自己仍在追查旧案。",
            }
        ],
        "bomb_contracts": [
            {
                "bomb_id": "bomb-swapped-evidence",
                "bomb_type": "danger",
                "chapter_range": "1-5",
                "reader_knows": "读者知道证物袋已被反派调包。",
                "character_blindspot": "主角信任保管证物的旧同僚。",
                "danger": "错误证物会让主角成为伪造证据的人。",
                "trigger_condition": "证物袋在听证会上被当众打开。",
                "countdown": "三章后听证会开始。",
                "consequence": "主角清白线崩塌，妹妹被追加拘押。",
                "payoff_window": "第4-5章必须爆炸或反杀。",
                "rational_ignorance": "调包者使用了旧同僚独有封缄。",
                "escalation_steps": ["反派提前递交通知", "旧同僚失联"],
            }
        ],
        "antagonist_moral_contracts": [
            {
                "antagonist_key": "antag-mentor",
                "chapter_range": "1-20",
                "public_mask": "守规矩、救过许多弱者的老刑官。",
                "real_good_deeds": ["替主角挡过一次处分", "资助过受害者家属"],
                "hidden_desire": "保住自己亲手建立的秩序名声。",
                "fear_of_loss": "害怕旧案证明他的秩序从一开始就是错的。",
                "cracks": ["只在涉及旧案时失控", "宽容总刚好保护自己人"],
                "first_boundary_crossing": "默许下属销毁一页档案。",
                "self_justification": "他认为牺牲一个人能保住更多人的信任。",
                "collapse_wound": "他不敢承认自己救过的人也被他的制度吞掉。",
                "target_reader_response": "恨他，但理解他为什么走到这里。",
            }
        ],
        "ending_texture_contract": {
            "ending_type": "HE",
            "core_wish_fulfilled": "主角洗清旧案，妹妹获得自由。",
            "relationship_settlement": "兄妹重新成为彼此的归处。",
            "irreversible_cost_retained": "母亲错过的十年无法补回。",
            "theme_answer": "真相不能复原过去，但能阻止伤害继续复制。",
            "future_open": "主角成立新的民间调查所。",
            "aesthetic_callback": "结尾回到第一章潮湿档案室，但窗户终于打开。",
        },
        "emotion_chain": [
            {
                "chapter_range": "1-3",
                "target_reader_emotion": "心疼和焦虑",
                "reader_waiting_for": "主角什么时候发现证物被调包。",
                "reader_worry": "他会不会在听证会上被反杀。",
                "pressure_source": "听证会倒计时。",
                "payoff_or_aftereffect": "第3章让错误证物进入公开流程。",
                "callback": "潮湿档案袋作为回收物。",
            }
        ],
        "callback_motifs": ["潮湿档案袋", "打不开的窗"],
    }


def test_kernel_round_trip_and_prompt_block() -> None:
    kernel = emotion_driven_kernel_from_dict(_valid_kernel_payload())

    dumped = emotion_driven_kernel_to_dict(kernel)
    assert dumped["reader_emotion_promise"] == "让读者先心疼主角，再等待他带着代价完成反击。"
    assert dumped["bomb_contracts"][0]["bomb_id"] == "bomb-swapped-evidence"

    block = render_emotion_driven_kernel_prompt_block(kernel, chapter_number=2)
    assert "emotion_driven_core" in block
    assert "主角当前欲望" in block
    assert "证物袋已被反派调包" in block
    assert "真实善行" in block
    assert "不可逆代价" in block


def test_kernel_normalizes_common_llm_aliases() -> None:
    payload = _valid_kernel_payload()
    payload["ending_texture_contract"] = {
        **payload["ending_texture_contract"],
        "ending_type": "HE-with-cost",
    }
    payload["callback_motifs"] = [
        {
            "motif_id": "death-countdown-red-sun",
            "symbol": "红色日落",
            "meaning": "死亡倒计时与最终关闭规则。",
        }
    ]

    kernel = emotion_driven_kernel_from_dict(payload)
    dumped = emotion_driven_kernel_to_dict(kernel)

    assert dumped["ending_texture_contract"]["ending_type"] == "HE"
    assert dumped["callback_motifs"] == ["death-countdown-red-sun：红色日落：死亡倒计时与最终关闭规则。"]


def test_extract_chapter_emotion_contract_filters_by_range() -> None:
    kernel = emotion_driven_kernel_from_dict(_valid_kernel_payload())

    chapter_2 = extract_chapter_emotion_contract(kernel, chapter_number=2)
    assert len(chapter_2["empathy_contracts"]) == 1
    assert len(chapter_2["bomb_contracts"]) == 1
    assert len(chapter_2["emotion_chain"]) == 1

    chapter_9 = extract_chapter_emotion_contract(kernel, chapter_number=9)
    assert chapter_9["empathy_contracts"] == []
    assert chapter_9["bomb_contracts"] == []
    assert len(chapter_9["antagonist_moral_contracts"]) == 1


def test_evaluate_emotion_contracts_reports_missing_fields() -> None:
    payload = _valid_kernel_payload()
    payload["empathy_contracts"] = [
        {
            "contract_id": "broken-empathy",
            "character_key": "protagonist",
            "chapter_range": "1",
            "situation": "主角被围堵。",
            "current_desire": "离开现场。",
            "fear_or_loss": "失去证据。",
        }
    ]
    payload["bomb_contracts"] = [
        {
            "bomb_id": "broken-bomb",
            "bomb_type": "danger",
            "chapter_range": "1",
            "reader_knows": "读者知道有人埋伏。",
            "character_blindspot": "主角不知道。",
            "danger": "会被抓。",
        }
    ]
    payload["ending_texture_contract"] = {
        "ending_type": "HE",
        "core_wish_fulfilled": "主角胜利。",
        "relationship_settlement": "朋友和好。",
        "theme_answer": "要相信未来。",
        "future_open": "继续生活。",
    }

    report = evaluate_emotion_contracts(emotion_driven_kernel_from_dict(payload))

    assert not report.passed
    assert "EMPATHY_CHAIN_MISSING" in {issue.code for issue in report.issues}
    assert "BOMB_TRIGGER_MISSING" in {issue.code for issue in report.issues}
    assert "ENDING_COST_ERASED" in {issue.code for issue in report.issues}


def test_be_gate_requires_value_choice_and_callback() -> None:
    payload = _valid_kernel_payload()
    payload["ending_texture_contract"] = {
        "ending_type": "BE",
        "core_wish_fulfilled": "他们曾经差点逃走。",
        "irreversible_cost_retained": "旧城被烧毁。",
        "tragic_causality": "家族契约要求继承人留下断后。",
    }

    report = evaluate_emotion_contracts(emotion_driven_kernel_from_dict(payload))

    assert not report.passed
    codes = {issue.code for issue in report.issues}
    assert "TRAGEDY_CHOICE_MISSING" in codes
    assert "ENDING_CALLBACK_MISSING" in codes
