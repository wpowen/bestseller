"""
Batch 25: World historical events / wars / revolutions / civilizations.
Activates concrete historical knowledge for period dramas, alt-history,
war narratives, civilization-fall stories.
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
    # 二战
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-hist-ww2",
        name="第二次世界大战（1939-1945）",
        narrative_summary="人类历史最大规模战争。轴心国（德意日）vs 同盟国（美英苏中法）。"
                          "死亡 7000 万 + 大屠杀 600 万犹太人 + 广岛长崎核爆 + 战后两极格局。"
                          "适用战争 / 谍战 / 抗战 / 历史 / 二战架空。",
        content_json={
            "core_phases": "1939 入侵波兰 → 1940 法国陷落 / 不列颠空战 → 1941 巴巴罗萨入苏 / 珍珠港 → 1942 中途岛 / 斯大林格勒 → 1943 库尔斯克 → 1944 诺曼底 → 1945 雅尔塔 / 柏林 / 广岛长崎",
            "key_battles": "敦刻尔克大撤退 / 中途岛海战 / 斯大林格勒巷战 / 库尔斯克坦克战 / 诺曼底登陆 / 硫磺岛 / 柏林战役",
            "asia_theater": "卢沟桥 1937 / 南京大屠杀 / 武汉会战 / 长沙四次会战 / 滇缅远征 / 苏军出兵东北 1945",
            "atrocities": "犹太人大屠杀 600 万（奥斯维辛 / 特雷布林卡）/ 南京大屠杀 30 万 / 731 部队人体实验 / 列宁格勒围城 900 天饿死 100 万",
            "weapons_tech": "坦克战（虎式 / T-34）/ 战斗机（Spitfire / Bf-109 / 零式）/ 航母决战（中途岛）/ V-1 V-2 火箭 / 核武器（曼哈顿计划）/ 雷达 / 密码战（恩尼格玛 / 图灵）",
            "famous_figures": "丘吉尔 / 罗斯福 / 斯大林 / 蒋介石 / 希特勒 / 墨索里尼 / 东条英机 / 隆美尔 / 巴顿 / 朱可夫 / 麦克阿瑟",
            "narrative_use": "正面战场（《拯救大兵瑞恩》）/ 后方间谍（《风声》）/ 大屠杀幸存者（《辛德勒名单》）/ 抗战剧 / 架空历史（如果德国胜利）",
            "activation_keywords": ["二战", "诺曼底", "斯大林格勒", "南京大屠杀", "奥斯维辛", "曼哈顿计划", "图灵", "丘吉尔", "希特勒"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("第二次世界大战", ""), llm_note("WW2 史")],
        tags=["历史", "二战", "通用"],
    ),
    # 一战
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-hist-ww1",
        name="第一次世界大战（1914-1918）",
        narrative_summary="工业化战争首演。萨拉热窝刺杀点燃 → 同盟国（德奥土）vs 协约国（英法俄美）。"
                          "战壕战 + 机枪 + 毒气 + 坦克首登场。死亡 1700 万。"
                          "战后凡尔赛和约 → 二战种子。适用近代史 / 谍战 / 战壕生死。",
        content_json={
            "trigger_alliance": "萨拉热窝刺杀斐迪南大公 / 德奥意三国同盟 vs 英法俄三国协约 / 多米诺式参战",
            "key_battles": "马恩河会战 / 凡尔登绞肉机（70 万死）/ 索姆河会战 / 加里波利惨败 / 日德兰海战 / 兴登堡防线",
            "trench_warfare": "西线 700 公里战壕 / 老鼠 / 战壕足 / 机枪扫射冲锋自杀式 / 炮击不间断 / 化学武器（芥子气 / 氯气）",
            "new_weapons": "马克沁机枪 / 毒气 / 坦克（首战索姆河）/ 飞艇 / 战斗机 / 潜艇（U 艇）/ 远程火炮（巴黎大炮）",
            "russian_revolution": "1917 二月革命推翻沙皇 / 十月革命布尔什维克夺权 / 列宁退出战争 / 《布列斯特和约》",
            "us_entry": "1917 德国无限制潜艇 + 齐默尔曼电报 → 美国参战 → 协约国反败为胜",
            "aftermath": "凡尔赛和约苛刻德国 / 奥匈解体 / 奥斯曼解体 / 苏俄诞生 / 国联成立失败 / 西班牙流感 5000 万死",
            "narrative_use": "近代史 / 战壕生死（《1917》）/ 谍战 / 贵族末日（《唐顿庄园》）/ 飞行员浪漫",
            "activation_keywords": ["一战", "凡尔登", "战壕", "索姆河", "凡尔赛和约", "齐默尔曼", "马克沁", "U 艇"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("第一次世界大战", ""), llm_note("WW1 史")],
        tags=["历史", "一战", "通用"],
    ),
    # 法国大革命
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-hist-french-revolution",
        name="法国大革命（1789-1799）",
        narrative_summary="人类政治史分水岭。攻陷巴士底狱 → 共和 → 雅各宾恐怖 → 热月政变 → 拿破仑。"
                          "口号自由 / 平等 / 博爱 / 人权宣言。断头台成时代符号。"
                          "适用历史 / 政治悬疑 / 阶级革命 / 双城记式爱情。",
        content_json={
            "phase_1_constitutional": "1789 攻陷巴士底狱 / 国民议会 / 人权宣言 / 路易十六还在 / 君主立宪",
            "phase_2_republic": "1792 推翻王政 / 路易十六上断头台 / 第一共和 / 吉伦特派 vs 雅各宾派",
            "phase_3_terror": "1793-1794 雅各宾恐怖 / 罗伯斯庇尔 / 公安委员会 / 数万人上断头台 / 革命吃自己的孩子（丹东 / 罗兰夫人）",
            "phase_4_thermidor": "1794 热月政变 / 罗伯斯庇尔上断头台 / 督政府腐败 / 1799 拿破仑雾月政变 / 革命终结",
            "key_figures": "路易十六 + 玛丽王后 / 罗伯斯庇尔 / 丹东 / 马拉（被刺）/ 米拉波 / 拿破仑 / 雅各宾领袖们",
            "ideologies": "卢梭《社会契约论》/ 孟德斯鸠三权分立 / 伏尔泰反教权 / 启蒙思想成革命弹药",
            "guillotine_symbol": "断头台 1792 启用 / 罗伯斯庇尔最后也被砍 / 玛丽王后临终轶事 / 平等死亡机器",
            "narrative_use": "历史小说（《双城记》《九三年》）/ 政治悬疑（间谍 / 革命委员会）/ 贵族末日 / 革命爱情",
            "activation_keywords": ["法国大革命", "巴士底狱", "断头台", "罗伯斯庇尔", "雅各宾", "人权宣言", "玛丽王后", "拿破仑", "热月"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("法国大革命", ""), llm_note("法国大革命史")],
        tags=["历史", "革命", "通用"],
    ),
    # 美国独立战争
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-hist-american-revolution",
        name="美国独立战争（1775-1783）",
        narrative_summary="北美十三殖民地反抗英国统治建立合众国。波士顿倾茶 → 列克星敦 → 独立宣言 → 萨拉托加 → 约克敦。"
                          "华盛顿、富兰克林、杰斐逊、汉密尔顿群星璀璨。",
        content_json={
            "trigger_grievances": "印花税法 / 茶叶税 / 没有代表权不纳税 / 波士顿屠杀 1770 / 波士顿倾茶 1773",
            "key_battles": "列克星敦与康科德 1775 第一枪 / 邦克山 / 特拉华河横渡 / 萨拉托加（转折）/ 约克敦（终战）",
            "founding_documents": "1776 独立宣言（杰斐逊主笔，七月四日）/ 邦联条例 / 1787 联邦宪法 / 权利法案",
            "founding_fathers": "华盛顿（总司令 + 首任总统）/ 富兰克林（外交 + 雷电实验）/ 杰斐逊（独立宣言）/ 汉密尔顿（金融）/ 亚当斯 / 麦迪逊（宪法之父）/ 杰伊",
            "key_themes": "代议制民主 / 三权分立 / 联邦制 / 权利法案 / 没有国王",
            "french_alliance": "萨拉托加后法国援助 / 拉法耶特 / 海军支援 / 这是法国大革命预热",
            "narrative_use": "建国神话 / 政治剧（《汉密尔顿》音乐剧）/ 海上战争 / 间谍故事 / 印第安战争",
            "activation_keywords": ["独立战争", "波士顿倾茶", "独立宣言", "华盛顿", "杰斐逊", "汉密尔顿", "约克敦", "权利法案"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("美国独立战争", ""), llm_note("美国建国")],
        tags=["历史", "革命", "通用"],
    ),
    # 罗马帝国
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-hist-roman-empire",
        name="罗马帝国兴亡",
        narrative_summary="西方文明源头。从公元前 753 罗慕路斯建城到 1453 君士坦丁堡陷落 = 2200 年。"
                          "共和 → 帝国 → 衰亡。凯撒 / 屋大维 / 君士坦丁 / 查士丁尼。"
                          "法律 / 道路 / 拉丁文 / 基督教化全部源此。",
        content_json={
            "phases": "王政时期 BC 753-509 → 共和国 BC 509-27 → 帝国 BC 27-AD 476（西部）/ AD 1453（东部拜占庭）",
            "republic_highlights": "元老院 + 执政官 + 平民会 / 布匿战争三场（汉尼拔翻越阿尔卑斯）/ 高卢征服（凯撒）/ 三巨头 / 凯撒被刺",
            "early_empire": "屋大维 = 奥古斯都 / 罗马和平 200 年 / 罗马极盛期 117 年图拉真 / 五贤帝时代 / 帝国版图至中东英伦北非",
            "crisis_3rd_century": "军人皇帝 50 年内 26 帝 / 通货膨胀 / 蛮族入侵开始 / 戴克里先四帝共治",
            "christianization": "313 米兰敕令君士坦丁皇帝合法化 / 380 国教 / 325 尼西亚会议 / 帝国基督教化",
            "fall_west": "395 帝国分裂东西 / 410 阿拉里克哥特人洗劫罗马 / 455 汪达尔人洗劫 / 476 西罗马灭亡",
            "byzantine_east": "395-1453 拜占庭帝国 / 查士丁尼法典 / 君士坦丁堡 / 圣索菲亚大教堂 / 1453 奥斯曼陷落",
            "lasting_legacy": "罗马法 / 拉丁字母 + 拉丁文 / 罗马道路（条条大路通罗马）/ 拱券与混凝土 / 共和制 + 元老院基因",
            "narrative_use": "历史小说（《我，克劳迪乌斯》）/ 角斗士 / 凯撒被刺政治悬疑 / 拜占庭宫廷 / 蛮族入侵史诗",
            "activation_keywords": ["罗马", "凯撒", "屋大维", "元老院", "君士坦丁", "拜占庭", "查士丁尼", "蛮族入侵", "拉丁"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("罗马帝国", ""), llm_note("罗马通史")],
        tags=["历史", "西方", "通用"],
    ),
    # 蒙古帝国
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-hist-mongol-empire",
        name="蒙古帝国（1206-1368）",
        narrative_summary="史上最大陆地帝国。成吉思汗统一草原 → 横扫欧亚 → 元朝中国 / 钦察汗国 / 伊尔汗国 / 察合台。"
                          "重塑欧亚地理 + 黑死病传播 + 火药西传。",
        content_json={
            "rise_genghis": "1206 铁木真统一草原称成吉思汗 / 革新万户 / 千户制 / 怯薛军 / 全民皆兵游牧战术",
            "expansion": "1219 西征花剌子模 / 1223 卡尔卡河败基辅罗斯 / 1227 灭西夏 / 1234 灭金 / 1241 莱格尼察 + 蒂萨河败欧洲联军 / 1258 攻陷巴格达",
            "four_khanates": "元朝（中国）/ 钦察汗国（俄罗斯草原）/ 伊尔汗国（波斯）/ 察合台汗国（中亚）",
            "mongol_warfare": "复合弓 / 草原弓骑 / 假撤退包围 / 心理战恐怖屠城 / 后勤马奶肉干 / 怯薛精锐",
            "yuan_china": "1271 忽必烈定国号大元 / 1279 灭南宋 / 大都（北京）/ 行省制度 / 四等人制 / 红巾军起义",
            "pax_mongolica": "蒙古和平 / 丝绸之路重启 / 马可波罗东游 / 黑死病 1346 经丝路传欧洲 / 火药西传",
            "decline": "继位之争分裂 / 中央汉化失草原本色 / 元末红巾军 / 1368 朱元璋灭元",
            "narrative_use": "金庸射雕（成吉思汗 + 郭靖）/ 历史架空 / 古装战争 / 文明碰撞",
            "activation_keywords": ["蒙古", "成吉思汗", "忽必烈", "怯薛", "四大汗国", "元朝", "钦察", "马可波罗"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("蒙古帝国", ""), llm_note("蒙古史")],
        tags=["历史", "蒙古", "通用"],
    ),
    # 文艺复兴
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-hist-renaissance",
        name="文艺复兴（14-17 世纪）",
        narrative_summary="欧洲从中世纪向近代过渡。源起佛罗伦萨 / 美第奇家族赞助。"
                          "复兴古希腊罗马 / 人文主义 / 透视画法 / 解剖学 / 科学革命前夜。"
                          "达芬奇 / 米开朗基罗 / 拉斐尔 / 但丁 / 莎士比亚。",
        content_json={
            "italian_birthplace": "佛罗伦萨（美第奇家族）/ 威尼斯共和国 / 罗马（教皇赞助）/ 米兰（斯福尔扎）",
            "high_renaissance_trio": "达芬奇（蒙娜丽莎 / 最后的晚餐 / 万能天才）/ 米开朗基罗（西斯廷天顶 / 大卫像 / 圣母怜子）/ 拉斐尔（雅典学院 / 圣母）",
            "literature": "但丁《神曲》/ 彼特拉克十四行诗 / 薄伽丘《十日谈》/ 莎士比亚（英国后期文艺复兴）/ 塞万提斯《堂吉诃德》",
            "humanism": "人本主义对抗神权 / 重新发现古希腊罗马典籍 / 拉丁文学复兴 / 人是万物的尺度",
            "science_precursor": "哥白尼日心说 / 伽利略望远镜 + 落体 / 维萨里解剖学 / 哈维血液循环 / 培根《新工具》",
            "key_inventions": "古登堡活字印刷术 1455 / 远洋航行 / 火炮普及 / 透视法（布鲁内莱斯基）/ 油画技法",
            "patronage_system": "美第奇 / 教皇 / 各诸侯 / 艺术家 = 工匠转知识分子身份",
            "darker_side": "宗教裁判所 / 焚布鲁诺 / 黑死病背景 / 宫廷阴谋 / 雇佣军 / 切萨雷波吉亚",
            "narrative_use": "历史悬疑（达芬奇密码）/ 艺术家传记 / 宫廷阴谋 / 美第奇家族剧",
            "activation_keywords": ["文艺复兴", "达芬奇", "米开朗基罗", "美第奇", "佛罗伦萨", "人文主义", "古登堡", "哥白尼"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("文艺复兴", ""), llm_note("文艺复兴史")],
        tags=["历史", "西方", "通用"],
    ),
    # 工业革命
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-hist-industrial-revolution",
        name="工业革命（1760-1840）",
        narrative_summary="人类生产力分水岭。蒸汽机 / 工厂 / 铁路 / 城市化 / 阶级分化。"
                          "源起英国曼彻斯特 / 蔓延欧美。"
                          "适用维多利亚时代背景 / 资本悲歌 / 蒸汽朋克。",
        content_json={
            "first_revolution": "1760-1840 / 蒸汽机（瓦特）/ 纺织机（珍妮 / 飞梭）/ 煤铁工业 / 铁路 / 蒸汽船 / 工厂制度",
            "second_revolution": "1870-1914 / 电力（爱迪生 / 特斯拉）/ 钢铁（贝塞麦炉）/ 内燃机 / 化工 / 流水线（福特）/ 大企业",
            "social_changes": "圈地运动驱农进城 / 工厂 12-16 小时工作 / 童工 / 城市贫民窟 / 工会萌芽 / 中产阶级崛起",
            "philosophical_response": "亚当斯密《国富论》/ 马克思《资本论》/ 狄更斯小说揭露惨状 / 卢德派砸机器 / 宪章运动",
            "manchester_birthplace": "棉纺工厂林立 / 烟雾蔽日 / 工人窝棚 / 恩格斯《英国工人阶级状况》",
            "victorian_era": "1837-1901 维多利亚女王 / 大英帝国巅峰 / 阶级森严 / 道德保守 / 小说黄金期",
            "key_inventors": "瓦特蒸汽机 / 史蒂芬森火车 / 爱迪生灯泡 / 贝尔电话 / 莱特兄弟飞机 / 福特 T 型车",
            "narrative_use": "蒸汽朋克世界观 / 维多利亚悬疑（福尔摩斯）/ 阶级抗争 / 资本家原罪 / 发明家传记",
            "activation_keywords": ["工业革命", "蒸汽机", "瓦特", "曼彻斯特", "维多利亚", "工厂", "童工", "马克思", "蒸汽朋克"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("工业革命", ""), llm_note("工业革命史")],
        tags=["历史", "工业革命", "通用"],
    ),
    # 冷战
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-hist-cold-war",
        name="冷战（1947-1991）",
        narrative_summary="美苏两极对抗 44 年。柏林墙 / 古巴危机 / 越南战争 / 阿富汗战争 / 太空竞赛 / 间谍战。"
                          "1991 苏联解体落幕。适用谍战 / 政治悬疑 / 太空 / 七八十年代背景。",
        content_json={
            "core_phases": "1947 杜鲁门主义起 → 朝鲜战争 1950 → 古巴导弹危机 1962（核战边缘）→ 越南战争 1955-1975 → 缓和期 1970s → 里根重振军备 1980s → 苏联解体 1991",
            "berlin_symbol": "1948 柏林空运 / 1961 柏林墙建立 / 1989 柏林墙倒塌 = 冷战分水岭",
            "cuban_missile_crisis": "1962 苏联在古巴部署导弹 / 肯尼迪海上封锁 / 13 天博弈 / 离核大战最近的一次 / 赫鲁晓夫退让",
            "vietnam_war": "1955-1975 / 美军 5.8 万阵亡 / 越南数百万 / 反战运动 / 美国战后心理创伤 / 凯申中标范本",
            "space_race": "1957 苏联斯普特尼克 / 1961 加加林首位太空人 / 1969 阿波罗 11 号阿姆斯特朗登月 / 太空竞赛美胜",
            "espionage": "CIA vs KGB / 剑桥五杰 / 罗森伯格夫妇电椅 / 鼹鼠 / 双重间谍 / 死信箱 / 微缩胶卷 / 詹姆斯邦德式",
            "proxy_wars": "朝鲜 / 越南 / 阿富汗 / 安哥拉 / 中东多次战争 / 拉美各国军事政变",
            "end": "1985 戈尔巴乔夫改革开放 / 1989 东欧剧变 / 1991 苏联解体 / 美国独超时代",
            "narrative_use": "谍战经典（《锅匠裁缝士兵间谍》）/ 政治悬疑 / 太空（阿波罗 13）/ 越战创伤 / 80 年代背景",
            "activation_keywords": ["冷战", "柏林墙", "古巴危机", "越战", "KGB", "CIA", "太空竞赛", "阿波罗", "戈尔巴乔夫"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("冷战", ""), llm_note("冷战史")],
        tags=["历史", "冷战", "通用"],
    ),
    # 鸦片战争
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-hist-opium-wars",
        name="鸦片战争（1840-1842 / 1856-1860）",
        narrative_summary="近代中国屈辱开端。林则徐虎门销烟 → 英舰开战 → 南京条约 → 香港割让。"
                          "第二次更惨：英法联军烧圆明园。"
                          "中国从天朝跌入半殖民。适用近代史 / 谍战 / 革命铺垫。",
        content_json={
            "first_opium_war": "1840-1842 / 林则徐 1839 虎门销烟 → 英舰北上 → 攻陷广州 / 厦门 / 定海 / 南京城下 → 1842 南京条约",
            "nanjing_treaty": "割香港岛 / 五口通商（广州 / 厦门 / 福州 / 宁波 / 上海）/ 赔款 2100 万银元 / 协定关税 / 治外法权（虎门条约）",
            "second_opium_war": "1856-1860 / 亚罗号事件起 / 英法联军 / 1860 攻入北京 / 火烧圆明园 / 北京条约 / 割九龙",
            "domestic_impact": "国库空虚 / 太平天国乘虚而起（1851-1864）/ 自强运动 / 同光中兴 / 洋务运动",
            "key_figures": "林则徐 / 道光皇帝 / 璞鼎查 / 巴麦尊 / 咸丰帝 / 恭亲王 / 额尔金（火烧圆明园）",
            "lasting_lessons": "落后就要挨打 / 师夷长技以制夷 / 东亚秩序崩塌 / 日本看到机会即明治维新 / 中国百年屈辱开端",
            "narrative_use": "近代史正剧（《大清盐商》）/ 抗英义士 / 谍战铺垫 / 革命前夜 / 香港殖民史",
            "activation_keywords": ["鸦片战争", "林则徐", "虎门销烟", "南京条约", "圆明园", "割香港", "近代史", "百年屈辱"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("鸦片战争", ""), llm_note("近代中国史")],
        tags=["历史", "近代", "通用"],
    ),
    # 苏联解体
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-hist-ussr-collapse",
        name="苏联解体（1985-1991）",
        narrative_summary="20 世纪最大地缘事件。戈尔巴乔夫改革失控 → 八一九政变 → 别洛韦日协议 → 15 加盟共和国独立。"
                          "适用谍战末期 / 90 年代俄罗斯衰落 / 寡头崛起。",
        content_json={
            "preconditions": "经济停滞勃列日涅夫时代 / 阿富汗战争烂账 / 切尔诺贝利 1986 信任崩塌 / 油价低位 / 民族矛盾",
            "gorbachev_reforms": "1985 戈尔巴乔夫上任 / 公开性 Glasnost / 改革 Perestroika / 新思维外交 / 撤军阿富汗 / 终结冷战",
            "1989_eastern_europe": "波兰团结工会 / 匈牙利改革 / 柏林墙倒塌 / 罗马尼亚齐奥塞斯库被处决 / 苏东体系崩溃",
            "august_coup": "1991 八月强硬派政变软禁戈尔巴乔夫 / 叶利钦坦克前演说 / 三天政变失败 / 加速解体",
            "dissolution": "1991 12 月 8 日别洛韦日协议（俄白乌）/ 12 月 25 日戈尔巴乔夫辞职 / 红旗降下 / 苏联终结",
            "shock_therapy": "1992 盖达尔休克疗法 / 通胀千倍 / 卢布暴跌 / 寡头瓜分国企 / 90 年代乱世",
            "rise_oligarchs": "别列佐夫斯基 / 霍多尔科夫斯基 / 阿布拉莫维奇 / 通过 loans-for-shares 占有石油 / 媒体 / 矿业",
            "putin_rise": "1999 普京被叶利钦指定接班 / 2000 当选 / 整顿寡头（霍多 2003 入狱）/ 强势复苏",
            "narrative_use": "末日苏联谍战 / 90 年代俄罗斯黑帮 / 寡头崛起 / 切尔诺贝利灾难 / 国家解体心理冲击",
            "activation_keywords": ["苏联解体", "戈尔巴乔夫", "叶利钦", "切尔诺贝利", "别洛韦日", "寡头", "休克疗法", "普京"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("苏联解体", ""), llm_note("苏联末期")],
        tags=["历史", "苏联", "通用"],
    ),
    # 大航海时代
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-hist-age-of-discovery",
        name="大航海时代（15-17 世纪）",
        narrative_summary="哥伦布 1492 发现新大陆 / 达伽马印度航路 / 麦哲伦环球。"
                          "葡萄牙 → 西班牙 → 荷兰 → 英国相继称霸海洋。"
                          "全球贸易 / 殖民帝国 / 三角贸易奴隶贸易 / 物种大交换。",
        content_json={
            "key_voyages": "1488 迪亚士绕好望角 / 1492 哥伦布到加勒比 / 1498 达伽马到印度卡利卡特 / 1519-1522 麦哲伦环球 / 1606 荷兰人到澳洲",
            "iberian_pioneers": "葡萄牙学校航海亲王 / 香料贸易垄断印度洋 / 西班牙征服阿兹特克（科尔特斯）+ 印加（皮萨罗）/ 教皇子午线划地球",
            "dutch_century": "17 世纪荷兰海上马车夫 / 东印度公司 VOC（最早跨国公司）/ 阿姆斯特丹证交所 / 郁金香泡沫 / 巴达维亚（雅加达）",
            "british_empire": "16 世纪打败西班牙无敌舰队 / 17 世纪东印度公司印度 / 北美十三殖民地 / 18 世纪日不落帝国",
            "columbian_exchange": "新世界 → 旧世界：玉米 / 土豆 / 番茄 / 烟草 / 可可 / 辣椒 / 火鸡 / 旧 → 新：小麦 / 牛羊马 / 天花致美洲原住民死 90%",
            "triangle_trade": "欧洲制造品 → 非洲（换奴隶）→ 美洲（卖奴隶 + 买糖棉）→ 欧洲 / 跨大西洋奴隶贸易 1200 万非洲人",
            "navigation_tech": "卡拉维尔船 / 卡拉克船 / 星盘 / 罗盘 / 海图 / 经纬度（经度难题至 18 世纪哈里森航海钟解决）",
            "famous_figures": "哥伦布 / 达伽马 / 麦哲伦 / 科尔特斯 / 皮萨罗 / 德雷克爵士 / 库克船长",
            "narrative_use": "海盗奇幻 / 殖民史 / 探险（《加勒比海盗》）/ 商业崛起（VOC 故事）/ 航海冒险",
            "activation_keywords": ["大航海", "哥伦布", "麦哲伦", "无敌舰队", "东印度公司", "三角贸易", "美洲征服", "环球航行"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("地理大发现", ""), llm_note("大航海时代")],
        tags=["历史", "大航海", "通用"],
    ),
    # 古埃及
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-hist-ancient-egypt",
        name="古埃及文明（BC 3100-BC 30）",
        narrative_summary="尼罗河孕育 3000 年文明。法老 + 金字塔 + 木乃伊 + 象形文字 + 多神教。"
                          "古王国（吉萨金字塔）/ 中王国 / 新王国（图坦卡蒙 / 拉美西斯 / 阿肯那顿）。"
                          "克娄巴特拉七世 BC 30 终结，被罗马吞并。",
        content_json={
            "three_kingdoms": "古王国 BC 2686-2181（金字塔时代）/ 中王国 BC 2055-1650 / 新王国 BC 1550-1069（帝国时代）/ 后王朝期 + 托勒密希腊化",
            "great_pyramids": "吉萨三大金字塔 / 胡夫金字塔 146 米 / 230 万块巨石 / 已知最大法老陵墓 / 至今保留",
            "famous_pharaohs": "胡夫（金字塔）/ 哈特谢普苏特（女法老）/ 图特摩斯三世（征服者）/ 阿肯那顿（一神教改革）/ 图坦卡蒙（少年早夭墓未盗）/ 拉美西斯二世（最长在位 67 年）/ 克娄巴特拉七世（末代）",
            "religion_pantheon": "拉（太阳神）/ 奥西里斯（冥王）/ 伊西斯（母神）/ 荷鲁斯（鹰神）/ 阿努比斯（豺狼神 / 木乃伊化）/ 玛阿特（真理 / 心脏称重）",
            "afterlife_beliefs": "死亡之书 / 心脏称重审判 / 木乃伊保存 / 卡（生命力）/ 巴（灵魂）/ 阿赫（升天）/ 陪葬品 + 仆从俑",
            "writing": "象形文字（圣书体）/ 僧侣体 / 世俗体 / 罗塞塔石碑 1799 拿破仑发现 + 商博良破译",
            "key_sites": "吉萨金字塔群 / 卢克索（底比斯）/ 卡纳克神庙 / 帝王谷 / 阿布辛贝神庙 / 亚历山大",
            "narrative_use": "古埃及奇幻（《木乃伊归来》）/ 考古悬疑 / 法老转世 / 神秘学",
            "activation_keywords": ["古埃及", "金字塔", "法老", "图坦卡蒙", "拉美西斯", "克娄巴特拉", "象形文字", "罗塞塔", "木乃伊"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("古埃及", ""), llm_note("古埃及史")],
        tags=["历史", "古文明", "通用"],
    ),
    # 三国时代（深化）
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-hist-three-kingdoms-deep",
        name="三国时代深化（184-280）",
        narrative_summary="中国最具戏剧性的乱世。黄巾起义 → 群雄割据 → 官渡 / 赤壁 / 夷陵三大战 → 三国鼎立 → 司马氏夺权 → 西晋统一。"
                          "源出《三国志》/ 演义出《三国演义》。",
        content_json={
            "phases": "184 黄巾起义 / 189 董卓乱政 / 200 官渡（曹胜袁）/ 208 赤壁（孙刘破曹）/ 219 关羽失荆州 / 222 夷陵（陆逊败刘）/ 234 五丈原（诸葛卒）/ 263 蜀亡 / 265 司马炎篡魏 / 280 吴亡晋统",
            "wei_camp": "曹操（治世能臣乱世奸雄）/ 曹丕（魏文帝）/ 司马懿（隐忍夺权）/ 五子良将 + 八虎骑 / 荀彧 + 郭嘉 + 贾诩",
            "shu_camp": "刘备（仁义皇叔）/ 关羽（武圣）/ 张飞 / 诸葛亮（卧龙）/ 赵云 / 马超 / 黄忠 / 法正 / 庞统（凤雏）/ 五虎上将",
            "wu_camp": "孙坚 → 孙策 → 孙权 / 周瑜（公瑾）/ 鲁肃 / 吕蒙（白衣渡江）/ 陆逊（火烧连营）/ 大都督世系",
            "key_battles": "官渡之战（曹操以少胜多 + 火烧乌巢）/ 赤壁之战（火攻 + 苦肉计 + 借东风）/ 夷陵之战（陆逊火烧七百里）",
            "famous_strategies": "三顾茅庐 / 隆中对 / 草船借箭 / 苦肉计 / 反间计 / 空城计 / 七擒孟获 / 六出祁山",
            "post_kingdoms": "司马懿 → 司马昭 → 司马炎篡魏立晋 / 八王之乱 / 五胡乱华 / 晋室南渡",
            "narrative_use": "正剧（《三国演义》《新三国》）/ 历史架空 / 重生回三国 / 谋士竞技",
            "activation_keywords": ["三国", "官渡", "赤壁", "夷陵", "诸葛亮", "曹操", "司马懿", "周瑜", "桃园三结义"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("三国", ""), llm_note("三国志 + 演义")],
        tags=["历史", "三国", "通用"],
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
