"""T6 (2026-07-09) — conception.py 接线：简介独立文案工序替换 finalize 直出简介。

同 T1/T4 的既有测试惯例（见 test_persona_click_judge_wiring.py / test_reader_
promise_adaptation.py 的说明）：整段编排内联在 ~4000+ 行的
``run_conception_pipeline`` 里，不引入整段管线 mock 基建的前提下无法端到端跑
（本仓已有先例：test_web_server.py 对该函数整体打桩）。这里用源码结构断言钉
控制流关键锚点；``run_blurb_copywriting`` 本身的行为已由 test_blurb_
copywriter.py 用注入 fake generator/judge 完整覆盖。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import conception as conception_services

pytestmark = pytest.mark.unit


def _source() -> str:
    return inspect.getsource(conception_services.run_conception_pipeline)


def test_copywriting_call_precedes_champion_assignment_and_appeal_block():
    source = _source()
    cw_call_pos = source.index("_copywriting_result = await run_blurb_copywriting(")
    champion_assign_pos = source.index("_copywriting_result.champion", cw_call_pos + 1)
    appeal_block_pos = source.index("Story/blurb appeal evaluation")
    title_invariant_pos = source.index("title_profile[\"primary_title\"] = title")

    assert title_invariant_pos < cw_call_pos, (
        "copywriting must run after the title finalization invariant"
    )
    assert cw_call_pos < champion_assign_pos < appeal_block_pos, (
        "champion must be assigned to synopsis before the appeal evaluation block runs, "
        "so the appeal system evaluates/regenerates from the copywriting champion, not v0"
    )


def test_copywriting_champion_is_sanitized_and_truncated():
    """回归钉子(检测报告 P1-1/P1-2)：冠军简介绕开了跨书污染消毒
    (``_sanitize_forbidden_default_motifs``，本函数更早处对 synopsis 的原始
    finalize 版本已经跑过一次，但赋值发生在那之后，必须补跑) 和 500 字句界
    截断兜底(旧路径全都有，唯独冠军赋值没有)——两者都必须在冠军赋值处补上。"""

    source = _source()
    cw_call_pos = source.index("_copywriting_result = await run_blurb_copywriting(")
    surrounding = source[cw_call_pos : cw_call_pos + 1200]
    assert "_sanitize_forbidden_default_motifs(_copywriting_result.champion" in surrounding
    assert "truncate_at_sentence(synopsis, 500)" in surrounding


def test_copywriting_call_receives_v0_synopsis_and_book_jargon_terms():
    source = _source()
    call_start = source.index("_copywriting_result = await run_blurb_copywriting(")
    call_region = source[call_start : call_start + 800]
    assert "v0_synopsis=synopsis" in call_region
    assert "book_jargon_terms=_book_jargon_terms" in call_region
    assert "spine=story_spine" in call_region


def test_copywriting_block_is_wrapped_in_fail_open_try_except():
    source = _source()
    block_start = source.index("# ── 简介独立文案工序（T6")
    surrounding = source[block_start : block_start + 4500]
    assert "try:" in surrounding
    assert 'logger.warning("Blurb copywriting tournament failed (non-fatal)"' in surrounding


def test_tournament_report_persisted_independent_of_appeal_system_state():
    """结构断言：copywriting_tournament 的持久化必须在 appeal try/except 结束
    之后、不依赖 appeal 系统是否启用/是否失败——否则 appeal 系统关闭时文案工序
    的淘汰赛报告永远进不了 story_appeal_report，分段验收就看不到它。"""

    source = _source()
    except_pos = source.index('logger.warning("Story appeal evaluation failed (non-fatal)"')
    persist_pos = source.index(
        'story_appeal_report["copywriting_tournament"] = _copywriting_result.to_dict()'
    )
    assert persist_pos > except_pos


def test_max_attempts_compressed_when_copywriting_ran():
    source = _source()
    assert "max_attempts_after_copywriting" in source
    assert "if _copywriting_ran" in source


def test_logline_rederivation_only_when_not_fallen_back_to_v0():
    source = _source()
    logline_call_pos = source.index("_new_logline, _logline_ids = await _derive_logline_from_champion(")
    guard_pos = source.rindex(
        "if not _copywriting_result.fell_back_to_v0:", 0, logline_call_pos
    )
    # 守卫必须紧挨着调用点(中间不该隔着别的无关大段代码)。
    assert logline_call_pos - guard_pos < 200


def test_derive_logline_from_champion_fails_open_on_pathology():
    """回归钉子：_derive_logline_from_champion 命中 fatal 病理时必须返回空串
    (调用方保留原 logline)，不能把病句写进 market.logline。"""

    source = inspect.getsource(conception_services._derive_logline_from_champion)
    assert 'return "", ids' in source
    assert "detect_blurb_pathology(candidate)" in source


def test_final_prompt_bans_omniscient_tease_ending() -> None:
    """结尾留悬念 with no shape guidance defaulted to '可她自己都不知道…' — the
    suspense must land on a concrete imminent threat/choice/deadline."""

    import inspect

    from bestseller.services import blurb_copywriter as bc

    source = inspect.getsource(bc)
    assert "全知旁白式吊胃口" in source
    assert "具体的、即将发生的" in source
