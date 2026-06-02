from __future__ import annotations

import pytest

from bestseller.domain.anti_commonsense_hook import HookMechanism, HookSpec
from bestseller.services.anti_commonsense_hook import build_hook_spec_from_mechanism
from bestseller.services.anti_commonsense_mechanisms import get_mechanism
from bestseller.services.hook_formula_pool import (
    HookFormula,
    get_formula,
    list_formulas,
    render_one_liner_for_spec,
    select_formula_for_mechanism,
)


def test_formula_pool_has_at_least_ten_entries() -> None:
    formulas = list_formulas()
    assert len(formulas) >= 10
    ids = {item.id for item in formulas}
    assert len(ids) == len(formulas)
    # The 6 legacy EXPRESSION_STYLES ids are preserved as aliases.
    for legacy in (
        "rule_collision",
        "public_misread",
        "opening_deadlock",
        "cost_first",
        "reader_question",
        "market_logline",
    ):
        assert legacy in ids


def test_mechanism_formula_affinities_reference_existing_formulas() -> None:
    from bestseller.services.anti_commonsense_mechanisms import list_mechanisms

    formula_ids = {item.id for item in list_formulas()}
    missing = sorted(
        {
            formula_id
            for mechanism in list_mechanisms()
            for formula_id in mechanism.formula_affinity
            if formula_id not in formula_ids
        }
    )
    assert missing == []


def test_select_formula_for_mechanism_prefers_affinity() -> None:
    mechanism = HookMechanism(
        key="fake_test",
        label="测试机制",
        formula_affinity=("bawang_clause", "villain_taunt"),
        base_desire_pool=("测试欲望",),
        reversal_template="每次越想 X 越能 Y",
        reward_pool=("测试奖励",),
        cost_templates=("测试代价",),
        misunderstanding_patterns=("测试误解",),
    )
    chosen = select_formula_for_mechanism(mechanism, genre="都市", variant_index=0)
    assert chosen.id in {"bawang_clause", "villain_taunt"}


def test_select_formula_falls_back_when_no_affinity() -> None:
    mechanism = HookMechanism(
        key="fake_test",
        label="测试",
        base_desire_pool=("X",),
        reversal_template="Y",
        reward_pool=("Z",),
        cost_templates=("W",),
        misunderstanding_patterns=("V",),
    )
    chosen = select_formula_for_mechanism(mechanism, genre="未知题材", variant_index=5)
    assert isinstance(chosen, HookFormula)
    assert chosen.id


def test_render_one_liner_for_spec_substitutes_slots() -> None:
    spec = HookSpec(
        mechanism_key="fake_test",
        genre="都市",
        protagonist_role="陆寒",
        base_desire="翻盘",
        reversal="越亏越赚",
        rewards=("权限升级",),
        costs=("现金流断裂",),
        misunderstanding="旁人以为他在败家",
        expression_style="cost_first",
        one_liner="placeholder",
        core_rule="placeholder",
    )
    text = render_one_liner_for_spec(spec, formula_id="cost_first")
    assert "陆寒" in text
    assert "翻盘" in text
    assert "越亏越赚" in text
    assert "权限升级" in text
    assert "现金流断裂" in text
    assert "旁人以为他在败家" in text
    assert "{" not in text and "}" not in text


def test_render_one_liner_for_spec_handles_missing_optional_slots() -> None:
    spec = HookSpec(
        mechanism_key="fake_test",
        genre="未知",
        protagonist_role="",
        base_desire="X",
        reversal="Y",
        rewards=(),
        costs=(),
        misunderstanding=None,
        one_liner="placeholder",
        core_rule="placeholder",
    )
    text = render_one_liner_for_spec(spec, formula_id="public_misread")
    # No literal slot tokens leak into output.
    assert "{role}" not in text and "{desire}" not in text
    assert "{" not in text and "}" not in text


@pytest.mark.parametrize("formula_id", [item.id for item in list_formulas()])
def test_every_formula_renders_without_residual_slot_tokens(formula_id: str) -> None:
    spec = HookSpec(
        mechanism_key="fake_test",
        genre="都市",
        protagonist_role="陆寒",
        base_desire="翻盘",
        reversal="越亏越赚",
        rewards=("权限升级",),
        costs=("现金流断裂",),
        misunderstanding="旁人以为他在败家",
        one_liner="placeholder",
        core_rule="placeholder",
    )
    text = render_one_liner_for_spec(spec, formula_id=formula_id)
    assert "{" not in text and "}" not in text, formula_id


def test_get_formula_raises_keyerror_for_unknown_id() -> None:
    with pytest.raises(KeyError):
        get_formula("not_a_real_formula")


def test_render_one_liner_runs_end_to_end_via_build_hook_spec() -> None:
    """Smoke test: build_hook_spec_from_mechanism should use the pool end-to-end."""

    real = get_mechanism("death_grows")
    spec = build_hook_spec_from_mechanism(real, genre="都市", variant_index=0)
    assert spec.one_liner
    assert spec.expression_style in real.formula_affinity
    assert "{" not in spec.one_liner and "}" not in spec.one_liner
