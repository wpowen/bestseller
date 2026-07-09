"""题材 → 读者画像 映射 + 画像锚定的一句话钩子生成（接入框架）。

题材与读者画像强关联：定题材+频道即定人群/知识面/爽点/雷点/对标/钩子公式。
生成简介与一句话钩子时，把画像作为**硬锚**注入（写给谁、他能懂什么、他要什么爽），
而非事后过滤。配套 docs/读者画像驱动-内容生成与优化流程-20260630.md。

调研锚定(2026-06)：番茄/七猫=免费下沉、男女略男多、两头大(青少年+40-50)、千人千面;
男频=都市/玄幻/科幻/仙侠/系统升级;女频=现代/幻想/古代言情(甜宠/豪门/重生复仇居前)。
"""
from __future__ import annotations

# ruff: noqa: RUF001
from dataclasses import dataclass


@dataclass(frozen=True)
class ReaderPersona:
    channel: str
    genres: tuple[str, ...]
    who: str
    demographics: str
    knowledge: str
    fantasy: str
    click_triggers: tuple[str, ...]
    turnoffs: tuple[str, ...]
    benchmarks: tuple[str, ...]
    hook_formula: str
    persona_judge_role: str


_MALE_SHUANG = ReaderPersona(
    channel="男频",
    genres=("都市", "玄幻", "高武", "系统", "升级", "赛博", "修仙", "仙侠", "异能", "战神", "赘婿", "兵王", "历史", "无限流"),
    who="一个被现实压着、想看爽文翻身解压的普通男性",
    demographics="18-35岁男性为主,三四五线/县城/乡镇;学生、蓝领、工厂、外卖快递网约车司机、服务业",
    knowledge="初中/高中/中专为主,非高知。不懂编程/赛博/'编译·算法·数据化·杠杆'等专业名词,出现即天书",
    fantasy="代入底层/被看不起的主角,获得逆天金手指,把踩过他、瞧不起他的人一个个打脸碾压、扮猪吃虎",
    click_triggers=("废物/赘婿/被退婚/被瞧不起的开局", "觉醒金手指/外挂", "打脸碾压装逼", "越级杀人/逆袭", "复仇灭门旧仇"),
    turnoffs=("专业名词/黑话堆砌看不懂", "主角窝囊太久不爽", "节奏慢/铺垫长", "文绉绉/烧脑", "需要背景知识"),
    benchmarks=("斗破苍穹", "诡秘之主", "大奉打更人", "十日终焉", "我在精神病院学斩神"),
    hook_formula="可代入的底层主角 + 憋屈到想骂街的处境(被踩/被弃/被羞辱) + 立刻能爽的承诺(金手指/逆袭/打脸,全大白话,零专名)",
    persona_judge_role=(
        "你是一个28岁的男读者,在三线城市送外卖,初中文化,每天挤地铁和上厕所时用番茄小说看免费爽文解压。"
        "你看书只看书名+简介3秒就决定点不点;你要的是代入感和爽——废物逆袭、打脸装逼、金手指碾压;"
        "你最烦看不懂的专业名词和文绉绉的东西,一看不懂就立刻划走。"
    ),
)

_FEMALE_EMO = ReaderPersona(
    channel="女频",
    # 2026-07-09 L3真机验收发现:题材preset "女性成长/情感拉扯" 一个token都命不中
    # 本表 → 女频书被路由到男频判官(判官理由全是"打脸吃绝户带劲") → 文案淘汰赛
    # 被男频口味带偏,简介承诺漂向复仇爽文。补齐女频preset实际使用的题材词。
    genres=("言情", "甜宠", "豪门", "总裁", "重生", "穿越", "虐恋", "古言", "宫斗", "大女主", "先婚后爱", "复仇", "青春", "女性成长", "情感拉扯", "女频"),
    who="一个要情绪拉扯、要爽要甜要虐的女性读者",
    demographics="16-40岁女性为主,城市+下沉皆有;学生、白领、宝妈、服务业;晋江偏高知挑剔,番茄/七猫偏小白",
    knowledge="阅读量大、对情感套路敏感;不吃机制黑话,吃关系张力和情绪",
    fantasy="代入女主,在情感关系里掌握主动——被偏爱/被追悔/打脸渣男绿茶/重生复仇翻盘/先苦后甜",
    click_triggers=("撞破背叛/被渣", "重生回到关键节点", "扮猪吃虎打脸渣男绿茶", "高位男主偏爱独宠", "虐到极致再甜", "马甲掉落/真千金"),
    turnoffs=("女主恋爱脑/窝囊", "男主油腻/爹味", "机制设定堆砌", "情绪不到位/不戳心"),
    benchmarks=("偷偷藏不住", "知否知否", "甄嬛传", "豪门重生复仇头部作"),
    hook_formula="可代入的女主 + 一个戳心的情感引爆点(背叛/重生/被弃) + 立刻能爽的情绪承诺(打脸渣/被偏爱/复仇翻盘)",
    persona_judge_role=(
        "你是一个26岁的女读者,在二线城市做行政,平时用番茄/晋江看言情;看书只看书名+简介几秒决定点不点;"
        "你要的是情绪拉扯——撞破背叛、重生复仇、打脸渣男绿茶、被高位男主偏爱;你最烦女主恋爱脑、男主爹味、和一堆冷冰冰的设定术语。"
    ),
)

