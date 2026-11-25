"""Plain-language rhythm guidance passed to the prose writer."""

from .chapter_rhythm import chapter_type, relationship_contract
from .q1_schema import rows
from .relationship_revisit import GUIDANCE as REVISIT_GUIDANCE


NONCOMBAT = {"情感", "日常", "收获", "揭秘", "谋划"}
FOCUS = {
    "情感": "两场戏都围绕关系变化；调查、开门、救援或能力使用只能作背景，不能占一整场。",
    "日常": "两场戏只写休整、照料、分配或生活行进，不展开路线论证、机关破解或战斗。",
    "收获": "两场戏实际领取、试用或分配所得。写清东西到了谁手里、让他办成什么事；不再争夺，也不展开新调查。",
    "揭秘": "两场戏都用现场线索和证据核对并推翻旧判断，不能改写成路线分工或战斗。",
    "谋划": "两场戏只比较方案、风险、代价并完成取舍与分工；救援、开门、追踪或借力等执行动作移到相邻章。",
}


def writer_hook_type(outline, scheduled, canon, ending):
    if ending:
        return "完结收束"
    if outline.get("ending_content"):
        return None  # Concrete endings have their own vocabulary; never grade against a second target.
    if outline.get("hook") and outline.get("hook_type") in canon["hook_types"]:
        return outline["hook_type"]
    return scheduled


def writer_main_scene_contract(outline):
    """Exact outline obligations placed before style guidance in the writer prompt."""
    kind = chapter_type(outline) or outline.get("rhythm_type") or "未定"
    scenes = []
    for scene in rows(outline.get("scenes")):
        detail = {key: scene.get(key, "") for key in ("goal", "action", "choice", "result")}
        if kind in ("对抗", "揭秘"):
            detail.update({key: scene[key] for key in ("where", "obstacle") if scene.get(key)})
        scenes.append(detail)
    contract = {
        "chapter_type": kind,
        "rhythm_focus": outline.get("rhythm_focus", ""),
        "scenes": scenes,
        "relationship_change": relationship_contract(outline) or outline.get("relationship_change"),
    }
    if outline.get("ending"):
        contract["hook"] = outline.get("hook", "")
    elif ending := outline.get("ending_content"):
        last_result = scenes[-1]["result"] if scenes else None
        same_result = (
            isinstance(ending, str) and isinstance(last_result, str) and ending.rstrip("。") == last_result.rstrip("。")
        )
        contract["hook"] = "以末场结果收束" if same_result else ending
    else:
        contract.update(hook_type=outline.get("hook_type", ""), hook=outline.get("hook", ""))
    if kind in ("对抗", "揭秘"):
        noticed = next(
            (
                step["detail"]
                for step in rows(outline.get("solution_steps"))
                if step.get("role") == "感知" and step.get("detail")
            ),
            None,
        )
        if noticed:
            contract["人物据以判断的现场细节"] = noticed
    return contract


def remaining_outline(outline, contract):
    """Leave details already given in the main scene section out of the secondary outline."""
    rest = {k: v for k, v in outline.items() if k not in ("rhythm_type", "rhythm_focus", "hook_type", "hook")}
    scenes = outline.get("scenes")
    if isinstance(scenes, list) and len(scenes) == len(contract["scenes"]):
        rest["scenes"] = [
            {key: val for key, val in scene.items() if key not in shown or val != shown[key]}
            for scene, shown in zip(scenes, contract["scenes"], strict=True)
        ]
    application = outline.get("core_application")
    if isinstance(application, str) and application:
        # The ability planner may construct this field verbatim from action and result.
        for scene in contract["scenes"]:
            action, result = scene.get("action"), scene.get("result")
            if not isinstance(action, str) or not isinstance(result, str):
                continue
            if application in (action, action.rstrip("。；") + "；" + result):
                rest.pop("core_application", None)
                break
    return rest


def writer_rhythm_instruction(outline, *, revisit=None):
    kind = chapter_type(outline) or outline.get("rhythm_type") or "未定"
    if revisit:
        instruction = (
            f"这次要由两人当场作出的选择：{revisit['scene']['choice']}\n"
            f"第{revisit['chapter']}章两人已经历的事：{revisit['shared_event']}\n" + REVISIT_GUIDANCE
        )
    else:
        instruction = "本章主场景硬合同（优先于文风与局部措辞）见下文。"
    instruction += f"\n本章章型：{kind}。"
    if kind == "对抗":
        instruction += (
            "人物动手时，沿章纲里的位置与阻碍写他注意到了什么、改了哪一下。有人帮忙时，让同伴所受的影响看得见。"
        )
        if outline.get("opponent_resolution"):
            instruction += "若处置对手还改变了旁人的待遇，交代权利落到谁手里；他实际用上时，再写生活的变化。"
    if kind in NONCOMBAT:
        instruction += (
            "本章不安排正面打斗；对手只能作为消息或远处压力，不得在两场戏中到场动手、追杀、突围或反杀。"
            "若章纲中的局部动作与本章章型冲突，保留必须推进的剧情事实，但不得照抄与章型冲突的动作。" + FOCUS[kind]
        )
    change = relationship_contract(outline) or outline.get("relationship_change")
    if kind == "情感" and isinstance(change, dict) and change and not revisit:
        people = "、".join(change.get("people", [])) if isinstance(change.get("people"), list) else ""
        instruction += (
            f"关系变化合同：人物={people}；起因={change.get('cause', '')}；"
            f"变化前={change.get('before', '')}；变化后={change.get('after', '')}。"
            "两场主场景都要把人物关系从变化前推到变化后：坦白、试探、拒绝、让步或和解是主线，"
            "对方必须当场作出不同于action的choice并产生反应，不能只在结尾补一句关系总结。"
            "追杀、闯关、受控搏斗和机关执行最多一笔带过，不能占据主场景。"
        )
    return instruction
