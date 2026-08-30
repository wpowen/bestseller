"""L1 tests for the silent-reply-family (沉默应答族) detector.

真机病灶（《摸一摸，救我妹》ch1，2026-08-13 用户终审「没答话/没xx 的ai味
描述还是有」）: 1843 字里「没答话」类短语出现 4 次 = 21.7 次/万字。人类语料
基线（.distillation_private 抽样 60 本 / 11.9M 字）= 0.01 次/万字，约 2000×。
写手模型把「没答话/没吭声」当万能反应镜头，每逢对话回合就沉默一次。

规则口径: rate ≥2/万字 且 绝对次数 ≥2 且 全文 ≥800 字（min_chars 防折叠
计数量级失明）。advisory warn，进 deslop 触发集整段重写。
"""

from __future__ import annotations

import re

import pytest

import bestseller.services.ai_flavor.detector as detector_module
from bestseller.services.ai_flavor.detector import detect

pytestmark = pytest.mark.unit


_FILLER = (
    "他把伞收好靠在墙边，桌上的饭菜已经凉了。母亲坐在灯下补衣服，"
    "针脚一排排推过去。窗外落着小雨，巷子里有人骑车经过，铃铛响了两声。"
)  # 无沉默应答短语的中性叙述，用来拉长度/稀释密度


def _saturated_text() -> str:
    # 贴真机口径：~1800 字里 4 处沉默应答（≈22/万字）。
    beats = (
        "陈默没答话，把茶杯往桌心推了推。"
        "对面的人又问了一遍，他还是没接话。"
        "屋里静了半晌，妹妹没吭声。"
        "电话那头的人等了三秒，没回应，挂了。"
    )
    return beats + _FILLER * 12


def test_silent_reply_spam_flags_saturated_chapter() -> None:
    report = detect(_saturated_text(), language="zh-CN", chapter_number=1)
    spans = [s for s in report.spans if s.category == "silent_reply_spam"]
    assert spans, "沉默应答族高密度复读必须被检出"
    assert spans[0].severity == "warn"
    assert "万字" in spans[0].why
    # 具名主语（陈默没答话）也要被计入 —— neg_action 老规则只认他/她/它。
    assert "没答话" in spans[0].why


def test_silent_reply_single_occurrence_is_human_prose() -> None:
    # 一整章只出现 1 次「没说话」是正常人类写法，不该触发。
    text = "他低下头，半天没说话。" + _FILLER * 20
    report = detect(text, language="zh-CN", chapter_number=2)
    assert not [s for s in report.spans if s.category == "silent_reply_spam"]


def test_silent_reply_low_rate_not_flagged() -> None:
    # 2 处散落在 ~13000 字里（≈1.5/万字）低于 2/万字阈值，不触发。
    text = (
        "他没答话。" + _FILLER * 90 + "她没吭声。" + _FILLER * 90
    )
    assert len(text) > 10000
    report = detect(text, language="zh-CN", chapter_number=3)
    assert not [s for s in report.spans if s.category == "silent_reply_spam"]


def test_silent_reply_min_chars_floor_guards_fragments() -> None:
    # 短片段（<800 字）哪怕命中 2 次也不评速率 —— 防折叠计数量级失明。
    text = "他没答话。她也没吭声。" + _FILLER[:100]
    assert len(text) < 800
    report = detect(text, language="zh-CN", chapter_number=4)
    assert not [s for s in report.spans if s.category == "silent_reply_spam"]


def test_silent_reply_triggers_deslop(tmp_path) -> None:
    """闭环：检出后必须路由 deslop 整段重写 —— patcher 无静态替换，
    沉默镜头要换成具体反应，只有整段重写能清。"""
    from bestseller.services.ai_flavor_gate import (
        AiFlavorGateConfig,
        DESLOP_DISCOURSE_CATEGORIES,
        needs_deslop_revise,
        run_ai_flavor_gate,
    )

    assert "silent_reply_spam" in DESLOP_DISCOURSE_CATEGORIES
    outcome = run_ai_flavor_gate(
        chapter_number=1,
        content_md=_saturated_text(),
        language="zh-CN",
        config=AiFlavorGateConfig(),
        project_output_dir=tmp_path,
    )
    # 锚定到本检测项的 issue id —— 饱和文本可能同时触发其他 deslop 类别，
    # 只断言 needs_deslop_revise 会被别的类别掩护（no-op 验证曾抓到）。
    assert outcome.report is not None
    issue_ids = {i.id for i in outcome.report.issues}
    assert "AI_FLAVOR_SILENT_REPLY_SPAM" in issue_ids
    assert needs_deslop_revise(outcome) is True


def test_silent_reply_is_advisory_capped() -> None:
    # 与 negative_action_filler 同族：advisory-capped，不能单独把章推过 block。
    from bestseller.services.ai_flavor.detector import _score
    from bestseller.services.ai_flavor.types import AiFlavorSpan

    spans = tuple(
        AiFlavorSpan(
            start=i,
            end=i + 3,
            matched_text="没答话",
            rule_id="zh.tic.silent_reply_spam",
            category="silent_reply_spam",
            severity="warn",
            suggestions=(),
            sentence_span=(i, i + 3),
            why="沉默应答族复读",
            remove_sentence_on_block=False,
        )
        for i in range(0, 40, 4)
    )
    # 10 个 warn × 4 分 = 40，若未截断会 >24；截断证明在 advisory cap 集里。
    assert _score(spans) <= 24.0


def test_noop_guard_empty_regex_kills_detection(monkeypatch) -> None:
    """no-op 验证：把检测正则改成永不匹配，检出必须消失。

    与 test_silent_reply_spam_flags_saturated_chapter 成对：同一文本，
    真正则必检出、空正则必不检出 —— 证明正则真被走到，改空必翻红。
    """

    monkeypatch.setattr(
        detector_module, "_SILENT_REPLY_RE", re.compile(r"(?!x)x")
    )
    report = detector_module.detect(
        _saturated_text(), language="zh-CN", chapter_number=1
    )
    assert not [s for s in report.spans if s.category == "silent_reply_spam"]