# 中性通用画像：题材/标签/频道都解析不出人群时的兜底。
# 之前无信号一律硬套男频爽文画像(憋屈打脸公式)——悬疑/科幻/治愈等
# 非爽文向的书被套错人群,钩子/简介被固化成打脸逆袭模板(跨书固化)。
_GENERAL = ReaderPersona(
    channel="通用",
    genres=(),
    who="一个在免费书城刷书、看简介3秒决定点不点的普通读者",
    demographics="全年龄段大众读者,城市与下沉皆有",
    knowledge="不懂专业名词和生造黑话,出现即天书;要一眼看懂",
    fantasy="一眼看懂的强处境+可代入的主角+立刻想知道接下来会怎样",
    click_triggers=("一眼看懂的危机或反差开局", "强悬念/强目标", "主角可代入"),
    turnoffs=("专业名词/黑话堆砌看不懂", "开局平淡没钩子", "铺垫长/节奏慢"),
    benchmarks=(),
    hook_formula=(
        "可代入的主角 + 一眼看懂的具体危机或反差处境 + "
        "一个让人非点不可的悬念或承诺(大白话,零专名);按本书题材给读者最想要的东西,不套打脸逆袭模板"
    ),
    persona_judge_role=(
        "你是一个用免费小说APP打发时间的普通读者,看书只看书名+简介3秒决定点不点;"
        "你要一眼看懂、立刻被吊住;看不懂或平淡就划走。"
    ),
)

_PERSONAS = (_MALE_SHUANG, _FEMALE_EMO)


def resolve_persona(
    genre: str | None,
    sub_genre: str | None = None,
    tags: tuple[str, ...] = (),
    channel: str | None = None,
) -> ReaderPersona:
    """按题材/标签/频道解析读者画像。命中关键词最多者胜;无任何信号时用中性通用画像
    (不再硬套男频爽文,避免非爽文题材被固化成打脸逆袭模板)。"""
    blob = " ".join(str(x or "") for x in (genre, sub_genre, *tags))
    if channel:
        for p in _PERSONAS:
            if p.channel == channel:
                return p
    best, best_hits = _GENERAL, 0
    for p in _PERSONAS:
        hits = sum(1 for g in p.genres if g in blob)
        if hits > best_hits:
            best, best_hits = p, hits
    return best


def build_persona_hook_messages(
    *,
    genre: str | None,
    sub_genre: str | None = None,
    tags: tuple[str, ...] = (),
    premise: str,
    channel: str | None = None,
) -> tuple[str, str]:
    """画像锚定的一句话钩子生成 (system, user)。给定题材+设定 → 写给目标读者的钩子。"""
    p = resolve_persona(genre, sub_genre, tags, channel)
    system = (
        "你是顶尖中文网文主编，最擅长写让目标读者在书城里一眼就想点进去的一句话钩子。"
        "你深知不同题材对应不同读者，钩子必须为这个具体读者量身定做。"
    )
    user = (
        f"【题材】{genre or ''}（{sub_genre or ''}）\n"
        f"【目标读者】{p.channel}：{p.who}\n"
        f"【他的知识面】{p.knowledge}\n"
        f"【他要的爽点】{p.fantasy}\n"
        f"【他的雷点】{('、'.join(p.turnoffs))}\n"
        f"【钩子公式】{p.hook_formula}\n\n"
        f"【本书设定】\n{premise}\n\n"
        "请据此写【一条一句话钩子】，硬性要求：\n"
        "①≤35字，一口气读完，第一眼就让上面这个读者想点；\n"
        "②严禁任何他看不懂的专业名词/生造黑话/系统术语/等级编号——用大白话；\n"
        "③必须直给这个题材读者最想要的那个爽点/情感引爆点（按钩子公式），让他立刻知道'爽在哪/虐在哪'；\n"
        "④主角要可代入，处境要够憋屈或够反差。\n"
        '只输出一个JSON：{"hook":"一句话钩子"}'
    )
    return system, user


def build_persona_blurb_messages(
    *,
    genre: str | None,
    sub_genre: str | None = None,
    tags: tuple[str, ...] = (),
    premise: str,
    hook: str,
    channel: str | None = None,
) -> tuple[str, str]:
    """画像锚定的点击型简介扩写 (system, user)。把一句话钩子扩成详情页简介。"""
    p = resolve_persona(genre, sub_genre, tags, channel)
    system = (
        "你是顶尖中文网文主编，最擅长写详情页【点击型】作品简介——让目标读者只看这段就忍不住点进去。"
        "简介必须为这个具体题材的读者量身定做。"
    )
    user = (
        f"【题材】{genre or ''}（{sub_genre or ''}）\n"
        f"【目标读者】{p.channel}：{p.who}\n"
        f"【他的知识面】{p.knowledge}\n"
        f"【他要的爽点】{p.fantasy}\n"
        f"【他的雷点】{('、'.join(p.turnoffs))}\n"
        f"【已定的一句话钩子】{hook}\n"
        f"【本书设定】\n{premise}\n\n"
        "把这条钩子扩写成一段【点击型简介】，硬性要求：\n"
        "①首句就用或承接这条钩子，3秒抓住上面这个读者；\n"
        "②全程零专业黑话/生造名词/系统术语/等级编号——用大白话，没读过设定的人也秒懂；\n"
        "③把这个题材读者最想要的爽点/情感引爆点(按其爽点)写实写到位，让他立刻知道'爽在哪/虐在哪'；\n"
        "④主角可代入，处境够憋屈或够反差；\n"
        "⑤100-160字，分2-3段，动词驱动、克制形容词，结尾留悬念钩子不剧透；\n"
        "⑥禁AI腔套话(本以为/却没想到/命运的齿轮/何去何从/敬请期待)。\n"
        '只输出一个JSON：{"blurb":"简介正文"}'
    )
    return system, user


__all__ = [
    "ReaderPersona",
    "resolve_persona",
    "build_persona_hook_messages",
    "build_persona_blurb_messages",
]
