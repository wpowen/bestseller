#!/usr/bin/env python3
"""
Batch 5 — 跨题材通用知识域（genre=None）
用途：给 LLM 激活写作底层知识，不绑定具体题材。
覆盖：叙事结构原理 / 人物心理学 / 中国传统文化 / 写作技法 / 力量体系设计原则
~55 条

Usage: uv run python scripts/seed_material_library_batch5.py [--dry-run]
"""
from __future__ import annotations
import argparse, asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from bestseller.infra.db.session import session_scope
from bestseller.services.material_library import MaterialEntry, insert_entry

def wiki(t, u): return {"url": u, "title": t, "accessed": "2026-04"}
def ref(t): return {"text": t, "confidence": 0.75}
def llm(t): return {"text": f"[LLM推演] {t}", "confidence": 0.60}
L = "llm_synth"

SEED_DATA: list[MaterialEntry] = [

    # =========================================================================
    # 叙事结构原理 (plot_patterns, genre=None)
    # =========================================================================
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="gen-pp-three-act",
        name="三幕结构",
        narrative_summary="西方叙事学最基础框架：建立世界（幕一）→冲突升级（幕二）→高潮解决（幕三），每幕有明确的叙事功能",
        content_json={
            "act1_function": "建立主角的日常世界→触发事件打破日常→主角面临选择（进入冒险）",
            "act2_function": "追求目标→遭遇障碍→中点转折→一切崩溃（最低点）→重新武装自我",
            "act3_function": "带着新视角回到冲突→高潮战→解决→新的日常",
            "chinese_adaptation": "网文常见变体：三幕压缩在前20章，然后进入「升级循环」结构",
            "when_to_subvert": "日常→冲突→解决的顺序可以被颠覆，但每个功能仍然需要存在",
        },
        source_type=L, confidence=0.85,
        source_citations=[ref("Syd Field《剧本》三幕结构理论"), ref("Joseph Campbell英雄旅程与三幕的对应关系")],
        tags=["通用", "叙事结构", "三幕", "写作理论"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="gen-pp-heros-journey",
        name="英雄旅程（坎贝尔模型）",
        narrative_summary="跨文化最普适的叙事原型：普通世界→召唤→拒绝→跨越门槛→考验→最深洞穴→磨难→回归→带着宝物归来",
        content_json={
            "stages_12": "普通世界/冒险召唤/拒绝召唤/遇见导师/跨越门槛/考验与盟友/进入最深洞穴/最大磨难/获得奖励/归途/复活/带着宝物归来",
            "chinese_novel_mapping": "废材→得宝/遇师傅→离开宗门→行走江湖→最大危机→突破→回来更强",
            "symbolic_level": "旅程是内在转变的外部化——物理旅程对应心理成长旅程",
            "counter_hero": "反英雄旅程：英雄不回来/回来带来的是破坏/回来但宝物是假的——每种变体都有叙事意义",
        },
        source_type=L, confidence=0.85,
        source_citations=[ref("Joseph Campbell《千面英雄》"), wiki("英雄旅程", "https://zh.wikipedia.org/wiki/英雄之旅")],
        tags=["通用", "叙事结构", "英雄旅程", "原型"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="gen-pp-kishoten",
        name="起承转合（日本四段式）",
        narrative_summary="东方叙事的节奏框架：起（引入）→承（展开）→转（转折/意外）→合（回收）；转是关键，必须意外但事后合理",
        content_json={
            "qi_setup": "在最小范围内建立世界规则和角色状态",
            "cheng_development": "顺着起的逻辑展开，建立读者期待",
            "zhuan_pivot": "做一件「违背期待但事后合理」的事——这是整段叙事的灵魂",
            "he_resolution": "用转带来的新状态解决起时提出的问题",
            "chinese_chapter_use": "网文每章的微型起承转合：起（推进剧情）→承（展开细节）→转（结尾钩子）→合（下章）",
        },
        source_type=L, confidence=0.82,
        source_citations=[wiki("起承转合", "https://zh.wikipedia.org/wiki/起承轉合"), ref("中国古典叙事诗法与现代小说结构")],
        tags=["通用", "叙事结构", "起承转合", "东方叙事"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="gen-pp-in-medias-res",
        name="开篇入戏（In medias res）",
        narrative_summary="从故事中间最紧张处开始，倒叙补充背景——网文黄金三章的核心策略，让读者先「在乎」再解释「为什么」",
        content_json={
            "principle": "读者的注意力由「在乎结果」维持，而不是「理解背景」——先制造「在乎」再补充背景",
            "chinese_hook_types": "高强度战斗开场/悬念设置（这道门为什么不能开）/情感危机开场/系统降临开场",
            "backfill_technique": "背景信息的「散播」：通过对话/回忆/NPC反应自然补充，不要集中背景介绍",
            "danger": "悬念太强但背景迟迟不补会让读者困惑；平衡点是第1章建立紧张，第3章以内基本建立世界",
        },
        source_type=L, confidence=0.83,
        source_citations=[ref("网文黄金三章开篇技法研究"), llm("中文网文In medias res应用分析")],
        tags=["通用", "叙事结构", "开篇", "钩子"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="gen-pp-tension-breath",
        name="张力与缓冲节奏",
        narrative_summary="叙事节奏的基本规律：高强度张力之后必须有缓冲，没有缓冲的叙事会让读者麻木；缓冲不是无聊，是为下一次张力蓄力",
        content_json={
            "tension_types": "外部张力（物理危险/冲突）/内部张力（角色心理危机）/关系张力（人际冲突/误解）",
            "breathing_scenes": "缓冲场景的功能：角色反思/关系推进/世界观展示/幽默缓解，不是「没有内容」",
            "rhythm_pattern": "战斗/冲突→缓冲→新信息→升级冲突→更深缓冲，螺旋上升",
            "chinese_novel_trap": "网文常见问题：为了爽感去掉所有缓冲，导致读者「打开没什么感觉，但也不想关」",
            "micro_tension": "缓冲场景里也要有微型张力：一个没说出口的问题/一个迟疑的动作",
        },
        source_type=L, confidence=0.83,
        source_citations=[llm("叙事张力节奏设计系统研究")],
        tags=["通用", "叙事技法", "节奏", "张力"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="gen-pp-chekhov-gun",
        name="契诃夫之枪（预埋与兑现）",
        narrative_summary="「第一幕里出现的枪，第三幕里必须开枪」——预埋细节与后期兑现的循环是优质叙事的DNA",
        content_json={
            "setup_types": "物品（独特的道具）/性格特点（特殊的能力或弱点）/信息（看似无关的细节）/关系",
            "payoff_delay": "预埋与兑现的距离越远，兑现时读者的满足感越强",
            "multiple_payoffs": "一个预埋可以有多次小兑现，最终大兑现",
            "how_to_hide": "预埋时让读者「看见但不在意」——通过对话/环境细节嵌入，不要强调",
            "chinese_use": "网文中的契诃夫之枪：早期被嘲笑的「废物技能」/不被当回事的配角/主角自己也忘记的承诺",
        },
        source_type=L, confidence=0.85,
        source_citations=[ref("契诃夫之枪叙事理论"), wiki("契诃夫之枪", "https://zh.wikipedia.org/wiki/契訶夫的槍")],
        tags=["通用", "叙事技法", "伏笔", "兑现"],
    ),

    # =========================================================================
    # 人物心理学 (character_archetypes, genre=None)
    # =========================================================================
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="gen-ca-jung-shadow",
        name="荣格阴影原型",
        narrative_summary="荣格的「阴影」是人格中被压抑、否认的部分——好的反派或复杂角色往往是主角「阴影」的外化，代表主角最不愿承认的自己",
        content_json={
            "shadow_definition": "阴影包含「负面」情绪（愤怒/贪婪/恐惧）和被社会压抑的「反常规」欲望",
            "narrative_use": "主角与反派的最深冲突不是价值观对立，而是主角在反派身上看见了自己的可能",
            "integration": "成熟的人格发展是整合阴影，而不是消灭它——接受自己有这些部分，才能选择不被它们驱动",
            "character_design": "让反派有吸引力的方法：给他们主角内心深处渴望但拒绝承认的东西",
        },
        source_type=L, confidence=0.82,
        source_citations=[wiki("荣格心理学", "https://zh.wikipedia.org/wiki/分析心理學"), ref("Carl Jung《心理类型》阴影概念")],
        tags=["通用", "心理学", "荣格", "阴影"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="gen-ca-attachment-styles",
        name="依恋类型与CP设计",
        narrative_summary="依恋理论（安全型/焦虑型/回避型/混乱型）是CP动态的底层逻辑——理解依恋风格才能写出真实的感情",
        content_json={
            "secure": "安全依恋：可以依靠对方，也可以被依靠；不焦虑分离，不因亲密而恐惧",
            "anxious": "焦虑依恋：需要持续的确认；害怕被抛弃；过度解读对方的信号",
            "avoidant": "回避依恋：表面冷漠独立；被亲密感到恐惧；用远离保护自己",
            "disorganized": "混乱依恋：一方面渴望亲密，一方面恐惧亲密（多来自创伤）",
            "cp_dynamics": "焦虑+回避是最有张力的CP组合（痛苦）；安全+任何类型是最有治愈感的组合",
        },
        source_type=L, confidence=0.83,
        source_citations=[ref("John Bowlby依恋理论"), wiki("依恋理论", "https://zh.wikipedia.org/wiki/依附理論")],
        tags=["通用", "心理学", "依恋", "CP设计"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="gen-ca-trauma-response",
        name="创伤反应与角色设计",
        narrative_summary="创伤不是性格标签，而是影响神经系统的真实经历——4F反应（战/逃/冻/讨好）是创伤在角色行为上的真实表现",
        content_json={
            "fight_response": "以攻击应对威胁——愤怒、暴力、控制倾向，是创伤的「强硬」面具",
            "flight_response": "以逃离应对威胁——回避、工作狂、成瘾，是创伤的「消失」策略",
            "freeze_response": "无法行动，解离、麻木、恍惚，是创伤的「关机」模式",
            "fawn_response": "以讨好应对威胁——过度顺从、失去自我、把别人的需求放在自己前面",
            "design_principle": "真实的创伤角色：在「安全」情境也会触发创伤反应；创伤反应不是弱点而是曾经有用的生存策略",
        },
        source_type=L, confidence=0.83,
        source_citations=[ref("Peter Walker《从生存到成长》创伤反应4F模型"), wiki("PTSD", "https://zh.wikipedia.org/wiki/創傷後壓力症候群")],
        tags=["通用", "心理学", "创伤", "角色设计"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="gen-ca-moral-injury",
        name="道德创伤角色",
        narrative_summary="道德创伤（Moral Injury）：当一个人做了、目睹了、或无法阻止了违背核心价值观的事——这种创伤比PTSD更难处理，因为它挑战了「世界是公正的」信念",
        content_json={
            "definition": "与普通创伤（恐惧/危险）不同，道德创伤核心是「我做了不该做的事」或「我没有做应该做的事」",
            "symptoms": "深刻的羞耻感/强烈的自我批评/对未来的虚无感/对人际关系的退缩",
            "narrative_richness": "道德创伤角色拥有最深层的内心冲突，是「好人做了坏事」叙事的基础",
            "healing_path": "不是「找到理由原谅自己」，而是「在不抹去错误的情况下继续前行」",
            "character_examples": "战士（下令或执行了不应该的行动）/领导者（牺牲了某人换取更大利益）/旁观者（有能力阻止但没有）",
        },
        source_type=L, confidence=0.82,
        source_citations=[ref("Jonathan Shay《阿喀琉斯在越南》道德创伤理论"), llm("道德创伤与文学人物设计研究")],
        tags=["通用", "心理学", "道德创伤", "角色深度"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="gen-ca-mentor-archetype",
        name="导师原型与功能",
        narrative_summary="导师不只是给主角技能的存在，是主角内在潜力的「镜子」——好的导师教的是「你已经是谁」，不是「你需要变成谁」",
        content_json={
            "mentor_types": "知识型（传授技能/知识）/智慧型（传授世界观）/阴影型（教反面教材）/短暂型（一次改变主角一生）",
            "death_function": "导师的死亡是主角「独立」的叙事标志——主角不能再依靠外部权威，必须内化导师的教诲",
            "healthy_mentorship": "好导师：给工具，不代替主角做选择；看见主角潜力，不投射自己的期待",
            "toxic_mentor": "毒性导师：以爱的名义控制/把主角培养成自己的工具/让主角永远依赖不能独立",
            "narrative_design": "导师与主角的关系要有明确的「结束点」：主角在什么时候不再需要导师？",
        },
        source_type=L, confidence=0.82,
        source_citations=[ref("Joseph Campbell英雄旅程中的导师原型"), llm("文学导师角色功能设计研究")],
        tags=["通用", "心理学", "导师", "原型"],
    ),

    # =========================================================================
    # 中国传统文化知识库 (real_world_references, genre=None)
    # =========================================================================
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-confucianism",
        name="儒家思想叙事激活层",
        narrative_summary="仁义礼智信忠孝是中国叙事的底层道德语言，理解儒家的核心矛盾（个人与家族/忠与孝的冲突）才能写出真实的传统叙事张力",
        content_json={
            "core_values": "仁（人际关爱）/义（道义正直）/礼（社会规范）/智（明辨是非）/信（信义守诺）",
            "key_conflicts": "忠孝难两全/礼义相冲（正式规范vs实质公正）/仁与决断（仁慈的领导者vs必要时的冷酷）",
            "narrative_use": "忠孝冲突是中国历史叙事最深的张力源；礼义矛盾是宫斗/权谋的底层逻辑",
            "modern_tension": "儒家价值观在现代叙事中的张力：集体vs个人/家族利益vs个人意志",
            "activation_keywords": ["三纲五常", "君君臣臣父父子子", "士可杀不可辱", "仁者爱人", "舍生取义"],
        },
        source_type=L, confidence=0.85,
        source_citations=[wiki("儒家", "https://zh.wikipedia.org/wiki/儒家"), ref("《论语》《孟子》核心思想概要")],
        tags=["通用", "中国文化", "儒家", "叙事底层"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-taoism-narrative",
        name="道家思想叙事激活层",
        narrative_summary="道法自然/无为而治/阴阳相生是仙侠/武侠/洪荒的哲学底色，也是所有东方叙事「高手淡然」气质的来源",
        content_json={
            "core_concepts": "道（宇宙规律/终极本体）/德（道在个体的体现）/无为（顺应自然而非强力干预）/柔克刚",
            "narrative_archetypes": "大隐隐于市的高人/不在名利场的得道者/以弱胜强的叙事美学",
            "paradoxes": "无为不是不作为，而是「不妄为」；柔不是软弱，是内在的强大",
            "contrast": "道家vs儒家在叙事中的体现：儒家是「应该」的规范，道家是「自然」的哲学",
            "activation_keywords": ["道可道非常道", "上善若水", "无为而无不为", "知足者富", "反者道之动"],
        },
        source_type=L, confidence=0.85,
        source_citations=[wiki("道家", "https://zh.wikipedia.org/wiki/道家"), ref("老子《道德经》思想概述")],
        tags=["通用", "中国文化", "道家", "哲学"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-buddhism-narrative",
        name="佛家概念叙事激活层",
        narrative_summary="因果/轮回/业力/慈悲/执念是佛教向中国叙事渗透最深的概念，是人物业力线、转世叙事、「放下执念」弧的思想底层",
        content_json={
            "key_concepts": "业（行为的力量积累）/因果（行为与结果的宇宙规律）/轮回（生死循环）/执（妨碍解脱的执着）/空（万物无永恒自性）",
            "narrative_patterns": "前世因果在今世兑现（快穿/重生）/放下执念才能突破（修炼叙事）/慈悲者得庇护（功德叙事）",
            "chinese_buddhism": "禅宗（直指人心顿悟）/净土宗（念佛往生）/藏传（活佛转世）各有叙事资源",
            "tension_source": "佛家的「放下」与中国叙事的「执着复仇/执着突破」形成内在张力",
            "activation_keywords": ["因果轮回", "执念", "业障", "放下屠刀立地成佛", "菩提本无树", "阿弥陀佛"],
        },
        source_type=L, confidence=0.85,
        source_citations=[wiki("中国佛教", "https://zh.wikipedia.org/wiki/中國佛教"), ref("佛教因果业力概念在中国文学中的体现")],
        tags=["通用", "中国文化", "佛家", "叙事底层"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-chinese-history-timeline",
        name="中国历史朝代脉络激活",
        narrative_summary="中国历史的分合节律：大一统→分裂→再统一，每个节点有对应的权力结构、文化特征、叙事背景",
        content_json={
            "unification_periods": "秦（法家统治/中央集权原型）/汉（儒家确立/丝绸之路）/唐（开放多元/安史之乱分水岭）/宋（文化顶峰/军事积弱）/明（封建保守/海禁）/清（满汉张力/近代变局）",
            "division_periods": "战国（百家争鸣/合纵连横）/三国（乱世英雄/义气忠诚叙事来源）/南北朝（佛教传播/民族融合）/五代十国（军阀割据/武夫政治）",
            "power_structure_types": "皇权独大/相权制衡/外戚/宦官/藩镇，不同时期有不同主导权力",
            "narrative_richness": "每个朝代有独特的社会风气和叙事可能性：汉代慷慨/唐代浪漫/宋代精雅/明代险恶",
            "activation_points": ["楚汉争霸", "三国演义", "隋唐英雄", "靖康之耻", "土木之变", "扬州十日"],
        },
        source_type=L, confidence=0.85,
        source_citations=[wiki("中国历史", "https://zh.wikipedia.org/wiki/中国历史"), ref("钱穆《国史大纲》历史分期")],
        tags=["通用", "中国历史", "朝代", "世界观底层"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-chinese-mythology-system",
        name="中国神话体系激活",
        narrative_summary="中国神话不是统一体系，而是多源汇聚：道教神仙谱系/佛教菩萨系统/民间信仰/上古神话各自独立又相互渗透",
        content_json={
            "daoist_pantheon": "三清（道德天尊/灵宝天尊/元始天尊）/四御/十方天帝/真武玄天/八仙",
            "folk_deities": "城隍（地方守护）/土地公（田地守护）/灶王（家庭守护）/财神/门神，每个都有完整传说",
            "mythology_events": "盘古开天/女娲造人/后羿射日/夸父追日/共工怒触不周山/精卫填海",
            "mythological_creatures": "龙（皇权象征/水神）/凤凰（吉祥/火）/麒麟（太平祥瑞）/玄武（北方守护）",
            "activation_keywords": ["天庭", "地府", "蟠桃", "女娲", "鸿钧", "道祖", "原始天尊", "灵宝"],
        },
        source_type=L, confidence=0.85,
        source_citations=[wiki("中国神话", "https://zh.wikipedia.org/wiki/中國神話"), wiki("山海经", "https://zh.wikipedia.org/wiki/山海經")],
        tags=["通用", "中国神话", "神祇", "世界观底层"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-chinese-martial-arts",
        name="中国武术流派知识库",
        narrative_summary="真实武术体系是武侠/仙侠的血肉：内家拳（太极/形意/八卦）与外家拳（少林/南拳）的哲学差异决定了武侠人物的气质",
        content_json={
            "internal_styles": "太极（以柔克刚/借力打力）/形意（形象意志合一/直线爆发）/八卦（圆转走化/方位变换）",
            "external_styles": "少林（力量刚猛/禅武合一）/长拳（大开大合/展示性）/南拳（马步稳固/短促有力）",
            "weapon_systems": "剑（轻灵/君子/刺击为主）/刀（勇猛/格挡切割）/枪/棍（一寸长一寸强）",
            "philosophy_in_style": "武术风格即人格：太极→内敛深沉/形意→果决直接/少林→坚忍有力/长拳→外向张扬",
            "activation_keywords": ["内力", "气沉丹田", "以柔克刚", "后发先至", "形意合一", "真气运行"],
        },
        source_type=L, confidence=0.80,
        source_citations=[wiki("中国武术", "https://zh.wikipedia.org/wiki/中國武術"), wiki("太极拳", "https://zh.wikipedia.org/wiki/太极拳")],
        tags=["通用", "武术", "参考", "武侠底层"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-tcm-knowledge",
        name="中医药学叙事激活层",
        narrative_summary="传统中医理论（阴阳五行/经络/气血/脏腑）是修仙/玄幻设定的真实知识底层，也是「灵气运行」「丹田」「经脉」等概念的来源",
        content_json={
            "yin_yang_in_body": "阴阳平衡是健康基础；修炼中的「阴盛阳衰」或「阳亢阴虚」是体质失衡的隐喻",
            "meridian_system": "十二正经+奇经八脉，经络是「气」的通道，打通经脉是修炼突破的生理隐喻",
            "herbs_knowledge": "人参（大补元气）/甘草（百药之王）/当归（补血）/朱砂（清热）/何首乌（延年益寿）",
            "pulse_diagnosis": "望闻问切，脉象是生命状态的窗口，医者通过脉象知晓秘密的叙事可能",
            "activation_keywords": ["丹田", "经脉", "奇经八脉", "阴阳失衡", "气血双修", "炼气化神", "以形补形"],
        },
        source_type=L, confidence=0.82,
        source_citations=[wiki("中医", "https://zh.wikipedia.org/wiki/中醫"), wiki("经络", "https://zh.wikipedia.org/wiki/經絡")],
        tags=["通用", "中医", "参考", "修炼底层"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="gen-rwr-psychology-of-power",
        name="权力心理学激活层",
        narrative_summary="权力如何改变人的心理和行为——斯坦福监狱实验/米尔格伦服从实验揭示的规律，是所有政治/宫斗叙事的心理底层",
        content_json={
            "power_corrupts": "权力降低同理心、增加刻板印象使用，有科学依据（Dacher Keltner权力悖论）",
            "authority_obedience": "米尔格伦实验：大多数普通人在权威命令下会做出超出自身道德预期的事",
            "in_group_out_group": "一旦被分组，人类天然对内群体产生偏爱和对外群体的贬低（斯坦福监狱实验）",
            "narrative_use": "平庸之恶（不做坏人，只是服从）/权力腐化弧（好人得到权力后的变化）/群体压力下的个人",
            "counter_examples": "什么使人抵抗权力的腐化？有时是外部锚点（被某人看见真实的自己）",
        },
        source_type=L, confidence=0.82,
        source_citations=[ref("Stanley Milgram《服从权威》"), ref("菲利普·津巴多《路西法效应》"), wiki("米尔格伦实验", "https://zh.wikipedia.org/wiki/米爾格倫實驗")],
        tags=["通用", "心理学", "权力", "社会心理"],
    ),

    # =========================================================================
    # 写作技法 (scene_templates + dialogue_styles, genre=None)
    # =========================================================================
    MaterialEntry(
        dimension="scene_templates", genre=None,
        slug="gen-st-death-scene",
        name="角色死亡场景",
        narrative_summary="重要角色的死亡是叙事最高能量节点——准备不足会让读者感到背叛，过度准备会消耗掉死亡的冲击",
        content_json={
            "pre_death_setup": "死前要有「意义」的积累：完成了某件事/说了某句话/和某人有了最后的真实接触",
            "death_itself": "不要「英雄死亡演讲」——真实的死亡往往比想象更快、更混乱，或者更安静",
            "aftermath": "死亡的重量由活下来的人的反应来测量——留存者的悲痛是死亡真实性的证明",
            "functional_death": "每个重要角色的死亡应该改变叙事走向，不只是情感事件",
            "avoid": "「假死」过多会让读者不再相信死亡是真实的；英雄式独白死亡常常是紫文",
        },
        source_type=L, confidence=0.82,
        source_citations=[llm("叙事死亡场景写作技法研究")],
        tags=["通用", "场景", "死亡", "高潮"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre=None,
        slug="gen-st-reveal-scene",
        name="真相揭露场景",
        narrative_summary="读者期待最久的真相被说出来的那一刻——这个场景的情绪管理决定了整个铺垫是否值得",
        content_json={
            "timing": "揭露时机：不在最方便的时候，而在最有戏剧张力的时候（关键决策前/关系变化点）",
            "delivery": "谁说、怎么说比「说什么」更重要；侧面暗示的揭露比直接说出来更有力量",
            "reaction": "接受者的反应是揭露场景的情感重心：震惊→否认→接受，每个阶段都值得写",
            "consequence": "揭露之后世界改变了——场景不能在揭露之后立刻切走，要让后果展开",
            "visual_anchor": "揭露时刻要有一个物理细节作为锚点：掉落的杯子/窗外的雨/没有表情的脸",
        },
        source_type=L, confidence=0.82,
        source_citations=[llm("叙事揭露场景写作技法研究")],
        tags=["通用", "场景", "揭露", "高潮"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="gen-ds-unspoken-truth",
        name="未说出口的真话写法",
        narrative_summary="最重要的话往往不是被说出来的，而是通过「说了另一件事」来传达——侧面台词比直接表达更有力量",
        content_json={
            "indirect_confession": "主角说「天气变冷了，多穿点」实际意思是「我在乎你」",
            "denial_confirmation": "「我才不担心你」配上的行动是「但我在原地等了三个小时」",
            "questions_as_statements": "「你一定会回来吗？」= 「我想让你知道我希望你回来」",
            "changing_subject": "话题的回避也是一种答案：当有人问了一个问题，对方聊了别的，读者知道那就是答案",
            "technique": "写未说出口的真话：给角色一个他/她想说的真话→给出一个替代的说法→让行动/眼神传递真话",
        },
        source_type=L, confidence=0.83,
        source_citations=[llm("叙事对话侧面真话写作技法研究")],
        tags=["通用", "台词", "潜台词", "写作技法"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="gen-ds-character-voice",
        name="角色台词个性化",
        narrative_summary="每个角色应该有独特的说话方式，即使不看说话者标记，读者也应该知道是谁在说——声音差异是角色真实存在的证明",
        content_json={
            "vocabulary_distinction": "受教育程度/成长背景影响用词：书生引经据典/武人直接粗砺/商人精于数字",
            "sentence_rhythm": "性格影响句子节奏：急切→短句多/思虑周全→长句完整/情绪压抑→说到一半停住",
            "topic_preference": "角色的关注点决定他们会「先注意到」什么：军人看地形/商人看价格/厨师看食材",
            "avoidance": "每个角色都有不愿意说的话/不擅长说的话，这个限制使他们真实",
            "test": "写完对话后，去掉「XX说」标记，看是否仍然知道是谁在说——如果不知道，声音还没差异化",
        },
        source_type=L, confidence=0.83,
        source_citations=[llm("角色台词个性化写作研究")],
        tags=["通用", "台词", "角色声音", "写作技法"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="gen-ds-argument-structure",
        name="争吵/冲突对话结构",
        narrative_summary="好的争吵对话：双方都有道理，读者理解两边，冲突揭示了人物深层的价值观和需求，不只是「谁输谁赢」",
        content_json={
            "surface_vs_deep": "表面吵的是事（「你为什么那么做」），深层争的是需求（「你为什么不尊重我的判断」）",
            "escalation_pattern": "争吵通常升级：事实层→评价层→人格层，升级到人格层时最难缓和",
            "both_right": "好的争吵：读者对双方都可以代入，都可以说「我理解你」",
            "subtext": "争吵中说不出口的那句话往往是最真实的——「我害怕失去你」常常以「你总是这样」的形式出现",
            "aftermath": "争吵之后的沉默/回避/小心翼翼，往往比争吵本身更有叙事密度",
        },
        source_type=L, confidence=0.82,
        source_citations=[llm("冲突对话叙事结构研究")],
        tags=["通用", "台词", "争吵", "冲突"],
    ),

    # =========================================================================
    # 爽文机制 (thematic_motifs, genre=None)
    # =========================================================================
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="gen-tm-face-slap",
        name="打脸爽感机制",
        narrative_summary="「打脸」是中文网文最核心的情绪机制之一——其本质是「被低估者以结果证明价值」，有心理学上的公平感满足",
        content_json={
            "psychological_root": "「打脸」满足了读者对「公正被恢复」的原始渴望——嘲笑者被证明错了",
            "setup_requirement": "打脸的前提是「充分的轻视」——轻视越深，打脸越爽",
            "elevation_principle": "被打脸者不能是纯粹的恶人（那变成了惩罚），应该是「自以为是但有一定理由」的人",
            "variation_types": "当场打脸（即时满足）/延迟打脸（积累效果）/代替打脸（第三方代为/让轻视者亲口说「我错了」）",
            "quality_test": "高质量打脸：读者在打脸前一刻已经感受到「要来了」，打脸时感受到「果然」",
        },
        source_type=L, confidence=0.82,
        source_citations=[llm("网文打脸爽感心理机制研究")],
        tags=["通用", "爽文机制", "打脸", "满足感"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="gen-tm-underdog-peak",
        name="逆境崛起爽感机制",
        narrative_summary="从最低谷到最高峰的叙事弧——弧度越大（起点越低/终点越高），满足感越强，但中间的「合理性」是成败关键",
        content_json={
            "bottom_design": "起点的低谷要有两个维度：外部（条件最差）+内部（心理/能力也在低点），不能只有外部",
            "climb_credibility": "每步上升都要有内在逻辑——付出/顿悟/际遇，三者结合比单一来源更可信",
            "obstacles_必要性": "没有障碍的崛起不产生满足感；障碍必须在读者「觉得不可能」时出现，然后被克服",
            "peak_recognition": "崛起的顶点应该被他人认可——读者通过他人对主角的态度转变感受到主角的成长",
            "chinese_version": "中文特有的「打脸式崛起」：那些轻视你的人亲眼见证你的崛起，这个「见证」是顶点的标配",
        },
        source_type=L, confidence=0.82,
        source_citations=[llm("逆袭崛起叙事满足感机制研究")],
        tags=["通用", "爽文机制", "逆袭", "崛起"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="gen-tm-collectible-companions",
        name="伙伴收集与团队叙事",
        narrative_summary="主角逐渐汇聚伙伴的叙事结构——每个伙伴的加入都是一个完整的小叙事，团队化学反应是长期叙事的基础",
        content_json={
            "companion_entry": "每个伙伴的加入都要有「为什么是这个人」的叙事理由：共同目标/相互救助/技能互补/情感共鸣",
            "team_dynamics": "伙伴之间的关系不是「大家都爱主角」，而是有各自的矛盾/友谊/竞争",
            "individual_arcs": "每个伙伴都有自己的弧线，不能在加入后就变成背景板",
            "departure_cost": "伙伴的离开/牺牲/背叛应该有叙事重量——失去伙伴应该改变主角",
            "team_climax": "团队的最高光时刻是每个人都用自己的特长解决了一个部分，而不是主角单打独斗",
        },
        source_type=L, confidence=0.80,
        source_citations=[llm("伙伴收集团队叙事设计研究")],
        tags=["通用", "团队叙事", "伙伴", "结构"],
    ),

    # =========================================================================
    # 力量体系设计原则 (power_systems, genre=None)
    # =========================================================================
    MaterialEntry(
        dimension="power_systems", genre=None,
        slug="gen-ps-hard-soft-magic",
        name="硬魔法vs软魔法设计原则",
        narrative_summary="Brandon Sanderson第一定律：主角用魔法解决问题时，读者必须事先了解规则；问题的戏剧性来自规则的已知限制",
        content_json={
            "hard_magic": "规则明确、代价清晰、逻辑一致——读者可以推算什么能做什么不能，适合解谜/战术叙事",
            "soft_magic": "规则神秘、效果惊人、难以预测——制造奇迹感和神秘感，适合史诗/恐怖叙事",
            "sanderson_laws": "第一定律：了解后再解决问题/第二定律：弱点和代价比能力更有趣/第三定律：在扩展前先深化已有系统",
            "chinese_xianxia": "仙侠通常是软硬混合：基础等级是硬的（突破规则明确），秘技/神器是软的",
            "design_trap": "新能力解决旧问题（Deus ex machina）是破坏读者信任的根本原因",
        },
        source_type=L, confidence=0.85,
        source_citations=[ref("Brandon Sanderson魔法体系设计三定律"), llm("硬软魔法体系在中文网文中的应用分析")],
        tags=["通用", "力量体系", "设计原则", "魔法系统"],
    ),
    MaterialEntry(
        dimension="power_systems", genre=None,
        slug="gen-ps-cost-limitation",
        name="代价与限制：有趣力量的核心",
        narrative_summary="力量体系中最重要的设计元素不是「能做什么」而是「不能做什么」和「代价是什么」——限制创造叙事张力",
        content_json={
            "cost_types": "资源代价（消耗灵力/体力/生命）/时间代价（施法时间/冷却时间）/伦理代价（使用需要做某件事）/牺牲代价（用某种无法再得的东西换取）",
            "limitation_types": "能力边界（对什么有效/对什么无效）/使用条件（需要什么才能用）/副作用（使用后的负面影响）",
            "narrative_value": "代价越有叙事意义（不只是数值损耗），力量使用场景越有情感重量",
            "example_good": "用未来寿命换取力量：每次使用主角都在缩短自己的生命——时间窗口产生紧迫感",
            "example_bad": "消耗魔力/灵力，然后打坐恢复——纯数值，无情感重量",
        },
        source_type=L, confidence=0.85,
        source_citations=[llm("力量体系代价设计研究"), ref("Brandon Sanderson Sanderson's Second Law: Limitations > Powers")],
        tags=["通用", "力量体系", "代价", "设计原则"],
    ),
    MaterialEntry(
        dimension="power_systems", genre=None,
        slug="gen-ps-cultivation-tier-design",
        name="修炼等级体系设计原则",
        narrative_summary="中文网文的核心设计元素——等级体系好坏的关键不是层数多少，而是每个等级的「质变」是否清晰且有叙事意义",
        content_json={
            "qualitative_change": "好的等级体系：每次突破不只是数值提升，而是「能做到以前做不到的事」",
            "naming_philosophy": "等级命名要有内在逻辑（反映哲学/自然规律/修炼本质），不只是数字或随机命名",
            "bottleneck_design": "等级瓶颈不只是「需要更多资源」，而是「需要某种领悟/经历/代价」",
            "inter_level_gap": "等级差距要清晰但不能是唯一决定因素——战略/特殊能力/代价可以对抗纯等级差",
            "ceiling_foreshadowing": "最高等级应该在世界观建立早期就以「传说/禁忌」的方式存在，主角的进化路径要有指向",
        },
        source_type=L, confidence=0.83,
        source_citations=[llm("中文网文修炼体系设计原则研究")],
        tags=["通用", "力量体系", "修炼", "等级设计"],
    ),

    # =========================================================================
    # 情感弧通用模板 (emotion_arcs, genre=None)
    # =========================================================================
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="gen-ea-trust-building",
        name="信任建立弧",
        narrative_summary="信任不是「感觉对方好」，而是在有风险的情境下仍然选择依靠对方——每一步信任的建立都需要一次实际的验证",
        content_json={
            "trust_levels": "陌生→容忍→合作→信赖→完全信任，每级之间需要一次「考验通过」",
            "trust_test_types": "在可以背叛时选择不背叛/在主角最脆弱时出现并没有利用/主动承担与主角共同的风险",
            "betrayal_aftermath": "信任一旦被破坏，重建的代价是原始建立代价的数倍——而且永远会留下痕迹",
            "vulnerability_requirement": "真正的信任要求展示脆弱——那个人看见了你最弱的地方，还是选择了你",
            "narrative_use": "对于有创伤背景的角色，信任的每一步都更难、更有意义",
        },
        source_type=L, confidence=0.83,
        source_citations=[ref("Brené Brown《脆弱的力量》信任理论"), llm("叙事信任建立弧设计研究")],
        tags=["通用", "情感弧", "信任", "关系"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="gen-ea-identity-arc",
        name="身份认同弧",
        narrative_summary="「我是谁」是所有成长叙事的底层问题——身份认同危机到解决的弧线，是角色成长最深层的维度",
        content_json={
            "identity_crisis_trigger": "外部触发：被贴上不符合自我认知的标签/失去原有身份/进入全新环境",
            "false_identity": "危机后主角往往先接受一个「外部给予的身份」（强者/受害者/叛徒），这不是真相",
            "identity_quest": "在行动中寻找真实的自己：「我在做这件事时感觉是我」vs「我做这件事时感觉不像我」",
            "identity_integration": "真正的身份解决不是「发现了固定的我」，而是「接受我是在变化中的，但有某些核心不变」",
            "narrative_expression": "主角第一次不用解释、不用证明地做了「自己」的事，那一刻是身份弧的顶点",
        },
        source_type=L, confidence=0.82,
        source_citations=[ref("Erik Erikson身份认同理论"), llm("成长叙事身份弧设计研究")],
        tags=["通用", "情感弧", "身份", "成长"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="gen-ea-redemption-arc",
        name="救赎弧",
        narrative_summary="救赎不是「犯错的人变好了」，而是「一个人为自己曾经造成的伤害承担了真实的代价并做出了不同的选择」",
        content_json={
            "false_redemption": "主角道歉/说「我错了」→大家原谅→继续前行——这不是救赎，是廉价的感情清算",
            "true_redemption_components": "承认→理解造成的伤害→承担代价（不由对方「原谅」免除）→在相似情境做出不同选择",
            "cost_specificity": "代价必须和罪行有内在关联：伤害了一个人的信任→用行动（不是话语）重建，需要时间",
            "victim_agency": "被伤害者有权选择不原谅，救赎弧不应该以「得到原谅」为终点",
            "arc_quality_test": "读者看到救赎弧时应该感受到「这代价是真实的，这改变是真实的」，而不是「好吧就算你赎罪了」",
        },
        source_type=L, confidence=0.83,
        source_citations=[llm("叙事救赎弧设计研究"), ref("Christopher Vogler《作家之旅》英雄救赎分析")],
        tags=["通用", "情感弧", "救赎", "代价"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="gen-ea-found-family",
        name="羁绊家人弧",
        narrative_summary="非血缘家庭的建立——在流浪/冒险/危机中发现彼此是「家人」，这段弧线往往比浪漫爱情更有共鸣，因为它基于选择而非血缘",
        content_json={
            "formation_stages": "陌生→临时同伴→经历共同危机→看见彼此脆弱→主动选择继续在一起",
            "family_definition": "家人是「在最坏的时候仍然在场的人」，不需要血缘，需要的是选择",
            "tension_within": "即使是「选择的家人」也有摩擦——不同背景/价值观/创伤的人在一起会有真实冲突",
            "loss_impact": "失去「家人」比失去陌生人更重，因为这段关系是主动建立的，不是被给予的",
            "reader_resonance": "羁绊家人主题在城市孤立/离家的现代读者中有强烈共鸣",
        },
        source_type=L, confidence=0.83,
        source_citations=[llm("羁绊家庭叙事情感弧研究"), ref("当代心理学家庭定义的演变")],
        tags=["通用", "情感弧", "羁绊", "家人"],
    ),
]


async def seed_library(dry_run: bool = False) -> None:
    print(f"{'[DRY RUN] ' if dry_run else ''}Seeding {len(SEED_DATA)} entries...\n")
    by_d: dict[str, int] = {}
    for e in SEED_DATA:
        by_d[e.dimension] = by_d.get(e.dimension, 0) + 1
    print(f"By dimension: {dict(sorted(by_d.items()))}\n")
    if dry_run:
        return
    errors = 0
    async with session_scope() as session:
        for e in SEED_DATA:
            try:
                await insert_entry(session, e, compute_embedding=True)
            except Exception as ex:
                print(f"  ✗ {e.slug}: {ex}")
                errors += 1
        await session.commit()
    print(f"✓ {len(SEED_DATA) - errors} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(seed_library(args.dry_run))
