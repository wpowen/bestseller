"""Rewind 《青囊不语问阴阳》 to the clean chapter-7 frontier.

This is a one-off operational repair for output/exorcist-detective-1778051012.
It clears downstream generated state, sanitizes persisted prompt context, and
rebuilds planned chapter/scene rows for chapters 8-50 from clean contracts.
"""

# ruff: noqa: ANN001, ANN401, E501, I001, RUF001

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text

from bestseller.domain.enums import ChapterStatus, ProjectStatus, SceneStatus
from bestseller.infra.db.models import (
    CharacterModel,
    ChapterDraftVersionModel,
    ChapterModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    RetrievalChunkModel,
    SceneCardModel,
    SceneDraftVersionModel,
    VolumeModel,
)
from bestseller.infra.db.session import create_engine, create_session_factory
from bestseller.services.narrative_contracts import (
    build_identity_manifest,
    validate_foundation_identity_contract,
)
from bestseller.settings import load_settings


PROJECT_SLUG = "exorcist-detective-1778051012"
ROOT = Path("output") / PROJECT_SLUG
FRONTIER = 7
REWRITE_FROM = 8
FIRST_VOLUME_END = 50

REPLACEMENTS = [
    ("守夜人组织内部叛徒派系", "张家开门人旧线"),
    ("镜中局游戏系统", "镜债规则"),
    ("死亡游戏APP", "镜债凶讯"),
    ("灵异游戏APP", "镜债凶讯"),
    ("游戏APP", "镜债凶讯"),
    ("APP", "镜债凶讯"),
    ("死亡副本", "镜中凶局"),
    ("副本", "镜局"),
    ("玩家ID", "入局者名帖"),
    ("玩家", "入局者"),
    ("游戏系统", "镜债规则"),
    ("游戏", "镜局"),
    ("守夜人组织", "三族旧盟"),
    ("守夜人", "张家开门人"),
    ("北马沈家", "沈家旧卷"),
    ("北马", "出马仙"),
    ("陈守正", "陈家旧账"),
    ("周德昌", "旧案证人"),
    ("张德福", "张建军"),
    ("裴正阳", "张家开门人"),
    ("钱桂芬", "钱婆婆"),
    ("父亲林远山", "曾祖父林远山"),
    ("林远山（父）", "林远山（曾祖）"),
    ("林远山父亲", "林远山曾祖"),
]

FORBIDDEN_ACTIONS = [
    "不得出现玩家、副本、APP、游戏等无限流表达",
    "不得出现守夜人、裴正阳、陈守正、周德昌、钱桂芬等旧设定",
    "不得把林远山写成林渊父亲；林远山只能是曾祖父/封镜祖先",
    "不得让已死亡或已获救人物回到旧状态",
]

