"""
Batch 11: Western literature & world religions & history & science -
Universal knowledge activation layer for cross-genre LLM activation.
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
    # ═══════════ WESTERN LITERATURE THEORY ═══════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-greek-tragedy",
        name="希腊悲剧叙事激活层",
        narrative_summary="古希腊悲剧的核心叙事原理：英雄因『致命缺陷』（hamartia）走向必然失败，"
                          "经历『发现』（anagnorisis）和『反转』（peripeteia），引发观众的『净化』（catharsis）。"
                          "这是西方戏剧的根基，无数现代作品仍在使用其结构。",
        content_json={
            "core_concepts": {
                "hamartia": "致命缺陷——英雄性格中导致毁灭的关键弱点",
                "hubris": "傲慢——挑战神/天命的过度自信",
                "anagnorisis": "发现——主角认识到真相的瞬间",
                "peripeteia": "命运反转——情势从顺利急转",
                "catharsis": "情感净化——观众通过共情得到释放",
            },
            "classic_plays": ["俄狄浦斯王", "美狄亚", "安提戈涅", "阿伽门农"],
            "modern_applications": "几乎所有悲剧型角色弧线（教父/绝命毒师/麦克白）",
            "Chinese_parallels": "项羽/红楼梦贾宝玉 — 致命缺陷 + 命运不可逆",
            "activation_keywords": ["希腊悲剧", "致命缺陷", "命运反转", "净化", "傲慢"],
        },
        source_type="llm_synth", confidence=0.80,
        source_citations=[wiki("希腊悲剧", ""), wiki("亚里士多德诗学", ""), llm_note("悲剧理论")],
        tags=["通用", "悲剧", "希腊", "叙事理论"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-shakespeare-archetypes",
        name="莎士比亚原型角色激活",
        narrative_summary="莎士比亚塑造的角色原型已成为西方文学的基本人物词典："
                          "犹豫的王子（哈姆雷特）、嫉妒的将军（奥赛罗）、野心的王（麦克白）、"
                          "聪明的傻子（李尔王中的弄人）、无法被爱的公主（克娄巴特拉）。",
        content_json={
            "archetypes": {
                "犹豫的王子": "知行分离的知识分子（哈姆雷特）→ 现代变体：知道真相却无法行动者",
                "嫉妒的将军": "强者被自己最大的弱点击溃（奥赛罗）→ 现代变体：被操纵的英雄",
                "野心的王": "为得到一切而失去自我（麦克白）→ 现代变体：黑化的成功者",
                "聪明的傻子": "用幽默说真话的边缘人 → 现代变体：吐槽担当",
                "傲慢的父亲": "用爱伤害孩子的家长（李尔王）→ 现代变体：错误期待的父辈",
            },
            "writing_techniques": "独白展示内心 / 双关语暗示真相 / 悲喜剧并置",
            "modern_inheritors": "几乎所有当代复杂角色都有莎翁影子",
            "activation_keywords": ["哈姆雷特", "奥赛罗", "麦克白", "李尔王", "莎翁人物"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("莎士比亚", ""), llm_note("莎翁原型分析")],
        tags=["通用", "莎士比亚", "原型", "西方文学"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-russian-novel",
        name="俄国小说心理深度激活",
        narrative_summary="陀思妥耶夫斯基/托尔斯泰确立的心理写作传统：通过主角内心独白挖掘人性最深处的矛盾，"
                          "罪与罚、信仰与怀疑、贵族与平民的张力。这种叙事密度仍是高水准心理描写的标杆。",
        content_json={
            "core_themes": ["罪与赎罪", "信仰与怀疑", "贵族良心", "底层尊严", "知识分子的痛苦"],
            "core_authors": {
                "陀思妥耶夫斯基": "罪与罚/卡拉马佐夫兄弟 — 人性最黑暗的辩证",
                "托尔斯泰": "战争与和平/安娜卡列尼娜 — 历史洪流中的个人选择",
                "契诃夫": "短篇 — 沉默的痛苦/未说出的真话",
            },
            "writing_techniques": "长段内心独白 / 多重视角 / 哲学辩论式对话",
            "Chinese_parallel_thinking": "鲁迅的『国民性』分析与陀氏的『俄罗斯灵魂』有共鸣",
            "activation_keywords": ["陀思妥耶夫斯基", "托尔斯泰", "俄国小说", "心理深度", "罪与罚"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("俄罗斯文学", ""), wiki("陀思妥耶夫斯基", ""), llm_note("俄国文学传统")],
        tags=["通用", "俄国文学", "心理", "文学传统"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-magical-realism",
        name="魔幻现实主义激活层",
        narrative_summary="加西亚·马尔克斯确立的魔幻现实主义传统：将奇幻元素以日常笔法叙述，"
                          "让超自然与现实无缝并存，通过这种『不合理的真实』揭示历史和文化的深层结构。",
        content_json={
            "core_principle": "奇幻现象用平静、客观的笔调叙述",
            "key_authors": ["加西亚·马尔克斯（《百年孤独》）", "博尔赫斯", "阿连德", "卡彭铁尔"],
            "narrative_techniques": [
                "时间循环与多代家族",
                "鬼魂/亡灵与生者共处",
                "历史隐喻通过奇幻表达",
                "数字/血缘/命名的神秘联系",
            ],
            "Chinese_applications": "莫言（《红高粱》）/ 余华 / 残雪 都吸取此传统",
            "适用题材": "灵异 / 历史 / 心理惊悚 都可借鉴",
            "activation_keywords": ["魔幻现实主义", "百年孤独", "马尔克斯", "鬼魂", "拉美文学"],
        },
        source_type="llm_synth", confidence=0.77,
        source_citations=[wiki("魔幻现实主义", ""), wiki("加夫列尔·加西亚·马尔克斯", ""), llm_note("魔幻现实主义")],
        tags=["通用", "魔幻现实主义", "拉美", "叙事传统"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-noir-tradition",
        name="黑色电影/黑色小说传统",
        narrative_summary="20世纪美国黑色侦探小说传统（雷蒙德·钱德勒、达希尔·哈米特）"
                          "及其衍生的黑色电影美学：硬汉侦探/腐败城市/femme fatale/无解的道德灰色地带。",
        content_json={
            "core_elements": [
                "受过创伤的硬汉主角",
                "腐败到底的城市生态",
                "致命女性（femme fatale）",
                "雨夜/霓虹/爵士乐氛围",
                "没有真正胜利的结局",
            ],
            "key_authors": ["雷蒙德·钱德勒", "达希尔·哈米特", "詹姆斯·埃尔罗伊"],
            "filmic_inheritors": "教父/赌城风云/真探/赛博朋克",
            "中国_compatible_genres": "民国/都市悬疑/赛博朋克",
            "activation_keywords": ["黑色电影", "硬汉派", "马洛", "致命女性", "霓虹雨夜"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[wiki("黑色电影", ""), wiki("硬汉派侦探小说", ""), llm_note("黑色传统")],
        tags=["通用", "黑色电影", "硬汉", "美学传统"],
    ),

    # ═══════════ WORLD RELIGIONS ═══════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-christianity-narrative",
        name="基督教叙事符号库",
        narrative_summary="基督教的核心叙事元素：原罪—救赎—复活的弧线，"
                          "圣母与圣子的母性原型，犹大式背叛，朝圣者的旅途。这套符号系统已渗入西方所有叙事，"
                          "对中文创作者了解后能丰富自己的叙事词汇。",
        content_json={
            "core_arc": "原罪→堕落→救主→受难→复活→升天",
            "key_archetypes": {
                "基督形象": "无辜受难者，为众人牺牲（变体：超级英雄/反英雄）",
                "犹大形象": "亲密之人的背叛（变体：朋友的出卖）",
                "圣母形象": "受难者母亲的悲悯（变体：所有母亲的痛苦）",
                "玛丽抹大拉": "被污名化但被救赎的女性",
                "彼拉多": "明知不公但选择洗手的当权者",
            },
            "narrative_devices": "最后晚餐 / 客西马尼园祷告 / 三次否认 / 复活 / 升天",
            "Chinese_uses": "《活着》《白鹿原》中可见受难/救赎结构",
            "activation_keywords": ["原罪", "救赎", "受难", "复活", "犹大", "圣母"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("基督教", ""), wiki("圣经", ""), llm_note("基督教叙事符号")],
        tags=["通用", "宗教", "基督教", "叙事符号"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-norse-mythology",
        name="北欧神话激活层",
        narrative_summary="北欧神话的核心叙事：诸神也会死的世界观（诸神黄昏/Ragnarök）；"
                          "巨人/精灵/侏儒/亡灵的多元种族；奥丁/索尔/洛基的复杂神格。是西方奇幻的主要源泉之一。",
        content_json={
            "key_gods": {
                "奥丁": "智慧之神，为求知舍弃一目",
                "索尔": "雷神，力量但鲁莽",
                "洛基": "诡诈之神，亦正亦邪——现代『反英雄』原型",
                "弗蕾娅": "爱与战争女神",
            },
            "key_concepts": {
                "Yggdrasil": "世界树连接九个世界",
                "Ragnarök": "诸神黄昏——连神都会死的末日观",
                "Valhalla": "战死英雄的天堂",
                "Norns": "命运三女神织造命运",
            },
            "modern_inheritors": "《指环王》/ 漫威雷神 / 各种欧美奇幻设定",
            "Chinese_parallels": "《山海经》的神话结构有相似之处",
            "activation_keywords": ["奥丁", "索尔", "洛基", "诸神黄昏", "世界树", "瓦尔哈拉"],
        },
        source_type="llm_synth", confidence=0.77,
        source_citations=[wiki("北欧神话", ""), wiki("诸神黄昏", ""), llm_note("北欧神话叙事")],
        tags=["通用", "神话", "北欧", "西方奇幻"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-greek-mythology",
        name="希腊罗马神话激活层",
        narrative_summary="希腊罗马神话作为西方叙事的另一根基：人格化神祇/英雄旅程/"
                          "神与人的混血/特洛伊战争。这些素材在现代奇幻/科幻/言情中持续被改写。",
        content_json={
            "twelve_olympians": "宙斯/赫拉/波塞冬/雅典娜/阿波罗等的人格化神格",
            "key_heroes": ["俄狄浦斯（命运不可逆）", "赫拉克勒斯（赎罪十二功）", "奥德修斯（漫长归乡）", "珀尔修斯（屠龙救美）"],
            "core_concepts": {
                "Hubris": "傲慢挑战神 → 必然惩罚",
                "Catharsis": "情感净化",
                "Hero's Journey": "出征→挑战→回归 → 现代英雄叙事的母版",
            },
            "modern_inheritors": "《神奇女侠》/ 波西杰克逊 / 《星球大战》（坎贝尔的神话母题）",
            "activation_keywords": ["宙斯", "俄狄浦斯", "赫拉克勒斯", "特洛伊", "希腊神话"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("希腊神话", ""), wiki("英雄之旅", "坎贝尔"), llm_note("希腊神话叙事")],
        tags=["通用", "神话", "希腊罗马", "英雄"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-buddhism-narrative",
        name="佛教叙事激活层（深化）",
        narrative_summary="佛教超出基础概念的叙事维度：菩萨发愿/转世与轮回/"
                          "末法时代/密宗的曼陀罗/禅宗公案的反逻辑。可作为玄幻/灵异/历史的宇宙观底色。",
        content_json={
            "narrative_structures": {
                "菩萨发愿": "为众生而修行的弧线（地藏王菩萨/观世音）",
                "本生故事": "前世今生因果链（佛陀500世）",
                "末法时代": "正法→像法→末法的衰变叙事",
                "禅宗公案": "通过悖论引发顿悟",
            },
            "key_concepts": ["四圣谛（苦集灭道）", "八正道", "十二因缘", "六道轮回", "三世因果"],
            "Chinese_traditions": "禅宗/天台宗/华严宗/密宗各有侧重",
            "narrative_uses": "玄幻中作为修行体系 / 灵异中作为驱邪基础 / 历史中作为时代背景",
            "activation_keywords": ["菩萨", "轮回", "因果", "禅宗", "末法", "六道", "佛家"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("佛教", ""), wiki("禅宗", ""), llm_note("佛教叙事深化")],
        tags=["通用", "佛教", "禅宗", "宗教叙事"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-islam-narrative",
        name="伊斯兰文化叙事元素",
        narrative_summary="伊斯兰文化的叙事资源：一千零一夜的故事中故事结构；"
                          "苏菲派的诗意神秘主义；圣战与朝圣的核心仪式；阿拉伯诗歌传统。"
                          "可丰富中东/西域/丝路相关题材。",
        content_json={
            "narrative_traditions": [
                "一千零一夜——故事中嵌套故事（meta叙事典范）",
                "苏菲派诗歌——鲁米/哈菲兹的神秘主义抒情",
                "阿拉伯英雄史诗——安塔尔/曼苏尔",
            ],
            "key_concepts": ["朝圣（哈吉）", "斋戒（拉马丹）", "天命（Qadar）", "圣战（吉哈德）"],
            "cultural_motifs": "沙漠/绿洲/帐篷文化 / 集市与商队 / 苏菲旋转舞",
            "Chinese_parallel_uses": "西域题材/丝绸之路 / 历史穿越 / 异域言情",
            "activation_keywords": ["一千零一夜", "苏菲", "鲁米", "朝圣", "沙漠文化"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[wiki("伊斯兰教", ""), wiki("苏非派", ""), llm_note("伊斯兰叙事")],
        tags=["通用", "伊斯兰", "叙事", "中东"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-celtic-myth",
        name="凯尔特神话与德鲁伊",
        narrative_summary="凯尔特神话：精灵/仙女/德鲁伊/亚瑟王传说。"
                          "与北欧神话相比更注重自然崇拜、女性力量、循环时间观。是现代奇幻/异世界穿越的另一重要源泉。",
        content_json={
            "key_elements": {
                "德鲁伊": "祭司+魔法师+学者三合一的智者",
                "仙境（Tír na nÓg）": "永生不老的仙境，时间流速不同",
                "亚瑟王": "理想王国卡美洛/圆桌骑士/圣杯传说",
                "梅林": "智者+魔法师原型",
            },
            "narrative_structures": "圣物追寻（圣杯）/ 三角恋（兰斯洛特）/ 王者归来",
            "modern_inheritors": "《梅林传奇》《阿瓦隆迷雾》",
            "Chinese_parallel_uses": "异世界穿越 / 玄幻欧式背景 / 仙气贵族浪漫",
            "activation_keywords": ["德鲁伊", "亚瑟王", "梅林", "圣杯", "凯尔特", "仙境"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("凯尔特神话", ""), wiki("亚瑟王传说", ""), llm_note("凯尔特叙事")],
        tags=["通用", "凯尔特", "亚瑟王", "西方奇幻"],
    ),

    # ═══════════ HISTORY DEEP ═══════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-roman-empire",
        name="罗马帝国体系激活",
        narrative_summary="罗马帝国的政治制度/军事组织/法律传统/文化遗产，"
                          "是西方政治叙事的根基。元老院/凯撒/共和制到帝制的转型/法律体系等都成为现代叙事的隐喻资源。",
        content_json={
            "political_evolution": "王政→共和→帝制的转型——核心张力是权力集中与制衡",
            "iconic_figures": ["凯撒", "屋大维", "马可·奥勒留", "君士坦丁"],
            "institutional_legacy": "元老院 / 法律体系（万民法）/ 行省制度 / 公民权",
            "narrative_archetypes": "暗杀的恐惧（布鲁图斯）/ 哲人皇帝（奥勒留）/ 帝国衰落的必然",
            "适用_genres": "历史改编 / 玄幻帝国设定 / 政治权谋",
            "activation_keywords": ["罗马", "凯撒", "元老院", "罗马法", "拜占庭"],
        },
        source_type="llm_synth", confidence=0.77,
        source_citations=[wiki("罗马帝国", ""), wiki("罗马法", ""), llm_note("罗马帝国叙事")],
        tags=["通用", "历史", "罗马", "政治"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-medieval-europe",
        name="中世纪欧洲世界观",
        narrative_summary="中世纪欧洲的封建制度/骑士文化/教会权威/十字军东征。"
                          "是西方奇幻文学的标准背景，对中文奇幻设计有借鉴价值。",
        content_json={
            "social_structure": "国王/贵族/骑士/平民/农奴/教士的金字塔",
            "key_institutions": ["教会（双重权力）", "封建采邑", "城邦/汉萨同盟", "大学的兴起"],
            "cultural_elements": "骑士精神（荣誉/忠诚/服务女士）/ 黑死病的创伤 / 异端审判",
            "narrative_devices": "圣战 / 骑士比武 / 朝圣 / 异教徒压制",
            "modern_inheritors": "《指环王》/ 《冰与火之歌》 / 各类西方奇幻",
            "activation_keywords": ["中世纪", "骑士", "封建", "十字军", "教会"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[wiki("中世纪", ""), wiki("骑士精神", ""), llm_note("中世纪叙事")],
        tags=["通用", "中世纪", "欧洲", "封建"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-cold-war",
        name="冷战时期叙事激活",
        narrative_summary="二战后到苏联解体的全球对峙：意识形态战争/间谍博弈/核威慑/代理人战争。"
                          "为间谍/政治悬疑/末日预演类作品提供了海量原型。",
        content_json={
            "key_concepts": ["铁幕", "古巴导弹危机", "代理人战争（朝鲜/越南/阿富汗）", "和平演变"],
            "spy_tradition": "勒卡雷/弗莱明等的间谍小说传统——道德灰色 vs 浪漫英雄",
            "ideological_split": "资本主义 vs 共产主义——影响所有叙事选择",
            "psychological_legacy": "核恐惧 / 阴谋论文化 / 双面间谍的认同问题",
            "modern_resonance": "新冷战 / 中美博弈在文学中的再现",
            "activation_keywords": ["冷战", "铁幕", "古巴导弹", "间谍", "意识形态战"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[wiki("冷战", ""), wiki("约翰·勒卡雷", ""), llm_note("冷战叙事")],
        tags=["通用", "冷战", "间谍", "政治"],
    ),

    # ═══════════ SCIENCE / RATIONAL FRAMEWORKS ═══════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-physics-basics",
        name="物理学基础叙事激活",
        narrative_summary="科幻/玄幻/赛博朋克对物理学概念的隐喻使用：相对论时间/量子叠加/熵增/"
                          "热力学三定律。这些概念在叙事中既是设定也是哲学隐喻。",
        content_json={
            "key_concepts": {
                "相对论": "时间膨胀——飞船人回家时家人都老了",
                "量子叠加": "薛定谔的猫——观察改变结果",
                "熵增": "宇宙必然走向混乱（热寂叙事）",
                "蝴蝶效应": "微小初始条件→巨大不同结果",
                "黑洞": "信息丢失/视界外/奇点",
            },
            "narrative_uses": [
                "时间穿越的硬科学锚点",
                "平行宇宙的多重观察",
                "末日叙事的物理基础",
            ],
            "Chinese_genre_compat": "科幻直接用 / 玄幻可借喻（修炼者突破=熵减）",
            "activation_keywords": ["相对论", "量子", "熵增", "蝴蝶效应", "黑洞", "薛定谔"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[wiki("相对论", ""), wiki("量子力学", ""), llm_note("物理学叙事")],
        tags=["通用", "物理学", "科幻", "概念"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-evolution-biology",
        name="进化论与生物学叙事激活",
        narrative_summary="达尔文进化论及现代生物学概念在叙事中的应用："
                          "自然选择/适者生存/共生/突变/灭绝。可用于末日/科幻/历史宏观叙事。",
        content_json={
            "core_concepts": {
                "自然选择": "环境压力下的适应——末日小说的核心机制",
                "共生": "互利依赖（海葵和小丑鱼）→ 关系叙事的隐喻",
                "突变": "随机变异→ 异能/觉醒的科学外衣",
                "K vs r 策略": "少而精后代 vs 多而广后代——适用于种族/文明对比",
                "性选择": "孔雀尾巴的悖论——审美驱动",
            },
            "narrative_applications": [
                "末日叙事中的进化压力",
                "外星文明的不同生存策略",
                "人类心理的进化解释（如恐惧/嫉妒）",
            ],
            "activation_keywords": ["自然选择", "适者生存", "共生", "突变", "进化", "达尔文"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[wiki("进化论", ""), wiki("达尔文", ""), llm_note("生物学叙事")],
        tags=["通用", "生物学", "进化", "科学"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-game-theory",
        name="博弈论叙事激活",
        narrative_summary="博弈论的关键概念已成为现代政治/商战/心理小说的常用工具："
                          "囚徒困境/纳什均衡/零和博弈/重复博弈/谢林点。提供严谨的人际/集体决策分析框架。",
        content_json={
            "key_concepts": {
                "囚徒困境": "理性反而导致集体次优 → 信任/背叛叙事",
                "纳什均衡": "无人能单方面改善的状态 → 僵局/冷战",
                "零和博弈": "你赢=我输 → 残酷竞争",
                "重复博弈": "反复博弈中合作可以浮现 → 长期关系",
                "谢林点": "无沟通时双方共同选择的方案 → 默契/惯性",
                "可信威胁": "威胁要可信才有效 → 政治/谈判",
            },
            "narrative_applications": [
                "政治权谋（联盟博弈）",
                "商战谈判（信息博弈）",
                "末日生存（资源争夺）",
                "感情博弈（投入产出比）",
            ],
            "Chinese_parallels": "孙子兵法的博弈智慧/三十六计",
            "activation_keywords": ["囚徒困境", "纳什均衡", "博弈论", "零和", "重复博弈"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("博弈论", ""), wiki("囚徒困境", ""), llm_note("博弈论叙事")],
        tags=["通用", "博弈论", "策略", "决策"],
    ),

    # ═══════════ NARRATIVE / WRITING DEEPENING ═══════════
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="gen-tm-doubles",
        name="双子/镜像主题",
        narrative_summary="文学中反复出现的『双子』主题：双胞胎/分身/影子/对手——"
                          "通过另一个『自己』来质问和审视主角。从《双重人格》到《搏击俱乐部》到《让子弹飞》。",
        content_json={
            "variant_forms": [
                "双胞胎（实体双重）",
                "分身（心理双重）",
                "影子（道德双重）",
                "宿敌（互相塑造的他者）",
                "镜中倒影（异化的自我）",
            ],
            "narrative_function": "外部化主角内心冲突",
            "famous_works": ["《双重人格》陀思妥耶夫斯基", "《搏击俱乐部》", "《化身博士》", "《黑天鹅》"],
            "Chinese_uses": "《红楼梦》宝玉与甄宝玉 / 《三国》刘备与曹操作为镜像",
            "activation_keywords": ["双子", "镜像", "分身", "影子", "宿敌", "另一个自己"],
        },
        source_type="llm_synth", confidence=0.77,
        source_citations=[wiki("双子主题", "文学"), llm_note("镜像主题分析")],
        tags=["通用", "镜像", "双子", "主题"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="gen-tm-fall-grace",
        name="跌落/堕落主题",
        narrative_summary="从高位跌落的叙事原型：从天堂到地狱、从纯真到堕落、"
                          "从权力到流放。这种弧线本身就有戏剧张力，是悲剧/反转/重生题材的核心。",
        content_json={
            "fall_types": [
                "道德堕落（靡菲斯特协议）",
                "社会跌落（贵族沦为乞丐）",
                "心灵堕落（圣徒变为罪人）",
                "权力跌落（国王变流浪汉）",
            ],
            "structural_options": ["跌落到底再不起", "跌落是为了真正崛起", "跌落本身是觉醒"],
            "famous_examples": ["撒旦堕落", "李尔王", "《了不起的盖茨比》", "《追风筝的人》"],
            "thematic_resonance": "我们都在某种意义上是堕落者——这种共鸣最普遍",
            "activation_keywords": ["堕落", "跌落", "失乐园", "覆灭", "坠落"],
        },
        source_type="llm_synth", confidence=0.77,
        source_citations=[wiki("失乐园", "弥尔顿"), llm_note("堕落主题分析")],
        tags=["通用", "堕落", "跌落", "主题"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="gen-tm-time-cycles",
        name="时间循环/轮回主题",
        narrative_summary="时间不是直线而是循环的叙事观：从佛家轮回到《土拨鼠之日》到《魔法少女小圆》，"
                          "重复的同一时间段成为人物成长/解谜/觉醒的容器。",
        content_json={
            "narrative_variants": [
                "字面时间循环（土拨鼠之日）",
                "代际重复（百年孤独）",
                "宿命论（俄狄浦斯）",
                "佛家轮回（前世今生）",
            ],
            "core_question": "人能否打破循环？打破的代价是什么？",
            "modern_applications": "重生类作品的根基 / 平行宇宙的多次尝试",
            "Chinese_parallels": "《红楼梦》的『食尽鸟投林』的循环感",
            "activation_keywords": ["时间循环", "轮回", "宿命", "重生", "循环"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[wiki("时间循环", "电影"), llm_note("轮回主题分析")],
        tags=["通用", "时间", "循环", "轮回"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="gen-tm-secret-identity",
        name="秘密身份主题",
        narrative_summary="主角拥有不为人知的真实身份：从超级英雄到武林高手到隐世富豪。"
                          "这种主题的核心张力是『何时揭示』和『揭示给谁』——比身份本身更重要。",
        content_json={
            "common_variants": [
                "超级英雄（蝙蝠侠/超人/蜘蛛侠）",
                "武林高手低调入世",
                "亿万富翁伪装平民",
                "公主/王子流落民间",
                "穿越者隐藏来历",
            ],
            "narrative_tensions": [
                "身边人发现真相的恐惧",
                "保护亲人的责任",
                "双重生活的疲惫",
                "选择哪个身份才是真正的自己",
            ],
            "writing_principle": "身份的揭示要触发真正的关系/情节变化，否则就是噱头",
            "activation_keywords": ["双重身份", "秘密身份", "隐世", "假面", "真实身份"],
        },
        source_type="llm_synth", confidence=0.77,
        source_citations=[wiki("超级英雄", "双重身份"), llm_note("秘密身份叙事分析")],
        tags=["通用", "身份", "秘密", "主题"],
    ),
]


async def main(dry_run: bool = False) -> None:
    print(f"{'[DRY RUN] ' if dry_run else ''}Seeding {len(ENTRIES)} entries...\n")
    from collections import Counter
    g, d = Counter(), Counter()
    for e in ENTRIES:
        g[e.genre or "(通用)"] += 1
        d[e.dimension] += 1
    print(f"By genre:     {dict(g)}")
    print(f"By dimension: {dict(d)}\n")
    if dry_run:
        return
    errors = 0
    async with session_scope() as session:
        for entry in ENTRIES:
            try:
                await insert_entry(session, entry, compute_embedding=True)
            except Exception as exc:
                print(f"  ERROR {entry.slug}: {exc}")
                errors += 1
    print(f"\n✓ {len(ENTRIES) - errors} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    asyncio.run(main(dry_run=dry))
