"""伪精确计量检测（2026-08-08 画面感深度调研固化）。

语料定罪（.distillation_private 4494 万字真实出版章）：
* 动作+半寸/半分/半尺：0 命中 → 硬 AI 标记；
* 停顿/动作+N秒：0.7/百万字；拧/转半圈：0.3/百万字；停顿N拍≈0（仅「慢半拍」熟语）；
* 但「退一步」9.5/百万、「敲了两下」7.0/百万、「半晌」14.6/百万、「数息」25.2/百万
  均为人类正常写法 → 必须豁免（词表误报老坑，见 lexical-proxy 教训）。

规则全部 advisory warn（threshold=1），归 pseudo_precision 类，供 deslop 定向改写。
"""

from __future__ import annotations

from bestseller.services.ai_flavor.detector import detect
from bestseller.services.deslop_revise import _EXTRA_SELF_CHECK


def _cats(text: str) -> list[str]:
    return [s.category for s in detect(text, language="zh").spans]


PAD = "他把窗关上，回头看了一眼灶台，锅里的水已经开了，白汽顶得锅盖啪啪作响。"


def test_amplitude_measurement_flags() -> None:
    text = PAD + "少年把工资条往前推了半寸，纸边擦过桌沿。" + PAD
    assert "pseudo_precision" in _cats(text)


def test_clock_time_on_pause_flags() -> None:
    text = PAD + "他站了三秒才动，随后拉开门闩。" + PAD
    assert "pseudo_precision" in _cats(text)


def test_half_turn_flags() -> None:
    text = PAD + "他捏住钥匙拧了半圈，锁芯没有动静。" + PAD
    assert "pseudo_precision" in _cats(text)


def test_human_normal_forms_exempt() -> None:
    # 人类正常写法逐一豁免：整步移动 / 敲击计数 / 模糊时长 / 慢半拍熟语 / 数息 / 半分钟
    text = (
        PAD
        + "他后退一步，靠上门框。她抬手在门板上敲了两下。他站了半晌没言语，"
        + "反应总是慢了半拍。数息之间刀已出鞘。他等了半分钟才拨通电话。"
        + PAD
    )
    assert "pseudo_precision" not in _cats(text)


def test_dialogue_exempt() -> None:
    text = PAD + "他把杯子墩在桌上：“你给我往前挪半寸试试！”" + PAD
    assert "pseudo_precision" not in _cats(text)


def test_deslop_self_check_mentions_pseudo_precision() -> None:
    assert "伪精确计量" in _EXTRA_SELF_CHECK
    assert "18 条" in _EXTRA_SELF_CHECK


def test_pseudo_precision_triggers_deslop_rewrite() -> None:
    """检出但不在触发集 = 检测了从不清理（R9 老坑）。

    伪精确没有静态替换可用（要按上下文改成瞬时态或后果，常需重排半句），
    只有整段 deslop 重写能治；人类≈0 命中故罕见即触发划算。
    """
    from bestseller.services.ai_flavor_gate import DESLOP_DISCOURSE_CATEGORIES

    assert "pseudo_precision" in DESLOP_DISCOURSE_CATEGORIES
