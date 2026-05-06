"""
Batch 10: dialogue_styles + factions large expansion across 12+ genres.
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
    # ═══════════ DIALOGUE STYLES ═══════════
    MaterialEntry(
        dimension="dialogue_styles", genre="玄幻",
        slug="xhuan-ds-cultivator-speech",
        name="修炼者口吻",
        narrative_summary="玄幻世界修炼者的语言风格：等级悬殊带来的居高临下；"
                          "对凡俗事务的疏离感；以『道』『缘』『天』等概念替代具体动机；"
                          "高阶强者言语简短但分量极重。",
        content_json={
            "tier_speech_pattern": {
                "底层弟子": "急切/带情绪/想证明自己",
                "中阶修士": "克制/带优越感/常用门派术语",
                "高阶强者": "言简意赅/常以一字定生死/眼神比话语重要",
                "至尊": "几乎不说话/一旦开口字字诛心/对话本身已是赐予",
            },
            "vocabulary_markers": ["道友", "前辈", "缘法", "天意", "气数", "因果"],
            "avoid_modern": "禁用『没问题』『搞定』等现代口语",
            "tone_shift_in_combat": "战斗中可破例使用更直接表达",
            "activation_keywords": ["道友", "前辈", "贫道", "晚辈", "造化"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[llm_note("玄幻对话风格"), wiki("中国神魔小说", "")],
        tags=["玄幻", "对话", "修炼者", "语气"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="都市",
        slug="urban-ds-power-dialogue",
        name="都市权贵对话",
        narrative_summary="都市文中权贵阶层的对话特点：表面客套实则较劲；"
                          "用『听说...』、『有人告诉我...』等间接句揭示信息；"
                          "真正的威胁往往用最平静的语气说出。",
        content_json={
            "social_layers_speech": {
                "顶级权贵": "极简/不说废话/每句话都有目的",
                "中产精英": "信息密度大/用专业术语展示能力",
                "底层奋斗者": "情绪外露/急切/容易暴露弱点",
            },
            "subtext_layer": "表面是商业讨论，底层是势力划分",
            "indirect_threats": "「不知道X会怎么想」「我有个朋友最近...」「这事如果传到Y那里」",
            "权力_显示": "通过让对方等/打断对方/不接电话表达地位",
            "activation_keywords": ["权贵对话", "都市精英", "客套", "暗中较劲"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("都市权贵对话风格")],
        tags=["都市", "对话", "权贵", "潜台词"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="灵异",
        slug="liyi-ds-spirit-dialogue",
        name="人鬼对话",
        narrative_summary="活人与鬼魂对话的独特语言风格：鬼魂语言往往带有重复性、时代错位、"
                          "信息断裂——他们困在自己的执念里，因此对话本身就是揭示其死因和未了心愿的过程。",
        content_json={
            "ghost_speech_traits": [
                "重复某句话（执念固化）",
                "对现代事物表现疑惑（时代错位）",
                "语言断断续续（意识不完整）",
                "突然清晰说出关键信息（重要时刻）",
            ],
            "human_response_strategy": "不要直接质疑/用日常话题缓解/借物品引出记忆",
            "language_shifts": "对话过程中鬼魂逐渐想起自己是谁",
            "danger_markers": "鬼魂突然变得过于流畅 = 可能不是单纯亡灵",
            "activation_keywords": ["人鬼对话", "亡灵言语", "执念", "通灵", "阴阳交流"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("灵异对话设计")],
        tags=["灵异", "对话", "鬼魂", "语言"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="心理惊悚",
        slug="psy-ds-unreliable-narrator",
        name="不可靠叙述者口吻",
        narrative_summary="心理惊悚的核心叙述技巧：主角的内心独白看似坦诚，但留有破绽让读者察觉异常。"
                          "对话中也是同理——主角说出的话和读者从环境推断出的真相之间存在张力。",
        content_json={
            "narrator_unreliability_signals": [
                "对自己情绪的过度合理化（'我没生气'后的暴力描述）",
                "选择性遗忘关键细节",
                "用第三人称称呼自己",
                "对时间感的混乱",
            ],
            "dialogue_function": "其他角色的反应揭示主角言行的真实样貌",
            "reader_suspicion_arc": "信任→怀疑→震惊→重新解读全部前文",
            "key_techniques": "在主角『正常』叙述中夹杂极小但反常的细节",
            "activation_keywords": ["不可靠叙述", "心理悬疑", "扭曲视角", "自我蒙蔽"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[wiki("不可靠叙述者", "文学理论"), llm_note("心理惊悚叙述")],
        tags=["心理惊悚", "叙述", "不可靠", "对话"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="校园",
        slug="campus-ds-teen-vernacular",
        name="青少年校园用语",
        narrative_summary="真实校园对话的语言特点：网络流行语+学科术语+集体内梗+短句节奏。"
                          "不要写得过于成熟（少年内心独白可以深，但对话要保持年龄感）。",
        content_json={
            "age_specific_markers": {
                "初中": "简短直接/常用网络梗/情绪外放",
                "高中": "略带成年化/学习焦虑/对未来的模糊议论",
                "大学": "更接近成年人语言/但仍带集体认同感",
            },
            "dialogue_situations": ["课间闲聊", "宿舍夜谈", "考前互怼", "运动场上"],
            "trap_to_avoid": "过度使用流行语会让作品快速过时",
            "emotion_layering": "表面打趣/底层是真实关心或焦虑",
            "activation_keywords": ["校园用语", "学生对话", "青春期口吻", "课间"],
        },
        source_type="llm_synth", confidence=0.71,
        source_citations=[llm_note("校园对话设计")],
        tags=["校园", "对话", "青少年", "语言"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="重生",
        slug="rebirth-ds-double-time",
        name="重生者双重时间感对话",
        narrative_summary="重生者与他人对话时的独特张力：他知道对方未来的命运，"
                          "对方却以为是日常对话——这种信息不对称制造了大量内心戏与言外之意。",
        content_json={
            "reversal_techniques": [
                "对一句日常话产生过度反应（因为知道这话引发未来悲剧）",
                "对某人的态度突变（前世此人是仇人/恩人）",
                "提前回答尚未问的问题",
                "对未来灾难的家人讲『要小心』（被当成杞人忧天）",
            ],
            "POV_dual_layer": "主角内心独白 + 外部对话 + 对方的不解反应",
            "emotional_undertone": "面对前世挚爱/前世死亡的人时的失控反应",
            "activation_keywords": ["重生者", "双重时间", "前世记忆", "信息不对称"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[llm_note("重生叙事对话")],
        tags=["重生", "对话", "时间感", "信息差"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="美食",
        slug="food-ds-tasting-language",
        name="味觉描述语言",
        narrative_summary="美食类作品中关于味道的对话特点：通过比喻、记忆唤起、文化关联描述味觉。"
                          "好的美食对话不直接说『好吃』，而是用一段话让读者尝到那种味道。",
        content_json={
            "describing_techniques": [
                "比喻法（『像初春的雨』）",
                "记忆唤起（『让我想起小时候奶奶的灶台』）",
                "对比法（『先甜后苦，像人生』）",
                "感官联动（『酸得我眼睛都亮了』）",
            ],
            "emotional_dimension": "食物激发的不只是味觉，还有时间/地点/情感",
            "professional_vs_amateur": "专家用术语（火候/层次/回甘）；常人用情感（暖/家/想念）",
            "dialogue_chemistry": "两人一起品尝食物的对话本身是关系试探",
            "activation_keywords": ["味道描述", "品鉴用语", "美食评论", "食物对话"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("美食评论", ""), llm_note("味觉对话设计")],
        tags=["美食", "对话", "味觉", "感官"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="快穿",
        slug="kuaichuan-ds-system-banter",
        name="系统对话语气",
        narrative_summary="快穿主角与系统的对话：系统从冷漠机械→逐渐人格化的语言演变。"
                          "系统的语气既要保持非人类感（避免完全像普通AI助手），又要承担叙事节奏调节器的功能。",
        content_json={
            "early_system_voice": "纯任务播报/数据冷漠/无情感",
            "evolved_system_voice": "偶尔吐槽/有了偏好/开始吐槽宿主",
            "narrative_function": "系统是作者向读者解释设定的工具——但要做得自然",
            "common_phrases": ["叮——", "任务进度", "宿主请注意", "本系统建议"],
            "humor_potential": "系统的『理性』与主角的『情感化』互为吐槽",
            "activation_keywords": ["系统提示", "任务播报", "快穿系统", "宿主对话"],
        },
        source_type="llm_synth", confidence=0.71,
        source_citations=[llm_note("快穿系统对话设计")],
        tags=["快穿", "对话", "系统", "AI"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="萌宠",
        slug="meng-ds-pet-monologue",
        name="宠物视角内心独白",
        narrative_summary="从宠物视角讲述的内心独白：保持其物种特性（嗅觉为主/无法理解人类复杂概念）"
                          "但带有可爱的情感深度。读者最享受的是『以宠物纯真视角看人类世界荒诞』的反差。",
        content_json={
            "species_appropriate_perception": "猫的傲娇视角 / 狗的过度热情 / 兔子的警觉",
            "vocabulary_limits": "不应理解『金钱』『工作』『分手』等抽象概念，但能感受情绪",
            "emotional_depth": "对主人的依赖/对其他宠物的嫉妒/对失去的悲伤",
            "humor_source": "宠物的误解（把吵架理解为人类的奇怪游戏）",
            "tearjerker_moments": "宠物理解自己生命有限但仍选择陪伴",
            "activation_keywords": ["宠物视角", "动物内心", "萌宠独白", "毛孩子"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("萌宠叙事视角"), wiki("动物拟人化", "文学")],
        tags=["萌宠", "对话", "宠物", "视角"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="游戏",
        slug="game-ds-player-talk",
        name="游戏玩家口语",
        narrative_summary="游戏世界中玩家间的对话特点：缩写/术语密集/带玩梗倾向。"
                          "区分『游戏内对话』（即时/战术导向）和『公会群聊』（社交/八卦）。",
        content_json={
            "in_game_speech": "短促/指令式（『集合』『拉怪』『集火』）",
            "voice_chat_dynamic": "高强度战斗中的喊叫 / 任务间隙的玩笑",
            "abbreviations": ["RT", "WP", "GG", "DPS", "MT", "AOE"],
            "old_player_vs_new": "老玩家对新玩家的指导/调侃 / 新玩家的小心翼翼",
            "guild_tone": "群聊偏向社交/八卦/分享攻略",
            "activation_keywords": ["游戏对话", "玩家用语", "公会群", "副本指挥"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[llm_note("游戏类小说对话设计")],
        tags=["游戏", "对话", "玩家", "术语"],
    ),

    # ═══════════ FACTIONS expansion ═══════════
    MaterialEntry(
        dimension="factions", genre="都市",
        slug="urban-fac-power-circles",
        name="都市权力圈层",
        narrative_summary="都市背景下的隐性权力网：从顶级豪门到地方派系到江湖人脉，"
                          "彼此既合作又制衡。主角的崛起意味着不断挤入更高一层的网络。",
        content_json={
            "top_tier": "金融/政界顶级家族——人数极少但影响力遍及全国",
            "middle_tier": "省/市级豪门、跨国公司本土代理人",
            "underground_tier": "江湖帮会、地下组织、灰色行业",
            "interaction_logic": "明面合作/暗中倾轧；婚姻/继承制造网络变化",
            "narrative_use": "主角不断打破阶层壁垒/被某层势力盯上",
            "activation_keywords": ["都市豪门", "权贵圈", "地方派系", "江湖", "上层"],
        },
        source_type="llm_synth", confidence=0.71,
        source_citations=[llm_note("都市权力网设计")],
        tags=["都市", "权力", "圈层", "势力"],
    ),
    MaterialEntry(
        dimension="factions", genre="言情",
        slug="rom-fac-family-clans",
        name="豪门家族派系",
        narrative_summary="言情中常见的『家族派系』：表面是恋爱阻力，实质是不同价值观/利益的较量。"
                          "好的家族派系设计让两位主角的爱情不只是情感问题，而是两种生活方式的选择。",
        content_json={
            "family_types": ["传统精英世家", "新贵财阀", "跨国混血家族", "学术清流家族"],
            "internal_dynamics": "家长权威 / 兄弟竞争 / 联姻政治",
            "obstacle_to_protagonist": "门第偏见 / 安排联姻 / 家族秘密",
            "resolution_paths": "改变家族 / 离开家族 / 在家族内争得位置",
            "activation_keywords": ["豪门家族", "联姻", "门第", "家族阻力"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("言情家族叙事")],
        tags=["言情", "家族", "豪门", "阻力"],
    ),
    MaterialEntry(
        dimension="factions", genre="娱乐圈",
        slug="ent-fac-industry-circles",
        name="娱乐圈派系生态",
        narrative_summary="娱乐圈内部的派系结构：经纪公司体系/导演圈/资本派系/艺人小团体。"
                          "明星个人能力之外，归属哪个派系决定了能拿什么资源、能避开什么坑。",
        content_json={
            "agency_systems": "传统大经纪公司 / 工作室 / 海外分支 / 艺人自营",
            "director_circles": "学院派 / 商业大片派 / 文艺独立派",
            "capital_factions": "传统影视资本 / 互联网资本 / 海外资本",
            "star_groups": "前辈+晚辈派系 / 同期出道CP绑定",
            "narrative_uses": "主角换公司=换派系=面临原派系打压",
            "activation_keywords": ["娱乐圈派系", "经纪公司", "圈内人", "幕后资本"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("娱乐圈生态分析")],
        tags=["娱乐圈", "派系", "经纪", "资本"],
    ),
    MaterialEntry(
        dimension="factions", genre="心理惊悚",
        slug="psy-fac-cult-circles",
        name="邪教/秘密团体",
        narrative_summary="心理惊悚中的核心势力：表面合法的宗教/治愈/灵性团体，"
                          "实质用心理操控控制成员。从外部看是组织，从内部看是一种社会疾病。",
        content_json={
            "structural_layers": "魅力教主 / 内圈忠诚追随者 / 外围被操控的普通成员",
            "control_mechanisms": "信息隔离 / 群体压力 / 经济依赖 / 性/家庭控制",
            "real_world_analogs": ["人民圣殿教", "奥姆真理教", "现代各种心理操控团体"],
            "narrative_function": "主角调查/逃离/对抗 = 揭示心理操控的运作方式",
            "exit_difficulty": "成员往往不认为自己被控制，外人救援极困难",
            "activation_keywords": ["邪教", "精神控制", "秘密团体", "心理操纵"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[wiki("邪教", "社会学"), wiki("BITE模型", "心理控制"), llm_note("心理惊悚势力设计")],
        tags=["心理惊悚", "邪教", "操纵", "势力"],
    ),
    MaterialEntry(
        dimension="factions", genre="种田",
        slug="farm-fac-village-clans",
        name="乡村宗族网络",
        narrative_summary="种田/乡村背景下的宗族派系：以血缘/地理/经济实力为基础形成的隐性派系。"
                          "新人进入乡村或本地人崛起，都要在这个网络中找到自己的位置或挑战既有结构。",
        content_json={
            "clan_axes": "血缘宗族 / 地理乡里 / 行业利益 / 几代积累的人情债",
            "leadership": "宗族族长 / 本地能人 / 老一辈见证者",
            "subtle_rules": "婚丧嫁娶的人情往来 / 土地纠纷的私下解决",
            "outsider_dilemma": "新人受欢迎程度取决于其与现有派系的关系",
            "activation_keywords": ["乡村宗族", "村里关系", "本地派", "人情网"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[wiki("中国宗族", ""), llm_note("乡村社会派系")],
        tags=["种田", "乡村", "宗族", "派系"],
    ),
    MaterialEntry(
        dimension="factions", genre="校园",
        slug="campus-fac-cliques",
        name="校园小圈子生态",
        narrative_summary="校园里的隐性派系结构：学霸圈/社团核心层/校霸团/边缘人——"
                          "每个圈子有自己的语言、规则、地位标志，主角的成长往往伴随圈子归属的转变。",
        content_json={
            "circle_types": ["学霸学神圈", "社团/学生会核心", "校园风云人物圈", "兴趣小众圈", "边缘个体"],
            "entry_thresholds": "成绩 / 社交能力 / 家庭背景 / 颜值",
            "intra_school_politics": "圈际联盟 / 圈际敌对 / 个体跨圈穿梭的特殊地位",
            "graduation_aftermath": "圈子在毕业后大多解散，但塑造主角已成事实",
            "activation_keywords": ["校园圈子", "小团体", "学霸圈", "校园派系"],
        },
        source_type="llm_synth", confidence=0.71,
        source_citations=[llm_note("校园社交派系分析")],
        tags=["校园", "圈子", "社交", "派系"],
    ),
    MaterialEntry(
        dimension="factions", genre="灵异",
        slug="liyi-fac-mystic-orders",
        name="灵异/玄门体系",
        narrative_summary="灵异世界的核心势力：道门/茅山派/天师府等正派；佛门系；密宗系；"
                          "民间术士；以及不入流的私门各类。每派擅长领域不同（驱邪/超度/通灵/医病）。",
        content_json={
            "orthodox_traditions": "道教（茅山/龙虎山/全真）/ 佛教（密宗/禅宗驱魔）",
            "folk_traditions": "民间神汉/巫师/萨满/地方术士",
            "specialization": {
                "茅山": "符箓驱邪",
                "龙虎山": "正一道法事",
                "天师府": "全面玄学",
                "民间": "本土化结合",
            },
            "internal_politics": "正派对邪派的剿杀 / 派系间的传承之争",
            "activation_keywords": ["茅山派", "玄门", "天师府", "灵异门派"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("茅山道术", ""), wiki("天师道", ""), llm_note("灵异门派设计")],
        tags=["灵异", "玄门", "门派", "宗教"],
    ),
    MaterialEntry(
        dimension="factions", genre="重生",
        slug="rebirth-fac-pre-known-actors",
        name="重生者已知势力图",
        narrative_summary="重生类作品的特殊设定：所有势力对主角都不再陌生——"
                          "他知道每个组织未来的兴衰、关键人物的变节、决定命运的事件。这种『先知』优势如何使用是核心张力。",
        content_json={
            "known_factions_advantage": "知道哪个公司将上市/哪个家族将垮台/哪个领导将贪腐",
            "manipulation_options": "提前布局获利 / 阻止某事件发生 / 改变某个人的轨迹",
            "ethical_dilemma": "是否阻止灾难（蝴蝶效应）/ 是否利用先知优势赚钱",
            "tension_source": "重生者的知识也可能被利用——其他重生者/系统/势力盯上他",
            "activation_keywords": ["先知优势", "蝴蝶效应", "重生预知", "改变命运"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("重生势力图设计")],
        tags=["重生", "先知", "势力", "预判"],
    ),
    MaterialEntry(
        dimension="factions", genre=None,
        slug="gen-fac-three-body-balance",
        name="三足鼎立势力结构",
        narrative_summary="叙事中最稳定的势力格局：三方力量相互制衡，没有任何一方能独大。"
                          "这种结构能让主角在三方间穿梭，制造复杂的政治博弈，比二元对立更耐看。",
        content_json={
            "structure_logic": "A vs B 时 C 是变量；B vs C 时 A 是变量；任意两方联合可压制第三方",
            "real_examples": ["三国（魏蜀吴）", "冷战（美苏中）", "西方制衡（英法德）"],
            "narrative_advantages": "提供丰富的联盟切换 / 任何一方崩塌都改变全局 / 主角可作为中间力量",
            "writing_principles": "三方都有自己的合理性 / 没有纯粹的善恶",
            "fall_modes": "三方失衡 → 二元对立 → 重新洗牌",
            "activation_keywords": ["三足鼎立", "三国", "势力平衡", "联盟博弈"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("三国", "中国历史"), llm_note("通用势力结构")],
        tags=["通用", "势力", "三足", "结构"],
    ),
    MaterialEntry(
        dimension="factions", genre=None,
        slug="gen-fac-secret-society",
        name="秘密会社模型",
        narrative_summary="跨越多个题材的隐藏组织：影响力遍布社会各层但不为人知，"
                          "成员通过暗号/标记/仪式互相识别。常见于历史/玄幻/都市/赛博朋克等题材。",
        content_json={
            "structural_archetype": "金字塔结构 / 细胞结构（互不知晓）/ 网络结构",
            "membership_mechanics": "继承 / 邀请 / 任务考验 / 血誓",
            "real_world_analogs": ["共济会", "三K党", "黑手党", "白莲教"],
            "narrative_uses": "主角发现/加入/对抗 = 探索社会的隐藏维度",
            "tension_source": "组织目的的善恶模糊 / 成员可能是身边任何人",
            "activation_keywords": ["秘密结社", "暗中势力", "隐藏组织", "影子势力"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[wiki("共济会", ""), wiki("秘密结社", "社会学"), llm_note("秘密会社叙事")],
        tags=["通用", "秘密结社", "组织", "暗中势力"],
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
