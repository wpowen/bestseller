"""character_evolution 从零引用接入生成端——角色停滞提示（只提示，不杀）。

2026-08-22 定罪：`character_evolution`（230 行）提供人物知识状态 / 演化
时间线 / **停滞检测** / 关系演化——正是「角色性格与成长」那套能力，
在 src / scripts / tests 里**零引用**。它是建在 character_state_snapshots
与 relationship_events 之上的读取层，而这两张表直到今天才被修活
（快照脱离恒假条件、feedback 抽取复活），所以它此前接了也没数据。

接法照抄人际承诺块的既有形状：story_bible 上下文出 payload →
drafts 渲染成**软提示块**进写手 prompt。按铁律：新能力只挣提示与留痕，
不发杀权——停滞不是缺陷，是给写手的信息。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
import inspect

from bestseller.domain.contradiction import CharacterStagnationWarning
from bestseller.services.character_evolution import render_stagnation_block


def _w(name: str, since: int, fields: list[str]) -> CharacterStagnationWarning:
    return CharacterStagnationWarning(
        character_name=name,
        last_update_chapter=10,
        chapters_since_update=since,
        stagnant_fields=fields,
    )


def test_block_names_characters_and_stays_soft() -> None:
    block = render_stagnation_block(
        [_w("沈昭", 7, ["arc_state", "emotional_state"])], language="zh-CN"
    )
    assert "沈昭" in block
    assert "7" in block
    # 软约束措辞：允许写手不动它，禁止硬塞
    assert "不要生硬" in block or "自然" in block
    # 不许出现任何门禁/阻断措辞
    for hard in ("必须", "阻断", "禁止跳过"):
        assert hard not in block


def test_empty_warnings_render_nothing() -> None:
    assert render_stagnation_block([], language="zh-CN") == ""


def test_block_caps_at_three_characters() -> None:
    """prompt 预算有限（10k 上限有淘汰），最多点名 3 人。"""

    warnings = [_w(f"甲乙丙{i}", 6 + i, ["arc_state"]) for i in range(6)]
    block = render_stagnation_block(warnings, language="zh-CN")
    # 数「（已 」出现次数——数名字会撞上标题里的「角色」二字（第一版就撞了）
    assert block.count("（已 ") == 3


def test_context_builder_produces_the_payload() -> None:
    from bestseller.services import story_bible

    src = inspect.getsource(story_bible)
    assert "detect_character_stagnation" in src
    assert '"character_stagnation"' in src


def test_writer_prompt_renders_it_next_to_promises() -> None:
    from bestseller.services import drafts

    src = inspect.getsource(drafts)
    anchor = src.index("render_promises_block(")
    window = src[anchor : anchor + 4000]
    assert "render_stagnation_block" in window
