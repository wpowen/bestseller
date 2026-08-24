"""source-bound 的 BookSpec 不许被 LLM 名字覆盖掉快照里的主角。

2026-08-25 真机（custom-xuanhuan-1787590978，cost_style=minimal）：书卡在
`needs_replan`，已重试 2 次，原因 `protagonist_identity_mismatch`：

    book_design_snapshot.protagonist  = 纪辙   （锁定快照，status=locked）
    creation_protagonist_name         = 纪辙
    book_spec.protagonist             = 沈砚舟  ← 矛盾
    cast_spec.protagonist             = 沈砚舟  ← 从 book_spec 继承

同一次规划里（全部产物 01:23:42），world_spec 打了 `source_bound_design=true`，
book_spec 却**没有** source_compiler 元信息——两个编译器对「这本书是不是
source-bound」给出了不同答案。

追下去不是判据不一致（两处都用 `_source_bound_cast_enabled`），而是**覆盖发生
在判断之前**：

    book_spec_fallback = _compile_source_bound_book_spec(...)  # protagonist=纪辙
    book_spec_fallback["protagonist"]["name"] = llm_protagonist_name   # ← 无条件覆盖
    if _source_bound_cast_enabled(project):
        book_spec_payload = book_spec_fallback      # ← 用的正是被覆盖过的那份

那行覆盖的本意是「让 LLM 调用在兜底上下文里看到同一个名字」——只对**走 LLM 的
那条路**有意义。source-bound 根本不调 LLM，却照样被覆盖，于是快照绑定被推翻，
身份门正确开火把书卡住。
"""

from __future__ import annotations

import re
from pathlib import Path

import bestseller.services.planner as planner_mod

_SRC = Path(planner_mod.__file__).read_text(encoding="utf-8")


def _overwrite_sites() -> list[int]:
    return [
        no
        for no, line in enumerate(_SRC.split("\n"), start=1)
        if 'book_spec_fallback["protagonist"]["name"] = llm_protagonist_name' in line
    ]


def test_the_overwrite_sites_exist() -> None:
    """守卫不许静默空转——找不到覆盖点说明代码结构变了，需要重看这条。"""

    assert _overwrite_sites(), "找不到 protagonist 覆盖点，本守卫已失效"


def test_the_overwrite_is_guarded_by_the_source_bound_check() -> None:
    """每一处覆盖都必须挂在「非 source-bound」的条件下。

    source-bound 不调 LLM，覆盖对它没有任何意义，只会推翻快照绑定。
    """

    lines = _SRC.split("\n")
    unguarded = []
    for no in _overwrite_sites():
        window = "\n".join(lines[max(0, no - 8) : no])
        if "_source_bound_cast_enabled" not in window:
            unguarded.append(no)
        elif not re.search(r"if\s+not\s+_source_bound_cast_enabled", window):
            unguarded.append(no)
    assert not unguarded, (
        "这些覆盖点没有被 `if not _source_bound_cast_enabled(...)` 守住："
        + ", ".join(f"planner.py:{n}" for n in unguarded)
    )
