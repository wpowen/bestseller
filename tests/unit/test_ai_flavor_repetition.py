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
