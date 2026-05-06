"""Batch 43: locale_templates + emotion_arcs + dialogue_styles depth

Fills under-served:
- locale_templates: niche-genre venues (直播间/选秀舞台/堂口/出马仙坛/末日地铁/赛博间)
- emotion_arcs: relationship arcs (parent-child/friendship-betrayal/master-disciple)
- dialogue_styles: voice-deep (内心独白节奏 / 留白 / 文白雅俗)
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
    # ---------- locale_templates (6) ----------
    MaterialEntry(
        dimension="locale_templates", genre="娱乐圈",
        slug="locale-streaming-room-mic",
        name="场景：百万直播间 / 头部主播工作室",
        narrative_summary="顶流主播的直播间。绿幕墙+ 32:9 弧形显示+8 颗补光+高级麦+观众弹幕大屏。空间小但每寸都精算过。能放下主角的孤独和紧张。",
        content_json={
            "physical_layout": "10-15㎡密闭间；地上一圈 PVC 防滑；正前方 32:9 显示+ 4K 摄像机+主光（柔光箱）+ 双侧补光（环形+蝶光）+ 顶部主灯+ 反光板；右后方贴墙的 4×8m 绿幕；左后方零食小吧台",
            "tech_props": "RodeWireless 麦克风+电容头戴+OBS 切换台+提词器（写关键梗+绝不能说的雷区清单）+ 弹幕大屏+ 后期混音师远程接入",
            "social_layer": "门外是经纪人（控时长）+ 后期粉丝群组（实时反馈）+ 母公司监控（流量考核）",
            "scene_use_cases": "1) 主角第一次百万人观看时的颤抖 / 2) 关键时刻断网/麦克风失效 / 3) 弹幕暴雷塌房 / 4) 老板突然推门进来",
            "anti_cliche": "不要纯写'帅哥美女吃喝'；要写空间内的孤独感+ 镜头之外的崩溃",
            "activation_keywords": ["直播间", "顶流", "工作室", "弹幕", "OBS"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("淘宝直播+斗鱼+B 站头部主播工作室综合调研")],
        tags=["locale", "娱乐圈", "直播间"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre="娱乐圈",
        slug="locale-talent-show-stage",
        name="场景：选秀舞台 / 末位淘汰",
        narrative_summary="选秀节目的舞台：360° 升降台+冰屏环绕+ 评委席+导师席+座位区分等级（A 班 / B 班 / 末位淘汰区）。每个细节都是阶级符号。",
        content_json={
            "physical_layout": "圆形主舞台 12m 直径+ 8m 升降；360° 冰屏背景+ 顶部移动灯阵+ 4 路鸟瞰摇臂；前排评委席（5 位+1 主席）+ 中区导师席（4 位）+ 后区学员席（A 班 30 人/B 班 30 人/淘汰区 10 人）",
            "ranking_props": "学员胸前挂牌（A/B/X）+ 抢话筒灯（按下亮起）+ 末位排名屏（每周播一次）",
            "social_layer": "导演组（在副控）/ 评委（前排打分）/ 导师（场上能护学员）/ 学员（按等级分座，互相竞争）",
            "scene_use_cases": "1) 主角第一次站到 A 班的爽点 / 2) 突然被踢到 B 班的耻辱 / 3) 导师抢人 / 4) 末位淘汰前的告别独白",
            "anti_cliche": "不要纯写'公平 PK'；要写后台资源+导演剪辑+评委私下沟通的暗箱",
            "activation_keywords": ["选秀", "舞台", "A 班", "导师抢人", "末位淘汰"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("《偶像练习生》《青春有你》《创造营》舞台综合")],
        tags=["locale", "娱乐圈", "选秀", "舞台"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre="末日",
        slug="locale-doomsday-subway-bunker",
        name="场景：末日地铁站 / 地下避难所",
        narrative_summary="末日后第一批幸存者藏身地：废弃地铁站。月台变床位+ 控制室变指挥部+ 隧道变运输线。封闭+黑暗+丧尸潮的环境。",
        content_json={
            "physical_layout": "地铁站 3 层结构：地表入口（已封死，留侧窗）→ 站厅层（哨岗+检疫）→ 月台层（住宿+物资仓+训练区）→ 隧道（运输+下水+逃生通道）",
            "tech_props": "应急柴油发电机（控制室）+ 老旧广播喇叭（喊话用）+ 马灯+矿灯+ 临时净水器（接列车水箱）+ 武器架（钢筋焊出的长矛+铁锹）",
            "social_layer": "幸存者 50-200 人；分管理层（站长+副站长+治安）+ 战斗组（巡逻+清丧）+ 后勤（炊事+医务）+ 普通幸存者（轮值）",
            "scene_use_cases": "1) 突然停电+丧尸潮（隧道里黑暗中传来咆哮）/ 2) 内部人偷物资被发现 / 3) 上层突然发现外面有救援信号 / 4) 男女主第一次接吻是借着马灯",
            "anti_cliche": "不要纯写'地铁=安全'；要写空气浑浊+ 资源争夺+ 老人想出去送死的伦理困境",
            "activation_keywords": ["末日", "地铁站", "避难所", "幸存者", "丧尸潮"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Metro_2033"), llm_note("Walking Dead 庇护所综合")],
        tags=["locale", "末日", "地铁", "避难所"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre="赛博朋克",
        slug="locale-cyberpunk-night-market",
        name="场景：赛博夜市 / 黑医生改造铺",
        narrative_summary="赛博朋克城市的最低端：夜市黑市+黑医生铺子+小吃摊+情报贩+ 改装店。霓虹+雨水+ NoodleCart+巷子深处。所有交易都在阴影里。",
        content_json={
            "physical_layout": "巷子 3-4m 宽+ 头顶霓虹招牌乱挂+ 地面常年湿+ 小吃摊（拉面/烤鸡架）+ 黑医生铺子（玻璃窗+ 简陋手术椅+老旧 cyberdeck）+ 情报贩（电脑+耳机+全息屏幕）",
            "tech_props": "便携手术包+ 第二手义体（堆在角落）+ 加密通讯器+ 黑市数据卡贩售机+ 拉面机器人（有意识的）",
            "social_layer": "顾客（黑帮+雇佣兵+流民）+ 店家（黑医生+情报贩+小贩）+ 治安巡逻（NCPD 偶尔突袭）+ 帮派保护费收取者",
            "scene_use_cases": "1) 主角第一次买义体 / 2) 黑医生告知改造代价 / 3) 情报贩卖给主角公司机密 / 4) 突袭中冲出店面追杀",
            "anti_cliche": "不要纯写'赛博风霓虹炫酷'；写普通人在夜市里靠卖肝/卖记忆为生的悲惨现实",
            "activation_keywords": ["赛博朋克", "夜市", "黑医生", "改造铺", "霓虹"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Cyberpunk_2077"), wiki("Blade_Runner"), llm_note("Ghost in the Shell 街景综合")],
        tags=["locale", "赛博朋克", "夜市", "黑市"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre="灵异",
        slug="locale-occult-altar-room",
        name="场景：出马仙堂口 / 仙家神坛",
        narrative_summary="东北民俗灵异题材的核心场景：出马仙堂口。供奉胡黄常蟒+祖师爷+师爷的神坛。屋内永远昏暗+ 香火缭绕+ 镜子全部蒙红布。",
        content_json={
            "physical_layout": "20-30㎡的家中独立间；正面神坛（三层，最上仙家+中层祖师+下层师爷）；左侧香炉+黄表纸+朱砂；右侧神签筒+ 铜铃+ 桃木剑；地面铺红布；屋角四面镜子蒙红布",
            "props": "供品（水果+酒+ 糕点+ 香烟）+ 师爷牌位+ 桃木剑+ 朱砂+ 黄表纸+ 罗盘+ 八卦镜+ 铜铃+ 师爷令牌（家传不外借）",
            "social_layer": "弟子（开堂者）+ 客户（来求帮）+ 师爷（仙家附身后说话的中间人）+ 仙家（看不见的，附在弟子身上）",
            "scene_use_cases": "1) 第一次开堂的紧张 / 2) 仙家附体说出客户家秘辛 / 3) 仙家发怒打砸 / 4) 半夜独坐听见仙家窃窃私语",
            "anti_cliche": "不要纯写'神棍迷信'；要写堂口的真实社会功能（社区心理咨询+ 邻里调解）",
            "activation_keywords": ["灵异", "出马仙", "堂口", "神坛", "胡黄常蟒"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("Chinese_folk_religion"), llm_note("东北出马仙民俗实地调研综合")],
        tags=["locale", "灵异", "出马仙", "堂口"],
    ),
    MaterialEntry(
        dimension="locale_templates", genre="校园",
        slug="locale-campus-rooftop",
        name="场景：校园天台 / 升国旗台",
        narrative_summary="校园文里所有秘密都发生在天台：表白、打架、自杀阻止、学霸独白。开放空间+俯瞰全校+下课铃声远远传来+栏杆只到腰部+ 风很大。",
        content_json={
            "physical_layout": "高三教学楼顶层；20×10m 平台+1.2m 矮护栏+水塔（圆柱）+太阳能板+排风口+ 锈蚀的铁门（应急锁，常被同学撬）",
            "atmospheric_details": "下课铃远远传来+ 操场上跑步声+ 春夏=晒到反光/秋冬=风大/雨天=滑+ 黄昏的夕阳染红水塔+ 远处可见城市轮廓",
            "scene_use_cases": "1) 学霸来这里独自背诵 / 2) 转学生在这里被霸凌 / 3) 男主在这里告白 / 4) 老师爬上来阻止学生跳楼 / 5) 烟头+情书的考古",
            "social_layer": "禁地（应急通道+不允许学生上）但实际上=学生的自由空间；老师睁眼闭眼；偶尔有保安巡逻",
            "anti_cliche": "不要纯写'天台告白'套路；可以写主角第一次站上来想跳又被风吹回来的瞬间",
            "activation_keywords": ["校园", "天台", "高考", "告白", "黄昏"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("八月长安《最好的我们》、九夜茴《匆匆那年》校园天台综合")],
        tags=["locale", "校园", "天台"],
    ),

    # ---------- emotion_arcs (3) ----------
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="emo-arc-parent-child-reconciliation",
        name="情感弧线：父子和解 / 父女隔阂",
        narrative_summary="父子/父女从冷战到理解到和解的经典弧线。常用结构：童年崇拜→青春叛逆→成年陌生→重大事件→和解。每段都有具体的代表性场景。",
        content_json={
            "five_stages": "1) 童年崇拜期（8-12 岁，父亲是英雄）/ 2) 青春叛逆期（13-19 岁，恨父亲不理解）/ 3) 成年陌生期（20-30 岁，逢年过节才回家）/ 4) 重大事件触发期（父亲生病/离世/犯错）/ 5) 和解期（理解父亲也是普通人）",
            "common_triggers": "父亲生病住院 / 父亲做出不光彩事被孩子撞破 / 父亲老了说不清楚话 / 父亲女主诞生 / 父亲在孩子毕业典礼上独自坐着",
            "key_dialogue": "1) 童年：'爸爸是天下第一'/ 2) 叛逆：'你根本不懂我'/ 3) 陌生：'妈让我打电话问你'/ 4) 触发：'你怎么不告诉我...?' / 5) 和解：'爸，我懂了'",
            "anti_cliche": "不要纯煽情；让和解之后还有日常的尴尬和疏远（关系不可能完全修复）",
            "activation_keywords": ["父子", "父女", "和解", "代沟", "童年崇拜"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("《情感勒索》Emotional Blackmail + 心理咨询常见亲子模板")],
        tags=["emotion_arcs", "亲子", "和解", "通用"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="emo-arc-friendship-betrayal-trust",
        name="情感弧线：友情 → 背叛 → 重建信任",
        narrative_summary="经典三段式：携手开始→ 一方背叛→ 痛苦割裂→ 重建or 永别。背叛动机要复杂（不是纯坏），重建过程要痛苦。",
        content_json={
            "five_stages": "1) 携手期（共同目标+互相托付）/ 2) 裂痕期（一方有秘密/价值观分叉）/ 3) 背叛期（明显的背叛事件，如卖给对手/抢功）/ 4) 痛苦割裂期（对峙+分手）/ 5) 重建期 OR 永别期（看故事走向）",
            "betrayal_motivations": "1) 个人利益（钱+权）/ 2) 价值观分叉（道德选择）/ 3) 第三方挑拨 / 4) 自我保全（被威胁时弃友）/ 5) 误解（其实不是真背叛）",
            "rebuild_conditions": "1) 背叛者真心忏悔 + 2) 受害者愿意接受 + 3) 中间有补偿行为 + 4) 双方关系结构改变（不再是平等好友，可能变成战友/上下级）",
            "anti_cliche": "不要纯写'背叛者后悔自杀'；让背叛者活着+继续在受害者眼前+迫使日常面对",
            "activation_keywords": ["友情", "背叛", "重建", "信任", "三段式"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("Carl Jung 阴影论 + 亚里士多德友情类型综合")],
        tags=["emotion_arcs", "友情", "背叛", "通用"],
    ),
    MaterialEntry(
        dimension="emotion_arcs", genre=None,
        slug="emo-arc-master-disciple-bond",
        name="情感弧线：师徒情 / 传承+反叛+继承",
        narrative_summary="师徒关系的经典弧线：拜师→ 学习+敬仰→ 矛盾分裂→ 师傅死/失踪→ 徒弟继承衣钵或反叛传统。东方文化里的核心情感纽带之一。",
        content_json={
            "five_stages": "1) 拜师期（崇敬+三步一拜）/ 2) 学艺期（敬仰+模仿）/ 3) 自我觉醒期（开始质疑师傅）/ 4) 冲突分裂期（理念分叉/师傅犯错被徒弟发现）/ 5) 传承期（师傅死，徒弟继承衣钵或彻底反叛）",
            "cultural_layer": "东方师徒='一日为师终生为父'；西方=技能传授+导师辞职后即结束。两种文化对'师徒情'的强度差异大",
            "common_dramatic_arcs": "1) 师傅是隐藏反派 / 2) 师傅一生未尽愿，徒弟代为完成 / 3) 师徒爱上同一人 / 4) 师傅最后死在徒弟手里（不是徒弟杀的而是替身/挡刀）",
            "anti_cliche": "不要纯写'师傅是好人徒弟敬爱'；让师傅有缺陷（嫉妒/狭隘/隐藏野心），徒弟也要质疑",
            "activation_keywords": ["师徒", "传承", "反叛", "拜师", "衣钵"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("Star Wars Yoda/Luke + 仙侠武侠师徒模板综合")],
        tags=["emotion_arcs", "师徒", "传承", "通用"],
    ),

    # ---------- dialogue_styles (3) ----------
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="dialogue-internal-monologue-rhythm",
        name="对话风格：内心独白节奏 / 意识流",
        narrative_summary="第一人称或贴近视角第三人称里的内心独白。要节奏化：长句+短句+断句+重复。让读者感受到角色的呼吸+心跳+ 焦虑节奏。",
        content_json={
            "rhythm_patterns": "1) 长-短交替（'她想着所有那些没说出口的话——以及那些说出来又收不回的。可她还是没开口。'）/ 2) 重复+变奏（'再五分钟。再五分钟。再五分钟，他就来了。'）/ 3) 断句节奏（'门。开了。是他。'）/ 4) 假设句堆叠（'如果她当初说了。如果她当初没说。如果...')",
            "punctuation_use": "破折号——做停顿+ 删节号......做迟疑+ 感叹号！做爆发+ 问号？做不安",
            "tense_layer": "现在时拉近+ 过去时拉远+ 未来时焦虑+ 完成时悔恨",
            "model_examples": "Virginia Woolf《Mrs Dalloway》/ 王安忆《长恨歌》/ 米兰·昆德拉",
            "anti_cliche": "不要纯写'她想'+长段抒情；用断句+ 节奏+ 留白制造焦虑",
            "activation_keywords": ["内心独白", "意识流", "节奏", "断句", "Virginia Woolf"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Stream_of_consciousness"), llm_note("Woolf, Joyce, Faulkner 意识流综合")],
        tags=["dialogue_styles", "内心独白", "意识流"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="dialogue-iceberg-omission",
        name="对话风格：留白省略 / 海明威冰山",
        narrative_summary="对话中刻意不说出关键信息，让读者从沉默+ 转移话题+ 重复行为里推断。海明威经典手法。适合冷战+ 隐忍+ 暗恋。",
        content_json={
            "techniques": "1) 言他（不直接回答而谈天气/食物/路况）/ 2) 重复无意义动作（搅咖啡 3 次/ 整理桌布）/ 3) 沉默用动作描写代替（'她没说话。只是看着窗外'）/ 4) 突然的话题转换",
            "use_cases": "1) 离婚谈判（避免说'离婚'两字）/ 2) 父子和解（避免说'对不起'）/ 3) 暗恋表白前的犹豫 / 4) 重病诊断后的家庭聚餐",
            "model_examples": "海明威《白象似的群山》/ 余华《活着》/ 是枝裕和《海街日记》",
            "anti_cliche": "不要把所有情感都让人物说出来；让读者自己从沉默+ 动作里读出来",
            "activation_keywords": ["留白", "海明威", "冰山", "省略", "沉默"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[wiki("Iceberg_theory"), llm_note("Hemingway 'Hills Like White Elephants' 经典留白")],
        tags=["dialogue_styles", "留白", "海明威", "通用"],
    ),
    MaterialEntry(
        dimension="dialogue_styles", genre=None,
        slug="dialogue-classical-vernacular-mix",
        name="对话风格：文白夹杂 / 古言现代撞击",
        narrative_summary="古代题材里夹现代词汇，或现代背景里突然蹦古词，制造反差萌或讽刺感。穿越文+ 古风轻喜剧+ 网文常用。",
        content_json={
            "common_mixings": "1) 古人说现代词（'卿卿我我...卧槽这梨真甜！'）/ 2) 现代人说古词（'美人请上车，本郡主送你去地铁'）/ 3) 公文文白互转（古代奏折＋现代 PPT 用语）",
            "style_register_levels": "雅言（朝堂正式）→ 文言（官话）→ 半文半白（小说+话本）→ 白话（市井）→ 俚语（粗话）",
            "use_cases": "1) 穿书反派郡主吐槽 / 2) 修仙大佬在凡间假装游客 / 3) 古风轻喜剧的搞笑桥段 / 4) 系统提示音+ 古风世界",
            "model_examples": "金庸《鹿鼎记》韦小宝白话+ 经典古风的反差 / 莺莺草《反派他过分美丽》/ 巫哲《撒野》方言+ 普通话",
            "anti_cliche": "不要硬塞古词显摆；要在角色性格逻辑内（穿越+ 学者+ 文盲不同表现）",
            "activation_keywords": ["文白", "夹杂", "穿越", "古风", "反差"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("金庸+莺莺草+巫哲文白杂糅模板综合")],
        tags=["dialogue_styles", "文白", "穿越", "古风"],
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
