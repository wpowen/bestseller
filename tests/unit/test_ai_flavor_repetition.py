"""AI-flavor detection: chapter-level narrative repetition (车轱辘内心戏).

ch004_deslop_e2e.md scored 0 on the detector yet read as exhausting — its
second half repeated the same body sensation (麻/凉/酸/痒钻骨缝) and the same
subtext gloss a dozen times. Sentence/span rules cannot see chapter-wide
repetition; this family quantifies it (meaningful 4-gram repeated ≥5x, or
sensation words stacked) so the chapter routes to a whole-passage deslop.
Calibrated: bad sample = many repeats, good drafts = 0.
"""

from __future__ import annotations

from bestseller.services.ai_flavor.detector import detect


def _rep_spans(text: str) -> list:
    return [s for s in detect(text, language="zh-CN").spans if s.category == "narrative_repetition"]


def test_repeated_ngram_flagged() -> None:
    # Same meaningful phrases hammered many times across the passage.
    text = (
        "他扛住这种酸。骨缝里那种酸。喉结滚了又咽。指尖抖了半分。"
        "他扛住这种凉。骨缝里那种凉。喉结滚了又咽。指尖抖了半分。"
        "他扛住这种痒。骨缝里那种痒。喉结滚了又咽。指尖抖了半分。"
        "他扛住这种麻。骨缝里那种麻。喉结滚了又咽。指尖抖了半分。"
        "扛住这种痛，骨缝里那种痛，喉结滚了又咽，指尖抖了半分。"
    )
    assert _rep_spans(text), "chapter-wide ngram repetition not flagged"


def test_sensation_stack_flagged() -> None:
    assert _rep_spans("麻、凉、酸、痒一层套一层。酸，凉，痒，麻，胀，痛。")


def test_clean_prose_not_flagged() -> None:
    clean = (
        "畦垄土硌脚，鞋底压着一块烂瓦片角。谢迟把袖口往下拽，掩住那块深色渍。"
        "裴萤蹲在他畦前，伸出一根手指拨开苗叶，露出根须第三节。"
        "她声音压得低：青蒿混种，五年份的苗栽进三年份的畦里，本来活不过三天。"
    )
    assert not _rep_spans(clean), "clean prose false-flagged as repetition"


def _near_copy_spans(text: str) -> list:
    return [
        s
        for s in detect(text, language="zh-CN").spans
        if "near_copy" in s.rule_id
    ]


def test_near_copy_line_flagged() -> None:
    # The same ≥8-char description written twice within a short window
    # (ch004's "看旧疤——疤的边缘沾着灰白粉末" repeated 4 paragraphs apart).
    text = (
        "他低头看自己右腕那道旧疤——疤的边缘沾着灰白粉末，粉痕顺着脉门往里渗。"
        "指甲盖底下的血色褪去一层。他手指慢慢攥紧。"
        "他把手背翻过来，盯着那道旧疤——疤的边缘沾着灰白粉末，粉痕顺着脉门往里渗。"
    )
    assert _near_copy_spans(text), "near-copy line not flagged"


def test_far_apart_callback_not_flagged() -> None:
    # The same phrase as a 首尾呼应 (>600 chars apart) is intentional, not AI slop.
    # Filler is a real non-repeating passage (no near-copy of its own).
    filler = (
        "畦垄土硌脚，鞋底压着一块去年烂在根边的瓦片角。辰时光还没过山墙，露水压在青蒿叶面上，"
        "亮得像碎铜钱。考牌官是个黑瘦中年人，袖口别铜符，手里的香刚点上。他扬了扬香头，叫了一声出圃。"
        "软底鞋踩进畦垄，几乎没声响。裴萤蹲在他畦前，距他不到两尺，灰褐短褐，扎紧的衣襟下露出半截手腕，"
        "指缝里沾着研钵残粉，细得发亮。她伸出一根手指拨开第五株苗叶面，露出根须第三节，点在那道弯折上。"
        "谢迟的喉结滚了一下，咽下半个发干的音节。她收回手指，在裤腿上蹭了蹭，残粉簌簌落，几粒沾在土面上。"
        "她从腰间布囊里摸出一只巴掌大的研钵，边缘还沾着几缕药渣，往畦垄上一搁，钵底朝天轻轻磕了一声。"
        "远处考院的钟楼撞了三下，余音顺着夹道的高墙荡开，惊起墙头一只夜鸦，扑棱棱掠过半边天。"
        "他蹲下身，指节抵着青砖缝，泥里那股苦根味又浓了几分，混着夜露的湿气往鼻腔里钻。"
        "月亮升到中天，把整片药圃照得发白，连最末一排矮苗的影子都拉得细长，斜斜搭在田埂上。"
        "巡夜的杂役提着灯笼从西头过来，灯光晃了晃，又拐进另一条岔道，脚步声渐渐听不见了。"
        "夹道尽头的灯火忽明忽暗，像是有人在那头守着，又像只是穿堂的风。"
        "他摸了摸怀里的砚台，缺角的地方硌着肋骨，凉意透过单衣一点点渗进来。"
        "更鼓从城楼方向传来，一长两短，敲过了三更，余响落在空荡的甬道里。"
        "墙根的野草被夜风压得伏倒，又一根根弹起来，叶尖刮过砖面，沙沙作响。"
        "他数着自己的呼吸，把喉咙里那点发紧压下去，肩背一寸寸松开。"
        "檐角铁马被风撞得叮当一声，惊得梁上宿鸟扑翅，又重新缩回了窝。"
        "东边天际隐隐透出一线灰白，离天亮还早，可夜色已经薄了一层。"
    )
    assert len(filler) > 600  # the two callbacks are beyond the near-copy window
    text = "谢迟把袖口往下拽，掩住腕侧那道旧渍。" + filler + "谢迟把袖口往下拽，掩住腕侧那道旧渍。"
    assert not _near_copy_spans(text), "far-apart callback false-flagged"


def test_clean_prose_no_near_copy() -> None:
    clean = (
        "畦垄土硌脚，鞋底压着一块烂瓦片角。裴萤蹲在他畦前，拨开苗叶露出根须第三节。"
        "她声音压得低，指尖点在那道弯折上。谢迟的喉结滚了一下，咽下半个发干的音节。"
    )
    assert not _near_copy_spans(clean), "clean prose false-flagged as near-copy"
