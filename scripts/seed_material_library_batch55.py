"""
Batch 55: world_settings depth for niche genres + character_archetypes for emerging spaces +
thematic_motifs for niche genres.
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
    # ═══════════ WORLD SETTINGS（niche/emerging）═══════════
    MaterialEntry(
        dimension="world_settings", genre="洪荒",
        slug="honghuang-world-pre-kalpa-cosmos",
        name="洪荒世界设定：盘古开天后的洪荒大陆",
        narrative_summary="洪荒文世界观骨架：盘古开天后形成的混沌世界 / 三十六重天 / 九幽地府 / 四海八荒 / "
                          "凤巢龙宫 / 不周山与四方海域 / 是一切修仙文的源流。",
        content_json={
            "cosmology": {
                "三十六重天": "由低到高 / 离恨天最高",
                "九幽地府": "六道轮回 / 由后土娘娘掌管",
                "四海": "东南西北 / 各有龙王 / 四海龙王属下",
                "八荒": "中央洪荒 + 八方蛮荒",
                "不周山": "支撑天地之柱 / 共工怒触后倒",
                "凤巢": "凤族祖地 / 西岐之西",
                "龙宫": "东海最深 / 镇海神针",
                "玄黄塔": "通天彻地 / 鸿钧居其中",
            },
            "races": {
                "巫族": "盘古十二祖巫 / 力量最强 / 不通灵",
                "妖族": "天庭统治 / 帝俊太一 / 数量最多",
                "人族": "女娲所造 / 起初最弱 / 后来居上",
                "上古魔神": "陨落者 / 残魂飘荡",
                "龙凤": "上古霸主 / 大劫后衰落",
                "鳞甲": "龙族支脉",
                "羽属": "凤族支脉",
                "麟趾": "麒麟一族",
            },
            "great_kalpas": {
                "龙汉初劫": "龙汉两族争霸 / 三族联盟瓜分天地",
                "巫妖大战": "巫妖两族灭世 / 不周山倒",
                "封神大战": "成汤灭亡 / 阐截教斗",
                "西游劫": "佛教东进 / 道教衰",
                "未来劫": "尚未到来 / 留待后世",
            },
            "physical_laws": {
                "灵气浓度": "极高 / 万物皆可成仙",
                "天道": "意志体现 / 鸿钧合道",
                "因果律": "万物有因必有果 / 圣人也无法逆",
                "气运": "可以争夺 / 可被夺取",
            },
            "key_artifacts_locations": "紫霄宫 / 火云洞 / 五庄观 / 蟠桃园 / 离恨天 / 兜率宫",
            "activation_keywords": ["盘古", "三十六重天", "四海八荒", "巫妖", "封神", "鸿钧"],
        },
        source_type="llm_synth", confidence=0.88,
        source_citations=[wiki("封神演义", "原典世界观"), wiki("山海经", "上古地理"), wiki("淮南子", "古代宇宙论")],
        tags=["洪荒", "世界观", "上古", "三教"],
    ),

    MaterialEntry(
        dimension="world_settings", genre="女尊",
        slug="nuzun-world-matriarchal-empire",
        name="女尊世界设定：女子掌权的封建帝国",
        narrative_summary="标准女尊世界观：女子掌权 + 男子操持家务 / 完整反转的法律/宗法/经济/教育/文化体系 / "
                          "并非简单性别替换 / 整个文明逻辑都基于母系优先。",
        content_json={
            "legal_inversion": {
                "婚姻法": "女主娶男 / 男从女姓 / 一女多夫合法",
                "继承法": "嫡女继承 / 男子无继承权（部分朝代允许有限继承）",
                "财产法": "女子掌控家产 / 男子嫁妆陪嫁",
                "刑法": "通奸男方判重 / 妻子可休夫",
                "兵役法": "女子从军 / 男子免役（部分许役）",
            },
            "economic_structure": {
                "女子主营": "农耕主力 + 商贾大宗 + 官商一体",
                "男子辅营": "织绣 + 烹饪 + 教书 + 艺术",
                "女子科举": "完全垄断官场",
                "男子职业上限": "御医、画师、文人、农夫、匠人",
            },
            "education_system": {
                "女学": "六艺 / 兵法 / 政事",
                "男学": "持家 / 才艺 / 妇德 / 美学（罕见识字）",
                "私塾": "男女分校 / 女子学堂普及 / 男子学堂多富贵之家",
            },
            "cultural_values": {
                "女子美德": "刚强 / 果断 / 责任 / 担当",
                "男子美德": "温柔 / 体贴 / 守身 / 持家",
                "审美": "女子方刚为美 / 男子柔美为善",
                "禁忌": "女子柔弱 = 失礼 / 男子强势 = 不端",
            },
            "religion_inversion": {
                "至上神": "女娲 / 西王母为主神",
                "祖先崇拜": "女祖优先 / 男祖陪祀",
                "婚礼仪式": "女子披红 / 男子穿白",
            },
            "subtle_inequalities": {
                "母系皇室": "皇位传嫡女 / 庶女不立",
                "宗法": "母传女 / 父系不录",
                "贞节观": "男子贞洁视为美德 / 二嫁难嫁",
            },
            "activation_keywords": ["女尊", "母系", "嫡女", "皇夫", "妻主", "女子科举"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("摩梭族", "母系氏族参考"), wiki("女儿国", "古典文学反转设定")],
        tags=["女尊", "世界观", "母系", "性别反转"],
    ),

    MaterialEntry(
        dimension="world_settings", genre="无限流",
        slug="wuxian-world-trial-realm",
        name="无限流世界设定：主神空间与试炼世界",
        narrative_summary="无限流标准世界观：主神空间 / 试炼世界（每个世界基于一部电影/小说/游戏） / "
                          "团队竞争 / 永久死亡 / 这是『跨题材剧情』的元世界。",
        content_json={
            "main_god_space": {
                "形态": "白色阶梯 + 任务大厅 + 黑屋（关被惩罚的）",
                "管理者": "主神（人格化AI）/ 冷漠 / 残忍 / 公正",
                "玩家": "被强行带入的现代人 / 等级分1-9级",
                "积分": "完成世界获得 / 用于强化基因或购买道具",
            },
            "trial_worlds": {
                "电影类": "异形/活死人黎明/午夜凶铃/毁灭战士",
                "小说类": "三国/西游/金庸/三体",
                "游戏类": "生化危机/求生路/丧尸围城",
                "原创类": "主神原创世界 / 难度SS+",
                "进入方式": "强制 / 拒绝则积分降级或抹杀",
            },
            "team_dynamics": {
                "组队规则": "1-9人 / 团队评分基于最弱者",
                "信任问题": "可能被同伴出卖 / 队友是临时的",
                "永久死亡": "在世界中死亡 / 不可复活",
                "团队类型": "求生派 / 强者派 / 收集派 / 反主神派",
            },
            "world_difficulty_scale": {
                "F级": "新手村 / 普通现代",
                "D级": "稍危险 / 普通战争",
                "C级": "异形等危险电影",
                "B级": "丧尸/恐怖",
                "A级": "克苏鲁/超自然",
                "S级": "禁忌世界 / 已有团灭",
                "SS级": "终极挑战 / 见过的人都死了",
                "SSS级": "传说级 / 通关后可挑战主神",
            },
            "rules_of_genre": {
                "限时": "通常24-72小时",
                "目标": "活下来 + 主线任务 + 隐藏任务",
                "禁忌": "不能透露主神空间存在",
                "回归": "完成 → 强制返回空间",
            },
            "activation_keywords": ["主神", "空间", "试炼", "电影世界", "永久死亡", "积分"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("无限恐怖原型")],
        tags=["无限流", "世界观", "主神", "试炼"],
    ),

    # ═══════════ CHARACTER ARCHETYPES（emerging spaces）═══════════
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-broken-mirror-protag",
        name="角色原型：破镜主角（Broken Mirror Protagonist）",
        narrative_summary="原型：曾经辉煌但被现实打碎的主角 / 一段过去让他从神坛跌落 / 现在以残缺的状态前行；"
                          "比纯粹的草根更有故事 / 比纯天才更接近读者；典型如『退役之王』『失败的天才』。",
        content_json={
            "core_traits": {
                "曾经": "辉煌 / 顶级 / 公认的天才或英雄",
                "破碎事件": "一次重大失败/背叛/失去 / 改变一切",
                "现在": "失语 / 隐居 / 自我放逐 / 拒绝承认",
                "潜伏的火": "底色仍是天才 / 只是被覆盖",
            },
            "archetypes_subcategory": {
                "退役之王": "电竞/武术/NBA曾经的王者 / 因伤离开",
                "失败的天才": "天才之路被折断 / 转入平凡",
                "陨落的英雄": "曾救世 / 被反噬 / 被放逐",
                "归田的将军": "战功赫赫 / 卸甲归田",
                "封笔的作家": "作品被毁 / 不再创作",
                "下野的学者": "学术造假被揭 / 从此沉默",
            },
            "psychological_layers": {
                "自我怀疑": "我还能行吗",
                "拒绝复出": "怕再次失败",
                "暗中关注": "看到圈内动态会心痛",
                "突破口": "某个事件 / 某个人 / 让他重新拾起",
            },
            "narrative_arc": {
                "前期": "颓废 / 与世界格格不入 / 拒绝召唤",
                "触发": "意外卷入 / 必须出手 / 被人激怒",
                "中期": "勉强复出 / 找回手感 / 但仍有阴影",
                "高潮": "面对当年阴影 / 选择不再逃避",
                "结尾": "重塑自我 / 不一定回到巅峰 / 但更完整",
            },
            "examples": "John Wick / Ip Man（叶问） / 灵笼里的退役军人 / 飞驰人生的张驰",
            "tropes_avoid": "禁止『复出立刻又是顶尖』 / 必须有真挫败和真复健",
            "activation_keywords": ["破镜", "退役", "陨落", "复出", "天才", "阴影"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("英雄之旅", "Joseph Campbell角色框架"), wiki("复出叙事", "电影常见原型")],
        tags=["原型", "通用", "破镜", "复出"],
    ),

    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-pragmatic-villain",
        name="角色原型：务实型反派（Pragmatic Villain）",
        narrative_summary="原型：不为狂喜或仇恨而做恶 / 只为利益、效率、生存做事 / 冷血理性 / "
                          "出现『他可能是对的』的瞬间 / 是最让读者深思的反派类型。",
        content_json={
            "core_traits": {
                "理性": "情绪极少 / 决策基于成本收益",
                "效率": "最短路径达成目标",
                "底线": "有 / 但与主角的不同 / 自己内心一致",
                "礼貌": "对手下/敌人都礼貌 / 让人不寒而栗",
                "可怕之处": "不仇恨主角 / 主角只是『阻碍』",
            },
            "subcategory": {
                "企业家反派": "为利润不择手段",
                "政客型": "为权力 / 一切手段合理",
                "军人型": "执行任务 / 不问对错",
                "学者型": "为知识/真相 / 牺牲他人也无所谓",
                "改革者型": "为大局 / 牺牲小我",
            },
            "psychological_logic": {
                "他的世界观": "效率/规则/秩序 > 情感",
                "对主角": "不是仇恨 / 只是必须铲除",
                "对底层": "可怜 / 但不会停下手",
                "自我认知": "我是必要之恶 / 没有我世界更糟",
            },
            "narrative_function": {
                "提出问题": "他的逻辑是否完全错？",
                "推动思考": "主角的浪漫主义是否幼稚？",
                "结局选择": "可能投降 / 可能自我牺牲 / 可能隐退",
                "和解可能": "主角可能理解他 / 但仍要阻止",
            },
            "vs_other_villain_types": {
                "vs 复仇型": "他不仇恨 / 只是执行",
                "vs 疯狂型": "他理智 / 不享受痛苦",
                "vs 黑暗渴望型": "他不渴望权力本身 / 权力是工具",
            },
            "examples": "Thanos / Walter White / Boyd Crowder / 无问西东里的某些角色",
            "tropes_avoid": "禁止『其实是为了爱』 / 不要把他洗白成好人 / 他的逻辑就是冷酷",
            "activation_keywords": ["务实反派", "理性", "效率", "Thanos", "Walter White", "必要之恶"],
        },
        source_type="llm_synth", confidence=0.88,
        source_citations=[wiki("复仇者联盟3", "Thanos原型"), wiki("绝命毒师", "Walter White分析")],
        tags=["原型", "通用", "反派", "务实"],
    ),

    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-trickster-mentor",
        name="角色原型：诡道导师（Trickster Mentor）",
        narrative_summary="原型：表面是混乱的酒鬼/疯子/流浪汉 / 实际是真正的高人 / 用『不教而教』方式启发主角；"
                          "颠覆传统师父形象 / 让人物更有趣 / 主角必须自己悟。",
        content_json={
            "core_traits": {
                "外表": "邋遢/疯癫/看似无用",
                "言行": "胡言乱语 / 但藏着至理",
                "教学": "不直说 / 让主角自己跌倒/爬起",
                "底线": "关键时刻必出手 / 但不肯承认",
                "情感": "对主角既严厉又溺爱 / 嘴硬心软",
            },
            "examples": {
                "中国古典": "济公 / 酒中仙 / 老顽童",
                "武侠": "周伯通 / 风清扬 / 老顽童",
                "日漫": "龟仙人（七龙珠）/ 自来也（火影）",
                "西方": "Yoda（早期）/ Mr. Miyagi / Dumbledore（部分时刻）",
                "现代": "扫地僧 / 隐世大师",
            },
            "psychological_layers": {
                "为何隐藏身份": "曾经有创伤 / 不愿承认师父之名",
                "为何选这徒弟": "看中某种特质 / 想看他超越自己",
                "为何不直说": "亲身体验比说教深",
                "深层情感": "可能曾失去过弟子 / 不敢再承诺",
            },
            "narrative_uses": {
                "笑料": "插科打诨 / 缓解紧张",
                "悬念": "他到底是谁",
                "成长": "迫使主角主动思考",
                "情感": "意外死亡可让主角彻底成长",
                "传承": "在最后揭示他真正的身份",
            },
            "vs_classic_mentor": {
                "正统导师": "言传身教 / 严肃认真",
                "诡道导师": "胡闹中教 / 你以为他在闹他在悟",
            },
            "tropes_avoid": "禁止『其实他是无敌的』 / 必须有真实弱点 / 不要变成万能挂",
            "activation_keywords": ["诡道导师", "Trickster", "济公", "周伯通", "Mr.Miyagi", "扫地僧"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("Trickster", "神话学诡道角色"), wiki("Mr. Miyagi", "电影导师原型")],
        tags=["原型", "通用", "导师", "诡道"],
    ),

    # ═══════════ THEMATIC MOTIFS（niche genre depth）═══════════
    MaterialEntry(
        dimension="thematic_motifs", genre="洪荒",
        slug="honghuang-motif-hongmeng-purple",
        name="洪荒主题意象：鸿蒙紫气",
        narrative_summary="洪荒文核心象征：鸿蒙紫气是鸿钧道祖讲道时凝聚的至宝 / 三朵入三清 / "
                          "象征『证道之机』『天命选择』『不可强求』。",
        content_json={
            "literal_meaning": "混沌初开时凝聚的至阴至阳之气 / 紫色 / 极稀有",
            "symbolic_layers": {
                "证道": "得到=有机会成圣",
                "天命": "鸿钧不分三清外的人 / 有资格者已被选",
                "不可强求": "争夺者无人成功 / 只有自然得到者得",
                "圣人之道": "三清得后皆与世无争",
            },
            "narrative_uses": {
                "出现场合": "鸿钧讲道 / 紫霄宫殿",
                "象征意义": "主角接近紫气 = 接近大道",
                "失之交臂": "主角错过紫气 = 命运转折",
                "拒绝紫气": "主角放弃紫气 = 走自己的路",
                "新生紫气": "主角自创新型紫气 = 开创新道",
            },
            "cultural_context": {
                "源流": "源自《老子》『紫气东来』",
                "道家意涵": "至高瑞气 / 与道相通",
                "修真借用": "修真小说常用",
            },
            "color_symbolism": {
                "紫": "中国文化中至高 / 帝王之色 / 神秘",
                "金": "佛家用 / 至高佛性",
                "青": "道家清虚",
                "白": "纯净本源",
            },
            "scenes_to_use": [
                "主角第一次见鸿钧讲道",
                "三清得紫气 / 主角围观 / 心生道",
                "失败者注视紫气消散",
                "主角晚年留下一缕紫气供后人参悟",
            ],
            "activation_keywords": ["鸿蒙紫气", "紫气东来", "证道", "鸿钧", "三清"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("紫气东来", "道家典故"), wiki("封神演义", "紫气意象")],
        tags=["主题意象", "洪荒", "紫气", "证道"],
    ),

    MaterialEntry(
        dimension="thematic_motifs", genre="末世",
        slug="moshi-motif-skyline-dust",
        name="末世主题意象：废墟天际线",
        narrative_summary="末世文最核心视觉：曾经辉煌的城市天际线 / 现在塌陷 / 钢铁骸骨耸立 / 烟尘弥漫 / "
                          "象征『文明的失败』『秩序消逝』『人类的脆弱』。",
        content_json={
            "literal_meaning": "末日后高楼倒塌 / 远处仍可见摩天楼骸骨 / 天空灰红",
            "symbolic_layers": {
                "文明失败": "曾经的繁华瞬间崩塌",
                "秩序消逝": "法律/规则/价值都失效",
                "人类傲慢": "我们以为能控制 / 其实不能",
                "怀旧": "对失去的世界的哀悼",
                "希望": "在废墟中重建 / 文明会延续",
            },
            "narrative_uses": {
                "开场": "主角站在天台看废墟天际线 / 揭示末日已来",
                "回忆": "想起末日前的繁华夜景 / 对比",
                "决策": "面对城市废墟思考是否还能重建",
                "对决": "在标志性建筑废墟前决战",
                "结尾": "新文明从废墟中崛起",
            },
            "subcategories": {
                "丧尸末日": "城市空无一人 + 大量丧尸",
                "核战末日": "一切熔化 / 辐射风沙",
                "天灾末日": "海啸/地震 / 城市半淹半埋",
                "外星入侵": "外星建筑改造城市",
                "病毒爆发": "看似完整 / 但人类绝迹",
            },
            "famous_scenes": {
                "我是传奇": "纽约空城 / 鹿群奔过",
                "进击的巨人": "巨大城墙后崩塌",
                "活死人黎明": "购物中心末日",
                "辐射": "废土天际线",
            },
            "color_palette": {
                "灰红": "灰尘 + 残阳",
                "灰蓝": "冷淡 + 雾霾",
                "土黄": "沙尘暴 + 废土",
                "铁锈": "工业废墟",
            },
            "scenes_to_use": [
                "主角第一次离开避难所 / 看到城市废墟",
                "黄昏末世 / 远处烟尘 + 鸦群",
                "雪后废墟 / 寂静 / 唯有风声",
                "废墟中找到亲人遗物",
                "在标志性建筑（鸟巢/上海中心）顶端俯瞰",
            ],
            "activation_keywords": ["废墟", "天际线", "文明失败", "末世", "怀旧"],
        },
        source_type="llm_synth", confidence=0.88,
        source_citations=[wiki("我是传奇", "末世美学经典"), wiki("辐射系列", "废土游戏美学")],
        tags=["主题意象", "末世", "废墟", "怀旧"],
    ),

    MaterialEntry(
        dimension="thematic_motifs", genre="赛博朋克",
        slug="cyberpunk-motif-neon-rain",
        name="赛博朋克主题意象：霓虹雨夜",
        narrative_summary="赛博朋克美学的标志：永远的雨夜 / 霓虹招牌闪烁 / 玻璃反射光斑 / "
                          "象征『高科技与低生活』『被技术吞噬的人性』『冷漠的城市』。",
        content_json={
            "literal_meaning": "高密度都市的雨夜 / 各色霓虹灯映在湿漉漉的街道 / 全息广告遮天蔽日",
            "symbolic_layers": {
                "高科技低生活": "技术先进 / 人却挣扎",
                "技术吞噬人性": "人们机械化 / 失去本真",
                "冷漠都市": "千万人擦肩 / 无人相识",
                "永恒夜晚": "未来没有白昼 / 永处暧昧",
                "雨水净化": "雨在洗刷罪与污 / 但永远洗不净",
            },
            "narrative_uses": {
                "开场建立气氛": "主角走过霓虹雨夜 / 镜头慢推",
                "心理对照": "主角孤独 + 喧闹的霓虹 / 内外反差",
                "决斗场景": "雨中刀光 / 霓虹反光 / 像舞蹈",
                "邂逅": "在霓虹下与神秘人擦肩 / 命运转折",
                "结尾": "雨停 / 黎明微现 / 新的开始",
            },
            "color_palette": {
                "蓝紫粉": "霓虹基调",
                "黑色": "雨夜街道",
                "金色": "灯光反射",
                "红色": "招牌+血",
            },
            "famous_scenes": {
                "银翼杀手": "霓虹+雨+UNI日本广告",
                "攻壳机动队": "霓虹+水浮城",
                "Cyberpunk 2077": "夜之城霓虹街",
                "黑客帝国": "霓虹码雨",
            },
            "supporting_motifs": {
                "全息广告": "巨型动态广告 / 冷漠商业",
                "肉体改造": "义肢闪光 / 人体边界",
                "泛光路面": "雨水反射霓虹 / 视觉碎片",
                "黑伞下行人": "孤独的剪影",
                "屋顶VS地下": "贫富两层世界",
            },
            "scenes_to_use": [
                "主角第一次进入下层贫民区 / 霓虹雨夜",
                "雨中追逐 / 霓虹光斑闪过",
                "情感戏 / 撑伞站在霓虹下 / 沉默",
                "高潮决斗 / 雨夜屋顶 / 城市灯光为背景",
                "结尾 / 雨停 / 主角看晨曦",
            ],
            "activation_keywords": ["霓虹", "雨夜", "赛博朋克", "全息广告", "夜之城", "孤独"],
        },
        source_type="llm_synth", confidence=0.90,
        source_citations=[wiki("银翼杀手", "赛博朋克美学奠基"), wiki("攻壳机动队", "日式赛博朋克"), wiki("Blade Runner 2049", "赛博朋克现代")],
        tags=["主题意象", "赛博朋克", "霓虹", "雨夜"],
    ),
]


async def main() -> None:
    inserted = 0
    errors: list[tuple[str, str]] = []
    by_genre: dict[str, int] = {}
    by_dim: dict[str, int] = {}

    print(f"Seeding {len(ENTRIES)} entries to material_library (batch 55)...")
    async with session_scope() as session:
        for entry in ENTRIES:
            try:
                await insert_entry(session, entry, compute_embedding=True)
                inserted += 1
                key = entry.genre or "(通用)"
                by_genre[key] = by_genre.get(key, 0) + 1
                by_dim[entry.dimension] = by_dim.get(entry.dimension, 0) + 1
            except Exception as e:
                errors.append((entry.slug, str(e)))

    print(f"\nBy genre: {dict(sorted(by_genre.items(), key=lambda x: -x[1]))}")
    print(f"By dimension: {dict(sorted(by_dim.items(), key=lambda x: -x[1]))}")
    print(f"\n✓ {inserted} inserted/updated ({len(errors)} errors)")
    for slug, err in errors:
        print(f"  ✗ {slug}: {err}")


if __name__ == "__main__":
    asyncio.run(main())
