"""
Batch 29: Religion deeper — Buddhism schools / Christianity denominations /
Islam sects / Hindu deities / Judaism / Sikhism / Zoroastrianism /
modern movements. Activates religious vocabulary across world traditions.
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
    # 佛教各宗派
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-rel-buddhism-schools-deep",
        name="佛教宗派深化",
        narrative_summary="原始佛教（部派）→ 大乘 vs 上座部 → 大乘分汉传（八宗）+ 藏传（四派）+ 日本 → 上座部为东南亚主流。"
                          "禅 / 净土 / 密 / 唯识 / 华严 / 天台 / 律 / 三论 = 汉传八宗。",
        content_json={
            "three_main_branches": "上座部（南传 / 斯里兰卡 + 缅泰柬老）/ 大乘（汉传 + 藏传 + 日本 + 韩越）/ 金刚乘（藏传是大乘融密）",
            "chinese_eight_schools": "禅宗（达摩 + 慧能 + 顿悟 + 临济 + 曹洞）/ 净土宗（昙鸾 + 善导 + 念佛往生 + 净土三经）/ 密宗（开元三大士 + 不空 + 失传后日本承传）/ 唯识宗（玄奘 + 法相 + 心识转识）/ 华严宗（杜顺 + 法藏 + 五教十宗）/ 天台宗（智顗 + 三谛圆融 + 法华为本）/ 律宗（道宣 + 戒律）/ 三论宗（吉藏 + 中观）",
            "tibetan_four_schools": "宁玛派（红教 + 莲花生大士 + 大圆满 + 最古老）/ 萨迦派（花教 + 道果 + 元朝国师）/ 噶举派（白教 + 米拉日巴 + 大手印）/ 格鲁派（黄教 + 宗喀巴 + 达赖班禅 + 当代主流）",
            "japanese_buddhism": "天台宗（最澄 + 比叡山）/ 真言宗（空海 + 高野山 + 密宗）/ 净土宗（法然）/ 净土真宗（亲鸾 + 唯念佛）/ 临济宗（荣西 + 禅宗）/ 曹洞宗（道元）/ 日莲宗（日莲 + 法华独尊）",
            "key_practices": "禅修 / 念佛 / 持咒 / 拜佛 / 朝山 / 闭关 / 茶禅一味 / 戒律 / 苦行（南传）/ 灌顶（密）/ 辩经（藏）",
            "buddhist_canon": "三藏 = 经（佛说）+ 律（戒）+ 论（论疏）/ 巴利三藏（南传）/ 汉文大藏经 / 藏文大藏经 / 大正藏（日本编辑最完整）",
            "famous_monks_modern": "虚云老和尚 / 印光大师（净土）/ 弘一法师（律 + 李叔同）/ 太虚大师（人间佛教）/ 印顺导师 / 圣严法师 / 星云大师 / 一行禅师（越南）/ 达赖喇嘛 + 班禅（藏）",
            "narrative_use": "佛门题材（禅宗悬疑《不空之夜》）/ 历史（玄奘西游）/ 仙侠混入佛理 / 武侠（少林 + 武当）/ 修行小说",
            "activation_keywords": ["佛教", "禅宗", "净土", "藏传", "宁玛", "格鲁", "玄奘", "达摩", "慧能", "弘一"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("佛教宗派", ""), llm_note("佛学院")],
        tags=["宗教", "佛教", "通用"],
    ),
    # 基督教派系
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-rel-christianity-denominations",
        name="基督教派系全谱",
        narrative_summary="天主教（罗马教廷 + 教皇）vs 东正教（君士坦丁堡 + 莫斯科）vs 新教（宗教改革后多支）。"
                          "新教又分路德 + 加尔文 + 安立甘 + 浸信 + 卫理 + 福音 + 五旬节。",
        content_json={
            "great_schism_1054": "1054 东西大分裂 / 罗马教皇 vs 君士坦丁堡牧首 / 教义（圣灵从父出还是从父子出）+ 政治（罗马 vs 拜占庭）/ 至今未复合",
            "catholic_features": "教皇至上 / 七圣事 / 圣母崇拜 / 圣徒敬拜 / 拉丁弥撒（梵二改用本地语）/ 全球 13 亿信众 / 梵蒂冈 / 神职禁婚",
            "orthodox_features": "牧首制（君堡 + 莫斯科 + 安提阿 + 亚历山大 + 耶路撒冷 + 各国自治教会）/ 圣像崇拜 / 神职可婚（主教不行）/ 礼仪复杂宏伟 / 俄罗斯 + 希腊 + 东欧主流",
            "reformation_1517": "马丁路德 1517 年贴 95 条论纲 / 因信称义 / 唯独圣经 / 唯独恩典 / 反对赎罪券 + 教皇权威 / 宗教改革引发战争 + 三十年战争",
            "lutheran": "路德宗 / 路德传统 / 北欧 + 德国部分 / 尊圣经 + 二圣事 / 路德会 / 因信称义旗帜",
            "calvinist_reformed": "加尔文宗 / 改革宗 / 长老会 / 预定论（双重预定）/ TULIP 五要点 / 苏格兰 + 荷兰 + 美国清教 / 工作伦理（韦伯论资本主义）",
            "anglican": "圣公会 / 英国国教 / 亨利八世为离婚立 / 兼具天主教礼仪 + 新教教义 / 普世圣公宗 / 英联邦",
            "baptist": "浸信会 / 全身入水浸礼 / 个人信仰决志 / 美国南方浸信会最大新教教派 / 灵恩派常浸信",
            "methodist": "卫理公会 / 卫斯理兄弟创立 / 强调成圣 + 社会关怀 / 卫理公会派教会",
            "pentecostal_charismatic": "五旬节派 / 灵恩运动 / 说方言 + 神医 + 预言 / 20 世纪暴增 / 拉美 + 非洲爆炸式 / 韩国汝矣岛纯福音教会世界最大单体",
            "eastern_branches": "聂斯脱利派（景教，唐入华）/ 一性论（埃塞俄比亚 + 科普特）/ 亚美尼亚使徒",
            "modern_movements": "福音派（圣经无误 + 重生 + 宣教）/ 基要派 / 灵恩 / 自由派神学（更新教义）/ 解放神学（拉美 + 关注穷人）",
            "narrative_use": "天主教悬疑（《达芬奇密码》）/ 修女题材 / 宗教改革背景 / 中世纪 + 教廷阴谋 / 当代福音派文化",
            "activation_keywords": ["基督教", "天主教", "东正教", "新教", "宗教改革", "马丁路德", "加尔文", "圣公会", "教皇"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("基督教派系", ""), llm_note("教会史")],
        tags=["宗教", "基督教", "通用"],
    ),
    # 伊斯兰教派系
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-rel-islam-sects",
        name="伊斯兰教派系",
        narrative_summary="逊尼派 85% + 什叶派 15%。"
                          "逊尼下分四大教法学派（哈乃斐 / 沙菲仪 / 马立克 / 罕百里）。"
                          "什叶下分十二伊玛目派（伊朗主流）+ 伊斯玛仪派 + 栽德派。"
                          "苏菲神秘主义 + 瓦哈比保守。",
        content_json={
            "sunni_shia_split": "632 穆罕默德归真 / 谁继任分歧 / 逊尼支持选举哈里发 / 什叶坚信阿里（穆罕默德女婿堂弟）应继 / 680 卡尔巴拉之战阿里之子侯赛因被杀 → 永久仇恨",
            "sunni_features": "85% 全球穆斯林 / 主要分布阿拉伯世界 + 土耳其 + 北非 + 印巴 + 东南亚 / 哈里发为政治领袖（已无）/ 四大教法学派",
            "four_sunni_madhabs": "哈乃斐派（最大 + 土耳其 + 印巴 + 巴尔干）/ 沙菲仪派（东非 + 也门 + 印尼 + 马来）/ 马立克派（北非 + 西非 + 海湾部分）/ 罕百里派（沙特 + 卡塔尔 + 严格 + 瓦哈比之根）",
            "shia_features": "15% 全球 / 集中伊朗（90%）/ 伊拉克（60%）/ 巴林（70%）/ 黎巴嫩（30+%）/ 也门胡塞 / 伊玛目（精神领袖）传承 / 卡尔巴拉哀悼 / 穆哈兰姆月",
            "twelver_main_shia": "伊斯纳阿沙里 = 十二伊玛目派 / 信第十二位伊玛目隐遁 + 末日重现 / 伊朗 + 伊拉克 + 黎巴嫩 + 巴林主流",
            "ismaili": "伊斯玛仪派 / 第七位伊玛目分歧 / 阿迦汗为现代领袖 / 阿迦汗发展署 / 全球分散精英化",
            "wahhabi_salafi": "瓦哈比派（18 世纪沙特半岛兴起 + 极保守 + 反偶像）/ 萨拉菲（追随经典前三代 + 全球）/ 沙特国教 + 输出伊斯兰复兴",
            "sufism_mystical": "苏菲派 / 神秘主义 + 与神合一 / 旋转托钵僧（土耳其梅夫拉维）/ 鲁米诗 / 加扎里 / 普世苏菲音乐 + 舞蹈 / 中亚 + 印巴广为流传",
            "five_pillars_recap": "念证（清真言）/ 礼拜（每日五次朝麦加）/ 斋戒（莱麦丹月）/ 天课（财产 2.5%）/ 朝觐（一生至少一次）",
            "extremist_offshoots": "基地组织 / ISIS 伊斯兰国 / 塔利班 / 大多数穆斯林反对 / 不代表主流 / 2001 9-11 后全球反恐",
            "modern_thinkers": "赛义德库特卜（穆斯林兄弟会理论家）/ 毛杜迪（巴基斯坦伊斯兰复兴）/ 霍梅尼（伊朗革命）/ 法德拉拉（黎巴嫩什叶派）",
            "narrative_use": "中东背景 / 谍战（贝鲁特 + 伊朗 + 沙特）/ 历史（奥斯曼 + 撒拉森）/ 移民题材 / 美国 9-11 后",
            "activation_keywords": ["伊斯兰", "逊尼", "什叶", "瓦哈比", "苏菲", "卡尔巴拉", "伊玛目", "哈乃斐", "鲁米"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("伊斯兰教派别", ""), llm_note("伊斯兰研究")],
        tags=["宗教", "伊斯兰", "通用"],
    ),
    # 印度教神祇
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-rel-hindu-pantheon",
        name="印度教神祇与体系",
        narrative_summary="世界最古老活宗教（4000+ 年）。三大主神：梵天（创造）/ 毗湿奴（维护）/ 湿婆（毁灭）。"
                          "毗湿奴十大化身：罗摩 + 黑天 + 佛陀 + 迦尔吉。湿婆 + 帕尔瓦蒂 + 象头神 + 战神。",
        content_json={
            "trimurti_three_main": "梵天 Brahma（创造神 + 4 头 4 手 + 妻萨拉斯瓦蒂智慧女神）/ 毗湿奴 Vishnu（维护神 + 蓝肤 + 妻拉克什米吉祥天 + 多次化身入世救劫）/ 湿婆 Shiva（毁灭神 + 第三眼 + 蛇缠颈 + 跳宇宙之舞 + 妻帕尔瓦蒂）",
            "vishnu_avatars": "毗湿奴十化身（Dashavatara）/ 鱼 / 龟 / 野猪 / 人狮 / 矮人 / 持斧罗摩 / 罗摩（《罗摩衍那》）/ 黑天 Krishna（《摩诃婆罗多》《薄伽梵歌》）/ 佛陀（融合佛教）/ 迦尔吉（末世骑白马救世）",
            "shiva_family": "湿婆 + 帕尔瓦蒂（雪山神女）+ 长子象头神迦内什（智慧障难破除神）+ 次子室建陀（战神）/ 室利兰卡或南迪公牛坐骑",
            "key_goddesses": "拉克什米（财富 + 美丽 + 莲花 + 毗湿奴妻）/ 萨拉斯瓦蒂（智慧 + 艺术 + 梵天妻）/ 帕尔瓦蒂（雪山神女 + 湿婆妻）/ 杜尔伽（战神化身 + 骑虎）/ 卡莉（黑色毁灭 + 吐舌挂头骨 + 帕尔瓦蒂愤怒形态）",
            "core_concepts": "梵 Brahman（终极实在）/ 我 Atman（个人灵魂）/ 业 Karma（行为后果）/ 轮回 Samsara / 解脱 Moksha / 法 Dharma（正法 + 责任）/ 四种姓 + 四生命阶段",
            "scriptures": "吠陀 Vedas（最早 4 部）/ 奥义书 Upanishads（哲学）/ 史诗：摩诃婆罗多（最长史诗）+ 罗摩衍那 / 往世书 Puranas / 薄伽梵歌（哲学高峰）",
            "schools_of_thought": "六派哲学：数论 + 瑜伽 + 正理 + 胜论 + 吠檀多 + 弥曼差 / 当代主流：吠檀多（不二论 / 限定不二论 / 二元论）",
            "yoga_paths": "业瑜伽（行动）/ 巴克提瑜伽（虔信）/ 智瑜伽（知识）/ 王瑜伽（哈达 + 冥想）/ 现代瑜伽是哈达瑜伽简化",
            "festivals": "排灯节 Diwali（最盛大 + 拉克什米 + 罗摩归来）/ 洒红节 Holi（春之色彩）/ 大壶节 Kumbh Mela（恒河朝圣）/ 杜尔伽普祭",
            "narrative_use": "印度史诗 / 奇幻借神话 / 瑜伽修行小说 / 移民题材 / 哈雷克里希纳运动",
            "activation_keywords": ["印度教", "毗湿奴", "湿婆", "梵天", "黑天", "罗摩", "拉克什米", "帕尔瓦蒂", "排灯节"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("印度教", ""), llm_note("印度教神祇")],
        tags=["宗教", "印度教", "通用"],
    ),
    # 犹太教
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-rel-judaism",
        name="犹太教",
        narrative_summary="一神教鼻祖。亚伯拉罕 → 摩西 → 大卫 → 所罗门 → 流亡 → 拉比传统 → 现代三派。"
                          "塔木德 + 妥拉 + 卡巴拉。正统 / 保守 / 改革三派 + 哈西迪派。",
        content_json={
            "history_timeline": "亚伯拉罕 BC 1800 / 摩西出埃及 BC 1300 / 大卫王国 BC 1000 / 所罗门圣殿 / 北国以色列 BC 722 亡 / 南国犹大 BC 586 巴比伦掳掠 / 第二圣殿 BC 516-AD 70 罗马毁 / 大流散 / 1948 以色列复国",
            "core_texts": "妥拉 Torah（摩西五经 + 创世 + 出埃及 + 利未 + 民数 + 申命）/ 塔纳赫 Tanakh = 妥拉 + 先知书 + 圣录 / 米示拿（口传律法成文）+ 革马拉（米示拿评注）= 塔木德",
            "talmud_centrality": "塔木德 = 巴比伦塔木德（更权威）+ 耶路撒冷塔木德 / 6 大部 63 卷 / 拉比辩论汇编 / 犹太法律 + 伦理 + 故事 + 神学百科",
            "613_commandments": "妥拉中 613 条诫命 / 248 正面（应做）+ 365 负面（不可做）/ 摩西十诫为核心 / 安息日 / 洁食 / 割礼",
            "three_main_modern_branches": "正统派（严守传统 + 男女分坐 + 严格洁食 + 安息日不开车）/ 保守派（中间路线 + 现代化 + 但保留核心）/ 改革派（自由派 + 美国主流 + 现代化解释）",
            "hasidic_movement": "哈西迪派 18 世纪东欧 / 巴尔谢姆托夫 / 强调虔敬 + 喜悦 + 故事 + 拉比效忠 / 当今美国 + 以色列特定社区 / 黑帽长髯",
            "kabbalah_mystical": "卡巴拉神秘主义 / 创世记神秘解释 / 生命之树 + 10 个赛菲洛特 / 索哈尔之书（中世纪西班牙）/ 现代名人误传 = 红线手镯不严肃版",
            "festivals": "逾越节 Pesach（出埃及）/ 五旬节 Shavuot（接受妥拉）/ 住棚节 Sukkot / 赎罪日 Yom Kippur（最神圣）/ 新年 Rosh Hashana / 光明节 Hanukkah / 普珥节",
            "kashrut_dietary": "洁食 / 不可猪 + 不可虾蟹（无鳍鳞海鲜）/ 牛羊鸡可但需正确屠宰 / 肉奶不可同时吃 / 安息日不工作 + 不开车 + 不点火",
            "anti_semitism_holocaust": "反犹主义历史悠久 / 中世纪迫害 + 俄国大屠杀 + 德雷福斯案 / 纳粹大屠杀 600 万 / 安妮日记 + 辛德勒名单 / 当代仍有但定义争议",
            "modern_israel": "1948 建国 + 以阿战争 + 巴勒斯坦冲突至今 / 特拉维夫世俗 vs 耶路撒冷虔诚 / 阿什肯纳兹（欧洲 + 主流）vs 西法迪（中东西亚）vs 米兹拉希（中东）",
            "narrative_use": "二战犹太人题材 / 纽约犹太家庭文化 / 以色列谍战 / 犹太黑帮 / 文学（罗斯 + 贝娄 + 卡夫卡 + 辛格）",
            "activation_keywords": ["犹太教", "妥拉", "塔木德", "卡巴拉", "哈西迪", "安息日", "光明节", "大屠杀", "以色列"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("犹太教", ""), llm_note("犹太学")],
        tags=["宗教", "犹太教", "通用"],
    ),
    # 道教深化
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-rel-daoism-deep",
        name="道教深化（教派 + 神祇）",
        narrative_summary="老子 + 庄子哲学 → 张道陵正一道（五斗米道）→ 全真道（王重阳）。"
                          "三清四御 + 玉皇 + 太上老君 + 元始天尊 + 灵宝天尊。"
                          "外丹 → 内丹 / 符箓 + 斋醮 + 房中 + 服食。",
        content_json={
            "philosophical_origins": "老子《道德经》/ 庄子《南华经》/ 列子 / 黄老学派 / 战国到汉初 / 道家哲学先于道教",
            "religious_founding": "东汉张道陵 + 五斗米道（蜀地）/ 张角太平道（黄巾起义）/ 葛洪《抱朴子》（晋）/ 寇谦之改革（北朝）/ 陆修静（南朝）+ 陶弘景（茅山宗）",
            "two_main_schools": "正一道（符箓派 + 张天师世袭 + 在家可修 + 江西龙虎山）/ 全真道（北宋王重阳 + 内丹 + 出家三戒 + 北京白云观 + 山西永乐宫）",
            "key_subschools": "茅山（上清派 + 江苏茅山 + 符咒）/ 武当（张三丰 + 内家武术）/ 龙虎山（张天师正一祖庭）/ 阁皂山 / 玄武当 / 楼观台",
            "three_pure_ones_sanqing": "元始天尊（玉清 + 创世）/ 灵宝天尊（上清 + 度众）/ 道德天尊（太清 + 老子化身）= 道教最高神祇三清",
            "four_emperors_siyu": "玉皇大帝（执掌三界）/ 紫微大帝（北极）/ 勾陈大帝 / 后土皇地祇 = 辅佐三清",
            "popular_deities": "玉皇大帝（民间最高）/ 王母娘娘 / 八仙（吕洞宾 + 张果老 + 何仙姑 + 韩湘子 + 蓝采和 + 曹国舅 + 钟离权 + 铁拐李）/ 关帝 / 妈祖 / 财神（赵公明 + 比干 + 范蠡）/ 城隍 / 土地 / 灶神",
            "alchemy_traditions": "外丹（炼金石 + 黄白术 + 铅汞）唐代盛行但服食致死多 / 内丹（精气神三宝 + 周天功 + 任督二脉 + 性命双修）宋以后主流",
            "rituals": "斋醮（法会做法）/ 符箓（画符念咒）/ 步罡踏斗 / 召神驱鬼 / 章表（上奏天庭）",
            "key_concepts": "道（终极实在）/ 德 / 阴阳 / 五行 / 八卦 / 无为 / 自然 / 长生 / 神仙 / 真人 / 黄帝 + 老子托名",
            "famous_immortals": "黄帝（人文初祖）/ 老子（道祖）/ 庄子 / 列子 / 张道陵 / 葛洪 / 吕洞宾 / 张三丰 / 王重阳 + 全真七子（丘处机 + 马钰 + 谭处端 + 王处一 + 郝大通 + 刘处玄 + 孙不二）",
            "qiu_chuji_genghis": "丘处机西行万里见成吉思汗 / 一言止杀 / 元朝赐免税 + 龙门派",
            "narrative_use": "仙侠题材根基 / 武侠武当 / 历史（茅山宗修真）/ 玄幻 + 仙道 / 全真教（射雕英雄传）",
            "activation_keywords": ["道教", "三清", "玉皇", "八仙", "全真", "正一", "张三丰", "丘处机", "内丹", "符箓"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("道教", ""), llm_note("道教史")],
        tags=["宗教", "道教", "通用"],
    ),
    # 神道教
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-rel-shinto",
        name="神道教（日本）",
        narrative_summary="日本本土宗教。万物有灵 / 八百万神 / 天皇神裔。"
                          "天照大神 + 须佐之男 + 月读三贵子 + 各地土地神。"
                          "神社 + 鸟居 + 巫女 + 神乐 + 御朱印。",
        content_json={
            "core_concepts": "Kami 神 = 自然 + 祖灵 + 帝王 + 英杰 + 物件中存在的神性 / 八百万神（数不清）/ 万物有灵 / 不强调来世 + 强调今生洁净",
            "creation_myth": "伊邪那岐 + 伊邪那美夫妇神搅乱大海生岛屿 + 万物 / 伊邪那美产火神死下黄泉 / 伊邪那岐黄泉归来 + 净身 + 左眼生天照（太阳）+ 右眼生月读（月亮）+ 鼻生须佐之男（暴风雨）",
            "main_deities": "天照大神（太阳神 + 皇室祖神 + 伊势神宫）/ 须佐之男（暴风雨 + 出云）/ 月读（月亮）/ 八幡神（武神）/ 稻荷大神（农业 + 狐狸使者）/ 大国主（出云大社）",
            "imperial_descent": "天皇号称天照大神后裔 / 神武天皇 BC 660 建国（神话）/ 万世一系 / 二战前神道教国教 + 战败后政教分离 + 天皇象征化",
            "shrines": "伊势神宫（天照神宫 + 最神圣）/ 出云大社（大国主）/ 明治神宫（明治天皇）/ 靖国神社（战死英灵 + 二战甲级战犯合祀争议）/ 严岛神社（海中鸟居）/ 平安神宫 / 鹤冈八幡宫",
            "sacred_objects": "三种神器：八咫镜（镜）+ 八尺琼勾玉（玉）+ 草薙剑（剑）/ 天皇即位授予 / 镜 = 智 + 玉 = 仁 + 剑 = 勇",
            "rituals_practices": "参拜步骤：鸟居一礼 + 洗手漱口 + 投钱 + 摇铃 + 二拜二拍一拜 / 御朱印章 / 御守护身符 / 新年初诣 / 七五三 / 神前结婚 / 地镇祭",
            "folk_kami": "稻荷神（农业 + 商业 + 狐狸使者 + 鸟居红色）/ 道祖神（路口）/ 山神 / 海神 / 水神 / 妖怪也属神道延伸：天狗 / 河童 / 鬼 / 雪女",
            "shinto_buddhism_fusion": "神佛习合 / 6 世纪佛教传入与神道融合 / 本地垂迹（佛是神的本来形态）/ 明治维新强行神佛分离",
            "matsuri_festivals": "三大祭（祇园祭京都 + 天神祭大阪 + 神田祭东京）/ 阿波舞 / 七夕 / 神社祭典 + 神舆 + 山车 + 太鼓 + 屋台",
            "narrative_use": "日本古风 / 阴阳师题材（《阴阳师》）/ 民俗悬疑 / 妖怪故事（《夏目友人帐》）/ 武士神社祈愿",
            "activation_keywords": ["神道教", "天照", "鸟居", "神社", "靖国", "伊势", "稻荷", "阴阳师", "三种神器"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("神道", ""), llm_note("Shinto")],
        tags=["宗教", "神道", "通用"],
    ),
    # 锡克教
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-rel-sikhism",
        name="锡克教",
        narrative_summary="15 世纪印度旁遮普诞生。融合印度教 + 伊斯兰教精华。"
                          "Guru Nanak 始 → 十大上师 → Guru Granth Sahib 经书永为宗师。"
                          "白头巾 + 长须 + 短剑 + 金庙阿姆利则。",
        content_json={
            "founder_history": "Guru Nanak（1469-1539）/ 旁遮普 / 反对印度教种姓制度 + 反对伊斯兰强迫改宗 / 主张一神 + 平等",
            "ten_gurus": "1 Guru Nanak / 2 Angad（创新字母 Gurmukhi）/ 3 Amar Das / 4 Ram Das（建阿姆利则）/ 5 Arjan（编 Adi Granth）/ 6 Hargobind（武装化）/ 7 Har Rai / 8 Har Krishan（孩童夭）/ 9 Tegh Bahadur（殉教抗莫卧儿）/ 10 Gobind Singh（建卡尔萨 + 五 K）",
            "sacred_book": "Guru Granth Sahib / 第十位上师宣布从此经书永为宗师 / 1430 页 / 多语言（旁遮普语 + 印地 + 波斯）/ 不只锡克上师 + 印度教 + 伊斯兰圣徒诗作",
            "khalsa_brotherhood": "1699 Guru Gobind Singh 创卡尔萨（纯洁者团体）/ 五位首批 = 五挚爱 / 锡克男姓 Singh（狮）+ 女姓 Kaur（公主）",
            "five_ks": "Kesh（不剪发 + 头巾包起 + 男长须）/ Kara（铁手镯）/ Kanga（小木梳）/ Kachera（短裤式内衣）/ Kirpan（短剑）= 五件标志物",
            "core_beliefs": "一神论 Ek Onkar / 平等（无种姓 + 男女平等）/ 服务 Seva / 诚实工作 / 分享所得 / 反对偶像 + 朝圣 + 苦修 + 算命",
            "key_practices": "晨昏祈祷 / 共济厨房 Langar（任何人免费用餐 + 平等）/ 阿姆利则金庙 Harmandir Sahib（最神圣 + 镀金 + 在水中央）/ 不剪发 + 包头巾",
            "modern_distribution": "全球 2500 万 / 主体在印度旁遮普邦 / 大规模移民英国 + 加拿大 + 美国 / 加拿大现任贾格梅特辛格 NDP 党首",
            "history_conflicts": "莫卧儿迫害 / 第九上师殉教 / 19 世纪锡克帝国（兰季特辛格）/ 1947 印巴分治流血 / 1984 印度军方进攻金庙（蓝星行动）+ 英迪拉甘地遇刺（锡克卫兵报复）",
            "narrative_use": "印度题材 / 移民故事 / 加拿大新移民 / 旁遮普文化 / 历史（锡克帝国 + 抗莫卧儿）",
            "activation_keywords": ["锡克教", "Sikh", "Guru Nanak", "金庙", "阿姆利则", "Singh", "卡尔萨", "五 K", "包头巾"],
        },
        source_type="llm_synth", confidence=0.90,
        source_citations=[wiki("锡克教", ""), llm_note("Sikhism")],
        tags=["宗教", "锡克", "通用"],
    ),
    # 琐罗亚斯德教（拜火教）
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-rel-zoroastrianism",
        name="琐罗亚斯德教（拜火教）",
        narrative_summary="世界最古老一神教之一（约 BC 1500）。"
                          "波斯先知琐罗亚斯德 / 阿胡拉马兹达（光明善神）vs 安格拉曼纽（黑暗恶神）。"
                          "影响犹太 + 基督 + 伊斯兰末日观。中国称祆教 / 摩尼教近亲。",
        content_json={
            "founder_dating": "琐罗亚斯德 Zarathustra / 出生年代有争议 BC 1500-BC 600 / 波斯东北部 / 被尼采借名作《查拉图斯特拉如是说》",
            "core_dualism": "阿胡拉马兹达 Ahura Mazda（光明 + 真理 + 善神 + 唯一至高）vs 安格拉曼纽 Angra Mainyu / 阿里曼（黑暗 + 谎言 + 恶神）/ 二元对立 + 末日善胜恶",
            "sacred_texts": "阿维斯陀 Avesta / 上古经文 / 包括 Gathas（先知亲作颂歌）+ Yasna（仪式经）+ Vendidad（律法）+ Yashts（神话）",
            "fire_worship": "圣火 = 阿胡拉马兹达象征 / 火神庙 Atash Behram 圣火不灭 / 不是崇拜火本身而是借火朝向至高神 / 误称拜火教",
            "key_concepts": "Asha（真理 + 宇宙秩序 + 正义）vs Druj（谎言 + 混乱）/ 三大美德：好思 + 好言 + 好行 / 自由意志选择善恶",
            "death_funerary": "尸体不洁 / 不土葬不火葬 / 寂静之塔 Dakhma 上让秃鹫食尸 / 现代印度孟买帕西人仍用 / 也用电力分解",
            "ancient_persia": "阿契美尼德波斯帝国（居鲁士 + 大流士 + 薛西斯）国教 / 帕提亚 + 萨珊波斯延续 / 651 阿拉伯征服伊朗 / 大批信众逃亡印度 = 帕西人",
            "parsi_in_india": "8-10 世纪帕西人逃印度孟买 / 商业繁盛 / 现代名人：扎德拉拉塔塔（塔塔集团）+ 弗雷迪墨丘里 Queen 主唱 + 印第拉甘地丈夫费罗兹甘地",
            "influence_other_religions": "影响犹太教（巴比伦掳掠期波斯文化交融）/ 末日审判 + 复活 + 救世主 + 善恶二元论 / 进入基督教 + 伊斯兰教 / 摩尼教是混血儿",
            "manichaeism_offshoot": "摩尼教 / 3 世纪摩尼创 / 综合琐罗亚斯德 + 基督 + 佛教 + 灵知派 / 唐代入华为摩尼教 / 后伪装为明教 + 影响明朝起义 + 金庸《倚天屠龙记》",
            "modern_status": "全球仅 10-20 万信徒 / 主要印度孟买 + 伊朗（少量）+ 北美 / 即将濒危 / 不接受改宗 / 内婚",
            "narrative_use": "波斯历史背景 / 古代世界宗教碰撞 / 摩尼教明教武侠 / 拜火教悬疑（《天方夜谭》变调）",
            "activation_keywords": ["琐罗亚斯德", "拜火教", "阿胡拉马兹达", "查拉图斯特拉", "帕西人", "摩尼教", "明教", "圣火"],
        },
        source_type="llm_synth", confidence=0.90,
        source_citations=[wiki("琐罗亚斯德教", ""), llm_note("Zoroastrianism")],
        tags=["宗教", "古波斯", "通用"],
    ),
    # 北欧神话
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-rel-norse-mythology",
        name="北欧神话（维京宗教）",
        narrative_summary="维京时代古日耳曼宗教。九界 + 世界树 + 诸神黄昏。"
                          "奥丁 + 索尔 + 洛基 + 弗丽嘉 + 弗雷亚 + 海姆达尔。"
                          "影响 Marvel 漫画 + 一周天数命名 + 托尔金奇幻。",
        content_json={
            "nine_realms": "Asgard 阿斯加德（神族）/ Midgard 中庭（人类）/ Vanaheim 华纳海姆（华纳神族）/ Jotunheim 约顿海姆（巨人）/ Alfheim 精灵之地 / Svartalfheim 黑精灵 / Niflheim 冰雾世界 / Muspelheim 火焰之国 / Helheim 海拉之国（亡灵）/ 由世界树 Yggdrasil 连接",
            "two_god_clans": "阿萨神族 Aesir（奥丁 + 索尔 + 弗丽嘉 + 巴德尔等 + 主战）/ 华纳神族 Vanir（弗雷 + 弗雷亚 + 尼约德 + 主丰饶）/ 早期战争后联合",
            "main_aesir": "奥丁 Odin（众神之王 + 独眼 + 矛 Gungnir + 八腿马 Sleipnir + 渡鸦 Huginn + Muninn）/ 索尔 Thor（雷神 + 锤 Mjolnir + 战车山羊）/ 巴德尔（最美之神 + 被洛基害死）/ 提尔（战神 + 失手）/ 海姆达尔（守桥神 + 千里耳）/ 弗丽嘉（女王 + 智慧）",
            "loki_trickster": "洛基 / 巨人之子但与奥丁结义 / 美貌善变 + 阴谋诡计 + 既是同伴也是叛徒 / 害死巴德尔 / 终被绑诅咒 / 诸神黄昏率亡灵反阿斯加德",
            "world_tree_yggdrasil": "宇宙树 / 巨大梣树 / 三根穿三泉 / 鹰栖顶 + 龙啮根 + 松鼠传话 / 连接九界",
            "vanir_examples": "弗雷 Freyr（丰饶 + 太阳 + 和平 + 黄金野猪）/ 弗雷亚 Freya（爱与战争双性 + 飞鹰羽衣 + 半亡魂归她）/ 尼约德（海洋 + 风）",
            "ragnarok_apocalypse": "诸神黄昏 / 终末之战 / 三冬不暖 + 太阳被狼吞 / 洛基率亡灵 + 巨狼芬里尔 + 大蛇耶梦加得反攻 / 主神大都死 / 世界毁灭后重生 / 巴德尔归来",
            "key_creatures": "巨狼芬里尔（洛基子 + 吞日）/ 大蛇耶梦加得（环绕中庭 + 索尔死敌）/ 八腿马 / 双山羊战车 / 龙尼德霍格 / 女武神瓦尔基里 / 矮人黑精灵 / 巨人尤弥尔 + 索列姆等",
            "afterlife": "战死英雄一半归奥丁瓦尔哈拉殿 + 一半归弗雷亚 / 平凡死归海拉冥府 / 武士梦想战死光荣",
            "weekday_origins": "Tuesday = Tiu's day（提尔）/ Wednesday = Odin's / Wodan's day / Thursday = Thor's day / Friday = Freya's day",
            "marvel_pop_culture": "Marvel 雷神索尔系列 + 洛基系列影响西方流行文化 / 但与原神话有差异 / 维京时代影视复兴",
            "vikings_society": "8-11 世纪 / 北欧海盗 + 商人 + 探险者 / 长船航海 / 殖民冰岛 + 格陵兰 + 北美短暂（莱夫埃里克松 1000 年）/ 神话由埃达诗集保存（冰岛中世纪）",
            "narrative_use": "西方奇幻基础（《指环王》采用矮人 + 精灵 + 龙）/ 漫威雷神 / 维京题材（《维京传奇》）/ 末日叙事 / 中世纪欧洲",
            "activation_keywords": ["北欧神话", "奥丁", "索尔", "洛基", "诸神黄昏", "瓦尔哈拉", "维京", "世界树", "瓦尔基里"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("北欧神话", ""), llm_note("Norse mythology")],
        tags=["宗教", "北欧", "通用"],
    ),
    # 现代精神运动
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-rel-modern-spirituality",
        name="现代精神运动 + 新兴宗教",
        narrative_summary="新时代运动 + 山达基 + 邪教警示 + 瑜伽 + 正念。"
                          "巴哈伊 + 罗素的耶和华见证人 + 摩门教 + 一神普救派。"
                          "21 世纪精神选项多样化。",
        content_json={
            "new_age_movement": "新时代运动 / 1960s-70s 兴起 / 反传统宗教 + 借东方神秘主义 + 泛灵论 + 水晶 + 通灵 + 占星 / 个人灵修 + 身心灵全面健康 / Esalen 学院",
            "new_religious_movements": "巴哈伊（19 世纪波斯 / 巴哈欧拉 / 全人类一家 / 总部以色列海法）/ 摩门教（约瑟史密斯 1830 / 美国 + 犹他州 / 摩门经 + 多妻已废 / 大学杨百翰）/ 耶和华见证人（罗素 1879 / 严格末世论 / 不参政 / 不输血）/ 基督教科学派（玛丽贝克埃迪 / 灵性医治）",
            "scientology": "山达基 / L Ron Hubbard 1954 创 / 戴尼提 + 听析 / 高额会员费 / 阿汤哥 + 约翰特拉沃尔塔 / 屡有邪教争议",
            "moonies": "统一教 / 文鲜明 1954 韩国 / 集体婚礼著称 / 文鲜明世界政商网络 / 安倍晋三遇刺案件背景",
            "cult_warning_signs": "邪教警示：唯一真理（断绝外联系）+ 教主神化 + 内部秘密 + 经济掠夺 + 性控制 + 末日恐吓 + 离教恐怖 / 著名：人民圣殿 1978 圭亚那集体自杀 + 大卫支派韦科围城 1993 + 奥姆真理教东京地铁毒气 1995 + 法轮功",
            "yoga_meditation_west": "瑜伽 1960s 西渗 + 鲍勃马利 + 披头士印度灵修 / 现代化 + 健身化 + 远离印度教根 / 正念 mindfulness 借自佛教 + 卡巴金压力减压（MBSR）",
            "psychedelic_revival": "60s 嬉皮士 LSD + 致幻蘑菇 + 蒂莫西利里教授 / 21 世纪正经研究 PSYchedelic-assisted therapy + 抑郁 + PTSD + 临终焦虑",
            "wicca_paganism": "Wicca 巫教 / 1950s 英国 Gerald Gardner 创 / 自然崇拜 + 双性神（角神 + 月神）+ 8 个安息日 + 13 月亮 / 现代异教徒（北欧异教 + 凯尔特复兴 + 罗马异教 + 希腊异教）",
            "atheism_secular_humanism": "新无神论（道金斯 + 哈里斯 + 希钦斯 + 丹尼特四骑士）/ 世俗人本主义 / 美国无神论者协会 / 无宗教者 None 增长最快人口分组",
            "spiritual_but_not_religious": "SBNR / '我灵性但不宗教' / 21 世纪美国 + 西欧大趋势 / 个人折衷 + 反建制 + 重视体验 / 千禧 + Z 世代多",
            "narrative_use": "邪教受害者题材 / 阿汤哥山达基 / 摩门教剧（《魔门经》音乐剧）/ 新时代讽刺 / 心灵成长励志（但需小心）",
            "activation_keywords": ["新时代", "山达基", "摩门", "邪教", "正念", "瑜伽", "邪教警示", "灵性"],
        },
        source_type="llm_synth", confidence=0.90,
        source_citations=[wiki("新兴宗教", ""), llm_note("现代精神运动")],
        tags=["宗教", "现代", "通用"],
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
