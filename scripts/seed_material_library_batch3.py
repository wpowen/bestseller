#!/usr/bin/env python3
"""
Material Library Seed Script - Batch 3
新增 4 个题材（武侠/游戏虚拟/快穿/萌宠灵宠），并补充已有题材的缺失维度。

Usage:
    uv run python scripts/seed_material_library_batch3.py [--dry-run] [--genre GENRE]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bestseller.infra.db.session import session_scope
from bestseller.services.material_library import MaterialEntry, insert_entry

def wiki(title: str, url: str) -> dict:
    return {"url": url, "title": title, "accessed": "2026-04"}

def eval_src(text: str) -> dict:
    return {"text": f"evaluative source: {text}", "confidence": 0.65}

def llm_note(text: str) -> dict:
    return {"text": f"[LLM推演] {text}", "confidence": 0.55}

L = "llm_synth"

SEED_DATA: list[MaterialEntry] = [

    # ===========================================================================
    # 武侠 — NEW GENRE
    # ===========================================================================

    MaterialEntry(
        dimension="world_settings", genre="武侠",
        slug="wuxia-ws-jianghu-geography",
        name="江湖地理格局",
        narrative_summary="五大门派/七大帮会控制的江湖格局，武当少林是名义上的中立仲裁者，绿林黑道与正派并非截然对立",
        content_json={
            "factions": "名门正派（少林武当峨眉）→江湖散人→绿林豪强→朝廷鹰犬，四者交织",
            "geography_logic": "水路控制权=情报权；山岳要道=军事控制；城市=商业+政治节点",
            "neutrality_myth": "没有真正的中立——名义中立的名派都有自己的私利",
            "power_vacuum": "武林大会是利益重新分配的正式场合，每次大会之后江湖格局都会变",
        },
        source_type=L, confidence=0.68,
        source_citations=[wiki("武侠小说", "https://zh.wikipedia.org/wiki/武俠小說"), llm_note("武侠江湖政治地理分析")],
        tags=["武侠", "世界观", "江湖", "门派"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="武侠",
        slug="wuxia-ws-martial-cosmology",
        name="武学宇宙观",
        narrative_summary="武功不只是技术，是哲学——刚与柔、快与慢、攻与守对应不同人生观，武学境界对应人格完成度",
        content_json={
            "power_philosophy": "以柔克刚/四两拨千斤是太极思想的外化；以力破巧是阳刚意志的外化",
            "境界_levels": "练气→入道→化境→天人合一，每个境界对应世界观转变而非单纯数值提升",
            "inner_vs_outer": "外功（拳脚刀剑）vs内功（内力修炼），两者平衡才是真正高手",
            "武学_as_identity": "一个人的武功风格即其性格——急切激进的人练霸道剑法，内敛的人走内家拳路",
        },
        source_type=L, confidence=0.70,
        source_citations=[wiki("武术", "https://zh.wikipedia.org/wiki/武術"), llm_note("武侠武学哲学分析")],
        tags=["武侠", "世界观", "武学", "哲学"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="武侠",
        slug="wuxia-ca-lone-xia",
        name="孤侠",
        narrative_summary="没有门派背景、没有师承庇护、凭一己之力行走江湖——孤立是弱点也是自由，不受门派利益束缚反而能做最难的选择",
        content_json={
            "freedom_cost": "没有门派意味着没有后援，每次受伤只能独自治愈；没有归属意味着始终异乡人",
            "moral_independence": "不用为门派面子和利益牺牲原则，可以做'正确的事'即使没人支持",
            "arc": "不需要归属→被江湖拖入→意外结下义气→意识到孤独和连接不是非此即彼",
            "contrast": "与有门派背景的伙伴形成对比：门派是资源也是枷锁",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm_note("武侠孤侠原型叙事研究")],
        tags=["武侠", "主角", "孤侠", "自由"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="武侠",
        slug="wuxia-ca-fallen-hero",
        name="堕落的英雄",
        narrative_summary="曾经是江湖正道的代表，因一次错误/背叛/失去而走向黑暗，成为主角必须面对的最复杂的对手或者盟友",
        content_json={
            "fall_trigger": "被正道背叛（无辜被冤）/失去最重要的人/为保住某人做了一件跨越底线的事",
            "inner_conflict": "旧日的侠义精神还在，与当前的行为方式形成持续内耗",
            "relationship_with_protagonist": "主角的成功必须经过这个人——要么拉他回来，要么代价极大地战胜他",
            "redemption_options": ["牺牲式回归（用死换回）", "活着的和解（最难写的那种）", "不救赎，带着遗憾离场"],
        },
        source_type=L, confidence=0.68,
        source_citations=[eval_src("武侠小说堕落英雄原型：萧峰、令狐冲等经典人物研究")],
        tags=["武侠", "反派", "堕落", "救赎"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="武侠",
        slug="wuxia-pp-martial-arts-secret",
        name="武学秘籍争夺线",
        narrative_summary="秘籍/神功是麦格芬，真正的剧情是围绕它产生的人心与人性——谁得到秘籍只是表面，得到之后如何选择才是核心",
        content_json={
            "MacGuffin_function": "秘籍是各方势力的公共目标，汇集所有矛盾于一点",
            "reveal_structure": "秘籍的真正价值被误解→各方为误解的价值相互厮杀→真相揭露时战争已无意义",
            "protagonist_choice": "主角得到/不得到秘籍，如何处置它，体现其武侠精神的境界",
            "anti_cliche": "秘籍不应该直接赋予主角碾压实力，修炼过程中的领悟才是真正的力量来源",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("武侠武学秘籍叙事功能研究")],
        tags=["武侠", "情节", "秘籍", "麦格芬"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="武侠",
        slug="wuxia-pp-wrongful-accusation",
        name="莫须有罪名与申冤线",
        narrative_summary="主角被江湖公认的势力冤枉，在所有人的敌意中寻找真相——申冤过程中发现的真相往往比罪名本身更震撼",
        content_json={
            "setup": "有分量的势力诬陷，让主角短时间内人人喊打",
            "investigation": "在追杀中调查真相，每找到一条线索就更接近危险",
            "revelation": "真相指向更大的阴谋，幕后黑手是看似最不可能的人",
            "justice_question": "江湖自有公道——但这个'公道'是谁定义的？",
            "cost": "申冤成功之后，失去的东西（朋友/声名/某人的性命）无法归还",
        },
        source_type=L, confidence=0.68,
        source_citations=[eval_src("古龙金庸武侠冤案叙事模式研究")],
        tags=["武侠", "情节", "冤案", "申冤"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="武侠",
        slug="wuxia-tm-xia-spirit",
        name="侠之精神母题",
        narrative_summary="侠不是无敌的战士，是在有选择的时候选择了承担——侠的定义在每一次选择中被重新书写",
        content_json={
            "core_definition": "侠：以个人力量承担超出自身利益的责任，知道代价仍然选择",
            "levels": "小侠（救一人）→中侠（护一城）→大侠（为天下）——层级越高代价越重",
            "modern_question": "独善其身和兼济天下哪个更难？武侠好的作品不给简单答案",
            "narrative_expression": "侠的精神在最难的那个选择里体现：主角可以不管，但选择了管",
        },
        source_type=L, confidence=0.73,
        source_citations=[wiki("侠", "https://zh.wikipedia.org/wiki/俠"), llm_note("武侠精神哲学研究")],
        tags=["武侠", "主题", "侠", "精神"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre="武侠",
        slug="wuxia-ea-revenge-redemption",
        name="复仇与超越弧",
        narrative_summary="从仇恨驱动到超越仇恨——不是'原谅仇人'，而是主角意识到复仇完成之后自己变成了什么，是否还愿意做那个人",
        content_json={
            "stage1": "仇恨期：目标明确，痛苦反而让人觉得有方向",
            "stage2": "路途发现：复仇路上遇到比仇恨更重要的东西（人/使命/信念）",
            "stage3": "临界时刻：仇人就在眼前，主角可以结束，但感觉有什么不对",
            "stage4": "选择时刻：复仇成了一个选项而非必须——这才是真正的超越",
            "cost": "超越仇恨不等于一切恢复如初，有些失去永远失去了",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("武侠复仇叙事情感弧研究")],
        tags=["武侠", "情感弧", "复仇", "成长"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="武侠",
        slug="wuxia-ac-invincible-protagonist",
        name="无敌主角禁忌",
        narrative_summary="武功到后期碾压所有对手，危机永远靠实力解决——武侠的魂（侠义精神的考验）被武打数值淹没",
        content_json={
            "problem": "当主角永远是最强时，每次出场都是答案而非过程，叙事张力消失",
            "why_bad": "武侠的核心考验是道义困境，不是打架——无敌主角绕开了真正的主题",
            "correct_approach": "物理力量有上限；真正的考验来自必须在两个都有道义支撑的选项中选一个",
            "benchmark": "萧峰、令狐冲的最大挑战从来不是遇到了更强的对手，而是遇到了更难的道义选择",
        },
        source_type=L, confidence=0.75,
        source_citations=[eval_src("武侠小说主角力量设计研究")],
        tags=["武侠", "反套路", "主角", "力量"],
    ),

    # ===========================================================================
    # 游戏/虚拟世界 — NEW GENRE
    # ===========================================================================

    MaterialEntry(
        dimension="world_settings", genre="游戏",
        slug="game-ws-full-dive-vr",
        name="完全沉浸VR游戏世界",
        narrative_summary="意识完全进入虚拟现实的游戏世界，感官与现实无异——'这只是游戏'的认知防线被侵蚀是核心叙事张力",
        content_json={
            "immersion_level": "五感完全同步，痛觉开启/关闭是设定选项，死亡体验的心理创伤是真实的",
            "reality_blur": "在VR里待的时间越长，大脑越难区分'真实'和'虚拟'",
            "unique_rules": "游戏机制（复活/存档/技能系统）和游戏内社会规则并存，在哪里更重要是角色选择",
            "stakes_question": "如果感知无法区分，虚拟世界里的情感和关系是否是'真实的'？",
        },
        source_type=L, confidence=0.67,
        source_citations=[llm_note("全沉浸VR游戏世界设定研究"), wiki("虚拟现实", "https://zh.wikipedia.org/wiki/虛擬實境")],
        tags=["游戏", "虚拟现实", "世界观", "沉浸"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="游戏",
        slug="game-ws-game-world-real",
        name="游戏世界变真实",
        narrative_summary="原本的游戏世界因某种原因变为真实（或主角被困在里面），NPC有了真实生命，游戏规则开始与现实逻辑冲突",
        content_json={
            "transition_trigger": "服务器事故/主角无法登出/世界融合/游戏公司实验",
            "rule_conflict": "游戏机制（等级/血条）在'真实化'后有多少仍然有效？哪些被覆盖了？",
            "npc_awakening": "原来的NPC有了自我意识，他们如何理解自己的存在？",
            "player_advantage": "玩家知道这是游戏（或曾经是），这个优势在多大程度上仍然有效？",
        },
        source_type=L, confidence=0.65,
        source_citations=[llm_note("游戏世界真实化设定叙事研究")],
        tags=["游戏", "世界观", "真实化", "规则"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="游戏",
        slug="game-ca-veteran-player",
        name="老玩家主角",
        narrative_summary="在游戏里有丰富经历的老手，知道攻略、了解机制、有旧时社群关系——但也有老玩家特有的视角盲区和固化思维",
        content_json={
            "advantage": "机制知识、地图熟悉、人脉/声誉积累",
            "blind_spot": "把所有事按'已知游戏逻辑'框架理解，反而错过'游戏变了'的信号",
            "social_capital": "旧时游戏里的仇敌/盟友在新世界里成为不可预期的变量",
            "identity_question": "游戏内的'成就'和现实身份有多大距离？游戏里的自己是否是更真实的自己？",
        },
        source_type=L, confidence=0.67,
        source_citations=[llm_note("游戏题材老玩家主角设计研究")],
        tags=["游戏", "主角", "老玩家", "经验"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="游戏",
        slug="game-pp-hidden-mechanics",
        name="隐藏机制发现线",
        narrative_summary="主角逐渐发现游戏里的隐藏机制/彩蛋/真正的规则——每次发现都改变了对游戏世界的理解和对策",
        content_json={
            "discovery_types": "隐藏地图/隐藏职业/NPC隐藏对话线/游戏公司留下的秘密/世界设定深处的真相",
            "narrative_function": "推动主角从'攻略游戏'到'理解这个世界为什么被设计成这样'",
            "meta_layer": "最终的隐藏机制可能指向游戏外的真相（游戏设计者的意图/游戏的真实目的）",
            "pacing": "每次发现必须改变前面已知的某些东西，不能是纯新增信息",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm_note("游戏世界叙事隐藏机制设计研究")],
        tags=["游戏", "情节", "发现", "机制"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="游戏",
        slug="game-tm-real-vs-virtual",
        name="真实感与虚拟感主题",
        narrative_summary="游戏题材的核心哲学：在虚拟世界里建立的情感和做出的选择，是否与'真实'世界的同等重要",
        content_json={
            "core_question": "如果感知是真实的、痛苦是真实的、感情是真实的，'虚拟'只是一个标签吗？",
            "narrative_arc": "主角从'这只是游戏'到'这对我是真实的'的认知转变",
            "value_judgement": "好的游戏题材不给这个问题简单答案——让读者自己判断",
            "symbolic_moment": "主角第一次因为游戏里的失去而真实流泪，是这个主题最好的具象化",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("游戏题材虚实哲学主题研究")],
        tags=["游戏", "主题", "真实", "虚拟"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="游戏",
        slug="game-ac-cheat-system",
        name="系统挂机禁忌",
        narrative_summary="主角得到的系统/金手指让所有游戏挑战轻松解决，游戏设定只是升级的背景板",
        content_json={
            "problem": "游戏题材的吸引力在于'规则下的创造性解题'，无限系统消除了规则约束",
            "why_bad": "读者感兴趣的是'用有限的工具解决问题'，无限工具让过程无聊",
            "correct_approach": "系统/金手指提供独特视角或一种额外可能，但不消除游戏的规则约束",
            "test": "如果去掉系统，主角还有能力应对挑战吗？有的话才是真实成长",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm_note("游戏题材主角能力设计研究")],
        tags=["游戏", "反套路", "系统", "金手指"],
    ),

    # ===========================================================================
    # 快穿/系统 — NEW GENRE
    # ===========================================================================

    MaterialEntry(
        dimension="world_settings", genre="快穿",
        slug="qc-ws-task-world-structure",
        name="任务世界结构",
        narrative_summary="每个穿越世界都是独立的故事场景，有自己的剧情逻辑、原有角色记忆、任务目标——快穿的乐趣在于用同一个核心角色适应不同世界规则",
        content_json={
            "world_types": "小说世界/影视世界/历史世界/架空世界/游戏世界，每种有不同信息密度",
            "task_logic": "任务通常是修复因前人干扰导致的剧情崩坏，或完成原角色未竟之事",
            "memory_mechanics": "每世界结束后记忆如何处理？完全带走/部分保留/只保留技能是三种叙事选择",
            "world_residue": "前面几个世界的经历如何影响主角在后续世界的判断——不是'每次重置'",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm_note("快穿题材任务世界系统设计研究")],
        tags=["快穿", "世界观", "任务", "系统"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="快穿",
        slug="qc-ws-system-contract",
        name="系统契约关系",
        narrative_summary="主角与系统的关系不只是工具使用，是一种有张力的合作关系——系统有自己的立场、限制、甚至秘密",
        content_json={
            "system_personality": "系统可以是冷漠工具/有个性的助手/隐藏议程者，三种定位影响叙事深度",
            "contract_terms": "完成任务的奖励与代价；失败的后果；可以拒绝任务吗？",
            "trust_dynamics": "主角与系统的信任是如何建立的？系统是否有欺骗主角的能力和动机？",
            "meta_question": "谁雇用了系统？系统本身是否也在某个更大的体系里被使用？",
        },
        source_type=L, confidence=0.67,
        source_citations=[llm_note("快穿系统契约叙事设计研究")],
        tags=["快穿", "世界观", "系统", "契约"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="快穿",
        slug="qc-ca-mission-specialist",
        name="任务专家型主角",
        narrative_summary="经历多个世界后形成的'专业性'——判断速度快、情感投入谨慎、但也因此错过了每个世界最珍贵的东西",
        content_json={
            "advantage": "快速识别每个世界的规律、高效完成任务、不被表面情绪误导",
            "blind_spot": "把效率放在体验前面，失去了每个世界的独特性",
            "character_arc": "从纯任务导向→在某个世界开始真正在乎→被迫选择任务完成和留下来之间",
            "core_question": "在每个世界都是'过客'，如何确认自己有真实的存在感？",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm_note("快穿题材主角人格设计研究")],
        tags=["快穿", "主角", "专业", "存在感"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="快穿",
        slug="qc-ca-fixed-cp",
        name="固定CP跨世界存在",
        narrative_summary="在多个世界里以不同身份出现的同一灵魂——主角认出他/她，但每次都需要重新建立关系，这种熟悉和陌生的叠加是快穿独特的情感张力",
        content_json={
            "recognition_mechanics": "如何确认是'他'？灵魂特质/无意识的习惯/某个专属细节",
            "relationship_reset": "每个世界的关系从零开始，但主角有前几世的记忆，不对等的信息差造成心理压力",
            "evolution": "每个世界的关系积累如何影响最终世界？是否有一世比其他世更真实？",
            "anti_pattern": "CP在每个世界都秒懂主角、立刻相爱——失去了关系建立的过程",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("快穿固定CP叙事设计研究")],
        tags=["快穿", "CP", "跨世界", "灵魂"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="快穿",
        slug="qc-pp-world-collapse",
        name="世界崩溃危机弧",
        narrative_summary="任务世界因主角到来或前任干预而出现非预期的崩溃信号，主角必须在完成任务的同时处理崩溃，两者目标可能相互矛盾",
        content_json={
            "collapse_signals": "NPC行为反常/天气异常/时间线混乱/原著情节无法被触发",
            "cause": "前任任务者遗留的改动/主角自己的蝴蝶效应/世界本身的不稳定性",
            "dual_challenge": "完成原有任务 vs 修复世界崩溃，两个目标在关键节点发生冲突",
            "resolution": "世界崩溃的真正原因往往指向系统或更高层的真相",
        },
        source_type=L, confidence=0.67,
        source_citations=[llm_note("快穿世界崩溃叙事结构研究")],
        tags=["快穿", "情节", "崩溃", "危机"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="快穿",
        slug="qc-tm-identity-flow",
        name="身份流动与核心自我主题",
        narrative_summary="每个世界扮演不同的人，如何确认哪个才是真正的自己——快穿的哲学核心不是任务完成，而是在无数身份流动中保持自我连续性",
        content_json={
            "identity_threat": "每个世界都要入戏，入得太深会忘记原来是谁；太浅又无法完成任务",
            "core_self_markers": "主角始终保有的某些特质/习惯/价值观，在每个世界里以不同形式表现",
            "final_question": "多次改变之后，'原来的自己'还存在吗？这个问题的答案决定结局的走向",
            "resolution": "核心自我不是不变的，而是有连续性的——每个世界都在增添而非覆盖",
        },
        source_type=L, confidence=0.72,
        source_citations=[llm_note("快穿身份叙事哲学研究")],
        tags=["快穿", "主题", "身份", "自我"],
    ),

    # ===========================================================================
    # 萌宠/灵宠 — NEW GENRE
    # ===========================================================================

    MaterialEntry(
        dimension="world_settings", genre="萌宠",
        slug="pet-ws-spirit-beast-ecology",
        name="灵兽生态体系",
        narrative_summary="灵兽不是工具，是有自己社会结构的种族——与人类的契约关系是双向选择，而非人类单方面的控制",
        content_json={
            "hierarchy": "灵兽有自己的等级体系和领地概念，高阶灵兽不会轻易接受低修为人类的契约",
            "contract_nature": "契约是双向的承诺，灵兽要求主人做到某些事才会提供力量支持",
            "ecology": "灵兽群落有自己的生态位，顶层掠食者与底层灵兽的关系影响整个区域的灵气分布",
            "communication": "灵兽与人类的沟通方式：情感共鸣/意象传递/部分灵兽可直接对话",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm_note("灵兽世界观与生态设计研究")],
        tags=["萌宠", "灵兽", "世界观", "生态"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="萌宠",
        slug="pet-ca-spirit-beast-partner",
        name="灵兽伙伴（配角）",
        narrative_summary="不只是战斗辅助，而是真正意义上的伙伴——有自己的性格、判断力、喜恶，会在主角犯错时用行动表示不满",
        content_json={
            "personality_types": "傲娇高冷型/憨厚忠诚型/捣蛋好奇型/智慧老者型，每种有独特的叙事节奏",
            "independence": "灵兽可以做主角没有说的事，甚至有时比主角更早察觉危险",
            "communication_depth": "灵兽不用说话也能表达复杂情感——肢体语言/行动/拒绝配合都是表达",
            "growth": "灵兽在与主角共历中也在成长，不是永远停留在'初见时的可爱小兽'",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("灵兽配角角色设计研究")],
        tags=["萌宠", "灵兽", "配角", "性格"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="萌宠",
        slug="pet-pp-bonding-arc",
        name="契约建立弧",
        narrative_summary="从初次相遇的互相戒备，到危机中建立信任，到真正的心意相通——灵兽契约的建立过程本身就是一条完整的情感叙事线",
        content_json={
            "first_meeting": "灵兽不会轻易信任人类，初见应有警惕/考验/拒绝的过程",
            "trust_events": "几次关键事件建立信任：主角保护灵兽/为灵兽牺牲利益/尊重灵兽的判断",
            "contract_moment": "契约不应该是单方面的能力获取，而应该是双方真正意义上的承诺",
            "deepening": "契约之后感情继续加深——灵兽越来越了解主角，双方的默契来自真实积累",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("灵兽契约叙事情感弧研究")],
        tags=["萌宠", "情节", "契约", "信任"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="萌宠",
        slug="pet-st-first-bond",
        name="初次眼神相遇场景",
        narrative_summary="主角与灵兽第一次真正对视的那一刻——高光场景，用灵兽眼睛中的情绪告诉读者这段关系未来的走向",
        content_json={
            "atmosphere": "周围人的嘈杂、战场的混乱或静谧的丛林——背景不重要，那一刻只有两人",
            "灵兽_gaze": "眼睛里有的东西：警惕/好奇/某种难以描述的认出感",
            "主角_response": "主角的直觉反应：本能的平静/想靠近/感觉被看见了",
            "physical_details": "毛发颜色与光线的互动/呼吸的节奏/距离的缓慢缩短",
            "ending": "不以契约成功结束，而以双方都没有离开结束——最好的开端",
        },
        source_type=L, confidence=0.72,
        source_citations=[llm_note("灵兽初遇场景写作技法研究")],
        tags=["萌宠", "场景", "初遇", "感官"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="萌宠",
        slug="pet-tm-mutual-trust",
        name="双向信任主题",
        narrative_summary="主角通过灵兽学会信任——这是萌宠题材最深的主题层：有些人在无法信任人类时先与动物建立了连接",
        content_json={
            "psychological_layer": "主角的信任创伤往往来自人类关系；灵兽的无条件（或有条件的）信任是修复的起点",
            "healing_mechanism": "灵兽不会说谎、不会背叛（或背叛有更大代价），是不同于人类关系的安全锚",
            "narrative_function": "主角学会信任灵兽→开始能够信任某些人类→情感关系修复",
            "avoid": "不要让灵兽成为'不需要人类关系'的替代品，而是'通向人类关系的桥梁'",
        },
        source_type=L, confidence=0.72,
        source_citations=[llm_note("宠物/灵兽主题心理治愈叙事研究")],
        tags=["萌宠", "主题", "信任", "治愈"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="萌宠",
        slug="pet-ac-cute-powerup",
        name="纯增益萌宠禁忌",
        narrative_summary="灵兽只有无条件萌和无限战斗力，没有自己的需求和局限——萌宠的生命感全部消失，变成会跑的道具",
        content_json={
            "problem": "真实的生命关系是双向的：灵兽也有需要被满足、有时候会累/受伤/犯错",
            "why_bad": "灵兽的局限性才是它最可爱的地方；无限萌宠失去了让读者投入的脆弱性",
            "correct_approach": "灵兽应该有脆弱的时候、有令主角担心的时候、有做错事的时候",
            "test": "读者应该有时候会担心灵兽，而不只是欣赏它的萌",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm_note("灵兽角色真实感设计研究")],
        tags=["萌宠", "反套路", "灵兽", "真实感"],
    ),

    # ===========================================================================
    # 补充：历史 缺失维度 (locale_templates + scene_templates)
    # ===========================================================================

    MaterialEntry(
        dimension="locale_templates", genre="历史",
        slug="hist-lt-imperial-court",
        name="朝廷大殿场所",
        narrative_summary="皇权展示的物理空间——建筑尺度碾压个体，空间设计服务于权力展示，每寸距离代表不同地位",
        content_json={
            "spatial_hierarchy": "御座→御前二步→朝臣一阶→末席臣工：物理距离=政治距离",
            "light_atmosphere": "高窗射入的斜光/香烟缭绕/上朝时的冷意，是权力空间的感官底色",
            "ritual_function": "跪拜/奏折/退朝，每个动作都有严格程式，违反即是政治信号",
            "narrative_use": "重大决定往往在这个空间做出，但真正的谋划在私下，大殿只是宣告",
        },
        source_type=L, confidence=0.68,
        source_citations=[wiki("故宫", "https://zh.wikipedia.org/wiki/故宮"), llm_note("历史朝廷空间叙事分析")],
        tags=["历史", "场所", "朝廷", "权力"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="历史",
        slug="hist-st-execution-ground",
        name="问斩与刑场场景",
        narrative_summary="最极端的权力展示场景——不只是死亡，是国家对身体和尊严的最后控制；旁观者的沉默也是政治表态",
        content_json={
            "atmosphere": "秋後问斩的寒意/人群的安静/刑部官员的程式化/监斩者的目光",
            "power_display": "公开处刑是给所有在场者的信息：这就是违抗权力的结局",
            "narrative_use": "可以是高潮前的最低点（最后关头救人）/可以是真正的结局（权力的最终胜利）",
            "internal_monologue": "被执行者在最后时刻的内心——回忆/遗憾/意外的平静",
            "bystander_perspective": "旁观者的视角往往比当事人更能展示这个场景的政治重量",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm_note("历史题材极端权力场景写作研究")],
        tags=["历史", "场景", "权力", "生死"],
    ),

    # ===========================================================================
    # 补充：都市 补充维度
    # ===========================================================================

    MaterialEntry(
        dimension="world_settings", genre="都市",
        slug="urban-ws-modern-underworld",
        name="都市隐性秩序",
        narrative_summary="现代都市表面法制，实则存在看不见的隐性秩序——商界潜规则/地下势力/情报网络，与正式法律体系并行运作",
        content_json={
            "layers": "法律层（警察法院）→灰色层（合法但走弯道的利益网络）→黑色层（地下势力）",
            "power_transfer": "灰色层是三层之间的翻译者，了解灰色层才能在都市游走",
            "protagonist_position": "主角如何在三层中定位自己，决定了故事的道德张力",
            "conflict_source": "当法律层和黑色层在同一个利益上直接碰撞时，主角站哪边？",
        },
        source_type=L, confidence=0.65,
        source_citations=[llm_note("都市隐性秩序叙事结构分析")],
        tags=["都市", "世界观", "地下", "秩序"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="都市",
        slug="urban-ca-self-made-protagonist",
        name="白手起家主角",
        narrative_summary="从底层出发凭真实努力和智慧在都市立足的主角——没有背景没有金手指，靠对规则的理解和人性的洞察走出来",
        content_json={
            "starting_point": "真实的底层起点（不是'其实家世显赫'的假平民）",
            "growth_engine": "智慧/勤奋/人脉建立，而非奇遇或系统",
            "vulnerability": "没有后盾意味着每次失败代价更大；不能承受太多次",
            "values": "来自底层的道德观：对钱的态度/对规则的理解，与精英阶层形成真实碰撞",
        },
        source_type=L, confidence=0.67,
        source_citations=[llm_note("都市白手起家主角设计研究")],
        tags=["都市", "主角", "白手起家", "成长"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre="都市",
        slug="urban-ea-ambition-cost",
        name="野心代价弧",
        narrative_summary="主角实现了最初的目标，却发现为它付出的代价改变了自己——成功是真实的，失去的也是真实的",
        content_json={
            "stage1": "纯粹渴望：对目标的向往干净直接，还没开始计算代价",
            "stage2": "代价显现：开始需要在原则和目标之间做权衡",
            "stage3": "深陷其中：已经走了很远，回头需要放弃太多",
            "stage4": "目标实现时刻：发现自己成了另一种人",
            "resolution_options": ["接受这种改变（不一定是悲剧）", "主动选择放弃一部分成功换回某种真实", "意识到失去的不可挽回（悲剧）"],
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("都市野心叙事情感弧研究")],
        tags=["都市", "情感弧", "野心", "代价"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="都市",
        slug="urban-tm-class-mobility",
        name="阶层流动主题",
        narrative_summary="从一个阶层进入另一个阶层并非只是成功故事——新阶层有新的规则和代价，而原来的阶层和关系也在悄悄变化",
        content_json={
            "upward_mobility_cost": "进入新阶层意味着原来的语言/习惯/关系方式需要改变",
            "identity_split": "向上流动的人往往活在两个世界之间，在每个地方都有点格格不入",
            "relationship_impact": "原来的朋友如何看待成功后的你？成功=离开了他们？",
            "class_consciousness": "每个阶层都觉得自己的规则是'正常的'，碰撞时才发现规则是约定而非自然",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm_note("都市阶层流动主题叙事研究")],
        tags=["都市", "主题", "阶层", "流动"],
    ),
]


async def seed_library(dry_run: bool = False, filter_genre: str | None = None) -> None:
    entries_to_seed = SEED_DATA
    if filter_genre is not None:
        entries_to_seed = [e for e in entries_to_seed if e.genre == filter_genre]

    print(f"{'[DRY RUN] ' if dry_run else ''}Seeding {len(entries_to_seed)} entries into material_library...\n")

    by_genre: dict[str, int] = {}
    by_dim: dict[str, int] = {}
    for e in entries_to_seed:
        key = e.genre or "NULL"
        by_genre[key] = by_genre.get(key, 0) + 1
        by_dim[e.dimension] = by_dim.get(e.dimension, 0) + 1

    print(f"By genre: {dict(sorted(by_genre.items()))}")
    print(f"By dimension: {dict(sorted(by_dim.items()))}\n")

    if dry_run:
        return

    errors = 0
    async with session_scope() as session:
        for entry in entries_to_seed:
            try:
                await insert_entry(session, entry, compute_embedding=True)
            except Exception as exc:
                print(f"  ✗ Error inserting {entry.slug}: {exc}")
                errors += 1
        await session.commit()

    print(f"\n✓ Inserted/updated {len(entries_to_seed) - errors} entries ({errors} errors)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed material library - batch 3")
    parser.add_argument("--dry-run", action="store_true", help="Preview without inserting")
    parser.add_argument("--genre", default=None, help="Only seed entries for this genre")
    args = parser.parse_args()
    asyncio.run(seed_library(dry_run=args.dry_run, filter_genre=args.genre))


if __name__ == "__main__":
    main()
