"""
Batch 39: Genre-specific signature scenes / set pieces.
Iconic scenes that define each genre — opening / midpoint / climax variants.
Helps planner inject genre-faithful but novel scene structure.
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
    # 仙侠 - 入门拜师
    MaterialEntry(
        dimension="scene_templates", genre="仙侠",
        slug="scene-xianxia-master-disciple-rite",
        name="拜师 / 入门仪式",
        narrative_summary="仙侠经典开场场景。"
                          "宗门测试 + 拜师礼 + 师徒缘起。"
                          "新人主角入修真门派的第一步。",
        content_json={
            "stage_setup": "宗门正殿（天柱殿 / 凌云殿 / 太清殿）/ 千年古香 / 道家壁画 / 长老十数位坐高位 / 弟子百千跪低座 / 庄严肃穆",
            "ritual_steps": "1) 入殿三鞠躬 / 2) 念入门誓词 / 3) 师徒确认 / 4) 师傅赐道号 / 5) 师傅传第一卷功法 / 6) 师傅赠初阶法器 / 7) 师徒结拜 / 8) 师弟妹相认",
            "key_lines": "'弟子 X，今日叩拜师尊 / 永遵宗门规矩 / 不踏邪路 / 死生相伴' / '徒儿 / 起身 / 此后我便是你师傅 / 此剑赠你' / '愿弟子他日青出于蓝'",
            "common_subtypes": "1) 主角是天才 / 多个长老抢徒 / 2) 主角是废物 / 没有师傅愿收 / 3) 主角偶遇大师 / 私下拜师 / 4) 师傅是隐居老人 / 不在宗门 / 5) 主角女扮男装入门",
            "narrative_value": "正式开始修真之路 / 引出师徒情节线 / 引出师弟妹情节线 / 引出宗门冲突",
            "famous_works": "《诛仙》青云入门 / 《凡人修仙传》韩立入门 / 《仙逆》王林拜师 / 《缥缈之旅》李强入门",
            "activation_keywords": ["拜师", "入门", "宗门测试", "师徒礼", "道号", "师弟妹", "正殿"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("仙侠经典场景")],
        tags=["仙侠", "开场", "拜师"],
    ),
    # 仙侠 - 突破境界
    MaterialEntry(
        dimension="scene_templates", genre="仙侠",
        slug="scene-xianxia-realm-breakthrough",
        name="境界突破（练气 → 筑基 / 金丹 → 元婴等）",
        narrative_summary="仙侠主角升阶必经场景。"
                          "感悟 + 真气暴动 + 突破成功 / 失败。"
                          "每升一阶 = 章节高潮 = 节奏锚点。",
        content_json={
            "buildup": "主角找瓶颈 / 心境困惑 / 寻灵药 / 找天材地宝 / 闭关 N 天 / 师傅护法 / 朋友守门",
            "moment_of_breakthrough": "1) 全身真气剧烈震动 / 2) 经脉拓宽（剧痛）/ 3) 体内灵气如海 / 4) 神识扩展 / 5) 看见天地灵气流动 / 6) 一道金光冲顶 / 7) 顿悟",
            "side_effects": "突破成功 = 体力消耗大 / 突破失败 = 走火入魔 + 经脉断裂 / 重伤 / 死亡 / 修为倒退",
            "obstacles_design": "1) 心境瓶颈（要靠悟）/ 2) 资质不够（要灵药）/ 3) 突然敌袭（破坏闭关）/ 4) 修为冲突（双修不和）/ 5) 心魔（对亲友的执念）",
            "narrative_use": "章节高潮 / 卷末必备 / 角色阶段性成长 / 与对手相对实力变化点",
            "famous_works": "《凡人修仙传》韩立每次突破 / 《诛仙》张小凡突破 / 《斗破苍穹》萧炎突破",
            "activation_keywords": ["突破", "境界", "瓶颈", "顿悟", "走火入魔", "升阶", "金丹"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("仙侠升阶场景")],
        tags=["仙侠", "高潮", "突破"],
    ),
    # 武侠 - 比武招亲
    MaterialEntry(
        dimension="scene_templates", genre="武侠",
        slug="scene-wuxia-bibwu-bride",
        name="比武招亲 / 武林大会",
        narrative_summary="武侠经典聚会场景。"
                          "豪门设擂 + 江湖好手云集 + 选婿。"
                          "传统女性父亲为女儿寻夫的设定。",
        content_json={
            "stage_setup": "高楼大院 / 主席台高耸 / 围观江湖人 + 看客 / 锣鼓喧天 / 美女面纱遮面（女主）",
            "common_arcs": "1) 招亲设定 / 2) 各路豪杰挑战 / 3) 主角偶然路过 / 4) 主角不愿但被卷入 / 5) 主角胜利 / 6) 美女对主角动情 / 7) 主角'我已有意中人'离去（典型反套路）",
            "key_dynamics": "1) 设擂家族目的（招才 / 政治联姻 / 试探女儿心意）/ 2) 各路高手心机（真打 vs 装弱）/ 3) 主角姿态（不在意 / 路见不平）",
            "famous_subversions": "1) 美女自己上台打 / 2) 美女是反派 / 3) 招亲实为陷阱 / 4) 主角输了反而被女主追求",
            "narrative_value": "聚会场景引出多线人物 + 武林八卦 + 主角声名鹊起 / 红颜知己引入",
            "famous_works": "金庸《天龙八部》/ 金庸《神雕侠侣》郭芙比武 / 古龙系列",
            "activation_keywords": ["比武招亲", "武林大会", "擂台", "江湖好手", "面纱", "招亲"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("武侠经典聚会")],
        tags=["武侠", "聚会", "招亲"],
    ),
    # 武侠 - 雪夜决战
    MaterialEntry(
        dimension="scene_templates", genre="武侠",
        slug="scene-wuxia-snow-night-duel",
        name="雪夜决战 / 紫禁之巅",
        narrative_summary="武侠最高规格对决。"
                          "雪夜 + 顶级两人对决 + 一招定胜负。"
                          "古龙最经典场景手法。",
        content_json={
            "stage_setup": "雪夜寂静 / 月光下 / 山巅 / 屋脊 / 古剑 / 长发飞扬 / 周围空无一人 / 雪花飘落",
            "duel_pacing": "1) 长时间静默 / 互相打量 / 2) 突然出剑 / 一剑闪过 / 3) 一招过 / 一人倒下 / 4) 胜者沉默离去",
            "key_descriptions": "'风过 / 剑落 / 雪上一缕血迹' / '夜很静 / 静到能听见雪花的声音' / '剑出鞘的一刹那 / 时间停了'",
            "iconic_works": "《多情剑客无情剑》李寻欢 / 《陆小凤传奇》西门吹雪 vs 叶孤城紫禁之巅 / 《剑神一笑》",
            "narrative_value": "卷末或全书末高潮 / 终极对决 / 慢节奏 + 静默 + 一招 / 作家的极致诗意",
            "modern_subversions": "1) 决战之后两人同归于尽 / 2) 两人对决一夜不分胜负 / 3) 决战实为约定 / 输者其实是赢者（古龙惯用）",
            "activation_keywords": ["雪夜决战", "紫禁之巅", "决斗", "一招定胜负", "西门吹雪", "叶孤城", "月夜"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("古龙武侠诗意决战")],
        tags=["武侠", "高潮", "决战"],
    ),
    # 都市 - 商战谈判
    MaterialEntry(
        dimension="scene_templates", genre="都市",
        slug="scene-urban-business-negotiation",
        name="商战谈判 / 董事会博弈",
        narrative_summary="都市商战 / 总裁文核心场景。"
                          "豪华会议室 + 千亿大单 + 股权争夺。"
                          "智斗 + 心理战 + 翻盘。",
        content_json={
            "stage_setup": "落地窗顶层会议室 / 长桌 / 高级董事 + 律师 + 助理 / 男主西装革履 / 女助理高跟鞋 / 文件 + 笔 + 矿泉水 / 紧张气氛",
            "common_arcs": "1) 双方坐定 / 寒暄 / 2) 对方提出苛刻条件 / 3) 主角沉默 / 4) 主角抛出反制条件（提前埋伏）/ 5) 对方哑口无言 / 6) 双方僵持 / 7) 中途有人退场 / 8) 主角胜利 / 对方屈服",
            "psychological_tactics": "心理战 / 沉默施压 / 故意延后 / 制造危机 / 反向设套 / 表面合作暗中博弈 / 利用第三方信息",
            "key_dynamics": "信息不对称 / 谁先泄露弱点谁输 / 主角有'某个意料之外的资源' / 反派以为自己赢实则输",
            "famous_works": "《杉杉来了》《亲爱的翻译官》/ 韩剧《迷雾》《老实点》/ 美剧《Suits》《Billions》",
            "common_dishes_during_meeting": "黑咖啡 / 矿泉水 / 几乎不吃 / 紧张气氛下味同嚼蜡",
            "activation_keywords": ["商战", "谈判", "董事会", "股权", "千亿大单", "总裁文", "心理战"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("都市商战核心场景")],
        tags=["都市", "商战", "谈判"],
    ),
    # 总裁文 - 雨中告白
    MaterialEntry(
        dimension="scene_templates", genre="言情",
        slug="scene-romance-rain-confession",
        name="雨中告白 / 机场告别（言情两大经典）",
        narrative_summary="言情催泪场景模板。"
                          "雨中淋湿 + 告白 / 机场误会赶到。"
                          "韩剧 / 偶像剧 / 网文标配。",
        content_json={
            "rain_confession_setup": "暴雨突然下 / 女主一个人在街上 / 男主追来 / 没带伞 / 全身湿透 / 长头发贴脸 / 眼眶含泪 / 大声告白",
            "airport_setup": "女主即将登机 / 男主开车狂飙 / 错过登机时间 / 男主跑安检 / 突然广播'X 女士请回到 X 号登机口'/ 女主回头 / 看到男主泪眼",
            "common_subtypes_rain": "1) 女主拒绝 + 男主大吼 / 2) 女主接受 + 紧抱亲吻 / 3) 男主表白后默默走开 / 4) 雨中独白后女主被感动",
            "common_subtypes_airport": "1) 男主喊女主名字 / 2) 男主跪求 / 3) 男主'我错了' / 4) 女主放弃登机 / 投入男主怀抱",
            "key_lines": "'我喜欢你 / 不行吗' / '别走 / 留下来' / '我以为我可以放弃 / 但我不能' / '我不要爱情 / 我要你'",
            "modern_subversions": "1) 雨中告白后女主'不接受'笑着离开 / 2) 机场没赶上 / 双方各自走 / 数年后再见 / 3) 雨中告白发现男主搞错对象",
            "famous_works": "《何以笙箫默》/ 《杉杉来了》/ 韩剧无数 / 《The Notebook》",
            "activation_keywords": ["雨中告白", "机场告别", "暴雨", "登机", "告白", "言情高潮", "催泪"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("言情催泪场景")],
        tags=["言情", "高潮", "告白"],
    ),
    # 末世 - 第一只丧尸
    MaterialEntry(
        dimension="scene_templates", genre="末世",
        slug="scene-doomsday-first-zombie",
        name="末世第一只丧尸出现",
        narrative_summary="末世故事开场标志场景。"
                          "正常生活 → 突如其来的恐怖。"
                          "TLOU / Walking Dead / 28 Days Later 模板。",
        content_json={
            "stage_setup": "正常都市 / 早晨 / 主角上班路上 / 突然听到尖叫 / 对面有人扑向行人 / 撕咬 / 鲜血四溅 / 周围人开始逃 / 警车赶来",
            "key_pacing": "1) 主角看到 / 不敢相信 / 2) 第二个第三个出现 / 主角冲回家 / 3) 全城混乱 / 4) 上车开车出城 / 5) 高速公路堵塞 / 6) 找避难地 / 第一夜战斗",
            "atmosphere_techniques": "电视广播突然中断 / 手机信号渐渐消失 / 妻子打电话发现说不通 / 路上看见亲人变丧尸 / 必须杀亲人 = 经典高潮",
            "common_subgenres": "1) 病毒爆发型（28 Days Later）/ 2) 实验室泄漏型（Resident Evil）/ 3) 神秘灾难型（World War Z）/ 4) 上古觉醒型（中国本土末世流）",
            "key_lines": "'快上车 / 别看' / '他们已经不是人了' / '不能犹豫 / 要么你死要么他死' / '世界完了'",
            "famous_works": "TLOU 第一集 / Walking Dead 第一集 / 28 Days Later / Train to Busan / World War Z",
            "activation_keywords": ["末世", "第一只丧尸", "病毒爆发", "撕咬", "灾难开端", "TLOU"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("末世开场场景")],
        tags=["末世", "开场", "丧尸"],
    ),
    # 校园 - 转学生入校
    MaterialEntry(
        dimension="scene_templates", genre="校园",
        slug="scene-school-transfer-student",
        name="转学生入校（神秘新生）",
        narrative_summary="校园流经典开场。"
                          "新生入校 + 全班震惊 + 隐藏身份。"
                          "日漫 + 韩剧 + 国产校园文标配。",
        content_json={
            "stage_setup": "教室 / 老师介绍 / 'X 同学今天起加入我们班' / 新生站在讲台 / 全班瞪眼 / 美貌震惊 / 男 / 女主互相对视",
            "common_subtypes": "1) 转学生是冷酷美少女 / 2) 转学生是富二代 / 3) 转学生是隐藏身份天才 / 4) 转学生是家族联姻对象 / 5) 转学生是从国外回来 / 6) 转学生是异能者",
            "narrative_arc": "1) 第一日震惊 / 2) 第二日发现新生身份不简单 / 3) 班花 / 校草开始接近 / 4) 班级霸凌 vs 新生 / 5) 新生展示能力 / 6) 班级臣服 / 7) 男 / 女主开始情愫",
            "key_lines": "'你叫什么名字' / '我可以坐这里吗' / '我以前在 X 国 / 习惯了' / '原来你是 X 家的人'",
            "modern_subversions": "1) 转学生其实是平凡 / 但全班误以为是天才 / 2) 转学生是反派复仇 / 3) 转学生是穿越者 / 4) 转学生失忆 / 不知自己身份",
            "famous_works": "《青春派》《我的大叔》/ 日漫无数 / 韩剧《天空之城》《W》",
            "activation_keywords": ["转学生", "新生", "入校", "讲台", "美貌", "隐藏身份", "校园"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[llm_note("校园流标准开场")],
        tags=["校园", "开场", "转学生"],
    ),
    # 谍战 - 接头暗号
    MaterialEntry(
        dimension="scene_templates", genre="谍战",
        slug="scene-spy-rendezvous",
        name="谍战接头 / 暗号交换",
        narrative_summary="谍战 / 特工流核心场景。"
                          "公园 / 咖啡馆 / 报亭 / 旧书店。"
                          "暗号 + 物件交换 + 走开。",
        content_json={
            "common_locations": "公园长椅 / 报亭 / 老书店 / 咖啡馆角落 / 公交站 / 教堂祈祷处 / 古董店 / 黑市深处",
            "ritual_steps": "1) 接头者先到 / 假装看报 / 喝咖啡 / 2) 接头者后到 / 邻座 / 3) 第一句暗号 / 4) 第二句确认 / 5) 物件交换（或信息口述）/ 6) 各自离开 / 7) 监视者跟踪",
            "iconic_codewords": "'你看过《XX》吗' / '看过 / 但结尾不喜欢' / 'XX 街的咖啡店还开吗' / '老地方 / 旧时光' / 一句诗的上半 / 对方回下半",
            "narrative_dynamics": "1) 暗号错 = 立即识破 = 拔枪 / 2) 暗号对 = 信任 / 3) 双方都是双面间谍 = 观众悬念 / 4) 接头者已被抓 = 替身代替",
            "famous_works": "《潜伏》谍战 / 《风筝》/ 《暗算》/ 《伪装者》/ Bond 007 / Bourne / Tinker Tailor Soldier Spy",
            "key_psychology": "心理紧张 / 周围观察 / 任何人都可能监视 / 动作细节关键（喝咖啡 / 读报方向）",
            "activation_keywords": ["接头", "暗号", "谍战", "接头者", "情报", "公园长椅", "公交站"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("谍战经典场景")],
        tags=["谍战", "接头"],
    ),
    # 玄幻 - 拍卖会激斗
    MaterialEntry(
        dimension="scene_templates", genre="玄幻",
        slug="scene-xuanhuan-auction",
        name="拍卖会 / 万年宝物争夺",
        narrative_summary="玄幻 / 仙侠经典聚会场景。"
                          "各方势力齐聚 + 万年宝物 + 主角竞拍 + 反派来抢。"
                          "推动主角资源 + 引出新势力。",
        content_json={
            "stage_setup": "豪华拍卖大厅 / 透明罩子展示宝物 / 拍卖师高台 / VIP 包厢 / 各路修士 / 散发不同气压 / 主角进来时引人注目",
            "auction_items_design": "1) 普通修真材料（暖场）/ 2) 中等灵药（兴趣）/ 3) 法宝兵器（争夺）/ 4) 上古残卷（白热化）/ 5) 神秘压轴（主角觊觎）",
            "key_arcs": "1) 主角以低姿态入场 / 2) 普通宝物不参与 / 3) 看到关键物 / 突然出价 / 4) 与某势力对抗 / 5) 出价飙到天文数字 / 6) 主角拿下 / 7) 离场被劫 / 8) 主角反杀",
            "common_dynamics": "1) 主角与神秘女子对竞拍 / 后来发现是合作 / 2) 主角资金不够 / 朋友 / 路人借钱 / 3) 反派故意抬价 / 让主角财耗 / 4) 拍卖会被反派围攻",
            "iconic_lines": "'10 万灵石起拍 / 每次加价至少 1 万灵石' / '我出 100 万' / '101 万' / '500 万' / 拍卖师惊叹 / 全场震惊",
            "famous_works": "《斗破苍穹》《武动乾坤》《诛仙》《凡人修仙传》",
            "activation_keywords": ["拍卖会", "拍卖师", "万年宝物", "竞拍", "灵石", "VIP包厢"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("玄幻聚会场景")],
        tags=["玄幻", "聚会", "拍卖"],
    ),
    # 通用 - 葬礼
    MaterialEntry(
        dimension="scene_templates", genre=None,
        slug="scene-funeral",
        name="葬礼 / 死别送行",
        narrative_summary="跨题材重要情感场景。"
                          "失去亲友 + 转折点 + 主角心境变化。"
                          "几乎所有题材都用。",
        content_json={
            "common_settings_modern": "教堂 / 殡仪馆 / 火葬场 / 墓地 / 黑色西装 / 鲜花 / 哭声 / 灰天",
            "common_settings_ancient": "灵堂 / 白布裹尸 / 黑色棺材 / 长子披麻戴孝 / 全村送行 / 焚纸祭 / 跪拜礼",
            "common_settings_xianxia": "宗门正殿设灵 / 弟子白衣 / 师傅遗物展 / 主角断指立誓 / 长老致悼词",
            "narrative_uses": "1) 主角童年丧父 / 立志的起点 / 2) 师傅死 / 主角觉醒 / 3) 朋友死 / 主角愤怒 / 4) 仇人死 / 主角空虚 / 5) 自己'死' / 葬礼是别人办 / 主角已重生",
            "key_psychology": "悲痛 / 自责 / 愤怒 / 麻木 / 空虚 / 决心 / 五阶段（Kübler-Ross）",
            "iconic_lines": "'师傅 / 您一路走好' / '我答应过你 / 一定要 X' / '走好不送' / '愿你来世投个好胎'",
            "modern_techniques": "葬礼上插入回忆 / 葬礼后空荡的家 / 葬礼时下雨 / 葬礼上敌人也来 / 葬礼引发新冲突",
            "activation_keywords": ["葬礼", "送葬", "灵堂", "悼词", "黑纱", "哭声", "披麻戴孝"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("跨题材情感场景")],
        tags=["通用", "情感", "死别"],
    ),
    # 通用 - 黎明 / 日出
    MaterialEntry(
        dimension="scene_templates", genre=None,
        slug="scene-dawn-resolution",
        name="黎明 / 日出（结尾或转折）",
        narrative_summary="跨题材结尾或转折点意象。"
                          "黑夜过去 + 新一天 + 希望。"
                          "传统 + 现代都用。",
        content_json={
            "common_settings": "山顶 / 海边 / 阳台 / 战场过后 / 城市天际线 / 古战场 / 主角伤痕累累但望日出",
            "narrative_uses": "1) 大决战胜利后 / 2) 重要人物死后 / 3) 长卷收尾 / 4) 主角心境转变 / 5) 新阶段起点",
            "atmospheric_descriptions": "天边一线红 / 太阳从山后探头 / 鸟开始鸣 / 风很轻 / 露水未干 / 静",
            "iconic_meaning": "希望 / 新生 / 终结 / 重启 / 时间流逝 / 不可逆",
            "key_lines": "'又是新的一天' / '太阳照常升起' / '昨天死了 / 今天活' / '明天会更好' / '黑夜过去 / 黎明就到'",
            "famous_works": "《老人与海》'太阳照常升起'/ 《白鲸记》/ 海明威多用 / 中国文学《活着》《平凡的世界》/ 武侠多用",
            "modern_subversions": "1) 黎明却预示更大灾难 / 2) 黎明时主角发现自己已死 / 3) 黎明是主角想象 / 现实仍黑夜",
            "activation_keywords": ["黎明", "日出", "结尾", "希望", "新生", "山顶", "海边"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("跨题材结尾意象")],
        tags=["通用", "结尾", "意象"],
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
