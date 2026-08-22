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
    # 取到本段末尾，而不是固定字符窗口。
    #
    # 这个窗口 2026-08-06 已经因为在中间插入代码放宽过一次（1200→2400），
    # 2026-08-22 又被插入的 reader_contract 参数挤破了第二次。每次插入几行
    # 就要来调一次常数，说明脆的是取样方式不是断言——改成按下一个顶层
    # 语句边界切，断言一字未动。
    _tail = source[cw_call_pos:]
    _next_block = _tail.find("\n        # ── ")
    surrounding = _tail[: _next_block if _next_block > 0 else 4000]
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
    # 窗口 6500（原 4500）：同上，正典人名校验分支加长了该块。
    surrounding = source[block_start : block_start + 6500]
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


def test_logline_rederivation_only_when_champion_is_actually_in_use():
    """logline 只在 synopsis 真的换成冠军时才重新提炼。

    守卫原本只看 ``fell_back_to_v0``。2026-08-06 增加了第二条回退路径——冠军换掉
    正典主角时被拒、synopsis 保持 v0——它同样必须阻断重提炼，否则会从一份并未
    采用的简介里推导 logline。
    """

    source = _source()
    logline_call_pos = source.index(
        "_new_logline, _logline_ids = await _derive_logline_from_champion("
    )
    guard_pos = source.rindex("if not (", 0, logline_call_pos)
    guard = source[guard_pos:logline_call_pos]
    assert "_copywriting_result.fell_back_to_v0" in guard
    assert "_copywriting_result.canon_name_rejected" in guard
    # 守卫必须紧挨着调用点(中间不该隔着别的无关大段代码)。
    assert logline_call_pos - guard_pos < 300


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
    # 2026-08-11 百本调研后收尾规则改为「陈述句或名场面截断」（问句仅 6/100，
    # 全知吊胃口仍然违禁）。
    assert "名场面截断" in source


def test_prompt_encodes_board_research_form() -> None:
    """2026-08-11 百本榜单调研的三条硬改（docs/research/board-blurb-hook-research）。

    ①标签行（62/100 头部有，且必须事实接地、禁编信用背书）；②体验样本硬要求
    （52/100 直贴正文级引语，我们此前零引语是最大缺口）；③短句分行+第三方见证
    +预期违背。旧「三五个长句」规则出自 42 条精选语料，与在榜活数据（中位 209 字
    /9 句/10 行）冲突，已废弃——本测试同时钉住它不许回潮。
    """

    import inspect

    from bestseller.services import blurb_copywriter as bc

    source = inspect.getsource(bc)
    for token in ("标签行", "体验样本，缺失即废稿", "短句分行", "预期违背",
                  "第三方反应", "禁止编造出版/短剧/评分"):
        assert token in source, token
    assert "三五个长句" not in source, "42 条语料的旧形态规则不得回潮"


def test_appeal_gate_scores_past_the_tag_line() -> None:
    """标签行是契约槽位不是句子——首句 30 字钩子检查必须跳过它评分。"""

    from bestseller.services.blurb_appeal_gate import strip_leading_tag_line

    body = strip_leading_tag_line("【无系统+单女主+轻松爽文】\n岁首前一天，许拙被按住了肩膀。")
    assert body.startswith("岁首前一天")
    assert strip_leading_tag_line("岁首前一天。") == "岁首前一天。"
