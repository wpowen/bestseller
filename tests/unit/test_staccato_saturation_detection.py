"""L1 guard for the staccato-saturation detector blind-spot fix.

Root cause (real book 祭词改写后我疯了, every chapter): the chapter is 30-42%
single-sentence paragraphs (the 分镜脚本 AI-tic the reader feels), but the
detector only counted single-sentence paragraphs ≤12 chars AND divided by ALL
paragraphs (dialogue diluting the ratio) → it saw 0-1 spans, scored 0-8, never
fired → never triggered deslop (staccato_saturation IS in DESLOP categories) →
AI-flavor shipped untouched.

Fix: count single-sentence narration paragraphs up to 40 chars, ratio over
NON-dialogue paragraphs only. So a chapter whose narration is mostly one
sentence per paragraph fires; flowing multi-sentence prose does not.
"""

# ruff: noqa: RUF001 — Chinese prose fixtures are the test subject.
from __future__ import annotations

from bestseller.services.ai_flavor.detector import detect


def _has_staccato(text: str) -> bool:
    rep = detect(text, language="zh")
    return any(s.category == "staccato_saturation" for s in rep.spans)


# ~40% single-sentence narration paragraphs of varied length (8-30 chars) —
# the real tic. Medium-length single sentences (>12 chars) must count.
_STACCATO = "\n\n".join(
    [
        "他停在门口。",
        "门内的光从门缝里漏出来，细细一条，像谁用刀在黑暗上划了一道口子。",
        "他没有立刻进去。",
        "走廊尽头那盏灯还亮着，灯丝在嗡嗡地响。",
        "他听见自己的心跳。",
        "三年前那场火之后，他第一次回到这里。",
        "墙皮还是焦的。",
        "空气里有股铁锈味，混着旧纸张受潮发霉的气息。",
        "他攥紧了手里的钥匙。",
        "钥匙是冷的。",
        "他想起母亲临走前塞给他的那句话。",
        "那句话他一直没敢细想。",
        "现在他必须想了。",
        "门后传来一声轻响。",
    ]
)

# Flowing multi-sentence paragraphs — must NOT fire (no over-trigger).
_FLOWING = "\n\n".join(
    [
        "他停在门口，没有立刻进去。门内的光从门缝里漏出来，细细一条，"
        "像谁用刀在黑暗上划了一道口子，而走廊尽头那盏灯还亮着，灯丝嗡嗡作响。",
        "三年前那场火之后，他第一次回到这里。墙皮还是焦的，空气里有股铁锈味，"
        "混着旧纸张受潮发霉的气息，他攥紧了手里那把冰冷的钥匙，想起母亲临走前"
        "塞给他的那句话——那句话他一直没敢细想，可现在他必须想了。",
    ]
    * 3
)


def test_staccato_saturated_chapter_fires():
    assert _has_staccato(_STACCATO), "30-40% single-sentence narration must fire"


def test_flowing_prose_does_not_fire():
    assert not _has_staccato(_FLOWING), "flowing multi-sentence prose must not fire"


def test_medium_length_single_sentences_count():
    """The blind spot: single-sentence paragraphs of 13-30 chars must count
    (the old ≤12-char limit made the dominant pattern invisible)."""
    medium = "\n\n".join(
        [
            "他望着窗外那片在暮色里渐渐模糊下去的远山轮廓。",
            "雨水顺着玻璃蜿蜒而下汇成一道道细小的河流。",
            "他想起很多年前也是这样一个阴沉的黄昏。",
            "母亲站在门口久久没有回头看他一眼。",
            "那时候他还不懂什么叫做永别。",
            "如今他终于懂了却已经太迟。",
            "桌上的茶早就凉透了。",
            "他却一直没有动过那只杯子。",
            "屋子里安静得能听见墙上挂钟的滴答。",
            "每一声都像敲在他绷紧的神经上。",
        ]
    )
    assert _has_staccato(medium), "medium-length single-sentence paragraphs must count"
