"""Repair residual outline hotspots for paused 《道种破虚》.

The broad structural outline repair removes generic templates across the full
range. This script handles the remaining deterministic hotspot where chapters
379-401 still shared similar goals, hook types, and placeholder scene purposes.

It repairs planning inputs and queues prose regeneration. It does not edit
existing prose directly.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

_THIS = Path(__file__).resolve()
_SRC = _THIS.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.domain.enums import ArtifactType, ChapterStatus  # noqa: E402
from bestseller.domain.planning import PlanningArtifactCreate  # noqa: E402
from bestseller.infra.db.models import (  # noqa: E402
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    RewriteTaskModel,
    SceneCardModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.projects import import_planning_artifact  # noqa: E402


PROJECT_SLUG = "xianxia-upgrade-1776137730"
REPORT_DIR = Path("artifacts/daozhong_repair_audit")
REPAIR_SOURCE = "structural_outline_hotspot_repair_v1"


def scene(
    scene_type: str,
    title: str,
    task: str,
    state_change: str,
    location: str = "",
) -> dict[str, str]:
    return {
        "scene_type": scene_type,
        "title": title,
        "task": task,
        "state_change": state_change,
        "location": location,
    }


PATCHES: dict[int, dict[str, Any]] = {
    379: {
        "title": "碎影死线",
        "goal": "宁尘在碎影死线中发现敌方封锁不是人手围堵，而是覆盖整片区域的碎影阵；他必须用因果感应找出阵眼，而不是硬闯。",
        "conflict": "碎影阵会把任何正面突围拆成多重误影，宁尘每次出手都会暴露一条因果痕迹。",
        "hook_type": "阵眼反转",
        "hook": "宁尘锁定阵眼时发现阵眼借用的是他上一战留下的因果残痕，破阵等于先承认自己暴露过底牌。",
        "scenes": [
            scene("array_pressure", "碎影阵压境", "宁尘和陆沉误入碎影阵边缘，确认正面突围只会复制更多误影。", "宁尘从战斗目标改为寻找阵眼。", "碎影阵外缘"),
            scene("cause_scan", "因果痕迹试探", "宁尘以道种感应排查碎影流向，萧无咎的误导逼他暴露一缕因果痕迹。", "宁尘获得阵眼方位，同时留下可被追踪的代价。", "阵纹断坡"),
            scene("array_eye_reversal", "旧痕成眼", "宁尘抵达阵眼，发现阵眼绑定的是自己上一战遗留的因果残痕。", "破阵选择被转化为是否承认底牌暴露的钩子。", "碎影阵眼"),
        ],
    },
    381: {
        "title": "残局封锁",
        "goal": "宁尘在残局封锁中确认敌人真正目标是切断他与陆沉、萧无咎的联络链；他必须选择保人还是保情报。",
        "conflict": "封锁令不杀宁尘，却逐个冻结他的外部支点，任何求援都会把盟友拖入审讯。",
        "hook_type": "盟友断联",
        "hook": "陆沉留下的传讯玉简只响了一声便碎裂，碎片里反映出萧无咎被迫站在封锁阵另一侧。",
        "scenes": [
            scene("communication_blockade", "传讯链熄灭", "宁尘尝试联系陆沉，发现所有传讯都被封锁令反向登记。", "宁尘确认敌人要切断联络链，而不是立即围杀。", "废阵通讯点"),
            scene("ally_cost_choice", "保人或保情报", "宁尘发现继续求援会把陆沉和萧无咎拖入审讯，只能在保人和保情报之间取舍。", "宁尘牺牲一段情报链，换取盟友暂时不被锁定。", "封锁令边界"),
            scene("forced_alignment_hook", "碎玉照影", "传讯玉简碎裂，碎片映出萧无咎站在封锁阵另一侧的身影。", "盟友立场被强行制造成下一章的直接危机。", "残阵中心"),
        ],
    },
    384: {
        "title": "雾锁逼近",
        "goal": "宁尘在雾锁逼近中发现旧日废灵根记录被人翻出，叶长青准备用这份档案逼盟友切割。",
        "conflict": "档案里的旧名与宁尘隐藏功法痕迹相连，一旦公开，陆沉和苏瑶都会被迫表态。",
        "hook_type": "旧档案曝光",
        "hook": "雾锁尽头出现宁尘入宗时的废灵根判词，上面多出一行从未见过的朱批。",
        "scenes": [
            scene("archive_exposure", "废灵根旧档", "宁尘在雾锁里看见自己的入宗档案被重新封签，判词指向废灵根旧名。", "旧档案从背景设定变成当前威胁。", "雾锁档案廊"),
            scene("alliance_pressure", "盟友被迫表态", "叶长青用档案副本逼陆沉和苏瑶切割，宁尘必须阻止盟友被公开卷入。", "宁尘保住盟友立场，却暴露功法痕迹疑点。", "雾墙议事台"),
            scene("verdict_hook", "朱批多出一行", "宁尘夺回判词时发现多出一行陌生朱批，内容指向道种来源。", "旧档案问题升级成追查道种来源的钩子。", "雾锁尽头"),
        ],
    },
    385: {
        "title": "暗潮崩弦",
        "goal": "宁尘在暗潮崩弦中追查叶长青暗线的施压节奏，必须在暗线全面收紧前夺回一条资源通道。",
        "conflict": "暗线不直接攻击宁尘，而是连续压断补给、情报和落脚点，让每次反击都更昂贵。",
        "hook_type": "资源弦断",
        "hook": "宁尘夺回的通道只维持半刻便自行断裂，断口处留下叶长青预先写好的第二层调令。",
        "scenes": [
            scene("pressure_probe", "暗线节奏", "宁尘比对三处补给断点，确认暗潮按固定节奏压迫他的行动窗口。", "宁尘找到可反向利用的半刻空隙。", "暗潮补给巷"),
            scene("resource_breakpoint", "夺回短道", "宁尘冒险夺回一条资源通道，却必须放弃另一处落脚点。", "资源状态从全面被动变成短暂可用。", "断桥仓口"),
            scene("string_snap_hook", "弦断调令", "通道突然断裂，叶长青第二层调令暴露敌人早已预判宁尘的夺路选择。", "夺回行动反变成更深伏线。", "通道断口"),
        ],
    },
    386: {
        "title": "断点加压",
        "goal": "宁尘在断点加压中抓住能改写局势的断点线索，却必须承受一名外围接应者被牵连的损失。",
        "conflict": "线索越接近真相，敌方越用外围人命加压，逼宁尘在查证和止损之间分裂。",
        "hook_type": "线索换损",
        "hook": "接应者留下的最后半句不是求救，而是指出断点背后还有一个更高层的令牌编号。",
        "scenes": [
            scene("clue_cost", "断点线索", "宁尘从残缺令符中读出断点位置，同时得知外围接应者已被牵连。", "关键线索获得，但救援时间被压缩。", "残符暗室"),
            scene("loss_choice", "查证与止损", "宁尘必须决定先查证令符来源，还是先切断敌人对接应者的追索。", "宁尘保住部分人证，却失去完整口供。", "接应暗巷"),
            scene("trail_break_hook", "令牌编号", "接应者留下半句编号，指向比叶长青更高层的调度源。", "断点从局部线索升级成上层令牌。", "暗巷尽头"),
        ],
    },
    387: {
        "title": "变局失衡",
        "goal": "宁尘在变局失衡中发现威胁盟友关系的不是旧秘密本身，而是有人伪造了他背叛同盟的证据。",
        "conflict": "伪证同时指向陆沉与苏瑶，宁尘若急于辩解反而会坐实有人替他遮掩。",
        "hook_type": "伪证反咬",
        "hook": "苏瑶带来第二份证据，落款竟是宁尘只在道种识海中见过的因果印记。",
        "scenes": [
            scene("forged_evidence", "背盟伪证", "宁尘看到伪造的背盟证据，发现证词同时压向陆沉和苏瑶。", "威胁从旧秘密转为伪证攻击。", "临时会审厅"),
            scene("trust_crossfire", "不能急辩", "宁尘克制辩解，转而让陆沉保留怀疑，以便诱出伪证来源。", "盟友信任被短暂降级，但获得反查空间。", "会审厅侧廊"),
            scene("cause_mark_hook", "因果落款", "苏瑶拿来第二份证据，落款是宁尘只在道种识海见过的因果印记。", "伪证问题连接到道种识海。", "侧廊封印门"),
        ],
    },
    389: {
        "title": "冷锋换轨",
        "goal": "宁尘在冷锋换轨中被迫接受一名旧敌的临时合作，但必须先验证对方递来的冷锋路线是否藏着换轨陷阱。",
        "conflict": "旧敌提供的路线能避开封锁，却会让宁尘失去原本掌握的追踪节奏；信任一步错，陆沉就会被引向假出口。",
        "hook_type": "旧敌换轨",
        "hook": "冷锋路线贯通的一刻，宁尘发现旧敌交出的不是完整地图，而是一枚只对陆沉血气有反应的换轨符。",
        "scenes": [
            scene("cold_route_offer", "旧敌递路", "旧敌在封锁缺口递出冷锋路线，宁尘必须先判断这是不是叶长青的二次诱导。", "合作从口头试探进入可验证路线。", "冷锋缺口"),
            scene("trust_verification", "路线试真", "宁尘用因果残痕验证路线，发现它能避开封锁但会改变陆沉的撤离方向。", "宁尘确认路线半真半假，必须重新设计换轨方式。", "封锁斜桥"),
            scene("ally_route_cost", "陆沉假出口", "陆沉的撤离标记被路线牵向假出口，宁尘临时切断原计划。", "宁尘保住陆沉位置，却暴露自己正在反向追踪。", "斜桥尽头"),
            scene("blood_talisman_hook", "血气换轨符", "冷锋路线贯通后，旧敌交出的换轨符只对陆沉血气有反应。", "合作问题升级成陆沉身份与换轨符来源的钩子。", "冷锋内线"),
        ],
    },
    390: {
        "title": "沉渊翻盘",
        "goal": "宁尘在沉渊翻盘中利用敌方以为他资源耗尽的误判，反向逼出沉渊线人的真实交易条件。",
        "conflict": "线人愿意交出关键情报，但要求宁尘先承认一笔会削弱同盟声望的旧债。",
        "hook_type": "旧债翻盘",
        "hook": "线人交出的不是名单，而是一张写着陆沉旧债的沉渊契页。",
        "scenes": [
            scene("abyss_bargain", "沉渊线人", "宁尘假装资源耗尽，逼沉渊线人提前提出交易条件。", "宁尘从被动追查转为主动谈判。", "沉渊暗阶"),
            scene("turning_point", "旧债条件", "线人要求宁尘承认一笔旧债，代价是同盟声望受损。", "宁尘换到情报入口，但背上可被攻击的名义代价。", "暗阶水镜"),
            scene("hidden_debt_hook", "契页非名单", "线人交出的契页写着陆沉旧债，而不是宁尘预期的敌方名单。", "翻盘成果转成盟友旧债钩子。", "沉渊契台"),
        ],
    },
    391: {
        "title": "荒火脱钩",
        "goal": "宁尘在荒火脱钩中处理先前埋下的荒火印，必须切断它对同盟据点的追踪。",
        "conflict": "荒火印会沿着人情债追踪，越是亲近宁尘的人越先被锁定。",
        "hook_type": "人情债反噬",
        "hook": "宁尘割断荒火印后，因果账簿上反而出现了陆沉欠下的第一笔代价。",
        "scenes": [
            scene("fire_mark_trace", "荒火印回燃", "宁尘发现荒火印沿人情债追向同盟据点，常规遮掩已经无效。", "追踪机制从术法变成人情债。", "荒火残坛"),
            scene("debt_cost", "谁先被锁", "宁尘切断荒火印时发现越亲近的人越先被锁，只能主动承担账簿反噬。", "宁尘保住据点位置，却把代价转到自己与陆沉的因果账上。", "残坛内圈"),
            scene("ledger_hook", "陆沉第一债", "因果账簿显出陆沉欠下的第一笔代价，荒火印只是收债标记。", "荒火线转向陆沉旧债。", "因果账簿前"),
        ],
    },
    392: {
        "title": "盲区重铸",
        "goal": "宁尘在盲区重铸中把敌方制造的视野盲区改造成诱敌路线，争取重新掌握队伍移动权。",
        "conflict": "盲区会吞掉灵识锚点，宁尘若借盲区诱敌，就必须短暂失去对盟友方位的确认。",
        "hook_type": "锚点失明",
        "hook": "盲区被重铸后没有亮起，反而吞下宁尘留给陆沉的最后一个方位锚点。",
        "scenes": [
            scene("blind_spot_map", "盲区成图", "宁尘绘出敌方盲区边界，发现它能被反向改造成诱敌路线。", "盲区从威胁变成可控工具。", "灵识盲坡"),
            scene("route_recast", "诱敌路线", "宁尘以一枚锚点为代价重铸路线，引导追兵偏离同盟撤离方向。", "队伍移动权被暂时夺回。", "盲坡内线"),
            scene("anchor_shift_hook", "最后锚点失明", "重铸完成后，陆沉方向的最后锚点被盲区吞没。", "行动优势立刻换成盟友方位危机。", "盲区出口"),
        ],
    },
    393: {
        "title": "锈迹逆转",
        "goal": "宁尘在锈迹逆转中被迫进入锈迹地界，必须用有限资源辨认这里的旧规则，避免被叶长青的棋局牵走。",
        "conflict": "锈迹地界会腐蚀常规灵力标记，宁尘越依赖旧手段，越会失去方向。",
        "hook_type": "地界旧规",
        "hook": "宁尘识破第一条旧规则时，地面锈纹拼出一个不属于叶长青的古老警告。",
        "scenes": [
            scene("rusted_border_trap", "锈界落子", "叶长青把宁尘逼入锈迹地界，宁尘发现常规灵力标记开始腐蚀。", "地界规则成为本章首要敌人。", "锈迹边界"),
            scene("scarce_resource", "省下最后标记", "宁尘放弃追击，保留最后一枚灵力标记来测试旧规则。", "资源消耗被压低，但节奏被敌方牵制。", "锈纹甬道"),
            scene("foreign_rule_hook", "锈纹古警", "锈纹拼出古老警告，证明这片地界并非叶长青所造。", "地界来源变成下一步调查目标。", "锈纹井口"),
        ],
    },
    395: {
        "title": "裂痕破局",
        "goal": "宁尘在裂痕破局中不再被动应对陌生地界，而是利用地界裂痕分化追兵，夺回行动路线。",
        "conflict": "裂痕会吞噬灵识标记，宁尘若借它脱身，也会失去对陆沉方位的感知。",
        "hook_type": "路线反夺",
        "hook": "裂痕闭合前，宁尘看见叶长青的追踪符被另一个更古老的符号吞掉。",
        "scenes": [
            scene("rift_split", "裂痕分兵", "宁尘主动引追兵靠近地界裂痕，逼他们分成两路。", "宁尘从被动逃离转为夺路。", "地界裂口"),
            scene("pursuit_division", "夺回路线", "宁尘利用裂痕错位夺回行动路线，但必须舍弃一枚陆沉方位标记。", "路线优势换来盟友定位风险。", "裂口内层"),
            scene("sense_loss_cost", "灵识被吞", "裂痕吞掉灵识标记，宁尘短暂失去陆沉方位感知。", "破局代价落到具体盟友身上。", "裂痕盲带"),
            scene("ancient_symbol_hook", "古符吞符", "叶长青追踪符被更古老的符号吞掉，说明第三方规则正在介入。", "裂痕线索指向更古老势力。", "裂痕闭合处"),
        ],
    },
    397: {
        "title": "浮标崩口",
        "goal": "宁尘在浮标崩口中发现追踪浮标并非失效，而是被敌人改造成诱导他判断失误的假信号。",
        "conflict": "假信号会把宁尘引向错误救援点，真正的损失发生在他看不见的另一侧。",
        "hook_type": "假信号牺牲",
        "hook": "宁尘拆穿假浮标后，真正浮标传回的最后画面是一名接应者主动切断归路。",
        "scenes": [
            scene("signal_buoy_failure", "浮标假崩", "宁尘发现浮标崩口不是故障，而是假信号诱导。", "追踪判断从依赖信号转为验证信号。", "浮标河湾"),
            scene("personal_loss", "错救代价", "宁尘放弃假救援点，承受真正接应点已经受损的后果。", "个人损失被确认，但错误路线被避免。", "河湾分岔"),
            scene("new_pattern_hook", "归路自断", "真正浮标传回接应者切断归路的画面，说明对方在保护更重要的线索。", "损失背后出现新线索。", "浮标残片处"),
        ],
    },
    400: {
        "title": "回声决堤",
        "goal": "宁尘在回声决堤中让旧秘密主动决堤，把对手准备的爆料改成反追踪陷阱。",
        "conflict": "秘密一旦公开会伤及盟友信任，但压住不放又会让对手继续掌握节奏。",
        "hook_type": "反追踪落子",
        "hook": "公开的回声没有流向叶长青，反而指向一个藏在宗门记录之外的虚界传声点。",
        "scenes": [
            scene("controlled_disclosure", "主动放声", "宁尘主动释放旧秘密的一部分，把被动爆料改成可控回声。", "旧秘密从威胁变成反追踪诱饵。", "回声石阶"),
            scene("ally_trust_cost", "信任受损", "陆沉和苏瑶必须面对被隐瞒的事实，宁尘用可验证证据争取最低信任线。", "盟友信任受损但没有崩盘。", "石阶回廊"),
            scene("countertrace_setup", "回声改流", "宁尘在公开内容里嵌入反追踪节点，等待敌方接声。", "对手节奏被迫接入宁尘布置。", "回声阵心"),
            scene("outside_record_hook", "虚界传声点", "回声没有流向叶长青，而是指向宗门记录外的虚界传声点。", "旧秘密线扩展到虚界坐标。", "回声阵外"),
        ],
    },
}


def clean(value: Any) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def chapter_items(content: Any) -> list[dict[str, Any]]:
    chapters = content.get("chapters") if isinstance(content, dict) else content
    return [item for item in chapters or [] if isinstance(item, dict)]


def artifact_chapters_by_number(content: Any) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for chapter in chapter_items(content):
        number = chapter.get("chapter_number")
        if isinstance(number, int):
            out[number] = chapter
    return out


def make_scene_story(chapter_number: int, scene_number: int, patch: dict[str, Any], scene_patch: dict[str, str]) -> str:
    is_last = scene_number == len(patch["scenes"])
    tail = f"尾场必须把「{patch['hook']}」具象成下一章压力。" if is_last else "本场必须推进信息、资源或盟友状态的一项变化。"
    location = scene_patch.get("location")
    location_text = f"场景落点固定在「{location}」。" if location else ""
    return (
        f"第{chapter_number}章第{scene_number}场（{scene_patch['scene_type']}）："
        f"{scene_patch['task']}本场结束时的独有状态变化是：{scene_patch['state_change']}"
        f"{location_text}{tail}服务本章目标：{patch['goal']}"
    )


def patch_artifact_chapter(chapter: dict[str, Any], chapter_number: int, patch: dict[str, Any]) -> bool:
    changed = False
    for key, value in (
        ("title", patch["title"]),
        ("chapter_title", patch["title"]),
        ("chapter_goal", patch["goal"]),
        ("goal", patch["goal"]),
        ("main_conflict", patch["conflict"]),
        ("hook_type", patch["hook_type"]),
        ("hook_description", patch["hook"]),
    ):
        if clean(chapter.get(key)) != value:
            chapter[key] = value
            changed = True

    scenes = chapter.get("scenes")
    if not isinstance(scenes, list):
        scenes = []
        chapter["scenes"] = scenes
        changed = True
    while len(scenes) < len(patch["scenes"]):
        scenes.append({"scene_number": len(scenes) + 1, "purpose": {}})
        changed = True

    for index, scene_patch in enumerate(patch["scenes"], start=1):
        scene_payload = scenes[index - 1]
        if not isinstance(scene_payload, dict):
            scene_payload = {"scene_number": index, "purpose": {}}
            scenes[index - 1] = scene_payload
            changed = True
        purpose = scene_payload.get("purpose") if isinstance(scene_payload.get("purpose"), dict) else {}
        new_story = make_scene_story(chapter_number, index, patch, scene_patch)
        updates: dict[str, Any] = {
            "scene_number": index,
            "scene_type": scene_patch["scene_type"],
            "title": scene_patch["title"],
            "goal": scene_patch["task"],
            "story_task": scene_patch["task"],
            "main_conflict": patch["conflict"],
            "location": scene_patch.get("location", ""),
            "purpose": {
                **purpose,
                "story": new_story,
                "emotion": "压力、判断和代价同步升级",
            },
        }
        for key, value in updates.items():
            if scene_payload.get(key) != value:
                scene_payload[key] = value
                changed = True
    return changed


async def repair(*, execute: bool, create_tasks: bool) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    counts: Counter[str] = Counter()
    changed_chapters: list[int] = []
    repaired_artifact_version: int | None = None

    async with session_scope() as session:
        project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))
        if project is None:
            raise SystemExit(f"project not found: {PROJECT_SLUG}")

        latest_artifact = await session.scalar(
            select(PlanningArtifactVersionModel)
            .where(
                PlanningArtifactVersionModel.project_id == project.id,
                PlanningArtifactVersionModel.artifact_type == ArtifactType.CHAPTER_OUTLINE_BATCH.value,
            )
            .order_by(PlanningArtifactVersionModel.version_no.desc())
            .limit(1)
        )
        if latest_artifact is None:
            raise SystemExit("latest chapter_outline_batch artifact not found")

        repaired_content = copy.deepcopy(latest_artifact.content)
        artifact_by_number = artifact_chapters_by_number(repaired_content)
        for chapter_number, patch in PATCHES.items():
            artifact_chapter = artifact_by_number.get(chapter_number)
            if artifact_chapter is None:
                counts["artifact_chapters_missing"] += 1
                continue
            if patch_artifact_chapter(artifact_chapter, chapter_number, patch):
                counts["artifact_chapters_repaired"] += 1

        chapters = list(
            await session.scalars(
                select(ChapterModel)
                .where(
                    ChapterModel.project_id == project.id,
                    ChapterModel.chapter_number.in_(PATCHES.keys()),
                )
                .order_by(ChapterModel.chapter_number.asc())
            )
        )
        chapters_by_number = {chapter.chapter_number: chapter for chapter in chapters}
        scenes = list(
            await session.scalars(
                select(SceneCardModel)
                .where(SceneCardModel.chapter_id.in_([chapter.id for chapter in chapters]))
                .order_by(SceneCardModel.chapter_id.asc(), SceneCardModel.scene_number.asc())
            )
        )
        scenes_by_chapter: dict[int, list[SceneCardModel]] = {number: [] for number in PATCHES}
        id_to_number = {chapter.id: chapter.chapter_number for chapter in chapters}
        for scene_card in scenes:
            chapter_number = id_to_number.get(scene_card.chapter_id)
            if chapter_number is not None:
                scenes_by_chapter.setdefault(chapter_number, []).append(scene_card)

        for chapter_number, patch in PATCHES.items():
            chapter = chapters_by_number.get(chapter_number)
            if chapter is None:
                counts["db_chapters_missing"] += 1
                continue

            chapter_changed = False
            field_updates = {
                "title": patch["title"],
                "chapter_goal": patch["goal"],
                "main_conflict": patch["conflict"],
                "hook_type": patch["hook_type"],
                "hook_description": patch["hook"],
            }
            for field, value in field_updates.items():
                if clean(getattr(chapter, field, None)) != value:
                    counts[f"db_{field}_repaired"] += 1
                    chapter_changed = True
                    if execute:
                        setattr(chapter, field, value)

            for scene_card in scenes_by_chapter.get(chapter_number, []):
                if scene_card.scene_number < 1 or scene_card.scene_number > len(patch["scenes"]):
                    continue
                scene_patch = patch["scenes"][scene_card.scene_number - 1]
                new_story = make_scene_story(chapter_number, scene_card.scene_number, patch, scene_patch)
                new_purpose = {
                    **(scene_card.purpose or {}),
                    "story": new_story,
                    "emotion": "压力、判断和代价同步升级",
                }
                scene_updates = {
                    "scene_type": scene_patch["scene_type"],
                    "title": scene_patch["title"],
                    "purpose": new_purpose,
                }
                for field, value in scene_updates.items():
                    if getattr(scene_card, field, None) != value:
                        counts[f"db_scene_{field}_repaired"] += 1
                        chapter_changed = True
                        if execute:
                            setattr(scene_card, field, value)
                if execute:
                    scene_card.metadata_json = {
                        **(scene_card.metadata_json or {}),
                        "structural_outline_hotspot_repaired": True,
                        "structural_outline_hotspot_repaired_at": now,
                        "structural_outline_hotspot_source": REPAIR_SOURCE,
                    }

            if chapter_changed:
                changed_chapters.append(chapter_number)
                if execute:
                    chapter.status = ChapterStatus.REVISION.value
                    chapter.metadata_json = {
                        **(chapter.metadata_json or {}),
                        "structural_repair_required": True,
                        "structural_outline_hotspot_repaired": True,
                        "structural_outline_hotspot_repaired_at": now,
                        "structural_outline_hotspot_source": REPAIR_SOURCE,
                        "structural_repair_codes": sorted(
                            set((chapter.metadata_json or {}).get("structural_repair_codes", []))
                            | {"RESIDUAL_OUTLINE_HOTSPOT"}
                        ),
                    }

        if execute:
            artifact = await import_planning_artifact(
                session,
                PROJECT_SLUG,
                PlanningArtifactCreate(
                    artifact_type=ArtifactType.CHAPTER_OUTLINE_BATCH,
                    content=repaired_content,
                    notes=(
                        f"{REPAIR_SOURCE}: repaired residual planning hotspots "
                        f"for chapters {min(PATCHES)}-{max(PATCHES)}"
                    ),
                ),
            )
            repaired_artifact_version = artifact.version_no
            project.status = "paused"
            project.metadata_json = {
                **(project.metadata_json or {}),
                "production_paused": True,
                "production_pause_reason": "structural_repair_before_continuation",
                "generation_resume_blocked_until_repair_audit": True,
                "structural_outline_hotspot_repaired_at": now,
                "structural_outline_hotspot_source_artifact_version": latest_artifact.version_no,
                "structural_outline_hotspot_repaired_artifact_version": repaired_artifact_version,
            }

        if create_tasks:
            existing_tasks = list(
                await session.scalars(
                    select(RewriteTaskModel).where(
                        RewriteTaskModel.project_id == project.id,
                        RewriteTaskModel.status.in_(["pending", "queued"]),
                    )
                )
            )
            existing_target_chapters = {
                int((task.metadata_json or {}).get("chapter_number"))
                for task in existing_tasks
                if isinstance((task.metadata_json or {}).get("chapter_number"), int)
            }
            created = 0
            for chapter_number in sorted(set(PATCHES) - existing_target_chapters):
                chapter = chapters_by_number.get(chapter_number)
                if chapter is None:
                    continue
                if execute:
                    session.add(
                        RewriteTaskModel(
                            project_id=project.id,
                            trigger_type="structural_outline_hotspot_repair",
                            trigger_source_id=chapter.id,
                            rewrite_strategy="chapter_outline_regeneration",
                            priority=1,
                            status="pending",
                            instructions=(
                                f"第{chapter_number}章已完成规划热点修复。请基于新的 chapter_goal、"
                                "main_conflict、hook、scene purpose 重新生成章节，不得复用旧的"
                                "开场/推进/尾钩占位任务或通用生存压力模板。"
                            ),
                            context_required=["story_bible", "identity_manifest", "chapter_outline"],
                            metadata_json={
                                "chapter_id": str(chapter.id),
                                "chapter_number": chapter_number,
                                "source": REPAIR_SOURCE,
                                "source_artifact_version": latest_artifact.version_no,
                                "created_at": now,
                            },
                        )
                    )
                created += 1
            counts["rewrite_tasks_created"] = created

        if execute:
            await session.flush()

        report = {
            "project": {"slug": project.slug, "title": project.title, "status": project.status},
            "execute": execute,
            "created_at": now,
            "latest_artifact_version": latest_artifact.version_no,
            "repaired_artifact_version": repaired_artifact_version,
            "patched_chapters": sorted(PATCHES),
            "changed_chapters": changed_chapters,
            "counts": dict(counts),
        }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "hotspot_repair_execute" if execute else "hotspot_repair_dry_run"
    json_path = REPORT_DIR / f"{PROJECT_SLUG}_{min(PATCHES)}_{max(PATCHES)}_{suffix}.json"
    md_path = REPORT_DIR / f"{PROJECT_SLUG}_{min(PATCHES)}_{max(PATCHES)}_{suffix}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    report["output"] = {"json": str(json_path), "markdown": str(md_path)}
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 《道种破虚》残留规划热点修复",
        "",
        f"- 执行写入：{report['execute']}",
        f"- 来源 artifact v{report['latest_artifact_version']}",
        f"- 新 artifact v{report['repaired_artifact_version']}",
        f"- 修复章节：{', '.join(str(item) for item in report['patched_chapters'])}",
        "",
        "## 计数",
        "",
    ]
    for key, value in sorted(report["counts"].items()):
        lines.append(f"- `{key}`: {value}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--create-tasks", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(repair(execute=args.execute, create_tasks=args.create_tasks))
    print(
        json.dumps(
            {
                "execute": report["execute"],
                "counts": report["counts"],
                "changed_chapters": report["changed_chapters"],
                "output": report["output"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