SPECS = {
    1: ("青囊不语", "建立林渊、青囊秘卷和十七栋委托入口。", "镜局启动，七人入局。"),
    2: ("镜中局", "确认七名入局者和否认者先死规则。", "老张死亡留下第一条外扩线索。"),
    3: ("血字规则", "林渊用风水方法推断镜债规则。", "青囊显字，陈默与小雨线加压。"),
    4: ("困魂镜", "完成小雨认账获救，并让陈默入303镜眼。", "林渊首次证明规则可破。"),
    5: ("第二个死者", "周雪死亡并入账，手机反光物开始外扩。", "钱婆婆揭露三族债。"),
    6: ("镜中月", "三姓钱暂闭镜门，王建业离局后死亡。", "苏婉宁从现实坠楼案接入。"),
    7: ("执念深处", "确认王建业死亡是回执，镜影林渊开始生成。", "林渊决定先取回回执。"),
    8: ("回执镜片", "从王建业尸体手中识别回执镜片，阻止它长成现实镜眼。", "监控里出现长着林渊脸的人。"),
    9: ("屏幕外的脸", "追查周雪手机记录与屏幕外扩，确认镜债能借反光物蔓延。", "陈默隐瞒的事不是小雨。"),
    10: ("陈默的否认", "围绕陈默当年的一次替人否认揭开救援条件。", "303镜眼要求林渊替陈默承认那笔账。"),
    11: ("第三零三室", "林渊进入303边界救人，付出代价取得陈默真实供词。", "张家开门人的线索第一次成形。"),
    12: ("镜影回执", "陈默完成认账，镜影林渊利用回执制造栽赃。", "林渊发现陈默真正隐瞒的不是小雨。"),
    13: ("老张旧案", "追查张建军临死话和旧案，厘清他不是张家开门人。", "旧案卷宗里出现林正淳笔迹。"),
    14: ("开门痕迹", "识别张家开门术留下的门槛、铜灰和倒贴符痕。", "十七栋出现第二道不该存在的门。"),
    15: ("林正淳旧照", "释放林正淳曾替林渊还过第一笔债的局部信息。", "照片背面写着一笔未清。"),
    16: ("钱家账页", "钱婆婆交出残账页，但隐瞒钱家当年真正代价。", "账页显示王建业不是最后一个回执。"),
    17: ("否认账", "林渊建立否认者入账模型，发现镜债会选择最会逃避真相的人。", "小雨证词指向另一名幸存者。"),
    18: ("十七栋旧门", "现实侧和镜局侧同时追查旧门，证明有人主动开门。", "旧门背后传来林正淳的敲击声。"),
    19: ("门后回声", "林渊判断门后声音真假，并阻止镜影借父亲声音诱门。", "苏婉宁开始相信案件不是普通坠楼。"),
    20: ("张家后人", "回收老张旧案，锁定张家后人出现在十七栋外。", "张家后人说：门不是我开的。"),
    21: ("第三姓钱", "钱家旁支线浮出水面，三姓钱真正用途被修正。", "一枚铜钱自行翻面。"),
    22: ("镜债过户", "林渊发现镜债可以通过承认与替认转移代价。", "有人试图把陈默的债过到林渊名下。"),
    23: ("旧楼封条", "警方封楼反而触发镜局边界变化。", "封条上出现青囊字迹。"),
    24: ("午夜照名", "午夜照名规则启动，幸存者必须说出一件真事。", "第七个名字被镜面擦掉。"),
    25: ("纸钱路", "林渊沿纸钱路寻找王建业背后委托人。", "纸钱尽头是林家旧印。"),
    26: ("钱婆婆的价", "钱婆婆承认守镜人每帮一次都要折一笔阳寿。", "她要求林渊别救所有人。"),
    27: ("镜影借脸", "镜影林渊开始接触现实人际关系，制造身份压力。", "苏婉宁拿到一段不利监控。"),
    28: ("王建业遗物", "王建业遗物指向真正付钱的人。", "遗物里夹着张家门契残页。"),
    29: ("第七名真名", "林渊发现第七名不是编号，而是镜债为林家预留的位置。", "青囊第一次拒绝显字。"),
    30: ("三族缺口", "三族契约缺口显现，钱家守镜、张家开门、林家记账关系成形。", "张家后人约林渊单独见面。"),
    31: ("青囊账页", "林渊在青囊残页中看到三百年前第一笔镜债。", "账页上出现林远山的曾祖印。"),
    32: ("老宅来信", "林家老宅寄来迟到三年的信，林正淳线推进。", "信中写着不要相信我的脸。"),
    33: ("旧门再开", "张家后人证明有人借张家门术再次开门。", "旧门通向十七栋不存在的楼层。"),
    34: ("回执失控", "回执镜片失控，受困者的否认开始在现实投影。", "小雨看见另一个自己。"),
    35: ("受困者名单", "林渊核对名单，发现有人被镜局从名单上抹掉。", "被抹掉的人留下求救声。"),
    36: ("三百年第一账", "三百年前林远山封镜的第一账露出轮廓。", "第一账和林正淳三年前选择相连。"),
    37: ("井口铜钱", "孙九斤在井口铜钱中找到钱家旧誓。", "铜钱指向主镜门。"),
    38: ("门契残页", "门契残页补全张家开门条件。", "开门必须有人主动否认真相。"),
    39: ("林正淳的笔迹", "林渊确认父亲笔迹，并看见父亲为他抵债的证据。", "镜影用同样笔迹写下一句话。"),
    40: ("镜中债主", "林渊误以为找到债主，却发现只是镜债规则的一层假面。", "真正债主仍未现身。"),
    41: ("十七栋封局", "林渊准备封住十七栋主镜门，救出一半受困者。", "封局要求他交出一个名字。"),
    42: ("陈默回声", "陈默在镜中提供关键回声，帮助定位主镜门。", "回声里混入林正淳声音。"),
    43: ("小雨证词", "小雨作为幸存者作证，现实线和镜债线合拢。", "她说镜影已经来过。"),
    44: ("钱家守镜", "钱婆婆承认钱家守镜不是守住镜，而是守住欠账人。", "她把最后一枚三姓钱给林渊。"),
    45: ("张家来人", "张家后人正式入场，带来开门人的反证。", "真正开门的人可能在林家。"),
    46: ("七人清账", "第一批七人因果逐一清点，能救与不能救的边界明确。", "镜局要求林渊承认自己的债。"),
    47: ("主镜门", "林渊抵达主镜门，镜影试图替他做选择。", "主镜门后出现林正淳旧物。"),
    48: ("三族旧契", "三族契约的第一层规则被完整拼出。", "契约缺了一枚林家印。"),
    49: ("半数归人", "林渊付出代价暂闭主镜门，救出一半受困者。", "没出来的人名字进入青囊账本。"),
    50: ("第一卷余账", "第一卷局部收束：困魂镜未灭，只是第一笔旧账暂缓。", "青囊显出下一笔债的位置。"),
}

VOLUMES = [
    ("十七栋困魂镜局", "完成十七栋镜债入局、七人因果清点和主镜门暂封。"),
    ("旧城阴宅门", "现实旧城改造案把镜债扩到城市民俗圈。"),
    ("水库借命案", "借命案揭露镜债可转移寿数。"),
    ("纸人婚与活人替身", "替身案推动林渊面对父亲抵债真相。"),
    ("戏台回魂夜", "林渊公开破局，民俗圈开始注意林家。"),
    ("医院七日灯", "七日灯案让现实生命倒计时进入主线。"),
    ("古镇三族账", "三族契约完整版第一次接近真相。"),
    ("地铁阴路", "城市级规则怪谈爆发，林渊从救单人变成救一城。"),
    ("青囊反噬", "青囊开始反噬执卷人，林渊必须重定救人边界。"),
    ("镜中旧世", "林渊入镜寻找父亲，确认真正债主不是镜本身。"),
]


