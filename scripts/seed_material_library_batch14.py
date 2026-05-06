"""
Batch 14: Universal real_world_references — specialized domain knowledge for
LLM activation across medical/legal/business/financial/sports/military scenes.

These are genre=None (universal) entries that activate the LLM's latent
domain knowledge whenever a scene crosses into specialized territory.
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
    # 医学/医疗
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-medical-emergency-room",
        name="急诊室运作机制",
        narrative_summary="急诊医学的核心是分诊（Triage）+ ABCDE 评估法 + 黄金一小时。"
                          "分诊护士按红/黄/绿/蓝四级决定优先级，主治医生用气道-呼吸-循环-意识-暴露 5 步评估。"
                          "适用于车祸/枪伤/突发疾病等紧急医疗场景。",
        content_json={
            "triage_levels": "红（立即）/黄（10 分钟内）/绿（可等待）/蓝（轻症）",
            "ABCDE_protocol": "Airway 气道→Breathing 呼吸→Circulation 循环→Disability 意识→Exposure 暴露检查",
            "golden_hour": "外伤后 60 分钟内是抢救黄金期，此后存活率断崖下降",
            "common_drugs": "肾上腺素（心搏骤停）/吗啡（剧痛）/纳洛酮（毒品过量）/胰岛素（糖尿病）",
            "specialized_terms": "GCS 评分（昏迷指数）/CPR（心肺复苏）/插管/除颤",
            "narrative_use": "都市医学小说/重生医生流/末日医疗资源稀缺/犯罪小说创伤场景",
            "activation_keywords": ["急诊室", "分诊", "黄金一小时", "ABCDE", "心肺复苏", "插管", "GCS"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[wiki("急诊医学", ""), wiki("黄金一小时", ""), llm_note("急诊运作通识")],
        tags=["医学", "急诊", "通用"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-medical-tcm-diagnosis",
        name="中医诊疗体系",
        narrative_summary="中医以阴阳/五行/经络为理论基础，望闻问切四诊合参，辨证论治用方剂。"
                          "重生穿越/古代背景/民国背景/玄幻医修题材常引用。",
        content_json={
            "diagnostic_methods": "望（看气色舌苔）/闻（听声闻气味）/问（症状起病）/切（脉象）",
            "core_theory": "阴阳平衡 / 五行（金木水火土）相生相克 / 经络十二正经 + 任督二脉",
            "common_pulse_types": "浮（表证）/沉（里证）/迟（寒）/数（热）/滑（湿/孕）/涩（血淤）",
            "famous_classics": "《黄帝内经》《伤寒论》《本草纲目》《神农本草经》",
            "common_syndromes": "气虚 / 血瘀 / 阴虚火旺 / 肝郁脾虚 / 痰湿",
            "narrative_use": "中医世家穿越 / 系统流神医 / 玄幻医修 / 历史宫廷御医",
            "activation_keywords": ["望闻问切", "脉象", "经络", "阴阳", "五行", "气血", "辨证"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("中医", ""), wiki("四诊", ""), llm_note("中医诊疗体系")],
        tags=["中医", "医学", "传统文化"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-medical-surgery-protocol",
        name="外科手术流程",
        narrative_summary="外科手术从术前评估→无菌准备→麻醉→切开→止血→操作→缝合→术后监护，"
                          "每一步都有严格 SOP。适用于医疗剧、灾难救援、战争小说。",
        content_json={
            "stages": "术前讨论 → 麻醉（全麻/局麻/腰麻）→ 消毒铺巾 → 切开 → 解剖暴露 → 操作 → 止血 → 缝合 → 包扎",
            "team_roles": "主刀 / 一助 / 二助 / 麻醉师 / 器械护士 / 巡回护士",
            "instruments": "手术刀 / 止血钳 / 镊子 / 持针器 / 拉钩 / 吸引器 / 电刀",
            "complications": "大出血 / 麻醉意外 / 感染 / 缝合开裂 / 神经损伤",
            "ethics": "知情同意书 / 多学科会诊 / 风险告知",
            "narrative_use": "医学小说手术救命 / 战地军医 / 末日临时手术",
            "activation_keywords": ["主刀", "麻醉", "止血", "缝合", "无菌", "手术台", "术中"],
        },
        source_type="llm_synth", confidence=0.81,
        source_citations=[wiki("外科手术", ""), llm_note("手术流程通识")],
        tags=["医学", "外科", "通用"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 法律
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-legal-civil-trial",
        name="民事诉讼流程",
        narrative_summary="民事案件流程：起诉立案 → 答辩 → 证据交换 → 庭审 → 辩论 → 判决 → 上诉/执行。"
                          "中国大陆采两审终审制；普通法系（英美）有陪审团。",
        content_json={
            "stages_civil": "起诉状 → 立案审查 → 送达答辩 → 举证质证 → 开庭审理 → 法庭调查 → 法庭辩论 → 判决",
            "evidence_types": "书证 / 物证 / 视听资料 / 证人证言 / 当事人陈述 / 鉴定意见 / 勘验笔录",
            "burden_of_proof": "民事 = 优势证据；刑事 = 排除合理怀疑",
            "common_terms": "原告 / 被告 / 第三人 / 代理人 / 抗辩 / 反诉 / 撤诉",
            "narrative_use": "都市法律剧 / 重生律师流 / 商战诉讼 / 离婚财产纠纷",
            "activation_keywords": ["立案", "庭审", "举证", "辩论", "判决", "原告", "被告", "代理人"],
        },
        source_type="llm_synth", confidence=0.80,
        source_citations=[wiki("民事诉讼", ""), llm_note("民事诉讼流程")],
        tags=["法律", "诉讼", "通用"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-legal-criminal-investigation",
        name="刑事侦查流程",
        narrative_summary="刑案侦查从立案 → 现场勘查 → 取证 → 嫌疑人询问 → 起诉。"
                          "讯问 24 小时内必须告知律师权利；刑诉不得自证其罪。"
                          "悬疑/犯罪/警匪小说核心机制。",
        content_json={
            "stages_criminal": "报案 → 立案 → 现场勘查 → 调查取证 → 拘留 → 侦查终结 → 移送审查起诉 → 公诉",
            "evidence_chain": "现场保护 → 痕迹采集 → 物证封存 → 实验室鉴定 → 出庭作证（链条任何环节断裂则证据无效）",
            "interrogation_rules": "MIRANDA 警告（米兰达权利）/ 讯问录音录像 / 律师在场权",
            "common_techniques": "现场勘查 / 走访摸排 / 视频侦查 / DNA 比对 / 指纹比对 / 测谎（参考性）",
            "narrative_use": "悬疑推理 / 警匪片 / 法医秦明类 / 重生破案",
            "activation_keywords": ["立案", "侦查", "勘查", "讯问", "证据链", "DNA", "嫌疑人", "审讯"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[wiki("刑事侦查", ""), wiki("米兰达警告", ""), llm_note("刑侦通识")],
        tags=["法律", "刑侦", "悬疑"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 商业/金融
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-business-ma-deal",
        name="企业并购（M&A）流程",
        narrative_summary="并购交易：意向书 LOI → 尽职调查 DD → 谈判定价 → 签 SPA → 监管审批 → 交割。"
                          "投行/律所/会计师介入，敌意收购可能触发毒丸/白衣骑士反制。"
                          "商战重生 / 都市霸总 / 财阀小说核心。",
        content_json={
            "stages_ma": "战略目标 → 标的筛选 → LOI 意向书 → 尽调 DD → 估值 → SPA 股权购买协议 → 反垄断审批 → 交割 PMI",
            "valuation_methods": "DCF 现金流折现 / 可比公司倍数（PE/PB/EV/EBITDA）/ 可比交易",
            "deal_types": "现金收购 / 换股 / 杠杆收购 LBO / 反向收购",
            "hostile_takeover_defense": "毒丸 Poison Pill / 白衣骑士 White Knight / 焦土战术",
            "narrative_use": "商战小说 / 都市霸总 / 重生股市 / 反派资本运作",
            "activation_keywords": ["并购", "尽调", "DD", "估值", "DCF", "毒丸", "杠杆收购", "敌意收购"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[wiki("企业并购", ""), wiki("敌意收购", ""), llm_note("M&A 通识")],
        tags=["商业", "金融", "通用"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-business-stock-trading",
        name="股市交易机制",
        narrative_summary="股市运作：开盘集合竞价 → 连续竞价 → 收盘 → 财报披露周期 → 内幕交易禁区。"
                          "K 线/MACD/RSI 是技术分析三大件。"
                          "重生股神 / 都市金融 / 反派操盘小说核心。",
        content_json={
            "trading_hours_cn": "上海/深圳：9:30-11:30 + 13:00-15:00；T+1 制；涨跌停 ±10%",
            "k_line_basics": "阳线（红/绿）/ 阴线 / 长上影 / 长下影 / 十字星 / 十字星变盘信号",
            "indicators": "MACD（金叉死叉）/ RSI（超买超卖）/ KDJ / BOLL 布林带 / 量价背离",
            "common_terms": "牛市 / 熊市 / 多头 / 空头 / 解禁 / 限售 / 大宗交易 / 涨停板 / 庄家 / 散户",
            "illegal_acts": "内幕交易 / 操纵市场 / 老鼠仓 / 虚假信息披露 → SEC/证监会重罚 + 刑责",
            "narrative_use": "重生股神 / 操盘手 / 商战金融 / 反派庄家",
            "activation_keywords": ["K 线", "涨停", "MACD", "庄家", "内幕交易", "牛市", "解禁", "财报"],
        },
        source_type="llm_synth", confidence=0.80,
        source_citations=[wiki("证券交易", ""), wiki("技术分析", ""), llm_note("股市通识")],
        tags=["商业", "金融", "股市"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-business-startup-vc",
        name="创业与风险投资体系",
        narrative_summary="创业公司融资轮次：种子→天使→A→B→C→D→Pre-IPO→IPO。"
                          "VC 看赛道+团队+商业模式+护城河；估值靠 PE/PS/SaaS Multiple。"
                          "都市创业 / 商战 / 重生互联网小说必备。",
        content_json={
            "funding_rounds": "种子（idea 阶段）→ 天使（MVP）→ A（PMF）→ B（规模化）→ C（行业领先）→ Pre-IPO",
            "vc_terms": "TS 投资意向书 / SHA 股东协议 / Vesting 兑现 / Cliff 悬崖 / 反稀释 / 优先清算 / 对赌",
            "valuation_basis": "互联网早期烧 GMV / SaaS 看 ARR + LTV/CAC / 制造业看现金流",
            "famous_funds": "红杉 Sequoia / 高瓴 / IDG / 经纬 / 真格 / 软银愿景基金",
            "narrative_use": "都市创业重生 / 互联网小说 / 商战题材",
            "activation_keywords": ["天使轮", "A 轮", "估值", "VC", "投资意向书", "对赌", "PMF", "护城河"],
        },
        source_type="llm_synth", confidence=0.81,
        source_citations=[wiki("风险投资", ""), llm_note("创业融资通识")],
        tags=["商业", "创业", "投资"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 体育竞技
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-sports-football-tactics",
        name="足球战术体系",
        narrative_summary="足球阵型：4-4-2 经典 / 4-3-3 现代攻击 / 3-5-2 翼卫 / 4-2-3-1 主流。"
                          "战术风格：tiki-taka 传控 / Gegenpressing 高位压迫 / 反击 / 防守反击。"
                          "体育竞技流主角通常逆袭世界顶级联赛。",
        content_json={
            "formations": "4-4-2 / 4-3-3 / 3-5-2 / 4-2-3-1 / 5-3-2 / 4-1-4-1",
            "tactical_styles": "Tiki-taka 控球 / Gegenpressing 全场逼抢 / 防守反击 / Catenaccio 链式防守",
            "positions": "GK 门将 / CB 中卫 / FB 边卫 / DM 后腰 / CM 中场 / AM 前腰 / W 边锋 / ST 前锋",
            "key_skills": "盘带 / 长传 / 直塞 / 斜传 / 头球 / 任意球 / 点球 / 扑救",
            "leagues": "英超 / 西甲 / 意甲 / 德甲 / 法甲 / 中超 / 美职联",
            "narrative_use": "体育竞技逆袭 / 重生足球 / 系统流球员",
            "activation_keywords": ["4-3-3", "tiki-taka", "前锋", "中场", "传控", "盘带", "前腰"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("足球战术", ""), llm_note("足球通识")],
        tags=["体育", "足球", "竞技"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-sports-basketball-systems",
        name="篮球战术与体系",
        narrative_summary="篮球进攻体系：跑轰 / 普林斯顿 / 三角进攻 / 挡拆 / 区域联防破解。"
                          "防守体系：人盯人 / 区域联防 2-3 / 3-2 / Box-and-One。"
                          "NBA / CBA / 街球文化各有体系。重生篮球 / 系统流球员小说常用。",
        content_json={
            "offensive_systems": "三角进攻（Triangle）/ 普林斯顿 / 跑轰（Run-and-Gun）/ Pick-and-Roll 挡拆 / 七秒进攻",
            "defensive_systems": "人盯人 / 2-3 联防 / 3-2 联防 / Box-and-One / Full-Court Press 全场紧逼",
            "positions": "PG 控卫 / SG 得分后卫 / SF 小前锋 / PF 大前 / C 中锋 / Stretch-4 空间型大前",
            "key_metrics": "PER / TS% 真实命中率 / WS 胜利贡献值 / +/-",
            "narrative_use": "体育穿越 / 重生 NBA / 校园篮球 / 灌篮高手向",
            "activation_keywords": ["挡拆", "三角进攻", "联防", "控卫", "灌篮", "三分", "突破"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("篮球战术", ""), llm_note("篮球通识")],
        tags=["体育", "篮球", "竞技"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-sports-esports-moba",
        name="电竞 MOBA 体系",
        narrative_summary="MOBA 五位分工：上单 / 打野 / 中单 / ADC / 辅助。"
                          "运营节奏：对线期 → 中期游走 → 团战 → 推塔 → 大龙肉山 → Game Over。"
                          "电竞重生 / 主播流 / 系统流游戏小说核心机制。",
        content_json={
            "roles": "上单（Top）/ 打野（Jungle）/ 中单（Mid）/ ADC 下路 / 辅助（Support）",
            "macro_phases": "对线期（0-15 min）→ 中期游走团战 → 后期决战 → 推家",
            "key_objectives": "兵线 / 经济差 / 等级差 / 视野（眼位）/ 大小龙 / 推塔",
            "skill_categories": "AOE 范围 / 单体爆发 / 控制（眩晕/沉默/嘲讽）/ 位移 / 真伤",
            "famous_titles": "Dota / LoL / 王者荣耀 / 风暴英雄",
            "narrative_use": "电竞小说 / 全职高手向 / 主播流 / 重生世界冠军",
            "activation_keywords": ["上单", "打野", "ADC", "辅助", "团战", "推塔", "兵线", "视野"],
        },
        source_type="llm_synth", confidence=0.80,
        source_citations=[wiki("MOBA", ""), llm_note("电竞 MOBA 通识")],
        tags=["体育", "电竞", "MOBA"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 军事
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-military-modern-warfare",
        name="现代战争形态",
        narrative_summary="信息化战争核心是 C4ISR（指挥控制通信计算机情报监视侦察）。"
                          "三军协同：陆军装甲集群 / 空军制空 / 海军远海打击 + 特种作战。"
                          "军事重生 / 末日 / 历史穿越科技碾压小说必备。",
        content_json={
            "doctrine": "诸兵种合成 / 联合作战 / 信息化作战 / 网络战 / 电子战 / 不对称作战",
            "C4ISR": "Command 指挥 / Control 控制 / Communications 通信 / Computers 计算机 / Intelligence 情报 / Surveillance 监视 / Reconnaissance 侦察",
            "weapon_categories": "单兵（步枪/RPG）/ 装甲（主战坦克/IFV）/ 火炮（自行火炮/MLRS）/ 防空（短程/中程/远程）/ 海空（驱护舰/战机/航母）",
            "modern_features": "无人机蜂群 / 高超音速 / 隐身 / 网络攻击 / 太空战",
            "narrative_use": "现代军事 / 末日装甲 / 穿越带科技碾压古代 / 国战",
            "activation_keywords": ["合成旅", "信息化", "C4ISR", "无人机", "电子战", "特种作战", "联合作战"],
        },
        source_type="llm_synth", confidence=0.81,
        source_citations=[wiki("现代战争", ""), wiki("C4ISR", ""), llm_note("军事通识")],
        tags=["军事", "战争", "通用"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-military-special-forces",
        name="特种作战机制",
        narrative_summary="特种作战核心：小队（4-12 人）+ 高度训练 + 特种装备 + 情报支撑。"
                          "美军 SEAL/三角洲、英军 SAS、俄军阿尔法、中国特战队。"
                          "任务类型：直接行动 / 特殊侦察 / 反恐 / 人质营救 / 非常规战。",
        content_json={
            "famous_units": "美 SEAL Team 6 / 三角洲 / 英 SAS / 俄阿尔法 / 以色列 Sayeret Matkal / 中国雪豹",
            "team_composition": "队长 / 副手 / 通讯 / 医疗 / 爆破 / 狙击 / 武器（4-12 人 A-Team）",
            "mission_types": "DA 直接行动 / SR 特殊侦察 / CT 反恐 / FID 外训 / UW 非常规战",
            "core_skills": "近距离作战 CQB / 狙击 / 爆破 / 跳伞 / 潜水 / 山地 / 城市作战",
            "narrative_use": "现代军事 / 都市退役兵神 / 反恐惊悚 / 重生特种兵",
            "activation_keywords": ["特种部队", "SEAL", "CQB", "狙击", "侦察", "营救", "反恐"],
        },
        source_type="llm_synth", confidence=0.79,
        source_citations=[wiki("特种部队", ""), llm_note("特种作战通识")],
        tags=["军事", "特种作战", "现代"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 心理学
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-psychology-trauma-ptsd",
        name="创伤后应激障碍 PTSD",
        narrative_summary="PTSD 由极端创伤事件触发：闪回 / 噩梦 / 警觉过度 / 回避 / 情感麻木。"
                          "适用于战后归来、灾难幸存、童年虐待主角。心理深度向小说常用。",
        content_json={
            "core_symptoms": "侵入性回忆 / 闪回 / 噩梦 / 警觉过度 / 回避线索 / 情感麻木 / 内疚",
            "trigger_types": "战争 / 性侵 / 严重事故 / 自然灾害 / 童年虐待 / 暴力目击",
            "treatment": "暴露疗法 / EMDR 眼动脱敏 / 认知行为疗法 CBT / 药物（SSRI）",
            "narrative_use": "战后归来主角 / 末日幸存者 / 灵异创伤 / 心理悬疑",
            "activation_keywords": ["创伤", "闪回", "噩梦", "警觉", "PTSD", "回避", "麻木"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[wiki("创伤后应激障碍", ""), llm_note("PTSD 通识")],
        tags=["心理学", "创伤", "PTSD"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-psychology-attachment-styles",
        name="依恋类型理论",
        narrative_summary="Bowlby/Ainsworth 依恋理论：安全型 / 焦虑型 / 回避型 / 紊乱型。"
                          "成人依恋决定亲密关系模式：焦虑追逐 + 回避逃离构成『焦虑-回避陷阱』循环。"
                          "言情/虐恋/心理向小说人物建模强力工具。",
        content_json={
            "four_types": "安全型（信任表达）/ 焦虑型（需要确认追逐）/ 回避型（独立疏离）/ 紊乱型（爱恨交织）",
            "couple_dynamics": "焦虑×回避 = 拉扯地狱 / 回避×回避 = 冷战僵化 / 焦虑×焦虑 = 依赖共生 / 安全 × 任意 = 治愈",
            "origin": "童年与照顾者关系 + 关键创伤 / 成年后可重塑（依恋修复）",
            "narrative_use": "言情人物建模 / 双人关系动力学 / 校园初恋 / 婚后重塑",
            "activation_keywords": ["依恋", "安全型", "焦虑型", "回避型", "亲密关系", "拉扯", "Push-Pull"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("依恋理论", ""), llm_note("依恋类型分析")],
        tags=["心理学", "依恋", "言情"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-psychology-dark-triad",
        name="暗黑三角人格",
        narrative_summary="Dark Triad：自恋型（NPD）/ 马基雅维利主义（Mach）/ 反社会人格（ASPD）三种交叉特征。"
                          "心理悬疑/反派/犯罪小说塑造立体反派的核心理论。",
        content_json={
            "narcissism": "夸大自我 / 需要崇拜 / 缺乏共情 / 优越感 / 嫉妒",
            "machiavellianism": "工具理性 / 操纵他人 / 冷血计算 / 不信任 / 玩世不恭",
            "antisocial_psychopathy": "反社会冲动 / 缺乏内疚 / 病理性撒谎 / 浅薄情感 / 寻求刺激",
            "intersection": "三者并存 = 顶级冷血操纵者；连环杀手/政治反派常具备",
            "narrative_use": "心理悬疑反派 / 罪案小说 / 都市反派 / 黑化主角",
            "activation_keywords": ["暗黑三角", "自恋", "马基雅维利", "反社会", "操纵", "冷血"],
        },
        source_type="llm_synth", confidence=0.81,
        source_citations=[wiki("暗黑三人格", ""), llm_note("Dark Triad 心理学")],
        tags=["心理学", "人格", "反派"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 经济学/博弈
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-econ-power-law",
        name="幂律分布与马太效应",
        narrative_summary="马太效应：富者愈富，强者愈强。互联网/财富/影响力均呈幂律（80/20）分布。"
                          "都市/重生/创业小说体现『起飞曲线』机制。",
        content_json={
            "power_law": "P(X≥x) ∝ x^(-α)：少数节点占据大部分价值",
            "examples": "财富 / 城市人口 / 网络访问量 / 学术引用 / 应用下载量 / 社交粉丝",
            "matthew_principle": "已经有的将更多得到，没有的连仅有的也夺去（《马太福音》25:29）",
            "implication": "起跑差距会指数放大；先发优势 + 复利 + 网络效应叠加",
            "narrative_use": "重生股神 / 都市创业 / 社交媒体崛起 / 帝国扩张",
            "activation_keywords": ["幂律", "80/20", "马太效应", "复利", "网络效应", "先发优势"],
        },
        source_type="llm_synth", confidence=0.81,
        source_citations=[wiki("幂律", ""), wiki("马太效应", ""), llm_note("分布通识")],
        tags=["经济学", "幂律", "通用"],
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
