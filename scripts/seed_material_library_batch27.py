"""
Batch 27: Music + Visual Arts + Fashion + Cuisine domains.
Activates aesthetic vocabulary from major art / music / food traditions.
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
    # 古典音乐
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-music-classical",
        name="古典音乐（西方）",
        narrative_summary="巴洛克 → 古典 → 浪漫 → 现代。巴赫 / 莫扎特 / 贝多芬 / 肖邦 / 柴可夫斯基 / 德彪西 / 斯特拉文斯基。"
                          "钢琴奏鸣曲 / 交响曲 / 协奏曲 / 室内乐 / 歌剧。"
                          "适用文艺 / 音乐家传记 / 古典背景。",
        content_json={
            "periods": "巴洛克 1600-1750（巴赫 / 亨德尔 / 维瓦尔第）/ 古典 1750-1820（莫扎特 / 海顿 / 早贝多芬）/ 浪漫 1820-1900（晚贝多芬 / 肖邦 / 李斯特 / 舒曼 / 柴可夫斯基 / 瓦格纳）/ 现代 1900-（德彪西 / 拉威尔 / 斯特拉文斯基 / 肖斯塔科维奇）",
            "key_forms": "交响曲 4 乐章 / 协奏曲（独奏 + 乐队 3 乐章）/ 奏鸣曲 / 弦乐四重奏 / 歌剧 / 艺术歌曲 / 安魂曲",
            "iconic_works": "巴赫《平均律》《哥德堡变奏》/ 莫扎特《安魂曲》第 41《朱庇特》/ 贝多芬第 5 命运 + 第 9 合唱 + 月光奏鸣曲 / 肖邦练习曲 + 夜曲 / 柴《天鹅湖》《1812 序曲》/ 拉赫玛尼诺夫第 2 钢协",
            "instruments": "钢琴 / 小提琴 / 大提琴 / 长笛 / 单簧管 / 双簧管 / 竖琴 / 定音鼓 / 法国号",
            "famous_conductors": "卡拉扬 / 伯恩斯坦 / 富特文格勒 / 阿巴多 / 小泽征尔 / 杜达梅尔 / 克莱伯",
            "famous_pianists": "霍洛维兹 / 鲁宾斯坦 / 里赫特 / 古尔德 / 阿格里奇 / 朗朗 / 王羽佳 / 齐默尔曼",
            "narrative_use": "音乐家传记（《钢琴家》）/ 文艺爱情 / 古典背景烘托 / 神童成长 / 钢琴比赛",
            "activation_keywords": ["古典音乐", "巴赫", "莫扎特", "贝多芬", "肖邦", "钢琴", "交响曲", "协奏曲"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("古典音乐", ""), llm_note("古典音乐史")],
        tags=["音乐", "古典", "通用"],
    ),
    # 摇滚乐
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-music-rock",
        name="摇滚乐 60 年史",
        narrative_summary="50 年代猫王 / Chuck Berry 起 → 60 年代披头士 / 鲍勃迪伦 → 70 年代硬摇滚 + 朋克 → 80 年代金属 → 90 年代垃圾摇滚 → 21 世纪混血。"
                          "电吉他 + 鼓 + 贝斯 + 主唱四件套。",
        content_json={
            "1950s_birth": "Elvis Presley 猫王 / Chuck Berry / Buddy Holly / 黑人布鲁斯 + 乡村音乐杂交诞生 / 蓝调摇滚",
            "1960s_revolution": "Beatles（披头士 1960-1970）/ Rolling Stones / Bob Dylan / Jimi Hendrix / The Doors / Led Zeppelin（晚 60s）/ Beach Boys / Pink Floyd / Woodstock 音乐节 1969",
            "1970s_diversification": "硬摇滚（Led Zeppelin / Deep Purple / AC/DC）/ 朋克（Sex Pistols / Ramones / Clash）/ 重金属（Black Sabbath / Iron Maiden）/ 前卫摇滚（Yes / Genesis）/ Queen 皇后乐队",
            "1980s_metal_pop": "重金属（Metallica / Megadeth）/ 流行金属（Bon Jovi / Guns N' Roses）/ 后朋克（Joy Division / Cure）/ 哥特摇滚 / U2 / Bruce Springsteen",
            "1990s_grunge_alternative": "垃圾摇滚（Nirvana / Pearl Jam / Soundgarden / Alice in Chains）/ 后另类（Radiohead / Oasis / Blur）/ 鲍勃·迪伦诺奖 / 柯本死",
            "21st_century": "数字时代 / Coldplay / Arctic Monkeys / The Strokes / Foo Fighters / 嘻哈逐渐挤掉摇滚主流地位",
            "iconic_albums": "Beatles《Sgt. Pepper》/ Pink Floyd《The Dark Side of the Moon》/ Led Zeppelin IV / Nirvana《Nevermind》/ Radiohead《OK Computer》",
            "chinese_rock": "崔健《一无所有》/ 唐朝 / 黑豹 / 魔岩三杰 / 二手玫瑰 / 谢天笑 / 万能青年旅店",
            "narrative_use": "音乐人传记 / 80-90 年代背景 / 朋克叛逆 / 经典段子（柯本 27 俱乐部）",
            "activation_keywords": ["摇滚", "Beatles", "披头士", "Nirvana", "Pink Floyd", "Led Zeppelin", "崔健", "重金属", "朋克"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("摇滚乐", ""), llm_note("摇滚史")],
        tags=["音乐", "摇滚", "通用"],
    ),
    # 嘻哈
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-music-hiphop",
        name="嘻哈 / 说唱（Hip-Hop）",
        narrative_summary="70 年代 Bronx 黑人贫民区诞生。四元素：MC 说唱 + DJ 打碟 + 涂鸦 + Breakdance。"
                          "东西海岸恩怨 + 2Pac / Notorious BIG / Eminem / Jay-Z / Kanye West / Kendrick Lamar。",
        content_json={
            "four_elements": "MC（说唱）/ DJ（打碟 + 取样）/ Graffiti 涂鸦 / Breakdance / 后加 Beatbox 第五元素",
            "old_school_70s_80s": "DJ Kool Herc / Grandmaster Flash / Sugarhill Gang / Run-DMC / LL Cool J / Public Enemy / NWA（西海岸）",
            "golden_age_90s": "东海岸：Notorious BIG / Wu-Tang Clan / Nas / Jay-Z / 西海岸：2Pac / Snoop Dogg / Dr. Dre《The Chronic》/ 1996 2Pac 死 / 1997 Biggie 死 = 东西恩怨高潮",
            "2000s_mainstream": "Eminem（白人最佳）/ 50 Cent / Kanye West（学院派）/ Lil Wayne / OutKast / Missy Elliott",
            "2010s_trap_modern": "Trap 节奏（Migos / Future）/ Drake / Kendrick Lamar（普利策）/ J. Cole / Travis Scott / Cardi B / Lil Uzi Vert",
            "subgenres": "East Coast（金链 + 复杂押韵）/ West Coast（G-funk + 街头叙事）/ Trap（808 鼓 + 三连音）/ Mumble Rap / Boom Bap / Conscious Rap / Drill",
            "techniques": "Flow 流（节奏踩点）/ Rhyme Scheme 押韵（多韵脚）/ Punchline 包袱 / Storytelling 叙事 / Diss 互怼 / Beef 恩怨 / Cypher 即兴轮唱",
            "chinese_rap": "中国有嘻哈 2017 现象级 / GAI / PG One / 万能青年王嘉尔 / Higher Brothers / 张震岳 / 热狗（台湾）/ 早期阴三儿地下",
            "narrative_use": "都市青年觉醒 / 街头逆袭 / 音乐选秀 / 黑帮阶层故事 / 中国说唱比赛",
            "activation_keywords": ["嘻哈", "说唱", "Hip-Hop", "2Pac", "Eminem", "Kendrick", "Trap", "中国有嘻哈", "Flow", "Punchline"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("嘻哈音乐", ""), llm_note("Hip-Hop history")],
        tags=["音乐", "嘻哈", "通用"],
    ),
    # 西方美术史
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-art-western-history",
        name="西方美术史脉络",
        narrative_summary="文艺复兴 → 巴洛克 → 古典主义 → 浪漫主义 → 印象派 → 后印象 → 立体派 → 抽象表现主义 → 当代。"
                          "达芬奇 / 卡拉瓦乔 / 大卫 / 莫奈 / 梵高 / 毕加索 / 杜尚 / 安迪沃霍尔。",
        content_json={
            "renaissance": "达芬奇 / 米开朗基罗 / 拉斐尔 / 波提切利《维纳斯诞生》/ 透视法 + 解剖学 + 油画",
            "baroque_17c": "戏剧光影 / 卡拉瓦乔（暗调）/ 鲁本斯（动感）/ 伦勃朗（夜巡 + 自画像）/ 维米尔（戴珍珠耳环少女）/ 委拉斯凯兹（宫女图）",
            "neoclassicism_18c": "回归古典 / 大卫（拿破仑加冕 + 马拉之死）/ 安格尔（土耳其浴室）",
            "romanticism_19c": "情感激烈 / 德拉克洛瓦（自由领导人民）/ 哥雅（黑色绘画）/ 透纳（暴风雨海景）/ 弗里德里希（雾海上的旅人）",
            "realism_impressionism": "库尔贝（写实）/ 马奈（草地午餐 + 奥林匹亚 = 现代主义起点）/ 莫奈（睡莲 + 干草堆）/ 雷诺阿 / 德加（芭蕾舞女）",
            "post_impressionism": "梵高（星夜 + 向日葵 + 自画像 + 割耳）/ 高更（塔希提）/ 塞尚（苹果之父）/ 修拉（点彩派）",
            "early_20c_isms": "立体派（毕加索 + 布拉克）/ 野兽派（马蒂斯）/ 表现主义（蒙克《呐喊》）/ 抽象（康定斯基 + 蒙德里安）/ 超现实（达利 + 马格里特）/ 包豪斯",
            "post_war": "抽象表现主义（波洛克泼洒 / 罗斯科色域）/ 波普艺术（沃霍尔金宝汤罐 + 梦露）/ 极简主义 / 观念艺术（杜尚小便池 + 博伊斯）/ 涂鸦（巴斯奎特 + 班克斯）",
            "narrative_use": "艺术家传记 / 画廊悬疑 / 名作失窃 / 文艺爱情 / 跨时代穿越",
            "activation_keywords": ["美术史", "达芬奇", "梵高", "毕加索", "印象派", "立体派", "杜尚", "沃霍尔", "莫奈"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("西方美术史", ""), llm_note("艺术通史")],
        tags=["艺术", "美术", "通用"],
    ),
    # 中国画
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-art-chinese-painting",
        name="中国画体系",
        narrative_summary="工笔 vs 写意 / 山水 vs 花鸟 vs 人物三大科 / 文人画传统。"
                          "顾恺之 / 吴道子 / 范宽 / 黄公望 / 八大山人 / 齐白石 / 张大千 / 徐悲鸿。"
                          "笔墨纸砚四宝 + 提按顿挫 + 留白 + 题跋钤印。",
        content_json={
            "three_subjects": "山水（北宗工笔 + 南宗写意）/ 花鸟（折枝 / 工笔 / 大写意）/ 人物（白描 / 工笔重彩 / 写意）",
            "north_vs_south_school": "北宗（李思训父子 / 工笔重彩 / 院体）/ 南宗（王维 / 文人写意 / 水墨）/ 董其昌总结分宗",
            "key_dynasties": "魏晋顾恺之（女史箴图）/ 唐吴道子（吴带当风）/ 五代荆关董巨（北方山水四大家）/ 北宋范宽 + 郭熙 + 李公麟 + 苏轼倡文人画 / 南宋马远夏圭 / 元黄公望（富春山居图）+ 倪瓒 + 吴镇 + 王蒙 / 明四家：沈周 + 文徵明 + 唐寅 + 仇英 / 清八大山人 + 石涛 + 扬州八怪 / 近代齐白石 + 张大千 + 徐悲鸿 + 黄宾虹 + 林风眠",
            "techniques": "笔法（中锋 / 侧锋 / 逆锋 / 散锋）/ 墨法（焦 / 浓 / 重 / 淡 / 清五墨）/ 皴法（披麻 / 斧劈 / 雨点 / 牛毛 / 折带 / 解索）/ 设色（重彩 / 浅绛 / 没骨 / 泼墨）",
            "philosophy": "气韵生动（六法之首）/ 骨法用笔 / 应物象形 / 经营位置 / 留白即天 / 诗书画印一体",
            "tools": "笔（狼毫 / 羊毫 / 兼毫）/ 墨（油烟 / 松烟）/ 纸（生宣 / 熟宣 / 皮纸）/ 砚（端 / 歙 / 洮 / 澄泥）/ 颜料（朱砂 / 花青 / 藤黄 / 赭石）",
            "iconic_works": "顾恺之《洛神赋图》/ 张择端《清明上河图》/ 黄公望《富春山居图》/ 范宽《溪山行旅图》/ 八大山人冷眼鱼鸟 / 齐白石虾",
            "narrative_use": "古代士大夫 / 画师传奇 / 鉴宝悬疑 / 文人风骨 / 跨代寻画",
            "activation_keywords": ["中国画", "山水", "工笔", "写意", "齐白石", "张大千", "皴法", "气韵生动", "富春山居"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("中国画", ""), llm_note("中国美术史")],
        tags=["艺术", "国画", "通用"],
    ),
    # 摄影
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-art-photography",
        name="摄影艺术史",
        narrative_summary="1839 达盖尔法诞生 → 直接摄影 → 决定性瞬间 → 街头摄影 → 数码革命。"
                          "Atget / Cartier-Bresson / Robert Capa / Ansel Adams / 中国吴印咸。"
                          "镜头 / 光圈 / 快门 / 构图 / 黑白 vs 彩色。",
        content_json={
            "history_phases": "1839 达盖尔银版法 / 19 世纪摄影画意主义模仿绘画 / 20 世纪直接摄影（Group f/64）/ 决定性瞬间（Cartier-Bresson）/ 街头摄影 / 战地摄影 / 时尚摄影 / 数码革命（2000s）",
            "iconic_photographers": "Eugène Atget（巴黎街景）/ Henri Cartier-Bresson（决定性瞬间）/ Robert Capa（战地《士兵之死》）/ Ansel Adams（黑白风景）/ Diane Arbus（边缘人）/ Annie Leibovitz（人像）/ Sebastião Salgado（人道主义）",
            "chinese_photographers": "吴印咸（延安摄影）/ 解海龙（大眼睛希望工程）/ 卢广（污染中国）/ 严明（中国独立摄影）/ 任航",
            "key_photos": "决定性瞬间《圣拉扎尔车站后面》/ 阿富汗少女（Steve McCurry）/ 胜利之吻（V-J Day）/ 火炬手（Robert Frank）/ 大眼睛（解海龙）",
            "technical_basics": "曝光三角：光圈 / 快门 / ISO / 镜头焦距（35mm 标准 / 50mm 人像 / 85mm 长焦 / 24mm 广角）/ 景深 / 构图（三分法 / 黄金比 / 引导线）",
            "genres": "新闻摄影 / 战地 / 街头 / 风光 / 人像 / 时尚 / 商业 / 微距 / 天文 / 水下 / 纪录 / 观念",
            "narrative_use": "摄影师题材 / 战地记者 / 时尚行业 / 调查记者 / 美院学生 / 街拍偶遇",
            "activation_keywords": ["摄影", "Cartier-Bresson", "决定性瞬间", "Robert Capa", "战地", "光圈快门", "黑白", "解海龙"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("摄影", ""), llm_note("摄影史")],
        tags=["艺术", "摄影", "通用"],
    ),
    # 西方时装
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-fashion-western-history",
        name="西方时装史 + 顶级品牌",
        narrative_summary="Worth 1858 创高级订制 → Chanel 解放女性 → Dior New Look → Yves Saint Laurent 男装借入 → 80 年代设计师崇拜 → 90 年代极简 → 21 世纪街头化。"
                          "巴黎 / 米兰 / 纽约 / 伦敦四大时装周。",
        content_json={
            "haute_couture_giants": "Charles Worth（高定鼻祖）/ Coco Chanel（小黑裙 + 男装借入 + 解放束身衣）/ Christian Dior（New Look 1947）/ Yves Saint Laurent（吸烟装）/ Givenchy（赫本造型）/ Balenciaga / Karl Lagerfeld（CHANEL 接班）",
            "italian_milan": "Giorgio Armani（极简）/ Versace（性感巴洛克）/ Prada（极简知性）/ Gucci（80 年代奢华）/ Dolce&Gabbana（地中海风）/ Fendi / Valentino / Bottega Veneta",
            "american_new_york": "Ralph Lauren / Calvin Klein / Tommy Hilfiger / Marc Jacobs / Tom Ford / Michael Kors / Donna Karan",
            "british_london": "Vivienne Westwood（朋克教母）/ Alexander McQueen（暗黑）/ Burberry（风衣）/ Stella McCartney（环保）/ John Galliano（戏剧）",
            "japanese_avant": "Issey Miyake（褶皱）/ Yohji Yamamoto（黑暗解构）/ Comme des Garçons（川久保玲反美）/ Junya Watanabe",
            "modern_streetwear": "Off-White / Supreme / Yeezy / A Cold Wall / Fear of God / 街头与高奢杂交（Virgil Abloh / 余文乐 -madness）",
            "key_concepts": "高级订制 Haute Couture / 高级成衣 Pret-a-Porter / Capsule Collection / Lookbook / 走秀 Runway / Front Row 一线观秀 / Resort 度假系列",
            "fashion_weeks_calendar": "纽约（2 月 + 9 月）→ 伦敦 → 米兰 → 巴黎 / 每年两季春夏 + 秋冬",
            "narrative_use": "时尚行业（《穿普拉达的女王》《Emily in Paris》）/ 设计师传记 / 模特生涯 / 商战奢侈品 / 富二代日常",
            "activation_keywords": ["时装", "Chanel", "Dior", "Hermès", "Gucci", "时装周", "高定", "Vogue", "McQueen"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("时装史", ""), llm_note("时装产业")],
        tags=["时尚", "时装", "通用"],
    ),
    # 法餐
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-cuisine-french",
        name="法餐体系",
        narrative_summary="西餐之首。Carême 现代化 / Escoffier 厨房军团编制 / Bocuse 新派料理。"
                          "前菜 + 汤 + 主菜 + 奶酪 + 甜点 + 咖啡六道。"
                          "母酱五大 + 七种烹饪法 + 米其林评级。",
        content_json={
            "five_mother_sauces": "Béchamel 白酱（牛奶 + 黄油 + 面粉）/ Velouté 天鹅绒酱（白色高汤）/ Espagnole 西班牙酱（褐色高汤）/ Tomate 番茄酱 / Hollandaise 荷兰酱（蛋黄 + 黄油 + 柠檬）",
            "cooking_techniques": "Sauté 煎 / Braise 炖 / Confit 油封 / Sous-vide 真空低温 / Flambé 火焰 / Reduction 收汁 / Roux 油糊 / Beurre Blanc 白黄油酱",
            "iconic_dishes": "鹅肝（Foie Gras）/ 蜗牛（Escargot）/ 法式洋葱汤 / 红酒炖牛肉（Bœuf Bourguignon）/ 鸭胸（Magret de Canard）/ 焗龙虾（Lobster Thermidor）/ 舒芙蕾 Soufflé / 焦糖布丁 / 马卡龙 / 可丽饼 / 牛角包 / 法棍",
            "french_master_chefs": "Auguste Escoffier（现代法餐之父）/ Paul Bocuse（新派 nouvelle cuisine）/ Alain Ducasse（多米其林帝国）/ Joël Robuchon / Pierre Hermé（甜点皇）/ Pierre Gagnaire",
            "wine_pairing": "白酒配白肉 / 红酒配红肉 / 香槟开胃 / 索泰尔纳配鹅肝 / 干邑或波特餐后",
            "michelin_system": "1 星：值得停留 / 2 星：值得绕路 / 3 星：值得专程 / 全球约 3000 家 1 星 + 130 家 3 星 / 必比登推荐 = 平价好店",
            "regional_branches": "巴黎（精致）/ 里昂（家常浓郁）/ 普罗旺斯（地中海）/ 阿尔萨斯（德式）/ 诺曼底（奶油）/ 勃艮第（红酒文化）",
            "narrative_use": "美食题材 / 米其林餐厅故事 / 主厨成长 / 富裕家庭日常 / 巴黎留学 / 商务宴请",
            "activation_keywords": ["法餐", "鹅肝", "舒芙蕾", "Béchamel", "Escoffier", "米其林", "Bocuse", "马卡龙"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("法国菜", ""), llm_note("法餐体系")],
        tags=["美食", "法餐", "通用"],
    ),
    # 中餐八大菜系（深化）
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-cuisine-chinese-eight-deep",
        name="中餐八大菜系深化",
        narrative_summary="鲁 / 川 / 粤 / 苏 / 浙 / 闽 / 湘 / 徽。"
                          "鲁菜以爆 / 烧 / 扒著称为北方代表 / 川菜麻辣鲜香二十四味 / 粤菜清鲜活嫩讲究火候 / 苏菜浓油赤酱讲究刀工。",
        content_json={
            "lu_shandong": "鲁菜（齐鲁）/ 北方代表 / 爆 + 烧 + 扒 + 焖 / 葱烧海参 / 糖醋黄河鲤鱼 / 九转大肠 / 油爆双脆 / 高汤清汤 / 山东孔府菜",
            "chuan_sichuan": "川菜（四川重庆）/ 麻辣鲜香二十四味（鱼香 / 麻辣 / 怪味 / 蒜泥 / 红油 / 椒盐 / 椒麻 / 怪味 / 糊辣等）/ 麻婆豆腐 / 宫保鸡丁 / 回锅肉 / 鱼香肉丝 / 水煮鱼 / 夫妻肺片 / 川式火锅 / 串串香",
            "yue_cantonese": "粤菜（广东）/ 清鲜活嫩 + 极重食材原味 / 白切鸡 / 烧鹅 / 叉烧 / 烧腊 / 早茶点心（虾饺 / 烧麦 / 凤爪 / 肠粉）/ 老火靓汤 / 顺德鱼生 / 顺德双皮奶",
            "su_jiangsu": "苏菜（江苏）/ 浓油赤酱 + 讲究刀工 / 松鼠桂鱼 / 红烧狮子头 / 盐水鸭 / 蟹粉狮子头 / 鸭血粉丝汤 / 阳澄湖大闸蟹 / 文思豆腐（豆腐切丝）",
            "zhe_zhejiang": "浙菜（浙江）/ 清淡鲜嫩 + 重原汁原味 / 西湖醋鱼 / 龙井虾仁 / 东坡肉 / 叫化鸡 / 宋嫂鱼羹 / 杭帮菜代表",
            "min_fujian": "闽菜（福建）/ 海鲜 + 山珍 + 红糟 / 佛跳墙（招牌）/ 沙茶面 / 兴化米粉 / 福建白斩鸡 / 闽南卤面",
            "xiang_hunan": "湘菜（湖南）/ 香辣酸辣 + 重油 / 剁椒鱼头 / 毛氏红烧肉 / 腊味合蒸 / 永州血鸭 / 与川菜不同：辣偏酸不偏麻",
            "hui_anhui": "徽菜（安徽）/ 山珍野味 + 重油重色 + 善用火 / 臭鳜鱼（招牌）/ 毛豆腐 / 火腿炖甲鱼 / 黄山炖鸽 / 一品锅",
            "narrative_use": "美食小说 / 厨师成长 / 餐厅经营 / 地域风情 / 寻味之旅 / 重生回民国开酒楼",
            "activation_keywords": ["八大菜系", "鲁菜", "川菜", "粤菜", "麻婆豆腐", "佛跳墙", "西湖醋鱼", "二十四味"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("八大菜系", ""), llm_note("中餐体系")],
        tags=["美食", "中餐", "通用"],
    ),
    # 日料
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-cuisine-japanese",
        name="日本料理（和食）",
        narrative_summary="2013 联合国非遗。怀石料理 + 寿司 + 天妇罗 + 拉面 + 居酒屋 + 烧肉。"
                          "讲究季节 + 原味 + 摆盘 + 餐具 + 仪式感。"
                          "和服师傅 + 鲜活渔市 + 米饭文化。",
        content_json={
            "main_categories": "和食（传统）/ 洋食（明治后混血）/ 中华料理（拉面 / 饺子 / 麻婆豆腐改良）/ 居酒屋（小吃 + 酒）/ B 级美食（拉面 / 牛丼 / 关东煮）",
            "kaiseki_high_cuisine": "怀石料理 = 茶道前简餐演化 / 7-15 道顺序：先付 → 八寸 → 椀物 → 向付（刺身）→ 烧物 → 煮物 → 蒸物 → 食事 → 水物 / 极致摆盘 + 季节器皿",
            "sushi_world": "握寿司 / 卷寿司 / 散寿司 / 押寿司 / 江户前 vs 关西 / 醋饭 + 海苔 + 山葵 + 鱼料 / 名店：寿司之神小野二郎",
            "famous_dishes": "刺身 / 寿司 / 天妇罗 / 烧鸟 / 寿喜烧 / 涮涮锅 / 怀石料理 / 拉面（豚骨 / 酱油 / 味噌 / 盐）/ 乌冬 / 荞麦 / 牛丼 / 鳗鱼饭 / 章鱼烧 / 大阪烧 / 鲷鱼烧 / 关东煮 / 御好烧",
            "key_ingredients": "米（越光米 / 银舍利）/ 海产（蓝鳍金枪鱼 / 海胆 / 鲍鱼）/ 神户和牛 / 京野菜 / 出汁（昆布 + 鲣节）/ 味噌 / 酱油 / 山葵 / 紫苏 / 柚子",
            "drinks": "清酒（大吟酿 / 纯米吟酿）/ 烧酎 / 啤酒（朝日 / 麒麟 / 札幌）/ 威士忌（山崎 / 响 / 余市）/ 抹茶 / 玄米茶",
            "philosophy": "旬（季节当令）/ 和（和谐）/ 见立（视觉造型借自然）/ 一期一会（茶道 + 怀石精神）/ 间（留白）",
            "narrative_use": "寿司主厨 / 怀石厨房 / 京都古都 / 居酒屋日常 / 黑帮料亭 / 美食漫画移植",
            "activation_keywords": ["和食", "怀石", "寿司", "刺身", "拉面", "天妇罗", "出汁", "米其林日本", "小野二郎"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("日本料理", ""), llm_note("和食")],
        tags=["美食", "日料", "通用"],
    ),
    # 葡萄酒文化
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-cuisine-wine-culture",
        name="葡萄酒文化",
        narrative_summary="法国波尔多 + 勃艮第 + 香槟 + 罗讷河 + 卢瓦尔五大产区。"
                          "意大利 / 西班牙 / 美国加州 / 澳洲 / 智利 / 中国宁夏。"
                          "品种 + 产区 + 年份 + 评级 + 醒酒 + 配餐。",
        content_json={
            "key_grape_varieties": "红：赤霞珠 Cabernet Sauvignon / 梅洛 Merlot / 黑皮诺 Pinot Noir / 西拉 Syrah / 桑娇维塞 Sangiovese / 白：霞多丽 Chardonnay / 长相思 Sauvignon Blanc / 雷司令 Riesling / 灰皮诺 Pinot Grigio",
            "french_appellations": "波尔多（左岸赤霞珠 + 右岸梅洛 / 五大酒庄拉菲 / 拉图 / 玛歌 / 木桐 / 奥比昂）/ 勃艮第（黑皮诺 + 霞多丽 / 罗曼尼康帝 = 世界最贵）/ 香槟区（起泡酒发源）/ 罗讷河 / 卢瓦尔",
            "italian_regions": "Tuscany 托斯卡纳（Chianti / Brunello / Super Tuscan）/ Piedmont 皮埃蒙特（Barolo / Barbaresco）/ Veneto（Prosecco / Amarone）",
            "new_world": "美国加州（Napa Valley 纳帕赤霞珠 + Sonoma）/ 智利 / 阿根廷（Malbec）/ 澳洲（Penfolds Grange / 设拉子）/ 新西兰（Marlborough 长相思 + Pinot）",
            "chinese_wine": "宁夏贺兰山东麓 / 山东烟台 / 新疆 / 张裕 + 长城 + 王朝 + 容辰 + 加贝兰 / 国产精品近年崛起",
            "tasting_steps": "看（颜色 + 挂杯）→ 闻（一闻初香 + 摇杯二闻）→ 品（入口 + 中段 + 余韵）/ 评分系统 RP100 / WS100 / 酒评家 Robert Parker / James Suckling",
            "service": "醒酒 30 分至几小时 / 杯型（波尔多杯 / 勃艮第杯 / 香槟笛）/ 适饮温度（红 16-18℃ / 白 8-12℃ / 起泡 6-8℃）/ 软木塞 vs 螺旋盖",
            "food_pairing": "红肉配单宁重的赤霞珠 / 白肉海鲜配霞多丽长相思 / 甜点配甜白（贵腐酒 / 冰酒）/ 中餐辣菜配甜白或起泡 / 川菜配新世界果味红",
            "narrative_use": "富商品酒局 / 品酒师电影《Sideways》/ 酒庄家族 / 收藏拍卖 / 法国留学 / 葡萄酒投资",
            "activation_keywords": ["葡萄酒", "波尔多", "勃艮第", "拉菲", "罗曼尼康帝", "赤霞珠", "霞多丽", "宁夏", "醒酒"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("葡萄酒", ""), llm_note("葡萄酒文化")],
        tags=["美食", "酒", "通用"],
    ),
    # 咖啡文化
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-cuisine-coffee-culture",
        name="咖啡文化全景",
        narrative_summary="埃塞俄比亚发源 → 阿拉伯传播 → 欧洲咖啡馆 → 意大利浓缩 → 美国连锁 → 第三波精品。"
                          "豆种 + 产地 + 烘焙 + 萃取 + 配方 + 拉花。"
                          "适用都市青年 / 咖啡师 / 创业 / 文艺生活。",
        content_json={
            "two_main_species": "Arabica 阿拉比卡（80%，海拔高，风味好，咖啡因低）/ Robusta 罗布斯塔（20%，低海拔，苦，咖啡因高，常用速溶）",
            "famous_origins": "埃塞俄比亚耶加雪菲（花香果香）/ 肯尼亚 AA（莓果酸）/ 也门摩卡（巧克力）/ 哥伦比亚 / 巴西（甘香）/ 牙买加蓝山 / 巴拿马瑰夏（最贵）/ 印尼曼特宁（厚醇土香）/ 哥斯达黎加 / 危地马拉",
            "roasting_levels": "浅焙（保留产地花果酸）/ 中焙（平衡）/ 中深焙（焦糖巧克力）/ 深焙（浓郁苦烧）/ 第三波偏浅焙突出风味",
            "extraction_methods": "Espresso 意式浓缩（9 bar 压力 25-30 秒）/ 手冲（V60 / Kalita / 滤纸滤）/ 法压壶（浸泡）/ 摩卡壶（家用炉式）/ 虹吸壶 / 冷萃 Cold Brew / 爱乐压 AeroPress",
            "espresso_drinks": "Espresso（30ml 浓缩）/ Americano 美式（兑水）/ Latte 拿铁（牛奶 + 奶泡）/ Cappuccino 卡布奇诺（1:1:1）/ Macchiato 玛奇朵（少量奶泡）/ Mocha 摩卡（巧克力）/ Flat White 澳白（细密奶泡）",
            "third_wave_movement": "1990s 起 / 重视产地溯源 + 单品咖啡 + 浅焙 + 手冲 + 拿铁拉花 / 代表：蓝瓶 Blue Bottle / Stumptown / Intelligentsia / 中国：% Arabica / Manner / Seesaw / Greybox",
            "chinese_market": "星巴克 5000+ 家 / 瑞幸闪电扩张 / 蜜雪 Lucky / 精品店爆发 / 上海 7000+ 咖啡店全球第一",
            "barista_skills": "意式浓缩萃取 / 奶泡打发（Microfoam）/ 拉花（心形 / 树叶 / 郁金香 / 天鹅）/ 手冲技术 / 杯测 Cupping",
            "narrative_use": "都市文艺青年 / 咖啡师创业 / 咖啡馆相遇言情 / 美剧办公文化 / 商务会面",
            "activation_keywords": ["咖啡", "Espresso", "拿铁", "瑰夏", "蓝山", "手冲", "瑞幸", "星巴克", "拉花"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("咖啡", ""), llm_note("咖啡文化")],
        tags=["美食", "咖啡", "通用"],
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