def character(
    name: str,
    role: str,
    gender: str,
    background: str,
    goal: str,
    *,
    aliases: list[str] | None = None,
    arc_state: str = "第一卷",
    alive_status: str = "alive",
    death_chapter_number: int | None = None,
) -> dict[str, Any]:
    pronouns = {
        "male": ("他", "he/him"),
        "female": ("她", "she/her"),
        "nonbinary": ("ta", "they/them"),
    }
    zh, en = pronouns[gender]
    return {
        "name": name,
        "role": role,
        "gender": gender,
        "pronoun_set_zh": zh,
        "pronoun_set_en": en,
        "aliases": aliases or [],
        "background": background,
        "goal": goal,
        "arc_state": arc_state,
        "metadata": {
            "commercial_repair": True,
            "alive_status": alive_status,
            "death_chapter_number": death_chapter_number,
        },
    }


def clean_cast_spec() -> dict[str, Any]:
    protagonist = character(
        "林渊",
        "protagonist",
        "male",
        "背负林家旧债的青囊执卷人，能以风水、符箓和青囊残页追认镜债因果。",
        "查清十七栋困魂镜局、父亲林正淳失踪与三族旧契之间的关系，同时尽量救下仍可救的人。",
    )
    antagonist = character(
        "镜影林渊",
        "antagonist",
        "male",
        "从回执与镜面反射中生成的假身，拥有林渊的脸，却服务于镜债规则。",
        "借林渊身份制造现实污点，逼他承认不属于他的债。",
        aliases=["镜影"],
    )
    supporting_cast = [
        character(
            "孙九斤",
            "ally",
            "male",
            "懂旧城民俗和偏门江湖规矩的跑腿搭档，嘴碎但讲义气。",
            "帮林渊查证现实线索，并在危险边缘替他联系灰色人脉。",
        ),
        character(
            "钱婆婆",
            "mentor",
            "female",
            "十七栋附近的守镜老人，知道三姓钱和困魂镜局的旧规矩。",
            "在保命、守契与救人之间摇摆，逐步交出钱家旧账。",
        ),
        character(
            "小雨",
            "survivor",
            "female",
            "十七栋镜局第一批被困者之一，已通过认账被救回现实。",
            "作为幸存证人帮助林渊确认否认者先死规则。",
            alive_status="alive",
        ),
        character(
            "陈默",
            "survivor",
            "male",
            "小雨的父亲，曾因逃避真相而被镜局抓住弱点。",
            "完成认账，交代当年隐瞒，为后续救援提供真实供词。",
        ),
        character(
            "苏婉宁",
            "investigator",
            "female",
            "追查现实坠楼案的法医/调查者，重证据，不轻信玄学。",
            "从现实证据侧切入，逐步确认镜债并非普通刑案。",
        ),
        character(
            "林正淳",
            "missing_family",
            "male",
            "林渊父亲，三年前失踪，曾替林渊抵过第一笔债。",
            "以遗留笔迹、旧物和封存线索推动父子主线。",
            alive_status="missing",
        ),
        character(
            "林远山",
            "ancestor",
            "male",
            "林家曾祖与封镜祖先，和三百年前第一笔镜债有关。",
            "作为旧契源头被追查，只能以旧账、印记和传说出现。",
            alive_status="deceased",
        ),
        character(
            "周雪",
            "victim",
            "female",
            "十七栋镜局受害者，死亡后入账，推动手机反光物外扩线。",
            "作为已死受害者留下证据，不再进行现实当前行动。",
            alive_status="deceased",
            death_chapter_number=5,
        ),
        character(
            "王建业",
            "victim",
            "male",
            "十七栋镜局离局后死亡者，尸体与遗物留下回执镜片。",
            "用死亡回执证明镜债并不会因离开镜局自动结束。",
            aliases=["王老板"],
            alive_status="deceased",
            death_chapter_number=6,
        ),
        character(
            "张建军",
            "victim",
            "male",
            "第一批死者，临死前留下张家开门人相关旧案入口。",
            "以旧案和遗言引出张家门术，而不是在当前线复活行动。",
            aliases=["老张"],
            alive_status="deceased",
            death_chapter_number=2,
        ),
        character(
            "张家后人",
            "rival_ally",
            "male",
            "掌握张家开门术残脉的人，既能提供反证，也可能隐瞒旧债。",
            "证明门不是自己开的，并把林渊引向真正开门者。",
        ),
        character(
            "旧案证人",
            "witness",
            "male",
            "旧案中被替换姓名的证人，用于承载周德昌旧线被清洗后的合法证词位置。",
            "只提供证词和账页关联，不带回旧设定身份。",
        ),
    ]
    core_characters = [
        protagonist,
        supporting_cast[0],
        supporting_cast[1],
        supporting_cast[2],
        supporting_cast[3],
        supporting_cast[4],
    ]
    return {
        "protagonist": protagonist,
        "antagonist": antagonist,
        "supporting_cast": supporting_cast,
        "characters": core_characters,
        "allies": [supporting_cast[0], supporting_cast[1], supporting_cast[4]],
        "antagonists": [antagonist],
        "antagonist_forces": [
            {
                "name": "镜债规则",
                "force_type": "systemic",
                "active_volumes": [1, 2, 3, 4, 5],
                "core_mechanism": "否认真相者先被入账，回执和反光物会把镜局代价外扩到现实。",
                "escalation_path": "十七栋困魂镜局外溢到旧城、医院、地铁和三族旧契。",
                "threat_description": "不是游戏系统，也不是APP，而是依附镜、账、门三类民俗物的旧债规则。",
            },
            {
                "name": "张家开门人旧线",
                "force_type": "faction",
                "active_volumes": [1, 2, 3],
                "core_mechanism": "门槛、铜灰、倒贴符和门契残页共同构成开门术证据链。",
                "escalation_path": "从老张旧案露头，到张家后人提供反证，再指向真正开门者。",
                "threat_description": "三族旧盟中的开门残脉，和十七栋第一道镜门有关。",
            },
        ],
        "conflict_map": [
            {
                "character_a": "林渊",
                "character_b": "镜影林渊",
                "conflict_type": "身份夺取",
                "trigger_condition": "回执镜片长成现实镜眼后，镜影借脸制造栽赃。",
            },
            {
                "character_a": "林渊",
                "character_b": "钱婆婆",
                "conflict_type": "救人与守契",
                "trigger_condition": "每救一人都会增加守镜代价。",
            },
            {
                "character_a": "林渊",
                "character_b": "张家后人",
                "conflict_type": "互相怀疑",
                "trigger_condition": "开门痕迹指向张家，但张家后人否认开门。",
            },
        ],
        "book_structure_overview": {
            "total_volumes": 10,
            "chapters_per_volume": 50,
            "total_chapters": 500,
            "main_conflict_arc": "林渊从十七栋困魂镜局入手，沿青囊账页、三族旧契和父亲失踪线，查清镜债真正来源。",
            "volume_themes": [item[0] for item in VOLUMES],
        },
    }


