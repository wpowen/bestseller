"""
Batch 17: Writing craft mastery — narrative techniques, POV, time, voice,
foreshadowing, motif weaving, unreliable narrator, narrative distance.
All universal entries that activate sophisticated craft knowledge for any genre.
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
    # ═══════════════════════════════════════════════════════════════
    # Narrative Time / Pacing
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="craft-narrative-time-management",
        name="叙事时间管理（场景 vs 概述 vs 省略）",
        narrative_summary="叙事时间三态：场景（实时同步，1页 = 1分钟）/ 概述（压缩，1页 = 1天/1月）/ 省略（跳过空白）。"
                          "高手通过这三态切换控制阅读节奏：关键时刻拉到场景级、过渡用概述、无意义时段省略。",
        content_json={
            "three_modes": "Scene 场景：实时展开对话动作（最详细）/ Summary 概述：压缩跨度叙述 / Ellipsis 省略：直接跳过用换章/分隔符标记",
            "ratio_guideline": "高潮 80% 场景；过渡 60% 概述；铺垫 40% 概述 + 60% 场景",
            "switching_signals": "时间副词（『三天后』『当夜』）/ 段落切换 / 分隔符 *** / 章节边界",
            "common_mistakes": "全用场景节奏拖沓 / 全用概述无沉浸 / 不当省略漏关键过程",
            "narrative_use": "任何题材；调节阅读爽点密度",
            "activation_keywords": ["叙事时间", "场景", "概述", "省略", "实时", "压缩", "节奏"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("叙事学", ""), llm_note("叙事时间管理")],
        tags=["写作技巧", "叙事时间", "节奏"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="craft-flashback-techniques",
        name="闪回（Flashback）技法",
        narrative_summary="闪回是打断当下叙述插入过往。三种用法：信息 dump（背景揭露）/ 情感 anchor（主角动机根源）/ 反转伏笔（解锁记忆推进剧情）。"
                          "需明确触发点（味道/物件/对话）+ 清晰边界（斜体/换段/时态）+ 与当前剧情同步推进。",
        content_json={
            "three_uses": "1) 揭露背景（如何走到今天）/ 2) 锚定情感（主角伤痕来源）/ 3) 解锁伏笔（被压抑记忆突然涌出）",
            "trigger_types": "感官触发（气味/声音/物件）/ 对话触发（敌人提起往事）/ 梦境触发 / 重返故地",
            "boundary_marking": "斜体 / 换段 + 时态切换（过去完成时）/ 章节单独闪回 / 视觉符号（『—— 十年前 ——』）",
            "common_pitfalls": "闪回过长拖慢主线 / 信息 dump 而无情感 / 与当下叙事脱节 / 时间线混乱",
            "famous_examples": "《教父》青年维多闪回 / 《Lost》岛民前传 / 《盗梦空间》层层梦境",
            "narrative_use": "悬疑揭谜 / 重生穿越 / 心理向 / 言情身世",
            "activation_keywords": ["闪回", "倒叙", "记忆涌现", "时间切换", "前史", "触发点"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("闪回 (叙事)", ""), llm_note("Flashback craft")],
        tags=["写作技巧", "闪回", "叙事"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="craft-foreshadowing-payoff",
        name="伏笔与回收（Setup & Payoff）",
        narrative_summary="伏笔是早期『种』，回收是后期『收』。三层伏笔：明面（细节注入）→ 中段（旁敲侧击）→ 高潮（爆点回收）。"
                          "必须『种了不显眼，收时一击致命』。Chekhov 之枪：第一幕墙上挂枪，第三幕必须开火。",
        content_json={
            "three_layers": "明面（一句细节）/ 旁敲（重复出现的物件人物）/ 爆点回收（高潮时揭示）",
            "chekhov_principle": "出现的元素必须有作用，不能浪费观众注意力 → 反过来：未来要爆发的元素必须提前出现",
            "best_practices": "首次出现轻描淡写 / 中间偶尔重提（埋深）/ 最终爆发时读者『啊！原来如此』恍然大悟",
            "famous_examples": "《哈利波特》斯内普 vs 莉莉记忆全 7 部跨度回收 / 《冰与火之歌》布兰看到屋顶 / 《大宋提刑官》凶器隐喻",
            "common_mistakes": "伏笔太显眼 = 剧透 / 伏笔从未回收 = 浪费 / 回收时读者忘记 = 缺中段重复",
            "narrative_use": "悬疑必备 / 长篇都市 / 重生（重生信息逐步释放）/ 仙侠（前世今生）",
            "activation_keywords": ["伏笔", "回收", "Setup", "Payoff", "Chekhov", "前期种子", "后期爆发"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("伏笔", ""), wiki("契诃夫之枪", ""), llm_note("Foreshadowing craft")],
        tags=["写作技巧", "伏笔", "结构"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # POV / Voice
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="craft-pov-mastery",
        name="POV 叙述视角精控",
        narrative_summary="第一人称（我）/ 第二人称（你）/ 第三人称受限（紧贴一个角色）/ 第三人称全知（上帝）。"
                          "现代爽文主流是『紧贴主角的第三人称受限』+ 偶尔切换其他人物 POV 揭示主角看不到的伏笔。",
        content_json={
            "first_person": "强代入感 / 主观偏见 / 不可靠叙事可能 / 但视野受限",
            "third_limited": "紧贴一个角色（『他想』）/ 既有代入又能切角度 / 现代主流",
            "third_omniscient": "上帝视角，可任意切人物 / 适合史诗规模 / 但易疏离",
            "second_person": "罕见，强调读者代入（你做了 X）/ 多用于游戏书 / 部分实验性小说",
            "switching_rules": "每章只切一次 POV / 章节边界清晰标注 / 切换时新视角必须带新信息",
            "common_pitfalls": "POV 角色不知情却描述出来（POV 漏洞）/ 频繁切换造成混乱",
            "narrative_use": "言情双 POV 男女各章 / 悬疑多线索 POV / 都市单一 POV 紧贴主角",
            "activation_keywords": ["POV", "视角", "第一人称", "第三人称受限", "上帝视角", "切换"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("视角 (文学)", ""), llm_note("POV 写作技巧")],
        tags=["写作技巧", "POV", "视角"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="craft-unreliable-narrator",
        name="不可靠叙事者",
        narrative_summary="叙述者本身有问题——撒谎、欺骗自己、有精神疾病、记忆错乱、立场偏颇——读者最终发现他不能信。"
                          "适合心理悬疑、回忆体小说、反转题材。代表《了不起的盖茨比》尼克、《消失的爱人》艾米。",
        content_json={
            "types": "撒谎型（明知故说假）/ 自欺型（真信但错）/ 精神病变型 / 记忆受损型 / 偏见型 / 顽童型（认知有限）",
            "techniques": "细节自相矛盾 / 用词不准 / 关键时刻含糊 / 第二次复述与第一次不符 / 旁人评价反差",
            "famous_examples": "《了不起的盖茨比》尼克 / 《消失的爱人》艾米 + 尼克双 POV / 《一个陌生女人来信》/ 《告白》湊佳苗多视角",
            "reader_experience": "前期信任 → 中期生疑 → 后期颠覆认知 → 重读发现暗示密布",
            "narrative_use": "心理悬疑 / 反转结尾 / 言情虐恋（恋爱中谎言）/ 重生不可靠回忆",
            "activation_keywords": ["不可靠叙事", "撒谎", "自欺", "记忆错乱", "颠覆", "反转", "重读"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("不可靠的叙述者", ""), llm_note("Unreliable narrator")],
        tags=["写作技巧", "叙事者", "悬疑"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # Show vs Tell / Subtext
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="craft-show-not-tell",
        name="Show, Don't Tell（展示而非告知）",
        narrative_summary="不要告诉读者『他很愤怒』，而是展示『他攥紧拳头指节发白』。"
                          "情感、性格、关系都通过具象动作/细节/对白让读者自己体会，而非作者直接断言。",
        content_json={
            "telling_examples": "他很伤心 / 她非常聪明 / 他们关系很差 / 她有钱 / 他生气了",
            "showing_equivalents": "他没说话，只是看着雨打窗 / 她三秒就解开题 / 两人吃饭从不对视 / 钥匙串挂着保时捷标 / 茶杯被他摔在地上",
            "exceptions": "节奏需要时也能 tell（过渡段落）/ 概述模式必然 tell / 关键情感时刻必须 show",
            "tools_for_showing": "动作（攥拳/转身）/ 表情微变 / 对白（说反话/沉默）/ 物件（摔杯/丢戒指）/ 环境（雨夜独行）",
            "common_mistakes": "全 show 拖沓节奏 / 全 tell 失去沉浸 / 关键场景偷懒 tell",
            "narrative_use": "言情情感颗粒度 / 心理描写 / 人物建模 / 任何强情感场景",
            "activation_keywords": ["展示", "Show", "Tell", "具象", "细节", "动作", "对白"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("Show, don't tell", ""), llm_note("Show-not-tell craft")],
        tags=["写作技巧", "展示", "细节"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="craft-subtext-iceberg",
        name="对白潜文本与冰山理论",
        narrative_summary="海明威『冰山理论』：水面 1/8 是写出来的，水下 7/8 是潜文本（subtext）。"
                          "高手对白：表面在聊天气，潜在层在权力博弈、情感试探、立场暴露。"
                          "读者通过线索自行解读冰下，故事得以浓缩深邃。",
        content_json={
            "iceberg_principle": "海明威：写一座冰山，露出 1/8，水下 7/8 是读者通过细节自己感受到的力量",
            "subtext_techniques": "答非所问（拒绝触及核心）/ 重复一个无关词暗示焦虑 / 沉默 / 谈天说地实际权斗 / 冷笑话掩饰悲伤",
            "famous_examples": "《老人与海》/ 《白象似的群山》（2 人聊堕胎全程没说『堕胎』二字）/ Pinter 戏剧",
            "writer_questions": "这场对话表面是 X，实际想要 Y / 双方真实意图 / 读者听到的弦外之音",
            "narrative_use": "言情试探（含蓄表白）/ 商战谈判（话里有话）/ 权谋（每句都暗藏立场）",
            "activation_keywords": ["潜文本", "Subtext", "冰山理论", "海明威", "弦外之音", "答非所问"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("冰山理论", ""), llm_note("Subtext craft")],
        tags=["写作技巧", "对白", "潜文本"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # Structural Patterns
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="craft-three-act-structure",
        name="三幕剧结构",
        narrative_summary="经典叙事 25-50-25 比例：第一幕 setup（建立世界 + 主角 + 冲突），第二幕 confrontation（升级冲突 + 中点反转），第三幕 resolution（高潮 + 解决）。"
                          "好莱坞 / 商业小说通用骨架。",
        content_json={
            "act_1_setup": "前 25%：日常 → 召唤 → 拒绝 → 接受 → 跨过门槛进入新世界（Plot Point 1）",
            "act_2_confrontation": "中间 50%：试炼 → 盟友与敌人 → 中点（midpoint）大反转 → 接近核心 → 至暗时刻（Plot Point 2）",
            "act_3_resolution": "末 25%：第三次行动 → 决战 → 死亡复活 → 凯旋归来",
            "key_beats": "Inciting Incident 触发事件 / Plot Point 1（出第一幕）/ Midpoint 中点反转 / Plot Point 2（黑夜将晓）/ Climax 高潮",
            "famous_users": "Syd Field 编剧理论 / Robert McKee / Save the Cat（Blake Snyder 25 拍）",
            "narrative_use": "短篇/中篇/单卷书 / 任何题材；爽文体可压缩到第一卷",
            "activation_keywords": ["三幕剧", "Setup", "Confrontation", "Resolution", "中点反转", "Plot Point"],
        },
        source_type="llm_synth", confidence=0.87,
        source_citations=[wiki("三幕剧", ""), llm_note("Three-act structure")],
        tags=["结构", "三幕剧", "通用"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="craft-five-act-structure",
        name="五幕剧（Freytag 金字塔）",
        narrative_summary="德国 Freytag 五幕：Exposition → Rising Action → Climax → Falling Action → Denouement。"
                          "比三幕更细腻，适合长篇/悲剧/古典戏剧。莎士比亚/古希腊悲剧的骨架。",
        content_json={
            "five_acts": "1) 铺陈 → 2) 上升动作 → 3) 高潮 → 4) 下降动作 → 5) 结局",
            "comparison_to_3act": "1+2 = 第一幕 / 3 = 第二幕中点+第三幕高潮 / 4+5 = 落下与回归",
            "famous_users": "莎士比亚悲剧（《哈姆雷特》《李尔王》）/ 古希腊悲剧 / 古典歌剧",
            "narrative_use": "长篇连载（每『卷』一个五幕循环）/ 古风历史悲剧",
            "activation_keywords": ["五幕剧", "Freytag", "高潮", "下降动作", "结局"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("Freytag's pyramid", ""), llm_note("Five-act structure")],
        tags=["结构", "五幕", "古典"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="craft-kishotenketsu",
        name="起承转合（東亞四段結構）",
        narrative_summary="東亞经典四幕：起（介绍）→ 承（发展）→ 转（突变）→ 合（收束）。"
                          "不依赖『冲突』推进，而依赖『转』的意外+『合』的回归。"
                          "日韩中漫画/中短篇/抒情散文常用。比三幕更含蓄。",
        content_json={
            "ki": "起 Ki：建立场景人物日常",
            "sho": "承 Sho：自然延展铺垫",
            "ten": "转 Ten：意外/反转/视角切换（核心机关，与西方『冲突』不同）",
            "ketsu": "合 Ketsu：意外回归收束 / 留白 / 余韵",
            "comparison_to_west": "西方靠 Conflict-Resolution 推动；起承转合靠 Surprise-Return 节奏",
            "famous_examples": "日本四格漫画 / 唐诗律诗结构 / 《细语》（吉田秋生）",
            "narrative_use": "言情含蓄 / 古风诗意 / 灵异散文化 / 现代抒情都市",
            "activation_keywords": ["起承转合", "Kishotenketsu", "起", "承", "转", "合", "意外", "回归"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("起承轉合", ""), llm_note("起承转合通识")],
        tags=["结构", "東亞", "古典"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="craft-save-the-cat-beats",
        name="《拯救小猫》15 拍结构",
        narrative_summary="Blake Snyder 商业电影编剧 15 拍模板：Opening Image → Theme Stated → Setup → Catalyst → Debate → Break Into 2 → B Story → Fun and Games → Midpoint → Bad Guys Close In → All Is Lost → Dark Night → Break Into 3 → Finale → Final Image。"
                          "好莱坞/网剧/商业小说精确刻度。",
        content_json={
            "15_beats": "1) 开场画面 / 2) 主题陈述 / 3) 铺陈 / 4) 触发事件 / 5) 辩论 / 6) 进入第二幕 / 7) B 故事 / 8) 游戏与娱乐 / 9) 中点 / 10) 反派逼近 / 11) 一切尽失 / 12) 黑夜灵魂 / 13) 进入第三幕 / 14) 决战 / 15) 终幕画面",
            "page_targets_110": "1=p1 / 5=p1 / 8=p10 / 12=p25 / 15=p30 / 25=p55 / 35=p55 / 50=p75 / 55=p85 / 60=p85 / 65=p85 / 75=p85 / 85=p85 / 110=p110",
            "narrative_use": "短篇 / 短书 / 网剧改编向 / 节奏紧凑爽文每卷可套",
            "famous_users": "皮克斯电影 / 商业网文 / 好莱坞类型片",
            "activation_keywords": ["Save the Cat", "拯救小猫", "15 拍", "中点", "黑夜灵魂", "B 故事"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("Save the Cat!", ""), llm_note("Blake Snyder 15 beats")],
        tags=["结构", "好莱坞", "商业"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # Symbol / Motif
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="craft-motif-weaving",
        name="意象编织（Motif Weaving）",
        narrative_summary="一个意象在小说中反复出现，每次承载不同情感重量，最终成为主题的视觉锚点。"
                          "如《了不起的盖茨比》绿光 / 《百年孤独》黄蝴蝶 / 《追风筝的人》风筝。"
                          "高手以意象贯穿全书，避免主题口号化。",
        content_json={
            "definition": "重复出现的具体物象（颜色/物件/天气）—— 第一次出现时无重量，反复出现累积情感，最后一次回归即主题彰显",
            "principles": "1) 必须具象（不是抽象概念）/ 2) 与情感同步演化 / 3) 最少出现三次 / 4) 最后一次必含改变",
            "famous_examples": "《盖茨比》对岸绿光 / 《百年孤独》黄蝴蝶 / 《追风筝的人》风筝 / 《情人》湄公河 / 《活着》黄牛 / 《雷雨》四凤手腕",
            "design_steps": "1) 确定主题 → 2) 找一个具象代表 → 3) 在开头/中段/结尾各埋一次 → 4) 每次微变",
            "narrative_use": "文学性提升 / 主题视觉化 / 情感连续累积 / 任何题材",
            "activation_keywords": ["意象", "Motif", "重复", "贯穿", "象征", "视觉锚点"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("文学母题", ""), llm_note("Motif weaving craft")],
        tags=["写作技巧", "意象", "主题"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="craft-color-symbolism",
        name="颜色象征系统",
        narrative_summary="颜色在叙事中天然带情感符号：红（血/激情/危险）、白（纯洁/死亡）、蓝（忧郁/冷静）、黑（神秘/恐惧）、金（权力/腐败）。"
                          "不同文化语境略有差异（中国白丧西方白婚）。可组合形成场景情绪谱。",
        content_json={
            "primary_colors_emotion": "红：血/爱/危险/警示 / 白：纯洁/死亡（中）/婚礼（西）/医院 / 黑：神秘/恐惧/死亡 / 蓝：忧郁/冷静/水/远方 / 金：权力/腐败/夕阳 / 绿：生机/嫉妒/腐朽",
            "cultural_differences": "中：白色丧 / 红色喜 / 黄色帝王 / 西：白色婚 / 黑色丧 / 紫色皇室",
            "literary_uses": "《红楼梦》红楼绛色（女儿）/ 《白鹿原》白鹿与黑娃 / 《大红灯笼高高挂》大红压抑 / 《辛德勒名单》黑白片中红色女孩",
            "design_principles": "全书统一色调 / 关键场景颜色对位 / 角色色彩标识 / 场景情绪用颜色衬托",
            "narrative_use": "文学化提升 / 视觉化叙事 / 情感强化 / 任何题材",
            "activation_keywords": ["颜色", "象征", "红白黑蓝金", "色调", "情绪"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("颜色象征", ""), llm_note("Color symbolism")],
        tags=["写作技巧", "象征", "颜色"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # Conflict Theory
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="craft-conflict-types-deepened",
        name="冲突七型深化",
        narrative_summary="冲突七大类型：人 vs 人 / 人 vs 自然 / 人 vs 社会 / 人 vs 自我 / 人 vs 命运 / 人 vs 科技 / 人 vs 超自然。"
                          "好的故事至少同时跑两条冲突线，主线 + 内心。",
        content_json={
            "seven_conflicts": "1) 人 vs 人（最常见）/ 2) 人 vs 自然（求生）/ 3) 人 vs 社会（体制）/ 4) 人 vs 自我（成长）/ 5) 人 vs 命运（宿命）/ 6) 人 vs 科技（赛博）/ 7) 人 vs 超自然（灵异/玄幻）",
            "layering_principle": "外冲突推动情节 + 内冲突推动人物弧；两者必须有意义地连接",
            "examples": "《老人与海》= 人 vs 自然 + 人 vs 自我 / 《1984》= 人 vs 社会 + 人 vs 自我 / 《指环王》= 人 vs 人 + 人 vs 命运 + 人 vs 自我",
            "common_mistakes": "只跑外冲突剧情爽但人物薄 / 只跑内冲突过于沉闷 / 内外不连接两条线脱节",
            "narrative_use": "构建立体冲突 / 任何题材升级",
            "activation_keywords": ["冲突", "人vs自然", "人vs自我", "人vs社会", "外冲突", "内冲突"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("文学冲突", ""), llm_note("Conflict types deep")],
        tags=["写作技巧", "冲突", "结构"],
    ),
]


async def main() -> None:
    print(f"Seeding {len(ENTRIES)} entries...\n")
    by_genre = {}
    by_dim = {}
    inserted = 0
    errors = 0
    async with session_scope() as session:
        for e in ENTRIES:
            try:
                await insert_entry(session, e, compute_embedding=True)
                inserted += 1
                by_genre[e.genre or "(通用)"] = by_genre.get(e.genre or "(通用)", 0) + 1
                by_dim[e.dimension] = by_dim.get(e.dimension, 0) + 1
            except Exception as exc:
                errors += 1
                print(f"ERROR {e.slug}: {exc}")
        await session.commit()
    print(f"By genre:     {by_genre}")
    print(f"By dimension: {by_dim}")
    print(f"\n\u2713 {inserted} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    asyncio.run(main())
