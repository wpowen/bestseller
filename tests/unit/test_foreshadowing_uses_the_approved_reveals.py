"""伏笔不许是「已批准核心机制将在当下场景中第一次生效」这种方法论。

2026-08-24 真机（书9，source-bound 路径）：

  volume_plan.foreshadowing_planted =
    ["[S1] 已批准核心机制将在当下场景中第一次生效",
     "[S2] 核心机制的第二次运行必须形成不同的可见结果", … 共8条]
  clues 表登记的 8 条 = 同样 8 句，**全部埋在第1章、全部约在第50章收**。

这不是伏笔，是框架方法论；而且没有排程可言。真伏笔应该是「他袖口那道旧疤」
埋 ch3、收 ch27。（这 8 条与 world_rules 的 8 条方法论规则同源，同一个模板。）

而已批准材料里有现成的收口排程：

  info_reveal_strategy「卷一揭清债者一脉守秘人，卷二揭母系血脉源头，
    卷三揭坊市黑市与遗族守墓人，卷四揭六大宗借力协议，卷五揭上古借力源头」

同一形状的第六例（阶梯／派系／势力表落库／逐卷揭示／过滤太窄）：路走到了，
材料没拿。**这里只搬运不发明**：揭示项逐字用作收口，同一项作为埋点放在更早
的卷；材料不够填满下限时，用既有模板补齐剩余——不发明，也不让门要求的下限
落空。
"""

from __future__ import annotations

from types import SimpleNamespace

from bestseller.services.planner import derive_source_bound_foreshadowing

_REVEAL = (
    "账本翻页式释放——卷一揭清债者一脉守秘人，卷二揭母系血脉源头，"
    "卷三揭坊市黑市与遗族守墓人，卷四揭六大宗借力协议，卷五揭上古借力源头"
)


def _project(reveal=_REVEAL):
    return SimpleNamespace(
        metadata_json={"writing_profile": {"world": {"info_reveal_strategy": reveal}}},
        language="zh-CN",
    )


def test_approved_reveals_become_the_payoffs() -> None:
    plants, payoffs = derive_source_bound_foreshadowing(_project(), volume_count=5)
    joined = " ".join(payoffs)
    for item in ("清债者一脉守秘人", "母系血脉源头", "六大宗借力协议", "上古借力源头"):
        assert item in joined, (item, payoffs)


def test_no_methodology_text_survives() -> None:
    plants, payoffs = derive_source_bound_foreshadowing(_project(), volume_count=5)
    for text in plants + payoffs:
        assert "已批准核心机制" not in text, text
        assert "当下验证" not in text, text


def test_every_payoff_has_a_plant_carrying_the_same_thread() -> None:
    """收口必须有对应的埋点——只列收口不算伏笔账本。"""

    plants, payoffs = derive_source_bound_foreshadowing(_project(), volume_count=5)
    for item in ("清债者一脉守秘人", "上古借力源头"):
        assert any(item in p for p in plants), (item, plants)


def test_ordering_is_preserved() -> None:
    plants, payoffs = derive_source_bound_foreshadowing(_project(), volume_count=5)
    idx = [next(i for i, t in enumerate(payoffs) if k in t)
           for k in ("清债者一脉守秘人", "母系血脉源头", "上古借力源头")]
    assert idx == sorted(idx), idx


def test_no_reveal_schedule_returns_nothing() -> None:
    """没有排程就不编——交还给既有模板兜底。"""

    assert derive_source_bound_foreshadowing(_project(""), volume_count=3) == ([], [])
    assert derive_source_bound_foreshadowing(
        _project("信息随剧情自然释放"), volume_count=3
    ) == ([], [])


def test_the_compiler_prefers_approved_items() -> None:
    from pathlib import Path

    import bestseller.services.planner as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "derive_source_bound_foreshadowing" in src
    body = src.split("plant_templates = [", 1)[1][:2500]
    assert "derive_source_bound_foreshadowing" in body, body[:200]
