"""R1: cross-paragraph staccato-saturation detection.

The existing ``cn.rhythm.choppy`` rule is paragraph-INTERNAL — it needs
≥3 sentences inside one paragraph. The dominant model-ism in real output
is the opposite: each short sentence gets its OWN paragraph, so every
paragraph holds one sentence and the choppy rule never fires. This pass
measures the chapter-level saturation of solo short narration lines plus
the mechanical repetition of sentence-initial subjects.
"""

from __future__ import annotations

from bestseller.services.ai_flavor.detector import detect


def _score(text: str) -> tuple[float, list[str]]:
    report = detect(text, language="zh")
    cats = [s.category for s in report.spans]
    return report.overall_score, cats


def test_solo_line_saturation_fires_warn() -> None:
    # 14 solo short narration lines in a row — pathological staccato.
    text = "\n\n".join(
        [
            "油灯在木架上跳了一下。",
            "灯芯爆了一声。",
            "那只手正在涂死一行字。",
            "他伸手去拨墨碟。",
            "册页被塞进袖袋。",
            "可苏岫没抽。",
            "他只塞。",
            "谢迟的心一沉。",
            "窗栓被拨开。",
            "他没有第二条路。",
            "他没带笔杆。",
            "他只带了砚。",
            "苏岫的肩胛骨一僵。",
            "苏岫的眉心一皱。",
        ]
    )
    score, cats = _score(text)
    assert "staccato_saturation" in cats, cats
    assert score > 0


def test_healthy_prose_does_not_fire() -> None:
    # Long-short interleaved, full sentences with subjects — clean.
    text = (
        "焦糊味裹着湿灰从塌了半边的义仓地窖口灌进来，呛得人睁不开眼，"
        "谢迟把妹妹往墙角又挪了挪，才敢回头看那束手电光。\n\n"
        "卫荆站在光里，灰绳束腰，旧布包袱压得很低，像是怕被什么人看见，"
        "他第一眼没看谢迟，先看了躺在旧袄上的谢萤一眼，又把目光收了回来。\n\n"
        "“活不过今晚。”卫荆的声音很低，像在报一笔旧账。"
    )
    score, cats = _score(text)
    assert "staccato_saturation" not in cats, cats


def test_few_solo_lines_are_allowed() -> None:
    # A couple of punchy beats inside otherwise flowing prose: legit device.
    text = (
        "他把那方缺角古砚从怀里掏出来，指尖在豁口上来回磨着，"
        "磨到指腹发烫也没停，砚池里那滴血被吞得比心跳还快。\n\n"
        "齐了。\n\n"
        "他长出一口气，把砚重新揣回怀里，又伸手探了探妹妹的额头，"
        "那点温度还在，他这才靠着焦黑的梁木坐下来。"
    )
    score, cats = _score(text)
    assert "staccato_saturation" not in cats, cats


def test_mechanical_subject_repetition_fires() -> None:
    # Same opening subject "他" hammered across consecutive solo lines.
    text = "\n\n".join(
        [
            "他没接话。",
            "他没退。",
            "他只塞。",
            "他没带笔杆。",
            "他只带了砚。",
            "他听见自己的心跳。",
        ]
    )
    score, cats = _score(text)
    assert "staccato_saturation" in cats, cats
