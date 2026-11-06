"""INBOX 37 item 2: reader privacy and useful de-AI diagnostics, without models."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


NOTICE = "去 AI 未完成，使用原稿"


def render(chapter, total=1):
    node = shutil.which("node")
    assert node, "Node is required to execute the actual frontend modules"
    book = dict(
        meta=dict(id="webui/visibility", title="阅读测试", total=total, chapters=1, status="complete"),
        chapters=[dict(dict(number=1, title="回家", draft="原稿。", final="原稿。", words=2), **chapter)],
    )
    result = subprocess.run(
        [
            node,
            "--experimental-vm-modules",
            str(Path(__file__).with_name("deai_visibility_37.mjs")),
        ],
        input=json.dumps(book, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_internal_notice_is_only_in_detection_report():
    view = render(dict(deai_notice=NOTICE, report=dict(status="retained")))
    for area in ("directory", "toc", "meta", "prose"):
        assert NOTICE not in view[area], (area, view[area])
    assert "终稿" in view["directory"]
    assert "原稿。" in view["prose"]
    assert "保留原稿" in view["report"]
    assert "原因：未知" in view["report"]
    assert view["unchanged"], "Rendering must retain the API's compatibility fields"


@pytest.mark.parametrize("draft", ["已有草稿。", ""])
def test_unavailable_chapter_uses_neutral_preparation_label(draft):
    view = render(dict(final="", draft=draft, report={}), total=2)
    for area in ("directory", "toc", "meta", "prose"):
        assert "去 AI" not in view[area], (area, view[area])
        assert "准备中" in view[area]


def test_report_displays_failure_warning_and_structured_recovery():
    view = render(
        dict(
            deai_notice=NOTICE,
            report=dict(
                status="retained",
                checks=dict(
                    failure=dict(stage="inventory", reason="返回字段缺失"),
                    recovery=dict(
                        strategy="smaller_packets",
                        attempts=[dict(stage="retry-1", reason="复检不合格", status="retained")],
                        recovered=False,
                    ),
                ),
                warnings=[dict(rollback="facts", paragraphs=[2, 5]), "实体发生变化"],
            ),
        )
    )
    for detail in (
        "inventory",
        "返回字段缺失",
        "facts",
        "2",
        "5",
        "实体发生变化",
        "smaller_packets",
        "retry-1",
        "复检不合格",
        "retained",
        "false",
    ):
        assert detail in view["report"], detail
    assert "[object Object]" not in view["report"]
    assert view["unchanged"]


@pytest.mark.parametrize("status", ["retained", "partial", "unknown"])
@pytest.mark.parametrize("checks", [{}, {"failure": {"stage": "facts"}}])
def test_legacy_failure_without_reason_is_explicitly_unknown(status, checks):
    view = render(dict(report=dict(status=status, checks=checks)))
    assert "原因：未知" in view["report"]
    if not checks:
        assert "失败阶段" not in view["report"]


def test_recovered_success_keeps_history_in_report_only():
    view = render(
        dict(
            report=dict(
                status="polished",
                checks=dict(recovery=[dict(strategy="split", status="polished", attempts=1)]),
                warnings=["已用更小范围重试"],
                after_score=8,
            )
        )
    )
    assert "split" in view["report"] and "已用更小范围重试" in view["report"]
    assert "润色后参考分：8" in view["report"]
    assert "失败原因：未知" not in view["report"]
    assert "split" not in view["directory"] + view["toc"] + view["meta"] + view["prose"]


def test_clean_and_missing_reports_keep_existing_unknown_semantics():
    view = render(dict(checks=dict(within_band=True, hard_conflicts=[]), report={}))
    assert "字数范围：符合" in view["report"]
    assert "一致性问题：0 项" in view["report"]
    assert "整书体检尚未生成，结果未知" in view["report"]
    assert "失败原因" not in view["report"]


def test_diagnostic_markup_is_rendered_as_text():
    view = render(
        dict(
            report=dict(
                status="retained",
                checks=dict(
                    failure=dict(stage="facts", reason='<img src=x onerror="throw Error(1)">'),
                ),
            )
        )
    )
    assert '<img src=x onerror="throw Error(1)">' in view["report"]


@pytest.mark.parametrize("status", ["recovered", "retained", "error", "quota_exhausted", "interrupted"])
def test_single_item_recovery_shows_first_failure_and_retry_result(status):
    retry = dict(status="polished" if status == "recovered" else status)
    if status != "recovered":
        retry["failure"] = dict(stage="facts-retry", reason="最后一次失败")
    checks = dict(
        recovery=dict(
            strategy="single_item",
            status=status,
            attempts=[
                dict(
                    status="retained",
                    failure=dict(
                        stage="inventory",
                        reason="首次返回格式不完整",
                        warnings=["初次警告"],
                    ),
                ),
                retry,
            ],
        )
    )
    if status != "recovered":
        checks["failure"] = retry["failure"]
    view = render(dict(report=dict(status="polished" if status == "recovered" else "retained", checks=checks)))
    report = view["report"]
    assert "首次失败阶段：inventory" in report
    assert "首次失败原因：首次返回格式不完整" in report
    assert "初次警告" in report
    assert "缩小范围" in report and "single_item" in report
    assert "缩范围重试结果：" in report and status in report
    if status == "recovered":
        assert "最后失败原因" not in report
    else:
        assert "最后失败原因：最后一次失败" in report
    assert view["unchanged"]


def test_retained_without_failure_does_not_claim_failure_or_retry():
    view = render(dict(deai_notice=NOTICE, report=dict(status="retained", warnings=["未找到可采用的修改"])))
    assert "保留原稿" in view["report"]
    assert "失败" not in view["report"] and "重试" not in view["report"]
    assert "未找到可采用的修改" in view["report"]
