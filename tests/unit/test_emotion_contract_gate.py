# ruff: noqa: RUF001
from __future__ import annotations

from bestseller.services.emotion_contract_gate import (
    BOMB_PAYOFF_OVERDUE,
    BOMB_TRIGGER_MISSING,
    EMOTION_DEBT_CODES,
    EMPATHY_CHAIN_MISSING,
    ENDING_COST_ERASED,
    TRAGEDY_CAUSALITY_WEAK,
    build_emotion_contract_checker_report,
    emotion_contract_gate_snapshot,
    emotion_debt_code_for_issue,
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


def test_valid_emotion_kernel_becomes_clean_checker_report() -> None:
    report = build_emotion_contract_checker_report(_valid_kernel_payload(), chapter=0)

    assert report.agent == "emotion-contract-gate"
    assert report.chapter == 0
    assert report.passed is True
    assert report.overall_score == 100
    assert report.issues == ()
    assert report.metrics["emotion_kernel_present"] is True


def test_contract_issues_become_stable_checker_debt_codes() -> None:
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

    report = build_emotion_contract_checker_report(payload, chapter=2)
    codes = {issue.id for issue in report.issues}

    assert report.passed is False
    assert {EMPATHY_CHAIN_MISSING, BOMB_TRIGGER_MISSING, ENDING_COST_ERASED} <= codes
    assert report.blocks_write is False
    assert all(issue.can_override for issue in report.issues)
    assert set(report.metrics["debt_codes"]) <= EMOTION_DEBT_CODES


def test_bomb_payoff_overdue_surfaces_as_debt_code() -> None:
    report = build_emotion_contract_checker_report(
        _valid_kernel_payload(),
        chapter=6,
        current_chapter=6,
    )

    assert report.passed is False
    assert any(issue.id == BOMB_PAYOFF_OVERDUE for issue in report.issues)
    assert report.metrics["overdue_bomb_ids"] == ["bomb-swapped-evidence"]
    assert report.blocks_write is False

    resolved = build_emotion_contract_checker_report(
        _valid_kernel_payload(),
        chapter=6,
        current_chapter=6,
        resolved_bomb_ids=("bomb-swapped-evidence",),
    )
    assert resolved.passed is True
    assert resolved.issues == ()


def test_tragedy_causality_remains_hard_checker_violation() -> None:
    payload = _valid_kernel_payload()
    payload["ending_texture_contract"] = {
        "ending_type": "BE",
        "core_wish_fulfilled": "他们曾经差点逃走。",
        "irreversible_cost_retained": "旧城被烧毁。",
    }

    report = build_emotion_contract_checker_report(payload, chapter=0)
    hard_codes = {issue.id for issue in report.hard_violations}

    assert TRAGEDY_CAUSALITY_WEAK in hard_codes
    assert report.blocks_write is True


def test_snapshot_and_code_mapping_are_stable() -> None:
    assert emotion_debt_code_for_issue("BOMB_CONTRACT_INCOMPLETE") == BOMB_TRIGGER_MISSING

    report = build_emotion_contract_checker_report(None, chapter=0)
    snapshot = emotion_contract_gate_snapshot(report)

    assert snapshot["passed"] is True
    assert snapshot["score"] < 100
    assert snapshot["issue_codes"] == ["EMOTION_KERNEL_MISSING"]
    assert snapshot["soft_suggestion_count"] == 1
