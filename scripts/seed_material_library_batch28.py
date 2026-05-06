"""
Batch 28: Sports deep dive — basketball / football / esports / racing /
boxing / tennis / Go / chess / e-sports leagues. Activates sports-specific
vocabulary for sports-fiction or sports-as-metaphor narratives.
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
    # 篮球 NBA
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-sport-basketball-nba",
        name="篮球（NBA + CBA）",
        narrative_summary="NBA 30 队 / 东西部 / 季后赛 + 总决赛 / 全明星。"
                          "传奇：乔丹 / 詹姆斯 / 科比 / 库里 / 杜兰特。"
                          "中国 CBA + 姚明 / 王治郅 / 易建联。"
                          "适用体育题材 / 重生球星 / 选秀逆袭。",
        content_json={
            "positions": "控卫 PG / 分卫 SG / 小前 SF / 大前 PF / 中锋 C / 现代位置模糊（小球时代）",
            "nba_structure": "30 队（东 15 + 西 15）/ 82 场常规赛 / 季后赛 16 队（每区前 8）/ 4-of-7 系列赛 / 总决赛 / 选秀 60 顺位 / 工资帽 + 奢侈税",
            "legendary_players": "迈克尔乔丹（公牛 6 冠 + 飞人）/ 科比（湖人 5 冠 + 81 分 + 黑曼巴）/ 詹姆斯（4 冠 4FMVP）/ 邓肯（马刺 5 冠）/ 大鸟伯德 + 魔术师约翰逊 / 张伯伦 100 分 / 拉塞尔 11 冠",
            "current_stars": "斯蒂芬库里（三分革命）/ 凯文杜兰特 / 莱昂纳德 / 字母哥（MVP）/ 约基奇（三 MVP）/ 卢卡东契奇 / 恩比德 / 塔图姆",
            "iconic_moments": "乔丹 The Shot 1989 / 1998 The Last Shot / 雷阿伦三分 G6 2013 / 库里 GSW73 胜 / 詹姆斯 1-3 翻盘 2016",
            "advanced_stats": "PER 效率值 / TS% 真实命中率 / Usage Rate 使用率 / Plus-Minus / VORP / Win Shares / 4 因素 Four Factors",
            "tactics": "挡拆 P&R（最常见）/ 跑轰 / 三角进攻 / 普林斯顿 / Motion / Triangle / Iso 单打 / 死亡 5 小 / 区域联防 2-3 / 1-3-1 / Box-and-1",
            "chinese_basketball": "CBA 20 队 / 姚明（火箭名人堂）/ 王治郅 / 易建联 / 周琦 / 郭艾伦 / 赵继伟 / 男篮黄金一代 / 男篮失意时代",
            "narrative_use": "重生球星（穿越回选秀夜）/ 校园篮球（《灌篮高手》《我喜欢你》）/ NBA 经理 / 草根逆袭 / 篮球场恋爱",
            "activation_keywords": ["NBA", "篮球", "乔丹", "科比", "詹姆斯", "库里", "姚明", "三分", "灌篮"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("NBA", ""), llm_note("篮球")],
        tags=["体育", "篮球", "通用"],
    ),
    # 足球 World Cup + 五大联赛
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-sport-football-world",
        name="足球（世界杯 + 五大联赛）",
        narrative_summary="全球第一运动。世界杯每 4 年 / 32 队 / 巴西 5 冠最多。"
                          "五大联赛：英超 / 西甲 / 意甲 / 德甲 / 法甲。"
                          "梅西 + C 罗时代 + 内马尔 + 姆巴佩 + 哈兰德。",
        content_json={
            "world_cup_history": "1930 起 / 4 年 1 届 / 巴西 5 冠（最多）/ 德国 / 意大利各 4 冠 / 阿根廷 3 冠（含 2022）/ 法国 2 冠 / 乌拉圭 / 英格兰 / 西班牙各 1 / 卡塔尔世界杯 2022 梅西封王",
            "five_major_leagues": "英超 Premier League（最商业化）/ 西甲 La Liga（皇马 + 巴萨）/ 意甲 Serie A（曾经最强）/ 德甲 Bundesliga（拜仁）/ 法甲 Ligue 1（巴黎独大）",
            "uefa_champions_league": "欧冠 = 各国联赛冠亚军 + 名次队参赛 / 32 队小组赛 / 16 强淘汰 / 5 月决赛 / 皇马 14 冠最多 / AC 米兰 + 利物浦 6 / 拜仁 6",
            "legendary_players": "贝利（巴西 3 世界杯）/ 马拉多纳（86 上帝之手 + 世纪进球）/ 贝肯鲍尔（队长 + 教练 + 主席）/ 齐达内 / 罗纳尔多（外星人）/ 梅西（8 金球 + 22 世界杯）/ C 罗（5 金球 + 5 欧冠）",
            "current_stars": "梅西（迈阿密国际）/ C 罗（利雅得胜利）/ 姆巴佩（皇马）/ 哈兰德（曼城）/ 维尼修斯 / 贝林厄姆 / 罗德里 / 拉莫斯",
            "iconic_moments": "1986 马拉多纳上帝之手 + 世纪进球 / 1999 曼联诺坎普逆转 / 2005 利物浦伊斯坦布尔奇迹 / 2014 巴西 1-7 德国 / 2022 梅西封王",
            "tactics": "4-4-2 / 4-3-3 / 3-5-2 / Tiki-Taka 传控（瓜迪奥拉巴萨）/ Gegenpressing 高位逼抢（克洛普）/ Catenaccio 链式防守 / Total Football 全攻全守（米歇尔斯）",
            "chinese_football": "中超 16 队 / 国足三十年低谷 / 99 一代 / 武磊 / 球场氛围依然狂热 / 归化球员尝试",
            "narrative_use": "球员重生（《球状闪电》风格）/ 经理（FM 梗）/ 国足成长 / 校园足球 / 足球商业",
            "activation_keywords": ["足球", "世界杯", "欧冠", "梅西", "C 罗", "马拉多纳", "贝利", "英超", "皇马"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("足球", ""), llm_note("足球")],
        tags=["体育", "足球", "通用"],
    ),
    # 网球
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-sport-tennis",
        name="网球",
        narrative_summary="四大满贯：澳网 / 法网 / 温网 / 美网。"
                          "费德勒 / 纳达尔 / 德约科维奇三巨头 = 男子黄金时代。"
                          "李娜 + 大威小威 + 莎拉波娃 = 女子。"
                          "适用体育竞技 / 富裕家庭 / 留学。",
        content_json={
            "four_grand_slams": "澳网 Australian Open（1 月 + 硬地）/ 法网 Roland Garros（5-6 月 + 红土）/ 温网 Wimbledon（6-7 月 + 草地）/ 美网 US Open（8-9 月 + 硬地）",
            "atp_wta_tours": "ATP 男子 / WTA 女子 / 大师赛 1000 / 500 / 250 / ATP 年终总决赛 8 强 / 大满贯 + 奥运 = 金满贯",
            "big_three_men": "费德勒 Roger Federer（20 大满贯 / 优雅）/ 纳达尔 Rafael Nadal（22 / 红土之王 / 14 法网）/ 德约科维奇 Novak Djokovic（24 + 全满贯 + 史上最多）",
            "next_gen": "梅德韦杰夫 / 阿尔卡拉斯（西班牙新王）/ 辛纳（意大利）/ 鲁内 / 西西帕斯",
            "women_legends": "格拉芙 Steffi Graf（22 + 全满贯）/ 大威 + 小威廉姆斯 Serena（23）/ 莎拉波娃 / 李娜（中国 2 大满贯：法网 2011 + 澳网 2014）/ 海宁 / 海宁 / 巴蒂",
            "scoring_system": "0 / 15 / 30 / 40 / 局点 / Deuce 平 / Ad / 抢七 / 5 盘 3 胜或 3 盘 2 胜 / 男子大满贯 5 盘",
            "court_types": "硬地（澳网 + 美网 + DecoTurf 蓝绿）/ 红土（法网 + 慢 + 高弹）/ 草地（温网 + 快 + 低弹）/ 室内地毯 / 不同表面利不同打法",
            "key_techniques": "正手 Forehand / 反手 Backhand 单反 vs 双反 / 发球 Serve / 截击 Volley / 高压 Smash / 切削 Slice / 上旋 Topspin / 削球 Drop Shot",
            "chinese_tennis": "李娜两大满贯 / 张帅 / 王蔷 / 郑钦文（澳网 2024 亚军 + 奥运冠军）/ 中国网球新黄金一代",
            "narrative_use": "体育题材 / 富家小姐 / 校园网球 / 重生球星 / 网球俱乐部社交",
            "activation_keywords": ["网球", "费德勒", "纳达尔", "德约", "李娜", "大满贯", "温网", "法网", "硬地"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("网球", ""), llm_note("网球")],
        tags=["体育", "网球", "通用"],
    ),
    # 拳击 + 综合格斗
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-sport-boxing-mma",
        name="拳击 + 综合格斗（MMA）",
        narrative_summary="拳击四大组织 WBA / WBC / IBF / WBO + 12 体重级。"
                          "传奇：穆罕默德阿里 / 泰森 / 帕奎奥 / 梅威瑟。"
                          "MMA = UFC 八角笼 + 综合技术 + Conor / Khabib / Jon Jones。",
        content_json={
            "boxing_organizations": "WBA / WBC / IBF / WBO 四大组织 / 拳王头衔分散 / 重磅人物可统一 / 17 个体重级别",
            "weight_classes_men": "重量级（90.7+kg）/ 超中量级 / 中量级 / 次中 / 轻量 / 羽量 / 最低蝇量级 / 共 17 级",
            "boxing_legends": "穆罕默德阿里（70 年代 + 蝴蝶步 + 反越战 + 帕金森）/ 麦克泰森（最快重量级冠军 + 咬耳朵）/ 罗伊琼斯 / 帕奎奥（菲律宾 8 级别冠军）/ 弗洛伊德梅威瑟（50-0）/ 萨乌·阿尔瓦雷兹（墨西哥）",
            "iconic_fights": "拳王阿里 vs 弗雷泽三战 / 阿里 vs 福尔曼丛林之战 1974 / 莱昂纳多 vs 哈格勒 / 泰森 vs 霍利菲尔德咬耳战 / 梅威瑟 vs 帕奎奥 2015",
            "mma_ufc_world": "UFC 总部拉斯维加斯 / 8 角笼 / 5 分钟 3 回合或 5 回合 / 综合武术 = 站立（拳击 / 泰拳）+ 摔跤 + 巴西柔术（地面）",
            "mma_legends": "Royce Gracie（柔术开山）/ Anderson Silva（巅峰统治中量级）/ Georges St-Pierre（GSP）/ Jon Jones（GOAT 候选 + 麻烦缠身）/ Khabib Nurmagomedov（29-0 + 俄罗斯）/ Conor McGregor（爱尔兰 + 双量级）",
            "key_techniques": "站立：直拳 / 勾拳 / 摆拳 / 低扫 / 高扫 / 膝击 / 摔技：双腿抱摔 / 拌摔 / Suplex / 地面：袈裟固 / 三角锁 / 后裸绞 / 木村锁 / V1 锁",
            "thai_boxing": "泰拳 / 八条腿艺术 / 拳 + 肘 + 膝 + 腿 + 内围抱摔 / 地表最强站立",
            "chinese_mma": "张伟丽（UFC 草量级冠军 + 中国第一 UFC 冠军）/ 李景亮 / 严森 / 闫晓楠 / 中国综合格斗起步晚但势头猛",
            "narrative_use": "拳手成长（《洛奇》《摔跤吧爸爸》）/ 重生拳王 / 暗黑地下拳赛 / 一龙真功夫 / 校园格斗",
            "activation_keywords": ["拳击", "MMA", "UFC", "阿里", "泰森", "梅威瑟", "Khabib", "Conor", "张伟丽", "八角笼"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("拳击", ""), llm_note("MMA")],
        tags=["体育", "格斗", "通用"],
    ),
    # F1 赛车
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-sport-f1-racing",
        name="F1 一级方程式赛车",
        narrative_summary="世界顶级赛车运动。10 队 20 车手 / 23 站全球巡回 / 周五练习 + 周六排位 + 周日正赛。"
                          "汉密尔顿 + 维斯塔潘时代。"
                          "速度 350+km/h + 顶级科技 + 战略 + 团队协作。",
        content_json={
            "race_format": "FP1/FP2/FP3 三场练习 / 排位赛（Q1/Q2/Q3 决前 10 发车顺序）/ 正赛 305km 或 2 小时 / 积分前 10 / 25-18-15-12-10-8-6-4-2-1",
            "current_teams": "红牛 Red Bull / 法拉利 Ferrari / 梅赛德斯 Mercedes / 迈凯伦 McLaren / 阿斯顿马丁 / 阿尔法塔伦 / 阿尔法罗密欧 / 哈斯 Haas / 威廉姆斯 Williams / 阿尔派 Alpine",
            "legendary_drivers": "胡安·曼努埃尔·范吉奥（5 冠 50 年代）/ 阿伊尔顿塞纳（巴西 + 神级 + 1994 死）/ 阿兰普罗斯特 / 迈克尔舒马赫（7 冠 + 法拉利时代）/ 刘易斯汉密尔顿（7 冠 + 平舒马赫）/ 塞巴斯蒂安维特尔（红牛 4 连冠）/ 马克斯维斯塔潘（红牛 + 3 冠）",
            "iconic_circuits": "摩纳哥（街道窄 + 不能超车）/ 银石（英国老牌）/ 蒙扎（速度神殿 + 法拉利红海）/ 斯帕（比利时 + Eau Rouge 弯）/ 铃鹿（日本 + 8 字形）/ 上海国际赛车场",
            "tech_innovations": "DRS 减阻系统 / KERS / ERS 能量回收 / 半自动变速器 / 碳纤维单体壳 / 风洞优化 / 模拟器 / 数据 telemetry",
            "race_strategy": "胎选（软中硬 + 雨胎）/ 进站窗口 / 安全车 SC + 虚拟安全车 VSC 时机 / 燃油负载 / 过弯线路",
            "drama_culture": "Ferrari Tifosi（法拉利红魔）/ Drive to Survive 网飞纪录片 / 车手与车手对抗 / 车队内政 / 工程师战术 / Pit Wall",
            "f1_2021_finale": "阿布扎比 + 汉密尔顿 vs 维斯塔潘最终圈反超 + 安全车规则争议 + 时代交替",
            "narrative_use": "赛车手成长（《极速车王》）/ 重生车手 / 中国车手梦 / 团队工程师 / 富商车队老板",
            "activation_keywords": ["F1", "赛车", "汉密尔顿", "维斯塔潘", "舒马赫", "塞纳", "法拉利", "红牛", "蒙扎"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("一级方程式赛车", ""), llm_note("F1")],
        tags=["体育", "赛车", "通用"],
    ),
    # 围棋深化
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-sport-go-deep",
        name="围棋深化（中日韩 + AI）",
        narrative_summary="2500 年东方智慧。中日韩三国争雄。"
                          "古代国手：黄龙士 / 范西屏 / 施襄夏 / 本因坊。"
                          "现代：吴清源 + 木谷实 + 聂卫平 + 曹薰铉 + 李昌镐 + 李世石 + 柯洁。"
                          "2016 AlphaGo 改变围棋史。",
        content_json={
            "rules_basics": "19 路棋盘 = 361 交叉 / 黑先白后 / 7.5 目贴目 / 子有气则活无气则死 / 提子 + 打劫 / 数地 + 数子 = 中日两规则",
            "ranking_system": "段位 1-9 段（业余 + 职业）/ 中国职业 1-9 段 / 日本九段最高 / 韩国类似 / 国际等级分 = ELO 化",
            "ancient_chinese_masters": "黄龙士（清初圣手 + 与梁魏今争霸）/ 范西屏 + 施襄夏（清中期当湖十局）/ 现代发现古谱研习",
            "japanese_masters": "本因坊家 + 安井家 + 井上家 + 林家四家 / 道策（圣 / 17 世纪）/ 道节 / 秀策（不败传说）/ 秀甫 / 本因坊秀哉（昭和初）",
            "modern_legends": "吴清源（昭和棋圣 + 中日双国籍 + 1939-1956 升降十番棋全胜）/ 木谷实（吴的对手 + 木谷道场）/ 高川格 / 坂田荣男 / 大竹英雄 / 武宫正树（宇宙流）/ 小林光一",
            "korean_dynasty": "曹薰铉（曹九段 + 聂卫平劲敌）/ 李昌镐（石佛 + 收官无敌 + 90s-00s 世界第一 + 17 世界冠军）/ 李世石（公认天才 + 中盘暴力 + 2016 抗 AI）/ 朴廷桓",
            "chinese_modern": "陈祖德 / 聂卫平（80 年代擂台赛大杀日本 + 棋圣）/ 马晓春 / 常昊 / 古力 / 柯洁（2017 AlphaGo Master 三连败 + 但仍现役顶尖）/ 范廷钰 / 江维杰",
            "ai_revolution": "1997 IBM 深蓝胜国象 / 围棋因复杂度长期被认为 AI 不可破 / 2015 AlphaGo 5-0 樊麾 / 2016 4-1 李世石（神之一手 78 + 李世石 4 局唯一胜机）/ 2017 Master 60-0 互联网 + 3-0 柯洁 / AlphaGo Zero 自我对弈 / KataGo / 围棋职业从此与 AI 共学",
            "famous_games": "本因坊秀策 - 因彻 1846 耳赤之局 / 吴清源 - 木谷镰仓十番棋 / 聂卫平 1985 擂台赛连胜小林光一 / 李昌镐 - 曹薰铉师徒第二届应氏杯决赛 / 李世石 - AlphaGo G4 神之一手 78 ",
            "go_culture": "围棋 = 哲学 + 美学 + 修养 / 武宫宇宙流（重外势）vs 小林流（实地）/ 韩国实利 vs 日本厚味 / 中国力战 / 流派之争反映文化",
            "narrative_use": "棋圣成长（《棋魂》）/ 古今穿越对弈 / AI 与人对抗 / 围棋少年 / 古代国手归来",
            "activation_keywords": ["围棋", "吴清源", "李昌镐", "李世石", "柯洁", "AlphaGo", "聂卫平", "秀策", "宇宙流"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("围棋", ""), llm_note("围棋史 + AI")],
        tags=["体育", "围棋", "通用"],
    ),
    # 国际象棋
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-sport-chess-deep",
        name="国际象棋深化",
        narrative_summary="起源印度 6 世纪 → 阿拉伯 → 欧洲 → 现代标准化。"
                          "世界冠军血脉：Steinitz → Lasker → 卡帕布兰卡 → 阿廖欣 → 博特温尼克 → 苏联学派 → 菲舍尔 → 卡尔波夫 vs 卡斯帕罗夫 → 卡尔森。"
                          "中国侯逸凡 + 丁立人。",
        content_json={
            "rules_recap": "8x8 棋盘 / 6 种棋子（王后象马车兵）/ 6 走法 / 王车易位 / 吃过路兵 / 升变 / 将军 / 死局 = 输 / 逼和 = 平",
            "world_champions": "Steinitz（首位）/ Lasker（27 年最长）/ 卡帕布兰卡（古巴 + 棋艺机器）/ 阿廖欣（前苏联 + 战术大师）/ 博特温尼克（苏联学派祖）/ 斯梅斯洛夫 / 塔尔（魔鬼）/ 彼得罗相 / 斯帕斯基 / 菲舍尔（69 年神迹 + 1972 大战斯帕斯基）/ 卡尔波夫（位置大师）/ 卡斯帕罗夫（1985-2000 统治 + 与深蓝 + 与卡尔波夫世纪之战）/ 克拉姆尼克 / 阿南德（印度）/ 卡尔森（挪威 + 现役多冠）/ 丁立人 2023（首位中国男冠军）",
            "famous_games": "1851 不朽之局（Anderssen vs Kieseritzky）/ 1858 歌剧院之战（莫菲）/ 1972 菲舍尔斯帕斯基世纪冷战大战 / 1997 卡斯帕罗夫败深蓝 / 2013 卡尔森拿冠",
            "soviet_school": "苏联从 1948 到 2000 几乎垄断国际象棋 / 培养体系 + 国家投入 / 博特温尼克祖师爷 / 卡斯帕罗夫继承 / 倒苏后人才分散到全球",
            "modern_engines": "Stockfish（开源 + 已超越人类）/ AlphaZero（DeepMind + 自学 4 小时超 Stockfish）/ Leela Chess Zero / 引擎评估到 +0.30 已经是优势 / 业余 vs 引擎完全没有胜机",
            "chinese_chess_rise": "侯逸凡（女子 4 冠世界冠军 + 史上最强女棋手）/ 丁立人（2023 男子冠军 + 历史首位）/ 韦奕 / 余泱漪 / 中国队多次奥赛冠军",
            "common_openings": "意大利开局 / 西班牙开局（鲁伊洛佩兹）/ 后翼弃兵 / 西西里防御 / 卡罗康防御 / 法国防御 / 印度防御 / 国王印度 / 尼姆佐印度",
            "tournaments": "世界冠军赛（候选人赛 → 挑战赛）/ 国际象棋奥赛（双年一届团体）/ 烛台杯 / 林那雷斯 / Wijk aan Zee 老牌 / 大师巡回赛",
            "queen's_gambit_phenomenon": "网飞 2020 剧爆红 / 60 年代女棋手虚构 / 推动全球国象热潮 / 老剧本竞技体育 + 性别议题",
            "narrative_use": "棋手传记 / 国象少女 / 重生大师 / 苏联背景冷战 / AI 时代焦虑",
            "activation_keywords": ["国际象棋", "卡斯帕罗夫", "卡尔森", "菲舍尔", "Stockfish", "AlphaZero", "丁立人", "侯逸凡"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("国际象棋", ""), llm_note("国际象棋史")],
        tags=["体育", "国象", "通用"],
    ),
    # 电子竞技 LOL + Dota + CSGO
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-sport-esports-major",
        name="电子竞技主流项目",
        narrative_summary="LoL 英雄联盟（Riot + 拳头 + S 系列赛）/ Dota 2（Valve + TI 国际邀请赛）/ CS:GO 反恐精英 / 王者荣耀 / DNF / Apex / PUBG。"
                          "SKT Faker / EDG / IG / RNG / TI 中国队夺冠 / Astralis。",
        content_json={
            "lol_world_championship": "S 系列赛 = 全球总决赛 / 每年 10-11 月 / 韩国 LCK SKT/T1 多冠 / 中国 LPL 2018 IG + 2021 EDG + 2024 BLG（亚） / 欧洲 LEC 北美 LCS",
            "lol_legends": "Faker 李相赫（SKT/T1 + 4 冠 + 史上最伟大）/ Uzi 简自豪（中国 ADC 神 + 未夺冠遗憾）/ TheShy 姜承录（IG 上单）/ Rookie / Ning / 369 / 小明 / Knight 卓定（中国本土选手）",
            "dota_ti": "TI 国际邀请赛 / 奖金最高电竞赛事（最高 4000 万美元）/ 中国 Wings 2016 + LGD 多次亚军 / OG 蝉联 2018-2019 / 中国 dota 衰落",
            "csgo_world": "CS 反恐精英系列 / Major 锦标赛 / Astralis 黄金一代（丹麦 + 4 冠）/ NaVi（乌克兰 + s1mple）/ 中国 CS 长期沉沦但 17shou + Mo 等坚守",
            "mobile_esports": "王者荣耀 KPL / 中国市场霸主 / AG 超玩会 + RNG.M + 重庆狼队 / 王者世界冠军杯 / 王者亚运会金牌 2023",
            "valorant_overwatch": "Valorant（Riot + 类 CS）/ Overwatch 守望先锋 + OWL 联盟（已撤）/ 暴雪电竞普遍衰落",
            "infrastructure": "战队 + 教练 + 数据分析 + 心理 + 训练赛 Scrim / Bo3 Bo5 多局多败 / Pick & Ban 流派 / 直播平台 Twitch + 斗鱼 + 虎牙 + B 站",
            "esports_culture": "选秀（韩国学院 → 一军）/ 转会窗口 / 战队公司化 / 老选手退役 → 教练 / 主播 / 赞助商主导 / 中韩对抗叙事",
            "iconic_moments": "Faker 千场致敬 / Uzi 1v3 / IG vs FNC 决赛 3-0 / Wings vs DC TI6 / 大魔王经典反杀",
            "narrative_use": "电竞少年（《全职高手》《你是我的荣耀》）/ 重生选手 / 草根战队逆袭 / 国家荣誉 / 电竞主播",
            "activation_keywords": ["电竞", "LOL", "Dota", "CSGO", "Faker", "Uzi", "TI", "S 赛", "王者荣耀"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("电子竞技", ""), llm_note("电竞产业")],
        tags=["体育", "电竞", "通用"],
    ),
    # 田径
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-sport-track-field",
        name="田径（径赛 + 田赛）",
        narrative_summary="奥运第一大项。径赛（短中长跑 / 接力 / 跨栏）+ 田赛（跳跃 + 投掷）+ 全能。"
                          "博尔特 / 苏炳添 / 刘翔 / 乔伊娜 / 凯尼比萨 / 巴雪斯潘卡。",
        content_json={
            "track_events": "100m / 200m / 400m（短）/ 800m / 1500m（中）/ 3000m 障碍 / 5000m / 10000m（长）/ 马拉松 42.195km / 4x100 接力 / 4x400 接力 / 110m 栏（男）100m 栏（女）/ 400m 栏",
            "field_events": "跳跃：跳高 / 跳远 / 三级跳远 / 撑杆跳 / 投掷：铅球 / 铁饼 / 链球 / 标枪 / 全能：男十项 / 女七项",
            "sprint_legends": "尤塞恩博尔特（牙买加 + 100m 9.58 + 200m 19.19 + 史上最快人）/ 卡尔刘易斯（80-90s + 9 奥运金）/ 迈克尔约翰逊 / 苏炳添（亚洲第一 + 9.83 9.85 9.91 99 + 男子百米黄种人最佳）",
            "long_distance_kings": "肯尼亚 + 埃塞俄比亚 (海拔 + 基因 + 训练）/ 凯尼塞贝萨贝拉（5000m + 10000m 双金）/ 法拉赫（英国 + 4 奥运金）/ 基普乔格（马拉松 + 1:59 challenge 突破 2 小时）",
            "high_jump_pole_vault": "跳高：索托马约尔 2.45m / 巴雪斯潘卡 2.42 室内 / 撑杆跳：布勃卡 6.14 / 杜普兰蒂斯 6.24 当代 / 朱建华 80 年代亚洲第一",
            "throwing_events": "铅球：科瓦尔斯 22.91 男 / 铁饼：阿尔特尔 / 标枪：泽勒兹尼 98.48 / 链球：塞迪赫女子 82.98",
            "chinese_track": "刘翔（2004 雅典 110 栏 12.91 + 12.88 世界纪录 + 北京奥运退赛 + 全民偶像 + 全民质疑）/ 苏炳添（百米黄种人最快）/ 朱婷（排球非田径但精神）/ 黄翔（撑杆跳）/ 中国田径长期投入大但精英少",
            "doping_scandal": "本约翰逊 1988 汉城首爆 / 兰斯阿姆斯特朗自行车 / 俄罗斯系统性兴奋剂 + 2016 部分团体禁赛 / 中国马家军 / 田径作弊永远的话题",
            "narrative_use": "运动员逆袭（刘翔题材）/ 退役教练成长（《摔跤吧爸爸》）/ 国家荣耀 / 重生短跑王 / 兴奋剂悬疑",
            "activation_keywords": ["田径", "博尔特", "苏炳添", "刘翔", "100m", "马拉松", "基普乔格", "撑杆跳"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("田径", ""), llm_note("田径运动")],
        tags=["体育", "田径", "通用"],
    ),
    # 游泳
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-sport-swimming",
        name="游泳",
        narrative_summary="奥运第二大项。四种泳姿（自蛙仰蝶）+ 多距离 + 接力 + 个人混合。"
                          "菲尔普斯 23 金 = 史上奥运第一。中国孙杨 / 叶诗文 / 张雨霏。",
        content_json={
            "four_strokes": "自由泳（最快）/ 蛙泳（最慢但有效率）/ 仰泳 / 蝶泳（最累）/ 个人混合（蝶仰蛙自顺序 200m / 400m）",
            "olympic_distances": "50/100/200 自由泳 / 400 自由泳 / 800 自由泳（女）/ 1500 自由泳（男）/ 100/200 其他三泳 / 200/400 个混 / 接力 4x100 + 4x200 自 + 4x100 混",
            "phelps_legend": "迈克尔菲尔普斯 / 23 奥运金牌 + 28 奖牌 / 史上最伟大奥运选手 / 2008 北京 8 金 / 长身高 + 长臂展 + 长脚掌完美生理 / 2016 退役回归再获金",
            "other_legends": "马克斯皮兹（72 慕尼黑 7 金 + 大胡子）/ 索普（澳大利亚 + 蛙泳之外多冠）/ 马尼努多（女子明星）/ 莱德基（女子长距离统治）/ 德雷塞尔",
            "chinese_swimmers": "孙杨（自由泳长距离世界冠军 + 兴奋剂禁赛）/ 叶诗文（伦敦 200 个混 + 400 个混双金 + 16 岁创神迹）/ 张雨霏（蝶泳东京双金）/ 覃海洋（蛙泳）/ 汪顺（个混）",
            "training_regime": "泳池每天 1 万米 + 力量 + 出发 / 转身 / 触壁技术 / 高原训练 / 泳衣科技（Speedo LZR Racer 2008 引发禁用 + 现在限定布料）",
            "narrative_use": "游泳神童 / 孙杨题材 / 重生游泳 / 残奥励志 / 跨界铁三 / 灾难求生（求生游）",
            "activation_keywords": ["游泳", "菲尔普斯", "孙杨", "叶诗文", "自由泳", "蝶泳", "蛙泳", "奥运"],
        },
        source_type="llm_synth", confidence=0.90,
        source_citations=[wiki("游泳", ""), llm_note("游泳竞技")],
        tags=["体育", "游泳", "通用"],
    ),
    # 体操
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-sport-gymnastics",
        name="体操（艺术体操 + 竞技体操）",
        narrative_summary="艺术体操（女子）+ 竞技体操（男 6 项 + 女 4 项）+ 蹦床 + 跳水。"
                          "完美 10 分时代终结 / 现行打分 D + E 难度 + 完成度。"
                          "中国跳水梦之队 + 体操强国。",
        content_json={
            "men_six_events": "自由体操 / 鞍马 / 吊环 / 跳马 / 双杠 / 单杠 / 全能 = 六项总和",
            "women_four_events": "跳马 / 高低杠 / 平衡木 / 自由体操（女子有音乐）/ 全能 = 四项总和",
            "scoring_evolution": "1976 纳迪亚科马内奇 14 岁 7 个完美 10 分 = 当时上限 / 2006 改 D 难度分（无上限）+ E 完成分（10 满分）/ 现总分常 14-16 分",
            "legends": "纳迪亚科马内奇（罗马尼亚 + 14 岁完美 10）/ 拉里萨拉提尼娜（前苏联 + 18 奥运奖牌）/ 维塔利舍尔博（白俄 + 92 巴塞罗那 6 金）/ 西蒙拜尔斯（美国 + 当代王者 + GOAT）",
            "chinese_dive_dynasty": "中国跳水梦之队 / 男子 + 女子双线收割 / 高敏 / 伏明霞 / 郭晶晶 / 吴敏霞 / 陈若琳 / 全红婵（2020 三跳满分轰动）/ 跳水占中国奥运金牌相当部分",
            "trampoline": "蹦床 + 翻转 + 高度 + 难度 / 中国何雯娜 / 董栋 / 朱雪莹 / 中国蹦床奥运强项",
            "rhythmic_gymnastics": "艺术体操（圈 / 球 / 棒 / 带 / 绳）/ 俄罗斯传统统治 / 中国近年崛起",
            "famous_falls": "尤尔琴科跳马以名命名 / 团身跳 / 直体后空翻 / 落地稳 = 完美收官",
            "narrative_use": "体操少女（《奇迹的女儿》）/ 跳水神童（全红婵题材）/ 重生奥运 / 教练成长 / 残酷训练揭露",
            "activation_keywords": ["体操", "跳水", "全红婵", "纳迪亚", "拜尔斯", "蹦床", "鞍马", "平衡木"],
        },
        source_type="llm_synth", confidence=0.90,
        source_citations=[wiki("体操", ""), llm_note("体操竞技")],
        tags=["体育", "体操", "通用"],
    ),
]


async def main():
    print(f"Seeding {len(ENTRIES)} entries...\n")
    inserted, errors = 0, 0
    by_genre, by_dim = {}, {}
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
    print(f"\nBy genre:     {by_genre}")
    print(f"By dimension: {by_dim}")
    print(f"\n✓ {inserted} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    asyncio.run(main())
