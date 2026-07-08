"""Repair playbooks for commercial quality findings."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from bestseller.services.methodology_book_selector import render_book_methodology_block


@dataclass(frozen=True)
class QualityRepairPlaybook:
    code: str
    scope: str
    instruction: str
    acceptance: str

    def render(self) -> str:
        return f"[{self.code}] {self.instruction}\n验收：{self.acceptance}"


_PLAYBOOKS: dict[str, QualityRepairPlaybook] = {
    "CHAPTER_TOO_SHORT": QualityRepairPlaybook(
        code="CHAPTER_TOO_SHORT",
        scope="chapter",
        instruction="本章篇幅低于商业连载硬下限。重写时必须保留现有因果节拍，扩写当下行动、阻力、证据细节、对话交锋和感官画面，不得用解释性总结凑字数。",
        acceptance="正文达到章节目标硬下限，且新增内容全部推进当前冲突或揭示新事实。",
    ),
    "CHAPTER_BELOW_TARGET": QualityRepairPlaybook(
        code="CHAPTER_BELOW_TARGET",
        scope="chapter",
        instruction="本章接近但未达到目标篇幅。补强最薄的场景，使关键选择、代价、反应和章末悬念都落在可见动作里。",
        acceptance="正文达到目标区间下沿，章末仍保留明确下一章阅读动力。",
    ),
    "CHAPTER_LENGTH_BLOCK_HIGH": QualityRepairPlaybook(
        code="CHAPTER_LENGTH_BLOCK_HIGH",
        scope="chapter",
        instruction="本章超过发布硬上限。删减重复心理解释、同义对白、过场铺陈和不改变局面的说明，保留因果节点、异常物、人物选择和章末钩子。",
        acceptance="正文 CJK 字数不超过硬上限，删减后主冲突、关键证据和章末牵引仍完整。",
    ),
    "INTRA_CHAPTER_REPETITION": QualityRepairPlaybook(
        code="INTRA_CHAPTER_REPETITION",
        scope="paragraph",
        instruction="删除或重写章内重复段落。相同信息只能出现一次，后续必须升级为新动作、新证据、新情绪变化或新阻力。",
        acceptance="没有连续近似段、循环句式或同一动作/结论反复出现。",
    ),
    "REPEATED_EVENT_BEAT": QualityRepairPlaybook(
        code="REPEATED_EVENT_BEAT",
        scope="paragraph",
        instruction="本章重复了同一事件节拍。重写时必须合并重复桥段，保留第一次作为规则展示，第二次必须改成新阻力、新证据、新人物反应或直接删除。",
        acceptance="同一角色+同一物件+同一动作功能不再分散重复出现，每次出现都产生新的状态变化。",
    ),
    "SCENE_JUMP_UNRESOLVED": QualityRepairPlaybook(
        code="SCENE_JUMP_UNRESOLVED",
        scope="chapter",
        instruction="补齐场景跳转桥。每次地点、时间或视角切换前后必须增加明确承接：谁带着什么物证离开、经过多久、如何到达新地点、上一场未解压力如何继续压到下一场。",
        acceptance="所有地点/时间切换都有可读桥接句，读者不会感觉人物瞬移或事件断层。",
    ),
    "CROSS_CHAPTER_REPETITION": QualityRepairPlaybook(
        code="CROSS_CHAPTER_REPETITION",
        scope="paragraph",
        instruction="本章不得复刻前文段落、桥段或开场组织方式。保留必要承接信息，但换成新的现场动作、道具、冲突入口和读者疑问。",
        acceptance="与最近章节不存在高相似正文块，承接信息不超过两句。",
    ),
    "CHAPTER_OPENING_REPETITION": QualityRepairPlaybook(
        code="CHAPTER_OPENING_REPETITION",
        scope="paragraph",
        instruction="重写本章前300字。开篇必须从新的戏剧入口切入，可以是动作、异常物证、对话逼问或危险升级，不得沿用最近章节的抽象总结/发现/沉默模板。",
        acceptance="开篇80字与最近12章开篇低相似，并在前300字内给出具体问题或危险。",
    ),
    "ANTI_META_LEAK": QualityRepairPlaybook(
        code="ANTI_META_LEAK",
        scope="paragraph",
        instruction="删除正文里的创作术语、章节说明、读者提示、主线/钩子/节奏等元叙事语言，改写成角色当下可感知的动作、物证或对白。",
        acceptance="正文不出现策划词、门禁词、提示词痕迹，信息只通过故事内部表达。",
    ),
    "ANTI_META_ENDING_OUT_OF_SCENE": QualityRepairPlaybook(
        code="ANTI_META_ENDING_OUT_OF_SCENE",
        scope="ending",
        instruction=(
            "重写章末最后300字，让钩子落到完成画面帧。若钩子是对白，必须在对白后追加一"
            "句现场动作、物件变化、门/灯/镜面/身体反应等可视化收束帧；不得让最后一句只"
            "是一句台词、抽象解释、作者口吻总结或仍在进行中的动作。"
        ),
        acceptance="最后一句仍在现场内，包含明确主体+动作/物件变化，并自然制造下一章阅读问题。",
    ),
    "HOOK_ECHO_MISSING": QualityRepairPlaybook(
        code="HOOK_ECHO_MISSING",
        scope="chapter",
        instruction="本章必须兑现上一章尾钩。前1000字内点名或行动化处理上一章留下的人物、危险、物件或未答问题，再进行升级或反转。",
        acceptance="上一章尾钩至少一个核心承诺在前1000字内被看见、验证、升级或反转。",
    ),
    "HOOK_ECHO_LOW": QualityRepairPlaybook(
        code="HOOK_ECHO_LOW",
        scope="chapter",
        instruction="增强上一章尾钩回响。把模糊承接改成至少两个具体回响点：人物、地点、物证、威胁、未答问题或代价。",
        acceptance="读者能明确感到上一章结尾在本章开场和中段继续施压。",
    ),
    "SIGNATURE_SCENE_MISSING": QualityRepairPlaybook(
        code="SIGNATURE_SCENE_MISSING",
        scope="chapter",
        instruction="补足本章应有的招牌场景。围绕核心卖点写出不可替代的视觉/仪式/推理/对抗场面，不得只用旁白说明已经发生。",
        acceptance="招牌场景以完整现场呈现，包含角色选择、阻力、代价和新信息。",
    ),
    "SIGNATURE_IMAGE_MISSING": QualityRepairPlaybook(
        code="SIGNATURE_IMAGE_MISSING",
        scope="chapter",
        instruction="补足方法论场景契约里的招牌意象。每个 scene 的 signature_image 必须被写成可见物件、动作、声响、光影或身体反应，不得只复述抽象主题。",
        acceptance="所有关键场景都有可被读者看见的标志性画面，且画面推动冲突或揭示新信息。",
    ),
    "OPENING_PRESSURE_THIN": QualityRepairPlaybook(
        code="OPENING_PRESSURE_THIN",
        scope="paragraph",
        instruction="重写开篇前100字。必须从当下压力进入：可见动作、感官刺激、异常物证、逼问或危险逼近至少出现两项，禁止先解释背景。",
        acceptance="前100字内有正在发生的行动和具体压力，读者能立刻知道问题正在逼近。",
    ),
    "ENDING_HOOK_MISSING": QualityRepairPlaybook(
        code="ENDING_HOOK_MISSING",
        scope="ending",
        instruction="重写章末最后120-300字。结尾必须抛出新的未解问题、可见威胁、反转信息、未完成动作或选择后果；不得以总结、平静离场或已经解决的问题收束。",
        acceptance="最后120字内存在明确下一章阅读动力，且钩子落在故事现场而非作者解释。",
    ),
    "PARAGRAPH_DUPLICATE_PARAPHRASE": QualityRepairPlaybook(
        code="PARAGRAPH_DUPLICATE_PARAPHRASE",
        scope="paragraph",
        instruction="删除或合并语义重复段落。保留第一次有效表达，后续重复必须改为新证据、新阻力、新选择或直接删除。",
        acceptance="章内不存在同义改写式循环段，每个段落都产生新的状态变化。",
    ),
    "CALLBACK_OBLIGATION_MISSING": QualityRepairPlaybook(
        code="CALLBACK_OBLIGATION_MISSING",
        scope="chapter",
        instruction="补回本章必须兑现的 callback obligation。把指定 clue_surface 写进现场：被角色看见、验证、误解、利用或付出代价，不能只在旁白里说已经处理。",
        acceptance="本章应兑现的回调义务至少一个以现场结果落地，并同步种下新的后续压力。",
    ),
    "LENGTH_OUT_OF_BAND": QualityRepairPlaybook(
        code="LENGTH_OUT_OF_BAND",
        scope="chapter",
        instruction="把章节压回发布字数硬范围。过短时补足行动链、对白交锋、证据变化和代价；过长时删除重复心理、解释性背景和不改变局面的铺陈。",
        acceptance="章节真实字数回到硬范围内，新增或删除内容不破坏主冲突和章末牵引。",
    ),
    "GOLDEN_THREE_WEAK": QualityRepairPlaybook(
        code="GOLDEN_THREE_WEAK",
        scope="opening",
        instruction="强化黄金三章。前1000字必须兑现卖点信号、主角处境压力、核心冲突和至少一个具体悬念，避免慢热铺陈和抽象介绍。",
        acceptance="前三章读者能迅速看见题材卖点、主角困境、冲突方向和继续阅读的问题。",
    ),
    "NAMING_OUT_OF_POOL": QualityRepairPlaybook(
        code="NAMING_OUT_OF_POOL",
        scope="chapter",
        instruction="移除角色池外姓名。重要角色改用项目角色池/本章参与者中的既有人名；功能性人物改为身份称谓，不再临时创造专名。",
        acceptance="正文中的专名均来自允许名单、正典实体或明确的案卷/记录引用。",
    ),
    "POV_DRIFT": QualityRepairPlaybook(
        code="POV_DRIFT",
        scope="scene",
        instruction="叙述人称与全书视角不一致（如第三人称书中整场以'我'叙述）。按全书统一视角整体重写人称错误的场景：叙述层不得出现第一人称，内心念头改用自由间接思维或引号内心声呈现，剧情与对白内容不变。",
        acceptance="全章叙述层（引号对白除外）人称与全书视角一致，无第一人称叙述残留。",
    ),
    "CLIFFHANGER_REPEAT": QualityRepairPlaybook(
        code="CLIFFHANGER_REPEAT",
        scope="ending",
        instruction="更换章末悬念类型。若前文已用身体反应、门外来人、电话/传讯、突然停顿等模板，本章必须改用新证据、代价选择、关系反转或行动未完成态。",
        acceptance="章末钩子与近期章节不重复，并提供新的信息压力或行动压力。",
    ),
    "EXPOSITION_DUMP": QualityRepairPlaybook(
        code="EXPOSITION_DUMP",
        scope="paragraph",
        instruction="拆解连续设定说明。把背景、规则、关系和推理分散进行动、对话、物证检验和现场冲突，删除不改变场面的解释段。",
        acceptance="连续说明段被打散，读者通过现场变化理解信息。",
    ),
    "CAST_VIOLATION": QualityRepairPlaybook(
        code="CAST_VIOLATION",
        scope="chapter",
        instruction="删除或替换本章不允许登场角色的当下动作、对白、心理和现场反应。历史提及时必须明确是案卷、记录、回忆或他人转述。",
        acceptance="角色出场与章节允许名单、生命周期状态和正典状态一致。",
    ),
    "DIALOG_UNPAIRED": QualityRepairPlaybook(
        code="DIALOG_UNPAIRED",
        scope="paragraph",
        instruction="修复未闭合引号、无归属对白和连续悬浮对白。每段对话都必须能识别说话者、动作状态和对话推动的信息。",
        acceptance="对白标点成对，人物归属清晰，且没有无场景支撑的台词堆叠。",
    ),
    "ENDING_SENTENCE_WEAK": QualityRepairPlaybook(
        code="ENDING_SENTENCE_WEAK",
        scope="ending",
        instruction="重写最后一句。结尾必须是具体现场动作、异常物证、威胁到达或选择落下，不得是情绪总结、道理、空泛悬念。",
        acceptance="最后一句可拍成镜头，并让读者自然想看下一章。",
    ),
    "CANON_FORBIDDEN_TERM": QualityRepairPlaybook(
        code="CANON_FORBIDDEN_TERM",
        scope="chapter",
        instruction="移除正典禁止词或旧设定名。若必须保留，只能作为案卷原文或错误线索出现，并在同场景内被角色识别为不可信。",
        acceptance="正文不再把禁止词当作真实世界状态使用。",
    ),
    "CANON_STATE_REGRESSION": QualityRepairPlaybook(
        code="CANON_STATE_REGRESSION",
        scope="chapter",
        instruction="修复正典状态倒退。以最新故事事实为准重写角色状态、地点控制权、物件归属和组织关系。",
        acceptance="本章状态与最新正典一致，没有回退到旧版本设定。",
    ),
    "WORD_COUNT_METADATA_MISMATCH": QualityRepairPlaybook(
        code="WORD_COUNT_METADATA_MISMATCH",
        scope="chapter",
        instruction="本章正文实际汉字数远低于声称字数（疑似只写了大纲摘要/骨架）。必须把每个场景写成完整现场：动作链、对白交锋、感官细节、人物选择与代价，直到正文真实汉字数达到章节目标硬下限。严禁用形容词堆叠或重复同义句凑数。",
        acceptance="正文真实 CJK 汉字数达到章节硬下限，且新增内容均为可读现场而非概述。",
    ),
    "PAYOFF_LEDGER_LOW": QualityRepairPlaybook(
        code="PAYOFF_LEDGER_LOW",
        scope="chapter",
        instruction="本章钩子多、兑现少（读者一直被吊、很少被满足）。在本章内至少落地一个具体兑现：揭示一项确凿事实、解决一个悬念、让主角付出或赢得可见代价，并写成现场结果而非旁白预告。",
        acceptance="本章存在至少一个读者可感知的明确兑现，兑现/钩子比回到健康区间。",
    ),
    "PAYOFF_HOOK_ONLY": QualityRepairPlaybook(
        code="PAYOFF_HOOK_ONLY",
        scope="chapter",
        instruction="本章只抛钩子、几乎无兑现。补一个当章闭环的小兑现（线索被证实/一次对抗分出胜负/一个秘密被揭开），再用新钩子收尾，避免空转。",
        acceptance="本章至少兑现一个此前埋设或本章新生的悬念，且仍保留下一章动力。",
    ),
    "PERSONA_ABANDON_RATE_HIGH": QualityRepairPlaybook(
        code="PERSONA_ABANDON_RATE_HIGH",
        scope="chapter",
        instruction="模拟读者弃读率过高。定位最可能弃读的段落（开篇拖沓、信息倾倒、缺乏冲突或兑现），重写为：更快进入冲突、把设定藏进动作、补足情绪与兑现，砍掉不推进的过场。",
        acceptance="开篇即有钩子与张力，全章无大段说明倾倒，弃读风险段被改写。",
    ),
    "PERSONA_WEIGHTED_SCORE_LOW": QualityRepairPlaybook(
        code="PERSONA_WEIGHTED_SCORE_LOW",
        scope="chapter",
        instruction="模拟读者综合读感分偏低。同时提升节奏、冲突清晰度、情绪冲击与新鲜感：强化主角主动选择、增加具体感官画面、避免套路化桥段与模板化措辞。",
        acceptance="综合读感分回到合格线以上，章节在节奏/冲突/情绪/新鲜感各维均无明显短板。",
    ),
    "PERSONA_PAYOFF_DENSITY_LOW": QualityRepairPlaybook(
        code="PERSONA_PAYOFF_DENSITY_LOW",
        scope="chapter",
        instruction="模拟读者反馈兑现密度过低。把至少一处悬念在本章内落地为可见结果（证据/对抗结果/关系变化/代价），并确保兑现以现场动作呈现而非概述交代。",
        acceptance="兑现密度回到目标阈值以上，读者能在本章获得明确满足感。",
    ),
}


def get_quality_repair_playbook(code: str) -> QualityRepairPlaybook | None:
    return _PLAYBOOKS.get(str(code or "").strip())


def render_quality_repair_playbooks(
    codes: Iterable[str],
    *,
    include_book_methodology: bool = True,
) -> str:
    rendered: list[str] = []
    seen: set[str] = set()
    scopes: set[str] = set()
    for raw_code in codes:
        code = str(raw_code or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        playbook = get_quality_repair_playbook(code)
        if playbook is not None:
            scopes.add(playbook.scope)
            rendered.append(playbook.render())
    if include_book_methodology and rendered:
        methodology_block = _render_book_methodology_repair_block(scopes)
        if methodology_block:
            rendered.append(methodology_block)
    return "\n".join(rendered)


def _render_book_methodology_repair_block(scopes: set[str]) -> str:
    scope = "scene" if scopes and scopes.issubset({"paragraph", "ending"}) else "chapter"
    try:
        return render_book_methodology_block(
            stage="repair",
            scope=scope,
            language="zh-CN",
            max_cards=3,
            token_budget=600,
        )
    except Exception:
        return ""


__all__ = [
    "QualityRepairPlaybook",
    "get_quality_repair_playbook",
    "render_quality_repair_playbooks",
]
