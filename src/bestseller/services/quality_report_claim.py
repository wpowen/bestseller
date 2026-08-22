"""一份质量报告评的是**哪一版**草稿。

2026-08-22 真机定罪（《书院笔仙》ch38）：一次修复轮里同一秒落了三份
质量报告——两份评在架的那版（2772 汉字，窗口 1800-3500，OK），一份评
一版**没有上架**的超长稿（3904 汉字，BLOCK_HIGH）。

判定端取「时间上最新的一份报告」，上架端取「最优的一版草稿」，两条
不同的选择规则，于是被丢弃那版的阻断码把一个干净的在架稿锁成了
``production_state=blocked``；重跑多少轮都消不掉——在架稿本来就没有
那个毛病。这是「同一事实住两地，后写的赢」的又一例。

报告表没有任何指向草稿的列，所以下游无从知道一份报告评的是哪一版。
修法是让报告自带被评正文的指纹，读取端按指纹认领。

**两端都要用**：标锁端认错版会误锁（已发生），解锁端认错版会误放
（同一形状，方向相反）。
"""

from __future__ import annotations

# ruff: noqa: RUF002, RUF003 — 中文标点是刻意的。
from collections.abc import Mapping, Sequence
import hashlib
from typing import Any

from bestseller.services.chapter_length_gate import cjk_chars_only, count_zh_chars

# 认领时回看的报告条数。一次修复轮最多落几份报告，10 条足够跨过
# 「同一秒三份」的情形，又不会把很久以前旧稿的报告拉进来。
REPORT_CLAIM_LOOKBACK = 10


def _cjk_only(text: str) -> str:
    """只留 CJK 字符——与 :func:`count_zh_chars` 同一个字符集。

    指纹刻意**不含标点**：同一版稿被标点规范化（全角/半角、行末句号）
    后仍认得出自己，否则指纹会在无关改动上失效、退回「按时间取最新」
    的旧错路径。代价是两版只差标点的稿指纹相同——重写必然改字，实践中
    不会发生。
    """

    return cjk_chars_only(str(text or ""))


def graded_text_fingerprint(text: str) -> dict[str, Any]:
    """被评正文的内容指纹。

    字数走框架唯一那把尺子 :func:`count_zh_chars`（含 CJK 扩展 A 区）。
    自己再写一个正则就是第二套口径——真机上正好差了 1 个字，够让「哪份
    报告评的是哪一版」对不上号。这正是本模块要修的病的微型版。
    """

    body = _cjk_only(text)
    return {
        "chars": count_zh_chars(str(text or "")),
        # 内容寻址用，不是安全用途——显式声明，免得被当成弱哈希告警。
        "sha1": hashlib.sha1(body.encode("utf-8"), usedforsecurity=False).hexdigest()[:16],
    }


def report_grades_text(report_json: Mapping[str, Any] | None, text: str) -> bool:
    """这份报告评的就是 ``text`` 吗？

    旧行没有指纹，一律返回 False：**认不出来比认错版安全**——认错版正是
    这个模块要修的 bug。调用方对认不出来的处理是退回旧行为并留痕。
    """

    graded = dict((report_json or {}).get("graded") or {})
    if not graded:
        return False
    return graded == graded_text_fingerprint(text)


def claim_report_for_draft(
    rows: Sequence[Any], current_draft_text: str | None
) -> tuple[Any | None, str]:
    """从（按时间倒序的）报告行里挑出「评的就是在架稿」的那份。

    返回 ``(row, reason)``。``reason`` 是留痕用的判定依据：

    * ``"claimed"``         — 找到了认领在架稿的报告（可能不是最新那份）。
    * ``"no_claim"``        — 有带指纹的报告，但没有一份评的是在架稿。
    * ``"unfingerprinted"`` — 全是旧行，无从认领。
    * ``"no_draft"``        — 在架稿读不到。
    * ``"empty"``           — 一份报告都没有。

    除 ``"claimed"`` 外一律退回最新那份（旧行为）。
    """

    rows = list(rows)
    if not rows:
        return None, "empty"
    newest = rows[0]
    if current_draft_text is None:
        return newest, "no_draft"
    for row in rows:
        if report_grades_text(getattr(row, "report_json", None), current_draft_text):
            return row, "claimed"
    if any((getattr(row, "report_json", None) or {}).get("graded") for row in rows):
        return newest, "no_claim"
    return newest, "unfingerprinted"
