"""
Batch 12: Thin-genre deep dive.
Bulk-up 校园/女尊/灵异/重生/机甲/赛博朋克/穿书 with multiple dimensions each.
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
    # ═══════════ 灵异 deep dive ═══════════
    MaterialEntry(
        dimension="plot_patterns", genre="灵异",
        slug="liyi-pp-haunting-investigation",
        name="灵异调查弧",
        narrative_summary="收到求助→实地勘察→灵异现象升级→历史/真相挖掘→对峙/超度。"
                          "标准灵异调查情节模板：每个阶段都要有新的恐惧或线索递进，最后揭示的不只是鬼，而是一个完整的人间悲剧。",
        content_json={
            "act_1": "委托人描述（往往不完整或撒谎）/ 初步勘察发现端倪",
            "act_2": "灵异升级（更频繁/更危险）/ 历史挖掘（旧档案/旧居民/旧物件）",
            "act_3": "真相还原（亡灵的执念是什么）/ 对峙（不一定是战斗，可能是说服）/ 解决（超度或共存）",
            "tension_arc": "起初是好奇 → 逐渐恐惧 → 同情 → 哀伤",
            "resolution_layers": "解决案件 + 主角自我认知的成长",
            "activation_keywords": ["灵异调查", "查案", "凶宅", "鬼屋", "案件还原"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("灵异调查情节设计")],
        tags=["灵异", "调查", "情节", "弧线"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="灵异",
        slug="liyi-tm-unfinished-business",
        name="未了心愿主题",
        narrative_summary="灵异类作品的核心情感主题：每一个鬼魂背后都有一个未完成的故事。"
                          "好的灵异作品让读者在恐惧之后产生悲悯——『鬼只是没能告别的人』。",
        content_json={
            "unfinished_business_types": [
                "想见某人最后一面",
                "想说一句没说出口的话",
                "想完成一件未做完的事",
                "想报复造成自己死亡的人",
                "不愿离开自己挚爱的人/物",
            ],
            "resolution_mechanics": "解开心愿 = 鬼魂得以离去",
            "tragic_irony": "活人也有未了心愿，只是不自知；与鬼对话即是与自己对话",
            "writing_advice": "把每个鬼当作一个完整的人去写——不是符号是生命",
            "activation_keywords": ["未了心愿", "执念", "亡灵", "告别", "灵魂回响"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("超度", "佛教"), llm_note("灵异主题深化")],
        tags=["灵异", "心愿", "执念", "主题"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="灵异",
        slug="liyi-ac-反鬼必恶",
        name="鬼必恶劣陷阱",
        narrative_summary="把所有鬼魂写成单纯的恶意存在会丧失叙事深度。"
                          "好的灵异让读者看到鬼魂作为『曾经的人』的复杂性——他们的恶意往往源自人间的伤害。",
        content_json={
            "cliché": "见鬼=必须降伏 / 鬼=恐怖怪物",
            "deep_writing": "鬼是带着人世记忆和情感的存在 / 他们的恶意有原因",
            "fix_strategies": [
                "每个鬼有完整的『生前故事』，揭示让读者共情",
                "鬼魂的『恶』和『善』是模糊的——可能是误会而非恶意",
                "活人的恶往往比鬼更可怕",
            ],
            "benchmark": "《鬼吹灯》/《阴阳师》中鬼的多面性",
            "activation_keywords": ["鬼魂深度", "灵异共情", "鬼的故事", "反符号化"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("灵异反套路分析")],
        tags=["灵异", "反套路", "深度", "鬼魂"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre="灵异",
        slug="liyi-ea-fear-to-empathy",
        name="恐惧→共情情感弧",
        narrative_summary="主角从对鬼魂的纯粹恐惧到逐渐产生共情的情感转化弧。"
                          "这种转化让灵异作品超越类型片范畴，触及人性深层主题。",
        content_json={
            "phase_1_fear": "纯生理恐惧 / 想要逃避或消灭",
            "phase_2_curiosity": "在被迫接触中开始好奇『为什么』",
            "phase_3_understanding": "了解鬼魂的故事，恐惧降低",
            "phase_4_empathy": "为鬼魂感到悲伤 / 主动想帮助",
            "phase_5_acceptance": "接受『阴阳并存』的世界观",
            "narrative_principle": "这种弧线让读者也走完同样的心路",
            "activation_keywords": ["恐惧到共情", "灵异成长", "人鬼共情", "情感转化"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("灵异情感弧线分析")],
        tags=["灵异", "情感", "弧线", "共情"],
    ),

    # ═══════════ 校园 deep dive ═══════════
    MaterialEntry(
        dimension="character_templates", genre="校园",
        slug="campus-ct-rebel-elite",
        name="叛逆精英型主角",
        narrative_summary="家境优越但厌弃精英规则的校园学生：成绩可以全校第一但故意只考第二，"
                          "可以获得所有资源但选择最低调的方式。其叛逆是对父辈精英价值观的拒绝。",
        content_json={
            "background": "家族顶级精英背景 / 父辈对其有明确期待",
            "personality": "理性但情感封闭 / 对规则极度敏感 / 会做选择题但不愿做选择题",
            "social_position": "可以站在金字塔顶端但故意游走在边缘",
            "growth_axis": "学会接受自己的优势不等于接受家族安排的人生",
            "love_interest_potential": "被某个真正不在乎他家境的人触动",
            "activation_keywords": ["叛逆精英", "校园隐藏王牌", "故意低调", "家族期待"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("校园主角原型分析")],
        tags=["校园", "精英", "叛逆", "主角"],
    ),
    MaterialEntry(
        dimension="character_templates", genre="校园",
        slug="campus-ct-warm-light",
        name="班级温柔光源型角色",
        narrative_summary="班级里那个被所有人喜欢的『温柔光源』：成绩中上、长相普通但很舒服、"
                          "总是记得每个人的小事、被霸凌时第一个站出来。常常是配角但是叙事支柱。",
        content_json={
            "personality": "情商高 / 善于倾听 / 不站队但温暖",
            "narrative_function": "凝聚班级 / 主角的情感锚点 / 转折点的触发者",
            "tragic_potential": "正因为太懂事，自己的痛苦无人察觉",
            "common_arcs": "逐渐被主角发现其也有挣扎 / 在某个事件中爆发",
            "activation_keywords": ["温柔同学", "班级光", "情商高", "温暖型"],
        },
        source_type="llm_synth", confidence=0.71,
        source_citations=[llm_note("校园配角原型分析")],
        tags=["校园", "温柔", "配角", "光源"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="校园",
        slug="campus-tm-coming-of-age",
        name="成长主题（青春骨架）",
        narrative_summary="校园文学的根本主题：从孩子到成人的过渡。"
                          "面对第一次的爱、第一次的失去、第一次理解世界的复杂——这些『第一次』构成了青春的所有重量。",
        content_json={
            "key_first_times": [
                "第一次喜欢一个人",
                "第一次失去重要的人/物",
                "第一次理解父母的不完美",
                "第一次承担后果",
                "第一次做不退缩的选择",
            ],
            "narrative_principle": "成长不是直线，而是反复的前进与倒退",
            "Chinese_specific_pressures": "高考/家庭期待/同辈竞争是叠加的特殊压力",
            "writing_anchors": "用具体物件（毕业册/校服/操场）承载情感",
            "activation_keywords": ["成长", "青春", "第一次", "蜕变", "校园回忆"],
        },
        source_type="llm_synth", confidence=0.77,
        source_citations=[wiki("成长小说", ""), llm_note("青春主题分析")],
        tags=["校园", "成长", "青春", "主题"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="校园",
        slug="campus-ac-反完美初恋",
        name="完美初恋幻觉陷阱",
        narrative_summary="把校园初恋写成纯洁完美的浪漫会失去真实感。"
                          "真实的青春恋爱有笨拙、有误解、有自尊心作祟、有突如其来的冷暴力——这些反而是最珍贵的部分。",
        content_json={
            "cliché": "校园恋爱=纯白甜美/无杂质",
            "fix_strategies": [
                "保留笨拙感（不知道怎么表达）",
                "让自尊心和爱意一起作怪",
                "误解和冷战是常态而非偶然",
                "结局未必是在一起——分开也可以美好",
            ],
            "real_first_love": "可能没说出口就过去了 / 多年后才意识到那就是爱",
            "activation_keywords": ["真实初恋", "校园恋爱", "笨拙青春", "反糖衣"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("校园爱情反套路")],
        tags=["校园", "反套路", "初恋", "真实"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre="校园",
        slug="campus-ea-jealousy-friendship",
        name="嫉妒中的友谊弧",
        narrative_summary="校园好友间因比较产生的嫉妒情感弧：从单纯友谊→意识到差距→嫉妒→自我消化或爆发→重新理解友谊。"
                          "这是校园人际中最真实的情感动力之一。",
        content_json={
            "trigger_events": "考试排名 / 异性关注 / 家庭条件比较 / 才艺展示",
            "phases": [
                "1. 单纯友谊 - 没有比较意识",
                "2. 觉察差距 - 客观存在但不愿承认",
                "3. 隐性嫉妒 - 内心矛盾但表面正常",
                "4. 微小裂痕 - 阴阳怪气、突然的冷淡",
                "5. 爆发或消化 - 大吵一架/独自处理",
                "6. 重塑 - 接受差距同时维持友谊",
            ],
            "writing_principle": "嫉妒不是缺德——是真实情感，承认才有出路",
            "activation_keywords": ["嫉妒友谊", "校园情感", "比较心理", "友谊危机"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("嫉妒", "心理学"), llm_note("校园情感弧线")],
        tags=["校园", "嫉妒", "友谊", "情感弧"],
    ),

    # ═══════════ 重生 deep dive ═══════════
    MaterialEntry(
        dimension="thematic_motifs", genre="重生",
        slug="rebirth-tm-second-chance",
        name="第二次机会主题",
        narrative_summary="重生类作品最核心的情感诉求：『如果当时...』的全人类共同执念。"
                          "好的重生不只是爽，而是认真探索『再来一次真的能不一样吗』这个问题。",
        content_json={
            "core_question": "再来一次，我会做出不同选择吗？",
            "complications": [
                "不只是知识/能力的差异，更是性格/认知的局限",
                "前世的某些选择即使重来仍会做相同决定",
                "改变某事的连锁后果可能让其他事变更糟",
            ],
            "psychological_truth": "重生者最痛苦的不是知道未来，而是知道自己的局限",
            "narrative_endgame": "真正的成长不是做对所有题，而是接纳无法做对所有题",
            "activation_keywords": ["再来一次", "第二次机会", "如果当时", "重生的意义"],
        },
        source_type="llm_synth", confidence=0.77,
        source_citations=[llm_note("重生主题深化分析")],
        tags=["重生", "机会", "悔悟", "主题"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="重生",
        slug="rebirth-ac-反全胜",
        name="重生全胜陷阱",
        narrative_summary="重生主角凭借先知优势全程碾压会让叙事失去张力。"
                          "好的重生需要：先知优势不万能、蝴蝶效应反噬、新出现的非前世变量。",
        content_json={
            "cliché": "重生=开挂全胜",
            "fix_strategies": [
                "前世发生的事这一世因主角行动而不发生（信息失效）",
                "新的人物/事件出现是主角不知道的",
                "蝴蝶效应让某些原本好的事反而变糟",
                "心理压力（孤独/责任感）成为新挑战",
            ],
            "tension_principle": "知道未来 ≠ 控制未来",
            "activation_keywords": ["重生反套路", "蝴蝶效应", "信息失效", "新变量"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("重生反套路分析")],
        tags=["重生", "反套路", "全胜", "张力"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="重生",
        slug="rebirth-ds-knowing-silence",
        name="重生者沉默的对话",
        narrative_summary="重生者面对前世死去/伤害自己的人时的特殊对话方式：表面平静的话语下藏着汹涌的情感。"
                          "通过『未说出口的部分』比说出来的部分更有力量。",
        content_json={
            "writing_techniques": [
                "对方说出某句前世也说过的话 → 主角微小的物理反应（停顿/呼吸）",
                "主角说一句普通话，对方完全理解不到深意",
                "在对话中突然意识到这次可以不一样",
                "对方的笑容一如前世——主角内心的复杂",
            ],
            "POV_management": "内心独白和外部对话之间的张力是核心",
            "emotional_principle": "节制比宣泄更打动人",
            "activation_keywords": ["重生对话", "未说出口", "内心翻涌", "知情者沉默"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[llm_note("重生对话设计")],
        tags=["重生", "对话", "沉默", "张力"],
    ),

    # ═══════════ 机甲 deep dive ═══════════
    MaterialEntry(
        dimension="character_archetypes", genre="机甲",
        slug="mecha-ca-rebellious-pilot",
        name="规则之外的天才驾驶员",
        narrative_summary="天赋极高但拒绝遵守驾驶员规则的角色：操作非主流但效果惊人，"
                          "上级又爱又恨。这种角色把机甲驾驶推向艺术维度——它不只是技术，更是与机器对话的能力。",
        content_json={
            "skill_set": "天赋极高 / 拒绝标准操作流程 / 自创战术",
            "personality": "桀骜不驯 / 对体制不耐烦 / 只对真正强者尊敬",
            "narrative_function": "打破读者对机甲操作的固定想象 / 推动战术创新",
            "vulnerability": "因不团队合作易在大规模战役中孤立",
            "common_arcs": "从被排斥的天才到成为团队不可或缺的灵魂",
            "activation_keywords": ["天才驾驶员", "非主流战术", "机甲艺术", "桀骜"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("机甲主角原型")],
        tags=["机甲", "天才", "叛逆", "驾驶员"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="机甲",
        slug="mecha-tm-evolution-of-war",
        name="战争形态进化主题",
        narrative_summary="机甲题材的根本主题：技术进步如何重塑战争和人的关系。"
                          "从冷兵器到火器到机甲，每一次技术跃迁都重新定义了『勇敢』『荣誉』『牺牲』的意义。",
        content_json={
            "core_questions": [
                "当机甲能远程操作，前线的勇气还有意义吗？",
                "AI辅助决策时，人的直觉是冗余还是关键？",
                "机甲改造人的身体，他还是『人』吗？",
                "战争的胜利从『谁更勇』变成『谁科技更强』",
            ],
            "philosophical_dimension": "技术进步与人性的张力",
            "Chinese_resonance": "结合中国哲学（孙子兵法/老子）的视角更深",
            "activation_keywords": ["战争形态", "技术进步", "勇气", "机甲哲学", "未来战争"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("机甲主题分析"), wiki("机器人伦理", "")],
        tags=["机甲", "战争", "技术", "主题"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre="机甲",
        slug="mecha-rwr-military-doctrine",
        name="现代军事学说激活",
        narrative_summary="给机甲创作提供真实战术基础：现代陆海空联合作战/不对称战争/电子战/无人机蜂群。"
                          "理解这些可让机甲战斗从『放大版功夫片』升级为有真实战略思考的作品。",
        content_json={
            "key_doctrines": [
                "联合作战（陆海空协同）",
                "OODA循环（观察-定向-决策-行动）",
                "不对称战争（弱方对抗强方）",
                "网络中心战（信息节点）",
                "蜂群战术（数量对质量）",
            ],
            "real_battles_to_study": ["海湾战争", "克里米亚冲突", "无人机战术"],
            "narrative_applications": "战术情节的真实感 / 战略层面的格局",
            "Chinese_thinkers": "孙武/孙膑/毛泽东军事思想 都有现代价值",
            "activation_keywords": ["现代军事", "OODA", "不对称战", "联合作战", "蜂群"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[wiki("OODA循环", ""), wiki("联合作战", ""), llm_note("军事学说叙事")],
        tags=["机甲", "军事", "战术", "现实"],
    ),

    # ═══════════ 赛博朋克 deep dive ═══════════
    MaterialEntry(
        dimension="character_templates", genre="赛博朋克",
        slug="cyber-ct-netrunner",
        name="底层网络黑客",
        narrative_summary="生活在地下层的高技术黑客：拥有顶尖入侵能力但身体破败、神经系统不稳定。"
                          "他们是赛博朋克世界的实际权力——能进入任何系统的人比有钱人更危险。",
        content_json={
            "skills": "网络入侵 / 系统熟悉度 / 反追踪 / 临场代码",
            "physical_state": "通常瘦弱 / 神经系统超载/睡眠不足",
            "social_position": "被企业追杀同时被人崇拜",
            "moral_stance": "对企业彻底敌对 / 对底层有保护欲",
            "vulnerability": "依赖技术 / 物理对抗能力弱 / 黑冰系统的恐惧",
            "activation_keywords": ["黑客", "网络战士", "netrunner", "入侵者"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("赛博空间", ""), llm_note("赛博朋克主角原型")],
        tags=["赛博朋克", "黑客", "技术", "底层"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="赛博朋克",
        slug="cyber-tm-humanity-vs-machine",
        name="人性vs机械主题",
        narrative_summary="赛博朋克的根本主题：在技术深度改造人体的世界，"
                          "什么定义了『人性』？是肉身？是大脑？是情感？是回忆？这是该体裁永恒的哲学追问。",
        content_json={
            "core_dilemmas": [
                "用义体替换器官还是『人』吗？",
                "意识上传后那是『你』吗？",
                "情感被改造移除还有自由意志吗？",
                "AI产生情感后它是『生命』吗？",
            ],
            "famous_works": ["《银翼杀手》仿生人是否有灵魂", "《攻壳机动队》Ghost的本质", "《赛博朋克2077》义体过载"],
            "philosophical_anchors": "笛卡尔身心二元论 / 忒修斯之船悖论",
            "Chinese_compatible_thoughts": "庄周梦蝶 / 人之初性本善的怀疑",
            "activation_keywords": ["人性边界", "义体", "意识上传", "灵魂", "忒修斯之船"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("赛博朋克", "哲学"), wiki("银翼杀手", ""), llm_note("赛博朋克主题")],
        tags=["赛博朋克", "人性", "机器", "主题"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre="赛博朋克",
        slug="cyber-ea-humanity-loss",
        name="人性流失情感弧",
        narrative_summary="主角在不断改造义体的过程中逐渐失去情绪敏感度的弧线。"
                          "每一次改造换来力量，但每一次都失去一点对生活细节的感受力。最终面临选择：完全机械还是回归。",
        content_json={
            "stages": [
                "1. 第一次改造（兴奋的力量提升）",
                "2. 习惯（对疼痛的麻木）",
                "3. 注意（情绪反应变弱）",
                "4. 警觉（朋友指出变化）",
                "5. 临界（无法感受爱/喜悦）",
                "6. 抉择（继续机械化还是停止）",
            ],
            "narrative_function": "外部改造照见内在选择",
            "common_endgames": ["完全机械化（失去人性的胜利）", "拒绝改造（保留人性的失败）", "找到平衡（最难的选项）"],
            "activation_keywords": ["人性流失", "义体改造", "失去情感", "机械化"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[llm_note("赛博朋克情感弧线分析")],
        tags=["赛博朋克", "人性", "情感", "弧线"],
    ),

    # ═══════════ 穿书 deep dive ═══════════
    MaterialEntry(
        dimension="character_templates", genre="穿书",
        slug="chuanshu-ct-self-aware-character",
        name="知道自己是配角的穿书者",
        narrative_summary="穿书主角穿入小说后发现自己是悲剧配角：原作中此人被女主男主联手害死。"
                          "于是开始反向操作——既要避免自己的死亡，又不能让原作主线崩坏到引发系统报复。",
        content_json={
            "starting_state": "穿入即知自己结局",
            "core_strategy": "在原作主线允许的范围内最大化生存可能",
            "limitation": "不能完全脱离原作角色性格 / 不能让女主男主CP崩塌",
            "growth_arc": "从被动求生 → 主动改写命运 → 发现这本书背后另有真相",
            "irony_layer": "为生存所做的事反而推动了情节深化",
            "activation_keywords": ["炮灰逆袭", "穿书配角", "知情者", "改写结局"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("穿书主角设计")],
        tags=["穿书", "配角", "自救", "主角"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="穿书",
        slug="chuanshu-pp-meta-rebellion",
        name="原作设定反抗弧",
        narrative_summary="穿书主角逐渐发现原作中的某些设定有问题（女主有暗黑面/男主行为伤害很多无辜配角），"
                          "决定不只是为自己生存，更是为整个故事中的『被忽视者』改写命运。",
        content_json={
            "act_structure": "穿入→自救→发现原作问题→联合配角→颠覆原作主线",
            "themes": "对原创作者的隐性批判 / 给『工具人配角』正义",
            "danger_level": "对原作主线的过度颠覆会触发系统反扑",
            "endgame_options": "建立新平衡 / 发现作者身份 / 跳出书页",
            "activation_keywords": ["反抗原作", "改写主线", "配角群英", "穿书觉醒"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[llm_note("穿书叙事结构")],
        tags=["穿书", "反抗", "原作", "情节"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="穿书",
        slug="chuanshu-tm-narrative-ethics",
        name="叙事伦理主题",
        narrative_summary="穿书类作品独有的元主题：作者对笔下人物有什么责任？"
                          "把人物写得不公平是否就是『不道德』？这种自反性思考让穿书超越简单玛丽苏，触及创作论本身。",
        content_json={
            "philosophical_questions": [
                "笔下人物的痛苦如果有真实感，作者的伦理责任是什么？",
                "作者塑造的『反派』如果其实是受害者怎么办？",
                "读者代入主角的舒适感是建立在配角痛苦之上的吗？",
            ],
            "narrative_devices": [
                "穿书者作为代理读者审判原作",
                "原作主角的『工具人化』被揭露",
                "原作者出场作为高维存在",
            ],
            "activation_keywords": ["叙事伦理", "作者责任", "配角觉醒", "穿书反思"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[llm_note("穿书元叙事分析"), wiki("元小说", "文学理论")],
        tags=["穿书", "叙事", "伦理", "元主题"],
    ),

    # ═══════════ 女尊 deep dive ═══════════
    MaterialEntry(
        dimension="character_templates", genre="女尊",
        slug="nüzun-ct-female-emperor",
        name="女尊世界的女皇/女帝",
        narrative_summary="女尊背景下的最高统治者女性：拥有男权世界皇帝同等的权力，"
                          "但镜像中也有男性后宫、儿子继承的考量、对男宠的复杂情感。是反思权力本质的最佳载体。",
        content_json={
            "power_dynamics": "和男性皇帝同样的孤独/猜忌/责任",
            "personal_complications": "如何看待男后/男宠 / 对爱情的渴望与不可得",
            "narrative_function": "通过性别反转让读者重新审视权力的本质",
            "common_arcs": "继位之初的挑战 / 与男性配偶的特殊关系 / 立储之争",
            "activation_keywords": ["女皇", "女帝", "女尊君主", "权力反转"],
        },
        source_type="llm_synth", confidence=0.71,
        source_citations=[wiki("武则天", ""), llm_note("女尊君主原型")],
        tags=["女尊", "女皇", "权力", "镜像"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre="女尊",
        slug="nüzun-ea-power-dynamics",
        name="权力关系中的情感弧",
        narrative_summary="女尊设定下，权力高位的女主与处于弱势的男主之间的情感关系如何在不平等中找到真正的爱。"
                          "这种弧线对现实中性别权力关系是一种深刻的镜像审视。",
        content_json={
            "starting_imbalance": "女主拥有所有权力 / 男主在制度上处于劣势",
            "evolution": [
                "1. 制度性互动（按规则）",
                "2. 个人吸引（突破规则）",
                "3. 权力的负担（女主意识到自己的优势是负担）",
                "4. 对等的奇迹（不靠制度的相互信任）",
            ],
            "real_world_resonance": "通过镜像让读者反思现实中的性别权力",
            "activation_keywords": ["权力关系", "女尊爱情", "不对等", "信任建立"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[llm_note("女尊情感叙事分析")],
        tags=["女尊", "权力", "情感", "弧线"],
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
