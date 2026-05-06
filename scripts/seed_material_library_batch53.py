"""
Batch 53: locale_templates for niche genres + emotion_arcs depth + dialogue_styles for thin areas.
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
    # ═══════════ LOCALE TEMPLATES ═══════════
    MaterialEntry(
        dimension="locale_templates", genre="洪荒",
        slug="honghuang-loc-zixiao-palace",
        name="洪荒地点：紫霄宫（讲道圣地）",
        narrative_summary="洪荒第一圣地：鸿钧道祖讲道之所/混沌之中的一座道宫/三清接引准提皆在此听道；"
                          "出入紫霄宫=接近天道意志/讲道时间凝固/弟子坐论坐次决定未来地位。",
        content_json={
            "geography": "混沌之外的至高秩序中心 / 不属任何位面 / 紫色云气环绕",
            "physical_description": {
                "外观": "六根紫玉柱撑起穹顶 / 紫色云霞 / 混沌不到",
                "蒲团": "六个圣人位 / 三千听道客位",
                "讲台": "鸿钧道祖端坐 / 太极图悬空",
                "光照": "紫霞自生 / 不分昼夜",
            },
            "historic_events": {
                "第一次讲道": "鸿钧分气运 / 三清得鸿蒙紫气",
                "第二次讲道": "二度证道 / 西方教接引准提取得圣位",
                "第三次讲道": "封神大战前夕 / 鸿钧惩通天",
            },
            "spatial_significance": {
                "位次": "前三排 = 圣人 / 中间三排 = 准圣 / 后排 = 大罗",
                "蒲团之争": "听道占座=未来地位",
                "时间": "讲道时间凝固 / 一日相当于外界千年",
            },
            "narrative_uses": {
                "出场场合": "封神大战决议 / 鸿钧训诫 / 三清议事",
                "意识形态": "代表至高规则 / 来此=接受『天道』",
                "禁忌": "不可斗法 / 不可恶语 / 违者直接沦为蒲团",
            },
            "activation_keywords": ["紫霄宫", "鸿钧", "讲道", "蒲团", "混沌", "三清"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("封神演义", "紫霄宫意象"), llm_note("洪荒圣地")],
        tags=["洪荒", "地点", "圣地", "紫霄宫"],
    ),

    MaterialEntry(
        dimension="locale_templates", genre="女尊",
        slug="nuzun-loc-fenghua-court",
        name="女尊地点：凤华殿（女皇议政）",
        narrative_summary="女尊朝代核心权力舞台：女皇接见臣下与各国使臣的正殿/凤纹金顶/朝堂女子戎装/"
                          "唯一允许男子涉足处是『皇夫朝拜区』；典型场景包括早朝、亲蚕礼、册封大典。",
        content_json={
            "architecture": {
                "外形": "黄琉璃瓦 + 凤纹金顶 + 三层石阶",
                "正殿": "女皇龙椅居中 / 朱红龙凤抱柱",
                "朝臣站位": "女官左右两班 / 左文右武",
                "皇夫区": "侧殿独立 / 仅册封时进入",
                "暗道": "通往养心殿后宫 / 仅女皇知晓",
            },
            "court_protocol": {
                "早朝": "卯时三刻 / 女官朝服 / 皇夫不参与",
                "亲蚕礼": "春日女皇带女眷亲手采桑 / 象征农耕母权",
                "册封大典": "女皇赐封土地兵权 / 受封女将率部下叩首",
                "选夫宴": "女皇为爱女选婿 / 男子在偏殿展示才艺",
            },
            "key_events_likely": {
                "刺杀": "敌国细作伪装宫女 / 早朝行刺",
                "夺权": "皇夫与女将合谋 / 借早朝发难",
                "情侵": "女皇看上臣下夫君 / 召入凤华殿",
                "改朝换代": "新女皇登基 / 凤华殿易主",
            },
            "atmosphere": {
                "白天": "阳光从凤眼透入 / 金光满殿 / 庄严威压",
                "夜晚": "烛火摇曳 / 凤纹影动 / 阴谋滋生",
                "雨天": "屋檐凤吻吐水 / 隐喻女主之泪",
            },
            "activation_keywords": ["凤华殿", "女皇", "早朝", "皇夫", "亲蚕", "册封"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[llm_note("女尊朝堂建筑")],
        tags=["女尊", "地点", "宫殿", "朝堂"],
    ),

    MaterialEntry(
        dimension="locale_templates", genre="萌宠",
        slug="mengchong-loc-pet-cafe",
        name="萌宠地点：契约兽咖啡馆",
        narrative_summary="现代都市萌宠文标志地标：宠物咖啡馆 / 大学校园式驯兽广场 / 古风兽园温泉 / "
                          "这些场所是契约兽与人类社交、治愈、产生情感的关键场景空间。",
        content_json={
            "modern_pet_cafe": {
                "外观": "原木风+玻璃幕墙 / 招牌画着主人爱兽",
                "内部": "高低分区 / 鸟禽笼 / 海洋池 / 软沙猫窝",
                "特色饮料": "桃心拉花 / 兽形棉花糖 / 主人专属调饮",
                "客人": "佛系上班族 / 治愈控小学生 / 来谈业务的驭兽师",
                "工作人员": "店长是退役驯兽师 / 兼职大学生兽医实习",
                "事件触发点": "稀有客人带来神秘小兽 / 偷兽贼摸进 / 暧昧偶遇",
            },
            "campus_arena": {
                "外观": "椭圆形露天场 / 看台围一圈",
                "内部分区": "训练场 / 比赛场 / 治疗区 / 看台",
                "氛围": "白天人声鼎沸 / 夜晚月光寂静",
                "传说": "校园四大谜团之一发生于此",
            },
            "ancient_zoo_hot_spring": {
                "外观": "山间温泉 / 周围围以竹篱 / 兽群自由出入",
                "内部": "粉色温泉花瓣 / 银鱼游动 / 灵兽栖息",
                "传说": "曾是上古驭兽师隐居处 / 残留远古血脉",
                "事件": "突破契约层级 / 打破伪装相认",
            },
            "activation_keywords": ["宠物咖啡馆", "驯兽广场", "兽园温泉", "契约兽", "校园"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[llm_note("萌宠文场景")],
        tags=["萌宠", "地点", "咖啡馆", "校园"],
    ),

    MaterialEntry(
        dimension="locale_templates", genre="快穿",
        slug="kuaichuan-loc-system-space",
        name="快穿地点：系统空间（休息站）",
        narrative_summary="宿主跨世界之间的中转站：白色无尽空间 / 悬浮全息屏 / 系统化身在此显形 / "
                          "宿主可休整、消费积分、查看任务详情；高阶宿主可在此邂逅其他宿主。",
        content_json={
            "physical_description": {
                "颜色": "纯白无尽 / 偶尔泛蓝色光晕",
                "天花板": "无限高 / 像被云气盖住",
                "地面": "光滑似镜 / 走过无声无影",
                "陈设": "悬浮的全息任务屏 / 数据流瀑布 / 中央服务台",
                "门": "现实之门 / 任务之门 / 兑换之门 / 资料库之门",
            },
            "system_avatar": {
                "外形": "可选萌系幼女 / 性冷淡管家 / 二次元御姐",
                "态度": "毒舌或温柔 / 由宿主等级决定",
                "权限": "只能在此处显形",
            },
            "exchange_market": {
                "等级": "F级到S级商品 / 道具/技能/素材/血脉",
                "限购": "新手禁止购买SSS级",
                "彩蛋商品": "仅特定时间出现 / 抢购",
            },
            "lounge_area": {
                "休息舱": "类似太空舱 / 模拟睡眠",
                "训练房": "可模拟任务世界 / 演练战术",
                "宿主大厅": "高阶宿主才能进入 / 像茶馆一样可交流",
            },
            "narrative_uses": {
                "心理疗愈": "宿主在惨痛任务后回此处疗伤",
                "信息节点": "在此揭示主线伏笔",
                "邂逅伏笔": "和『他』在此相遇 / 跨世界恋人节点",
            },
            "activation_keywords": ["系统空间", "休息站", "全息屏", "兑换", "宿主大厅"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[llm_note("快穿元世界场景")],
        tags=["快穿", "地点", "系统", "中转站"],
    ),

    MaterialEntry(
        dimension="locale_templates", genre="游戏",
        slug="game-loc-final-boss-room",
        name="游戏地点：终极BOSS房（红龙女王厅）",
        narrative_summary="MMO游戏经典终极副本场景：广阔的环形战厅 / 中央是BOSS / 四周地形会变化 / "
                          "天花板高耸到看不见 / 多阶段战斗 / 是公会首杀的兵家必争之地。",
        content_json={
            "physical_description": {
                "形状": "椭圆 + 中央升降台 / 直径百米",
                "天花板": "高耸入云 / 红龙可飞起",
                "地面": "玄铁地板 + 火焰岩浆裂缝",
                "光照": "红色火光 / 战斗中拌闪电",
                "出口": "战斗中关闭 / 通关后开启传送门",
            },
            "boss_phases": {
                "阶段1（100%-70%）": "正常战斗 / BOSS用基础技能",
                "阶段2（70%-40%）": "召唤小怪 / 地面变熔岩",
                "阶段3（40%-15%）": "BOSS狂化 / 全屏AOE",
                "阶段4（15%-0%）": "本源觉醒 / 必须打断关键技 / 否则团灭",
            },
            "mechanics": {
                "击杀必须": "DPS轮换+治疗群疗+T仇恨稳定",
                "陷阱": "倒计时炸弹 / 火焰墙 / 暗影陷阱",
                "彩蛋": "BOSS特定血量讲述背景故事",
            },
            "rewards": {
                "首杀": "全服公告 + 头衔 + 隐藏装备",
                "金币": "百万级",
                "稀有掉落": "传说装备 / 坐骑蛋 / 超稀有图纸",
                "成就": "屠龙者 / 终结者 / 时间纪录持有",
            },
            "narrative_uses": {
                "对决": "情感死敌战在此达到高潮",
                "陨落": "公会首席陨落于此 / 改写战队史",
                "重生": "玩家因首杀晋升首席",
                "彩蛋": "BOSS讲述古代背景 / 揭示主线",
            },
            "activation_keywords": ["BOSS房", "终极副本", "红龙", "首杀", "团灭", "AOE"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("魔兽世界", "副本BOSS战体验")],
        tags=["游戏", "地点", "BOSS房", "副本"],
    ),

    MaterialEntry(
        dimension="locale_templates", genre="美食",
        slug="food-loc-iron-chef-arena",
        name="美食地点：铁人料理对决厅",
        narrative_summary="日式经典料理对决场景：高悬幕布 / 中央两座工作台 / 围观席环绕 / "
                          "评审席居高临下 / 镜头无处不在 / 这是料理流走向高潮的标志地点。",
        content_json={
            "physical_description": {
                "形状": "圆形大厅 / 直径30米 / 高8米",
                "天花板": "高挂日式纸灯笼 / 飘下樱花雨",
                "工作台": "两座中央对峙 / 各占一半区域",
                "评审席": "升起6米 / 五位评审俯视",
                "围观区": "三层看台 / 美食家与同行混坐",
                "摄像机": "六个机位 / 包括俯拍/特写/慢动作",
            },
            "battle_format": {
                "时间": "60分钟限时 / 倒计时大屏幕",
                "食材": "中央食材库 / 双方共用 / 限时取材",
                "评审标准": "味/形/意/创新 四轴各25分",
                "盲品": "评审看不到选手 / 仅凭味道",
            },
            "atmosphere": {
                "开场": "解说员激情陈述对手历史宿仇",
                "中段": "镜头切换 / 紧张配乐 / 关键调味时镜头特写",
                "结尾": "选手摆盘 / 评审举牌 / 胜负宣布",
                "尾声": "胜者哭泣 / 败者向前辈致敬",
            },
            "narrative_uses": {
                "宿仇对决": "主角对师兄/曾经搭档",
                "传承之争": "新派vs传统",
                "国际赛场": "中vs日vs法 / 民族荣光",
                "情感羁绊": "评审中有主角恩师 / 不能徇私",
                "反转": "一道家常菜击败精雕细琢",
            },
            "activation_keywords": ["铁人料理", "对决厅", "评审", "盲品", "盘式", "倒计时"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("料理铁人", "日本经典节目"), wiki("食戟之灵", "现代料理对决")],
        tags=["美食", "地点", "对决", "料理"],
    ),

    # ═══════════ EMOTION ARCS（深化）═══════════
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="emo-arc-rivalry-to-respect",
        name="情感弧线：宿敌→相互理解→并肩",
        narrative_summary="经典『宿敌→并肩』情感弧线：开始视对方为最大威胁/产生不可避免的对抗→"
                          "在对抗中渐渐了解对方的痛/发现彼此的相似→最后并肩对抗更大的敌人。",
        content_json={
            "stage1_hostility": {
                "情绪": "敌意 / 看不起 / 轻视 / 厌恶",
                "事件": "正面对抗 / 互相揭短 / 公开较劲",
                "心理": "我必须打败他 / 他不配 / 我才是对的",
                "外显": "言语相讥 / 暗中使绊 / 公开宣战",
            },
            "stage2_curiosity": {
                "情绪": "好奇 / 困惑 / 一丝惊讶",
                "事件": "意外发现对方做了一件不像他的事",
                "心理": "他为什么这么做？我误解了？",
                "外显": "停手观察 / 私下打听 / 不再急着出手",
            },
            "stage3_understanding": {
                "情绪": "怜悯 / 共鸣 / 内疚",
                "事件": "目睹对方的痛苦根源 / 倒下时拉了一把",
                "心理": "原来他和我一样 / 我曾误解他 / 我有点欠他",
                "外显": "不再攻击 / 私下关注 / 偶尔提醒",
            },
            "stage4_alliance": {
                "情绪": "信任 / 默契 / 战友情",
                "事件": "面对更强敌人 / 必须背靠背",
                "心理": "他是唯一能懂我的 / 没有他我做不到",
                "外显": "并肩 / 默契配合 / 一个眼神就懂",
            },
            "narrative_arc_length": "通常占故事 60-80% / 是核心弧线",
            "key_turning_moment": "第三阶段的『目睹对方痛苦』瞬间是定调",
            "activation_keywords": ["宿敌", "理解", "并肩", "战友情", "对抗", "和解"],
        },
        source_type="llm_synth", confidence=0.88,
        source_citations=[wiki("瑟", "电影/小说宿敌发展典型"), llm_note("宿敌情感弧线")],
        tags=["通用", "情感弧线", "宿敌", "并肩"],
    ),

    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="emo-arc-pride-to-humility",
        name="情感弧线：傲慢→失败→谦卑",
        narrative_summary="天才主角的成长经典曲线：因天赋骄傲→以为自己无敌→遭遇毁灭性失败→"
                          "崩溃→看到比自己更强的人/更可贵的品质→真正的谦卑→更深的实力。",
        content_json={
            "stage1_arrogance": {
                "情绪": "自满 / 看不起庸人 / 嘲讽对手",
                "事件": "连续胜利 / 加冕 / 鲜花掌声",
                "心理": "我是最强的 / 别人不配挑战我",
                "外显": "傲慢言辞 / 拒绝合作 / 嘲讽他人",
            },
            "stage2_fall": {
                "情绪": "惊愕 / 怀疑 / 否认 / 绝望",
                "事件": "强敌出现 / 一招败北 / 全网嘲笑",
                "心理": "怎么可能 / 一定有原因 / 我应该可以更强",
                "外显": "失常 / 拒绝接受 / 暴怒 / 自闭",
            },
            "stage3_breakdown": {
                "情绪": "崩溃 / 自我怀疑 / 抑郁",
                "事件": "封闭自我 / 远离曾经的舞台",
                "心理": "也许我不是天才 / 我什么都不是",
                "外显": "酗酒 / 闭门 / 不再练习 / 连续失败",
            },
            "stage4_meeting_master": {
                "情绪": "震撼 / 反思 / 仰望",
                "事件": "遇到一个真正的高手 / 体会到差距",
                "心理": "原来这才叫强 / 我之前的傲慢可笑",
                "外显": "拜师 / 重新练习基本功 / 沉默",
            },
            "stage5_humility_strength": {
                "情绪": "踏实 / 谦逊 / 平和 / 内心强大",
                "事件": "重出江湖 / 不再急于证明 / 默默精进",
                "心理": "胜负都不重要 / 道才重要",
                "外显": "礼让对手 / 鼓励后辈 / 修养内在",
            },
            "activation_keywords": ["傲慢", "失败", "崩溃", "拜师", "谦卑", "成长"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("食戟之灵", "天才主角成长弧线"), llm_note("傲慢→谦卑情感曲线")],
        tags=["通用", "情感弧线", "成长", "谦卑"],
    ),

    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="emo-arc-revenge-to-redemption",
        name="情感弧线：复仇执念→放下→救赎",
        narrative_summary="复仇者的灵魂赎回曲线：因仇恨活着→以仇恨为唯一动力→在追杀仇人过程中变得越来越像仇人→"
                          "目睹自己的恶→放下→寻求救赎而非复仇→变得比之前更强（精神层面）。",
        content_json={
            "stage1_obsession": {
                "情绪": "仇恨 / 燃烧 / 偏执",
                "事件": "至亲被害 / 立誓复仇 / 苦练多年",
                "心理": "活着只为他死 / 没有他的死我就不能眠",
                "外显": "拒绝爱 / 拒绝朋友 / 全身心练武 / 冷酷",
            },
            "stage2_becoming_what_you_hate": {
                "情绪": "麻木 / 漠然 / 暗黑",
                "事件": "为复仇手段越来越狠 / 杀无辜 / 利用他人",
                "心理": "为达目的不择手段 / 这是必须的",
                "外显": "面色冷峻 / 不笑 / 看人如看物 / 朋友离开",
            },
            "stage3_mirror_moment": {
                "情绪": "震惊 / 自厌 / 哀痛",
                "事件": "做出某事后发现自己像极了仇人 / 或被无辜者畏惧",
                "心理": "我成了我恨的人 / 我的复仇没有意义",
                "外显": "停下脚步 / 自我审视 / 失声痛哭",
            },
            "stage4_letting_go": {
                "情绪": "释怀 / 悲悯 / 平和",
                "事件": "面对仇人时选择不杀 / 转身离开",
                "心理": "他的死不能让母亲复活 / 我要替母亲活着",
                "外显": "饶过仇人 / 拥抱朋友 / 重新笑",
            },
            "stage5_redemption": {
                "情绪": "释然 / 安宁 / 灵魂自由",
                "事件": "回到母亲坟前道别 / 救赎当年被自己伤害的人",
                "心理": "我替母亲过好的人生 / 才是最好的复仇",
                "外显": "恢复温暖 / 帮助他人 / 真正强大",
            },
            "key_turning_point": "stage3的『镜像时刻』",
            "activation_keywords": ["复仇", "仇恨", "镜像", "放下", "救赎", "灵魂自由"],
        },
        source_type="llm_synth", confidence=0.88,
        source_citations=[wiki("基督山伯爵", "复仇与救赎"), wiki("赵氏孤儿", "中国复仇悲剧")],
        tags=["通用", "情感弧线", "复仇", "救赎"],
    ),

    # ═══════════ DIALOGUE STYLES ═══════════
    MaterialEntry(
        dimension="dialogue_styles", genre="洪荒",
        slug="honghuang-ds-saint-speech",
        name="洪荒：圣人/准圣对话风格",
        narrative_summary="洪荒流圣人级别人物对话特征：极简短/带因果意味/常以比喻或寓言而非直说/"
                          "动辄『天数』『道』『缘法』；与凡人对话时层次落差极大。",
        content_json={
            "saint_pattern": {
                "字数": "极少 / 三五字解决",
                "重量": "一句话即定生死或论道",
                "概念": "天数 / 道果 / 因果 / 量劫 / 气运",
                "对凡人": "近乎不语 / 偶尔一句『有缘』",
                "争论": "讲道理而非吵架 / 引经据典",
            },
            "quasi_saint_pattern": {
                "字数": "适中 / 偶尔慷慨陈词",
                "情感": "可以有 / 但不外显",
                "对待师门": "敬而不亲 / 用谦称『师弟』『师妹』",
            },
            "common_phrases": {
                "圣人": "「贫道有理」「此乃道意」「天数注定」「机缘已尽」",
                "准圣": "「师妹莫怪」「我观此子有缘」「应天数所引」「不在因果」",
                "对凡人": "「你与我有缘」「贫道收你为徒」「机缘到了」",
            },
            "tone_carriers": {
                "字外之意": "话不说尽 / 留白让对方悟",
                "拒绝时": "不直接拒绝 / 用『缘分未到』推托",
                "决定时": "一字定 / 「行」「不」「等」",
            },
            "anti_modern_oral": "禁用『没问题』『搞定』『放心』 / 全部用古意",
            "activation_keywords": ["道友", "贫道", "缘法", "天数", "因果", "圣人"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("封神演义", "圣人台词参考"), llm_note("洪荒对话")],
        tags=["洪荒", "对话", "圣人", "古风"],
    ),

    MaterialEntry(
        dimension="dialogue_styles", genre="女尊",
        slug="nuzun-ds-female-emperor-speech",
        name="女尊：女皇/朝臣对话风格",
        narrative_summary="女尊文核心对话：女子掌权/语气霸气/男子温柔；女皇训诫如帝王/朝臣女官如名将；"
                          "男子嫁人后对妻语气带依附；这种『反向』语气是女尊文的核心识别。",
        content_json={
            "female_emperor_pattern": {
                "对朝臣": "「卿家平身」「准奏」「下旨即可」",
                "对皇夫": "温柔但带主动 / 「夫君安好」「朕来看你」",
                "对仇敌": "霸气至极 / 「朕给你三日」「卿可知罪」",
                "私下": "可以撒娇 / 偶尔露出『我也累』",
            },
            "female_minister_pattern": {
                "朝堂": "「臣以为」「臣斗胆」「请陛下圣裁」",
                "对下属": "比男性官员更直接 / 不绕弯子",
                "对夫": "温柔但带保护 / 『家中你莫操劳』",
                "对子女": "兼具母性与教导 / 比父亲更严",
            },
            "male_consort_pattern": {
                "对妻": "「妻主」「夫人」「我等妻主回来」",
                "撒娇": "「妻主又这般欺负妾身」",
                "委屈": "用眼泪/不会用言语顶撞",
                "决断时": "极少决断 / 通常是『请妻主定夺』",
            },
            "vocab_inversion": {
                "称谓": "妾身（男子自称）/ 妻主 / 君上 / 内人（指丈夫）",
                "动作": "嫁人 / 守身 / 三妻四夫 / 纳男为妾",
                "美德": "温柔 / 体贴 / 善良 / 守贞",
            },
            "anti_pattern": "禁用现代『老公』『宝贝』 / 用古风『夫君』『内子』",
            "activation_keywords": ["朕", "妻主", "妾身", "君上", "嫁夫", "夫君"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[llm_note("女尊文反转语境")],
        tags=["女尊", "对话", "女皇", "性别反转"],
    ),

    MaterialEntry(
        dimension="dialogue_styles", genre="美食",
        slug="food-ds-chef-jargon",
        name="美食：厨师圈对话风格",
        narrative_summary="美食流核心对话：厨师之间用厨房黑话/烹饪术语/料理感悟；这种行业内对话是美食文的『行业感』来源；"
                          "包含技术黑话/前辈训徒/盘式美学讨论/品尝评语等多种亚类。",
        content_json={
            "kitchen_slang": {
                "上单了": "客单进来",
                "出菜": "菜品准备好",
                "走菜": "服务员来取",
                "落单": "客人点菜结束",
                "呼锅": "让锅热起来",
                "焯水": "热水汆一下",
                "勾芡": "用淀粉勾稠",
                "泡水": "凉水浸",
                "改刀": "重切",
                "醒面": "让面松弛",
            },
            "master_to_apprentice": {
                "训诫": "「火候到了吗」「试过盐了吗」「你的刀工废了」",
                "鼓励": "「这道有点意思」「再试三次」「你比我那时强」",
                "传承": "「记住，料理是给客人吃的，不是给自己看的」",
                "悟道": "「料理之道无止境」「我做了五十年，仍在学」",
            },
            "tasting_evaluation": {
                "肯定": "「鲜」「醇」「回味甘」「层次分明」「火候在",
                "否定": "「淡了」「腻了」「火大」「调味不准」「形丑」",
                "高级评语": "「有故事」「带温度」「有作者」「能感动人」",
                "禁忌": "「饮料级别」「家庭水平」「外行能做」",
            },
            "battle_dialogue": {
                "宣战": "「我和你赌一道菜」「你的料理我必胜」",
                "中段": "「你这火候可以」「我的没你心思深」",
                "终局": "「我输了」「你赢得漂亮」「下次再来」",
            },
            "atmosphere": {
                "厨房": "繁忙、急促、命令式",
                "比赛": "紧张、竞争、内心独白多",
                "传授": "缓慢、用心、夹杂哲思",
                "评审": "冷静、专业、字字珠玑",
            },
            "activation_keywords": ["上单了", "走菜", "改刀", "勾芡", "醒面", "回味甘", "有故事"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("中華一番", "厨师圈对话参考"), wiki("食戟之灵", "现代料理对话")],
        tags=["美食", "对话", "厨师", "黑话"],
    ),
]


async def main() -> None:
    inserted = 0
    errors: list[tuple[str, str]] = []
    by_genre: dict[str, int] = {}
    by_dim: dict[str, int] = {}

    print(f"Seeding {len(ENTRIES)} entries to material_library (batch 53)...")
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
