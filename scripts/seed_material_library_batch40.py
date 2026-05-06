"""
Batch 40: Cross-cultural symbolism + emotion arcs depth.
Universal motifs (sun/moon/blood/scar/door/mirror)
+ deep emotional progressions (trust→betrayal→forgiveness etc).
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
    # 跨文化 - 月亮
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-moon",
        name="月亮（Moon / 跨文化）",
        narrative_summary="文学最常见意象之一。"
                          "中西通用 + 多重含义。"
                          "思念 + 阴性 + 时间 + 命运。",
        content_json={
            "chinese_meanings": "中秋团圆 / '举头望明月，低头思故乡'李白 / 嫦娥奔月 / 月老红线 / 阴柔代表 / '月满则亏'命理观",
            "western_meanings": "月神 Artemis / Luna / Diana 狩猎 / 月圆变狼人 / 月亮 = 疯狂（'lunatic'词源）/ 月是女性 / 周期 / 神秘",
            "literary_uses": "1) 思念远方亲人 / 2) 时间流逝 / 3) 守夜场景 / 4) 命运无常（月有阴晴圆缺）/ 5) 月光下表白 / 6) 月光揭示真相 / 7) 月光下决战（武侠）",
            "famous_lines": "李白'举头望明月' / 苏轼'明月几时有' / Wordsworth 月诗 / 海明威《老人与海》月光",
            "subgenre_uses": "仙侠（月华吸纳灵气 / 月圆突破）/ 武侠（月夜决斗）/ 言情（月下表白）/ 都市（夜归人月光下沉思）/ 玄幻（月族 / 月神信仰）",
            "modern_subversions": "1) 月亮是诅咒（《月亮和六便士》）/ 2) 月亮是怪物（《Moon》韩国电影）/ 3) 双月（外星 / 末世预兆）",
            "activation_keywords": ["月亮", "Moon", "嫦娥", "Artemis", "月圆", "月光", "思念", "中秋"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("跨文化月亮意象")],
        tags=["通用", "意象", "跨文化"],
    ),
    # 跨文化 - 太阳
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-sun",
        name="太阳（Sun / 跨文化）",
        narrative_summary="光明 + 力量 + 王权 + 阳性。"
                          "中西通用 + 多重含义。"
                          "对立月亮的阴性。",
        content_json={
            "chinese_meanings": "羿射九日 / 后羿射日 / 阳气 / 男性力量 / 阳极 / 帝王自比'真龙天子'但不是太阳",
            "western_meanings": "太阳神 Apollo / 法老 = 太阳神之子 / Helios / 路易十四'太阳王' / 印加神 Inti / 阿兹特克太阳神 = 王权象征",
            "literary_uses": "1) 新生 + 希望（黎明）/ 2) 力量 + 燃烧 / 3) 王权 + 至高 / 4) 真理（阳光下无秘密）/ 5) 烈日 = 残酷（沙漠）/ 6) 日落 = 终结",
            "famous_works": "《老人与海》'太阳照常升起'/ Camus《局外人》正午太阳 / 中国《淮南子》羿射九日 / 印加文明 + 玛雅文明 / 浪漫主义诗中阳光",
            "subgenre_uses": "玄幻（太阳真火 / 三足金乌）/ 仙侠（吸纳日精 / 阳气修炼）/ 末世（红色血日 / 末日预兆）/ 西方奇幻（Apollo 神族）",
            "modern_subversions": "1) 太阳是死亡（撒哈拉沙漠暴晒）/ 2) 黑日（日食末日）/ 3) 双日（外星）/ 4) 死亡之星",
            "activation_keywords": ["太阳", "Sun", "Apollo", "羿射九日", "阳气", "光明", "王权", "黎明"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("跨文化太阳意象")],
        tags=["通用", "意象", "跨文化"],
    ),
    # 跨文化 - 血
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-blood",
        name="血（Blood / 跨文化）",
        narrative_summary="生命 + 死亡 + 仇恨 + 亲缘。"
                          "全文化都重视的核心意象。"
                          "宗教仪式 + 血脉传承 + 复仇。",
        content_json={
            "religious_meanings": "基督血 = 救赎（最后的晚餐）/ 中国祭祀血 = 通灵 / 玛雅血祭 = 滋养神 / 印度教血 = 净化 / 日本切腹血 = 名誉",
            "narrative_archetypes": "1) 第一次染血 / 主角变成战士 / 2) 血脉觉醒 / 主角觉醒 / 3) 滴血认主 / 物归主人 / 4) 血盟兄弟 / 5) 血债血偿 / 复仇 / 6) 血诅咒 / 家族罪孽",
            "literary_uses_chinese": "'血脉相通'家族 / '血债血偿'武侠 / '血溅五步'大丈夫 / '一腔热血'忠义",
            "literary_uses_western": "'blood is thicker than water'家族 / 'in cold blood'冷血 / 'first blood'第一胜 / 'bad blood'仇怨",
            "subgenre_uses": "仙侠（滴血认主 / 血脉传承）/ 玄幻（血脉觉醒 / 上古血脉）/ 武侠（血战 / 仇杀）/ 末世（丧尸啃血）/ 言情（鲜血玫瑰 = 浪漫）/ 谍战（血盟）",
            "modern_techniques": "见血就晕 = 弱者 / 见血兴奋 = 战士觉醒 / 第一次杀人后呕吐 = 真实人性 / 麻木见血 = 黑化标志",
            "activation_keywords": ["血", "Blood", "血脉", "血债", "滴血认主", "血盟", "血祭", "鲜血"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("跨文化血意象")],
        tags=["通用", "意象", "跨文化"],
    ),
    # 跨文化 - 镜
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-mirror",
        name="镜（Mirror / 跨文化）",
        narrative_summary="自我 + 真相 + 异世界入口。"
                          "白雪公主魔镜 + Lewis Carroll 镜中世界 + 中国 '正衣冠'。"
                          "深度心理意象。",
        content_json={
            "self_recognition": "镜中是真我 vs 假我 / 拉康'镜像阶段'（婴儿首次认识自己）/ 主角看镜中自己'我变了'",
            "western_archetypes": "白雪公主魔镜（'谁是世界上最美'）/ Lewis Carroll《Through the Looking-Glass》镜中世界 / 哈利波特厄里斯魔镜（看心愿）",
            "chinese_archetypes": "唐太宗'以铜为镜可以正衣冠 / 以人为镜可以明得失' / 妖怪现原形必照镜 / 照妖镜 / 照胆镜",
            "narrative_uses": "1) 主角第一次认识自己已变（重生 / 觉醒后）/ 2) 镜中显示真相（反派伪装现形）/ 3) 镜是异世界入口（穿越类）/ 4) 镜碎裂 = 自我崩溃 / 5) 镜映心愿 = 主角心魔",
            "subgenre_uses": "仙侠（照妖镜）/ 都市（女主对镜化妆 / 心理）/ 玄幻（破碎之境穿越）/ 灵异（镜中鬼影）/ 心理（人格分裂）",
            "famous_works": "白雪公主魔镜 / 哈利波特厄里斯魔镜 + 显形镜 / 黑镜（科技反思）/ 《镜中花》中国古典 / Lacan 镜像理论",
            "activation_keywords": ["镜", "Mirror", "魔镜", "照妖镜", "厄里斯魔镜", "镜中世界", "Lacan", "自我"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("跨文化镜意象")],
        tags=["通用", "意象", "心理"],
    ),
    # 跨文化 - 门
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-door",
        name="门（Door / 跨文化）",
        narrative_summary="过渡 + 选择 + 进入 / 出。"
                          "深度叙事意象。"
                          "Stargate / 哈利波特 9 3/4 站台 / 中国'登堂入室'。",
        content_json={
            "narrative_function": "1) 进入新世界（穿越 / 时空门）/ 2) 选择（一扇还是另一扇）/ 3) 关上后回不去 / 4) 钥匙找寻 / 5) 守门者考验",
            "western_archetypes": "Stargate 星际门 / 哈利波特 9 3/4 站台 / Narnia 衣柜 / Doctor Who 蓝色电话亭 / 《黑客帝国》红蓝药丸（门的隐喻）",
            "chinese_archetypes": "桃花源记入口 / 神话天宫南天门 / '入室弟子' / '登堂入室' / '门当户对'婚配",
            "psychological_meaning": "门 = 意识 / 潜意识界限 / Bachelard《空间诗学》门是'生而活的存在' / 主角'走过门' = 心理过渡仪式",
            "subgenre_uses": "穿越（异世界门）/ 仙侠（封印之门）/ 末世（避难所门）/ 都市（家门 / 公司门）/ 言情（情人公寓门）",
            "modern_techniques": "1) 主角站门前犹豫 = 心理转折 / 2) 门后是未知 = 悬念 / 3) 门关后回不去 = 不可逆 / 4) 推开门看到尸体 / 灾难 = 突变",
            "famous_works": "《Stargate》《Doctor Who》《Narnia》/ 哈利波特 / 桃花源记 / Tolkien LOTR 摩瑞亚之门",
            "activation_keywords": ["门", "Door", "Stargate", "桃花源", "南天门", "9¾站台", "Narnia", "穿越"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("跨文化门意象")],
        tags=["通用", "意象", "过渡"],
    ),
    # 跨文化 - 火
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-fire",
        name="火（Fire / 跨文化）",
        narrative_summary="毁灭 + 净化 + 激情 + 文明起源。"
                          "Prometheus 偷火 + 燧人氏钻木 + 维斯塔火炉。"
                          "万物皆源于火。",
        content_json={
            "civilization_origin": "Prometheus 普罗米修斯偷火 / 燧人氏钻木取火 / 是文明起源 / 火 = 文明 / 智慧 / 与动物分野",
            "religious_meanings": "拜火教（Zoroastrianism）信仰永恒之火 / 基督教圣灵降临火舌 / 维斯塔贞女守护永燃之火 / 中国五行火",
            "literary_archetypes": "1) 凤凰浴火重生 / 2) 火葬死者归宇宙 / 3) 火祭神灵 / 4) 火炬传递使命 / 5) 燃烧的爱情",
            "destruction_imagery": "罗马大火尼禄 / 长安洛阳被焚 / 末日大火 / 末世焚城 / 报仇放火 / 焚琴煮鹤 = 毁雅",
            "subgenre_uses": "玄幻（异火 / 真火 / 三昧真火 / 离火）/ 仙侠（炼丹炉火 / 心火 / 业火）/ 西方奇幻（红龙吐息）/ 末世（核爆 + 大火 / 焚城）",
            "psychological_meaning": "激情 / 愤怒 / 创造冲动 / 诱惑（飞蛾扑火）/ 永恒燃烧 / 不熄之火",
            "famous_works": "Prometheus 神话 / 圣火（奥运）/ 中国 '炎黄子孙' = 火神炎帝后裔 / 《老人与海》海上火堆 / Bachelard《火的精神分析》",
            "activation_keywords": ["火", "Fire", "Prometheus", "燧人氏", "三昧真火", "凤凰浴火", "焚城", "拜火教"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("跨文化火意象")],
        tags=["通用", "意象", "跨文化"],
    ),
    # 情感弧线 - 信任→背叛→宽恕
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="emotion-trust-betrayal-forgiveness",
        name="情感弧线：信任 → 背叛 → 宽恕（或拒宽恕）",
        narrative_summary="跨题材深度情感曲线。"
                          "信任建立 + 背叛打击 + 宽恕（救赎）/ 拒宽恕（悲剧）。"
                          "言情 / 武侠 / 谍战通用。",
        content_json={
            "stage_one_trust_building": "缓慢 / 多次小事 / 共患难 / 知根知底 / 'I would die for you' / 比血亲更亲",
            "stage_two_betrayal": "1) 一次大事件 / 2) 表面误会逐渐成真 / 3) 第三方真相揭露 / 4) 最深的伤是被最信的人伤 / 5) 主角心碎 / 不可置信 / 大恨",
            "stage_three_path_a_forgiveness": "1) 主角理解背叛者动机 / 2) 时间冲淡 / 3) 第三方推动 / 4) 背叛者真心忏悔 / 5) 主角'我原谅你 / 但回不到从前'",
            "stage_three_path_b_no_forgiveness": "1) 主角'我永不原谅' / 2) 杀了背叛者 / 3) 远走他乡 / 4) 心病一辈子 / 5) 临终前提一句'当年那个人'",
            "famous_works_path_a": "《肖申克的救赎》/ 《辛德勒的名单》/ 《追风筝的人》/ 韩剧《冬季恋歌》",
            "famous_works_path_b": "《复仇》（韩剧 + 武侠）/ 《基督山伯爵》/ 《名侦探柯南》/ 《V for Vendetta》",
            "psychological_layers": "Kübler-Ross 五阶段 / 否认 + 愤怒 + 讨价还价 + 抑郁 + 接受 / 路径 A 走完 / 路径 B 卡在愤怒",
            "activation_keywords": ["信任", "背叛", "宽恕", "救赎", "复仇", "心碎", "情感弧线"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("Kübler-Ross 模型 + 跨题材")],
        tags=["通用", "情感", "弧线"],
    ),
    # 情感弧线 - 仇恨→理解→放下
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="emotion-hatred-understanding-letting-go",
        name="情感弧线：仇恨 → 理解 → 放下（仇人和解）",
        narrative_summary="武侠 / 复仇 / 历史题材深度弧线。"
                          "纯仇恨 + 真相揭示 + 放下复仇 + 灵魂解脱。"
                          "高级叙事手法。",
        content_json={
            "stage_one_pure_hatred": "1) 童年 / 青年灾难 / 父母被杀 / 家族被灭 / 2) 立志复仇 / 一辈子目标 / 3) 训练 + 隐忍 + 寻找仇人",
            "stage_two_understanding": "1) 找到仇人 / 准备杀 / 2) 偶然或第三方揭示真相 / 仇人有不得已 / 3) 仇人也曾受害 / 4) 仇人临死前讲真话",
            "stage_three_letting_go": "1) 主角放下剑 / 2) 不杀 / 走开 / 3) 数年后释怀 / 4) 与自己和解 / 5) 'I forgive but never forget'",
            "alternative_arcs": "1) 主角不放下 / 杀了仇人 + 心病一辈子 / 2) 主角放下但仇人继续作恶 / 不得不再杀 / 3) 主角接受'仇人是受害者' / 帮助仇人弥补",
            "psychological_meaning": "仇恨绑住自己 / 放下 = 自由 / 复仇是吞下毒希望对方死 / 放下是自我救赎",
            "famous_works": "《肖申克的救赎》/ 《追风筝的人》Hassan 原谅 / 《V for Vendetta》/ 武侠《雪山飞狐》/ 古龙《英雄无泪》",
            "activation_keywords": ["仇恨", "复仇", "理解", "放下", "和解", "真相", "情感弧线"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("跨题材深度复仇弧")],
        tags=["通用", "情感", "复仇"],
    ),
    # 情感弧线 - 自我怀疑→坚定→接纳
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="emotion-self-doubt-affirmation",
        name="情感弧线：自我怀疑 → 寻找答案 → 接纳自我",
        narrative_summary="成长类弧线。"
                          "主角不知道自己是谁 / 该做什么 + 探索 + 找到答案。"
                          "校园 / 励志 / 心理 / 文学题材通用。",
        content_json={
            "stage_one_doubt": "1) 主角觉得自己平凡 / 没用 / 2) 比较他人 / 自卑 / 3) 不知道未来 / 4) 父母期望与自己想要不同 / 5) 失败 / 挫折",
            "stage_two_search": "1) 离家出走 / 旅行 / 2) 遇到导师 / 朋友 / 3) 尝试不同人生 / 4) 经历挫折后顿悟 / 5) 看清楚自己想要什么",
            "stage_three_acceptance": "1) 接受自己的平凡或非凡 / 2) 不再与他人比 / 3) 找到自己的节奏 / 4) 平静 / 自信 / 5) '我就是我'",
            "subgenre_arc_school": "校园题材 / 主角面对高考 + 父母 + 同学竞争 / 找到自己路",
            "subgenre_arc_inspirational": "励志题材 / 创业失败 + 重头再来 + 真正成功",
            "famous_works": "《阳光小美女》/ 《当幸福来敲门》/ 《青春派》/ 《Lady Bird》/ 《Little Women》",
            "psychological_meaning": "Maslow 自我实现 / Erikson 同一性 vs 角色混乱（青春期）/ Carl Rogers 真实自我",
            "activation_keywords": ["自我怀疑", "成长", "迷茫", "找自己", "接纳", "自我实现", "Maslow"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("成长心理学 + 文学")],
        tags=["通用", "情感", "成长"],
    ),
    # 情感弧线 - 浪漫弧线
    MaterialEntry(
        dimension="emotion_arcs", genre="言情",
        slug="emotion-romance-meet-conflict-resolution",
        name="情感弧线：相遇 → 误会 → 信任 → 危机 → 表白",
        narrative_summary="言情核心弧线。"
                          "浪漫电影 + 偶像剧 + 网文言情通用 5 阶段。"
                          "适用甜文 + 虐文 + 双向奔赴。",
        content_json={
            "stage_one_meet_cute": "1) 街上撞 / 2) 工作偶遇 / 3) 相亲 / 4) 同学 / 5) 异国偶遇 / 6) 雨中借伞 / 第一印象不一定好",
            "stage_two_misunderstanding": "1) 对方说话冷淡 / 2) 看见对方与异性 / 3) 第三者挑拨 / 4) 性格不合 / 5) 误会重重",
            "stage_three_trust": "1) 共渡难关 / 2) 真相揭示 / 3) 心结解开 / 4) 暧昧加深 / 5) 默默关心",
            "stage_four_crisis": "1) 重大误会再起 / 2) 第三者强势介入 / 3) 家族反对 / 4) 一方生病或意外 / 5) 似乎要分手",
            "stage_five_confession_or_reunion": "1) 雨中告白 / 2) 机场赶到 / 3) 病床前表白 / 4) 公开告白 / 5) Happy Ending",
            "alternative_endings": "悲剧（一方死 / 远走）/ 开放（似在似不在一起）/ 反转（分手才发现真心）",
            "famous_works": "《Pride and Prejudice》5 阶段经典 / 《何以笙箫默》/ 《杉杉来了》/ 《微微一笑很倾城》/ 《Love Actually》",
            "activation_keywords": ["浪漫弧线", "相遇", "误会", "表白", "言情", "甜文", "虐文"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("Pride & Prejudice 模板")],
        tags=["言情", "情感", "弧线"],
    ),
    # 跨文化 - 蛇
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-snake",
        name="蛇（Snake / 跨文化）",
        narrative_summary="跨文化矛盾意象。"
                          "西方多负面（伊甸园诱惑）+ 东方多正面（医药 / 智慧）。"
                          "深度象征。",
        content_json={
            "western_negative": "伊甸园蛇诱夏娃 = 原罪 / 美杜莎蛇发 = 致命 / 蛇 = 邪恶 / 撒旦化身 / Slytherin 斯莱特林（哈利波特反派学院）",
            "eastern_positive": "中国白蛇娘子 / 印度湿婆颈蛇 / 蛇医（医学符号 = 阿斯克勒庇俄斯之杖 / 二蛇绕杖 = WHO 标志）/ 中医草药蛇 / 龙的雏形",
            "common_meanings": "智慧 / 诱惑 / 重生（蜕皮）/ 隐藏 / 突袭 / 致命 / 永生（衔尾蛇 Ouroboros）",
            "famous_works": "圣经创世纪 / 哈利波特 Voldemort 蛇蜥 + 蛇语 / 中国《白蛇传》白素贞 / 印度湿婆 / 玛雅羽蛇神 Quetzalcoatl",
            "subgenre_uses": "西方奇幻（蛇怪 / 蛇之神）/ 东方仙侠（蛇修 / 白素贞）/ 都市（蛇蝎心肠 / 美女蛇）/ 灵异（蛇精）/ 言情（蛇美人）",
            "modern_subversions": "1) 蛇是主角（《白蛇》动画 / 中国新作）/ 2) 蛇是导师（哈利波特蛇语者）/ 3) 蛇是 medicine（中医）",
            "activation_keywords": ["蛇", "Snake", "白蛇", "美杜莎", "Voldemort", "蛇语", "Quetzalcoatl", "原罪"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("跨文化蛇意象")],
        tags=["通用", "意象", "跨文化"],
    ),
    # 情感弧线 - 孤独到归属
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="emotion-loneliness-belonging",
        name="情感弧线：孤独 → 寻求归属 → 找到家",
        narrative_summary="跨题材深度情感曲线。"
                          "主角孤独 + 寻找归属 + 找到 / 接纳孤独。"
                          "公路片 + 流浪文学 + 移民题材通用。",
        content_json={
            "stage_one_loneliness": "1) 主角孤儿 / 异国 / 失去家人 / 2) 内心封闭 / 不与人交流 / 3) 表面冷漠实则渴望被爱 / 4) 看到他人家庭羡慕但回避",
            "stage_two_searching": "1) 加入新组织（学校 / 公司 / 帮派）/ 2) 与朋友共生 / 3) 找寻家人下落 / 4) 主动表达情感 / 5) 一次次被拒后退缩",
            "stage_three_belonging": "1) 找到真正接纳自己的群体 / 2) 第二个'家' / 3) 与朋友 = 家人 / 4) 自己也成为他人的家",
            "alternative_endings_a": "找到血亲 / 失而复得 / 完美归属",
            "alternative_endings_b": "选择自己的家（朋友 / 配偶）/ 而非血亲",
            "alternative_endings_c": "接纳孤独 / 不需归属 / 自己就是家（哲学性）",
            "famous_works": "《Up》皮克斯老人 + 男孩 / 《追风筝的人》/ 《Slumdog Millionaire》/ 中国《活着》/ 《Forrest Gump》",
            "psychological_meaning": "Maslow 归属感 = 第三层需求 / Erikson 亲密 vs 孤独（青年期）/ 依恋理论",
            "activation_keywords": ["孤独", "归属", "家", "孤儿", "流浪", "情感弧线", "Maslow"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("Maslow + 依恋理论")],
        tags=["通用", "情感", "归属"],
    ),
]


async def main() -> None:
    print(f"Seeding {len(ENTRIES)} entries...")
    inserted = 0
    errors = 0
    by_genre: dict = {}
    by_dim: dict = {}
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
    print()
    print(f"\nBy genre:     {by_genre}")
    print(f"By dimension: {by_dim}")
    print(f"\n✓ {inserted} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    asyncio.run(main())
