"""Prompt-strategy arena for scene prose writing.

The production writer prompt already carries many methodology cards, but it is
hard to tell which instruction shape actually changes the prose. This module
builds a same-input, many-strategy experiment package from a real prompt trace.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
from html import escape
import json
from pathlib import Path
from typing import Any

# ruff: noqa: RUF001


@dataclass(frozen=True)
class PromptTraceCase:
    case_id: str
    source_path: str
    system_prompt: str
    user_prompt: str
    project: dict[str, Any] = field(default_factory=dict)
    chapter: dict[str, Any] = field(default_factory=dict)
    scene: dict[str, Any] = field(default_factory=dict)
    prompt_stats: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PromptStrategy:
    strategy_id: str
    title: str
    hypothesis: str
    instruction: str
    diagnostic_focus: str


@dataclass(frozen=True)
class PromptVariant:
    variant_id: str
    case_id: str
    strategy: PromptStrategy
    system_prompt: str
    user_prompt: str


@dataclass(frozen=True)
class DraftResult:
    draft_id: str
    variant_id: str
    writer_model: str
    sample_index: int
    text: str
    provider: str | None = None
    finish_reason: str | None = None
    output_path: str | None = None


@dataclass(frozen=True)
class JudgeResult:
    draft_id: str
    judge_model: str
    scores: dict[str, float]
    winner_reason: str
    risk_notes: list[str] = field(default_factory=list)
    raw_text: str = ""


@dataclass(frozen=True)
class ExperimentReport:
    case: PromptTraceCase
    variants: list[PromptVariant]
    drafts: list[DraftResult]
    judgements: list[JudgeResult]
    created_at: str
    dry_run: bool = False


SCORE_KEYS = (
    "opening_hook",
    "golden_three_fit",
    "shuangwen_payoff",
    "suspense_hook",
    "scene_causality",
    "character_embodiment",
    "prose_texture",
    "anti_ai_flavor",
    "reader_onboarding",
    "ending_hook",
    "overall",
)

SCORE_DIMENSION_GUIDANCE = {
    "opening_hook": {
        "label": "开篇钩子",
        "prompt_probe": "正文 prompt 是否把第一眼异常/危险/欲望写成当前场景硬要求。",
        "outline_probe": "细纲是否已经给出可见异常、压力来源和主角被迫行动的触发点。",
    },
    "golden_three_fit": {
        "label": "黄金三章职责",
        "prompt_probe": "prompt 是否要求前三章承担锁欲望、抛问题、显卖点的具体动作。",
        "outline_probe": "前三章章节功能是否明确，是否每章都有追读问题和商业卖点兑现。",
    },
    "shuangwen_payoff": {
        "label": "爽点交付",
        "prompt_probe": "prompt 是否要求压迫、选择、执行、反馈四拍，而不是只写结论。",
        "outline_probe": "细纲是否有清晰压迫、主角选择、结果反馈和收益落袋。",
    },
    "suspense_hook": {
        "label": "悬念/信息差",
        "prompt_probe": "prompt 是否把悬疑拆成异常、误判、证据、反向验证。",
        "outline_probe": "大纲是否提供线索阶梯，而不是只给最终答案。",
    },
    "scene_causality": {
        "label": "场景因果",
        "prompt_probe": "prompt 是否逐拍锁定目标、阻力、代价、不可逆变化。",
        "outline_probe": "场景合同是否缺目标、阻力、代价或状态变化。",
    },
    "character_embodiment": {
        "label": "角色具身",
        "prompt_probe": "prompt 是否要求身体动作、微选择和触感先行，少解释判断。",
        "outline_probe": "细纲是否给角色即时压力和具体可做动作，而不只是心理结论。",
    },
    "prose_texture": {
        "label": "正文质感",
        "prompt_probe": "prompt 是否要求具体物料、非整齐细节和可读句式。",
        "outline_probe": "场景素材是否有地点、道具、规则、人物反应可供落笔。",
    },
    "anti_ai_flavor": {
        "label": "去 AI 味",
        "prompt_probe": "prompt 是否禁止总结、评价、套话和机械排比。",
        "outline_probe": "素材是否足够具体；素材空时模型会回到抽象解释。",
    },
    "reader_onboarding": {
        "label": "新读者可读性",
        "prompt_probe": "prompt 是否要求前 500 字让没看过设定的新读者能弄清主角是谁、"
        "此刻在做什么、想要什么、眼前威胁/异常是什么，并限制未解释的专名/术语数量。",
        "outline_probe": "大纲是否把开篇处境写成可懂的大白话，而不是堆叠世界观专名与黑话。",
    },
    "ending_hook": {
        "label": "结尾钩子",
        "prompt_probe": "prompt 是否锁定最后 120 字必须出现新问题/代价/强敌动作。",
        "outline_probe": "场景切点是否压过旧问题，是否留下下一章必须看的具体未解物。",
    },
    "overall": {
        "label": "整体体验",
        "prompt_probe": "prompt 是否把方法论翻译成当前场景的少数硬约束。",
        "outline_probe": "如果多个维度同时低，优先回查大纲/细纲而不是继续堆 prompt 术语。",
    },
}

METHODOLOGY_APPLICATION_RULES = (
    {
        "dimension": "golden_three_opening",
        "label": "黄金三章/开篇抓人",
        "concept_terms": ("黄金三章", "开篇", "开场", "第一段", "留存", "追读"),
        "operational_terms": (
            "第一段必须",
            "前100字",
            "前 100 字",
            "立刻给出",
            "异常",
            "压力",
            "欲望",
            "危险",
            "禁止用世界观解释",
        ),
        "risk": "只写“开篇抓人”会被模型理解成抽象氛围，未必会改第一段结构。",
    },
    {
        "dimension": "shuangwen_payoff",
        "label": "爽点交付",
        "concept_terms": ("爽点", "正反馈", "打脸", "反转", "碾压", "解谜爽感"),
        "operational_terms": (
            "压迫",
            "选择",
            "执行",
            "反馈",
            "收益落袋",
            "旁观者反应",
            "正反馈节点",
        ),
        "risk": "只提爽点会让模型写结果摘要，缺压迫到反馈的完整四拍。",
    },
    {
        "dimension": "suspense_ladder",
        "label": "悬念/信息差阶梯",
        "concept_terms": ("悬念", "信息差", "线索", "伏笔", "揭示", "未答之问"),
        "operational_terms": (
            "异常",
            "错误解释",
            "关键证据",
            "反向验证",
            "新问题",
            "线索阶梯",
        ),
        "risk": "只要求悬念容易变成一次性解释答案，而不是层层推进。",
    },
    {
        "dimension": "ending_hook",
        "label": "结尾悬念",
        "concept_terms": ("结尾", "章末", "钩子", "hook", "下一章", "追读"),
        "operational_terms": (
            "最后120字",
            "最后 120 字",
            "新变量",
            "强敌登场",
            "代价显形",
            "身份暴露",
            "新问题压过旧问题",
        ),
        "risk": "只提醒章末钩子容易平收，必须锁定最后一段出现的新压力。",
    },
    {
        "dimension": "scene_causality",
        "label": "场景因果合同",
        "concept_terms": ("目标", "阻力", "代价", "状态变化", "场景合同", "因果"),
        "operational_terms": (
            "主角要什么",
            "谁/什么挡住",
            "失败会失去什么",
            "不可逆变化",
            "入场状态",
            "出场状态",
        ),
        "risk": "因果合同如果不落成逐拍问题，正文会像按大纲顺序复述。",
    },
    {
        "dimension": "anti_ai_flavor",
        "label": "去 AI 味",
        "concept_terms": ("AI味", "AI 味", "去 AI", "套话", "总结", "解释"),
        "operational_terms": (
            "禁止",
            "少解释",
            "少总结",
            "具体动作",
            "生活细节",
            "机械排比",
            "空泛",
        ),
        "risk": "只说去 AI 味不够，需要明确禁止总结评价，并要求动作/物料承载信息。",
    },
)


def load_prompt_trace(path: str | Path) -> PromptTraceCase:
    trace_path = Path(path)
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    prompts = _mapping(payload.get("prompts"))
    system_prompt = str(prompts.get("system") or payload.get("system_prompt") or "").strip()
    user_prompt = str(prompts.get("user") or payload.get("user_prompt") or "").strip()
    if not system_prompt or not user_prompt:
        raise ValueError(f"Trace does not contain prompts.system/prompts.user: {trace_path}")

    project = dict(_mapping(payload.get("project")))
    chapter = dict(_mapping(payload.get("chapter")))
    scene = dict(_mapping(payload.get("scene")))
    case_parts = [
        str(project.get("slug") or trace_path.parent.parent.name or "unknown-project"),
        f"c{chapter.get('number', 'x')}",
        f"s{scene.get('number', 'x')}",
    ]
    return PromptTraceCase(
        case_id="-".join(case_parts),
        source_path=str(trace_path),
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        project=project,
        chapter=chapter,
        scene=scene,
        prompt_stats=dict(_mapping(payload.get("prompt_stats"))),
    )


def build_methodology_application_audit(case: PromptTraceCase) -> dict[str, Any]:
    """Check whether the original prose prompt operationalizes core methodology."""

    prompt_text = f"{case.system_prompt}\n{case.user_prompt}"
    findings = []
    status_counts = {"operationalized": 0, "mentioned_only": 0, "missing": 0}
    for rule in METHODOLOGY_APPLICATION_RULES:
        concept_hits = _find_terms(prompt_text, rule["concept_terms"])
        operational_hits = _find_terms(prompt_text, rule["operational_terms"])
        if len(operational_hits) >= 2:
            status = "operationalized"
        elif concept_hits or operational_hits:
            status = "mentioned_only"
        else:
            status = "missing"
        status_counts[status] += 1
        findings.append(
            {
                "dimension": rule["dimension"],
                "label": rule["label"],
                "status": status,
                "concept_hits": concept_hits,
                "operational_hits": operational_hits,
                "risk": rule["risk"],
            }
        )

    if status_counts["mentioned_only"] or status_counts["missing"]:
        summary = (
            "原始正文 prompt 存在方法论未落成硬约束的风险；横评策略会把这些概念"
            "分别翻译成开篇、爽点、悬念、因果和去 AI 味的可执行要求。"
        )
    else:
        summary = "原始正文 prompt 已覆盖主要方法论硬约束；横评重点转向约束组合和信息预算。"
    return {
        "summary": summary,
        "status_counts": status_counts,
        "findings": findings,
    }


def build_default_strategies() -> list[PromptStrategy]:
    """Return the ordered prose-prompt strategy catalogue."""

    return [
        _strategy(
            "production_control",
            "生产原样控制组",
            "验证现有 prompt 在同一素材下的自然表现，作为横评基线。",
            "完全服从原始正文 prompt，不额外强化任何单一技法。",
            "baseline",
        ),
        _strategy(
            "human_process_first",
            "过程优先·去AI腔",
            "强制‘先动作后判断、逐层透出’的人类叙事结构，能否去掉总分总的AI腔。",
            "像真正的网文作者那样写，让读者跟着人物体验过程，而不是先告诉读者结论。"
            "硬性规则：\n"
            "1. 不要‘结论先行’：禁止先抛出一个判断、情绪标签或场面总结，再用描写去补证。"
            "先写正在发生的具体动作和感知，判断让读者自己得出，能不说就不说。"
            "删掉所有替读者把账算完的句子（如‘他算了一笔账’‘命不能拿来垫房租’）。\n"
            "2. 禁止用‘没做什么’当叙事主句（如‘他没抬头’‘他没回头’‘他没吭声’）；"
            "直接写他正在做的那件事。\n"
            "3. 禁止为文学感硬造比喻（如‘像指甲刮过搪瓷盆底’‘像骨头响’‘跟心电图似的’）；"
            "要么不用比喻，要么只用这个人物在此刻真会联想到的、贴他生活经验的东西。\n"
            "4. 开篇直接落进一个正在进行的具体动作里，先让读者跟着人物做一件事，"
            "再把环境、旁人、不对劲的地方一层层慢慢透出来——是逐步发现，不是开场announce。\n"
            "5. 句子向前流动，不要为了节奏切成一连串短促独行句。\n"
            "示范这种结构（仅示意写法，不要照抄内容）：开篇=人物正在做的一个具体动作+这个动作的"
            "手感/声音→镜头自然扩到周围→某个细节开始不对劲→人物的身体先有反应。",
            "anti_ai_structure",
        ),
        _strategy(
            "ban_ai_tics_only",
            "只删AI腔四件套",
            "只外科式删除四类AI腔句式、不加任何正面框架，看‘减法’本身值多少。",
            "在不改变剧情和信息的前提下，删除/改写以下四类句子，其余照常：\n"
            "①结论先行句：先给判断/情绪/总结再补描写——改成只留下具体动作与感知；\n"
            "②负面动作主句：‘他没抬头/没回头/没吭声’——改成他实际在做的动作；\n"
            "③硬造比喻：‘像指甲刮过搪瓷盆底’这类为文学感而生的比喻——删掉或换成人物真实会想到的；\n"
            "④总结式内心：‘他算了一笔账’‘脑子里过的是A、B、再加上C’——改成让这些念头落到具体动作或单个画面上。",
            "anti_ai_structure",
        ),
        _strategy(
            "reader_onboarding_contract",
            "新读者入场契约",
            "限制未解释术语并强制大白话交代处境后，新读者是否更读得懂、且不丢画面感。",
            "把这一场当成一个完全没读过本书的新读者的第一次接触来写："
            "前 500 字必须让他能弄清——主角是谁、此刻在做什么、眼下想要什么、面前的"
            "异常或威胁是什么，全部用大白话讲清。世界观/体系专有名词（境界名、功法名、"
            "组织名、行业黑话）本场最多出现 2 个，且每个第一次出现时必须能从角色动作或"
            "当场后果推断出含义；其余一律改用普通说法或推迟到后文。宁可暂时不点名，"
            "也不要堆砌读者无法理解的名词。具体的动作和物象照常保留，不要退回抽象旁白。",
            "reader_onboarding",
        ),
        _strategy(
            "defer_jargon_show_anomaly",
            "先怪事后名词",
            "把机制先写成可感怪事、名字后置，是否消除‘看不懂在讲什么’。",
            "任何‘机制/体系/能力’都先当成一桩具体、可感、读者能看见也能怕的怪事来写；"
            "它的正式名称、原理、归属一律推迟到本章之后。第一次出现时只写现象本身和"
            "主角的身体反应（手、眼、呼吸、动作），绝不写它‘叫什么/是什么/属于哪一套体系’。",
            "reader_onboarding",
        ),
        _strategy(
            "plain_throughline_lead",
            "大白话主线先行",
            "开篇先一句口语化处境句、主线贯穿，是否提升追读且不显啰嗦。",
            "第一段先用一句像跟朋友口述一样的普通话，把‘主角此刻的处境＋一个具体麻烦’"
            "交代清楚，再进入氛围与细节。之后每一段，读者都应能用一句话说清‘现在主角在"
            "干什么、为什么’。氛围与具体物象只能服务这条主线，不得把主线淹没。",
            "reader_onboarding",
        ),
        _strategy(
            "clarity_keep_concrete",
            "清晰且具体（兼得）",
            "同时满足新读者秒懂与具体画面，验证可读性无需牺牲密度。",
            "两条同时满足：①一个没读过设定的新读者读完前 500 字，能复述主角是谁、此刻在"
            "干嘛、想要什么、威胁是什么，且未被术语劝退（未解释专名≤2，且当场可推断）；"
            "②保留具体可视的动作、物件、感官，不退回‘他很害怕/他意识到’式抽象旁白。"
            "清晰优先于炫技：当某个意象或术语会让新读者卡住时，先保证读懂。",
            "reader_onboarding",
        ),
        _strategy(
            "golden_three_opening",
            "黄金三章开篇钩子",
            "把黄金三章的抓人原则迁移到任意正文场景后，模型是否会先抓住读者。",
            "把黄金三章的开篇职责迁移到当前场景：第一段必须立刻给出异常、压力、"
            "欲望或危险，禁止用世界观解释、天气铺陈、抽象感慨开场。",
            "opening",
        ),
        _strategy(
            "reader_question_chain",
            "读者问题链",
            "用连续未答问题推动读者往下读，而不是只复述设定。",
            "每 300-500 字至少制造一个读者想问的具体问题；每次解答一个小问题，"
            "立刻抛出更强问题。问题必须来自行动结果，不要用旁白提问。",
            "retention",
        ),
        _strategy(
            "shuangwen_payoff_first",
            "爽点交付优先",
            "正文是否能把智取、碾压、反转或规则利用写成可感爽点。",
            "找到本场景的核心爽点，把准备、出手、众人反应、收益落袋写完整。"
            "爽点不能只写成结论，必须有压迫、选择、执行、反馈四拍。",
            "shuangwen",
        ),
        _strategy(
            "suspense_reveal_ladder",
            "悬疑揭示阶梯",
            "悬疑类场景需要线索递进，而不是一次性解释答案。",
            "把真相拆成至少三层：异常现象、错误解释、关键证据、反向验证。"
            "每层都用可见物件或动作推进，禁止纯心理独白揭秘。",
            "suspense",
        ),
        _strategy(
            "ending_hook_lock",
            "章末悬念锁定",
            "结尾悬念单独前置后，是否能避免平收。",
            "最后 120 字必须改变读者预期：强敌登场、代价显形、规则反噬、"
            "身份暴露或新问题压过旧问题。不要用“他不知道的是”式旁白。",
            "ending",
        ),
        _strategy(
            "scene_contract_visible",
            "场景合同可见化",
            "把目标、阻力、代价、状态变化显性化后，因果是否更强。",
            "每个行动段都要回答：主角要什么、谁/什么挡住、失败会失去什么、"
            "这一拍结束后局面有什么不可逆变化。",
            "causality",
        ),
        _strategy(
            "character_body_first",
            "角色身体先行",
            "用身体反应和微选择替代作者解释，提升代入。",
            "主角的判断必须先落到手、眼、呼吸、步伐、停顿、触感等具体反应，"
            "再给出一句以内的判断。少写‘他意识到/他明白了’。",
            "embodiment",
        ),
        _strategy(
            "concrete_materials_only",
            "物料具体化",
            "检查抽象方法论是否能落到道具、规则、地点、人物动作。",
            "优先使用原 prompt 给出的地点、道具、规则、人物名。每个关键动作都要"
            "碰到一个具体物件；禁止用泛词替代已给定物料。",
            "grounding",
        ),
        _strategy(
            "dialogue_subtext",
            "对白潜台词",
            "用言行不一和微动作制造人物张力。",
            "对白不得只承担解释功能。每段关键对白都要带一个微动作、隐藏目的或"
            "关系压迫；让人物通过话外之意争夺主动权。",
            "dialogue",
        ),
        _strategy(
            "low_exposition_high_action",
            "低解释高行动",
            "压低说明文比例，测试正文是否更像小说。",
            "连续解释不得超过两句；设定、规则、背景必须通过试探、失败、反应、"
            "代价显形来呈现。删除总结式评价。",
            "anti_exposition",
        ),
        _strategy(
            "micro_conflict_everybeat",
            "小冲突密度",
            "让每一小段都有阻力，避免平铺。",
            "每 200-350 字必须出现一次新的阻力或误判：物理阻碍、规则限制、"
            "人物反对、时间压力、信息错误均可。",
            "pacing",
        ),
        _strategy(
            "payoff_reaction_amplifier",
            "反馈放大器",
            "爽点如果缺旁观反馈，会像剧情摘要。",
            "主角行动成功或失败后，必须写出环境、对手、旁观者或规则系统的即时反应。"
            "反应要改变压力，不要只写震惊。",
            "reaction",
        ),
        _strategy(
            "stakes_clock",
            "筹码倒计时",
            "把输掉代价和时间压力钉住，提升追读。",
            "从开场就放出倒计时或不可逆代价；每次拖延都让代价逼近。"
            "读者必须知道主角为什么不能等。",
            "stakes",
        ),
        _strategy(
            "signature_image",
            "招牌画面牵引",
            "用一个强画面组织场景，而不是散点执行大纲。",
            "围绕原 prompt 的 signature_image 或最强视觉结果写作。开头埋视觉部件，"
            "中段推进，结尾兑现或反转这个画面。",
            "imagery",
        ),
        _strategy(
            "genre_voice_xuanhuan",
            "类型声音强化",
            "测试正文是否更像目标网文类型，而不是通用 AI 小说。",
            "句子要短促、可读、动作明确；保留玄幻/民俗悬疑的规则感、禁忌感、"
            "压迫感。不要文学腔、散文化、影视分镜腔。",
            "genre_voice",
        ),
        _strategy(
            "anti_ai_texture",
            "去 AI 味纹理",
            "直接压制空泛、总结、解释、套路转折。",
            "禁止使用空泛套话、总括评价、情绪标签、机械排比。用不整齐的生活细节、"
            "具体动作后果和人物误判制造真实感。",
            "anti_ai",
        ),
        _strategy(
            "premise_diagnosis",
            "大纲缺陷探针",
            "如果素材本身没有强钩子，此策略会暴露只能补写不能救活的问题。",
            "不要替大纲发明新主线。严格使用已有目标、阻力、代价、结尾钩子；"
            "如果某项缺失，就在正文中用最小补丁补成可见行动，不扩写新设定。",
            "outline_probe",
        ),
        _strategy(
            "methodology_translation",
            "方法论翻译器",
            "把黄金三章、爽点、悬念从概念翻译成可执行动作。",
            "先在心里把方法论翻译为本场景三件事：第一眼钩子、核心爽点、末尾悬念。"
            "正文只呈现翻译后的行动和画面，不出现方法论术语。",
            "methodology_application",
        ),
        _strategy(
            "minimal_bestseller_brief",
            "极简爆款 brief",
            "测试原 prompt 是否过载；只给最少但强约束的写法。",
            "忽略冗余解释，只抓：开场钩子、主角目标、阻力、爽点兑现、章末钩子。"
            "写成读者愿意继续翻页的网文正文。",
            "prompt_budget",
        ),
    ]


def build_prompt_variants(
    case: PromptTraceCase,
    strategies: list[PromptStrategy] | None = None,
    *,
    limit: int | None = None,
) -> list[PromptVariant]:
    chosen = strategies or build_default_strategies()
    if limit is not None:
        chosen = chosen[:limit]
    return [
        PromptVariant(
            variant_id=f"{case.case_id}__{strategy.strategy_id}",
            case_id=case.case_id,
            strategy=strategy,
            system_prompt=case.system_prompt,
            user_prompt=render_strategy_user_prompt(case.user_prompt, strategy, case),
        )
        for strategy in chosen
    ]


def render_strategy_user_prompt(
    base_user_prompt: str,
    strategy: PromptStrategy,
    case: PromptTraceCase | None = None,
) -> str:
    if strategy.strategy_id == "production_control":
        return base_user_prompt
    resource_brief = build_scene_resource_brief(case) if case is not None else ""
    block = f"""# 本次正文横评策略（优先级高于下方同类抽象要求）
{resource_brief}

