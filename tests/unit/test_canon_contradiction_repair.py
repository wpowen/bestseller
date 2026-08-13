"""finalize 正典自洽修复的采纳守卫（P4，2026-08-07）。

毒正典（premise 35 岁 vs spine「三十年」、why_now 双期限）产自 finalize；
自闭环 5 轮证明下游救不回。修复协议：验出矛盾 → 一次有界最小修复 →
**复验通过且矛盾数下降才采纳**。真机冒烟已过（2→0，改动仅「三十年」→「多年」）；
本文件钉守卫：坏修复稿不采纳、结构缩水不采纳、判官挂了 fail-open。
"""

from __future__ import annotations

import json

import pytest

from bestseller.services import blurb_coherence_judge as bcj
from bestseller.services import conception as C

pytestmark = pytest.mark.unit

_PREMISE = "三十五岁盒饭铺老板纪釜，开铺七年。"
_SPINE = {"who": "三十年市井厨房摸爬滚打", "why_now": "灶火只剩一个时辰"}
_SYN = "灵气复苏这天，他看见一缕青光。"

_FINDING = bcj.CoherenceFinding(
    kind="number", quote_a="三十五岁盒饭铺老板纪釜，开铺七年",
    quote_b="三十年市井厨房摸爬滚打", explanation="年龄与年数打架",
    touches_synopsis=False,
)


def _report(n_canon: int) -> bcj.CoherenceReport:
    return bcj.CoherenceReport(findings=(_FINDING,) * n_canon, llm_used=True)


def _patch(monkeypatch, *, verify_seq: list, llm_payload: str) -> None:
    calls = {"n": 0}

    async def _fake_verify(session, settings, **kwargs):
        r = verify_seq[min(calls["n"], len(verify_seq) - 1)]
        calls["n"] += 1
        return r

    async def _fake_complete(session, settings, request):
        class _R:
            content = llm_payload
            llm_run_id = None
        return _R()

    monkeypatch.setattr(bcj, "verify_blurb_coherence", _fake_verify)
    monkeypatch.setattr(C, "complete_text", _fake_complete)


async def test_clean_canon_is_untouched(monkeypatch) -> None:
    _patch(monkeypatch, verify_seq=[_report(0)], llm_payload="{}")
    p, s, rep = await C._repair_canon_contradictions(
        None, None, premise=_PREMISE, spine=_SPINE, synopsis=_SYN)
    assert p == _PREMISE and s == _SPINE


async def test_good_repair_is_adopted(monkeypatch) -> None:
    fixed = {"premise": _PREMISE, "spine": {**_SPINE, "who": "多年市井厨房摸爬滚打"}}
    _patch(monkeypatch, verify_seq=[_report(1), _report(0)],
           llm_payload=json.dumps(fixed, ensure_ascii=False))
    p, s, rep = await C._repair_canon_contradictions(
        None, None, premise=_PREMISE, spine=_SPINE, synopsis=_SYN)
    assert s["who"] == "多年市井厨房摸爬滚打"
    assert rep["repaired"] is True and rep["findings_before"] == 1


async def test_repair_that_does_not_improve_is_rejected(monkeypatch) -> None:
    fixed = {"premise": _PREMISE, "spine": dict(_SPINE)}
    _patch(monkeypatch, verify_seq=[_report(1), _report(1)],
           llm_payload=json.dumps(fixed, ensure_ascii=False))
    p, s, rep = await C._repair_canon_contradictions(
        None, None, premise=_PREMISE, spine=_SPINE, synopsis=_SYN)
    assert s == _SPINE  # 原样保留
    assert rep["repaired"] is False


async def test_shrunken_spine_is_rejected(monkeypatch) -> None:
    # 修复稿把 spine 字段弄丢 → 结构守卫拒绝，复验都不必跑。
    fixed = {"premise": _PREMISE, "spine": {"who": "多年"}}  # why_now 丢了
    _patch(monkeypatch, verify_seq=[_report(1), _report(0)],
           llm_payload=json.dumps(fixed, ensure_ascii=False))
    p, s, rep = await C._repair_canon_contradictions(
        None, None, premise=_PREMISE, spine=_SPINE, synopsis=_SYN)
    assert s == _SPINE


async def test_truncated_premise_is_rejected(monkeypatch) -> None:
    fixed = {"premise": "纪釜。", "spine": dict(_SPINE)}
    _patch(monkeypatch, verify_seq=[_report(1), _report(0)],
           llm_payload=json.dumps(fixed, ensure_ascii=False))
    p, s, rep = await C._repair_canon_contradictions(
        None, None, premise=_PREMISE, spine=_SPINE, synopsis=_SYN)
    assert p == _PREMISE


async def test_garbage_llm_output_fails_open(monkeypatch) -> None:
    _patch(monkeypatch, verify_seq=[_report(1)], llm_payload="不是JSON")
    p, s, rep = await C._repair_canon_contradictions(
        None, None, premise=_PREMISE, spine=_SPINE, synopsis=_SYN)
    assert p == _PREMISE and s == _SPINE


async def test_verifier_down_fails_open(monkeypatch) -> None:
    async def _boom(session, settings, **kwargs):
        raise RuntimeError("judge down")

    monkeypatch.setattr(bcj, "verify_blurb_coherence", _boom)
    p, s, rep = await C._repair_canon_contradictions(
        None, None, premise=_PREMISE, spine=_SPINE, synopsis=_SYN)
    assert p == _PREMISE and s == _SPINE and rep is None
