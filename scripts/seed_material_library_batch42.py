"""Batch 42: niche-genre factions + character_templates

Fills under-served:
- factions for 娱乐圈 / 末日 / 赛博朋克 / 灵异 / 校园 / 直播
- character_templates with named exemplars per genre
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bestseller.infra.db.session import session_scope
from bestseller.services.material_library import MaterialEntry, insert_entry


def llm_note(note: str) -> dict:
    return {"source": "llm_synth", "note": note}


def wiki(title: str, note: str = "") -> dict:
    return {"source": "wikipedia", "title": title, "note": note}


ENTRIES: list[MaterialEntry] = [
    # ---------- factions (8) ----------
    MaterialEntry(
        dimension="factions", genre="娱乐圈",
        slug="faction-celeb-agency-megacorp",
        name="娱乐圈派系：顶流经纪公司 / 资本系",
        narrative_summary="娱乐圈三大势力：经纪公司（培养+经营）、资本（影视投资+资源置换）、平台（爱腾优芒+卫视）。三方互相博弈，决定一个艺人能上哪部戏。",
        content_json={
            "agencies": "壹心娱乐（顶流系）/ 嘉行传媒（杨幂系）/ 唐人影视（古装系）/ 工作室独立（流量自营）",
            "capital_players": "阿里影业 / 腾讯影业 / 万达影视 / 光线传媒 / 华策 — 资本决定主投/联投地位",
            "platforms": "爱奇艺 / 优酷 / 腾讯视频 / 芒果TV / 央八 / 浙江卫视 — 头部内容必上一线平台",
            "rivalry_dynamics": "经纪公司争咖位 + 资本争代理权 + 平台争独家；艺人卡在中间被消耗",
            "anti_cliche": "不要写'女主签了壹心就一路顺' — 写每个剧组背后的资本博弈+经纪公司妥协",
            "activation_keywords": ["娱乐圈", "经纪公司", "资本", "平台", "派系"],
        },
        source_type="llm_synth", confidence=0.8,
        source_citations=[llm_note("中国娱乐圈生态调研，参考 36kr 娱乐资本论")],
        tags=["factions", "娱乐圈", "经纪公司", "资本"],
    ),
    MaterialEntry(
        dimension="factions", genre="末日",
        slug="faction-doomsday-survivor-bases",
        name="末日势力：幸存者基地三方鼎立",
        narrative_summary="末世社会重组的三大势力：军方残部（秩序）、变异者联盟（异能优先）、商人公会（资源贸易）。每方有自己的伦理+生存策略+硬冲突。",
        content_json={
            "factions": "1) 军方残部（前国家军队，强秩序+禁止变异/疫苗派）/ 2) 变异者联盟（异能至上+反人类原教旨）/ 3) 商人公会（资源贸易，谁付钱卖给谁）",
            "secondary_factions": "宗教派（末日是神罚）/ 学者派（搞研究找疫苗）/ 科技派（机器人+AI 重建）/ 流民营（无组织）",
            "ideology_conflict": "军方=回到秩序 / 变异者=新人类登顶 / 商人=利益至上；三方常打代理人战",
            "anti_cliche": "不要二元化（'好基地 vs 坏基地'）；让每方都有正义性+残忍面",
            "activation_keywords": ["末日", "幸存者", "军方", "变异者", "商人公会"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("Walking Dead 派系生态 + 末日流通用结构")],
        tags=["factions", "末日", "幸存者", "三方"],
    ),
    MaterialEntry(
        dimension="factions", genre="赛博朋克",
        slug="faction-cyberpunk-megacorps",
        name="赛博朋克：六大巨型公司",
        narrative_summary="赛博朋克题材的核心势力是 megacorps：每家垄断一个领域（武器/医药/媒体/网络/食品/能源），公司高于国家，拥有私军和治外法权。",
        content_json={
            "megacorps": "1) Arasaka（武器+安全）/ 2) Militech（军工）/ 3) Biotechnica（医药+食品）/ 4) Network 54（媒体）/ 5) Petrochem（能源）/ 6) NetWatch（网络监管）",
            "rivalry_pattern": "公司间冷战+代理人战；不破坏 'Net 公约' 的前提下用黑客/雇佣兵互相打",
            "underclass": "街头帮派（瓦伦丁/布吉斯瓦/动物帮）/ 反企业组织（NetRunners 联盟/赛博恐怖分子）",
            "ngo_or_government": "联合国残部（弱）/ 城邦市政府（被公司架空）/ 媒体（被公司收买）",
            "anti_cliche": "不要二元化善恶；公司间互打+下层贫民起义+主角无意中被卷入；每家公司都有自己的 PR 美化",
            "activation_keywords": ["赛博朋克", "megacorp", "Arasaka", "公司战争", "街头帮派"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Cyberpunk_2020"), wiki("Cyberpunk_2077"), llm_note("Mike Pondsmith TRPG 体系")],
        tags=["factions", "赛博朋克", "megacorp", "公司"],
    ),
    MaterialEntry(
        dimension="factions", genre="灵异",
        slug="faction-occult-eight-clans",
        name="灵异组织：四大门 / 五大家 / 八派",
        narrative_summary="民俗灵异题材的派系结构。东北出马仙：胡黄常蟒（四大门）+ 胡黄灰白柳（五大家仙）；茅山：上清+灵宝+三皇；龙虎山：正一道；藏传：宁玛/萨迦/噶举/格鲁。",
        content_json={
            "four_gates": "胡（狐仙）/ 黄（黄鼠狼）/ 常（蛇）/ 蟒（大蟒）— 东北出马仙四大门",
            "five_clans": "胡（狐）/ 黄（黄鼠狼）/ 灰（老鼠）/ 白（刺猬）/ 柳（蛇）— 出马五大家仙",
            "maoshan_branches": "上清派 / 灵宝派 / 三皇派 — 茅山三大主脉",
            "tianshi_lineage": "正一道（龙虎山张天师）/ 全真道（北宗，道教改革）",
            "tibetan": "宁玛派（红教）/ 萨迦派（花教）/ 噶举派（白教）/ 格鲁派（黄教，达赖班禅）",
            "rivalry": "茅山和出马看不上彼此（仪式 vs 仙家附体）；正一道和全真道千年争夺道统",
            "anti_cliche": "不要把派系写成纯敌对；写出每派的局限性+互补可能（如茅山请出马仙合作大案）",
            "activation_keywords": ["灵异", "出马仙", "茅山", "四大门", "五大家"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("Chinese_folk_religion"), wiki("Maoshan"), wiki("Quanzhen_School")],
        tags=["factions", "灵异", "出马仙", "茅山", "民俗"],
    ),
    MaterialEntry(
        dimension="factions", genre="校园",
        slug="faction-campus-cliques",
        name="校园派系：学霸 / 富二代 / 文艺 / 体育 / 老师",
        narrative_summary="校园文的隐性派系：学霸圈（前 10 名）/ 富二代圈（家世圈）/ 文艺圈（社团明星）/ 体育圈（校队大佬）/ 老师阵营（班主任+教导主任+校长）。每圈有自己的领袖+规则。",
        content_json={
            "student_cliques": "1) 学霸圈（学神+复习资料垄断）/ 2) 富二代（豪车+社交场+给人贷款）/ 3) 文艺（社团+校刊+话剧社）/ 4) 体育（校队+国旗手+军训教官）/ 5) 边缘（社恐+插班生+留学生）",
            "teacher_factions": "班主任派 / 教导主任派 / 校长派 / 资深骨干派 — 互相争抢资源+排课+评优",
            "school_levels": "重点高中 / 普通高中 / 民办高中 / 国际部 — 学校的派系背景影响家长资源",
            "rivalry_pattern": "学霸 vs 富二代（实力 vs 背景）/ 文艺 vs 体育（艺术 vs 力量）/ 老师 vs 老师（评优）",
            "anti_cliche": "不要把富二代=纯坏 / 学霸=纯善；每个圈都有内部派系+暗面",
            "activation_keywords": ["校园", "学霸", "富二代", "文艺", "体育", "派系"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("八月长安《最好的我们》、辛夷坞《致青春》校园生态综合")],
        tags=["factions", "校园", "学霸", "富二代"],
    ),
    MaterialEntry(
        dimension="factions", genre="无限流",
        slug="faction-infinite-flow-sectors",
        name="无限流：主神空间七大势力",
        narrative_summary="无限恐怖类的主神空间常分多个势力：暗黑佣兵 / 太阳金牌 / 中立公会 / 邪魔 / 神佑教廷 / 自由独狼 / 主神之子。每势力争夺副本+任务+空间话语权。",
        content_json={
            "factions": "1) 暗黑佣兵（杀人爽手）/ 2) 太阳金牌（正义+保护新人）/ 3) 中立公会（贸易+情报）/ 4) 邪魔（牺牲新人+黑暗仪式）/ 5) 神佑教廷（圣战）/ 6) 自由独狼（不结盟）/ 7) 主神之子（特殊血统）",
            "rivalry": "太阳 vs 暗黑：理念战；中立公会卖情报给所有人；邪魔被全 5 方追杀；独狼是潜在大佬",
            "growth_path": "新人选阵营 → 老兵升职 → 队长开公会 → 大佬建势力",
            "anti_cliche": "不要二元化；让主角先在邪魔阵营再叛逃 / 或太阳金牌内部腐败把他踢出",
            "activation_keywords": ["无限流", "主神空间", "公会", "佣兵", "正邪"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("zhttty《无限恐怖》派系结构衍生模板")],
        tags=["factions", "无限流", "公会", "派系"],
    ),
    MaterialEntry(
        dimension="factions", genre="武侠",
        slug="faction-wuxia-six-sects-extended",
        name="武侠：六大门派 / 七十二邪宗 / 朝廷势力",
        narrative_summary="武侠经典派系结构：少林+武当+峨眉+昆仑+华山+衡山 六大正派 / 日月教+五毒教+血刀门 邪魔 / 锦衣卫+东厂+西厂 朝廷 / 丐帮+魔教+各种地方帮派。",
        content_json={
            "six_sects": "少林（佛门正宗）/ 武当（道门正宗）/ 峨眉（女帜）/ 昆仑（剑道）/ 华山（剑+气两宗）/ 衡山（音律剑）",
            "evil_sects": "日月神教（魔教）/ 五毒教（南疆）/ 血刀门（藏地）/ 古墓派（独立）/ 星宿派（西域）/ 丁春秋系/欧阳锋系（个体大魔）",
            "court_factions": "锦衣卫（明）/ 东厂（明，宦官）/ 西厂（明，宦官）/ 大内高手 / 各省督抚军权",
            "neutral_factions": "丐帮（北丐）/ 漕帮 / 镖局 / 七大世家（慕容、姚、范、独孤、李等）",
            "anti_cliche": "不要写六大派+魔教二元；写六大派内部矛盾（华山剑气宗）+朝廷渗透+各方利益博弈",
            "activation_keywords": ["武侠", "六大门派", "魔教", "锦衣卫", "丐帮"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("金庸全集 + 古龙、梁羽生武侠派系综合")],
        tags=["factions", "武侠", "六大门派", "魔教"],
    ),
    MaterialEntry(
        dimension="factions", genre="科幻",
        slug="faction-scifi-galactic-empires",
        name="科幻：星际帝国/联邦/反抗军/外星种族",
        narrative_summary="星际科幻典型派系：人类联邦/帝国/共和国 + 外星种族联盟 + 商业贸易公司 + 反抗军/海盗 + 神秘前文明遗存。多边博弈是星际故事的骨架。",
        content_json={
            "human_polities": "1) 银河联邦（民主+人类首都地球）/ 2) 帝国（专制+殖民扩张）/ 3) 共和国（残部）/ 4) 自由商盟（去中心化）",
            "alien_factions": "1) 鸟系（高科技+傲慢）/ 2) 蜥蜴系（武力+宗教）/ 3) 类人系（盟友）/ 4) 群体智能（奇异）/ 5) 失落的先驱（神秘）",
            "non_state": "贸易公司（半官方）/ 海盗联盟 / 反抗军 / 神教（信仰先驱）/ 黑市黑客",
            "war_dynamics": "公开战 / 代理人战 / 信息战 / 文化战 / 经济战 — 每种战让外交官/特工/科学家成为主角候选",
            "anti_cliche": "不要写人类纯善 vs 外星纯恶；让外星有自己的伦理+宗教+利益",
            "activation_keywords": ["科幻", "星际", "联邦", "帝国", "外星", "反抗军"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Star_Trek"), wiki("Star_Wars"), llm_note("Asimov 基地+银河帝国综合")],
        tags=["factions", "科幻", "星际", "帝国"],
    ),

    # ---------- character_templates (8) ----------
    MaterialEntry(
        dimension="character_templates", genre="娱乐圈",
        slug="character-tpl-top-streamer-she",
        name="角色模板：顶流主播林晓晚（女主）",
        narrative_summary="出身西北小县城的草根主播，被算法推到顶流。表面甜美邻家，实际野心压抑。粉丝营销会高情商接梗。最大短板是对资本游戏一无所知。",
        content_json={
            "name": "林晓晚",
            "background": "甘肃定西农村→兰州师专辍学→快手/抖音直播→千万级顶流",
            "appearance": "165cm，圆脸甜美，左耳有一颗痣，喜欢马尾",
            "personality": "MBTI: ENFJ；表面甜美高情商+ 内心野心压抑+对家人愧疚",
            "speech_pattern": "说话带甘肃方言尾音 'sa'/'么'；急了会蹦出'瓜娃子'；公开场合切普通话",
            "growth_arc": "ch1: 被算法推上顶流 → ch10: 被资本看上 → ch20: 学会反向利用资本 → ch40: 自建工作室",
            "trauma": "母亲做促销员被城里人歧视 → 主角对'被瞧不起'敏感",
            "anti_cliche": "不要写'纯无脑甜女主'；要写她对粉丝/资本/家人三方撕裂的内心戏",
            "activation_keywords": ["顶流", "主播", "草根", "甘肃", "ENFJ"],
        },
        source_type="llm_synth", confidence=0.8,
        source_citations=[llm_note("快手主播头部实例 + ENFJ 性格综合")],
        tags=["character_templates", "娱乐圈", "顶流", "女主"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="末日",
        slug="character-tpl-doomsday-survivor-she",
        name="角色模板：末日女幸存者苏念",
        narrative_summary="末日前是普通护士，世界末日第三天觉醒治疗系异能（弱）。看似柔弱实则是医院黑暗面看多了的硬核理性派。最大优势：会救命。",
        content_json={
            "name": "苏念",
            "background": "上海三甲医院 ICU 护士→末日 D+3 觉醒治疗异能→无家人在世",
            "appearance": "170cm，瘦削，眼下有黑眼圈，习惯戴口罩",
            "personality": "MBTI: ISTJ；理性冷静+黑色幽默+ 关键时刻护人",
            "speech_pattern": "对人称呼按职业/家属称谓（不熟人='家属'）；急救时口令快+精准",
            "growth_arc": "ch1: 觉醒救活第一个幸存者 → ch15: 进入军方基地 → ch25: 揭露病毒源头 → ch50: 建立医疗中心",
            "trauma": "末日前一晚医院被丧尸冲垮，亲手补救失败的同事 → 对'救不到'极度焦虑",
            "anti_cliche": "不要写'圣母治疗师'；要让她做艰难选择（救一人弃一人/拒绝救坏人）",
            "activation_keywords": ["末日", "护士", "治疗系", "ICU", "ISTJ"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("末世救援题材综合 + ISTJ 性格")],
        tags=["character_templates", "末日", "护士", "女主"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="赛博朋克",
        slug="character-tpl-cyberpunk-hacker-they",
        name="角色模板：赛博黑客无名（中性）",
        narrative_summary="夜之城的传奇黑客，性别模糊（用'他们'代称）。皮下植入级别中端，主战脑机接口。表面冷淡，内核是失去全家的复仇者。",
        content_json={
            "name": "无名 / Anonymous",
            "background": "夜之城贫民窟→18 岁全家被 Arasaka 'NCPD 误击'灭门→自学黑客复仇",
            "appearance": "175cm，瘦，左眼是机械义眼（红色光学），后颈有数据接口插槽",
            "personality": "MBTI: INTJ；冷淡+计划+黑色幽默+对权力极端不信任",
            "speech_pattern": "代码隐喻：'flush this'/' '/' encrypted'； 急了切回街头西语+街头日语",
            "growth_arc": "ch1: 接受复仇任务 → ch10: 入侵 Arasaka 边缘服务器 → ch20: 发现幕后是更大阴谋 → ch40: 选择自杀式攻击",
            "trauma": "全家被灭门画面循环 + 亲哥哥被植入 cyberpsychosis → 对企业 PR 极端敏感",
            "anti_cliche": "不要写'酷盖独狼黑客'套路；要让无名有亲密的小队（每个都死过一次）+无法处理活人感情",
            "activation_keywords": ["赛博朋克", "黑客", "夜之城", "Arasaka", "INTJ"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Cyberpunk_2077"), llm_note("Neuromancer Case 模板派生")],
        tags=["character_templates", "赛博朋克", "黑客", "复仇"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="灵异",
        slug="character-tpl-occult-disciple-male",
        name="角色模板：出马仙弟子陈砚行",
        narrative_summary="东北黑龙江出马家族第七代弟子，22 岁。双修茅山+出马，正在面临升红册师试炼。表面寡言内心急于证明自己。",
        content_json={
            "name": "陈砚行",
            "background": "黑龙江哈尔滨道里区→陈家出马仙第七代→18 岁开堂",
            "appearance": "182cm，瘦高，颧骨突出，左手中指戴老式银戒指（仙家给的）",
            "personality": "MBTI: INFJ；寡言+敏感+对仙家极度敬畏+ 对城里人有距离感",
            "speech_pattern": "东北话+口头禅'仙家说'；办法事时切学术化（'此案因果纠葛'）",
            "growth_arc": "ch1: 接第一个独立大案 → ch15: 仙家暴露身份冲突 → ch25: 升红册师 → ch40: 揭露百年家族秘辛",
            "trauma": "12 岁丧母（仙家附体过度损耗）+ 父亲走火入魔失踪 → 对'仙家代价'极度警惕",
            "anti_cliche": "不要写'神棍'刻板印象；他是有学术背景的（兼读大学历史系）+ 现代医学常识",
            "activation_keywords": ["灵异", "出马仙", "陈家", "黑龙江", "INFJ"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("东北民俗调研 + INFJ 性格 + 茅山+出马双修罕见模板")],
        tags=["character_templates", "灵异", "出马仙", "男主"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="校园",
        slug="character-tpl-campus-genius-she",
        name="角色模板：校园学神顾思源（女主）",
        narrative_summary="高三全校第一，外表冷艳的'冰山学神'，内心其实是焦虑型 INFJ，靠规则感建立安全感。家庭高知背景但父母离异。",
        content_json={
            "name": "顾思源",
            "background": "北京海淀重点高中高三→全校第一→父母离异（学者家庭）",
            "appearance": "168cm，长发，戴金边眼镜，校服永远整洁",
            "personality": "MBTI: INFJ-T（焦虑型）；冷静外表+内心焦虑+完美主义+ 隐藏热情",
            "speech_pattern": "标准普通话+学术词汇'其实从语义上'/' 严谨地说'；急了会咬唇沉默",
            "growth_arc": "ch1: 转学新桌挑战她权威 → ch15: 学会接纳第二名 → ch25: 与父亲和解 → ch40: 选大学不再为父母",
            "trauma": "10 岁父母离异+从小被'必须第一'压力 → 对'让父母失望'极度恐惧",
            "anti_cliche": "不要写'冷艳学霸纯爽'；要写她在班级里的孤立+和真实朋友建立的笨拙过程",
            "activation_keywords": ["校园", "学神", "海淀", "INFJ", "完美主义"],
        },
        source_type="llm_synth", confidence=0.8,
        source_citations=[llm_note("八月长安《最好的我们》、辛夷坞青春题材综合 + INFJ 性格")],
        tags=["character_templates", "校园", "学神", "女主"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="穿书",
        slug="character-tpl-transmigrator-villainess",
        name="角色模板：穿书反派郡主沈蘅之",
        narrative_summary="现代社畜穿书后成了大女主小说里的反派恶毒郡主，原书第 12 章被女主毒杀。穿来后绑定了'保命系统'，必须改写命运又不能引起女主警觉。",
        content_json={
            "name": "沈蘅之（穿越前：李静雯，35 岁码农）",
            "background": "现代→996 公司加班猝死→穿成《摄政王的小娇妃》中反派郡主",
            "appearance": "原身 18 岁，160cm，柳叶眉，杏眼，红衣艳色（原书设定就是反派打扮）",
            "personality": "原身=作天作地反派；李静雯=社畜理性 INTP；融合后=表面任性私下精算的腹黑",
            "speech_pattern": "公开场合古文（'本郡主'/'休得'）；私下心里骂'卧槽'/'这剧情真的离谱'",
            "growth_arc": "ch1: 穿越觉醒+发现是被毒杀的反派 → ch15: 故意惹女主送嫁妆 → ch25: 被反向爱上 → ch40: 抢走原男主+拆原书",
            "trauma": "现代猝死前的孤独+穿越后的任务焦虑 → 对'重新被孤立'极度敏感",
            "anti_cliche": "不要写'反穿原女主'套路；让她和女主达成奇怪的同盟+原男主被她无视",
            "activation_keywords": ["穿书", "反派", "郡主", "社畜", "INTP"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("酱子贝《我家娘子才不是恶毒女配》、莺莺草《反派他过分美丽》模板派生")],
        tags=["character_templates", "穿书", "反派", "女主"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="种田",
        slug="character-tpl-farming-she",
        name="角色模板：种田女主秦娘子",
        narrative_summary="现代农学硕士穿越古代下嫁村农户。本就是技术党+精算型，下嫁后用现代农业知识翻身。隐忍+实用主义+家族观念强。",
        content_json={
            "name": "秦娘子（原名秦时月，27 岁农大硕士）",
            "background": "现代农大博士→意外死亡穿越→大唐贞观年间陇西农户家",
            "appearance": "原身 19 岁，156cm，瘦小，皮肤被晒得偏黑",
            "personality": "MBTI: ISTP；务实+寡言+技术党+ 关键时刻护短",
            "speech_pattern": "带学术词的白话（'其实是因为土壤肥力下降'）；急了会蹦农民俚语",
            "growth_arc": "ch1: 穿越下嫁傻汉 → ch15: 建大棚改种经济作物 → ch25: 培育新种被县令注意 → ch40: 进京献策成农官",
            "trauma": "穿越前在实验田事故中失去同伴+对古代女性地位极度警惕",
            "anti_cliche": "不要纯堆'空间产 2000 斤亩'；让她和族中长辈+夫家+县令的关系真实复杂",
            "activation_keywords": ["种田", "穿越", "农学", "陇西", "ISTP"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("种田流综合：寻找失落的爱情、希行、桃花露 模板")],
        tags=["character_templates", "种田", "穿越", "女主"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="心理惊悚",
        slug="character-tpl-psycho-thriller-shrink",
        name="角色模板：心理咨询师沈听澜",
        narrative_summary="35 岁注册心理师，京沪知名诊所合伙人。表面温和专业，内心是有未解决童年创伤的'黑色 ENFJ'。最危险的咨询师是她自己。",
        content_json={
            "name": "沈听澜",
            "background": "复旦心理学博士→沪上 H 心理诊所合伙人→ 35 岁未婚",
            "appearance": "170cm，长发盘起，永远穿米白/灰色 cape，左肩有童年烧伤疤",
            "personality": "MBTI: ENFJ-T（黑色暗面）；专业温和+ 危险共情+对'掌控感'极端依恋",
            "speech_pattern": "咨询时'我注意到你说...'/'你能多告诉我一点吗？'；私下会用术语形容自己",
            "growth_arc": "ch1: 接到一个名为'方域'的连环杀手案 → ch10: 发现凶手与自己有童年关联 → ch25: 自己也成了嫌犯 → ch40: 直面童年创伤",
            "trauma": "8 岁母亲精神病发烧伤她+父亲冷漠 → 选心理学是为了理解母亲 → 也变得擅长操纵情感",
            "anti_cliche": "不要写'白衣天使咨询师'；让她自己也是潜在反派+和警察合作但暗中阻挠",
            "activation_keywords": ["心理惊悚", "咨询师", "ENFJ-T", "童年创伤", "操纵"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("Mindhunter 系列+ Hannibal 心理咨询师反派衍生")],
        tags=["character_templates", "心理惊悚", "咨询师", "女主"],
    ),
]


async def main() -> None:
    print(f"Seeding {len(ENTRIES)} entries...\n")
    by_genre: dict[str, int] = {}
    by_dim: dict[str, int] = {}
    errors = 0
    async with session_scope() as session:
        for e in ENTRIES:
            try:
                await insert_entry(session, e, compute_embedding=True)
                by_genre[e.genre or "(通用)"] = by_genre.get(e.genre or "(通用)", 0) + 1
                by_dim[e.dimension] = by_dim.get(e.dimension, 0) + 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                print(f"  ✗ {e.slug}: {exc}")
    print(f"\nBy genre:     {by_genre}")
    print(f"By dimension: {by_dim}")
    print(f"\n✓ {len(ENTRIES) - errors} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    asyncio.run(main())
