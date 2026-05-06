"""
Batch 35: Locale templates / signature places / atmospheric settings.
Cross-genre place templates: ancient cities, dungeons, megacities,
forests, mountains, oceans, taverns, schools, prisons.
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
    # 仙侠 - 万年古墓
    MaterialEntry(
        dimension="locale_templates", genre="仙侠",
        slug="locale-ancient-tomb",
        name="上古万年古墓 / 仙人遗迹",
        narrative_summary="仙侠 / 玄幻经典探险地。"
                          "上古修真者陵墓 / 仙人飞升前留下。"
                          "充满机关 + 守墓兽 + 传承宝物。",
        content_json={
            "atmosphere": "千年阴气沉重 / 火把昏黄 / 石碑残缺 / 墓道幽深 / 寒气逼骨 / 不闻虫声鸟鸣",
            "common_features": "九重封印石门 / 飞剑机关 + 万箭齐发 / 守墓尸（不死僵尸）/ 灵识困阵 / 心魔幻境 / 镇墓兽（朱雀玄武）/ 宝物层层",
            "treasure_layout": "外层（凡器 + 灵石）/ 中层（法宝 + 丹药）/ 内层（功法残卷）/ 核心（仙人遗物 + 真传）",
            "danger_levels": "守墓兽 = 等阶最高（与墓主同阶）/ 同阶修士进 = 必死无疑 / 主角必有金手指开路",
            "narrative_uses": "中后期主角资源副本 / 提供质变升级机会 / 朋友牺牲 / 反派同期入墓 = 鹬蚌相争",
            "famous_works": "《盗墓笔记》（虽是非仙侠 / 但通用古墓元素）/ 《诛仙》《凡人修仙传》《完美世界》",
            "activation_keywords": ["古墓", "仙人遗迹", "守墓兽", "封印", "机关", "传承", "幽深", "万年"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("仙侠探险地标杆")],
        tags=["仙侠", "古墓", "副本"],
    ),
    # 仙侠 - 仙宫 / 圣地
    MaterialEntry(
        dimension="locale_templates", genre="仙侠",
        slug="locale-celestial-palace",
        name="仙宫 / 天庭 / 圣地",
        narrative_summary="仙侠最高级别地点。"
                          "仙人居住的飞升后世界。"
                          "九重天 + 各路仙人宫殿。",
        content_json={
            "atmosphere": "祥云缭绕 / 金光万丈 / 仙鹤飞舞 / 仙乐悠扬 / 灵气浓郁如雾 / 永恒春日",
            "structure_layers": "九重天（玉皇大帝主殿）/ 各路仙人独立宫殿 / 蟠桃园 / 凌霄宝殿 / 天牢（关犯仙）",
            "famous_examples": "西游记天庭 / 封神榜玉虚宫 + 截教碧游宫 / 完美世界仙古时代 / 遮天九大圣地",
            "atmospheric_details": "凡人入仙界 = 灵气过浓窒息 / 凡器入仙界 = 自动进化 / 时间流速比凡间慢",
            "narrative_uses": "终极目标 / 飞升后续故事 / 仙界之争 / 凡尘修士冲击仙路 / 仙界堕落（黑暗仙宫）",
            "modern_subversions": "仙界腐败（堕仙）/ 真正的仙也是凡人 / 仙界之上有更高世界",
            "activation_keywords": ["仙宫", "天庭", "九重天", "凌霄宝殿", "圣地", "祥云", "玉虚宫"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("仙侠最高地点")],
        tags=["仙侠", "仙界", "圣地"],
    ),
    # 西方奇幻 - 黑暗森林
    MaterialEntry(
        dimension="locale_templates", genre="西方奇幻",
        slug="locale-dark-forest",
        name="黑暗森林 / 妖怪林",
        narrative_summary="西方奇幻 / 童话经典险地。"
                          "古老茂密 + 阳光不入 + 妖怪出没。"
                          "格林童话 + LOTR + 巫师系列共用元素。",
        content_json={
            "atmosphere": "巨树参天 / 苔藓覆盖 / 阳光不入 / 雾气弥漫 / 鸟兽不闻 / 莫名脚步声",
            "denizens": "树精 / 古树之灵 / 狼群 / 蜘蛛巨怪 / 巫女 / 黑暗精灵 / 巨人 / 哥布林 / 山贼",
            "famous_examples": "LOTR 老森林 / Sleeping Hollow 无头骑士林 / 巫师 3 黑森林 / 寻龙诀塔克拉玛干",
            "navigation_dangers": "迷路诅咒（GPS 失效）/ 时间错乱 / 心理幻觉 / 树木相互移动",
            "narrative_uses": "主角必经险地 / 寻宝关卡 / 隐居贤者所在 / 过界仪式 / 童年记忆背景",
            "metaphor_meaning": "未知 / 无意识 / 内心阴影 / 死亡威胁 / 文明边缘",
            "activation_keywords": ["黑暗森林", "古树", "树精", "巫女", "狼群", "迷路", "森林"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("奇幻森林通用")],
        tags=["西方奇幻", "童话", "森林"],
    ),
    # 西方奇幻 - 龙之巢穴
    MaterialEntry(
        dimension="locale_templates", genre="西方奇幻",
        slug="locale-dragon-lair",
        name="龙之巢穴 / 火山洞窟",
        narrative_summary="西方奇幻 BOSS 战场所。"
                          "古老巨龙的窟穴 + 金银宝藏。"
                          "Hobbit 史矛革 + DnD 龙巢必备。",
        content_json={
            "atmosphere": "硫磺味 / 高温 / 鳞片碎屑铺地 / 骸骨堆积（吃过的英雄）/ 火光闪烁 / 死寂",
            "treasure_pile": "金币山 / 宝石海 / 古剑 + 圣甲 / 失踪的国王王冠 / 卷轴 + 魔法书",
            "dragon_types": "红龙（火）/ 蓝龙（雷）/ 黑龙（酸）/ 绿龙（毒）/ 白龙（冰）/ 金龙（神圣）/ 银龙（守护）",
            "common_dragons": "Smaug（霍比特人）/ 黑龙 Maleficent / 龙妈三龙 / Reign of Fire 龙 / Dragonheart",
            "narrative_uses": "屠龙 = 经典英雄成名战 / 偷宝藏 = 主角早期资源副本 / 友龙 = 御龙骑士流",
            "famous_works": "霍比特人 + LOTR / 龙骑士 / 龙背上的奇兵 / 冰与火之歌",
            "activation_keywords": ["龙巢", "火山", "宝藏", "屠龙", "Smaug", "金币山", "古剑"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("奇幻龙窟通用")],
        tags=["西方奇幻", "龙", "BOSS"],
    ),
    # 都市 - 上海摩天楼
    MaterialEntry(
        dimension="locale_templates", genre="都市",
        slug="locale-shanghai-skyscraper",
        name="上海陆家嘴摩天楼 / 顶级公寓",
        narrative_summary="都市豪门 / 总裁文 / 商战必备地。"
                          "陆家嘴金茂 + 上海中心 + 环球金融中心 = 三巨头。"
                          "顶层公寓 = 主角阶层象征。",
        content_json={
            "real_buildings": "上海中心 632 米 / 环球金融中心 492 米 / 金茂大厦 421 米 / 浦东电视塔 / 国金中心 IFC",
            "atmosphere": "全玻璃幕墙 / 极简白色 + 黑色家具 / 落地窗外灯火 / 真皮沙发 / 鲸钻吊灯 / 一千米高空",
            "common_scenes": "总裁办公室（200 平 + 个人会议室）/ 顶层公寓（无敌江景）/ 私人电梯 / 直升机停机坪 / 顶层私人会所",
            "narrative_uses": "总裁文男主住所 / 商战谈判地 / 黑社会会议地 / 跳楼场景 / 奢华装饰对比贫民区",
            "famous_works": "《何以笙箫默》（上海）/ 《杉杉来了》/ 《千山暮雪》/ 商战类作品",
            "modern_elements": "智能家居 / iPad 控制窗帘 / 私人厨师 / 法拉利 + 兰博基尼车库 / 私人健身房",
            "activation_keywords": ["陆家嘴", "上海中心", "金茂", "总裁办公室", "顶层公寓", "落地窗", "豪门"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("都市豪门必备地")],
        tags=["都市", "豪门", "总裁文"],
    ),
    # 都市 - 老胡同
    MaterialEntry(
        dimension="locale_templates", genre="都市",
        slug="locale-beijing-hutong",
        name="北京老胡同 / 四合院",
        narrative_summary="都市怀旧 / 北京文学背景。"
                          "明清遗留至今的四合院 + 灰色墙面。"
                          "老北京 / 京味文学 / 现实主义。",
        content_json={
            "atmosphere": "灰墙青瓦 / 门楣斗拱 / 槐树阴下 / 蝉鸣不绝 / 邻里嘈杂 / 大爷大妈下棋",
            "famous_hutongs": "南锣鼓巷 / 烟袋斜街 / 什刹海 / 后海 / 鼓楼 / 雍和宫 / 国子监 / 五道营",
            "structure": "院门 → 影壁 → 大门 → 院落 → 正房（北 / 主人）+ 厢房（东西 / 子女）+ 倒座房（南）/ 抄手游廊",
            "modern_changes": "拆迁vs保护 / 老北京搬迁 / 外地人租住 / 改造商业街（南锣鼓巷过度商业化）",
            "narrative_uses": "京味怀旧 / 老人去世 / 邻里恩仇 / 童年记忆 / 非遗手艺人居所",
            "famous_works": "《茶馆》老舍 / 《城南旧事》林海音 / 《五号屠场》/ 《大宅门》/ 《城北纪事》",
            "activation_keywords": ["胡同", "四合院", "北京", "南锣鼓巷", "什刹海", "院落", "京味"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("北京胡同", ""), llm_note("京味文学背景")],
        tags=["都市", "北京", "怀旧"],
    ),
    # 历史 - 长安城
    MaterialEntry(
        dimension="locale_templates", genre="历史",
        slug="locale-tang-changan",
        name="盛唐长安城",
        narrative_summary="历史小说 / 武侠 / 玄幻通用古都。"
                          "唐代世界第一大城 + 80 万人口。"
                          "108 坊 + 朱雀大街 + 西市东市。",
        content_json={
            "scale": "84 平方公里 / 80-100 万人口 / 当时世界第一大都市 / 拜占庭帝国君士坦丁堡的 8 倍",
            "structure": "108 坊（每坊一城）/ 朱雀大街南北贯通 / 长 11 公里 / 宽 150 米 / 西市（外贸 / 番邦）/ 东市（本地豪商）",
            "famous_areas": "大明宫（皇帝居所）/ 兴庆宫（玄宗 + 杨贵妃）/ 慈恩寺（玄奘）+ 大雁塔 / 平康坊（妓女区）/ 朱雀门",
            "atmosphere": "胡商遍地 / 长安八街 / 万国来朝 / 寒山寺夜半钟 / 春风得意马蹄疾",
            "famous_residents": "李白 + 杜甫 + 王维 + 白居易 + 玄奘 + 唐玄宗 + 杨贵妃 + 武则天 + 安禄山",
            "narrative_uses": "唐代历史 / 武侠（如黄易《大唐双龙》）/ 唐穿越 / 公主侠客追逐",
            "famous_works": "《长安十二时辰》《大唐双龙传》《妖猫传》/ 唐代历史正剧",
            "activation_keywords": ["长安", "唐朝", "朱雀大街", "大明宫", "西市", "胡商", "108坊"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("唐长安城", ""), llm_note("历史 + 武侠 + 玄幻通用")],
        tags=["历史", "唐朝", "古都"],
    ),
    # 通用 - 山中道观
    MaterialEntry(
        dimension="locale_templates", genre=None,
        slug="locale-mountain-taoist-temple",
        name="深山道观 / 古刹",
        narrative_summary="武侠 / 玄幻 / 仙侠通用隐居地。"
                          "深山中的道观 / 寺庙。"
                          "归隐 + 修炼 + 师徒传承场景。",
        content_json={
            "atmosphere": "古松苍翠 / 钟声悠悠 / 烟雾缭绕 / 石阶千级 / 鸟语花香 / 远离尘嚣",
            "famous_real_places": "武当山道观 / 青城山道观 / 龙虎山天师府 / 终南山 / 华山西岳庙 / 少林寺 / 五台山",
            "structure": "山门 → 哼哈二将 → 天王殿 → 钟鼓楼 → 大雄宝殿 → 后山修炼洞府 → 师傅闭关室",
            "narrative_uses": "归隐 / 拜师 / 修炼 / 大隐隐于山 / 童年成长 / 师傅去世",
            "famous_works": "《风清扬》武侠 / 《白蛇传》峨嵋 / 《青云志》青云山 / 《道士下山》",
            "modern_subversions": "道观住的是骗子假道士 / 道观改商业景区 / 真正隐居在偏远难入处",
            "activation_keywords": ["道观", "寺庙", "武当山", "青城山", "深山", "古松", "钟声", "归隐"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("传统隐居地")],
        tags=["通用", "山林", "古刹"],
    ),
    # 校园 - 学院 / 学府
    MaterialEntry(
        dimension="locale_templates", genre=None,
        slug="locale-academy-school",
        name="学院 / 学府 / 学校",
        narrative_summary="校园流 / 学院流核心场所。"
                          "现代校园 + 玄幻学院 + 西方魔法学院共用模板。"
                          "Hogwarts / 哈佛 + 北大清华 + 斗破学院。",
        content_json={
            "physical_layout": "教学楼 + 宿舍区 + 食堂 + 图书馆 + 体育馆 + 操场 + 实验楼 + 礼堂 / 校园园林",
            "social_structure": "校长 / 副校长 / 院长 / 教授 / 讲师 / 学生会 / 班长 / 各社团 / 各小群体",
            "famous_real_examples": "哈佛 / MIT / 北大 / 清华 / 牛津剑桥 / 中央戏剧学院 / 北京电影学院",
            "famous_fictional": "Hogwarts（哈利波特）/ 加贝兰学院（斗破苍穹）/ 圣堂武学院 / 中州学院（武动乾坤）/ 上之天学院",
            "narrative_uses": "青春爱情 / 校园暴力 / 班级争斗 / 师生情 / 高考压力 / 毕业告别 / 校友聚会",
            "common_arcs": "新生入学（破冰）/ 比赛大会 / 校园节 / 毕业典礼 / 校史秘密",
            "activation_keywords": ["学院", "学校", "Hogwarts", "校园", "教学楼", "图书馆", "操场", "学生会"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("校园 / 学院通用")],
        tags=["通用", "校园", "学院"],
    ),
    # 武侠 - 江湖客栈
    MaterialEntry(
        dimension="locale_templates", genre="武侠",
        slug="locale-jianghu-inn",
        name="江湖客栈 / 古代驿站",
        narrative_summary="武侠经典聚会场景。"
                          "三教九流 + 信息交汇 + 偶遇高人。"
                          "张艺谋《新龙门客栈》经典符号。",
        content_json={
            "atmosphere": "二楼酒香 / 三教九流 / 大堂喧闹 / 偶尔咳血声 / 某个角落静坐高手 / 跑堂高声 / 油灯昏黄",
            "common_characters": "店家（多半江湖隐退）/ 跑堂（情报员）/ 客商 / 镖师 / 浪迹江湖侠客 / 朝廷探子 / 江湖名流",
            "narrative_functions": "信息交换（偶遇老友 / 听说传闻）/ 偶发冲突（恶霸欺负人）/ 投宿避雨 / 拜师机缘 / 美食交流",
            "famous_examples": "新龙门客栈 / 醉仙楼（黄飞鸿）/ 悦来客栈（李寻欢）/ 风月宝鉴（金庸）/ 三盛客栈（古龙）",
            "common_dishes": "黄酒 / 烧鸡 / 牛肉 / 馒头 / 豆腐 / 酱牛肉 / 花生米",
            "narrative_uses": "开场（主角入店遇关键人物）/ 转折（偶发恶仗）/ 揭秘（旁观高手交锋）/ 旧友重逢 / 临别送行",
            "activation_keywords": ["客栈", "酒楼", "江湖", "跑堂", "醉仙楼", "悦来客栈", "黄酒"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("武侠经典场景")],
        tags=["武侠", "江湖", "客栈"],
    ),
    # 末世 - 废弃城市
    MaterialEntry(
        dimension="locale_templates", genre="末世",
        slug="locale-abandoned-city",
        name="末世废弃城市 / 鬼城",
        narrative_summary="末世 / 后启示录核心场景。"
                          "曾繁华的城市变废墟。"
                          "TLOU + Walking Dead + Fallout 视觉。",
        content_json={
            "atmosphere": "高楼倒塌 / 杂草穿过水泥 / 烧毁汽车 / 墙面斑驳 / 风声呼啸 / 空荡街道 / 鸽群偶飞",
            "common_locations": "废弃超市（资源点）/ 烧毁警局（武器）/ 倒塌商场 / 加油站 / 医院 / 学校 / 高架桥 / 地铁站",
            "dangers": "丧尸群 / 异变野兽 / 强盗团 / 倒塌建筑 / 辐射污染 / 水源中毒 / 食物腐烂",
            "famous_works": "The Last of Us（波士顿 / 匹兹堡）/ Fallout 4（联邦区）/ Walking Dead（亚特兰大）/ 28 天后",
            "narrative_uses": "主角 + 队友穿越鬼城找资源 / 突遇丧尸潮 / 发现幸存者基地 / 揭露大灾难记忆",
            "atmospheric_techniques": "电影镜头：广角空镜 + 风扬纸张 + 残破海报 + 钟楼停摆 = 沉默感传递",
            "activation_keywords": ["末世", "鬼城", "废墟", "丧尸", "Fallout", "Walking Dead", "TLOU"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("末世标杆场景")],
        tags=["末世", "废墟", "鬼城"],
    ),
    # 现代 - 都市夜店
    MaterialEntry(
        dimension="locale_templates", genre="现代",
        slug="locale-urban-nightclub",
        name="都市夜店 / 高端会所",
        narrative_summary="都市重要场景。"
                          "霓虹 + 酒精 + 欲望 + 黑帮 + 商战。"
                          "总裁文 + 黑社会 + 谍战常用。",
        content_json={
            "atmosphere": "霓虹灯交错 / 重金属 / EDM 节奏 / 烟雾缭绕 / 酒精味 / 香水味 / 美女不绝 / 暧昧光线",
            "structure": "卡座区 / 舞池 / DJ 台 / VIP 包厢 / 私人会议室（楼上）/ 后门（黑帮交易）",
            "narrative_uses": "总裁文男女初见 / 黑帮谈判 / 主角拳打恶霸 / 美女搭讪 / 醉酒后失态 / 商业间谍交换情报",
            "common_drinks": "威士忌 / 龙舌兰 / 香槟 / 鸡尾酒（莫吉托 + Long Island Iced Tea）/ 红酒",
            "famous_examples": "上海外滩 / 北京三里屯 / 香港兰桂坊 / Las Vegas 拉斯维加斯 / 东京六本木 / 首尔江南",
            "tropes": "醉酒主角差点被欺负 → 男主救 / 黑帮老大谈生意 = 桌下藏枪 / 卧底特工拍照 / 偶像潇洒",
            "activation_keywords": ["夜店", "酒吧", "VIP包厢", "DJ", "EDM", "兰桂坊", "三里屯", "霓虹"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[llm_note("都市夜场标杆")],
        tags=["都市", "夜店", "现代"],
    ),
    # 现代 - 监狱
    MaterialEntry(
        dimension="locale_templates", genre="现代",
        slug="locale-prison",
        name="监狱 / 劳改所 / 黑监",
        narrative_summary="罪犯 / 复仇 / 越狱故事核心。"
                          "Shawshank Redemption / 越狱 / 监狱风云。"
                          "权力关系 + 帮派 + 绝境求生。",
        content_json={
            "atmosphere": "铁栅栏 / 灰墙 / 钢门哐当 / 警笛 / 厨房油烟 / 操场水泥 / 监舍闷热（夏）/ 寒冷（冬）",
            "internal_hierarchy": "狱卒 → 老大（帮派头目）→ 中层（小头目）→ 普通犯人 → 老实人 / 受害者",
            "gang_dynamics": "种族帮派（白 / 黑 / 拉丁）/ 黑帮帮派 / 老乡帮（中国监狱）/ 牢头（中国传统）",
            "narrative_uses": "主角被陷入狱 → 适应 + 拉势力 + 反击 + 越狱 → 复仇 / 变身大佬",
            "famous_works": "Shawshank Redemption / 越狱（Prison Break）/ 监狱风云（周润发）/ 肖申克的救赎",
            "common_arcs": "新犯入狱（被欺负）/ 找帮派 / 第一次反抗 / 站稳脚 / 越狱筹划 / 出狱复仇",
            "activation_keywords": ["监狱", "牢房", "狱卒", "越狱", "Shawshank", "黑监", "服刑"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("监狱叙事标杆")],
        tags=["现代", "监狱", "越狱"],
    ),
    # 通用 - 海港 / 码头
    MaterialEntry(
        dimension="locale_templates", genre=None,
        slug="locale-harbor-dock",
        name="海港 / 码头 / 仓库区",
        narrative_summary="谍战 / 黑帮 / 走私 / 历险地。"
                          "现代 + 古代 + 武侠通用。"
                          "重要的灰色场景。",
        content_json={
            "atmosphere": "海水咸味 / 鱼腥气 / 铁锈钢索 / 巨型集装箱 / 木板潮湿 / 海鸥盘旋 / 港口工人粗话",
            "common_uses": "走私（毒品 / 武器 / 人口）/ 黑帮交易 / 谍战交换 / 命案抛尸 / 跨国偷渡 / 古代海盗劫掠",
            "famous_examples": "上海外滩 / 香港维多利亚港 / 大连港 / 广州港 / 三亚 / 古代泉州 / 厦门 / 古代马六甲",
            "narrative_uses": "主角追凶到码头 / 黑帮枪战 / 走私船被拦 / 古代海上贸易 / 海盗洗劫",
            "famous_works": "《杀破狼》（张力浪 + 阮经天）/ 《潜伏》谍战 / 《无双》/ 《加勒比海盗》",
            "common_subgenres": "现代 = 走私 + 黑帮 / 古代 = 海上贸易 + 海盗 / 武侠 = 江湖船只暗杀",
            "activation_keywords": ["码头", "海港", "走私", "集装箱", "海盗", "外滩", "维多利亚港"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[llm_note("海港 / 码头通用场景")],
        tags=["通用", "码头", "黑帮"],
    ),
    # 通用 - 地下黑市
    MaterialEntry(
        dimension="locale_templates", genre=None,
        slug="locale-underground-blackmarket",
        name="地下黑市 / 暗市",
        narrative_summary="跨题材灰色地带。"
                          "仙侠暗市 / 武侠黑市 / 都市地下交易 / 赛博朋克黑市。"
                          "稀有 + 违法 + 高利润。",
        content_json={
            "categories": "仙侠（拍卖会 + 妖兽市集）/ 武侠（夜市 + 黑帮交易）/ 都市（古玩 + 黑客 + 武器）/ 赛博朋克（义体 + 数据 + 黑科技）/ 末世（资源以物换物）",
            "atmosphere": "昏暗 / 水洼 / 各色人 / 戴帽口罩 / 隐秘通道 / 暗号 / 地下灯泡 / 冷脸守卫",
            "narrative_uses": "主角寻稀有物 = 入黑市 / 偶遇高人 / 黑吃黑大战 / 拍卖会引大事件 / 信息买卖 / 主角积蓄换神器",
            "famous_examples": "诛仙黑石坊 / 凡人修仙宝坊 / Cyberpunk 2077 黑市 / 哈利波特对角巷夜版",
            "common_items": "禁忌功法 / 上古神器 / 稀有材料 / 黑客工具 / 假身份 / 武器 / 毒品 / 人口",
            "activation_keywords": ["黑市", "暗市", "拍卖会", "夜市", "黑帮交易", "稀有", "走私"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("跨题材灰色地带")],
        tags=["通用", "黑市", "灰色"],
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
