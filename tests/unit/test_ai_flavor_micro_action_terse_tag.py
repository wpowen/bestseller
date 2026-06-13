"""R2: body micro-action template over-reliance + terse bare dialogue tags.

Two second-family model-isms the goal names explicitly:

* compound micro-action templates ("瞳孔缩了一下" / "眼皮掀了一下又合上" /
  "心一沉" / "眉心一皱") repeated as the default emotion delivery, and
* terse bare dialogue tags ("「冷。」他说") — a one-word emotional line
  glued to a bare attribution with no action beat.

Both are *density* tells: one use is fine, a chapter full of them reads
like a model. Detection is soft (warn), profile-free, dialogue-aware.
"""

from __future__ import annotations

from bestseller.services.ai_flavor.detector import detect


def _cats(text: str) -> list[str]:
    return [s.category for s in detect(text, language="zh").spans]


def test_micro_action_template_over_reliance_fires() -> None:
    # 4 compound body micro-actions in narration → over-reliance.
    text = (
        "他翻开那本旧册，瞳孔一缩。\n\n"
        "灯花爆了一下，他心头一紧，没敢出声。\n\n"
        "对方报出那个名字时，他眉心一皱。\n\n"
        "听到脚步声近了，他喉结一滚，把册子塞回袖里。\n\n"
        "门被推开的瞬间，他指节一僵。"
    )
    cats = _cats(text)
    assert "body_micro_action" in cats, cats


def test_single_micro_action_is_allowed() -> None:
    # One legit micro-action surrounded by grounded prose: not flagged.
    text = (
        "他翻开那本旧册，指尖在父亲那一行名字上停了停，"
        "瞳孔一缩，随即把册子合上，借着灯影把它压进袖底，"
        "又抬眼朝门口看了一眼，外头还是那条空巷。"
    )
    cats = _cats(text)
    assert "body_micro_action" not in cats, cats


def test_terse_bare_dialogue_tag_cluster_fires() -> None:
    # 3 one-word lines + bare attribution, no action beat → model-ism.
    text = (
        "“冷。”他说。\n\n"
        "“滚。”她道。\n\n"
        "“快。”他说。"
    )
    cats = _cats(text)
    assert "terse_dialogue_tag" in cats, cats


def test_normal_dialogue_with_beats_not_flagged() -> None:
    # Real lines with action beats / longer content: clean.
    text = (
        "“活不过今晚。”卫荆的声音很低，像在报一笔旧账。\n\n"
        "谢迟没接话，只把砚台往腕骨又压了压。\n\n"
        "“你怀里那块破石头，救不了她第二次。”卫荆又说。"
    )
    cats = _cats(text)
    assert "terse_dialogue_tag" not in cats, cats
