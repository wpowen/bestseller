"""原地改 `content_md` 的每一处都必须同步 `word_count`——不许再产过期副本。

2026-08-22 定罪：9 处原地修改只有 2 处同步，全库 19% 的稿 word_count 与
实际不符（最大偏差 16688）。后果不是账面难看：ch7 的退化稿（实际 18902
字、记录 2558）靠过期字段在选优排序里赢了「在窗口内」判据、被换回在架稿。

本测试扫描源码钉住这条纪律：pipelines / exports 里每个
`X.content_md = ...` 赋值点，后 6 行内必须出现 word_count 同步
（`resync_draft_word_count` 或直接赋值 `word_count`）。
新增一个不同步的修改点会让本测试变红——这正是它存在的意义。
"""

from __future__ import annotations

# ruff: noqa: RUF002 — 中文标点是刻意的。
import inspect
import re

from bestseller.services import exports, pipelines


def _unsynced_sites(module) -> list[int]:
    lines = inspect.getsource(module).split("\n")
    bad: list[int] = []
    for i, line in enumerate(lines):
        if re.search(r"\.content_md\s*=\s*[^=]", line) and "content_md=content_md" not in line:
            window = "\n".join(lines[i : i + 7])
            if "word_count" not in window:
                bad.append(i + 1)
    return bad


def test_every_pipelines_mutation_resyncs() -> None:
    assert _unsynced_sites(pipelines) == []


def test_every_exports_mutation_resyncs() -> None:
    assert _unsynced_sites(exports) == []


def test_resync_helper_uses_the_one_ruler() -> None:
    """同步必须走 authoritative_word_count_for_language——不许另立口径。"""

    from bestseller.services.drafts import resync_draft_word_count

    src = inspect.getsource(resync_draft_word_count)
    assert "authoritative_word_count_for_language" in src
