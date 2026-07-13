from __future__ import annotations

from bestseller.services.protagonist_decision_agent import (
    evaluate_protagonist_decision_protocol,
    render_outline_decision_agent_prompt,
    render_planner_decision_protocol_contract,
    render_writer_decision_protocol,
)


def _complete_protocol() -> dict[str, object]:
    return {
        "viewpoint_character": "裴渡",
        "decision_point": "黑棺要求他立刻钉下第七枚往生钉，否则亡修会在义庄起尸。",
        "known_facts": [
            "每钉一枚往生钉都会缩短自己的寿命",
            "这一枚若不钉，三名守灵人会先被起尸者袭击",
        ],
        "unknowns": ["黑棺为何等了他七十年", "能否由别人代钉"],
        "immediate_goal": "先保住守灵人，同时留下查清黑棺来历的机会。",
        "options_considered": [
            "立刻钉钉压尸",
            "先疏散守灵人再封锁义庄",
            "让助手代钉并观察代价是否转移",
        ],
        "obvious_safe_option": "先疏散守灵人并封门拖延。",
        "chosen_action": "先让助手带人撤到镇魂线外，自己用半枚废钉试探后再决定是否落钉。",
        "why_not_safer_option": "封门最多撑十息，无法覆盖三名守灵人的撤离时间，必须试探性压尸。",
        "personality_basis": "裴渡谨慎、惜命，但不会把无辜者当成试错成本。",
        "risk_control": "只用废钉试探；助手守在镇魂线外，异变就斩断棺绳撤离。",
        "expected_gain": "争取撤离时间，并验证寿命代价是否只在完整落钉时发生。",
        "failure_cost": "试探失败会损失寿命，且起尸者可能突破棺绳。",
        "new_information_or_pressure": "棺盖已经抬起一指，封门只剩十息。",
        "first_person_reasoning": (
            "我不拿命赌一个未知规则；先救人、用最小代价试出边界，再决定要不要钉。"
        ),
    }


def test_complete_first_person_decision_protocol_passes() -> None:
    report = evaluate_protagonist_decision_protocol(
        _complete_protocol(),
        chapter_number=1,
    )

    assert report.passed is True
    assert report.blocking_findings == ()


def test_legacy_five_field_protocol_is_not_decision_intelligence() -> None:
    report = evaluate_protagonist_decision_protocol(
        {
            "chosen_action": "裴渡直接钉下往生钉。",
            "alternatives_rejected": ["逃走", "找人帮忙"],
            "why_this_not_that": "剧情已经没有时间。",
            "constraint": "情况紧急。",
            "wrong_choice_loss": "裴渡会死。",
        },
        chapter_number=1,
    )

    assert report.passed is False
    assert "PROTAGONIST_DECISION_CONTEXT_INCOMPLETE" in report.blocking_codes
    assert "known_facts" in report.missing_fields
    assert "obvious_safe_option" in report.missing_fields
    assert "first_person_reasoning" in report.missing_fields


def test_high_risk_choice_without_risk_control_is_blocked() -> None:
    protocol = _complete_protocol()
    protocol["chosen_action"] = "裴渡独自躺进黑棺，赌黑棺不会立刻合上。"
    protocol["failure_cost"] = "一旦判断错误就会当场死亡，且无法撤回。"
    protocol["risk_control"] = ""

    report = evaluate_protagonist_decision_protocol(protocol, chapter_number=4)

    assert report.passed is False
    assert "PROTAGONIST_HIGH_RISK_WITHOUT_EXIT" in report.blocking_codes


def test_agent_prompt_forces_normal_person_and_character_counterfactual() -> None:
    prompt = render_outline_decision_agent_prompt(language="zh-CN")

    assert "正常人基线" in prompt
    assert "角色基线" in prompt
    assert "暂时忘掉作者想让剧情去哪里" in prompt
    assert "低成本" in prompt
    assert "认知边界" in prompt
    assert "PROTAGONIST_PLOT_SERVING_STUPIDITY" in prompt


def test_planner_and_writer_share_the_same_decision_contract() -> None:
    planner_contract = render_planner_decision_protocol_contract(language="zh-CN")
    writer_contract = render_writer_decision_protocol(
        _complete_protocol(),
        language="zh-CN",
    )

    assert "known_facts" in planner_contract
    assert "obvious_safe_option" in planner_contract
    assert "risk_control" in planner_contract
    assert "显然更安全的选项" in writer_contract
    assert "止损/退路/后手" in writer_contract
    assert "不要用旁白一句『他别无选择』" in writer_contract
