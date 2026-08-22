"""提升状态机的资格判据改认商业判官——不再挂在回声合成分上。

2026-08-22/23 定罪链的最后一环：章审 verdict 修好后（ch6/ch8 历史首批
pass），提升仍然失败——

    Chapter 6 promotion evidence was not eligible

机制：`promote_chapter_draft(min_overall=0.85)` 对照的 `score_overall`
是**同一把回声公式合成的 overall**（ch6 = 0.56，全库最好 0.600，0.85
恒不可达）；`_core_scores` 六元组里还混着回声 hook（0.29）。数值路径
整条建在坏尺子上。

而同一行 evidence 里躺着 `llm_commercial_judge`：16 维 + 二元清单 +
逐条 blocking issue 的**真判官**——ch6 它判 pass=false，理由正是用户
最初的抱怨（PROTAGONIST_PLOT_SERVING_STUPIDITY：主角原地坐等师叔推门，
没有可见的自保动作），并附完整可执行的重写方案。它有判断力，没有权力。

修法：evidence 里有商业判官判决时，资格 = 硬门全过 + 零阻断码 +
**判官 pass**；判官缺席（超时被跳过/未启用）时退回原数值路径。
真尺子掌权，坏尺子只当没有真尺子时的回退。
"""

from __future__ import annotations

# ruff: noqa: RUF002, RUF003 — 中文标点是刻意的。
from types import SimpleNamespace
from uuid import uuid4

from bestseller.services.draft_promotion import _eligible_row


def _score(*, overall: float, judge_payload: dict | None, draft_id) -> SimpleNamespace:
    return SimpleNamespace(
        judge_key="chapter_quality_v1",
        chapter_draft_version_id=draft_id,
        scene_draft_version_id=None,
        score_overall=overall,
        score_goal=0.9,
        score_conflict=0.8,
        score_emotion=0.8,
        score_dialogue=0.8,
        score_style=0.85,
        score_hook=0.29,  # 回声 hook——数值路径的坏尺子成分，真机原样
        evidence_summary={
            "hard_gates_passed": True,
            "blocking_codes": [],
            **({"llm_commercial_judge": judge_payload} if judge_payload else {}),
        },
    )


def _draft(draft_id) -> SimpleNamespace:
    # 非 SceneDraftVersionModel 实例即可——_eligible_row 只用 isinstance
    # 区分场景/章节稿来选 score id 列，其余只读 id / version_no。
    return SimpleNamespace(id=draft_id, version_no=3)


def test_judge_pass_grants_eligibility_despite_echo_overall() -> None:
    """真机 ch6 的形状反过来：判官 pass 时，0.56 的回声 overall 不再挡路。"""

    did = uuid4()
    row = _eligible_row(
        _draft(did),
        _score(overall=0.56, judge_payload={"pass": True}, draft_id=did),
        min_overall=0.85,
        min_core=0.80,
    )
    assert row is not None


def test_judge_fail_denies_eligibility() -> None:
    """ch6 真机原样：判官 pass=false（主角坐等推门）→ 不许提升。"""

    did = uuid4()
    row = _eligible_row(
        _draft(did),
        _score(overall=0.95, judge_payload={"pass": False}, draft_id=did),
        min_overall=0.85,
        min_core=0.80,
    )
    assert row is None


def test_no_judge_falls_back_to_the_numeric_path() -> None:
    """判官缺席（超时被跳过/未启用）→ 原数值路径逐字保留。"""

    did = uuid4()
    assert (
        _eligible_row(
            _draft(did),
            _score(overall=0.56, judge_payload=None, draft_id=did),
            min_overall=0.85,
            min_core=0.80,
        )
        is None
    )
    high = _score(overall=0.9, judge_payload=None, draft_id=did)
    high.score_hook = 0.85
    assert _eligible_row(_draft(did), high, min_overall=0.85, min_core=0.80) is not None


def test_hard_gates_and_blockers_still_veto_even_with_judge_pass() -> None:
    """判官掌权不豁免硬门：硬门没过或有阻断码照样不许提升。"""

    did = uuid4()
    score = _score(overall=0.9, judge_payload={"pass": True}, draft_id=did)
    score.evidence_summary["hard_gates_passed"] = False
    assert _eligible_row(_draft(did), score, min_overall=0.85, min_core=0.80) is None

    score2 = _score(overall=0.9, judge_payload={"pass": True}, draft_id=did)
    score2.evidence_summary["blocking_codes"] = ["LENGTH_OVER"]
    assert _eligible_row(_draft(did), score2, min_overall=0.85, min_core=0.80) is None
