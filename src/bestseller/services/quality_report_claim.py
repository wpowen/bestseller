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
import re
from typing import Any

from bestseller.services.chapter_length_gate import count_zh_chars

# 认领时回看的报告条数。一次修复轮最多落几份报告，10 条足够跨过
# 「同一秒三份」的情形，又不会把很久以前旧稿的报告拉进来。
REPORT_CLAIM_LOOKBACK = 10


FINGERPRINT_VERSION = 2
_WHITESPACE = re.compile(r"\s+")


def _normalized_text(text: str) -> str:
    """Normalize layout whitespace while retaining all content characters.

    The previous fingerprint kept only CJK characters, so English and numeric
    changes could collide.  Version 2 deliberately hashes the complete text;
    only whitespace runs are normalized because line wrapping is not content.
    """

    return _WHITESPACE.sub(" ", str(text or "")).strip()


def graded_text_fingerprint(text: str) -> dict[str, Any]:
    """被评正文的内容指纹。

    字数走框架唯一那把尺子 :func:`count_zh_chars`（含 CJK 扩展 A 区）。
    自己再写一个正则就是第二套口径——真机上正好差了 1 个字，够让「哪份
    报告评的是哪一版」对不上号。这正是本模块要修的病的微型版。
    """

    return {
        "version": FINGERPRINT_VERSION,
        "chars": count_zh_chars(str(text or "")),
        # 内容寻址用，不是安全用途——显式声明，免得被当成弱哈希告警。
        "sha1": hashlib.sha1(
            _normalized_text(text).encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:16],
    }


def report_grades_text(report_json: Mapping[str, Any] | None, text: str) -> bool:
    """这份报告评的就是 ``text`` 吗？

    旧行没有指纹，一律返回 False：**认不出来比认错版安全**——认错版正是
    这个模块要修的 bug。未知来源只能展示历史，不能授权修复或放行。
    """

    if not isinstance(report_json, Mapping):
        return False
    graded = report_json.get("graded")
    if not isinstance(graded, Mapping) or not graded:
        return False
    return graded == graded_text_fingerprint(text)


def claim_report_for_draft(
    rows: Sequence[Any], current_draft_text: str | None, *, allow_historical: bool = False
) -> tuple[Any | None, str]:
    """从（按时间倒序的）报告行里挑出「评的就是在架稿」的那份。

    返回 ``(row, reason)``。``reason`` 是留痕用的判定依据：

    * ``"claimed"``         — 找到了认领在架稿的报告（可能不是最新那份）。
    * ``"no_claim"``        — 有带指纹的报告，但没有一份评的是在架稿。
    * ``"unfingerprinted"`` — 全是旧行，无从认领。
    * ``"no_draft"``        — 在架稿读不到。
    * ``"empty"``           — 一份报告都没有。

    除 ``"claimed"`` 外返回 None。展示历史的只读调用方可以显式传入
    ``allow_historical=True``，但必须保留 reason，不能用于修复或晋升。
    """

    rows = list(rows)
    if not rows:
        return None, "empty"
    newest = rows[0] if allow_historical else None
    if current_draft_text is None:
        return newest, "no_draft"
    for row in rows:
        if report_grades_text(getattr(row, "report_json", None), current_draft_text):
            return row, "claimed"
    if any(
        isinstance(getattr(row, "report_json", None), Mapping) and row.report_json.get("graded")
        for row in rows
    ):
        return newest, "no_claim"
    return newest, "unfingerprinted"
