"""选最佳稿时必须现算字数，不能信 `word_count` 列。

2026-08-22 真机定罪（custom-xuanhuan-1787383584 第 7 章）：

    v1  word_count=2558（**过期**，实际 18902，三行各重复 121 遍）
    v3  word_count=5404（真实）
    v5  word_count=5048（真实）

`rank_chapter_draft_candidate` 的第一判据 `in_band` 读的是
`draft.word_count`。于是 v1 靠一个过期字段落进 1800-3500 的窗口、
拿到 `in_band=1`，而两份真实字数超上限的重写稿都是 0——**退化稿因此
胜出，被换回在架稿**。

这个函数注释里已经记着同款事故（《纸背》4942 字对 2600 目标，2026-07-26），
上一次修加了 ceiling 判据，**但没修字数的来源**。同一个坑的第二次。

`word_count` 是 `content_md` 的副本，而 9 个原地修改 content_md 的地方
只有 2 个同步了它——全库 19% 的稿字数记录与实际不符（《书院笔仙》
276 稿里 52 稿不符，最大偏差 16688）。所以排序判据必须现算。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002 — 中文标点是刻意的。
from types import SimpleNamespace

from bestseller.services.pipelines import rank_chapter_draft_candidate

_BAND = {"hard_min": 1800, "hard_max": 3500, "target_words": 2600}


def _draft(*, stored: int, real_chars: int, version: int) -> SimpleNamespace:
    """word_count 与正文长度可以不一致——真机上 19% 的稿就是这样。"""

    return SimpleNamespace(
        word_count=stored,
        content_md="字" * real_chars,
        version_no=version,
    )


def test_stale_word_count_cannot_win_the_in_band_term() -> None:
    """真机原样：v1 记 2558 实为 18902，不许赢过真实超长的 v3。"""

    degenerate = _draft(stored=2558, real_chars=18902, version=1)
    rewritten = _draft(stored=5404, real_chars=5404, version=3)
    quality = SimpleNamespace(score_overall=0.5)

    deg = rank_chapter_draft_candidate(degenerate, quality, **_BAND)
    rew = rank_chapter_draft_candidate(rewritten, quality, **_BAND)
    assert deg[0] == 0, "18902 字的稿不该被算作『在窗口内』"
    assert deg[0] == rew[0], "两份都超上限，in_band 应当同为 0"


def test_a_genuinely_in_band_draft_still_wins() -> None:
    """现算不改变正常情形：真正在窗口内的稿仍然胜出。"""

    good = _draft(stored=2600, real_chars=2600, version=1)
    long_one = _draft(stored=4200, real_chars=4200, version=2)
    quality = SimpleNamespace(score_overall=0.5)

    assert rank_chapter_draft_candidate(good, quality, **_BAND)[0] == 1
    assert rank_chapter_draft_candidate(long_one, quality, **_BAND)[0] == 0


def test_missing_content_falls_back_to_the_stored_count() -> None:
    """读不到正文时退回旧行为，不因为取不到内容就把稿判死。"""

    no_body = SimpleNamespace(word_count=2600, content_md=None, version_no=1)
    assert rank_chapter_draft_candidate(no_body, None, **_BAND)[0] == 1
