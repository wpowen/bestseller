"""
Batch 56: scene_templates depth for niche genres + real_world_references for emerging niches +
emotion_arcs / dialogue_styles polish.
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
    # ═══════════ SCENE TEMPLATES ═══════════
    MaterialEntry(
        dimension="scene_templates", genre="洪荒",
        slug="honghuang-scene-zixiao-class",
        name="洪荒经典场景：紫霄宫听道",
        narrative_summary="洪荒文最高规格场景：鸿钧道祖在紫霄宫讲道 / 三千客 / 紫气分予三清 / "
                          "时间凝固 / 是修真者千年修行也未必能到的至高际遇。",
        content_json={
            "setup": {
                "招集": "鸿钧讲道 / 有缘者自至 / 无需邀请",
                "环境": "紫霄宫 / 紫气环绕 / 蒲团三千 / 明珠生辉",
                "出席": "鸿钧道祖 / 三清 / 后来的接引准提 / 三千客",
                "心理": "众人屏息 / 不敢造次 / 紫气拂面",
            },
            "scene_beats": {
                "讲道开始": "鸿钧端坐 / 声若洪钟 / 字字珠玑",
                "时间凝固": "外界千年 / 此处一日",
                "悟道分层": "前排领悟最深 / 后排略懂",
                "紫气出现": "讲道结束 / 紫气凝聚 / 三朵入三清",
                "众人反应": "三清谢恩 / 准圣懊悔 / 众散归",
            },
            "key_moments": {
                "主角靠前": "若主角在前排 / 可获顶级感悟",
                "主角靠后": "听了一言半句 / 但已脱凡",
                "主角缺席": "未来某次再见 / 已晚",
                "主角拒绝": "不愿听道 / 自创新道 / 引人侧目",
            },
            "emotional_layers": {
                "敬畏": "鸿钧之威让人不敢直视",
                "贪婪": "争夺紫气的暗流",
                "感激": "得道者落泪",
                "孤寂": "悟道者发现仍未悟透",
            },
            "narrative_uses": {
                "确立设定": "树立鸿钧/紫气的至高性",
                "划分阵营": "三清得紫气=未来分教",
                "主角身份": "在此能否露面=身份揭示",
                "悟道伏笔": "讲道内容多年后回响",
            },
            "scene_writing_notes": {
                "镜头": "由远及近 / 紫气作引线",
                "音效": "钟鸣 / 风过 / 鸿钧之声",
                "色彩": "紫为主 / 金为辅",
                "节奏": "缓慢 / 凝重 / 时间错乱感",
            },
            "activation_keywords": ["紫霄宫", "鸿钧", "讲道", "紫气", "三清", "悟道"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("封神演义", "鸿钧讲道"), llm_note("洪荒紫霄宫场景")],
        tags=["场景", "洪荒", "紫霄宫", "讲道"],
    ),

    MaterialEntry(
        dimension="scene_templates", genre="女尊",
        slug="nuzun-scene-emperor-court",
        name="女尊经典场景：女皇早朝",
        narrative_summary="女尊文标志场景：凤华殿早朝 / 女官两班 / 女皇升座 / 朝议政事 / "
                          "皇夫不参与；这是展现女尊政治结构最直接的场景。",
        content_json={
            "setup": {
                "时间": "卯时三刻 / 天蒙蒙亮",
                "地点": "凤华殿 / 朱红抱柱 / 黄琉璃",
                "人物": "女皇升座 / 女文左/女武右 / 三品以上有座",
                "皇夫位置": "不参与 / 在偏殿候召",
            },
            "scene_beats": {
                "上朝": "钟鸣三响 / 群臣入殿 / 朝服整齐",
                "升座": "女皇从内殿至 / 太监高呼『女皇驾到』",
                "朝议": "依次启奏 / 女皇下旨",
                "斗争": "派系暗流 / 表面客气 / 内心交锋",
                "退朝": "女皇退场 / 群臣相互攀谈 / 后续运作",
            },
            "key_moments": {
                "首次面圣": "新女官第一次上朝 / 紧张到出汗",
                "顶撞": "女武将敢反对女皇政策",
                "弹劾": "几人合谋弹劾对手",
                "急报": "前方军报突至 / 朝议中断",
                "宣读圣旨": "册封新皇夫 / 全朝震动",
            },
            "atmosphere": {
                "肃穆": "黎明微光 / 朝服飒飒",
                "紧张": "派系暗中较劲",
                "庄严": "女皇之威 / 群臣战栗",
                "暗流": "真正交锋在退朝后",
            },
            "narrative_uses": {
                "信息释放": "重要朝政在此公布",
                "派系划分": "谁站谁的队 / 一目了然",
                "权力展示": "女皇的真实威严 vs 表面",
                "情感伏笔": "皇夫为何不在场",
            },
            "common_dialogue": {
                "启奏": "「臣启陛下」「臣有本要奏」",
                "下旨": "「准奏」「依卿所言」「另议」「降罪」",
                "辩论": "「陛下三思」「臣以为不可」「臣斗胆」",
            },
            "activation_keywords": ["凤华殿", "早朝", "女皇", "启奏", "派系", "退朝"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[llm_note("女尊宫廷场景")],
        tags=["场景", "女尊", "朝堂", "早朝"],
    ),

    MaterialEntry(
        dimension="scene_templates", genre="无限流",
        slug="wuxian-scene-trial-entry",
        name="无限流经典场景：进入新世界",
        narrative_summary="无限流标志场景：主神空间召唤 / 玩家被强制进入新世界 / "
                          "环境陌生 / 第一关任务展开 / 充满恐惧和未知。",
        content_json={
            "setup": {
                "前置": "主神空间警报 / 玩家收到任务",
                "倒计时": "60秒进入 / 来不及准备",
                "传送": "白光闪过 / 转入新世界",
                "落地": "在某个陌生场景 / 完全没有信息",
            },
            "scene_beats": {
                "传送瞬间": "白光 / 失重感 / 短暂窒息",
                "落地": "硬着陆 / 周围环境扑入感官",
                "信息确认": "主神简短任务说明",
                "环境侦察": "查看自己装备 / 周围地形",
                "首次遇敌": "5-10分钟内必有威胁",
                "队友碰头": "团队成员陆续出现 / 互相判断",
            },
            "common_world_types": {
                "电影世界": "异形号宇宙飞船 / 丧尸蔓延的购物中心",
                "小说世界": "三国战场 / 西游中某地",
                "游戏世界": "生化危机蜂巢 / 求生路高速公路",
                "原创": "纯抽象 / 主神原创",
            },
            "key_moments": {
                "认识队友": "评估彼此实力 / 是否可靠",
                "意识到永久死亡": "看到队友被杀 / 心态崩",
                "首次任务进展": "找到关键NPC / 触发主线",
                "意外发现": "世界规则与表面不同",
                "决定回归": "完成主线 / 等待传送",
            },
            "psychological_layers": {
                "恐惧": "未知的世界 / 死亡威胁",
                "怀疑": "队友是否会出卖",
                "适应": "快速学习生存",
                "决心": "为了回家 / 必须活下来",
            },
            "scene_writing_notes": {
                "节奏": "极快 / 不允许拖沓",
                "信息密度": "必须传达 5W1H 给读者",
                "感官": "重视味觉/嗅觉/听觉细节",
                "对话": "队友对话快速判断",
            },
            "activation_keywords": ["主神", "传送", "新世界", "永久死亡", "队友", "任务"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("无限恐怖参考"), llm_note("无限流入世界场景")],
        tags=["场景", "无限流", "传送", "新世界"],
    ),

    MaterialEntry(
        dimension="scene_templates", genre="女尊",
        slug="nuzun-scene-marriage-procession",
        name="女尊经典场景：女子娶夫迎亲",
        narrative_summary="女尊文情感线高潮：女子骑高头大马 / 红妆华服 / 迎接郎君 / "
                          "男方红嫁衣 / 哭嫁 / 这是性别反转最典型的展现场合。",
        content_json={
            "setup": {
                "新娘（实为新郎）": "男方在家中梳妆 / 红嫁衣 / 凤冠霞帔 / 流泪",
                "新郎（实为新娘）": "女方骑高马 / 黑甲红披风 / 持迎亲剑",
                "迎亲队伍": "女方家将 / 红绸鼓乐 / 蜂拥而至",
                "送嫁": "男方家人哭嫁 / 慈母教郎",
            },
            "scene_beats": {
                "起轿": "男方含泪上花轿 / 母亲嘱咐",
                "拦门": "男方好友拦轿要红包 / 调笑女方",
                "迎接": "女方到门 / 跨门槛 / 接男上轿",
                "拜堂": "一拜天地 / 二拜高堂 / 夫妻对拜（女在前）",
                "入洞房": "进新房 / 小别胜新婚",
                "宴席": "贺客如云 / 女方频频劝酒",
            },
            "key_moments": {
                "新郎悔嫁": "男方临走想反悔",
                "迎亲对手戏": "另一位女子也想娶 / 半路抢婚",
                "母女告别": "婆媳交班 / 哭别老母",
                "拜堂出意外": "客人闹场 / 揭发某事",
                "入洞房窃窃私语": "情感最浓时刻 / 真心揭露",
            },
            "emotional_layers": {
                "新娘（男）": "依依不舍 / 兴奋 / 害怕未知",
                "新郎（女）": "兴奋 / 责任感 / 决心保护他",
                "送嫁母": "心痛 + 祝福",
                "围观": "议论 + 羡慕",
            },
            "cultural_inversion_details": {
                "服色": "男方红嫁衣 / 女方黑披风（武）或紫袍（文）",
                "动作": "女方挥剑斩花球 / 男方撑伞遮羞",
                "语言": "女方说『我顾长缨今日娶你为正君』 / 男方答『妾身愿守一生』",
            },
            "narrative_uses": {
                "情感顶点": "全文情感线的高潮",
                "世界观展示": "性别反转最直观",
                "矛盾爆发": "婚礼上的揭发",
                "圆满承诺": "至此白头偕老",
            },
            "activation_keywords": ["迎亲", "凤冠霞帔", "拜堂", "入洞房", "新郎", "新娘", "迎亲剑"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[llm_note("女尊婚礼场景"), wiki("古代婚礼", "传统迎亲流程")],
        tags=["场景", "女尊", "婚礼", "迎亲"],
    ),

    # ═══════════ REAL WORLD REFERENCES ═══════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="real-ref-historical-revolutions",
        name="真实参考：历史革命与社会变革典型",
        narrative_summary="历史上的重大革命与变革（法国大革命/俄国十月革命/中国辛亥/明治维新）/ "
                          "为虚构小说提供『新旧交替』『理想破灭』『血与汗换来的进步』模式。",
        content_json={
            "french_revolution_1789": {
                "时间": "1789-1799",
                "起因": "三级会议/王权专制/启蒙思想",
                "关键事件": "巴士底狱攻陷 / 雅各宾派恐怖 / 拿破仑崛起",
                "经典模式": "理想 → 暴力 → 反弹 → 妥协",
                "narrative_lessons": "革命常吃自己的孩子 / 理想主义者最先死",
            },
            "russian_revolution_1917": {
                "时间": "1917 二月+十月",
                "起因": "一战疲惫 / 沙皇专制 / 工人运动",
                "关键事件": "二月革命推沙皇 / 十月革命布尔什维克",
                "经典模式": "渐进失败 → 激进胜利 → 内战",
                "narrative_lessons": "理想路线分裂 / 流血代价沉重",
            },
            "xinhai_revolution_1911": {
                "时间": "1911-1912",
                "起因": "清末腐败 / 民族主义 / 同盟会",
                "关键事件": "武昌起义 / 各省独立 / 清帝退位",
                "经典模式": "推翻 → 共和 → 军阀混战",
                "narrative_lessons": "推翻容易建设难 / 革命未竟",
            },
            "meiji_restoration_1868": {
                "时间": "1868-1889",
                "起因": "黑船事件 / 倒幕派",
                "关键事件": "戊辰战争 / 废藩置县 / 文明开化",
                "经典模式": "外压 → 改革 → 现代化",
                "narrative_lessons": "保留传统 + 拥抱现代 / 但代价为军国",
            },
            "narrative_application": {
                "新旧交替": "用历史变革节奏写小说改朝换代",
                "理想者悲剧": "理想者多在革命中死",
                "底层视角": "通过普通人看大时代",
                "代际冲突": "祖辈拥护旧 / 子辈倡新",
                "胜利之疲惫": "胜利后的倦怠和分裂",
            },
            "scenes_to_use": [
                "起义前的暗中聚会",
                "第一面旗帜飘起的瞬间",
                "革命领袖在群众前演讲",
                "革命成功后第一夜的酒宴",
                "失败后逃亡 / 流亡他国",
                "多年后回望 / 理想是否实现",
            ],
            "activation_keywords": ["革命", "变革", "理想", "推翻", "代价", "历史"],
        },
        source_type="llm_synth", confidence=0.88,
        source_citations=[wiki("法国大革命", "1789-1799"), wiki("十月革命", "1917"), wiki("辛亥革命", "1911"), wiki("明治维新", "1868")],
        tags=["真实参考", "历史", "革命", "通用"],
    ),

    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="real-ref-medical-disease-history",
        name="真实参考：人类重大疾病与医学历史",
        narrative_summary="历史上的重大疾病与医学突破（黑死病/天花/SARS/抗生素/疫苗）/ "
                          "为末世/医疗/科幻文提供真实的疾病模式与人类反应。",
        content_json={
            "black_death_14thc": {
                "时间": "1346-1353",
                "致病": "鼠疫杆菌 / 跳蚤传播",
                "死亡率": "欧洲1/3至1/2人口",
                "社会影响": "封建制度松动 / 教会权威动摇 / 文艺复兴种子",
                "narrative_lessons": "瘟疫=社会大重组",
            },
            "smallpox": {
                "致病": "天花病毒",
                "死亡率": "30%以上 / 幸存者留疤",
                "历史": "古埃及木乃伊已有",
                "灭绝": "1980 WHO宣布灭绝（人类首次灭绝病毒）",
                "narrative_lessons": "可以战胜疾病的人类决心",
            },
            "spanish_flu_1918": {
                "时间": "1918-1920",
                "致病": "H1N1流感",
                "死亡": "5000万-1亿（一战之外）",
                "特点": "青年死亡率高 / 与一战交叠",
                "narrative_lessons": "现代世界第一次全球流行",
            },
            "sars_2003": {
                "时间": "2002-2003",
                "致病": "SARS-CoV",
                "死亡率": "10%",
                "传播": "亚洲 / 加拿大",
                "narrative_lessons": "现代公卫体系初次大考",
            },
            "covid19_2019": {
                "时间": "2019-",
                "致病": "SARS-CoV-2",
                "影响": "全球封锁 / 经济震荡 / 政治分裂",
                "narrative_lessons": "现代社会的脆弱性 / 信息战与疫苗争议",
            },
            "antibiotics_revolution": {
                "1928": "Fleming发现青霉素",
                "1940s": "大规模生产",
                "影响": "感染死亡率骤降 / 现代手术成可能",
                "narrative_lessons": "抗生素耐药性是新威胁",
            },
            "vaccine_history": {
                "1796": "Jenner天花牛痘",
                "1885": "Pasteur狂犬病疫苗",
                "20世纪": "脊髓灰质炎/麻疹疫苗",
                "21世纪": "mRNA技术",
                "narrative_lessons": "疫苗反对者的存在",
            },
            "narrative_application": {
                "疾病蔓延": "末世/灾难文真实感",
                "医患关系": "医疗文人物动机",
                "公卫决策": "封锁vs自由 / 真实困境",
                "科研突破": "为科幻文提供真实节奏",
                "失败案例": "也有失败的医学决策",
            },
            "activation_keywords": ["瘟疫", "疫苗", "黑死病", "SARS", "Covid", "抗生素", "天花"],
        },
        source_type="llm_synth", confidence=0.88,
        source_citations=[wiki("黑死病", "中世纪鼠疫"), wiki("天花", "病毒灭绝"), wiki("SARS", "2003流行"), wiki("青霉素", "Fleming")],
        tags=["真实参考", "医学", "疾病", "通用"],
    ),

    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="real-ref-economic-crises",
        name="真实参考：重大经济危机与金融事件",
        narrative_summary="历史经济危机模式（1929大萧条/1973石油危机/1997亚洲金融/2008金融海啸/2020疫情萧条）/ "
                          "为商战/末世/职场文提供真实经济动荡参考。",
        content_json={
            "great_depression_1929": {
                "起因": "股市泡沫 / 银行倒闭 / 紧缩政策",
                "持续": "1929-1939",
                "影响": "失业率25% / 罗斯福新政",
                "narrative_lessons": "经济危机=社会大动荡",
            },
            "oil_crisis_1973": {
                "起因": "OPEC禁运",
                "影响": "通胀+失业（滞胀）/ 西方经济模式重组",
                "narrative_lessons": "能源是命脉",
            },
            "asian_financial_1997": {
                "起因": "热钱炒作 / 索罗斯做空泰铢",
                "影响": "泰国/韩国/印尼破产 / IMF介入",
                "narrative_lessons": "新兴市场脆弱",
            },
            "global_financial_crisis_2008": {
                "起因": "次贷 / CDO衍生 / 雷曼倒闭",
                "影响": "全球衰退 / QE开启",
                "narrative_lessons": "金融衍生品复杂性",
            },
            "covid_recession_2020": {
                "起因": "疫情封锁",
                "影响": "失业+财政刺激 / 不平等加剧",
                "narrative_lessons": "瘟疫直接冲击经济",
            },
            "financial_concepts_for_fiction": {
                "做空": "押注下跌 / 高风险高收益",
                "杠杆": "借钱投资放大收益",
                "对冲基金": "灵活+激进 / 大动作",
                "央行": "利率/QE 决定全市场情绪",
                "热钱": "投机性资金 / 一周可流入流出",
                "做局": "联手砸盘",
            },
            "narrative_application": {
                "商战文": "用真实危机模式写斗争",
                "都市文": "主角失业/破产",
                "末世文": "经济崩溃引发末日",
                "重生文": "回到危机前夕预知机会",
                "穿越文": "用现代知识赚古代钱",
            },
            "scenes_to_use": [
                "股市暴跌瞬间 / 操盘手脸色",
                "银行挤兑 / 群众排队",
                "央行紧急会议",
                "破产清算 / 高管自杀",
                "敌对基金做空决战",
                "底层失业者抗议",
            ],
            "activation_keywords": ["大萧条", "金融危机", "石油危机", "次贷", "做空", "央行"],
        },
        source_type="llm_synth", confidence=0.88,
        source_citations=[wiki("大萧条", "1929"), wiki("亚洲金融危机", "1997"), wiki("2008金融海啸"), wiki("索罗斯", "做空案例")],
        tags=["真实参考", "经济", "金融", "商战"],
    ),

    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="real-ref-physics-cosmology",
        name="真实参考：物理学与宇宙学前沿",
        narrative_summary="现代物理学概念（量子力学/相对论/弦论/多重宇宙/暗物质）/ "
                          "为科幻/玄幻文提供严肃科学背景；不是软科幻乱写而是真知识。",
        content_json={
            "quantum_mechanics": {
                "核心": "波粒二象性 / 不确定性原理 / 量子纠缠",
                "实验": "双缝实验 / 薛定谔的猫 / EPR悖论",
                "narrative_uses": "平行宇宙 / 量子计算 / 心灵感应理论化",
            },
            "general_relativity": {
                "核心": "引力=时空弯曲 / 光速不变",
                "结论": "时间膨胀 / 黑洞 / 引力波",
                "narrative_uses": "时间旅行 / 太空航行 / 重力武器",
            },
            "string_theory": {
                "核心": "万物是10-26维振动的弦",
                "状态": "未被证实但数学优雅",
                "narrative_uses": "高维空间 / 跨维度旅行",
            },
            "multiverse": {
                "类型": "Everett多世界 / 宇宙泡沫 / 数学结构所有",
                "状态": "假说",
                "narrative_uses": "穿越文 / 平行世界 / 选择不同未来",
            },
            "dark_matter_energy": {
                "暗物质": "占总质量85% / 不发光",
                "暗能量": "占68% / 加速宇宙膨胀",
                "narrative_uses": "未知力量 / 阴影世界 / 反派能量",
            },
            "cosmology_timeline": {
                "138亿年前": "大爆炸",
                "10^-43秒": "普朗克纪元",
                "宇宙暴涨": "10^-36到10^-32秒",
                "宇宙微波背景": "38万年时形成",
                "结构形成": "首颗恒星 / 星系",
                "未来": "热寂 / 大撕裂 / 大坍缩",
            },
            "famous_thought_experiments": {
                "薛定谔的猫": "量子叠加",
                "麦克斯韦妖": "热力学第二定律",
                "光速火车": "相对论时间膨胀",
                "孪生悖论": "两兄弟时差",
                "祖父悖论": "时间旅行矛盾",
            },
            "narrative_application": {
                "科幻硬核": "用真实物理写飞船航行",
                "玄幻借用": "把『道』比作弦的振动",
                "时间穿越": "基于相对论 / 不是穿越就行",
                "高维": "用弦论解释修真境界",
                "宇宙观": "给小说『天』『道』一个真实根",
            },
            "activation_keywords": ["量子", "相对论", "弦论", "多重宇宙", "暗物质", "黑洞", "时空"],
        },
        source_type="llm_synth", confidence=0.90,
        source_citations=[wiki("量子力学"), wiki("广义相对论"), wiki("弦理论"), wiki("多重宇宙"), wiki("暗物质")],
        tags=["真实参考", "物理", "宇宙", "科幻"],
    ),

    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="real-ref-psychology-personality",
        name="真实参考：心理学与人格研究",
        narrative_summary="心理学经典理论（弗洛伊德/荣格/马斯洛/MBTI/大五人格）/ "
                          "为角色塑造、心理描写、人物关系提供科学基础。",
        content_json={
            "freud_psychoanalysis": {
                "核心": "本我/自我/超我 / 潜意识 / 性驱力",
                "防御机制": "压抑/投射/合理化/升华",
                "narrative_uses": "角色潜意识动机 / 童年阴影",
            },
            "jung_analytical": {
                "原型": "阴影/阿尼玛/智者/英雄",
                "集体潜意识": "全人类共享的原型",
                "narrative_uses": "原型角色塑造 / 神话回响",
            },
            "maslow_hierarchy": {
                "层次": "生理→安全→爱归属→尊重→自我实现",
                "narrative_uses": "角色目标动机层次",
            },
            "mbti_16types": {
                "维度": "E/I外向内向 / S/N直觉感觉 / T/F思考情感 / J/P判断知觉",
                "争议": "科学性受质疑 / 但流行",
                "narrative_uses": "速写人物大致风格",
            },
            "big_five_personality": {
                "维度": "开放性/尽责性/外向性/宜人性/神经质",
                "科学度": "高 / 学界主流",
                "narrative_uses": "更精准刻画人物",
            },
            "attachment_theory": {
                "类型": "安全型/焦虑型/回避型/恐惧型",
                "形成": "童年与照顾者关系",
                "narrative_uses": "情感关系 / 恋爱模式",
            },
            "trauma_psychology": {
                "PTSD": "重复闪回 / 麻木 / 警觉",
                "复杂性创伤": "长期虐待 / 多种症状",
                "creative_recovery": "艺术疗愈 / 叙事疗法",
                "narrative_uses": "战争/家暴幸存者人物",
            },
            "cognitive_biases": {
                "确认偏误": "只看自己想看",
                "幸存者偏差": "只看赢家",
                "锚定效应": "首个数字影响判断",
                "Dunning-Kruger": "无知者更自信",
                "narrative_uses": "反派思维 / 人物决策",
            },
            "moral_psychology": {
                "Kohlberg六阶段": "前习俗/习俗/后习俗",
                "道德困境": "电车难题",
                "narrative_uses": "灰色道德人物 / 选择困境",
            },
            "narrative_application": {
                "人物塑造": "用大五写真实角色",
                "反派理解": "用心理学解释为何坏",
                "情感线": "用依恋理论写恋爱",
                "成长弧": "用马斯洛写自我实现",
                "对话": "MBTI差异制造冲突",
            },
            "activation_keywords": ["弗洛伊德", "荣格", "MBTI", "大五人格", "依恋", "PTSD", "原型"],
        },
        source_type="llm_synth", confidence=0.88,
        source_citations=[wiki("弗洛伊德"), wiki("卡尔·荣格"), wiki("马斯洛需求层次"), wiki("MBTI"), wiki("大五人格")],
        tags=["真实参考", "心理学", "人格", "通用"],
    ),

    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="real-ref-warfare-tactics-history",
        name="真实参考：战争史与军事战术",
        narrative_summary="历史战术（孙子兵法/汉尼拔/拿破仑/二战/现代战争）+ 现代军事概念 / "
                          "为军事文/历史文/玄幻战争场面提供真实战术深度。",
        content_json={
            "ancient_chinese": {
                "孙子兵法": "上兵伐谋 / 知己知彼 / 出奇制胜",
                "三十六计": "瞒天过海/借刀杀人/无中生有/以逸待劳/调虎离山",
                "经典战例": "围魏救赵 / 长平之战 / 赤壁之战",
            },
            "ancient_western": {
                "希腊密集阵": "重装步兵+长矛",
                "汉尼拔包抄": "坎尼会战 / 双翼包抄",
                "凯撒": "高卢战争 / 围三阙一",
                "亚历山大": "迦太基/印度远征 / 闪电战雏形",
            },
            "medieval": {
                "蒙古骑射": "速度+射术 / 心理战",
                "十字军": "宗教动员",
                "诺曼底": "黑斯廷斯1066 / 弓箭+骑兵",
            },
            "modern_napoleonic": {
                "拿破仑炮兵": "集中火力",
                "纵队战术": "突破点击穿",
                "克劳塞维茨": "战争论 / 政治延伸",
            },
            "industrial_warfare": {
                "美国南北": "铁甲舰/铁路/电报",
                "一战堑壕": "马克沁机枪 / 毒气 / 坦克",
                "二战闪电战": "装甲+空中支援 / 突击",
                "二战中国": "持久战 / 游击战",
            },
            "modern_warfare": {
                "信息战": "电子战/赛博战",
                "无人化": "无人机/AI",
                "心理战": "宣传/认知战",
                "混合战争": "正规+非正规",
                "外科手术打击": "精准+有限目标",
            },
            "tactical_principles": {
                "集中": "集中优势兵力",
                "机动": "速度=力量",
                "突袭": "时空+心理意外",
                "防御": "纵深+预备队",
                "情报": "知敌+保己秘密",
                "后勤": "战争最重要",
            },
            "narrative_application": {
                "玄幻战场": "修真斗战用真实战术",
                "历史文": "战役还原",
                "末世文": "幸存者战争",
                "科幻文": "未来战争形式",
                "反战主题": "战术之外的人性",
            },
            "activation_keywords": ["孙子兵法", "三十六计", "汉尼拔", "拿破仑", "闪电战", "信息战"],
        },
        source_type="llm_synth", confidence=0.90,
        source_citations=[wiki("孙子兵法"), wiki("三十六计"), wiki("汉尼拔"), wiki("拿破仑战争"), wiki("克劳塞维茨")],
        tags=["真实参考", "军事", "战术", "战争"],
    ),
]


async def main() -> None:
    inserted = 0
    errors: list[tuple[str, str]] = []
    by_genre: dict[str, int] = {}
    by_dim: dict[str, int] = {}

    print(f"Seeding {len(ENTRIES)} entries to material_library (batch 56)...")
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
