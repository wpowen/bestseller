"""Planning commentary must be distinguished from concrete story actions."""

from copy import deepcopy

import pytest

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.services.narrative_contracts import (
    _has_meta_planning_language,
    validate_chapter_plan_contract,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "text",
    [
        "完成当事人对录音与约定的确认，并接受她设定的会面边界。",
        "完成仪器的正式修复、遵守检验员设定的四十分钟发言边界，交回钥匙。",
        "确认；主任设定下午三点开始会诊。",
        "确认，主持人设定每人十分钟。",
        "完成检修。管理员设定下一班的交接时间。",
        "完成检修\n管理员设定下一班的交接时间。",
        "已经完成\n角色设定被印在退回的稿纸上。",
        "已经确认\n世界观原稿被留在失物柜里。",
        "完成检修并遵守主持人设定的会面时间。",
        "确认她设定的温度，再接通试验台电源。",
        "确认设定的温度与仪表读数一致。",
        "护士核对腕带和病历，确认患者身份后带他进入诊室。",
        "门卫用访客证确认来访者身份，登记后开门。",
        "本章在前台核对申请人的证件，确认身份后退回原件。",
        "核验申请人的工作背景，发现他隐瞒了上一份合同。",
        "确认照片背景没有路人，再把申请表交给窗口。",
        "完成图像背景的修复，再检查人像边缘。",
        "完善医院的分诊体系后，护士立即接诊第一批伤员。",
        "确认冷却体系没有渗漏，工程师才启动水泵。",
        "建立救援队的通讯结构，随后带人进入隧道。",
        "完成正式修复后，图像背景恢复清晰。",
        "She confirms the applicant's identity against a passport and returns it.",
        "The nurse checks the patient's identity before the examination.",
        "She completed the repair; the chair set a forty-minute meeting limit.",
        "He confirms the image background is clear before submitting it.",
    ],
)
def test_concrete_actions_are_not_planning_commentary(text):
    assert not _has_meta_planning_language(text)


@pytest.mark.parametrize(
    "text",
    [
        "建立世界观。",
        "完善城邦的世界观体系。",
        "本章揭示角色设定。",
        "完善世界观背景。",
        "深化关系线。",
        "完成角色关系线的深化。",
        "完成设定。",
        "确认角色设定。",
        "揭示世界观。",
        "揭示终极悬念。",
        "本章功能：确认身份并深化关系线。",
        "本章目标：建立背景。",
        "通过证人讲述引入组织内部势力分裂的线索。",
        "让读者期待下一章的反转。",
        "章内必须落到这件可见事件。",
        "Chapter goal: establish the character arc.",
        "Narrative function: deepen the relationship arc.",
        "Chapter 3 closing hook brings the reader back.",
        "The visible chapter event must be a turning point.",
    ],
)
def test_clear_planning_functions_remain_detected(text):
    assert _has_meta_planning_language(text)


def _batch(**changes):
    chapter = {
        "chapter_number": 12,
        "title": "玻璃柜后的信封",
        "chapter_goal": "林岚拿着检测记录找到漏气的阀门，并要求许川当面签收。",
        "opening_situation": "两人在实验室门口核对待移交的仪器。",
        "main_conflict": "阀门持续漏气，许川却拒绝在移交单上签字。",
        "hook_description": "铅封突然弹开，柜内多出了一份没有署名的检测记录。",
        "scenes": [
            {
                "scene_number": 1,
                "scene_type": "confrontation",
                "time_label": "实验室门口，下午四点",
                "participants": ["林岚", "许川"],
                "purpose": {
                    "story": "林岚打开玻璃柜取出原始记录，请许川核对签名。",
                    "emotion": "林岚担心记录被隐瞒，仍坚持当面核验。",
                },
                "entry_state": {"summary": "仪器尚未签收，阀门仍在漏气。"},
                "exit_state": {"summary": "阀门已关闭，原始记录被放进交接袋。"},
            }
        ],
    }
    chapter.update(changes)
    return ChapterOutlineBatchInput.model_validate(
        {"batch_name": "laboratory", "chapters": [chapter]}
    )


