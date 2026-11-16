"""Instruction 68 (E3): a repair may not add a paragraph that restates the chapter."""

from bookengine.repair_additions import added_paragraph_findings, guard_repair_additions

# Shape of 《散修丹途》 chapter 15: the conflict repair re-ran a scene and copied three earlier lines.
DRAFT = (
    "顾夏珩抬脚将算盘踢远，俯身盯着那滴黑水。\n\n"
    "“秦野，今日在黑市引火，是你自己找死。”\n\n"
    "他直起身，朝廊下喝道：“所有出口，封死！”\n\n"
    "护卫们应声散开。“一只苍蝇也不许飞出去！”\n\n"
    "秦野按住胸口的火种，退进了药柜后的暗门。"
)
ADDED = (
    "秦野把算盘踢到远处，俯身盯着那滴黑水，指尖却没有离开胸口。“秦野，今日在黑市引火，是你自己找死。”"
    "顾夏珩的声音从廊下传来，“所有出口，封死！”“一只苍蝇也不许飞出去！”"
)


def test_inserted_paragraph_that_copies_earlier_dialogue_is_removed():
    repaired = DRAFT + "\n\n" + ADDED
    findings = added_paragraph_findings(DRAFT, repaired)
    assert [row["kind"] for row in findings] == ["copied_dialogue"]
    assert findings[0]["paragraph"] == 6
    text, note = guard_repair_additions(DRAFT, repaired)
    assert text == DRAFT
    assert note["reason"] == "repair_restates_chapter"


def test_rewritten_paragraph_that_restates_another_is_restored_to_the_draft():
    draft = (
        "石子翎停住脚，回头看去。双腿的酸麻尚未退去，腿骨深处却有一股沉劲沿着足底托住身体，使他稳稳站在泥地里。\n\n"
        "雷九把木棍扔给了赵虎。\n\n"
        "赵虎接住木棍，绕到石子翎身后。"
    )
    repaired = draft.replace(
        "雷九把木棍扔给了赵虎。",
        "石子翎停住脚回头看去，双腿的酸麻尚未退去，腿骨深处有一股沉劲沿着足底托住身体，他稳稳站在泥地里。",
    )
    text, note = guard_repair_additions(draft, repaired)
    assert text == draft
    assert note["findings"][0]["kind"] == "restated_paragraph"


def test_new_content_and_untouched_drafts_pass():
    repaired = DRAFT + "\n\n" + "暗门后是一条向下的石阶，尽头亮着一盏没有人点的灯，灯油的气味和药柜里的完全不同。"
    assert guard_repair_additions(DRAFT, repaired) == (repaired, {"status": "checked", "findings": []})
    assert guard_repair_additions(DRAFT, DRAFT)[1]["status"] == "checked"
    # Repetition the writer drafted is not a repair addition; other checks own it.
    doubled = DRAFT + "\n\n" + ADDED
    assert added_paragraph_findings(doubled, doubled) == []
