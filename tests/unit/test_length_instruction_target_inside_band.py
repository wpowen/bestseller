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

import re
from uuid import uuid4

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


def test_the_band_declares_itself_the_single_authority_on_length() -> None:
    """判官会自己发明字数命令，与权威闸门撞车。

    真机：34 条带商业判官整改方案的重写指令里，10 条含判官自拟的字数数字，
    **这 10 条全部同时挂着字数收敛闸门**——每次报数字都撞车。判官的整改方案是
    自由文本，逐句过滤会误伤它其它有用的意见；改为由闸门声明优先级。

    ⚠️ 这条测试**真渲染一次**再断言，不断言源码字符串——后者不执行代码，
    本项目已因此吃过假绿。
    """

    from bestseller.infra.db.models import ChapterModel, RewriteTaskModel
    from bestseller.services.reviews import _render_recent_length_failure_directive

    chapter = ChapterModel(
        project_id=uuid4(),
        chapter_number=7,
        title="断墨",
        chapter_goal="推进",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=2600,
    )
    failure = RewriteTaskModel(
        project_id=chapter.project_id,
        trigger_type="quality_gate",
        rewrite_strategy="chapter",
        status="failed",
        metadata_json={
            "candidate_word_count": 1773,
            "candidate_quality_gate_violations": [{"code": "LENGTH_UNDER"}],
        },
    )

    text = _render_recent_length_failure_directive(
        [failure], chapter=chapter, language="zh", project=None
    )

    assert text, "渲染结果不该为空"
    assert "唯一权威" in text, text
    band = re.search(r"落在 (\d+)-(\d+)", text)
    target = re.search(r"目标约 (\d+)", text)
    assert band and target, text
    assert int(band.group(1)) <= int(target.group(1)) <= int(band.group(2)), text
