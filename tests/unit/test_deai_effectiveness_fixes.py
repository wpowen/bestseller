# -*- coding: utf-8 -*-
"""2026-08-31 去AI味「接线了但没生效」三处修复的回归锁。

真机定罪（《攥着残页从渡口骂到寨里》42 章，新代码上线后生成）：
检测层确实开火（29/42 章命中新轴），但
  ① deslop 的「短而干净的稿先留下、下一轮补长度」救援只对 moment_slice
     一条轴生效，其余病态轴的干净稿照旧被丢——39 次终轮拒绝里 9 次（23%）
     属于此类，其中一例 stock_reaction 1.29→0.00、badness 9.04→4.55 被整份作废；
  ② 终轮拒绝的两种原因（太短 / 变差）打印同一句 "regressed"，日志自己在骗人；
  ③ step 记录的 before/after 都测在已重写的稿上，DB 上永远显示零改善；
  ④ 简介定稿路径（planner + book_listing）copy_flavor 引用数为 0，
     读者第一眼看到的文案反而不查 AI 腔。
"""

import inspect

import pytest

from bestseller.services import deslop_revise as dr
from bestseller.services import pipelines
from bestseller.services.planner import _resolve_promotional_brief_blurb

pytestmark = pytest.mark.unit


# ── ① 短而干净的救援必须覆盖全部病态轴，不能只认 moment_slice ──────────


def test_short_but_cleaner_rescue_covers_every_pathological_axis() -> None:
    src = inspect.getsource(dr._revise_prose_deslop_inner)
    # 救援条件必须挂在「带病进来」的合取旗标上，而不是单独的 slice_first
    assert "entered_pathological" in src
    # 判据是全轴合取（_rescue_worthwhile），不是字典序——见下面那条测试
    assert "_rescue_worthwhile" in src
    # 旧的单轴判据不得复活
    assert "slice_first\n            and _moment_slice_rate(revised)" not in src


def test_rescue_requires_no_axis_to_regress() -> None:
    """救援判据必须全轴合取，不能用 keep-better 的字典序。

    真实反例（2026-08-31 ch27）：staccato 0.43→0.15 改好，但 verb_tic
    96.2→142.8（恶化 48%）、micro 5.92→7.79。字典序只看第一位就判「更干净」，
    收下后补完长度总分 60→61 反而更差。
    """

    cur = {"staccato": 0.43, "verb_tic": 96.2, "micro": 5.92}
    ch27 = {"staccato": 0.15, "verb_tic": 142.8, "micro": 7.79}
    assert dr._rescue_worthwhile(cur, ch27) is False, "有轴恶化就不该救援"

    # 全轴不倒退且至少一条变好 → 该救
    better = {"staccato": 0.15, "verb_tic": 90.0, "micro": 5.92}
    assert dr._rescue_worthwhile(cur, better) is True

    # 一模一样（没变好）→ 不救，免得白白多跑一轮补字数
    assert dr._rescue_worthwhile(cur, dict(cur)) is False


def test_rescue_criterion_is_not_lexicographic() -> None:
    # 源码层面确认：救援用 _rescue_worthwhile，不再是 _key 比较
    src = inspect.getsource(dr._revise_prose_deslop_inner)
    assert "_rescue_worthwhile(_axes(content), _axes(revised))" in src
    assert "_key(revised) < _key(content)" not in src


def test_pathological_measures_only_reports_active_axes() -> None:
    # 带外的轴不参与判断（与 keep-better「带外恒 0.0」同一条纪律）
    text = "他把担子放下来，往灶膛里添了一把柴。" * 40
    only_verb = dr._pathological_measures(text, verb_tic_first=True)
    assert set(only_verb) == {"verb_tic"}
    assert dr._pathological_measures(text) == {}


def test_entered_pathological_includes_all_six_axes() -> None:
    src = inspect.getsource(dr._revise_prose_deslop_inner)
    start = src.index("entered_pathological = (")
    block = src[start : start + 260]
    for flag in (
        "slice_first",
        "staccato_first",
        "verb_tic_first",
        "repetition_first",
        "stock_first",
        "micro_first",
    ):
        assert flag in block, flag


