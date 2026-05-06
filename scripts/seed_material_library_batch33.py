"""
Batch 33: Power systems / cultivation systems / magic systems / tech ladders.
Cross-genre framework templates: cultivation tier ladders, magic schools,
psionic disciplines, mech tiers, ki/chakra, professional rankings.
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
    # 仙侠 - 经典九阶
    MaterialEntry(
        dimension="power_systems", genre="仙侠",
        slug="ps-xianxia-classic-nine-realms",
        name="仙侠经典九阶（练气-筑基-金丹-元婴-化神-炼虚-合体-大乘-渡劫）",
        narrative_summary="仙侠九阶通用模板。修真小说 70% 套用此模式。"
                          "每阶分上中下三品 = 27 小阶。"
                          "用一个完整人生跨度（30-3000 岁）。",
        content_json={
            "tier_breakdown": "1) 练气期 / 2) 筑基期 / 3) 金丹期 / 4) 元婴期 / 5) 化神期 / 6) 炼虚期 / 7) 合体期 / 8) 大乘期 / 9) 渡劫期 / 飞升仙界",
            "qi_period_subdiv": "练气 9 层（外门子弟 1-3 / 内门 4-6 / 真传 7-9）",
            "lifespan_per_tier": "练气 100 / 筑基 200 / 金丹 500 / 元婴 1000 / 化神 3000 / 炼虚 5000 / 合体 10000 / 大乘 100000 / 渡劫 = 飞升或永生",
            "abilities_per_tier": "练气 = 凡人级（飞剑、御物）/ 筑基 = 飞天 / 金丹 = 神识 + 法宝 / 元婴 = 元婴出窍可分身 / 化神 = 万年寿 + 神通 / 炼虚 = 半仙 / 合体 = 地仙 / 大乘 = 一方圣地之主 / 渡劫 = 仙人",
            "tribulation_design": "金丹 = 金丹劫（小）/ 元婴 = 元婴劫（中）/ 化神 = 心魔劫 / 渡劫 = 九重雷劫（最危险）",
            "narrative_value": "提供 27 小阶节奏点 = 长篇连载主线天然 / 每升一阶 = 一个副本 / 适合 200-1000 万字大长篇",
            "famous_works": "《凡人修仙传》《仙逆》《缥缈之旅》《诛仙》《佛本是道》",
            "activation_keywords": ["练气", "筑基", "金丹", "元婴", "化神", "炼虚", "合体", "大乘", "渡劫", "九阶"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("仙侠九阶通用框架")],
        tags=["仙侠", "修真", "等级体系", "通用"],
    ),
    # 仙侠 - 玄黄修仙
    MaterialEntry(
        dimension="power_systems", genre="仙侠",
        slug="ps-xianxia-xuanhuang-cultivation",
        name="洪荒玄黄修真体系（圣人 / 准圣 / 大罗 / 太乙）",
        narrative_summary="洪荒流 / 封神流核心修真体系。"
                          "顶层境界跳出三界。圣人不死不灭。"
                          "适用 OP 流 + 反 OP 流。",
        content_json={
            "tier_top_down": "鸿蒙 → 圣人（混元大罗金仙）→ 准圣（混元金仙）→ 大罗金仙 → 太乙金仙 → 金仙 → 玄仙 → 真仙 → 天仙 → 散仙",
            "saint_count": "鸿蒙 6 圣 / 三清（元始 + 太上 + 通天）+ 接引 + 准提 + 女娲 + 后期补 = 7 圣",
            "saint_unfallen": "圣人不死不灭 / 洪荒法则保护 / 只能斗法不能击杀",
            "race_systems": "妖族 + 巫族 + 龙凤 + 麒麟 + 人族 / 各自有种族特长（妖族修元神 / 巫族修肉身）",
            "weapons_artifacts": "先天灵宝（盘古十二品红莲 / 弑神枪 / 诛仙四剑 / 番天印）",
            "narrative_value": "上限极高 / 适合洪荒穿越 + 封神同人 / 反派天花板",
            "famous_works": "《佛本是道》《巫族奋斗史》《我的师妹是大佬》洪荒同人无数",
            "activation_keywords": ["洪荒", "圣人", "准圣", "大罗", "太乙", "金仙", "封神", "鸿蒙"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("洪荒流框架")],
        tags=["仙侠", "洪荒", "封神", "高阶"],
    ),
    # 玄幻 - 斗气大陆
    MaterialEntry(
        dimension="power_systems", genre="玄幻",
        slug="ps-xuanhuan-douqi-tier",
        name="斗气大陆体系（斗者 → 斗师 → 大斗师 → ... → 斗帝）",
        narrative_summary="天蚕土豆《斗破苍穹》体系。"
                          "玄幻斗气流标杆。"
                          "9 阶 + 圣阶 + 帝阶。",
        content_json={
            "tier_breakdown": "斗者 → 斗师 → 大斗师 → 斗灵 → 斗王 → 斗皇 → 斗宗 → 斗尊 → 斗圣 → 斗帝（顶级）",
            "subtier_per_rank": "每阶分 1-9 星 / 加上'巅峰' = 每阶 10 段 = 100 段总量",
            "abilities_per_tier": "斗者 = 凡人战士 / 斗师 = 飞行 / 斗王 = 大宗师 / 斗皇 = 一方霸主 / 斗宗 = 跨大陆 / 斗尊 = 半神 / 斗圣 = 一方位面之主 / 斗帝 = 不灭",
            "key_features": "异火（斗气强化）/ 灵魂之力 / 异火三阶段 / 萧炎收 18 异火 / 灵魂之子",
            "famous_works": "《斗破苍穹》《武动乾坤》《大主宰》《元尊》（天蚕土豆四作系出同源）",
            "narrative_value": "节奏鲜明 / 适合爽文 / 萧炎模板 = 废柴翻身 + 收异火 + 救父 + 灭对头 + 登顶",
            "activation_keywords": ["斗气", "斗者", "斗师", "斗王", "斗皇", "斗宗", "斗尊", "斗圣", "斗帝", "异火"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("天蚕土豆斗气体系")],
        tags=["玄幻", "斗气流", "等级体系"],
    ),
    # 玄幻 - 武道宗师
    MaterialEntry(
        dimension="power_systems", genre="玄幻",
        slug="ps-wuxia-grandmaster-tier",
        name="武道宗师体系（外功 → 内功 → 化劲 → 宗师 → 大宗师 → 武圣）",
        narrative_summary="武侠 / 异世武道 / 国术流共用框架。"
                          "源自传统武术理论 + 网文加工。"
                          "比仙侠'低武' / 比都市无异能'高武'。",
        content_json={
            "tier_breakdown": "1) 外家明劲（练肉 + 骨）/ 2) 暗劲（练筋 + 气）/ 3) 化劲（劲入骨髓）/ 4) 宗师（一招出 = 决胜）/ 5) 大宗师（神而明之）/ 6) 武圣（一拳裂山）/ 7) 陆地神仙（脱凡）",
            "lifespan": "明劲 60 / 暗劲 80 / 化劲 100 / 宗师 120 / 大宗师 200 / 武圣 500 / 陆地神仙近不死",
            "real_world_basis": "李书文八极拳 / 形意拳孙禄堂 / 太极拳张三丰传说 / 这套理论部分基于真实国术",
            "abilities": "化劲 = 内伤无形 / 宗师 = 几十米掌风 / 大宗师 = 千里取人首级 / 武圣 = 一招拍碎山岳",
            "famous_works": "《剑来》《一念永恒》《武道宗师》《国术》《唐砖》《国士无双》",
            "narrative_value": "比修真'低武' / 适合都市异能 + 武林争锋 + 国术穿越流",
            "activation_keywords": ["武道", "明劲", "暗劲", "化劲", "宗师", "大宗师", "武圣", "陆地神仙"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("国术流框架")],
        tags=["玄幻", "武侠", "国术", "等级体系"],
    ),
    # 西方奇幻 - DnD 法师阶段
    MaterialEntry(
        dimension="power_systems", genre="西方奇幻",
        slug="ps-dnd-magic-tier",
        name="DnD 魔法体系（学徒 → 魔法师 → 大法师 → 神官 / 圣骑士）",
        narrative_summary="DnD 龙与地下城 + 西方奇幻通用体系。"
                          "20 级法师 + 0-9 级法术。"
                          "8 大魔法学派。",
        content_json={
            "level_tiers": "1-4 级新手 / 5-10 中级英雄 / 11-15 高阶 / 16-20 传奇 / 21+ 史诗（半神级别）",
            "spell_levels": "0 戏法（无消耗）/ 1-3 低级（治疗、火球、闪电）/ 4-6 中级（位面门、变形）/ 7-9 高级（陨石术、时间停止、愿望术）",
            "magic_schools": "1) Abjuration 防护 / 2) Conjuration 召唤 / 3) Divination 占卜 / 4) Enchantment 附魔 / 5) Evocation 塑能 / 6) Illusion 幻术 / 7) Necromancy 死灵 / 8) Transmutation 变化",
            "classes": "Wizard 巫师 / Sorcerer 术士 / Warlock 邪术师 / Cleric 牧师 / Druid 德鲁伊 / Paladin 圣骑士 / Bard 吟游诗人 / Ranger 游侠",
            "spell_components": "V 言语 / S 姿势 / M 材料 / 高阶法术需稀有材料（钻石粉、龙血）",
            "famous_works": "DnD 系列 / 《被遗忘的国度》/ 《龙枪》/ Pathfinder / Baldur's Gate / 《魔戒》（部分契合）",
            "activation_keywords": ["DnD", "法师", "巫师", "魔法", "塑能", "死灵", "圣骑士", "牧师"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("D&D职业列表", ""), llm_note("DnD 5e 标准")],
        tags=["西方奇幻", "DnD", "魔法体系"],
    ),
    # 西方奇幻 - 元素亲和系
    MaterialEntry(
        dimension="power_systems", genre="西方奇幻",
        slug="ps-elemental-affinity",
        name="元素亲和体系（火水风土 + 光暗）",
        narrative_summary="元素魔法常见框架。"
                          "每个法师 / 异能者 / 召唤兽天生有亲和元素。"
                          "结合古希腊四元素说。",
        content_json={
            "four_elements": "火 / 水 / 风 / 土 = 古希腊基础 / 阴阳五行变体在中国 = 金木水火土",
            "extension_elements": "光（神圣）/ 暗（亵渎）/ 雷（风火合）/ 冰（水风合）/ 时空 / 灵 / 死 / 生命 / 命运 / 混沌",
            "affinity_tiers": "0% 不可习 / 10-30% 普通天赋 / 50% 高资质 / 80% 天才 / 99%+ 神选 / 100% 元素之子（独一无二）",
            "dual_element": "双元素亲和 = 万中无一（如雷电 = 风+火 / 冰川 = 水+土）/ 三元素 = 神级（亿中无一）",
            "elemental_creatures": "火 = 火元素 / 凤凰 / 火蜥蜴 / 水 = 水元素 / 美人鱼 / 水龙 / 风 = 风元素 / 鹰 / 雷鸟 / 土 = 土元素 / 巨人 / 矿物精灵",
            "narrative_uses": "废柴主角觉醒后 = 双元素 / 全元素亲和 = 元素之子 / 结合上古血脉 = 创世神嫡系",
            "famous_works": "《魔法少女》系列 / 《风之大陆》/ 《狂神》/ 《元素法则》",
            "activation_keywords": ["元素", "火水风土", "光暗", "元素之子", "亲和度", "双元素"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("元素魔法通用框架")],
        tags=["西方奇幻", "元素", "魔法"],
    ),
    # 科幻 - 超能力等级
    MaterialEntry(
        dimension="power_systems", genre="科幻",
        slug="ps-psionic-power-tier",
        name="超能力等级（Psionic / 异能者）",
        narrative_summary="科幻 / 超能力流通用框架。"
                          "心灵 / 念动 / 时空 / 元素 / 控物 / 治愈系。"
                          "Marvel / X-Men / 一拳超人都套用。",
        content_json={
            "ability_categories": "心灵系（读心 / 心控 / 幻觉）/ 念动系（隔空取物 / 飞行 / 时空操控）/ 元素系（火球 / 冻结）/ 治愈系（治愈 / 复活）/ 强化系（变身 / 力气放大）/ 创造系（凭空造物）",
            "tier_classification": "E 级（基础）/ D 级 / C 级 / B 级 / A 级 / S 级 / SS 级 / SSS 级（神级）/ 不可测（超越级）",
            "x_men_classification": "Class 1（小能力）/ Class 2-3（中等）/ Class 4（强大）/ Class 5（神级 - 凤凰之力 / Apocalypse / Magneto 顶峰）",
            "side_effects": "强能力 = 强反噬 / 心灵能力使用过度 = 头疼脑爆 / 时空能力 = 老化加速 / 治愈系 = 寿元换",
            "narrative_uses": "异能学院 / 神秘组织选拔 / 普通人觉醒 / 反派 OP 主角逆袭",
            "famous_works": "《X-Men》/ 《一拳超人》/ 《我的英雄学院》/ 《超神制造商》/ 《全球超神》",
            "activation_keywords": ["超能力", "异能", "psionic", "S级", "心灵", "念动", "X-Men"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("超能力流框架")],
        tags=["科幻", "超能力", "异能流"],
    ),
    # 科幻 - 文明等级
    MaterialEntry(
        dimension="power_systems", genre="科幻",
        slug="ps-civilization-kardashev",
        name="卡尔达舍夫文明等级（Type I-VII）",
        narrative_summary="俄罗斯天体物理学家 1964 年提出的科幻文明等级。"
                          "按能源利用规模分级。"
                          "硬科幻 / 星际题材标杆。",
        content_json={
            "type_one": "Type I 行星级文明 / 利用一颗行星全部能源 / 现地球处于 0.7 级（接近 I 级）",
            "type_two": "Type II 恒星级文明 / 戴森球完全利用一颗恒星能源",
            "type_three": "Type III 星系级文明 / 利用整个银河系能源",
            "extension": "Type IV 宇宙级 / 利用可观测宇宙 / Type V 多元宇宙级 / Type VI 全维 / Type VII 上帝级",
            "real_examples": "Type I-II 接近 = 三体三体人 / Type II-III = 银河帝国 / Type IV+ = 漫威永恒族 / 三体歌者文明",
            "narrative_uses": "等级越高威胁越大 / 主角文明被高等级文明俯视 = 黑暗森林 / 升级文明等级 = 长篇主线",
            "famous_works": "《三体》刘慈欣 / 《银河英雄传说》/ 《基地》阿西莫夫 / 《Star Trek》宇宙观",
            "activation_keywords": ["卡尔达舍夫", "文明等级", "Type I", "戴森球", "三体", "宇宙级"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("卡尔达舍夫等级", ""), llm_note("Kardashev 1964")],
        tags=["科幻", "硬科幻", "文明级"],
    ),
    # 末世 - 进化阶段
    MaterialEntry(
        dimension="power_systems", genre="末世",
        slug="ps-doomsday-evolution",
        name="末世进化阶段（一阶丧尸 → 七阶王者 → 异变之神）",
        narrative_summary="末世流核心进化体系。"
                          "丧尸 / 异兽 / 异能者 / 都共享进化模板。"
                          "末世第 N 月 = 进化第 N 阶 = 节奏锚点。",
        content_json={
            "tier_breakdown": "1 阶（普通丧尸 + 觉醒能力新人）/ 2-3 阶（进化丧尸 / 异能小能力）/ 4-5 阶（变异 BOSS / 大杀器）/ 6-7 阶（区域王者 / 全国级）/ 8+ 阶（神级 / 灭世）",
            "evolution_triggers": "吃晶核 / 吞噬同类 / 吸末世能量 / 觉醒上古血脉 / 突变事件",
            "category_humans": "战斗系（火/冰/电/金属）/ 辅助系（治愈/防御/侦察）/ 召唤系 / 特殊系（时空/精神）",
            "category_zombies": "普通丧尸 / 跳跃丧尸 / 巨力丧尸 / 智能丧尸 / 异变之王 / 王中之王",
            "narrative_uses": "末世第 1 月 = 1 阶 / 第 6 月 = 3 阶 / 第 1 年 = 5 阶 / 第 3 年 = 7 阶 / 第 10 年 = 神级 / 节奏天然",
            "famous_works": "《超级兵王》末世篇 / 《末世重生》/ 《全球进化》/ 《我的末世女友》",
            "activation_keywords": ["末世", "进化", "晶核", "异能者", "丧尸", "异兽", "BOSS"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[llm_note("末世流通用框架")],
        tags=["末世", "进化", "等级体系"],
    ),
    # 都市 - 异能等级
    MaterialEntry(
        dimension="power_systems", genre="都市",
        slug="ps-urban-anomalous-tier",
        name="都市异能等级（D-S）",
        narrative_summary="都市超能 / 隐世家族流框架。"
                          "比仙侠'低武'。"
                          "外表常人 / 内里异能 / 圈子封闭。",
        content_json={
            "tier_breakdown": "F 级（启蒙）/ E 级（小成）/ D 级（成熟）/ C 级（高手）/ B 级（精英）/ A 级（大师）/ S 级（宗师）/ SS 级（绝顶）/ SSS 级（神级）",
            "manifestation_types": "异术家族（隐世修真）/ 雇佣兵 / 异能特工 / 觉醒平凡人 / 古武世家 / 海外修士",
            "secret_world_organization": "七大异能组织 / 国家神秘部门（道协 / 玄司 / FBI X 部）/ 海外神秘势力",
            "powers_taxonomy": "古武 / 异能 / 阴阳师 / 巫蛊 / 风水 / 茅山 / 西方魔法 / 东方道术 / 上古血脉",
            "narrative_uses": "废柴小白领觉醒 → 接触异能圈 → 揭开世界真相 → 一阶一阶往上打 / 经典都市异能流套路",
            "famous_works": "《最强弃少》《超级兵王》《古今奇人录》《都市超级医生》",
            "activation_keywords": ["都市异能", "古武", "异能者", "隐世家族", "S级高手", "都市修真"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[llm_note("都市异能流标杆")],
        tags=["都市", "异能", "古武"],
    ),
    # 武侠 - 内功心法
    MaterialEntry(
        dimension="power_systems", genre="武侠",
        slug="ps-wuxia-internal-energy",
        name="武侠内功体系（一甲子 → 百年功力 → 千年功力 → 神功大成）",
        narrative_summary="金庸 / 古龙武侠通用内功量化。"
                          "用'年功力'量化战力。"
                          "六脉神剑 / 易筋经 / 九阴九阳 / 北冥神功为顶级。",
        content_json={
            "year_quantification": "一甲子 = 60 年功力 / 张无忌乾坤大挪移 = 百年 / 张三丰 = 千年（武学神话）/ 萧峰降龙十八掌 = 数百年内力",
            "top_tier_techniques": "九阳神功 + 九阴真经 + 易筋经 + 六脉神剑 + 北冥神功 + 葵花宝典 + 独孤九剑 + 太极 + 天魔解体大法",
            "yang_vs_yin": "九阳 = 至刚至阳 / 九阴 = 至阴至柔 / 阴阳相生 = 张无忌兼修",
            "side_effects": "强行修不匹配心法 = 走火入魔（心脉俱断）/ 内力相冲（裘千仞）/ 心法误传 = 残废",
            "tradition_lineage": "少林 + 武当 + 峨嵋 + 华山 + 昆仑 + 崆峒 + 古墓 + 桃花岛 + 全真 / 各派核心心法",
            "narrative_uses": "学一门绝技 = 一卷书 / 学多门 = 多卷 / 集齐九阴九阳 = 神级 / 武侠主线推力天然",
            "famous_works": "金庸《射雕》《神雕》《倚天》《天龙》《笑傲》/ 古龙《楚留香》《陆小凤》/ 黄易《大唐》《寻秦》",
            "activation_keywords": ["内功", "心法", "九阳神功", "九阴真经", "易筋经", "六脉神剑", "降龙十八掌", "甲子"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("金庸古龙武侠通用")],
        tags=["武侠", "内功", "心法"],
    ),
    # 御兽 - 契约兽等级
    MaterialEntry(
        dimension="power_systems", genre="御兽",
        slug="ps-beast-tamer-tier",
        name="御兽 / 召唤兽等级（普通 → 史诗 → 传说 → 神兽）",
        narrative_summary="御兽流 / 神奇宝贝流 / 召唤兽流通用框架。"
                          "Pokemon + DnD 召唤合体。"
                          "《我的契约兽》《全球进化》经典框架。",
        content_json={
            "rarity_tiers": "普通 / 优质 / 稀有 / 史诗 / 传奇 / 神话 / 神兽 / 上古神兽 / 创世神兽",
            "ranks": "1 阶（小怪）/ 2-3 阶（精英）/ 4-5 阶（强者）/ 6-7 阶（区域王者）/ 8-9 阶（巅峰）/ 10 阶（神兽）",
            "evolution_paths": "进化树（多分支选最终形态）/ Mega 进化 / 觉醒形态 / 远古形态 / 创世形态",
            "famous_creatures": "九尾狐 / 朱雀 / 玄武 / 麒麟 / 龙 / 凤凰 / 烛龙 / 应龙 / 苍龙 / 白虎 / 中国上古神兽",
            "binding_methods": "签约（吞契约符）/ 灵魂烙印 / 血脉融合 / 神兽信任",
            "narrative_uses": "宠物升级 = 主角升级 / 收集多元宠物 = 队伍战力 / 神兽契约 = 终极目标",
            "famous_works": "《全球进化》《我的契约兽》《宠物宇宙》《最强宠兽训练师》",
            "activation_keywords": ["御兽", "召唤兽", "宠物", "契约", "进化", "神兽", "九尾狐", "凤凰"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[llm_note("御兽流框架")],
        tags=["御兽", "召唤", "宠物"],
    ),
    # 通用 - 黄阶 - 玄阶 - 地阶 - 天阶
    MaterialEntry(
        dimension="power_systems", genre=None,
        slug="ps-generic-tier-yellow-mysterious-earth-heaven",
        name="物品 / 功法 / 心法等级（黄玄地天 + 神品）",
        narrative_summary="网文通用品级。"
                          "适用于功法 / 法宝 / 灵药 / 武学。"
                          "9 级体系 + 9 阶子级。",
        content_json={
            "tier_breakdown": "1) 黄阶 / 2) 玄阶 / 3) 地阶 / 4) 天阶 / 5) 圣阶 / 6) 神阶 / 7) 仙阶 / 8) 帝阶 / 9) 至尊（绝阶）",
            "subtier_per_level": "下品 / 中品 / 上品 / 极品 / 4 段位",
            "applies_to": "功法 / 武学 / 法宝 / 兵器 / 灵药 / 灵兽 / 阵法 / 符箓 / 矿石 / 任何稀有物",
            "narrative_uses": "起点 = 黄阶 / 主角逐步收集到天阶 / 圣阶以上 = 中后期资源 / 神阶 = 大事件",
            "famous_works": "《斗破苍穹》《武动乾坤》《大主宰》斗气流系列 / 《圣墟》《遮天》辰东系列",
            "activation_keywords": ["黄阶", "玄阶", "地阶", "天阶", "圣阶", "神阶", "极品", "等级"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("网文通用品级")],
        tags=["通用", "等级体系", "网文"],
    ),
    # 太空歌剧 - 舰船等级
    MaterialEntry(
        dimension="power_systems", genre="科幻",
        slug="ps-spaceship-fleet-class",
        name="太空舰船等级（驱逐舰 → 巡洋舰 → 战列舰 → 母舰）",
        narrative_summary="太空歌剧 / 星际战争流框架。"
                          "源自二战海军舰种。"
                          "Star Wars / Star Trek / 银英全套用。",
        content_json={
            "ship_classes_traditional": "炮艇 / 护卫舰 / 驱逐舰 / 巡洋舰 / 重巡 / 战列巡洋 / 战列舰 / 战列航母 / 母舰 / 移动堡垒",
            "tonnage": "护卫舰 5000-2万吨 / 驱逐 5万吨 / 巡洋 30万吨 / 战列 200万吨 / 母舰 上亿吨 / 移动堡垒 兆吨级",
            "armament": "护卫舰（防空导弹）/ 驱逐舰（鱼雷 / 中口径）/ 战列舰（主炮 / 反物质炮）/ 母舰（搭载千架战机）",
            "fleet_doctrine": "Starwars 帝国（毁灭者级）/ 银英帝国（伊谢尔伦巨炮要塞）/ 三体水滴（一根棍子灭舰队）",
            "narrative_uses": "主角从开飞行员到舰长到舰队司令 = 长篇晋升线 / 单舰对舰队 = 经典爽文 / 旗舰陨落 = 重大事件",
            "famous_works": "《银河英雄传说》田中芳树 / 《Star Wars》/ 《灰烬战线》/ 《超时空要塞》",
            "activation_keywords": ["太空舰", "战列舰", "驱逐舰", "母舰", "舰队", "旗舰", "巡洋舰"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[llm_note("太空歌剧框架")],
        tags=["科幻", "太空歌剧", "舰队"],
    ),
    # 通用 - 段位 / 圈阶
    MaterialEntry(
        dimension="power_systems", genre=None,
        slug="ps-generic-circle-tier",
        name="圈阶体系（一品 → 九品 / 段位 / 道行）",
        narrative_summary="中国古代官阶 / 段位 / 道家道行通用。"
                          "九品 = 中国古代基础分阶。"
                          "适用古代仕途 / 武林段位 / 修真道行。",
        content_json={
            "tier_breakdown_official": "一品（最高）→ 九品（最低）/ 各品分正从 = 18 阶 / 古代官制",
            "tier_breakdown_martial": "一段 → 九段 / 围棋 / 围棋之外的武功 / 段位制",
            "applies_to": "古代官位 / 武林段位 / 道家道行 / 一阶到九阶通用 / 1 顶 9 底",
            "narrative_uses": "古代官场升迁 / 武林段位赛 / 修真道行进阶 / 节奏天然清晰",
            "famous_works": "《琅琊榜》/ 武林段位题材 / 古代官场题材 / 玄幻部分套用",
            "activation_keywords": ["一品", "九品", "段位", "圈阶", "道行", "晋升"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[llm_note("中国古代分阶框架")],
        tags=["通用", "古代", "官阶"],
    ),
    # 修真 - 雷劫等级
    MaterialEntry(
        dimension="power_systems", genre="仙侠",
        slug="ps-tribulation-thunder-tiers",
        name="天劫体系（金丹劫 → 元婴劫 → 化神劫 → 飞升劫）",
        narrative_summary="仙侠修真天劫细化框架。"
                          "升阶必渡天劫。"
                          "九重雷劫 / 心魔劫 / 风火劫 / 三花聚顶分类。",
        content_json={
            "tribulation_per_realm": "金丹（小三劫）/ 元婴（六劫）/ 化神（七劫）/ 炼虚（八劫）/ 合体（九劫）/ 大乘（紫雷劫）/ 飞升（九九八十一劫）",
            "tribulation_types": "雷劫（天罚）/ 心魔劫（自我）/ 风火劫（物理冲击）/ 三花聚顶（小自我超越）/ 业障劫（前世今生债）",
            "thunder_colors": "白雷 < 紫雷 < 金雷 < 黑雷 < 五彩雷 / 越后越难",
            "duration": "金丹劫几分钟 / 飞升劫七天七夜 / 时间越长危险越大",
            "death_rate_per_tier": "金丹 30% / 元婴 50% / 化神 70% / 飞升 90% 死亡率",
            "narrative_uses": "渡劫 = 章节高潮 / 失败 = 主角面临死亡 / 渡劫成功 = 上一阶 / 适合卡点",
            "famous_works": "《凡人修仙传》《缥缈之旅》《仙逆》《诛仙》《魔道祖师》",
            "activation_keywords": ["天劫", "雷劫", "心魔劫", "三花聚顶", "渡劫", "九重雷劫", "飞升劫"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("仙侠渡劫体系")],
        tags=["仙侠", "天劫", "升阶"],
    ),
]


async def main() -> None:
    print(f"Seeding {len(ENTRIES)} entries...")
    inserted = 0
    errors = 0
    by_genre: dict = {}
    by_dim: dict = {}
    async with session_scope() as session:
        for entry in ENTRIES:
            try:
                await insert_entry(session, entry, compute_embedding=True)
                inserted += 1
                by_genre[entry.genre or "(通用)"] = by_genre.get(entry.genre or "(通用)", 0) + 1
                by_dim[entry.dimension] = by_dim.get(entry.dimension, 0) + 1
            except Exception as e:
                print(f"  ✗ {entry.slug}: {e}")
                errors += 1
        await session.commit()
    print()
    print(f"\nBy genre:     {by_genre}")
    print(f"By dimension: {by_dim}")
    print(f"\n✓ {inserted} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    asyncio.run(main())
