from __future__ import annotations

import re

import pytest

from bestseller.services.concept_title_formulas import (
    TitleFormula,
    clamp_title_length,
    load_title_cores,
    load_title_formulas,
    render_title,
)

pytestmark = pytest.mark.unit

_CJK_PATTERN = re.compile(r"[一-鿿]")


def _cjk_length(text: str) -> int:
    return len(_CJK_PATTERN.findall(text))


def test_title_formula_pool_has_at_least_five_entries() -> None:
    formulas = load_title_formulas()
    ids = {item.id for item in formulas}
    assert len(formulas) >= 5
    assert len(ids) == len(formulas)
    for expected in ("contrarian_truth", "genre_deadlock", "direction_x_hook"):
        assert expected in ids


def test_title_cores_cover_all_legacy_eight() -> None:
    cores = load_title_cores()
    for legacy in (
        "death_grows",
        "forced_loss",
        "emotion_value",
        "hide_anti_trope",
        "misunderstanding",
        "fourth_disaster",
        "rule_horror",
        "profession_reversal",
    ):
        assert legacy in cores
        # Each core must be a short punchy Chinese phrase.
        assert 4 <= _cjk_length(cores[legacy]) <= 12


def test_title_cores_cover_at_least_40_mechanisms() -> None:
    cores = load_title_cores()
    assert len(cores) >= 40


@pytest.mark.parametrize("mech_key,fragment", [
    ("death_grows", "死线"),
    ("sign_in_overload", "签到"),
    ("system_levy", "升级"),
    ("lottery_curse", "好运"),
    ("gold_finger_leak", "金手指"),
    ("trial_loop", "试炼"),
    ("time_loop", "死"),
    ("mastered_disciple", "徒弟"),
    ("forced_loss", "亏"),
    ("profession_reversal", "职业"),
    ("divine_doctor", "救"),
    ("mind_reader", "真心"),
    ("fake_heir", "千金"),
    ("dog_chaser", "退"),
    ("silver_lining_loser", "舔"),
    ("playboy_pivot", "海王"),
    ("emotion_value", "情绪"),
    ("misunderstanding", "解释"),
    ("green_tea_reveal", "绿茶"),
    ("crazy_girlfriend", "疯"),
    ("sicko_devotion", "病"),
    ("everyone_loves_me", "万人"),
    ("villain_redeem", "反派"),
    ("revenge_then_what", "仇"),
    ("hide_anti_trope", "低调"),
    ("master_above", "师尊"),
    ("substitute_bride", "替嫁"),
    ("joy_wedding", "冲喜"),
    ("cannon_fodder_rises", "炮灰"),
    ("mass_transmigration", "穿"),
    ("withdraw_engagement", "退婚"),
    ("divorce_husband", "休夫"),
    ("rule_horror", "规矩"),
    ("fourth_disaster", "玩家"),
    ("script_within_script", "被写"),
    ("apocalypse_hoard", "囤"),
    ("npc_resistance", "NPC"),
    ("body_swap", "错位"),
    ("loop_killer", "循环"),
    ("proxy_war", "代理"),
])
def test_title_core_for_mechanism(mech_key: str, fragment: str) -> None:
    cores = load_title_cores()
    assert mech_key in cores
    assert fragment in cores[mech_key]


def test_render_title_substitutes_known_slots() -> None:
    formulas = load_title_formulas()
    genre_deadlock = next(item for item in formulas if item.id == "genre_deadlock")
    out = render_title(
        genre_deadlock,
        title_core="core",
        genre_label="末日",
        reward="资源",
        cost="代价",
        direction_title="情绪轴",
        hook_type="悬疑",
        n=7,
    )
    assert "末日" in out
    assert "资源" in out
    assert "代价" in out
    assert "{" not in out and "}" not in out


def test_clamp_title_length_truncates_to_high() -> None:
    out = clamp_title_length("一" * 40, low=6, high=25)
    assert _cjk_length(out) == 25


def test_clamp_title_length_preserves_short_titles() -> None:
    out = clamp_title_length("签到一次", low=6, high=25)
    assert out == "签到一次"
