"""⚠️ 已翻案：商业判官不再握提升否决权（原文件名保留以便追溯）。

2026-08-23 我把提升资格改成「认商业判官」，理由写在下面这段原始定罪里，
当时看着很有说服力——判官意见带引文、指出的正是用户抱怨过的问题。

2026-08-24 用数据推翻了那个前提：

* 该判官对书 7 的 **149 份判决 0 通过**（均分 0.538、最高 0.78、零份 ≥0.80）；
* 把它拿去跑 **10 本真实出版小说的章节，10/10 全判 fail**
  （0.42–0.72，与我们自己的 0.538 均值完全重叠）。

也就是说：**在它的通过线上零区分力**。它是优秀的批评者（rewrite_plan 已接进
重写反馈，见 test_commercial_judge_teaches_even_when_not_blocking），
但不是合格的验收尺——把提升挂在它身上，等于把「永不可达的回声合成分」
换成了「永不可达的判官」，症状一模一样。

真正的病根是同一个、且我当时只修了一半：`score_overall` 与 `_core_scores`
里掺着回声公式轴（hook 0.321 / continuity 0.558）。真机 169 份质量分——
含回声时最弱维 ≥0.75 的有 **0 份**；只用诚实轴（goal/coverage/coherence/
style）则 141 份（83%），诚实轴均分 0.858、106 份（63%）≥0.85。
2026-08-23 已让回声轴不能否决 chapter verdict，2026-08-24 补齐另一半：
它也不该决定一稿能不能上架。

本文件现在钉住的契约是：**判官判 fail 不阻止提升**（教学权保留、否决权撤回），
达标看诚实轴（见 test_promotion_uses_honest_axes.py）。

2026-08-23 的原始推理（保留以便追溯）：章审 verdict 修好后提升仍报
「Chapter 6 promotion evidence was not eligible」，因为 min_overall=0.85
对照的是回声合成分；而同一行 evidence 里躺着 16 维商业判官的完整判决，
它指出的正是用户最初抱怨的问题（主角原地坐等、无自保动作）。当时的结论是
「真尺子掌权」——错在没有先验证这把「真尺子」自己是否可达。
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


def test_judge_fail_no_longer_blocks_promotion() -> None:
    """判官判 fail 不再挡路——它对 10/10 真实出版章节也判 fail。

    诚实轴够高（0.9/0.88/0.86/0.9 → 均 0.885）时照样可以上架。
    """

    did = uuid4()
    score = _score(overall=0.56, judge_payload={"pass": False}, draft_id=did)
    score.score_conflict = 0.88
    score.score_emotion = 0.86
    score.score_style = 0.90
    row = _eligible_row(_draft(did), score, min_overall=0.85, min_core=0.80)
    assert row is not None


def test_judge_verdict_does_not_change_the_outcome() -> None:
    """同一份稿，判官 pass / fail / 缺席 → 三种情况结果必须一致。"""

    outcomes = []
    for payload in ({"pass": True}, {"pass": False}, None):
        did = uuid4()
        score = _score(overall=0.56, judge_payload=payload, draft_id=did)
        score.score_conflict = 0.88
        score.score_emotion = 0.86
        score.score_style = 0.90
        outcomes.append(
            _eligible_row(_draft(did), score, min_overall=0.85, min_core=0.80) is not None
        )
    assert outcomes == [True, True, True]


def test_echo_hook_no_longer_decides_promotion() -> None:
    """回声 hook（真机均 0.32）不再参与达标——它连 verdict 都否决不了。"""

    did = uuid4()
    score = _score(overall=0.56, judge_payload=None, draft_id=did)
    score.score_conflict = 0.88
    score.score_emotion = 0.86
    score.score_style = 0.90
    score.score_hook = 0.05          # 回声轴压到地板
    score.score_dialogue = 0.05      # 续接同为回声代理轴
    assert _eligible_row(_draft(did), score, min_overall=0.85, min_core=0.80) is not None


def test_hard_gates_and_blockers_still_veto_even_with_judge_pass() -> None:
    """判官掌权不豁免硬门：硬门没过或有阻断码照样不许提升。"""

    did = uuid4()
    score = _score(overall=0.9, judge_payload={"pass": True}, draft_id=did)
    score.evidence_summary["hard_gates_passed"] = False
    assert _eligible_row(_draft(did), score, min_overall=0.85, min_core=0.80) is None

    score2 = _score(overall=0.9, judge_payload={"pass": True}, draft_id=did)
    score2.evidence_summary["blocking_codes"] = ["LENGTH_OVER"]
    assert _eligible_row(_draft(did), score2, min_overall=0.85, min_core=0.80) is None
