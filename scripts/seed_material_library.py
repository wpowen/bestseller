#!/usr/bin/env python3
"""
Material Library Seed Script
给物料库批量填充初始种子数据，覆盖 10+ 题材 × 7 核心维度。

Usage:
    uv run python scripts/seed_material_library.py [--dry-run] [--genre GENRE]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bestseller.infra.db.session import session_scope
from bestseller.services.material_library import MaterialEntry, insert_entry

# ---------------------------------------------------------------------------
# Source citation helper
# ---------------------------------------------------------------------------

def wiki(title: str, url: str) -> dict:
    return {"url": url, "title": title, "accessed": "2026-04"}

def eval_src(text: str) -> dict:
    return {"text": f"evaluative source: {text}", "confidence": 0.65}

def llm_note(text: str) -> dict:
    return {"text": f"[LLM推演] {text}", "confidence": 0.55}

LLM_SYNTH = "llm_synth"
CONFIDENCE = 0.55


# ===========================================================================
# ██╗  ██╗██╗███████╗████████╗ ██████╗ ██████╗ ██╗   ██╗
# ██║  ██║██║██╔════╝╚══██╔══╝██╔═══██╗██╔══██╗╚██╗ ██╔╝
# ███████║██║███████╗   ██║   ██║   ██║██████╔╝ ╚████╔╝
# ██╔══██║██║╚════██║   ██║   ██║   ██║██╔══██╗  ╚██╔╝
# ██║  ██║██║███████║   ██║   ╚██████╔╝██║  ██║   ██║
# ===========================================================================

SEED_DATA: list[MaterialEntry] = []


# ---------------------------------------------------------------------------
# ── 历史/权谋 ──────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

SEED_DATA += [
    # world_settings
    MaterialEntry(
        dimension="world_settings",
        genre="历史",
        slug="hist-ws-fanzhen-empire",
        name="藩镇割据帝国",
        narrative_summary="唐末式的中央衰弱结构：皇权名存实亡，地方节度使各拥兵自重，朝堂是表演，真实权力在边疆军头手中",
        content_json={
            "geography_model": "中央盆地+四方藩镇，信息传递延迟三日以上导致政令失效",
            "power_vacuum": "皇帝无法直接调兵，依赖宦官与外戚互相制衡，任何一方坐大即乱",
            "civilizational_rules": "科举仍运转但出路堵死，进士得靠幕府投效而非朝廷任官",
            "unique_conflict_source": "节度使的继承权：父死子继 vs 朝廷任命，每次权力交接都是战争触发点",
        },
        source_type=LLM_SYNTH,
        source_citations=[
            wiki("唐朝藩镇", "https://zh.wikipedia.org/wiki/藩鎮割據"),
            llm_note("以唐末五代格局为原型的架空历史权谋世界观"),
        ],
        confidence=0.60,
        tags=["历史", "权谋", "藩镇", "唐末"],
    ),
    MaterialEntry(
        dimension="world_settings",
        genre="历史",
        slug="hist-ws-merchant-guild-state",
        name="商帮共治时代",
        narrative_summary="虚构南宋式商业共和：官府不直接管市，晋商徽商通过钱庄票号实质掌控地方财政，政治权力向资本倾斜",
        content_json={
            "geography_model": "江南水网城市群，运河控制权=政治控制权",
            "power_vacuum": "战争频繁导致朝廷财政崩溃，被迫以盐铁专卖权换取商帮资金",
            "civilizational_rules": "信用即身份，欠债不还比杀人更丢脸；商帮内部有自己的仲裁法庭",
            "unique_conflict_source": "两大商帮争夺唯一的跨境汇票网络，掌控者可让敌国的军饷路上消失",
        },
        source_type=LLM_SYNTH,
        source_citations=[
            wiki("晋商", "https://zh.wikipedia.org/wiki/晋商"),
            wiki("徽商", "https://zh.wikipedia.org/wiki/徽商"),
        ],
        confidence=0.65,
        tags=["历史", "商战", "权谋", "商帮"],
    ),
    MaterialEntry(
        dimension="world_settings",
        genre="历史",
        slug="hist-ws-eunuch-court",
        name="宦官专政宫廷",
        narrative_summary="明代魏忠贤式权力结构：皇帝懒政，宦官以内廷为根据地控制信息流，文官集团试图从外廷反制",
        content_json={
            "geography_model": "皇城内外两套政治系统，内廷（太监）vs外廷（文官），物理隔绝产生信息壁垒",
            "power_vacuum": "皇帝的个人喜好决定谁能进乾清宫，这成为最核心的权力资源",
            "civilizational_rules": "一切以圣旨为名，宦官伪造圣旨的成本极低；弹劾需要证据，构陷只需揣测",
            "unique_conflict_source": "东厂档案：所有人都有把柄在宦官手里，清洗能消灭证据，也能制造证据",
        },
        source_type=LLM_SYNTH,
        source_citations=[
            wiki("魏忠贤", "https://zh.wikipedia.org/wiki/魏忠贤"),
            wiki("明朝宦官", "https://zh.wikipedia.org/wiki/明朝宦官"),
        ],
        confidence=0.65,
        tags=["历史", "宦官", "宫廷", "权谋"],
    ),

    # character_archetypes
    MaterialEntry(
        dimension="character_archetypes",
        genre="历史",
        slug="hist-ca-tactician-retreat",
        name="以退为进型谋士",
        narrative_summary="表面辞官归隐实则积蓄资源，利用『不在局中』的身份规避风险同时布置全局，等敌人犯错再出手",
        content_json={
            "core_wound": "曾经忠心事主却被出卖，深知权谋本质是利益而非忠义",
            "external_goal": "扶持一个新主公完成政治目标（复仇/复国/变革）",
            "internal_need": "在死之前做一件真正有意义的事，证明谋略可以改变历史",
            "fatal_flaw": "过度控制，无法真正信任他人，将人视为棋子导致孤立",
            "typical_arc": "被动出山→主动入局→关键时刻选择牺牲→遗志由他人完成",
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("历史上的范蠡、张良等'功成身退'谋士原型分析")],
        confidence=0.60,
        tags=["历史", "谋士", "权谋"],
    ),
    MaterialEntry(
        dimension="character_archetypes",
        genre="历史",
        slug="hist-ca-female-merchant",
        name="女商人政治博弈者",
        narrative_summary="靠商业网络而非家族背景在男权朝堂周旋的女主角，商业关系是她的情报网和保命符",
        content_json={
            "core_wound": "父亲的商业帝国被权贵侵吞，官府是帮凶而非主持公道者",
            "external_goal": "重建商帮并让吞并她家产的权贵付出代价",
            "internal_need": "证明女性可以在规则不为她设计的游戏里赢",
            "fatal_flaw": "为了目标过度工具化人际关系，错过了真实的情感连接",
            "typical_arc": "被迫继业→学习规则→颠覆规则→代价是孤独",
        },
        source_type=LLM_SYNTH,
        source_citations=[wiki("中国古代女性", "https://zh.wikipedia.org/wiki/中国古代女性")],
        confidence=0.58,
        tags=["历史", "女主", "商战", "大女主"],
    ),

    # plot_patterns
    MaterialEntry(
        dimension="plot_patterns",
        genre="历史",
        slug="hist-pp-three-faction-balance",
        name="三方博弈破局模式",
        narrative_summary="皇权/权臣/外敌三足鼎立，主角通过激化其中两方冲突为第三方渔翁，最终在混乱中完成真实目标",
        content_json={
            "trigger": "某一方突然获得压倒性优势，打破原有均衡",
            "escalation_logic": "主角用信息差让两个强者互相消耗，同时积蓄自己的资源",
            "midpoint_reversal": "主角发现自己也被当成棋子，第四方势力才是真正的幕后",
            "resolution_type": "非全赢，所有方都有所失有所得，主角用最小代价实现核心目标",
            "subplots": ["情感线：两个对立阵营的人相爱", "身世线：主角身份与某方势力有关联"],
        },
        source_type=LLM_SYNTH,
        source_citations=[wiki("战国策", "https://ctext.org/zhan-guo-ce/zh")],
        confidence=0.62,
        tags=["历史", "权谋", "博弈"],
    ),
    MaterialEntry(
        dimension="plot_patterns",
        genre="历史",
        slug="hist-pp-info-asymmetry",
        name="信息差决胜叙事",
        narrative_summary="主角因掌握关键情报而非战力获得优势，核心冲突是信息的传递、隐藏、伪造和误读",
        content_json={
            "trigger": "主角意外获知某个本不该知道的秘密",
            "escalation_logic": "每一步行动都必须在不暴露自己知道什么的前提下进行",
            "midpoint_reversal": "主角发现对方也知道他知道，双方进入互相佯装不知的博弈",
            "resolution_type": "谁先打破沉默谁输，最终通过创造新事实迫使对方摊牌",
            "subplots": ["间谍线：双方都有对方的内线", "真相线：最初的秘密其实是假的"],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("孙子兵法情报战思想在权谋小说中的叙事转化")],
        confidence=0.62,
        tags=["历史", "权谋", "信息战"],
    ),

    # thematic_motifs
    MaterialEntry(
        dimension="thematic_motifs",
        genre="历史",
        slug="hist-tm-loyalty-dilemma",
        name="忠义两难",
        narrative_summary="忠于君主 vs 忠于道义，中国历史叙事的核心张力，伴生于每个权谋主角的终极抉择时刻",
        content_json={
            "symbol": "跪与不跪——下跪代表臣服，拒跪代表独立人格",
            "cultural_origin": "儒家忠君思想 vs 孟子民贵君轻，两者同时存在于历史人物行为逻辑中",
            "narrative_functions": [
                "驱动关键抉择：帮昏君还是背叛他",
                "制造人物悲剧：忠义两者不可兼得时的代价",
                "读者反思：现代语境下什么是'忠'",
            ],
            "variations": ["忠于集体vs忠于个人", "忠于制度vs忠于人", "父命vs君命"],
        },
        source_type=LLM_SYNTH,
        source_citations=[wiki("忠", "https://zh.wikipedia.org/wiki/忠")],
        confidence=0.65,
        tags=["历史", "母题", "忠义"],
    ),

    # emotion_arcs
    MaterialEntry(
        dimension="emotion_arcs",
        genre="历史",
        slug="hist-ea-idealist-fall",
        name="理想主义者的权谋蜕变",
        narrative_summary="从相信体制可以改变到被体制改变，主角在一次次妥协中失去初心，高光时刻反而是最孤独的时候",
        content_json={
            "arc_name": "入局→适应→工具化→代价认知→晚期悲悯",
            "stages": [
                "初入：有理想，相信规则可以被善用",
                "适应：发现必须妥协才能生存",
                "蜕变：主动使用权谋，但告诉自己是为了更大善",
                "代价：失去一个真正在乎的人或信念",
                "终局：成功了，但不再是当初的自己",
            ],
            "turning_point_trigger": "第一次为了目标主动牺牲无辜者",
            "reader_investment_mechanism": "读者认同初始的理想主义，因此每一次妥协都是情感消耗",
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("历史权谋题材中主角弧线设计的常见模式分析")],
        confidence=0.60,
        tags=["历史", "情感弧", "权谋"],
    ),

    # anti_cliche_patterns
    MaterialEntry(
        dimension="anti_cliche_patterns",
        genre="历史",
        slug="hist-ac-no-modern-cheat",
        name="穿越者知识万能陷阱",
        narrative_summary="主角靠现代知识无阻力碾压古代人是最大的叙事毒药，真实历史知识有时比无知更危险",
        content_json={
            "cliche_description": "穿越者用现代化学/物理/医学/商业知识无限辗转古代人",
            "why_it_fails": "消灭了本该有的历史阻力，让读者失去'他是怎么做到的'的期待；并且古代人不是蠢的，他们有自己时代的经验智慧",
            "alternative_approach": "给穿越者设置知识盲区：历史课学了但不记得细节；当地方言不会；古代政治潜规则完全不懂",
            "examples": ["主角用青霉素救人但不知道青霉菌从哪里提取", "知道某年会发生什么但不记得具体月份"],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("穿越历史小说叙事陷阱分析")],
        confidence=0.62,
        tags=["历史", "反套路", "穿越"],
    ),
]


# ---------------------------------------------------------------------------
# ── 悬疑/推理 ──────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

SEED_DATA += [
    MaterialEntry(
        dimension="world_settings",
        genre="悬疑",
        slug="susp-ws-closed-village",
        name="南方封闭村落",
        narrative_summary="外来者进不来也出不去的宗族村庄，地方志规则和血统传说构成隐形法律，祠堂是一切秘密的储存器",
        content_json={
            "geography_model": "四面山水环绕的孤立村落，只有一条路可进出且春季必然发洪水封路",
            "power_vacuum": "村长权威源于族谱，外来法律进不来，内部争端靠'族规'解决",
            "civilizational_rules": "祖宗定下的禁忌（不能在XX地方建房/不能嫁给XX姓）有实际后果",
            "unique_conflict_source": "外来人/混血/改嫁者的存在挑战血统纯洁论，而村落的秘密往往就隐藏在这些边缘人身上",
        },
        source_type=LLM_SYNTH,
        source_citations=[
            wiki("中国民间信仰", "https://zh.wikipedia.org/wiki/中国民间信仰"),
            eval_src("南方宗族村落作为悬疑叙事封闭空间的分析"),
        ],
        confidence=0.62,
        tags=["悬疑", "民俗", "封闭空间"],
    ),
    MaterialEntry(
        dimension="world_settings",
        genre="悬疑",
        slug="susp-ws-unreliable-memory",
        name="记忆不可信世界观",
        narrative_summary="主角的记忆本身是谜题的一部分：失忆/创伤记忆/他人植入的假记忆，读者和主角同时不知道真相是什么",
        content_json={
            "geography_model": "叙事空间随记忆碎片化，同一地点在不同记忆片段里呈现不同样子",
            "power_vacuum": "当记忆是武器，掌握某人记忆的人掌握其命运",
            "civilizational_rules": "没有客观证据，只有各方叙述，读者必须自己拼图",
            "unique_conflict_source": "主角记忆恢复的每一片段都重新改写已有的理解",
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("不可靠叙述者在心理悬疑中的叙事技法")],
        confidence=0.58,
        tags=["悬疑", "心理", "不可靠叙述者"],
    ),

    MaterialEntry(
        dimension="character_archetypes",
        genre="悬疑",
        slug="susp-ca-reluctant-detective",
        name="被迫介入的普通人",
        narrative_summary="没有任何侦探技能，但因为独特处境只有他能解开谜团——朋友、工作、意外目击让他无法置身事外",
        content_json={
            "core_wound": "曾经逃避过一次责任，留下阴影，这次不能再逃",
            "external_goal": "找到真相，保护一个人（自己/他人/记忆中的人）",
            "internal_need": "证明自己的判断力，不再被人认为是懦弱或无能",
            "fatal_flaw": "执念让他无法客观：确信了某个结论后会忽视反驳证据",
            "typical_arc": "意外卷入→否认→被迫行动→危险升级→关键突破→代价",
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("推理小说主角类型分析：非专业侦探视角的优势与设计要点")],
        confidence=0.60,
        tags=["悬疑", "侦探", "普通人"],
    ),
    MaterialEntry(
        dimension="character_archetypes",
        genre="悬疑",
        slug="susp-ca-social-root-villain",
        name="社会根因凶手",
        narrative_summary="动机来自系统性压迫而非天生恶意，凶手是个体，但是整个社会结构的产物，让读者无法简单谴责",
        content_json={
            "core_wound": "被不公正的制度/关系彻底摧毁后，没有任何合法出路",
            "external_goal": "让特定的人/制度付出代价",
            "internal_need": "被看见，被承认所受的伤是真实的",
            "fatal_flaw": "将个人仇恨扩大到不相关的人",
            "typical_arc": "受伤→求助失败→走投无路→极端行为→揭露时读者理解但不认同",
        },
        source_type=LLM_SYNTH,
        source_citations=[
            wiki("犯罪学", "https://zh.wikipedia.org/wiki/犯罪学"),
            eval_src("社会派推理中凶手动机设计的叙事伦理"),
        ],
        confidence=0.62,
        tags=["悬疑", "反派", "社会派推理"],
    ),

    MaterialEntry(
        dimension="plot_patterns",
        genre="悬疑",
        slug="susp-pp-three-layer-mystery",
        name="三层谜题结构",
        narrative_summary="表层谜（谁做的）→中层谜（为什么做）→底层谜（什么是真正的犯罪），每层揭示都颠覆对前层的理解",
        content_json={
            "trigger": "一个看似普通的事件，有一个细节明显不对",
            "escalation_logic": "主角每解一层，发现下一层更黑暗，且已有的结论需要修正",
            "midpoint_reversal": "中层揭示改变了读者对凶手的判断（原本以为是坏人的是受害者）",
            "resolution_type": "底层真相揭示后，凶手不再是单一个体，而是某种结构性力量",
            "subplots": ["副线案件与主线在底层同源", "调查过程中主角自己的秘密被危及"],
        },
        source_type=LLM_SYNTH,
        source_citations=[wiki("本格推理", "https://zh.wikipedia.org/wiki/本格推理")],
        confidence=0.65,
        tags=["悬疑", "推理", "叙事结构"],
    ),

    MaterialEntry(
        dimension="thematic_motifs",
        genre="悬疑",
        slug="susp-tm-truth-memory",
        name="真相与记忆",
        narrative_summary="真相是客观的，记忆是主观的；悬疑叙事的核心母题是当两者冲突时谁更可信，以及为什么人类宁愿相信舒适的谎言",
        content_json={
            "symbol": "照片/录音——证据的客观性 vs 可被伪造性",
            "cultural_origin": "记忆研究（Elizabeth Loftus虚假记忆实验）：证人记忆在暗示下会改变",
            "narrative_functions": [
                "驱动主角调查：他记得的和发生的不一样",
                "读者参与：读者和主角同时拼图，各自解读",
                "道德复杂性：知道真相是否总是更好",
            ],
            "variations": ["集体记忆被权力塑造", "创伤导致的选择性遗忘", "谎言维持的共识现实"],
        },
        source_type=LLM_SYNTH,
        source_citations=[wiki("记忆", "https://zh.wikipedia.org/wiki/記憶")],
        confidence=0.62,
        tags=["悬疑", "母题", "记忆"],
    ),

    MaterialEntry(
        dimension="scene_templates",
        genre="悬疑",
        slug="susp-st-interrogation",
        name="信息博弈审讯场景",
        narrative_summary="审讯不是逼问，是双方都在撒谎、都在试探的棋局，每句话都有情报目的，沉默比开口更危险",
        content_json={
            "scene_type": "信息博弈",
            "entry_condition": "侦探掌握部分证据但不够，嫌疑人知道被怀疑",
            "tension_source": "双方都在假装不知道对方知道什么",
            "exit_hook": "审讯结束时有一个细节让读者知道有一方说谎，但不确定是哪一方",
            "variations": [
                "嫌疑人反向审讯侦探",
                "第三者在旁边导致双方无法明说",
                "审讯中途真正的凶手打来电话",
            ],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("侦探小说审讯场景设计技法分析")],
        confidence=0.62,
        tags=["悬疑", "场景", "审讯"],
    ),

    MaterialEntry(
        dimension="anti_cliche_patterns",
        genre="悬疑",
        slug="susp-ac-no-deus-ex-clue",
        name="突然出现关键证据陷阱",
        narrative_summary="结局靠主角突然想起一个之前没提到的线索解决一切，是推理作品最大的诚信危机",
        content_json={
            "cliche_description": "主角最后灵光一现想起关键证据，但读者之前根本没有机会看到",
            "why_it_fails": "违反推理叙事的公平性原则——读者理论上应该有和侦探相同的信息",
            "alternative_approach": "所有关键线索必须在揭示前出现过，但要用障眼法掩盖其重要性",
            "examples": ["主角早就看到过凶器但以为是装饰品", "被害者说的一句话后来被证明是暗语"],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("本格推理公平性原则：诺克斯十诫和范达因二十法则的叙事意义")],
        confidence=0.65,
        tags=["悬疑", "反套路", "推理"],
    ),
]


# ---------------------------------------------------------------------------
# ── 末日/废土 ─────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

SEED_DATA += [
    MaterialEntry(
        dimension="world_settings",
        genre="末日",
        slug="apoc-ws-city-ruin-ecology",
        name="城市废墟生态",
        narrative_summary="末日后城市不是简单的废墟而是一个新生态：植物侵入建筑、动物重新分区、人类聚居点围绕现有基础设施而非重建",
        content_json={
            "geography_model": "按资源分布而非行政区划重组的城市区域：水源处/发电设施/食物产地为核心节点",
            "power_vacuum": "物理基础设施的控制权=政治权力；谁掌握净水站谁统治方圆十公里",
            "civilizational_rules": "物资储量而非货币是身份的标志；囤积者被尊重，消费者被轻视",
            "unique_conflict_source": "前世界遗留物：武器库/医药仓/网络中心，发现者拥有毁灭性优势",
        },
        source_type=LLM_SYNTH,
        source_citations=[wiki("后启示录", "https://zh.wikipedia.org/wiki/后启示录")],
        confidence=0.60,
        tags=["末日", "废土", "世界观"],
    ),
    MaterialEntry(
        dimension="world_settings",
        genre="末日",
        slug="apoc-ws-tiered-survivor-society",
        name="末日阶层社会",
        narrative_summary="觉醒者与普通人之间形成新阶级制度，但觉醒者内部也有职业分工和地位差异，治愈者地位高于战士",
        content_json={
            "geography_model": "基地以同心圆结构：核心区（觉醒者精英）→中圈（普通觉醒者+技术人才）→外圈（普通人）",
            "power_vacuum": "觉醒早期谁强谁说话，中期开始制度化，规则制定者往往是第一批觉醒的人",
            "civilizational_rules": "觉醒等级公示制度，日常物资分配与等级挂钩",
            "unique_conflict_source": "等级流动性问题：普通人的孩子觉醒后是否自动进入觉醒阶级",
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("末日文阶级设定分析：觉醒者社会结构的常见模式")],
        confidence=0.58,
        tags=["末日", "觉醒", "社会结构"],
    ),

    MaterialEntry(
        dimension="character_archetypes",
        genre="末日",
        slug="apoc-ca-resource-hoarder-villain",
        name="资源控制型反派",
        narrative_summary="不靠武力而靠垄断稀缺资源统治的末日势力领袖，让主角面临的不是打打杀杀而是经济困境",
        content_json={
            "core_wound": "末日前是被社会淘汰的边缘人，末日给了他报复规则的机会",
            "external_goal": "建立一个完全由他控制的秩序，再也不被抛弃",
            "internal_need": "被需要，被承认，用权力填补自我价值的空缺",
            "fatal_flaw": "将所有外部威胁解读为针对自己的阴谋，偏执导致内部清洗",
            "typical_arc": "末日初期从无到有→控制核心资源→建立制度→偏执升级→崩溃从内部开始",
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("末日题材反派动机设计：资源控制者的心理模型")],
        confidence=0.60,
        tags=["末日", "反派", "资源"],
    ),

    MaterialEntry(
        dimension="plot_patterns",
        genre="末日",
        slug="apoc-pp-supply-crisis",
        name="物资危机驱动叙事",
        narrative_summary="不靠怪物攻击而靠资源枯竭制造主线张力：粮食/药品/燃料某一项告急，所有人的行为在压力下变形",
        content_json={
            "trigger": "基地核心物资只剩X天，常规补给路线被切断",
            "escalation_logic": "物资减少→内部分配矛盾→派系出现→试图抢夺其他幸存者→道德底线被侵蚀",
            "midpoint_reversal": "发现一个充足的物资点，但控制者提出无法接受的条件",
            "resolution_type": "非零和：通过合作而非征服解决，但付出了关系/道德代价",
            "subplots": ["派系内部清洗", "外部威胁趁虚而入"],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("末日文情节分析：非战斗类冲突驱动的叙事结构")],
        confidence=0.62,
        tags=["末日", "情节", "物资"],
    ),

    MaterialEntry(
        dimension="thematic_motifs",
        genre="末日",
        slug="apoc-tm-humanity-under-pressure",
        name="极限压力下的人性",
        narrative_summary="末日叙事的核心不是怪物，是人类在资源稀缺时会做什么；末日是放大镜，放大平时被文明压制的人性",
        content_json={
            "symbol": "食物——分享食物是文明，独占食物是原始",
            "cultural_origin": "社会契约论：文明是协议，当协议无法维持时人类如何选择",
            "narrative_functions": [
                "测试主角：他会为了生存做什么",
                "揭示社会：末日前的不平等在末日后被放大",
                "读者反思：我处于同样境况会怎么做",
            ],
            "variations": ["道德妥协的滑坡", "小善举在绝望中的异常分量", "集体牺牲 vs 个人生存"],
        },
        source_type=LLM_SYNTH,
        source_citations=[wiki("社会契约", "https://zh.wikipedia.org/wiki/社会契约")],
        confidence=0.63,
        tags=["末日", "母题", "人性"],
    ),

    MaterialEntry(
        dimension="anti_cliche_patterns",
        genre="末日",
        slug="apoc-ac-realistic-psychology",
        name="末日心理失真问题",
        narrative_summary="主角在末日里始终保持现代人的道德观和心理健康，没有PTSD、没有道德动摇，是最大的失真",
        content_json={
            "cliche_description": "主角目睹大量死亡和暴力，心理完全不受影响，继续以现代道德标准行事",
            "why_it_fails": "失去了末日世界的重量感；读者无法代入一个不会被环境改变的人",
            "alternative_approach": "给主角设计渐进式心理变化：起初坚守底线，慢慢发现自己已经做了以前认为不可能做的事",
            "examples": ["主角第一次杀人和第十次杀人的心理描写要完全不同", "主角对某种暴力从震惊到麻木"],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("灾难心理学：创伤后应激障碍在末日叙事中的真实呈现")],
        confidence=0.63,
        tags=["末日", "反套路", "心理"],
    ),
]


# ---------------------------------------------------------------------------
# ── 都市异能 ──────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

SEED_DATA += [
    MaterialEntry(
        dimension="world_settings",
        genre="都市",
        slug="urb-ws-dual-society",
        name="双层都市社会",
        narrative_summary="普通都市之下运行着另一套规则体系：异能者公会、秘密裁判所、地下交易市场——两层社会在特定地点交叠",
        content_json={
            "geography_model": "城市地标（地铁站/老旧建筑/医院地下室）作为双层社会的交叠点",
            "power_vacuum": "异能者社会的法律不被普通政府承认，也无法向普通人求救",
            "civilizational_rules": "暴露异能是违规的，但验证对方也是异能者不需要暴露自己",
            "unique_conflict_source": "双重身份管理：异能者在普通社会必须伪装，这本身就是长期心理压力",
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("都市异能文世界观设计分析：双重社会结构的叙事优势")],
        confidence=0.60,
        tags=["都市", "异能", "双层社会"],
    ),

    MaterialEntry(
        dimension="character_archetypes",
        genre="都市",
        slug="urb-ca-reluctant-hero",
        name="被迫参与型都市主角",
        narrative_summary="本来只想普通过活，异能被激活是灾难而非礼物，卷入秘密组织是事故而非选择，抗拒到无法抗拒",
        content_json={
            "core_wound": "上一次相信别人/组织被彻底背叛，从此只信自己",
            "external_goal": "搞清楚发生了什么然后全身而退",
            "internal_need": "发现真正在乎的东西，不能再假装什么都无所谓",
            "fatal_flaw": "孤立主义——拒绝建立真实的人际关系，但这恰恰是他们的弱点",
            "typical_arc": "被迫接触→拒绝参与→被卷进去→无法独力面对→接受伙伴→代价",
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("都市异能主角类型分析：非天选之人的叙事优势")],
        confidence=0.60,
        tags=["都市", "异能", "主角原型"],
    ),

    MaterialEntry(
        dimension="plot_patterns",
        genre="都市",
        slug="urb-pp-identity-exposure",
        name="身份暴露危机叙事",
        narrative_summary="主角的异能身份被错误的人知道了——这条信息扩散的过程就是整个情节的主轴，反应比行动更重要",
        content_json={
            "trigger": "某次意外使用异能留下了无法解释的痕迹",
            "escalation_logic": "知情者数量增加，每个知情者都有各自的利益考量（保护/利用/举报）",
            "midpoint_reversal": "主角以为的最大威胁其实是保护者，真正的威胁来自内部",
            "resolution_type": "无法完全消除风险，只能管控——建立新的信任关系代替彻底封锁",
            "subplots": ["职场/家庭关系因暴露而质变", "被迫协助某个组织以换取保密"],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("都市异能题材身份保密叙事：信息扩散驱动情节的设计模式")],
        confidence=0.62,
        tags=["都市", "异能", "身份危机"],
    ),

    MaterialEntry(
        dimension="anti_cliche_patterns",
        genre="都市",
        slug="urb-ac-no-free-power",
        name="无代价异能的叙事空洞",
        narrative_summary="都市异能如果没有真实代价，就失去了'为什么主角不早就解决一切'的合理性",
        content_json={
            "cliche_description": "主角异能强大且使用没有任何代价，随时可用，随意升级",
            "why_it_fails": "破坏张力：主角随时可以用异能为什么有些事他不做？只能靠作者故意不让他用",
            "alternative_approach": "异能代价与主角在乎的东西挂钩：用异能会损伤记忆/情感/寿命/与某人的关系",
            "examples": ["每次使用异能失去一段记忆", "用异能后无法感受特定情绪（爱/恐惧/痛苦）"],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("超能力代价设计的叙事功能分析")],
        confidence=0.65,
        tags=["都市", "反套路", "异能"],
    ),
]


# ---------------------------------------------------------------------------
# ── 宫斗/大女主 ──────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

SEED_DATA += [
    MaterialEntry(
        dimension="world_settings",
        genre="宫斗",
        slug="pal-ws-power-tools",
        name="后宫权力工具体系",
        narrative_summary="后宫不是爱情场而是政治场：子嗣/家族背景/宫廷人脉/信息控制/皇帝情感是五种权力工具，每种有独特成本",
        content_json={
            "geography_model": "宫殿物理布局即权力结构：离正殿近的宫殿 = 受宠程度；冷宫的物理条件=政治待遇",
            "power_vacuum": "皇帝一旦驾崩，所有权力工具同时失效；唯一延续性资产是子嗣",
            "civilizational_rules": "表面礼仪是必须维持的，破坏礼仪者先失去道义制高点",
            "unique_conflict_source": "宫女/太监的忠诚度：她们同时服务多人，谁的情报更多取决于谁给的利益更实",
        },
        source_type=LLM_SYNTH,
        source_citations=[
            wiki("后宫", "https://zh.wikipedia.org/wiki/後宮"),
            wiki("武则天", "https://zh.wikipedia.org/wiki/武則天"),
        ],
        confidence=0.65,
        tags=["宫斗", "后宫", "权力"],
    ),

    MaterialEntry(
        dimension="character_archetypes",
        genre="宫斗",
        slug="pal-ca-strategic-empress",
        name="主动谋略型女主",
        narrative_summary="不等皇帝施恩，主动经营人脉构建情报网，每次出手都有三步推演，失败也在计划内",
        content_json={
            "core_wound": "最信任的人出卖了她/家族因被背叛而覆灭，深知情感是最大的弱点",
            "external_goal": "在后宫建立不依赖皇帝宠爱的独立权力基础",
            "internal_need": "在不能表达情感的地方找到一个真实关系",
            "fatal_flaw": "过度预判他人动机，当有人真诚对她时反而怀疑是陷阱",
            "typical_arc": "入宫弱势→布局积累→重要盟友牺牲→以代价赢得关键战役",
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("宫斗剧/小说女主角类型分析：主动谋略者 vs 被动宠妃的叙事差异")],
        confidence=0.62,
        tags=["宫斗", "大女主", "谋略"],
    ),

    MaterialEntry(
        dimension="plot_patterns",
        genre="宫斗",
        slug="pal-pp-three-step-strategy",
        name="宫廷谋略三步推演",
        narrative_summary="每个宫廷计谋必须展示完整的逻辑链：初始状态→行动A→对方反应B→主角利用B完成目标，中间不允许跳步",
        content_json={
            "trigger": "对手有了一个新的优势或盟友，需要化解",
            "escalation_logic": "第一步制造疑心→第二步提供合理解释（实为误导）→第三步利用对方行动的后果",
            "midpoint_reversal": "对手的反制行动出乎意料，主角的计划A失败但触发了更好的计划B",
            "resolution_type": "高光时刻必须是主角的主动决策，而非运气或外力干预",
            "subplots": ["皇帝的政治需求有时和后宫稳定冲突，被利用", "某个旁观者知道了全部计谋"],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("宫斗叙事中谋略设计的逻辑完整性分析")],
        confidence=0.63,
        tags=["宫斗", "情节", "谋略"],
    ),

    MaterialEntry(
        dimension="thematic_motifs",
        genre="宫斗",
        slug="pal-tm-power-loneliness",
        name="权力的孤独",
        narrative_summary="越强大越孤立——后宫/政治里的孤独不是没有人陪，而是没有人能够真正知道你，这是大女主题材最深刻的代价",
        content_json={
            "symbol": "深宫的夜晚——宫殿越大越冷，权力越大越孤独",
            "cultural_origin": "《红楼梦》贾元春省亲场景：荣华富贵背后的骨肉分离之痛",
            "narrative_functions": [
                "揭示权力的代价",
                "创造情感节点：主角的防御在唯一真实关系前崩溃",
                "读者认同：成功的代价是孤独，这是人类共同经验",
            ],
            "variations": ["情感被迫工具化的代价", "不能向任何人示弱的疲惫", "没有人知道真实的她"],
        },
        source_type=LLM_SYNTH,
        source_citations=[wiki("红楼梦", "https://zh.wikipedia.org/wiki/红楼梦")],
        confidence=0.65,
        tags=["宫斗", "母题", "孤独"],
    ),
]


# ---------------------------------------------------------------------------
# ── 言情/女频 ─────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

SEED_DATA += [
    MaterialEntry(
        dimension="character_archetypes",
        genre="言情",
        slug="rom-ca-self-reliant-female",
        name="自立女主",
        narrative_summary="有明确的事业目标和自我判断，不因男主的出现改变人生方向，爱情是生活的一部分而非全部",
        content_json={
            "core_wound": "被人否定过能力/价值，深度绑定了'被需要'与自我价值",
            "external_goal": "在自己的领域做到顶端，证明当初否定她的人是错的",
            "internal_need": "被看见真实的自己，而不是某个角色（女儿/下属/女朋友）",
            "fatal_flaw": "过度独立成为障碍：拒绝依赖任何人，在应该求助时硬撑",
            "typical_arc": "独立奋斗→遇见男主（他不阻止也不代替她的成长）→情感建立→共同面对而非被拯救",
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("女频言情主角类型分析：自立女主的叙事优势与设计要点")],
        confidence=0.60,
        tags=["言情", "女频", "主角"],
    ),

    MaterialEntry(
        dimension="emotion_arcs",
        genre="言情",
        slug="rom-ea-slow-burn",
        name="慢热情感积累弧",
        narrative_summary="情感不靠一见钟情，靠接触次数的积累与每次接触中对方出乎意料的一面，转折点是'我什么时候开始在意的'",
        content_json={
            "arc_name": "抗拒→接触→意外共鸣→开始在意→否认→承认",
            "stages": [
                "1-初期：有理由不喜欢对方（误会/对立/竞争）",
                "2-接触：因为外部压力必须合作",
                "3-共鸣：发现一个完全预期外的共同点",
                "4-在意：开始注意对方的状态",
                "5-否认：用合理化解释为什么不是喜欢",
                "6-承认：某个关键时刻无法再自欺欺人",
            ],
            "turning_point_trigger": "对方在不知有人看见的情况下做了某件事，完全颠覆主角的判断",
            "reader_investment_mechanism": "读者比主角更早知道她喜欢了，形成'快承认啊'的期待张力",
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("言情情感弧线设计：慢热CP的节奏控制与读者投入机制")],
        confidence=0.63,
        tags=["言情", "情感弧", "慢热"],
    ),

    MaterialEntry(
        dimension="plot_patterns",
        genre="言情",
        slug="rom-pp-misunderstanding-resolved-by-action",
        name="行动化解误会模式",
        narrative_summary="误会不靠说清楚而靠行动证明，最终解决误会的场景是对方用行动而非语言展示真实意图",
        content_json={
            "trigger": "一个似乎证明了负面判断的误会出现",
            "escalation_logic": "语言解释无效（因为之前已有积累），对方选择沉默或离开",
            "midpoint_reversal": "发现误会背后有更深的原因，两人都有问题",
            "resolution_type": "用行动（不是独白）打破僵局；解释本身也是行动的一部分",
            "subplots": ["第三者的出现加剧误会", "误会本身揭示了两人都没说出口的真实需求"],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("言情情节设计：如何让误会不显得廉价")],
        confidence=0.60,
        tags=["言情", "情节", "冲突解决"],
    ),

    MaterialEntry(
        dimension="anti_cliche_patterns",
        genre="言情",
        slug="rom-ac-no-perfect-male-lead",
        name="完美男主的空洞感",
        narrative_summary="没有缺陷的男主角无法产生真实感，读者可以崇拜但无法共情，完美是情感投入的对立面",
        content_json={
            "cliche_description": "男主帅/有钱/能干/温柔/专一，没有真实的缺陷，所有行为都是正确的",
            "why_it_fails": "完美意味着读者没有机会担心他/为他感到心疼，情感投入的来源消失",
            "alternative_approach": "给男主一个真实的弱点：恐惧/不擅长的东西/真实的错误——而且这个弱点对女主有影响",
            "examples": ["高冷男主对拒绝有真实的恐惧，因此才显得冷漠", "强大男主在某个特定领域完全无助"],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("言情男主角设计：完美幻想与真实共情的平衡")],
        confidence=0.63,
        tags=["言情", "反套路", "男主"],
    ),
]


# ---------------------------------------------------------------------------
# ── 心理惊悚/魔修/黑化 ────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

SEED_DATA += [
    MaterialEntry(
        dimension="world_settings",
        genre="心理惊悚",
        slug="psych-ws-systemic-dark",
        name="结构性黑暗世界",
        narrative_summary="邪恶不来自个人心理问题，而来自世界规则本身：强者即正义、弱者活该被淘汰，主角的黑化是对这个规则的主动接受",
        content_json={
            "geography_model": "等级森严的修真/社会结构，底层受苦是明文规则而非意外",
            "power_vacuum": "正派腐败，所谓正义只维护强者利益，道义行为无法生存",
            "civilizational_rules": "强者不需要理由，弱者不能有不满；同情弱者被视为愚蠢",
            "unique_conflict_source": "主角的价值观与世界规则的根本矛盾：要活下去就必须接受这套规则",
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("黑化主角叙事分析：世界观设计如何为道德堕落提供合理化基础")],
        confidence=0.60,
        tags=["心理惊悚", "黑化", "魔道"],
    ),

    MaterialEntry(
        dimension="character_archetypes",
        genre="心理惊悚",
        slug="psych-ca-graduated-corruption",
        name="渐进式堕落者",
        narrative_summary="从有明确底线到底线不断被侵蚀，每一步都有内部逻辑，读者跟着主角一起被一步步带入黑暗",
        content_json={
            "core_wound": "被这个世界用它自己的规则彻底伤害，而非个人不幸",
            "external_goal": "表面目标是生存/复仇，内在目标是证明'只有这样才能活'",
            "internal_need": "在黑暗中维持一点点让自己相信还没有完全失去的东西",
            "fatal_flaw": "每次合理化之后下一次合理化的门槛降低，自我欺骗是递进的",
            "typical_arc": "有原则→第一次被迫越界（有充分理由）→越界后更顺利→主动越界→失去之前在乎的东西→顿悟或沉沦",
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("班杜拉道德脱离理论在小说反派弧线设计中的应用")],
        confidence=0.63,
        tags=["心理惊悚", "黑化", "人物弧线"],
    ),

    MaterialEntry(
        dimension="thematic_motifs",
        genre="心理惊悚",
        slug="psych-tm-devil-philosophy",
        name="魔道哲学：道与魔的边界",
        narrative_summary="最高级的魔道不是'为所欲为'而是'知道规则并选择拒绝'，魔修有自己的精神体系，不是道的失败者而是对立的思想者",
        content_json={
            "symbol": "道与魔如同阴阳——没有绝对的善恶，只有对相同问题不同的解答",
            "cultural_origin": "道教'道可道非常道'——最高的道是不可被定义的；魔是对道的解构性质疑",
            "narrative_functions": [
                "让主角的魔道选择有哲学深度而非单纯放弃道德",
                "给反派或转型主角提供说服力强的内在逻辑",
                "读者被迫思考：如果道的规则是不公的，拒绝道是否合理",
            ],
            "variations": ["魔是道的试炼而非对立", "道是强者写的规则，魔是弱者的反抗", "两者都是人对宇宙规律的有限认知"],
        },
        source_type=LLM_SYNTH,
        source_citations=[
            wiki("道教", "https://zh.wikipedia.org/wiki/道教"),
            wiki("道可道非常道", "https://ctext.org/dao-de-jing/zh"),
        ],
        confidence=0.65,
        tags=["心理惊悚", "魔修", "哲学"],
    ),
]


# ---------------------------------------------------------------------------
# ── 娱乐圈 ───────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

SEED_DATA += [
    MaterialEntry(
        dimension="world_settings",
        genre="娱乐圈",
        slug="ent-ws-capital-ecology",
        name="资本生态下的娱乐圈",
        narrative_summary="艺人是资本的产品，公司是投资方，观众是消费者；流量不是才华的衡量而是资本效率的指标",
        content_json={
            "geography_model": "北京/上海影视基地为核心，综艺/影视/音乐三个圈子部分重叠但规则不同",
            "power_vacuum": "老牌经纪公司和新兴互联网资本争夺艺人控制权，艺人夹在中间",
            "civilizational_rules": "公开恋情=票房毒药（传统逻辑）；粉丝应援文化形成了独立的政治经济体",
            "unique_conflict_source": "流量 vs 实力：靠流量崛起的艺人与有实力无流量的艺人的体系冲突",
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("中国娱乐圈生态分析：资本逻辑与艺人成长的结构性矛盾")],
        confidence=0.60,
        tags=["娱乐圈", "资本", "流量"],
    ),

    MaterialEntry(
        dimension="character_archetypes",
        genre="娱乐圈",
        slug="ent-ca-hidden-depth-idol",
        name="深度隐藏的顶流",
        narrative_summary="完美偶像人设背后是孤独和表演的疲惫，在所有人崇拜或嫉妒的目光中找不到真实关系",
        content_json={
            "core_wound": "出道太早失去了普通人的成长经历，不知道真实的自己是什么样的",
            "external_goal": "完成某个个人目标（拍一部真正想拍的戏/证明某件事）",
            "internal_need": "被一个人以真实的样子认识，而不是角色或偶像",
            "fatal_flaw": "长期表演导致不知道哪些感情是真实的，对真实连接有恐惧",
            "typical_arc": "完美人设→意外展露真实→被某人接住→公众压力测试关系→代价",
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("娱乐圈题材明星主角的孤独叙事：人气与孤立的悖论")],
        confidence=0.60,
        tags=["娱乐圈", "顶流", "孤独"],
    ),

    MaterialEntry(
        dimension="anti_cliche_patterns",
        genre="娱乐圈",
        slug="ent-ac-realistic-industry",
        name="娱乐圈规则失真问题",
        narrative_summary="娱乐圈文里的公司规则/合同/媒体运作与现实完全不同，行业人一看就出戏",
        content_json={
            "cliche_description": "艺人可以随时解约/公司完全被主角控制/媒体24小时跟拍但主角可以随意避开",
            "why_it_fails": "娱乐圈题材的卖点之一是'行业内幕感'，失真直接破坏代入感",
            "alternative_approach": "研究真实经纪合同结构/发行周期/宣传逻辑；让主角的行动有真实的行业约束",
            "examples": ["艺人无法单方面解约，只能通过谈判或法律途径", "公司控制艺人社交账号的权限有合同明文规定"],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("娱乐圈小说行业真实性分析：常见失真点与对应处理方式")],
        confidence=0.62,
        tags=["娱乐圈", "反套路", "行业真实性"],
    ),
]


# ---------------------------------------------------------------------------
# ── 穿书/乙女游戏 ─────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

SEED_DATA += [
    MaterialEntry(
        dimension="world_settings",
        genre="穿书",
        slug="vil-ws-narrative-resistance",
        name="剧情强制力世界",
        narrative_summary="书中世界有自己的修复机制：改变关键情节后世界会尝试把事件'修正'回原有轨道，主角需要付出更大代价才能永久改变",
        content_json={
            "geography_model": "原著剧情线是看不见的引力，偏离越远反弹力越强",
            "power_vacuum": "原著男主/女主带有叙事光环：遇到危险会有人相救，遇到机遇会有人送到面前",
            "civilizational_rules": "改变小事代价小，改变核心剧情节点（关键死亡/关键相遇）需要消耗某种代价",
            "unique_conflict_source": "主角越来越偏离反派剧本，原本注定发生的事件开始出现裂缝",
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("穿书题材世界规则设计分析：命运阻力机制的叙事功能")],
        confidence=0.62,
        tags=["穿书", "乙女游戏", "命运"],
    ),

    MaterialEntry(
        dimension="character_archetypes",
        genre="穿书",
        slug="vil-ca-self-aware-villain",
        name="元认知反派主角",
        narrative_summary="知道自己在书里的反派，初期用的是信息差优势，但随着与书中人物的真实接触，元认知反而成为障碍",
        content_json={
            "core_wound": "穿越前的现实生活有某种遗憾/痛苦，投射到这个'重来'的机会里",
            "external_goal": "避免原著里自己的坏结局（被女主打脸/被放逐/被杀死）",
            "internal_need": "从旁观者视角真正进入这个世界，与书中人物建立真实的关系",
            "fatal_flaw": "过度依赖剧情知识，当剧情偏轨后反而比其他人更迷失",
            "typical_arc": "依靠知识求生→剧情开始偏轨→知识失效→被迫用真实反应代替策略",
        },
        source_type=LLM_SYNTH,
        source_citations=[wiki("异世界转生", "https://zh.wikipedia.org/wiki/异世界转生")],
        confidence=0.62,
        tags=["穿书", "反派", "元认知"],
    ),

    MaterialEntry(
        dimension="plot_patterns",
        genre="穿书",
        slug="vil-pp-butterfly-effect",
        name="连锁反应情节模式",
        narrative_summary="主角改变A，导致B发生了变化，B的改变又影响了C，最终改变的结果比原本更难预测",
        content_json={
            "trigger": "主角做了一件看似很小的事来避免坏结局",
            "escalation_logic": "这件小事的连锁反应影响了一个不在主角计划内的角色",
            "midpoint_reversal": "主角发现某个'应该'发生的重要事件没有发生，但不知道哪里出了问题",
            "resolution_type": "主角接受无法完全控制剧情，转而处理眼前真实的人际关系",
            "subplots": ["某个原著炮灰因为主角的改变获得了独立的故事线", "原著男主的行为轨迹偏离了主角的预期"],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("穿书题材蝴蝶效应情节设计：改变代价与连锁反应的叙事逻辑")],
        confidence=0.62,
        tags=["穿书", "情节", "蝴蝶效应"],
    ),
]


# ---------------------------------------------------------------------------
# ── 种田/田园仙侠 ──────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

SEED_DATA += [
    MaterialEntry(
        dimension="world_settings",
        genre="种田",
        slug="cozy-ws-spiritual-farm",
        name="灵脉农庄生态",
        narrative_summary="灵脉等级影响作物品质但不是线性的：过高灵脉让部分灵植'躁动'失控，农庄维护需要平衡而非单纯拔高",
        content_json={
            "geography_model": "以灵脉中心点为核心向外辐射的农庄区域，不同区域种不同灵植",
            "power_vacuum": "高阶修士不稀罕农业，反而给了主角可以安静经营的空间",
            "civilizational_rules": "灵植有'灵性'——长期善待会产生认主效果，粗暴对待会结出毒果",
            "unique_conflict_source": "某种珍稀灵植的成熟会吸引高阶修士注意，打破宁静",
        },
        source_type=LLM_SYNTH,
        source_citations=[
            wiki("二十四节气", "https://zh.wikipedia.org/wiki/二十四節氣"),
            eval_src("修仙种田文世界观设计：灵气农业经济与仙侠世界的融合逻辑"),
        ],
        confidence=0.60,
        tags=["种田", "仙侠", "农庄"],
    ),

    MaterialEntry(
        dimension="plot_patterns",
        genre="种田",
        slug="cozy-pp-daily-three-act",
        name="种田日常三件事结构",
        narrative_summary="每章/每日小目标→小障碍→小成就，结合季节节奏每5-8章有一次外部危机，形成微观闭合+宏观递进的节奏",
        content_json={
            "trigger": "某个日常目标被意外打断",
            "escalation_logic": "解决方案带来新的意外发现，比最初目标更有趣",
            "midpoint_reversal": "某个邻居/常客的到来揭示了外部世界的状态",
            "resolution_type": "完成了更大的事，但主角最在乎的还是农庄本身的某个细节",
            "subplots": ["某个常客的故事线悄悄推进", "农庄某个角落的小秘密"],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("种田文节奏设计：微观满足感与宏观故事推进的平衡")],
        confidence=0.62,
        tags=["种田", "节奏", "日常"],
    ),

    MaterialEntry(
        dimension="thematic_motifs",
        genre="种田",
        slug="cozy-tm-labor-value",
        name="劳动的价值",
        narrative_summary="种田是对'不劳而获金手指'的反命题：成果来自真实的投入，成就感来自过程而非结果，这是种田文的精神根基",
        content_json={
            "symbol": "第一次收获——自己种的粮食是最好吃的，不是因为灵气，是因为自己种的",
            "cultural_origin": "中国农耕文化的劳动价值观：'粒粒皆辛苦'；道教自然观：顺应天时地利",
            "narrative_functions": [
                "对抗现代焦虑：读者在主角的慢生活中获得喘息",
                "建立成就感：每一个小收获都有前期铺垫",
                "揭示主角品格：愿意真实付出的人比靠技巧取巧的人更值得信任",
            ],
            "variations": ["种田=自我疗愈", "种田=建立家园/安全感", "种田=与土地建立生命连接"],
        },
        source_type=LLM_SYNTH,
        source_citations=[wiki("农耕文化", "https://zh.wikipedia.org/wiki/农业")],
        confidence=0.63,
        tags=["种田", "母题", "劳动"],
    ),
]


# ---------------------------------------------------------------------------
# ── 通用维度补充（genre=None）────────────────────────────────────────────
# ---------------------------------------------------------------------------

SEED_DATA += [
    MaterialEntry(
        dimension="dialogue_styles",
        genre=None,
        slug="common-ds-subtext-dialogue",
        name="潜台词对话设计",
        narrative_summary="说A表达B，沉默传递C；中文叙事中含蓄表达比直接说出情感更有力量，尤其在情感高张力场景",
        content_json={
            "register": "表层：礼貌/日常；实际：情感宣言/威胁/求饶",
            "subtext_pattern": "角色说的是表面需求，实际传递的是内心状态",
            "rhythm_notes": "高潜台词场景需要短句；一句话后的沉默比回答更有重量",
            "examples": [
                "「你还没吃饭吧」（= 我在等你回来/我一直想着你）",
                "「你真的决定了？」（= 我不同意但不会拦你）",
                "「好的」（= 我很受伤但不想让你看到）",
            ],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("中文叙事的含蓄表达传统：言外之意在现代网文中的应用")],
        confidence=0.65,
        tags=["通用", "对话", "潜台词"],
    ),
    MaterialEntry(
        dimension="dialogue_styles",
        genre=None,
        slug="common-ds-villain-monologue",
        name="有说服力的反派独白",
        narrative_summary="反派论点要有内部一致性：听完后读者觉得'虽然错了但理解了'，而不是'这只是强词夺理'",
        content_json={
            "register": "冷静、逻辑性强，避免情绪化；相信自己是对的",
            "subtext_pattern": "每个论点都指向反派世界观的核心假设，而不是随机辩解",
            "rhythm_notes": "长句建立论点→短句落地核心→沉默让论点渗透",
            "examples": [
                "「你们说的正义，是谁定义的？」（质疑道德来源）",
                "「我做的每一件事，都有人在做，只是我不假装没做」（揭示伪善）",
                "「你现在恨我，十年后你会明白我做的是对的」（时间维度的自我辩护）",
            ],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("反派独白设计：内在逻辑一致性是关键，而非狡辩技巧")],
        confidence=0.65,
        tags=["通用", "对话", "反派"],
    ),
    MaterialEntry(
        dimension="scene_templates",
        genre=None,
        slug="common-st-farewell",
        name="告别场景",
        narrative_summary="好的告别不说'再见'：最后一次共同完成某件日常事物，双方都知道这是最后一次但没有人说出来",
        content_json={
            "scene_type": "情感收束",
            "entry_condition": "分离即将发生，双方都知道，但没有人先开口",
            "tension_source": "时间流逝的感知：每一秒都在缩短剩余时间",
            "exit_hook": "离开的一方走到门口停顿了一下，但没有回头",
            "variations": [
                "通过一件小物品完成情感交接而非语言",
                "全程讨论无关紧要的事，只有最后一个细节暴露情感",
                "其中一方故意制造普通感，让另一方相信还有下次",
            ],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("告别场景的叙事技法：沉默与行动胜过语言的情感表达")],
        confidence=0.65,
        tags=["通用", "场景", "告别"],
    ),
    MaterialEntry(
        dimension="emotion_arcs",
        genre=None,
        slug="common-ea-trust-rebuild",
        name="信任破裂与重建弧",
        narrative_summary="信任不靠一次道歉重建：需要足够多次的小行动让受伤的人相信变化是真实的，而非表演",
        content_json={
            "arc_name": "信任→背叛→冷却→试探→积累→重建",
            "stages": [
                "信任期：完全依赖，有脆弱性",
                "背叛时刻：一个具体事件打破信任（不是误会）",
                "冷却期：受伤方的防御机制，拒绝接触",
                "试探期：伤害方开始用小行动证明改变",
                "积累期：每次行动都是一块砖，但任何新的背叛会推倒一切",
                "重建期：信任重新建立，但形式已经不同了",
            ],
            "turning_point_trigger": "伤害方在受伤方不知情的情况下做了某件牺牲性的事",
            "reader_investment_mechanism": "读者更早知道伤害方在努力，产生'快发现啊'的期待",
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("信任修复心理学在小说情感弧设计中的应用")],
        confidence=0.65,
        tags=["通用", "情感弧", "信任"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns",
        genre=None,
        slug="common-ac-no-convenient-rescue",
        name="及时救场金手指",
        narrative_summary="每次主角处于真正危险时都有外力及时相救，消灭了所有真实代价感，读者停止了担心",
        content_json={
            "cliche_description": "主角陷入绝境→外力（盟友/系统/超能力觉醒）及时解救，完全没有损失",
            "why_it_fails": "读者学会了'主角肯定没事'，之后所有危险场景的张力全部消失",
            "alternative_approach": "救场可以有，但必须有代价：救了身体但失去了某样东西；或者主角用自己的能力救了自己但付出了真实代价",
            "examples": [
                "及时赶到但没来得及救某个重要配角",
                "自救成功，但暴露了身份/秘密",
                "被救了，但救他的人从此负债",
            ],
        },
        source_type=LLM_SYNTH,
        source_citations=[eval_src("叙事张力设计：为什么'没有代价的救场'会破坏悬念")],
        confidence=0.65,
        tags=["通用", "反套路", "代价"],
    ),
]


# ===========================================================================
# 执行插入
# ===========================================================================

async def seed_library(dry_run: bool = False, filter_genre: str | None = None) -> None:
    entries = SEED_DATA
    if filter_genre:
        entries = [e for e in entries if e.genre == filter_genre or e.genre is None]

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Seeding {len(entries)} entries into material_library...")

    if dry_run:
        from collections import Counter
        by_genre = Counter(e.genre or "NULL" for e in entries)
        by_dim = Counter(e.dimension for e in entries)
        print("\nBy genre:", dict(sorted(by_genre.items())))
        print("By dimension:", dict(sorted(by_dim.items())))
        return

    async with session_scope() as session:
        success = 0
        errors = 0
        for entry in entries:
            try:
                await insert_entry(session, entry, compute_embedding=True)
                success += 1
            except Exception as exc:
                print(f"  ✗ {entry.slug}: {exc}")
                errors += 1

        await session.commit()

    print(f"\n✓ Inserted/updated {success} entries ({errors} errors)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the material library with initial data")
    parser.add_argument("--dry-run", action="store_true", help="Preview without inserting")
    parser.add_argument("--genre", default=None, help="Only seed entries for this genre")
    args = parser.parse_args()

    asyncio.run(seed_library(dry_run=args.dry_run, filter_genre=args.genre))


if __name__ == "__main__":
    main()
