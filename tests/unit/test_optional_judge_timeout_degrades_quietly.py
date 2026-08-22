"""可选 LLM 判官层超时是**已知的有界降级**，不许刷整段 traceback。

2026-08-22 用户在前端看到报错：`chapter_llm_commercial_judge timed out
after 150.0s`——完整 traceback。实际上这层是 fail-open 的（超时只是跳过
语义判官），但四个调用点全用 `logger.exception` 把可选层的超时打成事故
现场。60 分钟 1 次 = 供应商延迟尖峰，不是故障。

区分两类失败：
* **TimeoutError**：预期的有界降级 → 一行 WARNING（说清跳过了什么层）。
* 其他异常：真故障 → 保留完整 `logger.exception`（此处曾从 debug 升级
  过——静默吞掉判官崩溃让语义层整体失效无人知晓，那个教训不能回退）。
"""

from __future__ import annotations

# ruff: noqa: RUF002 — 中文标点是刻意的。
import logging

from bestseller.services.reviews import _log_optional_review_llm_failure


def test_timeout_logs_one_warning_line_without_traceback(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="bestseller.services.reviews"):
        _log_optional_review_llm_failure(
            TimeoutError("chapter_llm_commercial_judge timed out after 150.0s"),
            what="chapter LLM commercial judge",
            chapter_number=1,
        )
    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert rec.levelno == logging.WARNING
    assert rec.exc_info is None, "超时不许带 traceback"
    assert "skipped" in rec.getMessage() or "跳过" in rec.getMessage()


def test_real_exceptions_keep_the_full_traceback(caplog) -> None:
    """判官真崩溃必须带堆栈——曾因 debug 级静默吞掉而整层失效，不能回退。"""

    with caplog.at_level(logging.ERROR, logger="bestseller.services.reviews"):
        _log_optional_review_llm_failure(
            ValueError("schema drift"),
            what="chapter LLM commercial judge",
            chapter_number=1,
        )
    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is not None


def test_all_four_optional_sites_use_the_helper() -> None:
    import inspect

    from bestseller.services import reviews

    src = inspect.getsource(reviews)
    assert src.count("_log_optional_review_llm_failure(") >= 5  # 1 def + 4 调用
