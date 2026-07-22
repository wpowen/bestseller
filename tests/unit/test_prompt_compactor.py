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
