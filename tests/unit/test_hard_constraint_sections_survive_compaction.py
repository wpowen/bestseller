"""框架先把要求从写手 prompt 里删掉，再因为它没做到而毙掉它。

2026-08-24 真机（书 9）：89 次触发压缩的写手调用里，被淘汰的段落分布是

    【故事圣经上下文】     89  (100%)
    【活动主线/伏笔/回收】 86  ( 97%)
    【章末规则】           68  ( 76%)
    【角色认知规则】       34  ( 38%)

prompt 从 16655 字压到 9859 字，砍掉 41%。而同一批章节被
``ENDING_HOOK_MISSING``（block 级、全书重写头号阻断码）、``foreshadowing_*``、
``canon_coverage`` 判有罪 —— 判它有罪的那几条要求，正是被删掉的那几块。

保护清单里本来就有「章末收尾钩子」「收尾钩子」，但真实段名是**【章末规则】**，
子串对不上就漏了。prompt_compactor 自己的注释早就警告过这个形状：
「把承重规则的存活寄托在别的 marker 的子串巧合上，是压缩器最容易复发的静默失效。」
它复发了。

⚠️ 【故事圣经上下文】排在最后一档是**刻意的**（静态世界信息，pack 与 system
prompt 里另有一份），不在本次修复范围内 —— 别把有据可依的设计当 bug 一起改掉。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
import pathlib
import re

import pytest

from bestseller.services.prompt_compactor import _section_is_protected

pytestmark = pytest.mark.unit

#: 段名里自称「硬约束 / 硬规则 / 硬要求 / 不可更改」的，一律不该参与位置淘汰。
_SELF_DECLARED_HARD = re.compile(r"硬约束|硬规则|硬要求|不可更改")


def _rendered_section_labels() -> set[str]:
    """写手 prompt 真正渲染出来的段名，从源码里抽。"""

    src = pathlib.Path("src/bestseller/services/drafts.py").read_text(encoding="utf-8")
    return set(re.findall(r'"(【[^】]{1,24}】)', src))


def test_the_chapter_ending_rules_section_is_protected() -> None:
    """真机被淘汰 68 次的那一段，且判它有罪的门是 block 级。"""

    assert _section_is_protected("【章末规则】\n章末必须落到未解问题或具体威胁。")


def test_every_self_declared_hard_section_is_protected() -> None:
    """结构守卫：将来谁再加一个自称硬约束的段，忘了进保护清单就会红。"""

    unprotected = sorted(
        label
        for label in _rendered_section_labels()
        if _SELF_DECLARED_HARD.search(label) and not _section_is_protected(label + "\n内容")
    )
    assert not unprotected, f"这些段自称硬约束却会被位置淘汰：{unprotected}"


def test_the_deliberately_last_ranked_sections_stay_evictable() -> None:
    """别把有据可依的设计一起改掉：故事圣经与检索补充仍应可淘汰。"""

    assert not _section_is_protected("【故事圣经上下文】\n世界设定……")
    assert not _section_is_protected("【检索补充】\n参考片段……")


def test_protection_is_matched_on_the_real_label_not_a_lucky_substring() -> None:
    """「收尾钩子」匹配不到「章末规则」——本病的成因，钉死它。"""

    assert not re.search("收尾钩子", "章末规则")
    assert _section_is_protected("【章末规则】\nx")
    assert _section_is_protected("【章末收尾钩子】\nx")
