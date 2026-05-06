"""
Batch 34: Factions / organizations / sects / corporations / secret societies.
Cross-genre faction templates: cultivation sects, mafia, spy agencies,
megacorps, royal families, mercenary guilds.
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
    # 仙侠 - 正派宗门模板
    MaterialEntry(
        dimension="factions", genre="仙侠",
        slug="faction-orthodox-sect-template",
        name="正派宗门模板（青云 / 太白 / 蜀山 / 玄门正宗）",
        narrative_summary="仙侠正派宗门通用模板。"
                          "教义崇正辟邪 + 守山门 + 内门 / 外门体系。"
                          "金庸武当少林 → 网文宗门翻版。",
        content_json={
            "structure": "宗主（最高）→ 太上长老（半隐退强者）→ 长老团（多位元婴 / 化神）→ 各峰首席 → 内门弟子 → 外门弟子 → 杂役",
            "internal_competition": "首座之争（长老选拔继承人）/ 内门赛（年度排位）/ 比试 / 立功换功法",
            "ideology": "崇正辟邪 / 救世苍生 / 守护一方 / 道家思想（无为/养气/天人合一）",
            "famous_examples": "诛仙青云门 / 仙剑蜀山派 / 修真世界太白派 / 凡人修仙黄枫谷 / 完美世界天音宗",
            "common_arc": "外门弟子（主角入门）→ 试炼通过 → 内门 → 真传 → 接任长老 → 宗主 / 出走自立",
            "anti_pattern": "宗门长老腐化 / 上层投靠魔道 / 主角觉醒后离开 = 反套路",
            "activation_keywords": ["宗门", "正派", "玄门", "青云", "蜀山", "弟子", "长老", "宗主"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("仙侠正派模板")],
        tags=["仙侠", "宗门", "正派", "模板"],
    ),
    # 仙侠 - 魔道宗门模板
    MaterialEntry(
        dimension="factions", genre="仙侠",
        slug="faction-demonic-sect-template",
        name="魔道宗门模板（天魔门 / 鬼王宗 / 万妖窟）",
        narrative_summary="仙侠魔道反派组织模板。"
                          "崇尚力量 / 修魔功 / 视生命为蝼蚁。"
                          "正派对立面 + 部分主角隐居其间。",
        content_json={
            "structure": "魔尊（独裁）→ 魔王 4 大长老 → 魔将 → 普通魔修 / 邪修",
            "ideology": "强者为尊 / 魔本天道 / 我命由我不由天 / 极端利己 / 修魔吞食他人",
            "techniques": "魔功（吸食精元 / 真气 / 灵魂）/ 血祭（活祭凡人）/ 蛊毒 / 邪术 / 召唤魔物",
            "famous_examples": "诛仙鬼王宗 / 凡人修仙血鸦盟 / 缥缈之旅冥皇宫 / 魔道祖师温家",
            "narrative_uses": "永恒反派 / 主角误入逐渐转变 / 魔门弟子是主角的朋友 / 正魔不两立但又有交集",
            "modern_subversions": "《魔道祖师》打破正魔二分 = 魏无羡是好的魔修 / 灰色道德",
            "activation_keywords": ["魔道", "魔门", "邪魔", "魔尊", "魔修", "邪术", "血祭"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("仙侠魔道模板")],
        tags=["仙侠", "魔道", "反派", "模板"],
    ),
    # 仙侠 - 散修联盟
    MaterialEntry(
        dimension="factions", genre="仙侠",
        slug="faction-free-cultivator-alliance",
        name="散修联盟 / 灰色地带",
        narrative_summary="仙侠中立组织。"
                          "无门无派的修真者集合。"
                          "佣兵性质 + 资源交易 + 拍卖会。",
        content_json={
            "structure": "无明显领袖（推选盟主）/ 公会式管理 / 任务发布 / 等级制度（一星-九星散修）",
            "services": "任务承接（赏金）/ 灵药买卖 / 拍卖会 / 信息买卖 / 雇佣保镖 / 仙缘介绍",
            "common_locations": "天南地北的散修聚集地（修真坊市 / 黑市 / 拍卖城 / 神秘酒楼）",
            "famous_examples": "诛仙黑石坊 / 凡人修仙傀儡门 / 完美世界一些散修流派",
            "narrative_uses": "主角入坊市 = 引出拍卖 + 任务 + 信息 / 散修联盟接任务推动主线 / 中立第三方角色",
            "twist_pattern": "联盟盟主是隐藏大魔头 / 联盟为正派提供情报 = 双面 / 主角从散修起家成立自己门派",
            "activation_keywords": ["散修", "拍卖", "任务", "佣兵", "坊市", "黑市", "联盟"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[llm_note("仙侠散修组织")],
        tags=["仙侠", "散修", "中立", "佣兵"],
    ),
    # 西方奇幻 - 国王 / 贵族议会
    MaterialEntry(
        dimension="factions", genre="西方奇幻",
        slug="faction-royal-council",
        name="王室 + 贵族议会（君主立宪 / 封建)",
        narrative_summary="西方奇幻王室政治结构。"
                          "国王 + 王后 + 王储 + 公爵 + 伯爵 + 男爵 + 骑士。"
                          "Game of Thrones / LOTR / 历史剧通用框架。",
        content_json={
            "feudal_hierarchy": "国王 → 公爵 → 侯爵 → 伯爵 → 子爵 → 男爵 → 骑士 / 平民 / 奴隶",
            "political_dynamics": "王权 vs 贵族会议 / 王后 vs 王太后 / 王储 vs 庶子 / 各贵族家族联姻 / 内斗",
            "key_roles": "首相（内阁掌权）/ 大法官 / 王室骑士团长 / 大主教 / 财政大臣 / 国王密友",
            "famous_examples": "Game of Thrones 七国 / LOTR 刚铎贡多 / 中世纪英国法国 / 《红楼梦》金陵贾府",
            "narrative_uses": "宫斗 / 政治阴谋 / 王位争夺 / 奸臣陷害 / 联姻政治 / 家族秘史",
            "tropes": "私生子复仇 / 失忆王子 / 失踪王女 / 篡位贼 / 老国王临死前选继承人",
            "activation_keywords": ["国王", "王室", "贵族", "公爵", "伯爵", "宫斗", "王位", "权力的游戏"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("西方奇幻王室框架")],
        tags=["西方奇幻", "王室", "宫斗", "贵族"],
    ),
    # 西方奇幻 - 法师塔
    MaterialEntry(
        dimension="factions", genre="西方奇幻",
        slug="faction-mage-tower",
        name="法师塔 / 巫师议会",
        narrative_summary="西方奇幻法师组织。"
                          "高塔 / 神秘庄园 / 学院模式。"
                          "DnD / Harry Potter / Lord of the Rings 系列。",
        content_json={
            "structure": "大法师议会（5-9 位长老）→ 大魔法师 → 资深法师 → 见习法师 → 学徒 → 凡人助手",
            "research_focus": "8 大魔法学派 / 元素亲和 / 召唤研究 / 死灵学（禁忌）/ 时空魔法 / 失落历史考古",
            "famous_examples": "Hogwarts 霍格沃茨 / Strixhaven / 提瑞斯法议会（魔兽）/ 风云塔 / Discworld 隐形大学",
            "internal_politics": "传统派 vs 革新派 / 黑魔法禁令 vs 自由研究 / 院长选举 / 弟子继承",
            "narrative_uses": "学院流必备（主角入学）/ 魔法考试 / 师徒传承 / 法师叛逆 / 禁书事件",
            "common_threats": "禁书泄露 / 巫师变节 / 凡人歧视 / 神级威胁觉醒",
            "activation_keywords": ["法师塔", "巫师议会", "霍格沃茨", "魔法学院", "大法师", "学徒", "禁书"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("西方奇幻法师组织")],
        tags=["西方奇幻", "法师", "学院"],
    ),
    # 都市 - 隐世家族
    MaterialEntry(
        dimension="factions", genre="都市",
        slug="faction-urban-hidden-family",
        name="都市隐世家族（古武世家 / 玄学世家 / 商业巨头）",
        narrative_summary="都市异能流核心组织。"
                          "在都市表象下隐藏的真正主宰。"
                          "千年世家 + 古武传承 + 现代商业。",
        content_json={
            "categories": "古武世家（练武百年）/ 玄学世家（茅山 + 周易）/ 中医世家 / 商业巨头家族 / 政治家族 / 隐居修真家族",
            "hierarchy": "家主 → 太上家主（隐退）→ 嫡系子弟 → 旁系 → 客卿（外聘高手）→ 仆从 / 死士",
            "feuding_dynamics": "家族世仇（百年血仇 + 联姻和解）/ 内斗（嫡系庶系）/ 联手对外（八大家联盟）/ 家族 vs 国家",
            "famous_examples": "《最强弃少》九大家族 / 《我的极品总裁老婆》上海四大家 / 《极品家丁》/ 《唐家三少》系列里的家族",
            "narrative_uses": "主角是被赶出的废物嫡子 → 家族危机时回归 → 主导反击 → 重整家族",
            "common_archetypes": "嫡长子（被偏爱）/ 嫡次子（叛逆）/ 庶子（隐忍）/ 招婿（赘婿流主角）",
            "activation_keywords": ["古武世家", "隐世家族", "九大家族", "豪门", "嫡子", "废物", "家主"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("都市异能家族")],
        tags=["都市", "家族", "古武", "豪门"],
    ),
    # 都市 - 黑帮
    MaterialEntry(
        dimension="factions", genre="都市",
        slug="faction-urban-gang",
        name="都市黑帮（黑龙会 / 三合会 / 帮派）",
        narrative_summary="都市黑帮 / 江湖组织。"
                          "现代都市叙事必备灰色势力。"
                          "类比 mafia + 三合会 + 日系黑社会。",
        content_json={
            "structure": "龙头大哥（话事人）→ 元老 → 红棍（武力）→ 白纸扇（军师）→ 草鞋（情报）→ 一般帮众 → 边缘外围",
            "business_categories": "夜场（夜店 + 酒吧 + KTV）/ 赌场 / 高利贷 / 走私 / 毒品（黑心）/ 演艺圈灰色 / 风月场",
            "geographic_division": "地盘（北区南区）/ 各帮派之间 / 联盟 vs 单打 / 与黑警勾结",
            "famous_examples": "香港四大社团（14K + 新义安 + 和胜和 + 福义兴）/ 日本山口组 / 美国意大利 mafia / 中国东北灰道",
            "narrative_uses": "主角入江湖 = 进入黑道 = 拳头打天下 / 主角废柴 + 被欺负 + 异能觉醒 = 反杀整个帮派",
            "subgenres": "古惑仔型（江湖义气）/ 教父型（家族传承）/ 黑客 + 黑社会型（赛博朋克）",
            "activation_keywords": ["黑帮", "黑社会", "龙头", "红棍", "话事人", "三合会", "帮派"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("都市黑帮模板")],
        tags=["都市", "黑帮", "江湖", "灰色"],
    ),
    # 历史 - 朝廷六部
    MaterialEntry(
        dimension="factions", genre="历史",
        slug="faction-imperial-six-ministries",
        name="历史朝廷六部 + 三省制",
        narrative_summary="中国古代官制框架。"
                          "三省（中书 + 门下 + 尚书）+ 六部（吏 + 户 + 礼 + 兵 + 刑 + 工）。"
                          "历史小说 / 古代权谋必备。",
        content_json={
            "three_provinces": "中书省（决策）/ 门下省（审议）/ 尚书省（执行）/ 唐宋时三权分立",
            "six_ministries": "吏部（人事）/ 户部（财政）/ 礼部（教育祭祀）/ 兵部（军事）/ 刑部（司法）/ 工部（工程）",
            "ranks": "尚书（正二品）/ 侍郎（从二品）/ 郎中（正五品）/ 主事（从七品）/ 各部下分若干司",
            "key_political_dynamics": "首辅 vs 次辅 / 文官 vs 武将 / 党争（清流 vs 浊流）/ 皇帝 vs 内阁",
            "historic_periods": "唐三省六部成熟 / 宋元相承 / 明朝废丞相 / 清朝六部 + 八部（满族特色）",
            "narrative_uses": "古代官场升迁 / 党争政变 / 太子之争 / 主角从七品做到一品 = 经典科举官场流",
            "famous_works": "《琅琊榜》《大明王朝 1566》《大秦帝国》《孤臣孽子》",
            "activation_keywords": ["六部", "尚书", "侍郎", "三省", "首辅", "党争", "科举", "官场"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("六部", ""), llm_note("中国古代官制")],
        tags=["历史", "官制", "古代", "权谋"],
    ),
    # 武侠 - 五大门派
    MaterialEntry(
        dimension="factions", genre="武侠",
        slug="faction-wuxia-five-major-sects",
        name="武林五大门派（少林 + 武当 + 峨嵋 + 华山 + 昆仑）",
        narrative_summary="金庸 / 古龙武侠通用门派体系。"
                          "9 大派 / 5 大派 / 7 大派变体多。"
                          "正派联盟 + 江湖秩序基础。",
        content_json={
            "shaolin": "天下武功出少林 / 七十二绝技 / 易筋经 + 洗髓经 / 少林神僧",
            "wudang": "张三丰祖师 / 太极拳 / 太极剑 / 武当七侠",
            "emei": "郭襄祖师 / 峨嵋九阳功 / 倚天剑 / 灭绝师太",
            "huashan": "气宗 vs 剑宗 / 紫霞神功 / 独孤九剑 / 笑傲江湖核心",
            "kunlun": "西域门派 / 何足道 / 昆仑山高人云集",
            "other_factions": "丐帮（最大民间帮派 / 降龙十八掌）/ 全真教（王重阳 / 道教）/ 古墓派（小龙女）/ 桃花岛（黄药师）",
            "narrative_dynamics": "正派联盟 / 武林大会 / 围剿魔教 / 正魔之争 / 派系内斗（华山气宗 vs 剑宗）",
            "famous_works": "金庸全集 / 古龙系列 / 黄易《大唐双龙》/ 凤歌《沧海》/ 时未寒《明将军》",
            "activation_keywords": ["少林", "武当", "峨嵋", "华山", "昆仑", "丐帮", "全真", "武林", "正派"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("金庸武侠门派")],
        tags=["武侠", "门派", "古风"],
    ),
    # 科幻 - 银河帝国
    MaterialEntry(
        dimension="factions", genre="科幻",
        slug="faction-galactic-empire",
        name="银河帝国 / 联邦",
        narrative_summary="太空歌剧标志组织。"
                          "Star Wars / 银英 / Foundation / Dune 全套用。"
                          "皇帝 + 议会 + 总督 + 舰队的星际版王权。",
        content_json={
            "structure": "皇帝（独裁）→ 摄政会（垂帘）→ 总督（星域）→ 舰队司令 → 星系长官 → 行星总督 → 殖民地",
            "imperial_dynamics": "继承战争 / 总督叛乱 / 边境纷争 / 宗教与皇权 / 议会专权 vs 皇权",
            "famous_examples": "Star Wars 银河帝国 + 第一秩序 / 银英帝国（罗严克拉姆王朝 / 戈登巴姆王朝）/ Foundation 银河帝国 / Dune 帕迪沙皇帝",
            "narrative_uses": "造反流（主角反抗帝国）/ 篡位流（主角夺位）/ 保皇流（守护少年皇帝）",
            "common_themes": "腐败 + 衰落 + 大革命 / 共和派 vs 帝国派 / 民主理想 vs 强人统治",
            "modern_subversions": "《银英》肯定帝国 / 否定民主腐败 = 反共和叙事 / 《Star Wars》肯定共和反帝国",
            "activation_keywords": ["银河帝国", "皇帝", "舰队", "总督", "造反", "Star Wars", "银英"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("太空歌剧帝国")],
        tags=["科幻", "太空歌剧", "帝国"],
    ),
    # 科幻 - 巨型公司 / 科技寡头
    MaterialEntry(
        dimension="factions", genre="科幻",
        slug="faction-megacorp",
        name="赛博朋克 / 反乌托邦巨型公司",
        narrative_summary="赛博朋克 / 反乌托邦核心组织。"
                          "公司比国家还强大。"
                          "Cyberpunk 2077 / Neuromancer / Bladerunner。",
        content_json={
            "categories": "军工巨头（武器研发）/ 信息巨头（监控数据）/ 生物巨头（基因 / 克隆）/ 媒体巨头（思想控制）/ 银行巨头（金融）/ 全栈巨头（垄断一切）",
            "structure": "CEO（神一般权威）→ 董事会 → 各大执行官 → 各事业部部长 → 高级职员 → 一般员工 → 实习生",
            "famous_examples": "Cyberpunk 2077 Arasaka / Militech / Continental / Neuromancer Tessier-Ashpool / Bladerunner Tyrell",
            "private_armies": "公司有自己的军队 / 私军 / 雇佣兵 / 黑客部队 / 暗杀小队",
            "narrative_uses": "主角是底层员工 / 实习生 / 突击队员 / 黑客 / 卧底 / 反抗公司大计划",
            "common_themes": "公司 vs 政府 / 公司 vs 个人 / 数据控制 / 反抗系统 / 内部良心觉醒",
            "activation_keywords": ["公司", "Megacorp", "CEO", "Arasaka", "赛博朋克", "私军", "巨头"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("赛博朋克巨型公司")],
        tags=["科幻", "赛博朋克", "公司"],
    ),
    # 科幻 - 反抗军 / 自由联盟
    MaterialEntry(
        dimension="factions", genre="科幻",
        slug="faction-rebel-alliance",
        name="反抗军 / 自由联盟（义军 / 抵抗组织）",
        narrative_summary="科幻反乌托邦 / 太空歌剧标志。"
                          "对抗帝国 / 公司 / 极权的草根组织。"
                          "Star Wars / Hunger Games / 反乌托邦经典。",
        content_json={
            "structure": "领袖（魅力型）/ 元老会（理念派）/ 军事指挥（实战派）/ 地下网络（情报）/ 各地分部（独立行动）",
            "tactics": "游击战 / 暗杀 / 黑客 / 宣传 / 暴动 / 起义 / 暗中输送资源",
            "famous_examples": "Star Wars 起义军 / Hunger Games 13 区 / Matrix 锡安 / Hellboy + V for Vendetta / 三体降临派",
            "internal_dynamics": "理念分裂（温和 vs 激进）/ 领袖之争 / 间谍内鬼 / 道德困境（牺牲少数救多数）",
            "narrative_uses": "主角加入起义军 → 从小兵到指挥 → 推翻帝国 → 建新秩序 / 经典革命叙事",
            "modern_subversions": "起义军内部腐败 / 反抗成功后变成新独裁 / 主角发现自己是棋子",
            "activation_keywords": ["反抗军", "起义军", "义军", "抵抗", "革命", "Star Wars", "13区", "锡安"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("科幻反抗叙事")],
        tags=["科幻", "反抗", "革命"],
    ),
    # 现代 - 间谍机构
    MaterialEntry(
        dimension="factions", genre="现代",
        slug="faction-spy-agency",
        name="间谍机构（CIA / MI6 / GRU / 八局 / 龙组）",
        narrative_summary="谍战 / 特工流标志组织。"
                          "国家级情报系统 + 行动小组。"
                          "James Bond / Bourne / 谍影重重 + 中国谍战。",
        content_json={
            "real_world_agencies": "美 CIA / 英 MI6 / 俄 GRU + FSB / 以色列 Mossad / 中国国安部 + 龙组（虚构）",
            "structure": "局长 / 副局长 / 行动主任 / 特工 / 教官 / 分析师 / 技术员 / 后勤",
            "training_focus": "格斗术 + 武器 + 心理战 + 多语言 + 黑客 + 伪装 + 拷问 / 反拷问 + 极限生存",
            "famous_examples": "James Bond MI6 / Jason Bourne CIA / Black Widow + 神盾局 / 红色房间 KGB / 中国《潜伏》《风筝》",
            "narrative_uses": "主角是顶尖特工 / 退役复出 / 卧底反卧底 / 双面间谍 / 国家阴谋",
            "common_tropes": "失忆特工 / 退役英雄 / 黑警内鬼 / 美女间谍 / 老对头复仇 / 上级背叛",
            "activation_keywords": ["特工", "CIA", "MI6", "间谍", "James Bond", "国安", "Bourne", "潜伏"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("谍战通用框架")],
        tags=["现代", "谍战", "特工"],
    ),
    # 现代 - 黑客组织
    MaterialEntry(
        dimension="factions", genre="科幻",
        slug="faction-hacker-collective",
        name="黑客组织 / 数字反抗者",
        narrative_summary="赛博朋克 / 现代科技小说核心组织。"
                          "去中心化 / 互联网游民 / 政治激进。"
                          "Anonymous / 4chan / WikiLeaks 现实 + 虚构变体。",
        content_json={
            "real_world": "Anonymous（匿名者）/ LulzSec / Chaos Computer Club / 中国老一代红客联盟 / 朝鲜 Lazarus Group",
            "structure": "无明显领袖 / 内部地位靠技术声誉 / 群组（IRC / Discord / 暗网论坛）/ 行动小组 / 自由人",
            "techniques": "DDoS 攻击 / SQL 注入 / 钓鱼 / 社工 / 0day 漏洞 / 加密货币洗钱 / Tor 匿名 / VPN 跳板",
            "ideology": "信息自由 / 反监控 / 反极权 / 黑客文化 / 隐私至上 / 政府透明",
            "famous_works": "Mr. Robot / Hackers / The Net / Watch Dogs / 攻壳机动队",
            "narrative_uses": "主角是天才黑客 / 加入地下组织 / 反抗科技寡头 / 揭露阴谋 / 与政府对抗",
            "activation_keywords": ["黑客", "Anonymous", "Tor", "0day", "DDoS", "WikiLeaks", "Mr.Robot"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[llm_note("黑客组织框架")],
        tags=["科幻", "现代", "黑客", "赛博朋克"],
    ),
    # 武侠 - 朝廷武装
    MaterialEntry(
        dimension="factions", genre="历史",
        slug="faction-imperial-guards",
        name="朝廷武装（锦衣卫 / 东厂 / 大内 / 御前侍卫）",
        narrative_summary="历史 / 武侠特殊机构。"
                          "皇帝亲军 + 特务机构 + 江湖之外的力量。"
                          "明朝东西厂 + 锦衣卫为标杆。",
        content_json={
            "categories": "锦衣卫（明）/ 东厂 + 西厂（明）/ 大内侍卫（清）/ 御林军（汉唐宋）/ 粘杆处（清雍正）/ 北镇抚司",
            "imperial_secret_police": "皇帝直辖 / 越过六部刑部司法 / 任意逮捕 / 严刑拷问 / 监视百官",
            "famous_historic_figures": "锦衣卫指挥使陆炳 / 东厂魏忠贤 / 雍正粘杆处密探 / 清代血滴子（虚构）",
            "narrative_role": "皇权 vs 江湖 / 主角与侍卫亦敌亦友 / 锦衣卫高手压制江湖大师",
            "famous_works": "《绣春刀》《新龙门客栈》《雍正王朝》《大明王朝 1566》《孤臣孽子》",
            "activation_keywords": ["锦衣卫", "东厂", "西厂", "大内侍卫", "御前", "粘杆处", "血滴子"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("锦衣卫", ""), llm_note("明清特务机构")],
        tags=["历史", "武侠", "朝廷"],
    ),
    # 末世 - 幸存者基地
    MaterialEntry(
        dimension="factions", genre="末世",
        slug="faction-survivor-base",
        name="末世幸存者基地（避难所 / 安全区）",
        narrative_summary="末世题材核心组织。"
                          "末世后人类聚集点。"
                          "军事基地 + 城堡 + 摩天大楼 + 地下室。",
        content_json={
            "categories": "军事基地（残存军方）/ 城市安全区（高墙围）/ 商业大厦改造 / 地下避难所 / 海上船队 / 山地堡垒 / 流动车队",
            "internal_structure": "基地长（最高决策）/ 议会（重大决定）/ 战斗队（战）/ 后勤队（食物）/ 医疗队 / 探索队 / 工程队",
            "resource_management": "食物 / 水 / 弹药 / 药品 / 燃料 / 电力 / 各部门分工",
            "internal_conflicts": "派系之争 / 资源分配 / 强奸 + 抢夺 + 私刑 / 民主 vs 独裁 / 内鬼通敌",
            "famous_works": "Walking Dead 亚历山大 / The Last of Us / Fallout 76 / 末日血战 / 《北京折叠》",
            "narrative_uses": "主角加入基地 / 基地被攻 / 基地内斗 / 主角自建基地 / 多基地联盟",
            "activation_keywords": ["末世", "幸存者", "基地", "安全区", "避难所", "高墙", "丧尸"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[llm_note("末世幸存者框架")],
        tags=["末世", "幸存者", "基地"],
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
