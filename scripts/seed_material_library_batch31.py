"""
Batch 31: Specific story plot patterns / archetypal narrative beats.
Activates concrete plot scaffolds: Hero's Journey / Save the Cat /
Heist / Revenge / Fall-from-grace / Coming-of-age / Mystery / Romance.
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
    # 英雄之旅
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="plot-pattern-heros-journey",
        name="英雄之旅（Hero's Journey / Monomyth）",
        narrative_summary="约瑟夫·坎贝尔《千面英雄》1949 + Vogler 简化为 12 阶段。"
                          "适用奇幻 / 仙侠 / 重生 / 大部分类型小说骨架。"
                          "三大阶段：启程 + 启蒙 + 归来。",
        content_json={
            "campbell_seventeen_stages": "1) 历险召唤 / 2) 拒绝召唤 / 3) 超自然援助 / 4) 跨越第一门槛 / 5) 鲸腹（重生准备）/ 6) 试炼之路 / 7) 与女神相遇 / 8) 诱惑 / 9) 与父神和解 / 10) 神化 / 11) 最终恩赐 / 12) 拒绝归返 / 13) 神奇逃脱 / 14) 营救 / 15) 跨越归途门槛 / 16) 两个世界主宰 / 17) 自由生活",
            "vogler_twelve_stages": "1) 平凡世界 / 2) 历险召唤 / 3) 拒绝召唤 / 4) 与导师会面 / 5) 跨越门槛 / 6) 试炼 + 盟友 + 敌人 / 7) 接近最深洞穴 / 8) 严峻考验 / 9) 报酬（剑）/ 10) 归途 / 11) 复活 / 12) 携药归还",
            "three_acts": "Departure 启程（1-5）= 平凡 → 召唤 → 跨界 / Initiation 启蒙（6-9）= 试炼 → 高潮 → 报酬 / Return 归来（10-12）= 回归 → 复活 → 携药",
            "famous_examples": "《星球大战》卢克 / 《指环王》弗罗多 / 《哈利波特》/ 《盗梦空间》/ 《黑客帝国》尼奥 / 《狮子王》辛巴 / 《飞屋环游记》/ 大量好莱坞 + 日本动漫",
            "key_archetypes": "英雄 Hero / 导师 Mentor（甘道夫 + 邓布利多 + 欧比旺）/ 阴影 Shadow（萨鲁曼 + 伏地魔）/ 阈限守护者 / 信使 / 变形者 / 骗子 / 盟友",
            "crisis_pattern": "高潮前必有最深洞穴 / 主角失去一切（导师死 + 朋友叛 + 信仰崩）/ 然后顿悟 + 复活",
            "twist_subversions": "《冰与火之歌》打破：奈德死 + 红色婚礼 = 反抗英雄套路 / 当代后现代刻意打破 + 创造对应反英雄叙事",
            "narrative_use": "几乎一切类型小说骨架 / 仙侠 / 玄幻 / 都市修仙 / 重生流 / 系统流大都套用",
            "activation_keywords": ["英雄之旅", "Hero's Journey", "坎贝尔", "Vogler", "历险召唤", "导师", "试炼", "携药归还"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("英雄之旅", ""), llm_note("Campbell + Vogler")],
        tags=["剧情", "结构", "通用"],
    ),
    # Save the Cat 15 拍
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="plot-pattern-save-the-cat-15-beats",
        name="Save the Cat 15 拍（Blake Snyder）",
        narrative_summary="好莱坞编剧圣经。Blake Snyder 2005《Save the Cat!》。"
                          "把 110 页剧本拆 15 个精确拍点。"
                          "适用网文章节节奏借鉴 + 长篇分卷拆分。",
        content_json={
            "fifteen_beats": "1) Opening Image（开场画面，p1）/ 2) Theme Stated（主题陈述，p5）/ 3) Set-Up（铺设，p1-10）/ 4) Catalyst（催化剂，p12）/ 5) Debate（辩论，p12-25）/ 6) Break into Two（进入第二幕，p25）/ 7) B Story（B 故事 = 爱情线/友情线，p30）/ 8) Fun and Games（玩耍与乐趣 = 主体卖点段，p30-55）/ 9) Midpoint（中点 = 假胜利或假失败，p55）/ 10) Bad Guys Close In（坏蛋紧逼，p55-75）/ 11) All Is Lost（一切尽失，p75）/ 12) Dark Night of the Soul（灵魂的暗夜，p75-85）/ 13) Break into Three（进入第三幕，p85）/ 14) Finale（终曲，p85-110）/ 15) Final Image（终场画面，p110）",
            "snyder_genre_categories": "Snyder 把电影分 10 类：Monster in the House / Golden Fleece / Out of the Bottle / Dude with a Problem / Rites of Passage / Buddy Love / Whydunit / The Fool Triumphant / Institutionalized / Superhero",
            "midpoint_importance": "中点是关键反转 / 把'要救妈'变'要救全城' / 提升 stakes / 不能松懈在一半",
            "all_is_lost_pattern": "75 页主角失去一切 / 死亡气息（实质或象征性死亡 + 导师死 + 友谊破裂 + 失去爱情）/ 必须有这个谷底 / 之后才有复活动力",
            "save_the_cat_origin": "片名来自'救猫法则'：主角第一次出场必须做一件让观众喜欢的事（救猫）/ 锁定共情",
            "applied_to_novel": "网文转化：开场 = 第 1 章 / 催化剂 = 第 1-3 章金手指 / 进入第二幕 = 卷 1 第 1 卷 / 中点 = 全书中点剧情大反转 / All Is Lost = 高潮前一卷主角失去一切 / 终曲 = 大结局",
            "famous_save_the_cat_films": "《公主与青蛙》/《阿凡达》/《黑暗骑士》（部分）/《饥饿游戏》/《指环王》各部分都吻合",
            "narrative_use": "网文长篇骨架 / 每卷参考一拍 / 章节节奏 / 编剧改编",
            "activation_keywords": ["Save the Cat", "15 拍", "Blake Snyder", "中点", "灵魂暗夜", "进入第二幕", "终曲"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("Save the Cat!", ""), llm_note("Snyder 编剧")],
        tags=["剧情", "结构", "通用"],
    ),
    # 复仇线
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="plot-pattern-revenge-arc",
        name="复仇线（Revenge Arc）",
        narrative_summary="经典原型 = 基督山伯爵 + 哈姆雷特 + 杀死比尔 + 老男孩。"
                          "三阶段：受害 → 沉潜 → 反击。"
                          "适用武侠 / 都市黑帮 / 重生 / 修仙皆用。",
        content_json={
            "three_phases": "Phase 1 Wounding 受害（爱人死 + 家族灭 + 师父被害 + 自己死过一次）/ Phase 2 Preparation 沉潜（武功 / 财富 / 权力的累积）/ Phase 3 Execution 反击（一一报仇 + 揭真相）",
            "five_acts_structure": "Act 1 受害（开场惨剧 + 主角发誓）/ Act 2 收集筹码（习武 + 富贵 + 盟友）/ Act 3 第一次复仇（小角色 + 试水）/ Act 4 接近大反派（揭层层真相 + 损失盟友）/ Act 5 终极对决（往往主角自己也成了类似反派 + 道德拷问）",
            "famous_revenge_works": "《基督山伯爵》（金钱 + 智略 + 14 年沉潜）/ 哈姆雷特（迟疑型）/ 楚留香 / 萧十一郎 / 《杀死比尔》/ 《老男孩》（韩国 + 反转）/ 《V 字仇杀队》/ 《罗刹海市》/ 网文重生流大量",
            "moral_complexity": "复仇者是否也变恶？/ 复仇成功后空虚 / 复仇路上失去自己 / 真正赢家是放下复仇者吗？",
            "subtypes": "1) 个人复仇（杀夫之仇）/ 2) 家族复仇（满门）/ 3) 国仇（亡国）/ 4) 阶级复仇（贫民对富豪）/ 5) 集体复仇（受害者团体）",
            "key_motifs": "假死归来 / 易容改名 / 财富武功累积 / 复仇清单 / 揭穿真相 / 同归于尽 / 道德两难（杀仇人女儿吗）",
            "subversion_patterns": "复仇途中爱上仇人之女 / 仇人本身也是受害 / 真正凶手是另有其人 / 复仇者发现自己变成了仇人那样的人",
            "modern_examples": "《琅琊榜》梅长苏（最经典中国版基督山伯爵）/ 《无人生还》/《消失的爱人》（婚姻复仇）/ 重生女主复仇文（《娇娆》《嫡女归来》）",
            "narrative_use": "古装武侠 / 都市重生 / 仙侠破家寻仇 / 现实犯罪 / 言情 + 虐恋复仇",
            "activation_keywords": ["复仇", "基督山伯爵", "哈姆雷特", "梅长苏", "杀死比尔", "假死归来", "复仇清单", "受害"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("复仇主题", ""), llm_note("复仇剧情")],
        tags=["剧情", "复仇", "通用"],
    ),
    # 蜕变成长（Coming of Age）
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="plot-pattern-coming-of-age",
        name="成长蜕变（Coming of Age）",
        narrative_summary="未成年到成熟的精神之旅。Bildungsroman 教养小说。"
                          "受纯真 → 受冲击 → 困惑 → 经验 → 成熟。"
                          "适用青春 / 校园 / 玄幻初期 / 重生少年 / 文艺。",
        content_json={
            "core_pattern": "童年纯真 → 接触现实复杂性 → 第一次幻想破灭 → 反叛 + 自我探索 → 重大事件（失恋 + 死亡 + 失败 + 战争）→ 痛定思痛 → 与世界达成新协议（不是放弃理想是成熟版）",
            "key_themes": "纯真之失 Loss of Innocence / 性的觉醒 / 父母权威崩塌 / 友谊与背叛 / 第一次爱情 / 第一次面对死亡 / 自我身份 / 道德成熟",
            "famous_works": "Salinger《麦田里的守望者》（霍尔顿）/ Joyce《青年艺术家肖像》（斯蒂芬）/ 简·奥斯汀《艾玛》/ Mark Twain《哈克贝利费恩》/《杀死一只知更鸟》（Scout）/ 《追风筝的人》（阿米尔）/《伊豆的舞女》/《挪威的森林》/《红楼梦》宝玉",
            "chinese_youth": "鲁迅《故乡》/ 钱钟书《围城》/ 王小波《黄金时代》/ 余华《在细雨中呼喊》/ 王朔《动物凶猛》/ 《阳光灿烂的日子》/ 莫言《红高粱》/ 路遥《平凡的世界》",
            "structure_compared": "和英雄之旅区别：英雄之旅强调外在大世界对抗 / 成长强调内在自我转变 / 但常融合（哈利波特两者皆有）",
            "subtypes": "1) 学校型（学校政治 + 友情 + 老师）/ 2) 旅行型（一个夏天的远行改变 + 《菊次郎的夏天》）/ 3) 战争型（突然成熟 + 《伊豆的舞女》）/ 4) 失去型（亲人死带来的成熟 + 《飞屋》）/ 5) 政治觉醒（《活着》）",
            "key_scenes": "第一次得知世界不是你想的那样（圣诞老人是假的型）/ 第一次失败 / 第一次性 / 第一次死亡（祖父祖母）/ 离家上大学 / 第一份工作 + 第一次失业 / 父母变老",
            "narrative_use": "校园 / 青春 / 玄幻仙侠初期（少年初出茅庐）/ 都市初创 / 文艺 / 重生改命",
            "activation_keywords": ["成长", "Coming of Age", "Bildungsroman", "麦田守望者", "纯真之失", "成熟", "霍尔顿"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("成长小说", ""), llm_note("Bildungsroman")],
        tags=["剧情", "成长", "通用"],
    ),
    # 偷盗剧情（Heist）
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="plot-pattern-heist-caper",
        name="偷盗剧（Heist / Caper）",
        narrative_summary="组团 + 计划 + 执行 + 反转 + 散场 五段式。"
                          "经典：十一罗汉 / 偷天换日 / 大鱼吃小鱼。"
                          "现代：完美陷阱 + 双重底牌 + 内鬼。"
                          "适用商战 + 谍战 + 武侠盗墓 + 都市悬疑。",
        content_json={
            "five_act_structure": "Act 1 The Setup 起意（钱多到无法拒绝 + 私人理由）/ Act 2 The Crew 组团（每人一项独门技 + 主谋 + 黑客 + 内鬼 + 美女）/ Act 3 The Plan 策划（沙盘 + 演练 + B 计划）/ Act 4 The Heist 执行（必出意外 + 即兴解决）/ Act 5 The Twist 反转（主角早预料 + 内鬼 + 真正目标 + 散场）",
            "essential_team_roles": "Mastermind 主谋 / Pickpocket 扒手 / Hacker 黑客 / Demolitions 爆破 / Driver 司机 / Inside Man 内鬼 / Honeytrap 美人 / Muscle 打手 / Forger 仿冒",
            "famous_heists": "《十一罗汉》Ocean's 11 + 12 + 13 / 《偷天换日》Italian Job / 《大鱼吃小鱼》/ 《纸钞屋》La Casa de Papel / 《卖花女》/ 《偷天陷阱》/ 《狮子大开口》/ 《盗梦空间》（梦中盗窃变种）",
            "real_world_inspiration": "纽约 1978 卢夫汉萨大劫案 / 银行劫案 / 加密币交易所被黑 / 内幕交易",
            "must_have_elements": "1) 看似不可能的目标（高安保 + 银行金库 + 王宫珠宝）/ 2) 至少 1 个意外（警报响 + 同伙叛 + 时间紧）/ 3) 双重计划（A 失败启动 B）/ 4) 反转必有（队员中早有内应 + 真正目标不是观众以为的）/ 5) 主谋永远比观众多想一步",
            "double_cross_patterns": "队员中有内鬼 / 主谋早设套利用某队员 / 看似失败实是计划 / 钱被掉包 / 真正目标是身份 + 信息 + 复仇而非钱 / 警察其实站主角一边",
            "korean_heists": "《老手》《特工》/ 韩国偷盗剧多带政治 + 阶级压迫元素",
            "chinese_versions": "《天下无贼》（火车 + 王薄）/ 《唐人街探案》系列 / 《风声》（谍战版）/ 古装：《古董局中局》",
            "narrative_use": "商战大戏 / 谍战 / 盗墓 / 都市黑帮 / 网文系统流偷东西 / 仙侠偷宝",
            "activation_keywords": ["Heist", "偷盗", "十一罗汉", "纸钞屋", "组团", "策划", "反转", "内鬼", "盗梦空间"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("偷盗题材", ""), llm_note("Heist genre")],
        tags=["剧情", "偷盗", "通用"],
    ),
    # 鱼出水困境
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="plot-pattern-fish-out-of-water",
        name="鱼出水（Fish Out of Water）",
        narrative_summary="角色突然进入完全陌生的环境。穿越 / 异界 / 时空错位 / 跨阶级 / 跨文化。"
                          "戏剧张力来自反差 + 适应过程 + 学习曲线 + 最终融入或反向改变。",
        content_json={
            "core_recipe": "1) 主角原属环境 A / 2) 强制进入环境 B（穿越 / 流放 / 派遣 / 误闯）/ 3) 第一阶段震惊 + 笨拙 + 闹笑话 / 4) 第二阶段学习 + 找盟友 + 误解 / 5) 第三阶段融入 + 也带来 A 世界视角影响 B 世界 / 6) 选择留下 + 回去 + 两边平衡",
            "subtypes": "1) 时空穿越（古今互穿 / 异世界穿越 / 重生）/ 2) 阶级跨越（豪门入贫 + 灰姑娘）/ 3) 文化跨越（国际生 + 移民）/ 4) 行业跨越（皇帝去打工 + 富翁体验流浪）/ 5) 物种跨越（人变动物 + 神变凡）/ 6) 性别跨越（男女互穿）",
            "famous_examples": "《灰姑娘》（仙女让她进入舞会）/《王子复仇记》/《公主日记》/《拉拉和女佣》/《鬼吹灯》（学者闯古墓）/ 大量穿越文 / 《与神同行》/《来自星星的你》",
            "comedy_potential": "笨拙误用语言 + 不懂礼仪闹笑话 + 跨文化笑点 / 喜剧片大爱用 / 《憨豆先生在美国》《唐人街探案》",
            "drama_potential": "震惊 + 自我认同危机 + 我是谁 / 失去身份的痛苦 / 适应代价",
            "novelty_value": "读者借主角眼看新世界 / 同时给读者熟悉的 A 世界视角作支点 / 比纯异世界更易代入",
            "key_arcs": "Arc 1 Survival 求生（语言 + 食物 + 住）/ Arc 2 Acceptance 被接纳（找朋友 + 立功 + 救人）/ Arc 3 Influence 反向影响（改变 B 世界 + 引入 A 世界知识）/ Arc 4 Choice 抉择（留 / 回 / 平衡）",
            "narrative_use": "穿越文（最爆款类型）/ 异世界（《Re:从零开始》《盾之勇者》）/ 重生（带未来知识）/ 跨界（医生穿越成皇后）",
            "activation_keywords": ["鱼出水", "Fish out of Water", "穿越", "重生", "异世界", "灰姑娘", "适应"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("鱼出水", ""), llm_note("Fish out of water")],
        tags=["剧情", "穿越", "通用"],
    ),
    # 三角恋
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="plot-pattern-love-triangle",
        name="三角恋（Love Triangle）",
        narrative_summary="A 爱 B + B 在 A 与 C 之间犹豫 + C 也爱 B。"
                          "经典：钢铁男友 vs 隐忍青梅 / 暮光：爱德华 vs 雅各 / 红楼梦：宝玉 + 黛玉 + 宝钗。"
                          "驱动言情核心张力。",
        content_json={
            "classic_archetype": "1 受 + 2 攻 + 受在两者之间挣扎 / 1 主角 + 2 异性追求者 / 主角必须在身上选一个",
            "two_suitor_archetypes": "经典对照：1) 富贵 vs 贫穷 / 2) 完美 vs 危险 / 3) 青梅 vs 新欢 / 4) 现实 vs 梦想 / 5) 安稳 vs 激情 / 6) 阳光 vs 阴郁",
            "famous_triangles": "《红楼梦》宝玉 + 黛玉 + 宝钗（最经典）/ 《呼啸山庄》凯瑟琳 + 希斯克利夫 + 林顿 / 《飘》斯嘉丽 + 巴特勒 + 卫希礼 / 《了不起的盖茨比》黛西 + 盖茨比 + 汤姆 / 《暮光》贝拉 + 爱德华 + 雅各 / 《饥饿游戏》凯特尼斯 + 皮塔 + 盖尔 / 《何以笙箫默》/ 《微微一笑很倾城》前期",
            "structural_dynamics": "三角推动剧情：决策延宕 + 错位告白 + 误会重重 + 各自心机 + 见证者煽风点火 / 两男争一女或两女争一男都可",
            "common_resolutions": "1) 主角终选其一（剩一被告别）/ 2) 一人退出（成全）/ 3) 一人死（《泰坦尼克》/《钢琴师》）/ 4) 双双失去（悲剧版）/ 5) 现代重组（前任仍是朋友）/ 6) 三人和解",
            "subverted_versions": "现代后现代：双女主皆爱主角 + 主角拒绝抉择 / 三角中第三者其实更爱另一对 / 揭穿三人都被操纵 / 时间循环造三角",
            "narrative_techniques": "POV 切换让读者了解三方心 / 误会必积累至极 / 关键场景让选择成必然 / 不要拖太久（读者疲劳）",
            "modern_variations": "百合三角 / 同性三角 / 多元（Polyamorous）/ 网文常见美强惨多人追主角 + 后宫文 + 反向后宫文",
            "narrative_use": "言情主线 / 校园 / 古装 / 都市 / 仙侠（三师叔 + 大师姐设定）/ 网文女频铺底层",
            "activation_keywords": ["三角恋", "宝玉黛玉宝钗", "暮光", "钢铁男友", "选择", "失之交臂", "成全"],
        },
        source_type="llm_synth", confidence=0.90,
        source_citations=[wiki("三角恋情", ""), llm_note("Love triangle")],
        tags=["剧情", "言情", "通用"],
    ),
    # 末日生存
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="plot-pattern-apocalyptic-survival",
        name="末日生存（Apocalyptic Survival）",
        narrative_summary="文明崩塌后求生。丧尸 / 核战 / 瘟疫 / 异变 / 外星入侵。"
                          "三阶段：崩溃 → 求生 → 重建。"
                          "经典：行尸走肉 / 末日危途 / 流浪地球。",
        content_json={
            "three_phases": "Phase 1 The Fall 崩塌（前 1-2 章 / 病毒爆发 / 核弹落 / 异变）/ Phase 2 Survival 求生（找食 + 找水 + 找住 + 抗丧尸 + 团队组合 + 道德两难）/ Phase 3 Rebuilding 重建（建立营地 + 政治 + 找其他幸存者 + 寻找解药或希望）",
            "core_conflicts": "人 vs 环境（找资源）/ 人 vs 怪物（丧尸 / 异变）/ 人 vs 人（更可怕的是人 + 抢资源 + 邪教 + 独裁者）/ 人 vs 自我（道德崩溃 + 该不该杀染病的同伴 + 生存还是人性）",
            "famous_works": "《行尸走肉》（丧尸长篇）/《我是传奇》（孤独求生）/《末日危途》/《釜山行》/《世界大战》（外星）/《地球停转之日》/《流浪地球》（中国硬科幻）/《最后生还者》游戏 + 美剧 / 国内：《无人区》《丧尸路》",
            "subgenres": "丧尸末日（真菌 / 病毒 + 咬人传染 + 大脑被驱使）/ 核战末日（《辐射》系列）/ 瘟疫末日（《复明症漫记》）/ 异变末日（《迷雾》《湮灭》）/ 外星入侵（《独立日》《降临》）/ 自然灾难（《2012》《后天》）/ 资源耗尽（《沙丘》之水）/ 数字毁灭（停电 + AI 反叛）",
            "key_supplies": "水 + 食物 + 燃料 + 武器 + 弹药 + 药品 + 通讯 + 交通 + 住所 + 衣物 + 工具 / 资源永远不够",
            "social_structure": "强人独裁（《釜山行》列车里）/ 民主社区（《行尸走肉》亚历山大）/ 邪教（崇拜外星 + 死神 + 救世主）/ 武装团伙 / 商队 / 流民",
            "moral_dilemmas": "1) 同伴被咬要不要杀 / 2) 救陌生人浪费资源吗 / 3) 杀活人取食物 / 4) 放弃老弱保壮 / 5) 谁应留下断后",
            "hope_endings": "1) 找到解药（《我是传奇》原版）/ 2) 找到避难所（最常见）/ 3) 找到其他幸存者建立联盟 / 4) 接受新生活态度 / 5) 反英雄式：主角变成新世界的恶（少见）",
            "narrative_use": "末日 / 丧尸 / 灾难 / 重生末日 / 系统流末日改命 / 仙侠融合（修仙者末日求生）",
            "activation_keywords": ["末日", "求生", "丧尸", "行尸走肉", "末日危途", "流浪地球", "核战", "瘟疫"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("末日题材", ""), llm_note("Apocalyptic")],
        tags=["剧情", "末日", "通用"],
    ),
    # 卧底（Undercover）
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="plot-pattern-undercover",
        name="卧底（Undercover）",
        narrative_summary="警察混入黑帮 / 间谍混入敌国 / 战争混入军营。"
                          "极致的张力 = 任何一刻可能身份暴露 + 与卧底对象产生真感情。"
                          "经典：无间道 / 风声 / 加州旅馆。",
        content_json={
            "core_tension": "1) 永远的曝光焦虑 / 2) 双面人格分裂 / 3) 与渗透对象建立真感情（亲情 + 友情 + 爱情）/ 4) 任务完成后归属问题 / 5) 上线断联或牺牲（信任谁？）",
            "classic_three_act": "Act 1 渗透（建立身份 + 取得初步信任 + 获取一些情报）/ Act 2 深入（成为对方核心 + 与对方角色建立真感情 + 心理冲突）/ Act 3 摊牌（行动时刻 + 暴露危险 + 最终选择）",
            "famous_works": "《无间道》（梁朝伟刘德华双卧底 + 港片巅峰）/《风声》（黄晓明 + 周迅 + 谍战经典）/《加州旅馆》/《狗咬狗》/《伪装者》/ 《潜伏》（孙红雷 + 余则成 + 国共谍战经典）/《伊甸湖》",
            "psychological_layers": "1) 任务身份 vs 真实身份分裂 / 2) 表面表演的情感 vs 真实积累的情感 / 3) 上线指示 vs 现场判断 / 4) 同情敌人 / 5) 完成任务后是否还能回到原来的自己",
            "double_agent_variations": "单纯卧底（往敌方放）/ 双面间谍（两边都效力 + 不知谁是真主）/ 假叛逃 / 假死复出 / 反向卧底（被敌方认为已转化但其实没）",
            "key_scenes": "假娶仇敌之女 / 杀死至亲取得信任 / 接到上线传令必须杀某人 / 朋友质疑你 / 关键证据时刻不知该不该出手 / 上线被杀失联 / 最终摊牌",
            "moral_complexity": "为大义牺牲个人感情值得吗 / 任务完成但你也变成了他们 / 如果对面被你害的人是无辜的呢 / 任务期间结婚生子怎么办",
            "modern_variations": "《无间道》改编《无间风云》（马丁斯科塞斯）/《伪装者》/《北京无影手》/ 网文卧底重生 / 仙侠卧底入魔门",
            "narrative_use": "谍战 / 警匪 / 黑帮 / 仙侠（卧底魔教）/ 历史（地下党）/ 现代（卧底毒贩）",
            "activation_keywords": ["卧底", "无间道", "风声", "潜伏", "双面间谍", "渗透", "伪装", "暴露", "摊牌"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("卧底题材", ""), llm_note("Undercover")],
        tags=["剧情", "卧底", "通用"],
    ),
    # 真相揭露（Whodunit）
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="plot-pattern-whodunit-mystery",
        name="侦探推理 Whodunit",
        narrative_summary="谁是凶手？经典推理三步 = 案件 → 调查 → 揭露。"
                          "黄金时代：阿加莎克里斯蒂 / 柯南道尔 / 范达因 / 阿瑟柯南。"
                          "本格 vs 社会派 vs 硬汉派。",
        content_json={
            "structure_classic": "Act 1 案件发生（尸体 + 不可能犯罪 + 多嫌疑）/ Act 2 调查（取证 + 询问每个嫌疑 + 假线索 + 发现新尸体）/ Act 3 大团圆揭露（侦探集合所有人 + 还原作案过程 + 指出凶手 + 凶手解释动机）",
            "subgenres": "本格 Honkaku（注重诡计 + 公平 + 读者可推理 + 阿加莎 + 横沟正史 + 岛田庄司 + 绫辻行人）/ 社会派 Shakai-ha（揭社会问题 + 松本清张 + 宫部美雪 + 东野圭吾）/ 硬汉派 Hardboiled（侦探主角粗砺 + Chandler + Hammett）/ 警察程序（procedural + 真实警务 + 87 分局 Ed McBain）/ 心理悬疑（Patricia Highsmith + 希区柯克）",
            "golden_age_authors": "Agatha Christie 阿加莎（《无人生还》《东方快车谋杀案》《尼罗河谋杀案》《罗杰艾克罗伊德谋杀案》/ 80+ 部 + Hercule Poirot + Miss Marple）/ Arthur Conan Doyle 柯南道尔（福尔摩斯）/ Ellery Queen / Dorothy Sayers / John Dickson Carr",
            "japanese_masters": "横沟正史（《八墓村》《犬神家族》）/ 松本清张（社会派祖）/ 江户川乱步（黑暗推理）/ 岛田庄司《占星术杀人魔法》/ 绫辻行人《十角馆事件》/ 东野圭吾《嫌疑人 X 的献身》《白夜行》/ 宫部美雪 / 京极夏彦《魍魉之匣》",
            "chinese_practitioners": "蔡骏 / 周浩晖 / 紫金陈（《坏小孩》《长夜难明》）/ 雷米《心理罪》",
            "fairness_principle": "Knox 十诫 + Van Dine 二十规则 / 凶手必早出场 + 不能用超自然 + 不能突然引入未知毒药 + 侦探不能是凶手（少见地早期克里斯蒂《罗杰艾克罗伊德谋杀案》打破）",
            "famous_tricks": "密室杀人 / 不在场证明 / 双胞胎 / 易容 / 凶器消失 / 时间错位 / 误导身份 / 暗示叙诡 / 被害者其实是凶手",
            "narrative_devices": "Red Herring 假线索 / Chekhov's Gun 契诃夫之枪（出现的物件必有用）/ MacGuffin 麦高芬（驱动剧情但本身不重要的目标）/ Twist 反转",
            "modern_innovations": "时间倒叙（《记忆碎片》）/ 不可靠叙事者（《消失的爱人》）/ 多重 POV（《无人生还》）/ 叙诡（叙述本身误导）/ 元推理（推理小说自指）",
            "narrative_use": "推理悬疑（专门类型）/ 武侠加推理（《狄公案》/《大唐狄公案》/《长安十二时辰》）/ 修仙加推理（魔门刺杀案）/ 都市悬疑",
            "activation_keywords": ["推理", "Whodunit", "阿加莎", "福尔摩斯", "本格", "社会派", "东野圭吾", "密室", "不在场证明"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("推理小说", ""), llm_note("Mystery genre")],
        tags=["剧情", "推理", "通用"],
    ),
    # 跌落神坛
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="plot-pattern-fall-from-grace",
        name="跌落神坛（Fall from Grace / Tragedy）",
        narrative_summary="高位人物因傲慢 / 缺陷 / 过失 → 一步步走向毁灭。"
                          "亚里士多德《诗学》六要素 + Hubris 傲慢 + Hamartia 致命缺陷。"
                          "经典：李尔王 / 麦克白 / 教父 / 绝命毒师。",
        content_json={
            "aristotle_six_elements": "情节 Mythos / 性格 Ethos / 思想 Dianoia / 言语 Lexis / 歌咏 Melos / 景象 Opsis",
            "hubris_hamartia": "Hubris 傲慢 = 主角自以为是凌驾命运 / Hamartia 致命缺陷 = 主角内在性格弱点（贪 / 嫉妒 / 自卑）/ Anagnorisis 顿悟 = 主角认识到自己错的瞬间 / Peripeteia 反转 = 命运逆转",
            "five_stage_arc": "Stage 1 高位（成功 + 名利 + 被尊敬）/ Stage 2 傲慢（一次大胜后忽略警告 + 越界）/ Stage 3 错误决定（受 hamartia 驱动 + 不可逆的 fatal flaw）/ Stage 4 滚雪球（一错再错 + 失友 + 失爱 + 失利）/ Stage 5 毁灭（死 / 入狱 / 疯 / 流亡 + 也可能 anagnorisis）",
            "famous_works": "莎士比亚四大悲剧《麦克白》《哈姆雷特》《李尔王》《奥赛罗》/《俄狄浦斯王》/《教父》迈克尔（开始拒绝家族 + 最终成最冷酷教父）/《绝命毒师》Walter White（教师 → 毒枭 → 失去家庭）/《了不起的盖茨比》/《浮士德》（卖魂给魔鬼）/《雷雨》",
            "modern_examples": "《华尔街之狼》/《社交网络》扎克伯格（虚构线）/《纸牌屋》Frank Underwood / 《金钱》/《华尔街》（Gordon Gekko）/《大空头》/《饥饿游戏 3》斯诺总统",
            "tragic_pleasure": "亚里士多德 Catharsis 净化 / 通过观看悲剧释放观众的怜悯与恐惧 / 我们看主角毁灭获得情感清洁 + 也提醒自己别犯同样错",
            "common_hamartia_types": "Hubris 傲慢 / Greed 贪婪 / Lust 欲念 / Wrath 愤怒 / Envy 嫉妒 / Pride 骄傲 / Naivete 天真 / Loyalty to wrong person 对错的人忠诚",
            "twentieth_century_anti_hero": "20 世纪反英雄悲剧：主角不是高贵但有共鸣 / Death of a Salesman 《推销员之死》Willy Loman / 老人与海 + 平凡人的悲剧",
            "subverted_recovery": "现代版：跌落不必死 + 可以爬回来（《当幸福来敲门》）/ 但传统悲剧必死或毁灭",
            "narrative_use": "都市霸总跌落 / 仙侠魔头由来 / 重生救赎线 / 历史人物（雍正 / 慈禧 / 拿破仑）/ 商战巅峰跌落",
            "activation_keywords": ["跌落神坛", "悲剧", "Hubris", "Hamartia", "麦克白", "李尔王", "教父", "绝命毒师", "毁灭"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("悲剧", ""), llm_note("Aristotle Poetics")],
        tags=["剧情", "悲剧", "通用"],
    ),
    # 救赎线
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="plot-pattern-redemption-arc",
        name="救赎弧（Redemption Arc）",
        narrative_summary="主角或反派曾犯错 → 内心觉醒 → 行动赎罪 → 自我或他人原谅。"
                          "可以是配角洗白 + 反派转白 + 主角自我救赎。"
                          "经典：辛德勒名单 + 老人与海 + 罪与罚 + 哈利波特斯内普。",
        content_json={
            "five_stage_arc": "Stage 1 罪行（角色曾做大恶 + 杀人 + 背叛 + 弃儿）/ Stage 2 良心唤醒（事件触发 + 看到受害者 + 重逢 + 失去亲人）/ Stage 3 内疚潜行（私下做小善事 + 不被发现 + 自我惩罚）/ Stage 4 公开赎罪（一次重大牺牲 + 救人 + 忏悔 + 自首）/ Stage 5 平静（被原谅 / 自我和解 / 死也算 / 没被原谅但内心释然）",
            "famous_works": "《辛德勒名单》辛德勒（纳粹商人 → 救犹太人 1200）/《罪与罚》拉斯柯尔尼科夫（杀放高利贷老太婆 → 自首服刑 → 索尼娅救赎）/《老人与海》/《救赎》（Stephen King 中篇 + 改电影）/《海上钢琴师》/《哈利波特》斯内普（暗中保护哈利 + 死前真相）/《指环王》波罗米尔（被戒指诱惑 → 牺牲救霍比特人）/《古墓丽影》劳拉（不算典型）",
            "key_motifs": "1) 镜像反派：救赎者帮助受害者类似自己曾害过的 / 2) 牺牲：必须是真正的代价（生命 / 名声 / 爱情）/ 3) 不求原谅：救赎者不期望被原谅 + 反而更动人 / 4) 第三方见证：让旁观者认可（妻子 + 儿子 + 朋友）",
            "subtypes": "1) 反派洗白（《狮子王》刀疤不行 + 但弟弟苏鲁可 / 龙母弧错位）/ 2) 配角觉醒（斯内普 / 朱迪与狐狸搭档）/ 3) 主角自我救赎（《肖申克的救赎》安迪平反）/ 4) 群体救赎（曾参战暴行的退伍兵 / 《现代启示录》）",
            "twelve_step_inspired": "现代救赎弧借鉴匿名戒酒会：承认错误 + 找回信仰 + 道歉给受害人 + 服务他人 / 治愈系作品常用",
            "danger_signs": "Redemption Equals Death 救赎等于死 / 太多作品让赎罪角色必死才算赎罪 / 现代越来越多让活下来继续行善 / 但仍是常见套路",
            "vs_revenge": "复仇是把别人毁了 / 救赎是把自己重塑 / 一硬一软 / 经典作品常常两条线交织",
            "rl_examples": "辛德勒（真实）/ 阿尔弗雷德诺贝尔（弟弟葬礼讣告 = 死亡商人 → 设诺贝尔奖）/ 比尔盖茨（垄断 → 慈善基金）",
            "narrative_use": "反派洗白线 / 主角内心 + 复仇双线 / 都市重生（前世害人重生赎）/ 仙侠（魔头改邪归正）/ 言情（前任错过赎罪）",
            "activation_keywords": ["救赎", "Redemption", "辛德勒", "罪与罚", "斯内普", "肖申克", "赎罪", "悔改"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("救赎主题", ""), llm_note("Redemption arc")],
        tags=["剧情", "救赎", "通用"],
    ),
    # 时间循环
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="plot-pattern-time-loop",
        name="时间循环（Time Loop）",
        narrative_summary="主角被困在某段时间反复重生。每次循环带前世记忆。"
                          "经典：土拨鼠日 / 明日边缘 / 你的名字 / 罗拉快跑。"
                          "适用悬疑 / 重生 / 末日求解 + 心灵成长。",
        content_json={
            "core_mechanics": "1) 循环触发条件（睡 / 死 / 到固定时间）/ 2) 记忆是否保留（必须保留才有意思）/ 3) 物件是否带回（不能带 = 智力游戏 / 能带 = 攻略游戏）/ 4) 别人是否知道（默认只主角知）/ 5) 解开循环的钥匙（学会某事 / 救某人 / 改变心态）",
            "five_phase_arc": "Phase 1 困惑（第一两次循环 + 不敢相信 + 实验）/ Phase 2 滥用（享受不死 + 玩弄世界 + 成熟版加恶趣味）/ Phase 3 厌倦（一切重复无意义 + 抑郁倾向 + 自杀但又复活）/ Phase 4 觉醒（找到目标 + 救某人 + 改某事 + 学会某项技能 + 心灵成长）/ Phase 5 解开（完成关键事件 + 循环结束 + 时间继续）",
            "famous_works": "《土拨鼠日》Groundhog Day 1993 / 《明日边缘》Edge of Tomorrow / 《罗拉快跑》Run Lola Run / 《你的名字》（半时间循环 + 半时空交错）/《明日的我与昨日的你约会》/ 《忌日快乐》/《盗梦空间》（梦中循环）/《Re:从零开始的异世界生活》（动漫）/《Russian Doll》Netflix",
            "japanese_isekai_ohshu": "Re:Zero（菜月昴每死必循环）+ 异世界食堂 + 多个 isekai 用 / 受动漫深远影响",
            "subgenres": "1) 单日循环（土拨鼠日 / 忌日快乐）/ 2) 战斗循环（明日边缘）/ 3) 短期间隔循环（罗拉快跑 + 几分钟）/ 4) 死亡触发循环（Re:Zero）/ 5) 长循环（数年）/ 6) 共同循环（双人或多人共享）",
            "philosophical_themes": "存在主义 / Sisyphus 西西弗推石头 / 重复中找意义 / 改变环境不如改变自我 / 终归一事被困但精神可超越 / 命运 vs 自由意志",
            "common_milestones": "第 1 次：困惑 / 第 5 次：开始记下细节 / 第 50 次：抑郁 / 第 100 次：开始系统改进 / 第 1000 次：达成几乎所有可能 / 关键次：找到出口",
            "key_resolution_keys": "1) 救特定人 / 2) 学会爱 / 3) 心灵成长接受现实 / 4) 解决一个外部谜题（凶手是谁）/ 5) 道歉 / 6) 自我牺牲 / 7) 接受循环本身",
            "novel_adaptations": "网文大爱：重生流（一次性循环 = 重生）+ 多次循环流（《全职高手》《诡秘之主》部分桥段）+ 系统流（带任务循环）",
            "narrative_use": "悬疑（每次循环揭一点真相）/ 求生（每次循环活更久）/ 言情（追到对方）/ 修仙（重生改命）/ 哲学（卡缪式叩问）",
            "activation_keywords": ["时间循环", "土拨鼠日", "明日边缘", "Re:Zero", "你的名字", "重生", "西西弗", "命运"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("时间循环", ""), llm_note("Time loop")],
        tags=["剧情", "时间循环", "通用"],
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
