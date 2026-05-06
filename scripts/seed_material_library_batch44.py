"""Batch 44: anti_cliche_patterns niche genres + character_archetypes complex villains

Fills:
- anti_cliche for 校园/灵异/穿书/娱乐圈/末日/赛博朋克/历史/灵异/无限流
- character_archetypes for grey-morality villains (复杂反派/灰色道德)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bestseller.infra.db.session import session_scope
from bestseller.services.material_library import MaterialEntry, insert_entry


def llm_note(note: str) -> dict:
    return {"source": "llm_synth", "note": note}


def wiki(title: str, note: str = "") -> dict:
    return {"source": "wikipedia", "title": title, "note": note}


ENTRIES: list[MaterialEntry] = [
    # ---------- anti_cliche_patterns (8) ----------
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="校园",
        slug="anti-cliche-campus-cold-male-lead",
        name="反套路：校园冷酷男主+学渣女主套路重灾",
        narrative_summary="校园文最重的套路：冰山男主+学渣女主+ 男主默默送笔记+雨天接送+暗中保护。这个组合在 2010-2024 年被消费过千次。给现代读者看就是审美疲劳。",
        content_json={
            "banned_patterns": "1) 冰山校草（180+/全校第一/家世好/不爱说话）+ 学渣女主（170-/挂科/家境一般/呆萌）/ 2) 雨天接送 / 3) 默默送笔记 / 4) 篮球赛绝杀回头看女主 / 5) 教训女生用'她是我的'/ 6) 排球场把球反弹打到女主接 ",
            "alternative_strategies": "1) 让冰山男主有具体的精神缺陷（社交焦虑+童年阴影），不是纯耍帅 / 2) 学渣女主有自己的特长（跳街舞/电竞 5 段/会修电脑），不是纯被救 / 3) 关系建立靠真实事件（小组合作+学校事故+共同的社团） / 4) 雨天接送换成更生活化的场景（食堂排队+图书馆隔桌+ 大扫除一组）",
            "voice_test": "如果男主第一次出场只是'高冷地看了她一眼' → 套路；如果是'盯着她写错的题目愣了 5 秒，最后只说\"...这一步逻辑反了\"' → 有真实细节",
            "activation_keywords": ["校园", "冷酷男主", "学渣女主", "套路", "反套路"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("近 10 年校园文头部模板分析")],
        tags=["anti_cliche", "校园", "重灾"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="灵异",
        slug="anti-cliche-occult-rookie-veteran",
        name="反套路：灵异'老司机带新人'死循环",
        narrative_summary="灵异文最稳的套路：经验老到的师傅+ 没见过世面的徒弟。前 100 万字+无数同行+无数大V 都用这个组合。每次新人看见鬼师傅都'淡定一笑：见怪不怪'。",
        content_json={
            "banned_patterns": "1) 老司机师傅+小白徒弟 / 2) 师傅每次都比鬼强 / 3) ' 淡定一笑' / 4) 徒弟尖叫师傅安慰 / 5) 第一次出马师傅压制+ 后期师傅死了徒弟继承 / 6) 师傅必有一个未解之谜（亲人被仇家所杀）",
            "alternative_strategies": "1) 让师徒势均力敌（互相需要彼此的擅长） / 2) 师傅自己也害怕（强者也有崩溃 moment） / 3) 用学术化的精确不是装 cool（'这个煞气是丁字格' 而不是'这鬼挺凶') / 4) 师徒先互相不信任（徒弟有自己的派系背景） / 5) 师傅一开始不是为了徒弟好，是利用",
            "concrete_test": "如果师傅每次出场=神态自若解决了 → 套路；如果师傅遇到自己一辈子第一次没见过的东西=慌乱+ 颤抖+ 错招 → 真实",
            "activation_keywords": ["灵异", "老司机", "新人", "师徒", "套路"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("天下霸唱+ 南派三叔+ 周浩晖灵异作品师徒模板分析")],
        tags=["anti_cliche", "灵异", "师徒", "重灾"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="穿书",
        slug="anti-cliche-transmigration-original-female",
        name="反套路：穿书反穿原女主任务套路",
        narrative_summary="穿书文最深的套路：穿成原书反派+ 系统提示'去抢回原女主未来' OR '去为原女主完成任务'。原男主必爱上现穿女主+ 原女主必恨现穿。死循环。",
        content_json={
            "banned_patterns": "1) 穿成反派/恶毒女配 / 2) 系统强制完成原女主任务 / 3) 原男主必爱穿越者 / 4) 原女主必嫉妒+陷害 / 5) ' 我才是真女主' 内心独白 / 6) 男主第一眼看出穿越者+觉得'她和书里不一样'",
            "alternative_strategies": "1) 让穿越者和原女主成为同盟（共同目标）而不是敌对 / 2) 原男主一开始就讨厌穿越者（穿越者必须靠人格魅力征服） / 3) 不写'我和书里不一样'，而是慢慢被发现 / 4) 系统是个无能/搞笑/会犯错的辅助，不是上帝视角 / 5) 反派不是为了恶而恶，有自己的苦衷",
            "concrete_test": "如果穿越后第一念是'我必须改变命运'+ 系统给任务 → 标准套路；如果第一念是'卧槽我能不能活下来不被毒杀算了',再慢慢被环境推着走 → 真实",
            "activation_keywords": ["穿书", "反派穿越", "原女主", "系统任务", "套路"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("酱子贝+ 莺莺草+ 吉祥夜+ 木兰竹 穿书 BG/BL 头部作品模板综合")],
        tags=["anti_cliche", "穿书", "反派", "重灾"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="娱乐圈",
        slug="anti-cliche-entertainment-tang-circle-resurgence",
        name="反套路：娱乐圈'退圈复出+黑红'套路",
        narrative_summary="娱乐圈文最爆的套路：女主曾是顶流→因塌房/家变退圈→几年后王者归来→所有黑她的人脸被打飞。爽是爽,套路也透。",
        content_json={
            "banned_patterns": "1) 退圈三五年 / 2) 复出参加选秀+所有评委震惊 / 3) 老黑粉/老对手出来挑事被打脸 / 4) 旧人现身道歉 / 5) 男主曾是粉丝默默支持 / 6) 复出第一支歌/电影直接拿奖封后",
            "alternative_strategies": "1) 让退圈不是清白被冤而是真实犯错（自己也有不光彩面） / 2) 复出第一年依然挫败（不是直接逆袭） / 3) 旧人没出来道歉,继续黑她,她要靠新作品打 / 4) 男主不是粉丝,而是行业内冷眼旁观的导演/编剧 / 5) 复出后的争议持续整本书,不在 ch10 平息",
            "concrete_test": "如果复出 ch1=被认出+ ch5= 拿奖 → 套路；如果复出 ch1=被认出但被嘲讽+ ch15= 试镜失败+ ch30=终于第一次拿到值得的角色 → 有真实",
            "activation_keywords": ["娱乐圈", "退圈复出", "塌房", "黑红", "套路"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("墨宝非宝+ 酥油饼+ 木苏里+ 棠梨煎雪 娱乐圈头部模板综合")],
        tags=["anti_cliche", "娱乐圈", "复出", "重灾"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="末日",
        slug="anti-cliche-doomsday-week-one",
        name="反套路：末日第一周固定套路",
        narrative_summary="末日文必有的第一周模板：丧尸出现→主角隔空有亲属/校友变丧尸→ 主角觉醒异能/抢到金手指→ 收编小队→ 找到第一个庇护所。每本都这么写,雷同度极高。",
        content_json={
            "banned_patterns": "1) 第一只丧尸是熟人/邻居/上司 / 2) 第三天必觉醒异能 / 3) 第一周必收编 4-6 人小队 / 4) 第七天必找到避难所 / 5) 主角必有'看见过末日预言' 的优势 / 6) 第一只 boss 必出在第十天",
            "alternative_strategies": "1) 让觉醒异能延迟到第 30 天甚至更晚（前期纯肉搏） / 2) 收编小队靠多次失败+ 死人才能成 / 3) 第一周可以纯逃亡+ 找不到庇护所 / 4) 主角异能不是'万能型'而是有强烈限制（如治疗系=别人有效自己无效） / 5) 末日 D+1 主角不是觉醒,而是普通人怕到尿裤子的诚实描写",
            "concrete_test": "如果 ch1=觉醒+ ch3=四人小队+ ch5=避难所 → 套路；如果 ch1=躲起来+ ch5=独自一人吃罐头+ ch10=被一群幸存者拣到+ ch20=身边的人都死光剩自己 → 有真实",
            "activation_keywords": ["末日", "第一周", "丧尸", "异能觉醒", "套路"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("方想+ 迷路的龙+ 卓凡+ 阿菩 末日头部作品模板综合")],
        tags=["anti_cliche", "末日", "第一周", "重灾"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="赛博朋克",
        slug="anti-cliche-cyberpunk-cool-loner",
        name="反套路：赛博朋克'酷盖独狼黑客'套路",
        narrative_summary="赛博朋克文里 90% 主角=酷酷的独狼黑客+ 不爱说话+ 雇佣兵接活+ 与社会保持距离。Neuromancer Case 模板被滥用。",
        content_json={
            "banned_patterns": "1) 酷盖独狼 / 2) '我只接钱给得够多的活'/ 3) 一身植入物 / 4) 黑色长风衣+太阳眼镜+银色机械义眼 / 5) 与世界保持距离的'我不在乎' / 6) 默默保护一个无辜女人/小孩",
            "alternative_strategies": "1) 让黑客有家庭（妈妈/姐姐还活着,经常打电话催回家） / 2) 黑客有非法律意义上的朋友（喝酒/打麻将/拼车） / 3) 接活时手会颤抖（不是冷面） / 4) 不穿黑色而是花衬衫/科技感运动装 / 5) 真的有恐惧+焦虑+ 心理咨询史",
            "concrete_test": "如果主角第一次出场=酒吧+ 长风衣+ 接单 → 套路；如果主角第一次出场=妈妈来电+ 一边吃外卖一边写代码+ 嫌外面太冷 → 有真实",
            "activation_keywords": ["赛博朋克", "黑客", "独狼", "Neuromancer", "套路"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Neuromancer"), llm_note("Cyberpunk 2077 V/Silverhand 模板分析")],
        tags=["anti_cliche", "赛博朋克", "黑客", "重灾"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="历史",
        slug="anti-cliche-history-modern-genius-loop",
        name="反套路：历史穿越'现代天才碾压古人'套路",
        narrative_summary="历史穿越文 90% 套路：现代理工男/医生/警察穿越古代→用现代知识(火药/化学/医学)碾压古人→帝王重用→当宰相/将军/驸马。古人变 NPC。",
        content_json={
            "banned_patterns": "1) 现代理工知识吊打古代 / 2) '我读过历史所以知道接下来会发生什么' / 3) 帝王第一眼觉得他是天才 / 4) 古代名臣排队投靠 / 5) 制造火枪/玻璃/水泥碾压战场 / 6) 三妻四妾后宫皆贤臣女",
            "alternative_strategies": "1) 让现代知识在古代遇到无法实施的瓶颈（缺原料+ 缺技术+ 缺人手） / 2) 帝王怀疑+利用+丢弃 / 3) 古代人物有自己的智慧（穿越者会被当地老学究教训） / 4) 历史读得不熟（背了高考考点其他全空） / 5) 三妻四妾换成单一伴侣+ 真实政治婚姻的痛苦",
            "concrete_test": "如果穿越后 ch3=皇帝召见+ ch10=火药制造成功+ ch20=封侯 → 套路；如果 ch3=被村民疑神疑鬼+ ch10=想做肥皂结果中毒+ ch20=才好不容易混到县衙小吏 → 有真实",
            "activation_keywords": ["历史", "穿越", "现代天才", "碾压古人", "套路"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("月关+ 阿越+ 蛤蟆夫人+ 顾雪柔 历史穿越头部模板综合")],
        tags=["anti_cliche", "历史", "穿越", "重灾"],
    ),
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="无限流",
        slug="anti-cliche-infinite-flow-cool-team",
        name="反套路：无限流'王牌小队万年不死'",
        narrative_summary="无限流套路：主角组建王牌小队→每副本都团灭其他队伍但小队全员存活→ 慢慢揭秘主神身份→ 最后挑战主神成功。每个副本=主角带飞队友。",
        content_json={
            "banned_patterns": "1) 主角小队万年不死 / 2) 副本流程=主角发现规则破局 / 3) 每副本都是 BOSS 级别 / 4) 队友各有金手指但都听主角的 / 5) 主神是隐藏反派 / 6) 最后主角变主神",
            "alternative_strategies": "1) 主角小队定期换血（队友会死,新人加入,旧情未了） / 2) 副本可以无解（活到最后但失败,扣分） / 3) 副本难度参差不齐（不是每个都是 BOSS 级） / 4) 队友有自己的目标可能背叛主角 / 5) 主神是无情系统而不是反派 / 6) 主角最后选择放弃成主神,过普通人生活",
            "concrete_test": "如果第 5 副本=主角破规则+ 团队全活下来 → 套路；如果第 5 副本= 主角受重伤+ 一队友死了+ 主角内心崩溃+ 第 6 副本拒绝出战 → 有真实",
            "activation_keywords": ["无限流", "王牌小队", "主神", "万年不死", "套路"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("zhttty《无限恐怖》衍生作品模板综合")],
        tags=["anti_cliche", "无限流", "小队", "重灾"],
    ),

    # ---------- character_archetypes (4) ----------
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-tragic-villain-suffering",
        name="原型：苦衷反派 / Sympathetic Villain",
        narrative_summary="不是为了恶而恶,而是因为创伤+ 苦难+ 错爱被逼成反派。读者既恨又怜悯。常见底层=被压迫者反抗变成压迫者。",
        content_json={
            "core_motivation": "不是统治世界,是为了某个具体的人/事 — 一个死去的女儿、一个被毁的故乡、一个不公的判决",
            "moral_complexity": "他做的事客观=恶,但每个选择都有逻辑。当他自杀/受罚时读者会为他流泪",
            "key_traits": "1) 创伤事件清晰可考(不是'天生坏') / 2) 从前是好人(有过纯真) / 3) 仍然爱具体的人(配偶+孩子+老朋友) / 4) 最终面对被自己抛弃的'前自己'",
            "famous_examples": "X-Men《Magneto》(纳粹幸存者)、《V for Vendetta》V、《Star Wars》Anakin/Darth Vader、电视剧《Breaking Bad》Walter White、灭霸 Thanos(为'宇宙平衡')",
            "dramatic_arcs": "1) 揭秘苦衷弧(慢慢透露过去) / 2) 救赎弧(快死时悔悟) / 3) 镜像弧(主角面对'我会不会变成他?') / 4) 共谋弧(主角发现自己也有反派的种子)",
            "anti_cliche": "不要让苦衷=借口而完全洗白；让他做的事确实可耻,只是可理解",
            "activation_keywords": ["反派", "苦衷", "Sympathetic Villain", "Magneto", "Anakin"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Sympathetic_villain"), wiki("Magneto"), wiki("Darth_Vader")],
        tags=["archetypes", "反派", "复杂"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-zealot-idealist-villain",
        name="原型：理想主义魔王 / Idealist Zealot",
        narrative_summary="为了'更高善'(救人类/救文明/救宇宙)而行恶。逻辑严密,牺牲少数救多数。读者会被说服一半。最危险的反派,因为可能是对的。",
        content_json={
            "core_motivation": "为了一个看起来正确的高大目标(节省宇宙资源/消灭种族冲突/拯救地球生态)而牺牲少数",
            "moral_complexity": "他的论点+证据是真的。如果你也用功利主义你可能会同意他",
            "key_traits": "1) 高智商+长期规划 / 2) 自我牺牲(他不享乐,只为目标) / 3) 严格的道德守则(不说谎+不背叛同志) / 4) 看不起'被他要救的'人",
            "famous_examples": "灭霸 Thanos(平衡论)、《Watchmen》Ozymandias(用大屠杀换世界和平)、《Death Note》Light Yagami(用死亡笔记建立无犯罪世界)、《敢能》尼采式超人",
            "dramatic_arcs": "1) 主角先认同他的理念然后发现代价 / 2) 主角用同样手段反击发现自己变成他 / 3) 揭穿其'理想'背后的隐藏私心 / 4) 让他活到结局看自己理想破灭",
            "anti_cliche": "不要让他临死前突然认为自己错了；让他到死都坚信自己对,主角也无法证明他错",
            "activation_keywords": ["理想主义", "Zealot", "Thanos", "Ozymandias", "Light Yagami"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("Thanos"), wiki("Ozymandias_(Watchmen)"), wiki("Light_Yagami")],
        tags=["archetypes", "反派", "理想主义"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-mirror-anti-self",
        name="原型：镜像反派 / Anti-Mirror",
        narrative_summary="和主角是同一种人但走相反方向 — 同样的天赋+同样的童年+同样的痛苦,但因关键时刻选择不同变成对立。最深的反派类型,因为他=主角的另一种可能。",
        content_json={
            "core_motivation": "和主角共享起点+目标,但价值观分叉。常见='我们曾是兄弟+我们都是为了改变命运,但你坚持原则我选择捷径'",
            "moral_complexity": "他不是'坏人'是'另一个我'。打败他=接受'我也可能变成那样'",
            "key_traits": "1) 同质感(同样口才+智力+追求) / 2) 共同经历(童年+师门+战友) / 3) 价值观分叉点(关键的一次选择) / 4) 互相理解(私下能聊得来)",
            "famous_examples": "Voldemort & Harry(都是混血孤儿+蛇语者+渴望永生)、Magneto & Xavier(都是 mutant+ 都想保护族裔但方法相反)、Light & L(都是天才+对正义的不同定义)、《盗梦空间》Cobb 与梦中自我",
            "dramatic_arcs": "1) 起点相同(童年闪回相互呼应) / 2) 关键分叉(展示选择那刻) / 3) 长期对峙(每次交手都更确认彼此理解) / 4) 终战时'我们本可以是朋友'",
            "anti_cliche": "不要让镜像反派死前彻底悔悟；他到死还在坚持自己的选择,主角是含泪杀他",
            "activation_keywords": ["镜像反派", "Anti-Mirror", "Voldemort", "Magneto", "镜像"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("Lord_Voldemort"), wiki("Magneto"), wiki("L_(Death_Note)")],
        tags=["archetypes", "反派", "镜像"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="archetype-charming-aristocrat-villain",
        name="原型：优雅反派 / Elegant Aristocrat",
        narrative_summary="贵族出身+高品味+ 优雅举止+ 有教养+ 有品德感却做最残忍的事。表面=完美绅士/淑女,内里=冷酷精算。一边喝红酒一边下令屠村。",
        content_json={
            "core_motivation": "维护自己阶级的优越感+秩序观。'下等人'就该被上等人统治,即使方法残酷也是为了'整体的优雅'",
            "moral_complexity": "他真的尊重艺术+音乐+礼仪。会救一只猫但下令杀一千人。这种割裂让读者发寒",
            "key_traits": "1) 极致教养(不抬高声音+ 不慌乱) / 2) 真心欣赏艺术 / 3) 有自己的'美学'选择杀谁怎么杀 / 4) 对仆人/家人极度温柔",
            "famous_examples": "《沉默的羔羊》Hannibal Lecter、《教父》Don Vito Corleone、《琅琊榜》谢玉、《纸牌屋》Frank Underwood",
            "dramatic_arcs": "1) 第一次见=被他的魅力征服(主角差点信任他) / 2) 慢慢发现端倪(他对一件小事过度反应) / 3) 揭露他的恶 / 4) 终战时他依然优雅,主角靠粗暴击败他",
            "anti_cliche": "不要让他在揭露后变成歇斯底里疯子；保持他的优雅到死,这才更恐怖",
            "activation_keywords": ["优雅反派", "Hannibal", "Aristocrat", "Charming", "贵族"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("Hannibal_Lecter"), wiki("Don_Vito_Corleone"), wiki("Frank_Underwood")],
        tags=["archetypes", "反派", "优雅"],
    ),
]


async def main() -> None:
    print(f"Seeding {len(ENTRIES)} entries...\n")
    by_genre: dict[str, int] = {}
    by_dim: dict[str, int] = {}
    errors = 0
    async with session_scope() as session:
        for e in ENTRIES:
            try:
                await insert_entry(session, e, compute_embedding=True)
                by_genre[e.genre or "(通用)"] = by_genre.get(e.genre or "(通用)", 0) + 1
                by_dim[e.dimension] = by_dim.get(e.dimension, 0) + 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                print(f"  ✗ {e.slug}: {exc}")
    print(f"\nBy genre:     {by_genre}")
    print(f"By dimension: {by_dim}")
    print(f"\n✓ {len(ENTRIES) - errors} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    asyncio.run(main())
