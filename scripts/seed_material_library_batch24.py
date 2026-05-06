"""
Batch 24: Psychology deep dive — mental disorders / cognitive biases /
motivation theories / personality models / trauma. Activates psychological
vocabulary for character interiority, conflict, and behavioral realism.
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
    # 抑郁症
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-psy-depression",
        name="抑郁症（重度抑郁障碍）",
        narrative_summary="抑郁症是最常见的精神障碍。核心症状：持续低落 / 兴趣丧失 / 自我贬低 / 自杀念头。"
                          "DSM-5 标准：9 项症状中至少 5 项持续 ≥ 2 周。"
                          "治疗：SSRI 药物 + CBT 认知行为疗法 + ECT（电休克）。"
                          "适用都市 / 校园 / 文艺 / 灾难幸存者题材。",
        content_json={
            "core_symptoms": "情绪低落 / 快感缺失 / 食欲改变 / 失眠或嗜睡 / 精神运动迟滞 / 疲劳 / 无价值感 / 注意力下降 / 自杀意念",
            "diagnostic_criteria": "DSM-5: 9 项中 ≥ 5 项 / 持续 ≥ 2 周 / 显著功能损害 / 排除药物及其他疾病",
            "subtypes": "重度抑郁障碍 MDD / 持续性抑郁障碍 PDD / 季节性 SAD / 产后抑郁 PPD / 双相抑郁",
            "neurobiology": "5-HT 5-羟色胺低 / NE 去甲肾上腺素失调 / 海马萎缩 / HPA 轴过度激活 / 炎症因子升高",
            "treatments": "SSRI（氟西汀 / 舍曲林）/ SNRI（文拉法辛）/ CBT 认知行为 / IPT 人际治疗 / ECT 电休克 / TMS 经颅磁刺激 / 氯胺酮快速抗抑郁",
            "narrative_use": "都市青年 / 校园霸凌后果 / 文艺写实 / 战争 PTSD 共病 / 产后家庭剧",
            "activation_keywords": ["抑郁症", "MDD", "SSRI", "快感缺失", "自杀意念", "海马萎缩", "5-HT", "CBT"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[wiki("抑郁症", ""), llm_note("精神病学 DSM-5")],
        tags=["心理学", "精神障碍", "通用"],
    ),
    # 焦虑谱系
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-psy-anxiety-spectrum",
        name="焦虑谱系障碍",
        narrative_summary="焦虑谱系：广泛性焦虑 GAD / 惊恐障碍 / 社交焦虑 SAD / 特定恐惧症 / OCD 强迫症 / PTSD 创伤后应激。"
                          "共同机制：杏仁核过度激活 + 前额叶抑制不足。"
                          "适用各类高压职场 / 战争 / 灾难 / 校园题材。",
        content_json={
            "subtypes": "广泛性焦虑 GAD（持续担忧）/ 惊恐障碍（突发恐慌发作）/ 社交焦虑 SAD（社交场合恐惧）/ 特定恐惧症（蛇 / 高空 / 密闭）/ OCD 强迫症（侵入性想法 + 仪式行为）/ PTSD 创伤后应激",
            "panic_attack": "心悸 / 出汗 / 颤抖 / 胸闷 / 现实感丧失 / 濒死感 / 持续 10-30 分钟达峰",
            "ptsd_clusters": "侵入性闪回 / 回避线索 / 负性情绪认知 / 高警觉（易惊跳 / 失眠）",
            "ocd_logic": "强迫思维 → 焦虑爆涨 → 仪式动作（洗手 / 检查 / 计数）→ 暂时缓解 → 强化循环",
            "neurobiology": "杏仁核过度激活 / vmPFC 腹内侧前额叶抑制不足 / GABA γ-氨基丁酸不足 / 蓝斑核 NE 高",
            "treatments": "SSRI / SNRI / 苯二氮卓（短期）/ CBT 暴露疗法 / EMDR 眼动脱敏（PTSD 一线）/ 内观冥想",
            "narrative_use": "战争退伍兵 PTSD / 校园霸凌社交焦虑 / 创业老板 GAD / 警察反复检查 OCD",
            "activation_keywords": ["焦虑", "PTSD", "OCD", "惊恐发作", "杏仁核", "EMDR", "暴露疗法", "侵入性闪回"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("焦虑症", ""), llm_note("焦虑障碍谱系")],
        tags=["心理学", "精神障碍", "通用"],
    ),
    # 双相障碍
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-psy-bipolar",
        name="双相情感障碍",
        narrative_summary="双相 = 抑郁 + 躁狂的极端摆动。I 型有完整躁狂；II 型只有轻躁狂 + 抑郁。"
                          "躁狂期：精力爆发 / 不睡 / 夸大妄想 / 冲动消费 / 性欲亢进。"
                          "高发于艺术家 / 创业者 / 极端表现型人物。",
        content_json={
            "two_types": "I 型（≥ 1 次完整躁狂发作 + 抑郁）/ II 型（轻躁狂 + 抑郁，无完整躁狂）/ 环性心境障碍（轻症慢性）",
            "manic_symptoms": "精力旺盛连续不睡 / 言语急促 / 思维奔逸 / 夸大妄想 / 冲动消费 / 性欲亢进 / 易激惹 / 判断力丧失",
            "depressive_episode": "和重度抑郁相同症状（混合发作时同时存在）",
            "famous_creators": "凡高 / 海明威 / 弗吉尼娅·伍尔夫 / 罗宾威廉姆斯 / 凯丽费雪",
            "neurobiology": "DA 多巴胺通路失调 / 锂可调节 GSK-3 / 昼夜节律紊乱 / 线粒体功能障碍",
            "treatments": "锂盐（一线情绪稳定剂）/ 丙戊酸钠 / 喹硫平 / 拉莫三嗪（抗抑郁相）/ 心理治疗",
            "narrative_use": "艺术家狂热创作期 + 谷底 / 创业老板暴富暴亏 / 角色情绪剧烈波动塑造",
            "activation_keywords": ["双相", "躁狂", "锂盐", "夸大妄想", "思维奔逸", "凡高", "情绪稳定剂"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("双相情感障碍", ""), llm_note("双相障碍")],
        tags=["心理学", "精神障碍", "通用"],
    ),
    # 精神分裂症
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-psy-schizophrenia",
        name="精神分裂症",
        narrative_summary="精神分裂症 = 现实感丧失。阳性症状：幻觉 / 妄想 / 思维紊乱。阴性症状：情感淡漠 / 意志缺乏 / 社交退缩。"
                          "终身患病率约 1%。年轻发病（15-30）。"
                          "适用悬疑 / 心理惊悚 / 反派塑造（《美丽心灵》《黑天鹅》）。",
        content_json={
            "positive_symptoms": "幻听（最常见，命令性 / 评论性）/ 视幻觉 / 被害妄想 / 关系妄想 / 被控制妄想 / 思维插入 / 言语混乱",
            "negative_symptoms": "情感平淡 / 意志缺乏 / 言语贫乏 / 社交退缩 / 快感缺失",
            "cognitive_symptoms": "工作记忆下降 / 注意力损害 / 执行功能差",
            "neurobiology": "DA 多巴胺假说（中脑边缘过亢 → 阳性 / 中脑皮质不足 → 阴性）/ 谷氨酸 NMDA 假说 / 灰质萎缩",
            "subtypes_dsm4": "偏执型（妄想为主）/ 紧张型 / 紊乱型 / 残留型 / 未分化型（DSM-5 已废除分型）",
            "treatments": "第一代抗精神病药（氯丙嗪 / 氟哌啶醇 → D2 拮抗）/ 第二代（利培酮 / 奥氮平 / 喹硫平 → 5HT2A + D2）/ 氯氮平（难治性）/ 心理社会康复",
            "famous_cases": "约翰纳什《美丽心灵》/《黑天鹅》/ 《梦之安魂曲》",
            "narrative_use": "悬疑（不可靠叙事者）/ 心理惊悚 / 反派塑造 / 数学家或天才人物",
            "activation_keywords": ["精分", "幻听", "被害妄想", "多巴胺", "氯氮平", "美丽心灵", "纳什"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("精神分裂症", ""), llm_note("精分病学")],
        tags=["心理学", "精神障碍", "通用"],
    ),
    # 人格障碍
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-psy-personality-disorders",
        name="人格障碍十型（DSM-5）",
        narrative_summary="人格障碍三大群组：A 古怪型（偏执 / 分裂样 / 分裂型）/ B 戏剧型（反社会 / 边缘 / 表演 / 自恋）/ C 焦虑型（回避 / 依赖 / 强迫）。"
                          "B 群组最具戏剧性，是反派和复杂主角宝库。",
        content_json={
            "cluster_a_odd": "偏执型 PPD（不信任）/ 分裂样 SPD（孤僻冷漠）/ 分裂型 STPD（怪异思维迷信）",
            "cluster_b_dramatic": "反社会 ASPD（无悔意 / 利用他人）/ 边缘 BPD（情绪不稳 / 抛弃恐惧 / 自伤）/ 表演 HPD（戏剧化求关注）/ 自恋 NPD（夸大 + 缺乏共情）",
            "cluster_c_anxious": "回避 AVPD（社交退缩怕拒绝）/ 依赖 DPD（过度依附）/ 强迫 OCPD（完美主义僵化，不同于 OCD）",
            "borderline_features": "情绪 24 小时大幅波动 / 强烈空虚感 / 理想化 + 贬低交替 / 自伤切割 / 滥交滥药 / 接近精神病性短暂发作",
            "narcissistic_features": "夸大自我重要性 / 幻想无限成功 / 需特殊待遇 / 利用他人 / 嫉妒 + 被嫉妒幻想 / 缺乏共情",
            "famous_examples": "BPD: Glenn Close《致命诱惑》/ NPD: Trump 公认 / ASPD: 汉尼拔莱克特 / OCPD: 监工型上司",
            "treatments": "BPD: DBT 辩证行为疗法 / NPD: 难治 / ASPD: 难治 / 整体药物只对症",
            "narrative_use": "B 群组造反派和复杂主角金矿 / 都市恋爱（NPD 渣男 / BPD 不稳定女友）/ 心理悬疑",
            "activation_keywords": ["人格障碍", "BPD", "NPD", "ASPD", "边缘", "自恋", "反社会", "汉尼拔", "DBT"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("人格障碍", ""), llm_note("DSM-5 人格障碍")],
        tags=["心理学", "人格", "通用"],
    ),
    # 认知偏差大全
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-psy-cognitive-biases",
        name="认知偏差大全（决策心理学）",
        narrative_summary="人类决策有数百种系统性偏差。Kahneman 双系统：System 1（直觉快速）/ System 2（理性慢速）。"
                          "偏差是叙事冲突源泉：角色因偏差犯错 / 反派利用偏差操纵。",
        content_json={
            "core_biases": "确认偏差（只看支持证据）/ 锚定效应（首数字定调）/ 可得性启发（最近想到的就是真的）/ 沉没成本谬误（已投入不舍弃）/ 损失厌恶（亏 1 块痛是赚 1 块爽 2 倍）",
            "social_biases": "邓宁 - 克鲁格效应（无能者高估自己）/ 基本归因错误（怪人不怪环境）/ 群体迷思 / 责任分散（围观不救）/ 从众效应",
            "memory_biases": "玫瑰色回忆 / 错误记忆植入（Loftus）/ 后见之明（早就知道）/ 闪光灯记忆（重大事件超清楚）",
            "decision_biases": "现状偏好 / 选择悖论（选项太多反而不选）/ 框架效应（同一信息正负向描述结果不同）/ 互惠原则（收礼必还）",
            "kahneman_two_systems": "System 1: 自动、快速、直觉、情绪化 / System 2: 受控、缓慢、逻辑、费力 / 大多决策由 System 1 主导",
            "narrative_use": "侦探推理（破除偏差）/ 商战（操纵对手）/ 反派操控群体 / 主角自我反思认错",
            "activation_keywords": ["认知偏差", "确认偏差", "锚定", "Kahneman", "System 1", "System 2", "邓宁克鲁格", "损失厌恶"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("认知偏差", ""), llm_note("Kahneman《思考快与慢》")],
        tags=["心理学", "认知", "通用"],
    ),
    # 动机理论
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-psy-motivation-theories",
        name="动机理论体系",
        narrative_summary="为什么角色做这件事？动机理论给出答案。"
                          "Maslow 五级 / Deci-Ryan 自决论 / McClelland 三大需求 / Adler 社会兴趣 / Frankl 意义寻求。"
                          "强动机 = 强人物。",
        content_json={
            "maslow_hierarchy": "生理 → 安全 → 归属与爱 → 尊重 → 自我实现 / 后增加：认知需求 / 审美需求 / 超越需求",
            "self_determination": "Deci-Ryan: 三大基本心理需求 = 自主感 + 胜任感 + 关系感 / 内在动机 vs 外在动机 / 内化连续体",
            "mcclelland_three": "成就需要 nAch（追求卓越）/ 权力需要 nPow（影响他人）/ 亲和需要 nAff（被接纳）/ 三种比例造就不同领导风格",
            "adler_individual_psy": "追求优越（克服自卑）/ 社会兴趣（贡献感）/ 生活风格 / 出生顺序影响",
            "frankl_meaning": "维克多·弗兰克尔《活出意义来》/ 三种意义来源：创造性（工作）/ 体验性（爱与美）/ 态度性（面对苦难的姿态）",
            "self_efficacy_bandura": "我相信我能做到 → 行动 → 成就 → 强化自信 / 反向：习得性无助 Seligman 三狗实验",
            "narrative_use": "角色动机分层（Maslow）/ 反派动机不全是钱（权力 / 自卑代偿）/ 主角觉醒（找到意义）/ 配角差异化",
            "activation_keywords": ["Maslow", "自我实现", "自主感", "Frankl", "意义", "习得性无助", "内在动机", "Adler"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("动机理论", ""), llm_note("动机心理学")],
        tags=["心理学", "动机", "通用"],
    ),
    # 大五人格 + MBTI
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-psy-big-five-mbti",
        name="人格模型：大五 / MBTI / Enneagram",
        narrative_summary="角色性格的科学坐标。大五（OCEAN）= 学术金标 / MBTI 16 型 = 大众流行 / Enneagram 9 型 = 创伤 + 成长。"
                          "用其中一套就能让角色稳定立得住。",
        content_json={
            "big_five_ocean": "Openness 开放性（艺术好奇）/ Conscientiousness 尽责（自律守时）/ Extraversion 外向 / Agreeableness 宜人（合作）/ Neuroticism 神经质（情绪不稳）/ 每维度高低 = 32 组合",
            "mbti_16": "E/I 内外向 + S/N 感觉直觉 + T/F 思考情感 + J/P 判断知觉 / 16 型：INTJ 战略家 / INTP 思想家 / ENTJ 指挥官 / ENTP 辩手 / INFJ 提倡者 / ESFP 表演者 / ISTJ 物流师 等",
            "enneagram_9": "1 完美主义者 / 2 助人者 / 3 成就者 / 4 个人主义者 / 5 思考者 / 6 忠诚者 / 7 多面手 / 8 挑战者 / 9 调停者 / 每型都有童年创伤根源 + 健康 / 不健康两极",
            "dark_triad_addon": "马基雅维利主义 / 自恋 / 反社会 / 加 Sadism 虐待狂 = Dark Tetrad / 反派常配置",
            "design_use": "1) 主角先定 OCEAN 5 个分数 / 2) 关键决策由其低分维度引发崩溃 / 3) 配角和主角形成 MBTI 互补 / 4) 反派加 Dark Tetrad",
            "activation_keywords": ["大五", "OCEAN", "MBTI", "INTJ", "Enneagram", "Dark Triad", "九型", "宜人性"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("大五人格特质", ""), llm_note("人格量化")],
        tags=["心理学", "人格", "通用"],
    ),
    # 创伤心理学
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-psy-trauma-cptsd",
        name="创伤心理学（C-PTSD / 解离）",
        narrative_summary="单次创伤 PTSD（车祸 / 战场 / 强暴）vs 复杂创伤 C-PTSD（长期家暴 / 童年虐待）。"
                          "解离 = 心理逃生：人格分裂 DID / 现实感丧失 / 身份遗忘。"
                          "适用悬疑 / 文艺 / 受虐主角 / 反派背景。",
        content_json={
            "ptsd_vs_cptsd": "PTSD: 单次重大创伤 + 4 大症状群 / C-PTSD: 长期反复创伤 + 情感调节困难 + 自我认知扭曲 + 关系障碍 + 解离",
            "dissociation_spectrum": "正常走神 → 现实感丧失 / 人格解体 → 解离性遗忘 → 解离性身份障碍 DID（多重人格）",
            "did_origins": "几乎全部源于童年（5 岁前）严重创伤 + 高解离倾向天赋 / 创造 alter 人格隔离痛苦",
            "trauma_responses_4f": "Fight 战斗 / Flight 逃跑 / Freeze 僵直 / Fawn 讨好（C-PTSD 特有）",
            "treatments": "EMDR 眼动脱敏（一线）/ TF-CBT 创伤聚焦认知 / Somatic Experiencing 躯体经验 / 内在家庭系统 IFS / 药物辅助",
            "famous_works": "《24 个比利》/《房思琪的初恋乐园》/《被讨厌的勇气》/《身体从未忘记》范德考克",
            "narrative_use": "悬疑（DID 凶手 = 自己）/ 文艺（创伤恢复弧）/ 反派背景（童年虐待）/ 主角觉醒疗愈线",
            "activation_keywords": ["创伤", "PTSD", "C-PTSD", "解离", "DID", "EMDR", "比利", "范德考克", "讨好型"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("创伤后压力症", ""), llm_note("创伤心理学")],
        tags=["心理学", "创伤", "通用"],
    ),
    # 依恋理论深入
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-psy-attachment-deep",
        name="依恋理论深入（Bowlby / Ainsworth / 成人依恋）",
        narrative_summary="Bowlby 提出依恋本能 / Ainsworth 陌生情境实验定义婴儿四型 / Hazan-Shaver 移植到成人恋爱。"
                          "依恋型决定恋爱模式 + 创伤反应。",
        content_json={
            "infant_four_types": "安全型（B 型 ~60%）/ 焦虑矛盾型（C 型 ~10-15%）/ 回避型（A 型 ~25%）/ 混乱型（D 型 ~5-10%，与创伤相关）",
            "adult_four_styles": "安全型（自尊 + 信任他人）/ 痴迷型（焦虑追逐 + 低自尊）/ 疏离型（高自尊 + 不依赖他人）/ 恐惧型（既要又怕，混乱）",
            "core_dimensions": "焦虑维度（怕被抛弃）+ 回避维度（怕亲密）/ 两维度组合出四型",
            "common_couples": "焦虑 + 回避 = 追逃循环（最常见痛苦组合）/ 安全 + 任何 = 治愈型 / 双回避 = 表面平静实则疏远",
            "transgenerational": "父母依恋型 70% 概率传给子女 / 不安全 → 不安全",
            "narrative_use": "言情设计（追逃 / 救赎）/ 角色亲密关系模板 / 角色和父母关系倒推 / 治愈系作品",
            "activation_keywords": ["依恋", "Bowlby", "焦虑型", "回避型", "追逃", "陌生情境", "成人依恋", "混乱型"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("依恋理论", ""), llm_note("成人依恋")],
        tags=["心理学", "依恋", "通用"],
    ),
    # 群体心理学
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-psy-group-dynamics",
        name="群体心理学（庞勒 / Asch / Milgram / Zimbardo）",
        narrative_summary="个体进入群体会发生质变。Le Bon《乌合之众》/ Asch 从众实验 / Milgram 服从权威实验 / Zimbardo 斯坦福监狱实验。"
                          "用于写群众场面 / 邪教 / 战争暴行 / 网暴。",
        content_json={
            "le_bon_crowd": "群体智商低于个体 / 易受暗示 / 情感放大 / 责任分散 / 失去自我 / 道德倒退",
            "asch_conformity": "7-9 人房间问长度 / 1 真受试 + 假大众 / 真受试 75% 至少一次跟错 / 群体压力强大",
            "milgram_obedience": "学习实验 / 65% 普通人在白大褂指令下电击到致死电压 / 服从权威而泯灭良知 / 解释纳粹普通士兵",
            "zimbardo_prison": "斯坦福地下室模拟监狱 / 大学生角色扮演 / 6 天提前终止 / 角色化导致虐待行为爆发 / 系统因素 > 个性",
            "groupthink_janis": "高凝聚高压力小群体 → 错误决策 / 8 大症状：无懈可击错觉 / 道德合理化 / 刻板敌人 / 自我审查 / 异议者被压 / 八字错觉一致 等",
            "deindividuation": "去个性化 = 匿名 + 群体 + 唤醒 → 失控 / 解释面具党 / 网暴 / 暴乱",
            "narrative_use": "群众场面 / 邪教覆灭 / 战争暴行 / 网络暴力 / 历史革命",
            "activation_keywords": ["群体心理", "乌合之众", "Asch", "Milgram", "Zimbardo", "斯坦福监狱", "群体迷思", "去个性化"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("社会心理学", ""), llm_note("群体心理实验")],
        tags=["心理学", "群体", "通用"],
    ),
    # 积极心理学
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-psy-positive-psychology",
        name="积极心理学（Seligman / Csikszentmihalyi）",
        narrative_summary="不只研究病理，也研究幸福。Seligman PERMA 模型 / Csikszentmihalyi 心流 / Fredrickson 积极情绪扩展。"
                          "用于励志成长 / 治愈 / 商战逆袭 / 体育竞技。",
        content_json={
            "perma_model": "Positive Emotion 积极情绪 / Engagement 投入（心流）/ Relationships 关系 / Meaning 意义 / Achievement 成就 / 幸福五要素",
            "flow_state": "Csikszentmihalyi 心流 / 条件：明确目标 + 即时反馈 + 挑战 ≈ 技能 / 体验：时间感丧失 + 自我消失 + 内在愉悦 / 出现于运动 / 创作 / 编程 / 音乐演奏 / 攀岩",
            "character_strengths": "VIA 24 项美德分类 / 6 大美德：智慧 / 勇气 / 仁爱 / 正义 / 节制 / 卓越 / 找到自己 5 大签名优势",
            "broaden_build": "Fredrickson 积极情绪扩展构建理论 / 积极情绪 → 思维扩展 → 资源构建 / 反向螺旋上升",
            "learned_optimism": "Seligman 习得性乐观 / 乐观者解释风格：负面归因外部 / 暂时 / 局部 / 悲观者反之 / 可以训练",
            "narrative_use": "竞技体育（心流时刻）/ 创业逆袭（PERMA 重建）/ 治愈系（关系修复）/ 主角解释风格转变（悲转乐）",
            "activation_keywords": ["积极心理", "PERMA", "心流", "Csikszentmihalyi", "Seligman", "习得性乐观", "VIA", "签名优势"],
        },
        source_type="llm_synth", confidence=0.90,
        source_citations=[wiki("积极心理学", ""), llm_note("Seligman PERMA")],
        tags=["心理学", "积极心理", "通用"],
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
