"""
Batch 23: Modern subcultures - 二次元 / 汉服 / 电竞 / 国风音乐 / Cosplay /
街舞 / 说唱 / 桌游 / 剧本杀 / 露营 / 潮玩.

Activates contemporary youth culture vocabulary for 都市 / 校园 / 言情 / 娱乐圈.
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
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-modern-erciyuan-otaku",
        name="二次元 / 御宅文化",
        narrative_summary="二次元（来自日语，意为『二维』）指 ACGN（动画 Anime / 漫画 Manga / 游戏 Game / 轻小说 Novel）爱好者文化。"
                          "御宅族（Otaku）= 极致爱好者 / 萌系审美 / CV 文化 / 同人创作 / Cosplay。"
                          "B 站、A 站、Comiket 是核心阵地。",
        content_json={
            "core_categories": "ACGN（动画 / 漫画 / 游戏 / 轻小说）/ 同人 / Cosplay / V Tuber / 痛包痛车",
            "common_terms": "二次元 / 三次元 / 御宅 / 死宅 / 萌 / 燃 / 厨 / 推 / 老婆 / 老公 / 本命 / CP",
            "trope_genres": "Galgame / 后宫 / 治愈 / 热血 / 异世界 / 百合 / 耽美 / 萌豚 / 战斗番",
            "famous_works": "《EVA》《凉宫春日》《K-On》《Fate》《Lovelive》《约会大作战》",
            "subcultures": "B 站直播 / 痛车痛包 / 同人本 / 漫展 / 声控 / V Tuber 投币",
            "narrative_use": "都市青年题材 / 校园 / 言情（二次元 × 三次元）/ 娱乐圈（声优）",
            "activation_keywords": ["二次元", "御宅", "ACGN", "萌", "本命", "推し", "V Tuber"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("二次元", ""), llm_note("二次元文化")],
        tags=["亚文化", "二次元", "现代"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-modern-hanfu-revival",
        name="汉服复兴运动",
        narrative_summary="汉服复兴自 2003 年开始，2010s 后大爆发：唐制 / 宋制 / 明制 / 魏晋 / 齐胸襦裙各形制。"
                          "B 站、淘宝、成都、武汉是中心。"
                          "汉服圈、形制党 vs 仙女党、复古妆容、簪娘文化。"
                          "提供国风 / 古风 / 都市言情中的现代汉服元素。",
        content_json={
            "form_systems": "唐制（齐胸襦裙）/ 宋制（褙子）/ 明制（袄裙 / 比甲）/ 魏晋（窄袖披帛）/ 秦汉（曲裾深衣）",
            "internal_factions": "形制党（严守历史）vs 仙女党（好看就行）/ 改良派 vs 古制派",
            "key_communities": "B 站汉服区 / 淘宝汉服店 / 成都春熙路 / 武汉江汉路 / 杭州西湖",
            "associated_culture": "簪娘（手工发簪）/ 古风妆容 / 汉服摄影 / 婚礼汉服 / 旧学诵读 / 国风音乐",
            "famous_brands": "重回汉唐 / 兰若庭 / 十三余 / 钟灵记 / 华裳九州",
            "narrative_use": "都市国风 / 校园（汉服社）/ 言情（汉服活动相遇）/ 文创题材",
            "activation_keywords": ["汉服", "形制", "齐胸襦裙", "簪娘", "国风", "复兴", "唐制", "明制"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("汉服运动", ""), llm_note("汉服复兴")],
        tags=["亚文化", "汉服", "国风"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-modern-jubensha",
        name="剧本杀文化",
        narrative_summary="剧本杀（推理推剧本）是 2018+ 兴起的桌面游戏：6-8 人围桌扮演剧本角色 / 通过线索讨论找出凶手。"
                          "类型：硬核推理 / 情感 / 恐怖 / 阵营 / 还原。"
                          "都市青年聚会主流。提供推理 / 角色扮演元素。",
        content_json={
            "game_structure": "DM（主持人）开场 → 第一幕介绍 → 自我介绍 → 搜证 → 讨论 → 投票 → 真相揭露 → 复盘",
            "script_types": "硬核推理（机械诡计）/ 情感（虐心剧情）/ 恐怖（惊悚氛围）/ 阵营（多方博弈）/ 还原（历史 / 科幻）",
            "famous_scripts": "《年轮》《风雪山神庙》《荒诞奇妙夜》《死神来了》《我是谁》",
            "subculture_terms": "DM / 玩家 / 凶手 / 证人 / 线索 / 复盘 / 沉浸 / 跑团 / 阵营本",
            "venues": "线下店 / 线上 APP（百变大侦探）/ 私人聚会 / 公司团建",
            "narrative_use": "都市青年聚会 / 言情（剧本杀店相遇）/ 悬疑（剧本杀凶杀案）/ 重生",
            "activation_keywords": ["剧本杀", "推理", "DM", "线索", "凶手", "搜证", "复盘", "阵营本"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("剧本杀", ""), llm_note("剧本杀文化")],
        tags=["亚文化", "剧本杀", "现代"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-modern-chinese-rap",
        name="中文说唱（嘻哈文化）",
        narrative_summary="中文说唱自 1990s 萌芽，2017《中国有嘻哈》大爆发。"
                          "流派：东北 / 华南粤语 / 川渝重庆 / 北京 / 台湾。"
                          "Diss / Battle / Punchline / Flow / Beat 文化。"
                          "代表 GAI / 法老 / VAVA / 万妮达 / 福克斯 / Tizzy T。",
        content_json={
            "regional_schools": "东北（接地气）/ 川渝（方言 + 江湖）/ 北京（北京话）/ 华南粤语 / 台湾（节奏感）",
            "core_concepts": "Flow（韵律节奏）/ Punchline（杀手锏台词）/ Beat（节拍）/ Diss（撕）/ Battle（对决）/ Cypher（围圈即兴）",
            "famous_artists": "GAI（川渝）/ 法老 / VAVA / Tizzy T / 万妮达 / 福克斯 / Higher Brothers / 龙井说唱",
            "subculture_elements": "卫衣 / 帽衫 / 金链 / 街头潮牌 / 涂鸦 / 滑板",
            "narrative_use": "都市青年题材 / 重生说唱手 / 校园（地下说唱）/ 言情（摇滚青年）",
            "activation_keywords": ["说唱", "嘻哈", "Flow", "Diss", "Battle", "GAI", "Punchline"],
        },
        source_type="llm_synth", confidence=0.81,
        source_citations=[wiki("中文说唱", ""), llm_note("中文说唱通识")],
        tags=["亚文化", "说唱", "现代"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-modern-camping-outdoor",
        name="露营 / 户外文化",
        narrative_summary="露营 2021+ 新中产生活方式：精致露营（Glamping）/ 重装徒步 / 自驾穿越 / 帐篷美学 / 山系穿搭。"
                          "代表：天幕 / 蛋卷桌 / 卡式炉 / 户外咖啡 / 篝火夜话。"
                          "提供都市言情『一起露营』场景的现代质感。",
        content_json={
            "categories": "精致露营（Glamping）/ 重装徒步（hiking）/ 自驾穿越 / 攀岩 / 滑雪 / 越野跑 / 桨板 SUP",
            "iconic_gear": "天幕 / 蛋卷桌 / 卡式炉 / 户外咖啡 / 折叠椅 / 营灯 / 睡袋 / 登山靴 / 冲锋衣",
            "famous_brands": "Snow Peak / Helinox / 牧高笛 / 挪客 / Patagonia / Arc'teryx / The North Face",
            "popular_destinations": "成都金堂 / 浙江莫干山 / 北京怀柔 / 海南陵水 / 川西丹巴 / 阿尔山",
            "lifestyle_aesthetic": "山系穿搭 / 篝火夜话 / 星空摄影 / 户外咖啡仪式 / Instagram 风",
            "narrative_use": "都市言情（露营约会）/ 校园（户外社团）/ 重生（户外达人）/ 言情（户外救援）",
            "activation_keywords": ["露营", "天幕", "徒步", "户外", "山系", "Glamping", "篝火"],
        },
        source_type="llm_synth", confidence=0.81,
        source_citations=[wiki("露营", ""), llm_note("户外露营文化")],
        tags=["亚文化", "户外", "现代"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-modern-pop-mart-figures",
        name="潮玩 / 盲盒文化",
        narrative_summary="盲盒（Blind Box）是 2020+ 中国年轻人新消费：泡泡玛特 IP（Molly / Dimoo / Skullpanda / Labubu）/ 隐藏款 / 二级市场炒作。"
                          "Z 世代社交货币。提供都市消费文化和年轻人聚会场景元素。",
        content_json={
            "core_brands": "泡泡玛特 Pop Mart（盲盒巨头）/ 52TOYS / TOP TOY",
            "popular_ips": "Molly / Dimoo / Skullpanda / Labubu / Pucky / Bunny / 大久保",
            "consumption_psychology": "盲盒不确定性 + 收集欲 + 社交炫耀 + 隐藏款（1/144）/ 改娃文化",
            "secondary_market": "闲鱼 / 微博漂流瓶 / 抖音盲盒主播 / 隐藏款溢价 5-10 倍",
            "demographics": "Z 世代女性为主 / 一线城市白领 / 学生党",
            "narrative_use": "都市消费 / 校园 / 言情（送盲盒礼物）/ 重生（潮玩创业）",
            "activation_keywords": ["盲盒", "潮玩", "泡泡玛特", "Molly", "Labubu", "隐藏款", "改娃"],
        },
        source_type="llm_synth", confidence=0.79,
        source_citations=[wiki("盲盒", ""), llm_note("潮玩盲盒文化")],
        tags=["亚文化", "潮玩", "现代"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-modern-streetdance",
        name="街舞文化",
        narrative_summary="街舞自 1970s 美国黑人街区诞生，主要五种：Breaking（霹雳舞）/ Locking / Popping / Hip-Hop / House。"
                          "中国《这就是街舞》《街舞 in China》带火。"
                          "Crew 文化 / Battle / Cypher / Underground 比赛。",
        content_json={
            "five_styles": "Breaking（地板托马斯）/ Locking（卡点定格）/ Popping（机器人 + 震感）/ Hip-Hop（律动）/ House（步伐）",
            "core_concepts": "Crew（队伍）/ Battle（对战）/ Cypher（围圈）/ Freestyle（即兴）/ Routine（编舞）/ Wave（波浪）",
            "famous_china_dancers": "韩宇 / 杨凯 / 王嘉尔 / 韩庚 / 鹿晗 / 黄子韬",
            "venue_culture": "舞蹈室 / 地下 Battle / 综艺舞台 / 街头随性",
            "famous_competitions": "BOTY（Battle of the Year）/ Juste Debout / Red Bull BC One / 这就是街舞",
            "narrative_use": "都市言情（学跳舞偶遇）/ 校园（街舞社）/ 娱乐圈（练习生）/ 重生街舞冠军",
            "activation_keywords": ["街舞", "Breaking", "Hip-Hop", "Battle", "Crew", "Cypher", "Locking"],
        },
        source_type="llm_synth", confidence=0.81,
        source_citations=[wiki("街舞", ""), llm_note("街舞通识")],
        tags=["亚文化", "街舞", "现代"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-modern-cosplay-culture",
        name="Cosplay 文化",
        narrative_summary="Cosplay = Costume + Play：扮演动漫游戏角色。"
                          "1980s 日本起源，2000s 中国大爆发。"
                          "Coser（扮演者）/ 摄影师 / 后期 / 道具师协作。"
                          "漫展 / Comiket / Cure 是核心场域。",
        content_json={
            "key_concepts": "Coser / 摄影师 / 后期 / 道具师 / 假发 / 美瞳 / 化妆 / 服装定制",
            "categories": "动漫角色 / 游戏角色 / 古风（汉服 +）/ Lolita / 兽装 Furry / 私设角色",
            "venues": "漫展（Comicon）/ 影楼出图 / 户外取景 / 网络个人主页",
            "famous_china_cosers": "小柔 / 北川美绪 / 雨绫 / 烧鸡（早期）",
            "associated_culture": "出图 / 抠图 / 还原度 / 神 cos / 梦中情人 cos",
            "narrative_use": "二次元题材 / 校园（cos 社团）/ 都市青年 / 言情（漫展相遇）",
            "activation_keywords": ["Cosplay", "Coser", "漫展", "出图", "Comicon", "假发", "还原度"],
        },
        source_type="llm_synth", confidence=0.81,
        source_citations=[wiki("Cosplay", ""), llm_note("Cosplay 文化")],
        tags=["亚文化", "Cosplay", "现代"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-modern-board-game",
        name="桌游文化",
        narrative_summary="桌游（Board Game）2010s 兴起：欧式（策略 / 经济）/ 美式（叙事 / 战斗）/ 派对（社交）。"
                          "代表：Catan 卡坦岛 / Pandemic 瘟疫危机 / Dixit 妙语说书人 / 狼人杀 / 三国杀。"
                          "提供都市青年聚会场景。",
        content_json={
            "categories": "欧式（策略经济）/ 美式（叙事冲突）/ 抽象（数学）/ 派对（社交）/ 合作（共赢）/ 阵营（多方）",
            "famous_games": "Catan 卡坦岛 / Pandemic 瘟疫危机 / Dixit 妙语说书人 / Carcassonne 卡卡颂 / Splendor 璀璨宝石 / 三国杀 / 狼人杀 / 阿瓦隆",
            "core_concepts": "DM / 玩家 / 资源 / 卡牌 / 骰子 / 板块 / 路线 / 阵营 / 投票 / 隐藏身份",
            "venues": "桌游吧 / 私人聚会 / 学校社团 / 公司团建 / 线上 BGA",
            "narrative_use": "都市言情（桌游吧相遇）/ 校园（桌游社）/ 言情（双方阵营对战）/ 重生（桌游设计师）",
            "activation_keywords": ["桌游", "Catan", "卡坦岛", "狼人杀", "三国杀", "DM", "卡牌"],
        },
        source_type="llm_synth", confidence=0.80,
        source_citations=[wiki("桌上遊戲", ""), llm_note("桌游通识")],
        tags=["亚文化", "桌游", "现代"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-modern-skincare-beauty",
        name="护肤美妆文化",
        narrative_summary="美妆护肤是 21 世纪女性消费核心：基础护肤（清洁 / 保湿 / 防晒）/ 抗老（A 醇 / 玻色因）/ 美妆（彩妆 / 修容）。"
                          "美妆 KOL / 国货崛起（完美日记 / 花西子）/ 直播带货。"
                          "提供都市女性日常细节。",
        content_json={
            "skincare_steps": "卸妆 → 清洁 → 化妆水 → 精华 → 乳液面霜 → 防晒",
            "core_ingredients": "玻尿酸（保湿）/ 烟酰胺（美白）/ A 醇（抗老）/ 玻色因（修复）/ 神经酰胺（屏障）",
            "makeup_basics": "底妆（粉底 / 遮瑕）/ 修容 / 眼影 / 眼线 / 睫毛 / 眉笔 / 口红 / 腮红 / 高光",
            "famous_brands": "国货：完美日记 / 花西子 / 毛戈平；国际：兰蔻 / 雅诗兰黛 / SK-II / 资生堂 / Dior",
            "kol_culture": "李佳琦（口红一哥）/ 薇娅 / 美妆博主 / 测评视频",
            "narrative_use": "都市言情女主细节 / 娱乐圈（化妆师 / 模特）/ 重生（美妆创业）",
            "activation_keywords": ["护肤", "化妆", "玻尿酸", "A 醇", "口红", "底妆", "李佳琦", "完美日记"],
        },
        source_type="llm_synth", confidence=0.80,
        source_citations=[wiki("化妆", ""), llm_note("美妆通识")],
        tags=["亚文化", "美妆", "现代"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-modern-coffee-tea-shop",
        name="咖啡 / 茶饮店文化",
        narrative_summary="咖啡店是都市第三空间（家 + 公司之外）：星巴克 / Manner / Costa / 瑞幸 / 独立精品店。"
                          "茶饮店：喜茶 / 奈雪 / 茶颜悦色 / Coco / 一点点。"
                          "都市言情常用场景。",
        content_json={
            "coffee_categories": "意式（拿铁 / 卡布奇诺 / 美式 / 摩卡）/ 单品手冲 / 冷萃 / 挂耳 / 速溶",
            "famous_coffee_chains": "星巴克 / Costa / Manner / 瑞幸 / Lavazza / Tim Hortons / % Arabica",
            "tea_drink_chains": "喜茶 / 奈雪的茶 / 茶颜悦色（长沙）/ Coco / 一点点 / 蜜雪冰城",
            "drink_types": "奶茶 / 鲜果茶 / 奶盖 / 满杯水果 / 黑糖珍珠 / 烧仙草",
            "venue_atmosphere": "工业风 / 北欧风 / 日式 mok / 复古 / 中式国风",
            "narrative_use": "都市言情（咖啡店初次见面）/ 重生（开店）/ 校园（约学习）/ 职场（约谈）",
            "activation_keywords": ["咖啡店", "拿铁", "美式", "喜茶", "奈雪", "茶颜悦色", "瑞幸"],
        },
        source_type="llm_synth", confidence=0.80,
        source_citations=[wiki("咖啡", ""), llm_note("饮品店文化")],
        tags=["亚文化", "饮品", "现代"],
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
