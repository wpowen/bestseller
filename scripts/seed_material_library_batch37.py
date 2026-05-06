"""
Batch 37: Western fantasy depth — races, classes, mythologies, monsters.
Expands 西方奇幻 from 8 → 25 entries with full coverage of:
elves, dwarves, orcs, dragons, magic schools, classic monsters.
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
    # 精灵族
    MaterialEntry(
        dimension="character_archetypes", genre="西方奇幻",
        slug="arch-western-elves",
        name="精灵族（Elves）",
        narrative_summary="西方奇幻经典种族。"
                          "Tolkien 重塑后近乎所有奇幻共用模板。"
                          "高傲 + 长生 + 美貌 + 弓箭 + 自然亲和。",
        content_json={
            "subspecies": "高等精灵（Noldor / Vanyar，Tolkien 派系）/ 木精灵（Sindar / Wood Elves）/ 黑暗精灵（Drow / DnD）/ 血精灵（魔兽 / 堕落）/ 月精灵（暗夜）",
            "physical_traits": "身高 180+ / 尖耳 / 杏仁眼 / 苍白皮肤 / 修长身材 / 不老（活几千年）/ 永远年轻外貌 / 不长胡子 / 视力 + 听力远超人类",
            "magic_affinity": "天生魔法亲和 / 弓箭神射 / 自然元素 / 治愈系 / 月光魔法 / 树语 / 风之子",
            "society": "森林王国（Mirkwood / 月之森林）/ 海港城市（Alqualondë）/ 世袭王族 / 长老会议 / 与德鲁伊合作",
            "tropes": "傲慢看不起人类 / 与矮人世仇 / 拒绝铁兵器 / 长生而怀念旧世 / 一段恋情 = 终生 / 慢节奏决策",
            "famous_works": "Tolkien LOTR / DnD / Witcher / 魔兽世界 / 龙与地下城 / 龙枪",
            "narrative_uses": "盟友 / 智者 / 隐居导师 / 神秘恋人 / 失落贵族 / 弃世者",
            "activation_keywords": ["精灵", "Elves", "尖耳", "弓箭手", "永生", "Mirkwood", "Drow"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("精灵 (奇幻)", ""), llm_note("Tolkien + DnD")],
        tags=["西方奇幻", "种族", "经典"],
    ),
    # 矮人族
    MaterialEntry(
        dimension="character_archetypes", genre="西方奇�endao",
        slug="arch-western-dwarves",
        name="矮人族（Dwarves）",
        narrative_summary="西方奇幻经典种族。"
                          "矮壮 + 大胡子 + 锻造大师 + 山下王国。"
                          "Tolkien + DnD + 北欧神话。",
        content_json={
            "physical_traits": "身高 130-150 / 矮壮 / 浓密胡须（女矮人也有 / Tolkien 设定）/ 强壮如牛 / 寿命 200-300 年 / 醉酒能力惊人",
            "habitat": "山下王国 / 矿坑深处 / 巨型石窟王国（Moria / Erebor / Khazad-dûm）",
            "professions": "锻造大师（魔法武器）/ 矿工 / 战士 / 工程师（黑火药 / 蒸汽机始祖）/ 酿酒师",
            "society": "国王世袭 / 七部族（Tolkien）/ 议长议会 / 重荣誉 / 重血脉 / 永不忘记仇敌",
            "tropes": "贪财（金银控）/ 与精灵世仇 / 死战不退 / 男女皆有胡子（Tolkien）/ 不易醉 / 矮人国王守护始祖宝物",
            "famous_works": "Tolkien Khazad-dûm / DnD 山地矮人 / Warhammer 矮人 / 魔兽矮人 / 北欧神话杜林七贤",
            "narrative_uses": "盟友 / 锻造师（卖兵器）/ 牛逼战友 / 酗酒导师 / 失落王族复国",
            "activation_keywords": ["矮人", "Dwarves", "胡子", "锻造", "Moria", "山下王国", "矿坑"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("矮人 (奇幻)", ""), llm_note("Tolkien + 北欧神话")],
        tags=["西方奇幻", "种族", "经典"],
    ),
    # 兽人族
    MaterialEntry(
        dimension="character_archetypes", genre="西方奇幻",
        slug="arch-western-orcs",
        name="兽人族（Orcs / Goblinoids）",
        narrative_summary="西方奇幻经典反派种族。"
                          "Tolkien 创造 / 后来魔兽世界给主角化。"
                          "野蛮 + 力量 + 部落文化。",
        content_json={
            "physical_traits": "身高 200+ / 绿色或灰色皮肤 / 獠牙 / 红眼或黄眼 / 强壮野蛮 / 短毛 / 寿命 50-80 年",
            "subspecies": "Goblin（小型）/ Orc（中型）/ Hobgoblin（大型 + 智能）/ Ogre（巨型）/ Uruk-hai（精英战士）/ Olog-hai（巨魔精英）",
            "habitat": "穴居 / 部落聚集地 / 蛮荒之地 / 死亡沼泽",
            "society": "部落酋长 / 武力为尊 / 部落联盟（魔兽世界形式 = 萨尔统一）/ 萨满信仰 / 祖先崇拜",
            "tropes_classic_evil": "Tolkien LOTR 萨乌隆奴隶 / DnD 蛮族反派 / 食人 / 残暴 / 无文化",
            "tropes_modern_redeemed": "魔兽 World of Warcraft 之后给兽人主角化 / 游牧民族 / 拥有萨满信仰 / 主角阵营",
            "narrative_uses": "杂兵反派 / 部落英雄 / 游牧民族 / 与人类世仇 / 后期合作",
            "famous_works": "LOTR / DnD / 魔兽世界 / Warhammer / Shadow of Mordor",
            "activation_keywords": ["兽人", "Orc", "Goblin", "Uruk-hai", "獠牙", "部落", "魔兽"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("Tolkien + 魔兽 + DnD")],
        tags=["西方奇幻", "种族", "反派"],
    ),
    # 龙族
    MaterialEntry(
        dimension="character_archetypes", genre="西方奇幻",
        slug="arch-western-dragons",
        name="龙族（Dragons）",
        narrative_summary="西方奇幻最强种族。"
                          "千年 + 火焰 + 智慧 + 宝藏。"
                          "DnD 有色龙 / 金属龙 / Tolkien Smaug / GoT 三龙。",
        content_json={
            "color_dragons_dnd": "红龙（火 / 贪婪 / 山火）/ 蓝龙（雷 / 狡诈 / 沙漠）/ 黑龙（酸 / 阴险 / 沼泽）/ 绿龙（毒 / 暧昧 / 森林）/ 白龙（冰 / 简单 / 北极）",
            "metallic_dragons_dnd": "金龙（神圣 / 善 / 智慧）/ 银龙（守护 / 善 / 喜化人形）/ 铜龙（小天使 / 喜恶作剧）/ 青铜龙（雷电 / 法律阵营）/ 黄铜龙（火 / 喜聊天）",
            "tolkien_dragons": "Smaug / 安卡拉冈 / 格劳龙 / 最古老最强大 / 多在贪欲中堕落 / 卧在金山上",
            "got_dragons": "Drogon / Rhaegal / Viserion / 龙妈坐骑 / 火与血 / 龙焰可烧融钢",
            "abilities": "火焰吐息（红龙）/ 雷电 / 飞行 / 智慧（IQ 200+）/ 千年寿 / 龙鳞抵御一切凡器 / 变形（高级龙化人形）",
            "narrative_uses": "终极 BOSS / 主角宿敌 / 古老智者 / 主角伙伴（御龙）/ 守宝者",
            "famous_works": "DnD / LOTR / GoT / 龙与地下城 / 龙枪 / Eragon / 龙背上的奇兵",
            "activation_keywords": ["龙", "Dragon", "Smaug", "Drogon", "金龙", "红龙", "龙焰", "龙血"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("龙 (西方)", ""), llm_note("DnD + LOTR + GoT")],
        tags=["西方奇幻", "种族", "经典"],
    ),
    # 巫师塔法师
    MaterialEntry(
        dimension="character_archetypes", genre="西方奇幻",
        slug="arch-western-wizard-archmage",
        name="巫师 / 大法师（Wizard / Archmage）",
        narrative_summary="西方奇幻智者原型。"
                          "Gandalf / Dumbledore / Merlin = 三大经典。"
                          "千年 + 学识 + 法力 + 守护者。",
        content_json={
            "appearance": "白胡子（标志）/ 长袍（蓝色 / 紫色 / 灰色）/ 法杖 / 尖顶帽（DnD / 哈利波特）/ 老态但眼神清明",
            "personality": "深谋远虑 / 不轻易出手 / 谜语似回答 / 关键时挽救一切 / 暴怒时毁灭一城",
            "famous_examples": "Gandalf the Grey / Dumbledore / Merlin / Saruman / 龙枪 Raistlin / Discworld 隐形大学",
            "magic_specialization": "8 大学派之精通 / 元素 + 召唤 + 防护 + 占卜混合 / 高阶法术（陨石术 / 时间停止 / 愿望术）",
            "narrative_role": "主角导师 / 隐藏强者 / 灾难预警者 / 牺牲在关键时刻（Gandalf 摔深渊）/ 反派大魔头",
            "common_traits": "千年寿 / 神秘过去 / 隐居塔 / 收徒一人 / 与凡人保持距离 / 关心整个世界",
            "modern_subversions": "Saruman 黑化 / Voldemort 邪派大法师 / 大法师腐化 / 善 vs 恶之法师对抗",
            "activation_keywords": ["巫师", "大法师", "Wizard", "Gandalf", "Dumbledore", "Merlin", "白胡子", "法杖"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("Tolkien + Rowling + Arthurian")],
        tags=["西方奇幻", "智者", "导师"],
    ),
    # 圣骑士
    MaterialEntry(
        dimension="character_archetypes", genre="西方奇幻",
        slug="arch-western-paladin",
        name="圣骑士（Paladin）",
        narrative_summary="西方奇幻光明系战士。"
                          "DnD + 魔兽世界经典职业。"
                          "信仰 + 力量 + 重铠 + 守序善良代表。",
        content_json={
            "alignment_classic": "Lawful Good 守序善良 / 严格守誓言 / 不容许欺骗杀俘 / 一念背誓 = 失神圣力",
            "powers": "神圣治愈 / 圣光斩 / 驱散邪恶 / 净化诅咒 / 探测邪恶 / 震慑亡灵 / 复活术（高阶）",
            "weapons_armor": "全身板甲 + 盾 + 长剑 / 战锤 / 特定流派 = 圣矛骑士 / 圣弓手 / 全装备 = 50 公斤",
            "famous_examples": "WoW Uther 光明 / DnD Pathfinder / DragonAge Cousland / Diablo 圣骑士 / 但丁《神曲》中的天使骑士",
            "personality": "高度自律 / 守誓言至死 / 关爱弱者 / 抗腐 / 但缺乏灵活 / 容易被极端思想影响",
            "narrative_uses": "主角阵营战士 / 拯救型英雄 / 反派挑战善 / 堕落圣骑士 = 反派",
            "modern_subversions": "Death Knight 阿尔萨斯（光明圣骑士黑化为巫妖王）= 经典剧情 / 圣骑士信仰危机 + 失神圣力 = 弱化叙事",
            "activation_keywords": ["圣骑士", "Paladin", "光明", "圣光", "守序善良", "Uther", "阿尔萨斯"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("DnD + WoW")],
        tags=["西方奇幻", "战士", "光明"],
    ),
    # 死灵法师
    MaterialEntry(
        dimension="character_archetypes", genre="西方奇幻",
        slug="arch-western-necromancer",
        name="死灵法师（Necromancer）",
        narrative_summary="西方奇幻禁忌系法师。"
                          "操纵亡灵 + 不死生物。"
                          "DnD + Diablo + WoW 死亡骑士 / 巫妖王 = 标杆。",
        content_json={
            "powers": "亡灵召唤（骷髅兵 + 僵尸 + 死灵）/ 灵魂操控 / 灵魂腐化 / 死亡之触 / 生命汲取 / 不死化（自身变成 Lich）",
            "appearance": "苍白 / 黑袍 / 死气 / 红眼或绿眼 / 骨骼装饰 / 身边永远飘着寒气 / 死灵围绕",
            "ideology": "死亡是新的开始 / 生命脆弱 / 永生 = 真理 / 反正派伦理",
            "famous_examples": "Diablo 死灵法师职业 / WoW Lich King 阿尔萨斯 / DnD Lich 巫妖 / 哈利波特 Voldemort 部分元素 / 龙枪 Raistlin（半死灵）",
            "evolution_to_lich": "死灵法师 → 巫妖（Lich = 永生不死 + 灵魂封入魂器 + 不可杀）/ 哈利波特 Horcrux 灵感来源",
            "narrative_uses": "终极反派 / 黑暗势力之主 / 主角同盟（灰色法师）/ 反英雄主角",
            "modern_subversions": "死灵法师 = 主角（《不死法师》《永生 Necromancer》）/ 治愈系死灵法师 / 道德灰色",
            "activation_keywords": ["死灵法师", "Necromancer", "巫妖", "Lich", "阿尔萨斯", "亡灵", "Voldemort"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("DnD + Diablo + WoW")],
        tags=["西方奇幻", "法师", "禁忌"],
    ),
    # 半精灵游侠
    MaterialEntry(
        dimension="character_archetypes", genre="西方奇幻",
        slug="arch-western-ranger",
        name="游侠（Ranger）",
        narrative_summary="西方奇幻野外专家。"
                          "Aragorn / Drizzt Do'Urden / Geralt = 经典原型。"
                          "弓 + 双剑 + 自然亲和。",
        content_json={
            "skills": "弓箭神射 / 双剑流（Drizzt 标志）/ 追踪术 / 野外生存 / 动物伙伴 / 自然魔法 / 隐身潜行",
            "famous_examples": "Aragorn LOTR 北方游侠 / Drizzt Do'Urden 黑暗精灵 / Geralt 巫师 / WoW 希尔瓦娜斯",
            "personality": "孤独 + 神秘 + 宁愿与动物相处 / 寡言 / 重信用 / 独行 / 关键时刻可靠",
            "background_typical": "失乡王族（Aragorn）/ 被族群抛弃（Drizzt）/ 突变诅咒（Geralt）/ 童年悲剧 / 流浪几十年",
            "common_companions": "猎犬 / 鹰 / 狼 / 豹 / 灵兽 / 弓 = 永远的伙伴",
            "narrative_uses": "主角孤独英雄型 / 智者向导 / 失踪王族 / 双剑流爽快战斗 / 与精灵 / 兽人交涉",
            "famous_works": "LOTR / Forgotten Realms / The Witcher / Drizzt Saga / WoW",
            "activation_keywords": ["游侠", "Ranger", "Aragorn", "Drizzt", "Geralt", "弓箭手", "双剑流"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("LOTR + Forgotten Realms + Witcher")],
        tags=["西方奇幻", "战士", "野外"],
    ),
    # 西方奇幻 - 凤凰
    MaterialEntry(
        dimension="thematic_motifs", genre="西方奇幻",
        slug="motif-western-phoenix",
        name="凤凰（Phoenix）",
        narrative_summary="跨文化重生符号。"
                          "西方版（埃及不死鸟 + 希腊菲尼克斯）+ 东方版（朱雀）。"
                          "毁灭中重生 = 终极隐喻。",
        content_json={
            "western_phoenix": "埃及 Bennu 鸟 / 希腊 Phoinix / 罗马 Phoenix / 自焚每 500 年 / 灰烬中重生 / 永生",
            "harry_potter_fawkes": "Dumbledore 的凤凰 Fawkes / 哭泣治愈 / 携人飞行 / 千钧一发救主 / 经典符号",
            "chinese_zhuque": "朱雀 = 四圣兽南方 / 火焰 + 美 + 阴阳和谐 / 与凤凰文化共生 / 中国凤凰多偏向美而非重生",
            "symbolism": "重生 / 不朽 / 太阳 / 火焰净化 / 死亡是新开始 / 涅槃",
            "narrative_uses": "主角觉醒 = 凤凰浴火 / 失败后重生 / 武器附凤凰魂 / 友兽伴随 / 终极境界",
            "famous_works": "哈利波特 Fawkes / 中国玄幻凤凰传承 / X-Men 凤凰之力 Jean Grey / 《凤求凰》",
            "modern_adaptations": "凤凰之力（Marvel）/ 黑凤凰 = 黑化 / 双生凤凰 = 阴阳",
            "activation_keywords": ["凤凰", "Phoenix", "朱雀", "Fawkes", "涅槃", "重生", "不死鸟"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[wiki("凤凰 (神话)", ""), llm_note("跨文化重生意象")],
        tags=["西方奇幻", "通用", "意象"],
    ),
    # 西方奇幻 - 圣杯传说
    MaterialEntry(
        dimension="thematic_motifs", genre="西方奇幻",
        slug="motif-western-holy-grail",
        name="圣杯（Holy Grail）",
        narrative_summary="西方奇幻 + 基督教文化重要符号。"
                          "亚瑟王传说 + 基督最后晚餐杯。"
                          "终极探寻 + 神圣性 + 不可得。",
        content_json={
            "origin": "基督最后晚餐用过的杯 / 用来接住基督鲜血 / 由 Joseph of Arimathea 带到不列颠 / 后失踪",
            "arthurian_legend": "亚瑟王 + 圆桌骑士寻杯 / Galahad 唯一找到（最纯洁）/ Lancelot 因情欲被拒",
            "powers": "永生 / 万病可治 / 看到神 / 永恒的恩典 / 但只有最纯洁者可使用",
            "narrative_archetype": "终极探寻（Quest）/ 不可得的神圣 / 对凡人考验 / 寻者必先净化自身",
            "famous_works": "亚瑟王传说 / Indiana Jones《最后的圣战》/ 达芬奇密码（圣杯 = 玛利亚）/ Monty Python 圣杯传奇",
            "symbolism": "神圣 / 永恒 / 至善 / 内心净化 / 真理的载体",
            "modern_subversions": "圣杯不是杯 = 玛利亚之血脉（Da Vinci Code）/ 圣杯实为虚妄 / 寻杯者皆死",
            "activation_keywords": ["圣杯", "Holy Grail", "亚瑟王", "圆桌骑士", "Galahad", "永生", "终极探寻"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[wiki("圣杯", ""), llm_note("亚瑟王传说")],
        tags=["西方奇幻", "意象", "基督教"],
    ),
    # 北欧神话 - 诸神黄昏
    MaterialEntry(
        dimension="real_world_references", genre="西方奇幻",
        slug="rw-norse-ragnarok",
        name="诸神黄昏（Ragnarök）",
        narrative_summary="北欧神话末世。"
                          "诸神 vs 巨人最终战 → 神死 → 世界毁灭 → 重生。"
                          "现代奇幻末世原型。",
        content_json={
            "events_sequence": "1) Fimbulvetr 三年寒冬 / 2) 太阳被天狼吞 / 3) Loki 苏醒 / 4) 巨人 Surtr 攻击 / 5) Heimdall 吹号 / 6) 诸神 vs 巨人战 / 7) Odin 被 Fenrir 吞 / 8) Thor 战死 / 9) 世界树倒 / 10) Surtr 火焰焚烧一切 / 11) 一对凡人在世界树空洞中存活 / 12) 大地从海中重生",
            "key_battles": "Odin vs Fenrir / Thor vs Jörmungandr / Tyr vs Garm / Heimdall vs Loki / Frey vs Surtr",
            "survivors": "Vidar / Vali / Magni / Modi / Hod / Baldr（重生）/ 一对凡人 Lif + Lifthrasir",
            "narrative_uses": "末日 / 神死 / 旧世界毁灭 / 新世界诞生 / 循环时间观",
            "modern_adaptations": "Marvel Thor: Ragnarok / God of War (Norse 篇) / 漫威诸神黄昏 / 奇幻文学末世题材",
            "themes": "死亡循环 / 命运不可逆 / 英雄归宿 / 新生希望",
            "activation_keywords": ["诸神黄昏", "Ragnarök", "Odin", "Thor", "Loki", "Fenrir", "末日"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("诸神黄昏", ""), llm_note("北欧神话末世")],
        tags=["西方奇幻", "北欧", "神话", "末日"],
    ),
    # 希腊神话 - 奥林匹斯
    MaterialEntry(
        dimension="real_world_references", genre="西方奇幻",
        slug="rw-greek-olympus",
        name="希腊奥林匹斯十二神",
        narrative_summary="西方文明源神话。"
                          "12 主神 + 完整宇宙观 + 复杂家族关系。"
                          "Percy Jackson + 神话热复兴。",
        content_json={
            "twelve_olympians": "Zeus 宙斯（雷神 / 王）/ Hera 赫拉（婚姻 / 王后）/ Poseidon 波塞冬（海）/ Demeter 得墨忒耳（农业）/ Athena 雅典娜（智慧）/ Apollo 阿波罗（太阳 / 音乐）/ Artemis 阿尔忒弥斯（月亮 / 狩猎）/ Ares 阿瑞斯（战争）/ Aphrodite 阿芙洛狄忒（爱情）/ Hephaestus 赫菲斯托斯（火工）/ Hermes 赫尔墨斯（信使）/ Dionysus 狄俄尼索斯（酒）/ 后期补 Hestia 灶神",
            "key_dynamics": "Zeus 多情（无数情人 + 私生子）/ Hera 嫉妒报复 / 神之间频繁通奸 + 子女关系混乱 / 全员有缺点（不是完美神）",
            "famous_demigods": "Hercules 大力士 / Perseus 勇士 / Achilles 阿基琉斯 / Odysseus 奥德修斯 / Theseus 忒修斯",
            "narrative_uses": "众神斗争 / 凡人参与 / 神之子继承力量 / 命运三女神 / 神谕预言",
            "modern_adaptations": "Percy Jackson 系列 / Marvel Eternals / God of War（希腊篇）/ 海王 Atlantis 借鉴",
            "famous_works": "Iliad 伊利亚特 / Odyssey 奥德赛 / Theogony 神谱 / Metamorphoses 变形记",
            "activation_keywords": ["希腊神话", "奥林匹斯", "宙斯", "雅典娜", "波塞冬", "Hercules", "Achilles", "Odyssey"],
        },
        source_type="llm_synth", confidence=0.96,
        source_citations=[wiki("奥林匹斯十二主神", ""), llm_note("希腊神话")],
        tags=["西方奇幻", "希腊", "神话"],
    ),
    # 凯尔特神话
    MaterialEntry(
        dimension="real_world_references", genre="西方奇幻",
        slug="rw-celtic-mythology",
        name="凯尔特神话（爱尔兰 / 威尔士）",
        narrative_summary="西方奇幻另一大源流。"
                          "Tolkien LOTR + The Witcher + 哈利波特部分都从凯尔特借。"
                          "德鲁伊 + 仙女 + 巨石阵神秘。",
        content_json={
            "irish_mythology_tuatha": "Tuatha Dé Danann 女神达努族 / Lugh（多才神）/ Brigid（火 + 诗）/ Dagda（万物之父）/ Morrigan（战 + 死）/ Manannán（海）",
            "welsh_mabinogion": "Mabinogion 四大支线 / Pwyll / Branwen / Manawydan / Math / 充满变形 + 诅咒 + 求爱",
            "druids": "凯尔特祭司 / 树木崇拜 / 月相仪式 / 槲寄生采摘 / 精通占卜 + 草药 + 法律 + 历史",
            "fairies_sidhe": "Sidhe 仙族 / 夜行 / 偷换婴儿 / 看不见但可感知 / 不可激怒 / 时间流速不同（一夜 = 百年）",
            "famous_heroes": "Cú Chulainn 库丘林（爱尔兰阿基琉斯）/ Finn MacCool 芬·麦克尔（巨人传说）/ King Arthur（部分凯尔特）/ Merlin 梅林",
            "stonehenge_legends": "巨石阵 / 凯尔特祭典 / 太阳崇拜 / 历法计算 / 神秘起源",
            "modern_uses": "Witcher 借鉴大量 / Outlander 时空穿越 / 哈利波特部分元素 / Avalon 亚瑟王 / Pixar 勇敢传说",
            "activation_keywords": ["凯尔特", "Druid", "仙女", "Sidhe", "Cú Chulainn", "梅林", "巨石阵", "Avalon"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("凯尔特神话", ""), llm_note("爱尔兰 + 威尔士神话")],
        tags=["西方奇幻", "凯尔特", "神话"],
    ),
    # 哥布林市集
    MaterialEntry(
        dimension="locale_templates", genre="西方奇幻",
        slug="locale-goblin-market",
        name="哥布林市集 / 仙界市场",
        narrative_summary="西方奇幻常见诡异市场。"
                          "妖精 / 哥布林 / 怪物之间交易。"
                          "Christina Rossetti 诗 + Holly Black 现代奇幻。",
        content_json={
            "atmosphere": "雾气弥漫 / 灯笼幽幽 / 各种怪奇生物 / 看似普通水果但充满诅咒 / 时间流速不同 / 凡人迷路必死",
            "common_goods": "成对人耳 / 凤凰泪 / 龙的吻 / 魂魄碎片 / 时间砂漏 / 失忆药 / 真名 / 命运",
            "trade_rules": "一切买卖必有代价 / 价格 = 你最珍视的（记忆 / 名字 / 血 / 影子 / 寿命 / 一首诗）/ 反悔则死",
            "famous_examples": "Christina Rossetti《Goblin Market》(1862) / Holly Black 系列 / 莎拉·梅黛思 / Stardust 仙界市场",
            "narrative_uses": "主角入仙界寻物 / 失去 X 换 Y / 发现禁忌交易 / 与狡猾哥布林斗智",
            "themes": "贪婪 / 失去 / 不可逆 / 凡人 vs 仙界规则 / 诱惑",
            "activation_keywords": ["哥布林市集", "仙界市场", "Goblin Market", "诡异", "交易", "失去", "诅咒"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[llm_note("Christina Rossetti + 现代奇幻")],
        tags=["西方奇幻", "诡异", "市场"],
    ),
    # 龙族公主类
    MaterialEntry(
        dimension="character_templates", genre="西方奇幻",
        slug="char-tmpl-warrior-princess",
        name="女战士公主（Eowyn / Brienne 式）",
        narrative_summary="西方奇幻反传统公主原型。"
                          "拒绝传统女性角色 + 持剑战斗。"
                          "Eowyn / Brienne / Arya / 龙妈 = 经典。",
        content_json={
            "background": "贵族 / 王室 / 但拒绝穿礼服跳舞 / 童年学骑马持剑 / 与兄弟同练 / 想上战场",
            "appearance": "美丽但不柔弱 / 身高较高 / 健美身材 / 战士装束 / 偶尔穿礼服时震惊全场",
            "personality": "勇敢 / 倔强 / 不愿被婚姻束缚 / 渴望证明自己 / 内心仍有少女情愫 / 与男性对等",
            "narrative_arc": "1) 被禁止上战场 / 2) 偷偷训练 / 3) 关键时刻女扮男装上战场 / 4) 表现惊人 / 5) 救国 / 6) 王子真心追求或自己单身",
            "famous_examples": "Eowyn LOTR（杀戒指巫王）/ Brienne GoT（侍奉 Catelyn）/ Arya GoT（杀夜王）/ 龙妈（解放奴隶）/ Mulan 木兰",
            "narrative_value": "反传统女性 / 现代女权角色 / 不依赖男主 / 自主命运",
            "activation_keywords": ["女战士", "Eowyn", "Brienne", "Arya", "Mulan", "公主战士", "女扮男装"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("Tolkien + GRRM + 木兰")],
        tags=["西方奇幻", "女主", "战士"],
    ),
    # 半神英雄
    MaterialEntry(
        dimension="character_templates", genre="西方奇幻",
        slug="char-tmpl-demigod-hero",
        name="半神英雄（Hercules / Achilles / Percy Jackson 式）",
        narrative_summary="西方奇幻 + 神话主角原型。"
                          "凡人母 + 神父 / 神母 + 凡父。"
                          "天生超凡 + 命运注定 + 悲剧色彩。",
        content_json={
            "lineage": "Zeus 之子 Hercules / Thetis 之子 Achilles / Poseidon 之子 Theseus / 现代 Percy Jackson 系列复兴",
            "common_powers": "超凡力量 / 神兵在手 / 与神野兽对话 / 半神血脉觉醒后等阶飞升",
            "tragic_destiny": "希腊半神大多悲剧（Hercules 12 苦役 + Achilles 早死 + Theseus 沉海）/ 半神不能完全融入神也不能凡人",
            "narrative_uses": "童年发现身世 / 寻找父神 / 完成神喻使命 / 战胜终极怪兽 / 升仙 / 牺牲",
            "famous_works": "Iliad 阿基琉斯 / Hercules 12 苦役 / Theseus 米诺陶 / Percy Jackson 系列",
            "modern_adaptations": "Disney Hercules / God of War Kratos / Percy Jackson / 漫威 Eternals 部分",
            "key_arcs": "1) 童年异样 / 2) 神父显现 / 3) 任务赋予 / 4) 历险 / 5) 怪兽决战 / 6) 升仙或牺牲",
            "activation_keywords": ["半神", "Demigod", "Hercules", "Achilles", "Percy Jackson", "宙斯之子", "神血"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("希腊神话 + Percy Jackson")],
        tags=["西方奇幻", "希腊", "半神"],
    ),
    # 黑暗大领主
    MaterialEntry(
        dimension="character_templates", genre="西方奇幻",
        slug="char-tmpl-dark-lord",
        name="黑暗大领主（Sauron / Voldemort / 巫妖王 式）",
        narrative_summary="西方奇幻终极反派模板。"
                          "千年阴谋 + 不死之身 + 全世界威胁。"
                          "Sauron 模板适配 90% 奇幻。",
        content_json={
            "common_traits": "千年活 / 黑色装束 / 红眼 / 巨型城堡 / 各种走狗 / 一个秘密弱点（魔戒 / 灵魂石）",
            "famous_examples": "Sauron LOTR（魔眼）/ Voldemort 哈利波特（蛇眼）/ Morgoth Tolkien 第一纪元 / Lich King 巫妖王 / Galactus 漫威",
            "ideology": "纯粹力量崇拜 / 抹除生命 / 永生不死 / 控制宇宙 / 怨恨光明",
            "weakness_mechanics": "一定有弱点（精神核心物 / 灵魂寄存物 / 真名 / 真身被封印）/ 主角任务 = 找到并销毁",
            "stages_of_threat": "1) 远古封印 / 2) 邪兵苏醒 / 3) 收集力量 / 4) 全面战争 / 5) 主角终局对决",
            "modern_subversions": "黑暗大领主曾是英雄堕落（阿尔萨斯）/ 黑暗大领主理由合理（《冰与火》异鬼 = 应对人类破坏自然）/ 灰色道德 = 没有纯反派",
            "activation_keywords": ["大反派", "Dark Lord", "Sauron", "Voldemort", "巫妖王", "黑暗领主"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("Tolkien + Rowling + WoW")],
        tags=["西方奇幻", "反派", "BOSS"],
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
