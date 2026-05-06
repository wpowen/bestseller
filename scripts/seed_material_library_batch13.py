"""
Batch 13: Universal writing craft + Eastern philosophy/aesthetics + character psych deepening.
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
    # ═══════════ EASTERN PHILOSOPHY / AESTHETICS ═══════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-japanese-aesthetics",
        name="日本美学激活层（物哀/侘寂/幽玄）",
        narrative_summary="日本传统美学的核心概念：物哀（mono no aware,事物中流露的哀感）、"
                          "侘寂（wabi-sabi,残缺之美）、幽玄（深邃神秘之美）、间（ma,留白）。"
                          "这些概念深化了对『美』的理解，可让中文创作的情感层次更丰富。",
        content_json={
            "core_concepts": {
                "物哀": "对事物本身的深沉感动——樱花的美在于其转瞬",
                "侘寂": "残缺、朴素、自然的美——茶碗的裂痕反而珍贵",
                "幽玄": "看不见的深意——黑夜中船笛的孤独",
                "间": "空白的力量——画卷中未画出的部分",
                "无常": "万物变迁的悲悯——人生如梦",
            },
            "Chinese_compatible": "与禅宗、道家自然观、宋代文人画美学相通",
            "narrative_uses": "言情中的离别美感 / 灵异中的物哀传递 / 历史中的时代悲悯",
            "activation_keywords": ["物哀", "侘寂", "幽玄", "无常", "日本美学"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("物哀", ""), wiki("侘寂", ""), llm_note("日本美学激活")],
        tags=["通用", "日本", "美学", "情感"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-zen-koan",
        name="禅宗公案叙事激活",
        narrative_summary="禅宗公案是用悖论和故事激发顿悟的特殊文体："
                          "『庭前柏树子』『狗有佛性否』『拈花微笑』。这种通过非逻辑达到深层理解的方法，"
                          "对深度叙事的建构是绝佳启发。",
        content_json={
            "famous_koans": {
                "拈花微笑": "佛陀拈花，迦叶微笑——无言传承",
                "狗有佛性否": "赵州的『无』——超越有无对立",
                "庭前柏树子": "如何是祖师西来意？庭前柏树子——具体即真理",
                "本来无一物": "六祖慧能——空无的大智慧",
            },
            "narrative_uses": [
                "用一个无解的问题贯穿全文",
                "用具体物象承载抽象哲理",
                "通过『答非所问』展现深度",
            ],
            "modern_inheritors": "村上春树/卡夫卡的某些荒诞场景借鉴此法",
            "activation_keywords": ["禅宗公案", "拈花微笑", "顿悟", "无", "佛性"],
        },
        source_type="llm_synth", confidence=0.77,
        source_citations=[wiki("禅宗", ""), wiki("公案", "佛教"), llm_note("禅宗公案叙事")],
        tags=["通用", "禅宗", "公案", "叙事"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-yiying-classics",
        name="易经及周易哲学激活",
        narrative_summary="易经的64卦不只是占卜工具，更是中国哲学最深的变化论："
                          "阴阳互转/物极必反/否极泰来/穷则变通。可作为玄幻/历史/政治叙事的深层结构。",
        content_json={
            "key_principles": {
                "阴阳": "对立又统一的根本动力",
                "物极必反": "极盛必衰，极衰必盛",
                "否极泰来": "最坏处转折点",
                "穷则变": "无路时必须变化",
                "变易/不易/简易": "变化中有不变的规律",
            },
            "famous_hexagrams": ["乾（创造）", "坤（顺承）", "屯（艰难初创）", "蒙（启蒙）", "需（等待）", "讼（争讼）"],
            "narrative_applications": "用卦象作为情节走向 / 用阴阳推演角色命运 / 物极必反的转折点",
            "activation_keywords": ["易经", "阴阳", "六十四卦", "物极必反", "周易"],
        },
        source_type="llm_synth", confidence=0.77,
        source_citations=[wiki("易经", ""), llm_note("易经叙事激活")],
        tags=["通用", "易经", "中国哲学", "变化"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-sun-tzu",
        name="孙子兵法叙事激活",
        narrative_summary="孙子兵法不只是军事著作，更是普适的策略论："
                          "知己知彼/不战而屈人之兵/正合奇胜。在权谋/商战/末日/机甲题材中提供经典战略框架。",
        content_json={
            "key_principles": {
                "知己知彼": "情报战的根本",
                "不战而屈": "最高境界是不战胜",
                "正合奇胜": "正面交锋+出其不意",
                "因敌制胜": "依据对手调整策略",
                "速战速决": "拖延会损耗自己",
                "用兵贵胜不贵久": "战争是手段不是目的",
            },
            "modern_applications": "商战/政治博弈/末日生存/机甲战术 都可借用",
            "Chinese_inheritors": "三十六计/孙膑兵法/吴子兵法",
            "Western_resonance": "克劳塞维茨《战争论》/ 现代博弈论",
            "activation_keywords": ["孙子兵法", "知己知彼", "兵不厌诈", "正合奇胜"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("孙子兵法", ""), llm_note("孙子兵法叙事")],
        tags=["通用", "兵法", "策略", "中国"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-classical-chinese-poetry",
        name="中国古典诗词意象库",
        narrative_summary="中国诗词中的标准意象系统：月（思乡/团圆）、柳（离别）、菊（隐逸）、剑（侠义）、酒（友谊/愁绪）、梅兰竹菊（君子）。"
                          "这些意象在中文读者心中有自动激活的情感反应，是叙事的高效情感杠杆。",
        content_json={
            "natural_images": {
                "月": "思乡/团圆/孤独/永恒",
                "柳": "离别（折柳送别）",
                "梅": "高洁/坚韧",
                "兰": "君子/孤芳",
                "竹": "正直/虚心",
                "菊": "隐逸/秋思",
                "桃": "美人/春情",
                "杨柳": "春情/温柔",
            },
            "weather_images": {
                "雨": "愁绪/离别",
                "雪": "纯洁/苦寒",
                "风": "变迁/悲凉",
                "霜": "冷酷/年华",
            },
            "human_objects": {
                "酒": "豪情/愁绪/友谊",
                "剑": "侠义/壮志",
                "笛": "孤独/漂泊",
                "舟": "漂泊/隐逸",
            },
            "narrative_uses": "用意象浓缩情感 / 让中文读者自动产生共鸣",
            "activation_keywords": ["明月", "柳枝", "梅竹", "诗词意象", "古典意境"],
        },
        source_type="llm_synth", confidence=0.79,
        source_citations=[wiki("中国诗词", ""), wiki("意象", "中国文学"), llm_note("古典诗词意象")],
        tags=["通用", "诗词", "意象", "中国文学"],
    ),

    # ═══════════ CHARACTER PSYCHOLOGY DEEPENING ═══════════
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="gen-ca-trickster",
        name="诡计师原型",
        narrative_summary="跨文化普遍存在的『诡计师』原型：用智慧而非力量获胜的人。"
                          "他们打破规则、戏弄权威、揭穿伪善——既是英雄也是麻烦制造者。"
                          "中国孙悟空、西方洛基、北欧Loki、非洲蜘蛛Anansi都是同一原型。",
        content_json={
            "core_traits": "聪明 / 机智 / 不守规则 / 道德灰色",
            "narrative_function": "推动情节 / 揭示伪善 / 提供笑料",
            "famous_examples": ["孙悟空", "洛基", "猫和老鼠", "侦探柯南"],
            "modern_variants": "反英雄主角 / 复杂的反派 / 黑客角色",
            "psychological_basis": "诡计师代表我们对规则的反抗欲望",
            "writing_principle": "诡计师必须有自己的道德底线，否则就是反派",
            "activation_keywords": ["诡计师", "孙悟空", "洛基", "智慧型主角", "反规则"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("诡计师", "神话"), wiki("猴王", "西游记"), llm_note("诡计师原型分析")],
        tags=["通用", "诡计师", "原型", "智慧"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="gen-ca-wise-fool",
        name="智者愚人原型",
        narrative_summary="表面是傻子或边缘人，实则拥有最深智慧的角色：李尔王中的弄人、济公、阿甘正传的阿甘。"
                          "用『非常人』的视角揭示常人看不见的真相。",
        content_json={
            "characteristics": "外表愚钝/言行怪异/但说出最深的话",
            "narrative_function": "用边缘视角批判主流价值",
            "social_advantage": "因被低估反而能说出禁忌话语",
            "famous_examples": ["李尔王的弄人", "济公", "阿甘", "雨人"],
            "writing_techniques": "傻言中藏锋 / 单纯逻辑揭示成人世界荒诞",
            "activation_keywords": ["智者愚人", "济公", "阿甘", "傻得透彻", "智慧的傻"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[wiki("智者愚人", "原型"), wiki("济公", ""), llm_note("智者愚人原型")],
        tags=["通用", "原型", "智者", "愚人"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="gen-ca-mentor-shadow",
        name="导师阴影面原型",
        narrative_summary="导师角色的阴影变体：教导主角的人本身有黑暗的过去/动机。"
                          "好的导师阴影不是简单的『反派伪装』，而是真实的复杂人——他既真心传授又有自己的算计。",
        content_json={
            "complexity_layers": [
                "真心想教 + 也希望主角实现自己未竟的事",
                "传授真知 + 隐瞒某些关键信息",
                "保护主角 + 也利用主角",
            ],
            "famous_examples": ["欧比旺（隐瞒了维德的事实）", "邓布利多（多重隐瞒）", "杜甫指点李商隐（虚构）"],
            "narrative_value": "比纯善导师更真实 / 比纯恶反派更有戏",
            "writing_principle": "阴影不等于背叛——好导师可以有暗面",
            "activation_keywords": ["导师阴影", "复杂导师", "亦师亦敌", "教导者的秘密"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[llm_note("导师原型变体分析")],
        tags=["通用", "导师", "阴影", "原型"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="gen-ca-femme-fatale",
        name="致命女性原型",
        narrative_summary="跨越文化的危险女性原型：美丽、智慧、致命——主角对她又爱又恨。"
                          "从希腊海妖到莎士比亚的克娄巴特拉到现代noir电影中的角色。她不是反派，是命运。",
        content_json={
            "core_paradox": "魅力的源泉同时是危险的源泉",
            "famous_archetypes": ["海妖塞壬", "克娄巴特拉", "莉莉丝", "黑色电影女主"],
            "modern_variants": "复杂女反派 / 多面女主 / 不依赖男性的强女角",
            "writing_pitfalls": "避免物化 / 给她真正的内心动机 / 让她超越『男人的考验』",
            "ethical_evolution": "现代版本应让她有自己的目的，不只是男主的镜像",
            "activation_keywords": ["致命女性", "femme fatale", "美丽危险", "蛇蝎美人"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("致命女性", "电影"), llm_note("致命女性原型分析")],
        tags=["通用", "女性", "致命", "原型"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="gen-ca-eternal-child",
        name="永恒少年原型",
        narrative_summary="拒绝长大的成年人：在心智上停留在童年的角色。"
                          "可以是有魅力的（彼得·潘式自由）也可以是悲剧的（无法承担责任）。"
                          "现代社会很多角色都是这种原型的变体。",
        content_json={
            "positive_aspects": "保持好奇心 / 不被规则束缚 / 创造力强",
            "negative_aspects": "无法承担责任 / 害怕承诺 / 逃避现实",
            "famous_examples": ["彼得·潘", "韦斯·安德森电影男主", "村上春树的某些主角"],
            "psychological_basis": "荣格的『永恒少年』—Puer Aeternus",
            "narrative_arcs": "永远长不大（悲剧） / 强行长大（成长） / 找到既不失童心又能承担责任的方式",
            "activation_keywords": ["永恒少年", "彼得·潘", "拒绝长大", "童心"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("永恒少年", "荣格心理学"), wiki("彼得·潘综合症", ""), llm_note("永恒少年原型")],
        tags=["通用", "原型", "童心", "成长"],
    ),

    # ═══════════ EMOTION ARCS DEEPENING ═══════════
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="gen-ea-grief-stages",
        name="哀伤五阶段情感弧",
        narrative_summary="库布勒-罗斯哀伤五阶段（否认/愤怒/讨价还价/抑郁/接受）作为角色面对重大失去时的情感弧线模板。"
                          "这五阶段不是线性的，常常反复——这种反复才是真实的。",
        content_json={
            "stages": {
                "1.否认": "拒绝相信失去发生 - 表面正常但内心崩溃",
                "2.愤怒": "对自己/他人/命运的愤怒 - 寻找责任人",
                "3.讨价还价": "如果当时...就好了 - 想要和命运谈判",
                "4.抑郁": "悲伤的低谷 - 真正接受失去",
                "5.接受": "整合失去并继续生活 - 不是忘记是带着记忆前行",
            },
            "writing_principles": "阶段会反复 / 不同人停留在不同阶段 / 没有人完全『走完』",
            "narrative_uses": "丧亲 / 失恋 / 重大挫折 / 末日的家园失去",
            "activation_keywords": ["哀伤五阶段", "悲伤", "失去", "库布勒-罗斯"],
        },
        source_type="llm_synth", confidence=0.80,
        source_citations=[wiki("库布勒-罗斯模型", ""), llm_note("哀伤心理学")],
        tags=["通用", "哀伤", "情感", "心理学"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="gen-ea-romantic-spiral",
        name="爱情螺旋弧",
        narrative_summary="爱情发展的真实螺旋而非直线：每一次靠近后总有一次推开/退缩，"
                          "每一次冲突后又有更深的理解。好的爱情叙事不是步步登高，而是来回反复中累积深度。",
        content_json={
            "spiral_phases": [
                "1. 初识 - 好感/警惕并存",
                "2. 第一次亲近 - 突然又退缩",
                "3. 重新接触 - 比之前更深一层",
                "4. 严重冲突 - 触及核心价值观",
                "5. 暂时分开 - 各自成长",
                "6. 重新相遇 - 都已经不一样",
                "7. 真正承诺 - 知道彼此弱点后的选择",
            ],
            "anti_pattern": "线性升温=不真实",
            "real_chemistry": "前进-退缩-前进-退缩-真正确认",
            "activation_keywords": ["爱情螺旋", "若即若离", "进退反复", "感情发展"],
        },
        source_type="llm_synth", confidence=0.77,
        source_citations=[llm_note("爱情心理学叙事")],
        tags=["通用", "爱情", "螺旋", "情感弧"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="gen-ea-revenge-trap",
        name="复仇者陷阱情感弧",
        narrative_summary="复仇者的核心悲剧：成功复仇后发现自己已经变成了仇人。"
                          "经典弧线：仇恨萌发→为复仇牺牲→变得残忍→复仇成功→空虚→醒悟『我变成了我恨的人』。",
        content_json={
            "stages": [
                "1. 受害 - 被夺去重要的人/事",
                "2. 觉醒 - 决定不再无能",
                "3. 准备 - 训练/积累/隐忍",
                "4. 行动 - 第一次伤害对方",
                "5. 滑落 - 逐渐失去最初的『正义感』",
                "6. 顶点 - 杀死/摧毁仇人",
                "7. 觉察 - 发现自己已经成为仇人的镜像",
            ],
            "endgame_options": [
                "悲剧版：完全毁灭（自杀/被杀/精神崩溃）",
                "悲悯版：放弃最后一击 / 找到救赎",
                "黑化版：彻底接受新身份",
            ],
            "famous_examples": ["基督山伯爵", "Kill Bill", "雪中悍刀行的某些角色"],
            "activation_keywords": ["复仇者陷阱", "成为仇人", "复仇代价", "黑化轨迹"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("基督山伯爵", ""), llm_note("复仇主题分析")],
        tags=["通用", "复仇", "情感弧", "黑化"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="gen-ea-imposter-syndrome",
        name="冒名顶替综合征弧",
        narrative_summary="主角取得成功但内心始终觉得『我不配』『我会被揭穿』的情感弧。"
                          "现代语境下大量人物的真实心理体验——这种自我怀疑恰恰是优秀人才的常见心境。",
        content_json={
            "core_phenomenon": "客观成就 + 主观觉得自己是骗子",
            "trigger_situations": "突然成名 / 高位 / 与名校精英对话 / 被表扬",
            "internal_dialogue": "『他们迟早会发现我并不优秀』『我只是运气好』",
            "narrative_uses": [
                "现代职场题材的真实心理",
                "穿越者的『未来知识』焦虑",
                "学霸/天才的内心独白",
            ],
            "resolution_paths": "接受自己确实优秀 / 把怀疑转化为持续努力 / 公开承认这种感受获得共鸣",
            "activation_keywords": ["冒名顶替", "我不配", "自我怀疑", "假象焦虑"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[wiki("冒充者综合征", "心理学"), llm_note("现代心理弧线")],
        tags=["通用", "冒名顶替", "焦虑", "情感弧"],
    ),

    # ═══════════ NARRATIVE TECHNIQUES ═══════════
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="gen-pp-mcguffin",
        name="麦高芬叙事装置",
        narrative_summary="希区柯克命名的叙事概念：一个驱动情节但本身不重要的物件。"
                          "重点不在它是什么，而在所有人都为它行动。手提箱里的东西是什么往往不揭示，"
                          "或揭示后让人发现整个故事其实是关于人物的。",
        content_json={
            "definition": "情节驱动器+本体不重要",
            "famous_examples": [
                "公民凯恩的玫瑰花蕾",
                "低俗小说的手提箱",
                "速激中的硬盘",
                "普通人版：求婚戒指/重要文件",
            ],
            "writing_function": "聚焦人物动机和关系，而不是物件本身",
            "trap": "如果观众过度好奇麦高芬本身就失败了",
            "activation_keywords": ["麦高芬", "驱动器", "争夺物件", "希区柯克"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("麦高芬", "电影"), llm_note("叙事装置分析")],
        tags=["通用", "麦高芬", "叙事", "技巧"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="gen-pp-frame-narrative",
        name="框架叙事结构",
        narrative_summary="故事中嵌套故事的结构：《一千零一夜》《呼啸山庄》《泰坦尼克号》都用此法。"
                          "外框故事提供视角，内核故事提供事件，两者相互照应制造独特的叙事张力。",
        content_json={
            "structure_types": [
                "讲述者讲故事（一千零一夜）",
                "找到的手稿（呼啸山庄）",
                "回忆录（泰坦尼克号）",
                "嵌套层数可有多重",
            ],
            "narrative_advantages": "提供解读视角 / 制造时间距离 / 增加可信度",
            "modern_uses": "现代/未来视角看历史 / 多代际叙事",
            "Chinese_examples": "《红楼梦》开篇的石头记框架 / 聊斋志异",
            "activation_keywords": ["框架叙事", "故事中故事", "嵌套结构", "讲述者"],
        },
        source_type="llm_synth", confidence=0.77,
        source_citations=[wiki("框架故事", ""), llm_note("叙事结构分析")],
        tags=["通用", "框架", "嵌套", "结构"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="gen-pp-dramatic-irony",
        name="戏剧性反讽设计",
        narrative_summary="读者知道角色不知道的信息所产生的张力："
                          "罗密欧不知道朱丽叶只是装死、奥赛罗不知道苔丝狄蒙娜的清白。"
                          "这种知识不对称是悲剧/喜剧的核心引擎之一。",
        content_json={
            "structural_options": [
                "读者知道+角色A不知道+角色B知道（奥赛罗）",
                "读者知道+所有角色不知道（罗密欧朱丽叶）",
                "读者知道+主角不知道+反派知道（很多悬疑）",
            ],
            "tension_dynamics": "读者眼睁睁看着角色走向悲剧",
            "writing_techniques": "在角色对话中埋伏笔 / 让角色无意中说出真相",
            "modern_uses": "悬疑大量使用 / 灵异类很常见",
            "activation_keywords": ["戏剧反讽", "信息差", "读者特权", "悲剧机制"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("戏剧性反讽", "文学"), llm_note("戏剧反讽分析")],
        tags=["通用", "反讽", "信息差", "戏剧"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="gen-pp-tragic-hero-fall",
        name="悲剧英雄陨落弧",
        narrative_summary="经典悲剧英雄的陨落弧：高贵英雄→致命缺陷暴露→错误决定→无法挽回→毁灭→读者的悲悯。"
                          "和『反英雄堕落』不同——悲剧英雄本性高贵但被命运/缺陷击败。",
        content_json={
            "core_structure": [
                "1. 高位起点（地位/才能/品德）",
                "2. 致命缺陷（傲慢/嫉妒/野心/犹豫）",
                "3. 触发事件（命运的考验）",
                "4. 错误决定（缺陷被激发）",
                "5. 后果累积（无法挽回）",
                "6. 顿悟时刻（看清自己）",
                "7. 毁灭",
            ],
            "famous_examples": ["奥赛罗", "麦克白", "李尔王", "哈姆雷特", "项羽"],
            "key_principle": "英雄必须有真实高贵以让陨落令人痛心",
            "modern_resonance": "黑化主角弧的母版",
            "activation_keywords": ["悲剧英雄", "陨落", "致命缺陷", "高贵的毁灭"],
        },
        source_type="llm_synth", confidence=0.79,
        source_citations=[wiki("悲剧英雄", "希腊戏剧"), llm_note("悲剧弧线分析")],
        tags=["通用", "悲剧", "英雄", "陨落"],
    ),

    # ═══════════ DIALOGUE STYLES UNIVERSAL ═══════════
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="gen-ds-subtext-mastery",
        name="潜台词艺术",
        narrative_summary="高水准对话的核心：人物说的话和真正想说的话之间的距离。"
                          "完全的坦诚=幼稚；完全的伪饰=做作；最好的对话是『说A暗指B』，让读者参与解码。",
        content_json={
            "subtext_levels": {
                "0层": "完全直白（小孩子）",
                "1层": "礼貌掩饰（成人日常）",
                "2层": "暗指他物（亲密关系）",
                "3层": "说反话（讽刺/自嘲）",
                "4层": "话语指向第三者而非对方",
            },
            "techniques": [
                "用一个具体物件比喻情感",
                "通过谈论别人间接谈自己",
                "礼貌的言辞下藏威胁",
                "看似无关的提问其实是核心",
            ],
            "examples": [
                "『今晚月色真美』=『我喜欢你』（夏目漱石）",
                "『天凉好个秋』=『现实让我无言』（辛弃疾）",
            ],
            "activation_keywords": ["潜台词", "言外之意", "话中有话", "弦外之音"],
        },
        source_type="llm_synth", confidence=0.79,
        source_citations=[llm_note("对话艺术分析"), wiki("潜台词", "戏剧")],
        tags=["通用", "对话", "潜台词", "技巧"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="gen-ds-power-dynamics",
        name="对话中的权力流动",
        narrative_summary="任何对话都隐含权力博弈：谁主导话题、谁被打断、谁的话被认真对待。"
                          "好的对话设计会通过这些微观信号让读者感受到权力的实时流动。",
        content_json={
            "power_signals": [
                "话题主导（谁决定谈什么）",
                "打断频率（谁打断谁）",
                "等待时间（强势者更愿等）",
                "称呼变化（亲昵/正式/居高临下）",
                "沉默使用（强势者用沉默施压）",
                "身体距离（强势者更靠近或更远）",
            ],
            "narrative_uses": [
                "面试场景的权力转移",
                "亲子对话中的隐性权力",
                "情侣对话中谁更投入",
            ],
            "writing_principle": "权力流动不是恒定的——一场对话中可以多次反转",
            "activation_keywords": ["对话权力", "主导权", "话语权", "对话博弈"],
        },
        source_type="llm_synth", confidence=0.77,
        source_citations=[wiki("权力动力学", "社会学"), llm_note("对话权力分析")],
        tags=["通用", "对话", "权力", "技巧"],
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
