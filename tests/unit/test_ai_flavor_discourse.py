"""Discourse-level AI-flavor backstop (the layer prompt alone can't kill).

Real-generation polishing (2026-06-13) showed the writer prompt drives the
headline tells to ~0 but a few *sticky* discourse habits survive every prompt
revision, single-sample to single-sample:

* 「他没X」negative-action stoicism used as a reaction filler (没动声/没抬头);
* anonymous crowd-reaction beats repeated (堂上有人吸气/哦/啊…);
* the 不是X，是Y / 不是X而是Y "negated definition" (esp. the comma-是 variant);
* omniscient info-narration markers (那是…的后遗症 / 没人看见… / X是…分下来的).

These are detected as *advisory* signals (warn, no auto-delete — deleting
content words breaks grammar) and capped so they can never alone force a
block. Density-gated where the construct is legitimate in moderation.
"""

from __future__ import annotations

from bestseller.services.ai_flavor.detector import detect


def _cats(text: str) -> list[str]:
    return [s.category for s in detect(text, language="zh").spans]


def test_negative_action_filler_density_fires() -> None:
    text = (
        "他没动声。他没抬头。他没接话。他没回头。他没去看她。他没松手。"
    )
    assert "negative_action_filler" in _cats(text)


def test_few_negative_actions_allowed() -> None:
    # One or two — legitimate (often a loaded, reverse-expectation choice).
    text = "众人都等他研那方公用砚。他没研。他把自己的旧砚按在了苗根上。"
    assert "negative_action_filler" not in _cats(text)


def test_crowd_reaction_beat_repetition_fires() -> None:
    text = (
        "堂上有人吸气。\n\n谢迟走上前。\n\n堂上有人哦了一声。\n\n"
        "他按下砚台。\n\n堂上有人啊了一声。"
    )
    assert "crowd_reaction_beat" in _cats(text)


def test_negated_definition_comma_shi_variant_fires() -> None:
    # The sticky variant without 而: 不是X，是Y.
    text = "墨色浮上来。不是寻常的黑，是一缕暗金的浓。"
    assert "negated_definition" in _cats(text)


def test_info_narration_markers_fire() -> None:
    assert "info_narration" in _cats("那是她替人挡了一记符箭的后遗症。")
    assert "info_narration" in _cats("没人看见她每夜往土里埋符灰。")


def test_clean_cinematic_prose_not_flagged() -> None:
    text = (
        "殷泱的话头停了。那只要落黜落印的手抬到一半，没再落下，腰间佩玉还在晃。"
        "墨从砚底渗出来，沿着苗根往上爬，爬过第一片叶。"
    )
    cats = _cats(text)
    for c in ("negative_action_filler", "crowd_reaction_beat", "info_narration", "negated_definition"):
        assert c not in cats, (c, cats)
