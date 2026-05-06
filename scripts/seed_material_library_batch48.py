"""Batch 48: thematic_motifs - cross-cultural symbols + emotion_arcs depth (12 entries)

Cross-cultural symbol depth (8 motifs):
- 桥, 树, 雨, 雪, 山, 海, 风, 蝴蝶

Plus emotion arcs (4):
- 自我接纳孤独
- 怀疑→坚信→破碎→重建（信仰弧）
- 同伴情谊死亡哀悼
- 第一爱→失去→疗愈
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
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-bridge-crossing",
        name="意象：桥 / 过渡与连接",
        narrative_summary="桥作为意象=连接两个世界+ 过渡阶段+ 不可逆的选择。东方=断桥（白蛇+许仙）；西方=Brooklyn Bridge（移民希望）；古希腊=Acheron 通往冥界。",
        content_json={
            "symbolic_layers": "1) 物理连接（两岸之间） / 2) 时空过渡（过去-现在-未来）/ 3) 不可逆选择（一旦过桥不能回头）/ 4) 阴阳界（生死边界）/ 5) 阶级跃迁（贫民窟到富人区）",
            "famous_uses": "白蛇传断桥（爱情起点）+ Bridges of Madison County 廊桥（婚外短暂爱情）+ Old Yeller 木桥（童年终结）+ Pont Neuf（巴黎爱情圣地）+ 鹊桥（牛郎织女）+ 三途川（日本死亡之桥）",
            "scene_use_cases": "1) 男女主第一次见面=桥上 / 2) 离别=桥头分别 / 3) 重大决定=过桥前犹豫 / 4) 死亡=桥下河水带走尸体 / 5) 重生=过桥之后变成另一个人",
            "metaphor_layers": "桥下水（时间流逝）+ 桥栏杆（约束）+ 桥身（物质转移工具）+ 桥头（决定点）+ 桥的另一头（未知）",
            "anti_cliche": "不要纯当背景板；让桥有自己的历史（建桥工人+ 桥上自杀者+ 修桥时的故事）",
            "activation_keywords": ["桥", "过渡", "断桥", "Brooklyn", "三途川"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Bridge_(symbolism)"), wiki("Sanzu_River"), wiki("White_Snake")],
        tags=["motifs", "桥", "过渡", "通用"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-tree-rooted",
        name="意象：树 / 生命与扎根",
        narrative_summary="树=生命+ 时间+ 扎根+ 家族传承。东方=神木+ 老槐+ 桃树；西方=Yggdrasil 世界树+ 圣经知识之树+ 凯尔特橡树。能见证整本书的开始和结束。",
        content_json={
            "symbolic_layers": "1) 生命力（季节循环） / 2) 时间（年轮） / 3) 扎根（家族）/ 4) 庇护（树荫）/ 5) 牺牲（被砍代表毁灭）",
            "famous_uses": "Yggdrasil（北欧世界树）+ 圣经知识之树+ 凯尔特橡树+ 桃园三结义桃树+ 红楼梦海棠+ 《白鹿原》白家槐树+ Avatar Hometree",
            "tree_types_meaning": "桃（爱情+长寿）+ 柳（离别+ 思念）+ 松（坚贞+ 长寿）+ 槐（家族）+ 樱花（短暂美好）+ 银杏（爱情）+ 椿/油桐（不祥）+ 橡树（坚强+ 凯尔特神圣）",
            "scene_use_cases": "1) 家族大事（生死婚嫁）必在大树下 / 2) 童年回忆+树下秋千 / 3) 树被砍=家族败落 / 4) 树重新发芽=希望复苏 / 5) 树下埋东西（信物+尸体）",
            "anti_cliche": "不要纯写'美丽的大树'；让树有真实的细节（虫蛀+ 苔藓+ 老枝断裂+ 鸟巢留下的羽毛）",
            "activation_keywords": ["树", "生命", "Yggdrasil", "桃花", "扎根"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Yggdrasil"), wiki("Tree_of_life"), wiki("Sacred_tree")],
        tags=["motifs", "树", "生命", "通用"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-rain-purification",
        name="意象：雨 / 净化与悲伤",
        narrative_summary="雨=情绪+ 净化+ 悲伤+ 重生+ 转折。雨中告白/雨中分手/雨中死亡是言情+悬疑+ 武侠+ 文艺片的最高频意象。要写出每滴雨的层次。",
        content_json={
            "symbolic_layers": "1) 净化（洗去过去）/ 2) 悲伤（眼泪的延伸）/ 3) 重生（雨后春笋）/ 4) 转折（决定性事件）/ 5) 阻碍（被困+ 无法行动）",
            "rain_types_meaning": "毛毛雨（暧昧）+ 雷阵雨（突变）+ 暴雨（情绪决堤）+ 阴雨连绵（哀愁）+ 太阳雨（迷茫）+ 雪+ 冻雨（绝望）",
            "famous_uses": "《Singin' in the Rain》（爱情爆发） + 《Blade Runner》Roy Batty 死亡场（雨中独白） + 《Romeo+Juliet》 + 《唐人街》/ 《情书》岩井俊二雨景 / 《卧虎藏龙》",
            "scene_use_cases": "1) 雨中告白（来不及躲雨）/ 2) 雨中分手（情绪和雨水一起决堤）/ 3) 雨中追逐（视线模糊+ 滑倒）/ 4) 雨夜葬礼（最哀的场景）/ 5) 雨后重生（彩虹+ 阳光）",
            "anti_cliche": "不要纯写'下雨好浪漫'；要写雨真实的不便（衣服湿了+ 头发贴脸+ 鞋子进水+ 感冒）",
            "activation_keywords": ["雨", "净化", "悲伤", "Singin' in the Rain", "Blade Runner"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Rain_(symbolism)"), wiki("Singin'_in_the_Rain"), wiki("Blade_Runner")],
        tags=["motifs", "雨", "净化", "通用"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-snow-purity-death",
        name="意象：雪 / 纯洁与死亡",
        narrative_summary="雪=纯洁+ 死亡+ 时间静止+ 隔绝+ 死人化作雪人。东方='孤舟蓑笠翁，独钓寒江雪'+ 雪夜决战；西方=Snowman+ 圣诞+ Doctor Zhivago。",
        content_json={
            "symbolic_layers": "1) 纯洁（白色无暇）/ 2) 死亡（覆盖一切）/ 3) 时间静止（雪后无声）/ 4) 隔绝（出不了门）/ 5) 浪漫（雪中相恋）",
            "famous_uses": "《Doctor Zhivago》雪夜分别 / 《卧虎藏龙》竹林雪景 / 《冰雪奇缘》Elsa / 《血字研究》福尔摩斯 / 川端康成《雪国》/ 鲁迅《祝福》/ 《让子弹飞》雪夜",
            "scene_use_cases": "1) 雪夜决战（武侠经典） / 2) 雪中告别（不忍走/ 不舍） / 3) 雪掩埋（尸体/秘密/罪行）/ 4) 雪后早晨（重新开始）/ 5) 雪中迷路（生死考验）",
            "snow_types": "鹅毛大雪（极致美/ 极致危险）+ 细雪（飘渺）+ 暴风雪（绝望）+ 干雪（北方）+ 湿雪（南方）+ 雪后晴（重生）",
            "anti_cliche": "不要纯写'雪很美'；要写雪真实的不便（冷+ 滑+ 看不见+ 长时间静止的孤独）",
            "activation_keywords": ["雪", "纯洁", "死亡", "Doctor Zhivago", "雪国"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Snow_in_film"), wiki("Doctor_Zhivago_(film)"), wiki("Snow_Country")],
        tags=["motifs", "雪", "纯洁", "通用"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-mountain-permanence",
        name="意象：山 / 力量与永恒",
        narrative_summary="山=永恒+ 力量+ 修行+ 高远+ 不可逾越。东方=泰山五岳+ 不周山+ 蓬莱+ 武当；西方=Olympus 奥林匹斯+ Sinai 西奈山+ Himalaya。修行+ 朝圣+ 决战的最爱场所。",
        content_json={
            "symbolic_layers": "1) 永恒（时间静止）/ 2) 力量（无法移动）/ 3) 修行（远离世俗）/ 4) 高远（追求理想）/ 5) 不可逾越（阻挡）",
            "famous_uses": "Olympus（希腊众神）+ Sinai（摩西十诫）+ Himalaya（释迦）+ 泰山（封禅）+ 武当山（道教圣地）+ 华山（论剑）+ 不周山（共工触山）+ Tolkien Mt. Doom",
            "scene_use_cases": "1) 山顶论剑（武侠经典）/ 2) 山顶修行（仙侠经典）/ 3) 山中迷雾（神秘）/ 4) 山顶看日出（觉悟）/ 5) 山下回望（人生反思）",
            "mountain_types": "雪山（纯洁+ 死亡）+ 火山（爆发+ 毁灭）+ 圣山（朝圣+ 信仰）+ 锯齿山（险峻）+ 平顶山（祭坛）",
            "anti_cliche": "不要纯写'山雄壮'；要写攀登的真实困难（高反+ 落石+ 失温+ 视野模糊）",
            "activation_keywords": ["山", "永恒", "Olympus", "泰山", "武当"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Mount_Olympus"), wiki("Mount_Tai"), wiki("Mountains_in_Chinese_culture")],
        tags=["motifs", "山", "永恒", "通用"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-sea-vastness",
        name="意象：海 / 广阔与未知",
        narrative_summary="海=广阔+ 未知+ 母性+ 危险+ 自由。东方=东海龙宫+ 蓬莱仙岛+ 妈祖；西方=Odyssey 奥德赛+ Moby Dick+ 海贼王 One Piece。最适合冒险+ 流浪+ 面对自我。",
        content_json={
            "symbolic_layers": "1) 广阔（地平线）/ 2) 未知（深处怪物）/ 3) 母性（孕育生命）/ 4) 危险（淹死+ 风暴）/ 5) 自由（不属于任何国家）",
            "famous_uses": "Odyssey（漂流回家）+ Moby Dick（执念追猎）+ 老人与海+ 海底两万里+ 海贼王（One Piece）+ 山海经（东海）+ 水手十字军《Master and Commander》",
            "scene_use_cases": "1) 海上漂流（孤独+ 自我对话）/ 2) 海上风暴（命运转折）/ 3) 沉船（一切归零）/ 4) 港口告别（梦想出发）/ 5) 海边瓶中信（神秘联系）",
            "sea_states": "平静（暗藏危机）+ 微浪（节奏） + 大浪（决定） + 风暴（毁灭） + 死海（异象） + 内海（封闭） + 大洋（世界）",
            "anti_cliche": "不要纯写'海很美'；要写海真实的痛苦（晕船+ 长时间不能洗澡+ 食物变质+ 淡水告罄）",
            "activation_keywords": ["海", "广阔", "Odyssey", "Moby Dick", "海贼王"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Odyssey"), wiki("Moby-Dick"), wiki("The_Old_Man_and_the_Sea")],
        tags=["motifs", "海", "广阔", "通用"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-wind-change",
        name="意象：风 / 变化与消息",
        narrative_summary="风=变化+ 消息+ 自由+ 时间+ 命运。东方=春风+ 秋风+ 北风（《风》刘半农）；西方=Wind in the Willows+ Gone with the Wind+ Bob Dylan《Blowin' in the Wind》。",
        content_json={
            "symbolic_layers": "1) 变化（季节交替）/ 2) 消息（远方传来）/ 3) 自由（无形无界）/ 4) 时间（吹过岁月）/ 5) 命运（不可控）",
            "famous_uses": "Gone with the Wind（南方旧时代消逝）+ Wind in the Willows（自由）+ 《风云雷电》武侠+ 《一帘幽梦》琼瑶+ Bob Dylan《Blowin' in the Wind》+ 王家卫《阿飞正传》",
            "scene_use_cases": "1) 风吹倒帽子（小事预兆大事）/ 2) 风吹散文件（机密外泄）/ 3) 风吹起红裙（爱情萌芽）/ 4) 风吹白发（时间流逝）/ 5) 风吹关门（被困+ 转折）",
            "wind_types": "微风（暧昧）+ 大风（决定） + 暴风（毁灭）+ 北风（萧瑟）+ 南风（温柔）+ 西风（悲秋）+ 东风（带来转机）+ 龙卷风（天灾）",
            "anti_cliche": "不要纯写'风吹动什么'；让风成为信息载体（远处的喊声+ 烧焦的味道+ 海腥）",
            "activation_keywords": ["风", "变化", "Gone with the Wind", "Blowin' in the Wind"],
        },
        source_type="research_agent", confidence=0.9,
        source_citations=[wiki("Gone_with_the_Wind"), wiki("Blowin'_in_the_Wind")],
        tags=["motifs", "风", "变化", "通用"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-butterfly-transformation",
        name="意象：蝴蝶 / 蜕变与灵魂",
        narrative_summary="蝴蝶=蜕变+ 灵魂+ 短暂美好+ 自由+ 转世。东方=庄子梦蝶+ 梁祝化蝶；西方=Psyche 灵魂+ Madame Butterfly+ 《沉默的羔羊》。最深的哲学意象之一。",
        content_json={
            "symbolic_layers": "1) 蜕变（毛毛虫→蝴蝶）/ 2) 灵魂（古希腊 psyche=蝴蝶）/ 3) 短暂美好（生命极短）/ 4) 转世（中国民间）/ 5) 自由（飞舞不定）",
            "famous_uses": "庄子梦蝶（哲学）+ 梁祝化蝶（爱情）+ Madame Butterfly（牺牲）+ 《沉默的羔羊》（暗黑）+ 《蝴蝶效应》（命运） + 蝶恋花（词牌）+ Murakami《诺威森林》",
            "scene_use_cases": "1) 蝴蝶停在主角肩上（异象）/ 2) 蝴蝶引路（神秘指引）/ 3) 蝴蝶死亡（短暂感）/ 4) 化蝶（爱情升华）/ 5) 蝶蛹孵化（蜕变成长）",
            "butterfly_species_meaning": "凤蝶（华丽）+ 蛱蝶（智慧）+ 黑蝶（死亡）+ 白蝶（魂灵）+ 蓝蝶（远方）",
            "anti_cliche": "不要纯写'美丽蝴蝶'；让蝴蝶有具体细节（翅膀有损+ 花蜜沾胸+ 风雨打湿+ 短暂的飞行）",
            "activation_keywords": ["蝴蝶", "蜕变", "庄子梦蝶", "Madame Butterfly", "化蝶"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Butterfly_in_Chinese_culture"), wiki("Madama_Butterfly"), wiki("Zhuangzi")],
        tags=["motifs", "蝴蝶", "蜕变", "通用"],
    ),

    # ---------- emotion_arcs (4) ----------
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="emo-arc-self-acceptance-loneliness",
        name="情感弧线：自我怀疑 → 接纳孤独 → 平静",
        narrative_summary="主角从害怕孤独+ 拼命融入+ 失败+ 转向接纳孤独+ 在独处中找到力量。是文艺片+ 成长小说+ 内心戏的核心弧。",
        content_json={
            "five_stages": "1) 拼命融入（讨好+ 假装合群）/ 2) 失败崩溃（被排挤+ 放弃自我）/ 3) 短暂逃避（独处但痛苦）/ 4) 转折（从一本书/一次散步/一个陌生人发现独处的美）/ 5) 接纳（享受独处+ 但不再害怕回到人群）",
            "key_dialogue": "1) 拼命：'我是不是太奇怪了？'/ 2) 失败：'其实我也不喜欢他们'/ 3) 转折：'原来我一个人也可以'/ 4) 接纳：'我喜欢现在的自己'",
            "famous_examples": "《Wild》荒野女主+ 《Eat Pray Love》+ 《一个人的朝圣》+ 王家卫《阿飞正传》+ 《Little Women》Jo",
            "anti_cliche": "不要纯写'独处后突然完美'；让接纳过程缓慢+ 反复（有时还是想找人陪伴+ 但能区分'渴望'和'必需'）",
            "activation_keywords": ["孤独", "接纳", "自我", "Wild", "成长弧"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Wild_(2014_film)"), llm_note("Cheryl Strayed《Wild》、Elizabeth Gilbert《Eat Pray Love》成长弧综合")],
        tags=["emotion_arcs", "孤独", "接纳", "通用"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="emo-arc-faith-doubt-rebuild",
        name="情感弧线：怀疑 → 坚信 → 破碎 → 重建（信仰弧）",
        narrative_summary="对宗教/理想/事业/爱情的信仰从坚信+ 到面对反证+ 信仰崩塌+ 经历虚无+ 重建更深的信仰（不再是天真的）。是宗教文学+ 成长小说+ 哲学小说的核心弧。",
        content_json={
            "five_stages": "1) 坚信期（天真的信徒，把信仰当真理）/ 2) 反证期（遇到不公+ 反例）/ 3) 怀疑期（动摇+ 痛苦）/ 4) 崩塌期（信仰彻底破碎，进入虚无）/ 5) 重建期（带着裂痕的更深信仰，知道不完美但仍选择相信）",
            "key_dialogue": "1) 坚信：'X 一定是对的' / 2) 反证：'但是为什么...?' / 3) 怀疑：'我不知道还能不能相信' / 4) 崩塌：'什么都没有意义' / 5) 重建：'我知道 X 不完美，但我还是选择相信'",
            "famous_examples": "《卡拉马佐夫兄弟》Alyosha / 《Pi 的奇幻漂流》/ 《Silence》遠藤周作 / 《Crime and Punishment》Raskolnikov / 《老人与海》Santiago / 《活着》福贵",
            "anti_cliche": "不要纯写'信仰回来变成超人'；让重建后的信仰带着伤痕和谦卑，而不是一种新的优越感",
            "activation_keywords": ["信仰", "怀疑", "重建", "Karamazov", "Silence"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("The_Brothers_Karamazov"), wiki("Silence_(novel)"), wiki("Crime_and_Punishment")],
        tags=["emotion_arcs", "信仰", "怀疑", "通用"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="emo-arc-comrade-grief",
        name="情感弧线：同伴情谊 → 失去 → 哀悼 → 继承遗志",
        narrative_summary="一群同伴并肩战斗+ 一员死亡+ 全员哀悼+ 化悲痛为力量继续前进。是战争小说+ 武侠+ 末日+ 末路英雄文的核心弧。",
        content_json={
            "five_stages": "1) 携手期（同伴情谊+ 互相托付） / 2) 危机期（任务+ 战斗+ 一员受伤）/ 3) 死亡期（一员牺牲，主角无法救回） / 4) 哀悼期（葬礼+ 内心崩溃+ 全队沉默）/ 5) 继承期（剩下的人化悲痛为力量+ 继承死者遗志）",
            "key_dialogue_moments": "1) 携手：'我们一起活下去' / 2) 危机：'我会保护你' / 3) 死亡：'你不能死...!' / 4) 哀悼：'对不起...' / 5) 继承：'X 没看到的世界，我们替他/她看'",
            "famous_examples": "《Saving Private Ryan》战友牺牲 / 《Game of Thrones》Robb Stark+Talisa+Catelyn 红色婚礼+全队反应 / 《指环王》Boromir 牺牲后 Aragorn 继承 / 《琅琊榜》卫峥案后众人态度",
            "anti_cliche": "不要纯写'继承后变得更强'；让哀悼过程拖很久（甚至到结尾）+ 主角时不时想起死者+ 不能完美继承",
            "activation_keywords": ["同伴情谊", "牺牲", "哀悼", "Saving Private Ryan", "Boromir"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Saving_Private_Ryan"), wiki("Red_Wedding"), llm_note("战争片+ 末路英雄综合")],
        tags=["emotion_arcs", "战友", "哀悼", "通用"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="emo-arc-first-love-loss-healing",
        name="情感弧线：初恋 → 失去 → 疗愈 → 重新去爱",
        narrative_summary="第一次真正的爱情+ 因外因（死亡/搬家/家人反对/误会）失去+ 长时间无法走出+ 慢慢疗愈+ 最终重新去爱（可能是同一个人也可能不是）。",
        content_json={
            "five_stages": "1) 初恋期（一切都是新的，世界是粉色的）/ 2) 失去期（突然的分别 / 死亡 / 误会 / 距离）/ 3) 否认期（拒绝接受 / 反复回忆 / 自责） / 4) 疗愈期（慢慢能看到其他人 / 还在伤痛但不痛苦）/ 5) 重新去爱期（决定再爱 / 知道会再痛但仍选择）",
            "key_dialogue": "1) 初恋：'我从来没这样过' / 2) 失去：'怎么会这样' / 3) 否认：'我不能没有他/她' / 4) 疗愈：'今天我没想他/她' / 5) 重新去爱：'我准备好了'",
            "famous_examples": "《情书》岩井俊二（初恋记忆+ 写信疗愈） / 《Eternal Sunshine》（失忆和重逢） / 《One Day》Emma+Dexter / 《Notebook》/ 《La La Land》",
            "anti_cliche": "不要纯写'忘记前任完美投入新恋情'；让前任在新恋情中阴影还在+ 偶尔提起+ 但能继续生活",
            "activation_keywords": ["初恋", "失去", "疗愈", "情书", "La La Land"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Love_Letter_(1995_film)"), wiki("Eternal_Sunshine_of_the_Spotless_Mind"), wiki("La_La_Land")],
        tags=["emotion_arcs", "初恋", "疗愈", "通用"],
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
