"""HYPE_MISSING 修复剧本（2026-08-16）。

定位：**不新建「爽点触发重写」的通路**，只在章因别的原因被修时顺带带上爽点指令。

为什么不建新触发：我们的书 84% 的章读不出爽点，新建触发等于让 84% 的章进重写
——那正是「门禁自恢复无限循环烧 88 万 token」的形状。覆盖率的正解在**生成端**
（爽点约束现已进 prompt，见 writing_presets._synthesized_hype_block 的修复），
不在修复端。修复端只负责「反正要重写，那就顺便把结算补上」，零新增触发、零 churn。

判据来自三类爽文读者独立盲评后的唯一共识（三人在憋屈耐受、反派智商下限、
是否接受「安静地赢」上全都相反，只有这三条一致）。
"""

from __future__ import annotations

from bestseller.services.quality_repair_playbooks import (
    _PLAYBOOKS,
    render_quality_repair_playbooks,
)


def test_playbook_registered() -> None:
    assert "HYPE_MISSING" in _PLAYBOOKS
    assert _PLAYBOOKS["HYPE_MISSING"].scope == "chapter"


def test_instruction_encodes_all_three_parts() -> None:
    """三段律逐条可指认。"""

    text = _PLAYBOOKS["HYPE_MISSING"].instruction
    assert "有名字的人" in text  # ①对象具体
    assert "看见并因此改变" in text  # ②证人可见且变化
    assert "下一章还能用" in text  # ③账上留一笔


def test_instruction_names_the_anti_patterns() -> None:
    """点名反模式，否则模型会拿概括交差。"""

    text = _PLAYBOOKS["HYPE_MISSING"].instruction
    assert "某个势力" in text
    assert "众人震惊" in text


def test_forbids_new_setting_to_win() -> None:
    """B 档读者最恨的一条：临时长出新能力救场 = 退订。"""

    text = _PLAYBOOKS["HYPE_MISSING"].instruction
    assert "不要新增设定" in text
    assert "本章之前已经建立过" in text


def test_no_hype_token_seeding() -> None:
    """种词铁律：只给结构，不给爽点 token。

    用户 2026-08-16 明确指出的病：把打脸/跪下/碾压当「最优解」注入系统，
    导致每本书必定长成同一副样子。内容层允许写这些，注入层不许点名。
    """

    rendered = render_quality_repair_playbooks(("HYPE_MISSING",))
    for token in ("打脸", "跪下", "求饶", "碾压", "装逼", "扮猪吃虎", "羞辱"):
        assert token not in rendered, f"剧本含爽点 token「{token}」= 种词"


def test_acceptance_is_checkable() -> None:
    """验收标准必须能被逐条指认，不能是「更爽一些」。"""

    acc = _PLAYBOOKS["HYPE_MISSING"].acceptance
    assert "谁被结算" in acc and "谁看见了" in acc and "带走了什么" in acc
    assert "三者缺一" in acc
