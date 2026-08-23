"""重写指令里报的目标字数必须落在它自己要求的区间内。

真机（书 9，2026-08-24）抓到的原话：

    最终正文必须落在 2820-3068 个有效中文汉字，目标约 2600 字。

区间根本不含目标 —— 模型听哪一句都会违反另一句。10 条带字数闸门的重写指令里
有 6 条是这个形状，而这些章正是反复因 LENGTH 触发新一轮重写的那些：写到 2600
就再次 LENGTH_UNDER，再触发一次扩写收敛，指令还是这句话。

成因：扩写模式把安全区整体抬到目标之上（``safe_min = hard_target + 220``），
而 ``hard_target`` 原样不动，渲染时直接印了出来。安全区是刻意的，不动；
只把**指令里报的目标**夹回区间内。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
import pytest

from bestseller.services.word_targets import RewriteLengthBand

pytestmark = pytest.mark.unit


def _band(**over: int) -> RewriteLengthBand:
    base = dict(
        hard_min=2000, hard_target=2600, hard_max=3200,
        safe_min=2314, safe_max=2886, model_output_chars=None,
    )
    base.update(over)
    return RewriteLengthBand(**base)  # type: ignore[arg-type]


def test_the_real_machine_contradiction_is_gone() -> None:
    """真机原样：区间 2820-3068、目标 2600。"""

    band = _band(safe_min=2820, safe_max=3068)
    assert band.instruction_target == 2820
    assert band.safe_min <= band.instruction_target <= band.safe_max


def test_a_target_already_inside_the_band_is_untouched() -> None:
    """正常模式不受影响：目标本来就在区间里，原样输出。"""

    band = _band()
    assert band.instruction_target == band.hard_target == 2600


def test_a_compression_band_below_the_target_clamps_down() -> None:
    """对称情形：压缩模式把区间整体压到目标之下，同样要夹回来。"""

    band = _band(safe_min=1900, safe_max=2200)
    assert band.instruction_target == 2200
    assert band.safe_min <= band.instruction_target <= band.safe_max


@pytest.mark.parametrize(
    ("safe_min", "safe_max", "hard_target"),
    [(2820, 3068, 2600), (2314, 2886, 2600), (1900, 2200, 2600), (2600, 2600, 2600)],
)
def test_the_instruction_target_is_always_satisfiable(
    safe_min: int, safe_max: int, hard_target: int
) -> None:
    """不变式：报出去的目标永远落在报出去的区间内。"""

    band = _band(safe_min=safe_min, safe_max=safe_max, hard_target=hard_target)
    assert safe_min <= band.instruction_target <= safe_max
