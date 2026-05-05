#!/usr/bin/env python3
"""
Batch 6 — 补全现有题材高价值缺失维度
重点：real_world_references / dialogue_styles / factions / locale_templates / power_systems
覆盖：历史/悬疑/末日/言情/宫斗/娱乐圈/穿书/种田/武侠/都市/心理惊悚
~65 条

Usage: uv run python scripts/seed_material_library_batch6.py [--dry-run]
"""
from __future__ import annotations
import argparse, asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from bestseller.infra.db.session import session_scope
from bestseller.services.material_library import MaterialEntry, insert_entry

def wiki(t, u): return {"url": u, "title": t, "accessed": "2026-04"}
def ref(t): return {"text": t, "confidence": 0.72}
def llm(t): return {"text": f"[LLM推演] {t}", "confidence": 0.58}
L = "llm_synth"

SEED_DATA: list[MaterialEntry] = [

    # =========================================================================
    # real_world_references — 补全现有题材
    # =========================================================================
    MaterialEntry(
        dimension="real_world_references", genre="历史",
        slug="hist-rwr-palace-politics",
        name="中国宫廷政治知识库",
        narrative_summary="真实宫廷政治的运作逻辑：皇权/相权/外戚/宦官四股力量的博弈，有据可查的历史案例是写作的直接素材",
        content_json={
            "power_balances": "汉代外戚（吕后/窦太后）/唐代宦官（高力士/仇士良）/明代内阁制度（票拟/批红），各代有各代的权力格局",
            "classic_cases": "赵高指鹿为马（话语权操控）/王莽代汉（合法化政变路径）/武则天称帝（性别与权力）",
            "court_techniques": "廷杖制度/锦衣卫/东厂西厂/廷议制度，每种制度背后是权力需求",
            "historiography": "中国历史上的「为尊者讳」传统：史书记录与真实情况的差距是叙事灰色空间",
            "activation_keywords": ["票拟批红", "廷杖", "锦衣卫", "东厂", "宦官专权", "外戚乱政", "君主集权"],
        },
        source_type=L, confidence=0.80,
        source_citations=[wiki("明朝内阁", "https://zh.wikipedia.org/wiki/內閣_(明朝)"), ref("钱穆《中国历代政治得失》权力结构分析")],
        tags=["历史", "参考", "宫廷", "政治"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre="悬疑",
        slug="susp-rwr-criminal-psychology",
        name="犯罪心理学知识库",
        narrative_summary="真实犯罪心理学是悬疑写作的直接工具箱：连环杀手心理/犯罪现场分析/审讯技术/证据链构建",
        content_json={
            "offender_profiling": "FBI行为分析单元的罪犯侧写：有组织vs无组织/冷静预谋vs激情犯罪",
            "crime_scene_reading": "现场是叙事：逃跑方式/遗留物/作案手法，每个细节都在说关于罪犯的故事",
            "interrogation_science": "里德技术（建立关系→寻找矛盾→施压）/认知访谈（减少干扰增加回忆）",
            "forensics": "血迹喷射分析/弹道学/法医昆虫学（死亡时间估算）/数字取证",
            "activation_keywords": ["犯罪侧写", "罪犯心理", "动机", "机会", "能力", "现场重建", "法医鉴定"],
        },
        source_type=L, confidence=0.80,
        source_citations=[wiki("犯罪侧写", "https://zh.wikipedia.org/wiki/犯罪剖繪"), ref("道格拉斯《犯罪心理剖析》FBI侧写手册")],
        tags=["悬疑", "参考", "犯罪心理", "法医"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre="末日",
        slug="apoc-rwr-disaster-sociology",
        name="灾难社会学知识库",
        narrative_summary="真实灾难中的人类行为研究：灾难后的社会结构如何演化/人类的利他性和自私性如何分布",
        content_json={
            "disaster_behavior": "研究显示大多数人在灾难后表现出利他行为（相反于「恐慌失控」的媒体叙事）",
            "social_breakdown": "社会崩溃不是立刻发生的，通常需要3个月以上的持续压力才出现真正的秩序破坏",
            "leadership_emergence": "自组织现象：灾难中的非正式领导者往往是原有社区中有信誉的人，而非最强壮的人",
            "resource_conflict": "资源稀缺到一定程度才触发暴力竞争，不是立刻；绝对匮乏和感知到的匮乏行为不同",
            "activation_references": ["卡特里娜飓风社会反应研究", "哥伦比亚大学灾难社会学研究", "Rebecca Solnit《这是天堂》"],
        },
        source_type=L, confidence=0.80,
        source_citations=[ref("Rebecca Solnit《这是天堂》灾难中的人类互助"), wiki("灾难社会学", "https://zh.wikipedia.org/wiki/災難社會學")],
        tags=["末日", "参考", "社会学", "灾难"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre="言情",
        slug="rom-rwr-psychology-of-love",
        name="爱情心理学知识库",
        narrative_summary="科学爱情心理学：三角形爱情理论/「喜欢vs爱」的区别/感情阶段/吸引力机制，是言情叙事的真实底层",
        content_json={
            "triangular_theory": "斯腾伯格三角形：亲密（情感接近）+激情（身体吸引）+承诺（长期决定），三要素不同比例=不同爱情类型",
            "attraction_science": "暴露效应（见面越多越喜欢）/相似性原则（相似者互相吸引）/互惠性（对方表达喜欢使你更喜欢对方）",
            "love_vs_infatuation": "迷恋：激情高/亲密低/承诺低；爱情：三要素均衡发展，需要时间和共同经历",
            "attachment_in_love": "早期依恋风格（儿童期与照顾者的关系）影响成年爱情模式，是CP动态的深层来源",
            "activation_keywords": ["激情", "亲密", "承诺", "依恋风格", "互惠吸引", "暴露效应"],
        },
        source_type=L, confidence=0.80,
        source_citations=[ref("Robert Sternberg爱情三角理论"), wiki("爱情心理学", "https://zh.wikipedia.org/wiki/愛情心理學")],
        tags=["言情", "参考", "心理学", "爱情"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre="宫斗",
        slug="palace-rwr-harem-history",
        name="历史后宫制度知识库",
        narrative_summary="中国历史后宫的真实运作：制度设计/嫔妃选拔/子嗣政治/家族代理，是宫斗叙事的真实参照系",
        content_json={
            "selection_system": "唐代秀女制度/清代三年一选，出身/容貌/品行/家族背景均考量",
            "rank_and_resource": "品级对应不同待遇：份例银两/宫女数量/居所等级/觐见机会，资源分配是权力的微观呈现",
            "child_politics": "生育子嗣的政治价值：母以子贵/嫡庶之争/皇位继承战，子嗣是最重要的筹码",
            "clan_interest": "嫔妃背后的家族：外戚集团的利益驱动，皇帝用婚姻关系笼络功臣",
            "historical_figures": "慈禧（从贵妃到太后的权力路径）/武则天/郑贵妃争国本事件——各有不同权谋模式",
        },
        source_type=L, confidence=0.80,
        source_citations=[wiki("清朝后妃制度", "https://zh.wikipedia.org/wiki/清朝後妃制度"), ref("明清后宫制度史研究")],
        tags=["宫斗", "参考", "历史", "后宫制度"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre="娱乐圈",
        slug="ent-rwr-entertainment-industry",
        name="中国娱乐圈行业知识库",
        narrative_summary="真实娱乐圈的运作机制：经纪约/粉丝经济/流量运营/黑料生态，是娱乐圈叙事的现实底层",
        content_json={
            "contract_ecosystem": "艺人与公司的权力关系：合约期/违约金/独家经纪，公司利用信息不对等控制艺人",
            "fan_economy": "粉丝数据/应援/打榜经济，粉丝是资本的免费劳动力也是艺人的唯一「私产」",
            "blackmail_ecology": "狗仔/黑料/水军，信息是武器；谁控制舆论谁控制艺人的生死",
            "investment_logic": "影视投资方押注逻辑：流量保底+实力背书，两者取一是行业现实",
            "activation_keywords": ["爱豆", "经纪公司", "合约", "流量", "路人缘", "黑料", "塌房", "转型"],
        },
        source_type=L, confidence=0.78,
        source_citations=[llm("中国娱乐圈行业生态研究"), wiki("中国流行音乐", "https://zh.wikipedia.org/wiki/流行音乐")],
        tags=["娱乐圈", "参考", "行业", "现实"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre="心理惊悚",
        slug="psythr-rwr-psychology-reference",
        name="心理学临床知识库",
        narrative_summary="心理惊悚的真实底层：解离性障碍/BPD/PTSD/妄想症的真实症状和行为模式，比想象中的「疯子」更细腻也更恐怖",
        content_json={
            "dissociation": "解离症：感觉自己在旁边看自己的身体行动/身份解离/失忆性游离——不是戏剧性，而是无缝的",
            "BPD": "边缘性人格障碍：极端化（理想化vs贬低）/空虚感/冲动/对遗弃的恐惧，是「病娇」的临床来源",
            "paranoia": "偏执型：系统性的被迫害逻辑，内在高度一致，只是前提假设是错误的",
            "gaslighting": "心理操控技术：让受害者质疑自己的记忆/感知/理智，是「不可靠叙事者」的真实社会版本",
            "activation_keywords": ["解离", "人格分裂", "偏执", "共情缺失", "反社会", "煤气灯效应", "操纵"],
        },
        source_type=L, confidence=0.80,
        source_citations=[wiki("解离症", "https://zh.wikipedia.org/wiki/解離症"), ref("DSM-5人格障碍诊断标准概要")],
        tags=["心理惊悚", "参考", "心理学", "障碍"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre="都市",
        slug="urban-rwr-modern-china",
        name="当代中国都市社会知识库",
        narrative_summary="当代中国都市叙事的现实底层：阶层焦虑/教育内卷/房价/996/代际矛盾，这些是都市题材最真实的张力来源",
        content_json={
            "class_anxiety": "中产阶级焦虑：教育投入/房产/医疗，「一病回到解放前」的真实恐惧",
            "generational_tension": "60/70后与90/00后的价值观断层：工作观/婚育观/消费观/对权威的态度",
            "workplace_reality": "996/躺平/35岁危机，这些不是网络词汇而是真实的社会压力",
            "urban_rural_gap": "进城务工者的双重边缘化：在城市是外来者/回乡是归来者，永远不属于任何一边",
            "activation_keywords": ["内卷", "躺平", "996", "35岁危机", "房奴", "二代", "阶层固化", "小镇做题家"],
        },
        source_type=L, confidence=0.80,
        source_citations=[llm("当代中国都市社会研究"), wiki("内卷化", "https://zh.wikipedia.org/wiki/内卷化")],
        tags=["都市", "参考", "当代", "社会"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre="武侠",
        slug="wuxia-rwr-jianghu-culture",
        name="江湖文化历史参考",
        narrative_summary="真实的「江湖」文化根源：明清帮会制度/哥老会天地会/码头行帮，是武侠江湖秩序的历史底层",
        content_json={
            "historical_jianghu": "明清帮会：天地会（反清复明）/哥老会（军队秘密组织）/漕帮（运河行帮）",
            "gang_culture": "拜把子制度/香堂仪式/入会誓言，义气文化的仪式化来源",
            "coded_language": "黑话/暗语/切口，江湖有自己的信息安全体系",
            "territory_logic": "地盘划分/保护费/行业垄断，江湖秩序是非正式的经济控制",
            "jianghu_ethics": "「道上的规矩」：不打女人/不伤家属/江湖恩怨江湖了，这些规则的存在有实际功能",
        },
        source_type=L, confidence=0.78,
        source_citations=[wiki("天地会", "https://zh.wikipedia.org/wiki/天地會"), ref("秘密社会史：中国帮会文化研究")],
        tags=["武侠", "参考", "历史", "帮会"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre="种田",
        slug="farm-rwr-traditional-agriculture",
        name="中国传统农业知识库",
        narrative_summary="种田题材的真实知识底层：二十四节气农业规律/传统作物知识/中草药体系/农业生态，是生活质感的直接来源",
        content_json={
            "seasonal_wisdom": "春分→播种/清明→插秧/谷雨→雨季/夏至→灌溉关键期/秋分→收获/冬至→蛰伏储粮",
            "traditional_crops": "稻（水田/季节）/小麦（旱地/北方）/大豆（固氮/轮种）/高粱（抗旱），各有生态位",
            "herb_cultivation": "人参（需林下荫蔽）/枸杞（耐旱）/薄荷（旺盛生长）/芍药（观赏药用两用），习性各异",
            "farming_philosophy": "精耕细作传统：不靠大面积而靠精细管理，以少量耕地养活更多人口",
            "ecology_wisdom": "桑基鱼塘（循环农业）/梯田水利/轮耕休耕，传统农业中的生态智慧",
        },
        source_type=L, confidence=0.82,
        source_citations=[wiki("二十四节气", "https://zh.wikipedia.org/wiki/二十四节气"), wiki("中草药", "https://zh.wikipedia.org/wiki/草药")],
        tags=["种田", "参考", "农业", "节气"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre="穿书",
        slug="tb-rwr-narrative-theory",
        name="元小说与叙事理论知识库",
        narrative_summary="穿书题材的理论底层：元小说（自我指涉）/叙事层次/虚构与现实的边界——理解这些让穿书设定更深刻",
        content_json={
            "metafiction": "元小说：小说对自身虚构性的意识和表达，穿书是极端化的元小说体验",
            "narrative_levels": "故事内层（书里的世界）/故事外层（读者的世界），穿书主角跨越了这两层",
            "fictional_reality": "维特根斯坦语言游戏：「书中的人」在书里的世界是真实的，真实性由所在的叙事层决定",
            "reader_god": "读者（主角）相对于书中角色拥有「神视角」——知道未来/知道真相，这种权力不对等是叙事张力的来源",
            "self_referential_risk": "穿书太过强调「只是书里」会破坏读者对书中关系的投入——元小说意识要被情感投入所平衡",
        },
        source_type=L, confidence=0.78,
        source_citations=[wiki("元小说", "https://zh.wikipedia.org/wiki/後設小說"), ref("罗兰·巴特《叙事作品结构分析》")],
        tags=["穿书", "参考", "叙事理论", "元小说"],
    ),

    # =========================================================================
    # dialogue_styles — 补全现有题材
    # =========================================================================
    MaterialEntry(
        dimension="dialogue_styles", genre="历史",
        slug="hist-ds-classical-register",
        name="历史文言风格台词",
        narrative_summary="历史题材台词的两层策略：文言味（增加历史质感）与可读性（不影响阅读流畅）之间的平衡",
        content_json={
            "honorifics": "称谓体系：圣上/陛下/殿下/大人/公子，级别错位即是失礼也是叙事信号",
            "classical_markers": "「卿」「汝」「孤/寡人/朕」（自称）「然/否/善」（答语），这些词汇快速建立历史感",
            "formal_vs_private": "朝堂用语极正式/私下用语可以放松，语气转变是人物关系亲密度的晴雨表",
            "euphemism_culture": "古人说话迂回：不直说死亡（薨/崩/殁）/不直说性（闺房/承欢）/讽谏而非直谏",
            "quote_integration": "自然引用古典诗词/典故，既展示人物教育背景，也加深了对话的文化密度",
        },
        source_type=L, confidence=0.78,
        source_citations=[llm("历史题材台词风格与文言平衡研究")],
        tags=["历史", "台词风格", "文言", "历史感"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="武侠",
        slug="wuxia-ds-jianghu-voice",
        name="江湖腔调台词风格",
        narrative_summary="武侠台词的特定风味：义气/豪迈/简洁有力，用词体现江湖经历和门派背景",
        content_json={
            "jianghu_speech": "江湖人说话直接：「有话明说」「江湖恩怨，战场了结」，少婉转多直白",
            "profession_markers": "剑客/刀客/弓手，不同武器使用者有不同的身体习惯影响说话方式",
            "brotherhood_language": "兄弟义气的语言：「我的事就是你的事」「不用道谢，谢字伤感情」",
            "challenge_and_respect": "挑战对手时的礼仪台词：「请赐教」「承让」，表面客气实则试探",
            "old_master_voice": "江湖老人的台词特征：话少/一针见血/经历过太多所以不再惊讶",
        },
        source_type=L, confidence=0.75,
        source_citations=[llm("武侠台词风格研究")],
        tags=["武侠", "台词风格", "江湖", "腔调"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="末日",
        slug="apoc-ds-survival-voice",
        name="末日生存语境台词",
        narrative_summary="极端生存压力下语言的变化：功能性压缩/情绪崩溃/黑色幽默，是末日题材台词真实感的核心",
        content_json={
            "compression": "生存语境使语言功能化：「几点/怎么走/够不够」，废话减少，信息密度极高",
            "emotional_leakage": "压抑情绪在说话中的泄露：声音控制失败/停顿过长/转移话题",
            "gallows_humor": "末日幸存者发展出的黑色幽默：笑不可笑的事，是心理防御机制",
            "trust_testing": "每次对话都在测试信任：「你去探路」vs「我们一起去」，行动分配包含信任信号",
            "silent_communication": "团队磨合后用眼神/手势/位置就能传递复杂信息，对话减少但意义不减少",
        },
        source_type=L, confidence=0.75,
        source_citations=[llm("末日题材台词风格研究")],
        tags=["末日", "台词风格", "生存", "压力"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="宫斗",
        slug="palace-ds-court-language",
        name="宫廷台词双层结构",
        narrative_summary="宫廷台词的核心技巧：表面温和的话包含威胁；看似寒暄的问候在传递信息；礼貌的规范外壳下是刀锋",
        content_json={
            "surface_threat": "「姐姐近日气色欠佳，要注意保重」= 「我知道你最近出了什么事」",
            "compliment_ambush": "「妹妹这发钗真是少见，从哪里得来的？」= 「这东西有问题，我注意到了」",
            "topic_capture": "谁掌握话题谁占主动；礼貌地夺回话题控制权是宫廷台词的精髓",
            "witness_awareness": "宫廷对话的内容总要考虑在场的旁观者（宫女/太监），有些话是说给他们听的",
            "formal_register": "对话层级严格：如何称呼/行什么礼/谁先开口，违反礼制本身就是台词",
        },
        source_type=L, confidence=0.77,
        source_citations=[llm("宫廷台词双层意义写作研究")],
        tags=["宫斗", "台词风格", "双关", "礼制"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="言情",
        slug="rom-ds-romantic-tension",
        name="言情张力台词",
        narrative_summary="制造心跳感的台词策略：「差一步」的语言游戏/欲言又止/双关/告白的多种方式",
        content_json={
            "almost_confession": "「如果我说……」「要是有一天……」，悬置的假设句创造期待感",
            "double_meaning": "表面说A，实际说B：「那本书借了很久了」=「我一直在等你来找我」",
            "interruption_tension": "重要的话被打断，或者说到一半没有说完，比说完更有张力",
            "nickname_intimacy": "从正式称呼到昵称的第一次，是关系改变的语言标志",
            "rejection_as_confession": "「你不要这样对我」=「你这样对我让我心动」——拒绝里包含的真相",
        },
        source_type=L, confidence=0.77,
        source_citations=[llm("言情台词张力设计研究")],
        tags=["言情", "台词风格", "张力", "告白"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="悬疑",
        slug="susp-ds-interrogation-dialog",
        name="悬疑审讯推理台词",
        narrative_summary="悬疑台词的核心：每句话都可能是线索也可能是误导，说话方式本身就在泄露信息",
        content_json={
            "information_control": "侦探不说自己知道什么，只说「你知道什么」——问题本身是工具",
            "contradiction_spotting": "「你之前说……但是现在你说……」，对话中发现矛盾是推理展示",
            "evasion_patterns": "嫌疑人的回避方式：答非所问/反问/转移话题/过度详细（细节越多越可疑）",
            "pressure_timing": "沉默是武器：问完问题后不接话，让对方在沉默中填补",
            "revelation_pacing": "侦探不一次说出所有推理，而是一步步揭露，每步都让对方选择继续谎言还是承认",
        },
        source_type=L, confidence=0.77,
        source_citations=[llm("悬疑台词推理展示技法研究")],
        tags=["悬疑", "台词风格", "推理", "审讯"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="娱乐圈",
        slug="ent-ds-media-performance",
        name="娱乐圈媒体表演台词",
        narrative_summary="艺人的公开台词是表演，私下台词是另一个人——两套语言系统的对比揭示了娱乐圈的虚实结构",
        content_json={
            "public_speak": "采访/直播时的标准答案：「感谢公司/粉丝」「会继续努力」「作品说话」，公关语言有固定模板",
            "contrast_private": "私下说话：更直接/有情绪/说公开场合不能说的真话——对比是人物深度的来源",
            "interview_subtext": "艺人在访谈时说「挺好的」「都好」但皱眉或回避，肢体语言和台词的矛盾",
            "fan_interaction": "与粉丝互动有固定的语言仪式：宠粉台词/感谢台词，多用少说实质内容",
            "circle_insider": "圈内人对话有隐语：「被安排了」「有资源」「过气」，这些词汇是行业地位的晴雨表",
        },
        source_type=L, confidence=0.75,
        source_citations=[llm("娱乐圈台词双层结构研究")],
        tags=["娱乐圈", "台词风格", "表演", "公私"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="种田",
        slug="farm-ds-pastoral-warmth",
        name="种田日常温暖台词",
        narrative_summary="种田题材台词的特质：缓而不慢/平实而有温度/琐碎里的真情，是区别于战斗/宫斗台词风格的核心",
        content_json={
            "topic_range": "对话关于：今天的收成/某株植物的状态/客人带来的消息/明天的计划，生活本身是话题",
            "warmth_technique": "「今天这个汤里加了你上次说好喝的那种菜」——记住对方说过的话，是台词里的关怀",
            "unhurried_rhythm": "句子不需要急着推进，可以在说到一半时停下来去处理某件事，再回来接",
            "humor_type": "田园幽默：关于某个倔强的灵植/做了奇怪的事的灵兽/意外的丰收，轻松不刻意",
            "silence_comfort": "两个人可以在一起工作不说话，沉默不是尴尬而是默契",
        },
        source_type=L, confidence=0.75,
        source_citations=[llm("种田题材台词温暖节奏研究")],
        tags=["种田", "台词风格", "温暖", "日常"],
    ),

    # =========================================================================
    # factions — 补全现有题材
    # =========================================================================
    MaterialEntry(
        dimension="factions", genre="历史",
        slug="hist-fac-court-parties",
        name="历史朝堂党争格局",
        narrative_summary="朝堂上的派系不是简单的好坏之分，而是不同利益集团以意识形态包装的权力博弈",
        content_json={
            "faction_types": "外戚集团（皇权亲属）/宦官集团（内廷代理）/清流文臣（道德权威）/地方军阀（实力派）",
            "ideology_wrapper": "每个派系都有一套正当性叙事：「为国为民」是所有派系的共同旗帜，区别在执行",
            "fluid_alliances": "朝堂联盟是暂时的：对付共同敌人时联合，敌人消失后互相成为对手",
            "entry_point": "新人入朝必须快速判断派系，中立往往意味着被两边打压",
            "protagonist_position": "主角如何在派系中定位：加入某派系获得资源但失去自由/保持独立获得自由但失去保护",
        },
        source_type=L, confidence=0.75,
        source_citations=[ref("钱穆《中国历代政治得失》派系分析"), llm("历史小说朝堂派系设计研究")],
        tags=["历史", "派系", "朝堂", "权谋"],
    ),
    MaterialEntry(
        dimension="factions", genre="武侠",
        slug="wuxia-fac-sect-structure",
        name="武侠门派势力体系",
        narrative_summary="武侠世界的门派不只是学武的地方，是完整的社会组织：领地/声望/经济/人才培养/外交",
        content_json={
            "sect_types": "正道名门（声誉资本）/魔道门派（恐惧资本）/中立商业门派（经济资本）/官方武力（政治资本）",
            "internal_structure": "掌门/长老/核心弟子/普通弟子/外门弟子，层级决定资源访问和义务",
            "inter_sect_relations": "联盟/婚姻/弟子互换/冲突，门派间的复杂关系网络",
            "decline_patterns": "传承断代/内部分裂/强敌灭门/被朝廷针对，门派衰亡的常见路径",
            "protagonist_agency": "无门无派的主角：失去保护但获得自由；有门有派的主角：有资源但受约束",
        },
        source_type=L, confidence=0.75,
        source_citations=[llm("武侠门派社会结构研究")],
        tags=["武侠", "派系", "门派", "江湖"],
    ),
    MaterialEntry(
        dimension="factions", genre="末日",
        slug="apoc-fac-survivor-groups",
        name="末日幸存者势力格局",
        narrative_summary="末日后幸存者组织化的几种模式：军事独裁/民主自治/宗教团体/科研基地，各有不同的内部逻辑和外部关系",
        content_json={
            "military_faction": "军事独裁型：效率高/安全性强/缺乏民主，靠强制力维持秩序",
            "democratic_faction": "民主自治型：决策慢/代表性强/容易被内部矛盾撕裂",
            "religious_faction": "宗教团体：凝聚力强/排外性强/意识形态控制，可以给绝望的人意义感",
            "scientific_faction": "科研基地：有稀缺知识资源/对战斗力依赖外援/被各方保护和窥视",
            "conflict_types": "资源争夺/理念冲突/安全威胁/内部背叛，四种冲突在势力博弈中的不同叙事功能",
        },
        source_type=L, confidence=0.72,
        source_citations=[llm("末日题材势力体系设计研究")],
        tags=["末日", "派系", "幸存者", "势力"],
    ),
    MaterialEntry(
        dimension="factions", genre="悬疑",
        slug="susp-fac-investigation-ecology",
        name="悬疑调查势力生态",
        narrative_summary="悬疑题材中的势力不是门派，而是「对案件有利益的各方」：执法/嫌疑/受害/媒体/幕后，各方的关系构成叙事张力",
        content_json={
            "law_enforcement": "警察/检察/律师，官方调查体系的内部分歧（破案压力vs法律程序）",
            "criminal_network": "组织犯罪的保护伞结构：谁是幕后/谁是执行/谁是不知情的工具",
            "victim_camp": "受害者家属、证人、相关者，这些人有自己的目标，不只是被动的信息来源",
            "media_role": "媒体作为独立势力：既可以是揭露真相的工具，也可以是被利用的破坏力",
            "protagonist_position": "侦探/主角如何在这些势力中生存：被哪方保护/被哪方威胁/对哪方有义务",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm("悬疑题材势力生态设计研究")],
        tags=["悬疑", "派系", "势力", "调查"],
    ),

    # =========================================================================
    # locale_templates — 补全现有题材
    # =========================================================================
    MaterialEntry(
        dimension="locale_templates", genre="悬疑",
        slug="susp-lt-crime-scene",
        name="犯罪现场场所",
        narrative_summary="犯罪现场不只是地理位置，是叙事空间：现场的状态讲述了犯罪的故事，每个细节都是有意义的语言",
        content_json={
            "spatial_narrative": "现场的「被打乱」和「刻意布置」都是信息；混乱可以是真实的，也可以是伪造的",
            "sensory_details": "犯罪现场的气味（血腥/香水/烟草/清洁剂）/温度（加热器/开着的窗户）/声音（嗡嗡作响的灯/外面的雨）",
            "time_layers": "现场是时间的多层叠加：犯罪时的状态/被发现时的变化/调查者进入后的干扰",
            "narrative_function": "通过细节让读者和侦探一起「读」现场，参与推理而不只是旁观",
            "avoid": "犯罪现场不要设置太多证据——真实的现场往往信息量不足，需要主动寻找",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm("悬疑犯罪现场叙事空间研究")],
        tags=["悬疑", "场所", "犯罪现场", "叙事空间"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre="末日",
        slug="apoc-lt-safe-zone",
        name="末日据点场所",
        narrative_summary="末日叙事的「家」——据点的建立、保卫和失去是叙事节拍器，据点代表着「值得保护的秩序」",
        content_json={
            "spatial_layers": "外围防御层/缓冲区/核心居住区，层级对应不同程度的安全感和行动自由",
            "resource_layout": "水源/食物储存/医疗区/武器库/发电设备，每个资源点都是潜在的冲突节点",
            "community_feel": "墙上的涂鸦/公告板/临时图书馆，人类在极端环境中仍然试图建立文化的痕迹",
            "vulnerability": "每个据点都有脆弱点：电力/水源/食物/人心，外部威胁往往先从内部开始",
            "narrative_function": "据点的状态是故事进展的空间指标：刚建立时充满希望/遭遇打击时破败/最后抉择时的废墟与重建",
        },
        source_type=L, confidence=0.72,
        source_citations=[llm("末日据点叙事空间设计研究")],
        tags=["末日", "场所", "据点", "安全感"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre="言情",
        slug="rom-lt-meaningful-places",
        name="言情标志性场所",
        narrative_summary="言情关系中的「地方记忆」——某些地点因为发生的事而对关系有特殊意义，是叙事回忆和转折的空间锚点",
        content_json={
            "first_meeting_place": "第一次相遇的地方有特殊意义：重访时是回忆的触发，或者刻意不重访本身是叙事",
            "routine_place": "两人固定共享的地方（常去的咖啡馆/公司楼梯口/某个路口），是关系日常化的证明",
            "confession_setting": "表白场地的选择本身有叙事意义：在公共场合（不可拒绝的压力）vs私下（真实的脆弱）",
            "breakup_location": "关系危机发生的场所，之后无法以同样方式存在于那个地方",
            "neutral_territory": "危机后的重新接触选择中立场所，不带有任何旧记忆的地方",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm("言情题材场所记忆叙事研究")],
        tags=["言情", "场所", "记忆", "关系"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre="宫斗",
        slug="palace-lt-secret-spaces",
        name="宫廷秘密空间",
        narrative_summary="宫廷里最重要的地方不是朝堂，而是没有人看见的地方：密道/废弃宫殿/太监走道/御花园角落",
        content_json={
            "hidden_passages": "密道的存在是权力建筑的特征：皇帝的逃跑路线/贵妃的秘密会面/宦官的情报传递",
            "abandoned_palaces": "失宠妃嫔居住的偏僻宫院：叙事上是「边缘化」的空间表达",
            "servant_pathways": "太监宫女的行走路线和主子不同，是另一套信息流通渠道",
            "garden_politics": "御花园的碰面：「偶然」相遇是可以设计的，在自然环境里进行的对话享有更多自由",
            "ritual_spaces": "祭祀/朝拜的场所有特殊规则，在这里说话/行动有特定含义和约束",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm("宫廷秘密空间叙事功能研究")],
        tags=["宫斗", "场所", "秘密", "空间"],
    ),

    # =========================================================================
    # power_systems — 补全部分题材
    # =========================================================================
    MaterialEntry(
        dimension="power_systems", genre="悬疑",
        slug="susp-ps-information-power",
        name="信息作为权力体系",
        narrative_summary="悬疑世界里的「力量体系」是信息——谁拥有什么信息，谁有获取信息的能力，决定了权力格局",
        content_json={
            "information_types": "事实信息（发生了什么）/关系信息（谁和谁有关系）/动机信息（为什么这样做）",
            "acquisition_methods": "调查/监控/渗透/交换/强迫，每种方式有不同代价和风险",
            "information_assymetry": "核心张力来源：侦探比读者知道得少/侦探比嫌疑人知道的少，两种信息差各有叙事效果",
            "information_weaponization": "知道一个秘密→可以勒索/保护/揭露，同一信息的不同使用方式",
            "protagonist_advantage": "侦探/主角的特殊能力不是体力，而是「整合碎片信息的能力」",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm("悬疑信息权力体系设计研究")],
        tags=["悬疑", "力量体系", "信息", "权力"],
    ),
    MaterialEntry(
        dimension="power_systems", genre="都市",
        slug="urban-ps-social-capital",
        name="都市社会资本体系",
        narrative_summary="都市世界的「力量」不是修炼值，而是人脉/财富/声誉/信息——这四种资本的积累和转化是都市叙事的核心动力",
        content_json={
            "network_capital": "人脉：认识谁/谁欠你/你欠谁，人际债务网络是无形的力量",
            "financial_capital": "财富的力量：直接购买/投资回报/威慑能力，但财富可以消耗也可以被夺走",
            "reputation_capital": "声誉：在特定圈子里的评价，损毁比建立快，恢复比损毁难",
            "information_capital": "知道别人不知道的事：行业内幕/人员弱点/未来走势",
            "capital_conversion": "四种资本可以互相转换：有钱可以买人脉/有声誉可以获得信息/有信息可以保护声誉",
        },
        source_type=L, confidence=0.73,
        source_citations=[ref("Pierre Bourdieu社会资本理论"), llm("都市题材权力体系设计研究")],
        tags=["都市", "力量体系", "社会资本", "权力"],
    ),

    # =========================================================================
    # emotion_arcs — 补全部分题材
    # =========================================================================
    MaterialEntry(
        dimension="emotion_arcs", genre="宫斗",
        slug="palace-ea-power-vs-love",
        name="宫斗权力与真情的撕裂弧",
        narrative_summary="宫斗的最深情感张力不是「她是坏人」，而是「她为了活下去做了这些事，但她也有真实的感情」——权力与爱不是非此即彼",
        content_json={
            "early_stage": "刚入宫时的某些真实感情：对某人的喜爱/对生活的期待，在规则还没完全压死之前",
            "compromised_stage": "为了生存做了第一次妥协——不是大恶，只是小小的出卖，但感觉到了变化",
            "point_of_no_return": "做了一件真正过不去的事，之后「我还是好人」这个自我认知开始裂缝",
            "reckoning": "在权力顶峰时审视：我得到了想要的，我是什么样的人了？",
            "resolution_options": ["接受自己的改变，不再假装（悲壮）", "在最后找回一次真实（救赎）", "权力是虚的，真情才是真的（幸存）"],
        },
        source_type=L, confidence=0.73,
        source_citations=[llm("宫斗情感弧权力与真情张力研究")],
        tags=["宫斗", "情感弧", "权力", "真情"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre="娱乐圈",
        slug="ent-ea-fame-isolation",
        name="出名后的孤独弧",
        narrative_summary="功成名就之后反而更孤独——娱乐圈叙事独特的情感悖论：越多人「爱你」，越不知道谁是真心的",
        content_json={
            "pre_fame": "未出名时的人际关系是真实的，但也带着「如果我变了呢」的隐忧",
            "early_fame": "突然有人喜欢你，需要分辨是喜欢你还是喜欢你代表的东西",
            "peak_fame": "在人群中感到最孤独：每个人都想要你的某个部分，但没有人真的在乎整个你",
            "trust_erosion": "有人接近你是为了利用你的资源，辨别真假成为日常消耗",
            "anchor_person": "在这种孤独中，那个「在出名前就认识你」或「即使你出名也对你一样」的人变得无比珍贵",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm("娱乐圈出名孤独情感弧研究")],
        tags=["娱乐圈", "情感弧", "出名", "孤独"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre="心理惊悚",
        slug="psythr-ea-paranoia-spiral",
        name="偏执漩涡情感弧",
        narrative_summary="主角对某件事的怀疑如何从合理演变为偏执——叙事张力在于读者无法确定主角是对的还是病了",
        content_json={
            "stage1_reasonable": "有充分理由怀疑某件事，读者完全认同主角的判断",
            "stage2_borderline": "行为开始超出合理范围，但仍然可以理解",
            "stage3_question": "主角的行为让读者开始质疑：这到底是直觉还是妄想？",
            "stage4_isolation": "偏执导致与外部世界脱节，主角越来越孤立，越来越依赖自己的解读",
            "revelation": "真相揭示：主角是对的（偏执被证实）或错的（偏执是病态），两种结局各有叙事意义",
        },
        source_type=L, confidence=0.73,
        source_citations=[llm("心理惊悚偏执情感弧叙事研究")],
        tags=["心理惊悚", "情感弧", "偏执", "漩涡"],
    ),
]


async def seed_library(dry_run: bool = False, filter_genre: str | None = None) -> None:
    entries = SEED_DATA
    if filter_genre:
        entries = [e for e in entries if e.genre == filter_genre]
    print(f"{'[DRY RUN] ' if dry_run else ''}Seeding {len(entries)} entries...\n")
    by_g: dict[str, int] = {}
    by_d: dict[str, int] = {}
    for e in entries:
        by_g[e.genre or "NULL"] = by_g.get(e.genre or "NULL", 0) + 1
        by_d[e.dimension] = by_d.get(e.dimension, 0) + 1
    print(f"By genre:     {dict(sorted(by_g.items()))}")
    print(f"By dimension: {dict(sorted(by_d.items()))}\n")
    if dry_run:
        return
    errors = 0
    async with session_scope() as session:
        for e in entries:
            try:
                await insert_entry(session, e, compute_embedding=True)
            except Exception as ex:
                print(f"  ✗ {e.slug}: {ex}")
                errors += 1
        await session.commit()
    print(f"✓ {len(entries) - errors} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--genre", default=None)
    args = ap.parse_args()
    asyncio.run(seed_library(args.dry_run, args.genre))
