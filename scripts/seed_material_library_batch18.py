"""
Batch 18: Thin-genre depth dive — 洪荒 / 萌宠 / 无限流 / 美食 / 游戏 / 机甲 /
赛博朋克 / 科幻 sub-genres each get multi-dimension expansion.
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
    # 洪荒 — 大幅深化
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="character_archetypes", genre="洪荒",
        slug="honghuang-arch-quasi-saint-failure",
        name="准圣求圣失败的执着者原型",
        narrative_summary="洪荒中常见准圣层级（大罗金仙顶点未成圣），一直追求圣位却无机缘。"
                          "其执念形成强大动机：要么变邪魔（截教某些大能）/要么投靠强者抱大腿/要么韬光养晦寻机。",
        content_json={
            "archetype_traits": "实力顶尖（准圣）/ 执念深重（求圣不得）/ 行事往往极端 / 容易被诱惑或恐惧驱使",
            "famous_cases": "通天教主（成圣不甘）/ 红云老祖（让圣位）/ 接引（求圣后的悲悯）/ 鸿钧紫霄宫弟子",
            "narrative_function": "中段反派（实力强但非顶级）/ 主角早期对手 / 关键转折点（站队 + 选边）",
            "deepening_layers": "求圣失败后的『证道』方式：以力证道 / 斩三尸证道 / 功德证道 / 杀劫证道",
            "narrative_use": "洪荒主流 / 以洪荒为背景的修仙",
            "activation_keywords": ["准圣", "证道", "斩三尸", "求圣不得", "执念", "杀劫"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("洪荒流", ""), llm_note("洪荒准圣原型")],
        tags=["洪荒", "准圣", "原型"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="洪荒",
        slug="honghuang-world-three-realms",
        name="洪荒三界六道地图",
        narrative_summary="洪荒世界从盘古开天后分天/地/人三界，外接六道轮回（天道/阿修罗道/人道/畜生道/饿鬼道/地狱道）。"
                          "天庭三十三重天 / 西方极乐 / 八景宫紫霄宫 / 东海四海龙宫 / 五行山 / 不周山。"
                          "提供洪荒标准世界地图。",
        content_json={
            "three_realms": "天界（玉帝太上老君）/ 地界（地仙界 + 蓬莱仙岛）/ 人界（凡人 + 散修）",
            "six_paths": "天/阿修罗/人/畜生/饿鬼/地狱 — 六道轮回",
            "key_locations": "紫霄宫（鸿钧讲道）/ 八景宫（太上老君）/ 玉虚宫（元始）/ 碧游宫（通天）/ 西方教（接引准提）/ 三十三天 / 不周山 / 东海四海龙宫 / 西天灵山 / 须弥山",
            "key_eras": "鸿钧讲道（混沌前）→ 三皇五帝（人皇）→ 巫妖大战（混沌后第一战）→ 封神（商周）→ 西游（取经）",
            "narrative_use": "洪荒世界观 / 重生洪荒 / 西游同人 / 封神同人",
            "activation_keywords": ["洪荒", "三界", "六道", "紫霄宫", "玉虚宫", "封神", "巫妖大战"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("洪荒小说", ""), llm_note("洪荒地图通识")],
        tags=["洪荒", "世界观", "三界"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="洪荒",
        slug="honghuang-device-merit-treasures",
        name="洪荒功德至宝体系",
        narrative_summary="洪荒至宝分先天至宝（鸿蒙紫气孕育）/ 后天至宝（圣人炼制）/ 功德至宝（功德加持）。"
                          "三大威力：先天 > 功德 > 后天。代表：开天斧（盘古）、东皇钟、混沌钟、太极图、盘古幡、诛仙四剑、金鳌岛。",
        content_json={
            "tier_levels": "鸿蒙至宝（盘古留下）/ 先天灵宝（鸿钧之前出现）/ 后天功德至宝（圣人炼）/ 后天灵宝 / 普通法宝",
            "famous_treasures": "盘古开天斧 / 东皇钟（妖族）/ 混沌钟 / 太极图（老子）/ 盘古幡（元始）/ 诛仙四剑（通天）/ 七宝妙树（接引）/ 十二品功德金莲（接引）/ 七宝玲珑塔（李靖）/ 山河社稷图",
            "merit_treasure_principle": "执掌世间气运 / 不为劫数所染 / 圣人不可击毁 / 功德加持伤害神圣化",
            "narrative_use": "洪荒主线宝物争夺 / 同人神器设定 / 西游/封神改写",
            "activation_keywords": ["先天至宝", "后天至宝", "功德金莲", "诛仙四剑", "太极图", "盘古幡"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("先天至宝", ""), llm_note("洪荒至宝通识")],
        tags=["洪荒", "至宝", "法宝"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 萌宠 — 深化
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="plot_patterns", genre="萌宠",
        slug="mengchong-plot-bond-stages",
        name="萌宠情感联结四阶段",
        narrative_summary="萌宠题材核心是『人宠羁绊』五阶段：相遇（误抓/捡到）→ 抗拒（猫嫌弃 / 狗害怕）→ 试探（互相试探边界）→ 融入（成为家人）→ 生离死别（宠物寿命短的必然主题）。"
                          "情感曲线决定读者代入程度。",
        content_json={
            "stage_1_meeting": "雨夜捡到 / 宠物店相遇 / 邻居遗弃 / 神秘出现",
            "stage_2_resistance": "猫不让摸 / 狗咬人 / 鸟尖叫 / 主角嫌麻烦",
            "stage_3_testing": "试探喂食 / 第一次主动靠近 / 第一次被信任",
            "stage_4_bonding": "睡同一张床 / 等门 / 见到生病主动安慰 / 相互保护",
            "stage_5_loss": "病重 / 失踪 / 离别 / 重新相遇（重生类）",
            "subgenres": "爆笑日常 / 萌宠+异能 / 萌宠+末日 / 萌宠+重生 / 萌宠+异世界",
            "narrative_use": "萌宠主流 / 治愈系 / 都市萌宠 / 修仙萌宠（带个小神兽）",
            "activation_keywords": ["萌宠", "羁绊", "捡到", "试探", "融入", "生离死别"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("宠物文学", ""), llm_note("萌宠情感曲线")],
        tags=["萌宠", "情感", "结构"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="萌宠",
        slug="mengchong-ct-mythical-beast-cub",
        name="神兽幼崽伙伴模板",
        narrative_summary="主角偶得一只看似普通宠物，实为传说神兽（青龙/白虎/朱雀/玄武/麒麟/凤凰/九尾狐）幼年形态。"
                          "幼崽阶段萌+笨+贪吃，成长后觉醒大神威。提供前期治愈+中后期战力的双重满足。",
        content_json={
            "common_species": "青龙（蓝色幼崽）/ 白虎（小奶虎）/ 凤凰（彩色幼鸟）/ 麒麟（独角小马）/ 九尾狐（白毛多尾）/ 玄武（小龟蛇）/ 鲲鹏（小金鱼）",
            "growth_stages": "蛋壳期（孵化）→ 幼崽期（萌）→ 成长期（小型态战力）→ 成熟期（神兽真身）→ 觉醒期（远古血脉）",
            "personality_archetypes": "傲娇（猫科）/ 忠诚（犬科）/ 贪吃（杂食）/ 高冷（凤凰麒麟）/ 调皮（狐狸猴子）",
            "key_scenes": "孵蛋 / 第一次叫主人 / 觉醒小招式 / 救主关键时刻 / 真身显现",
            "narrative_use": "玄幻萌宠 / 修仙带宠 / 异世界冒险 / 系统流",
            "activation_keywords": ["神兽幼崽", "青龙", "凤凰", "九尾狐", "麒麟", "孵化", "觉醒"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[wiki("神兽", ""), llm_note("神兽幼崽模板")],
        tags=["萌宠", "神兽", "角色"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 无限流 — 深化
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="world_settings", genre="无限流",
        slug="wuxianliu-world-mainstream-system",
        name="无限流主神空间设定",
        narrative_summary="无限流标准设定：所有玩家被强行拉入『主神空间』，每周/每月强制进入随机副本（电影/游戏/恐怖片世界），完成任务/活下来才能积分回归。"
                          "积分换技能/装备/血脉，死亡=永久删除（出局）。代表《无限恐怖》zhttty 开创。",
        content_json={
            "core_mechanism": "玩家被神秘主神（系统/原神/上位文明）选中 → 强制进入副本世界 → 完成主线任务 + 隐藏任务 → 主神积分 → 兑换 / 加点 / 升级",
            "world_types": "电影副本（黑客帝国/异形/生化/咒怨）/ 游戏副本（生化/古墓）/ 历史副本 / 恐怖小说改写",
            "team_dynamics": "队伍组队（互相利用）/ 信任建立 / 团灭风险 / 内讧 / 留底牌",
            "famous_works": "《无限恐怖》zhttty / 《死亡通知单》/ 《无限世界》系列 / 《全球高考》木苏里",
            "subgenres": "硬核生存 / 言情主神空间（双男主双女主）/ 喜剧无限 / 数据流（详细积分系统）",
            "narrative_use": "无限流主流 / 末日 + 无限混合",
            "activation_keywords": ["无限流", "主神空间", "副本", "积分", "团灭", "玩家", "任务"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("无限流", ""), llm_note("无限流主神设定")],
        tags=["无限流", "主神", "世界观"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="无限流",
        slug="wuxianliu-plot-instance-arc",
        name="无限流副本三段结构",
        narrative_summary="单个副本叙事三段：进入（被丢入副本+主线提示）→ 探索（地图踩点+收集情报+遇袭）→ 高潮（主线 boss + 隐藏任务竞速）→ 结算（积分+死人计数+活下来的奖励）。"
                          "节奏紧、信息密、生死高频。",
        content_json={
            "stage_1_entry": "副本广播 / 主线任务文字 / 队伍出现地点 / 第一次环境扫描",
            "stage_2_exploration": "搜寻线索 / 触发隐藏任务 / 遭遇怪物 / 队员能力试探 / 第一次牺牲",
            "stage_3_climax": "Boss 战 / 多线齐发（主线+隐藏+生存）/ 队伍内讧 / 关键时刻牺牲 / 翻盘",
            "stage_4_settlement": "倒计时归位 / 积分公布 / 死人记一次 / 兑换技能 / 下次副本预告",
            "tension_design": "信息不对称（主神隐藏情报）/ 时间压力（倒计时）/ 队员不可信（怀疑链）/ 死亡威胁（每个怪都可能是主谋）",
            "narrative_use": "无限流单本框架 / 短篇 / 章节聚类",
            "activation_keywords": ["副本", "主线任务", "隐藏任务", "Boss", "积分", "倒计时", "团灭"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("无限流", ""), llm_note("副本三段结构")],
        tags=["无限流", "副本", "结构"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 美食 — 深化
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="character_templates", genre="美食",
        slug="meishi-ct-soul-of-cuisine",
        name="厨魂传承者主角模板",
        narrative_summary="美食主角继承家族百年厨艺传承（祖父留下菜谱/手稿）或意外觉醒厨神血脉。"
                          "弱→强升级靠『悟道某种食材/技法』；每次开餐厅必经历劲敌/质疑/翻盘。"
                          "中华小当家 / 食戟之灵 / 食神范式。",
        content_json={
            "background_archetypes": "百年祖传（家族厨子）/ 意外觉醒（穿越/重生带菜谱）/ 系统流（料理系统）/ 异世界厨王",
            "core_skills": "刀工 / 火候 / 食材辨识 / 调味哲学 / 摆盘 / 创新菜",
            "growth_pattern": "学徒（学基本功）→ 扬名（赢小比赛）→ 立店（自创菜系）→ 大赛（全国/国际）→ 厨神（哲学领悟）",
            "famous_inspirations": "中华小当家 / 食戟之灵 / 食神 / 天下第一菜 / 神之水滴",
            "narrative_use": "美食爽文 / 开店日常 / 厨艺大赛 / 异世界开餐厅",
            "activation_keywords": ["厨神", "刀工", "火候", "百年祖传", "厨艺大赛", "悟道"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("美食小说", ""), llm_note("厨魂主角模板")],
        tags=["美食", "厨师", "角色"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="美食",
        slug="meishi-scene-cooking-duel",
        name="美食料理对决场景",
        narrative_summary="美食对决三段：选题/食材→制作过程（特写刀工/火候/汤色变化）→评判（评委试吃 + 蒙太奇内心戏）。"
                          "感官描写要五感齐发：视（颜色摆盘）/嗅（蒸汽香气）/听（油爆声）/触（刀感）/味（爆炸式描述）。",
        content_json={
            "stage_1_setup": "宣布主题 / 食材箱开启 / 主角的灵感闪现 / 对手的傲慢/怀疑",
            "stage_2_process": "刀工特写（豆腐切丝细如发）/ 火候（颠勺火球）/ 汤底翻滚 / 调味关键时刻",
            "stage_3_judgment": "评委筷子悬空 / 第一口眼神变化 / 内心蒙太奇（童年 / 故乡 / 母亲的味道）/ 沉默后宣判",
            "five_senses_design": "视：颜色对比 + 摆盘艺术 / 嗅：层次蒸汽 / 听：油爆声 / 触：弹牙感 / 味：爆点 + 余韵",
            "common_tropes": "食灵显形 / 评委升天 / 灵感来自一段记忆 / 关键秘方是亲情",
            "narrative_use": "美食爽文核心场景 / 大赛章节 / 餐厅试菜",
            "activation_keywords": ["料理对决", "刀工", "火候", "评委", "蒸汽", "蒙太奇", "试吃"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("料理动画", ""), llm_note("美食对决场景")],
        tags=["美食", "对决", "场景"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 游戏 / 网游 — 深化
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="world_settings", genre="游戏",
        slug="game-world-vrmmo-mainstream",
        name="VRMMO 主流世界设定",
        narrative_summary="VR-MMO 网游标准设定：全息头盔/神经接入舱 → 进入沉浸虚拟世界 → 角色创建（种族职业）→ 开放大世界 + 副本 + PVP + 公会战 + 国战。"
                          "代表《全职高手》《网游之 X》《刀剑神域》。",
        content_json={
            "tech_setup": "VR 全息眼镜 / 神经接入舱 / 大脑直连虚拟世界 / 五感同步 / 痛感降级",
            "mainstream_systems": "等级 / 职业（战士/法师/盗贼/牧师等）/ 装备（白绿蓝紫橙红）/ 公会 / 副本 / 战场",
            "common_arcs": "新手村 → 主城 → 第一职业 → 副本团 → PVP 竞技 → 公会战 → 国战 → 跨服 → 转职/觉醒 → 隐藏任务 / 神器",
            "famous_tropes": "氪金大佬 / 工作室农民 / 隐藏 BUG / 隐藏职业 / 第一公会 / 跨服争霸",
            "subgenres": "硬核竞技（全职高手）/ 言情（网游之 X）/ 异世界 VR / 末日强制游戏",
            "narrative_use": "网游小说 / VR 题材 / 重生网游 / 全职高手向",
            "activation_keywords": ["VRMMO", "公会", "副本", "PVP", "氪金", "等级", "装备", "国战"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("MMORPG", ""), llm_note("VRMMO 通识")],
        tags=["游戏", "VRMMO", "世界观"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="游戏",
        slug="game-plot-pvp-tournament",
        name="电竞 / PVP 大赛剧情曲线",
        narrative_summary="电竞剧情主流：主角组建战队 → 资格赛崛起 → 八强 → 四强 → 半决赛遇宿敌 → 决赛对最强战队 → 夺冠。"
                          "每场比赛是一次三幕：开局战术 → 中盘转折 → 收尾 + 复盘。",
        content_json={
            "macro_arc": "组队（招募奇人）→ 资格赛（小组出线）→ 八强（淘汰菜鸡）→ 四强（遇老对手）→ 半决赛（宿敌+情感最高潮）→ 决赛（终极对决+夺冠）",
            "single_match_3acts": "开局战术布置 → 中盘 boss 战转折 → 终局推塔/团灭 + 镜头切回观众席",
            "tension_layers": "战术层（招式选择）/ 心理层（队员压力）/ 商业层（赞助 + 收视）/ 私人层（队员家事+爱情）",
            "common_tropes": "下风局逆转 / 关键队员受伤 / 老对手退役复出 / 解说员崩溃 / 全场起立",
            "narrative_use": "电竞爽文 / 重生电竞 / 系统流玩家 / 校园电竞",
            "activation_keywords": ["资格赛", "八强", "决赛", "战队", "解说", "夺冠", "宿敌"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("电子竞技", ""), llm_note("电竞剧情曲线")],
        tags=["游戏", "电竞", "PVP"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 机甲 — 深化
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="power_systems", genre="机甲",
        slug="mecha-ps-pilot-link-tiers",
        name="机甲驾驶员同调度等阶",
        narrative_summary="机甲驾驶分『同调度』层级：60% 基础（合格新兵）/ 80% 精英 / 90% 王牌 / 95% 传奇 / 99% 神级。"
                          "同调度决定机甲性能上限：人机一体程度越高，机甲表现越强。"
                          "突破往往伴随极端情感（绝望 / 仇恨 / 守护）的『暴走』瞬间。",
        content_json={
            "tier_structure": "<60% 不合格 / 60-79% 合格 / 80-89% 精英 / 90-94% 王牌 / 95-98% 传奇 / 99%+ 神级",
            "boost_mechanisms": "情感爆发（仇恨/守护）/ 血脉觉醒（特殊基因）/ 搭档共鸣（双人机甲）/ 系统辅助 / 神经升级",
            "training_methods": "VR 模拟舱 / 实战 / 神经回路重塑 / 师承传授 / 极限求生",
            "famous_inspirations": "EVA 同调率 / 高达 NEWTYPE / 太平洋之环 Drift / 机巧少女不会受伤",
            "narrative_use": "机甲爽文 / 王牌驾驶员崛起 / 机甲学院 / 末日机甲",
            "activation_keywords": ["同调度", "机甲", "驾驶员", "暴走", "王牌", "神级", "NEWTYPE"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("机甲", ""), llm_note("驾驶员同调度")],
        tags=["机甲", "驾驶员", "力量体系"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="机甲",
        slug="mecha-device-mech-types",
        name="机甲分类与典型代表",
        narrative_summary="机甲按形态分：人形（高达 / EVA）/ 兽型（变形金刚 / 翠星）/ 异型（EVA Mark 06 / 索灵）/ 超巨大（太平洋之环）。"
                          "按驱动分：神经接入 / 操纵杆 / 同调精神共鸣 / AI 半自动。"
                          "提供机甲设定参考库。",
        content_json={
            "form_types": "人形（高达 RX-78 / EVA 初号机）/ 兽形（变形金刚）/ 异型有机（EVA 量产型）/ 超巨大（耶格）",
            "drive_systems": "操纵杆 + HUD（高达）/ 神经接入插入栓（EVA）/ 同调精神（NEWTYPE）/ Drift 双人脑链接（环）/ AI 助手（独角兽）",
            "weapon_loadouts": "光束步枪 / 实弹机炮 / 实体剑（光剑 / 振动刀）/ 浮游炮 / 远程导弹 / 链锯",
            "iconic_machines": "RX-78-2 高达 / EVA 初号机 / Strike Freedom / Wing Zero / 雷蒙斯坦 / 危险流浪者",
            "narrative_use": "机甲设计参考 / 机甲战斗描写 / 机甲题材小说",
            "activation_keywords": ["人形机甲", "EVA", "高达", "插入栓", "光束步枪", "Drift", "耶格"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("机甲", ""), llm_note("机甲分类")],
        tags=["机甲", "装备", "分类"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 赛博朋克 — 深化
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="locale_templates", genre="赛博朋克",
        slug="cyberpunk-locale-megacity",
        name="赛博朋克巨型都市",
        narrative_summary="赛博朋克城市标准元素：高耸天台霓虹（顶层富人）+ 中层商业区（霓虹巨幅广告 + 全息广告牌）+ 下层贫民窟（管道纵横 + 黑诊所 + 黑市）+ 地下城（无政府 + 改造人 + 黑客窝点）。"
                          "永夜雨水 + 蒸汽 + 霓虹蓝紫粉。",
        content_json={
            "vertical_layers": "1) 天台云端（公司 CEO 私人岛）/ 2) 商业霓虹层（购物消费监控）/ 3) 街道层（小贩 警察）/ 4) 地下层（黑诊所 黑客咖啡馆）/ 5) 下水道（无政府）",
            "iconic_visuals": "霓虹蓝紫粉绿广告 / 全息巨型广告人 / 雨夜湿光地面 / 蒸汽 / 飞行汽车 / 巨型企业 logo",
            "common_districts": "公司区（光鲜）/ 中国城（华裔黑帮）/ 红灯区（义体改造妓女）/ 黑市电子（脑插件）/ 教堂废墟（义体教派）",
            "famous_inspirations": "《银翼杀手》洛杉矶 / 《攻壳机动队》新港 / 《赛博朋克 2077》夜之城 / 《阿基拉》新东京",
            "narrative_use": "赛博朋克场景模板 / 中长篇都市未来 / 反乌托邦",
            "activation_keywords": ["赛博朋克", "霓虹", "巨型公司", "义体", "下水道", "黑市", "夜之城"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("赛博朋克", ""), wiki("银翼杀手", ""), llm_note("赛博城市通识")],
        tags=["赛博朋克", "都市", "场景"],
    ),
    MaterialEntry(
        dimension="factions", genre="赛博朋克",
        slug="cyberpunk-fac-megacorp",
        name="赛博朋克巨型公司派系",
        narrative_summary="赛博朋克世界由超国家巨型企业（Megacorp）实质统治：荒坂武装、Militech、Arasaka、Tyrell、Weyland。"
                          "拥有私人军队、自治区、立法权。"
                          "玩家/主角通常是夹在多家公司间的边缘人。",
        content_json={
            "key_megacorps": "荒坂（Arasaka，日企武装）/ Militech 美国军工 / Tyrell 仿生人公司 / Weyland-Yutani 太空殖民 / Petrochem 能源",
            "common_business": "私人军队 / 武器研发 / 仿生人改造 / 大脑插件 / 网络控制 / 媒体垄断",
            "internal_dynamics": "公司间冷战（公司战争）/ 内部派系斗争 / CEO 神化 / 雇佣赏金猎人对付反叛员工",
            "common_archetypes": "高管（西装暴徒）/ 安保（武装到牙）/ 研究员（黑科技狂）/ 公关（操纵媒体）/ CEO（神级反派）",
            "narrative_use": "赛博朋克主线对手 / 公司战争 / 公司逃亡 / 卧底",
            "activation_keywords": ["巨型公司", "Megacorp", "荒坂", "Militech", "Arasaka", "私人军队", "公司战争"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("Megacorporation", ""), llm_note("赛博朋克公司")],
        tags=["赛博朋克", "公司", "派系"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 科幻子题材 — 深化
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre="科幻",
        slug="scifi-rw-hard-vs-soft",
        name="硬科幻 vs 软科幻分野",
        narrative_summary="硬科幻：以已知物理学为底座，技术细节严格自洽（《三体》《2001》《火星救援》）。"
                          "软科幻：以社会学/心理学/哲学为核心，技术只是背景（《银河系搭车客》《华氏 451》《1984》）。"
                          "硬科幻挑战智识，软科幻探讨人性。",
        content_json={
            "hard_scifi_traits": "严格物理 / 数学计算 / 技术专业 / 真空 / 相对论 / 量子 / 黑洞",
            "soft_scifi_traits": "社会预言 / 哲学问询 / 反乌托邦 / 心理探索 / 文化想象",
            "hard_examples": "《三体》刘慈欣 / 《2001 太空漫游》/ 《火星救援》/ 《Foundation》阿西莫夫 / 《七月日记》",
            "soft_examples": "《1984》/ 《华氏 451》/ 《使女的故事》/ 《海伯利安》/ 《银河系搭车客》",
            "spectrum_position": "并非黑白：多数作品在中间，含硬度也含软度",
            "narrative_use": "科幻定位 / 题材选择 / 风格定调",
            "activation_keywords": ["硬科幻", "软科幻", "三体", "1984", "反乌托邦", "硬度"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("硬科幻", ""), wiki("软科幻", ""), llm_note("科幻分野")],
        tags=["科幻", "硬科幻", "软科幻"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="科幻",
        slug="scifi-plot-first-contact",
        name="第一次接触（First Contact）模式",
        narrative_summary="人类首次遇见外星文明的叙事模式：发现信号 → 试图破解 → 误解/沟通失败 → 武力冲突 or 文化交融 → 反思人类自身。"
                          "代表《三体》《2001》《降临》《天外来客》。",
        content_json={
            "core_arc": "发现 → 破解 → 沟通 → 误解或交融 → 升华",
            "common_subtypes": "和平接触（《降临》语言学家）/ 武力冲突（《独立日》《三体》水滴）/ 隐瞒（《天外来客》）/ 哲学交流（《2001》方碑）",
            "famous_works": "《三体》/ 《降临》（特德·姜《你一生的故事》）/ 《2001 太空漫游》/ 《索拉里斯》/ 《天外来客》",
            "thematic_concerns": "文明等级差距 / 沟通困难（语言/思维方式）/ 人类自我认知 / 宇宙黑暗森林",
            "narrative_use": "科幻长篇 / 中篇 / 末日侵略 / 神级文明",
            "activation_keywords": ["第一次接触", "外星文明", "信号", "降临", "三体", "黑暗森林"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("First contact (science fiction)", ""), llm_note("First Contact 模式")],
        tags=["科幻", "外星", "模式"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="科幻",
        slug="scifi-plot-time-travel-paradox",
        name="时间旅行悖论模式",
        narrative_summary="时间旅行小说三大悖论：祖父悖论（杀祖父导致自己不存在）/ 信息因果（未来人传知识给过去）/ 平行宇宙（每次干预创造分支）。"
                          "三种解决方式：宿命论（无法改变）/ 平行宇宙分支 / 时间警察（强制纠错）。",
        content_json={
            "three_paradoxes": "祖父悖论 / 信息因果（无源知识）/ 自洽悖论",
            "three_models": "1) 单线宿命论（《终结者》原版）/ 2) 平行宇宙分支（《回到未来》《复联》）/ 3) 时间纠错（《时间警察》《超时空要犯》）",
            "famous_works": "《回到未来》/ 《终结者》/ 《时间机器》威尔斯 / 《十二猴子》/ 《信条》/ 《时空恋旅人》",
            "narrative_uses": "重生 = 时间旅行变体 / 穿越 = 单向时间旅行 / 改变历史 / 多重结局",
            "common_tricks": "蝴蝶效应 / 自我相遇 / 父母时代 / 历史固定点",
            "activation_keywords": ["时间旅行", "悖论", "祖父悖论", "平行宇宙", "蝴蝶效应", "时间循环"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("时间旅行", ""), wiki("祖父悖论", ""), llm_note("时间旅行悖论")],
        tags=["科幻", "时间旅行", "悖论"],
    ),
]


async def main() -> None:
    print(f"Seeding {len(ENTRIES)} entries...\n")
    by_genre = {}
    by_dim = {}
    inserted = 0
    errors = 0
    async with session_scope() as session:
        for e in ENTRIES:
            try:
                await insert_entry(session, e, compute_embedding=True)
                inserted += 1
                by_genre[e.genre or "(通用)"] = by_genre.get(e.genre or "(通用)", 0) + 1
                by_dim[e.dimension] = by_dim.get(e.dimension, 0) + 1
            except Exception as exc:
                errors += 1
                print(f"ERROR {e.slug}: {exc}")
        await session.commit()
    print(f"By genre:     {by_genre}")
    print(f"By dimension: {by_dim}")
    print(f"\n\u2713 {inserted} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    asyncio.run(main())