策略ID：{strategy.strategy_id}
策略名称：{strategy.title}
验证假设：{strategy.hypothesis}
执行要求：{strategy.instruction}
诊断焦点：{strategy.diagnostic_focus}

注意：不要在正文中提及“策略、方法论、黄金三章、爽点、诊断”等元话语；只把要求落实为剧情动作、画面、对白和结尾钩子。
"""
    return f"{block}\n\n---\n\n{base_user_prompt}"


def build_scene_resource_brief(case: PromptTraceCase | None) -> str:
    if case is None:
        return ""
    chapter_meta = _mapping(case.chapter.get("metadata"))
    scene_meta = _mapping(case.scene.get("metadata"))
    chapter_contract = _mapping(chapter_meta.get("causal_contract"))
    chapter_methodology = _mapping(chapter_meta.get("methodology_contract"))
    scene_methodology = _mapping(scene_meta.get("methodology_contract"))
    selected_effects = _mapping(chapter_meta.get("selected_effect_skills"))
    expected_contracts = _mapping(selected_effects.get("expected_contracts"))
    purpose = _mapping(case.scene.get("purpose"))
    entry_state = _mapping(case.scene.get("entry_state"))
    exit_state = _mapping(case.scene.get("exit_state"))

    lines = [
        "# 同一场景资源摘要（20 个策略共用，不得改写成新剧情）",
        _brief_line("书名", case.project.get("title")),
        _brief_line("类型", case.project.get("genre") or case.project.get("sub_genre")),
        _brief_line("章节", _chapter_label(case.chapter)),
        _brief_line("场景", _scene_label(case.scene)),
        _brief_line("章节功能", chapter_contract.get("chapter_function")),
        _brief_line("主角欲望", chapter_contract.get("protagonist_desire")),
        _brief_line("当前压力", chapter_contract.get("pressure")),
        _brief_line("阻力", chapter_contract.get("resistance")),
        _brief_line("主角选择", chapter_contract.get("protagonist_choice")),
        _brief_line("可见行动/反馈", chapter_contract.get("visible_action_or_reaction")),
        _brief_line("收益/揭示", chapter_contract.get("gain_or_reveal")),
        _brief_line("代价", chapter_contract.get("cost_or_tradeoff")),
        _brief_line("下一追读欲望", chapter_contract.get("next_reader_desire")),
        _brief_line("场景故事目的", purpose.get("story")),
        _brief_line("场景情绪目的", purpose.get("emotion")),
        _brief_line("入场状态", entry_state.get("summary")),
        _brief_line("退场状态", exit_state.get("summary")),
        _brief_line("场景行动链", scene_methodology.get("action_sequence")),
        _brief_line("场景冲突筹码", scene_methodology.get("conflict_stakes")),
        _brief_line("切点", scene_methodology.get("cut_point") or scene_meta.get("cut_point")),
        _brief_line(
            "招牌画面",
            scene_methodology.get("signature_image") or scene_meta.get("signature_image"),
        ),
        _brief_line("方法论节奏", chapter_methodology.get("pacing_mode")),
        _brief_line("情绪相位", chapter_methodology.get("emotion_phase")),
        _brief_line("待解决钩子", chapter_methodology.get("hooks_to_resolve")),
        _brief_line("待种下钩子", chapter_methodology.get("hooks_to_plant")),
        _brief_line("选中效果主策略", selected_effects.get("primary")),
        _brief_line("选中效果副策略", selected_effects.get("secondary")),
        _brief_line("悬疑合同", expected_contracts.get("suspense_reveal_contract")),
        _brief_line("爽点合同", expected_contracts.get("hype_satisfaction_contract")),
        _brief_line("世界规则落地", chapter_meta.get("world_rule_landing")),
        _brief_line("规则资源", chapter_meta.get("world_rule_refs")),
        _brief_line("物料资源", chapter_meta.get("world_asset_refs")),
    ]
    return "\n".join(line for line in lines if line)


def build_judge_system_prompt() -> str:
    keys = "、".join(SCORE_KEYS)
    schema = json.dumps(build_judge_result_schema(), ensure_ascii=False)
    return (
        "你是网文正文盲评判官。你只根据正文成品评分，不知道它来自哪个提示词策略。"
        "按 0-10 分评估这些维度："
        f"{keys}。重点看：开篇是否抓人，前三章职责是否明确，爽点是否可感，"
        "悬念/信息差是否驱动阅读，章末是否让人想看下一段，正文是否少 AI 味。"
        "其中『新读者可读性(reader_onboarding)』单独严判：设想一个完全没读过本书设定的"
        "新读者，只读这一段，他能否说清主角是谁、此刻在做什么、想要什么、眼前的威胁/异常"
        "是什么；若被未解释的专有名词、黑话、境界/功法/组织名劝退，或读完仍不知道在讲什么、"
        "感觉塞了很多却抓不住主线，则该维必须给低分（≤4），即使画面感和文采很好也不例外。"
        "只输出 JSON，不要输出解释性正文。"
        "如果 user prompt 提供盲读编号和 Judge 标签，顶层必须原样带回 blind_label 和 judge_label。"
        f"JSON schema：{schema}。"
    )


def build_judge_result_schema(
    *,
    blind_label: str | None = None,
    judge_label: str | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {}
    if blind_label is not None:
        schema["blind_label"] = blind_label
    else:
        schema["blind_label"] = "optional; echo user blind label when provided"
    if judge_label is not None:
        schema["judge_label"] = judge_label
    else:
        schema["judge_label"] = "optional; echo user judge label when provided"
    schema.update(
        {
            "scores": dict.fromkeys(SCORE_KEYS, "0-10 number"),
            "winner_reason": "string",
            "risk_notes": ["string"],
        }
    )
    return schema


def build_judge_user_prompt(case: PromptTraceCase, draft_text: str) -> str:
    project_title = case.project.get("title") or ""
    genre = case.project.get("genre") or case.project.get("sub_genre") or ""
    chapter = case.chapter.get("number") or ""
    scene = case.scene.get("number") or ""
    return f"""项目：{project_title}
