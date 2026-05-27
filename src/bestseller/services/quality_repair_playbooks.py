"""Repair playbooks for commercial quality findings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


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
}


def get_quality_repair_playbook(code: str) -> QualityRepairPlaybook | None:
    return _PLAYBOOKS.get(str(code or "").strip())


def render_quality_repair_playbooks(codes: Iterable[str]) -> str:
    rendered: list[str] = []
    seen: set[str] = set()
    for raw_code in codes:
        code = str(raw_code or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        playbook = get_quality_repair_playbook(code)
        if playbook is not None:
            rendered.append(playbook.render())
    return "\n".join(rendered)


__all__ = [
    "QualityRepairPlaybook",
    "get_quality_repair_playbook",
    "render_quality_repair_playbooks",
]
