"""L1 guard: blurb 立意↔调性一致 cap (P0-2).

Real failure (祭词改写后我疯了): a high-concept cosmic-horror premise (read a
rule → lose sanity → ascending = becoming the monster) sold by a blurb stuffed
with 爽文 cliché beats (打脸全村跪地/逆袭碾压漫天邪神) — tonal betrayal, reads
cheap, repels the target reader. The gate now caps such a mismatch below the bar,
WITHOUT punishing pure 爽文 (where clichés are the legit selling point).
"""

# ruff: noqa: RUF001 — Chinese fixtures are the subject.
from __future__ import annotations

from bestseller.services.blurb_appeal_gate import evaluate_blurb_appeal
from bestseller.services.story_appeal import load_story_appeal_config, resolve_genre_lexicon

_CFG = load_story_appeal_config()


def _eval(title, syn, prem, genre="xuanhuan"):
    return evaluate_blurb_appeal(
        title=title, synopsis=syn, premise=prem, tags=None, genre=genre,
        config=_CFG, lexicon=resolve_genre_lexicon(genre, None),
    )


_SERIOUS_PREMISE = (
    "祭品改写祭词召来司辰，以献祭换能读不可名状之物的祀簿，每读一条规则降一截理智，"
    "代价是不可逆的自我异化，封神即成为自己曾对抗的怪物。"
)
# Strong, well-formed blurb (passes surface dims) BUT 爽文-cliché-saturated —
# this is the case the cap exists for (a mismatch that would otherwise score ≥80).
_CLICHE_BLURB = (
    "他睁眼的瞬间，族谱上自己的死期正在燃烧。手撕献词反杀神坛，请神上身碾邪祟，"
    "打脸全村跪地喊冤；傩面加身镇诡异，逆袭碾压漫天邪神。可救下的哑童瞳孔分裂，"
    "低声道：盯上你的从不是它，下一只已顺着他的理智裂缝爬了进来。"
)
_CLEAN_BLURB = (
    "他每读懂一条规则，就忘掉一段自己是谁。司辰把最后一道符按进识海，身后传来熟悉的脚步——"
    "那是三天前他亲手埋掉的人。再解一道，他将再也认不出镜子里的脸。"
)


def test_serious_premise_with_cliche_blurb_is_capped():
    v = _eval("吞我者亡", _CLICHE_BLURB, _SERIOUS_PREMISE)
    assert any("错配" in f for f in v.findings), "tone mismatch must be flagged"
    cap = float(_CFG["tone_consistency"]["tone_cap"])
    assert v.total <= cap, f"mismatch must cap ≤{cap}, got {v.total}"


def test_serious_premise_with_clean_blurb_not_tone_capped():
    v = _eval("吞我者亡", _CLEAN_BLURB, _SERIOUS_PREMISE)
    assert not any("错配" in f for f in v.findings), "clean serious blurb must not tone-cap"


def test_pure_shuangwen_not_capped():
    # 爽文 premise (no serious signals) → clichés are legit → no tone cap.
    v = _eval(
        "全球高武我无敌",
        "他一拳打脸全村，反手碾压仇敌，废物逆袭震惊全场，跪地求饶的正是当年退婚的未婚妻。",
        "都市废物觉醒最强系统，打脸逆袭，碾压一切看不起他的人，签到变强一路无敌。",
        genre="urban",
    )
    assert not any("错配" in f for f in v.findings), "pure 爽文 must not be tone-capped"
