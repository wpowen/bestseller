#!/usr/bin/env python3
"""Build the 玄幻 (xuanhuan / high-fantasy cultivation) material-library seed.

Why a builder instead of hand-written JSONL
--------------------------------------------
The ~300 玄幻/仙侠 桥段 supplied by the curator are *keyword fragments*
(机缘夺宝 / 闯阵法 / 心魔较量 / 御兽师 …). Dumping them as 300 thin rows
would be low-signal and would just bloat retrieval. Instead we cluster
them into ~8 themes and emit a **smaller set of richly-structured,
anti-cliché scene/plot templates**, folding the raw keywords back in as
``typical_tricks`` / ``variants`` sub-elements so every桥段 is still
*covered* and *searchable* — but surfacing happens at the template level.

These rows live in the GLOBAL ``material_library`` (genre = ``玄幻``,
``sub_genre = None`` so they match every 玄幻 book regardless of its own
sub-genre). From there the existing funnel keeps injection bounded:

    global pool ─► PlotForge differentiation (+novelty guard)
                ─► §slug project materials  (outline level)
    global pool ─► query_library top-k=4 by per-scene relevance
                ─► soft "灵感" reference  (drafter level, phase-gated)

So pool size never leaks into the outline: each scene only ever sees the
few candidates most relevant to *that* beat. See
``material_library_reference.render_library_soft_reference_block`` and
``material_forge/plot_forge.py`` (which explicitly keeps scene templates
"optional, not mandated" — the L4 雷同化 fix).

Usage
-----
    uv run python scripts/build_xuanhuan_seed.py
    # then preview / import:
    uv run python scripts/import_material_jsonl.py \
        data/seed_materials/xuanhuan_seed.jsonl --dry-run
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent.parent / "data" / "seed_materials" / "xuanhuan_seed.jsonl"

GENRE = "玄幻"
SRC = "user_curated"


def cite(note: str) -> dict[str, Any]:
    return {"title": "玄幻/仙侠桥段库（用户整理）", "note": note}


def st(
    slug: str,
    name: str,
    summary: str,
    *,
    beats: list[str],
    emotional_peak: str,
    payoff_angle: str,
    typical_tricks: list[str],
    common_pitfall: str,
    subversion: str,
    fits_phase: list[str],
    tags: list[str],
) -> dict[str, Any]:
    """A reusable scene skeleton (dimension=scene_templates)."""
    return {
        "dimension": "scene_templates",
        "slug": slug,
        "name": name,
        "narrative_summary": summary,
        "content_json": {
            "beats": beats,
            "emotional_peak": emotional_peak,
            "payoff_angle": payoff_angle,
            "typical_tricks": typical_tricks,
            "common_pitfall": common_pitfall,
            "subversion": subversion,
            # which chapter phases this set-piece naturally serves; the
            # drafter's phase-aware gate prefers templates that match.
            "fits_phase": fits_phase,
        },
        "genre": GENRE,
        "sub_genre": None,
        "tags": ["scene", *tags],
        "source_type": SRC,
        "source_citations": [cite("场景桥段聚类")],
        "confidence": 0.8,
    }


def pp(
    slug: str,
    name: str,
    summary: str,
    *,
    stages: list[dict[str, str]],
    payoff: str,
    common_pitfall: str,
    subversion: str,
    tags: list[str],
) -> dict[str, Any]:
    """A long-arc structural pattern (dimension=plot_patterns)."""
    return {
        "dimension": "plot_patterns",
        "slug": slug,
        "name": name,
        "narrative_summary": summary,
        "content_json": {
            "stages": stages,
            "payoff": payoff,
            "common_pitfall": common_pitfall,
            "subversion": subversion,
        },
        "genre": GENRE,
        "sub_genre": None,
        "tags": ["main-plot", *tags],
        "source_type": SRC,
        "source_citations": [cite("主线骨架聚类")],
        "confidence": 0.82,
    }


def motif(slug: str, name: str, summary: str, *, exprs: list[str], tags: list[str]) -> dict[str, Any]:
    return {
        "dimension": "thematic_motifs",
        "slug": slug,
        "name": name,
        "narrative_summary": summary,
        "content_json": {"expressions": exprs},
        "genre": GENRE,
        "sub_genre": None,
        "tags": ["motif", *tags],
        "source_type": SRC,
        "source_citations": [cite("母题聚类")],
        "confidence": 0.78,
    }


def emo(slug: str, name: str, summary: str, *, curve: list[str], tags: list[str]) -> dict[str, Any]:
    return {
        "dimension": "emotion_arcs",
        "slug": slug,
        "name": name,
        "narrative_summary": summary,
        "content_json": {"curve": curve},
        "genre": GENRE,
        "sub_genre": None,
        "tags": ["emotion", *tags],
        "source_type": SRC,
        "source_citations": [cite("情绪弧聚类")],
        "confidence": 0.78,
    }


ENTRIES: list[dict[str, Any]] = []

# ════════════════════════════════════════════════════════════════════════
# scene_templates — 8 clusters, ~300 桥段 folded in as typical_tricks
# ════════════════════════════════════════════════════════════════════════

# ── 1. 机缘夺宝 / 秘宝 ────────────────────────────────────────────────────
ENTRIES += [
    st(
        "xuanhuan-scene-auction-hidden-treasure",
        "拍卖会暗夺至宝",
        "拍卖会上一件被当作废物的拍品，主角一眼认出其真正价值（器灵未醒/灵宝出世/封印未解）。明面竞价，暗里有高阶势力也盯上它。三拍：识货（不动声色）—哄抬（势力施压、灵石将尽）—险得（以非财富手段拿下，或拍品当场显灵认主）。",
        beats=["识货", "哄抬", "险得"],
        emotional_peak="拍品认主/显灵的瞬间，全场哗然",
        payoff_angle="主角的眼光与底牌而非钱多，凸显其独特来历",
        typical_tricks=["拍卖会", "宝物显灵", "灵宝出世", "灵石匮乏", "器灵", "本命法宝", "奇珍异宝", "传位神秘人"],
        common_pitfall="变成纯比谁灵石多→失去机锋",
        subversion="真正的至宝是拍卖师本人/陪标的不起眼之物",
        fits_phase=["escalation", "twist"],
        tags=["treasure", "auction", "fortune"],
    ),
    st(
        "xuanhuan-scene-ruin-relic-claim",
        "古迹夺宝惊动守卫",
        "主角潜入古墓/旧宅秘室/地底秘道取宝，触动机关与守卫（枯骨复苏/阵纹亮起/图案警示）。取宝—惊动—险逃三段，最后常以跃入激流/冲破结界耗尽真气脱身。",
        beats=["潜入", "取宝惊动守卫", "险逃"],
        emotional_peak="抱宝跃入激流、回望坍塌的瞬间",
        payoff_angle="代价换收获：得宝必伤本、必结仇或必留隐患",
        typical_tricks=["盗宝", "古墓探险", "旧宅秘室", "地底秘道", "枯骨", "图案", "惊动守卫", "跃入激流", "逃出升天", "毁宝", "传家宝", "采药", "异草", "药草守护兽", "发现矿脉", "稀有灵器", "珍稀材料"],
        common_pitfall="守卫智商下线、机关只是摆设",
        subversion="守卫是宝物的旧主人/守宝者其实在等一个特定来人",
        fits_phase=["escalation", "twist", "climax"],
        tags=["treasure", "ruin", "heist"],
    ),
    st(
        "xuanhuan-scene-contested-spoils",
        "宝物争夺反被夺",
        "多方为同一奇珍异宝/矿脉/异草厮杀，主角被抢宝物后设局反抢。先失（被压制、被擒、宝物易主）后取（利用器灵认主/空间挪移/借势）。",
        beats=["多方争夺", "被抢/失宝", "反抢翻盘"],
        emotional_peak="反抢成功后对夺宝者的一句冷语",
        payoff_angle="智取+底牌，而非单纯更强",
        typical_tricks=["抢夺宝物", "被抢宝物", "反抢宝物", "夺宝", "赌石", "芥子空间", "洞府", "毁宝", "钻空子"],
        common_pitfall="反抢靠突然变强而非伏笔",
        subversion="主角故意让其被抢，宝物本身是诱饵/定位法器",
        fits_phase=["escalation", "twist"],
        tags=["treasure", "contest"],
    ),
]

# ── 2. 境界突破 / 修炼 ────────────────────────────────────────────────────
ENTRIES += [
    st(
        "xuanhuan-scene-bottleneck-breakthrough",
        "闭关破境渡心障",
        "主角卡在瓶颈（遭遇瓶颈），闭关/打坐/辟谷苦修，在外界倒计时压力下（雷劫将至/大比在即/仇敌逼近）强行突破。突破伴随身体异变与代价，而非轻飘升级。",
        beats=["卡瓶颈", "苦修压抑", "临界突破"],
        emotional_peak="境界壁垒碎裂、灵气灌体的轰鸣",
        payoff_angle="突破必有代价：寿元/根基/暴露/隐患",
        typical_tricks=["突破瓶颈", "遭遇瓶颈", "闭关", "闭关修炼", "打坐", "辟谷", "炼体", "悟道", "习得功法", "偷练武功", "神功大成", "升级", "新的等级"],
        common_pitfall="无痛升级=无感升级，缺前置压缩",
        subversion="突破后发现境界提升带来新的诅咒/被更高存在察觉",
        fits_phase=["setup", "escalation"],
        tags=["cultivation", "breakthrough"],
    ),
    st(
        "xuanhuan-scene-qi-deviation",
        "走火入魔心魔较量",
        "主角修炼逆天功法/强行突破/动情动怒，走火入魔，意识坠入心魔幻境。心魔以最痛的记忆/最深的欲望现身，须以本心而非武力破之。",
        beats=["催功", "入魔幻境", "本心破魔"],
        emotional_peak="直面心魔、说出/斩断执念的一刻",
        payoff_angle="内在成长外化为战力，凸显人物弧",
        typical_tricks=["走火入魔", "心魔较量", "心智失常", "悟道", "痛苦记忆", "封存异能", "蛊惑人心", "因爱成魔", "思念成魔"],
        common_pitfall="心魔只是打一架、与人物创伤无关",
        subversion="心魔说的是真话；战胜它反而是自欺",
        fits_phase=["twist", "climax"],
        tags=["cultivation", "inner-demon"],
    ),
    st(
        "xuanhuan-scene-sudden-ascension",
        "异象天降骤然飞升",
        "主角顿悟/服食灵物/血脉觉醒，引动天降异象（一夜飞升/羽化成蝶式蜕变），周遭势力的认知被一举刷新。重点写旁观者的认知崩塌而非光效。",
        beats=["异象起", "众人侧目", "实力跃迁定格"],
        emotional_peak="异象笼罩时，曾轻视者的脸色",
        payoff_angle="地位反转，靠'被看见'的层次反应放大",
        typical_tricks=["天降异象", "一夜飞升", "羽化成蝶", "御剑", "神功护体", "升级反杀", "升级打脸"],
        common_pitfall="只堆光效不写人，爽点空转",
        subversion="异象其实是大劫的前兆/招来杀身之祸",
        fits_phase=["climax", "resolution_hook"],
        tags=["cultivation", "ascension", "face-slap"],
    ),
]

# ── 3. 宗门门派 / 政治 ────────────────────────────────────────────────────
ENTRIES += [
    st(
        "xuanhuan-scene-sect-trial-sabotage",
        "宗门考核遭暗算",
        "测试灵根/宗门考核/晋升大典上，对立派系（同门打压/阴险掌门/内讧）暗中做手脚——咒符、涂毒、气机牵制。登场—异变—翻盘三拍，翻盘方式须个人化（信物/标志动作/隐藏底牌）。",
        beats=["登场受测", "察觉异变", "个人化翻盘"],
        emotional_peak="翻盘后对幕后者的一句话/一个眼神",
        payoff_angle="把'考核'变成'识破暗算+反将一军'",
        typical_tricks=["测试灵根", "宗门考核", "考核", "晋升", "同门打压", "内讧", "阴险掌门", "比武", "比武大会", "门派排行", "鸿门宴", "妒火中烧"],
        common_pitfall="翻盘过程技术化堆料，失去情绪",
        subversion="主考长老才是真正护着主角/下手的另有其人",
        fits_phase=["escalation", "twist", "climax"],
        tags=["sect", "trial", "face-slap"],
    ),
    st(
        "xuanhuan-scene-mountain-apprenticeship",
        "上山学艺拜入师门",
        "主角上山学艺/门派求学，经历招收弟子的甄别、外门到内门的攀爬、师兄师妹的初识与摩擦。日常感建立关系网与世界观，埋下后续羁绊与背叛的种子。",
        beats=["入门甄别", "立足外门", "结下同门关系"],
        emotional_peak="第一次被师门真正接纳/被师长点名",
        payoff_angle="关系网铺垫，为后续打压、护短、背叛蓄势",
        typical_tricks=["上山学艺", "门派求学", "门派探秘", "外门弟子", "内门弟子", "领事弟子", "客卿长老", "太上长老", "师兄师妹", "师姐师弟", "收徒弟", "收养孤儿", "招亲", "聚会", "论道"],
        common_pitfall="宗门只是背景板，弟子全是工具人",
        subversion="收他入门的师父别有目的/师门本身是仇家的延伸",
        fits_phase=["setup", "hook"],
        tags=["sect", "apprenticeship", "worldbuilding"],
    ),
    st(
        "xuanhuan-scene-sect-power-struggle",
        "门派斗争夺权位",
        "宗门内部或宗门之间的权力倾轧：掌门更替、客卿争位、太上长老角力。主角被卷入站队，须在不暴露底牌的前提下借势、离间、反将。",
        beats=["卷入站队", "暗中角力", "借势反将"],
        emotional_peak="局势翻转、对手意识到被算计的一刻",
        payoff_angle="权谋智斗，凸显主角城府与势力运营",
        typical_tricks=["门派斗争", "阴险掌门", "内讧", "秘密协议", "收买", "故意泄密", "示警", "设陷阱", "条件交换", "师门任务", "晋升"],
        common_pitfall="权谋只是开会吵架，无实际筹码",
        subversion="主角扶持的'盟友'才是终极对手",
        fits_phase=["escalation", "twist"],
        tags=["sect", "politics", "scheme"],
    ),
]

# ── 4. 情感羁绊 / 虐恋 ────────────────────────────────────────────────────
ENTRIES += [
    st(
        "xuanhuan-scene-fated-encounter",
        "险境结缘渐生情愫",
        "主角与未来道侣在险境中初遇（英雄救美/美救英雄/养伤/共患难），从戒备到日久生情。情愫在并肩与误会间起伏，赠玉/互诉衷肠作为情感锚点。",
        beats=["险境初遇", "共患难破冰", "情愫暗生"],
        emotional_peak="赠玉/互诉衷肠/一次不经意的守护",
        payoff_angle="感情线作为升级流的'呼吸口'与软肋",
        typical_tricks=["一见钟情", "日久生情", "渐生情愫", "互诉衷肠", "赠玉", "英雄救美", "美人计", "养伤", "共患难", "惺惺相惜", "结为道侣", "道侣", "梦中情人", "登门求见"],
        common_pitfall="一见钟情无铺垫、感情突兀",
        subversion="对方接近是带着任务的卧底/前世旧识",
        fits_phase=["breathe", "setup"],
        tags=["romance", "bond"],
    ),
    st(
        "xuanhuan-scene-love-becomes-demon",
        "因爱成魔孽缘再续",
        "至亲/爱人阴阳相隔或背叛，主角（或反派）因爱成魔、思念成魔，黑化或踏入魔道。前世情人/孽缘再续的重逢，把私人情感推到天人交战。",
        beats=["失去/背叛", "执念滋长", "重逢对峙"],
        emotional_peak="重逢时'故作无情'下的崩裂",
        payoff_angle="情感驱动的黑化，比纯反派更有共情",
        typical_tricks=["因爱成魔", "思念成魔", "阴阳相隔", "孽缘再续", "前世情人", "故作无情", "移情别恋", "棒打鸳鸯", "师徒虐恋", "心碎", "以死明志"],
        common_pitfall="黑化无逻辑、为虐而虐",
        subversion="所谓'背叛'是对方为护他而设的局",
        fits_phase=["twist", "climax"],
        tags=["romance", "tragedy", "fall"],
    ),
    st(
        "xuanhuan-scene-forced-marriage-upset",
        "婚礼变局抗婚错嫁",
        "政治/家族联姻（计划完婚/抢婚/错嫁）中途生变，主角拒绝成亲或婚礼意外暴雷。喜事变战场，借禁制消除/身份揭穿翻盘。",
        beats=["筹备喜事", "婚礼暴雷", "翻局脱身"],
        emotional_peak="当众撕破婚约/真身份揭晓",
        payoff_angle="把'喜庆场'反转成'打脸场'",
        typical_tricks=["计划完婚", "抢婚", "错嫁", "拒绝成亲", "婚礼意外", "违心答应", "禁制消除", "高岭之花", "假扮身份"],
        common_pitfall="婚礼桥段套路化、缺乏当事人动机",
        subversion="抗婚者其实早已与对方暗通款曲",
        fits_phase=["twist", "climax"],
        tags=["romance", "wedding", "reversal"],
    ),
    st(
        "xuanhuan-scene-mythic-love-interest",
        "异族倾城牵动三界",
        "青丘狐狸/南海鲛人/洛水女神/花神这类异族绝色登场，人妖相恋/仙魔相恋触动族群禁忌与三界律条，私情升格为势力与天道的对抗。",
        beats=["惊艳登场", "禁忌相恋", "族群/天道施压"],
        emotional_peak="为爱违逆族规/天条的抉择",
        payoff_angle="把恋爱线接进世界观主冲突",
        typical_tricks=["青丘狐狸", "南海鲛人", "洛水女神", "花神陨落", "人妖相恋", "仙魔相恋", "魅惑众生", "魅惑", "妖女"],
        common_pitfall="异族只是美貌标签、无族群逻辑",
        subversion="倾城者是为复族灭世而来",
        fits_phase=["setup", "escalation"],
        tags=["romance", "mythic-race"],
    ),
]

# ── 5. 危机 / 追杀 / 围困 ────────────────────────────────────────────────
ENTRIES += [
    st(
        "xuanhuan-scene-hunt-and-escape",
        "强敌追杀死里逃生",
        "主角被高阶势力盯上（暗中追杀/围捕/逃离魔掌），重伤、封印、灵力枯竭下逃命。绝境—自投罗网式诱敌/借地形—险脱。逃生靠脑子与代价，不靠天降。",
        beats=["被盯上", "重伤逃命", "险脱/反咬"],
        emotional_peak="绝境中赌命一搏的瞬间",
        payoff_angle="弱势期的张力与机锋",
        typical_tricks=["逃离魔掌", "围捕", "围困旧宅", "自投罗网", "被擒", "人质", "被重创", "封印", "内伤封印", "死里逃生", "拖累", "受困", "钻空子", "逃出升天"],
        common_pitfall="追兵降智、主角主角光环硬抗",
        subversion="追杀是为把他逼向某个预设之地",
        fits_phase=["escalation", "twist"],
        tags=["peril", "chase"],
    ),
    st(
        "xuanhuan-scene-poison-and-captivity",
        "中毒受制暗室周旋",
        "主角被下药/中毒/诱入暗室/挟持，灵力被压、行动受限。在受制状态下靠把脉/解毒/言语博弈翻盘，把'弱'写成'险中带智'。",
        beats=["落入圈套", "受制周旋", "解局反制"],
        emotional_peak="识破毒计、反将一军的对话",
        payoff_angle="信息与心理战胜过蛮力",
        typical_tricks=["中毒", "下毒", "下药", "用迷药", "诱入暗室", "挟持", "迷晕", "把脉", "病入膏肓", "复元", "恢复法力", "条件交换", "以死相胁", "威胁", "反指控"],
        common_pitfall="解毒靠金手指一键清除",
        subversion="下毒者是想救他/毒是唯一的解药",
        fits_phase=["setup", "escalation"],
        tags=["peril", "poison", "captivity"],
    ),
    st(
        "xuanhuan-scene-clan-massacre-vow",
        "灭门血仇立誓重振",
        "家族/宗门遭灭门灭族，主角作为遗孤死里逃生，于废墟前立誓。临终遗言/传家宝/病重托孤交接，把仇恨锚点与责任一并压上。常作开篇或卷首引擎。",
        beats=["突遭灭门", "废墟立誓", "背负前行"],
        emotional_peak="临终托付/废墟独白的那一刻",
        payoff_angle="为整部书立起最硬的情感锚点",
        typical_tricks=["灭门", "灭族", "重振家族", "遗孤", "病重托孤", "临终遗言", "传家宝", "大义灭亲", "替死", "牺牲自己", "骨肉情深", "血脉"],
        common_pitfall="灭门只为推动剧情、死者毫无存在感",
        subversion="灭门是主角某位至亲亲手默许/参与的",
        fits_phase=["hook", "setup"],
        tags=["peril", "revenge-seed", "tragedy"],
    ),
    st(
        "xuanhuan-scene-beast-tide-tribulation",
        "兽潮天劫共赴危局",
        "兽潮/天灾大劫/雷劫/受降天罪等群体级灾变压境，全城/全宗共御。主角在群像危局中担起关键一环，个人命运与众生存亡绑定。",
        beats=["灾变压境", "众志御敌", "关键扭转"],
        emotional_peak="主角顶住缺口、众人态度转变的一刻",
        payoff_angle="把个人战力放进'守护苍生'的群像里",
        typical_tricks=["兽潮", "天灾大劫", "神仙大劫", "人界大劫", "雷神", "受降天罪", "引发天劫", "世界毁灭危机", "拯救苍生", "拯救同伴", "怨气", "献祭"],
        common_pitfall="大场面只写规模不写具体人的取舍",
        subversion="灾变正是主角某次行动的连锁后果",
        fits_phase=["climax"],
        tags=["peril", "calamity", "ensemble"],
    ),
]

# ── 6. 秘境探索 / 机关古迹 ────────────────────────────────────────────────
ENTRIES += [
    st(
        "xuanhuan-scene-secret-realm-trial",
        "秘境试炼闯关夺机缘",
        "误入遗迹/试炼之地/秘境寻宝，层层机关与试炼（心性、战力、悟性）筛人。闯阵—解谜—得传承三段，机缘与杀机并存。",
        beats=["误入秘境", "闯阵解谜", "得机缘/触禁制"],
        emotional_peak="参透阵眼/获得传承认可的瞬间",
        payoff_angle="机缘需以'解开'获得，而非白捡",
        typical_tricks=["误入遗迹", "误入机关", "误入禁地", "试炼之地", "秘境寻宝", "闯阵法", "符文解密", "冲破结界耗真气", "巧遇高人", "意外发现", "大千世界", "探索新界", "发现新界"],
        common_pitfall="机关只是关卡、与世界观无关",
        subversion="秘境是某位前辈的意识残留在挑选继承人/在筛除主角",
        fits_phase=["escalation", "twist"],
        tags=["exploration", "secret-realm", "puzzle"],
    ),
    st(
        "xuanhuan-scene-formation-master-duel",
        "阵法对决以智破势",
        "阵法师布局，主角以阵破阵/借阵杀敌（闯阵法/冲破结界）。强调阵理与节奏：识阵—引敌入阵—反转阵眼，智力对抗胜过硬刚。",
        beats=["识阵", "引敌入彀", "反夺阵眼"],
        emotional_peak="阵眼易主、强敌反陷囹圄的一刻",
        payoff_angle="把'战斗'写成'谋局'，凸显职业专精",
        typical_tricks=["阵法", "阵法师", "阵修传承", "闯阵法", "冲破结界", "结界", "符文解密", "傀儡师", "作妖"],
        common_pitfall="阵法只是特效名词、无规则可循",
        subversion="布阵者是主角自己布给'未来的自己'的局",
        fits_phase=["escalation", "twist", "climax"],
        tags=["exploration", "formation", "tactics"],
    ),
    st(
        "xuanhuan-scene-underworld-passage",
        "魂入地府过忘川",
        "主角神魂入地府/过忘川河/坠血魔池，于轮回与执念之地直面生死与因果。阴森氛围与规则解谜并重，归来必带改变（记忆/契机/封印）。",
        beats=["魂坠幽冥", "直面执念/规则", "渡劫归来"],
        emotional_peak="忘川边'记得'还是'忘记'的抉择",
        payoff_angle="把生死观与因果设定具象化",
        typical_tricks=["地府", "过忘川河", "轮回", "血魔池", "怨气", "幽灵", "冥幡", "冥族", "封印", "起死回生"],
        common_pitfall="幽冥只是换地图刷怪",
        subversion="地府要回收的'债主'正是主角自己",
        fits_phase=["twist", "climax"],
        tags=["exploration", "underworld", "karma"],
    ),
]

# ── 7. 炼制 / 职业流 ──────────────────────────────────────────────────────
ENTRIES += [
    st(
        "xuanhuan-scene-alchemy-forging-feat",
        "炼丹炼器一鸣惊人",
        "炼丹师/炼器师当众炼制（炼丹/炼器/重铸武器），在材料匮乏或火候极限下成就异品，颠覆众人轻视。过程详写火候/材料/心法，成丹/成器的'品阶跃升'是爽点核心。",
        beats=["受质疑接活", "极限炼制", "异品出炉"],
        emotional_peak="丹成/器成、品阶碾压预期的一刻",
        payoff_angle="职业专精带来的'技术性打脸'",
        typical_tricks=["炼丹师", "炼丹", "炼制丹药", "绝情丹", "炼器师", "炼器", "重铸武器", "丹修传承", "器修传承", "符修", "赌石", "珍稀材料", "异草"],
        common_pitfall="炼制只报结果、无过程张力",
        subversion="成品有缺陷/暗藏代价，埋下后续伏笔",
        fits_phase=["escalation", "twist"],
        tags=["crafting", "profession", "face-slap"],
    ),
    st(
        "xuanhuan-scene-beast-tamer-bond",
        "御兽结契灵宠觉醒",
        "御兽师与神兽灵宠/异兽结契，宠物身世成谜，神兽觉醒带来战力与情感双线。收服—磨合—觉醒三段，灵宠不是道具而是伙伴。",
        beats=["相遇结契", "磨合患难", "血脉/神兽觉醒"],
        emotional_peak="灵宠为护主暴种/觉醒的瞬间",
        payoff_angle="情感羁绊驱动的战力升级",
        typical_tricks=["御兽师", "神兽灵宠", "宠物身世", "神兽觉醒", "药草守护兽", "变异", "血脉之力", "收养孤儿"],
        common_pitfall="灵宠工具化、只在打架时出现",
        subversion="灵宠的真实来历比主角更显赫/它在守护主角完成某使命",
        fits_phase=["setup", "escalation"],
        tags=["crafting", "beast-tamer", "bond"],
    ),
]

# ── 8. 身世 / 血脉 / 反转 ────────────────────────────────────────────────
ENTRIES += [
    st(
        "xuanhuan-scene-bloodline-revelation",
        "身世揭秘血脉觉醒",
        "主角查明身份/发现身世之谜，看似废柴实为上古血脉/神族遗孤。族谱、信物、异象层层指向真相（前面须有≥3处暗示）。觉醒带来力量也带来追杀。",
        beats=["疑点累积", "真相揭晓", "血脉觉醒+招祸"],
        emotional_peak="血脉共鸣/族谱认主的一刻",
        payoff_angle="反差爽点：被轻视者实为天命之子",
        typical_tricks=["身世之谜", "发现秘事身世之迷", "查明身份", "查真相", "神秘族谱", "遗孤", "血脉之力", "封存异能", "天赋异能", "转世投胎", "陨落重生", "起死回生", "秘中之秘"],
        common_pitfall="血脉真相无伏笔、突然天降",
        subversion="血脉真相是个被植入的谎言/觉醒即被操控",
        fits_phase=["twist", "climax"],
        tags=["identity", "bloodline", "reversal"],
    ),
    st(
        "xuanhuan-scene-mole-betrayal-reveal",
        "卧底反目敌友翻转",
        "可信赖之人实为卧底（友情背叛/反目成仇/大义灭亲），或被诬陷者实为忠良。前面须埋≥2处'不对劲'细节，揭穿时旧情与杀机交织。",
        beats=["并肩信任", "破绽浮现", "身份揭穿对峙"],
        emotional_peak="对峙中那句'你早就知道了？'",
        payoff_angle="信任崩塌的情感冲击+智斗回收伏笔",
        typical_tricks=["卧底", "友情背叛", "反目成仇", "大义灭亲", "诬陷", "反指控", "故意泄密", "示警", "几度试探", "隔墙有耳", "尽弃前嫌", "改邪归正"],
        common_pitfall="反转无伏笔、纯为震惊而震惊",
        subversion="卧底是双重的：他在背叛仇家以护主角",
        fits_phase=["twist", "climax"],
        tags=["identity", "betrayal", "reversal"],
    ),
    st(
        "xuanhuan-scene-disguise-infiltration",
        "改头换面潜入虎穴",
        "主角改头换面/假扮身份/冒名顶替潜入敌方势力，在层层试探下查真相、盗情报、伺机暗算。身份随时可能暴露，张力来自'差一点被识破'。",
        beats=["易容潜入", "周旋试探", "得手/暴露险脱"],
        emotional_peak="身份将破未破的极限拉扯",
        payoff_angle="谍战式张力嵌入修真世界",
        typical_tricks=["改头换面", "假扮身份", "冒牌顶替", "卧底", "几度试探", "伺机暗算", "盗尸", "密令", "借口离开", "巧妙化解", "保守秘密"],
        common_pitfall="伪装毫无破绽风险、无人起疑",
        subversion="他要顶替的人其实早已被对方掉包",
        fits_phase=["escalation", "twist"],
        tags=["identity", "infiltration"],
    ),
    st(
        "xuanhuan-scene-master-inheritance",
        "巧遇高人得传承",
        "主角巧遇神秘高手/前辈仙人，被相中授以仙人传承/独门绝技。传承非白给：需考核心性、付出代价或承下因果。师徒缘起也埋下师门来历之谜。",
        beats=["机缘相遇", "受考验", "承传承+背因果"],
        emotional_peak="被认可、接过传承的庄重一刻",
        payoff_angle="实力跳板，同时套上'人情债/使命'",
        typical_tricks=["巧遇高人", "神秘高手", "前辈指点", "仙人传承", "神秘师傅", "独门绝技", "习得功法", "传位神秘人", "收徒弟", "前辈仙人", "剑修", "佛子"],
        common_pitfall="传承纯属白嫖、毫无门槛",
        subversion="传承者的目的是借主角之手了结自己的旧仇/旧债",
        fits_phase=["setup", "escalation"],
        tags=["identity", "inheritance", "mentor"],
    ),
]

# ── 9. 心魔 / 大局 / 三界 ─────────────────────────────────────────────────
ENTRIES += [
    st(
        "xuanhuan-scene-tribulation-defy-heaven",
        "渡劫逆天斩因果",
        "主角渡劫/历劫时遭天道扼杀（逆天气运者必受针对），以逆天之势硬抗或斩掉因果。把'升级'升格为'与天争命'，代价沉重、九死一生。",
        beats=["天劫临身", "天道针对加码", "逆天闯过"],
        emotional_peak="劫云压顶仍向天举剑的一刻",
        payoff_angle="将个人突破接进'命运/天道'母题",
        typical_tricks=["历劫", "下凡历劫", "渡劫", "天道扼杀", "逆天气运", "斩掉因果", "因果", "受降天罪", "雷神", "羽化成蝶"],
        common_pitfall="渡劫只是更大的烟花、无命运重量",
        subversion="天道针对他，是因为他本就是天道的一块碎片",
        fits_phase=["climax", "resolution_hook"],
        tags=["cosmic", "tribulation", "fate"],
    ),
    st(
        "xuanhuan-scene-three-realms-war",
        "三界交战正魔决战",
        "仙魔/正魔/三界势力的总决战（三界斗争/正魔交战/仙魔勾结浮出）。多线交汇、阵营反水、底牌尽出。个人恩怨在文明级冲突中收束。",
        beats=["阵营集结", "多线交汇反水", "决战收束"],
        emotional_peak="底牌引爆、战局逆转的高燃点",
        payoff_angle="把全书伏笔在一战中集中回收",
        typical_tricks=["三界斗争", "三界之战", "正魔交战", "仙魔勾结", "魔族入侵", "阻止魔族谋划", "魔族密谋", "决战", "决斗", "爆底牌", "卷入阴谋"],
        common_pitfall="大战只写群殴、缺乏个人焦点",
        subversion="正魔之分本是同一存在的两面/挑起战争者另有其人",
        fits_phase=["climax"],
        tags=["cosmic", "war", "finale"],
    ),
    st(
        "xuanhuan-scene-demonic-temptation",
        "魔道蛊惑改邪与堕落",
        "魔头/混世魔王/天煞孤星以力量与解脱蛊惑人心，主角或同伴面临改邪归正与走火入魔的拉扯。把'变强诱惑'写成道德抉择。",
        beats=["魔道递饵", "动摇试探", "抉择(堕/守)"],
        emotional_peak="接还是不接那只'递来的手'",
        payoff_angle="价值观冲突外化为力量诱惑",
        typical_tricks=["蛊惑人心", "魔头", "混世魔王", "天煞孤星", "改邪归正", "魅惑众生", "因爱成魔", "卧底", "下凡"],
        common_pitfall="善恶非黑即白、抉择无重量",
        subversion="拒绝诱惑反而中了更深的局",
        fits_phase=["twist", "escalation"],
        tags=["cosmic", "temptation", "morality"],
    ),
]

# ════════════════════════════════════════════════════════════════════════
# plot_patterns — 长线骨架（outline 级；经 PlotForge 差异化后供大纲引用）
# ════════════════════════════════════════════════════════════════════════
ENTRIES += [
    pp(
        "xuanhuan-plot-bloodfeud-revenge",
        "主线·血仇复仇三幕",
        "玄幻最经典骨架：血仇—隐忍—爆发。灭门/背叛立起仇恨锚点；主角在各势力间周旋、查真相、定位仇人；最终精准惩处，每次对应读者期待的'羞辱式回响'。",
        stages=[
            {"name": "血仇", "purpose": "立仇恨锚点", "note": "灭门/背叛/至亲之死"},
            {"name": "隐忍", "purpose": "成长+查真相+定位", "note": "周旋各势力，逐步逼近"},
            {"name": "爆发", "purpose": "精准惩处", "note": "逐一清算，层层回响"},
        ],
        payoff="羞辱式回响+真相全貌揭晓",
        common_pitfall="隐忍段注水、爆发段仓促",
        subversion="仇人名单里有一个无辜者/最大仇人是自己人",
        tags=["revenge", "three-act"],
    ),
    pp(
        "xuanhuan-plot-mortal-to-immortal",
        "主线·凡人修仙登顶",
        "从测试灵根的废柴/凡人起步，经宗门、秘境、大劫一路攀爬境界与势力，最终飞升成仙/登帝位。核心是'阶层压制→打破压制'的循环螺旋上升。",
        stages=[
            {"name": "起微", "purpose": "受压立志", "note": "废柴/凡人/被轻视"},
            {"name": "攀爬", "purpose": "境界+势力扩张", "note": "宗门→区域→大千世界"},
            {"name": "登顶", "purpose": "飞升/封帝", "note": "打破最高阶层压制"},
        ],
        payoff="阶层彻底反转+世界地图扩张",
        common_pitfall="一路顺推、无同阶压制与越阶风险",
        subversion="登顶后发现'仙界'是更大的牢笼",
        tags=["progression", "rags-to-riches"],
    ),
    pp(
        "xuanhuan-plot-reincarnation-redo",
        "主线·重生归来改命",
        "陨落重生/转世投胎归来，带着前世记忆改写命运：避开旧劫、提前布局、回收旧仇旧情。爽点在'信息差碾压'，难点在避免前世剧本完全复刻。",
        stages=[
            {"name": "归来", "purpose": "重生+定锚改命目标", "note": "携前世记忆"},
            {"name": "布局", "purpose": "信息差预判+预埋", "note": "提前夺机缘/避劫"},
            {"name": "改命", "purpose": "改写关键节点", "note": "旧仇旧情新解"},
        ],
        payoff="信息差碾压+'这一世不一样了'",
        common_pitfall="全程预知导致零张力",
        subversion="重生本身是某人布的局/前世记忆有假",
        tags=["reincarnation", "knowledge-arbitrage"],
    ),
    pp(
        "xuanhuan-plot-profession-ascendancy",
        "主线·职业流封神",
        "以炼丹/炼器/符阵/御兽某一职业为根基崛起：从被轻视的'辅助'到一品宗师，靠专业碾压而非纯战力。每卷一个'技术性打脸'里程碑。",
        stages=[
            {"name": "入行", "purpose": "受轻视+显天赋", "note": "辅助职业被低估"},
            {"name": "扬名", "purpose": "技术里程碑", "note": "一品丹/神器/绝阵"},
            {"name": "封神", "purpose": "以专业定鼎", "note": "宗师地位+势力倚重"},
        ],
        payoff="专业碾压式打脸+不可替代地位",
        common_pitfall="职业只是名号、无规则与进阶感",
        subversion="顶级职业的真相是一门将吞噬施术者的禁术",
        tags=["profession", "crafting"],
    ),
    pp(
        "xuanhuan-plot-undercover-double-agent",
        "主线·卧底双面长线",
        "主角作为卧底潜入敌方（或反派卧底于正道），长期周旋、几度试探、伪装与真心拉扯。最终身份暴露引爆总冲突，忠诚归属是核心悬念。",
        stages=[
            {"name": "潜入", "purpose": "立身份+定任务", "note": "假投效/改头换面"},
            {"name": "周旋", "purpose": "取信+试探+动摇", "note": "真心与任务冲突"},
            {"name": "摊牌", "purpose": "暴露+归属抉择", "note": "引爆总冲突"},
        ],
        payoff="忠诚归属揭晓+伏笔总回收",
        common_pitfall="卧底身份毫无暴露风险",
        subversion="他连自己都不知道自己是被植入记忆的卧底",
        tags=["undercover", "loyalty"],
    ),
    pp(
        "xuanhuan-plot-save-the-realms",
        "主线·三界救世大局",
        "魔族入侵/世界毁灭危机/天道大劫逼近，主角从个人恩怨被推向'拯救苍生'。集结盟友、阻止魔族谋划、识破天道阴谋，个人成长与文明存亡并轨。",
        stages=[
            {"name": "征兆", "purpose": "危机伏线", "note": "异象/天劫前兆"},
            {"name": "集结", "purpose": "盟友+底牌+揭谋", "note": "阻止魔族谋划"},
            {"name": "决战", "purpose": "存亡级终局", "note": "拯救苍生"},
        ],
        payoff="文明存续+个人弧闭环",
        common_pitfall="'救世'口号化、无具体取舍代价",
        subversion="'魔族'是被天道选中的替罪者",
        tags=["save-the-world", "epic"],
    ),
]

# ════════════════════════════════════════════════════════════════════════
# thematic_motifs — 母题（供气氛/意象借鉴）
# ════════════════════════════════════════════════════════════════════════
ENTRIES += [
    motif("xuanhuan-motif-defy-heaven", "逆天改命", "以个人意志对抗天道/宿命/气运的母题，贯穿渡劫、斩因果、逆天气运等桥段。",
          exprs=["我命由我不由天", "天道扼杀偏要逆", "斩因果以自证"], tags=["fate"]),
    motif("xuanhuan-motif-dao-vs-heart", "大道与人心", "求道飞升与放不下的情/义之间的张力，是因爱成魔、师徒虐恋、为爱违天的母题内核。",
          exprs=["求长生却舍不得人间", "道心 vs 情劫", "斩情入道的代价"], tags=["romance", "dao"]),
    motif("xuanhuan-motif-face-and-hierarchy", "颜面与阶层", "境界即阶层、面子即秩序；打脸、越阶反杀、晋升都围绕'阶层压制—打破'运转。",
          exprs=["以下犯上的快感", "越阶碾压的羞辱回响", "废柴翻身改写排序"], tags=["face-slap", "hierarchy"]),
    motif("xuanhuan-motif-karma-cycle", "因果轮回", "今因结来果、转世续旧缘的母题，串起重生、地府、孽缘再续、斩因果。",
          exprs=["前世今生的债", "因果绕不过", "轮回里认出彼此"], tags=["karma"]),
    motif("xuanhuan-motif-bloodline-destiny", "血脉天命", "出身即命运、血脉即枷锁亦是钥匙；身世之谜、神族遗孤、血脉觉醒皆由此生。",
          exprs=["血里藏着的真相", "天命之子的重负", "觉醒即招祸"], tags=["bloodline"]),
    motif("xuanhuan-motif-good-evil-blur", "正魔一念", "正与魔同源、善与恶一念之差；魔道蛊惑、改邪归正、卧底归属都在叩问此题。",
          exprs=["正魔本同源", "一念成魔一念成佛", "谁才是真正的恶"], tags=["morality"]),
]

# ════════════════════════════════════════════════════════════════════════
# emotion_arcs — 情绪弧（压缩→释放的玄幻常用曲线）
# ════════════════════════════════════════════════════════════════════════
ENTRIES += [
    emo("xuanhuan-emo-humiliation-to-faceslap", "受辱压抑→越阶打脸",
        "被轻视、被夺机缘、被压制的压缩期，至少积压两章，再以越阶反杀一举释放，靠旁观者分层反应放大。",
        curve=["被轻视", "被压制/夺机缘", "隐忍蓄势", "越阶反杀", "地位反转"], tags=["face-slap"]),
    emo("xuanhuan-emo-grief-to-resolve", "丧亲悲恸→立誓前行",
        "灭门/至亲逝去的剧痛，经历麻木与崩溃，最终凝成冷静而沉重的复仇/守护之志。",
        curve=["噩耗", "崩溃", "麻木", "立誓", "负重前行"], tags=["revenge", "tragedy"]),
    emo("xuanhuan-emo-trust-to-betrayal", "交心信任→背叛崩塌",
        "从并肩患难建立的深厚信任，到破绽浮现的不安，最终背叛揭穿带来的情感塌方与决裂。",
        curve=["并肩", "交心", "隐隐不安", "背叛揭穿", "决裂/反噬"], tags=["betrayal"]),
    emo("xuanhuan-emo-temptation-struggle", "力量诱惑→道心抉择",
        "魔道/捷径递来诱惑，主角在'变强'与'守心'间反复拉扯，最终做出带代价的抉择。",
        curve=["递饵", "心动", "拉扯", "临界", "抉择(堕/守)"], tags=["morality"]),
    emo("xuanhuan-emo-bottleneck-breakthrough", "瓶颈郁结→顿悟突破",
        "久卡瓶颈的焦灼与自我怀疑，经外压逼迫或一念顿悟，迎来酣畅的突破释放。",
        curve=["卡瓶颈", "焦灼自疑", "外压/顿悟", "突破", "豁然"], tags=["cultivation"]),
    emo("xuanhuan-emo-forbidden-love", "禁忌相吸→为爱违天",
        "人妖/仙魔相恋的悸动与隐忍，在族规天条的高压下，走向为爱违逆一切的孤勇或两难。",
        curve=["惊艳", "悸动", "禁忌隐忍", "高压逼迫", "为爱违天"], tags=["romance"]),
]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # Guard against accidental duplicate slugs within a dimension.
    seen: set[tuple[str, str]] = set()
    for e in ENTRIES:
        key = (e["dimension"], e["slug"])
        if key in seen:
            raise SystemExit(f"duplicate (dimension, slug): {key}")
        seen.add(key)
    with OUT.open("w", encoding="utf-8") as fh:
        fh.write("# 玄幻 (xuanhuan) material-library seed — generated by scripts/build_xuanhuan_seed.py\n")
        fh.write("# genre=玄幻, sub_genre=null (matches every 玄幻 book); import via scripts/import_material_jsonl.py\n")
        for e in ENTRIES:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    by_dim: dict[str, int] = {}
    for e in ENTRIES:
        by_dim[e["dimension"]] = by_dim.get(e["dimension"], 0) + 1
    print(f"wrote {len(ENTRIES)} entries → {OUT}")
    print("by dimension:", by_dim)


if __name__ == "__main__":
    main()