def _report(batch):
    return validate_chapter_plan_contract(
        batch,
        identity_manifest=[{"name": "林岚", "aliases": []}, {"name": "许川", "aliases": []}],
    )


@pytest.mark.parametrize("field", ["chapter_goal", "main_conflict", "hook_description", "story"])
@pytest.mark.parametrize("meta", [False, True])
def test_public_contract_uses_precise_meta_detection_without_mutating_source(field, meta):
    text = "本章揭示角色设定。" if meta else "完成当事人对记录的确认，并接受她设定的会面边界。"
    batch = _batch()
    chapter = batch.chapters[0]
    if field == "story":
        chapter.scenes[0].purpose["story"] = text
        code = "PLAN_SCENE_STORY_PURPOSE_META"
    else:
        setattr(chapter, field, text)
        code = {
            "chapter_goal": "PLAN_CHAPTER_GOAL_META",
            "main_conflict": "PLAN_CHAPTER_CONFLICT_META",
            "hook_description": "PLAN_CHAPTER_HOOK_META",
        }[field]
    before = deepcopy(batch.model_dump(mode="json"))
    report = _report(batch)
    assert (code in {v.code for v in report.violations}) is meta
    if meta:
        assert report.blocks
    assert batch.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "hook",
    [
        "闭合尾钩：守夜人锁上仓门，带走账本和欠条，双方约定明早当面清账。",
        "无续集悬念的闭合尾钩：守夜人交回钥匙，仓库停用，未清债务写入账本。",
        "终章钩子：工程师归还通行证，关闭测试台，领回属于自己的工具箱。",
        "Closing hook: Mira returns the keys, signs the debt record, and leaves with her tools.",
        "Final hook: The ferry reaches the quay and the two passengers part with their own copies.",
        "守夜人交回钥匙，仓库停用，欠条由双方各留一份。",
    ],
)
def test_public_hook_reader_accepts_actual_tail_hook_and_concrete_ending_labels(hook):
    batch = _batch(hook_description="", tail_hook=hook)
    before = deepcopy(batch.model_dump(mode="json"))
    codes = {v.code for v in _report(batch).violations}
    assert not codes & {"PLAN_CHAPTER_HOOK_GENERIC", "PLAN_CHAPTER_HOOK_META"}
    assert batch.model_dump(mode="json") == before


@pytest.mark.parametrize(
    ("description", "tail", "code"),
    [
        (None, None, "PLAN_CHAPTER_HOOK_GENERIC"),
        (" ", " ", "PLAN_CHAPTER_HOOK_GENERIC"),
        ("", "尾钩：", "PLAN_CHAPTER_HOOK_GENERIC"),
        ("", "尾钩：采用三层悬念叠加", "PLAN_CHAPTER_HOOK_GENERIC"),
        ("", "尾钩：出现新的证据、时限或代价", "PLAN_CHAPTER_HOOK_GENERIC"),
        ("", "Closing hook: new evidence, deadline, or cost", "PLAN_CHAPTER_HOOK_GENERIC"),
        ("", "尾钩：让读者期待下一章。", "PLAN_CHAPTER_HOOK_META"),
        ("", "读者攻略尾钩：带走钥匙。", "PLAN_CHAPTER_HOOK_META"),
        ("本章功能：完善世界观。", "守夜人交回钥匙，仓库停用。", "PLAN_CHAPTER_HOOK_META"),
    ],
)
def test_hook_alias_and_label_view_never_bypass_empty_recipe_or_explicit_description(
    description, tail, code
):
    batch = _batch(hook_description=description, tail_hook=tail)
    before = deepcopy(batch.model_dump(mode="json"))
    report = _report(batch)
    assert code in {v.code for v in report.violations}
    assert report.blocks
    assert batch.model_dump(mode="json") == before
