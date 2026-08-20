from __future__ import annotations

from bestseller.services.prompt_compactor import compact_user_prompt


def test_chapter_first_hard_contracts_survive_prompt_cap() -> None:
    filler_sections = "\n".join(
        f"【普通上下文{i}】\n" + ("可删规划信息" * 160)
        for i in range(24)
    )
    raw = (
        "【任务】\n一次性写完整章节。\n"
        + filler_sections
        + "\n【字数与结构】\n发布硬范围 2500-3500 字，目标约2800字。"
        + "\n【隐藏节点执行规则】\n节点只作为连续叙事的隐藏事件。"
        + "\n【整章逻辑合同·隐藏硬事实】\n压力产生→判断→选择→反制→代价。"
        + "\n【AI套话黑名单】\n禁止结论先行和身体模板。"
        + "\n【章末收尾钩子】\n最后200字让木匣铅封被当场替换。"
    )

    compacted, report = compact_user_prompt(
        raw,
        chapter_no=1,
        forbidden_terms_full=[],
    )

    assert report.compacted_chars < report.original_chars
    assert "发布硬范围 2500-3500 字" in compacted
    assert "整章逻辑合同·隐藏硬事实" in compacted
    assert "隐藏节点执行规则" in compacted
    assert "AI套话黑名单" in compacted
    assert "章末收尾钩子" in compacted


# ── 连贯性核心不得被"位置+大小的意外"淘汰（2026-08-20 真机定罪）──────────
# 《罚我守坟》全部 18 章的写手 user prompt 都撞 10k 上限：单块上限之和
# （章节契约3500+故事圣经3200+活动主线3000+时间线2800+近期摘要1800≈14.3k）
# 结构性超出预算 ~1.4 倍，必然淘汰。而淘汰顺序由"谁靠前、谁小"决定——
# 最大的那块（近期章节摘要 / 活动主线伏笔）永远第一个被跳过，靠后的小块
# 反而活下来。写手因此在后半段拿不到前情与伏笔账本 = 后半段文风劣化。
# 消融证据（prose-prompt-diet）：PLAN 层价值 > CONST > CRAFT，
# 所以淘汰必须从 CRAFT 侧开始，而不是把 PLAN 层当填充物。


def _fat(marker: str, n: int = 1400) -> str:
    return f"\n【{marker}】\n" + ("上下文正文" * n)


def test_continuity_core_survives_over_generic_filler() -> None:
    raw = (
        "【任务】\n一次性写完整章节。"
        + _fat("章节契约", 400)
        + _fat("故事圣经上下文", 700)
        + _fat("近期章节/场景摘要", 300)
        + _fat("活动主线/伏笔/回收", 360)
        + _fat("时间线与硬事实快照", 300)
        + _fat("检索补充", 500)
        + "\n【删减策略】\n只写正文。"
    )
    compacted, report = compact_user_prompt(
        raw, chapter_no=18, forbidden_terms_full=[]
    )

    assert report.compacted_chars < report.original_chars, "本例必须触发淘汰"
    # 连贯性核心：前情摘要与伏笔账本是写手接得住上一章的唯一来源
    assert "近期章节/场景摘要" in compacted
    assert "活动主线/伏笔/回收" in compacted
    assert "时间线与硬事实快照" in compacted, (
        "旧逻辑按位置填充，时间线排在故事圣经之后必被挤掉"
    )
    assert "章节契约" in compacted
    # 预算没有变大：让位的是通用参考层，不是连贯性层
    assert "故事圣经上下文" not in compacted
    assert any("故事圣经" in name for name in report.evicted_sections)


def test_eviction_is_never_silent() -> None:
    raw = (
        "【任务】\n写正文。"
        + "".join(_fat(f"可删规划{i}", 500) for i in range(6))
        + "\n【删减策略】\n只写正文。"
    )
    _, report = compact_user_prompt(raw, chapter_no=9, forbidden_terms_full=[])
    assert report.evicted_sections, "被扔掉的段必须留痕，否则是静默失效"
    assert any("可删规划" in name for name in report.evicted_sections)