类型：{genre}
章节/场景：第{chapter}章 / 场景{scene}

盲评正文：
{draft_text}

请严格按 system 的 JSON schema 输出。"""


def make_dry_run_draft(variant: PromptVariant) -> str:
    strategy = variant.strategy
    return (
        f"【dry-run 占位稿：{strategy.title}】\n\n"
        "这不是 LLM 正文成品，只用于验证横评产物结构、HTML 展示和盲评链路。"
        f"该策略会优先测试：{strategy.instruction}\n\n"
        "正式运行时，此处会替换为同一 scene prompt 在对应 writer model 下生成的正文。"
    )


def make_dry_run_judgement(draft: DraftResult, variant: PromptVariant) -> JudgeResult:
    base = 5.0 if variant.strategy.strategy_id == "production_control" else 6.0
    if variant.strategy.diagnostic_focus in {"opening", "shuangwen", "ending"}:
        base = 7.0
    scores = dict.fromkeys(SCORE_KEYS, base)
    return JudgeResult(
        draft_id=draft.draft_id,
        judge_model="dry-run-judge",
        scores=scores,
        winner_reason="dry-run 结构评分，占位用于验证报告聚合。",
        risk_notes=["未调用真实 LLM，不能代表正文质量。"],
        raw_text=json.dumps({"scores": scores}, ensure_ascii=False),
    )


def parse_judge_result(draft_id: str, judge_model: str, raw_text: str) -> JudgeResult:
    payload = _parse_json_object(raw_text)
    scores = {
        key: _float_score(_mapping(payload.get("scores")).get(key))
        for key in SCORE_KEYS
    }
    return JudgeResult(
        draft_id=draft_id,
        judge_model=judge_model,
        scores=scores,
        winner_reason=str(payload.get("winner_reason") or "").strip(),
        risk_notes=[str(item) for item in _list(payload.get("risk_notes"))],
        raw_text=raw_text,
    )


def write_experiment_package(report: ExperimentReport, output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    prompts_dir = root / "prompts"
    drafts_dir = root / "drafts"
    judges_dir = root / "judgements"
    for path in (prompts_dir, drafts_dir, judges_dir):
        path.mkdir(parents=True, exist_ok=True)

    for variant in report.variants:
        _write_json(
            prompts_dir / f"{variant.strategy.strategy_id}.json",
            {
                "variant_id": variant.variant_id,
                "strategy": _strategy_to_dict(variant.strategy),
                "system_prompt": variant.system_prompt,
                "user_prompt": variant.user_prompt,
            },
        )

    draft_paths: dict[str, str] = {}
    for draft in report.drafts:
        path = drafts_dir / f"{draft.draft_id}.md"
        path.write_text(draft.text, encoding="utf-8")
        draft_paths[draft.draft_id] = str(path)
        _write_json(drafts_dir / f"{draft.draft_id}.json", draft_to_dict(draft, str(path)))

    for judgement in report.judgements:
        _write_json(
            judges_dir / f"{judgement.draft_id}__{_slug(judgement.judge_model)}.json",
            judgement_to_dict(judgement),
        )

    manifest_path = root / "manifest.json"
    html_path = root / "report.html"
    _write_json(manifest_path, report_to_dict(report, draft_paths=draft_paths))
    html_path.write_text(render_html_report(report), encoding="utf-8")
    return {"manifest": str(manifest_path), "html": str(html_path)}


def report_to_dict(
    report: ExperimentReport,
    *,
    draft_paths: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    draft_paths = draft_paths or {}
    blind_labels = build_blind_label_by_draft(report)
    return {
        "created_at": report.created_at,
        "dry_run": report.dry_run,
        "case": {
            "case_id": report.case.case_id,
            "source_path": report.case.source_path,
            "resource_brief": build_scene_resource_brief(report.case),
            "methodology_application_audit": build_methodology_application_audit(
                report.case
            ),
            "project": report.case.project,
            "chapter": report.case.chapter,
            "scene": report.case.scene,
            "prompt_stats": report.case.prompt_stats,
        },
        "variants": [
            {"variant_id": item.variant_id, "strategy": _strategy_to_dict(item.strategy)}
            for item in report.variants
        ],
        "drafts": [
            {
                "draft_id": draft.draft_id,
                "blind_label": blind_labels.get(draft.draft_id),
                "variant_id": draft.variant_id,
                "writer_model": draft.writer_model,
                "sample_index": draft.sample_index,
                "provider": draft.provider,
                "finish_reason": draft.finish_reason,
                "output_path": draft.output_path or draft_paths.get(draft.draft_id),
            }
            for draft in report.drafts
        ],
        "judgements": [
            {
                "draft_id": item.draft_id,
                "judge_model": item.judge_model,
                "scores": item.scores,
                "score_keys": sorted(_present_score_keys(item)),
                "winner_reason": item.winner_reason,
                "risk_notes": item.risk_notes,
            }
            for item in report.judgements
        ],
        "rankings": aggregate_rankings(report),
        "strategy_rankings": aggregate_strategy_rankings(report),
        "dimension_gaps": aggregate_dimension_gaps(report),
        "diagnosis": build_experiment_diagnosis(report),
    }


def draft_to_dict(draft: DraftResult, output_path: str | None = None) -> dict[str, Any]:
    return {
        "draft_id": draft.draft_id,
        "variant_id": draft.variant_id,
        "writer_model": draft.writer_model,
        "sample_index": draft.sample_index,
        "text": draft.text,
        "provider": draft.provider,
        "finish_reason": draft.finish_reason,
        "output_path": output_path or draft.output_path,
    }


def draft_from_dict(payload: Mapping[str, Any]) -> DraftResult:
    return DraftResult(
        draft_id=str(payload.get("draft_id") or ""),
        variant_id=str(payload.get("variant_id") or ""),
        writer_model=str(payload.get("writer_model") or ""),
        sample_index=int(payload.get("sample_index") or 1),
        text=str(payload.get("text") or ""),
        provider=_optional_str(payload.get("provider")),
        finish_reason=_optional_str(payload.get("finish_reason")),
        output_path=_optional_str(payload.get("output_path")),
    )


def judgement_to_dict(judgement: JudgeResult) -> dict[str, Any]:
    return {
        "draft_id": judgement.draft_id,
        "judge_model": judgement.judge_model,
        "scores": judgement.scores,
        "winner_reason": judgement.winner_reason,
        "risk_notes": judgement.risk_notes,
        "raw_text": judgement.raw_text,
    }


def judgement_from_dict(payload: Mapping[str, Any]) -> JudgeResult:
    return JudgeResult(
        draft_id=str(payload.get("draft_id") or ""),
        judge_model=str(payload.get("judge_model") or ""),
        scores={
            str(key): _float_score(value)
            for key, value in _mapping(payload.get("scores")).items()
        },
        winner_reason=str(payload.get("winner_reason") or ""),
        risk_notes=[str(item) for item in _list(payload.get("risk_notes"))],
        raw_text=str(payload.get("raw_text") or ""),
    )


def aggregate_rankings(report: ExperimentReport) -> list[dict[str, Any]]:
    draft_by_id = {item.draft_id: item for item in report.drafts}
    variant_by_id = {item.variant_id: item for item in report.variants}
    blind_labels = build_blind_label_by_draft(report)
    scores_by_draft: dict[str, list[float]] = {}
    for judgement in report.judgements:
        scores_by_draft.setdefault(judgement.draft_id, []).append(
            judgement.scores.get("overall", 0.0)
        )
    rows = []
    for draft_id, values in scores_by_draft.items():
        draft = draft_by_id.get(draft_id)
        if draft is None:
            continue
        variant = variant_by_id.get(draft.variant_id)
        if variant is None:
            continue
        rows.append(
            {
                "draft_id": draft_id,
                "blind_label": blind_labels.get(draft_id),
                "variant_id": draft.variant_id,
                "strategy_id": variant.strategy.strategy_id,
                "strategy_title": variant.strategy.title,
                "writer_model": draft.writer_model,
                "sample_index": draft.sample_index,
                "mean_overall": round(sum(values) / max(1, len(values)), 2),
                "judge_count": len(values),
            }
        )
    return sorted(rows, key=lambda item: item["mean_overall"], reverse=True)


def aggregate_strategy_rankings(report: ExperimentReport) -> list[dict[str, Any]]:
    draft_by_id = {item.draft_id: item for item in report.drafts}
    variant_by_id = {item.variant_id: item for item in report.variants}
    blind_labels = build_blind_label_by_draft(report)
    scores_by_strategy: dict[str, list[float]] = {}
    draft_count_by_strategy: dict[str, int] = {}
    writer_models_by_strategy: dict[str, set[str]] = {}
    blind_labels_by_strategy: dict[str, set[str]] = {}
    title_by_strategy: dict[str, str] = {}

    for draft in report.drafts:
        variant = variant_by_id.get(draft.variant_id)
        if variant is None:
            continue
        sid = variant.strategy.strategy_id
        draft_count_by_strategy[sid] = draft_count_by_strategy.get(sid, 0) + 1
        writer_models_by_strategy.setdefault(sid, set()).add(draft.writer_model)
        if draft.draft_id in blind_labels:
            blind_labels_by_strategy.setdefault(sid, set()).add(blind_labels[draft.draft_id])
        title_by_strategy[sid] = variant.strategy.title

    for judgement in report.judgements:
        draft = draft_by_id.get(judgement.draft_id)
        if draft is None:
            continue
        variant = variant_by_id.get(draft.variant_id)
        if variant is None:
            continue
        scores_by_strategy.setdefault(variant.strategy.strategy_id, []).append(
            judgement.scores.get("overall", 0.0)
        )

    rows = []
    for strategy_id, draft_count in draft_count_by_strategy.items():
        scores = scores_by_strategy.get(strategy_id, [])
        mean_overall = round(sum(scores) / max(1, len(scores)), 2) if scores else None
        rows.append(
            {
                "strategy_id": strategy_id,
                "strategy_title": title_by_strategy.get(strategy_id, strategy_id),
                "mean_overall": mean_overall,
                "draft_count": draft_count,
                "judge_score_count": len(scores),
                "blind_labels": sorted(
                    blind_labels_by_strategy.get(strategy_id, set()),
                    key=_blind_label_sort_key,
                ),
                "writer_models": sorted(writer_models_by_strategy.get(strategy_id, set())),
            }
        )
    return sorted(
        rows,
        key=lambda item: item["mean_overall"] if item["mean_overall"] is not None else -1.0,
        reverse=True,
    )


def aggregate_dimension_gaps(
    report: ExperimentReport,
    *,
    pass_bar: float = 7.5,
) -> list[dict[str, Any]]:
    blind_labels = build_blind_label_by_draft(report)
    values_by_key: dict[str, list[float]] = {key: [] for key in SCORE_KEYS}
    low_examples_by_key: dict[str, list[tuple[float, str]]] = {key: [] for key in SCORE_KEYS}
    for judgement in report.judgements:
        blind_label = blind_labels.get(judgement.draft_id, judgement.draft_id)
        present_keys = _present_score_keys(judgement)
        for key in SCORE_KEYS:
            if key not in present_keys:
                continue
            score = judgement.scores.get(key)
            if not isinstance(score, (int, float)):
                continue
            value = float(score)
            values_by_key[key].append(value)
            low_examples_by_key[key].append((value, blind_label))

    rows: list[dict[str, Any]] = []
    for key in SCORE_KEYS:
        values = values_by_key[key]
        guidance = SCORE_DIMENSION_GUIDANCE[key]
        mean_score = round(sum(values) / len(values), 2) if values else None
        low_examples = sorted(
            low_examples_by_key[key],
            key=lambda item: (item[0], _blind_label_sort_key(item[1])),
        )[:3]
        rows.append(
            {
                "dimension": key,
                "label": guidance["label"],
                "mean_score": mean_score,
                "judge_score_count": len(values),
                "status": _dimension_gap_status(mean_score, pass_bar),
                "lowest_blind_labels": [label for _score, label in low_examples],
                "prompt_probe": guidance["prompt_probe"],
                "outline_probe": guidance["outline_probe"],
            }
        )
    return sorted(
        rows,
        key=lambda item: item["mean_score"] if item["mean_score"] is not None else 99.0,
    )


def _present_score_keys(judgement: JudgeResult) -> set[str]:
    if judgement.raw_text:
        payload = _parse_json_object(judgement.raw_text)
        raw_scores = payload.get("scores")
        if isinstance(raw_scores, Mapping):
            return {str(key) for key in raw_scores}
    return set(judgement.scores)


def _dimension_gap_status(mean_score: float | None, pass_bar: float) -> str:
    if mean_score is None:
        return "no_scores"
    if mean_score >= pass_bar:
        return "passing"
    if mean_score >= pass_bar - 1.0:
        return "watch"
    return "gap"


def build_experiment_diagnosis(
    report: ExperimentReport,
    *,
    pass_bar: float = 7.5,
) -> dict[str, Any]:
    rankings = aggregate_strategy_rankings(report)
    dimension_gaps = aggregate_dimension_gaps(report, pass_bar=pass_bar)
    weakest_dimensions = [
        item
        for item in dimension_gaps
        if item["mean_score"] is not None and item["status"] in {"gap", "watch"}
    ][:3]
    if not rankings:
        return {
            "status": "no_judgements",
            "message": "尚无盲评结果；先人工横读或补跑 judge。",
            "pass_bar": pass_bar,
            "weakest_dimensions": [],
        }
    top = rankings[0]
    mean_overall = top.get("mean_overall")
    if not isinstance(mean_overall, (int, float)):
        return {
            "status": "no_judgements",
            "message": "已有正文，但尚无可聚合的盲评分。",
            "pass_bar": pass_bar,
            "weakest_dimensions": [],
        }
    if mean_overall >= pass_bar:
        return {
            "status": "strategy_signal_found",
            "message": "盲评均分超过阈值，可优先拆解该策略如何改写正文 prompt。",
            "pass_bar": pass_bar,
            "top_strategy": top,
            "weakest_dimensions": weakest_dimensions,
        }
    return {
        "status": "outline_or_prompt_gap",
        "message": (
            "最高策略仍未过阈值。下一步应反推：场景合同是否缺明确开篇钩子、"
            "爽点交付、代价、状态变化或章末悬念，而不是继续堆抽象方法论。"
        ),
        "pass_bar": pass_bar,
        "top_strategy": top,
        "weakest_dimensions": weakest_dimensions,
    }


def render_html_report(report: ExperimentReport) -> str:
    rankings = aggregate_rankings(report)
    strategy_rankings = aggregate_strategy_rankings(report)
    dimension_gaps = aggregate_dimension_gaps(report)
    diagnosis = build_experiment_diagnosis(report)
    methodology_audit = build_methodology_application_audit(report.case)
    blind_labels = build_blind_label_by_draft(report)
    variant_by_id = {item.variant_id: item for item in report.variants}
    judgements_by_draft: dict[str, list[JudgeResult]] = {}
    for judgement in report.judgements:
        judgements_by_draft.setdefault(judgement.draft_id, []).append(judgement)

    title = "正文提示词策略横评"
    project_title = report.case.project.get("title") or report.case.case_id
    dry_badge = " dry-run" if report.dry_run else ""
    resource_brief = escape(build_scene_resource_brief(report.case))
    cards = "\n".join(
        _render_draft_card(
            draft,
            variant_by_id[draft.variant_id],
            judgements_by_draft,
            blind_labels.get(draft.draft_id, ""),
        )
        for draft in report.drafts
        if draft.variant_id in variant_by_id
    )
    strategy_by_label = {
        blind_labels[draft.draft_id]: _strategy_reveal_payload(
            variant_by_id[draft.variant_id],
            draft,
        )
        for draft in report.drafts
        if draft.variant_id in variant_by_id and draft.draft_id in blind_labels
    }
    ranking_rows = "\n".join(
        "<tr>"
        f"<td>{idx}</td>"
        f"<td>{escape(str(row.get('blind_label') or ''))}</td>"
        f"<td>{escape(str(row['strategy_title']))}</td>"
        f"<td>{escape(str(row['writer_model']))}</td>"
        f"<td>{escape(str(row['mean_overall']))}</td>"
        f"<td>{escape(str(row['judge_count']))}</td>"
        "</tr>"
        for idx, row in enumerate(rankings, start=1)
    )
    strategy_rows = "\n".join(
        "<tr>"
        f"<td>{idx}</td>"
        f"<td>{escape(', '.join(row['blind_labels']))}</td>"
        f"<td>{escape(str(row['strategy_title']))}</td>"
        f"<td>{escape(str(row['strategy_id']))}</td>"
        f"<td>{escape(str(row['mean_overall']))}</td>"
        f"<td>{escape(str(row['draft_count']))}</td>"
        f"<td>{escape(', '.join(row['writer_models']))}</td>"
        "</tr>"
        for idx, row in enumerate(strategy_rankings, start=1)
    )
    dimension_rows = "\n".join(
        "<tr>"
        f"<td>{escape(str(row['label']))}</td>"
        f"<td>{escape(str(row['mean_score']))}</td>"
        f"<td>{escape(str(row['status']))}</td>"
        f"<td>{escape(', '.join(row['lowest_blind_labels']))}</td>"
        f"<td>{escape(str(row['prompt_probe']))}</td>"
        f"<td>{escape(str(row['outline_probe']))}</td>"
        "</tr>"
        for row in dimension_gaps
    )
    methodology_rows = "\n".join(
        "<tr>"
        f"<td>{escape(str(row['label']))}</td>"
        f"<td>{escape(str(row['status']))}</td>"
        f"<td>{escape('、'.join(row['concept_hits']) or '-')}</td>"
        f"<td>{escape('、'.join(row['operational_hits']) or '-')}</td>"
        f"<td>{escape(str(row['risk']))}</td>"
        "</tr>"
        for row in methodology_audit["findings"]
    )
    diagnosis_message = escape(str(diagnosis.get("message") or ""))
    methodology_summary = escape(str(methodology_audit.get("summary") or ""))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} - {escape(str(project_title))}</title>
  <style>
    :root {{
      color-scheme: light; --ink:#1f2328; --muted:#667085; --line:#d0d7de;
      --bg:#f6f8fa; --card:#fff;
    }}
    body {{
      margin:0;
      font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;
      color:var(--ink); background:var(--bg);
    }}
    header {{ padding:28px 32px 18px; background:#fff; border-bottom:1px solid var(--line); }}
    h1 {{ margin:0 0 8px; font-size:24px; }}
    h2 {{ margin:26px 0 10px; font-size:18px; }}
    main {{ max-width:1280px; margin:0 auto; padding:20px 24px 40px; }}
    .meta {{ color:var(--muted); font-size:13px; }}
    .badge {{
      display:inline-block; padding:2px 8px; border:1px solid var(--line);
      border-radius:6px; background:#fff; color:var(--muted);
    }}
    table {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid var(--line); }}
    th,td {{
      padding:8px 10px; border-bottom:1px solid var(--line);
      text-align:left; vertical-align:top;
    }}
    th {{ background:#eef2f6; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(360px,1fr)); gap:14px; }}
    article {{
      background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px;
    }}
    article h3 {{ margin:0 0 4px; font-size:16px; }}
    pre {{
      white-space:pre-wrap; word-wrap:break-word; background:#fbfbfc;
      border:1px solid var(--line); border-radius:6px; padding:12px;
      max-height:520px; overflow:auto;
    }}
    details {{ margin-top:10px; }}
    summary {{ cursor:pointer; color:#344054; }}
    .scores {{ color:#344054; font-size:13px; }}
    .review-panel {{
      position:sticky; top:0; z-index:2; background:#fff; border:1px solid var(--line);
      border-radius:8px; padding:12px; margin-bottom:16px;
    }}
    .review-actions {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }}
    .manual-reveal {{
      white-space:pre-wrap; margin-top:10px; padding:10px; border:1px solid var(--line);
      border-radius:6px; background:#fbfbfc; color:#344054; font-size:13px;
    }}
    .blind-tools {{
      display:flex; gap:8px; flex-wrap:wrap; margin:10px 0 14px;
    }}
    button {{
      border:1px solid var(--line); background:#fff; border-radius:6px;
      padding:5px 10px; cursor:pointer;
    }}
    button:hover {{ background:#f3f4f6; }}
    .pick {{ margin-top:8px; padding:8px; border:1px dashed var(--line); color:var(--muted); }}
    .pick label {{ margin-right:8px; white-space:nowrap; }}
    textarea {{
      width:100%; min-height:56px; margin-top:8px; box-sizing:border-box;
      border:1px solid var(--line); border-radius:6px; padding:8px; font:inherit;
    }}
    .hidden {{ display:none; }}
    body.compact pre {{ max-height:260px; }}
    body.compact .grid {{ grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); }}
    .selected-best {{ border-color:#2f6fed; box-shadow:0 0 0 2px rgba(47,111,237,.12); }}
  </style>
</head>
<body>
  <header>
    <h1>{escape(title)} <span class="badge">{escape(dry_badge.strip() or "live")}</span></h1>
    <div class="meta">
      项目：{escape(str(project_title))} | case：{escape(report.case.case_id)}
      | created：{escape(report.created_at)}
    </div>
    <div class="meta">源 trace：{escape(report.case.source_path)}</div>
  </header>
  <main>
    <section class="review-panel">
      <strong>人工判定</strong>
      <div class="meta" id="manualSummary">尚未选择最优方案。</div>
      <div class="manual-reveal" id="manualReveal">
        选择最优方案后，这里会显示对应的提示词策略设计和回灌建议。
      </div>
      <div class="review-actions">
        <button type="button" onclick="downloadManualSelection()">导出人工判定 JSON</button>
        <button type="button" onclick="clearManualSelection()">清空本地选择</button>
      </div>
    </section>
    <h2>场景资源摘要</h2>
    <pre>{resource_brief}</pre>
    <h2>原始 Prompt 方法论应用诊断</h2>
    <p class="meta">{methodology_summary}</p>
    <table>
      <thead>
        <tr>
          <th>方法论维度</th><th>状态</th><th>概念命中</th><th>执行约束命中</th><th>风险解释</th>
        </tr>
      </thead>
      <tbody>{methodology_rows}</tbody>
    </table>
    <h2>人工横读区</h2>
    <div class="meta">
      默认只展示盲读编号和正文；策略、模型、判官分数都折叠在揭示区，
      建议完成选择后再打开。
    </div>
    <div class="blind-tools">
      <button type="button" onclick="setTextOpen(true)">展开全部正文</button>
      <button type="button" onclick="setTextOpen(false)">收起全部正文</button>
      <button type="button" onclick="showUnselectedOnly()">只看未判定</button>
      <button type="button" onclick="showAllCards()">显示全部</button>
      <button type="button" onclick="toggleCompactMode()">紧凑模式</button>
    </div>
    <div class="grid">{cards}</div>
    <details>
      <summary>揭示盲评排序与策略映射</summary>
      <h2>策略排序</h2>
      <table>
        <thead>
          <tr>
            <th>#</th><th>盲读编号</th><th>策略</th><th>策略ID</th><th>Overall 均分</th>
            <th>稿件数</th><th>Writer</th>
          </tr>
        </thead>
        <tbody>{strategy_rows}</tbody>
      </table>
      <h2>实验诊断</h2>
      <p class="meta">{diagnosis_message}</p>
      <h2>维度缺口矩阵</h2>
      <table>
        <thead>
          <tr>
            <th>维度</th><th>均分</th><th>状态</th><th>低分方案</th>
            <th>Prompt 反推</th><th>大纲/细纲反推</th>
          </tr>
        </thead>
        <tbody>{dimension_rows}</tbody>
      </table>
      <h2>单稿排序</h2>
      <table>
        <thead>
          <tr>
            <th>#</th><th>盲读编号</th><th>策略</th><th>Writer</th>
            <th>Overall 均分</th><th>判官数</th>
          </tr>
        </thead>
        <tbody>{ranking_rows}</tbody>
      </table>
    </details>
  </main>
  <script>
    const STORAGE_KEY = "prosePromptArena:" + {json.dumps(report.case.case_id)};
    const STRATEGY_BY_LABEL = {json.dumps(strategy_by_label, ensure_ascii=False)};

    function loadManualState() {{
      try {{ return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{{}}"); }}
      catch (_err) {{ return {{}}; }}
    }}

    function saveManualState(state) {{
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    }}

    function onManualChange(label, field, value) {{
      const state = loadManualState();
      state[label] = state[label] || {{}};
      state[label][field] = value;
      saveManualState(state);
      renderManualState();
    }}

    function renderManualState() {{
      const state = loadManualState();
      const bestLabels = [];
      const counts = {{best: 0, useful: 0, reject: 0, unselected: 0, total: 0}};
      document.querySelectorAll("[data-blind-label]").forEach((card) => {{
        counts.total += 1;
        const label = card.dataset.blindLabel;
        const item = state[label] || {{}};
        const choice = item.choice || "";
        card.classList.toggle("selected-best", choice === "best");
        card.dataset.choice = choice;
        card.querySelectorAll("input[type=radio]").forEach((input) => {{
          input.checked = input.value === choice;
        }});
        const notes = card.querySelector("textarea");
        if (notes && document.activeElement !== notes) {{
          notes.value = item.notes || "";
        }}
        if (choice === "best") {{
          bestLabels.push(label);
          counts.best += 1;
        }} else if (choice === "useful") {{
          counts.useful += 1;
        }} else if (choice === "reject") {{
          counts.reject += 1;
        }} else {{
          counts.unselected += 1;
        }}
      }});
      document.getElementById("manualSummary").textContent = buildManualSummary(bestLabels, counts);
      document.getElementById("manualReveal").textContent =
        buildManualRevealText(bestLabels, counts);
    }}

    function buildManualSummary(bestLabels, counts) {{
      const progress = "已判定 " + (counts.total - counts.unselected) + "/" + counts.total
        + "；最优 " + counts.best + "，可取 " + counts.useful
        + "，淘汰 " + counts.reject + "，未判定 " + counts.unselected + "。";
      if (bestLabels.length) {{
        return progress + " 当前人工最优：方案 " + bestLabels.join("、");
      }}
      if (counts.total && counts.unselected === 0) {{
        return progress + " 本轮暂未选出最优，可按无赢家路径反推 prompt/大纲/细纲。";
      }}
      return progress + " 尚未选择最优方案。";
    }}

    function setTextOpen(open) {{
      document.querySelectorAll("details.prose-block").forEach((node) => {{
        node.open = open;
      }});
    }}

    function showUnselectedOnly() {{
      renderManualState();
      document.querySelectorAll("[data-blind-label]").forEach((card) => {{
        card.classList.toggle("hidden", !!card.dataset.choice);
      }});
    }}

    function showAllCards() {{
      document.querySelectorAll("[data-blind-label]").forEach((card) => {{
        card.classList.remove("hidden");
      }});
    }}

    function toggleCompactMode() {{
      document.body.classList.toggle("compact");
    }}

    function buildManualRevealText(bestLabels, counts) {{
      if (!bestLabels.length) {{
        if (counts && counts.total && counts.unselected === 0) {{
          return [
            "本轮暂未选出最优方案。",
            "导出 manual-selection.json 后运行 manual analysis，系统会把所有淘汰/可取理由反推为：",
            "1. production writer prompt 的候选补丁；",
            "2. 大纲/细纲需要补强的检查项；",
            "3. 下一轮 round2_outline_repair_* 策略草案。",
            "如果所有方案都标为淘汰，审计会把它视为有效的 no-winner 结论，而不是失败。"
          ].join("\\n");
        }}
        return "选择最优方案后，这里会显示对应的提示词策略设计和回灌建议。";
      }}
      return bestLabels.map((label) => {{
        const item = STRATEGY_BY_LABEL[label] || {{}};
        return [
          "方案 " + label + " / " + (item.strategy_title || ""),
          "策略ID：" + (item.strategy_id || ""),
          "Writer：" + (item.writer_model || ""),
          "验证假设：" + (item.hypothesis || ""),
          "执行要求：" + (item.instruction || ""),
          "诊断焦点：" + (item.diagnostic_focus || ""),
          "设计拆解：" + (item.design_summary || ""),
          "回灌建议：把这条执行要求作为 scene-specific hard requirement 注入生产 writer prompt；"
          + "如果仍不生效，回查细纲是否已经给出开篇钩子、爽点反馈、章末新问题和具体行动链。"
        ].join("\\n");
      }}).join("\\n\\n");
    }}

    function downloadManualSelection() {{
      const state = loadManualState();
      const payload = {{
        case_id: {json.dumps(report.case.case_id, ensure_ascii=False)},
        source_path: {json.dumps(report.case.source_path, ensure_ascii=False)},
        selections: state,
        exported_at: new Date().toISOString()
      }};
      const blob = new Blob([JSON.stringify(payload, null, 2)], {{type: "application/json"}});
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "manual-selection.json";
      anchor.click();
      URL.revokeObjectURL(url);
    }}

    function clearManualSelection() {{
      localStorage.removeItem(STORAGE_KEY);
      renderManualState();
    }}

    renderManualState();
  </script>
</body>
</html>
"""


