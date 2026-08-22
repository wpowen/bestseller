"""报告必须说明「它评的是哪一版草稿」。

2026-08-22 真机定罪：《书院笔仙》第 38 章被标 blocked，而它的在架稿
（v4，2772 汉字，窗口 1800-3500）完全合格、叙述层含「我」0%。锁来自
**没有上架的 v5**（3904 汉字，BLOCK_HIGH）——同一秒落了三份报告，
判定端取「时间上最新的一份」，上架端取「最优的一版」，两条不同的
选择规则，于是「最新报告」≠「在架稿的报告」。

这是「同一事实住两地，后写的赢」的又一例。报告表没有任何指向草稿的
列，所以下游根本无法知道一份报告评的是哪一版——修法是让报告自带被评
正文的指纹，读取端按指纹认领。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
from bestseller.services.chapter_length_gate import count_zh_chars
from bestseller.services.quality_report_claim import (
    claim_report_for_draft,
    graded_text_fingerprint,
    report_grades_text,
)


def test_fingerprint_is_stable_and_content_addressed() -> None:
    text = "他推开门，风灌了进来。"
    assert graded_text_fingerprint(text) == graded_text_fingerprint(text)
    assert graded_text_fingerprint(text) != graded_text_fingerprint(text + "他站住了。")


def test_fingerprint_ignores_punctuation_by_design() -> None:
    """只认汉字，标点不进指纹——这是刻意的取舍，不是疏漏。

    好处：同一版稿被标点规范化（全角/半角、行末句号）后仍认得出自己，
    否则指纹会在无关改动上失效、退回「按时间取最新」的旧错路径。
    代价：两版只差标点的稿指纹相同——但重写必然改字，实践中不会发生。
    """

    assert graded_text_fingerprint("他推开门") == graded_text_fingerprint("他推开门。")


def test_fingerprint_counts_han_characters_not_raw_length() -> None:
    """字数一律按汉字数——全文长度含标点，跨口径比较会整体偏移。"""

    assert graded_text_fingerprint("一二三四五")["chars"] == 5
    assert graded_text_fingerprint("一二三四五！！！！！")["chars"] == 5


def test_report_claims_only_the_draft_it_actually_graded() -> None:
    shipped = "在架的稿子，它是干净的。" * 20
    rejected = shipped + "一段没有上架的超长续写。" * 40

    report_on_rejected = {"graded": graded_text_fingerprint(rejected)}
    assert report_grades_text(report_on_rejected, rejected) is True
    assert report_grades_text(report_on_rejected, shipped) is False


def test_legacy_reports_without_fingerprint_are_not_claimed() -> None:
    """旧行没有指纹。**不认领**比瞎认领安全——认错版就是这个 bug 本身。

    读取端对「无法认领」的处理是退回旧行为并留痕，而不是在这里假装匹配。
    """

    assert report_grades_text({"blocking_codes": ["LENGTH_OVER"]}, "任何正文") is False
    assert report_grades_text({}, "") is False


class _Row:
    """报告行的最小替身——只有认领逻辑用到的那一个属性。"""

    def __init__(self, report_json: dict[str, object] | None) -> None:
        self.report_json = report_json


def _report(text: str, codes: list[str]) -> _Row:
    return _Row({"blocking_codes": codes, "graded": graded_text_fingerprint(text)})


def test_claims_the_report_for_the_shipped_draft_not_the_newest() -> None:
    """ch38 的真实形状：同一轮里三份报告，最新那份评的是没上架的稿。"""

    shipped = "干净的在架稿。" * 60
    rejected = shipped + "超长的续写。" * 200

    # 按时间倒序：最新的是评被丢弃稿的那份 BLOCK_HIGH。
    rows = [
        _report(rejected, ["LENGTH_OVER", "CHAPTER_LENGTH_BLOCK_HIGH"]),
        _report(shipped, []),
        _report(shipped, []),
    ]
    claimed, reason = claim_report_for_draft(rows, shipped)
    assert reason == "claimed"
    assert claimed is not rows[0]
    assert claimed.report_json["blocking_codes"] == []


def test_falls_back_to_newest_when_nothing_claims_the_draft() -> None:
    rows = [_report("完全不相干的另一版正文。" * 30, ["POV_DRIFT"])]
    claimed, reason = claim_report_for_draft(rows, "在架稿的正文。" * 30)
    assert reason == "no_claim"
    assert claimed is rows[0]


def test_legacy_rows_fall_back_to_newest_and_say_so() -> None:
    """旧数据没有指纹——必须退回旧行为，且理由可区分于「有指纹但没认领」。"""

    rows = [_Row({"blocking_codes": ["LENGTH_UNDER"]}), _Row({"blocking_codes": []})]
    claimed, reason = claim_report_for_draft(rows, "任何正文")
    assert reason == "unfingerprinted"
    assert claimed is rows[0]


def test_missing_draft_or_no_reports_never_raises() -> None:
    rows = [_report("正文", [])]
    assert claim_report_for_draft(rows, None) == (rows[0], "no_draft")
    assert claim_report_for_draft([], "正文") == (None, "empty")


def test_no_op_on_single_report_runs_the_common_case_unchanged() -> None:
    """绝大多数章只有一份报告——这条路径必须与修复前逐字等价。"""

    text = "只有一版稿。" * 40
    only = _report(text, ["POV_DRIFT"])
    assert claim_report_for_draft([only], text) == (only, "claimed")
    stale = _Row({"blocking_codes": ["POV_DRIFT"]})
    assert claim_report_for_draft([stale], text) == (stale, "unfingerprinted")


def test_fingerprint_word_count_matches_the_frameworks_one_ruler() -> None:
    """指纹的字数必须与 ``count_zh_chars`` 逐字相同——不许有第二套口径。

    真机上这两者曾差 1 个字：框架的字符集含 CJK 扩展 A 区（㐀-䶿），
    我在指纹里自己写的正则只有基本区。1 个字就足以让「哪份报告评的是
    哪一版」对不上号——而对不上号正是这个模块要修的病。
    """

    text = "他推开门㐀，风灌了进来。abc 123"
    assert graded_text_fingerprint(text)["chars"] == count_zh_chars(text)
    assert graded_text_fingerprint("㐀㐁㐂")["chars"] == 3
