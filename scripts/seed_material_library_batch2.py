#!/usr/bin/env python3
"""
Material Library Seed Script - Batch 2
扩充 10 个欠覆盖题材，每题材 × 5 核心维度 × 3-5 条目 = ~200 条新物料。

Usage:
    uv run python scripts/seed_material_library_batch2.py [--dry-run] [--genre GENRE]
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
    # 历史 × 扩充
    # ===========================================================================

    MaterialEntry(
        dimension="world_settings", genre="历史",
        slug="hist-ws-border-garrison",
        name="边疆军镇世界观",
        narrative_summary="中原内陆繁华与草原游牧之间的边境军事重镇，驻军文化与汉夷杂居，常年战备状态下的异化社会结构",
        content_json={
            "geography_model": "长城沿线军镇，南北各三百里代表两种文明秩序",
            "power_vacuum": "将领世家垄断军权，朝廷派来的文官只管粮草不懂军事",
            "civilizational_rules": "军功是唯一阶级上升通道，伤疤和战马比科举功名更值钱",
            "unique_conflict_source": "和亲公主/质子制度下的人质外交与情感纠葛",
            "seasonal_rhythm": "春末夏初游牧入侵高峰期，冬季封关内外贸易期",
        },
        source_type=L, confidence=0.62,
        source_citations=[wiki("长城", "https://zh.wikipedia.org/wiki/長城"), llm_note("边疆军镇社会结构分析")],
        tags=["历史", "军事", "边疆", "汉夷"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="历史",
        slug="hist-ws-jianghu-underworld",
        name="江湖与庙堂双轨世界",
        narrative_summary="官府管辖之外的武林江湖作为隐性权力体系并存，帮会门派有自己的法律秩序，与朝堂互相渗透利用",
        content_json={
            "geography_model": "水路交通枢纽城市是江湖势力核心，码头帮会控制物流即控制情报",
            "power_vacuum": "朝廷无力清剿江湖，以招安换取对流民的控制权",
            "civilizational_rules": "江湖人讲义气门规，朝廷人讲律法官制，两套规则在利益面前都会弯曲",
            "unique_conflict_source": "武功秘笈=政治资本，谁持有它谁就有招募高手的能力",
        },
        source_type=L, confidence=0.65,
        source_citations=[wiki("武侠小说", "https://zh.wikipedia.org/wiki/武俠小說"), llm_note("江湖社会学分析")],
        tags=["历史", "江湖", "武林", "权谋"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="历史",
        slug="hist-ca-eunuch-power",
        name="权宦",
        narrative_summary="以阉人身份在皇权核心积累真实权力的人，用忠诚面具掩盖政治野心，是皇帝的影子也是最危险的棋手",
        content_json={
            "core_wound": "身份残缺带来的永久屈辱感，用权力证明自己的价值",
            "external_goal": "控制皇帝的信息获取，将外朝宰相架空为摆设",
            "internal_need": "得到一种真正的归属感——但宦官身份让他永远在圈子外",
            "fatal_flaw": "对'背叛'极度敏感，一旦感到被抛弃即转为最危险的敌人",
            "typical_arc": "小心翼翼上位→小心翼翼扩权→激进暴露野心→覆灭或成功",
        },
        source_type=L, confidence=0.63,
        source_citations=[eval_src("魏忠贤、郑和等历史宦官人物原型研究")],
        tags=["历史", "宦官", "权谋", "宫廷"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="历史",
        slug="hist-ca-royal-daughter",
        name="皇室女儿的博弈者",
        narrative_summary="出生即是政治资本的公主/郡主，婚姻是筹码、身份是护盾，在没有选择余地的命运里争取最大主动权",
        content_json={
            "core_wound": "从小被当作棋子培养，从未被当作完整的人",
            "external_goal": "争取对自己婚事的控制权，或以政治联姻换取家族/帝国需要的东西",
            "internal_need": "找到一个只把她当人而非棋子对待的关系",
            "fatal_flaw": "太习惯用身份当防护壳，反而无法真正信任任何人",
            "typical_arc": "被动接受安排→发现棋局真相→主动操盘→以自身为代价完成使命",
        },
        source_type=L, confidence=0.65,
        source_citations=[eval_src("中国历史公主政治婚姻案例研究")],
        tags=["历史", "女性", "宫廷", "政治"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="历史",
        slug="hist-pp-counter-investigation",
        name="案中案反查结构",
        narrative_summary="主角以查案者身份介入，发现每一层真相都揭露更深的阴谋，最终调查对象从别人变成了自己或自己的阵营",
        content_json={
            "setup": "一桩看似普通的案件（贪腐/命案）牵出不寻常的证据链",
            "escalation": "幕后人不断派人阻止调查，逐渐暴露自己的存在",
            "midpoint_reversal": "主角发现委托者/上司才是真凶或共谋",
            "climax_structure": "主角以'继续查'换取'不被灭口'的谈判筹码",
            "resolution_options": ["正义胜出但代价极重", "以沉默换和平（悲剧结局）", "以证据为武器实现交换"],
        },
        source_type=L, confidence=0.65,
        source_citations=[llm_note("历史悬疑叙事结构分析")],
        tags=["历史", "悬疑", "反转", "权谋"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="历史",
        slug="hist-pp-exile-return",
        name="流放-归来复仇弧",
        narrative_summary="主角因政治失败被流放边地，在边疆积累新资源新人脉，带着与原来完全不同的力量体系回到权力中心",
        content_json={
            "setup": "朝堂失势，主角被扣上罪名发配偏远",
            "development": "流放地不是惩罚终点而是新的起点——军功/商路/地方豪族",
            "transformation": "主角在边疆完成价值观转变，回来时已不在乎原有阶层的认可",
            "climax_structure": "以边疆势力为筹码重返朝堂，与昔日对手在完全不同的规则下博弈",
            "anti_cliche": "流放期间不能无敌，必须经历真实失败和求人帮助",
        },
        source_type=L, confidence=0.67,
        source_citations=[eval_src("班超出使西域、苏武牧羊等流放复归历史原型")],
        tags=["历史", "流放", "复仇", "成长"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre="历史",
        slug="hist-ea-reluctant-alliance",
        name="敌对者结盟情感弧",
        narrative_summary="从利益对立到被迫合作，到相互理解，到真正信任——每一步都有代价，信任建立比爱情更难更真实",
        content_json={
            "stage1": "强制合作期：互相利用，表面礼貌实则监视",
            "stage2": "危机同盟期：共同敌人出现，不得不真正依靠对方",
            "stage3": "理解分歧期：发现对方的逻辑有其合理性，但仍不认同",
            "stage4": "真正选择期：在可以出卖对方获利时选择不出卖",
            "turning_point": "一次对方不知道的暗中相助，主角才意识到自己已经在乎",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm_note("古代政治联姻与对立阵营情感叙事研究")],
        tags=["历史", "情感", "CP", "政治"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="历史",
        slug="hist-tm-history-judge",
        name="历史书写权力主题",
        narrative_summary="谁书写历史谁就定义了功过——史官的笔、胜利者的叙述与失败者的真相构成的张力",
        content_json={
            "core_question": "功绩是客观存在的还是被叙述出来的？",
            "narrative_use": "主角试图在史书留下真实记录，却发现史官受命于权贵",
            "symbolic_objects": ["史官的墨笔", "被烧毁的档案", "民间野史与官方正史的矛盾"],
            "thematic_resolution": "个人的真实存在不依赖史书认可——活在人心里比活在史书更有力量",
        },
        source_type=L, confidence=0.70,
        source_citations=[wiki("中国历史编纂", "https://zh.wikipedia.org/wiki/史学"), llm_note("历史书写权力分析")],
        tags=["历史", "主题", "史学", "权力"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="历史",
        slug="hist-ac-modern-values",
        name="现代价值观穿越禁忌",
        narrative_summary="穿书/重生主角用现代平权/民主理念直接教化古代人——破坏世界观自洽，削弱叙事张力",
        content_json={
            "problem": "主角直接输出现代理念，周围古代人瞬间觉醒接受，无任何认知摩擦",
            "why_bad": "历史世界观崩坏，古代人成了听讲座的道具而非真实存在的人",
            "correct_approach": "主角可以有现代价值观，但必须通过行动而非说教影响他人；影响是缓慢的、有代价的",
            "warning_signs": ["主角一句话改变敌人三观", "古代女性立刻接受女权主义", "皇帝被说服废除等级制度"],
        },
        source_type=L, confidence=0.75,
        source_citations=[llm_note("历史题材叙事真实性研究")],
        tags=["历史", "反套路", "世界观", "穿越"],
    ),

    # ===========================================================================
    # 悬疑 × 扩充
    # ===========================================================================

    MaterialEntry(
        dimension="world_settings", genre="悬疑",
        slug="susp-ws-digital-surveillance",
        name="数字监控时代设定",
        narrative_summary="人脸识别、大数据画像、电子轨迹无处不在的都市，隐私已死——但在数字缝隙里仍然有无法被看见的角落",
        content_json={
            "surveillance_level": "城市90%覆盖，但农村边缘地带、地下设施、信号盲区是叙事空间",
            "power_distribution": "掌握数据的科技公司比警察局更早知道犯罪发生",
            "unique_conflict": "真正的罪行往往发生在监控刻意回避的地方——谁有权删除记录？",
            "atmosphere": "主角明明有海量数据，却越来越不确定自己看到的是真相还是被精心布置的表演",
        },
        source_type=L, confidence=0.65,
        source_citations=[llm_note("数字监控社会悬疑叙事分析"), wiki("监控资本主义", "https://zh.wikipedia.org/wiki/監控資本主義")],
        tags=["悬疑", "都市", "科技", "监控"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="悬疑",
        slug="susp-ws-isolated-community",
        name="封闭社群世界观",
        narrative_summary="与外界隔绝的小型社区（山村/海岛/邪教/精英学校），内部有独立规则体系，秘密用'共同体压力'维持",
        content_json={
            "isolation_mechanism": "地理隔绝/规则隔绝/心理隔绝三层，越深入越难逃脱",
            "power_distribution": "表面平等实则等级森严，核心成员掌握所有人的把柄",
            "unique_conflict": "外来者/新成员试图查明真相，却发现要离开必须先成为共谋",
            "atmosphere": "每个人都笑着告诉你'我们这里很好'，恰恰说明他们都知道事情不对",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm_note("封闭社群犯罪心理学分析")],
        tags=["悬疑", "封闭", "社群", "心理"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="悬疑",
        slug="susp-ca-trauma-detective",
        name="创伤侦探",
        narrative_summary="因亲历某桩未解悬案（失去至亲/被错判/目击暴力）而执念调查的非职业侦探，敏锐来自痛苦，盲区也来自痛苦",
        content_json={
            "core_wound": "一桩与自己有深度关联的案件留下永久阴影，官方结案却从未真正解决",
            "external_goal": "翻出旧案真相，或阻止类似案件再次发生",
            "internal_need": "证明当年不是自己的错，或承认某种程度的责任并和解",
            "fatal_flaw": "对案件主观代入太深，会扭曲证据解读以符合内心期待",
            "investigative_style": "靠直觉和情绪，而非逻辑推演——有时更接近真相，有时是最大的错误来源",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("创伤叙事与犯罪调查主角设计研究")],
        tags=["悬疑", "主角", "创伤", "调查"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="悬疑",
        slug="susp-ca-unreliable-witness",
        name="不可靠证人",
        narrative_summary="亲历关键事件却因心理原因（记忆障碍/利益相关/主动撒谎/认知偏差）无法提供可靠叙述的角色",
        content_json={
            "unreliability_source": "PTSD记忆碎片化/催眠暗示/主动保护某人/认知障碍任选1-2",
            "narrative_function": "制造叙事迷雾——读者和侦探都不知道应该信多少",
            "character_truth": "即使叙述不可靠，内心动机是真实的；ta最终会以自己的方式指向真相",
            "dramatic_irony": "读者有时比侦探更早看出证人的某些矛盾，形成张力",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm_note("不可靠叙述者技巧在悬疑小说中的应用")],
        tags=["悬疑", "叙事技巧", "配角", "信息差"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="悬疑",
        slug="susp-pp-red-herring-chain",
        name="链式误导结构",
        narrative_summary="多条红鲱鱼按顺序被排除，每次排除都让读者更确信'找到了'，最终真凶方向是排除过程中被忽视的细节",
        content_json={
            "herring1": "最明显的嫌疑人——有动机有机会，但被排除（太明显的通常不是）",
            "herring2": "第二嫌疑人——有隐藏秘密，查出秘密后发现是另一件事不是本案",
            "herring3": "调查者自己被怀疑——引导读者质疑主角，分散注意力",
            "true_reveal": "真凶藏在读者认为已经排除嫌疑的人中，靠一个极小细节前后矛盾",
            "fairplay_rule": "真凶的所有线索必须在前面出现过，只是读者被引导忽视了",
        },
        source_type=L, confidence=0.72,
        source_citations=[llm_note("推理小说叙事结构与公平游戏原则研究")],
        tags=["悬疑", "叙事结构", "误导", "反转"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="悬疑",
        slug="susp-st-interrogation-pressure",
        name="审讯压力场景",
        narrative_summary="侦探与嫌疑人在信息不对等中的心理博弈，节奏控制比台词更重要，沉默的力量大于问题",
        content_json={
            "setup": "物理空间刻意设计（灯光/椅子/温度），制造心理压迫感",
            "technique1": "假装已知——'我们知道你当晚去了哪里'（其实不知道）",
            "technique2": "制造时间压力——'你的同伙已经开口了'",
            "technique3": "沉默等待——嫌疑人往往在沉默中填补信息",
            "reversal_possibility": "嫌疑人也可以反审讯，揭露侦探的弱点",
            "sensory_details": "汗水气味、荧光灯嗡嗡声、被握紧的手、眼神不自然的方向",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("犯罪심理与审讯技术文学应用研究")],
        tags=["悬疑", "场景", "审讯", "心理"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="悬疑",
        slug="susp-tm-truth-cost",
        name="真相代价主题",
        narrative_summary="真相不是中性的——它总是会伤害某个人。主角必须在真相与保护之间做选择，没有完全正确的答案",
        content_json={
            "core_tension": "揭露真相 vs 保护无辜者（真相的受害者未必是罪犯）",
            "thematic_question": "如果真相只会让所有人更痛苦，揭露它还有意义吗？",
            "narrative_expression": "主角最终选择揭露真相，但必须亲手见证它造成的伤害",
            "symbolic_objects": ["旧照片", "被销毁的证据", "两个版本的真相"],
            "avoid": "不能让揭露真相完全无代价——那是童话而非悬疑",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm_note("悬疑小说主题道德哲学分析")],
        tags=["悬疑", "主题", "道德", "真相"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="悬疑",
        slug="susp-ac-genius-detective",
        name="无所不能天才侦探禁忌",
        narrative_summary="主角每次瞬间看穿一切从不走弯路——消除了悬疑核心的不确定性和读者的参与感",
        content_json={
            "problem": "侦探能力超强到读者感觉作者只要愿意任何时候都可以解决谜题，悬念不真实",
            "why_bad": "悬疑张力来自主角和读者在同等信息下的不确定性；主角全知则悬念消失",
            "correct_approach": "天才应该在某个维度有盲区：情感盲区/某类知识盲区/自身利益相关时判断力下降",
            "examples": ["不会读人际关系却能读证据", "能看穿陌生人却对亲近者有盲区"],
        },
        source_type=L, confidence=0.75,
        source_citations=[llm_note("悬疑叙事中侦探角色局限性设计研究")],
        tags=["悬疑", "反套路", "主角设计"],
    ),

    # ===========================================================================
    # 末日 × 扩充
    # ===========================================================================

    MaterialEntry(
        dimension="world_settings", genre="末日",
        slug="apoc-ws-tier-city",
        name="末日阶层城市",
        narrative_summary="末日后重建的有序城市，用幸存者等级制度替代了旧社会，外圈是底层新民，内圈是掌握物资和能力者，围墙内外代表两种人类",
        content_json={
            "geography_model": "同心圆结构：核心区（特权/物资充足）→中间区（工作区）→外圈（新来者/弱者）→墙外（野外危险地带）",
            "power_distribution": "能力者（异能/战斗）掌握安全，物资商人掌握食物，二者博弈",
            "civilizational_rules": "末日前的学历/财富归零，能力和体力重新洗牌了阶级",
            "unique_conflict": "旧精英阶层如何在新秩序中重建特权地位",
            "atmosphere": "文明的外壳维持着，但每个人都知道墙外是什么，所以什么都做得出来",
        },
        source_type=L, confidence=0.65,
        source_citations=[llm_note("末日题材社会结构分析"), wiki("末日文学", "https://zh.wikipedia.org/wiki/末日文學")],
        tags=["末日", "世界观", "阶层", "幸存者"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="末日",
        slug="apoc-ws-zombie-ecology",
        name="丧尸生态系末日",
        narrative_summary="丧尸不是静态威胁，而是会进化的生态压力——初期低智群体，后期出现变异个体，逼迫幸存者从逃跑者变成研究者",
        content_json={
            "threat_evolution": "普通丧尸→追踪型变异体→群体协作型高级体→拥有原始智慧的王者级",
            "ecology_balance": "丧尸需要猎食活体才能维持活跃；食物减少时进入'休眠'状态，幸存者以为安全时最危险",
            "human_adaptation": "幸存者从被动防御到主动猎杀丧尸取材（丧尸晶核/器官有特殊价值）",
            "unique_conflict": "有人研究丧尸进化规律成为最宝贵的人，也成为争夺目标",
        },
        source_type=L, confidence=0.63,
        source_citations=[llm_note("进化型丧尸设定分析")],
        tags=["末日", "丧尸", "生态", "进化"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="末日",
        slug="apoc-ca-reluctant-leader",
        name="被迫领袖",
        narrative_summary="不想承担责任却被情况推上领导位置的幸存者，每次决策都要在不够好的选项里选最不坏的那个",
        content_json={
            "core_wound": "末日前是普通人，从没想过要对他人的生死负责",
            "external_goal": "带领团队活下去（但内心一直想把责任推给别人）",
            "internal_need": "接受自己有能力也有责任，停止逃避",
            "fatal_flaw": "优柔寡断在危机时刻造成延误；或反应过度，为了快速决策而残忍",
            "arc": "推诿责任→被迫接管→犯错付代价→真正成长→在最难时刻做了最正确的选择",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm_note("末日题材领导力叙事分析")],
        tags=["末日", "主角", "领袖", "成长"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="末日",
        slug="apoc-ca-pre-apocalypse-villain",
        name="末日前的加害者",
        narrative_summary="在旧世界对主角造成伤害的人（欺凌者/剥削者/抛弃者），末日后两人同处一队，救还是不救构成道德核心张力",
        content_json={
            "setup_function": "旧世界权力关系被末日彻底颠覆，加害者失去原有优势",
            "tension_types": ["主角现在有能力复仇/抛弃对方，但这样做与'想成为的人'矛盾", "加害者在末日中真正改变了vs伪装改变换取保护"],
            "resolution_options": ["接纳但永不忘记（冷和解）", "拒绝帮助（付出代价的道德选择）", "帮助中意外发现对方的人性"],
            "thematic_value": "末日是'清零'还是'照妖镜'——人的本质在极端环境下暴露还是改变？",
        },
        source_type=L, confidence=0.65,
        source_citations=[llm_note("末日叙事中旧世界关系重建主题分析")],
        tags=["末日", "配角", "道德", "旧伤"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="末日",
        slug="apoc-pp-resource-war",
        name="资源争夺升级弧",
        narrative_summary="从个人觅食→团队争夺→据点扩张→势力战争，规模升级但道德选择始终是核心，胜利不等于正确",
        content_json={
            "phase1": "个人/小团体阶段：找水找食物，建立基本信任",
            "phase2": "据点建设阶段：防御、储存、分工，开始制定规则",
            "phase3": "势力接触阶段：遭遇其他幸存者团体，利益冲突不可避免",
            "phase4": "大规模冲突阶段：为了有限资源的真正战争，胜利意味着什么？",
            "anti_power_fantasy": "资源胜利不能解决内部分裂，赢了战争可能输了人心",
        },
        source_type=L, confidence=0.67,
        source_citations=[llm_note("末日题材权力叙事结构研究")],
        tags=["末日", "情节", "资源", "战争"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre="末日",
        slug="apoc-ea-hope-despair-cycle",
        name="希望与绝望的循环弧",
        narrative_summary="末日叙事不是线性下滑，而是在希望与绝望间来回振荡——每次以为找到出路时遭受重创，每次陷入绝境时意外找到新的理由活下去",
        content_json={
            "cycle_structure": "小希望（我们找到了安全屋）→打击（安全屋不安全）→更深绝望→意外转机（他为了救我）→新希望",
            "reader_function": "振荡频率控制读者情绪；绝望必须真实，否则希望也不可信",
            "character_function": "每次循环后主角的韧性应该变化——或更强，或学会'希望小一点但不放弃'",
            "avoid": "不要每章都绝望然后奇迹解救——节奏要有长段的低沉",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("末日叙事情感节奏设计研究")],
        tags=["末日", "情感", "节奏", "希望"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="末日",
        slug="apoc-tm-what-makes-us-human",
        name="何为人性主题",
        narrative_summary="末日撕去文明外壳，留下的是更真实的人性还是野蛮原型——两种答案都可以成立，好的末日叙事同时呈现两面",
        content_json={
            "humanist_pole": "危机中人们展示出平时不可能有的慷慨、牺牲和无私",
            "dark_pole": "危机中人们展示出平时被压抑的暴力、自私和残忍",
            "thematic_complexity": "同一个人可以在同一天做出两件极端相反的事——这才是人性",
            "narrative_ask": "主角在两极之间如何定位自己？选择人性光明面需要付出什么代价？",
            "symbolic_objects": ["最后一块面包的分配", "放弃一个弱者拯救更多人的时刻"],
        },
        source_type=L, confidence=0.72,
        source_citations=[llm_note("末日哲学与人性本质文学研究")],
        tags=["末日", "主题", "人性", "哲学"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="末日",
        slug="apoc-ac-lone-wolf-invincible",
        name="孤狼无敌禁忌",
        narrative_summary="主角一人清场所有威胁，不需要团队也不需要依赖他人——末日最重要的主题之一（人类需要彼此）直接失效",
        content_json={
            "problem": "无敌主角消除了物理威胁的紧张感，末日只是普通升级文的背景",
            "why_bad": "末日叙事的核心是'人类如何在极端条件下维持和建立关系'，孤狼路线绕过这一主题",
            "correct_approach": "主角有硬实力，但在社交/信任/道德方面有真实弱点；团队是强项也是弱点",
            "warning_signs": ["随便找到一个安全屋就无限续命", "所有敌对幸存者都是纸板坏蛋", "不需要任何人帮助"],
        },
        source_type=L, confidence=0.75,
        source_citations=[llm_note("末日叙事角色设计反模式研究")],
        tags=["末日", "反套路", "主角", "团队"],
    ),

    # ===========================================================================
    # 言情 × 扩充
    # ===========================================================================

    MaterialEntry(
        dimension="world_settings", genre="言情",
        slug="rom-ws-professional-setting",
        name="职场竞争言情世界观",
        narrative_summary="以高压职场为舞台，职业成就与情感关系互相干扰——职场规则和感情规则在同一空间内不断冲突",
        content_json={
            "workplace_type": "金融/法律/医疗/媒体/科技——每种行业有独特的权力结构和潜规则",
            "power_differential": "上下属关系/竞争对手关系/合作伙伴关系，任何CP组合各有叙事张力",
            "conflict_source": "职业目标与情感目标在同一关键决策点相撞，必须选一个",
            "unique_tension": "成功爱上对方意味着什么？是成为对方的弱点？还是获得更大力量？",
        },
        source_type=L, confidence=0.65,
        source_citations=[llm_note("都市言情职场背景叙事分析")],
        tags=["言情", "职场", "CP", "现代"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="言情",
        slug="rom-ws-ancient-aristocracy",
        name="古代贵族言情世界观",
        narrative_summary="门第制度下的言情，阶层差距是最大的外部障碍，内心感情要对抗的不只是另一个人的心，还有整个家族利益网络",
        content_json={
            "class_structure": "皇族/世家/官僚/平民，每一级之间的婚嫁都有政治意义",
            "marriage_politics": "婚姻是家族政治联盟的工具，自由恋爱意味着背叛家族",
            "female_agency": "女性主角的能动性空间在哪——不能改变规则时如何在规则内最大化自己的选择",
            "tension_source": "被指婚给不喜欢的人，却对不合适的人动了心",
        },
        source_type=L, confidence=0.65,
        source_citations=[llm_note("古代婚姻制度与言情叙事研究")],
        tags=["言情", "古代", "阶层", "婚姻"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="言情",
        slug="rom-ca-ice-king-melt",
        name="冷漠型男主融化弧",
        narrative_summary="表面冷漠隔绝实则有深重情感创伤的男主，女主不是'融化他'的道具，而是恰好出现在他愿意改变的时间点上",
        content_json={
            "coldness_source": "真实创伤（背叛/失去/原生家庭），不是天生性格，有明确来源",
            "change_trigger": "不是女主的'感化'，而是一个具体事件让他意识到隔绝代价太高",
            "change_pace": "必须缓慢、非线性——有进两步退一步的真实感",
            "anti_pattern": "女主强行进入他的世界，他反复拒绝最后突然接受——这是被迫，不是改变",
            "correct_pattern": "他自己做出选择，靠近她是主动的，即使很慢很难",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm_note("言情男主角色弧设计研究")],
        tags=["言情", "男主", "冷漠", "成长"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="言情",
        slug="rom-ca-strong-female-lead",
        name="真实女强主角",
        narrative_summary="真正的女强不是永远正确、情感无懈可击——而是在感情面前仍然是一个普通的、会受伤的、会做错选择的人",
        content_json={
            "strength_definition": "职业能力/意志力/独立性是真实的，不依赖男主的肯定",
            "vulnerability_source": "感情的弱点不是作为大女主的失格，是她作为人的真实存在",
            "common_failure": "完美大女主对男主若即若离当作'独立'，实则是作者怕写感情露出弱点",
            "correct_balance": "她在职业上强大，在感情上同样投入——投入也意味着真的可能受伤",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("女性主义言情叙事设计研究")],
        tags=["言情", "女主", "女强", "真实感"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="言情",
        slug="rom-pp-misunderstanding-resolution",
        name="误会驱动与解误节奏",
        narrative_summary="误会是言情引擎之一，但误会必须有'合理不被解除'的理由，否则读者会愤怒于'说一句话就解决了为什么不说'",
        content_json={
            "valid_misunderstanding": "权力差距/过往创伤/误会当事人有利可图/第三方主动维持误会",
            "resolution_pacing": "误会应该在有意义的时间点解除，不能拖到读者忍无可忍",
            "cost_of_resolution": "解除误会不应该是'说明了一切就好了'，而应该有情感代价",
            "timing": "误会解除可以是高潮前还是高潮本身，取决于后续冲突从哪里来",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm_note("言情叙事冲突机制研究")],
        tags=["言情", "情节", "误会", "节奏"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre="言情",
        slug="rom-ea-falling-in-denial",
        name="否认中坠落弧",
        narrative_summary="主角知道自己在动心，但一直告诉自己'不是那种意思'——读者比主角更早知道，这种信息差是言情最甜的张力之一",
        content_json={
            "stage1": "初识阶段：主角对对方有情绪反应但用其他理由解释（讨厌他/只是有趣/只是感激）",
            "stage2": "习惯阶段：开始在意对方的存在，但继续否认（只是因为在一起的时间长）",
            "stage3": "危机阶段：对方可能离开或被别人喜欢，否认开始动摇",
            "turning_point": "一个让主角无法再否认的时刻——不需要是对方的表白，可以是一个动作或选择",
            "reader_pleasure": "读者在stage1就知道了，享受主角慢慢醒悟的过程",
        },
        source_type=L, confidence=0.72,
        source_citations=[llm_note("言情感情发展心理弧设计研究")],
        tags=["言情", "情感弧", "否认", "甜点"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre="言情",
        slug="rom-ea-second-chance",
        name="重逢旧情弧",
        narrative_summary="曾经有过感情（或错过感情）的两人在多年后重新相遇，旧伤未愈但也无法否认当初的真实，比初恋更复杂，比陌生人更危险",
        content_json={
            "setup": "分离原因必须真实充分，不能是'因为误会分开'（太廉价）——应该是真实的价值观冲突或不可抗力",
            "reunion_tension": "重逢时双方都有新的人生，不是简单回到原点",
            "healing_vs_reopening": "重逢可以是愈合也可以是揭开旧伤，两种方向各有叙事张力",
            "stakes": "这次如果再失去，代价比第一次更重——因此每个选择都更沉重",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("二次相逢言情叙事研究")],
        tags=["言情", "重逢", "旧情", "治愈"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="言情",
        slug="rom-tm-choice-not-fate",
        name="选择而非命运主题",
        narrative_summary="真正的言情主题是'选择爱'而非'被命运安排'——只有当两人在有其他选择时选择彼此，感情才有重量",
        content_json={
            "core_principle": "每段感情关系必须有一个真实的'选择时刻'，有退路的情况下仍然选择前进",
            "narrative_expression": "制造真实的其他选项（其他追求者/职业机会/离开的可能），让主角的选择变得有意义",
            "avoid": "天命CP/注定在一起的设定——这让感情变成履行命运而非主动选择",
            "emotional_weight": "我可以不爱你，但我选择爱你——这才是情感力量所在",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm_note("言情主题哲学研究")],
        tags=["言情", "主题", "选择", "自由意志"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="言情",
        slug="rom-ac-perfect-couple",
        name="无冲突完美CP禁忌",
        narrative_summary="两人互相理解体贴、从不误会从不争吵、完美匹配——读者会在甜蜜中失去代入感，因为这不是真实的感情",
        content_json={
            "problem": "真实感情里有摩擦、有不理解、有需要调适的差异；完美CP让感情变成消费品",
            "why_bad": "读者代入感来自'我也会犯这个错'——完美CP切断代入",
            "correct_approach": "冲突来自两个都对的人在同一情境有不同判断，不是谁坏谁好",
            "golden_rule": "冲突必须让两人都有道理，否则就是作者在强行制造障碍",
        },
        source_type=L, confidence=0.75,
        source_citations=[llm_note("言情叙事冲突真实性研究")],
        tags=["言情", "反套路", "冲突", "真实感"],
    ),

    # ===========================================================================
    # 宫斗 × 扩充
    # ===========================================================================

    MaterialEntry(
        dimension="world_settings", genre="宫斗",
        slug="palace-ws-inner-court",
        name="后宫权力地形",
        narrative_summary="后宫不是爱情战场，是资源分配和政治代理的斗争场所，每个嫔妃都是她背后家族派来的棋子",
        content_json={
            "hierarchy": "皇后→贵妃→妃→嫔→贵人→常在→答应，每级对应不同资源访问权和政治价值",
            "power_flows": "皇帝宠爱→生育子嗣→外朝家族支持→内廷宫人控制权，四种权力相互转化",
            "intelligence_network": "宫女/太监是信息流动的毛细血管，每个角落都有人在听",
            "physical_geography": "位置即权力——靠近皇帝日常活动区域的宫殿比偏僻宫殿获得的信息多3倍",
        },
        source_type=L, confidence=0.68,
        source_citations=[wiki("后宫", "https://zh.wikipedia.org/wiki/後宮"), llm_note("后宫政治结构研究")],
        tags=["宫斗", "世界观", "后宫", "权力"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="宫斗",
        slug="palace-ca-empress-strategist",
        name="皇后权谋家",
        narrative_summary="坐在最高位置的人反而最危险——皇后的权力依附于皇帝，她的策略是在不依靠皇帝宠爱的情况下维持地位",
        content_json={
            "power_source": "礼仪权威（她是所有嫔妃的名义上母亲）+外朝家族（父兄的政治资本）",
            "vulnerability": "如果失去皇帝信任或外朝家族式微，礼仪权威也随之崩塌",
            "strategy": "控制规则而不是参与竞争——让竞争对手相互消耗，自己成为仲裁者",
            "internal_conflict": "曾经真心爱过皇帝，如今在感情和权力之间已经分不清边界",
            "anti_pattern": "皇后不应该从一开始就是纯粹坏人——她的手段来自她曾经被逼迫",
        },
        source_type=L, confidence=0.68,
        source_citations=[eval_src("中国历史皇后政治地位与策略研究")],
        tags=["宫斗", "皇后", "权谋", "女主"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="宫斗",
        slug="palace-ca-new-consort",
        name="新入宫的外来者",
        narrative_summary="来自外朝/民间的新面孔，不懂规则却反而有优势——她不知道'不能这样做'，反而走了别人不敢走的路",
        content_json={
            "advantage": "对规则的无知有时是真正的优势：她会做'不该做的事'，因为没人告诉她不能",
            "vulnerability": "每一步都踩着别人已经踩烂的坑，代价来自无知",
            "learning_arc": "从被规则伤害→主动学习规则→开始利用规则→最终选择打破规则",
            "key_decision": "当她学会了所有规则，她会选择成为另一个'老玩家'，还是用学到的知识走出第三条路",
        },
        source_type=L, confidence=0.67,
        source_citations=[llm_note("宫斗题材外来者叙事研究")],
        tags=["宫斗", "女主", "成长", "规则"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="宫斗",
        slug="palace-pp-alliance-betrayal",
        name="联盟-背叛-重组结构",
        narrative_summary="宫斗故事的核心节奏：联盟总是暂时的，背叛总是迟早的，每次背叛后产生新的敌对方和新的联盟，循环至最终决战",
        content_json={
            "alliance_logic": "联盟不基于感情而基于利益，所以双方都在算计联盟的有效期",
            "betrayal_types": ["主动背叛（对方的利用价值耗尽）", "被动背叛（被逼在两个联盟中选边）", "假背叛（为更大目标制造的策略性背叛）"],
            "aftermath": "被背叛方如何反应决定了她的策略水平：愤怒反扑/沉默等待/以背叛还背叛",
            "endgame": "最终局里所有盟友都变成了对手，主角靠什么赢？不是联盟，而是早就准备好的独立牌",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("宫斗联盟叙事结构研究")],
        tags=["宫斗", "情节", "联盟", "背叛"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="宫斗",
        slug="palace-pp-evidence-chain",
        name="证据链构建与毁灭",
        narrative_summary="宫斗核心技术：如何在不能被人看见的情况下收集证据，如何布局让对方在皇帝面前自己暴露",
        content_json={
            "collection_methods": "培养内线（宫女/太监）→截取书信→制造对话场景让目标人自己说→第三方见证人",
            "evidence_hierarchy": "物证（信物/书信）＞证人证词＞间接推断，越高级越难获取",
            "defense_strategy": "同时销毁对手的证据，让对手陷入'有苦说不出'的处境",
            "presentation": "证据不能自己呈上——最高明的方式是让皇帝'自己发现'",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm_note("宫斗叙事中证据与权谋设计研究")],
        tags=["宫斗", "情节", "证据", "谋略"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="宫斗",
        slug="palace-tm-cage-freedom",
        name="囚笼与自由主题",
        narrative_summary="宫殿是最精美的囚笼，权力越大锁链越重——最终的问题不是赢得权力游戏，而是赢了之后还剩下什么",
        content_json={
            "symbolic_meaning": "宫墙不只是物理隔绝，是主角内心越来越接受规则约束的象征",
            "freedom_definition": "在这个世界里，自由是什么？能走出宫门？能选择不参加游戏？能保住想保住的人？",
            "irony": "为自由而战的人往往在赢的那一刻坐上了最不自由的位置",
            "resolution": "真正的自由不来自赢得游戏，而来自在游戏里保住了某种不被游戏定义的东西",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm_note("宫斗题材主题哲学分析")],
        tags=["宫斗", "主题", "自由", "代价"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="宫斗",
        slug="palace-ac-one-sided-villain",
        name="单面坏人禁忌",
        narrative_summary="宫斗的对手是单纯邪恶的坏蛋——不让读者理解其动机，使冲突降格为'好人vs坏人'而非'战略vs战略'",
        content_json={
            "problem": "单面坏蛋消除了策略博弈的乐趣，宫斗变成打怪而非智斗",
            "why_bad": "读者享受宫斗是因为'我在旁边看两个聪明人互斗'，一旦其中一人明显愚蠢就无法成立",
            "correct_approach": "每个对手都有合理的动机和对局势的理性判断，她们只是和主角利益相反",
            "standard": "读者应该在看到对手的决策时说'她这样做是对的，但主角更聪明'，而不是'她怎么这么蠢'",
        },
        source_type=L, confidence=0.75,
        source_citations=[llm_note("宫斗叙事对手角色设计研究")],
        tags=["宫斗", "反套路", "对手", "智斗"],
    ),

    # ===========================================================================
    # 娱乐圈 × 扩充
    # ===========================================================================

    MaterialEntry(
        dimension="world_settings", genre="娱乐圈",
        slug="ent-ws-traffic-era",
        name="流量时代娱乐圈",
        narrative_summary="数据至上的互联网娱乐生态，粉丝经济取代专业评价，实力与人气脱钩——这个世界同时存在真正的艺术家和完全靠运营的空壳明星",
        content_json={
            "power_structure": "资本（影视公司/经纪公司）→流量（粉丝数据）→真实口碑（专业评价）三者互相博弈",
            "conflict_source": "流量明星抢占了资源，实力派沦为配角；但观众也慢慢开始厌倦流量",
            "dark_side": "卖人设/数据造假/互撕引流/粉丝控评是行业常态",
            "protagonist_position": "主角如何在这个系统里既不妥协又能存活——有没有第三条路？",
        },
        source_type=L, confidence=0.65,
        source_citations=[llm_note("当代娱乐圈生态叙事分析")],
        tags=["娱乐圈", "流量", "世界观", "现代"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="娱乐圈",
        slug="ent-ca-industry-insider",
        name="业内老人",
        narrative_summary="在娱乐圈摸爬滚打十几年的资深从业者，见过太多起落，对行业既没幻想也没放弃，是主角理解行业真相的导师或最大的障碍",
        content_json={
            "knowledge_value": "了解潜规则/真实运作机制/历史事件真相，是行走的信息库",
            "attitude": "对新人的善意基于'我也年轻过'，也可能基于'我需要一个棋子'",
            "dark_wisdom": "他/她教主角的不只是技能，还有'怎么保全自己'——这可能和主角的价值观冲突",
            "function": "作为'现实代言人'与主角的理想主义碰撞，但本人的结局是叙事对行业的最终评价",
        },
        source_type=L, confidence=0.67,
        source_citations=[llm_note("娱乐圈叙事导师角色设计研究")],
        tags=["娱乐圈", "配角", "导师", "行业"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="娱乐圈",
        slug="ent-pp-comeback-arc",
        name="谷底翻身弧",
        narrative_summary="从事业顶峰跌落谷底（黑料/意外/被替代），再以完全不同的方式重新证明自己——重来的不是原来那个人，是更好的那个",
        content_json={
            "fall_cause": "黑料必须有一定合理性（不是被无端陷害），主角要对坠落有部分责任",
            "valley_experience": "谷底期不能是纯受苦，而是真正的成长和改变期",
            "comeback_differentiation": "翻身方式必须和原来的成功路径不同——复制过去只是重复，真正的回来是带着新东西",
            "public_perception": "公众/粉丝的态度转变需要时间和代价，不能奇迹式瞬间翻盘",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm_note("娱乐圈题材翻盘叙事研究")],
        tags=["娱乐圈", "情节", "翻盘", "成长"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="娱乐圈",
        slug="ent-tm-real-self",
        name="真实自我与人设主题",
        narrative_summary="人设是职业工具还是人格囚笼——娱乐圈最核心的主题是：当所有人爱上的是你表演出来的自己，你怎么确认自己真实存在",
        content_json={
            "identity_crisis": "主角无法确定粉丝的爱是给'人设'还是给'真实的自己'",
            "performance_trap": "越成功的人设越难摘下——成功的代价是真实自我被更深地掩埋",
            "resolution_direction": ["选择一个人展示真实自我（CP对象）", "以某个作品展示真实自我（代表作品）", "接受表演与真实的边界从来不清晰"],
            "symbolic_actions": "素颜出现/说了一句不在剧本里的话/拒绝一次运营要求",
        },
        source_type=L, confidence=0.72,
        source_citations=[llm_note("娱乐圈身份认同主题研究")],
        tags=["娱乐圈", "主题", "身份", "真实"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="娱乐圈",
        slug="ent-ac-instant-fame",
        name="奇迹爆红禁忌",
        narrative_summary="主角一出道就凭一个机会爆红，所有人都爱她，没有挫折直接顶流——行业竞争的现实感完全消失",
        content_json={
            "problem": "娱乐圈设定的张力来自行业竞争的残酷现实；奇迹路线消除了这个前提",
            "why_bad": "读者对娱乐圈感兴趣恰恰是因为'成功很难且有特殊性'，轻松成功让行业设定失去意义",
            "correct_approach": "成功来自积累（哪怕是穿书主角的上辈子积累），行业的拒绝和挫折必须是真实的",
            "timeline": "哪怕是天才，从出道到真正被认可应该有合理的时间线",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm_note("娱乐圈叙事真实性研究")],
        tags=["娱乐圈", "反套路", "成功", "真实感"],
    ),

    # ===========================================================================
    # 穿书 × 扩充
    # ===========================================================================

    MaterialEntry(
        dimension="world_settings", genre="穿书",
        slug="tb-ws-plot-gravity",
        name="剧情引力世界观",
        narrative_summary="书中世界有自己的'剧情引力'：关键事件趋向于按原著发生，改变越大引力越强，直到强行修正——抵抗有代价",
        content_json={
            "gravity_mechanism": "微小改变可以轻易实现；中等改变需要代价；颠覆性改变会触发剧情修正力（NPC行为变异、外部事件牵引）",
            "information_asymmetry": "穿书者只知道大情节走向，不知道细节填充和真实动机——知识边界是设定核心",
            "blind_spots": "原著略写的章节是盲区；番外/隐藏支线是未知地雷",
            "world_response": "世界本身不是有意志的，但统计规律上总是把事件推向原著方向",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("穿书世界观设定分析研究")],
        tags=["穿书", "世界观", "剧情", "规则"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="穿书",
        slug="tb-ws-otome-game",
        name="乙女游戏世界设定",
        narrative_summary="穿进乙女游戏世界，攻略路线是预设的代码，但'人物'有了真实感情——系统逻辑与人性逻辑的冲突是核心张力",
        content_json={
            "game_logic": "好感度/触发事件/攻略条件，这些是游戏规则，但穿书者知道它们背后是可以被真实情感覆盖的",
            "character_awakening": "攻略对象知道/不知道自己在游戏里，各有不同叙事张力",
            "heroine_position": "原女主角也是一个人，不是障碍——处理她的方式是穿书女主三观的体现",
            "system_break": "当游戏人物开始做游戏攻略路线以外的事，说明他们已经是真实的人了",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("乙女游戏穿书设定分析")],
        tags=["穿书", "乙女游戏", "设定", "反派"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="穿书",
        slug="tb-ca-meta-aware-heroine",
        name="元意识穿书女主",
        narrative_summary="知道自己在书里的女主，必须在'读者/玩家心态'和'真实投入情感'之间完成关键转变——始终旁观者的故事是无聊的",
        content_json={
            "initial_mindset": "把一切当游戏攻略，理性计算收益，保持情感距离",
            "crack_in_armor": "某个时刻发现书中人物比原著描述更真实，冷静计算开始失效",
            "turning_point": "做了一个纯情感驱动的决定，不符合'理性攻略'——意识到自己已经真的在乎",
            "cost_of_investment": "真正在乎意味着真正可以失去，这是元意识女主必须接受的代价",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("穿书主角元意识叙事研究")],
        tags=["穿书", "女主", "元意识", "成长"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="穿书",
        slug="tb-ca-npc-person",
        name="NPC觉醒角色",
        narrative_summary="原著中的工具性角色（陪衬/背景板）穿书女主进入后被真正对待，逐渐发展出原著从未有过的真实性格",
        content_json={
            "function_in_story": "原著里只有几句台词，穿书女主的到来给了他/她真正被看见的机会",
            "awakening_process": "从按照设定行动→感受到被不同对待→开始做设定以外的事",
            "narrative_value": "NPC觉醒是对穿书影响的最好证明：主角改变了书里的世界，而不只是自己的命运",
            "relationship_depth": "往往成为穿书女主最真实的情感关系——因为这段关系完全是原著以外生长出来的",
        },
        source_type=L, confidence=0.67,
        source_citations=[llm_note("穿书题材NPC觉醒叙事研究")],
        tags=["穿书", "配角", "觉醒", "真实感"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="穿书",
        slug="tb-pp-butterfly-effect",
        name="蝴蝶效应情节链",
        narrative_summary="每次改变原著情节都引发连锁反应，越来越多的分支偏离原著，主角从'按攻略走'变成'真正面对未知'",
        content_json={
            "first_change": "一次看似微小的改变（救了本该死的配角/说了一句原著没有的话）",
            "cascade": "微小改变影响后续事件，导致原著节点以不同方式抵达或根本不出现",
            "unknown_territory": "当改变积累到足够多，主角的'原著知识'开始失效，开始真正面对未知",
            "dramatic_shift": "从有先知优势到和所有人一样面对未来——这是穿书故事最重要的转变",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("穿书蝴蝶效应叙事研究")],
        tags=["穿书", "情节", "蝴蝶效应", "未知"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="穿书",
        slug="tb-tm-fiction-reality",
        name="虚构与真实主题",
        narrative_summary="穿书的核心哲学问题：如果你知道对方是'小说中的人物'，你对ta的情感是真实的吗？——真实性不来自本体论而来自关系本身",
        content_json={
            "core_question": "虚构角色的感情是否是'真的'感情？主角是爱上了'书里的人设'还是真实的人？",
            "resolution": "关系本身的互动是真实的，不论起点在哪——真实性由当下的投入决定",
            "narrative_moment": "主角第一次在心里不称呼对方的'原著名字'而是叫ta自己的名字",
            "thematic_tension": "如果主角回到现实，书中一切是否仍然存在？这个问题不需要回答，只需要主角选择不去想",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm_note("元小说哲学与穿书叙事研究")],
        tags=["穿书", "主题", "虚构", "真实"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="穿书",
        slug="tb-ac-omniscient-cheatcode",
        name="全知作弊禁忌",
        narrative_summary="穿书主角完全记得原著所有细节，从不出现记忆盲区——叙事张力的核心来源（信息差和不确定性）被彻底消除",
        content_json={
            "problem": "如果主角什么都知道，'原著的不确定性'就无法产生张力",
            "why_bad": "读者跟着全知主角等剧情验证，而不是一起面对未知——参与感消失",
            "correct_approach": "原著只记得'大结局'和'高光片段'，细节记忆有错误，略写章节完全空白",
            "warning_signs": ["主角提前知道三卷后的剧情", "记得每个配角的死亡时间", "完全没有记忆偏差"],
        },
        source_type=L, confidence=0.75,
        source_citations=[llm_note("穿书叙事信息差设计研究")],
        tags=["穿书", "反套路", "信息差", "张力"],
    ),

    # ===========================================================================
    # 种田 × 扩充
    # ===========================================================================

    MaterialEntry(
        dimension="world_settings", genre="种田",
        slug="farm-ws-spirit-soil",
        name="灵脉土地生态系统",
        narrative_summary="土地不是惰性容器，而是有灵性的生态系统：灵脉影响作物品质，天气与灵气互动，生物相互共生，乱用则反噬",
        content_json={
            "soil_layers": "表层灵土（普通作物）→中层灵壤（灵植专用）→深层灵脉（不可随意开凿）",
            "symbiosis": "灵兽为灵植授粉并驱虫；灵植为灵兽提供食物；失去任何一方都破坏平衡",
            "seasonal_qi": "春分时灵气上涌利于播种；夏至灵气最旺盛；秋分灵气内收利于药材结成；冬至灵气入地蛰伏",
            "anti_overuse": "过度开采灵脉会导致土地'疲劳'，需要休养——不是无限开挂的来源",
        },
        source_type=L, confidence=0.70,
        source_citations=[wiki("二十四节气", "https://zh.wikipedia.org/wiki/二十四节气"), llm_note("仙侠农业生态系统设计研究")],
        tags=["种田", "世界观", "灵脉", "生态"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="种田",
        slug="farm-ws-inn-economy",
        name="修士客栈经济生态",
        narrative_summary="以修士路过需求为核心的驿站客栈经济：信息、稀有食材、特殊服务、秘境地图构成差异化竞争力",
        content_json={
            "customer_tiers": "散修（基本需求：食宿+情报）→宗门弟子（任务补给+安全休整）→大能（极稀有食材+绝密情报）",
            "differentiation": "普通客栈有什么？灵气食物。主角的客栈特别在哪里？主角亲自种植的独家灵植料理",
            "information_value": "消息比灵石更值钱；主角的客栈应该成为情报中转站",
            "pricing_strategy": "按修为高低差异定价合理（高修为者需求稀少，用服务换信息）",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm_note("修仙客栈经济体系设计研究")],
        tags=["种田", "客栈", "经济", "世界观"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="种田",
        slug="farm-ca-retired-great-cultivator",
        name="隐居大能",
        narrative_summary="曾经纵横天下，如今以普通农夫/老丈/孤寡身份隐居，实力已臻至境但选择了另一种生活——出手时有代价，绝不是随叫随到的保险",
        content_json={
            "reason_for_hiding": "真实的：失去了战斗的意义/寻找生命另一种可能/旧日因果还没化解",
            "interaction_style": "对主角的帮助是暗中的、间接的，而不是直接碾压解决问题",
            "reveal_pacing": "读者逐渐感觉到他的不寻常，但不是一次性大揭秘——细节一点点积累",
            "cost_of_acting": "出手意味着暴露身份，意味着旧日因果来找门，他不是无代价的后援",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("种田仙侠隐居高人角色设计研究")],
        tags=["种田", "配角", "隐居", "高人"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="种田",
        slug="farm-pp-seasonal-structure",
        name="四季叙事节奏",
        narrative_summary="以二十四节气为自然骨架，每季节有对应的农活、情感节点、小危机、小收获，形成天然的章节节奏",
        content_json={
            "spring": "清明→谷雨：播种期，新开始新人物，上一季留下的种子开始发芽（字面和隐喻）",
            "summer": "立夏→大暑：生长期，田间管理最费力，外部威胁也最活跃，主角压力最大",
            "autumn": "立秋→寒露：收获期，一季辛苦的回报，也是整理关系/情感的时节",
            "winter": "立冬→大寒：蛰伏期，农活最少，反而是主角内心成长的深水期，为下一年布局",
        },
        source_type=L, confidence=0.72,
        source_citations=[wiki("二十四节气", "https://zh.wikipedia.org/wiki/二十四节气"), llm_note("节气叙事节奏设计研究")],
        tags=["种田", "情节", "节气", "节奏"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="种田",
        slug="farm-st-harvest-sensory",
        name="灵植收获感官场景",
        narrative_summary="收获不只是数值提升，是五感的盛宴——颜色、气味、触感、灵气涌动的质感，让读者真实感受到劳动的回报",
        content_json={
            "visual": "灵植成熟时的颜色变化（青→金/红→白），表皮的光泽度，果实的通透感",
            "olfactory": "每种灵植独特的香气层次：基础气味+灵气特有的清甜/药香/木质感",
            "tactile": "灵植与普通蔬菜的触感差异：灵气充盈时微微弹手，成熟时有温热感",
            "sound": "灵植从土地离开时轻微的'根系松动'声，或者果实自然脱落的时机",
            "aura_sense": "主角感受到灵气从植物流向手掌的过程，温热而充盈",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("种田题材感官描写技法研究")],
        tags=["种田", "场景", "感官", "收获"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre="种田",
        slug="farm-ea-belonging",
        name="归属感建立弧",
        narrative_summary="从'在这里种地是因为没有其他选择'到'我舍不得离开这里'——归属感不是一天建立的，是每一株植物每一个客人积累的",
        content_json={
            "stage1": "被迫接受/消极留在：做农活是为了生存，不是热爱",
            "stage2": "发现有趣：某株植物特别有趣，某个客人的故事让主角觉得这里有意义",
            "stage3": "舍不得：第一次拒绝离开的机会，意识到有什么比外面的世界更吸引自己",
            "stage4": "主动选择：有能力离开却选择留下，因为这里已经是'家'而不是'避难所'",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm_note("种田题材情感弧设计研究")],
        tags=["种田", "情感", "归属", "成长"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="种田",
        slug="farm-tm-slow-rhythm",
        name="慢节奏作为抵抗主题",
        narrative_summary="种地的慢对抗了修仙世界的'快速升级'逻辑——主角用慢选择了另一种存在方式，这是现代焦虑的隐喻和解药",
        content_json={
            "cultural_context": "快节奏现代生活在穿书/异世界叙事中的投影——主角在另一个世界实现了读者想要的'慢下来'",
            "resistance": "种田主角不是弱者，而是主动拒绝了'必须变强'的游戏规则",
            "paradox": "在种田时，主角往往不知不觉变强了——但这个强大是副产品，不是目的",
            "reader_catharsis": "读者通过主角的慢生活完成焦虑释放；不要让主角的慢生活充满焦虑",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm_note("种田题材社会主题研究"), wiki("星露谷物语", "https://zh.wikipedia.org/wiki/星露谷物語")],
        tags=["种田", "主题", "慢生活", "抵抗"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="种田",
        slug="farm-ac-instant-expert",
        name="天生农业专家禁忌",
        narrative_summary="穿书后立刻精通所有灵植知识、所有配方、所有种植技巧——没有学习过程，等于把生活质感最重要的来源消除了",
        content_json={
            "problem": "种田的乐趣一部分在于'探索和发现'，天生全知消除了这个乐趣",
            "why_bad": "读者享受的是主角'搞清楚这株植物为什么不长/找到了正确方法'的过程，不是直接给答案",
            "correct_approach": "前世知识（农学/医学/烹饪）是有价值的基础，但灵植有其独特规律需要实际摸索",
            "learning_sources": "错误→观察→询问老前辈→意外发现，这才是真实的学习曲线",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm_note("种田题材主角能力设计研究")],
        tags=["种田", "反套路", "学习", "真实感"],
    ),

    # ===========================================================================
    # 心理惊悚 × 扩充
    # ===========================================================================

    MaterialEntry(
        dimension="world_settings", genre="心理惊悚",
        slug="psythr-ws-unreliable-reality",
        name="不可靠现实设定",
        narrative_summary="主角对现实的感知本身是不可信的——但读者必须在一定程度上跟随主角的感知，这种'共同被欺骗'是心理惊悚核心张力",
        content_json={
            "unreliability_source": "心理创伤导致记忆空白/药物影响感知/主角有意无意的认知偏差",
            "reader_position": "读者比主角知道更多（某些细节主角注意不到）或更少（只能看到主角看到的）",
            "atmosphere": "日常场景反复出现细微不对劲的信号，主角不确定是否真实",
            "reveal_structure": "现实的不可靠性最终以一个单一事实揭穿——'所有不对劲都有了解释'的那一刻",
        },
        source_type=L, confidence=0.68,
        source_citations=[llm_note("心理惊悚不可靠叙事结构研究")],
        tags=["心理惊悚", "世界观", "不可靠", "叙事"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="心理惊悚",
        slug="psythr-ca-obsessive-antagonist",
        name="执念型反派",
        narrative_summary="以一种对外人不可理解的执念为行动核心，内在逻辑极端自洽——越理解他/她，越恐惧",
        content_json={
            "obsession_source": "真实的创伤或剥夺（不是天生邪恶），发展成了与世界格格不入的扭曲回应",
            "internal_logic": "在ta的世界观里，ta的行为是完全合理的——这是恐怖所在",
            "reader_position": "读者在理解的过程中感受到恐惧：'我明白了为什么，但这还是错的'",
            "horror_type": "不是鬼怪的恐怖，是人可以走到这一步的恐怖",
        },
        source_type=L, confidence=0.70,
        source_citations=[llm_note("心理惊悚反派心理学设计研究")],
        tags=["心理惊悚", "反派", "执念", "心理"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="心理惊悚",
        slug="psythr-pp-reveal-layers",
        name="洋葱式揭露结构",
        narrative_summary="每次揭露都只是表层，更深一层才是真相——但最后的真相不能太远离读者的早期直觉，否则会感觉被愚弄",
        content_json={
            "layer1": "表面事件：明显的异常/罪行，有显而易见的解释",
            "layer2": "第一层真相：推翻表面解释，揭露更深动机（但仍然不是全貌）",
            "layer3": "核心真相：颠覆前两层，让所有细节重新有了一个统一解释",
            "fairplay": "核心真相必须在前面埋过线索，只是读者被引导关注了错误的线索",
            "avoid": "连续三次'其实这才是真相'——阅读合同破裂，读者失去信任",
        },
        source_type=L, confidence=0.72,
        source_citations=[llm_note("心理惊悚多层揭露叙事研究")],
        tags=["心理惊悚", "情节", "揭露", "反转"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="心理惊悚",
        slug="psythr-tm-perception-reality",
        name="感知与现实主题",
        narrative_summary="我们看到的现实是否就是现实？心理惊悚的终极主题不是'谁是凶手'而是'我的感知可信吗'",
        content_json={
            "philosophical_core": "所有感知都经过大脑过滤，受情绪、记忆、期待影响——完全客观的感知不存在",
            "narrative_expression": "主角的感知被动摇，读者的感知也被动摇",
            "resolution_options": ["真相是客观的，只是主角感知有误（最常见）", "真相是主观的，因人而异（更高阶）", "真相不重要，重要的是主角如何选择行动"],
            "thematic_question": "如果你不能完全信任自己的感知，你还能做出道德决策吗？",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm_note("心理惊悚哲学主题研究")],
        tags=["心理惊悚", "主题", "感知", "真实"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="心理惊悚",
        slug="psythr-ac-twist-for-twist",
        name="为反转而反转禁忌",
        narrative_summary="结尾的超级反转只是为了惊人而非叙事必要——当反转不能让前面所有细节重新有意义时，只是廉价的噱头",
        content_json={
            "problem": "反转必须让读者回想'原来第X章那里是这个意思'，若反转与前文割裂则失去叙事价值",
            "test": "好的反转：读者合上书再想想，觉得所有细节都有了新的解读。坏的反转：读者觉得被骗了",
            "correct_approach": "反转在服务故事的情感核心，不是展示作者的聪明",
            "warning_signs": ["最后一章出现之前完全没有暗示的事实", "反转使主角的所有行动变得毫无意义", "反转是'原来一切都是梦'级别"],
        },
        source_type=L, confidence=0.75,
        source_citations=[llm_note("心理惊悚叙事反转设计研究")],
        tags=["心理惊悚", "反套路", "反转", "叙事"],
    ),

    # ===========================================================================
    # 通用 (genre=None) 扩充
    # ===========================================================================

    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="gen-ds-power-subtext",
        name="台词下权力潜台词写法",
        narrative_summary="真正的权力对话不在明说，而在语气、称谓、话题选择、沉默时机——读者感受到的威胁比字面更真实",
        content_json={
            "title_signals": "称呼从正式到亲密，或从亲密突然变正式，都是权力变化信号",
            "topic_control": "谁能自然转移话题谁掌握主动权；被打断和无法打断对方",
            "silence_as_weapon": "有权力的人让沉默延续，无权力的人急着填满沉默",
            "compliment_threat": "用赞美包装威胁：'你做得很好，就像我期待的那样'（含义：如果你不按我期待的做）",
            "example_structure": "表层对话：谈论天气/茶叶/明显无关的事。潜台词：双方都知道真正在谈判什么",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm_note("权力对话写作技法研究")],
        tags=["通用", "台词", "权力", "写作技法"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="gen-ds-emotional-compression",
        name="情感压缩对话写法",
        narrative_summary="最重的情绪用最轻的话说出来——克制不是淡漠，而是感情太满溢出时的最小表达",
        content_json={
            "principle": "情感密度反比于文字密度：越是关键的情感节点，台词越短",
            "examples": [
                "再见 vs 我一直在等你说这句话（再见更重）",
                "你没事吧 vs 大段的关心描述（你没事吧更沉）",
                "对不起 vs 详细的道歉陈述（独立的'对不起'更有分量）",
            ],
            "context_requirement": "压缩台词有效的前提是前面已经积累了足够的情感密度",
            "body_language_pairing": "短台词配上精准的身体动作/表情，信息量倍增",
        },
        source_type=L, confidence=0.75,
        source_citations=[llm_note("对话写作情感克制技法研究")],
        tags=["通用", "台词", "情感", "写作技法"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre=None,
        slug="gen-st-reunion-aftermath",
        name="分离后重逢场景",
        narrative_summary="两人经历重大变故后再次相见——不能回到分离前，也还没到确定新关系，在陌生感和熟悉感的夹缝里重新认识彼此",
        content_json={
            "atmosphere": "物理空间熟悉，但人变了——用环境的相同衬托关系的不同",
            "first_moment": "相见的第一秒最重要：眼神的第一个动作/第一句话的选择/谁先开口",
            "small_actions": "不自觉的旧习惯/对方改变了的习惯，在细节里显现离开的时间",
            "subtext": "两人都在说无关紧要的话，但都在观察对方'我们还是那种关系吗'",
            "turning_point": "一个打破表面气氛的时刻——笑/沉默/意外触碰/说了一句真心话",
        },
        source_type=L, confidence=0.72,
        source_citations=[llm_note("重逢场景写作技法研究")],
        tags=["通用", "场景", "重逢", "情感"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="gen-ea-grief-stages",
        name="悲痛情感弧",
        narrative_summary="失去重要事物（人/梦想/身份）后的情感演变——不是线性的'悲伤→释然'，而是回绕的、不可预期的真实处理过程",
        content_json={
            "non_linear_stages": "否认→愤怒→交涉→抑郁→接受，但顺序不固定，可以反复回到早期阶段",
            "trigger_mechanism": "愈合中的伤口被意外触碰：一首歌/一个气味/一句无意的话",
            "functional_grief": "人在深度悲痛中仍然运转——去买东西/完成任务/照顾他人，悲痛和功能并存",
            "character_voice": "悲痛中的人往往不愿意谈论，但内心os异常丰富——行动与内心的落差",
            "avoid": "悲痛不应该在一章内'完成'，也不应该用来催泪而后立刻消失",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm_note("悲痛情感弧叙事写作研究")],
        tags=["通用", "情感弧", "悲痛", "写作技法"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="gen-tm-cost-of-power",
        name="力量代价母题",
        narrative_summary="真正的力量总有代价——不是惩罚性设定，而是价值哲学：你为了变强放弃了什么，这个放弃是否值得",
        content_json={
            "types_of_cost": ["孤立（变强意味着与普通人的距离）", "失去（用某种珍贵的东西换取力量）", "改变自我（为了做到某事成为不一样的人）"],
            "narrative_question": "主角在变强的过程中，是否意识到代价？是否在某个时刻感到不值得？",
            "resolution": "不是'代价太重就不值得变强'，而是'知道代价仍然选择，这个选择有重量'",
            "anti_power_fantasy": "无代价的力量是权力幻想；有代价的力量才是关于人的故事",
        },
        source_type=L, confidence=0.75,
        source_citations=[llm_note("力量与代价母题文学研究")],
        tags=["通用", "母题", "代价", "力量"],
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
    parser = argparse.ArgumentParser(description="Seed material library - batch 2")
    parser.add_argument("--dry-run", action="store_true", help="Preview without inserting")
    parser.add_argument("--genre", default=None, help="Only seed entries for this genre")
    args = parser.parse_args()
    asyncio.run(seed_library(dry_run=args.dry_run, filter_genre=args.genre))


if __name__ == "__main__":
    main()
