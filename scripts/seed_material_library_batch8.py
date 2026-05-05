"""
Batch 8: power_systems expansion (only 8 genres covered → target 15+),
character_templates for 5 more genres, device_templates for 4 more genres,
and bulk-up of thin genres: 赛博朋克/女尊/校园/灵异/美食/快穿.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bestseller.infra.db.session import session_scope
from bestseller.services.material_library import insert_entry, MaterialEntry


def wiki(title: str, note: str = "") -> dict:
    return {"type": "wikipedia", "title": title, "note": note}

def llm_note(note: str) -> dict:
    return {"type": "llm_synth", "note": note}


ENTRIES = [
    # ═══════════════════════════════════════════════════════════════
    # POWER SYSTEMS — 扩展到更多题材
    # ═══════════════════════════════════════════════════════════════

    MaterialEntry(
        dimension="power_systems", genre="武侠",
        slug="wuxia-ps-inner-energy-tiers",
        name="内力/真气等阶体系",
        narrative_summary="武侠世界的核心能量系统：以内力深厚程度区分强弱，"
                          "分先天/后天两大门槛，武功招式需内力驱动才能发挥真正威力。",
        content_json={
            "tier_structure": "普通人→后天武者（九重）→先天（化气入脉）→先天极境（天人感应）",
            "energy_type": "内力/真气/罡气/剑气",
            "cultivation_method": "功法+苦练+奇遇（奇书/圣药/名师）",
            "hard_ceiling": "先天之境需要天赋+机缘，非纯努力可达",
            "combat_expression": "掌力/剑气/暗劲——同样招式因内力高下有天壤之别",
            "Sanderson_rule": "软魔法体系：规则模糊，上限感知而非精确数值",
            "activation_keywords": ["内力", "真气", "先天", "后天", "内劲", "罡气"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("内力", "武侠"), llm_note("武侠能量体系分析")],
        tags=["武侠", "内力", "修炼", "力量体系"],
    ),
    MaterialEntry(
        dimension="power_systems", genre="历史",
        slug="hist-ps-social-capital",
        name="历史小说的权力等阶",
        narrative_summary="历史背景下的「实力」不是武力而是社会资本：爵位、官职、钱财、家族网络、皇帝信任度。"
                          "主角的「升级」轨迹是在这五维坐标上同步推进，任一维度崩塌都会触发危机。",
        content_json={
            "power_dimensions": ["爵位/官职（制度性权力）", "财富（经济杠杆）", "家族/宗族网络", "皇帝/太后信任度", "民心/声望"],
            "upgrade_triggers": "立功/联姻/献计/铲除政敌",
            "downgrade_triggers": "遭弹劾/失圣心/家族出事/政敌反击",
            "unique_mechanic": "历史权力是零和的：你上升意味着别人下降，制造天然对立",
            "activation_keywords": ["官位晋升", "爵位", "朝中势力", "圣心", "权谋升级"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[wiki("中国封建官制", ""), llm_note("历史权谋力量体系")],
        tags=["历史", "权谋", "社会资本", "力量体系"],
    ),
    MaterialEntry(
        dimension="power_systems", genre="宫斗",
        slug="palace-ps-favor-system",
        name="后宫宠爱等阶体系",
        narrative_summary="后宫以『宠爱程度』为核心升降指标：位分高低是表面，皇帝真实关注度才是实权。"
                          "伴随宠爱浮动的是财权/人脉/保护伞，失宠即是实质降级。",
        content_json={
            "tier_structure": "答应→贵人→嫔→妃→贵妃→皇贵妃→皇后",
            "actual_power_metric": "皇帝的晚膳时间/私下探望/赐物等级",
            "leverage_types": ["子嗣（最稳固）", "皇帝情感（最不稳定）", "家族势力（外部支柱）", "宫中人脉（内部网络）"],
            "power_shift_triggers": "怀孕/失子/家族立功或落败/竞争者被打压",
            "activation_keywords": ["宫位", "宠爱", "圣眷", "皇帝专宠", "后宫排位"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("清朝后宫制度", ""), llm_note("宫斗权力体系")],
        tags=["宫斗", "后宫", "宠爱", "力量体系"],
    ),
    MaterialEntry(
        dimension="power_systems", genre="末日",
        slug="apoc-ps-survival-hierarchy",
        name="末日资源与觉醒等阶",
        narrative_summary="末日小说双轨力量体系：普通人靠物资/枪械/组织；觉醒者拥有异能。"
                          "两轨并行但相互影响——高觉醒者若无资源支撑同样脆弱，普通人组织可围杀单个强者。",
        content_json={
            "track_1_普通人": "枪械武装→战术训练→物资控制→组织规模",
            "track_2_觉醒者": "觉醒等级E→D→C→B→A→S（每级质变）",
            "interaction": "S级觉醒者可独扛一支军队，但需大量食物/休息/不能长期单打独斗",
            "upgrade_triggers": "危机中的意志突破 / 进化液 / 高级丧尸/变异体晶核",
            "hard_ceiling_感": "S级以上的『神级觉醒』是传说，是主角终局目标",
            "activation_keywords": ["觉醒等级", "异能", "E级觉醒者", "晋级", "末日能力"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[llm_note("末日小说异能体系分析")],
        tags=["末日", "觉醒", "异能", "力量等级"],
    ),
    MaterialEntry(
        dimension="power_systems", genre="言情",
        slug="rom-ps-social-attraction",
        name="言情的社会地位与情感吸引力体系",
        narrative_summary="言情小说的「实力」是多维度的：社会地位/财富/外貌/情感成熟度。"
                          "主角的成长轨迹往往是某一维度的弥补——穷但自尊心强；美但不懂爱自己。",
        content_json={
            "dimensions": ["经济独立性", "社会地位", "外貌魅力", "情感智商（EQ）", "职业成就"],
            "romance_tension_source": "男女主某一维度的不对等制造张力",
            "growth_arc": "弱势维度的成长 + 强势方被强势方反向感化",
            "genre_specific": "商业言情强调经济落差；文艺言情强调情感成熟度落差",
            "activation_keywords": ["阶层差异", "灰姑娘", "总裁文", "逆袭", "情感成长"],
        },
        source_type="llm_synth", confidence=0.70,
        source_citations=[llm_note("言情小说叙事结构分析")],
        tags=["言情", "社会地位", "吸引力", "成长"],
    ),
    MaterialEntry(
        dimension="power_systems", genre="悬疑",
        slug="susp-ps-information-power",
        name="悬疑的信息权力等级",
        narrative_summary="悬疑小说中的「实力」就是信息掌握量：谁知道的多谁就处于优势。"
                          "推理过程是从信息劣势向信息等同甚至信息优势的攀升，凶手一旦丧失信息优势便落败。",
        content_json={
            "tier_structure": "完全无知→碎片线索→部分真相→全貌重建→凶手认罪",
            "power_reversal": "侦探最初信息最少，凶手信息最多；结尾时完全反转",
            "special_abilities": "观察力/逻辑推演/情报网络/技术工具",
            "tension_mechanic": "侦探每获得一块新信息，凶手也在行动（销毁证据/施压）",
            "activation_keywords": ["线索", "真相", "信息量", "证据", "推理"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("悬疑推理力量结构分析")],
        tags=["悬疑", "信息", "推理", "力量体系"],
    ),
    MaterialEntry(
        dimension="power_systems", genre="游戏",
        slug="game-ps-level-system",
        name="游戏世界等级与职业体系",
        narrative_summary="游戏类小说直接使用RPG数值系统：等级/属性/技能树/职业天赋。"
                          "主角往往选择了被系统低估的职业/属性走向，通过异常成长路径碾压标准流派。",
        content_json={
            "standard_structure": "1级→满级（通常99/100级）→觉醒→超越上限",
            "class_system": "战士/法师/弓手/牧师标准四职 + 稀有隐藏职业",
            "unique_build": "主角的非主流加点/职业选择是差异化核心",
            "stat_types": ["力量/敏捷/智力/体力/魅力/运气"],
            "卡级天花板": "某些属性或境界的自然上限打破是关键情节点",
            "activation_keywords": ["等级", "职业", "属性", "天赋", "技能树", "隐藏职业"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[llm_note("游戏类小说系统设计"), wiki("角色扮演游戏", "等级")],
        tags=["游戏", "等级", "职业", "RPG"],
    ),
    MaterialEntry(
        dimension="power_systems", genre="赛博朋克",
        slug="cyber-ps-augmentation-tiers",
        name="义体改造等级体系",
        narrative_summary="赛博朋克的实力来自义体改造深度：从皮肤传感器到神经接入到全身替换。"
                          "改造越深越强大，但人性流失风险（失常症/硅化）也越高——这是该体裁的核心代价设计。",
        content_json={
            "tier_structure": "皮肤级（外部改装）→器官级（内置）→神经级（脑机接口）→人机融合级",
            "humanity_cost": "改造越多，情绪感知越弱，可能触发「人格失常」",
            "economic_dimension": "顶级义体极贵，是阶级差异的物化表现",
            "special_limits": "EMP武器可瞬间废掉高度义体化者",
            "activation_keywords": ["义体", "改造", "脑机接口", "人机融合", "失常症"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[wiki("赛博格", "赛博朋克"), llm_note("赛博朋克力量体系")],
        tags=["赛博朋克", "义体", "改造", "力量体系"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # CHARACTER TEMPLATES — 5 new genres
    # ═══════════════════════════════════════════════════════════════

    MaterialEntry(
        dimension="character_templates", genre="种田",
        slug="farm-ct-归园者",
        name="厌倦都市的归园者",
        narrative_summary="在城市遭遇挫折（职场/感情/健康）后回到农村/小镇的主角，"
                          "初期满是不适和挫败，在与土地/植物/动物的相处中逐渐重建内心秩序。",
        content_json={
            "backstory": "都市精英/打工人遭遇转折点",
            "initial_state": "身心疲惫 / 对乡村充满偏见但无路可退",
            "growth_medium": "农业劳动本身是治愈介质：节律/成果可见/自然回应",
            "conflict_source": "乡村人情网络的复杂 / 现代与传统的冲突 / 土地权益",
            "relationship_arc": "从外来者→被接纳者→共同体一员",
            "activation_keywords": ["返乡", "种田", "治愈", "归园田居", "远离城市"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("种田小说主角原型")],
        tags=["种田", "治愈", "归园", "成长"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="娱乐圈",
        slug="ent-ct-过气明星",
        name="东山再起的过气明星",
        narrative_summary="曾经红过但因某事件（绯闻/事故/被雪藏）跌落，沉寂多年后试图东山再起，"
                          "面对的不只是新人竞争，更是公众对「过气」标签的集体偏见。",
        content_json={
            "fall_reason": "行业打压/丑闻/被队友或公司背刺",
            "comeback_motivation": "证明自己 / 完成未竟之事 / 为曾支持自己的人",
            "unique_advantage": "过气期沉淀的真实演技/创作力",
            "obstacle": "圈内人脉断裂 / 资本不信任 / 黑粉持续攻击",
            "tension_core": "复出之路比当年出道更难，因为更老更有故事",
            "activation_keywords": ["翻红", "东山再起", "过气明星", "复出", "娱乐圈翻身"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("娱乐圈主角类型分析")],
        tags=["娱乐圈", "过气", "复出", "励志"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="都市",
        slug="urban-ct-隐世高手",
        name="隐居都市的隐世高手",
        narrative_summary="拥有极强实力（武学/医术/商业/修炼）却刻意低调生活在普通都市环境中，"
                          "被卷入事件后不得不展露能力——这种「实力不得不显现」的结构是都市爽文的基本句型。",
        content_json={
            "hidden_power": "绝世武功/顶级医术/庞大财富/修炼境界",
            "cover_identity": "外卖员/超市员工/保安/普通教师",
            "reveal_trigger": "保护重要的人 / 面对欺凌而爆发",
            "relationship_dynamic": "身边人逐渐发现真相的过程就是关系深化的过程",
            "genre_specific": "都市文的「低开高走」结构——越平凡的起点越爽",
            "activation_keywords": ["隐世高手", "藏拙", "低调", "都市高手", "真实身份"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("都市隐世高手原型分析")],
        tags=["都市", "隐世", "高手", "爽文"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="游戏",
        slug="game-ct-first-player",
        name="游戏世界第一玩家",
        narrative_summary="在游戏世界中率先达到某个里程碑（第一个满级/第一个通关副本/第一个发现隐藏内容），"
                          "因此获得信息优势和追随者，同时成为竞争对手的打压目标。",
        content_json={
            "power_source": "信息先发优势 + 对游戏机制的深度理解",
            "social_dynamic": "被追随（利用）+ 被针对（嫉妒）",
            "narrative_utility": "他的存在给其他玩家提供参照目标，也给读者暗示世界规则",
            "arc_options": ["保持第一的压力", "被后来者超越的转变", "发现游戏背后的真相"],
            "activation_keywords": ["第一名", "游戏排行榜", "隐藏内容", "服务器第一"],
        },
        source_type="llm_synth", confidence=0.71,
        source_citations=[llm_note("游戏类小说角色原型")],
        tags=["游戏", "排名", "竞争", "第一"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="快穿",
        slug="kuaichuan-ct-任务执行者",
        name="情感渐渗的任务执行者",
        narrative_summary="系统派遣的任务者初始以完成目标为唯一驱动，不投入情感，"
                          "但每个世界里的真实关系逐渐动摇这种超然——核心是「本不该有情感的存在被情感俘获」。",
        content_json={
            "initial_state": "高效/冷静/把所有NPC当棋子",
            "vulnerability": "某一类型的角色/情境会触发某个已经遗忘的核心记忆",
            "crack_in_armor": "第N个世界里，有人看穿了她的表演",
            "系统冲突": "系统不希望执行者产生情感 → 情感化与任务完成的对抗",
            "meta_mystery": "谁是她真正的「本体」？为什么要执行任务？",
            "activation_keywords": ["快穿任务", "攻略目标", "系统", "穿越", "情感觉醒"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[llm_note("快穿主角原型分析")],
        tags=["快穿", "系统", "任务", "情感觉醒"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # DEVICE TEMPLATES — 4 new genres
    # ═══════════════════════════════════════════════════════════════

    MaterialEntry(
        dimension="device_templates", genre="游戏",
        slug="game-dt-hidden-quest-item",
        name="隐藏任务触发物品",
        narrative_summary="游戏世界里看起来毫无价值的垃圾物品，实为某条隐藏史诗任务的钥匙，"
                          "触发后展开一段通常玩家不知道的完整剧情线——是叙事密度最高的道具类型。",
        content_json={
            "discovery_trigger": "特殊条件（特定地点+特定时间+特定角色携带）",
            "quest_type": "通常揭示游戏世界观最深层的设定",
            "reward": "超规格的强力道具 + 叙事满足感",
            "rarity": "服务器只有极少数玩家发现过",
            "narrative_function": "推动主角理解游戏/世界的真实本质",
            "activation_keywords": ["隐藏任务", "触发条件", "稀有道具", "彩蛋", "隐藏副本"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[llm_note("游戏道具叙事设计")],
        tags=["游戏", "隐藏任务", "道具", "触发"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="快穿",
        slug="kuaichuan-dt-system-panel",
        name="快穿系统面板",
        narrative_summary="快穿主角的核心道具：显示任务状态/攻略进度/世界值的系统界面。"
                          "面板本身的设计方式（冷漠的评分/隐藏的真实信息/系统的人格化）决定了整体基调。",
        content_json={
            "panel_elements": ["任务目标与完成度", "攻略对象好感度", "世界线稳定度", "奖励预告"],
            "dramatic_uses": ["面板提示的信息与实际情况不符", "面板突然失灵的危机", "隐藏选项出现"],
            "system_personality": "从纯机械→越来越像真实存在的系统人格化设计",
            "meta_function": "系统面板是作者控制叙事节奏的工具——何时显示、何时隐藏",
            "activation_keywords": ["系统面板", "任务进度", "攻略度", "系统提示"],
        },
        source_type="llm_synth", confidence=0.70,
        source_citations=[llm_note("快穿道具叙事设计")],
        tags=["快穿", "系统", "面板", "任务"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="无限流",
        slug="wuxian-dt-survival-kit",
        name="副本生存套件",
        narrative_summary="无限流玩家的标配物资包：基础药品/武器/情报本/换装道具。"
                          "开局物资的多少是玩家等级的直接体现，分配/交换/抢夺生存套件本身就是社交博弈场。",
        content_json={
            "standard_contents": ["急救包", "临时武器", "上个副本的情报残片", "伪装道具"],
            "rarity_tiers": "新人套件（基础）→老手套件（定制）→顶级玩家隐藏仓库",
            "conflict_source": "稀缺物资的分配/抢夺/黑市交易",
            "thematic_resonance": "人的备战心态折射在套件上——偏重武器的人 vs 偏重情报的人",
            "activation_keywords": ["生存物资", "副本道具", "急救包", "无限流装备"],
        },
        source_type="llm_synth", confidence=0.70,
        source_citations=[llm_note("无限流物资叙事设计")],
        tags=["无限流", "物资", "生存", "道具"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="赛博朋克",
        slug="cyber-dt-black-ice",
        name="黑冰防御程序",
        narrative_summary="赛博朋克世界中高价值数据库的防御AI：入侵者一旦触发即会反向攻击神经接口，"
                          "轻则昏迷重则脑死亡——是黑客角色最恐惧的存在，也是高风险入侵的核心张力来源。",
        content_json={
            "mechanism": "主动反击的防御程序，追踪入侵者的数字/神经连接",
            "danger_level": "从低级（数据销毁）到高级（意识抹杀）",
            "countermeasure": "专用破解程序/团队协作同时突破多个节点/物理断开",
            "narrative_use": "黑客任务的时间压力来源 / 队友死亡的高风险设定",
            "activation_keywords": ["黑冰", "防御程序", "入侵风险", "神经攻击", "赛博朋克"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("黑冰", "赛博朋克设定"), llm_note("赛博朋克道具设计")],
        tags=["赛博朋克", "黑客", "防御", "道具"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # THIN GENRES BULK-UP: 赛博朋克/女尊/校园/灵异/美食/快穿
    # ═══════════════════════════════════════════════════════════════

    # 赛博朋克 补充
    MaterialEntry(
        dimension="character_archetypes", genre="赛博朋克",
        slug="cyber-ca-corpo-defector",
        name="叛逃的企业精英",
        narrative_summary="原本是企业高层的执行者，因为看到了太多企业的黑暗面而出逃，"
                          "带走了关键数据或秘密，成为企业追杀目标，与底层黑客结成不安的联盟。",
        content_json={
            "prior_life": "企业律师/研究员/安全主管——曾是体制受益者",
            "defection_trigger": "亲历某个无法忽视的罪行（人体实验/意识抹杀/屠村）",
            "asset": "企业内部数据/关系网络/技术知识",
            "liability": "无法完全信任底层黑客 / 旧有精英习惯影响生存能力",
            "arc": "从旁观者到参与者到承担者",
            "activation_keywords": ["企业叛逃", "内鬼", "赛博朋克", "黑客", "企业阴谋"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[llm_note("赛博朋克人物原型分析")],
        tags=["赛博朋克", "企业", "叛逃", "黑客"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="赛博朋克",
        slug="cyber-pp-data-heist",
        name="数字世界抢劫弧",
        narrative_summary="团队组建→目标确认→计划（不可能完成）→入侵执行（计划立刻偏轨）→逃脱/代价。"
                          "与实体抢劫区别在于战场在神经空间，危险是意识消亡而非肉体死亡。",
        content_json={
            "act_structure": "招募→踩点→入侵准备→执行→意外→抉择→结果",
            "unique_赛博_element": "现实行动与数字渗透同步进行的双线叙事",
            "tension_source": "黑冰防御/队友意识崩溃/内鬼/时间限制",
            "payload_options": ["解放被囚意识", "泄露企业罪证", "窃取研究数据", "抹去某人身份"],
            "activation_keywords": ["数字入侵", "网络抢劫", "黑客任务", "赛博盗窃"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("赛博朋克情节模式"), wiki("Cyberpunk", "fiction")],
        tags=["赛博朋克", "入侵", "情节", "团队"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre="赛博朋克",
        slug="cyber-lt-undercity",
        name="地下层城市",
        narrative_summary="赛博朋克城市垂直分层：上层阳光充足、义体精良、企业总部林立；"
                          "下层遮天蔽日、廉价义体、黑市横行、真实的人情冷暖。叙事往往发生在交界处或底层。",
        content_json={
            "vertical_layers": ["空中（精英阶层/企业区）", "地面（中产）", "地下（黑市/边缘人群）", "深地下（犯罪据点/旧基础设施）"],
            "atmosphere_markers": "霓虹灯/酸雨/漏水管道/廉价义体修理店/街头食物摊",
            "social_ecology": "底层居民互相依存 + 对上层的深切怨恨",
            "narrative_value": "底层场景展示人性最本质的一面：在极端贫困中仍然存在的善意",
            "activation_keywords": ["地下城", "贫民窟", "赛博朋克", "底层", "霓虹废土"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("赛博朋克", "城市设定"), llm_note("赛博朋克场景设计")],
        tags=["赛博朋克", "地下城", "贫民窟", "分层"],
    ),

    # 女尊 补充
    MaterialEntry(
        dimension="world_settings", genre="女尊",
        slug="nüzun-ws-matriarchal-logic",
        name="女尊社会的内在逻辑",
        narrative_summary="女尊世界的权力结构以女性为中心，但内在运行不是简单的性别反转，"
                          "而是建立在力量/生育/宗教/历史叙事等维度上的综合权力逻辑，与现实父权社会形成镜像对话。",
        content_json={
            "power_sources": ["体质/修炼优势（女性更适合）", "生育控制权在女方", "宗教叙事（女神崇拜）", "历史书写权"],
            "male_position": "类似现实中的女性地位——保护/供养/婚嫁由女方决定",
            "narrative_purpose": "通过镜像揭示现实社会性别权力结构的荒诞",
            "tensions": "男性主角适应系统 vs 反抗系统 / 女权既得利益者 vs 变革者",
            "activation_keywords": ["女尊", "母系社会", "性别反转", "女权", "权力镜像"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[wiki("母系社会", "人类学"), llm_note("女尊世界观构建")],
        tags=["女尊", "世界观", "性别", "权力"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="女尊",
        slug="nüzun-ca-submissive-rebel",
        name="表面顺从内心反叛的男主",
        narrative_summary="在女尊制度下被训练成温顺的男性主角，表面符合社会期待，"
                          "内心对自我价值有清醒认知，通过极度克制的方式一步步在规则内找到权力缝隙。",
        content_json={
            "social_expectation": "温柔/顺从/以家庭为重/不涉权力",
            "inner_reality": "高度自我意识/对不公有敏锐感知",
            "strategy": "表面顺从是保护自己的必要伪装",
            "growth_arc": "从伪装→找到突破口→用女主给予的空间真正展示自己",
            "thematic_resonance": "与现实社会中女性处境的隐喻对话",
            "activation_keywords": ["男主", "女尊", "隐忍", "反叛", "顺从与反抗"],
        },
        source_type="llm_synth", confidence=0.71,
        source_citations=[llm_note("女尊小说男性角色设计")],
        tags=["女尊", "男主", "反叛", "隐忍"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="女尊",
        slug="nüzun-pp-gender-reversal-arc",
        name="性别镜像觉醒弧",
        narrative_summary="女尊小说的核心情节结构：男主在经历系统性压制后逐渐觉醒，"
                          "不只是个人反抗，而是引发更大范围对性别规则合理性的质疑。",
        content_json={
            "act_1": "接受现实 + 寻找规则内的生存空间",
            "act_2": "遭遇核心压迫事件（自身或他人的极端例子）",
            "act_3": "联合志同道合者 + 用女主地位作保护伞",
            "resolution": "不一定是颠覆制度，而是在制度内建立新的可能性",
            "thematic_core": "反映的是创作者对性别权力的真实批判",
            "activation_keywords": ["性别觉醒", "女尊反抗", "制度质疑", "男权觉醒"],
        },
        source_type="llm_synth", confidence=0.70,
        source_citations=[llm_note("女尊叙事结构分析")],
        tags=["女尊", "觉醒", "制度", "情节"],
    ),

    # 校园 补充
    MaterialEntry(
        dimension="world_settings", genre="校园",
        slug="campus-ws-social-hierarchy",
        name="校园权力社交生态",
        narrative_summary="校园是浓缩的社会权力模型：家庭背景/颜值/成绩/社交能力各自形成权力轴，"
                          "这些轴相互交叉制造复杂关系网，是展示社会规则预演的完美容器。",
        content_json={
            "power_axes": ["家庭背景与财富", "学业成绩（特别是精英校）", "颜值与社交魅力", "运动/才艺表现"],
            "clique_structure": "顶层（各轴领跑者）→中层（某轴单强）→边缘（多轴弱势）",
            "conflict_ecology": "不同权力轴之间的冲突 / 规则内欺凌的默许机制",
            "narrative_purpose": "校园事件折射真实社会运行规则的雏形",
            "activation_keywords": ["校园排位", "社交圈层", "校霸", "学霸", "隐性规则"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[wiki("青春期社会化", "心理学"), llm_note("校园叙事世界观设计")],
        tags=["校园", "社交", "阶层", "青春"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="校园",
        slug="campus-ca-outsider-genius",
        name="刻意隐藏才能的局外人",
        narrative_summary="在校园顶层有能力立足却选择边缘位置的角色——不是无能被排挤，"
                          "而是主动选择不参与游戏，直到某件事让他不得不「入局」。",
        content_json={
            "reason_for_hiding": "对规则的不屑 / 保护某人 / 过去的创伤",
            "reveal_trigger": "朋友被欺凌 / 比赛/考试中被迫展示 / 喜欢的人面临危险",
            "social_impact": "展示后打破原有权力平衡，引发各层的重新排列",
            "archetype_appeal": "读者的幻想投射：我在这个世界是被低估的",
            "activation_keywords": ["低调天才", "隐藏实力", "校园局外人", "藏锋"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("校园角色原型分析")],
        tags=["校园", "天才", "低调", "局外人"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="校园",
        slug="campus-pp-competition-arc",
        name="校际/校内竞争弧",
        narrative_summary="学业/体育/艺术竞赛作为外壳，内部是角色关系与自我认知的碰撞。"
                          "重点不是赢，而是准备过程和竞争中暴露的内心真相。",
        content_json={
            "competition_types": ["高考/入学考试", "运动会/联赛", "文艺汇演", "辩论赛/学术竞赛"],
            "real_conflict": "竞争双方的关系在过程中发生质变（对手→盟友/朋友→竞争者）",
            "inner_game": "参赛者面对自我设限/父母期望/友情与胜负的选择",
            "emotional_peak": "赛前最后一夜的独白/对话，而非比赛本身的结果",
            "activation_keywords": ["校际比赛", "高考", "竞赛", "青春热血", "努力"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[llm_note("校园情节模式分析")],
        tags=["校园", "竞争", "成长", "情节"],
    ),

    # 灵异 补充
    MaterialEntry(
        dimension="world_settings", genre="灵异",
        slug="liyi-ws-yin-yang-boundary",
        name="阴阳边界世界观",
        narrative_summary="现实世界之下存在阴间/鬼域层叠，两界之间的边界在特定时间/地点/条件下变薄，"
                          "少数人天生能感知并穿越这层边界，这种能力是天赋也是诅咒。",
        content_json={
            "world_structure": "阳间（日常现实）/ 阴阳交界（薄弱点）/ 阴间（鬼魂领域）",
            "boundary_conditions": "特殊地理（古战场/乱葬岗）/ 时间（三更/鬼节）/ 情感浓度（极度思念）",
            "sensitive_persons": "阴阳眼持有者/命中缺五行某格/意外目睹者",
            "conflict_types": ["执念亡灵的解脱", "阴间秩序的维护", "被邪物附身者的救援"],
            "Chinese_folklore_roots": "源自道教阴阳观念/佛教六道/民间鬼怪传说",
            "activation_keywords": ["阴阳眼", "鬼域", "边界薄弱", "三更", "阴阳两界"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[wiki("阴阳", "中国哲学"), wiki("中国鬼怪", "民间传说"), llm_note("灵异世界观")],
        tags=["灵异", "阴阳", "鬼魂", "世界观"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="灵异",
        slug="liyi-ca-reluctant-seer",
        name="不情愿的见鬼者",
        narrative_summary="从小能看见鬼魂，但生活在极力否认或压制这种能力的状态中，"
                          "被卷入灵异事件后不得不正面接受，进而逐渐从「被动受害」转向「主动解决」。",
        content_json={
            "ability_origin": "胎记/血脉传承/意外触发（九死一生）",
            "coping_mechanism": "用「理性解释」否认见到的一切 / 过度忽视 / 强迫行为",
            "crisis_trigger": "无法再忽视的事件（有人因此受害）",
            "mentor_figure": "往往有一个更成熟的灵异感知者给予引导",
            "arc_goal": "从逃避到接受，最终达成「能见鬼」与「正常生活」的平衡",
            "activation_keywords": ["阴阳眼", "见鬼", "不情愿", "灵异体质", "被动觉醒"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("灵异角色原型分析")],
        tags=["灵异", "见鬼", "觉醒", "拒绝"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre="灵异",
        slug="liyi-rwr-chinese-ghost-lore",
        name="中国鬼怪民俗知识库",
        narrative_summary="中国民间鬼怪体系：从道教鬼神观到民间传说再到地方特色鬼怪，"
                          "提供灵异类创作的知识激活基础，让作品有文化根系而非凭空想象。",
        content_json={
            "major_categories": {
                "执念鬼": "带着未了心愿的亡灵，最多见",
                "冤鬼": "被害而死，怨气极重",
                "厉鬼": "怨气积累成的恶性存在",
                "孤魂野鬼": "无人祭祀的游魂",
                "地缚灵": "被束缚在某地的灵魂",
            },
            "cultural_roots": "道教度亡法事/佛教超度/民间驱鬼仪式（跳大神/问米/打小人）",
            "regional_variations": "江南水鬼/北方旱魃/西南赶尸/广东僵尸",
            "festival_connections": "清明/中元节/寒衣节——特定节日鬼门开",
            "taboo_system": "民间禁忌（不能叫人全名/深夜照镜子/吹口哨）",
            "activation_keywords": ["冤鬼", "执念", "中元节", "超度", "地缚灵", "孤魂野鬼"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("中国民间信仰", ""), wiki("鬼节", "中国传统"), llm_note("灵异民俗知识库")],
        tags=["灵异", "民俗", "鬼怪", "中国文化"],
    ),

    # 美食 补充
    MaterialEntry(
        dimension="character_archetypes", genre="美食",
        slug="food-ca-heritage-chef",
        name="传承危机中的厨师",
        narrative_summary="继承了独门厨艺但面临传承断绝危机的厨师——或因时代变化无人学，"
                          "或因家族矛盾后继无人，通过做菜与过去/家人/传统和解的过程是最打动人的叙事核心。",
        content_json={
            "传承_crisis": "绝技濒临失传 / 家族反对传给外人 / 自己的偏见阻碍传承",
            "cooking_as_language": "做某道菜=与已故亲人对话/与记忆和解",
            "student_dynamic": "接班人的出现 + 打破规则与保留传统的张力",
            "arc_resolution": "真正的传承不是复制，而是理解精髓后的再创造",
            "activation_keywords": ["传承", "绝技", "厨艺", "非遗", "家族菜谱"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("美食叙事角色原型"), wiki("饮食文化", "中国")],
        tags=["美食", "传承", "厨师", "家族"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="美食",
        slug="food-pp-cook-off-arc",
        name="美食竞技弧",
        narrative_summary="美食比赛作为外壳的情节结构：表面是烹饪技术的较量，"
                          "实质是两种对食物理解/人生哲学的碰撞，结果往往是意料之外的非二元结局。",
        content_json={
            "act_structure": "报名/赛题揭晓→食材准备/对手分析→烹饪过程（意外）→评审→反应",
            "deeper_meaning": "比赛规则代表某种价值观 / 主角的做法本身就是对规则的挑战",
            "judge_function": "评委的评语揭示食物的文化/情感维度",
            "competitor_arc": "对手从敌人到互相尊重，有时反而成为朋友",
            "Chinese_specific": "中国美食文化本身有丰富叙事资源：地域之争/正宗性之争",
            "activation_keywords": ["厨艺比拼", "美食竞技", "料理大赛", "食材挑战"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("美食叙事情节模式")],
        tags=["美食", "比赛", "竞技", "情节"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="美食",
        slug="food-tm-food-memory",
        name="食物作为记忆载体",
        narrative_summary="某道菜肴触发对某段时光、某个人的强烈记忆——普鲁斯特效应。"
                          "在美食文中，这往往是情感爆发的开关，食物的味道比任何语言都更直接地连接心灵。",
        content_json={
            "proust_mechanism": "味觉/嗅觉直接激活情绪记忆（绕过理性层）",
            "narrative_applications": [
                "主角第一次吃到与逝去亲人相关的食物",
                "在异乡吃到家乡味道的情感崩溃",
                "两个陌生人发现共同的「记忆食物」",
            ],
            "food_as_time_travel": "一道菜=穿越到某段时光",
            "writing_technique": "聚焦感官细节（色香味质）再转入记忆流",
            "activation_keywords": ["食物记忆", "普鲁斯特效应", "家的味道", "触景生情"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[wiki("普鲁斯特现象", "心理学"), llm_note("美食叙事主题分析")],
        tags=["美食", "记忆", "情感", "普鲁斯特"],
    ),

    # 快穿 补充
    MaterialEntry(
        dimension="world_settings", genre="快穿",
        slug="kuaichuan-ws-multiworld",
        name="快穿多元世界框架",
        narrative_summary="快穿的宏观设定：存在一个元层级的「系统空间」作为中枢，"
                          "下接无数个「剧情世界」。每个世界有既定走向，主角的任务是修复偏差的世界线。",
        content_json={
            "meta_structure": "系统中枢→各世界节点（古代/现代/仙侠/末日任意题材）",
            "world_selection": "系统分配 / 随机 / 任务完成度影响下个世界",
            "identity_mechanics": "主角进入世界后有宿主记忆覆盖 / 或清醒状态入场",
            "world_stability": "世界线偏差值：过高则世界崩塌，任务核心是将偏差值降至零",
            "meta_mystery_layer": "系统来源/真实目的/谁在背后运营——是快穿类的终极谜题",
            "activation_keywords": ["世界线", "任务系统", "宿主", "快穿空间", "剧情偏差"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[llm_note("快穿世界观架构分析")],
        tags=["快穿", "多元世界", "系统", "世界线"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="快穿",
        slug="kuaichuan-ac-反攻略机器人",
        name="快穿攻略机器人陷阱",
        narrative_summary="快穿主角从不失手、完美攻略每个世界的「机器人」模式让读者失去代入感。"
                          "好的快穿需要：主角在某个世界真正动情、真正失误、为某个世界线哭泣。",
        content_json={
            "cliché": "每个世界都是完美任务完成机 / 无差别地对每个攻略对象",
            "reader_impact": "预期满足但情感空洞",
            "fix_strategies": [
                "有一个世界主角真心动了，任务完成却是痛苦的",
                "某个世界的攻略对象让主角想起自己的本体记忆",
                "任务失败的世界作为转折点",
            ],
            "金标准": "读者记得住的是某个世界里的情感而不是任务清单",
            "activation_keywords": ["快穿情感", "任务失败", "动真情", "反套路快穿"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("快穿叙事反套路分析")],
        tags=["快穿", "反套路", "情感", "创作技巧"],
    ),
]


async def main(dry_run: bool = False) -> None:
    print(f"{'[DRY RUN] ' if dry_run else ''}Seeding {len(ENTRIES)} entries...\n")
    from collections import Counter
    genre_counter: Counter = Counter()
    dim_counter: Counter = Counter()
    for e in ENTRIES:
        genre_counter[e.genre or "(通用)"] += 1
        dim_counter[e.dimension] += 1
    print(f"By genre:     {dict(genre_counter)}")
    print(f"By dimension: {dict(dim_counter)}\n")
    if dry_run:
        return
    errors = 0
    async with session_scope() as session:
        for entry in ENTRIES:
            try:
                await insert_entry(session, entry, compute_embedding=True)
            except Exception as exc:
                print(f"  ERROR {entry.slug}: {exc}")
                errors += 1
    print(f"\n✓ {len(ENTRIES) - errors} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    asyncio.run(main(dry_run=dry))
