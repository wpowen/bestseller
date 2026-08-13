"""The anti-debt guardrail is retired — this file now guards its absence.

History. The framework's golden-finger design principle mandated a 代价, and an
LLM writing in a 宗门/升级/仙侠 register renders "cost" as debt by default, so a
guardrail was added to ban ledger framing unless the user asked for a
debt-themed book. It grew into a prompt block, a set of dominance detectors, a
planner veto, a prose-layer detector and a minimal-cost filter.

Why it is gone (2026-08-02). Debt is ordinary story material — a shop's unpaid
bill, a sect's resource accounts, a favour owed to an elder. Worse, the same
framework that banned the vocabulary was simultaneously ordering costs in the
chapter contract, the 代价账 hard gates and the per-category material rules;
books were executed in the foundation stage for writing exactly what they had
been told to write. The real fix was upstream: stop authoring motif content into
prompts at all. Cross-book sameness is handled by the quarantine + fingerprint
tests in ``test_prompt_pollution_quarantine.py``.
"""

from __future__ import annotations

from typing import Any

from bestseller.services import conception as C
from bestseller.services import anti_default_motif as M


def _ctx(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "genre": "仙侠升级流",
        "sub_genre": "宗门逆袭",
        "description": "宗门底层杂役逆袭。",
        "chapter_count": 100,
        "language": "zh-CN",
        "recommended_platforms": ["番茄小说"],
        "recommended_audiences": ["移动端追读读者"],
        "trend_keywords": ["宗门", "逆袭"],
        "trend_score": 80,
        "trend_summary": "宗门逆袭稳定走高。",
        "default_platform": "番茄小说",
        "existing_overrides": {},
    }
    base.update(kw)
    return base


def test_guardrail_blocks_render_nothing() -> None:
    assert C._anti_debt_metaphor_guardrail(_ctx(), is_en=False) == ""
    assert C._anti_debt_metaphor_guardrail(_ctx(), is_en=True) == ""
    assert C._default_motif_guardrail(_ctx(), is_en=False) == ""
    assert M.anti_debt_block(is_en=False) == ""
    assert M.anti_death_default_block(is_en=False) == ""


def test_conception_prompts_carry_no_ledger_ban() -> None:
    ctx = _ctx()
    for prompt in (
        C._finalize_user_prompt(ctx, {}, {}, {}, {}),
        C._character_user_prompt(ctx),
        C._market_user_prompt(ctx),
    ):
        assert "反债务化" not in prompt
        assert "绝不能表达为金融记账形态" not in prompt
        assert "自然后果规则" not in prompt


def test_debt_dominance_detector_revived_at_champion_level_only() -> None:
    """2026-08-13 修订（用户令）：连续两本用户书在未要求下撞进债务/丧葬默认族，
    冠军级 is_debt_dominated 靶向复活（支配阈值+用户意图豁免+只在构思冠军层
    消费）。账簿型金手指正是被定罪的收敛原型，必须可检出；其余债务检测器
    （contains_*、正文层）维持 8·2 退役。"""

    ledger_heavy = "金手指是一本账簿：每次出手都记一笔欠账，债主按利息讨债。"
    assert M.is_debt_dominated(ledger_heavy)
    assert not M.is_debt_dominated("突破需要偿还一份人情")
    assert not M.contains_debt_motif(ledger_heavy)
    assert not M.contains_core_debt_framing({"golden_finger": ledger_heavy})


def test_prose_layer_debt_detector_is_retired() -> None:
    from bestseller.services.ai_flavor.detector import _detect_debt_metaphor_leak

    assert _detect_debt_metaphor_leak("他认下这笔账，白板上的字就是欠条。", lang="zh") == []
