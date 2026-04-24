#!/usr/bin/env python3
"""
Batch 4 — 10 个全新重要题材
玄幻 / 洪荒封神 / 无限流 / 重生 / 机甲星战 / 校园青春 / 女尊 / 灵异鬼怪 / 赛博朋克 / 美食厨神
每题材 7-9 条 × 6-8 维度 ≈ 80 条

Usage: uv run python scripts/seed_material_library_batch4.py [--dry-run]
"""
from __future__ import annotations
import argparse, asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from bestseller.infra.db.session import session_scope
from bestseller.services.material_library import MaterialEntry, insert_entry

def wiki(t, u): return {"url": u, "title": t, "accessed": "2026-04"}
def ref(t): return {"text": t, "confidence": 0.65}
def llm(t): return {"text": f"[LLM推演] {t}", "confidence": 0.55}
L = "llm_synth"

SEED_DATA: list[MaterialEntry] = [

    # =========================================================================
    # 玄 幻 — 架空大陆修炼/魔法体系（与仙侠最大区别：无道教底色，纯架空）
    # =========================================================================
    MaterialEntry(
        dimension="world_settings", genre="玄幻",
        slug="xhuan-ws-continent-fracture",
        name="大陆裂变地缘格局",
        narrative_summary="远古神战将大陆撕裂为若干浮空板块，板块间的「裂隙」是危险通道也是最宝贵的资源产地，文明在裂隙边缘发展出独特秩序",
        content_json={
            "geography_model": "三大主陆块+无数碎岛，碎岛越小灵力越纯粹但生存越危险",
            "power_vacuum": "远古帝国崩溃留下的空白被新兴帝国/修炼圣地/商业联盟三方争夺",
            "civilizational_rules": "力量等级即法律——跨越特定等级则自动脱离凡俗法律体系，进入修炼者法则",
            "unique_conflict_source": "裂隙会周期性扩大，每次扩大都重新洗牌沿边势力的领土和资源",
            "cosmology": "天地有九层，每层对应一个修炼境界，突破意味着字面意义上进入更高维度",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm("玄幻大陆架空世界观设计分析"), wiki("玄幻小说", "https://zh.wikipedia.org/wiki/玄幻小說")],
        tags=["玄幻", "世界观", "大陆", "架空"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="玄幻",
        slug="xhuan-ws-ancient-ruins-ecology",
        name="远古遗址生态系统",
        narrative_summary="远古文明遗址是玄幻世界最重要的内容产出节点：危险、机遇、禁忌并存，每座遗址都是一个微型世界",
        content_json={
            "ruin_types": "远古帝国废都/倒塌的修炼圣地/封印的神魔战场/失落的炼器工坊",
            "ecology": "遗址内的灵力扭曲催生变异生物，形成独立食物链和生态位",
            "treasure_logic": "越深入越危险越宝贵，但大多数宝物有主（古老意志/封印/契约）",
            "narrative_function": "遗址探索=小型副本结构，提供自洽的风险-收益循环",
            "civilization_clues": "遗址里的壁画、器物、残留意志是世界历史的碎片，主角可通过它们还原真相",
        },
        source_type=L, confidence=0.67,
        source_citations=[llm("玄幻遗址探索叙事结构分析")],
        tags=["玄幻", "世界观", "遗址", "探索"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="玄幻",
        slug="xhuan-ca-waste-awakening",
        name="废材觉醒型主角（进化版）",
        narrative_summary="废材设定的正确用法：废材是叙事起点，不是永久标签——关键在于废材的根源是什么，觉醒意味着什么改变了",
        content_json={
            "废材_origin": "封印（体内有强力存在）/体质特殊（对常规检测不兼容）/前世压制（蓄势待发）",
            "觉醒_trigger": "必须有叙事代价或内因推动，不能是随机奇遇",
            "social_scar": "废材时期的社会性创伤（嘲讽/背叛/放弃）应该成为人格的一部分，不是觉醒后立刻消失",
            "power_gap": "觉醒不等于立刻无敌——废材积压的追赶期是重要叙事资源",
            "anti_pattern": "废材设定如果在第三章就翻转，等于浪费了最好的underdog叙事资本",
        },
        source_type=L, confidence=0.72,
        source_citations=[llm("玄幻废材主角叙事设计进化研究")],
        tags=["玄幻", "主角", "废材", "觉醒"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="玄幻",
        slug="xhuan-ca-ancient-being",
        name="远古存在觉醒者",
        narrative_summary="体内封印了远古强者意志/神魔碎片/上古功法，双重意识的拉锯是角色最独特的内心冲突",
        content_json={
            "dual_consciousness": "远古意志有自己的记忆、价值观、目的，与主角的现代心理产生真实冲突",
            "power_provenance": "力量来自于远古存在，但如何使用是主角自己的选择",
            "identity_question": "当远古意志越来越强，主角如何确认自己是主体而非容器",
            "reveal_pacing": "远古存在的真实身份和目的应该分层揭露，每层都颠覆前一层的理解",
            "symbiosis": "最终形态不是「驱逐远古意志」而是「融合为独特的新存在」",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm("玄幻双重意识角色设计研究")],
        tags=["玄幻", "主角", "远古", "双重意识"],
    ),
    MaterialEntry(
        dimension="power_systems", genre="玄幻",
        slug="xhuan-ps-element-resonance",
        name="元素共鸣力量体系",
        narrative_summary="以天地元素（金木水火土+光暗雷冰等）为力量来源，天赋体质决定亲和元素，体系设计关键在于元素间的克制/融合规律",
        content_json={
            "basic_elements": "五行基础+衍生元素（雷=火+风/冰=水+金），衍生元素稀有但不一定更强",
            "talent_tiers": "单元素纯度>双元素混合>无元素亲和（但无亲和可能意味着万物兼容的特殊体质）",
            "fusion_logic": "相生元素融合增强；相克元素融合产生破坏性不稳定能量，威力极大但危险",
            "power_ceiling": "元素掌握到极致可以「化元素为法则」，脱离元素本质进入更高维度",
            "design_principle": "元素体系好坏的关键不是元素数量，而是「融合/克制/特殊规则」是否有内在一致性",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm("玄幻元素体系设计内在逻辑分析"), wiki("五行", "https://zh.wikipedia.org/wiki/五行")],
        tags=["玄幻", "力量体系", "元素", "设计"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="玄幻",
        slug="xhuan-pp-tournament-arc",
        name="天才比武大会弧",
        narrative_summary="玄幻经典结构：多方势力汇聚、信息博弈、实力展示、隐藏阴谋四线并行，比武本身是框架，真正的故事在幕后",
        content_json={
            "setup": "大会是多势力的中立交流场所，但「中立」只是名义上的",
            "parallel_plots": "台上是对战展示/台下是情报交易/幕后是更大阴谋布局",
            "protagonist_goal": "主角的真正目的不只是赢，而是通过比武完成某个更大目标",
            "surprise_reveal": "比武结果改变了，但对真正格局影响的不是名次而是过程中的暴露",
            "anti_cliche": "主角不应该从第一轮就碾压所有对手——每场战斗应该暴露不同的挑战",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm("玄幻比武大会叙事结构分析")],
        tags=["玄幻", "情节", "比武", "结构"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="玄幻",
        slug="xhuan-tm-heaven-defiance",
        name="逆天命运主题",
        narrative_summary="玄幻最核心的主题矛盾：命运是天道刻写的还是个人意志可以改写的——真正深刻的叙事不给简单答案",
        content_json={
            "天道_as_system": "天道不是公正的，而是维持「稳定」的——打破稳定者即使正确也会被惩罚",
            "free_will_question": "主角每次「逆天」是真正的意志自由，还是天道用来打破旧平衡的工具？",
            "narrative_expression": "主角越强大，「天道」的压制越精妙——从明显的磨难变为无形的诱导",
            "resolution": "最深刻的逆天不是「打倒天道」而是「在天道规则内找到天道想不到的路」",
            "symbolic_objects": ["天命书/命运卷轴", "天劫（天道的显性干预）", "逆天者之殇"],
        },
        source_type=L, confidence=0.72,
        source_citations=[llm("玄幻逆天主题哲学分析"), wiki("道家哲学", "https://zh.wikipedia.org/wiki/道家")],
        tags=["玄幻", "主题", "逆天", "命运"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="玄幻",
        slug="xhuan-ac-flat-world",
        name="空洞世界观禁忌",
        narrative_summary="玄幻世界只有修炼等级和打架，没有经济/文化/历史/宗教——等于一个没有背景的数值游戏",
        content_json={
            "problem": "玄幻世界观的吸引力来自「一个真实存在的完整世界」，只有力量结构是骨架没有血肉",
            "missing_layers": "经济（谁在种地养活修炼者）/文化（普通人信仰什么）/历史（当前格局是如何形成的）",
            "correct_approach": "即使不展开，细节中也应该暗示这些层次的存在——路边的小贩、庙里的神像、老人讲的故事",
            "benchmark": "好的玄幻世界：读者感觉如果主角不在场，世界也在运转；坏的玄幻世界：主角不在就没有世界",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm("玄幻世界观深度设计研究")],
        tags=["玄幻", "反套路", "世界观", "深度"],
    ),

    # =========================================================================
    # 洪荒 / 封神 — 中国神话宇宙观写作
    # =========================================================================
    MaterialEntry(
        dimension="world_settings", genre="洪荒",
        slug="hh-ws-primordial-cosmos",
        name="洪荒宇宙观设定",
        narrative_summary="以《封神演义》《山海经》《道德经》为底层逻辑的神话宇宙：混沌开辟→三清分化→神仙体系→量劫循环",
        content_json={
            "cosmological_structure": "混沌（太初）→盘古开天（阴阳）→三清（道）→地水火风四大→万物",
            "power_hierarchy": "圣人（道祖/三清）→准圣→大罗金仙→金仙→真仙，圣人不死不灭境界截然不同",
            "量劫_logic": "天道以量劫淘汰旧势力、扶植新秩序，每次量劫都是文明更迭",
            "cause_effect": "洪荒世界因果极重——一个承诺、一块灵宝、一段因缘可能决定亿万年后的命运",
            "unique_rules": "圣人不可直接参战量劫，必须通过门人代行；鸿蒙紫气决定证道资格",
        },
        source_type=L, confidence=0.72,
        source_citations=[
            wiki("封神演义", "https://zh.wikipedia.org/wiki/封神演義"),
            wiki("山海经", "https://zh.wikipedia.org/wiki/山海經"),
            ref("《道德经》宇宙生成论：道生一，一生二，二生三，三生万物"),
        ],
        tags=["洪荒", "世界观", "神话", "宇宙观"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="洪荒",
        slug="hh-ws-treasure-ecology",
        name="先天灵宝生态",
        narrative_summary="先天灵宝/后天灵宝不只是武器，是有自身意志的存在——与主人的磨合是一段独特的叙事线",
        content_json={
            "天赐_vs_后天": "先天灵宝由混沌诞生，自带法则；后天灵宝由高手祭炼，力量来源于主人",
            "treasure_sentience": "越高阶的灵宝越可能有自我意识，对持有者有自己的判断和要求",
            "predestination": "灵宝与主人有「缘法」——强夺来的灵宝会反噬，有缘者持之自然契合",
            "unique_conflict": "两人都有宝物缘法，实力差距决定谁能拿到——宝物争夺本质是势力博弈",
        },
        source_type=L, confidence=0.68,
        source_citations=[wiki("法宝", "https://zh.wikipedia.org/wiki/法寶"), llm("洪荒灵宝设定体系分析")],
        tags=["洪荒", "世界观", "灵宝", "器灵"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="洪荒",
        slug="hh-ca-fallen-immortal",
        name="堕落天仙/跌境者",
        narrative_summary="曾经位列仙班、因量劫或一念之差跌入凡尘的存在，记忆封印/法力尽失，以凡人身份重新经历世界",
        content_json={
            "fall_mechanism": "量劫殒落→残魂转世/法力散尽→以凡人身份开始/封印/降低修为考验",
            "memory_recovery": "记忆以片段形式随成长逐渐回归——每次回忆都是一次「前世今生」的叙事震撼",
            "perspective_gain": "以凡人视角重历修仙世界，往往比原本境界更深刻的理解了「道」",
            "enemy_threat": "前世因果中的敌人可能更快恢复实力来追杀，形成追赶感",
            "reunion_dramatic_irony": "前世的战友/仇人以不知其真身的方式重新相遇",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm("洪荒跌境重修叙事原型研究")],
        tags=["洪荒", "主角", "转世", "记忆"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="洪荒",
        slug="hh-pp-tribulation-arc",
        name="量劫经历弧",
        narrative_summary="量劫是洪荒叙事最大规模的情节节点，是天道重新洗牌的时刻——主角如何在量劫中找到自己的位置是核心叙事",
        content_json={
            "pre_tribulation": "量劫前的征兆：天象异变/神仙下山/宝物出世/因果乱流",
            "tribulation_nature": "量劫不是坏的——是旧秩序终结和新秩序诞生的必要过程",
            "protagonist_position": "主角在量劫中的选择：站在哪方/保持中立/成为量劫的工具/试图超脱量劫",
            "cost": "量劫中每个选择都有代价——站对了赢得资源，站错了失去一切，超脱量劫最难且代价最重",
            "aftermath": "量劫后的世界格局根本改变，量劫前的人际关系/势力全部重组",
        },
        source_type=L, confidence=0.70,
        source_citations=[wiki("封神演义", "https://zh.wikipedia.org/wiki/封神演義"), llm("洪荒量劫叙事结构研究")],
        tags=["洪荒", "情节", "量劫", "叙事结构"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre="洪荒",
        slug="hh-rwr-chinese-mythology",
        name="中国神话体系真实参考",
        narrative_summary="洪荒写作的知识底库：《山海经》《淮南子》《封神演义》《西游记》构成四大参考层，加上先秦诸子的宇宙观",
        content_json={
            "山海经_value": "上古神兽原型库、地理神话依据、图腾崇拜背景",
            "封神演义_value": "仙神等级体系参考、量劫设定来源、人物原型（姜子牙/哪吒/杨戬）",
            "道德经_value": "「道」的哲学根基、无为/自然/阴阳辩证法",
            "淮南子_value": "共工怒触不周山/女娲补天等开天辟地神话",
            "activation_keywords": ["三清四御", "鸿钧老祖", "混沌珠", "诛仙剑阵", "封神榜", "功德金莲"],
        },
        source_type=L, confidence=0.80,
        source_citations=[
            wiki("山海经", "https://zh.wikipedia.org/wiki/山海經"),
            wiki("封神演义", "https://zh.wikipedia.org/wiki/封神演義"),
            wiki("道德经", "https://zh.wikipedia.org/wiki/道德經"),
        ],
        tags=["洪荒", "参考", "神话", "道家"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="洪荒",
        slug="hh-tm-merit-karma",
        name="功德与因果主题",
        narrative_summary="洪荒叙事的道德体系：善恶报应不是朴素的「好人有好报」，而是精密的宇宙因果会计——一切行为都在账上",
        content_json={
            "merit_system": "功德积累可以抵消劫难、加速修炼、护佑转世；业力积累则反之",
            "karmic_debt": "前世因果是今生命运的重要来源，解因果是修行的重要部分",
            "cosmic_justice": "天道因果不是即时的，可能隔亿万年兑现——这是「天道公平」的玄幻版本",
            "narrative_use": "主角不经意种下的善因/恶因，在最关键的时刻以意想不到的方式回报",
            "thematic_depth": "功德不是货币，而是主角对宇宙的净贡献度——做好事不是为了得回报，但回报确实存在",
        },
        source_type=L, confidence=0.73,
        source_citations=[wiki("佛教因果", "https://zh.wikipedia.org/wiki/因果"), llm("洪荒因果哲学叙事研究")],
        tags=["洪荒", "主题", "因果", "功德"],
    ),

    # =========================================================================
    # 无限流 — 死亡游戏/场景循环/恐怖副本
    # =========================================================================
    MaterialEntry(
        dimension="world_settings", genre="无限流",
        slug="inf-ws-game-system",
        name="主神空间/无限游戏系统",
        narrative_summary="玩家被强制抽入「游戏」，在一个个不同类型的恐怖/历史/科幻副本间穿越，积累点数升级，最终目标是解开系统本身的真相",
        content_json={
            "system_rules": "每次副本有任务目标/生死惩罚/奖励积分，积分可兑换能力或道具",
            "副本_diversity": "恐怖副本（鬼屋/灵异）/历史副本（战争/宫廷）/科幻副本（末日/星际）/架空副本（魔法/修仙）",
            "player_ecology": "老玩家/新玩家/叛变者/隐藏势力，形成副本内和副本外的双层社交",
            "meta_mystery": "主神空间本身是什么？谁建造的？为什么？是终极谜题",
            "horror_source": "最大的恐惧不来自副本怪物，而来自「不知道下一个副本是什么，不知道自己还能活多久」",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm("无限流副本系统世界观设计研究")],
        tags=["无限流", "世界观", "副本", "系统"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="无限流",
        slug="inf-ca-veteran-survivor",
        name="老玩家生存机器",
        narrative_summary="经历多次副本后人格趋于冷酷的老玩家，把一切都变成生存效率分析，直到遇到让他们重新「有感觉」的事",
        content_json={
            "cold_logic": "把队友的生死纳入效率计算，不是没有情感而是情感被系统性压抑",
            "survival_knowledge": "知道怎么活下来，但不知道「活下来之后做什么」——目的感的空缺",
            "crack_trigger": "某个新玩家/某个副本场景触发了压抑已久的人性",
            "arc": "效率机器→开始在乎某人→因为在乎而做了「低效」的选择→发现「低效」反而救了自己",
            "cost": "情感重新开放意味着痛苦也重新开放，但这才是真正活着",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm("无限流老玩家人格设计研究")],
        tags=["无限流", "主角", "创伤", "人性"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="无限流",
        slug="inf-pp-horror-survival",
        name="恐怖副本生存解谜结构",
        narrative_summary="无限流最基础叙事单元：进入副本→收集信息→识别规律→找到突破口→完成任务，每步都有死亡可能",
        content_json={
            "information_phase": "副本初期的信息收集：观察环境/测试NPC/探索规律，死亡成本最高但信息最少",
            "pattern_recognition": "副本有自己的「剧本」，找到剧本的规律才能找到生存路线",
            "cost_of_mistakes": "每个错误决策都应该有真实代价（受伤/失去道具/队友死亡），不能无限试错",
            "cooperation_tension": "临时组队的玩家利益不完全一致，合作中的猜忌是额外压力",
            "climax": "最终任务往往需要用到副本中所有收集到的线索，有「Everything clicks」的解谜满足感",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm("无限流副本解谜叙事结构研究")],
        tags=["无限流", "情节", "副本", "解谜"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="无限流",
        slug="inf-tm-choice-defines",
        name="极端压力下的选择主题",
        narrative_summary="无限流的本质是极端压力实验：去掉文明约束，人会做什么选择？主题不是「人性本恶」也不是「人性本善」，而是「选择构成了人」",
        content_json={
            "pressure_function": "副本的死亡压力把人的价值观暴露在白炽灯下",
            "choice_spectrum": "牺牲他人换自己活→等量交换→无私牺牲，每个位置都有人，且都可理解",
            "character_definition": "主角不是因为「天生善良」做好的选择，而是在有选择的情况下选择了善",
            "reader_mirror": "读者跟着主角做每个选择，不知不觉在思考「我会怎么做」——这是无限流最深的参与感",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm("无限流主题哲学研究")],
        tags=["无限流", "主题", "选择", "人性"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="无限流",
        slug="inf-ac-cheat-god",
        name="系统赋予无限外挂禁忌",
        narrative_summary="进入主神空间就获得碾压所有副本的超强能力，副本变成刷资源背景——恐怖感/解谜感/生死压力全部消失",
        content_json={
            "problem": "无限流的张力来源是「死亡是真实可能的」，无限外挂抽空了这个前提",
            "why_bad": "玩家若无危险则副本叙事等于旅游，而不是生存游戏",
            "correct_approach": "能力提升应该是循序渐进的，每个副本应该有真实的死亡边缘时刻",
            "correct_power_design": "外挂应该有「代价」或「局限」——物理上强但信息不足/情感上弱/对某类威胁无效",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm("无限流能力设计研究")],
        tags=["无限流", "反套路", "外挂", "张力"],
    ),

    # =========================================================================
    # 重 生 — 携带记忆重来（强调不同于穿书的当世重生）
    # =========================================================================
    MaterialEntry(
        dimension="world_settings", genre="重生",
        slug="reborn-ws-timeline-butterfly",
        name="重生时间线与蝴蝶效应",
        narrative_summary="重生在同一世界的过去，主角的每个行动都在改变历史——问题不是「我知道未来」而是「我改变的未来我不知道」",
        content_json={
            "knowledge_depreciation": "每次改变未来，相应的「先知优势」就减少一分，改变越多越像普通人",
            "butterfly_triggers": "微小改变（救了一个人/说了一句话）引发连锁，但方向不可控",
            "information_reassessment": "前世记忆的「事实」现在变成了「可能性」，主角必须重新验证一切",
            "new_threats": "改变历史可能催生前世不存在的新威胁",
            "timeline_anxiety": "主角永远不确定自己记忆中的未来还有多少仍然有效",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm("重生叙事蝴蝶效应设计研究")],
        tags=["重生", "世界观", "时间线", "蝴蝶效应"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="重生",
        slug="reborn-ca-second-chance-burden",
        name="带着创伤重来的人",
        narrative_summary="重生不是奖励而是负担——前世的失去/遗憾/背叛构成心理创伤，带着这些记忆的人无法简单「重新开始」",
        content_json={
            "trauma_types": "看着至亲死去/被最信任的人背叛/因无力而失去一切，三种前世创伤各有心理特征",
            "behavioral_symptoms": "对前世的失去/仇人保持执念；对现在的人过度保护或刻意疏远；无法相信某些人",
            "healing_arc": "真正的重生叙事不只是「我要改变命运」，还要处理「带着前世的自己如何活在当下」",
            "present_vs_past": "现在的人和前世认识的「那个人」不完全一样——主角必须分开对待",
            "identity_risk": "若主角完全活在前世的执念里，重生就是另一种形式的困在时间里",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm("重生主角心理创伤叙事研究")],
        tags=["重生", "主角", "创伤", "心理"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="重生",
        slug="reborn-pp-prevent-disaster",
        name="阻止灾难倒计时结构",
        narrative_summary="主角知道某个重大灾难/悲剧的时间，必须在倒计时中完成准备，但准备过程中不断遭遇意外阻碍和信息更新",
        content_json={
            "deadline_structure": "清晰的时间压力（前世X月发生了Y），每章都在确认/更新倒计时",
            "obstacle_types": "前世因果早于预期引爆/新变量出现（不在前世记忆中的人）/自身实力跟不上计划",
            "information_crisis": "越接近灾难日期，越发现前世记忆有漏洞——真正的威胁可能比记忆中更复杂",
            "cost_of_rushing": "为了赶上时间线而做的某些决策损害了某些关系，救了大局却失去了小处",
            "climax": "灾难发生了，但不是前世的方式——主角必须在陌生的形式下用已有的准备应对",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm("重生题材倒计时叙事结构分析")],
        tags=["重生", "情节", "倒计时", "结构"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre="重生",
        slug="reborn-ea-survivor-guilt",
        name="重生者的幸存者内疚弧",
        narrative_summary="前世所有人都死了，只有我活着（重生了）——这种幸存者内疚感是重生叙事最常被忽视的情感资源",
        content_json={
            "guilt_source": "前世他人的死亡和自己的幸存并行，「为什么是我」的问题无法回避",
            "survivor_drive": "内疚转化为「不能再让这些人死」的驱动力，但这是健康的转化还是对自己的惩罚？",
            "relationship_complication": "用前世对一个人的内疚来对待今世的他，是否公平？对方感受得到这种重量",
            "resolution": "接受自己无法拯救所有人，选择为自己活而不只是为赎罪活",
        },
        source_type=L, confidence=0.72,
        source_citations=[llm("重生幸存者叙事情感弧研究")],
        tags=["重生", "情感弧", "内疚", "心理"],
    ),

    # =========================================================================
    # 机甲 / 星战 — 硬科幻战争（有别于软科幻空间歌剧）
    # =========================================================================
    MaterialEntry(
        dimension="world_settings", genre="机甲",
        slug="mecha-ws-pilot-caste",
        name="机甲驾驶员精英阶层",
        narrative_summary="神经同步率决定能否驾驶高级机甲，驾驶员是稀缺战略资源，形成独特的精英地位与心理创伤双重结构",
        content_json={
            "pilot_selection": "神经同步阈值是先天的，精英驾驶员不可大量培训——稀缺性创造价值",
            "psychological_cost": "与机甲高度同步意味着战斗损伤部分传导为心理伤害",
            "social_contradiction": "驾驶员是英雄也是消耗品——军方珍视其战斗价值但对其心理健康漠视",
            "peer_bond": "队友的战死通过神经同步传递为「直接感受到的死亡」，超过普通战友情谊的创伤",
            "unique_intimacy": "双座/多人同步机甲创造了极度亲密的精神连接，催生不可替代的人际关系",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm("机甲驾驶员社会学叙事分析")],
        tags=["机甲", "世界观", "驾驶员", "精英"],
    ),
    MaterialEntry(
        dimension="power_systems", genre="机甲",
        slug="mecha-ps-mecha-specs",
        name="机甲等级与战力体系",
        narrative_summary="机甲不是单一工具，而是武器/载体/战略单元的集合——等级体系应该反映技术代差和战术用途差异",
        content_json={
            "tier_logic": "一代机（量产通用）→二代机（改装专项）→三代机（实验原型）→传说机（远古/外星技术）",
            "tech_differentiation": "近战型/远程型/支援型/侦查型，不同类型各有战场定位，没有全能机甲",
            "pilot_tech_synergy": "机甲等级高不代表一定赢——驾驶技术和机甲设计的「匹配度」比单项数值更重要",
            "power_source": "动力系统（核聚变/反物质/灵能晶石）决定战斗时间和风险——反物质最强但爆炸最危险",
            "design_principle": "好的机甲体系：每个等级都有自己的叙事优势，主角的机甲有鲜明特征而非全面超越",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm("机甲力量体系设计研究")],
        tags=["机甲", "力量体系", "机甲设计", "战力"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="机甲",
        slug="mecha-pp-war-escalation",
        name="星际战争升级弧",
        narrative_summary="从局部冲突到星际大战的叙事升级，每个阶段有不同的叙事焦点：个人战→小队战→战役战→星际战",
        content_json={
            "phase1_personal": "驾驶员主角的个人战斗，建立角色能力和性格",
            "phase2_tactical": "小队配合，展现战术层面，开始理解更大局势",
            "phase3_strategic": "参与战役级别决策，主角意见影响战争走向，开始承担非战斗的责任",
            "phase4_political": "战争不只是军事，是政治延伸——主角面对「正确的战争策略」和「道德正确的选择」的冲突",
            "escalation_cost": "每级升级都意味着主角要学习新规则、面对新类型的损失",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm("机甲星际战争叙事升级结构研究")],
        tags=["机甲", "情节", "战争", "升级"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="机甲",
        slug="mecha-tm-humanity-machine",
        name="人与机械融合主题",
        narrative_summary="机甲叙事核心：人类用机器延伸力量，但延伸到什么程度还是「人」——神经同步越深入，人的边界越模糊",
        content_json={
            "identity_boundary": "当机甲损毁感觉像自身受伤，当机甲被摧毁感觉像死亡——身体边界在哪里",
            "enhancement_vs_loss": "能力提升的代价是否包含某种人性的让渡",
            "machine_personhood": "高度自主AI机甲是否有人格？主角与其的关系是工具还是伴侣",
            "narrative_question": "如果意识可以完全上传到机甲，那个存在还是「人」吗？",
            "thematic_resolution": "「人」不由身体构成，而由选择、关系和责任构成——即使在机甲里也可以完整地是人",
        },
        source_type=L, confidence=0.72,
        source_citations=[llm("机甲人机融合哲学主题研究")],
        tags=["机甲", "主题", "人机", "身份"],
    ),

    # =========================================================================
    # 校 园 / 青春 — 现实感校园叙事
    # =========================================================================
    MaterialEntry(
        dimension="world_settings", genre="校园",
        slug="campus-ws-school-ecosystem",
        name="校园权力生态",
        narrative_summary="学校不是法外之地，是一个缩小版的社会——人气/学业/家庭背景构成不同的权力资本，各种资本之间有转化关系",
        content_json={
            "social_capital": "学业成绩/体育明星/人气社交/家庭背景，四种资本各有通货领域",
            "peer_pressure_system": "校规是正式权力；同伴压力是更真实的非正式权力",
            "teacher_power": "老师的偏爱/忽视/打压对学生的社会地位有直接影响",
            "class_reproduction": "家庭背景如何在校园内以「补课/资源/人脉」的形式继续复制阶层",
            "bullying_ecology": "霸凌不只是暴力，更多是孤立/信息操控/资源剥夺——复杂得多",
        },
        source_type=L, confidence=0.67,
        source_citations=[llm("校园社会生态叙事研究"), wiki("校园欺凌", "https://zh.wikipedia.org/wiki/校園霸凌")],
        tags=["校园", "世界观", "社会", "权力"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="校园",
        slug="campus-ca-underdog-genius",
        name="低调天才学生",
        narrative_summary="真实能力被某种原因掩盖的学生，不是表演普通，而是有深层原因不愿显露——背后的故事比才能本身更有意思",
        content_json={
            "hiding_reason": "家庭创伤（曾因才华引来嫉妒/迫害）/个人选择（不相信才能能带来幸福）/保护某人",
            "reveal_catalyst": "被逼到不得不展示的情境，或者遇到一个让他/她觉得「在这个人面前不用装」的人",
            "social_cost": "长期低调导致的社交结构难以改变，即使真相暴露，旧的社交定位也会惯性持续",
            "internal_conflict": "真实的自己 vs 被允许展示的自己，两者的距离是心理健康指标",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm("校园题材低调主角叙事设计研究")],
        tags=["校园", "主角", "天才", "隐藏"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre="校园",
        slug="campus-ea-first-love",
        name="初恋情感弧",
        narrative_summary="初恋的叙事价值不在于结局，而在于它对「什么是感情」的第一次真实定义——不一定幸福，但一定改变了人",
        content_json={
            "emotional_first": "第一次感受到「想见到某人」「在乎某人的看法」「舍不得结束对话」",
            "awkwardness": "初恋的真实感来自笨拙：说错话、做出奇怪的决定、不知道边界在哪里",
            "intensity": "初恋往往被感觉「世界上最重要的事」，这种强度不是幼稚而是真实",
            "transformation": "不管结局，初恋之后的人对「在乎什么」有了更清晰的答案",
            "melancholy": "初恋的叙事张力之一：知道它总会以某种方式结束，但不知道怎么结束",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm("初恋叙事情感弧设计研究")],
        tags=["校园", "情感弧", "初恋", "成长"],
    ),

    # =========================================================================
    # 女 尊 — 性别颠倒权力叙事
    # =========================================================================
    MaterialEntry(
        dimension="world_settings", genre="女尊",
        slug="matro-ws-matriarchy-logic",
        name="女尊社会内在逻辑",
        narrative_summary="女尊设定的意义不在于「把男女位置互换」，而在于通过镜像揭示现实性别结构的机制，有自洽的社会逻辑",
        content_json={
            "power_basis": "女性力量的来源必须有世界内的合理解释（灵根/体力/生育控制/法律历史）",
            "male_position": "男性的社会角色需要完整设定，不能只是「弱化版女性」",
            "institutional_support": "哪些制度维持了女尊结构（继承法/婚姻法/科举/军队）",
            "internal_diversity": "女尊社会内部也有利益分化，不是铁板一块的女性同盟",
            "narrative_depth": "好的女尊叙事：读者可以从中读出现实权力结构的映射，而不只是性别游乐场",
        },
        source_type=L, confidence=0.70,
        source_citations=[wiki("母系社会", "https://zh.wikipedia.org/wiki/母系社会"), llm("女尊叙事世界观设计研究")],
        tags=["女尊", "世界观", "权力", "性别"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="女尊",
        slug="matro-ca-male-lead",
        name="女尊世界的男主",
        narrative_summary="在女尊社会中有自我意识的男性主角或CP——不是「等待女主拯救的被动存在」，而是在受限结构中有真实能动性的人",
        content_json={
            "agency_within_limits": "在现有结构内找到真实的影响力——知识/情感智识/独特技能",
            "internalized_oppression": "在女尊社会出生的男性会有多少内化了的「男性应该如何」——这是真实的心理层次",
            "resistance_types": "主动抵抗（挑战规则）/消极抵抗（保持内心空间）/顺从（精明的生存策略）",
            "relationship_dynamics": "与女主的关系不是「被照顾」vs「照顾」，而是不同条件下的平等协作",
            "narrative_value": "男主应该让读者理解「这个社会结构对他的影响是真实的，但他不只是受害者」",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm("女尊题材男性角色能动性设计研究")],
        tags=["女尊", "配角", "男主", "能动性"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="女尊",
        slug="matro-tm-gender-mirror",
        name="性别镜像主题",
        narrative_summary="女尊叙事的核心价值：通过颠倒，让习以为常的性别权力结构变得可见——镜像不是批判，而是让读者「看到」",
        content_json={
            "defamiliarization": "将「理所当然的事」通过颠倒变陌生，读者才能看见原来看不见的东西",
            "specific_mirrors": ["女性为了事业不婚被赞美↔男性为了事业不婚被批评", "男性被要求温柔顺从↔现实中女性的对应", "男性美德是配合↔现实中女性的刻板期待"],
            "not_utopia": "好的女尊叙事不应该只是「女性很爽」——女尊社会本身也有问题，权力本身是问题不分性别",
            "reader_takeaway": "读完女尊，读者对现实的性别结构有了新的看见角度",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm("女尊叙事主题分析研究")],
        tags=["女尊", "主题", "性别", "镜像"],
    ),

    # =========================================================================
    # 灵 异 / 鬼 怪 — 中式恐怖/民俗恐怖
    # =========================================================================
    MaterialEntry(
        dimension="world_settings", genre="灵异",
        slug="ghost-ws-yin-yang-border",
        name="阴阳边界世界观",
        narrative_summary="阴间与阳间之间有一层可以渗透的薄膜——某些地方/某些人天生处于边界上，成为鬼怪世界的天然通道",
        content_json={
            "border_locations": "阴气聚集地：古战场/刑场遗址/长期无人居住的宅子/水边（溺死者聚集）",
            "liminal_persons": "天生阴眼者/死而复生者/命中注定孤阴纯阳的人，是连接两界的人形节点",
            "ghost_ecology": "怨鬼（强情绪）/守尸鬼（场地依附）/孤魂野鬼（无归处）/神级存在，不同类型行为逻辑各异",
            "rules": "阴兵过境不能看/子时前必须回家/中元节规矩，民俗规则是世界观的叙事密度来源",
        },
        source_type=L, confidence=0.68,
        source_citations=[wiki("中元节", "https://zh.wikipedia.org/wiki/中元節"), wiki("阴阳", "https://zh.wikipedia.org/wiki/陰陽"), llm("中式灵异世界观体系研究")],
        tags=["灵异", "世界观", "阴阳", "民俗"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="灵异",
        slug="ghost-ca-spirit-sensitive",
        name="见鬼者主角",
        narrative_summary="天生能看见鬼怪的人——「特殊能力」并不是礼物，而是把他/她永久隔绝在「常人」之外的诅咒",
        content_json={
            "isolation": "从小看见别人看不见的东西，被认为精神有问题或奇怪，社交隔离",
            "desensitization": "长期接触鬼怪后如何应对？麻木/建立规则/学会区分/特定情境下失控",
            "relationships_with_ghosts": "与某些鬼怪建立了复杂关系——同情/合作/恐惧的混合",
            "key_question": "这个能力是诅咒还是使命？主角的选择决定了故事的基调",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm("灵异题材见鬼主角设计研究")],
        tags=["灵异", "主角", "见鬼", "孤立"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre="灵异",
        slug="ghost-rwr-chinese-folklore",
        name="中国鬼怪民俗知识库",
        narrative_summary="中式灵异的知识底层：聊斋志异/搜神记/各省民间传说，不同地区的鬼怪信仰差异巨大，是叙事差异化的宝库",
        content_json={
            "classic_texts": "《聊斋志异》（文人笔记最完整的鬼怪体系）/《搜神记》（六朝志怪）/《阅微草堂笔记》",
            "regional_differences": "南方水鬼（江湖溺鬼）/北方宅鬼（四合院积年）/西南鬼（苗疆蛊毒）/东北萨满",
            "ghost_types_by_death": "溺死→水鬼（索命型）/冤死→怨鬼（执念型）/自杀→徘徊鬼（求解型）/无嗣→孤鬼（依附型）",
            "protective_methods": "符咒/桃木/镜子/五黄/糯米/公鸡血，各有真实民俗来源",
            "activation_keywords": ["阴眼", "孤魂野鬼", "七月半", "招魂", "夺舍", "鬼打墙", "午夜阴兵"],
        },
        source_type=L, confidence=0.80,
        source_citations=[
            wiki("聊斋志异", "https://zh.wikipedia.org/wiki/聊齋志異"),
            wiki("中元节", "https://zh.wikipedia.org/wiki/中元節"),
            ref("蒲松龄《聊斋志异》中的鬼怪体系研究"),
        ],
        tags=["灵异", "参考", "民俗", "鬼怪"],
    ),

    # =========================================================================
    # 赛 博 朋 克 — 技术失控 × 阶层固化
    # =========================================================================
    MaterialEntry(
        dimension="world_settings", genre="赛博朋克",
        slug="cyber-ws-corpo-dystopia",
        name="巨型企业统治城市",
        narrative_summary="政府形同虚设，超级企业（Megacorp）实际控制一切——城市是企业运营的私有财产，个人是消耗品",
        content_json={
            "corporate_power": "企业有私人军队/司法体系/医疗/教育，用就业合同替代公民身份",
            "city_stratification": "超高层（企业精英）→中层（有用的工人）→地面层（边缘人）→地下（完全遗弃的人）",
            "surveillance": "基因档案/神经记录/购买历史/社交数据，数据是企业比政府更精准的控制工具",
            "resistance_spaces": "企业覆盖不到的缝隙：破产区/EMP阴影区/古老的线下社区",
        },
        source_type=L, confidence=0.70,
        source_citations=[wiki("赛博朋克", "https://zh.wikipedia.org/wiki/賽博龐克"), llm("赛博朋克世界观社会结构分析")],
        tags=["赛博朋克", "世界观", "企业", "反乌托邦"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="赛博朋克",
        slug="cyber-ws-body-mod-culture",
        name="义体改造文化",
        narrative_summary="身体改造是赛博朋克的文化核心：以机械替换生物组织是选择、是身份认同、是阶层标志，也是「人」的定义边界",
        content_json={
            "modification_spectrum": "功能性（视力提升/臂力增强）→身份性（特定企业改造标志）→极端（更换大部分身体）",
            "social_meaning": "高端义体=财富和地位；过度改造=人文主义者眼中的堕落；特定改造=特定圈子的归属符号",
            "cyber_psychosis": "过度改造导致人格解体的神经崩溃——「人性」需要生物底层支撑的理论",
            "身份_question": "换掉90%的身体还是同一个人吗？这个问题在赛博世界是日常，而不是哲学课",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm("赛博朋克义体文化设计研究")],
        tags=["赛博朋克", "世界观", "义体", "身份"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="赛博朋克",
        slug="cyber-tm-humanity-in-machine",
        name="技术压迫下的人性主题",
        narrative_summary="赛博朋克的核心主题不是科技本身，而是「当科技成为压迫工具时，人如何保留人性」",
        content_json={
            "technology_as_control": "技术让监控无处不在/让记忆可以被删除/让身体可以被远程关闭",
            "resistance_forms": "模拟物（真正的纸书/真正的食物）的亚文化价值；不被数据化的行为是抵抗",
            "authentic_connection": "在数字虚假泛滥的世界里，真实的人际接触变得珍贵和危险",
            "rebel_question": "主角的反抗是为了「自由」还是为了「成为另一种控制」——反抗者夺权之后会变成什么",
            "dystopian_hope": "好的赛博朋克不给乌托邦答案，但让某个微小的真实连接照亮黑暗",
        },
        source_type=L, confidence=0.73,
        source_citations=[wiki("反乌托邦", "https://zh.wikipedia.org/wiki/反烏托邦"), llm("赛博朋克主题研究")],
        tags=["赛博朋克", "主题", "人性", "抵抗"],
    ),

    # =========================================================================
    # 美 食 / 厨 神 — 烹饪叙事
    # =========================================================================
    MaterialEntry(
        dimension="world_settings", genre="美食",
        slug="food-ws-culinary-competition",
        name="美食竞技世界",
        narrative_summary="以美食为竞技的世界：厨艺不只是技术，是地位/荣誉/哲学的综合体现；比赛是人生观的直接碰撞",
        content_json={
            "competition_structure": "食材产地/处理技法/味道设计/摆盘美学/食客感受，多维度评分复杂化单纯的「好吃」评判",
            "judge_ecology": "评委有各自的流派偏好和利益立场，「客观评分」并不存在",
            "philosophy_clash": "传统派vs创新派/地方特色vs国际化/「为食客做菜」vs「为自己的艺术做菜」",
            "pressure_narrative": "比赛现场的时间压力+食材意外+对手的心理战，多线压力并行",
        },
        source_type=L, confidence=0.67,
        source_citations=[llm("美食竞技叙事世界观研究")],
        tags=["美食", "世界观", "竞技", "厨艺"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="美食",
        slug="food-st-cooking-revelation",
        name="料理顿悟时刻场景",
        narrative_summary="厨师在制作过程中突然「领悟」的场景——不是单纯技法提升，而是通过食材/味道理解了某种人生道理",
        content_json={
            "sensory_trigger": "一种气味/一种口感/食材的状态，触发了超越技术层面的联想",
            "memory_connection": "料理的味道连接了某段记忆——为谁而做的记忆激活了新的理解",
            "philosophical_insight": "食材的时令/食物的易逝/味道的主观性，都可以成为领悟的内容",
            "physical_expression": "顿悟时的身体反应：手变稳了/呼吸变化/时间感变慢",
            "result": "这份菜因为顿悟而与之前完全不同，食客感受得到这种不同（即使说不清楚为什么）",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm("美食叙事顿悟场景写作研究")],
        tags=["美食", "场景", "顿悟", "感官"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre="美食",
        slug="food-rwr-culinary-knowledge",
        name="中国烹饪知识体系参考",
        narrative_summary="美食题材的知识底库：中国八大菜系/食材产地与时令/烹饪哲学（《随园食单》）/食材-情绪对应关系",
        content_json={
            "eight_cuisines": "川（麻辣鲜香）/粤（清淡鲜甜）/鲁（醇厚咸鲜）/苏（精细软糯）/浙（清秀鲜嫩）/闽（清鲜甘醇）/湘（香辣酸醇）/皖（重油重色）",
            "seasonal_ingredients": "二十四节气对应时令食材：春韭/夏莲/秋蟹/冬羊，越应季越鲜美",
            "culinary_philosophy": "《随园食单》的核心：「厨者之道，调和鼎鼐，不在豪奢，在精在真」",
            "taste_psychology": "酸→分泌唾液激发食欲；甜→慰藉情绪；苦→成熟复杂感；鲜（谷氨酸）→满足感/幸福感",
            "activation_keywords": ["鲜味", "火候", "收汁", "腌制", "发酵", "时令", "上汤", "清蒸原味"],
        },
        source_type=L, confidence=0.80,
        source_citations=[
            wiki("中国菜", "https://zh.wikipedia.org/wiki/中國菜"),
            ref("袁枚《随园食单》：清代饮食美学经典"),
            wiki("二十四节气", "https://zh.wikipedia.org/wiki/二十四节气"),
        ],
        tags=["美食", "参考", "菜系", "烹饪哲学"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="美食",
        slug="food-tm-food-as-love",
        name="食物作为爱的表达主题",
        narrative_summary="美食叙事最动人的主题层：无法用语言表达的感情通过一道菜传递——「为谁做菜」比「做什么菜」更重要",
        content_json={
            "love_language": "为不善言辞的人，做饭是最真实的关心表达方式",
            "memory_carrier": "一道菜可以携带整段关系的记忆——做出「和记忆中一模一样的味道」是叙事高潮",
            "sacrifice_in_cooking": "知道对方的口味/特地寻找特定食材/在疲惫时仍然做饭，都是无声的爱",
            "receiving_side": "懂得感受到这种爱的人，比发出爱的人更是故事核心",
            "bitter_sweet": "当做饭的人不在了，那道菜的味道成为无法复刻的遗憾",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm("美食叙事情感主题研究")],
        tags=["美食", "主题", "爱", "记忆"],
    ),
]


async def seed_library(dry_run: bool = False, filter_genre: str | None = None) -> None:
    entries = SEED_DATA
    if filter_genre:
        entries = [e for e in entries if e.genre == filter_genre]
    print(f"{'[DRY RUN] ' if dry_run else ''}Seeding {len(entries)} entries...\n")
    by_g: dict[str, int] = {}
    by_d: dict[str, int] = {}
    for e in entries:
        by_g[e.genre or "NULL"] = by_g.get(e.genre or "NULL", 0) + 1
        by_d[e.dimension] = by_d.get(e.dimension, 0) + 1
    print(f"By genre:     {dict(sorted(by_g.items()))}")
    print(f"By dimension: {dict(sorted(by_d.items()))}\n")
    if dry_run:
        return
    errors = 0
    async with session_scope() as session:
        for e in entries:
            try:
                await insert_entry(session, e, compute_embedding=True)
            except Exception as ex:
                print(f"  ✗ {e.slug}: {ex}")
                errors += 1
        await session.commit()
    print(f"✓ {len(entries) - errors} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--genre", default=None)
    args = ap.parse_args()
    asyncio.run(seed_library(args.dry_run, args.genre))