def build_blind_label_by_draft(report: ExperimentReport) -> dict[str, str]:
    return build_blind_label_by_draft_ids(draft.draft_id for draft in report.drafts)


def build_blind_label_by_draft_ids(draft_ids: Iterable[str]) -> dict[str, str]:
    ordered = sorted(
        {draft_id for draft_id in draft_ids if draft_id},
        key=lambda draft_id: (_stable_blind_sort_key(draft_id), draft_id),
    )
    return {
        draft_id: _blind_label(index)
        for index, draft_id in enumerate(ordered, start=1)
    }


def build_public_blind_packet(
    *,
    packet_seed: str,
    candidates: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate a provenance-free review packet from its private label map."""

    labels = build_blind_label_by_draft_ids(
        f"{packet_seed}:{draft_id}" for draft_id in candidates
    )
    label_by_draft = {
        draft_id: labels[f"{packet_seed}:{draft_id}"] for draft_id in candidates
    }
    packet_identity = {
        "seed": packet_seed,
        "candidate_hashes": sorted(
            hashlib.sha256(text.encode("utf-8")).hexdigest()
            for text in candidates.values()
        ),
    }
    packet_id = hashlib.sha256(
        json.dumps(packet_identity, sort_keys=True).encode("utf-8")
    ).hexdigest()[:20]
    public_packet = {
        "schema_version": "blind-review-packet/v1",
        "packet_id": packet_id,
        "candidates": [
            {"label": label_by_draft[draft_id], "text": text}
            for draft_id, text in sorted(
                candidates.items(), key=lambda item: label_by_draft[item[0]]
            )
        ],
        "questions": [
            "overall",
            "hook",
            "character",
            "prose",
            "ai_flavor",
            "continue_reading",
            "confidence",
            "evidence",
        ],
    }
    private_mapping = {
        "warning": "private provenance map; never include in a review packet",
        "packet_id": packet_id,
        "labels": {
            label_by_draft[draft_id]: draft_id for draft_id in sorted(candidates)
        },
    }
    return public_packet, private_mapping


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _strategy_reveal_payload(
    variant: PromptVariant,
    draft: DraftResult,
) -> dict[str, Any]:
    return {
        "strategy_id": variant.strategy.strategy_id,
        "strategy_title": variant.strategy.title,
        "hypothesis": variant.strategy.hypothesis,
        "instruction": variant.strategy.instruction,
        "diagnostic_focus": variant.strategy.diagnostic_focus,
        "design_summary": _strategy_design_summary(variant.strategy),
        "writer_model": draft.writer_model,
        "sample_index": draft.sample_index,
        "draft_id": draft.draft_id,
        "variant_id": draft.variant_id,
    }


def _render_draft_card(
    draft: DraftResult,
    variant: PromptVariant,
    judgements_by_draft: Mapping[str, list[JudgeResult]],
    blind_label: str,
) -> str:
    judgements = judgements_by_draft.get(draft.draft_id, [])
    score_lines = []
    for item in judgements:
        score_lines.append(
            f"{escape(item.judge_model)} overall={item.scores.get('overall', 0):.1f} "
            f"hook={item.scores.get('opening_hook', 0):.1f} "
            f"shuangwen={item.scores.get('shuangwen_payoff', 0):.1f} "
            f"ending={item.scores.get('ending_hook', 0):.1f}"
        )
    reasons = "\n".join(
        f"- {escape(item.judge_model)}: {escape(item.winner_reason)}"
        for item in judgements
        if item.winner_reason
    )
    return f"""<article data-blind-label="{escape(blind_label)}">
  <h3>方案 {escape(blind_label)}</h3>
  <div class="meta">字数：{len(draft.text)} | sample {draft.sample_index}</div>
  <div class="pick">
    <label><input type="radio" name="choice-{escape(blind_label)}" value="best"
      onchange="onManualChange('{escape(blind_label)}','choice',this.value)"> 最优</label>
    <label><input type="radio" name="choice-{escape(blind_label)}" value="useful"
      onchange="onManualChange('{escape(blind_label)}','choice',this.value)"> 可取部分</label>
    <label><input type="radio" name="choice-{escape(blind_label)}" value="reject"
      onchange="onManualChange('{escape(blind_label)}','choice',this.value)"> 淘汰</label>
    <textarea placeholder="人工备注"
      oninput="onManualChange('{escape(blind_label)}','notes',this.value)"></textarea>
  </div>
  <details>
    <summary>揭示策略和模型</summary>
    <p>{escape(variant.strategy.title)} / {escape(variant.strategy.strategy_id)}</p>
    <p>Writer：{escape(draft.writer_model)} | sample {draft.sample_index}</p>
    <p>{escape(variant.strategy.hypothesis)}</p>
    <p>{escape(variant.strategy.instruction)}</p>
    <p>{escape(_strategy_design_summary(variant.strategy))}</p>
  </details>
  <details class="prose-block" open>
    <summary>正文</summary>
    <pre>{escape(draft.text)}</pre>
  </details>
  <details>
    <summary>揭示判官分数和理由</summary>
    <div class="scores">{'<br>'.join(score_lines)}</div>
    <pre>{reasons}</pre>
  </details>
</article>"""


def _strategy_design_summary(strategy: PromptStrategy) -> str:
    focus = strategy.diagnostic_focus
    focus_text = {
        "baseline": "控制组保留生产原样，用来判断新策略是否真的带来增益。",
        "opening": "把读者第一眼看到的异常、危险或欲望前置，测试开篇留存是否受 prompt 直接影响。",
        "retention": "用连续问题链维持追读，测试正文是否能边回答边制造下一层未解问题。",
        "shuangwen": "把爽点拆成压迫、选择、执行、反馈四拍，测试读者是否能感到回报落袋。",
        "suspense": "把悬疑拆成异常、误判、证据和反向验证，避免一次性解释答案。",
        "ending": "单独锁定最后一段的新问题或代价，测试平收是否可以被 prompt 修正。",
        "causality": "用目标、阻力、代价、状态变化校准场景因果，测试正文是否从摘要变成行动链。",
        "embodiment": "让人物先通过身体动作和微选择反应，测试是否能减少作者解释。",
        "grounding": "优先消耗具体物料、地点、道具和规则，测试去空泛化是否依赖素材落地。",
        "dialogue": "把对白从信息搬运改成关系争夺，测试潜台词和微动作能否提升人物张力。",
        "anti_exposition": "压低背景解释比例，测试信息能否通过冲突、交易和结果释放。",
        "pacing": "提高小冲突密度，测试平铺段落是否能被持续阻力改造成追读推进。",
        "reaction": "放大行动后的环境、对手和旁观反馈，测试爽点是否需要可见回声。",
        "stakes": "加入倒计时或不可逆代价，测试读者是否更清楚主角为什么不能等。",
        "imagery": "用一个招牌画面贯穿开中结，测试强视觉锚点能否组织松散场景。",
        "genre_voice": "强化目标类型的句式和规则感，测试通用 AI 腔是否能贴近品类阅读口味。",
        "texture": "强调对白潜台词、具体细节或招牌画面，测试正文质感来源。",
        "anti_ai": "直接约束总结、评价、套话和机械排比，测试 AI 味是否可被 prompt 降低。",
        "outline_probe": "故意反查细纲素材，测试瓶颈是在 prompt 还是在场景合同本身。",
        "methodology_application": "把抽象方法论翻译成场景动作，测试理论是否必须先转成可写 beat。",
        "prompt_budget": "减少提示词负载，只保留高信号硬约束，测试 prompt 过载是否稀释执行。",
        "minimal": "只保留少量高信号硬约束，测试是否比大段方法论更有效。",
    }.get(focus, "用单一可观察变量改写 prompt，方便横向比较哪类信息真正改变正文。")
    return (
        f"设计变量={focus}；{focus_text} "
        f"核心操作：{strategy.instruction}"
    )


def _strategy(
    strategy_id: str,
    title: str,
    hypothesis: str,
    instruction: str,
    diagnostic_focus: str,
) -> PromptStrategy:
    return PromptStrategy(
        strategy_id=strategy_id,
        title=title,
        hypothesis=hypothesis,
        instruction=instruction,
        diagnostic_focus=diagnostic_focus,
    )


def _strategy_to_dict(strategy: PromptStrategy) -> dict[str, str]:
    return {
        "strategy_id": strategy.strategy_id,
        "title": strategy.title,
        "hypothesis": strategy.hypothesis,
        "instruction": strategy.instruction,
        "diagnostic_focus": strategy.diagnostic_focus,
    }


def _brief_line(label: str, value: object) -> str:
    rendered = _brief_value(value)
    return f"- {label}：{rendered}" if rendered else ""


def _brief_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _clip(value.strip())
    if isinstance(value, list):
        parts = [_brief_value(item) for item in value]
        return _clip("；".join(item for item in parts if item))
    if isinstance(value, Mapping):
        parts = []
        for key, item in value.items():
            rendered = _brief_value(item)
            if rendered:
                parts.append(f"{key}={rendered}")
        return _clip("；".join(parts))
    return _clip(str(value).strip())


def _blind_label(index: int) -> str:
    if index < 1:
        return ""
    letters = []
    value = index
    while value:
        value -= 1
        letters.append(chr(ord("A") + (value % 26)))
        value //= 26
    return "".join(reversed(letters))


def _stable_blind_sort_key(draft_id: str) -> str:
    return hashlib.sha256(draft_id.encode("utf-8")).hexdigest()


def _blind_label_sort_key(label: str) -> tuple[int, str]:
    value = 0
    for ch in label:
        if not ("A" <= ch <= "Z"):
            return (10_000, label)
        value = value * 26 + (ord(ch) - ord("A") + 1)
    return (value, label)


def _chapter_label(chapter: Mapping[str, Any]) -> str:
    number = chapter.get("number")
    title = chapter.get("title")
    if number and title:
        return f"第{number}章《{title}》"
    if number:
        return f"第{number}章"
    return str(title or "")


def _scene_label(scene: Mapping[str, Any]) -> str:
    number = scene.get("number")
    scene_type = scene.get("type")
    title = scene.get("title")
    parts = [f"场景{number}" if number else "", str(scene_type or ""), str(title or "")]
    return " / ".join(item for item in parts if item)


def _clip(value: str, *, limit: int = 260) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _find_terms(text: str, terms: Iterable[str]) -> list[str]:
    lowered = text.lower()
    hits = []
    for term in terms:
        needle = str(term)
        if needle and needle.lower() in lowered:
            hits.append(needle)
    return hits


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _float_score(value: object) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(10.0, score))


def _parse_json_object(raw_text: str) -> Mapping[str, Any]:
    stripped = raw_text.strip()
    if not stripped:
        return {}
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            payload = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return payload if isinstance(payload, Mapping) else {}


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value).strip("-") or "unknown"
