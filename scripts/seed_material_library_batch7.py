"""
Batch 7: character_templates + device_templates for major genres,
plus locale/faction expansion for new genres.
Focus: fill the two thinnest dimensions (char_templates=3 genres, device_templates=3 genres)
across 7+ new genres, and deepen coverage of 玄幻/洪荒/无限流/重生/机甲.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bestseller.infra.db.session import session_scope
from bestseller.services.material_library import insert_entry, MaterialEntry


def wiki(title: str, hint: str = "") -> dict:
    return {"type": "wikipedia", "title": title, "note": hint}

def llm_note(note: str) -> dict:
    return {"type": "llm_synth", "note": note}

ENTRIES = [
    # ─────────────────────────────────────────────────────────────
    # CHARACTER TEMPLATES — 玄幻
    # ─────────────────────────────────────────────────────────────
    MaterialEntry(
        dimension="character_templates", genre="玄幻",
        slug="xhuan-ct-废材觉醒者",
        name="废材逆袭型主角",
        narrative_summary="表面灵根废材/丹田破损，实则体内潜藏上古异体或意外获得传承，"
                          "从被所有人嘲笑的底层开始以不可思议的速度觉醒，以『我不信命』的意志对抗天道设定。",
        content_json={
            "archetype_basis": "废材觉醒者 / 洛奇英雄",
            "surface_flaw": "灵根废/丹田破/血脉无/天赋零",
            "hidden_advantage": "上古异体 / 特殊体质 / 先天道体 / 来自前世的记忆",
            "core_wound": "被家族放弃 / 被未婚妻退婚 / 被宗门驱逐",
            "growth_trigger": "绑定传承空间 / 巧合获得功法 / 意外激活血脉",
            "voice_markers": "不解释、不妥协，行动前冷笑一声；对敌人嘲讽极有力；对盟友极忠诚",
            "relationship_dynamic": "有一两个真正相信他的配角；与强权家族形成对立",
            "activation_keywords": ["逆天改命", "废材崛起", "我偏不信", "灵根觉醒", "血脉苏醒"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[llm_note("玄幻主角原型分析"), wiki("玄幻小说", "废材流")],
        tags=["玄幻", "废材", "逆袭", "主角"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="玄幻",
        slug="xhuan-ct-家族黑马",
        name="低调家族黑马",
        narrative_summary="出身大家族非嫡系、常被忽视，暗中以极强的自制力和规划力积累资源，"
                          "在关键时刻一鸣惊人。与废材主角不同：他从不被看扁，只是主动保持低调。",
        content_json={
            "archetype_basis": "谋士型英雄 / 渔翁得利者",
            "public_persona": "平平无奇的家族旁系子弟",
            "actual_capability": "极强的资源整合力 / 隐藏的特殊感悟",
            "strategic_style": "不出手则已，出手必定充分准备；擅长借力打力",
            "blind_spot": "情感表达困难 / 对亲情有执念",
            "tension_source": "家族嫡系的压制 vs 自身积累的爆发",
            "activation_keywords": ["厚积薄发", "隐忍不发", "蛰伏", "家族大比"],
        },
        source_type="llm_synth", confidence=0.68,
        source_citations=[llm_note("玄幻配角型主角分析")],
        tags=["玄幻", "谋略", "低调", "家族"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="玄幻",
        slug="xhuan-ct-古神意志觉醒者",
        name="古神意志宿主",
        narrative_summary="体内沉睡着上古神魔的意识残留或印记，平时与宿主共存，关键时刻觉醒提供力量，"
                          "同时带来身份认同危机——『我究竟是我，还是那个意志的延伸』。",
        content_json={
            "archetype_basis": "双重身份 / 寄生存在",
            "tension_engine": "宿主自我意志 vs 上古存在的冲击",
            "power_cost": "意志觉醒时失去部分自我控制；周期性幻境",
            "external_perception": "他人认为其危险 / 被追杀 / 被视为禁忌",
            "character_arc": "从抵抗到接受再到融合",
            "activation_keywords": ["古神苏醒", "意识残留", "印记", "禁忌体质", "融合"],
        },
        source_type="llm_synth", confidence=0.70,
        source_citations=[llm_note("双重存在型主角叙事分析")],
        tags=["玄幻", "古神", "双重人格", "禁忌"],
    ),

    # ─────────────────────────────────────────────────────────────
    # CHARACTER TEMPLATES — 武侠
    # ─────────────────────────────────────────────────────────────
    MaterialEntry(
        dimension="character_templates", genre="武侠",
        slug="wuxia-ct-mentor-ghost",
        name="已故师尊意志残留",
        narrative_summary="师门被灭，唯一传人带着师尊留在体内或器物中的一缕意识独行江湖，"
                          "一面复仇一面在交流中逐渐理解师尊当年选择的真相。",
        content_json={
            "archetype_basis": "复仇者 + 指引者二合一",
            "unique_mechanic": "师尊意识作为内心对话者，提供武学指导同时揭示过去的秘密",
            "emotional_core": "主角无法彻底放手，因为『失去』这师尊意味着真的孤身了",
            "武侠_specific": "门派秘技传承/武学残图/仇人名单三件套",
            "relationship_arc": "从依赖指引到独立判断，最终让师尊安息",
            "activation_keywords": ["遗命", "孤身", "门派灭门", "复仇", "武学传承"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[llm_note("武侠复仇主角模板"), wiki("武侠小说", "复仇线")],
        tags=["武侠", "复仇", "传承", "师门"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="武侠",
        slug="wuxia-ct-mad-swordsman",
        name="疯癫剑痴型强者",
        narrative_summary="痴迷于武道极致，为求境界突破几乎舍弃了正常人的情感联结，"
                          "行事不拘常理，既是同伴中最强的依靠也是最难预测的变数。",
        content_json={
            "archetype_basis": "天才型边缘人物",
            "武功_style": "自成一派 / 残缺功法练出独特境界",
            "social_flaw": "不遵江湖规矩 / 对『义气』有独特理解",
            "key_paradox": "越痴迷武道越孤独，越孤独越痴迷",
            "story_role": "主角成长的参照系 / 关键战役的变量",
            "activation_keywords": ["剑痴", "武道极致", "超凡入圣", "不疯魔不成活"],
        },
        source_type="llm_synth", confidence=0.70,
        source_citations=[llm_note("武侠偏执型配角/对手分析")],
        tags=["武侠", "剑客", "天才", "孤独"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="武侠",
        slug="wuxia-ct-female-xia",
        name="女侠独行客",
        narrative_summary="有家仇或义愤，独立行事不依附任何帮派，既有剑侠的硬气也有女性视角下江湖的双重压力，"
                          "往往比男性角色要付出更多才能被接纳。",
        content_json={
            "archetype_basis": "独行女侠 / 反传统",
            "gender_tension": "江湖以男性话语为主，她的能力需要反复证明",
            "武功_flavor": "轻盈飘逸型 / 或反直觉的刚猛型",
            "romance_handling": "不主动依赖，对伴侣要求对等",
            "activation_keywords": ["女侠", "独行", "巾帼", "江湖路", "剑客"],
        },
        source_type="llm_synth", confidence=0.68,
        source_citations=[llm_note("武侠女性角色设计"), wiki("女侠", "武侠小说")],
        tags=["武侠", "女性", "独立", "江湖"],
    ),

    # ─────────────────────────────────────────────────────────────
    # CHARACTER TEMPLATES — 历史/宫斗
    # ─────────────────────────────────────────────────────────────
    MaterialEntry(
        dimension="character_templates", genre="历史",
        slug="hist-ct-庶出谋士",
        name="庶出贵族谋士",
        narrative_summary="生于显贵之家却因庶出身份无法继承，以超群的谋略游走于权力中心，"
                          "目标不是名分而是真实影响力，内心深处隐藏着对正名的执念与对嫡系的复杂情感。",
        content_json={
            "social_position": "庶出 / 旁系 / 功臣之后",
            "power_base": "知识/谋略/人脉，而非血统",
            "blind_spot": "对家族情感的纠结影响判断",
            "narrative_function": "为主角提供谋略支持 / 代表智识阶层的困境",
            "historical_analogs": ["诸葛亮侧面", "范增", "张良"],
            "activation_keywords": ["运筹帷幄", "庶出", "谋士", "幕僚", "智者"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[wiki("谋士", "中国历史"), llm_note("历史谋士原型")],
        tags=["历史", "谋士", "庶出", "权谋"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="宫斗",
        slug="palace-ct-外来妃嫔",
        name="带政治使命入宫的外来妃嫔",
        narrative_summary="代表家族或母国利益入宫，初期政治性格鲜明，"
                          "随情节发展在家国使命与个人情感之间撕裂，最终做出无论哪边都会失去的选择。",
        content_json={
            "entry_motivation": "家族联姻 / 外邦和亲 / 母亲遗命",
            "initial_strategy": "明确政治目标，感情是手段",
            "crisis_point": "爱上皇帝/发现家族的阴谋 → 使命与情感冲突",
            "potential_arcs": ["为家国牺牲个人", "背叛家族选择个人", "寻找两全之法（几乎总是失败）"],
            "宫斗_skills": "表面温顺内心坚韧/擅长外交辞令",
            "activation_keywords": ["和亲", "母国", "使命", "两难", "外邦妃"],
        },
        source_type="llm_synth", confidence=0.71,
        source_citations=[wiki("后宫", "宫斗小说"), llm_note("宫斗政治婚姻角色")],
        tags=["宫斗", "政治婚姻", "家国", "情感冲突"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="宫斗",
        slug="palace-ct-暗棋皇子",
        name="被废置的暗棋皇子",
        narrative_summary="早年被视为威胁而低调蛰伏，表面上已被放弃争位，"
                          "实则在积蓄力量等待时机，与后宫某妃嫔形成互利的秘密同盟。",
        content_json={
            "public_front": "平庸、无争心、甘于平淡",
            "actual_chess": "暗中经营朝野关系 / 秘密培养势力",
            "alliance_type": "与女主或某妃嫔的利益同盟 → 可能发展为真情",
            "activation_keywords": ["韬光养晦", "暗棋", "皇子", "同盟", "假败真胜"],
        },
        source_type="llm_synth", confidence=0.69,
        source_citations=[llm_note("宫斗男性角色原型分析")],
        tags=["宫斗", "皇子", "谋略", "同盟"],
    ),

    # ─────────────────────────────────────────────────────────────
    # CHARACTER TEMPLATES — 悬疑/末日/言情
    # ─────────────────────────────────────────────────────────────
    MaterialEntry(
        dimension="character_templates", genre="悬疑",
        slug="susp-ct-创伤侦探",
        name="带创伤的天才侦探",
        narrative_summary="因亲历某起案件而产生心理创伤，这份创伤既是他推理直觉的来源，也是他最大的破绽。"
                          "案件调查过程往往同时是他自我疗愈或自我毁灭的过程。",
        content_json={
            "archetype_basis": "受损的英雄 / 创伤驱动者",
            "detective_strength": "对犯罪心理的直觉性理解 / 能感知被害者恐惧",
            "personal_weakness": "情绪失控临界点 / 酗酒或某种强迫行为",
            "narrative_double": "追凶 = 在追自己过去的那个阴影",
            "real_analogs": ["福尔摩斯的冷漠", "哥伦波的伪装", "博世的固执"],
            "activation_keywords": ["创伤侦探", "推理直觉", "心理阴影", "破案与自我"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("创伤知情护理", "心理学"), llm_note("侦探原型分析")],
        tags=["悬疑", "侦探", "创伤", "心理"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="末日",
        slug="apoc-ct-前警察保护者",
        name="前警察/军人保护者",
        narrative_summary="末日前是体制内的执法者，末日后体制消失，旧身份带来的价值观与新的弱肉强食规则冲突。"
                          "扮演保护者角色，但内心在『旧世界的法治信仰』与『现实的生存需要』之间撕裂。",
        content_json={
            "pre_apocalypse": "警察/军人/消防员",
            "skill_set": "战术训练/物资管理/团队协作",
            "core_conflict": "旧价值观（保护所有人）vs 末日现实（资源不够保所有人）",
            "arc": "从坚守规则到务实妥协 / 或反向：在废墟中重建秩序信仰",
            "activation_keywords": ["末日警察", "执法者", "秩序", "生存道德"],
        },
        source_type="llm_synth", confidence=0.71,
        source_citations=[llm_note("末日幸存者原型"), wiki("后启示录小说", "人物类型")],
        tags=["末日", "军人", "道德冲突", "保护者"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="言情",
        slug="rom-ct-独立女主",
        name="事业独立的现代女主",
        narrative_summary="在职场或事业上有明确独立性，感情上不主动依赖，"
                          "遇见男主后的挣扎不是爱不爱，而是「爱了又如何，我还是我自己」。",
        content_json={
            "professional_identity": "职场精英/创业者/艺术家——身份是她的盔甲",
            "romantic_barrier": "过去的伤痛 / 对依赖的恐惧 / 独立价值观",
            "breakthrough_moment": "男主展示真正尊重她独立性的行动，而非试图保护她",
            "避坑": "不要让独立性在第二幕突然消失变成依赖型",
            "activation_keywords": ["独立女性", "职场", "都市言情", "强女主", "自我价值"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[llm_note("现代言情女主角色设计")],
        tags=["言情", "都市", "独立", "女性成长"],
    ),

    # ─────────────────────────────────────────────────────────────
    # DEVICE TEMPLATES — 玄幻
    # ─────────────────────────────────────────────────────────────
    MaterialEntry(
        dimension="device_templates", genre="玄幻",
        slug="xhuan-dt-空间储物戒",
        name="储物空间类宝物",
        narrative_summary="玄幻世界最基础的空间道具，从廉价的小储物袋到价值连城的上古空间戒指，"
                          "是财富与地位的标志，也是隐藏秘密的容器——许多关键情节转折依赖其内含物的揭露。",
        content_json={
            "物品类型": "戒指/手镯/玉佩/古书等容纳小型子空间",
            "tier_system": "灵级/宝级/王级/皇级/圣级空间",
            "narrative_uses": ["藏匿禁书或禁物", "存放战利品引发觊觎", "意外发现旧主人遗留的秘密"],
            "conflict_source": "持有者死亡后戒指认主的规则产生争夺",
            "activation_keywords": ["储物戒指", "空间戒", "认主", "储物袋"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[llm_note("玄幻道具体系分析"), wiki("玄幻小说道具", "")],
        tags=["玄幻", "道具", "空间", "宝物"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="玄幻",
        slug="xhuan-dt-残破古剑",
        name="残破上古神兵",
        narrative_summary="上古大战遗留的残损神器，普通人眼中不过一块废铁，"
                          "但在主角觉醒血脉或修为提升后逐渐恢复，每次修复都揭示更多上古历史真相。",
        content_json={
            "progression": "废铁→显现纹路→认主→恢复部分能力→完全苏醒",
            "backstory_function": "通过武器记忆展现上古大战经过",
            "relationship": "武器有残留意识，与主角形成类伙伴关系",
            "plot_trigger": "觊觎此武器的势力持续追杀",
            "activation_keywords": ["上古神兵", "认主", "觉醒", "神器残片", "古剑"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[llm_note("武器觉醒叙事结构")],
        tags=["玄幻", "神兵", "觉醒", "上古"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="玄幻",
        slug="xhuan-dt-功法残篇",
        name="残缺上古功法",
        narrative_summary="传说中的无上功法只剩残缺部分，宗门视之为镇派之宝但无人能修炼，"
                          "主角因特殊体质能解读并逐步补全，核心张力是残缺本身带来的风险与诱惑。",
        content_json={
            "completeness": "1/3 → 1/2 → 大成 → 圆满",
            "risk_mechanic": "残缺部分强行修炼会走火入魔",
            "补全途径": "远古遗迹/敌人手中残页/机缘领悟",
            "设定呼应": "功法作者的身份往往与主角身世相连",
            "activation_keywords": ["残缺功法", "走火入魔", "无上功法", "功法圆满"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("玄幻功法道具叙事")],
        tags=["玄幻", "功法", "残缺", "传承"],
    ),

    # ─────────────────────────────────────────────────────────────
    # DEVICE TEMPLATES — 武侠/历史/悬疑/末日/宫斗
    # ─────────────────────────────────────────────────────────────
    MaterialEntry(
        dimension="device_templates", genre="武侠",
        slug="wuxia-dt-武学秘籍",
        name="绝世武学秘籍",
        narrative_summary="武林中流传的最高武学手册，拥有者即可傲视群雄，"
                          "因此成为各方势力争夺的核心驱动力，往往以『其实真正的秘籍在人心』收尾。",
        content_json={
            "narrative_role": "麦高芬 / 争夺焦点 / 武功突破的钥匙",
            "typical_forms": ["绣在衣物或器皿上的图谱", "前辈隐藏在寻常物件中的口诀", "人皮书"],
            "plot_reveals": "秘籍真正的主人 / 上一代恩怨 / 功法的禁忌代价",
            "江湖动乱_trigger": "秘籍曝光→各方出手→乱世始",
            "activation_keywords": ["武功秘籍", "武林至宝", "九阴真经式", "图谱"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[wiki("武功秘籍", "武侠"), llm_note("武侠道具叙事")],
        tags=["武侠", "秘籍", "武功", "争夺"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="历史",
        slug="hist-dt-先帝遗诏",
        name="先帝遗诏/密旨",
        narrative_summary="皇帝临终或秘密下达的诏令，持有者拥有合法性与政治武器，"
                          "但真伪难辨，往往是各方政治角力的核心道具——谁能证明遗诏是真，谁就能站在大义之上。",
        content_json={
            "power_source": "皇权正统性的物化",
            "dispute_axis": "真伪鉴定 / 持有权争夺 / 解读权（遗诏措辞可多义）",
            "historical_analogs": ["顺治遗诏康熙继位争议", "雍正继位传说", "汉景帝传位"],
            "narrative_uses": ["阻止政变的紧急工具", "政敌翻身的依据", "权臣篡改的目标"],
            "activation_keywords": ["遗诏", "密旨", "矫诏", "衣带诏", "传位诏书"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("遗诏", "中国历史"), llm_note("历史权谋道具")],
        tags=["历史", "权谋", "遗诏", "皇权"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="悬疑",
        slug="susp-dt-死者遗物",
        name="死者的关键遗物",
        narrative_summary="死者生前的某件物品（日记/手机/照片/信件）成为破案线索的核心容器，"
                          "承载着真相但也往往被篡改或不完整，侦探必须通过解读遗物来还原事实。",
        content_json={
            "物品类型": ["个人日记（纸质/电子）", "手机通讯记录", "遗书（可能是伪造的）", "照片/视频"],
            "narrative_function": "悬念保持器——读者与侦探同步解码",
            "red_herring_potential": "遗物本身可能被凶手置换或修改",
            "emotional_dimension": "读遗物=进入死者的内心世界，制造移情",
            "activation_keywords": ["遗物", "日记", "手机取证", "死亡证据", "线索物"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("悬疑推理道具设计"), wiki("物证", "刑事调查")],
        tags=["悬疑", "推理", "线索", "遗物"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="末日",
        slug="apoc-dt-旧世界许可证",
        name="旧世界权限证件",
        narrative_summary="末日前政府/军队颁发的许可证或识别码，在某些尚有秩序的据点仍具有效力，"
                          "但也成为伪造、抢夺的目标——谁拥有旧世界的「合法性凭证」，谁就有入场资格。",
        content_json={
            "types": ["军队ID卡", "CDC/实验室权限证", "核设施通行证", "旧政府官员证件"],
            "value_in_new_world": "进入特定据点/获得稀缺资源/免除某些检查",
            "conflict": "证件原主人的身份可能带来追杀 / 旧体制内的人对证件有强烈执念",
            "thematic_resonance": "旧世界符号在废墟中的意义——规则消失了，证件还剩什么",
            "activation_keywords": ["许可证", "身份证件", "旧世界凭证", "通行证", "末日"],
        },
        source_type="llm_synth", confidence=0.70,
        source_citations=[llm_note("末日道具叙事分析")],
        tags=["末日", "证件", "权力", "旧世界"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="宫斗",
        slug="palace-dt-毒物香料",
        name="宫廷毒物与香料",
        narrative_summary="宫廷争斗中「下毒」是核心手段，毒物往往伪装成香料、茶叶或补品，"
                          "解药与毒本身同样稀缺且是筹码——持有解药者掌握主动权。",
        content_json={
            "categories": ["慢毒（损耗型）", "迷药（控制型）", "绝育/堕胎类", "假死药"],
            "香料功能": "遮盖毒气味 / 香料本身含药效 / 身份标识",
            "获取来源": "宫廷御医 / 私下江湖药商 / 外邦进贡",
            "counter_mechanics": "银针验毒（不一定有效）/ 太医鉴定 / 体质抗药",
            "narrative_beats": ["投毒→暗查→嫁祸→清白", "持解药要挟对方"],
            "activation_keywords": ["宫廷毒药", "投毒", "慢毒", "解药", "香料"],
        },
        source_type="llm_synth", confidence=0.71,
        source_citations=[llm_note("宫廷剧毒物叙事"), wiki("宫廷阴谋", "历史")],
        tags=["宫斗", "毒药", "道具", "阴谋"],
    ),

    # ─────────────────────────────────────────────────────────────
    # FACTIONS — 玄幻/无限流/重生/机甲
    # ─────────────────────────────────────────────────────────────
    MaterialEntry(
        dimension="factions", genre="玄幻",
        slug="xhuan-fac-ancient-sects",
        name="三足鼎立宗门格局",
        narrative_summary="玄幻大陆标准势力格局：至少三支以上历史悠久的修炼宗门相互制衡，"
                          "有明确的意识形态差异（正/邪/中立/独立），主角往往从最弱势的一方起步。",
        content_json={
            "structure_model": "正道联盟 vs 魔道势力 vs 散修自由联盟",
            "internal_tension": "每方内部也有派系之争，没有纯粹的好与坏",
            "protagonist_entry": "被逐出主流宗门 / 加入弱小新生宗门 / 游离于三方之外",
            "conflict_drivers": ["宗门资源争夺", "功法传承冲突", "历史旧账引爆"],
            "political_analogs": "宗派政治 ≈ 冷战大国博弈的微缩版",
            "activation_keywords": ["正道宗门", "魔道", "散修", "宗门大战", "宗门格局"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("玄幻势力格局分析")],
        tags=["玄幻", "宗门", "势力", "格局"],
    ),
    MaterialEntry(
        dimension="factions", genre="玄幻",
        slug="xhuan-fac-imperial-clan",
        name="皇族与帝国体系",
        narrative_summary="玄幻世界中掌握凡俗政权的皇族，拥有大量世俗资源与部分修炼支持，"
                          "与纯粹修炼宗门既有合作又有张力——皇帝可以调动军队，却不能对付飞升期强者。",
        content_json={
            "power_legitimacy": "世俗法统 + 皇家修炼传承",
            "tension_with_sects": "宗门不受皇权约束，但需要皇权提供税收/人口/资源",
            "protagonist_relationship": "被皇族追杀 / 受皇族庇护 / 成为皇族合作对象",
            "narrative_use": "世俗政治危机 vs 修炼世界格局的交叉点",
            "activation_keywords": ["帝国皇族", "皇权", "凡俗帝国", "皇室修炼"],
        },
        source_type="llm_synth", confidence=0.70,
        source_citations=[llm_note("玄幻世界权力结构")],
        tags=["玄幻", "皇族", "帝国", "政治"],
    ),
    MaterialEntry(
        dimension="factions", genre="无限流",
        slug="wuxian-fac-player-guild",
        name="老玩家情报垄断公会",
        narrative_summary="无限流世界中，经历多个副本的老玩家结成公会，垄断关键副本情报并以此换取资源，"
                          "新玩家要么加入要么对抗——但公会的情报未必准确，也可能专门卖假消息。",
        content_json={
            "power_source": "信息垄断 + 集体行动能力",
            "internal_structure": "核心圈（完整信息）/ 外围成员（部分信息）/ 仆从型关系",
            "protagonist_tension": "公会规则束缚自由 / 但独立玩家死亡率极高",
            "betrayal_axis": "公会核心成员出卖同伴换取好处",
            "activation_keywords": ["玩家公会", "情报费", "老玩家", "信息差", "无限流"],
        },
        source_type="llm_synth", confidence=0.71,
        source_citations=[llm_note("无限流势力设计")],
        tags=["无限流", "公会", "信息垄断", "玩家"],
    ),
    MaterialEntry(
        dimension="factions", genre="机甲",
        slug="mecha-fac-military-elite",
        name="军方驾驶员精英派系",
        narrative_summary="掌握最先进机甲的正规军精英驾驶员群体，内部以战功为尊，"
                          "对体制外的改装派/黑客派高度警惕，视其为不稳定因素，但战场上又不得不依赖其能力。",
        content_json={
            "hierarchy": "战绩排名 / 机甲等级 / 任务执行率",
            "ideology": "服从体制 / 规则优先 / 技术标准化",
            "tension_with_protagonist": "主角往往走非主流路线，触犯精英体系规则",
            "internal_conflict": "精英内部也有对改革派/科技激进派的秘密支持",
            "activation_keywords": ["驾驶员精英", "机甲军团", "战功体系", "正规军"],
        },
        source_type="llm_synth", confidence=0.70,
        source_citations=[llm_note("机甲小说势力设计")],
        tags=["机甲", "军队", "精英", "体制"],
    ),

    # ─────────────────────────────────────────────────────────────
    # LOCALE TEMPLATES — 玄幻/洪荒/无限流/重生
    # ─────────────────────────────────────────────────────────────
    MaterialEntry(
        dimension="locale_templates", genre="玄幻",
        slug="xhuan-lt-trial-secret-realm",
        name="宗门试炼秘境",
        narrative_summary="由古代大能或天然地脉形成的封闭空间，定期开启供弟子修炼竞争，"
                          "内有随机生成的机缘与危险——是玄幻小说标配的竞技场，也是主角第一次展示真实实力的舞台。",
        content_json={
            "spatial_rules": "外部时间减慢 / 功力限制（往往有天花板）/ 杀戮限制（或无限制）",
            "resource_ecology": ["灵草灵兽聚集", "古人遗留的机缘", "随机出现的宝物"],
            "danger_sources": ["其他参与者", "秘境中的残余意识或守护兽", "地形陷阱"],
            "narrative_uses": "强行制造跨阶层接触点 / 同届对手关系建立",
            "atmosphere": "密林/迷宫/古战场——不同设定风格各异",
            "activation_keywords": ["试炼秘境", "宗门大比", "秘境开启", "机缘宝物"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("玄幻场景设计")],
        tags=["玄幻", "秘境", "竞争", "修炼"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre="玄幻",
        slug="xhuan-lt-ancient-battlefield",
        name="上古战场遗址",
        narrative_summary="远古大战后留下的能量污染区域，充斥着残余战意和未散的法则力量，"
                          "是危险与宝物并存的极端环境——能活着走出来的人往往获得了质变。",
        content_json={
            "atmosphere": "扭曲的空间规则 / 飘荡的残魂 / 未腐朽的古代武器",
            "entry_cost": "往往需要特定血脉或令牌才能进入",
            "danger_types": ["残余战意侵蚀神识", "古代机关陷阱", "争夺宝物的当代强者"],
            "reward_potential": "上古级功法/神兵/战争记忆（剧情回忆）",
            "activation_keywords": ["古战场", "上古遗址", "战意残留", "废墟探索"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[llm_note("玄幻遗址场景分析")],
        tags=["玄幻", "遗址", "探索", "危险"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre="洪荒",
        slug="hong-lt-chaos-edge",
        name="混沌边界荒原",
        narrative_summary="洪荒世界天地初开时混沌尚存的边缘地带，法则稀薄，时空不稳定，"
                          "充满了被遗忘的上古存在和已成混沌之气的失败演化物——是洪荒世界的「化外之地」。",
        content_json={
            "spatial_rules": "五行法则不完整 / 时间流速异常 / 随时可能坍塌回混沌",
            "inhabitants": "未进化完全的洪荒异兽 / 失败的生命演化体 / 混沌生灵",
            "why_enter": "混沌之气可作最高级炼丹材料 / 混沌灵宝藏匿于此",
            "atmosphere": "一片虚白与随机闪烁的灵气团，无方向感",
            "activation_keywords": ["混沌边界", "洪荒", "混沌之气", "化外"],
        },
        source_type="llm_synth", confidence=0.71,
        source_citations=[wiki("混沌（道教）", ""), llm_note("洪荒场景设计")],
        tags=["洪荒", "混沌", "边界", "原始"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre="无限流",
        slug="wuxian-lt-cleared-dungeon",
        name="已清空副本的残余空间",
        narrative_summary="被其他玩家完成过的副本世界，NPC/剧情已消失，只剩下空壳般的场景，"
                          "但偶尔存在隐藏剧情和未被发现的资源——老玩家知道但不说，是新玩家意外收获的来源。",
        content_json={
            "state": "NPC已消失 / 剧情树清零 / 但地形/建筑保留",
            "hidden_content": "隐藏支线（触发条件复杂）/ 遗漏的稀有物品",
            "atmosphere": "鬼城般的静寂，空荡荡的原本热闹的场景",
            "exploitation_tension": "在空副本里安全但效率低——冒险进活副本高风险高回报",
            "activation_keywords": ["已清副本", "鬼城副本", "隐藏剧情", "无限流"],
        },
        source_type="llm_synth", confidence=0.69,
        source_citations=[llm_note("无限流场景设计")],
        tags=["无限流", "副本", "废弃", "隐藏"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre="重生",
        slug="rebirth-lt-key-memory-location",
        name="重生者的记忆地标",
        narrative_summary="重生者前世中的某个特殊地点——发生过决定性事件的地方，"
                          "重生后再次踏入时产生强烈的时间错位感，是展示重生者内心状态的绝佳叙事场景。",
        content_json={
            "type_examples": ["前世遭背叛的地点", "前世最爱之人离开的地方", "前世死亡的场所"],
            "narrative_effect": "强烈的内心独白机会 / 展示重生者的双重时间感",
            "paradox": "现在看起来普通，但主角知道这里会发生什么",
            "emotional_use": "决心（这次绝不重蹈覆辙）的戏剧化确立场所",
            "activation_keywords": ["记忆地标", "重生地点", "前世", "时间错位"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[llm_note("重生叙事场景设计")],
        tags=["重生", "记忆", "地标", "情感"],
    ),

    # ─────────────────────────────────────────────────────────────
    # SCENE TEMPLATES — 新增高价值场景
    # ─────────────────────────────────────────────────────────────
    MaterialEntry(
        dimension="scene_templates", genre="玄幻",
        slug="xhuan-st-breakthrough",
        name="突破境界场景",
        narrative_summary="修炼者突破关键境界的核心体验场景：内部世界的剧变（灵力冲关）"
                          "与外部环境的共鸣（天地异象），是玄幻小说最标志性的高光时刻之一。",
        content_json={
            "internal_experience": "灵力壁垒瓦解 / 内视丹田变化 / 神识扩张",
            "external_manifestation": ["引发天地异象（雷劫/灵雨）", "周围生灵感应汇聚", "空间短暂扭曲"],
            "tension_source": "突破中途被干扰（天敌攻击/内心劫难）",
            "emotional_beats": "临界绝望→坚持→突破→短暂空白→狂喜",
            "common_mistakes": "每次突破都用完全相同的描写 → 要有差异化",
            "activation_keywords": ["突破境界", "天地异象", "灵力冲关", "境界突破"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("玄幻关键场景设计")],
        tags=["玄幻", "突破", "修炼", "场景"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="武侠",
        slug="wuxia-st-final-duel",
        name="决战场景",
        narrative_summary="武侠小说终极对决的叙事规范：这不只是武功高低的较量，"
                          "更是两种人生信念的碰撞——胜负已分时，真正的主题才通过对白显露。",
        content_json={
            "environment": "极端地形（悬崖/废墟/大雪/黑夜）对应内心状态",
            "pre_duel_ritual": "沉默的对视 / 简短的话语确立各自立场",
            "fight_narration": "不要堆砌招式名称——聚焦身体感知/意识流动/情绪",
            "turning_point": "某句话或某个动作引发对手心理动摇",
            "post_victory": "胜者的沉默 / 败者的话语往往比活人更重要",
            "activation_keywords": ["决战", "最终对决", "武林绝顶", "巅峰对决"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("武侠决战场景分析"), wiki("金庸小说", "决战桥段")],
        tags=["武侠", "决战", "场景", "信念"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="宫斗",
        slug="palace-st-alliance-negotiation",
        name="宫中密谈结盟场景",
        narrative_summary="两方势力在宫廷中的秘密谈判——每个人都在表面话语下藏着另一层意思，"
                          "读者需要同时理解表面层和真实层，这是宫斗写作最考验技巧的场景类型。",
        content_json={
            "双层对话结构": "表面内容（礼仪/寒暄/模糊承诺）vs 底层意图（威胁/要价/试探）",
            "环境细节": "室内光线/茶具/衣着暗示力量对比",
            "信号系统": "一个不经意的眼神/动作代表「同意」或「拒绝」",
            "stakes": "谈判失败的代价必须清晰，读者才能感受张力",
            "exit_strategy": "每方都留退路，没人会彻底承诺",
            "activation_keywords": ["宫中密谈", "结盟", "双层对话", "宫廷谈判"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("宫斗场景写作技巧")],
        tags=["宫斗", "密谈", "结盟", "双层叙事"],
    ),

    # ─────────────────────────────────────────────────────────────
    # EMOTION ARCS — 重生/机甲/无限流
    # ─────────────────────────────────────────────────────────────
    MaterialEntry(
        dimension="emotion_arcs", genre="重生",
        slug="rebirth-ea-survivor-guilt-redemption",
        name="幸存者内疚→救赎情感弧",
        narrative_summary="重生者前世因自己的错误或软弱导致他人死亡，"
                          "重生后虽有能力改写结局，但那份内疚本身要求比『改写』更深层的清算——承认错误、坦承自己、真正改变。",
        content_json={
            "guilt_origin": "前世的懦弱/出卖/自私导致的他人悲剧",
            "surface_action": "重生后主动保护那些前世死亡的人",
            "deeper_truth": "保护行动本身可能是逃避而非救赎",
            "crisis_beat": "被保护对象知道真相后的反应",
            "resolution_condition": "真正的救赎需要主角承认并说出前世的错误",
            "activation_keywords": ["幸存者内疚", "救赎", "重生", "弥补", "前世错误"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("重生叙事情感核心"), wiki("幸存者内疚", "心理学")],
        tags=["重生", "内疚", "救赎", "情感弧"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre="机甲",
        slug="mecha-ea-human-machine-bond",
        name="人机融合情感弧",
        narrative_summary="驾驶员与机甲之间超出工具关系的情感联结——机甲有某种意识或情绪反馈，"
                          "失去机甲等同于失去伙伴，围绕这种特殊关系建立的情感弧。",
        content_json={
            "bond_formation": "初期抵触（机甲只是工具）→ 战场中的无声配合 → 情感确认",
            "bond_crisis": "机甲被摧毁/改造/上缴，驾驶员的失去感",
            "philosophical_tension": "机甲是否真的有意识？还是驾驶员的投射？",
            "relationship_parallel": "往往与人际关系弧并行——学会信任机甲=学会信任他人",
            "activation_keywords": ["人机融合", "机甲伙伴", "机甲意识", "驾驶员情感"],
        },
        source_type="llm_synth", confidence=0.70,
        source_citations=[llm_note("机甲小说情感核心"), wiki("人机交互", "")],
        tags=["机甲", "人机", "伙伴", "情感"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre="无限流",
        slug="wuxian-ea-humanity-erosion",
        name="人性侵蚀情感弧",
        narrative_summary="在无限副本中不断杀戮和生存后，主角逐渐变得更高效但更冷漠，"
                          "核心情感弧是觉察到这一变化的某个人（往往是配角）或场景，引发主角对自我的追问。",
        content_json={
            "erosion_markers": "越来越快速的决策（牺牲他人）/ 情绪反应迟缓 / 对死亡的麻木",
            "mirror_character": "某个保有人性的配角作为对比",
            "crisis_trigger": "被迫伤害真正在意的人 / 意识到自己在计算一个人的「性价比」",
            "resolution_options": ["接受蜕变（黑化路线）", "主动抵抗（保有人性代价）", "找到平衡点"],
            "activation_keywords": ["人性侵蚀", "无限流", "冷血", "人性代价", "道德滑落"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[llm_note("无限流道德成本叙事")],
        tags=["无限流", "人性", "道德", "情感弧"],
    ),

    # ─────────────────────────────────────────────────────────────
    # ANTI-CLICHE — 新增跨类型反套路
    # ─────────────────────────────────────────────────────────────
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="玄幻",
        slug="xhuan-ac-反天才即废材",
        name="废材必逆袭陷阱",
        narrative_summary="『废材一定会逆袭』已成读者默认预设，真正的张力来自让读者不确定主角能否成功。"
                          "应设计真实的代价、非线性的成长，和至少一次『差点就失败了』的时刻。",
        content_json={
            "cliché": "废材开局→必有逆袭秘宝→一路升级无人拦",
            "reader_effect": "预期落空=无聊；需要在预期内制造意外",
            "fix_strategies": [
                "逆袭过程中有真实的失去（友情/亲人/身体代价）",
                "强调废材体质本身带来的永久性限制",
                "某些能力确实差，通过策略和情报弥补",
            ],
            "gold_standard": "逆袭后仍然有局限，不是无敌而是独特",
            "activation_keywords": ["废材逆袭反套路", "非线性成长", "代价", "不完美主角"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[llm_note("玄幻反套路创作指南")],
        tags=["玄幻", "反套路", "废材", "创作技巧"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="宫斗",
        slug="palace-ac-反全知女主",
        name="全知全能宫斗女主陷阱",
        narrative_summary="重生/穿越宫斗女主总是「比所有人都聪明」，导致张力消失。"
                          "优秀的宫斗女主应该有信息盲区、有真正的失败时刻、有自己造成的困境。",
        content_json={
            "cliché": "女主掌握所有情报/从不中计/每步都对",
            "fix_strategies": [
                "给对手真正的信息优势（比女主更了解某人/某事）",
                "女主的某个决定造成真正无法弥补的代价",
                "情感而非策略的地方是真实弱点",
            ],
            "好的张力来源": "女主足够聪明但不是神，对手足够危险但不是恶棍",
            "activation_keywords": ["宫斗全知主角", "真实代价", "信息不对称", "反套路宫斗"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("宫斗叙事反套路分析")],
        tags=["宫斗", "反套路", "女主", "张力"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre=None,
        slug="gen-ac-反升级无止境",
        name="无止境升级的空洞感",
        narrative_summary="修炼/成长类作品的最大陷阱：升级变成机械行为，读者对每次突破都麻木了。"
                          "升级需要伴随真实的情感变化和叙事意义，而不仅仅是数值提升。",
        content_json={
            "symptom": "主角每章突破→读者无感→作者用更大的敌人强行制造紧张",
            "root_cause": "升级与人物内在成长脱节",
            "fix_framework": [
                "每次关键突破应与角色的情感/价值观的变化同步",
                "设置真实的『达到天花板』时刻",
                "突破的代价（失去某能力/某人/某记忆）比收益更难忘",
            ],
            "benchmark": "《钢之炼金术师》的等价交换法则——升级必须有真实代价",
            "activation_keywords": ["升级空洞感", "代价机制", "成长与变化", "反刷级"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[llm_note("修炼类反套路"), wiki("Sanderson定律", "魔法设计")],
        tags=["通用", "反套路", "升级", "成长"],
    ),
]


async def main(dry_run: bool = False) -> None:
    print(f"{'[DRY RUN] ' if dry_run else ''}Seeding {len(ENTRIES)} entries...\n")

    from collections import Counter
    genre_counter: Counter = Counter()
    dim_counter: Counter = Counter()
    for e in ENTRIES:
        genre_counter[e.genre or "(通用)"] += 1
        dim_counter[e.dimension] += 1

    print(f"By genre:     {dict(genre_counter)}")
    print(f"By dimension: {dict(dim_counter)}\n")

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
