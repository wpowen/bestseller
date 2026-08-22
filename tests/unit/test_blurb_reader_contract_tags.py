"""简介的标签行必须给读者决策信息，不是设定关键词表。

2026-08-22 用户定罪（《书院笔仙》）：产出的标签行是

    【代笔杂役+话本成真+笔即是命+书院权斗】

四个全是**设定关键词**——对一个完全不懂本书设定的陌生读者，这四个词
不提供任何「我要不要点进去」的信息。真实榜单的标签行长这样：

    【无系统+单女主+轻松爽文】

它们是**避雷契约**：有没有系统、几个女主、虐不虐。读者靠这个决定点不点。

而这本书建书时勾的恰恰就是这类信息——

    effect_skills = ["comedy_engine", "hype_satisfaction_engine"]
    cost_style    = "minimal"

「轻松」「爽」「不虐主角」全在里面，一个都没传给文案师。同时
`_build_candidate_messages` 收了 `tags` 参数（含「逆袭」「男频爽文」
「凡人流」）却在 prompt 里一次都没用——传了不用。

⚠️ 这不是种词：这些标签是**用户自己勾的选项**确定性推导出来的，
不是给模型的通用词表。没勾就没有，绝不凭空补。
"""

from __future__ import annotations

# ruff: noqa: RUF002 — 中文标点是刻意的。
from bestseller.services.story_enhancers import reader_contract_labels


def test_ticked_options_become_reader_facing_contract_labels() -> None:
    labels = reader_contract_labels(
        {
            "story_enhancers": {
                "effect_skills": ["comedy_engine", "hype_satisfaction_engine"],
                "cost_style": "minimal",
            }
        }
    )
    joined = "".join(labels)
    assert "轻松" in joined, "勾了喜剧效果 → 读者该知道这本是轻松向"
    assert "爽" in joined, "勾了爽点引擎 → 读者该知道这本是爽文"
    assert "虐" in joined, "勾了无代价 → 读者该知道主角不受虐"


def test_nothing_ticked_yields_no_labels() -> None:
    """没勾就没有——绝不凭空给读者作出承诺。"""

    assert reader_contract_labels({}) == ()
    assert reader_contract_labels({"story_enhancers": {}}) == ()


def test_labels_are_deduped_and_stable() -> None:
    a = reader_contract_labels(
        {"story_enhancers": {"effect_skills": ["hype_satisfaction_engine"] * 3}}
    )
    b = reader_contract_labels({"story_enhancers": {"effect_skills": ["hype_satisfaction_engine"]}})
    assert a == b
    assert len(set(a)) == len(a)
