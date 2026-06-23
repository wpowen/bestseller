"""L1 guard: the writer-prompt syntactic-rhythm ceiling (anti-staccato prevention).

P0-1: the well-authored ``config/rhythm_engineering.yaml::solo_line_ceiling``
rule existed but was never injected into any writer prompt (prose-layer instance
of dormant methodology). It is now wired into ``build_anti_slop_footer`` (and the
golden-three rules), replacing the permissive "允许短句直接落地" that licensed the
碎句 tic. This guards that wiring so it cannot silently regress to dormant.
"""

from __future__ import annotations

from bestseller.services.prompt_constructor import (
    build_anti_slop_footer,
    render_solo_line_ceiling_rule,
)


def test_rhythm_rule_present_zh_single_line():
    r = render_solo_line_ceiling_rule("zh-CN")
    assert "句法节奏" in r
    assert "单句独段" in r
    assert "\n" not in r  # one clean bullet, not multi-line


def test_rhythm_rule_has_concrete_example():
    # the value-add over a vague rule is the 正反例 (fold beats back)
    r = render_solo_line_ceiling_rule("zh-CN")
    assert "拍点" in r or "并回" in r


def test_rhythm_rule_english_does_not_crash():
    r = render_solo_line_ceiling_rule("en")
    assert "Rhythm" in r and "paragraph" in r.lower()


def test_anti_slop_footer_injects_rhythm_and_drops_permissive_line():
    foot = build_anti_slop_footer("zh-CN")
    assert "句法节奏" in foot, "footer must carry the rhythm ceiling"
    # the old permissive line that licensed staccato must be gone
    assert "允许短句直接落地" not in foot


def test_english_footer_unchanged_shape():
    foot = build_anti_slop_footer("en")
    assert "DO NOT" in foot  # en footer still works (no-op for the zh-only change)
