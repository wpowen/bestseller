"""Batch 50: device_templates + dialogue_styles + plot_patterns mixed depth (15 entries)

Final round before audit:
- 5 device_templates: 古风器物（玉佩+玉简+令牌+罗盘+算盘）
- 5 dialogue_styles: voice register depth (官话+江湖切口+学者腔+少年腔+老人腔)
- 5 plot_patterns: niche genre formulas
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
    # ---------- device_templates (5) ----------
    MaterialEntry(
        dimension="device_templates", genre=None,
        slug="device-classical-jade-pendant",
        name="古风器物：玉佩 / 信物+护身",
        narrative_summary="中国古典必备物：玉佩。家族传承+ 定情信物+ 护身符号+ 身份象征+ 隐藏家族秘密+ 重逢凭据。古风言情+ 武侠+ 仙侠+ 宫斗都用。",
        content_json={
            "categories": "1) 家族族徽玉（祖传+ 一族一式） / 2) 定情双玉（雌雄一对+ 半阴半阳）/ 3) 护身玉（开光+ 避邪）/ 4) 信物玉（任务凭证+ 调兵令） / 5) 暗藏机关玉（拆开有密信/ 地图）",
            "materials": "和田白玉（最贵+ 帝王专用）+ 翡翠（清代+ 妃嫔最爱）+ 蓝田玉（历史悠久）+ 岫玉（民间）+ 紫玉（罕见+ 神秘）",
            "common_uses": "1) 见玉如见人（替代主人传话） / 2) 双玉合一（揭示身世）/ 3) 玉碎=人亡（情感感应） / 4) 失玉=家族倾覆（征兆） / 5) 玉脸的暗合（遇到天命之人）",
            "anti_cliche": "不要纯写'玉佩落水捡起结识男主'；让玉佩有真实的工艺细节（雕工+磨痕+玉质等级+保养方式）",
            "activation_keywords": ["玉佩", "信物", "和田玉", "古风", "传承"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("古风言情通用器物模板综合")],
        tags=["device", "古风", "玉佩", "通用"],
    ),
    MaterialEntry(
        dimension="device_templates", genre=None,
        slug="device-classical-jade-slip",
        name="古风器物：玉简 / 古代信息载体",
        narrative_summary="仙侠+ 玄幻必备：玉简。能记录大量信息（功法+ 地图+ 心法+ 阵图）+ 神识阅读+ 防偷防伪+ 用完会自毁。古代版的'U 盘'。",
        content_json={
            "categories": "1) 功法玉简（修炼心法）/ 2) 地图玉简（秘境路线）/ 3) 阵图玉简（布阵方法）/ 4) 任务玉简（宗门下达）/ 5) 信件玉简（机密通信）",
            "tech_layer": "神识刻录（修者用神识写）+ 神识阅读（修者读取）+ 自毁机制（指定次数后失效）+ 防伪签名（特定神识波动）",
            "common_uses": "1) 师傅传功（一片玉简够弟子学一年） / 2) 任务下达（任务玉简到手不可拒）/ 3) 秘境地图（探宝必备） / 4) 仇杀凭证（仇敌神识印） / 5) 遗言（陨落前神识全部刻入）",
            "value_tier": "凡品玉简（凡人也能读但只能写文字） / 灵品（修者神识专用） / 法品（防伪+ 自毁） / 仙品（无限刻录+ 不消失）",
            "anti_cliche": "不要纯写'玉简记载绝世功法'；让玉简有数据丢失（年代久远残缺）+ 神识不兼容+ 阅读副作用（伤神）",
            "activation_keywords": ["玉简", "功法", "神识", "仙侠", "信息载体"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("仙侠功法传承模板综合")],
        tags=["device", "仙侠", "玉简", "信息"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="武侠",
        slug="device-wuxia-token",
        name="武侠器物：令牌 / 帮派+朝廷凭证",
        narrative_summary="武侠帮派+ 朝廷的身份凭证。见令如见主+ 调动帮众+ 暗杀任务+ 联络信号。失令=失权。每个门派的令牌设计+ 材质+ 暗号都不同。",
        content_json={
            "types": "1) 帮派令（丐帮打狗棒+ 武当掌门玉令+ 少林袈裟）/ 2) 朝廷令（虎符+ 锦衣卫腰牌+ 圣旨）/ 3) 杀手令（杀手榜+ 唐门令+ 红衣杀手）/ 4) 江湖令（武林盟主令+ 江湖好汉聚义令）/ 5) 私令（豪门家主令+ 商号令）",
            "materials": "黄金（朝廷+ 皇家）+ 玉（豪门+ 帮派）+ 铜（一般门派）+ 木（民间帮会）+ 牌挂式+ 印章式+ 牌+ 符",
            "common_uses": "1) 见令如见主（任务下达） / 2) 调兵符（紧急调动）/ 3) 入门凭证（门派内部）/ 4) 接头暗号（隐藏身份）/ 5) 通缉令（江湖追杀）",
            "anti_cliche": "不要纯写'令牌一出敌人退散'；让令牌有真实的复杂（伪造+ 老旧+ 失效+ 政权交替时的尴尬）",
            "activation_keywords": ["令牌", "帮派", "朝廷", "武侠", "凭证"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("金庸+ 古龙武侠令牌模板综合")],
        tags=["device", "武侠", "令牌", "凭证"],
    ),
    MaterialEntry(
        dimension="device_templates", genre=None,
        slug="device-classical-compass",
        name="古风器物：罗盘 / 风水阴阳",
        narrative_summary="风水师+ 寻龙人+ 茅山道士+ 出马仙必备：罗盘。识方位+ 看龙脉+ 找阴阳+ 避煞气。从指南针演化的占卜+ 探险工具。",
        content_json={
            "structure": "中央天池（磁针）+ 周边 24 山（方位）+ 8 卦+ 五行+ 24 节气+ 28 宿+ 60 甲子（外圈）",
            "schools": "三合派（杨筠松传统）+ 三元派（蒋大鸿）+ 玄空飞星派（沈竹礽）+ 八宅派（建筑专用）",
            "common_uses": "1) 阳宅（房屋建造选址） / 2) 阴宅（坟墓选址） / 3) 寻龙脉（盗墓+ 风水大宝）/ 4) 避邪（家中布阵）/ 5) 看八字（婚配+ 时辰）",
            "famous_examples": "鬼吹灯系列（盗墓寻龙）+ 寻龙诀+ 风水世家+ 周易+ 撼龙经",
            "anti_cliche": "不要纯写'风水大师一看就准'；让罗盘有真实的复杂（方位偏差+ 老罗盘磁针失灵+ 不同山头要不同罗盘）",
            "activation_keywords": ["罗盘", "风水", "龙脉", "阴宅", "鬼吹灯"],
        },
        source_type="research_agent", confidence=0.9,
        source_citations=[wiki("Luopan"), wiki("Feng_shui"), llm_note("天下霸唱《鬼吹灯》风水世家描写")],
        tags=["device", "古风", "罗盘", "风水"],
    ),
    MaterialEntry(
        dimension="device_templates", genre=None,
        slug="device-classical-abacus",
        name="古风器物：算盘 / 计算+ 武器+ 法器",
        narrative_summary="算盘是中国古代计算工具+ 商人必备+ 也可做武器+ 道家法器。算筹拨动声+ 算盘字诀+ 高手心算如风。是商业+ 武侠+ 玄学的多功能道具。",
        content_json={
            "categories": "1) 商业算盘（账房+ 银号+ 票号必备） / 2) 武器算盘（铁制+ 暗器藏珠）/ 3) 法器算盘（道士算运程+ 卜卦） / 4) 教学算盘（私塾启蒙）/ 5) 礼仪算盘（科举送礼+ 文人雅集）",
            "tech_layer": "算盘字诀（如九因歌+ 借九还一）+ 高手心算 100 位数无需算盘+ 暗器藏珠（拍打弹出毒针）+ 卜卦时算+ 风水家用算盘换季",
            "common_uses": "1) 商号查账（一日万账） / 2) 战场算粮（军队后勤） / 3) 武器抢夺（用算盘打倒敌人） / 4) 卜卦算命（道士+ 算命先生） / 5) 启蒙教育（古代小学教材）",
            "famous_examples": "《大宅门》白家二爷打算盘+ 《乔家大院》乔致庸算盘+ 算盘在道家法器中的角色+ 古代算学家祖冲之",
            "anti_cliche": "不要纯写'算盘=算账'；让算盘成为人物特征（账房先生+ 武器人物+ 道士+ 启蒙先生）",
            "activation_keywords": ["算盘", "商业", "武器", "法器", "古风"],
        },
        source_type="research_agent", confidence=0.85,
        source_citations=[wiki("Suanpan"), llm_note("古风+ 武侠+ 商战通用算盘描写综合")],
        tags=["device", "古风", "算盘", "通用"],
    ),

    # ---------- dialogue_styles (5) ----------
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="dialogue-court-officialese",
        name="对话风格：官话腔 / 朝堂奏对",
        narrative_summary="古代官员+ 现代政客+ 公司高管的'官话腔'：含蓄+ 客气+ 多重否定+ 不直说不可说+ 充满'但是'/'然而'/'据悉'/'坊间传言'。让读者从字缝里读真相。",
        content_json={
            "rhetorical_devices": "1) 多重否定（'非不愿也，实不能也'） / 2) 委婉指代（'某些不愿透露姓名的人士'） / 3) 客套套话（'臣愚见'+ '微臣昧死奏'） / 4) 含糊敷衍（'此事尚需斟酌'+ '容臣下查阅'）/ 5) 礼数尊称（陛下+ 殿下+ 大人+ 阁下）",
            "register_levels": "1) 朝堂正式（'臣闻+ 陛下'） / 2) 宴席半正式（'兄台+ 在下'）/ 3) 私下密谈（'你看+ 这事'）/ 4) 紧急简短（'立即+ 即刻'）",
            "use_cases": "1) 朝廷奏对 / 2) 政治博弈 / 3) 公司高管会议 / 4) 古风权谋 / 5) 政治剧",
            "famous_examples": "《琅琊榜》朝堂奏对+ 《雍正王朝》朝议+ 《纸牌屋》议院+ 《Yes Minister》英国官场",
            "anti_cliche": "不要纯写'臣启奏'+ 长篇陈词；让官话有信息密度（每句都暗藏立场+ 利益+ 试探）",
            "activation_keywords": ["官话", "朝堂", "奏对", "官场", "权谋"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("琅琊榜+ 雍正王朝+ 大明王朝 1566 朝堂对话模板综合")],
        tags=["dialogue_styles", "官话", "通用"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre="武侠",
        slug="dialogue-jianghu-cant",
        name="对话风格：江湖切口 / 黑话",
        narrative_summary="武侠+ 帮派+ 黑社会的'江湖切口'：内部暗号+ 行话+ 隐喻。外人听不懂+ 自己人秒会意。是身份认同+ 防外人偷听的工具。",
        content_json={
            "categories": "1) 帮会切口（青帮黑话：跑车=出活+蹲点=放哨）/ 2) 江湖行话（武林：晓得点+ 叫板）/ 3) 江湖术语（接头='老地方+ 老人' / 报号=自称姓名+ 师承） / 4) 暗器暗号（招呼= '抱拳' / 警告='摆字'+ '划地')",
            "famous_examples_lines": "1) '你是哪个山头的？'（你属于哪一派） / 2) '在下姓 X 名 X，师承 X X 派 X X 长老' / 3) '行不更名坐不改姓' / 4) '不知江湖深浅'（你太嫩了） / 5) '请问尊姓大名，江湖人称什么号？' / 6) 接头暗号 + 报号回应",
            "modern_adaptation": "黑社会：吃饭+ 跑路+ 老大+ 兄弟+ 上路+ 挂了 / 网络黑话：种草+ 拔草+ 肝+ 氪+ 凉了+ 翻车",
            "use_cases": "1) 武侠帮派+ 黑社会题材 / 2) 隐藏身份+ 暗号接头 / 3) 凸显角色身份（用切口=老江湖；不会用=新人）",
            "anti_cliche": "不要纯堆切口炫耀；要有真实功能（只在需要保密时用 / 让小白听不懂的尴尬）",
            "activation_keywords": ["江湖", "切口", "黑话", "武侠", "帮派"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Cant_(language)"), llm_note("古龙+ 金庸+ 王朔《动物凶猛》黑话综合")],
        tags=["dialogue_styles", "江湖", "切口", "武侠"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="dialogue-academic-erudite",
        name="对话风格：学者腔 / 知识分子",
        narrative_summary="知识分子+ 教授+ 研究员的'学术腔'：精确术语+ 引用+ 长复合句+ 限定语（'某种意义上'+ '在某个语境下'）+ 礼让的辩论态度。让人物显得有深度。",
        content_json={
            "rhetorical_devices": "1) 术语精确（不用'感觉'用'体验/认知/语义层面'） / 2) 引用大师（'正如海德格尔所言+ 福柯指出'）/ 3) 长复合句（一句话三个从句）/ 4) 限定语（'某种意义上'+ '从某个角度') / 5) 礼让辩论（'我倾向认为... 但您的观点也有合理之处')",
            "use_cases": "1) 大学教授角色 / 2) 知识分子聚会 / 3) 学术辩论 / 4) 研究小说+ 科幻硬核+ 历史考据",
            "famous_examples": "《Big Bang Theory》Sheldon+ 《Good Will Hunting》Sean Maguire+ 《盗梦空间》Cobb 谈梦境理论+ 《云图》多线索哲学对白",
            "warning": "不要让学者腔变得装腔作势；保留人物真情感（学者也会害怕+ 哭+ 失控）",
            "anti_cliche": "不要纯堆术语炫耀；让学者腔在合适场景才用（专业讨论用 / 私人感情时切回口语）",
            "activation_keywords": ["学者腔", "知识分子", "学术", "海德格尔", "教授"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("Big Bang Theory + Good Will Hunting + Cloud Atlas 学术对话模板综合")],
        tags=["dialogue_styles", "学者", "学术", "通用"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="dialogue-youth-energetic",
        name="对话风格：少年腔 / 网生代",
        narrative_summary="14-22 岁年轻人的对话特征：网络梗+ 缩写+ 表情包语+ 自嘲+ 反差萌+ 短句+ 押韵+ 反语。是 Z 世代+ 校园+ 二次元+ 直播题材的标配。",
        content_json={
            "vocabulary_features": "网络梗（绝绝子+ yyds+ 破防了+ emo+ 老六+ 鸡你太美）+ 缩写（xswl+ 666+ tql+ wsl）+ 表情符号化语言（流泪+ 笑哭+ 狗头）+ 谐音（蓝瘦香菇+ 雨女无瓜）+ 拼贴（Q+人）",
            "rhetorical_features": "1) 自嘲（'啊我又是这么 emo 的一天'）/ 2) 反差萌（一会儿严肃一会儿搞怪）/ 3) 短句节奏（'啊...好...emo...的') / 4) 押韵（'你这个憨憨真馋馋'）/ 5) 反语（'你是真的牛'=讽刺）",
            "use_cases": "1) 校园题材 / 2) 直播主播 / 3) 二次元+ 同人圈 / 4) Z 世代职场 / 5) B 站 Vlog",
            "warning": "网络梗保质期短（3 个月-1 年就过气）；要记录写作时的具体年代，避免出版后过气",
            "anti_cliche": "不要让所有年轻人都说同样网络梗；保留个性化（学霸说梗+宅男说梗+ 体育生说梗 风格不同）",
            "activation_keywords": ["少年腔", "Z 世代", "网络梗", "校园", "B 站"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("B 站+ 抖音+ 微博 Z 世代用语综合 + 校园题材头部作品")],
        tags=["dialogue_styles", "少年", "网络梗", "Z 世代"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="dialogue-elder-wisdom",
        name="对话风格：老人腔 / 慢节奏+ 沧桑",
        narrative_summary="60+ 老人的对话特征：节奏慢+ 重复+ 跑题+ 引古+ 年代符号+ 心宽体胖式幽默+ 沧桑式哲学。是大家庭+ 怀旧+ 社区题材的关键。",
        content_json={
            "rhythm_features": "1) 慢节奏（不抢话+ 等下一口气）/ 2) 重复（关键句重复 2-3 次）/ 3) 跑题（话题中断+ 跳跃+ 看似无关其实有联系）/ 4) 长停顿（话说一半端起茶杯）",
            "vocabulary_features": "1) 引用过去（'我年轻那会儿'+ '想当年') / 2) 年代符号（粮票+ 永久自行车+ 老凤凰+ 钢精锅+ 万元户）/ 3) 老式称谓（'妞儿+ 小子+ 老姐姐'）/ 4) 民间俗语（'吃过的盐比你吃过的米还多'）",
            "philosophical_layer": "1) 心宽体胖式（'没事+都会过去的'）/ 2) 沧桑式（'人这一辈子+ 啥都见过')/ 3) 看穿不说破（'你自己心里有数')/ 4) 反向激励（明贬实褒）",
            "use_cases": "1) 大家庭+ 隔代关系 / 2) 怀旧+ 知青+ 文革题材 / 3) 社区+ 邻里 / 4) 主角的奶奶/姥姥/外公等关键角色",
            "anti_cliche": "不要纯写'慈祥老人讲道理'；让老人有自己的执拗+ 偏见+ 不可理喻的脾气（让人物真实）",
            "activation_keywords": ["老人腔", "怀旧", "慢节奏", "民间俗语", "大家庭"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("《活着》《兄弟》余华+ 《白鹿原》陈忠实+ 《大宅门》郭宝昌 老人对话模板综合")],
        tags=["dialogue_styles", "老人", "怀旧", "通用"],
    ),

    # ---------- plot_patterns (5) ----------
    MaterialEntry(
        dimension="plot_patterns", genre="校园",
        slug="plot-campus-three-act",
        name="情节模式：校园三幕式 / 暗恋-表白-毕业",
        narrative_summary="校园文最稳的三幕结构：高一/二（暗恋+ 朋友圈建立）→ 高二/三（表白+ 关系发展+ 高考压力）→ 高三/大学（毕业+ 分别+ 多年后重逢）。是青春疼痛文+ 治愈文的核心骨架。",
        content_json={
            "act_one_setup": "1) 转学/开学第一天 / 2) 偶遇暗恋对象 / 3) 朋友圈建立 / 4) 暗恋三个月（小心思+ 偷瞄+ 假装找笔） / 5) 第一次接触（值日同组+ 团队竞赛）",
            "act_two_confrontation": "1) 互相留意 / 2) 关系升温（一起复习+ 借笔记） / 3) 误会冲突（被人误解 + 第三者出现） / 4) 高考压力（焦虑+ 失眠） / 5) 关键时刻（高考前夕的告白 / 错过表白）",
            "act_three_resolution": "1) 高考结束 / 2) 毕业典礼 / 3) 分别（各奔东西） / 4) 多年后重逢（婚礼+ 同学会+ 偶遇） / 5) 重新选择 OR 永别",
            "key_scenes": "晨读+ 食堂+ 操场+ 黄昏教室+ 天台+ 校园樱花/槐花+ 校门口+ 公交车站+ 自习室",
            "famous_examples": "《最好的我们》八月长安+ 《致青春》辛夷坞+ 《匆匆那年》九夜茴+ 《沙漏》饶雪漫+ 《你的名字》新海诚",
            "anti_cliche": "不要让结局必然在一起；可以是错过+ 各自精彩+ 多年后偶遇+ 已为人父母",
            "activation_keywords": ["校园三幕", "暗恋", "高考", "毕业", "重逢"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("八月长安+辛夷坞+九夜茴 校园文头部模板综合")],
        tags=["plot_patterns", "校园", "三幕"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="末日",
        slug="plot-doomsday-five-act",
        name="情节模式：末日五幕式",
        narrative_summary="末日文典型五幕：1) 觉醒（D-day 前的预警 + D+0 爆发）/ 2) 求生（前两周独自 + 收编小队）/ 3) 立足（第一个庇护所 + 资源争夺）/ 4) 大战（基地间 + 异变体决战）/ 5) 人性终极考验（救人 vs 自保）。",
        content_json={
            "act_1_awakening": "D-7 异常征兆（新闻+ 网络谣言）/ D+0 爆发（第一只丧尸+ 城市混乱）/ D+3 异能觉醒+ 收编 1-2 个伙伴 / D+7 离开城市",
            "act_2_survival": "D+8-30 流亡（找食物+ 武器+ 庇护）/ 收编更多伙伴（5-10 人）/ 第一次失去队友 / 异变体强度提升",
            "act_3_settling": "找到第一个长期庇护所（地铁+ 学校+ 山洞+ 仓库）/ 内部秩序 / 资源短缺+ 人际矛盾 / 与其他基地接触+ 贸易",
            "act_4_war": "外部威胁（更大丧尸潮+ 敌对基地+ 政府/军方残部）/ 联盟 OR 独立 / 大战 / 主角进化（异能升级）",
            "act_5_humanity": "终极考验（救陌生人 vs 自保 / 救亲人 vs 杀同伴 / 选择独裁 vs 民主）/ 主角最终选择（人性 vs 兽性 / 集体 vs 个人）",
            "famous_examples": "《Walking Dead》/ 《I Am Legend》/ 《World War Z》+ 方想《卡徒》末日卷",
            "anti_cliche": "不要纯写'打怪升级'；让人性考验是真正的高潮（不是 boss 战 而是道德困境）",
            "activation_keywords": ["末日五幕", "觉醒", "求生", "庇护所", "人性考验"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("Walking Dead + Walking Dead 衍生小说 + 中国末日流头部作品综合")],
        tags=["plot_patterns", "末日", "五幕"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="灵异",
        slug="plot-occult-investigation-formula",
        name="情节模式：灵异查案+ 真相+ 超度",
        narrative_summary="灵异类标准三段式：1) 接案（客户求助 + 异象初探）/ 2) 调查（线索+ 历史考古+ 与鬼接触）/ 3) 真相+ 超度（揭露因果+ 化解怨气+ 客户得救 OR 牺牲）。每案 5-15 章一个完整闭环。",
        content_json={
            "act_1_intake": "1) 客户登门（亲属离奇死亡/ 家中闹鬼/ 自己被附身） / 2) 案情陈述 / 3) 主角接案（钱 + 因果 + 道义）/ 4) 现场勘查（罗盘+ 阴阳眼+ 香烛） / 5) 第一次撞鬼",
            "act_2_investigation": "1) 走访亲属+ 邻居+ 同事 / 2) 翻查历史档案 / 3) 与鬼对话（劝/ 斗/ 困）/ 4) 发现因果（鬼有原因 + 不是纯邪）/ 5) 找到关键物品+ 关键人",
            "act_3_resolution": "1) 揭露真相（往往是几十年前的旧仇怨+ 错误） / 2) 化解（道歉+ 烧纸+ 超度+ 还愿）/ 3) 鬼离去（哭笑离场） / 4) 客户得救 OR 主角自己付出代价（损寿+ 损神识） / 5) 案件归档",
            "famous_examples": "《鬼吹灯》单元+ 《盗墓笔记》单元+ 《茅山后裔》大力金刚不坏+ 《我当道士那些年》",
            "anti_cliche": "不要纯写'打鬼'；让 90% 鬼有真实苦衷（被冤+ 错爱+ 误解），主角需要的是'解'不是'打'",
            "activation_keywords": ["灵异", "查案", "超度", "因果", "茅山"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("天下霸唱+ 南派三叔+ 周浩晖灵异单元剧模板综合")],
        tags=["plot_patterns", "灵异", "查案"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="重生",
        slug="plot-rebirth-revenge-rebuild",
        name="情节模式：重生复仇+ 揭穿+ 重新选择",
        narrative_summary="重生文标准结构：1) 重生（睁眼+ 认清时间点） / 2) 复仇（揭穿前世害自己的人）/ 3) 重新选择（不只复仇 + 也修复亲情爱情）/ 4) 升华（前世做不到的事这一世做了）。",
        content_json={
            "act_1_rebirth": "1) 死前最后一刻 / 2) 重生瞬间（眩晕+ 不可置信） / 3) 反复确认（家人+ 日历+ 旧手机） / 4) 决心（这一次我一定要...）",
            "act_2_revenge": "1) 第一个目标（前世 1 号仇人）/ 2) 信息差 + 反向操作（用前世经历预判） / 3) 揭穿前世害自己的真相 / 4) 复仇执行（不是杀人 + 是断财路+ 揭穿身份+ 抢回机会）",
            "act_3_rebuild": "1) 修复亲情（前世忽视的家人 + 重新陪伴） / 2) 修复爱情（错过的人 / 错爱的人）/ 3) 重新选择职业方向 / 4) 阻止前世的错误（不是只复仇 + 是阻止悲剧重演）",
            "act_4_transcendence": "1) 反派最终败 / 2) 主角达到前世到不了的高度 / 3) 成全身边人 / 4) 平静的结局",
            "famous_examples": "《重生之独宠妖娆妻》+ 《重生小富婆》+ 《重生之名流巨星》+ 现代题材重生流",
            "anti_cliche": "不要纯写'复仇爽'；让主角发现复仇之外更重要的事（亲情+ 友情+ 自我）",
            "activation_keywords": ["重生", "复仇", "揭穿", "修复", "新选择"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("重生流流派代表作综合")],
        tags=["plot_patterns", "重生", "复仇"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="娱乐圈",
        slug="plot-entertainment-rise-fall",
        name="情节模式：娱乐圈起伏+ 资本+ 真才实学",
        narrative_summary="娱乐圈文标准模型：1) 入圈（从素人到三线）/ 2) 资本博弈（资源+ 反派陷害+ 塌房风险） / 3) 真才实学（用作品+ 实力争取尊重）/ 4) 顶峰（影后/ 顶流/ 国宝级 + 但人格保留初心）。",
        content_json={
            "act_1_entry": "1) 路人 / 2) 试镜+ 选秀+ 网红 / 3) 第一个角色 / 4) 第一次小爆 / 5) 三线 → 二线",
            "act_2_capital": "1) 公司签约 / 2) 第一份代言 / 3) 资本博弈（被换角+ 抢资源+ 八卦攻击） / 4) 反派陷害（前任+ 同期+ 资本敌对方） / 5) 塌房风险（人设崩塌+ 黑红）",
            "act_3_skill": "1) 转型（流量→ 实力派） / 2) 第一个艺术片 / 3) 拿奖（金鸡+ 百花+ 金马+ 奥斯卡） / 4) 评论圈认可 / 5) 与资本谈判力增强",
            "act_4_summit": "1) 顶流+ 影后 / 2) 选择性接戏（不为钱+ 为艺术）/ 3) 帮助新人 / 4) 离开舆论中心+ 保持创作 / 5) 国宝级地位",
            "famous_examples": "《一生一世》墨宝非宝+ 《三生三世》唐七公子+ 《当时年少春衫薄》木浮生+ 《全世界都说我喜欢你》藤萝为枝",
            "anti_cliche": "不要纯写'三个月顶流'+'男主救场'；让升级靠真实作品+ 长期努力（5-10 年时间跨度）",
            "activation_keywords": ["娱乐圈", "起伏", "资本", "实力派", "影后"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("墨宝非宝+ 唐七公子+ 木浮生 娱乐圈头部模板综合")],
        tags=["plot_patterns", "娱乐圈", "资本"],
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
