"""
Batch 20: Profession archetypes for character templates and real-world refs —
医生 / 律师 / 警察 / 教师 / 记者 / 工程师 / 程序员 / 心理医生 / 外交官 /
间谍 / 厨师 / 设计师 / 飞行员.

Activates professional vocabulary for any genre with realistic professions.
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
    # 律师
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="prof-arch-lawyer-shark",
        name="鲨鱼律师原型",
        narrative_summary="顶尖律师角色：表面冷静专业，内心嗜血好胜。庭辩咄咄逼人、对方证人三句话碎掉、媒体上一句话能造话题。"
                          "穿衣品味：定制西装 + 金属表 + 黑色公文包。代表《金装律师》Harvey Specter / 《Suits》。",
        content_json={
            "core_traits": "极度自信 / 嗜赢成性 / 雄辩之才 / 道德灰色 / 善用心理战",
            "signature_moves": "庭审反转证据 / 三句话击溃证人 / 媒体放话造势 / 谈判桌威慑",
            "appearance": "定制西装 / 金属手表 / 黑色公文包 / 锐利眼神",
            "weakness": "工作狂忽略亲情 / 道德焦虑 / 嗜赢导致私德有亏 / 真情时刻笨拙",
            "narrative_use": "都市职场 / 律政剧 / 悬疑罪案 / 重生律师文",
            "activation_keywords": ["律师", "庭辩", "顶尖", "嗜赢", "西装", "证据", "反转"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("律师", ""), llm_note("鲨鱼律师原型")],
        tags=["职业", "律师", "原型"],
    ),
    # 法医
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="prof-arch-forensic-pathologist",
        name="法医（冷静解剖者）原型",
        narrative_summary="法医角色：冷静理性 / 与尸体打交道 / 精通解剖 + 毒理 + DNA + 弹道 / 注重细节到偏执。"
                          "用专业证据帮警察破案。代表《CSI》《法医秦明》《沉默的证人》。",
        content_json={
            "core_skills": "尸体解剖 / 死因分析 / 毒理化验 / DNA 鉴定 / 弹道分析 / 现场重建 / 法医昆虫学",
            "personality_traits": "冷静理性 / 不善社交 / 对生命敬畏 / 完美主义 / 黑色幽默",
            "tools": "解剖刀 / 镊子 / 标本盒 / 显微镜 / 紫外灯 / 尸袋 / 笔记本",
            "common_arc": "外表冷漠 + 内心炽热 / 与活人疏离 + 对死者尊重",
            "famous_inspirations": "秦明（《法医秦明》）/ Maura Isles（《Rizzoli & Isles》）/ Quincy / 《沉默的证人》",
            "narrative_use": "悬疑罪案 / 重生法医 / 都市破案 / 灵异（法医 + 鬼魂线索）",
            "activation_keywords": ["法医", "解剖", "DNA", "尸检", "死因", "弹道", "毒理"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("法医学", ""), llm_note("法医原型")],
        tags=["职业", "法医", "悬疑"],
    ),
    # 警察 - 退役老兵
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="prof-arch-burnt-out-detective",
        name="耗竭老警探原型",
        narrative_summary="老刑警形象：经历过太多 → 烟酒不离 / 离婚或丧偶 / 但破案直觉炉火纯青。"
                          "上司讨厌他但他能搞定案子。新进搭档崇拜或对立。代表《真探》《沉默的羔羊》Will Graham。",
        content_json={
            "background_layers": "20+ 年警龄 / 创伤事件留下心结 / 家庭破碎 / 上司视为麻烦",
            "core_skills": "现场嗅觉 / 心理画像 / 与线人关系 / 老一套审讯术 / 江湖人脉",
            "personality_traits": "愤世嫉俗 / 黑色幽默 / 内心仍有正义 / 对新人或纵容或苛刻",
            "appearance": "皱风衣 / 烟黄手指 / 不刮胡子 / 眼袋深 / 老式手枪",
            "famous_inspirations": "《真探》Rust Cohle / 《沉默的羔羊》Crawford / 《七宗罪》Somerset",
            "narrative_use": "悬疑罪案 / 重生警察 / 黑色都市 / 末日警力",
            "activation_keywords": ["老警探", "刑警", "烟酒", "破案", "心理画像", "线人", "审讯"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("警探", ""), llm_note("老警探原型")],
        tags=["职业", "警察", "原型"],
    ),
    # 心理医生
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="prof-arch-psychiatrist-listener",
        name="心理医生（深邃倾听者）原型",
        narrative_summary="心理医生：表面温和倾听 / 一句话直击痛处 / 与患者保持职业距离却又被牵动。"
                          "可能自己也有心理问题。代表《扪心自问》《心灵猎人》/ 《阳光普照》。",
        content_json={
            "core_skills": "倾听 / 共情 / 微表情解读 / 提问技巧 / 移情反移情 / 防御机制识别",
            "therapy_methods": "精神分析 / 认知行为 CBT / EMDR / 正念 MBCT / 家庭治疗 / 团体治疗",
            "ethical_lines": "保密原则 / 双重关系禁忌 / 移情管理 / 自我督导",
            "personality_traits": "外冷内热 / 自我克制 / 深邃眼神 / 善察人于秋毫",
            "common_struggles": "替代创伤 / 倦怠 / 自我边界 / 前任移情",
            "narrative_use": "心理悬疑 / 都市言情（咨询师爱上来访者）/ 心理向案件破解 / 暗黑题材",
            "activation_keywords": ["心理医生", "咨询", "移情", "共情", "防御机制", "倾听", "CBT"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("心理治疗", ""), llm_note("心理医生原型")],
        tags=["职业", "心理", "原型"],
    ),
    # 程序员
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="prof-arch-hacker-genius",
        name="天才黑客原型",
        narrative_summary="天才黑客：年轻、孤僻、靠键盘 + 显示器吃饭、零社交但脑回路超快。"
                          "可正可邪：白帽（保护）/ 黑帽（攻击）/ 灰帽（混合）。"
                          "代表《Mr Robot》Elliot / 《社交网络》/ 《黑客帝国》Neo。",
        content_json={
            "skill_layers": "前端 / 后端 / 数据库 / 系统底层 / 渗透测试 / 加密解密 / 社工 / 暗网知识",
            "common_tools": "终端 / VPN / Tor / 代理 / 多显示器 / 自定义键盘 / 黑色 T 恤 + 帽衫",
            "personality_traits": "高智商 / 低情商 / 偏执 / 黑色幽默 / 反权威 / 偶尔焦虑症",
            "ethical_alignments": "白帽（道德边界）/ 黑帽（牟利或破坏）/ 灰帽（看心情）/ 行动主义（Anonymous）",
            "famous_inspirations": "Elliot（Mr Robot）/ Lisbeth Salander（龙纹身的女孩）/ Neo（Matrix）",
            "narrative_use": "现代悬疑 / 商战 / 赛博朋克 / 末日科技 / 反恐",
            "activation_keywords": ["黑客", "白帽", "黑帽", "终端", "渗透", "加密", "暗网"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("骇客", ""), llm_note("天才黑客原型")],
        tags=["职业", "程序员", "原型"],
    ),
    # 间谍
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="prof-arch-spy-cold-blood",
        name="冷血间谍原型",
        narrative_summary="间谍角色：双重身份 / 任务至上 / 情感是工具 / 永远多个 ID。"
                          "类型：渗透型（James Bond）/ 卧底型（《潜伏》余则成）/ 分析师（CIA）/ 技术（叶问）。"
                          "代表《谍影重重》《007》《潜伏》《伪装者》。",
        content_json={
            "spy_types": "渗透（外勤）/ 卧底（长期身份）/ 分析师（情报中心）/ 技术员 / 杀手 / 双面间谍",
            "core_skills": "伪装 / 多语言 / 武器 / 心理战 / 密码 / 反跟踪 / 微表情解读",
            "lifestyle": "无固定居所 / 多本护照 / 备用身份 / 不留痕 / 永远在评估退路",
            "personality_traits": "极度自律 / 任务优先 / 情感工具化 / 但深处仍有人性",
            "common_arcs": "执行任务 → 暴露身份 → 个人情感冲突任务 → 选择 → 救赎或牺牲",
            "famous_inspirations": "James Bond / Jason Bourne / 余则成（潜伏）/ 明楼（伪装者）/ Black Widow",
            "narrative_use": "谍战 / 民国 / 现代国安 / 都市言情（间谍 + 平凡爱人）",
            "activation_keywords": ["间谍", "卧底", "双面", "潜伏", "情报", "伪装", "密码"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("间谍", ""), llm_note("间谍原型")],
        tags=["职业", "间谍", "原型"],
    ),
    # 记者
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="prof-arch-investigative-reporter",
        name="调查记者原型",
        narrative_summary="调查记者：好奇心驱动 / 真相至上 / 不怕得罪权贵 / 工作狂。"
                          "为爆料连续熬夜 / 被威胁恐吓 / 与编辑斗争。"
                          "代表《聚焦》《华盛顿邮报》《乔治·索马》。",
        content_json={
            "core_skills": "信息源管理 / 文件挖掘 / 跨界调查 / 法律边界 / 写作快狠准",
            "ethical_principles": "三方核实 / 保护信源 / 公共利益 / 拒绝受贿",
            "personality_traits": "好奇心爆棚 / 倔强 / 工作狂 / 黑色幽默 / 不安分",
            "tools": "笔记本 / 录音笔 / 长焦镜头 / 加密通讯 / 关系网",
            "common_threats": "权贵打压 / 恐吓 / 收买 / 编辑撤稿 / 法律诉讼",
            "famous_inspirations": "《聚焦》波士顿环球报 / 《华盛顿邮报》水门事件 / 战地记者 Marie Colvin",
            "narrative_use": "都市悬疑 / 商战揭露 / 民国新闻人 / 重生记者文",
            "activation_keywords": ["记者", "调查", "信源", "爆料", "真相", "编辑", "采访"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("调查报道", ""), llm_note("调查记者原型")],
        tags=["职业", "记者", "原型"],
    ),
    # 老师
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="prof-arch-inspiring-teacher",
        name="启发式教师原型",
        narrative_summary="老师角色：关注每一个学生 / 用非传统方式教学 / 与体制斗争。"
                          "代表《死亡诗社》Robin Williams / 《放牛班的春天》/ 《心灵捕手》Sean。",
        content_json={
            "teaching_styles": "苏格拉底式（追问）/ 启发式（引导发现）/ 项目式（动手）/ 翻转课堂 / 混合式",
            "personality_traits": "热情 / 耐心 / 同理心强 / 个性独立 / 不被体制驯服",
            "common_conflicts": "与校长冲突 / 家长不理解 / 学生家庭问题 / 教育资源匮乏",
            "growth_arc": "刚来时困难 → 与学生建立信任 → 改变某些学生命运 → 自己也被启发",
            "famous_inspirations": "John Keating（死亡诗社）/ Mathieu（放牛班）/ Sean Maguire（心灵捕手）/ 《热血教师》",
            "narrative_use": "校园 / 言情（老师 × 学生 — 慎用）/ 重生教师 / 山区支教",
            "activation_keywords": ["老师", "启发", "苏格拉底", "学生", "教育", "体制", "改变"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("教师", ""), llm_note("启发教师原型")],
        tags=["职业", "教师", "原型"],
    ),
    # 厨师 - 米其林
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="prof-arch-michelin-chef",
        name="米其林星级主厨原型",
        narrative_summary="米其林主厨：完美主义 / 暴躁脾气 / 厨房如战场 / 视烹饪为艺术。"
                          "代表 Gordon Ramsay / 《料理鼠王》Skinner / 《主厨的餐桌》纪录片群像。"
                          "对食材近乎宗教虔诚，对副厨毫无情面。",
        content_json={
            "ranks": "学徒 → 副厨（Sous Chef）→ 主厨（Chef de Cuisine）→ 行政主厨（Executive Chef）",
            "kitchen_culture": "Brigade（旅）制 / Yes Chef 文化 / 高压精确 / 16 小时工作 / 烫伤刀伤如家常",
            "michelin_standards": "1 星：值得停车 / 2 星：值得绕路 / 3 星：值得专程",
            "personality_traits": "完美主义 / 暴躁 / 对食材敬畏 / 创造欲强烈 / 私下脆弱",
            "famous_inspirations": "Gordon Ramsay / Anthony Bourdain（《厨房机密》）/ 主厨的餐桌纪录片",
            "narrative_use": "美食小说 / 重生厨师 / 都市米其林创业 / 言情（性格反差）",
            "activation_keywords": ["米其林", "主厨", "Sous Chef", "Brigade", "三星", "完美主义", "厨房"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("米其林指南", ""), llm_note("米其林主厨原型")],
        tags=["职业", "厨师", "原型"],
    ),
    # 飞行员
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="prof-arch-fighter-pilot",
        name="王牌战斗机飞行员原型",
        narrative_summary="战斗机飞行员：纪律严明 / 临危不乱 / G 力承受能力极强 / 团队作战。"
                          "三代机 → 四代机 → 五代机演进。代表《壮志凌云》Maverick / 《独立日》。",
        content_json={
            "training_path": "空军学院 → 基础飞行 → 高级飞行 → 战斗机改装 → 中队 → 王牌（5+ 击落）",
            "core_skills": "G 力承受 / 仪表飞行 / 空战机动（破 S / Yo-Yo / 剪刀机动）/ 编队 / 武器系统",
            "common_aircraft": "F-22 / F-35 / Su-57 / 歼-20 / 阵风 / 台风 / F-16 / Su-27",
            "personality_traits": "自信（甚至自负）/ 沉着冷静 / 纪律性强 / 兄弟情深（中队战友）",
            "famous_inspirations": "Maverick（壮志凌云）/ 红男爵 / Saburo Sakai 坂井三郎 / Chuck Yeager",
            "narrative_use": "现代军事 / 末日空战 / 重生飞行员 / 言情（飞行员 × 平凡女）",
            "activation_keywords": ["飞行员", "王牌", "F-35", "歼-20", "G 力", "中队", "空战"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("战斗机飞行员", ""), llm_note("王牌飞行员原型")],
        tags=["职业", "飞行员", "军事"],
    ),
    # 设计师
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="prof-arch-fashion-designer",
        name="时装设计师原型",
        narrative_summary="设计师角色：审美极致 / 艺术家脾气 / 看世界用色彩与剪裁的眼睛。"
                          "代表《穿普拉达的女王》Miranda / Coco Chanel / Yves Saint Laurent / Karl Lagerfeld。"
                          "工作模式：灵感 → 草图 → 打版 → 成衣 → 走秀。",
        content_json={
            "process": "灵感采风 → 概念草图 → 面料选择 → 打版师制版 → 缝纫 → 试衣 → 修改 → 走秀",
            "fashion_systems": "高级定制 Haute Couture / 高级成衣 / 大众成衣 / 快时尚",
            "fashion_weeks": "巴黎 / 米兰 / 纽约 / 伦敦 — 春夏 + 秋冬 两季",
            "personality_traits": "审美苛刻 / 艺术家脾气 / 完美主义 / 亲手缝制狂热 / 缪斯依赖",
            "famous_inspirations": "Coco Chanel / Yves Saint Laurent / Karl Lagerfeld / Tom Ford / Miuccia Prada / 川久保玲",
            "narrative_use": "都市言情 / 娱乐圈 / 时尚商战 / 重生设计师文",
            "activation_keywords": ["设计师", "时装", "高定", "走秀", "灵感", "缪斯", "Chanel"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[wiki("时装设计师", ""), llm_note("设计师原型")],
        tags=["职业", "设计师", "时尚"],
    ),
    # 外交官
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="prof-arch-diplomat",
        name="外交官原型",
        narrative_summary="外交官：多语言精通 / 礼仪炉火纯青 / 内心冷静计算 / 表面温文。"
                          "工作场景：使馆 / 国际会议 / 鸡尾酒会 / 闭门谈判。"
                          "适用于民国谍战、现代国际题材、历史使节。",
        content_json={
            "core_skills": "语言（≥3 种）/ 国际法 / 礼仪 / 谈判 / 危机处理 / 跨文化沟通",
            "ranks": "大使 / 公使 / 参赞 / 一等秘书 / 二等秘书 / 三等秘书 / 随员",
            "venues": "使馆酒会 / 国际峰会 / 双边谈判 / 媒体采访 / 危机斡旋",
            "personality_traits": "内敛 / 多语言 / 文化敏感 / 沉着 / 善于隐藏真实想法",
            "famous_inspirations": "周恩来（建国后外交奠基）/ 基辛格 / 顾维钧 / Madeleine Albright",
            "narrative_use": "民国题材 / 现代国际 / 历史使节 / 言情（外交官 × 翻译官）",
            "activation_keywords": ["外交官", "大使", "使馆", "谈判", "国际法", "礼宾", "鸡尾酒会"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("外交官", ""), llm_note("外交官原型")],
        tags=["职业", "外交", "原型"],
    ),
    # 演员
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="prof-arch-method-actor",
        name="方法派演员原型",
        narrative_summary="方法派（Method Acting）演员：为角色彻底入戏 / 体重暴增暴减 / 长期保持角色心理状态。"
                          "代表 Marlon Brando / Daniel Day-Lewis / Christian Bale / 张曼玉。"
                          "适用于娱乐圈题材塑造『艺术派』演员形象。",
        content_json={
            "method_principles": "Stanislavski 方法 → Lee Strasberg Method / 角色心理沉浸 / 替代记忆 / 情感记忆",
            "famous_examples": "Brando《教父》/ Day-Lewis《我的左脚》《林肯》/ Bale《机械师》减重 30kg / Heath Ledger《小丑》/ 张曼玉《阮玲玉》",
            "preparation_methods": "采访原型 / 体验生活（住进角色环境）/ 体重身形改造 / 学习专业技能 / 维持角色心理状态",
            "personality_traits": "执着 / 自我消解 / 神秘 / 不易亲近 / 表演时灵魂出窍",
            "narrative_use": "娱乐圈 / 都市艺术家 / 重生演员 / 演艺圈悬疑",
            "activation_keywords": ["方法派", "Method", "入戏", "Brando", "Day-Lewis", "斯坦尼", "情感记忆"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[wiki("方法演技", ""), llm_note("方法派演员原型")],
        tags=["职业", "演员", "娱乐圈"],
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
