from __future__ import annotations

import pytest

from bestseller.services.drafts import _render_contract_section

pytestmark = pytest.mark.unit


def test_render_contract_section_includes_character_and_causal_fields() -> None:
    block = _render_contract_section(
        {
            "contract_summary": "沈砚追查暗门信号。",
            "core_conflict": "封港倒计时压缩调查窗口。",
            "causal_contract": {
                "protagonist_choice": "沈砚选择进入暗门而不是等待支援。",
                "character_delta": "沈砚从旁观者变成承担调查责任的人。",
                "pressure": "封港命令一小时后生效。",
                "visible_action_or_reaction": "他接下港务官的任务并开始追查信号。",
                "cost_or_tradeoff": "判断失误会失去最后一次追查机会。",
                "next_reader_desire": "读者想知道暗门后的第二枚印记是谁留下的。",
            },
        },
        None,
        language="zh-CN",
    )

    assert "人物变化与因果合同" in block
    assert "主角选择" in block
    assert "沈砚选择进入暗门" in block
    assert "下一章读者欲望" in block
