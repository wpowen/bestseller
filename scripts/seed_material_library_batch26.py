"""
Batch 26: Narrative voices and prose styles — 硬汉派 / 抒情派 / 极简主义 /
新闻体 / 魔幻散文 / 黑色幽默 / 后现代解构 / 元小说 / 流意识 / 残酷青春.
Activates prose register, sentence rhythm, narrative texture.
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
    # 硬汉派
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="prose-hardboiled",
        name="硬汉派叙事（Hardboiled）",
        narrative_summary="美国 30-50 年代由 Hammett / Chandler 创立。冷酷第一人称 + 短句子 + 城市夜雨 + 玩世不恭。"
                          "动词主导 / 形容词削减 / 警句式比喻。"
                          "适用都市悬疑 / 黑帮 / 反英雄 / 私家侦探题材。",
        content_json={
            "core_traits": "1) 第一人称冷叙 / 2) 短句 + 强动词 / 3) 比喻警句化（'她比清晨的子弹更冷'）/ 4) 不加形容词的暴力 / 5) 主角反英雄玩世不恭 / 6) 城市黑暗潮湿",
            "founding_masters": "Dashiell Hammett《马耳他之鹰》/ Raymond Chandler《漫长的告别》/ James M. Cain《邮差总按两次铃》/ Mickey Spillane",
            "voice_examples": "'她有一双眼睛，可以让你忘了自己叫什么。' / '雨打在我帽檐上像欠的债。' / '我接了案子，案子接了我。'",
            "imitation_recipe": "1) 用第一人称 / 2) 平均句长 < 12 字 / 3) 90% 动词主导 / 4) 形容词不超过名词数量 / 5) 比喻一定带物质暴力气息 / 6) 主角嘲讽自己同时嘲讽世界",
            "famous_chinese_imitators": "王朔早期 / 何伟（一定程度）/ 王小波（混合型）",
            "narrative_use": "都市悬疑 / 警匪 / 黑色幽默 / 反英雄主角 / 私家侦探小说",
            "activation_keywords": ["硬汉派", "Hardboiled", "Chandler", "马耳他之鹰", "私家侦探", "反英雄", "短句"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("硬汉派", ""), llm_note("Hardboiled fiction")],
        tags=["文体", "硬汉派", "通用"],
    ),
    # 极简主义
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="prose-minimalism-carver",
        name="极简主义（Minimalism / Carver-style）",
        narrative_summary="Carver / Hemingway 冰山理论。少即是多 / 删去 70% / 留下水面之上的 30%。"
                          "短句 + 留白 + 不解释 + 让读者填空。"
                          "适用文艺 / 现实主义 / 都市悲歌 / 离婚故事。",
        content_json={
            "core_principles": "1) 删形容词副词 / 2) 不解释心理直接写动作 / 3) 重要信息留白让读者推断 / 4) 对话承担叙事 / 5) 结尾不收束 / 6) 普通人琐碎日常",
            "founding_masters": "Raymond Carver《大教堂》《当我们谈论爱情》/ Hemingway 冰山理论 / Amy Hempel / Tobias Wolff / Ann Beattie",
            "iceberg_theory": "Hemingway: 文字是水面 1/8 / 7/8 在水下 / 删除知道的细节读者依然能感受到 / 重在不写什么",
            "carver_signature": "蓝领工人 / 酒鬼 / 婚姻破裂 / 失业 / 沉默饭桌 / 突然出现的电话或来客 / 不解决",
            "imitation_recipe": "1) 第一稿写完删 30% 形容词 / 2) 删所有'感觉''觉得''仿佛' / 3) 心理活动改成动作（紧张 → 反复看表）/ 4) 结尾停在动作中 / 5) 对话不说重要的事，重要的事在话外",
            "chinese_examples": "余华《活着》前期克制 / 苏童《妻妾成群》/ 朱文 / 早期阿乙",
            "narrative_use": "文艺现实 / 离婚故事 / 工人题材 / 短篇精品 / 长篇章节切片",
            "activation_keywords": ["极简主义", "Carver", "冰山理论", "Hemingway", "留白", "删除", "蓝领"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("极简主义文学", ""), llm_note("Carver / Hemingway")],
        tags=["文体", "极简主义", "通用"],
    ),
    # 抒情诗体
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="prose-lyrical-poetic",
        name="抒情诗体（Lyrical Prose）",
        narrative_summary="散文走在诗的边界。情感浓度高 / 意象密集 / 节奏起伏 / 长短句交替。"
                          "中文代表：鲁迅《野草》/ 沈从文《边城》/ 张爱玲 / 余光中。"
                          "适用文艺 / 言情 / 怀旧 / 爱情高潮。",
        content_json={
            "core_traits": "1) 比喻密度高 / 2) 意象成串（月 / 风 / 灯 / 影）/ 3) 节奏感（长短句交错 + 重复 + 排比）/ 4) 情感外溢但不矫情 / 5) 单段可朗诵",
            "chinese_masters": "鲁迅《野草》/ 沈从文《边城》/ 张爱玲《金锁记》/ 萧红《呼兰河传》/ 林海音 / 简媜 / 三毛 / 余华《在细雨中呼喊》",
            "western_masters": "Fitzgerald《了不起的盖茨比》末段 / Marquez 抒情段落 / 川端康成 / Virginia Woolf",
            "rhythm_recipe": "长（描写）+ 短（情绪）+ 长（回忆）+ 一句独立段（点睛）/ 排比三 / 反复一个词 / 收在意象上不收在解释",
            "imagery_chains": "月 + 灯 + 影 + 镜（夜的孤独）/ 风 + 海 + 沙 + 帆（漂泊）/ 雪 + 梅 + 烛 + 钟（怀旧）",
            "danger_zones": "过于堆砌 / 矫情 / 失去叙事推进 / 形容词雪崩",
            "narrative_use": "文艺主线 / 爱情高潮回忆 / 旅行散文 / 怀旧章节 / 序章定调",
            "activation_keywords": ["抒情", "诗体", "鲁迅", "沈从文", "张爱玲", "意象", "节奏", "排比"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("抒情散文", ""), llm_note("中文抒情传统")],
        tags=["文体", "抒情", "通用"],
    ),
    # 新闻体
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="prose-new-journalism",
        name="新闻体 / 非虚构（New Journalism）",
        narrative_summary="60 年代美国 Tom Wolfe / Truman Capote 创立。"
                          "用小说技法写真实事件 / 现场感 + 对话还原 + 人物心理 + 主观介入。"
                          "适用都市纪实 / 报告文学 / 商业纪录 / 社会案件。",
        content_json={
            "four_techniques_wolfe": "1) 场景一幕一幕推进 / 2) 完整对话还原（不只是引语）/ 3) 第三人称视角进入人物内心 / 4) 状态细节（衣着 / 物件 / 习惯）",
            "founding_masters": "Truman Capote《冷血》/ Tom Wolfe《电冷大酒桶酸性试验》/ Norman Mailer / Joan Didion / Hunter Thompson 贡品式 / 何伟《江城》",
            "chinese_practitioners": "李海鹏 / 柴静 / 罗永浩（部分）/ 刘瑜 / 何伟 / 张悦然非虚构",
            "imitation_recipe": "1) 现场细节像录像 / 2) 对话直接引述（用 \"\"）/ 3) 主角心理直接进入 / 4) 时间地点精确 / 5) 数据 + 实例混合 / 6) 作者主观见解可加",
            "structure_patterns": "倒金字塔（重要先说）/ 时间线（事件序）/ 现场 + 回顾 + 现场（三幕）/ 人物侧写（一人一章）",
            "narrative_use": "都市纪实悬案 / 商战写真 / 社会议题 / 重生主角写自传 / 新闻人物穿越",
            "activation_keywords": ["新闻体", "非虚构", "Tom Wolfe", "Capote", "冷血", "何伟", "现场感", "对话还原"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("新新闻主义", ""), llm_note("New Journalism")],
        tags=["文体", "新闻体", "通用"],
    ),
    # 魔幻散文
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="prose-magical-realism-style",
        name="魔幻散文体（Magical Prose）",
        narrative_summary="马尔克斯式语言。日常 + 神迹混在一句 / 时间松动 / 数字夸张精确 / 全知叙事 + 家族史 + 宿命预言。"
                          "适用奇幻 / 家族史 / 命运叙事 / 文艺仙侠。",
        content_json={
            "core_traits": "1) 神迹日常化（'下了 5 年 11 个月零 2 天的雨'）/ 2) 数字精确夸张 / 3) 时间循环 + 预言 / 4) 全知叙事（俯瞰几代人）/ 5) 家族姓氏重复 / 6) 一段几句话浓缩半生",
            "founding_masters": "Marquez《百年孤独》《霍乱时期的爱情》/ Borges《虚构集》/ Cortázar《跳房子》/ Allende《幽灵之家》",
            "chinese_imitators": "莫言《檀香刑》《红高粱》/ 阿来《尘埃落定》/ 苏童《罂粟之家》/ 陈忠实《白鹿原》",
            "imitation_recipe": "1) 开篇'多年以后……' / 2) 时间动 5-50 年 / 3) 数字精确（'47 颗' / '3 年 2 个月零 17 天'）/ 4) 现实事件 + 一个神迹（不解释，自然发生）/ 5) 家族史几代人浓缩 / 6) 反复出现的物件成符号",
            "famous_openings": "'多年以后，奥雷里亚诺·布恩迪亚上校面对行刑队，准会想起父亲带他去见识冰块的那个遥远的下午。'",
            "narrative_use": "家族史 / 古风奇幻 / 仙侠（《青蛇》风格）/ 命运叙事 / 历史架空",
            "activation_keywords": ["魔幻现实", "马尔克斯", "百年孤独", "莫言", "白鹿原", "多年以后", "全知"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("魔幻现实主义", ""), llm_note("Marquez 等")],
        tags=["文体", "魔幻", "通用"],
    ),
    # 意识流
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="prose-stream-of-consciousness",
        name="意识流（Stream of Consciousness）",
        narrative_summary="Joyce / Woolf / Faulkner 创立。直接呈现人物未筛选的思绪 + 感官 + 记忆 + 联想。"
                          "无标点长句 + 跳跃逻辑 + 多重时空叠加。"
                          "适用心理悬疑 / 文艺 / 精神病题材 / 高潮独白。",
        content_json={
            "core_traits": "1) 直接呈现内心（无'他想'）/ 2) 跳跃联想（钥匙 → 童年厨房 → 母亲气味）/ 3) 时态混乱 / 4) 标点稀疏 / 5) 感官压倒理性 / 6) 句子可达数页",
            "founding_masters": "Joyce《尤利西斯》《为芬尼根守灵夜》/ Virginia Woolf《达洛维夫人》《到灯塔去》/ Faulkner《喧哗与骚动》/ Proust《追忆似水年华》",
            "chinese_examples": "王蒙《组织部新来的年轻人》/ 刘以鬯《酒徒》（早期意识流）/ 西西部分小说 / 残雪",
            "techniques": "自由间接引语 / 内心独白 / 蒙太奇（电影手法借入）/ 感觉印象主义 / 时间叠层（现在 + 童年 + 想象同时）",
            "famous_passage": "Molly Bloom 在《尤利西斯》最后的 4391 词无标点独白 / 'yes' 结尾",
            "imitation_recipe": "1) 选高情绪节点（昏厥 / 失恋 / 重大决定前一秒）/ 2) 写感官（颜色 / 气味 / 触觉）/ 3) 一个感官触发记忆 / 4) 记忆触发联想 / 5) 删标点保留少量 / 6) 句子越长越好不超过 1 页",
            "narrative_use": "文艺主角心理高潮 / 精神病视角 / 临死回光 / 重大决策前 / 失恋崩溃",
            "activation_keywords": ["意识流", "Joyce", "尤利西斯", "Woolf", "Faulkner", "内心独白", "跳跃联想"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("意识流", ""), llm_note("意识流文学")],
        tags=["文体", "意识流", "通用"],
    ),
    # 黑色幽默
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="prose-black-humor",
        name="黑色幽默（Black Humor）",
        narrative_summary="残酷现实 + 玩笑语调。Heller《第二十二条军规》/ Vonnegut《五号屠场》/ 王小波。"
                          "用荒诞讥笑揭露悲剧。死亡 + 战争 + 体制说得像段子。"
                          "适用反战 / 体制批判 / 都市底层。",
        content_json={
            "core_traits": "1) 严肃议题（死亡 / 战争 / 极权）调侃化 / 2) 逻辑悖论戏弄理性 / 3) 荒诞场景写实笔触 / 4) 主角茫然但旁观者读出悲伤 / 5) 重复造梗（'so it goes'）",
            "founding_masters": "Joseph Heller《第二十二条军规》/ Kurt Vonnegut《五号屠场》《冠军早餐》/ Thomas Pynchon《V》/ Donald Barthelme",
            "chinese_masters": "王小波《黄金时代》《白银时代》/ 王朔《一半是海水一半是火焰》/ 老舍《我这一辈子》(部分) / 莫言《酒国》",
            "catch22_logic": "想停飞必须证明自己疯 / 但要求停飞证明你理性 / 你就不能停飞 / 完美悖论 = 体制荒谬本质",
            "imitation_recipe": "1) 选最悲惨场景（葬礼 / 战场 / 监狱）/ 2) 主角语调如说闲事 / 3) 加一个荒谬规则或台词 / 4) 反复出现的口头禅 / 5) 配角更荒谬作为放大器 / 6) 不点破悲哀 / 让读者自己掉进笑后哭",
            "narrative_use": "反战 / 体制批判 / 都市底层心酸 / 喜剧底色悲剧（《无问西东》风格）/ 王小波式时空穿越",
            "activation_keywords": ["黑色幽默", "第二十二条军规", "Heller", "Vonnegut", "王小波", "荒诞", "悖论"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("黑色幽默", ""), llm_note("Black Humor")],
        tags=["文体", "黑色幽默", "通用"],
    ),
    # 后现代解构
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="prose-postmodern-deconstruction",
        name="后现代解构（Postmodern）",
        narrative_summary="拒绝大叙事 / 碎片化 / 拼贴 / 元小说 / 互文 / 戏仿。"
                          "Pynchon / 卡尔维诺 / 博尔赫斯 / 余华先锋时期 / 马原。"
                          "适用文艺实验 / 元小说 / 反类型 / 拼贴式悬疑。",
        content_json={
            "core_techniques": "1) 元小说（叙述者承认自己在写）/ 2) 拼贴（菜单 / 信件 / 维基词条混入）/ 3) 戏仿现有文体 / 4) 碎片化（无线性时间）/ 5) 互文（致敬 / 引用 / 反讽他作）/ 6) 不可靠叙事者放大",
            "founding_masters": "Borges《小径分叉的花园》/ Calvino《如果在冬夜，一个旅人》/ Pynchon《V》《拍卖第 49 号》/ Barth / Auster《纽约三部曲》",
            "chinese_avant_garde": "马原《冈底斯的诱惑》/ 余华先锋（《现实一种》《世事如烟》）/ 残雪 / 苏童先锋时期 / 孙甘露",
            "metafiction_examples": "Calvino: '此刻你正在阅读卡尔维诺的小说《如果在冬夜，一个旅人》' / 余华叙述者跳出来评论",
            "intertextuality": "Eco《玫瑰的名字》混入亚里士多德诗学 / 《尤利西斯》对应《奥德赛》/ 同人文化广义后现代",
            "imitation_recipe": "1) 加入文本外文本（招聘启事 / 病历 / 报纸）/ 2) 叙述者偶尔承认是小说 / 3) 时间段落颠倒 / 4) 章节用奇怪标号（α / 章 ½）/ 5) 戏仿一种已有文体（侦探 / 圣经 / 教科书）",
            "narrative_use": "实验文学 / 文艺类型挑战 / 元写作 / 致敬经典反讽 / 拼贴悬疑",
            "activation_keywords": ["后现代", "元小说", "Borges", "Calvino", "Pynchon", "拼贴", "互文", "戏仿"],
        },
        source_type="llm_synth", confidence=0.90,
        source_citations=[wiki("后现代主义文学", ""), llm_note("后现代")],
        tags=["文体", "后现代", "通用"],
    ),
    # 残酷青春
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="prose-cruel-youth",
        name="残酷青春（Cruel Youth）",
        narrative_summary="日本太宰治《人间失格》开启 / 中国 80-90 后郭敬明 / 安妮宝贝 / 七堇年。"
                          "颓废 + 自伤 + 美 + 死亡冲动 + 不被理解。短句 + 第一人称 + 现在时为主。"
                          "适用青春疼痛 / 校园虐恋 / 文艺转向。",
        content_json={
            "core_traits": "1) 第一人称浸入式独白 / 2) 自我厌弃 + 美丽形象（病美人）/ 3) 死亡 / 自伤幻想常驻 / 4) 短促感性句 / 5) 物的恋物（红线 / 玻璃 / 烟）/ 6) 大量比喻和气味",
            "japanese_origins": "太宰治《人间失格》《斜阳》/ 三岛由纪夫《金阁寺》/ 川端康成《雪国》/ 村上龙《69》",
            "chinese_practitioners": "郭敬明《幻城》《悲伤逆流成河》/ 安妮宝贝《告别薇安》《素年锦时》/ 七堇年《被窝是青春的坟墓》/ 落落 / 春树",
            "iconic_imagery": "玻璃碎片 / 红线 / 老式电话亭 / 雨夜 / 长发 / 锁骨 / 烟雾 / 火车 / 海边 / 旧站台",
            "danger_zones": "过度矫情 / 落入 45 度仰望天空梗 / 缺乏现实质感 / 自我感动",
            "imitation_recipe": "1) 第一人称现在时 / 2) 主角自我厌弃但美 / 3) 物件恋物（一个反复出现）/ 4) 比喻倾向死亡 / 病 / 美 / 5) 不直接说事件，从感觉切入 / 6) 短促分行 + 长段独白交错",
            "evolved_form": "当代女频虐恋（《被嫌弃的松子》风格）/ 文艺现实（双雪涛）/ 影视化转 IP",
            "narrative_use": "青春疼痛 / 校园虐恋 / 同性 / 抑郁主角 / 文艺转向章节",
            "activation_keywords": ["残酷青春", "太宰治", "人间失格", "郭敬明", "安妮宝贝", "七堇年", "病美人", "颓废"],
        },
        source_type="llm_synth", confidence=0.90,
        source_citations=[wiki("青春小说", ""), llm_note("青春文学")],
        tags=["文体", "青春", "通用"],
    ),
    # 网文文风
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="prose-webnovel-style",
        name="网文文风（中国网文标准式）",
        narrative_summary="起点 / 番茄 / 17K 形成的标准网文文风。"
                          "短句短段 + 高频对话 + 爽点密集 + 数字化系统 + 1500-3000 字一节奏点。"
                          "和传统文学最大差异：信息密度低 / 重复确认 / 节奏前置。",
        content_json={
            "structural_traits": "1) 段落 1-3 句即换行 / 2) 对话占比 30-50% / 3) 章末必有钩子或爽点 / 4) 每 1500-3000 字一个完成节奏点 / 5) 章首点回上章 + 当前事件 / 6) 高潮章前先憋三章",
            "voice_traits": "1) 短句 + 现代口语 / 2) 主角内心独白多（'我必须……'）/ 3) 反派蠢化对话（让爽点立得住）/ 4) 配角夸张反应（眼珠瞪到地上）/ 5) 旁观者 NPC 弹幕式 / 6) 数字化系统提示框",
            "pacing_norms": "金手指开局 1 章内 / 第一次打脸 3 章内 / 第一卷高潮 30 章 / 卷末必须留新坑",
            "info_density_lower": "网文相比纯文学信息密度低（重复确认 / 反复强调）/ 这是阅读连载需要 / 不是缺点",
            "language_dont": "避免长句套从句 / 避免文言典故 / 避免大段景物描写 / 避免哲学思辨独白",
            "language_do": "短句 / 强动词 / 大白话 / 偶尔玩梗 / 系统冷笑话 / 反派不甘话 / 主角爽完一句标题党",
            "leveling_progression": "境界提升时画系统提示框 + 弹幕反应 + 主角内心爽 + 配角崇拜 = 四件套",
            "narrative_use": "起点系男频 / 番茄爽文 / 女频网文 / 系统流 / 重生流 / 都市修仙",
            "activation_keywords": ["网文", "起点", "番茄", "短句短段", "爽点", "金手指", "钩子", "系统提示"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[wiki("中国网络小说", ""), llm_note("网文写作规范")],
        tags=["文体", "网文", "通用"],
    ),
    # 电报体（极速叙事）
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="prose-telegraphic-fast",
        name="电报体 / 极速叙事",
        narrative_summary="句子省略主语 / 跳过过程直奔结果 / 大量动词 / 无心理铺垫。"
                          "适用动作戏 / 战斗高潮 / 紧迫追逃 / 倒计时章。"
                          "中文网文打斗段落常用。",
        content_json={
            "core_traits": "1) 主语省略 / 2) 动词连缀 / 3) 不写心理只写动作 / 4) 短句一句一段 / 5) 时间感被拉紧 / 6) 关键瞬间慢镜头（突然加细节）",
            "techniques": "动词链（'拔剑、转身、刺出、收回'）/ 一句一段加节奏感 / 数字加压（'三秒'）/ 物件特写（突然慢镜）/ 动作 + 反作用动作交错",
            "examples_recipe": "刀光闪。/ 血飞。/ 他后退一步。/ 第二刀已到。",
            "appropriate_scenes": "战斗高潮 / 追逃 / 爆炸前 5 秒 / 刺杀 / 抢救室 / 高考交卷前 / 求婚倒计时",
            "inappropriate_scenes": "情感戏 / 风景 / 心理独白 / 文艺抒情 / 情节铺垫",
            "blending_with_lyrical": "战斗中突然慢下来一句长句（'她看见他眼里映着雪'）/ 急 + 缓的对比制造张力顶峰",
            "narrative_use": "战斗章节 / 追逃 / 救人时刻 / 倒计时 / 电影感打斗",
            "activation_keywords": ["电报体", "极速", "动作戏", "短句", "动词链", "倒计时", "慢镜头"],
        },
        source_type="llm_synth", confidence=0.89,
        source_citations=[wiki("叙事节奏", ""), llm_note("Fast prose pacing")],
        tags=["文体", "极速", "通用"],
    ),
    # 哥特文体
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="prose-gothic-style",
        name="哥特文体（Gothic）",
        narrative_summary="18 世纪英国诞生。古堡 + 鬼影 + 暴风雨 + 美女 + 黑暗秘密。"
                          "Walpole 开端 / Mary Shelley / Poe / Bronte 姐妹 / Lovecraft 推到宇宙恐怖。"
                          "适用悬疑 / 灵异 / 爱情悲剧 / 西式恐怖。",
        content_json={
            "core_motifs": "古堡 / 修道院废墟 / 暴风雨夜 / 蜡烛 / 长廊 / 镜子 / 画像凝视 / 地下密室 / 隐藏血亲 / 受困纯洁少女 / 阴郁贵族男主",
            "founding_works": "Walpole《奥特兰托堡》1764 / Ann Radcliffe《尤多尔弗的奥秘》/ Mary Shelley《弗兰肯斯坦》/ Polidori《吸血鬼》/ Bronte《呼啸山庄》",
            "evolved_branches": "美国南方哥特（Faulkner / Flannery O'Connor / 麦卡锡）/ 心理哥特（James《拧紧螺丝》）/ 宇宙恐怖（Lovecraft）/ 当代浪漫哥特（《暮光之城》）",
            "language_features": "1) 形容词稠密 / 2) 古旧词汇（thee thou 翻成中文用文言）/ 3) 长句套句 / 4) 感官恐惧（声 / 影 / 寒意）/ 5) 反复'仿佛''或许''似乎'制造未确定感",
            "psychology": "压抑 / 被禁锢 / 罪恶感 / 家族诅咒 / 性压抑 / 死亡冲动 / 双重人格",
            "imitation_recipe": "1) 设定老房子 + 雷雨夜 / 2) 至少一个隐藏房间 / 3) 主角女青年单独 / 4) 男主黑发苍白阴郁 / 5) 一封旧信揭开秘密 / 6) 火烧或洪水大结局",
            "narrative_use": "西式悬疑 / 老宅鬼故事 / 古堡爱情 / 吸血鬼 / 心理惊悚",
            "activation_keywords": ["哥特", "古堡", "暴风雨", "弗兰肯斯坦", "呼啸山庄", "Lovecraft", "吸血鬼", "暗黑浪漫"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[wiki("哥特小说", ""), llm_note("Gothic fiction")],
        tags=["文体", "哥特", "通用"],
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
