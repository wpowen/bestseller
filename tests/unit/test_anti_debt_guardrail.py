"""Anti-debt-metaphor guardrail for conception.

The systemic root of the recurring 债/账/欠条/账本 golden fingers: the
golden-finger design principle mandates a 代价 (cost), but nothing forbade
expressing that cost as a financial ledger — and an LLM writing in a
宗门/升级/仙侠 register renders "cost" as debt by default. This guardrail is
the negative constraint that was missing (the debt twin of the family-trauma
``_default_motif_guardrail``): ban ledger framing of the golden finger / cost
unless the user explicitly asked for a debt-themed book.
"""

from __future__ import annotations

from typing import Any

from bestseller.services import conception as C


def _ctx(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "genre": "仙侠升级流",
        "sub_genre": "宗门逆袭",
        "description": "宗门底层杂役逆袭。",
    }
    base.update(kw)
    return base


# ── the guardrail bans ledger framing by default ────────────────────────────


def test_guardrail_zh_bans_debt_ledger_vocabulary() -> None:
    block = C._anti_debt_metaphor_guardrail(_ctx(), is_en=False)
    assert block  # non-empty
    for banned in ("债", "账本", "欠条", "记账", "还债"):
        assert banned in block, banned
    # …and it offers non-financial cost forms so the model has somewhere to go
    assert "反噬" in block or "损耗" in block


def test_guardrail_en_bans_ledger_and_has_no_zh() -> None:
    block = C._anti_debt_metaphor_guardrail(_ctx(description="A sect underdog rises."), is_en=True)
    assert block
    assert "debt" in block.lower() and "ledger" in block.lower()
    assert "账本" not in block


# ── respect explicit user intent: a real debt-themed book is allowed ─────────


def test_guardrail_skips_when_user_description_is_debt_themed() -> None:
    # A book the user deliberately wants about debt collection must not be gagged.
    ctx = _ctx(description="主角开一间阴间讨债事务所，专收因果债与阳寿欠账。")
    assert C._anti_debt_metaphor_guardrail(ctx, is_en=False) == ""


def test_guardrail_skips_when_user_hints_request_debt() -> None:
    ctx = _ctx(user_hints={"concept": "记账流金手指，用账本收人心"})
    assert C._anti_debt_metaphor_guardrail(ctx, is_en=False) == ""


def test_guardrail_active_for_incidental_non_debt_book() -> None:
    # A normal cultivation premise with no debt intent still gets the guardrail.
    ctx = _ctx(description="废灵根少年觉醒吞噬术法的残页，逆练禁术越阶。")
    assert C._anti_debt_metaphor_guardrail(ctx, is_en=False)


# ── it is wired into the conception prompts ─────────────────────────────────


def test_finalize_prompt_embeds_anti_debt_guardrail() -> None:
    prompt = C._finalize_user_prompt(_ctx(chapter_count=300), {}, {}, {}, {})
    assert "反债务" in prompt or "债" in prompt
    assert "反噬" in prompt or "损耗" in prompt


def test_character_prompt_embeds_anti_debt_guardrail() -> None:
    ctx = _ctx(chapter_count=300)
    prompt = C._character_user_prompt(ctx)
    assert "欠条" in prompt


def test_market_prompt_embeds_anti_debt_guardrail() -> None:
    ctx = _ctx(
        chapter_count=300,
        recommended_platforms=["番茄"],
        recommended_audiences=["男频"],
        trend_keywords=["升级"],
        trend_score=80,
    )
    prompt = C._market_user_prompt(ctx)
    assert "账本" in prompt


def test_debt_themed_book_prompt_omits_guardrail() -> None:
    # End-to-end: a user-requested debt book keeps its theme (no guardrail text).
    ctx = _ctx(
        description="阴间讨债人靠一本生死账簿收阳寿欠账。",
        chapter_count=300,
    )
    prompt = C._character_user_prompt(ctx)
    assert "反债务化" not in prompt


# ── deterministic detector for observability / gating ───────────────────────


def test_debt_dominance_flags_ledger_golden_finger() -> None:
    gf = "宗债簿——将人心愿力折算为可支出的债币，每次兑现同步记一笔反欠账，欠债入账。"
    assert C._is_debt_dominated_mechanism(gf) is True


def test_debt_dominance_ignores_single_incidental_mention() -> None:
    gf = "抚器残手——触碰残破旧物听见上一任主人的临终一念，反向还原失传手艺；执念越重反噬越快。"
    assert C._is_debt_dominated_mechanism(gf) is False


def test_debt_dominance_empty_is_false() -> None:
    assert C._is_debt_dominated_mechanism("") is False
    assert C._is_debt_dominated_mechanism(None) is False  # type: ignore[arg-type]