# ── ② 两种拒绝原因必须分开记 ────────────────────────────────────────────


def test_final_rejection_distinguishes_short_from_worse() -> None:
    src = inspect.getsource(dr._revise_prose_deslop_inner)
    assert "too_short = not _length_ok(content)" in src
    assert "got_worse = final_key > best_key" in src
    # 「更干净但太短」要有自己的、说人话的日志，不能再混进 regressed
    assert "CLEANER but too short" in src


# ── ③ step 记录保留进门原始分 ──────────────────────────────────────────


def test_step_run_preserves_entry_score() -> None:
    src = inspect.getsource(pipelines)
    assert "_af_entry_score = ai_flavor_outcome.before_score" in src
    assert '"entry_score": _af_entry_score' in src
    assert '"improvement": round(' in src


# ── ④ 简介定稿接 copy_flavor：只过滤不重排 ────────────────────────────


def _dirty(text: str) -> bool:
    from bestseller.services.copy_flavor import detect_copy_flavor

    return not detect_copy_flavor(text).clean


def test_blurb_prefers_clean_candidate_over_dirty_converged() -> None:
    # 收敛简介带生产口吻（章号=meta_cadence），LLM 简介干净 → 换干净的
    dirty_converged = "第50章，账本只剩最后几页，他必须在天亮前把名字划掉。"
    clean_llm = "他把最后一枚铜钱压在案上，抬头问掌柜：先杀谁？"
    assert _dirty(dirty_converged) and not _dirty(clean_llm)
    got = _resolve_promotional_brief_blurb(
        converged_synopsis=dirty_converged,
        llm_blurb=clean_llm,
        premise_fallback="一个账房先生的复仇。",
    )
    assert got == clean_llm


def test_blurb_keeps_top_preference_when_all_candidates_dirty() -> None:
    # 全脏时必须维持旧行为（返回优先级最高的收敛简介），不许把次选顶上来
    dirty_a = "第50章，账本只剩最后几页。"
    dirty_b = "一文读懂本书设定，建议收藏。"
    assert _dirty(dirty_a) and _dirty(dirty_b)
    got = _resolve_promotional_brief_blurb(
        converged_synopsis=dirty_a,
        llm_blurb=dirty_b,
        premise_fallback="一个账房先生的复仇。",
    )
    assert got == dirty_a


def test_blurb_never_promotes_premise_on_flavour_grounds() -> None:
    # premise 是前提句不是文案：两份文案都脏也不能让它上位（干净≠适合当简介）
    premise = "一个账房先生的复仇。"
    got = _resolve_promotional_brief_blurb(
        converged_synopsis="第50章，账本只剩最后几页。",
        llm_blurb="一文读懂本书设定，建议收藏。",
        premise_fallback=premise,
    )
    assert got != premise


def test_blurb_still_falls_back_to_premise_on_fatal_pathology() -> None:
    # 没有收敛简介 + LLM 简介致命病理 → 仍然退到 premise（旧行为不变）。
    # 样本必须真的触发 fatal，否则这条分支等于没测（第一版样本就没触发，
    # 只是被 skip 掉——skip 的测试不是通过的测试）。
    from bestseller.services.blurb_pathology import detect_blurb_pathology

    premise = "一个账房先生的复仇。"
    # tautology_choice：同义名词（饭碗/工作）+ 反义动词（保/丢）= 假选择
    fatal_blurb = "摆在他面前的只有一条路：是保住饭碗，还是丢掉工作？"
    assert any(
        f.severity == "fatal" for f in detect_blurb_pathology(fatal_blurb)
    ), "样本必须真的致命，否则这条分支没被覆盖"
    got = _resolve_promotional_brief_blurb(
        converged_synopsis="", llm_blurb=fatal_blurb, premise_fallback=premise
    )
    assert got == premise