def normalize_text(value: str) -> str:
    out = value
    for old, new in REPLACEMENTS:
        out = out.replace(old, new)
    return out


def normalize_obj(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, list):
        return [normalize_obj(item) for item in value]
    if isinstance(value, dict):
        return {normalize_text(str(k)): normalize_obj(v) for k, v in value.items()}
    return value


def identity_key(value: str) -> str:
    return "".join(str(value or "").strip().lower().split())


def wordish_count(value: str) -> int:
    return max(1, len("".join(value.split())))


def scene_state_payload(
    chapter_no: int,
    title: str,
    goal: str,
    hook: str,
    scene_number: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    previous_hook = SPECS.get(chapter_no - 1, ("", "", "第7章之后的镜影与回执危机"))[2]
    if scene_number == 1:
        entry = {
            "story_position": f"第{chapter_no}章开场，林渊带着上一章钩子“{previous_hook}”进入“{title}”线索。",
            "known_clue": "王建业死亡、镜影林渊生成、十七栋主镜门未封，三件事仍处于同一条镜债链上。",
            "immediate_pressure": f"林渊必须先确认“{goal}”中最能落地的一处证据，不能让现实线先被镜影抢走。",
        }
        exit_state = {
            "new_evidence": f"本场结束时，林渊拿到与“{title}”直接相关的第一枚实物或证词锚点。",
            "changed_pressure": "镜债从镜局内部威胁转为现实证据威胁，林渊必须立刻追第二处验证点。",
            "handoff": "下一场转入查证线索，重点不是解释真相，而是扩大可验证证据链。",
        }
    elif scene_number == 2:
        entry = {
            "story_position": f"第{chapter_no}章第二场，第一处锚点已出现，但仍无法单独证明“{title}”的真相。",
            "known_clue": f"林渊手里已有一条能指向“{goal}”的线索，却缺少现实侧交叉验证。",
            "immediate_pressure": "苏婉宁或孙九斤必须把线索带到现实物证、监控、账页或证词上，否则林渊会被镜影反咬。",
        }
        exit_state = {
            "new_evidence": f"查证后，线索确认不是偶然异常，而是“{title}”正在外溢的规则痕迹。",
            "changed_pressure": "现实证据开始反向压迫林渊，警方/旁观者/受困者至少有一方产生新的怀疑。",
            "handoff": "下一场必须让林渊面对选择或阻力，不能继续停留在信息搜集。",
        }
    elif scene_number == 3:
        entry = {
            "story_position": f"第{chapter_no}章第三场，林渊已经掌握两处证据，但镜债或镜影开始反制。",
            "known_clue": f"“{goal}”的关键条件浮出水面，却需要林渊付出代价才能继续验证。",
            "immediate_pressure": "林渊必须在救人、保全自己身份、保护证据三者之间做出短期取舍。",
        }
        exit_state = {
            "new_cost": f"林渊破解“{title}”的一层阻碍，但因此暴露出新的身份、寿数或信任代价。",
            "changed_pressure": "镜影林渊或张家开门人旧线获得一次反击机会，局势不再只是林渊单向追查。",
            "handoff": "下一场用章末钩子把本章代价转成下一章必须处理的悬念。",
        }
    else:
        entry = {
            "story_position": f"第{chapter_no}章末场，林渊刚付出代价，手中证据足以触碰“{title}”的章末核心。",
            "known_clue": f"本章目标“{goal}”已经完成大半，只剩一个会改变下一章方向的缺口。",
            "immediate_pressure": "所有人物必须围绕最后一处证据行动，不能另开无关支线。",
        }
        exit_state = {
            "chapter_result": f"第{chapter_no}章以“{hook}”收束，读者得到明确新问题。",
            "changed_pressure": "林渊暂时解决本章表层危机，但下一章的风险被具体化到人、物或地点。",
            "next_chapter_pull": hook,
        }
    return entry, exit_state


def chapter_payload(chapter_no: int) -> dict[str, Any]:
    title, goal, hook = SPECS[chapter_no]
    if chapter_no <= 7:
        participants = ["林渊", "孙九斤", "小雨", "陈默", "钱婆婆"]
    elif chapter_no <= 12:
        participants = ["林渊", "苏婉宁", "孙九斤", "小雨", "陈默"]
    elif chapter_no <= 20:
        participants = ["林渊", "苏婉宁", "孙九斤", "钱婆婆", "张家后人"]
    else:
        participants = ["林渊", "苏婉宁", "孙九斤", "钱婆婆"]
    scenes = []
    for scene_number, scene_type, scene_title in (
        (1, "setup", "承接危机"),
        (2, "investigation", "查证线索"),
        (3, "confrontation", "破局选择"),
        (4, "hook", "章末钩子"),
    ):
        entry_state, exit_state = scene_state_payload(chapter_no, title, goal, hook, scene_number)
        scenes.append(
            {
                "scene_number": scene_number,
                "scene_type": scene_type,
                "title": f"{title}·{scene_title}",
                "time_label": "承接上一章后续" if scene_number == 1 else "同日推进",
                "participants": participants,
                "purpose": {
                    "story": f"围绕“{title}”推进：{goal}",
                    "emotion": "保持悬疑压力，推动林渊用风水方法验证线索，而不是直接给出终局真相。",
                    "commercial_hook": hook if scene_number == 4 else "本场必须产生新线索、新代价或新选择。",
                },
                "entry_state": entry_state,
                "exit_state": exit_state,
                "target_word_count": 550,
                "forbidden_actions": FORBIDDEN_ACTIONS,
                "hook_requirement": hook if scene_number == 4 else "本场结尾保留可追踪问题。",
                "key_dialogue_beats": [],
                "sensory_anchors": {"image": "铜钱、镜光、青囊字迹、旧楼潮气"},
            }
        )
    return {
        "chapter_number": chapter_no,
        "title": title,
        "chapter_goal": goal,
        "goal": goal,
        "opening_situation": "严格承接第7章之后的正典状态，不重演已完成的死亡、救援或揭示。" if chapter_no >= 8 else goal,
        "main_conflict": "林渊必须在镜债外溢和现实追查夹击下，用青囊和风水方法取得可验证线索。" if chapter_no >= 8 else goal,
        "hook_type": "mystery_cliff",
        "hook_description": hook,
        "volume_number": 1,
        "target_word_count": 2200,
        "scenes": scenes,
    }


def clean_volume_plan() -> list[dict[str, Any]]:
    plans = []
    for idx, (title, goal) in enumerate(VOLUMES, start=1):
        start = 1 + (idx - 1) * 50
        end = idx * 50
        plans.append(
            {
                "volume_number": idx,
                "title": title,
                "chapter_range": [start, end],
                "volume_goal": goal,
                "arc_ranges": [[start, start + 11], [start + 12, start + 24], [start + 25, start + 37], [start + 38, end]],
                "key_reveals": ["三族契约", "青囊账本", "镜债代价"],
            }
        )
    return plans


async def next_artifact_version(session, project_id, artifact_type: str) -> int:
    current = await session.scalar(
        select(func.coalesce(func.max(PlanningArtifactVersionModel.version_no), 0)).where(
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.artifact_type == artifact_type,
        )
    )
    return int(current or 0) + 1


async def main() -> None:
    settings = load_settings()
    engine = create_engine(settings)
    sm = create_session_factory(engine=engine)
    summary: dict[str, int] = {}
    try:
        async with sm() as session:
            project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))
            if project is None:
                raise RuntimeError(f"project not found: {PROJECT_SLUG}")
            pid = project.id
            params = {"pid": str(pid), "from_ch": REWRITE_FROM}

            statements = [
                "delete from chapter_audit_findings where project_id=:pid and chapter_no >= :from_ch",
                "delete from chapter_quality_reports where chapter_id in (select id from chapters where project_id=:pid and chapter_number >= :from_ch)",
                "delete from chapter_state_snapshots where project_id=:pid and chapter_number >= :from_ch",
                "delete from character_state_snapshots where project_id=:pid and chapter_number >= :from_ch",
                "delete from relationship_events where project_id=:pid and chapter_number >= :from_ch",
                "delete from reader_knowledge_entries where project_id=:pid and chapter_number >= :from_ch",
                "delete from timeline_events where project_id=:pid and chapter_id in (select id from chapters where project_id=:pid and chapter_number >= :from_ch)",
                "delete from chapter_contracts where project_id=:pid and chapter_number >= :from_ch",
                "delete from scene_contracts where project_id=:pid and chapter_number >= :from_ch",
                "delete from arc_beats where project_id=:pid and scope_chapter_number >= :from_ch",
                "delete from antagonist_plans where project_id=:pid and target_chapter_number >= :from_ch",
                "delete from motif_placements where project_id=:pid and chapter_number >= :from_ch",
                "delete from narrative_tree_nodes where project_id=:pid and scope_chapter_number >= :from_ch",
                "delete from pacing_curve_points where project_id=:pid and chapter_number >= :from_ch",
                "delete from subplot_schedule where project_id=:pid and chapter_number >= :from_ch",
                "delete from deferred_reveals where project_id=:pid and reveal_chapter_number >= :from_ch",
                "delete from expansion_gates where project_id=:pid and unlock_chapter_number >= :from_ch",
                "delete from payoffs where project_id=:pid and (target_chapter_number >= :from_ch or actual_chapter_number >= :from_ch)",
                "update clues set actual_paid_off_chapter_number=null where project_id=:pid and actual_paid_off_chapter_number >= :from_ch",
                "delete from clues where project_id=:pid and planted_in_chapter_number >= :from_ch",
                "delete from foreshadowing_ledger where project_id=:pid and (setup_chapter_no >= :from_ch or planned_payoff_chapter_no >= :from_ch or actual_payoff_chapter_no >= :from_ch)",
                "update interpersonal_promises set resolved_chapter_number=null where project_id=:pid and resolved_chapter_number >= :from_ch",
                "delete from interpersonal_promises where project_id=:pid and made_chapter_number >= :from_ch",
                "delete from chase_debts where project_id=:pid and (chapter_no >= :from_ch or accrued_through_chapter >= :from_ch)",
                "delete from scene_draft_versions where scene_card_id in (select sc.id from scene_cards sc join chapters c on c.id=sc.chapter_id where c.project_id=:pid and c.chapter_number >= :from_ch)",
                "delete from chapter_draft_versions where chapter_id in (select id from chapters where project_id=:pid and chapter_number >= :from_ch)",
                "update canon_facts set source_scene_id=null where project_id=:pid and source_scene_id in (select sc.id from scene_cards sc join chapters c on c.id=sc.chapter_id where c.project_id=:pid and c.chapter_number >= :from_ch)",
                "delete from scene_cards where chapter_id in (select id from chapters where project_id=:pid and chapter_number >= :from_ch)",
            ]
            for stmt in statements:
                result = await session.execute(text(stmt), params)
                key = stmt.split()[2] if stmt.startswith("delete") else stmt.split()[1]
                summary[key] = summary.get(key, 0) + (result.rowcount or 0)

            await session.execute(
                text(
                    "update canon_facts set supersedes_fact_id=null "
                    "where project_id=:pid and supersedes_fact_id in "
                    "(select id from canon_facts where project_id=:pid and valid_from_chapter_no >= :from_ch)"
                ),
                params,
            )
            result = await session.execute(
                text("delete from canon_facts where project_id=:pid and valid_from_chapter_no >= :from_ch"),
                params,
            )
            summary["canon_facts_deleted"] = result.rowcount or 0
            await session.execute(
                text(
                    "update canon_facts set valid_to_chapter_no=null "
                    "where project_id=:pid and valid_to_chapter_no is not null and valid_to_chapter_no >= :from_ch"
                ),
                params,
            )
            await session.execute(text("update canon_facts set is_current=false where project_id=:pid"), {"pid": str(pid)})
            await session.execute(
                text(
                    "with ranked as (select id, row_number() over (partition by subject_type, subject_id, predicate "
                    "order by valid_from_chapter_no desc, created_at desc) as rn "
                    "from canon_facts where project_id=:pid and valid_from_chapter_no <= :frontier) "
                    "update canon_facts f set is_current=true from ranked r where f.id=r.id and r.rn=1"
                ),
                {"pid": str(pid), "frontier": FRONTIER},
            )

            project.metadata_json = normalize_obj(dict(project.metadata_json or {}))
            project.metadata_json.update(
                {
                    "canonical_frontier_chapter": FRONTIER,
                    "rewrite_from_chapter": REWRITE_FROM,
                    "commercial_gate_required": True,
                    "commercial_gate_package": str(ROOT),
                    "stuck_at_chapter": REWRITE_FROM,
                    "last_error": None,
                }
            )
            project.current_chapter_number = FRONTIER
            project.status = ProjectStatus.WRITING.value

            locked_cast_spec = normalize_obj(clean_cast_spec())
            identity_report = validate_foundation_identity_contract(locked_cast_spec)
            if not identity_report.passed:
                raise RuntimeError(identity_report.error_message(project_slug=PROJECT_SLUG, artifact="cast_spec"))
            identity_manifest = build_identity_manifest(locked_cast_spec)
            project.metadata_json.update(
                {
                    "cast_spec": locked_cast_spec,
                    "identity_manifest": identity_manifest,
                    "identity_manifest_status": "locked",
                }
            )

            volume_plan = clean_volume_plan()
            volume_by_no = {item["volume_number"]: item for item in volume_plan}
            for volume in await session.scalars(select(VolumeModel).where(VolumeModel.project_id == pid)):
                volume.metadata_json = normalize_obj(dict(volume.metadata_json or {}))
                if volume.volume_number in volume_by_no:
                    plan = volume_by_no[volume.volume_number]
                    volume.title = plan["title"]
                    volume.target_chapter_count = 50
                    volume.metadata_json.update(plan)
                volume.status = "planned"

            for artifact in await session.scalars(select(PlanningArtifactVersionModel).where(PlanningArtifactVersionModel.project_id == pid)):
                artifact.content = normalize_obj(artifact.content)
                artifact.notes = normalize_text(artifact.notes) if artifact.notes else artifact.notes

            clean_chapters = [chapter_payload(i) for i in range(1, FIRST_VOLUME_END + 1)]
            clean_outline = {
                "batch_name": "commercial-repair-outline-v2",
                "canon_frontier_chapter": FRONTIER,
                "rewrite_from_chapter": REWRITE_FROM,
                "chapters": clean_chapters,
            }
            for artifact_type, content in [
                ("cast_spec", locked_cast_spec),
                ("chapter_outline_batch", clean_outline),
                ("volume_chapter_outline", clean_outline),
                ("volume_plan", volume_plan),
            ]:
                session.add(
                    PlanningArtifactVersionModel(
                        project_id=pid,
                        artifact_type=artifact_type,
                        scope_ref_id=None,
                        version_no=await next_artifact_version(session, pid, artifact_type),
                        status="approved",
                        schema_version="commercial-repair-v1",
                        content=normalize_obj(content),
                        notes="Rebuilt after commercial gate rewind to chapter 7.",
                        created_by="codex-repair",
                    )
                )
                summary[f"artifact_{artifact_type}_created"] = 1

            for chapter_no in range(1, FRONTIER + 1):
                chapter = await session.scalar(
                    select(ChapterModel).where(ChapterModel.project_id == pid, ChapterModel.chapter_number == chapter_no)
                )
                if chapter is None:
                    continue
                path = ROOT / f"chapter-{chapter_no:03d}.md"
                if path.exists():
                    content = normalize_text(path.read_text(encoding="utf-8"))
                    draft = await session.scalar(
                        select(ChapterDraftVersionModel).where(
                            ChapterDraftVersionModel.chapter_id == chapter.id,
                            ChapterDraftVersionModel.is_current.is_(True),
                        )
                    )
                    if draft is not None:
                        draft.content_md = content
                        draft.word_count = wordish_count(content)
                    chapter.current_word_count = wordish_count(content)
                chapter.status = ChapterStatus.COMPLETE.value
                chapter.production_state = "ok"

            scene_drafts = await session.scalars(
                select(SceneDraftVersionModel)
                .join(SceneCardModel, SceneDraftVersionModel.scene_card_id == SceneCardModel.id)
                .join(ChapterModel, SceneCardModel.chapter_id == ChapterModel.id)
                .where(
                    SceneDraftVersionModel.project_id == pid,
                    SceneDraftVersionModel.is_current.is_(True),
                    ChapterModel.chapter_number <= FRONTIER,
                )
            )
            for draft in scene_drafts:
                draft.content_md = normalize_text(draft.content_md or "")

            for chapter_no in range(REWRITE_FROM, FIRST_VOLUME_END + 1):
                payload = chapter_payload(chapter_no)
                chapter = await session.scalar(
                    select(ChapterModel).where(ChapterModel.project_id == pid, ChapterModel.chapter_number == chapter_no)
                )
                if chapter is None:
                    chapter = ChapterModel(
                        project_id=pid,
                        chapter_number=chapter_no,
                        title=payload["title"],
                        chapter_goal=payload["chapter_goal"],
                        information_revealed=[],
                        information_withheld=[],
                        foreshadowing_actions={},
                    )
                    session.add(chapter)
                    await session.flush()
                chapter.title = payload["title"]
                chapter.chapter_goal = payload["chapter_goal"]
                chapter.opening_situation = payload["opening_situation"]
                chapter.main_conflict = payload["main_conflict"]
                chapter.hook_type = payload["hook_type"]
                chapter.hook_description = payload["hook_description"]
                chapter.target_word_count = payload["target_word_count"]
                chapter.current_word_count = 0
                chapter.revision_count = 0
                chapter.status = ChapterStatus.PLANNED.value
                chapter.production_state = "pending"
                chapter.hype_type = None
                chapter.hype_intensity = None
                chapter.hype_recipe_key = None
                chapter.dominant_line = None
                chapter.support_lines = None
                chapter.line_intensity = None
                chapter.metadata_json = {
                    **normalize_obj(dict(chapter.metadata_json or {})),
                    "rewritten_after_frontier": FRONTIER,
                    "commercial_repair": True,
                    "forbidden_actions": FORBIDDEN_ACTIONS,
                }
                for scene in payload["scenes"]:
                    session.add(
                        SceneCardModel(
                            project_id=pid,
                            chapter_id=chapter.id,
                            scene_number=scene["scene_number"],
                            scene_type=scene["scene_type"],
                            title=scene["title"],
                            time_label=scene["time_label"],
                            participants=scene["participants"],
                            purpose=scene["purpose"],
                            entry_state=scene["entry_state"],
                            exit_state=scene["exit_state"],
                            key_dialogue_beats=scene["key_dialogue_beats"],
                            sensory_anchors=scene["sensory_anchors"],
                            forbidden_actions=scene["forbidden_actions"],
                            hook_requirement=scene["hook_requirement"],
                            target_word_count=scene["target_word_count"],
                            status=SceneStatus.PLANNED.value,
                            metadata_json={"commercial_repair": True, "canon_frontier": FRONTIER},
                        )
                    )
                    summary["scene_cards_created"] = summary.get("scene_cards_created", 0) + 1
                summary["future_chapters_reset"] = summary.get("future_chapters_reset", 0) + 1

            core_states = {
                "林渊": ("alive", None, "protagonist"),
                "小雨": ("alive", None, "ally"),
                "陈默": ("alive", None, "ally"),
                "周雪": ("deceased", 5, "neutral"),
                "王老板": ("deceased", 6, "neutral"),
                "王建业": ("deceased", 6, "neutral"),
                "老张": ("deceased", 2, "neutral"),
                "张建军": ("deceased", 2, "neutral"),
                "林正淳": ("missing", None, "ally"),
                "林远山": ("deceased", None, "ally"),
                "苏婉宁": ("alive", None, "neutral"),
                "钱婆婆": ("alive", None, "ally"),
                "孙九斤": ("alive", None, "ally"),
            }
            characters = list(
                await session.scalars(select(CharacterModel).where(CharacterModel.project_id == pid))
            )
            characters.sort(key=lambda item: (item.name not in core_states, item.name))
            used_names: set[str] = set()
            for character in characters:
                candidate_name = normalize_text(character.name)
                if candidate_name in used_names:
                    base_name = f"{candidate_name}旧线"
                    candidate_name = base_name
                    suffix = 2
                    while candidate_name in used_names:
                        candidate_name = f"{base_name}{suffix}"
                        suffix += 1
                character.name = candidate_name
                used_names.add(candidate_name)
                for attr in [
                    "background",
                    "goal",
                    "fear",
                    "flaw",
                    "strength",
                    "secret",
                    "arc_trajectory",
                    "arc_state",
                    "power_tier",
                    "physical_description",
                ]:
                    value = getattr(character, attr, None)
                    if isinstance(value, str):
                        setattr(character, attr, normalize_text(value))
                character.metadata_json = normalize_obj(dict(character.metadata_json or {}))
                character.knowledge_state_json = normalize_obj(dict(character.knowledge_state_json or {}))
                if character.death_chapter_number is not None and character.death_chapter_number >= REWRITE_FROM:
                    character.death_chapter_number = None
                    character.alive_status = "alive"
            for character in characters:
                if character.name in core_states:
                    alive_status, death_chapter, stance = core_states[character.name]
                    character.alive_status = alive_status
                    character.death_chapter_number = death_chapter
                    character.stance = stance
            manifest_by_token: dict[str, dict[str, Any]] = {}
            for entry in identity_manifest:
                for token in [entry.get("name"), *(entry.get("aliases") or [])]:
                    key = identity_key(str(token or ""))
                    if key:
                        manifest_by_token[key] = entry
            for character in characters:
                entry = manifest_by_token.get(identity_key(character.name))
                if entry is None:
                    continue
                meta = dict(character.metadata_json or {})
                cast_entry = dict(meta.get("cast_entry") or {})
                cast_entry.update(
                    {
                        "gender": entry.get("gender") or "unknown",
                        "pronoun_set_zh": entry.get("pronoun_set_zh") or "",
                        "pronoun_set_en": entry.get("pronoun_set_en") or "",
                        "aliases": entry.get("aliases") or [],
                    }
                )
                meta.update(
                    {
                        "gender": cast_entry["gender"],
                        "pronoun_set_zh": cast_entry["pronoun_set_zh"],
                        "pronoun_set_en": cast_entry["pronoun_set_en"],
                        "aliases": cast_entry["aliases"],
                        "cast_entry": cast_entry,
                    }
                )
                character.metadata_json = meta

            for chunk in await session.scalars(select(RetrievalChunkModel).where(RetrievalChunkModel.project_id == pid)):
                chunk.chunk_text = normalize_text(chunk.chunk_text)
                if chunk.lexical_document:
                    chunk.lexical_document = normalize_text(chunk.lexical_document)
                chunk.metadata_json = normalize_obj(dict(chunk.metadata_json or {}))

            await session.commit()
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
