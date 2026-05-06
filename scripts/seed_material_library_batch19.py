"""
Batch 19: Traditional Chinese arts and scholarship — 琴棋书画茶酒花.
Activates classical Chinese cultural depth for 古风 / 仙侠 / 武侠 / 历史 / 重生.
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
    # 古琴
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-guqin",
        name="古琴文化与名曲",
        narrative_summary="古琴是中国最古老乐器之一（3000+年），文人四艺『琴棋书画』之首。"
                          "形制独特（七弦十三徽）、记谱专用『减字谱』、以『淡和静远』为审美。"
                          "代表名曲与典故：高山流水、广陵散、阳关三叠、平沙落雁、梅花三弄。",
        content_json={
            "instrument_features": "七弦（宫商角徵羽 + 文武）/ 十三徽（按音位）/ 减字谱（专用记谱）/ 桐木面板梓木底",
            "famous_pieces": "《高山流水》（伯牙子期）/ 《广陵散》（嵇康临刑）/ 《阳关三叠》（送别）/ 《平沙落雁》/ 《梅花三弄》/ 《潇湘水云》/ 《忆故人》",
            "famous_legends": "伯牙摔琴谢知音 / 嵇康广陵散绝 / 司马相如凤求凰 / 蔡邕焦尾琴 / 文王操",
            "aesthetic_principles": "淡 / 雅 / 清 / 静 / 远 / 古 / 和（不重技巧重意境）",
            "narrative_use": "古风言情（琴瑟和鸣）/ 仙侠（弹琴退万兵）/ 武侠（绿衣乐师）/ 历史（文人雅集）",
            "activation_keywords": ["古琴", "高山流水", "广陵散", "减字谱", "焦尾琴", "知音", "凤求凰"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("古琴", ""), wiki("高山流水", ""), llm_note("古琴文化通识")],
        tags=["古琴", "文化", "古风"],
    ),
    # 书法
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-calligraphy",
        name="书法五体与名家",
        narrative_summary="书法五体：篆 / 隶 / 楷 / 行 / 草。"
                          "名家谱：王羲之《兰亭序》（行书天下第一）/ 颜真卿《祭侄文稿》（行书天下第二）/ 苏轼《寒食帖》/ 怀素《自叙帖》/ 张旭《古诗四帖》。"
                          "提供古风/历史小说书法场景的专业坐标。",
        content_json={
            "five_styles": "篆书（甲骨金文小篆）/ 隶书（汉碑）/ 楷书（颜柳欧赵）/ 行书（王羲之苏轼）/ 草书（张旭怀素）",
            "famous_calligraphers": "王羲之（书圣）/ 颜真卿（雄浑）/ 柳公权（骨力）/ 欧阳询（严谨）/ 赵孟頫（流美）/ 张旭怀素（草圣）/ 苏黄米蔡（宋四家）/ 文徵明 / 董其昌",
            "iconic_works": "《兰亭序》/ 《祭侄文稿》/ 《寒食帖》（天下第三行书）/ 《九成宫醴泉铭》/ 《多宝塔碑》/ 《自叙帖》",
            "aesthetic_terms": "骨 / 筋 / 血 / 肉（骨力 vs 肉感）/ 中锋 / 侧锋 / 飞白 / 牵丝映带 / 章法 / 气韵生动",
            "narrative_use": "古风言情（书生）/ 历史朝堂（诏书）/ 武侠（剑书相通）/ 修仙（悟道于墨）",
            "activation_keywords": ["书法", "兰亭序", "王羲之", "颜真卿", "草书", "篆书", "中锋", "飞白"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("中国书法", ""), llm_note("书法五体通识")],
        tags=["书法", "文化", "古风"],
    ),
    # 围棋
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-go-weiqi",
        name="围棋（弈）文化",
        narrative_summary="围棋（古称『弈』）是中国最古老博弈，黑白二色 19×19 棋盘。"
                          "理论：定式 / 布局 / 中盘 / 官子 / 死活 / 劫争。"
                          "段位：业余 1-7 段 / 职业 1-9 段。"
                          "棋圣：吴清源、赵治勋、聂卫平、李昌镐、柯洁。围棋象征智慧与人生格局。",
        content_json={
            "rules_basics": "黑白轮流 / 19×19 / 围地多者胜 / 提子 / 劫 / 禁同形重复",
            "phase_structure": "布局（角部 + 边）→ 中盘（攻杀战斗）→ 官子（细收）→ 数目终局",
            "famous_concepts": "金角银边草肚皮 / 走自己的不让对方走 / 厚势 vs 实地 / 攻防一体 / 大场",
            "famous_masters": "吴清源（昭和棋圣）/ 赵治勋（执着）/ 聂卫平（中日擂台）/ 李昌镐（石佛）/ 古力 / 柯洁（人机大战）",
            "philosophical_layer": "围棋 = 人生 / 一着不慎满盘皆输 / 大局观 / 弃子争先 / 静中求动",
            "narrative_use": "古风谋士（一手布天下）/ 现代天才（围棋少年）/ 师徒题材 / 老人传承",
            "activation_keywords": ["围棋", "弈", "定式", "劫争", "段位", "棋圣", "金角银边", "厚势"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("围棋", ""), llm_note("围棋文化通识")],
        tags=["围棋", "文化", "通用"],
    ),
    # 中国象棋
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-xiangqi",
        name="中国象棋文化",
        narrative_summary="中国象棋以楚河汉界为格局，棋子为将士相车马炮兵卒。"
                          "经典布局：仙人指路 / 中炮 / 屏风马 / 飞相 / 起马 / 反宫马。"
                          "残局：双车错 / 大刀剜心 / 闷宫。提供古风/民间博弈场景元素。",
        content_json={
            "pieces": "帅将 / 仕士 / 相象 / 车 / 马 / 炮 / 兵卒",
            "opening_systems": "中炮 / 飞相 / 仙人指路 / 起马 / 屏风马 / 反宫马 / 顺手炮 / 列手炮",
            "famous_endgames": "双车错 / 大刀剜心 / 闷宫 / 卧槽马 / 双马饮泉",
            "famous_terms": "马走日 象走田 / 楚河汉界 / 当头炮 / 兵贵神速 / 双车困将",
            "famous_masters": "胡荣华（十连霸）/ 杨官璘 / 王嘉良 / 吕钦 / 许银川",
            "narrative_use": "古风茶馆 / 历史朝堂博弈隐喻 / 民国象棋会 / 重生象棋少年",
            "activation_keywords": ["象棋", "楚河汉界", "中炮", "马走日", "双车错", "卧槽马"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("象棋", ""), llm_note("中国象棋通识")],
        tags=["象棋", "文化", "通用"],
    ),
    # 茶道
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-tea-culture",
        name="中国茶文化与六大茶类",
        narrative_summary="中国茶分六类：绿（龙井 / 碧螺春）/ 红（祁红 / 滇红）/ 乌龙（铁观音 / 大红袍）/ 黄（君山银针）/ 白（白毫银针）/ 黑（普洱）。"
                          "茶艺：择水 / 备器 / 温杯 / 投茶 / 冲泡 / 品饮。"
                          "陆羽《茶经》、苏轼茶诗、紫砂壶。提供古风文人雅集核心场景。",
        content_json={
            "six_categories": "绿 / 红 / 乌龙（青）/ 白 / 黄 / 黑（普洱）",
            "famous_teas": "西湖龙井 / 碧螺春 / 黄山毛峰 / 信阳毛尖 / 安溪铁观音 / 武夷大红袍 / 君山银针 / 福鼎白毫银针 / 云南普洱",
            "tea_ware": "紫砂壶（宜兴）/ 盖碗 / 公道杯 / 闻香杯 / 茶则茶针茶夹 / 建盏",
            "tea_ceremony": "陆羽《茶经》二十四器 / 唐宋点茶 / 明清泡茶 / 当代功夫茶",
            "famous_water_sources": "天下第一泉（镇江中泠 / 庐山谷帘 / 趵突泉 / 虎跑）",
            "narrative_use": "古风雅集 / 仙侠（茶道悟剑）/ 武侠茶馆江湖 / 历史朝堂赐茶",
            "activation_keywords": ["六大茶类", "龙井", "铁观音", "普洱", "紫砂", "茶经", "功夫茶"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("中国茶文化", ""), wiki("茶经", ""), llm_note("茶文化通识")],
        tags=["茶道", "文化", "古风"],
    ),
    # 古玩鉴赏
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-antique-appraisal",
        name="古玩鉴定与收藏",
        narrative_summary="古玩门类：瓷（青花/官窑）/ 玉（和田/翡翠）/ 字画 / 青铜 / 文房四宝 / 钟表。"
                          "鉴定法：断代 / 辨真伪 / 析工艺 / 看包浆 / 看款识。"
                          "捡漏 / 打眼 / 行话『一眼』。提供古玩鉴宝小说核心要素。",
        content_json={
            "categories": "瓷器（青花 / 釉下彩 / 官民窑）/ 玉器（和田 / 翡翠 / 古玉）/ 字画 / 青铜器 / 文房四宝 / 古钱 / 钟表",
            "appraisal_methods": "断代（窑口 + 工艺）/ 辨真伪（包浆 + 款识）/ 析工艺 / 听声音 / 看胎质",
            "trade_terms": "捡漏（低价高物）/ 打眼（看走眼）/ 一眼（一看就准）/ 走宝 / 货色 / 老货 vs 新货 / 古玩三十六行",
            "famous_appraisers": "马未都 / 王世襄 / 沈从文（古服饰）/ 单士元 / 耿宝昌（瓷器）",
            "famous_markets": "北京潘家园 / 琉璃厂 / 上海城隍庙 / 香港中环 / 苏州文庙",
            "narrative_use": "都市鉴宝 / 重生捡漏 / 古玩世家 / 系统流（鉴定眼）",
            "activation_keywords": ["古玩", "鉴定", "捡漏", "打眼", "包浆", "款识", "潘家园"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("古玩", ""), llm_note("古玩鉴定通识")],
        tags=["古玩", "鉴定", "通用"],
    ),
    # 武术
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-martial-arts-systems",
        name="中国武术体系",
        narrative_summary="中国武术分内家（太极 / 形意 / 八卦）/ 外家（少林 / 武当 / 峨眉 / 南拳）。"
                          "理论：劲（明劲 / 暗劲 / 化劲）/ 招式 / 桩功 / 内功 / 气血。"
                          "武侠 / 都市退役兵神 / 修真都市流必备。",
        content_json={
            "internal_styles": "太极拳（陈杨吴武孙五式）/ 形意拳（五行 + 十二形）/ 八卦掌（走圈）",
            "external_styles": "少林（七十二绝技）/ 武当（剑 + 太极）/ 南拳（咏春 / 洪拳 / 蔡李佛）/ 峨眉（女子）",
            "skill_progression": "明劲（外发力）→ 暗劲（内蓄）→ 化劲（无形）→ 神圆境",
            "training_basics": "桩功（站桩）/ 套路 / 对练 / 实战 / 内功 / 兵器（刀枪剑棍）",
            "famous_masters": "张三丰 / 王重阳 / 杨露禅 / 董海川 / 黄飞鸿 / 霍元甲 / 李小龙 / 叶问",
            "narrative_use": "武侠 / 都市退役兵 / 民国武林 / 异界武学",
            "activation_keywords": ["内家拳", "外家拳", "太极", "形意", "八卦", "明劲", "暗劲", "化劲"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("中国武术", ""), llm_note("武术体系通识")],
        tags=["武术", "武侠", "通用"],
    ),
    # 中医深化
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-tcm-prescriptions",
        name="中药方剂经典",
        narrative_summary="中医方剂学按功能分十大类：解表 / 清热 / 泻下 / 和解 / 温里 / 补益 / 安神 / 开窍 / 理气 / 活血。"
                          "经典名方：麻黄汤 / 桂枝汤 / 小柴胡汤 / 四物汤 / 八珍汤 / 六味地黄丸 / 逍遥散。"
                          "中医穿越神医文必备。",
        content_json={
            "ten_categories": "解表 / 清热 / 泻下 / 和解 / 温里 / 补益 / 安神 / 开窍 / 理气 / 活血",
            "famous_prescriptions": "麻黄汤（风寒）/ 桂枝汤（伤寒）/ 小柴胡汤（少阳）/ 四物汤（补血）/ 八珍汤（气血双补）/ 六味地黄丸（滋阴）/ 金匮肾气丸（温阳）/ 逍遥散（疏肝）/ 归脾汤（心脾）/ 安宫牛黄丸（开窍）",
            "compounding_principle": "君臣佐使 — 主药 / 辅佐 / 缓和 / 引经",
            "famous_classics": "《伤寒杂病论》张仲景（医圣）/ 《本草纲目》李时珍 / 《千金方》孙思邈 / 《温病条辨》",
            "diagnostic_pairings": "脉证合参 / 舌象 / 八纲辨证 / 脏腑辨证 / 六经辨证 / 卫气营血",
            "narrative_use": "中医穿越 / 系统流神医 / 古风疫病题材 / 玄幻医修",
            "activation_keywords": ["麻黄汤", "桂枝汤", "小柴胡", "四物汤", "六味地黄", "君臣佐使", "伤寒论"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("方剂学", ""), wiki("伤寒杂病论", ""), llm_note("中医方剂通识")],
        tags=["中医", "方剂", "通用"],
    ),
    # 中国古典园林
    MaterialEntry(
        dimension="locale_templates", genre=None,
        slug="locale-china-classical-garden",
        name="中国古典园林美学",
        narrative_summary="中国古典园林分皇家（颐和园 / 圆明园 / 承德避暑山庄）和私家（拙政园 / 留园 / 网师园 / 沧浪亭）。"
                          "造园三原则：师法自然 / 移步换景 / 借景。"
                          "提供古风言情 / 历史 / 仙侠的标志性场景。",
        content_json={
            "garden_types": "皇家园林（北方雄浑）/ 江南私家园林（精致）/ 寺观园林 / 文人园林",
            "design_principles": "师法自然 / 移步换景 / 借景（远借 / 邻借 / 仰借 / 俯借）/ 框景 / 漏景",
            "key_elements": "山（假山 / 太湖石）/ 水（池 / 溪 / 瀑）/ 建筑（亭 / 台 / 楼 / 阁 / 廊 / 榭）/ 植物（松竹梅 / 牡丹）/ 题字（匾额对联）",
            "famous_gardens": "拙政园（苏州）/ 留园 / 网师园 / 沧浪亭 / 狮子林 / 颐和园 / 圆明园 / 承德避暑山庄 / 苏州耦园（恋人）",
            "narrative_use": "古风言情场景 / 仙侠洞府参考 / 历史朝堂寝殿 / 红楼梦类大观园",
            "activation_keywords": ["园林", "拙政园", "颐和园", "假山", "亭台", "借景", "移步换景"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("中国园林", ""), llm_note("古典园林通识")],
        tags=["园林", "建筑", "古风"],
    ),
    # 风水
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-fengshui",
        name="风水堪舆体系",
        narrative_summary="风水（堪舆）是中国传统空间环境学：寻龙点穴 / 砂水关锁 / 阴阳五行 / 罗盘二十四山。"
                          "三大派别：形势派（峦头）/ 理气派（罗盘）/ 玄空派（飞星）。"
                          "适用于灵异 / 盗墓 / 古风 / 都市命理小说。",
        content_json={
            "three_schools": "形势派（看山形）/ 理气派（用罗盘）/ 玄空派（飞星挨星）",
            "core_concepts": "龙（山脉）/ 穴（结地）/ 砂（环山）/ 水（水流）/ 向（朝向）/ 气（生气死气）/ 五行 / 八卦 / 二十四山",
            "tools": "罗盘（罗经）/ 三合 / 三元 / 玄空 / 紫白",
            "applications": "阳宅（住房）/ 阴宅（坟墓）/ 商铺布局 / 城市规划",
            "famous_classics": "《葬经》郭璞 / 《青囊经》/ 《撼龙经》杨筠松 / 《地理五诀》",
            "narrative_use": "灵异家族 / 盗墓寻龙 / 都市命理大师 / 古风风水之争",
            "activation_keywords": ["风水", "堪舆", "龙脉", "穴位", "罗盘", "玄空", "寻龙点穴"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("风水", ""), llm_note("风水堪舆通识")],
        tags=["风水", "玄学", "通用"],
    ),
    # 道教内丹
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-daoism-inner-alchemy",
        name="道教内丹学",
        narrative_summary="道教内丹学以人体为鼎炉，元精元气元神为药材，通过吐纳导引炼成金丹（性命双修）。"
                          "钟吕传统、全真南北宗、丹经四大宗师（魏伯阳 / 钟离权 / 吕洞宾 / 张伯端）。"
                          "提供仙侠 / 修真 / 灵异修炼描写的道家底蕴。",
        content_json={
            "core_principles": "性命双修（性 = 心神，命 = 形气）/ 三花聚顶 / 五气朝元 / 炼精化气炼气化神炼神还虚",
            "key_stages": "筑基 → 小周天 → 大周天 → 结丹 → 元婴 → 阳神出窍 → 还虚合道",
            "famous_classics": "《周易参同契》（魏伯阳，万古丹经王）/ 《悟真篇》（张伯端）/ 《钟吕传道集》/ 《太乙金华宗旨》",
            "lineages": "钟吕传统 / 全真南宗（张伯端）/ 全真北宗（王重阳）/ 武当（张三丰）",
            "famous_practitioners": "魏伯阳 / 钟离权 / 吕洞宾 / 张伯端 / 王重阳 / 丘处机 / 张三丰",
            "narrative_use": "仙侠修炼描写 / 道家心法 / 重生修真 / 古风奇遇",
            "activation_keywords": ["内丹", "性命双修", "周天", "金丹", "元婴", "钟吕", "周易参同契"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("内丹术", ""), wiki("周易参同契", ""), llm_note("道教内丹通识")],
        tags=["道教", "修炼", "仙侠"],
    ),
    # 戏曲
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-china-opera-traditions",
        name="中国戏曲流派",
        narrative_summary="中国戏曲三百多种地方剧种，主流：京剧（国粹）/ 昆曲（百戏之祖）/ 越剧（江南）/ 黄梅戏 / 豫剧 / 川剧（变脸喷火）/ 秦腔（高亢）。"
                          "角色行当：生旦净末丑。表演程式：唱念做打 + 手眼身法步。",
        content_json={
            "major_genres": "京剧 / 昆曲 / 越剧 / 黄梅戏 / 豫剧 / 川剧 / 秦腔 / 评剧 / 粤剧 / 沪剧",
            "role_categories": "生（老生 / 小生 / 武生）/ 旦（青衣 / 花旦 / 武旦 / 老旦）/ 净（花脸）/ 末（中老年男）/ 丑",
            "performance_arts": "唱（声腔）/ 念（韵白 / 京白）/ 做（身段）/ 打（武戏）/ 手眼身法步 / 四功五法",
            "famous_plays": "京剧《贵妃醉酒》《霸王别姬》《长坂坡》/ 昆曲《牡丹亭》《长生殿》/ 越剧《梁祝》/ 黄梅戏《天仙配》/ 川剧变脸",
            "famous_masters": "梅兰芳（梅派）/ 程砚秋（程派）/ 荀慧生（荀派）/ 尚小云（尚派）/ 周信芳（麒派）",
            "narrative_use": "民国戏班题材 / 古风戏曲世家 / 重生戏曲大师 / 言情艺术家",
            "activation_keywords": ["京剧", "昆曲", "梅兰芳", "霸王别姬", "贵妃醉酒", "唱念做打", "生旦净丑"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("中国戏曲", ""), wiki("京剧", ""), llm_note("戏曲通识")],
        tags=["戏曲", "传统艺术", "通用"],
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
